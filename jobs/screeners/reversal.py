"""Price-based early reversal screen; not a complete Neumann strategy."""

from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

from .common import MarketConfig, add_technical_features


def completed_histories(histories: Dict[str, pd.DataFrame], market: str,
                        now: Optional[pd.Timestamp] = None) -> Dict[str, pd.DataFrame]:
    timezone = "America/New_York" if market == "NASDAQ" else "Asia/Seoul"
    local = (now if now is not None else pd.Timestamp.now(tz="UTC")).tz_convert(timezone)
    # Conservative grace periods; Korean cutoff also covers the delayed CSAT close.
    cutoff_minutes = 16 * 60 + 15 if market == "NASDAQ" else 16 * 60 + 45
    include_today = local.hour * 60 + local.minute >= cutoff_minutes
    today = local.strftime("%Y-%m-%d")
    result = {}
    for ticker, history in histories.items():
        days = pd.to_datetime(history.index, errors="coerce").strftime("%Y-%m-%d")
        mask = days <= today if include_today else days < today
        result[ticker] = history.loc[mask].copy()
    return result


def clean_history(history: pd.DataFrame) -> pd.DataFrame:
    columns = ["open", "high", "low", "close", "volume"]
    if not all(column in history for column in columns):
        return pd.DataFrame()
    frame = history.copy()
    frame.index = pd.to_datetime(frame.index, errors="coerce")
    if frame.index.isna().any():
        return pd.DataFrame()
    frame = frame.sort_index()
    if frame.index.has_duplicates:
        return pd.DataFrame()
    values = frame[columns].apply(pd.to_numeric, errors="coerce")
    valid = np.isfinite(values).all(axis=1)
    valid &= (values[["open", "high", "low", "close"]] > 0).all(axis=1)
    valid &= (values.volume >= 0) & (values.high >= values[["open", "close", "low"]].max(axis=1))
    valid &= values.low <= values[["open", "close"]].min(axis=1)
    # Never silently bridge missing or corrupt bars in a trading-day trendline.
    if not valid.all():
        return pd.DataFrame()
    frame[columns] = values
    return frame.tail(504)


def descending_resistance(frame: pd.DataFrame) -> Optional[Tuple[int, int, float, np.ndarray]]:
    high = frame.high.to_numpy(dtype=float)
    n = len(high)
    # A pivot requires five subsequent bars, all strictly before today's signal.
    pivots = [i for i in range(5, n - 6) if high[i] == max(high[i - 5:i + 6])
              and high[i] > max(high[i - 5:i])]
    choices = []
    for second in pivots:
        if second < n - 84:
            continue
        for first in pivots:
            if second - first < 63 or first > n - 126:
                continue
            if high[second] > high[first] * 0.85:
                continue
            slope = (high[second] - high[first]) / (second - first)
            line = high[first] + slope * (np.arange(n) - first)
            if line[-1] <= 0:
                continue
            # Reject lines already broken before the current ten-bar setup.
            if np.any(frame.close.to_numpy()[second + 1:n - 10] > line[second + 1:n - 10] * 1.02):
                continue
            if np.mean(high[first:second + 1] <= line[first:second + 1] * 1.02) < 0.95:
                continue
            choices.append((second - first, second, first, slope, line))
    if not choices:
        return None
    # Prefer the longest supported resistance, not whichever produces a signal.
    _, second, first, slope, line = max(choices, key=lambda item: (item[0], item[1]))
    return first, second, slope, line


