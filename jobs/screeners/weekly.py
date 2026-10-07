"""Completed weekly OHLCV screens, sharing daily data acquisition only."""

from dataclasses import replace
from typing import Dict, Optional

import numpy as np
import pandas as pd

from .common import MarketConfig, score_universe
from .reversal import WEEKLY_PERIODS, analyze_reversal, clean_history
from .sectors import calculate_sector_strength


def completed_week_end(market: str, now: Optional[pd.Timestamp] = None) -> pd.Timestamp:
    timezone = "America/New_York" if market == "NASDAQ" else "Asia/Seoul"
    local = (now if now is not None else pd.Timestamp.now(tz="UTC")).tz_convert(timezone)
    today = local.tz_localize(None).normalize()
    friday = today + pd.Timedelta(days=4 - today.weekday())
    cutoff = 16 * 60 + (15 if market == "NASDAQ" else 45)
    if local.weekday() < 4 or (local.weekday() == 4 and local.hour * 60 + local.minute < cutoff):
        friday -= pd.Timedelta(days=7)
    return friday


def weekly_history(history: pd.DataFrame, week_end: pd.Timestamp) -> pd.DataFrame:
    history = history.copy()
    history.index = pd.to_datetime(history.index, errors="coerce")
    if history.index.isna().any():
        return pd.DataFrame()
    # Trim before the history cap so new intraweek bars cannot shift old anchors.
    daily = clean_history(history.loc[history.index <= week_end])
    if daily.empty:
        return pd.DataFrame()
    daily = daily.loc[daily.index <= week_end].copy()
    if daily.empty:
        return pd.DataFrame()
    value = daily["value"] if "value" in daily else daily.close * daily.volume
    daily["daily_adv20"] = value.rolling(20).mean()
    daily["price_date"] = daily.index.strftime("%Y-%m-%d")
    aggregation = {"open": "first", "high": "max", "low": "min", "close": "last",
                   "volume": "sum", "daily_adv20": "last", "price_date": "last"}
    if "value" in daily:
        aggregation["value"] = "sum"
    weekly = daily.resample("W-FRI").agg(aggregation).dropna(subset=["close"])
    # The first input week may start midweek; never use its partial volume/price range.
    first_week = daily.index[0].to_period("W-FRI").end_time.normalize()
    return weekly.loc[(weekly.index > first_week) & (weekly.index <= week_end)]


def weekly_regime(benchmark: pd.DataFrame) -> dict:
    if len(benchmark) < 40:
        return {"score": 0.0, "exposure": 0.0}
    close = benchmark.close
    above = close.iloc[-1] > close.rolling(40).mean().iloc[-1]
    strong = above and close.rolling(10).mean().iloc[-1] > close.rolling(40).mean().iloc[-1]
    exposure = 0.8 if strong else 0.4 if above else 0.0
    return {"score": exposure * 100, "exposure": exposure}


