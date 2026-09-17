"""Offline, synthetic diagnostics for the current screener; not a backtest."""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "jobs"))

from backtest_excel_reports import build_detail
from screeners import common, kosdaq, nasdaq


def main():
    result = {
        "evidence_type": "synthetic_behavior_checks_not_investment_performance",
        "nasdaq_configured_universe": nasdaq.CFG.universe_size,
        "nasdaq_universe_source": "official_listing_then_20d_adv",
        "kosdaq_fallback_list_count": len(kosdaq.BASE_KOSDAQ_TICKERS),
    }

    # A strictly widening range creates the nested-window VCP points anyway.
    idx = pd.bdate_range("2024-01-01", periods=300)
    width = np.linspace(1, 8, len(idx))
    close = np.full(len(idx), 100.0)
    expanding = pd.DataFrame({
        "open": close, "close": close, "high": close + width,
        "low": close - width, "volume": np.full(len(idx), 1000000.0),
    }, index=idx)
    # Strictly rising prices also satisfy nested ranges without compression.
    rising = expanding.copy()
    rising["close"] = np.arange(len(idx), dtype=float) + 100
    rising["open"] = rising["close"]
    rising["high"] = rising["close"] + width
    rising["low"] = rising["close"] - width
    featured = common.add_technical_features(rising)
    last = featured.iloc[-1]
    row = common.latest_feature_row("TEST", "Synthetic", featured, featured, featured, nasdaq.CFG)
    result["vcp_expanding_bar_width"] = {
        "range10": float(last["range10"]), "range20": float(last["range20"]),
        "range50": float(last["range50"]), "vcp_raw": row["vcp_raw"],
        "bar_width_increasing": bool(np.all(np.diff(width) > 0)),
    }

    bullish = pd.DataFrame({"close": np.arange(300, dtype=float) + 100})
    breadth = pd.DataFrame({"close": [200.0], "ma50": [150.0], "ma200": [100.0]})
    result["all_conditions_bullish_regime_score"] = common.build_market_regime(bullish, bullish, breadth)["score"]

    history = pd.DataFrame({
        "open": [100.0, 80.0], "high": [102.0, 85.0],
        "low": [95.0, 75.0], "close": [101.0, 82.0],
    }, index=pd.to_datetime(["2024-01-02", "2024-01-03"]))
    signals = pd.DataFrame([{
        "market": "NASDAQ", "ticker": "TEST", "close": 100.0,
        "report_date": "2024-01-01", "entry_trigger": True, "entry_pivot": 99.0,
    }])
    prices = {("NASDAQ", "TEST"): {
        "backtest_price_date": "2024-01-03", "backtest_close": 82.0,
        "history": history,
    }}
    detail = build_detail(signals, "2024-01-03", prices, stop_loss_pct=0.1)
    result["next_open_and_gap_execution"] = {
        "input_entry_trigger": True, "counted_rows": len(detail),
        "entry_price": float(detail.iloc[0]["entry_price"]),
        "gap_open": 80.0, "stop_level": 90.0,
        "recorded_exit": float(detail.iloc[0]["exit_price"]),
        "recorded_return": float(detail.iloc[0]["return_pct"]),
    }

    # At +1.5R, a previously activated +2R trail cannot be represented.
    frame = pd.DataFrame({
        "open": np.full(60, 115.0), "close": np.full(60, 115.0),
        "high": np.full(60, 116.0), "low": np.full(60, 114.0),
        "volume": np.full(60, 1000000.0),
    }, index=pd.bdate_range("2024-01-01", periods=60))
    result["trail_after_gain_falls_below_2r"] = common.evaluate_position_exit(frame, 100.0, 90.0, 130.0, nasdaq.CFG)
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()

