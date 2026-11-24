import sys
import unittest
from datetime import date
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch
from zipfile import ZipFile

import pandas as pd
import requests


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
        with patch.object(kospi_api, "fetch_naver_current_value", return_value=pd.DataFrame(columns=kospi_api.NAVER_COLUMNS)), \
                patch.object(kospi_api, "fetch_kis_kospi_master", side_effect=requests.Timeout):
            with self.assertRaisesRegex(RuntimeError, "too small"):
                kospi_api.fetch_top_by_current_value(client)

    def test_kis_master_excludes_etf_preferred_and_suspended(self):
        def master_line(ticker, group="ST", preferred="0", flags="NN"):
            fields = list(" " * 227)
            fields[:2] = group
            fields[29] = "N"
            fields[41:50] = "000005000"
            fields[60:62] = flags
            fields[81:93] = "000002000000"
            fields[158] = preferred
            return f"{ticker:9}KR7005930003Company" + "".join(fields)

        buf = BytesIO()
        with ZipFile(buf, "w") as archive:
            archive.writestr("kospi_code.mst", "\r\n".join([
                master_line("005930"), master_line("069500", group="EF"),
                master_line("005935", preferred="1"), master_line("123456", flags="YN"),
            ]).encode("cp949"))
        parsed = kospi_api.parse_kis_kospi_master(buf.getvalue())
        self.assertEqual(parsed.ticker.tolist(), ["005930"])
        self.assertEqual(parsed.adv.tolist(), [10_000_000_000])

    def test_verified_master_covers_both_listing_outages(self):
        master = pd.DataFrame({"ticker": [f"{i:06d}" for i in range(220)], "security_name": "KIS name",
                               "close": 5000.0, "adv": [i * 1e9 for i in range(1, 221)]})
        client = Mock()
        with patch.object(kospi_api, "fetch_naver_current_value", return_value=pd.DataFrame(columns=kospi_api.NAVER_COLUMNS)), \
                patch.object(kospi_api, "fetch_kind_listing", side_effect=requests.HTTPError), \
                patch.object(kospi_api, "fetch_kis_kospi_master", return_value=master):
            selected, sectors = kospi_api.select_kospi_universe(client)
        self.assertEqual(len(selected), 200)
        self.assertEqual(selected.ticker.iloc[0], "000219")
        self.assertEqual(sectors, {})
        client.volume_rank.assert_not_called()


if __name__ == "__main__":
    unittest.main()