def weekly_trend_row(ticker: str, name: str, frame: pd.DataFrame, cfg: MarketConfig, sector: Optional[str]) -> dict:
    x = frame.copy()
    for alias, weeks in [(20, 4), (50, 10), (150, 30), (200, 40)]:
        x[f"ma{alias}"] = x.close.rolling(weeks).mean()
    tr = pd.concat([x.high - x.low, (x.high - x.close.shift()).abs(),
                    (x.low - x.close.shift()).abs()], axis=1).max(axis=1)
    last = x.iloc[-1]
    atr = float(tr.rolling(14).mean().iloc[-1])
    high11 = float(x.high.iloc[-12:-1].max())
    high4 = float(x.high.iloc[-5:-1].max())
    low10, high10 = float(x.low.iloc[-10:].min()), float(x.high.iloc[-10:].max())
    checks = [last.close > last.ma200, last.ma50 > last.ma200,
              last.ma200 > x.ma200.iloc[-5], x.close.iloc[-5] > x.close.iloc[-27]]
    momentum = [float(x.close.iloc[-5] / x.close.iloc[-weeks - 1] - 1) for weeks in (13, 26, 52)]
    volume_mean = float(x.volume.iloc[-11:-1].mean())
    breakout = bool(last.close > high11)
    return {
        "ticker": ticker, "security_name": name, "sector_name": sector,
        "strategy": "trend", "timeframe": "weekly", "as_of": x.index[-1].strftime("%Y-%m-%d"),
        "close": float(last.close), "adv20": float(last.daily_adv20), "atr14": atr,
        "atr_pct": atr / last.close, "ma20": float(last.ma20), "ma50": float(last.ma50),
        "ma150": float(last.ma150), "ma200": float(last.ma200),
        "high20_prev": high4, "high55_prev": high11, "low252": float(x.low.iloc[-52:].min()),
        "close_to_52w_high_ratio": float(last.close / x.high.iloc[-52:].max()),
        "close_to_ma50_ratio": float(last.close / last.ma50),
        "base_depth_pct": (high10 - low10) / high10,
        "trend_template_pass": all(checks), "donchian20_breakout": bool(last.close > high4),
        "donchian55_breakout": breakout,
        "_prior_breakout": bool(x.close.iloc[-2] > x.high.iloc[-13:-2].max()),
        "_momentum_3m_skip_1m": momentum[0], "_momentum_6m_skip_1m": momentum[1],
        "_momentum_12m_skip_1m": momentum[2],
        "trend_raw": sum(int(check) for check in checks),
        "breakout_raw": float(last.close / high11),
        "volume_ratio": float(last.volume / volume_mean) if volume_mean > 0 else 0.0,
        "_structure_stop": float(x.low.iloc[-2:].min() - 0.5 * atr),
        "entry_trigger": False, "stop_price": None, "risk_to_stop": None, "is_candidate": False,
    }


def weekly_candidates(scored: pd.DataFrame, cfg: MarketConfig, regime: dict) -> pd.DataFrame:
    if scored.empty:
        return pd.DataFrame()
    eligible = ((scored.final_score >= cfg.min_final_score) & (scored.rs_rank >= cfg.min_rs_rank)
                & scored.trend_template_pass & (scored.close_to_ma50_ratio <= cfg.max_close_to_ma50_ratio)
                & (scored.base_depth_pct <= 0.45) & (scored.adv20 >= cfg.min_adv20)
                & (scored.close >= cfg.min_price) & (scored.atr_pct <= 0.20)
                & (regime["score"] >= cfg.min_market_regime_score))
    x = scored.loc[eligible].copy()
    if x.empty:
        return x
    x["stop_price"] = x["_structure_stop"]
    x["risk_to_stop"] = (x.close - x.stop_price) / x.close
    x = x.loc[(x.stop_price > 0) & (x.risk_to_stop > 0) & (x.risk_to_stop < 1)].copy()
    x["entry_pivot"] = x.high55_prev
    x["buy_zone_low"] = x.entry_pivot
    x["buy_zone_high"] = x.entry_pivot * (1 + cfg.max_entry_extension_pct)
    x["entry_extension_pct"] = x.close / x.entry_pivot - 1
    fresh = x.donchian55_breakout & ~x["_prior_breakout"]
    x["entry_trigger"] = (fresh & (x.volume_ratio >= cfg.entry_volume_multiplier)
                          & x.entry_extension_pct.between(0, cfg.max_entry_extension_pct)
                          & (x.risk_to_stop <= cfg.max_risk_to_stop))
    excess = x.risk_to_stop > cfg.max_risk_to_stop
    extended = x.entry_extension_pct > cfg.max_entry_extension_pct
    x["entry_signal"] = np.select([excess, extended, x.entry_trigger], ["wait_risk", "wait_extended", "buy_breakout"], default="watch_setup")
    x["entry_setup"] = np.select([excess, extended, x.entry_trigger], ["risk_watch", "extended_watch", "breakout"], default="watchlist")
    x["initial_stop_price"] = x.stop_price
    x["two_r_price"] = x.close + 2 * (x.close - x.stop_price)
    x["position_size_pct"] = np.where(x.entry_trigger, np.minimum(cfg.risk_per_trade / x.risk_to_stop, cfg.max_position_pct), 0.0)
    # Restrict actual triggers, not watch rows, to the portfolio's position limit.
    overflow = x.loc[x.entry_trigger].sort_values("final_score", ascending=False).iloc[cfg.max_positions:].index
    x.loc[overflow, ["entry_trigger", "position_size_pct", "entry_signal", "entry_setup"]] = [False, 0.0, "watch_setup", "watchlist"]
    x["sell_watch_price"], x["trend_exit_price"] = x.ma20, x.ma50
    x["stop_basis"] = "Two-week low minus 0.5 weekly ATR14; risk above 10% is watch only."
    x["entry_reason"] = "Fresh 11-week high breakout, completed weekly volume and risk confirmation."
    x["is_candidate"] = True
    return x.sort_values(["entry_trigger", "final_score"], ascending=False)


