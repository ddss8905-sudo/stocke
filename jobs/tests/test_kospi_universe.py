import sys
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "jobs"))

from screeners import kospi_api
from run_market import chart_candles


def stock(ticker, market="KOSPI", value="10000000000", name="Company"):
    return {
        "itemCode": ticker,
        "itemName": name,
        "marketType": market,
        "krx": {"currentPrice": "5000", "tradingVolume": "2000000", "tradingValue": value},
    }


class KospiUniverseTests(unittest.TestCase):
    def test_chart_snapshot_preserves_screening_close_and_dates(self):
        history = pd.DataFrame({
            "open": [100.0, 110.0],
            "high": [112.0, 120.0],
            "low": [98.0, 108.0],
            "close": [109.0, 117.0],
            "volume": [1000.0, 1200.0],
        }, index=[date(2026, 10, 2), date(2026, 10, 6)])
        candles = chart_candles({"005930": history})["005930"]
        self.assertEqual(candles[-1]["time"], "2026-10-06")
        self.assertEqual(candles[-1]["close"], 117.0)
        self.assertEqual(len(candles), 2)

    def test_naver_listing_paginates_and_keeps_only_kospi(self):
        responses = []
        for payload in (
            {"items": [stock("005930"), stock("240810", "KOSDAQ")], "hasNext": True},
            {"items": [stock("000660", value="20000000000"), stock("069500", name="KODEX ETF")], "hasNext": False},
        ):
            response = Mock()
            response.json.return_value = payload
            responses.append(response)

        with patch.object(kospi_api.requests, "get", side_effect=responses) as get:
            result = kospi_api.fetch_naver_current_value()

        self.assertEqual(result["ticker"].tolist(), ["000660", "005930"])
        self.assertEqual([call.kwargs["params"]["index"] for call in get.call_args_list], [0, 1])
        self.assertTrue(all(call.kwargs["params"]["size"] == 100 for call in get.call_args_list))

    def test_full_naver_universe_does_not_call_limited_kis_rank(self):
        rows = pd.DataFrame({
            "ticker": [f"{ticker:06d}" for ticker in range(200)],
            "security_name": "Company",
            "close": 5000.0,
            "volume": 1000.0,
            "value": [float(ticker + 1) for ticker in range(200)],
        })
        client = Mock()
        with patch.object(kospi_api, "fetch_naver_current_value", return_value=rows):
            selected = kospi_api.fetch_top_by_current_value(client)
        self.assertEqual(len(selected), 200)
        client.volume_rank.assert_not_called()

    def test_small_universe_fails_instead_of_publishing_misleading_result(self):
        client = Mock()
        client.volume_rank.return_value = pd.DataFrame(columns=kospi_api.SELECTED_COLUMNS)
        with patch.object(kospi_api, "fetch_naver_current_value", return_value=pd.DataFrame(columns=kospi_api.NAVER_COLUMNS)):
            with self.assertRaisesRegex(RuntimeError, "too small"):
                kospi_api.fetch_top_by_current_value(client)


if __name__ == "__main__":
    unittest.main()

