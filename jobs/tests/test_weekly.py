import sys
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from screeners.nasdaq import CFG
from screeners.reversal import WEEKLY_PERIODS, analyze_reversal, descending_resistance
from screeners.weekly import build_weekly, completed_week_end, weekly_candidates, weekly_history, weekly_regime, weekly_trend_row
from screeners.common import score_universe


def daily_history():
    close = np.linspace(50, 100, 490)
    return pd.DataFrame({"open": close - 0.2, "high": close + 0.5, "low": close - 0.5,
                         "close": close, "volume": 1_000_000., "value": 50_000_000.},
                        index=pd.bdate_range(end="2026-10-02", periods=490))


def weekly_reversal():
    x = np.arange(90)
    close = 100 - 0.5 * x + 1.6 * np.cos(2 * np.pi * (x - 5) / 12)
    close[77:88] = 54 + 0.15 * np.cos(np.arange(11))
    close[-2:] = [55.5, 56.1]
    frame = pd.DataFrame({"open": close - 0.1, "high": close + 0.4, "low": close - 0.4,
                          "close": close, "volume": 5_000_000., "daily_adv20": 50_000_000.},
                         index=pd.date_range(end="2026-10-02", periods=90, freq="W-FRI"))
    resistance = descending_resistance(frame, WEEKLY_PERIODS)
    assert resistance is not None
    price = resistance[3][-1] * 1.02
    frame.iloc[-1, frame.columns.get_indexer(["open", "high", "low", "close", "volume"])] = [price - .4, price + .2, price - .6, price, 10_000_000]
    return frame


