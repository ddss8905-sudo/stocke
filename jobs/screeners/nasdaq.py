import csv
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta, timezone
from io import StringIO
from typing import Dict, List
from urllib.request import urlopen

import pandas as pd
import requests

from .common import (
    MarketConfig,
    add_technical_features,
    build_market_regime,
    build_candidates,
    latest_feature_row,
    score_universe,
    start_date,
)


NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
NASDAQ_SCREENER_URL = "https://api.nasdaq.com/api/screener/stocks"
OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]
LIQUIDITY_PREFILTER_SIZE = 1000


CFG = MarketConfig(
    market="NASDAQ",
    lookback_days=420,
    universe_size=500,
    min_price=10.0,
    min_adv20=20_000_000.0,
    min_final_score=80.0,
    min_rs_rank=80.0,
    min_close_to_52w_high_ratio=0.85,
    entry_volume_multiplier=1.5,
    pullback_volume_multiplier=1.0,
    fixed_stop_pct=0.08,
    max_risk_to_stop=0.10,
    max_atr_pct=0.12,
    max_close_to_ma50_ratio=1.35,
    max_entry_extension_pct=0.05,
    stop_atr_multiple=2.5,
    structure_stop_atr_buffer=0.5,
    trailing_atr_multiple=3.0,
    min_market_regime_score=40.0,
    benchmark_tickers=["QQQ", "SPY"],
)


EXCLUDE_NAME_KEYWORDS = [
    "Warrant", "Warrants", "Right", "Rights", "Unit", "Units",
    "Preferred", "Depositary Shares", "Notes", "Note", "Bond",
    "Debenture", "Fund", "Trust Preferred", "Acquisition Corp", "Blank Check",
]


def fetch_universe() -> pd.DataFrame:
    with urlopen(NASDAQ_LISTED_URL, timeout=30) as response:
        content = response.read().decode("utf-8")

    rows = []
    reader = csv.DictReader(StringIO(content), delimiter="|")
    for row in reader:
        symbol = row.get("Symbol", "").strip()
        name = row.get("Security Name", "").strip()
        if not symbol or symbol.startswith("File Creation Time"):
            continue
        if row.get("ETF") == "Y" or row.get("Test Issue") == "Y":
            continue
        if any(keyword.lower() in name.lower() for keyword in EXCLUDE_NAME_KEYWORDS):
            continue
        rows.append({"ticker": symbol.replace(".", "-"), "security_name": name})

    return pd.DataFrame(rows, columns=["ticker", "security_name"]).drop_duplicates("ticker")


