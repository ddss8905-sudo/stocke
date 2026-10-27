import copy
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from screeners.gap224 import above, baseline_row, current_state, detect_gap, session_context
from screeners.gap224_sources import prepare_sources
from screeners import gap224_sources
from gap_telegram import deliver, event_message
from gap_store import GapStore
import run_gap224


DAY, REFERENCE = "2026-10-27", "2026-10-26"
NOW = pd.Timestamp("2026-10-27T00:05:00Z")


def history():
    close = np.full(300, 100.)
    close[-1] = 98.
    return pd.DataFrame({"open": close, "high": close + .5, "low": close - .5,
                         "close": close, "volume": 1_000_000.},
                        index=pd.bdate_range(end=REFERENCE, periods=300))


def quote(opening=102., closing=102.5):
    return {"open": opening, "high": max(opening, closing) + .5, "low": min(opening, closing) - .5,
            "close": closing, "volume": 1_000_000., "session_date": DAY, "previous_close": 98.,
            "regular_session": True, "source": "fixture", "quote_at": NOW.isoformat(),
            "regular_market_at": NOW.isoformat()}


class MemoryStore:
    def __init__(self):
        self.objects = {}

    def get(self, path):
        return copy.deepcopy(self.objects.get(path))

    def put(self, path, value, create=False):
        if create and path in self.objects:
            return False
        self.objects[path] = copy.deepcopy(value)
        return True


def prepared_store():
    store = MemoryStore()
    frame = history()
    stock = baseline_row("TEST", "Test", frame, REFERENCE, "Tech")
    stock["candles"] = run_gap224.candle_records(frame)
    store.put(f"KOSDAQ/baselines/{REFERENCE}.json", {"version": 1, "reference_date": REFERENCE,
              "history_source": "KIS/KRX",
              "status": "complete", "stocks": [stock], "missing_count": 0, "selected_count": 1, "excluded_count": 0})
    return store, stock


def context():
    return {"date": DAY, "reference_date": REFERENCE, "open": pd.Timestamp("2026-10-27T00:00:00Z")}


class GapDetectionTests(unittest.TestCase):
    def setUp(self):
        self.baseline = baseline_row("TEST", "Test", history(), REFERENCE)

    def test_gap_must_cross_both_previous_close_and_opening_ma224(self):
        event = detect_gap(self.baseline, quote(), DAY, NOW.isoformat())
        self.assertIsNotNone(event)
        self.assertAlmostEqual(event["ma224_open"], (self.baseline["sum223"] + 102) / 224)
        self.assertGreater(event["open_price"], event["previous_close"])
        self.assertGreater(event["open_price"], event["ma224_open"])
        self.assertIsNone(detect_gap(self.baseline, quote(99.), DAY, NOW.isoformat()))
        self.assertIsNone(detect_gap(self.baseline, quote(98.), DAY, NOW.isoformat()))
        self.assertIsNone(detect_gap(self.baseline, quote(97.), DAY, NOW.isoformat()))

    def test_intraday_breakout_and_todays_close_cannot_create_opening_signal(self):
        self.assertIsNone(detect_gap(self.baseline, quote(99., 500.), DAY, NOW.isoformat()))
        first = detect_gap(self.baseline, quote(102., 500.), DAY, NOW.isoformat())
        second = detect_gap(self.baseline, quote(102., 40.), DAY, NOW.isoformat())
        for key in ("open_price", "ma224_open", "gap_pct", "breakout_pct"):
            self.assertEqual(first[key], second[key])

    def test_already_above_and_exact_boundary_are_excluded(self):
        baseline = {**self.baseline, "previous_close": 101.}
        self.assertIsNone(detect_gap(baseline, quote(105.), DAY, NOW.isoformat()))
        baseline = {**self.baseline, "sum223": 22300., "previous_close": 98.}
        self.assertIsNone(detect_gap(baseline, quote(100.), DAY, NOW.isoformat()))
        self.assertFalse(above(99.9, 99.89999999999999))

    def test_stale_premarket_zero_and_invalid_open_are_excluded(self):
        for update in ({"session_date": REFERENCE}, {"regular_session": False}, {"open": 0}, {"open": np.nan}, {"open": np.inf}):
            self.assertIsNone(detect_gap(self.baseline, {**quote(), **update}, DAY, NOW.isoformat()))

    def test_baseline_needs_224_valid_completed_sessions_and_matching_last_date(self):
        frame = history()
        self.assertIsNone(baseline_row("TEST", "Test", frame.tail(223), REFERENCE))
        self.assertIsNone(baseline_row("TEST", "Test", frame.iloc[:-1], REFERENCE))
        broken = frame.copy()
        broken.iloc[-5, broken.columns.get_loc("close")] = np.nan
        self.assertIsNone(baseline_row("TEST", "Test", broken, REFERENCE))
        self.assertIsNone(baseline_row("TEST", "Test", pd.concat([frame, frame.tail(1)]), REFERENCE))
        future = frame.tail(1).copy() * 100
        future.index = pd.to_datetime([DAY])
        self.assertEqual(self.baseline, baseline_row("TEST", "Test", pd.concat([frame, future]), REFERENCE))

    def test_event_is_retained_when_current_price_falls_below_line(self):
        event = detect_gap(self.baseline, quote(), DAY, NOW.isoformat())
        changed = current_state(event, quote(closing=95))
        self.assertEqual(changed["state"], "below")
        self.assertEqual(changed["open_price"], event["open_price"])
        self.assertEqual(changed["detected_price"], 102.5)


