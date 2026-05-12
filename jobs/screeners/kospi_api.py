import os
from datetime import date, timedelta
from typing import Dict, List

import pandas as pd
import requests
from pykrx import stock

from .common import (
    MarketConfig,
    add_technical_features,
    build_candidates,
    latest_feature_row,
    market_regime_is_bullish,
    score_universe,
    start_date,
)


OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]
SELECTED_COLUMNS = ["ticker", "security_name", "close", "adv"]
EXCLUDE_NAME_KEYWORDS = [
    "ETF", "ETN",
    "KODEX", "TIGER", "ACE", "KBSTAR", "SOL", "HANARO", "KOSEF",
    "ARIRANG", "TIMEFOLIO", "PLUS", "RISE", "WON",
]

CFG = MarketConfig(
    market="KOSPI_API",
    lookback_days=420,
    universe_size=200,
    min_price=1_000.0,
    min_adv20=5_000_000_000.0,
    min_final_score=70.0,
    min_rs_rank=70.0,
    min_close_to_52w_high_ratio=0.80,
    entry_volume_multiplier=1.4,
    fixed_stop_pct=0.10,
    max_risk_to_stop=0.10,
    benchmark_tickers=["069500", "102110"],
)


def krx_date(value: str) -> str:
    return value.replace("-", "")


def resolve_latest_trading_date(end_date: str) -> str:
    current = date.fromisoformat(end_date)
    for _ in range(1095):
        try:
            df = stock.get_market_ohlcv_by_ticker(krx_date(current.isoformat()), market="KOSPI")
        except Exception:
            df = pd.DataFrame()
        if not df.empty:
            return current.isoformat()
        current = current - timedelta(days=1)
    raise RuntimeError(f"No available KRX trading date found before {end_date}.")


def is_excluded_name(name: str) -> bool:
    normalized = name.lower()
    return any(keyword.lower() in normalized for keyword in EXCLUDE_NAME_KEYWORDS)


def market_ohlcv_value(row: pd.Series, english_name: str, fallback_position: int) -> float:
    if english_name in row:
        return pd.to_numeric(row[english_name], errors="coerce")
    if len(row) > fallback_position:
        return pd.to_numeric(row.iloc[fallback_position], errors="coerce")
    return float("nan")


def fetch_top_by_current_value(end_date: str) -> pd.DataFrame:
    try:
        latest = stock.get_market_ohlcv_by_ticker(krx_date(end_date), market="KOSPI")
    except Exception:
        latest = pd.DataFrame()
    if latest.empty:
        return pd.DataFrame(columns=SELECTED_COLUMNS)

    rows = []
    for ticker, row in latest.iterrows():
        row = latest.loc[ticker]
        name = stock.get_market_ticker_name(ticker)
        if is_excluded_name(name):
            continue

        close = market_ohlcv_value(row, "close", 3)
        volume = market_ohlcv_value(row, "volume", 4)
        trading_value = market_ohlcv_value(row, "value", 5)
        if pd.isna(trading_value) and not pd.isna(close) and not pd.isna(volume):
            trading_value = close * volume
        if pd.isna(trading_value) or pd.isna(close) or close < CFG.min_price:
            continue
        rows.append({"ticker": ticker, "security_name": name, "close": float(close), "adv": float(trading_value)})

    if not rows:
        return pd.DataFrame(columns=SELECTED_COLUMNS)
    return pd.DataFrame(rows, columns=SELECTED_COLUMNS).sort_values("adv", ascending=False).head(CFG.universe_size).reset_index(drop=True)


