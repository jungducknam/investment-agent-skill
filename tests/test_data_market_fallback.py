import math
import unittest
from unittest.mock import patch

import pandas as pd

from src import data_market


class FakeTicker:
    def __init__(self, histories):
        self.histories = histories

    def history(self, period=None, interval=None):
        key = (period, interval)
        if key in self.histories:
            return self.histories[key]
        return self.histories[(period, None)]


class DataMarketFallbackTest(unittest.TestCase):
    def test_price_and_change_use_last_valid_regular_close(self):
        hist = pd.DataFrame({"Close": [100.0, 110.0, math.nan]})

        with patch.object(data_market.yf, "Ticker", return_value=FakeTicker({("5d", None): hist})):
            result = data_market._get_price_and_change("TEST")

        self.assertEqual(result["price"], 110.0)
        self.assertEqual(result["change_pct"], 10.0)

    def test_momentum_ignores_trailing_nan_rows(self):
        hist = pd.DataFrame({"Close": [100.0, 102.0, 104.0, 106.0, 108.0, 110.0, math.nan]})

        with patch.object(data_market.yf, "Ticker", return_value=FakeTicker({("1mo", None): hist})):
            result = data_market._get_momentum("TEST")

        self.assertTrue(math.isfinite(result["ret_5d"]))
        self.assertEqual(result["ret_5d"], 7.84)

    def test_realtime_price_falls_back_to_daily_close_when_intraday_is_nan(self):
        intraday = pd.DataFrame({"Close": [math.nan]})
        daily = pd.DataFrame({"Close": [108.0, 110.0]})

        with patch.object(
            data_market.yf,
            "Ticker",
            return_value=FakeTicker({
                ("1d", "1m"): intraday,
                ("5d", None): daily,
            }),
        ):
            price = data_market.get_realtime_price("TEST", "US")

        self.assertEqual(price, 110.0)

    def test_kr_realtime_price_prefers_kis_before_yfinance(self):
        with patch.object(data_market, "get_domestic_stock_price", return_value=72600.0), \
             patch.object(data_market.yf, "Ticker") as yf_ticker:
            price = data_market.get_realtime_price("005930", "KR")

        self.assertEqual(price, 72600.0)
        yf_ticker.assert_not_called()

    def test_kr_universe_price_prefers_kis_quote(self):
        stock = {"name": "삼성전자", "ticker": "005930", "yf": "005930.KS", "market": "KR", "sector": "반도체"}

        with patch.object(data_market, "get_domestic_stock_quote", return_value={"price": 72600.0, "change_pct": 1.4, "source": "KIS"}), \
             patch.object(data_market, "_get_price_and_change") as yf_price:
            result = data_market._get_stock_price_data(stock)

        self.assertEqual(result["source"], "KIS")
        self.assertEqual(result["price"], 72600.0)
        yf_price.assert_not_called()

    def test_indices_prefer_kis_for_kospi_and_kosdaq(self):
        def fake_kis_index(name):
            if name == "KOSPI":
                return {"price": 7493.18, "change_pct": -6.12, "source": "KIS"}
            if name == "KOSDAQ":
                return {"price": 1129.82, "change_pct": -5.14, "source": "KIS"}
            return None

        with patch.object(data_market, "get_domestic_index_quote", side_effect=fake_kis_index), \
             patch.object(data_market, "_get_price_and_change", return_value={"price": 1.0, "change_pct": 0.0}) as yf_price:
            result = data_market.fetch_indices()

        self.assertEqual(result["KOSPI"]["source"], "KIS")
        self.assertEqual(result["KOSPI"]["price"], 7493.18)
        self.assertEqual(result["KOSDAQ"]["source"], "KIS")
        called_yahoo_names = {call.args[0] for call in yf_price.call_args_list}
        self.assertNotIn("^KS11", called_yahoo_names)
        self.assertNotIn("^KQ11", called_yahoo_names)


if __name__ == "__main__":
    unittest.main()
