from dataclasses import dataclass
from datetime import date, timedelta
from typing import Dict, List, Optional

import numpy as np
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
    pullback_volume_multiplier: float
    fixed_stop_pct: float
    max_risk_to_stop: float
    max_atr_pct: float
    max_close_to_ma50_ratio: float
    max_entry_extension_pct: float
    stop_atr_multiple: float
    structure_stop_atr_buffer: float
    trailing_atr_multiple: float
    min_market_regime_score: float
    benchmark_tickers: List[str]


WEIGHTS: Dict[str, float] = {
    "trend": 0.17,
    "rs": 0.18,
    "momentum": 0.12,
    "breakout": 0.13,
    "accumulation": 0.08,
    "vcp": 0.09,
    "trend_template": 0.12,
    "setup_quality": 0.07,
    "fundamental_proxy": 0.01,
    "risk_liquidity": 0.03,
}


def start_date(end_date: str, lookback_days: int) -> str:
    return (date.fromisoformat(end_date) - timedelta(days=lookback_days)).isoformat()


def pct_rank(series: pd.Series) -> pd.Series:
    return series.rank(pct=True) * 100.0


def safe_ratio(numerator: object, denominator: object) -> float:
    try:
        top = float(numerator)
        bottom = float(denominator)
    except (TypeError, ValueError):
        return float("nan")
    if not np.isfinite(top) or not np.isfinite(bottom) or bottom == 0:
        return float("nan")
    return top / bottom


