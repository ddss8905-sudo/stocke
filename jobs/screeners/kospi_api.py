import os
from datetime import date, timedelta
from typing import Dict, List

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
from .sectors import calculate_sector_strength, complete_domestic_sectors, fetch_kind_sectors, fetch_krx_sectors, filter_official_listing


OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]
SELECTED_COLUMNS = ["ticker", "security_name", "close", "adv"]
NAVER_COLUMNS = ["ticker", "security_name", "close", "volume", "value"]
EXCLUDE_NAME_KEYWORDS = [
    "ETF", "ETN",
    "KODEX", "TIGER", "ACE", "KBSTAR", "SOL", "HANARO", "KOSEF",
    "ARIRANG", "TIMEFOLIO", "PLUS", "RISE", "WON",
]

CFG = MarketConfig(
    market="KOSPI_API",
    lookback_days=800,
    universe_size=200,
    min_price=1_000.0,
    min_adv20=5_000_000_000.0,
    min_final_score=80.0,
    min_rs_rank=80.0,
    min_close_to_52w_high_ratio=0.80,
    entry_volume_multiplier=1.4,
    pullback_volume_multiplier=1.0,
    fixed_stop_pct=0.10,
    max_risk_to_stop=0.10,
    max_atr_pct=0.10,
    max_close_to_ma50_ratio=1.35,
    max_entry_extension_pct=0.05,
    stop_atr_multiple=2.5,
    structure_stop_atr_buffer=0.5,
    trailing_atr_multiple=3.0,
    min_market_regime_score=40.0,
    benchmark_tickers=["069500", "102110"],
)


def krx_date(value: str) -> str:
    return value.replace("-", "")


def resolve_latest_trading_date(end_date: str) -> str:
    return end_date


def is_excluded_name(name: str) -> bool:
    normalized = name.lower()
    return any(keyword.lower() in normalized for keyword in EXCLUDE_NAME_KEYWORDS)


def market_ohlcv_value(row: pd.Series, english_name: str, fallback_position: int) -> float:
    if english_name in row:
        return pd.to_numeric(row[english_name], errors="coerce")
    if len(row) > fallback_position:
        return pd.to_numeric(row.iloc[fallback_position], errors="coerce")
    return float("nan")


