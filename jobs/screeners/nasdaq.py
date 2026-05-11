import csv
from datetime import date, timedelta
from io import StringIO
from typing import Dict, List
from urllib.request import urlopen

import pandas as pd
import yfinance as yf

from .common import (
    MarketConfig,
    add_technical_features,
    build_candidates,
    latest_feature_row,
    market_regime_is_bullish,
    score_universe,
    start_date,
)


NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]


CFG = MarketConfig(
    market="NASDAQ",
    lookback_days=420,
    universe_size=200,
    min_price=10.0,
    min_adv20=20_000_000.0,
    min_final_score=75.0,
    min_rs_rank=80.0,
    min_close_to_52w_high_ratio=0.85,
    entry_volume_multiplier=1.5,
    fixed_stop_pct=0.08,
    max_risk_to_stop=0.08,
    benchmark_tickers=["QQQ", "SPY"],
)


EXCLUDE_NAME_KEYWORDS = [
    "Warrant", "Warrants", "Right", "Rights", "Unit", "Units",
    "Preferred", "Depositary Shares", "Notes", "Note", "Bond",
    "Debenture", "Fund", "Trust Preferred",
]


BASE_TICKERS = [
    "AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "GOOG", "AVGO", "TSLA", "COST",
    "NFLX", "ADBE", "AMD", "INTC", "QCOM", "TXN", "AMAT", "MU", "PANW", "CRWD",
    "PLTR", "ASML", "LRCX", "KLAC", "ADP", "INTU", "CSCO", "PEP", "SBUX", "BKNG",
    "MELI", "MRVL", "SNPS", "CDNS", "MDB", "TEAM", "ZS", "DDOG", "SHOP", "ABNB",
    "ARM", "SMCI", "PYPL", "GILD", "REGN", "VRTX", "BIIB", "MRNA", "DXCM", "IDXX",
    "ADI", "APP", "AZN", "BKR", "CCEP", "CEG", "CHTR", "DASH", "EA", "EXC",
    "FANG", "FTNT", "GEHC", "HON", "KDP", "LIN", "MAR", "MDLZ", "MNST", "NXPI",
    "ORLY", "PCAR", "ROP", "ROST", "TTWO", "WDAY", "XEL", "MSTR", "RKLB", "SOUN",
    "WDC", "STX", "MTSI", "SITM", "LSCC", "AEHR", "MXL", "AXTI",
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


def download_ohlcv(tickers: List[str], start: str, end: str) -> Dict[str, pd.DataFrame]:
    if not tickers:
        return {}

    data = yf.download(
        tickers=tickers,
        start=start,
        end=end,
        auto_adjust=False,
        group_by="ticker",
        progress=False,
        threads=True,
    )

    result: Dict[str, pd.DataFrame] = {}
    if len(tickers) == 1:
        ticker = tickers[0]
        df = data.rename(columns=str.lower).copy()
        if all(c in df.columns for c in OHLCV_COLUMNS):
            result[ticker] = df[OHLCV_COLUMNS].dropna()
        return result

    for ticker in tickers:
        if ticker not in data.columns.get_level_values(0):
            continue
        df = data[ticker].copy()
        df.columns = [c.lower() for c in df.columns]
        if all(c in df.columns for c in OHLCV_COLUMNS):
            result[ticker] = df[OHLCV_COLUMNS].dropna()
    return result


def download_in_chunks(tickers: List[str], start: str, end: str, chunk_size: int = 200) -> Dict[str, pd.DataFrame]:
    result: Dict[str, pd.DataFrame] = {}
    for index in range(0, len(tickers), chunk_size):
        chunk = tickers[index:index + chunk_size]
        print(f"[INFO] NASDAQ download chunk {index // chunk_size + 1}: {len(chunk)}")
        result.update(download_ohlcv(chunk, start, end))
    return result


def select_top_by_adv(end_date: str) -> pd.DataFrame:
    listed = fetch_universe()
    listed_names = listed.set_index("ticker")["security_name"].to_dict()
    base_rows = [{"ticker": ticker, "security_name": listed_names.get(ticker, ticker)} for ticker in BASE_TICKERS]
    universe = pd.DataFrame(base_rows, columns=["ticker", "security_name"]).drop_duplicates("ticker")
    start = (date.fromisoformat(end_date) - timedelta(days=45)).isoformat()
    recent = download_in_chunks(universe["ticker"].tolist(), start, end_date, chunk_size=50)
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

    ohlcv = download_ohlcv(all_tickers, start_date(end_date, CFG.lookback_days), end_date)
    if CFG.benchmark_tickers[0] not in ohlcv or CFG.benchmark_tickers[1] not in ohlcv:
        raise RuntimeError("NASDAQ benchmark data is missing. Please retry later.")
    primary = add_technical_features(ohlcv[CFG.benchmark_tickers[0]])
    secondary = add_technical_features(ohlcv[CFG.benchmark_tickers[1]])
    market_bullish = market_regime_is_bullish(primary)

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
    candidates = build_candidates(scored, CFG, market_bullish) if not scored.empty else pd.DataFrame()
    return {
        "market": CFG.market,
        "run_date": end_date,
        "selected": selected,
        "scored": scored,
        "candidates": candidates,
        "market_bullish": market_bullish,
    }
