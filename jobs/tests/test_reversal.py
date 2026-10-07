import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from screeners.nasdaq import CFG
from screeners.reversal import analyze_reversal, build_reversals, completed_histories, descending_resistance
import run_market


def reversal_history():
    x = np.arange(300)
    close = 100 - 0.15 * x + 1.5 * np.cos(2 * np.pi * (x - 10) / 40)
    close[260:290] = 54 + 0.3 * np.cos(np.arange(30) * 0.3)
    close[290:] = np.linspace(54.3, 57.2, 10)
    frame = pd.DataFrame({"open": close - 0.1, "high": close + 0.4, "low": close - 0.4,
                          "close": close, "volume": 1_000_000.0},
                         index=pd.bdate_range("2025-01-01", periods=300))
    resistance = descending_resistance(frame)
    assert resistance is not None
    price = resistance[3][-1] * 1.02
    frame.iloc[-1, frame.columns.get_indexer(["open", "high", "low", "close", "volume"])] = [price - 0.4, price + 0.2, price - 0.6, price, 2_000_000]
    return frame


class ReversalTests(unittest.TestCase):
    def test_run_pipeline_persists_closed_reversal_metadata_without_db_columns(self):
        frame = reversal_history()
        selected = pd.DataFrame([{"ticker": "TEST", "security_name": "Test"}])
        result = {"market": "NASDAQ", "run_date": "2026-10-07", "market_bullish": False,
                  "selected": selected, "scored": pd.DataFrame(), "candidates": pd.DataFrame(),
                  "reversal_histories": {"TEST": frame, "QQQ": frame, "SPY": frame}, "reversal_sectors": {}}
        with patch.dict(run_market.RUNNERS, {"NASDAQ": lambda _: result}), \
             patch.object(sys, "argv", ["run_market.py", "--market", "NASDAQ"]), \
             patch.dict(run_market.os.environ, {"SUPABASE_URL": "https://example.invalid", "SUPABASE_SERVICE_ROLE_KEY": "test"}), \
             patch.object(run_market, "write_local_payload"), \
             patch.object(run_market, "upload_to_supabase", return_value="test-run") as database, \
             patch.object(run_market, "upload_sector_snapshot") as snapshot, \
             patch.object(run_market, "upload_chart_snapshot"):
            run_market.main()
        payload = database.call_args[0][0]
        self.assertTrue(payload["reversal_analysis"]["closed_bars_only"])
        self.assertEqual(payload["reversal_analysis"]["scanned_count"], 1)
        self.assertEqual(payload["reversal_analysis"]["as_of"], frame.index[-1].strftime("%Y-%m-%d"))
        self.assertEqual(len(payload["reversals"]), 1)
        self.assertEqual(payload["scored"], [])
        self.assertEqual(snapshot.call_args[0][1], "test-run")

    def test_domestic_intraday_and_delayed_close_bars_are_excluded(self):
        frame = reversal_history().tail(2)
        frame.index = pd.to_datetime(["2026-10-06", "2026-10-07"]).date
        for time in ("2026-10-07T09:08:00+09:00", "2026-10-07T16:30:00+09:00"):
            history = completed_histories({"TEST": frame}, "KOSDAQ", pd.Timestamp(time))["TEST"]
            self.assertEqual(len(history), 1)
        closed = completed_histories({"TEST": frame}, "KOSPI_API", pd.Timestamp("2026-10-07T16:45:00+09:00"))["TEST"]
        self.assertEqual(len(closed), 2)

    def test_us_cutoff_uses_eastern_time_and_dst(self):
        frame = reversal_history().tail(2)
        for day, utc_close in (("2026-07-07", "20:15"), ("2026-12-07", "21:15")):
            frame.index = pd.to_datetime(["2026-01-01", day])
            closed = pd.Timestamp(f"{day}T{utc_close}:00Z")
            before = completed_histories({"TEST": frame}, "NASDAQ", closed - pd.Timedelta(minutes=1))["TEST"]
            after = completed_histories({"TEST": frame}, "NASDAQ", closed)["TEST"]
            self.assertEqual(len(before), 1)
            self.assertEqual(len(after), 2)

    def test_fresh_breakout_ignores_negative_long_term_momentum(self):
        frame = reversal_history()
        row = analyze_reversal("TEST", "Test", frame, CFG, 80)
        self.assertIsNotNone(row)
        self.assertTrue(row["entry_trigger"])
        self.assertEqual(row["reversal_status"], "confirmed")
        self.assertGreater(row["decline_pct"], 0.25)
        self.assertLessEqual(row["position_size_pct"], 0.05)
        self.assertAlmostEqual(row["volume_ratio"], 2)
        self.assertLess(frame.close.iloc[-1], frame.close.iloc[0])

    def test_risk_off_and_low_volume_do_not_trigger(self):
        frame = reversal_history()
        row = analyze_reversal("TEST", "Test", frame, CFG, 0)
        self.assertEqual(row["reversal_status"], "market_wait")
        self.assertFalse(row["entry_trigger"])
        self.assertEqual(row["position_size_pct"], 0)
        frame.iloc[-1, frame.columns.get_loc("volume")] = 1_000_000
        row = analyze_reversal("TEST", "Test", frame, CFG, 80)
        self.assertFalse(row["entry_trigger"])
        self.assertEqual(row["reversal_status"], "volume_wait")

    def test_invalid_and_insufficient_histories_are_rejected(self):
        frame = reversal_history()
        self.assertIsNone(analyze_reversal("TEST", "Test", frame.tail(200), CFG, 80))
        for value in (0, float("nan"), float("inf")):
            broken = frame.copy()
            broken.iloc[-20, broken.columns.get_loc("close")] = value
            self.assertIsNone(analyze_reversal("TEST", "Test", broken, CFG, 80))
        self.assertIsNone(analyze_reversal("TEST", "Test", pd.concat([frame, frame.tail(1)]), CFG, 80))

    def test_structural_risk_is_not_clamped_to_make_an_entry(self):
        frame = reversal_history()
        frame.iloc[-20, frame.columns.get_loc("low")] = 50
        frame.iloc[-5, frame.columns.get_loc("low")] = 51
        row = analyze_reversal("TEST", "Test", frame, CFG, 80)
        self.assertIsNotNone(row)
        self.assertGreater(row["risk_to_stop"], 0.10)
        self.assertLess(row["stop_price"], 51)
        self.assertFalse(row["entry_trigger"])
        self.assertEqual(row["reversal_status"], "risk_high")

    def test_tracking_an_old_signal_is_not_a_fresh_entry(self):
        frame = reversal_history()
        yesterday = frame.copy()
        yesterday.index = pd.bdate_range("2025-01-02", periods=len(frame))
        today = yesterday.tail(1).copy()
        today.index = pd.bdate_range(yesterday.index[-1] + pd.Timedelta(days=1), periods=1)
        for column in ("open", "high", "low", "close"):
            today[column] *= 1.001
        row = analyze_reversal("TEST", "Test", pd.concat([yesterday, today]), CFG, 80)
        self.assertIsNotNone(row)
        self.assertFalse(row["entry_trigger"])
        self.assertEqual(row["reversal_status"], "tracking")

    def test_new_low_and_uptrend_are_not_bottom_reversals(self):
        frame = reversal_history()
        frame.iloc[-1, frame.columns.get_indexer(["open", "high", "low", "close"])] = [40, 41, 39, 40]
        self.assertIsNone(analyze_reversal("TEST", "Test", frame, CFG, 80))
        rising = reversal_history()
        price = np.linspace(50, 150, len(rising))
        rising["open"], rising["high"], rising["low"], rising["close"] = price, price + 1, price - 1, price
        self.assertIsNone(analyze_reversal("TEST", "Test", rising, CFG, 80))

    def test_as_of_excludes_future_and_rejects_stale_prices(self):
        frame = reversal_history()
        selected = pd.DataFrame([{"ticker": "TEST", "security_name": "Test"}])
        day = frame.index[-1].strftime("%Y-%m-%d")
        result, scanned = build_reversals(selected, {"TEST": frame}, CFG, 80, {}, pd.DataFrame(), day)
        future = frame.tail(1).copy()
        future.index = pd.bdate_range(frame.index[-1] + pd.Timedelta(days=1), periods=1)
        future *= 4
        again, _ = build_reversals(selected, {"TEST": pd.concat([frame, future])}, CFG, 80, {}, pd.DataFrame(), day)
        self.assertEqual(result.to_dict("records"), again.to_dict("records"))
        stale, stale_scanned = build_reversals(selected, {"TEST": frame.iloc[:-1]}, CFG, 80, {}, pd.DataFrame(), day)
        self.assertTrue(stale.empty)
        self.assertEqual(stale_scanned, 0)
        self.assertEqual(scanned, 1)

    def test_kis_python_date_index_is_supported(self):
        frame = reversal_history()
        expected = analyze_reversal("TEST", "Test", frame, CFG, 80)
        frame.index = frame.index.date
        selected = pd.DataFrame([{"ticker": "TEST", "security_name": "Test"}])
        rows, scanned = build_reversals(selected, {"TEST": frame}, CFG, 80, {}, pd.DataFrame(), frame.index[-1].isoformat())
        self.assertEqual(scanned, 1)
        self.assertEqual(rows.to_dict("records"), [expected])

    def test_pivots_are_confirmed_before_the_signal_day(self):
        frame = reversal_history()
        first, second, _, _ = descending_resistance(frame)
        self.assertLess(second, len(frame) - 6)
        self.assertGreaterEqual(second - first, 63)
        before = descending_resistance(frame)
        frame.iloc[-1, frame.columns.get_loc("high")] *= 5
        after = descending_resistance(frame)
        self.assertEqual(before[:3], after[:3])


if __name__ == "__main__":
    unittest.main()