def fetch_naver_current_value() -> pd.DataFrame:
    rows = []
    seen = set()
    url = "https://stock.naver.com/api/stockSecurity/individual-stocks/v3/domestic"
    for page in range(15):
        try:
            response = requests.get(
                url,
                params={
                    "listingType": "tradingValueDesc",
                    "exchangeType": "krx",
                    "index": page,
                    "size": 100,
                },
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=30,
            )
            response.raise_for_status()
            payload = response.json()
            items = payload.get("items")
            if not isinstance(items, list):
                raise ValueError("Naver stock list returned no items")
        except Exception as exc:
            print(f"[WARN] failed to fetch Naver KOSPI page {page + 1}: {exc}")
            break

        for item in items:
            ticker = str(item.get("itemCode") or "")
            name = str(item.get("itemName") or "").strip()
            if item.get("marketType") != "KOSPI" or len(ticker) != 6 or not ticker.isdigit():
                continue
            if ticker in seen or not name or is_excluded_name(name):
                continue
            quote = item.get("krx") or {}
            close = parse_number(quote.get("currentPrice"))
            volume = parse_number(quote.get("tradingVolume"))
            trading_value = parse_number(quote.get("tradingValue"))
            if pd.isna(close) or pd.isna(trading_value) or close < CFG.min_price or trading_value <= 0:
                continue

            seen.add(ticker)
            rows.append({
                "ticker": ticker,
                "security_name": name,
                "close": float(close),
                "volume": float(volume) if not pd.isna(volume) else None,
                "value": float(trading_value),
            })
        if len(rows) >= CFG.universe_size + 20 or not payload.get("hasNext") or not items:
            break

    if not rows:
        return pd.DataFrame(columns=NAVER_COLUMNS)
    result = pd.DataFrame(rows, columns=NAVER_COLUMNS)
    result = result.sort_values("value", ascending=False).head(CFG.universe_size + 20).reset_index(drop=True)
    print(f"[INFO] Naver KOSPI trading-value listing: {len(result)} rows")
    return result


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

    def volume_rank(self) -> pd.DataFrame:
        response = requests.get(
            f"{self.base_url}/uapi/domestic-stock/v1/quotations/volume-rank",
            headers=self.headers("FHPST01710000"),
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_COND_SCR_DIV_CODE": "20171",
                "FID_INPUT_ISCD": "0000",
                "FID_DIV_CLS_CODE": "1",
                "FID_BLNG_CLS_CODE": "3",
                "FID_TRGT_CLS_CODE": "111111111",
                "FID_TRGT_EXLS_CLS_CODE": "0000000000",
                "FID_INPUT_PRICE_1": "",
                "FID_INPUT_PRICE_2": "",
                "FID_VOL_CNT": "",
                "FID_INPUT_DATE_1": "",
            },
            timeout=30,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("rt_cd") not in (None, "0"):
            print(f"[WARN] KIS volume-rank failed: {payload.get('msg1') or payload}")
            return pd.DataFrame(columns=SELECTED_COLUMNS)

        rows = []
        for row in payload.get("output") or []:
            ticker = row.get("mksc_shrn_iscd") or row.get("stck_shrn_iscd")
            name = row.get("hts_kor_isnm") or row.get("stck_kor_isnm") or ticker
            if not ticker or is_excluded_name(name):
                continue
            close = parse_number(row.get("stck_prpr"))
            trading_value = parse_number(row.get("acml_tr_pbmn"))
            if pd.isna(trading_value):
                trading_value = parse_number(row.get("avrg_vol") or row.get("vol_tnrt"))
            if pd.isna(trading_value) or pd.isna(close) or close < CFG.min_price:
                continue
            rows.append({"ticker": ticker, "security_name": name, "close": float(close), "adv": float(trading_value)})

        if not rows:
            return pd.DataFrame(columns=SELECTED_COLUMNS)
        return pd.DataFrame(rows, columns=SELECTED_COLUMNS).sort_values("adv", ascending=False).reset_index(drop=True)

    def stock_sector(self, ticker: str) -> str:
        response = requests.get(
            f"{self.base_url}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers=self.headers("FHKST01010100"),
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": ticker},
            timeout=15,
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("rt_cd") not in (None, "0"):
            return ""
        return str((payload.get("output") or {}).get("bstp_kor_isnm") or "").strip()

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


def parse_number(value) -> float:
    if value is None:
        return float("nan")
    text = str(value).replace(",", "").strip()
    if not text:
        return float("nan")
    return pd.to_numeric(text, errors="coerce")


def fetch_top_by_current_value(client: KisClient) -> pd.DataFrame:
    naver = fetch_naver_current_value()
    selected = naver.rename(columns={"value": "adv"})[SELECTED_COLUMNS]
    if len(selected) < CFG.universe_size:
        print(f"[WARN] Naver listed {len(selected)} KOSPI stocks; supplementing with KIS volume-rank.")
        kis_rank = client.volume_rank()
        print(f"[INFO] KIS volume-rank returned {len(kis_rank)} rows")
        selected = pd.concat([selected, kis_rank[SELECTED_COLUMNS]], ignore_index=True)

    combined = selected
    combined = combined.drop_duplicates(subset=["ticker"], keep="first")
    combined = combined.sort_values("adv", ascending=False).head(CFG.universe_size + 20).reset_index(drop=True)
    if len(combined) < min(150, CFG.universe_size):
        raise RuntimeError(f"KOSPI universe is too small to publish: {len(combined)}/{CFG.universe_size}")
    return combined


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
    selected = fetch_top_by_current_value(client)
    selected = filter_official_listing(selected, "KOSPI").head(CFG.universe_size).reset_index(drop=True)
    if len(selected) < min(150, CFG.universe_size):
        raise RuntimeError(f"KOSPI official listing is too small to publish: {len(selected)}/{CFG.universe_size}")
    sectors = fetch_kind_sectors("KOSPI")
    sectors = complete_domestic_sectors(selected["ticker"].tolist(), sectors, client)
    if len(sectors) < len(selected) * 0.8:
        sectors = {**fetch_krx_sectors(effective_end_date, "KOSPI"), **sectors}
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
            "sector_strength": pd.DataFrame(),
            "market_bullish": False,
            "market_regime_score": 0.0,
            "market_exposure": 0.0,
        }

    primary = add_technical_features(ohlcv[CFG.benchmark_tickers[0]])
    secondary = add_technical_features(ohlcv[CFG.benchmark_tickers[1]])

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
            row["sector_name"] = sectors.get(ticker)
            rows.append(row)

    print(
        "[INFO] KOSPI_API scoring "
        f"rows={len(rows)} skipped_short_history={skipped_short_history} skipped_liquidity={skipped_liquidity}"
    )

    scored = score_universe(pd.DataFrame(rows)) if rows else pd.DataFrame()
    market_regime = build_market_regime(primary, secondary, scored)
    candidates = build_candidates(scored, CFG, market_regime) if not scored.empty else pd.DataFrame()
    sector_strength = calculate_sector_strength(scored, ohlcv, primary)
    return {
        "market": CFG.market,
        "run_date": effective_end_date,
        "selected": selected,
        "scored": scored,
        "candidates": candidates,
        "sector_strength": sector_strength,
        "reversal_histories": ohlcv,
        "reversal_sectors": sectors,
        "chart_histories": {ticker: ohlcv[ticker] for ticker in tickers if ticker in ohlcv},
        "market_bullish": market_regime["market_bullish"],
        "market_regime_score": market_regime["score"],
        "market_exposure": market_regime["exposure"],
    }