def fetch_liquidity_prefilter(listed: pd.DataFrame) -> pd.DataFrame:
    response = requests.get(
        NASDAQ_SCREENER_URL,
        params={
            "tableonly": "true",
            "limit": 5000,
            "offset": 0,
            "exchange": "NASDAQ",
            "download": "true",
        },
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json, text/plain, */*",
            "Origin": "https://www.nasdaq.com",
            "Referer": "https://www.nasdaq.com/market-activity/stocks/screener",
        },
        timeout=45,
    )
    response.raise_for_status()
    rows = response.json().get("data", {}).get("rows") or []
    listed_names = listed.set_index("ticker")["security_name"].to_dict()
    active = []
    for row in rows:
        ticker = str(row.get("symbol") or "").replace(".", "-")
        if ticker not in listed_names:
            continue
        close = pd.to_numeric(str(row.get("lastsale") or "").replace("$", "").replace(",", ""), errors="coerce")
        volume = pd.to_numeric(str(row.get("volume") or "").replace(",", ""), errors="coerce")
        if pd.isna(close) or pd.isna(volume) or close < CFG.min_price or volume <= 0:
            continue
        active.append({
            "ticker": ticker,
            "security_name": listed_names[ticker],
            "current_dollar_volume": float(close * volume),
        })
    if not active:
        raise RuntimeError("NASDAQ official screener returned no usable liquidity rows.")
    return (
        pd.DataFrame(active)
        .sort_values("current_dollar_volume", ascending=False)
        .head(LIQUIDITY_PREFILTER_SIZE)
        .reset_index(drop=True)
    )


def download_ohlcv(tickers: List[str], start: str, end: str) -> Dict[str, pd.DataFrame]:
    result: Dict[str, pd.DataFrame] = {}
    symbols = list(dict.fromkeys(tickers))
    if not symbols:
        return result
    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = {
            executor.submit(download_yahoo_chart, ticker, start, end): ticker
            for ticker in symbols
        }
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                df = future.result()
            except Exception as exc:
                print(f"[WARN] failed to download {ticker}: {exc}")
                continue
            if not df.empty:
                result[ticker] = df
    return result


def unix_time(value: str) -> int:
    return int(datetime.fromisoformat(value).replace(tzinfo=timezone.utc).timestamp())


def download_yahoo_chart(ticker: str, start: str, end: str) -> pd.DataFrame:
    period1 = unix_time(start)
    period2 = unix_time((date.fromisoformat(end) + timedelta(days=1)).isoformat())
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {
        "period1": period1,
        "period2": period2,
        "interval": "1d",
        "events": "history",
        "includeAdjustedClose": "true",
    }
    headers = {"User-Agent": "Mozilla/5.0"}

    for attempt in range(3):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=20)
            response.raise_for_status()
            payload = response.json()
            break
        except Exception as exc:
            if attempt == 2:
                print(f"[WARN] failed to download {ticker}: {exc}")
                return pd.DataFrame(columns=OHLCV_COLUMNS)
            time.sleep(0.5 * (2 ** attempt))

    try:
        result = (payload.get("chart", {}).get("result") or [None])[0]
        if not result:
            print(f"[WARN] no Yahoo chart result for {ticker}")
            return pd.DataFrame(columns=OHLCV_COLUMNS)

        timestamps = result.get("timestamp") or []
        quote = (result.get("indicators", {}).get("quote") or [None])[0]
        if not timestamps or not quote:
            print(f"[WARN] no Yahoo OHLCV quote for {ticker}")
            return pd.DataFrame(columns=OHLCV_COLUMNS)

        df = pd.DataFrame({
            "open": quote.get("open"),
            "high": quote.get("high"),
            "low": quote.get("low"),
            "close": quote.get("close"),
            "volume": quote.get("volume"),
        }, index=pd.to_datetime(timestamps, unit="s").date)
        return df[OHLCV_COLUMNS].dropna()
    except Exception as exc:
        print(f"[WARN] failed to download {ticker}: {exc}")
        return pd.DataFrame(columns=OHLCV_COLUMNS)


def download_in_chunks(tickers: List[str], start: str, end: str, chunk_size: int = 200) -> Dict[str, pd.DataFrame]:
    result: Dict[str, pd.DataFrame] = {}
    for index in range(0, len(tickers), chunk_size):
        chunk = tickers[index:index + chunk_size]
        print(f"[INFO] NASDAQ download chunk {index // chunk_size + 1}: {len(chunk)}")
        result.update(download_ohlcv(chunk, start, end))
    return result


def select_top_by_adv(end_date: str) -> pd.DataFrame:
    listed = fetch_universe()
    if listed.empty:
        raise RuntimeError("NASDAQ official listing returned no common-stock symbols.")
    universe = fetch_liquidity_prefilter(listed)
    start = (date.fromisoformat(end_date) - timedelta(days=45)).isoformat()
    recent = download_in_chunks(universe["ticker"].tolist(), start, end_date, chunk_size=200)
    names = universe.set_index("ticker")["security_name"].to_dict()

    rows = []
    for ticker, df in recent.items():
        if len(df) < 20:
            continue
        tail = df.tail(20)
        adv = (tail["close"] * tail["volume"]).mean()
        last_close = tail["close"].iloc[-1]
        if pd.isna(adv) or pd.isna(last_close) or last_close < CFG.min_price:
            continue
        rows.append({"ticker": ticker, "security_name": names.get(ticker, ""), "close": float(last_close), "adv": float(adv)})

    if not rows:
        raise RuntimeError("No NASDAQ symbols were selected. Yahoo Finance returned no usable recent OHLCV data.")

    return pd.DataFrame(rows, columns=["ticker", "security_name", "close", "adv"]).sort_values("adv", ascending=False).head(CFG.universe_size).reset_index(drop=True)


def run(end_date: str) -> dict:
    selected = select_top_by_adv(end_date)
    if selected.empty:
        raise RuntimeError("No NASDAQ symbols were selected by average dollar volume.")
    tickers = selected["ticker"].tolist()
    names = selected.set_index("ticker")["security_name"].to_dict()
    all_tickers = sorted(set(tickers + CFG.benchmark_tickers))

    ohlcv = download_in_chunks(all_tickers, start_date(end_date, CFG.lookback_days), end_date, chunk_size=200)
    if CFG.benchmark_tickers[0] not in ohlcv or CFG.benchmark_tickers[1] not in ohlcv:
        raise RuntimeError("NASDAQ benchmark data is missing. Please retry later.")
    primary = add_technical_features(ohlcv[CFG.benchmark_tickers[0]])
    secondary = add_technical_features(ohlcv[CFG.benchmark_tickers[1]])

    rows = []
    for ticker in tickers:
        if ticker not in ohlcv:
            continue
        df = add_technical_features(ohlcv[ticker])
        if len(df) < 260:
            continue
        last = df.iloc[-1]
        if last["close"] < CFG.min_price or last["adv20"] < CFG.min_adv20:
            continue
        row = latest_feature_row(ticker, names.get(ticker, ""), df, primary, secondary, CFG)
        if row:
            rows.append(row)

    scored = score_universe(pd.DataFrame(rows)) if rows else pd.DataFrame()
    market_regime = build_market_regime(primary, secondary, scored)
    candidates = build_candidates(scored, CFG, market_regime) if not scored.empty else pd.DataFrame()
    return {
        "market": CFG.market,
        "run_date": end_date,
        "selected": selected,
        "scored": scored,
        "candidates": candidates,
        "market_bullish": market_regime["market_bullish"],
        "market_regime_score": market_regime["score"],
        "market_exposure": market_regime["exposure"],
    }