def build_weekly(selected: pd.DataFrame, histories: Dict[str, pd.DataFrame], cfg: MarketConfig,
                 sectors: Dict[str, str], now: Optional[pd.Timestamp] = None,
                 requested_date: Optional[str] = None) -> tuple:
    end = completed_week_end(cfg.market, now)
    if requested_date is not None:
        timezone = "America/New_York" if cfg.market == "NASDAQ" else "Asia/Seoul"
        requested = pd.Timestamp(requested_date, tz=timezone) + pd.Timedelta(hours=23, minutes=59)
        end = min(end, completed_week_end(cfg.market, requested))
    weekly = {ticker: weekly_history(history, end) for ticker, history in histories.items()}
    benchmark = weekly.get(cfg.benchmark_tickers[0], pd.DataFrame())
    if benchmark.empty or len(benchmark) < 56:
        return None, {}
    as_of = benchmark.index[-1].strftime("%Y-%m-%d")
    price_date = benchmark.price_date.iloc[-1]
    regime = weekly_regime(benchmark)
    # Sector horizons remain 5/10/21 trading days, ending at the same completed week.
    daily = {ticker: history.loc[pd.to_datetime(history.index).strftime("%Y-%m-%d") <= price_date]
             for ticker, history in histories.items()}
    members = selected[["ticker"]].copy()
    members["sector_name"] = members.ticker.map(sectors)
    strength = calculate_sector_strength(members, daily, daily[cfg.benchmark_tickers[0]])
    sector_scores = {} if strength.empty else strength[strength.period_days == 21].set_index("sector_name")["score"].to_dict()
    features, reversals, charts = [], [], {}
    reversal_cfg = replace(cfg, max_atr_pct=0.20)
    for stock in selected.to_dict("records"):
        ticker = stock["ticker"]
        frame = weekly.get(ticker, pd.DataFrame())
        if len(frame) < 56 or frame.index[-1].strftime("%Y-%m-%d") != as_of or frame.price_date.iloc[-1] != price_date:
            continue
        if not np.isfinite(frame.daily_adv20.iloc[-1]):
            continue
        sector = sectors.get(ticker)
        features.append(weekly_trend_row(ticker, stock.get("security_name", ""), frame, cfg, sector))
        row = analyze_reversal(ticker, stock.get("security_name", ""), frame, reversal_cfg,
                               regime["score"], sector, sector_scores.get(sector), WEEKLY_PERIODS)
        if row:
            reversals.append(row)
        charts[ticker] = frame
    scored = score_universe(pd.DataFrame(features)) if features else pd.DataFrame()
    if not scored.empty:
        scored["accumulation_score"] = None
        scored["vcp_score"] = None
    candidates = weekly_candidates(scored, cfg, regime)
    if not scored.empty:
        candidate_map = {row["ticker"]: row for row in candidates.to_dict("records")}
        scored = pd.DataFrame([candidate_map.get(row["ticker"], row) for row in scored.to_dict("records")])
    reversal_frame = pd.DataFrame(reversals)
    if not reversal_frame.empty:
        reversal_frame = reversal_frame.sort_values(["entry_trigger", "final_score"], ascending=False)
    return {"scored": scored, "candidates": candidates, "reversals": reversal_frame, "sectors": strength,
            "analysis": {"version": 1, "timeframe": "weekly", "as_of": as_of, "price_date": price_date,
                         "closed_bars_only": True, "scanned_count": len(features),
                         "regime_score": regime["score"], "exposure": regime["exposure"]}}, charts