def analyze_reversal(ticker: str, name: str, history: pd.DataFrame, cfg: MarketConfig,
                     regime_score: float, sector: Optional[str] = None,
                     sector_score: Optional[float] = None) -> Optional[dict]:
    frame = clean_history(history)
    if len(frame) < 260:
        return None
    frame = add_technical_features(frame)
    last = frame.iloc[-1]
    if last.close < cfg.min_price or last.adv20 < cfg.min_adv20 or last.atr_pct > cfg.max_atr_pct:
        return None
    historical = frame.close.iloc[-126:-20].to_numpy(dtype=float)
    if np.polyfit(np.arange(len(historical)), np.log(historical), 1)[0] >= 0:
        return None
    resistance = descending_resistance(frame)
    if resistance is None:
        return None
    first, second, slope, line = resistance
    low = float(frame.low.iloc[-63:].min())
    decline = 1 - low / float(frame.high.iloc[first:second + 1].max())
    rebound = float(last.close / low - 1)
    higher_low = frame.low.iloc[-10:].min() >= frame.low.iloc[-30:-10].min() * 0.98
    if decline < 0.25 or not 0.04 <= rebound <= 0.40 or not higher_low:
        return None
    if last.close <= last.ma20 or last.ma20 <= frame.ma20.iloc[-6]:
        return None
    extension = float(last.close / line[-1] - 1)
    if not -0.05 <= extension <= 0.15:
        return None
    close = frame.close.to_numpy(dtype=float)
    crossed = (close[1:] >= line[1:] * 1.01) & (close[:-1] < line[:-1] * 1.01)
    recent_crosses = np.flatnonzero(crossed[-10:])
    above = close[-1] >= line[-1] * 1.01
    fresh = bool(crossed[-1])
    if above and not len(recent_crosses):
        return None
    previous_volume = float(frame.volume.iloc[-21:-1].mean())
    if previous_volume <= 0:
        return None
    volume_ratio = float(last.volume / previous_volume)
    stop = float(frame.low.iloc[-10:].min() - last.atr14 * 0.5)
    risk = float((last.close - stop) / last.close)
    if not 0 < risk < 1 or stop <= 0:
        return None
    volume_ok = volume_ratio >= 1.5
    strong_close = last.high > last.low and (last.close - last.low) / (last.high - last.low) >= 0.5
    trigger = bool(fresh and volume_ok and strong_close and extension <= 0.05
                   and risk <= 0.10 and regime_score >= 40)
    status = "preparing"
    if above:
        status = "confirmed" if trigger else "tracking" if not fresh else "volume_wait"
    if extension > 0.05:
        status = "extended"
    if risk > 0.10:
        status = "risk_high"
    if regime_score < 40:
        status = "market_wait"
    score = (min(decline / 0.5, 1) * 25 + 20 + min(max(extension + 0.05, 0) / 0.06, 1) * 25
             + min(volume_ratio / 1.5, 1) * 20 + (sector_score or 0) * 0.1)
    return {
        "ticker": ticker, "security_name": name, "sector_name": sector,
        "strategy": "reversal", "close": float(last.close), "adv20": float(last.adv20),
        "final_score": round(score, 1), "rs_rank": None, "trend_score": None,
        "momentum_score": None, "breakout_score": None, "accumulation_score": None, "vcp_score": None,
        "downtrend_days": len(frame) - 1 - first, "decline_pct": decline, "rebound_pct": rebound,
        "trendline_price": float(line[-1]), "volume_ratio": volume_ratio, "reversal_status": status,
        "trendline_anchors": [{"time": frame.index[i].strftime("%Y-%m-%d"), "price": float(frame.high.iloc[i])}
                              for i in (first, second)],
        "as_of": frame.index[-1].strftime("%Y-%m-%d"), "sector_score": sector_score,
        "entry_trigger": trigger, "buy_zone_low": float(line[-1] * 1.01),
        "buy_zone_high": float(line[-1] * 1.05), "stop_price": stop, "initial_stop_price": stop,
        "risk_to_stop": risk, "two_r_price": float(last.close + 2 * (last.close - stop)),
        "position_size_pct": min(0.0025 / risk, 0.05) if trigger else 0.0,
        "is_candidate": True, "entry_reason": "Fresh descending-resistance breakout; catalyst and share structure unverified.",
        "stop_basis": "Ten-session low minus 0.5 ATR; never clamped to a fixed risk.",
    }


def build_reversals(selected: pd.DataFrame, histories: Dict[str, pd.DataFrame], cfg: MarketConfig,
                    regime_score: float, sectors: Dict[str, str], strength: pd.DataFrame,
                    as_of: str) -> Tuple[pd.DataFrame, int]:
    sector_scores = {} if strength.empty else strength[strength.period_days == 21].set_index("sector_name")["score"].to_dict()
    rows, scanned = [], 0
    for stock in selected.to_dict("records"):
        ticker = stock["ticker"]
        history = histories.get(ticker)
        if history is None:
            continue
        history = history.copy()
        history.index = pd.to_datetime(history.index, errors="coerce")
        if history.index.isna().any():
            continue
        history = history.loc[history.index.strftime("%Y-%m-%d") <= as_of]
        clean = clean_history(history)
        # A stale quote is not a current reversal; never compare different dates.
        if len(clean) < 260 or clean.index[-1].strftime("%Y-%m-%d") != as_of:
            continue
        scanned += 1
        sector = sectors.get(ticker)
        row = analyze_reversal(ticker, stock.get("security_name", ""), clean, cfg, regime_score,
                               sector, sector_scores.get(sector))
        if row:
            rows.append(row)
    result = pd.DataFrame(rows)
    if not result.empty:
        result = result.sort_values(["entry_trigger", "final_score"], ascending=False).reset_index(drop=True)
    return result, scanned
