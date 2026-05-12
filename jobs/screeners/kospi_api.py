import os
from datetime import date, timedelta
from typing import Dict, List

import pandas as pd
import requests

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

KOSPI_UNIVERSE = [
    ("005930", "Samsung Electronics"),
    ("000660", "SK hynix"),
    ("373220", "LG Energy Solution"),
    ("207940", "Samsung Biologics"),
    ("005380", "Hyundai Motor"),
    ("000270", "Kia"),
    ("068270", "Celltrion"),
    ("035420", "NAVER"),
    ("005490", "POSCO Holdings"),
    ("051910", "LG Chem"),
    ("006400", "Samsung SDI"),
    ("028260", "Samsung C&T"),
    ("105560", "KB Financial Group"),
    ("055550", "Shinhan Financial Group"),
    ("012330", "Hyundai Mobis"),
    ("066570", "LG Electronics"),
    ("096770", "SK Innovation"),
    ("003670", "POSCO Future M"),
    ("017670", "SK Telecom"),
    ("032830", "Samsung Life"),
    ("402340", "SK Square"),
    ("000990", "DB HiTek"),
    ("011070", "LG Innotek"),
    ("353200", "Daeduck Electronics"),
    ("307950", "Hyundai Autoever"),
    ("336260", "Doosan Fuel Cell"),
    ("023530", "Lotte Shopping"),
    ("004710", "Hansol Technics"),
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


def select_top_by_adv(ohlcv: Dict[str, pd.DataFrame]) -> pd.DataFrame:
    names = dict(KOSPI_UNIVERSE)
    rows = []
    for ticker, df in ohlcv.items():
        if ticker in CFG.benchmark_tickers or len(df) < 20:
            continue
        tail = df.tail(20)
        fallback_value = tail["close"] * tail["volume"]
        trading_value = tail["value"].fillna(fallback_value) if "value" in tail.columns else fallback_value
        adv = trading_value.mean()
        last_close = tail["close"].iloc[-1]
        if pd.isna(adv) or pd.isna(last_close) or last_close < CFG.min_price:
            continue
        rows.append({"ticker": ticker, "security_name": names.get(ticker, ticker), "close": float(last_close), "adv": float(adv)})

    if not rows:
        print("[WARN] No KOSPI_API symbols were selected from KIS daily chart data.")
        return pd.DataFrame(columns=SELECTED_COLUMNS)

    return pd.DataFrame(rows, columns=SELECTED_COLUMNS).sort_values("adv", ascending=False).head(CFG.universe_size).reset_index(drop=True)


def run(end_date: str) -> dict:
    client = KisClient()
    start = start_date(end_date, CFG.lookback_days)
    universe_tickers = [ticker for ticker, _ in KOSPI_UNIVERSE]
    all_tickers = sorted(set(universe_tickers + CFG.benchmark_tickers))

    ohlcv = download_ohlcv(all_tickers, start, end_date, client)
    selected = select_top_by_adv(ohlcv)
    tickers = selected["ticker"].tolist()
    names = selected.set_index("ticker")["security_name"].to_dict()

    if CFG.benchmark_tickers[0] not in ohlcv or CFG.benchmark_tickers[1] not in ohlcv:
        print("[WARN] KOSPI_API benchmark ETF data is missing from KIS.")
        return {
            "market": CFG.market,
            "run_date": end_date,
            "selected": selected,
            "scored": pd.DataFrame(),
            "candidates": pd.DataFrame(),
            "market_bullish": False,
        }

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
