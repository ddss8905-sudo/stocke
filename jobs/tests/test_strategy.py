import sys
import unittest
from pathlib import Path

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "jobs"))

from backtest_excel_reports import build_detail
from screeners import common, nasdaq


class StrategyTests(unittest.TestCase):
    def test_market_regime_uses_80_40_0_exposure(self):
        def frame(last_close, ma50, ma200):
            close = np.full(220, last_close, dtype=float)
            df = pd.DataFrame({"close": close, "ma50": ma50, "ma200": ma200})
            return df

        self.assertEqual(common.build_market_regime(frame(120, 110, 100))["exposure"], 0.8)
        self.assertEqual(common.build_market_regime(frame(105, 95, 100))["exposure"], 0.4)
        self.assertEqual(common.build_market_regime(frame(90, 95, 100))["exposure"], 0.0)

    def test_leadership_score_weights_skip_month_momentum_ranks(self):
        rows = pd.DataFrame({
            "ticker": ["LOW", "HIGH"],
            "_momentum_3m_skip_1m": [0.1, 0.2],
            "_momentum_6m_skip_1m": [0.1, 0.3],
            "_momentum_12m_skip_1m": [0.1, 0.4],
        })
        scored = common.score_universe(rows).set_index("ticker")
        self.assertEqual(scored.loc["HIGH", "final_score"], 100.0)
        self.assertEqual(scored.loc["LOW", "final_score"], 50.0)

    def test_only_new_55_day_breakout_triggers_and_size_is_capped(self):
        row = {
            "ticker": "TEST", "final_score": 90.0, "rs_rank": 90.0,
            "close": 104.0, "close_prev": 99.0, "ma20": 95.0,
            "ma50": 90.0, "ma200": 80.0, "trend_template_pass": True,
            "close_to_ma50_ratio": 1.1, "base_depth_pct": 0.2,
            "adv20": 100_000_000.0, "atr_pct": 0.03, "atr14": 4.0,
            "high55_prev": 100.0, "donchian55_breakout": True,
            "_prior_donchian55_breakout": False,
            "volume": 2_000_000.0, "vol_ma50": 1_000_000.0,
            "low20_prev": 90.0,
        }
        candidates = common.build_candidates(
            pd.DataFrame([row]), nasdaq.CFG, {"score": 80.0, "exposure": 0.8}
        )
        self.assertEqual(len(candidates), 1)
        candidate = candidates.iloc[0]
        self.assertTrue(candidate["entry_trigger"])
        self.assertFalse(candidate["pullback_entry"])
        self.assertAlmostEqual(candidate["stop_price"], 94.0)
        self.assertLessEqual(candidate["position_size_pct"], 0.10)

        row["_prior_donchian55_breakout"] = True
        repeated = common.build_candidates(
            pd.DataFrame([row]), nasdaq.CFG, {"score": 80.0, "exposure": 0.8}
        )
        self.assertFalse(repeated.iloc[0]["entry_trigger"])

    def test_trailing_stop_stays_active_and_never_moves_down(self):
        idx = pd.bdate_range("2024-01-01", periods=60)
        df = pd.DataFrame({
            "open": 115.0, "high": 116.0, "low": 114.0,
            "close": 115.0, "volume": 1_000_000.0,
        }, index=idx)
        result = common.evaluate_position_exit(
            df, 100.0, 90.0, 130.0, nasdaq.CFG,
            previous_stop_price=120.0, trail_activated=True,
        )
        self.assertTrue(result["trail_activated"])
        self.assertGreaterEqual(result["trailing_stop_price"], 120.0)
        self.assertEqual(result["exit_reason"], "trailing_stop")

    def test_backtest_enters_next_open_and_uses_gap_open_for_stop(self):
        history = pd.DataFrame({
            "open": [100.0, 80.0], "high": [102.0, 85.0],
            "low": [95.0, 75.0], "close": [101.0, 82.0],
        }, index=pd.to_datetime(["2024-01-02", "2024-01-03"]))
        signals = pd.DataFrame([{
            "market": "NASDAQ", "ticker": "TEST", "close": 100.0,
            "report_date": "2024-01-01", "entry_trigger": True,
            "entry_pivot": 99.0,
        }])
        prices = {("NASDAQ", "TEST"): {
            "backtest_price_date": "2024-01-03", "backtest_close": 82.0,
            "history": history,
        }}
        detail = build_detail(signals, "2024-01-03", prices, stop_loss_pct=0.1)
        self.assertEqual(detail.iloc[0]["entry_price"], 100.0)
        self.assertEqual(detail.iloc[0]["exit_price"], 80.0)
        self.assertAlmostEqual(detail.iloc[0]["return_pct"], -0.20)
        self.assertTrue(str(detail.iloc[0]["exit_reason"]).startswith("gap_"))


if __name__ == "__main__":
    unittest.main()

