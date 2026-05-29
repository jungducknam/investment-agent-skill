import math
import unittest
from unittest.mock import patch

import pandas as pd

from src import data_yahoo


class FakeTicker:
    info = {
        "shortName": "Test Stock",
        "currentPrice": math.nan,
        "regularMarketPrice": None,
        "fiftyTwoWeekHigh": 150.0,
        "fiftyTwoWeekLow": 90.0,
    }

    def __init__(self, ticker):
        self.ticker = ticker

    def history(self, period=None):
        return pd.DataFrame({"Close": [100.0 + i for i in range(20)] + [math.nan]})


class DataYahooFallbackTest(unittest.TestCase):
    def test_insights_fall_back_to_last_regular_close_when_info_price_is_nan(self):
        with patch.object(data_yahoo.yf, "Ticker", FakeTicker):
            insights = data_yahoo.get_stock_insights("TEST", "US")

        self.assertEqual(insights["current_price"], 119.0)
        self.assertTrue(math.isfinite(insights["52w_position"]))
        self.assertTrue(math.isfinite(insights["rsi_14"]))


if __name__ == "__main__":
    unittest.main()
