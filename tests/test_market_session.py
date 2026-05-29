import unittest
from datetime import datetime

from src.config import KST
from src.market_session import build_market_session_status
from src.recommendation_safety import apply_recommendation_safety_controls


class MarketSessionTest(unittest.TestCase):
    def test_us_pre_open_after_memorial_day_holiday(self):
        status = build_market_session_status(KST.localize(datetime(2026, 5, 26, 7, 0)))

        us = status["US"]
        self.assertEqual(us["status"], "pre_open_after_holiday")
        self.assertTrue(us["previous_session_was_holiday"])
        self.assertTrue(us["regular_open_confirmation_required"])
        self.assertIn("2026-05-26T22:30:00+09:00", us["next_open_kst"])

    def test_us_holiday_stale_price_demotes_executable_recommendation(self):
        report = {
            "recommendations": [
                {
                    "ticker": "PLTR",
                    "name": "Palantir",
                    "market": "US",
                    "currency": "USD",
                    "style": "스윙",
                    "confidence_score": 72,
                    "investment_rationale": ["AI 데이터 수요가 견조하다."],
                }
            ],
        }
        ctx = {
            "collected_at": KST.localize(datetime(2026, 5, 26, 7, 0)).isoformat(),
            "stock_prices": {"PLTR": {"price": 137.0, "source": "Yahoo", "name": "Palantir", "market": "US"}},
            "entry_signals": {
                "PLTR": {
                    "current_price": 137.0,
                    "signal": "적정",
                    "rsi": 55.0,
                    "bb_position": 50.0,
                    "atr_14": 6.0,
                    "support_20d": 129.0,
                    "resistance_20d": 153.0,
                    "reasons": ["RSI 중립", "BB 중심선 부근"],
                }
            },
            "detailed_news": [{"title": "Palantir AI demand remains in focus", "source": "Reuters_Biz"}],
        }

        apply_recommendation_safety_controls(report, ctx)

        rec = report["recommendations"][0]
        self.assertFalse(rec["is_executable"])
        self.assertEqual(rec["action_status"], "conditional_entry")
        self.assertIn("us_regular_open_confirmation_required", rec["execution_blockers"])
        self.assertIn("us_market_holiday_stale_price", rec["execution_blockers"])


if __name__ == "__main__":
    unittest.main()
