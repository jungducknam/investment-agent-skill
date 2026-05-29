import math
import unittest

from src.entry_filter import calc_entry_filter


class EntryFilterTest(unittest.TestCase):
    def test_entry_filter_drops_nan_market_rows_before_calculating_indicators(self):
        prices = [100.0 + i for i in range(30)] + [math.nan]
        highs = [p + 1 if math.isfinite(p) else math.nan for p in prices]
        lows = [p - 1 if math.isfinite(p) else math.nan for p in prices]
        volumes = [1000.0 + i for i in range(30)] + [math.nan]

        result = calc_entry_filter(prices, highs=highs, lows=lows, volumes=volumes)

        self.assertTrue(math.isfinite(result.rsi))
        self.assertTrue(math.isfinite(result.bb_position))
        self.assertTrue(all("nan" not in reason.lower() for reason in result.reasons))


if __name__ == "__main__":
    unittest.main()