class GapPipelineTests(unittest.TestCase):
    def scan(self, store, value):
        with patch.object(run_gap224, "fetch_quotes", return_value=({"TEST": value}, {})), \
             patch.object(run_gap224, "utc_now", return_value=NOW), \
             patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "", "TELEGRAM_CHAT_ID": ""}):
            return run_gap224.scan(store, "KOSDAQ", context())

    def test_scan_saves_chart_and_event_without_touching_legacy_results(self):
        store, _ = prepared_store()
        result = self.scan(store, quote())
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["valid_count"], 1)
        self.assertEqual(result["notifications"], {"not_configured": 1})
        self.assertEqual(store.get(f"KOSDAQ/{DAY}/charts/TEST.json")["candles"][-1]["open"], 102.)
        self.assertTrue(all("screening_runs" not in path for path in store.objects))

    def test_repeated_scan_retains_original_chart_and_detection_time(self):
        store, _ = prepared_store()
        first = self.scan(store, quote())
        chart = store.get(f"KOSDAQ/{DAY}/charts/TEST.json")
        second = self.scan(store, quote(closing=95.))
        self.assertEqual(len(second["events"]), 1)
        self.assertEqual(second["events"][0]["state"], "below")
        self.assertEqual(first["events"][0]["detected_at"], second["events"][0]["detected_at"])
        self.assertEqual(chart, store.get(f"KOSDAQ/{DAY}/charts/TEST.json"))

    def test_adjustment_basis_change_is_pending_not_a_false_gap(self):
        store, _ = prepared_store()
        result = self.scan(store, {**quote(), "previous_close": 49.})
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["pending_count"], 1)
        self.assertEqual(result["valid_count"], 0)
        exact, _ = prepared_store()
        self.assertEqual(self.scan(exact, {**quote(), "previous_close": 99.})["pending_count"], 1)

    def test_no_valid_histories_cannot_publish_a_successful_empty_baseline(self):
        selected = pd.DataFrame([{"ticker": "TEST", "security_name": "Test"}])
        broken = history().tail(10)
        with patch.object(run_gap224, "prepare_sources", return_value=(selected, {"TEST": broken}, {})):
            self.assertRaises(RuntimeError, run_gap224.prepare, MemoryStore(), "KOSDAQ", REFERENCE)

    def test_revised_open_retains_record_but_marks_signal_corrected(self):
        store, _ = prepared_store()
        self.scan(store, quote())
        result = self.scan(store, quote(99.))
        self.assertEqual(result["valid_count"], 0)
        self.assertEqual(result["events"][0]["validation"], "corrected")

    def test_provider_failures_do_not_look_like_successful_zero_results(self):
        store, _ = prepared_store()
        with patch.object(run_gap224, "fetch_quotes", return_value=({}, {"TEST": "provider_error"})):
            result = run_gap224.scan(store, "KOSDAQ", context())
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["checked_count"], 0)
        self.assertEqual(result["pending_count"], 1)

    def test_revised_historical_average_requires_review_without_rewriting_snapshot(self):
        store, _ = prepared_store()
        with patch.object(run_gap224, "fetch_quotes", return_value=({"TEST": quote()}, {})):
            first = run_gap224.scan(store, "KOSDAQ", context())["events"][0]
            chart = store.get(f"KOSDAQ/{DAY}/charts/TEST.json")
            baseline_path = f"KOSDAQ/baselines/{REFERENCE}.json"
            baseline = store.get(baseline_path)
            baseline["stocks"][0]["sum223"] += 100
            store.put(baseline_path, baseline)
            revised = run_gap224.scan(store, "KOSDAQ", context())
        self.assertEqual(revised["events"][0]["validation"], "review")
        self.assertEqual(revised["events"][0]["ma224_open"], first["ma224_open"])
        self.assertEqual(revised["valid_count"], 0)
        self.assertEqual(store.get(f"KOSDAQ/{DAY}/charts/TEST.json"), chart)

    def test_tiny_domestic_rounding_can_be_repaired_but_large_errors_cannot(self):
        frame = history()
        frame.iloc[-5, frame.columns.get_loc("high")] = frame.close.iloc[-5] - .1
        self.assertIsNotNone(run_gap224.canonical_history(frame, REFERENCE, "KOSDAQ"))
        self.assertIsNone(run_gap224.canonical_history(frame, REFERENCE, "NASDAQ"))
        frame.iloc[-5, frame.columns.get_loc("high")] -= 5
        self.assertIsNone(run_gap224.canonical_history(frame, REFERENCE, "KOSDAQ"))


