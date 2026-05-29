import unittest

from src.recommendation_safety import apply_recommendation_safety_controls


class ReportEnginePriceHydrationTest(unittest.TestCase):
    def test_missing_ai_prices_are_calculated_by_rule_engine_when_basis_exists(self):
        report = {
            "recommendations": [
                {
                    "ticker": "079550.KS",
                    "name": "LIG넥스원",
                    "market": "KR",
                    "currency": "KRW",
                    "current_price": None,
                    "entry_price": None,
                    "target_price_1": None,
                    "target_price_2": None,
                    "stop_loss": None,
                    "upside_pct": None,
                    "confidence_score": 72,
                    "style": "스윙",
                    "investment_rationale": ["방산 수주 모멘텀이 이어지고 있다."],
                },
            ]
        }
        ctx = {
            "stock_prices": {"079550": {"price": 100000.0, "source": "KIS", "name": "LIG넥스원"}},
            "entry_signals": {
                "079550.KS": {
                    "current_price": 100000.0,
                    "signal": "적정",
                    "rsi": 55.0,
                    "bb_position": 50.0,
                    "atr_14": 4000.0,
                    "support_20d": 94000.0,
                    "resistance_20d": 112000.0,
                    "reasons": ["RSI 중립", "BB 중심선 부근"],
                }
            },
            "detailed_news": [
                {
                    "title": "방산 수주 기대가 이어졌다.",
                    "source": "Reuters_Biz",
                    "themes": ["방산"],
                    "link": "https://example.com/defense",
                }
            ],
        }

        apply_recommendation_safety_controls(report, ctx)

        rec = report["recommendations"][0]
        self.assertEqual(rec["current_price"], 100000.0)
        self.assertEqual(rec["entry_price"], 100000.0)
        self.assertEqual(rec["price_source"], "calculated_by_rule_engine")
        self.assertEqual(rec["risk_gate_status"], "PASS")
        self.assertTrue(rec["is_executable"])
        self.assertGreaterEqual(rec["risk_reward_1"], 1.5)
        self.assertLessEqual(rec["position_size_pct"], 4.0)
        self.assertIn("ATR", rec["price_basis"]["stop"])
        self.assertGreaterEqual(len(rec["evidence_ids"]), 2)

    def test_overbought_candidate_is_demoted_to_rejected_candidates(self):
        report = {
            "recommendations": [
                {
                    "ticker": "NVDA",
                    "name": "NVIDIA",
                    "market": "US",
                    "currency": "USD",
                    "current_price": 180.0,
                    "entry_price": 180.0,
                    "target_price_1": 210.0,
                    "target_price_2": 230.0,
                    "stop_loss": 170.0,
                    "position_size_pct": 5,
                    "confidence_score": 88,
                    "investment_rationale": ["AI 수요가 견조하다."],
                },
            ],
            "waiting_list": [],
        }
        ctx = {
            "stock_prices": {"NVDA": {"price": 180.0, "source": "Yahoo", "name": "NVIDIA"}},
            "entry_signals": {
                "NVDA": {
                    "current_price": 180.0,
                    "signal": "과열",
                    "rsi": 84.0,
                    "bb_position": 118.0,
                    "atr_14": 8.0,
                    "support_20d": 160.0,
                    "resistance_20d": 182.0,
                    "reasons": ["RSI 84 — 극단 과매수"],
                }
            },
        }

        apply_recommendation_safety_controls(report, ctx)

        self.assertEqual(report["recommendations"], [])
        self.assertEqual(report["rejected_candidates"][0]["ticker"], "NVDA")
        self.assertEqual(report["rejected_candidates"][0]["reason"], "overbought")

    def test_us_intraday_report_forces_close_confirmation_condition(self):
        report = {
            "recommendations": [
                {
                    "ticker": "079550.KS",
                    "name": "LIG넥스원",
                    "market": "KR",
                    "currency": "KRW",
                    "style": "스윙",
                    "confidence_score": 72,
                    "investment_rationale": ["방산 수주 모멘텀이 이어지고 있다."],
                },
            ]
        }
        ctx = {
            "report_type": "US_INTRADAY",
            "stock_prices": {"079550": {"price": 100000.0, "source": "KIS", "name": "LIG넥스원"}},
            "entry_signals": {
                "079550.KS": {
                    "current_price": 100000.0,
                    "signal": "적정",
                    "rsi": 55.0,
                    "bb_position": 50.0,
                    "atr_14": 4000.0,
                    "support_20d": 94000.0,
                    "resistance_20d": 112000.0,
                    "reasons": ["RSI 중립", "BB 중심선 부근"],
                }
            },
        }

        apply_recommendation_safety_controls(report, ctx)

        rec = report["recommendations"][0]
        self.assertEqual(rec["risk_gate_status"], "PASS_WITH_CONDITIONS")
        self.assertEqual(rec["action_status"], "conditional_entry")
        self.assertFalse(rec["is_executable"])
        self.assertIn("intraday_report_close_confirmation_required", rec["execution_blockers"])

    def test_abnormally_high_risk_reward_is_rejected(self):
        report = {
            "recommendations": [
                {
                    "ticker": "NVDA",
                    "name": "NVIDIA",
                    "market": "US",
                    "currency": "USD",
                    "style": "스윙",
                    "confidence_score": 88,
                    "investment_rationale": ["AI 수요가 견조하다."],
                },
            ],
        }
        ctx = {
            "stock_prices": {"NVDA": {"price": 100.0, "source": "Yahoo", "name": "NVIDIA"}},
            "entry_signals": {
                "NVDA": {
                    "current_price": 100.0,
                    "signal": "적정",
                    "rsi": 55.0,
                    "bb_position": 50.0,
                    "atr_14": 1.0,
                    "support_20d": 99.0,
                    "resistance_20d": 130.0,
                    "reasons": ["RSI 중립", "BB 중심선 부근"],
                }
            },
        }

        apply_recommendation_safety_controls(report, ctx)

        self.assertEqual(report["recommendations"], [])
        self.assertEqual(report["rejected_candidates"][0]["reason"], "risk_reward_abnormally_high")

    def test_sentiment_score_is_normalized_to_ten_point_scale(self):
        report = {
            "market_summary": {
                "overall_sentiment": "약세",
                "sentiment_score": 18.4,
            },
            "recommendations": [],
        }
        ctx = {
            "market_regime": {
                "global_regime": "crisis",
                "risk_score": 18.4,
                "risk_budget": {"max_executable_recommendations": 0},
            },
        }

        apply_recommendation_safety_controls(report, ctx)

        self.assertEqual(report["market_summary"]["sentiment_score"], 1.8)

    def test_existing_vague_rejected_candidate_gets_gate_details(self):
        report = {
            "recommendations": [],
            "rejected_candidates": [
                {
                    "ticker": "NVDA",
                    "name": "NVIDIA",
                    "reason": "리스크 게이트 미통과",
                }
            ],
        }
        ctx = {
            "stock_prices": {"NVDA": {"price": 180.0, "source": "Yahoo", "name": "NVIDIA"}},
            "entry_signals": {
                "NVDA": {
                    "current_price": 180.0,
                    "signal": "과열",
                    "rsi": 84.0,
                    "bb_position": 118.0,
                    "atr_14": 8.0,
                    "support_20d": 160.0,
                    "resistance_20d": 182.0,
                    "reasons": ["RSI 84 — 극단 과매수"],
                }
            },
        }

        apply_recommendation_safety_controls(report, ctx)

        rejected = report["rejected_candidates"][0]
        self.assertEqual(rejected["reason"], "overbought")
        self.assertIn("overbought", rejected["failed_rules"])


if __name__ == "__main__":
    unittest.main()
