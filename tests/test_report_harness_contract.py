import json
import unittest


SAMPLE_CONTEXT = {
    "indices": {
        "KOSPI": {"price": 2800.0, "change_pct": 1.2},
        "NASDAQ": {"price": 18000.0, "change_pct": 0.7},
    },
    "stock_prices": {
        "005930": {
            "name": "삼성전자",
            "market": "KR",
            "sector": "반도체",
            "price": 70000,
            "change_pct": 1.1,
            "source": "KIS",
            "volume": 1000000,
        }
    },
    "sector_mom": {"반도체(KR)": {"ret_5d": 2.0, "ret_20d": 4.0, "momentum": "WARMING"}},
    "stock_news": {"005930": "반도체 수요 회복"},
    "theme_news": {"반도체": ["메모리 가격 반등"]},
    "headlines": ["AI 인프라 투자 확대"],
    "detailed_news": [
        {
            "title": "엔비디아 차세대 GPU 공급 확대",
            "source": "Reuters_Biz",
            "published": "05/29 06:30",
            "themes": ["AI", "반도체"],
            "summary": "AI 서버 투자 수요가 이어진다는 내용",
            "why": "AI 이슈입니다.",
            "link": "https://example.com/nvidia",
        }
    ],
    "calendar": {"economic_events": ["FOMC"], "earnings": []},
    "yahoo_text": "삼성전자 목표가 상향",
    "historical_news_context": "━━━ 이전 중요 뉴스 ━━━\n• [2026-05-28][반도체] HBM 공급 부족 우려 완화",
    "entry_signals": {
        "005930": {
            "name": "삼성전자",
            "signal": "적정",
            "emoji": "🟢",
            "score": 82,
            "current_price": 70000,
            "rsi": 55,
            "bb_position": 48,
            "atr_14": 1800,
            "support_20d": 68000,
            "resistance_20d": 76000,
            "reasons": ["RSI 안정", "거래량 양호"],
            "suggested_entry": 69500,
        }
    },
    "collected_at": "2026-05-29T07:00:00+09:00",
}


class ReportHarnessContractTests(unittest.TestCase):
    def test_report_prompt_uses_structured_json_payload_and_execution_layer(self):
        from src.report_engine import build_report_input_payload, build_report_user_prompt

        payload = build_report_input_payload(SAMPLE_CONTEXT, "2026년 05월 29일")
        prompt = build_report_user_prompt(SAMPLE_CONTEXT, "2026년 05월 29일")
        prompt_payload = self._extract_prompt_payload(prompt)

        self.assertEqual(payload["metadata"]["prompt_version"], "v4_structured_json_input")
        self.assertEqual(prompt_payload["metadata"]["response_format"], "strict_json")
        self.assertIn("deterministic_layer", prompt_payload)
        self.assertIn("market_session_status", prompt_payload["deterministic_layer"])
        self.assertIn("market_data", prompt_payload)
        self.assertIn("technical_entry", prompt_payload)
        self.assertIn("news", prompt_payload)
        self.assertIn("output_schema", prompt_payload)
        self.assertTrue(prompt_payload["instructions"]["do_not_generate_execution_numbers"])
        self.assertIsNone(prompt_payload["output_schema"]["recommendations"][0]["entry_price"])
        self.assertIsNone(prompt_payload["output_schema"]["recommendations"][0]["position_size_pct"])

    def test_agent_report_request_forbids_outer_agent_from_inventing_execution_numbers(self):
        from src.agent_adapter import build_report_request

        request = build_report_request(context=SAMPLE_CONTEXT)

        self.assertEqual(request["task"], "investment_report")
        self.assertIn("deterministic 실행 레이어", request["user_prompt"])
        self.assertIn("price_engine_output/risk_gate_results", request["user_prompt"])

    def test_recommendation_safety_calculates_prices_and_applies_risk_gate(self):
        from src.recommendation_safety import apply_recommendation_safety_controls

        report = {
            "recommendations": [
                {
                    "ticker": "005930",
                    "name": "삼성전자",
                    "market": "KR",
                    "currency": "KRW",
                    "style": "스윙",
                    "confidence_score": 72,
                    "investment_rationale": ["반도체 수급 개선"],
                }
            ]
        }

        apply_recommendation_safety_controls(report, SAMPLE_CONTEXT)

        rec = report["recommendations"][0]
        self.assertEqual(rec["price_source"], "calculated_by_rule_engine")
        self.assertEqual(rec["risk_gate_status"], "PASS")
        self.assertTrue(rec["is_executable"])
        self.assertGreaterEqual(rec["risk_reward_1"], 1.5)
        self.assertLessEqual(rec["position_size_pct"], 4.0)
        self.assertIn("evidence", report)

    def _extract_prompt_payload(self, prompt: str) -> dict:
        marker = "REPORT_INPUT_JSON:"
        self.assertIn(marker, prompt)
        raw = prompt.split(marker, 1)[1].strip()
        return json.loads(raw)


if __name__ == "__main__":
    unittest.main()