class KisClient:
    def __init__(self) -> None:
        self.base_url = (os.environ.get("KIS_BASE_URL") or "https://openapi.koreainvestment.com:9443").rstrip("/")
        self.app_key = os.environ["KIS_APP_KEY"]
        self.app_secret = os.environ["KIS_APP_SECRET"]
        self.access_token = os.environ.get("KIS_ACCESS_TOKEN") or self.fetch_access_token()

    def fetch_access_token(self) -> str:
        response = requests.post(
            f"{self.base_url}/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": self.app_key,
                "appsecret": self.app_secret,
            },
            headers={"content-type": "application/json"},
            timeout=30,
        )
        response.raise_for_status()
        return response.json()["access_token"]

    def headers(self, tr_id: str) -> Dict[str, str]:
        return {
            "content-type": "application/json",
            "authorization": f"Bearer {self.access_token}",
            "appkey": self.app_key,
            "appsecret": self.app_secret,
            "tr_id": tr_id,
            "custtype": "P",
        }

    def daily_chart(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        start_dt = date.fromisoformat(start)
        end_dt = date.fromisoformat(end)
        frames = []

        chunk_start = start_dt
        while chunk_start <= end_dt:
            chunk_end = min(chunk_start + timedelta(days=89), end_dt)
            frames.append(self._daily_chart_chunk(ticker, chunk_start.isoformat(), chunk_end.isoformat()))
            chunk_start = chunk_end + timedelta(days=1)

        frames = [frame for frame in frames if not frame.empty]
        if not frames:
            return pd.DataFrame(columns=OHLCV_COLUMNS + ["value"])

        combined = pd.concat(frames).sort_index()
        combined = combined[~combined.index.duplicated(keep="last")]
        return combined[OHLCV_COLUMNS + ["value"]]

    def _daily_chart_chunk(self, ticker: str, start: str, end: str) -> pd.DataFrame:
        response = requests.get(
            f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
            headers=self.headers("FHKST03010100"),
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": ticker,
                "FID_INPUT_DATE_1": start.replace("-", ""),
                "FID_INPUT_DATE_2": end.replace("-", ""),
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        rows = payload.get("output2") or []
        if not rows:
            return pd.DataFrame(columns=OHLCV_COLUMNS + ["value"])

        df = pd.DataFrame(rows)
        normalized = pd.DataFrame({
            "open": to_number(df["stck_oprc"]).to_numpy(),
            "high": to_number(df["stck_hgpr"]).to_numpy(),
            "low": to_number(df["stck_lwpr"]).to_numpy(),
            "close": to_number(df["stck_clpr"]).to_numpy(),
            "volume": to_number(df["acml_vol"]).to_numpy(),
            "value": to_number(df["acml_tr_pbmn"] if "acml_tr_pbmn" in df else [None] * len(df)).to_numpy(),
        }, index=pd.to_datetime(df["stck_bsop_date"]).dt.date)
        normalized["value"] = normalized["value"].fillna(normalized["close"] * normalized["volume"])
        return normalized.sort_index()[OHLCV_COLUMNS + ["value"]].dropna(subset=OHLCV_COLUMNS)


def to_number(values) -> pd.Series:
    if values is None:
        return pd.Series(dtype="float64")
    return pd.to_numeric(pd.Series(values).astype(str).str.replace(",", "", regex=False), errors="coerce")


def download_ohlcv(tickers: List[str], start: str, end: str, client: KisClient) -> Dict[str, pd.DataFrame]:
    result: Dict[str, pd.DataFrame] = {}
    for ticker in sorted(set(tickers)):
        try:
            df = client.daily_chart(ticker, start, end)
        except Exception as exc:
            print(f"[WARN] failed to download {ticker} from KIS: {exc}")
            continue
        if len(df) > 0:
            result[ticker] = df
    return result


def retain_downloaded_universe(ohlcv: Dict[str, pd.DataFrame], universe: pd.DataFrame) -> pd.DataFrame:
    selected = universe[universe["ticker"].isin(ohlcv.keys())].copy()
    if selected.empty:
        print("[WARN] No KOSPI_API symbols were downloaded from KIS daily chart data.")
        return pd.DataFrame(columns=SELECTED_COLUMNS)
    return selected[SELECTED_COLUMNS].reset_index(drop=True)


def run(end_date: str) -> dict:
    client = KisClient()
    effective_end_date = resolve_latest_trading_date(end_date)
    selected = fetch_top_by_current_value(effective_end_date)
    tickers = selected["ticker"].tolist()
    names = selected.set_index("ticker")["security_name"].to_dict()
    all_tickers = sorted(set(tickers + CFG.benchmark_tickers))

    ohlcv = download_ohlcv(all_tickers, start_date(effective_end_date, CFG.lookback_days), effective_end_date, client)
    selected = retain_downloaded_universe(ohlcv, selected)
    tickers = selected["ticker"].tolist()
    names = selected.set_index("ticker")["security_name"].to_dict()
    print(f"[INFO] KOSPI_API downloaded={len(ohlcv)} selected={len(selected)}")

    if CFG.benchmark_tickers[0] not in ohlcv or CFG.benchmark_tickers[1] not in ohlcv:
        print("[WARN] KOSPI_API benchmark ETF data is missing from KIS.")
        return {
            "market": CFG.market,
            "run_date": effective_end_date,
            "selected": selected,
            "scored": pd.DataFrame(),
            "candidates": pd.DataFrame(),
            "market_bullish": False,
        }

    primary = add_technical_features(ohlcv[CFG.benchmark_tickers[0]])
    secondary = add_technical_features(ohlcv[CFG.benchmark_tickers[1]])
    market_bullish = market_regime_is_bullish(primary)

    rows = []
    skipped_short_history = 0
    skipped_liquidity = 0
    for ticker in tickers:
        if ticker not in ohlcv:
            continue
        df = add_technical_features(ohlcv[ticker])
        if len(df) < 260:
            skipped_short_history += 1
            continue
        last = df.iloc[-1]
        if last["close"] < CFG.min_price or last["adv20"] < CFG.min_adv20:
            skipped_liquidity += 1
            continue
        row = latest_feature_row(ticker, names.get(ticker, ""), df, primary, secondary, CFG)
        if row:
            rows.append(row)

    print(
        "[INFO] KOSPI_API scoring "
        f"rows={len(rows)} skipped_short_history={skipped_short_history} skipped_liquidity={skipped_liquidity}"
    )

    scored = score_universe(pd.DataFrame(rows)) if rows else pd.DataFrame()
    candidates = build_candidates(scored, CFG, market_bullish) if not scored.empty else pd.DataFrame()
    return {
        "market": CFG.market,
        "run_date": effective_end_date,
        "selected": selected,
        "scored": scored,
        "candidates": candidates,
        "market_bullish": market_bullish,
    }