class WeeklyTests(unittest.TestCase):
    def test_ohlcv_and_value_are_aggregated_not_averaged(self):
        frame = daily_history()
        result = weekly_history(frame, pd.Timestamp("2026-10-02"))
        days = frame.loc["2026-09-28":"2026-10-02"]
        last = result.iloc[-1]
        self.assertEqual(last.open, days.open.iloc[0])
        self.assertEqual(last.high, days.high.max())
        self.assertEqual(last.low, days.low.min())
        self.assertEqual(last.close, days.close.iloc[-1])
        self.assertEqual(last.volume, days.volume.sum())
        self.assertEqual(last.value, days.value.sum())
        self.assertEqual(last.daily_adv20, 50_000_000.)
        self.assertEqual(last.price_date, "2026-10-02")

    def test_first_partial_week_is_discarded(self):
        frame = daily_history().loc["2026-09-23":]
        weeks = weekly_history(frame, pd.Timestamp("2026-10-02"))
        self.assertEqual(weeks.index.tolist(), [pd.Timestamp("2026-10-02")])

    def test_friday_market_cutoffs_and_weekends(self):
        for market, cutoff in [("NASDAQ", "2026-10-09T16:15:00-04:00"), ("KOSPI_API", "2026-10-09T16:45:00+09:00")]:
            now = pd.Timestamp(cutoff)
            self.assertEqual(completed_week_end(market, now - pd.Timedelta(minutes=1)), pd.Timestamp("2026-10-02"))
            self.assertEqual(completed_week_end(market, now), pd.Timestamp("2026-10-09"))
            self.assertEqual(completed_week_end(market, now + pd.Timedelta(days=1)), pd.Timestamp("2026-10-09"))
        self.assertEqual(completed_week_end("NASDAQ", pd.Timestamp("2026-10-07T19:00:00Z")), pd.Timestamp("2026-10-02"))
        self.assertEqual(completed_week_end("NASDAQ", pd.Timestamp("2026-12-11T21:14:00Z")), pd.Timestamp("2026-12-04"))
        self.assertEqual(completed_week_end("NASDAQ", pd.Timestamp("2026-12-11T21:15:00Z")), pd.Timestamp("2026-12-11"))

    def test_holiday_week_uses_last_trade_but_friday_label(self):
        frame = daily_history().iloc[:-1]
        last = weekly_history(frame, pd.Timestamp("2026-10-02")).iloc[-1]
        self.assertEqual(last.name, pd.Timestamp("2026-10-02"))
        self.assertEqual(last.price_date, "2026-10-01")
        self.assertEqual(last.close, frame.close.iloc[-1])

    def test_unclosed_week_spike_does_not_change_either_strategy(self):
        frame = daily_history()
        selected = pd.DataFrame([{"ticker": "TEST", "security_name": "Test"}])
        histories = {"TEST": frame, "QQQ": frame, "SPY": frame}
        now = pd.Timestamp("2026-10-07T18:00:00Z")
        expected, _ = build_weekly(selected, histories, CFG, {}, now)
        future = frame.tail(1).copy()
        future.index = pd.to_datetime(["2026-10-07"])
        future *= 100
        changed = {ticker: pd.concat([history, future]) for ticker, history in histories.items()}
        actual, charts = build_weekly(selected, changed, CFG, {}, now)
        for key in ("scored", "candidates", "reversals", "sectors"):
            pd.testing.assert_frame_equal(expected[key], actual[key])
        self.assertEqual(actual["analysis"]["as_of"], "2026-10-02")
        self.assertEqual(charts["TEST"].index[-1], pd.Timestamp("2026-10-02"))

    def test_stale_and_short_stock_histories_not_scored(self):
        frame = daily_history()
        stocks = pd.DataFrame([{"ticker": "STALE"}, {"ticker": "SHORT"}])
        result, charts = build_weekly(stocks, {"QQQ": frame, "STALE": frame.iloc[:-1], "SHORT": frame.tail(100)}, CFG, {}, pd.Timestamp("2026-10-05T12:00:00Z"))
        self.assertEqual(result["analysis"]["scanned_count"], 0)
        self.assertTrue(result["scored"].empty)
        self.assertEqual(charts, {})

    def test_backdated_run_excludes_not_yet_completed_week(self):
        frame = daily_history()
        result, _ = build_weekly(pd.DataFrame(columns=["ticker"]), {"QQQ": frame}, CFG, {},
                                 pd.Timestamp("2026-10-07T18:00:00Z"), requested_date="2026-09-30")
        self.assertEqual(result["analysis"]["as_of"], "2026-09-25")

    def test_long_history_does_not_shift_on_new_intraweek_data(self):
        frame = daily_history()
        old = frame.head(40).copy()
        old.index -= pd.Timedelta(days=100)
        frame = pd.concat([old, frame])
        expected = weekly_history(frame, pd.Timestamp("2026-10-02"))
        future = frame.tail(1).copy()
        future.index = pd.to_datetime(["2026-10-07"])
        actual = weekly_history(pd.concat([frame, future]), pd.Timestamp("2026-10-02"))
        pd.testing.assert_frame_equal(expected, actual)

    def test_weekly_averages_and_momentum_use_weeks(self):
        weeks = weekly_history(daily_history(), pd.Timestamp("2026-10-02"))
        row = weekly_trend_row("TEST", "Test", weeks, CFG, "Tech")
        self.assertAlmostEqual(row["ma50"], weeks.close.tail(10).mean())
        self.assertAlmostEqual(row["ma150"], weeks.close.tail(30).mean())
        self.assertAlmostEqual(row["ma200"], weeks.close.tail(40).mean())
        self.assertAlmostEqual(row["_momentum_12m_skip_1m"], weeks.close.iloc[-5] / weeks.close.iloc[-53] - 1)
        self.assertEqual(row["timeframe"], "weekly")
        self.assertEqual(weekly_regime(weeks), {"score": 80.0, "exposure": 0.8})
        bearish = weeks.copy()
        bearish["close"] = bearish.close.iloc[::-1].to_numpy()
        self.assertEqual(weekly_regime(bearish)["exposure"], 0.0)

    def test_weekly_reversal_has_independent_pivots_and_week_units(self):
        frame = weekly_reversal()
        cfg = replace(CFG, max_atr_pct=.20)
        row = analyze_reversal("TEST", "Test", frame, cfg, 80, periods=WEEKLY_PERIODS)
        self.assertIsNotNone(row)
        self.assertEqual(row["timeframe"], "weekly")
        self.assertGreaterEqual(row["downtrend_bars"], 25)
        self.assertEqual(row["as_of"], "2026-10-02")
        self.assertEqual(row["volume_ratio"], 2.)
        self.assertIsNone(analyze_reversal("TEST", "Test", frame, cfg, 80))
        before = descending_resistance(frame, WEEKLY_PERIODS)
        frame.iloc[-1, frame.columns.get_loc("high")] *= 5
        self.assertEqual(before[:3], descending_resistance(frame, WEEKLY_PERIODS)[:3])

    def test_weekly_risk_watch_never_creates_a_position(self):
        frame = weekly_history(daily_history(), pd.Timestamp("2026-10-02"))
        row = weekly_trend_row("TEST", "Test", frame, CFG, None)
        row["_structure_stop"] = row["close"] * .75
        scored = score_universe(pd.DataFrame([row]))
        result = weekly_candidates(scored, CFG, {"score": 80., "exposure": .8})
        self.assertEqual(len(result), 1)
        self.assertEqual(result.iloc[0].entry_signal, "wait_risk")
        self.assertAlmostEqual(result.iloc[0].risk_to_stop, .25)
        self.assertFalse(result.iloc[0].entry_trigger)
        self.assertEqual(result.iloc[0].position_size_pct, 0)

    def test_fresh_weekly_trend_breakout_volume_and_position_cap(self):
        frame = weekly_history(daily_history(), pd.Timestamp("2026-10-02"))
        frame.iloc[-12:-1, frame.columns.get_indexer(["open", "high", "low", "close"])] = [100., 100.5, 99.5, 100.]
        price = frame.high.iloc[-12:-1].max() * 1.02
        frame.iloc[-1, frame.columns.get_indexer(["open", "high", "low", "close", "volume"])] = [price - .2, price + .3, price - .5, price, 10_000_000]
        rows = [weekly_trend_row(str(i), "Test", frame, CFG, None) for i in range(15)]
        cap_cfg = replace(CFG, min_final_score=0, min_rs_rank=0)
        result = weekly_candidates(score_universe(pd.DataFrame(rows)), cap_cfg, {"score": 80., "exposure": .8})
        self.assertEqual(result.entry_trigger.sum(), CFG.max_positions)
        self.assertTrue((result.loc[~result.entry_trigger, "position_size_pct"] == 0).all())
        self.assertTrue((result.loc[result.entry_trigger, "risk_to_stop"] <= .10).all())
        self.assertTrue((result.position_size_pct <= CFG.max_position_pct).all())
        frame.iloc[-1, frame.columns.get_loc("volume")] = 5_000_000
        quiet = weekly_candidates(score_universe(pd.DataFrame([weekly_trend_row("TEST", "Test", frame, CFG, None)])), CFG, {"score": 80., "exposure": .8})
        self.assertFalse(quiet.iloc[0].entry_trigger)
        off = weekly_candidates(score_universe(pd.DataFrame(rows)), CFG, {"score": 0., "exposure": 0.})
        self.assertTrue(off.empty)

    def test_invalid_history_and_insufficient_benchmark_unavailable(self):
        frame = daily_history()
        broken = frame.copy()
        broken.iloc[-20, broken.columns.get_loc("close")] = np.nan
        self.assertTrue(weekly_history(broken, pd.Timestamp("2026-10-02")).empty)
        result, _ = build_weekly(pd.DataFrame(columns=["ticker"]), {"QQQ": frame.tail(100)}, CFG, {})
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