class GapSourceTests(unittest.TestCase):
    def test_legacy_mixed_source_baseline_is_rebuilt(self):
        legacy = {"status": "complete"}
        self.assertFalse(run_gap224.baseline_ready(legacy, "KOSDAQ"))
        self.assertTrue(run_gap224.baseline_ready({**legacy, "history_source": "KIS/KRX"}, "KOSDAQ"))
        self.assertTrue(run_gap224.baseline_ready(legacy, "KOSPI_API"))

    def test_kosdaq_baseline_uses_same_kis_source_as_opening_quotes(self):
        selected = pd.DataFrame({"ticker": ["403870"], "security_name": ["HPSP"]})
        client = Mock()
        histories = {"403870": history()}
        with patch.object(gap224_sources, "_kis_client", None), \
                patch("screeners.kosdaq.select_top_by_adv", return_value=selected), \
                patch("screeners.kospi_api.KisClient", return_value=client), \
                patch("screeners.kospi_api.download_ohlcv", return_value=histories) as download, \
                patch("screeners.kosdaq.download_ohlcv", side_effect=AssertionError("Mixed price basis")), \
                patch("screeners.sectors.fetch_kind_sectors", side_effect=requests.HTTPError("metadata unavailable")):
            _, result, sectors = prepare_sources("KOSDAQ", REFERENCE)
        self.assertIs(result, histories)
        self.assertEqual(sectors, {})
        self.assertEqual(download.call_args.args[0], ["403870"])
        self.assertEqual(download.call_args.args[2:], (REFERENCE, client))

    def test_kis_token_is_reused_and_rate_limited_issuance_waits(self):
        response = Mock(status_code=403)
        response.json.return_value = {"error_code": "EGW00133"}
        client = Mock()
        with patch.object(gap224_sources, "_kis_client", None), \
                patch("screeners.kospi_api.KisClient", side_effect=[requests.HTTPError(response=response), client]) as create, \
                patch.object(gap224_sources.time, "sleep") as sleep:
            self.assertIs(gap224_sources.get_kis_client(), client)
            self.assertIs(gap224_sources.get_kis_client(), client)
        self.assertEqual(create.call_count, 2)
        sleep.assert_called_once_with(65)

    def test_kis_invalid_credentials_are_not_blindly_retried(self):
        response = Mock(status_code=401)
        response.json.return_value = {"error_code": "invalid_credentials"}
        with patch.object(gap224_sources, "_kis_client", None), \
                patch("screeners.kospi_api.KisClient", side_effect=requests.HTTPError(response=response)) as create, \
                patch.object(gap224_sources.time, "sleep") as sleep:
            self.assertRaises(requests.HTTPError, gap224_sources.get_kis_client)
        create.assert_called_once()
        sleep.assert_not_called()


