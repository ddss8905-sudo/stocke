from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List

import pandas as pd


@dataclass
class MarketConfig:
    market: str
    lookback_days: int
    universe_size: int
    min_price: float
    min_adv20: float
    min_final_score: float
    min_rs_rank: float
    min_close_to_52w_high_ratio: float
    entry_volume_multiplier: float
    fixed_stop_pct: float
    max_risk_to_stop: float
    benchmark_tickers: List[str]


WEIGHTS: Dict[str, float] = {
    "trend": 0.20,
    "rs": 0.20,
    "momentum": 0.15,
    "breakout": 0.15,
    "accumulation": 0.10,
    "vcp": 0.10,
    "fundamental_proxy": 0.05,
    "risk_liquidity": 0.05,
}


def start_date(end_date: str, lookback_days: int) -> str:
    return (date.fromisoformat(end_date) - timedelta(days=lookback_days)).isoformat()


def pct_rank(series: pd.Series) -> pd.Series:
    return series.rank(pct=True) * 100.0


def add_technical_features(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    x["ma20"] = x["close"].rolling(20).mean()
    x["ma50"] = x["close"].rolling(50).mean()
    x["ma150"] = x["close"].rolling(150).mean()
    x["ma200"] = x["close"].rolling(200).mean()
    x["high20"] = x["high"].rolling(20).max()
    x["high50"] = x["high"].rolling(50).max()
    x["high252"] = x["high"].rolling(252).max()

    tr = pd.concat([
        x["high"] - x["low"],
        (x["high"] - x["close"].shift(1)).abs(),
        (x["low"] - x["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    x["atr14"] = tr.rolling(14).mean()
    x["atr_pct"] = x["atr14"] / x["close"]
    x["trading_value"] = x["value"] if "value" in x.columns else x["close"] * x["volume"]
    x["adv20"] = x["trading_value"].rolling(20).mean()
    x["vol_ma50"] = x["volume"].rolling(50).mean()

    x["ret_1m"] = x["close"] / x["close"].shift(21) - 1
    x["ret_3m"] = x["close"] / x["close"].shift(63) - 1
    x["ret_6m"] = x["close"] / x["close"].shift(126) - 1
    x["ret_12m"] = x["close"] / x["close"].shift(252) - 1

    x["range10"] = (x["high"].rolling(10).max() - x["low"].rolling(10).min()) / x["close"]
    x["range20"] = (x["high"].rolling(20).max() - x["low"].rolling(20).min()) / x["close"]
    x["range50"] = (x["high"].rolling(50).max() - x["low"].rolling(50).min()) / x["close"]

    up = ((x["close"] > x["close"].shift(1)) & (x["volume"] > x["vol_ma50"] * 1.5)).astype(int)
    down = ((x["close"] < x["close"].shift(1)) & (x["volume"] > x["vol_ma50"] * 1.5)).astype(int)
    x["up_volume_days_20"] = up.rolling(20).sum()
    x["down_volume_days_20"] = down.rolling(20).sum()
    return x


def market_regime_is_bullish(df: pd.DataFrame) -> bool:
    x = df.copy()
    x["ma50"] = x["close"].rolling(50).mean()
    x["ma200"] = x["close"].rolling(200).mean()
    last = x.iloc[-1]
    return bool((last["close"] > last["ma50"]) and (last["ma50"] > last["ma200"]) and (last["close"] > last["ma200"]))


def latest_feature_row(ticker: str, name: str, df: pd.DataFrame, primary_benchmark: pd.DataFrame, secondary_benchmark: pd.DataFrame, cfg: MarketConfig) -> dict:
    if len(df) < 260 or len(primary_benchmark) < 260 or len(secondary_benchmark) < 260:
        return {}

    last = df.iloc[-1]
    trend_raw = (
        3 * (last["close"] > last["ma50"]) + 3 * (last["close"] > last["ma150"]) +
        3 * (last["close"] > last["ma200"]) + 3 * (last["ma50"] > last["ma150"]) +
        3 * (last["ma150"] > last["ma200"]) + 3 * (last["ma200"] > df["ma200"].iloc[-21]) +
        2 * (last["close"] > last["ma20"])
    )

    stock_6m = df["ret_6m"].iloc[-1]
    primary_6m = primary_benchmark["close"].iloc[-1] / primary_benchmark["close"].iloc[-126] - 1
    secondary_6m = secondary_benchmark["close"].iloc[-1] / secondary_benchmark["close"].iloc[-126] - 1
    rs_raw = 0.5 * (stock_6m - primary_6m) + 0.5 * (stock_6m - secondary_6m)

    momentum_raw = 0.15 * last["ret_1m"] + 0.25 * last["ret_3m"] + 0.30 * last["ret_6m"] + 0.30 * last["ret_12m"]
    if last["ret_1m"] > 0.5:
        momentum_raw *= 0.75
    if (last["close"] / last["ma50"]) > 1.35:
        momentum_raw *= 0.70

    breakout_raw = (
        4 * (last["close"] >= last["high20"] * 0.97) + 4 * (last["close"] >= last["high50"] * 0.95) +
        3 * (last["close"] >= last["high252"] * cfg.min_close_to_52w_high_ratio) + 2 * (last["close"] > last["ma20"]) +
        2 * (last["volume"] > last["vol_ma50"] * 1.3)
    )
    accumulation_raw = (
        3 * (last["up_volume_days_20"] >= 3) + 3 * (last["up_volume_days_20"] > last["down_volume_days_20"]) +
        2 * (last["adv20"] > cfg.min_adv20) + 2 * (last["close"] > last["ma20"])
    )
    vcp_raw = (
        3 * (last["range10"] < last["range20"]) + 3 * (last["range20"] < last["range50"]) +
        2 * (last["atr_pct"] < df["atr_pct"].rolling(50).mean().iloc[-1]) +
        2 * (df["volume"].rolling(10).mean().iloc[-1] < df["volume"].rolling(50).mean().iloc[-1])
    )
    fundamental_proxy_raw = 1 * (last["ret_12m"] > 0) + 1 * (last["close"] > last["ma200"]) + 1 * (last["ret_6m"] > 0)
    risk_liquidity_raw = 3 * (last["adv20"] > cfg.min_adv20) + 2 * (last["close"] > cfg.min_price)

    return {
        "ticker": ticker,
        "security_name": name,
        "close": float(last["close"]),
        "adv20": float(last["adv20"]),
        "atr_pct": float(last["atr_pct"]),
        "high50_prev": float(df["high50"].shift(1).iloc[-1]),
        "vol_ma50": float(last["vol_ma50"]),
        "volume": float(last["volume"]),
        "ma50": float(last["ma50"]),
        "ma200": float(last["ma200"]),
        "high252": float(last["high252"]),
        "close_to_52w_high_ratio": float(last["close"] / last["high252"]),
        "trend_raw": float(trend_raw),
        "rs_raw": float(rs_raw),
        "momentum_raw": float(momentum_raw),
        "breakout_raw": float(breakout_raw),
        "accumulation_raw": float(accumulation_raw),
        "vcp_raw": float(vcp_raw),
        "fundamental_proxy_raw": float(fundamental_proxy_raw),
        "risk_liquidity_raw": float(risk_liquidity_raw),
    }


def score_universe(features_df: pd.DataFrame) -> pd.DataFrame:
    df = features_df.copy()
    raw_cols = [
        "trend_raw", "rs_raw", "momentum_raw", "breakout_raw",
        "accumulation_raw", "vcp_raw", "fundamental_proxy_raw", "risk_liquidity_raw",
    ]
    for col in raw_cols:
        df[col.replace("_raw", "_score")] = pct_rank(df[col])

    df["final_score"] = (
        df["trend_score"] * WEIGHTS["trend"] +
        df["rs_score"] * WEIGHTS["rs"] +
        df["momentum_score"] * WEIGHTS["momentum"] +
        df["breakout_score"] * WEIGHTS["breakout"] +
        df["accumulation_score"] * WEIGHTS["accumulation"] +
        df["vcp_score"] * WEIGHTS["vcp"] +
        df["fundamental_proxy_score"] * WEIGHTS["fundamental_proxy"] +
        df["risk_liquidity_score"] * WEIGHTS["risk_liquidity"]
    )
    df["rs_rank"] = df["rs_score"]
    return df.sort_values("final_score", ascending=False).reset_index(drop=True)


def build_candidates(scored: pd.DataFrame, cfg: MarketConfig, market_bullish: bool) -> pd.DataFrame:
    x = scored.copy()
    cond = (
        (x["final_score"] >= cfg.min_final_score) &
        (x["close"] > x["ma50"]) &
        (x["close"] > x["ma200"]) &
        (x["close_to_52w_high_ratio"] >= cfg.min_close_to_52w_high_ratio) &
        (x["rs_rank"] >= cfg.min_rs_rank) &
        (x["adv20"] >= cfg.min_adv20) &
        market_bullish
    )

    candidates = x[cond].copy()
    candidates["entry_trigger"] = (
        (candidates["close"] > candidates["high50_prev"]) &
        (candidates["volume"] > candidates["vol_ma50"] * cfg.entry_volume_multiplier)
    )
    candidates["stop_price"] = candidates["close"] * (1 - cfg.fixed_stop_pct)
    candidates["risk_to_stop"] = (candidates["close"] - candidates["stop_price"]) / candidates["close"]
    candidates = candidates[candidates["risk_to_stop"] <= cfg.max_risk_to_stop].copy()
    return candidates.sort_values(["entry_trigger", "final_score"], ascending=[False, False])