def add_technical_features(df: pd.DataFrame) -> pd.DataFrame:
    x = df.copy()
    x["ma20"] = x["close"].rolling(20).mean()
    x["ma50"] = x["close"].rolling(50).mean()
    x["ma150"] = x["close"].rolling(150).mean()
    x["ma200"] = x["close"].rolling(200).mean()
    x["high20"] = x["high"].rolling(20).max()
    x["high50"] = x["high"].rolling(50).max()
    x["high55"] = x["high"].rolling(55).max()
    x["high252"] = x["high"].rolling(252).max()
    x["low10"] = x["low"].rolling(10).min()
    x["low20"] = x["low"].rolling(20).min()
    x["low50"] = x["low"].rolling(50).min()
    x["low252"] = x["low"].rolling(252).min()

    tr = pd.concat([
        x["high"] - x["low"],
        (x["high"] - x["close"].shift(1)).abs(),
        (x["low"] - x["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    close_for_ratio = x["close"].replace(0, np.nan)
    x["atr14"] = tr.rolling(14).mean()
    x["atr_pct"] = x["atr14"] / close_for_ratio
    x["trading_value"] = x["value"] if "value" in x.columns else x["close"] * x["volume"]
    x["adv20"] = x["trading_value"].rolling(20).mean()
    x["vol_ma50"] = x["volume"].rolling(50).mean()

    x["ret_1m"] = x["close"] / x["close"].shift(21).replace(0, np.nan) - 1
    x["ret_3m"] = x["close"] / x["close"].shift(63).replace(0, np.nan) - 1
    x["ret_6m"] = x["close"] / x["close"].shift(126).replace(0, np.nan) - 1
    x["ret_12m"] = x["close"] / x["close"].shift(252).replace(0, np.nan) - 1

    x["range10"] = (x["high"].rolling(10).max() - x["low"].rolling(10).min()) / close_for_ratio
    x["range20"] = (x["high"].rolling(20).max() - x["low"].rolling(20).min()) / close_for_ratio
    x["range50"] = (x["high"].rolling(50).max() - x["low"].rolling(50).min()) / close_for_ratio

    up = ((x["close"] > x["close"].shift(1)) & (x["volume"] > x["vol_ma50"] * 1.5)).astype(int)
    down = ((x["close"] < x["close"].shift(1)) & (x["volume"] > x["vol_ma50"] * 1.5)).astype(int)
    x["up_volume_days_20"] = up.rolling(20).sum()
    x["down_volume_days_20"] = down.rolling(20).sum()
    return x


def market_regime_is_bullish(df: pd.DataFrame) -> bool:
    return bool(build_market_regime(df)["market_bullish"])


def build_market_regime(primary: pd.DataFrame, secondary: Optional[pd.DataFrame] = None, scored: Optional[pd.DataFrame] = None) -> dict:
    x = primary.copy()
    if "ma50" not in x.columns or "ma200" not in x.columns:
        x["ma50"] = x["close"].rolling(50).mean()
        x["ma200"] = x["close"].rolling(200).mean()

    if len(x) < 200:
        return {"score": 0.0, "exposure": 0.0, "market_bullish": False, "breadth_above_ma50": None, "breadth_above_ma200": None}

    last = x.iloc[-1]
    score = 0.0
    score += 20.0 * bool(last["close"] > last["ma200"])
    score += 15.0 * bool(last["ma50"] > last["ma200"])
    score += 15.0 * bool(last["close"] > last["ma50"])
    score += 10.0 * bool(len(x) >= 22 and last["close"] > x["close"].iloc[-22])

    if secondary is not None and len(secondary) >= 200:
        y = secondary.copy()
        if "ma50" not in y.columns or "ma200" not in y.columns:
            y["ma50"] = y["close"].rolling(50).mean()
            y["ma200"] = y["close"].rolling(200).mean()
        secondary_last = y.iloc[-1]
        score += 10.0 * bool(secondary_last["close"] > secondary_last["ma200"])
        score += 5.0 * bool(secondary_last["close"] > secondary_last["ma50"])

    breadth_above_ma50 = None
    breadth_above_ma200 = None
    if scored is not None and not scored.empty and {"close", "ma50", "ma200"}.issubset(scored.columns):
        breadth_above_ma50 = float((scored["close"] > scored["ma50"]).mean())
        breadth_above_ma200 = float((scored["close"] > scored["ma200"]).mean())
        score += 7.5 * min(max(breadth_above_ma50 / 0.60, 0.0), 1.0)
        score += 7.5 * min(max(breadth_above_ma200 / 0.55, 0.0), 1.0)

    exposure = 1.0 if score >= 70.0 else 0.5 if score >= 55.0 else 0.25 if score >= 40.0 else 0.0
    return {
        "score": round(float(score), 2),
        "exposure": exposure,
        "market_bullish": score >= 70.0,
        "breadth_above_ma50": breadth_above_ma50,
        "breadth_above_ma200": breadth_above_ma200,
    }


def latest_feature_row(ticker: str, name: str, df: pd.DataFrame, primary_benchmark: pd.DataFrame, secondary_benchmark: pd.DataFrame, cfg: MarketConfig) -> dict:
    if len(df) < 260 or len(primary_benchmark) < 260 or len(secondary_benchmark) < 260:
        return {}

    last = df.iloc[-1]
    prev = df.iloc[-2]
    high20_prev = df["high20"].shift(1).iloc[-1]
    high50_prev = df["high50"].shift(1).iloc[-1]
    high55_prev = df["high55"].shift(1).iloc[-1]
    low20_prev = df["low20"].shift(1).iloc[-1]
    low50_prev = df["low50"].shift(1).iloc[-1]
    ma200_1m_ago = df["ma200"].iloc[-21]
    volume_ma10 = df["volume"].rolling(10).mean().iloc[-1]
    atr_mean50 = df["atr_pct"].rolling(50).mean().iloc[-1]
    close_to_52w_high_ratio = safe_ratio(last["close"], last["high252"])
    close_to_52w_low_ratio = safe_ratio(last["close"], last["low252"])
    close_to_ma50_ratio = safe_ratio(last["close"], last["ma50"])
    base_depth_pct = safe_ratio(last["high50"] - last["low50"], last["high50"])
    volume_dry_up_ratio = safe_ratio(volume_ma10, last["vol_ma50"])
    donchian20_breakout = bool(last["close"] > high20_prev)
    donchian55_breakout = bool(last["close"] > high55_prev)
    trend_template_checks = [
        last["close"] > last["ma50"],
        last["close"] > last["ma150"],
        last["close"] > last["ma200"],
        last["ma50"] > last["ma150"],
        last["ma150"] > last["ma200"],
        last["ma200"] > ma200_1m_ago,
        close_to_52w_low_ratio >= 1.30,
        close_to_52w_high_ratio >= cfg.min_close_to_52w_high_ratio,
    ]
    trend_template_raw = sum(int(bool(check)) for check in trend_template_checks)
    trend_template_pass = trend_template_raw == len(trend_template_checks)

    trend_raw = (
        3 * (last["close"] > last["ma50"]) + 3 * (last["close"] > last["ma150"]) +
        3 * (last["close"] > last["ma200"]) + 3 * (last["ma50"] > last["ma150"]) +
        3 * (last["ma150"] > last["ma200"]) + 3 * (last["ma200"] > ma200_1m_ago) +
        2 * (last["close"] > last["ma20"])
    )

    stock_6m = df["ret_6m"].iloc[-1]
    primary_6m = safe_ratio(primary_benchmark["close"].iloc[-1], primary_benchmark["close"].iloc[-126]) - 1
    secondary_6m = safe_ratio(secondary_benchmark["close"].iloc[-1], secondary_benchmark["close"].iloc[-126]) - 1
    rs_raw = 0.5 * (stock_6m - primary_6m) + 0.5 * (stock_6m - secondary_6m)

    momentum_raw = 0.15 * last["ret_1m"] + 0.25 * last["ret_3m"] + 0.30 * last["ret_6m"] + 0.30 * last["ret_12m"]
    if last["ret_1m"] > 0.5:
        momentum_raw *= 0.75
    if close_to_ma50_ratio > cfg.max_close_to_ma50_ratio:
        momentum_raw *= 0.70

    breakout_raw = (
        4 * (last["close"] >= last["high20"] * 0.97) + 4 * (last["close"] >= last["high50"] * 0.95) +
        3 * (last["close"] >= last["high252"] * cfg.min_close_to_52w_high_ratio) + 2 * (last["close"] > last["ma20"]) +
        2 * (last["volume"] > last["vol_ma50"] * 1.3) +
        2 * donchian20_breakout + 2 * donchian55_breakout
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
    setup_quality_raw = (
        3 * trend_template_pass +
        2 * (base_depth_pct <= 0.35) +
        2 * (volume_dry_up_ratio <= 0.75) +
        2 * (donchian20_breakout or last["close"] >= high20_prev * 0.98) +
        1 * (last["atr_pct"] < atr_mean50)
    )
    fundamental_proxy_raw = 1 * (last["ret_12m"] > 0) + 1 * (last["close"] > last["ma200"]) + 1 * (last["ret_6m"] > 0)
    risk_liquidity_raw = 3 * (last["adv20"] > cfg.min_adv20) + 2 * (last["close"] > cfg.min_price)

    return {
        "ticker": ticker,
        "security_name": name,
        "close": float(last["close"]),
        "close_prev": float(prev["close"]),
        "high": float(last["high"]),
        "low": float(last["low"]),
        "adv20": float(last["adv20"]),
        "atr14": float(last["atr14"]),
        "atr_pct": float(last["atr_pct"]),
        "high20_prev": float(high20_prev),
        "high50_prev": float(high50_prev),
        "high55_prev": float(high55_prev),
        "low20_prev": float(low20_prev),
        "low50_prev": float(low50_prev),
        "vol_ma50": float(last["vol_ma50"]),
        "volume": float(last["volume"]),
        "ma20": float(last["ma20"]),
        "ma50": float(last["ma50"]),
        "ma150": float(last["ma150"]),
        "ma200": float(last["ma200"]),
        "high252": float(last["high252"]),
        "low252": float(last["low252"]),
        "close_to_52w_high_ratio": float(close_to_52w_high_ratio),
        "close_to_52w_low_ratio": float(close_to_52w_low_ratio),
        "close_to_ma50_ratio": float(close_to_ma50_ratio),
        "base_depth_pct": float(base_depth_pct),
        "volume_dry_up_ratio": float(volume_dry_up_ratio),
        "trend_template_pass": bool(trend_template_pass),
        "donchian20_breakout": bool(donchian20_breakout),
        "donchian55_breakout": bool(donchian55_breakout),
        "trend_raw": float(trend_raw),
        "rs_raw": float(rs_raw),
        "momentum_raw": float(momentum_raw),
        "breakout_raw": float(breakout_raw),
        "accumulation_raw": float(accumulation_raw),
        "vcp_raw": float(vcp_raw),
        "trend_template_raw": float(trend_template_raw),
        "setup_quality_raw": float(setup_quality_raw),
        "fundamental_proxy_raw": float(fundamental_proxy_raw),
        "risk_liquidity_raw": float(risk_liquidity_raw),
    }


def score_universe(features_df: pd.DataFrame) -> pd.DataFrame:
    df = features_df.copy()
    raw_cols = [
        "trend_raw", "rs_raw", "momentum_raw", "breakout_raw",
        "accumulation_raw", "vcp_raw", "trend_template_raw", "setup_quality_raw",
        "fundamental_proxy_raw", "risk_liquidity_raw",
    ]
    for col in raw_cols:
        if col not in df.columns:
            df[col] = 0.0
        df[col.replace("_raw", "_score")] = pct_rank(df[col])

    df["final_score"] = (
        df["trend_score"] * WEIGHTS["trend"] +
        df["rs_score"] * WEIGHTS["rs"] +
        df["momentum_score"] * WEIGHTS["momentum"] +
        df["breakout_score"] * WEIGHTS["breakout"] +
        df["accumulation_score"] * WEIGHTS["accumulation"] +
        df["vcp_score"] * WEIGHTS["vcp"] +
        df["trend_template_score"] * WEIGHTS["trend_template"] +
        df["setup_quality_score"] * WEIGHTS["setup_quality"] +
        df["fundamental_proxy_score"] * WEIGHTS["fundamental_proxy"] +
        df["risk_liquidity_score"] * WEIGHTS["risk_liquidity"]
    )
    df["rs_rank"] = df["rs_score"]
    return df.sort_values("final_score", ascending=False).reset_index(drop=True)


def build_candidates(scored: pd.DataFrame, cfg: MarketConfig, market_regime: object) -> pd.DataFrame:
    regime = (
        market_regime
        if isinstance(market_regime, dict)
        else {"score": 100.0 if market_regime else 0.0, "exposure": 1.0 if market_regime else 0.0}
    )
    regime_score = float(regime.get("score") or 0.0)
    regime_exposure = float(regime.get("exposure") or 0.0)

    x = scored.copy()
    if "trend_template_pass" not in x.columns:
        x["trend_template_pass"] = True
    if "base_depth_pct" not in x.columns:
        x["base_depth_pct"] = np.nan

    cond = (
        (x["final_score"] >= cfg.min_final_score) &
        (x["close"] > x["ma50"]) &
        (x["close"] > x["ma200"]) &
        (x["trend_template_pass"]) &
        (x["close_to_52w_high_ratio"] >= cfg.min_close_to_52w_high_ratio) &
        (x["close_to_ma50_ratio"] <= cfg.max_close_to_ma50_ratio) &
        (x["base_depth_pct"].isna() | (x["base_depth_pct"] <= 0.45)) &
        (x["rs_rank"] >= cfg.min_rs_rank) &
        (x["adv20"] >= cfg.min_adv20) &
        (x["atr_pct"] <= cfg.max_atr_pct) &
        (regime_score >= cfg.min_market_regime_score)
    )

    candidates = x[cond].copy()
    candidates["market_regime_score"] = regime_score
    candidates["market_exposure"] = regime_exposure
    candidates["entry_pivot"] = candidates["high50_prev"]
    candidates["buy_zone_low"] = candidates["entry_pivot"]
    candidates["buy_zone_high"] = candidates["entry_pivot"] * (1 + cfg.max_entry_extension_pct)
    candidates["entry_extension_pct"] = (candidates["close"] - candidates["entry_pivot"]) / candidates["entry_pivot"]
    donchian_breakout = candidates.get("donchian20_breakout", False) | candidates.get("donchian55_breakout", False)
    candidates["breakout_entry"] = (
        (candidates["entry_extension_pct"] >= 0) &
        (candidates["entry_extension_pct"] <= cfg.max_entry_extension_pct) &
        (donchian_breakout | (candidates["close"] >= candidates["entry_pivot"])) &
        (candidates["volume"] > candidates["vol_ma50"] * cfg.entry_volume_multiplier)
    )
    candidates["pullback_entry"] = (
        (candidates["close"] > candidates["ma20"]) &
        (candidates["low"] <= candidates["ma20"] * 1.02) &
        (candidates["close"] > candidates["close_prev"]) &
        (candidates["volume"] > candidates["vol_ma50"] * cfg.pullback_volume_multiplier)
    )
    candidates["entry_trigger"] = candidates["breakout_entry"] | candidates["pullback_entry"]
    candidates["extended_watch"] = candidates["entry_extension_pct"] > cfg.max_entry_extension_pct
    candidates["entry_setup"] = np.select(
        [candidates["breakout_entry"], candidates["pullback_entry"], candidates["extended_watch"]],
        ["breakout", "pullback", "extended_watch"],
        default="watchlist",
    )
    candidates["entry_signal"] = np.select(
        [candidates["breakout_entry"], candidates["pullback_entry"], candidates["extended_watch"]],
        ["buy_breakout", "buy_pullback", "wait_extended"],
        default="watch_setup",
    )
    candidates["entry_reason"] = np.select(
        [candidates["breakout_entry"], candidates["pullback_entry"], candidates["extended_watch"]],
        [
            "Breakout through pivot with volume while trend template is intact.",
            "Pullback reclaimed short-term support while trend template is intact.",
            "Trend is strong but price is extended beyond the planned buy zone.",
        ],
        default="Trend template passed; wait for breakout volume or a controlled pullback.",
    )

    breakout_stop = candidates["entry_pivot"] - candidates["atr14"] * cfg.structure_stop_atr_buffer
    pullback_support = pd.concat([candidates["ma20"], candidates["low20_prev"]], axis=1).max(axis=1)
    pullback_stop = pullback_support - candidates["atr14"] * cfg.structure_stop_atr_buffer
    structure_stop = breakout_stop.where(candidates["breakout_entry"], pullback_stop)
    volatility_stop = candidates["close"] - candidates["atr14"] * cfg.stop_atr_multiple
    candidates["stop_price"] = pd.concat([structure_stop, volatility_stop], axis=1).max(axis=1)
    candidates["stop_basis"] = np.where(candidates["breakout_entry"], "breakout_pivot_atr", "support_atr")
    fallback_stop = candidates["close"] * (1 - cfg.fixed_stop_pct)
    invalid_stop = candidates["stop_price"].isna() | (candidates["stop_price"] <= 0) | (candidates["stop_price"] >= candidates["close"])
    candidates.loc[invalid_stop, "stop_price"] = fallback_stop[invalid_stop]
    candidates.loc[invalid_stop, "stop_basis"] = "fixed_fallback"
    candidates["risk_to_stop"] = (candidates["close"] - candidates["stop_price"]) / candidates["close"]
    candidates = candidates[candidates["risk_to_stop"] <= cfg.max_risk_to_stop].copy()
    candidates["initial_stop_price"] = candidates["stop_price"]
    candidates["sell_watch_price"] = candidates["ma20"]
    candidates["trend_exit_price"] = candidates["ma50"]
    candidates["two_r_price"] = candidates["close"] + 2 * (candidates["close"] - candidates["stop_price"])
    risk_nonzero = candidates["risk_to_stop"].replace(0, np.nan)
    candidates["position_size_pct"] = (0.005 / risk_nonzero).clip(upper=0.25)
    candidates["exit_plan"] = candidates.apply(
        lambda row: (
            f"Hard stop {row['initial_stop_price']:.2f}; trim/watch below MA20 "
            f"{row['sell_watch_price']:.2f}; exit on MA50 break {row['trend_exit_price']:.2f}; "
            f"after 2R {row['two_r_price']:.2f}, trail with ATR/MA50."
        ),
        axis=1,
    )
    return candidates.sort_values(["entry_trigger", "final_score"], ascending=[False, False])


def evaluate_position_exit(df: pd.DataFrame, entry_price: float, initial_stop_price: float, highest_high: float, cfg: MarketConfig, market_regime: Optional[dict] = None) -> dict:
    x = add_technical_features(df)
    if len(x) < 50:
        return {"exit_action": "insufficient_data"}

    last = x.iloc[-1]
    current_highest_high = max(float(highest_high or 0), float(x["high"].max()))
    initial_risk = max(float(entry_price) - float(initial_stop_price), 0.0)
    r_multiple = ((float(last["close"]) - float(entry_price)) / initial_risk) if initial_risk > 0 else 0.0
    atr_trailing_stop = current_highest_high - float(last["atr14"]) * cfg.trailing_atr_multiple
    ma_trailing_stop = float(last["ma50"]) - float(last["atr14"]) * cfg.structure_stop_atr_buffer
    trailing_stop = max(float(initial_stop_price), atr_trailing_stop if r_multiple >= 2.0 else float(initial_stop_price), ma_trailing_stop if r_multiple >= 2.0 else float(initial_stop_price))

    regime_exposure = float((market_regime or {}).get("exposure", 1.0))
    if float(last["close"]) <= float(initial_stop_price):
        action = "hard_exit"
        reason = "initial_stop"
    elif r_multiple >= 2.0 and float(last["close"]) <= trailing_stop:
        action = "hard_exit"
        reason = "trailing_stop"
    elif float(last["close"]) < float(last["ma50"]):
        action = "hard_exit"
        reason = "ma50_break"
    elif float(last["close"]) < float(last["ma20"]) or regime_exposure < 0.5:
        action = "trim_or_watch"
        reason = "ma20_or_regime_weakness"
    else:
        action = "hold"
        reason = "trend_intact"

    return {
        "exit_action": action,
        "exit_reason": reason,
        "last_close": float(last["close"]),
        "highest_high": current_highest_high,
        "initial_stop_price": float(initial_stop_price),
        "trailing_stop_price": float(trailing_stop),
        "r_multiple": float(r_multiple),
    }