class TelegramTests(unittest.TestCase):
    def setUp(self):
        self.store, baseline = prepared_store()
        self.event = detect_gap(baseline, quote(), DAY, NOW.isoformat())
        self.env = patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "test-only-token", "TELEGRAM_CHAT_ID": "12345"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_confirmed_message_is_not_sent_twice(self):
        response = Mock(ok=True)
        response.json.return_value = {"ok": True, "result": {"message_id": 1}}
        with patch("gap_telegram.requests.post", return_value=response) as post:
            self.assertEqual(deliver(self.store, "KOSDAQ", self.event), "sent")
            self.assertEqual(deliver(self.store, "KOSDAQ", self.event), "sent")
        self.assertEqual(post.call_count, 1)

    def test_timeout_is_unknown_and_not_blindly_resent(self):
        with patch("gap_telegram.requests.post", side_effect=requests.Timeout("must not log URL")) as post:
            self.assertEqual(deliver(self.store, "KOSDAQ", self.event), "unknown")
            self.assertEqual(deliver(self.store, "KOSDAQ", self.event), "unknown")
        self.assertEqual(post.call_count, 1)

    def test_rate_limit_retries_only_after_provider_delay(self):
        limited, success = Mock(ok=False, status_code=429), Mock(ok=True)
        limited.json.return_value = {"ok": False, "error_code": 429, "parameters": {"retry_after": 60}}
        success.json.return_value = {"ok": True, "result": {"message_id": 2}}
        with patch("gap_telegram.requests.post", side_effect=[limited, success]) as post:
            self.assertEqual(deliver(self.store, "KOSDAQ", self.event, 100), "retry")
            self.assertEqual(deliver(self.store, "KOSDAQ", self.event, 110), "retry")
            self.assertEqual(deliver(self.store, "KOSDAQ", self.event, 161), "sent")
        self.assertEqual(post.call_count, 2)

    def test_message_does_not_contain_secrets_or_order_claims(self):
        text = event_message("KOSDAQ", self.event)
        self.assertNotIn("test-only-token", text)
        self.assertIn("224", text)
        self.assertIn("시초가로 체결 가능하다는 의미가 아니며", text)

    def test_storage_conflict_is_not_confused_with_permission_failure(self):
        conflict = Mock(ok=False, status_code=400)
        conflict.json.return_value = {"error": "Duplicate", "message": "The resource already exists"}
        forbidden = Mock(ok=False, status_code=400)
        forbidden.json.return_value = {"error": "AccessDenied"}
        with patch.dict(os.environ, {"SUPABASE_URL": "https://example.invalid", "SUPABASE_SERVICE_ROLE_KEY": "test"}):
            store = GapStore()
            with patch("gap_store.requests.post", return_value=conflict):
                self.assertFalse(store.put("test", {}, create=True))
            with patch("gap_store.requests.post", return_value=forbidden):
                self.assertRaises(RuntimeError, store.put, "test", {}, create=True)


class CalendarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import exchange_calendars
        except ImportError:
            raise unittest.SkipTest("Install jobs/requirements-gap.txt on Python 3.10+")

    def test_krx_holiday_and_us_dst(self):
        self.assertFalse(session_context("KOSDAQ", pd.Timestamp("2026-10-09T02:00:00Z"))["is_session"])
        summer = session_context("NASDAQ", pd.Timestamp("2026-07-07T13:35:00Z"))
        winter = session_context("NASDAQ", pd.Timestamp("2026-12-07T14:35:00Z"))
        self.assertEqual(summer["minutes_since_open"], 5)
        self.assertEqual(winter["minutes_since_open"], 5)
        self.assertEqual(session_context("KOSPI_API", NOW)["reference_date"], REFERENCE)
        before = session_context("KOSPI_API", pd.Timestamp("2026-10-27T07:30:00Z"))
        after = session_context("KOSPI_API", pd.Timestamp("2026-10-27T07:45:00Z"))
        self.assertEqual(before["completed_date"], REFERENCE)
        self.assertEqual(after["completed_date"], DAY)


if __name__ == "__main__":
    unittest.main()

