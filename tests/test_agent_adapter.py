import unittest


SAMPLE_CONTEXT = {
    "indices": {
        "KOSPI": {"price": 2800.0, "change_pct": 1.2},
        "KOSDAQ": {"price": 900.0, "change_pct": -0.4},
        "SP500": {"price": 5600.0, "change_pct": 0.5},
        "NASDAQ": {"price": 18000.0, "change_pct": 0.7},
    },
    "stock_prices": {
        "005930": {
            "name": "삼성전자",
            "market": "KR",
            "sector": "반도체",
            "price": 70000,
            "change_pct": 1.1,
        }
    },
    "sector_mom": {"반도체(KR)": {"ret_5d": 2.0, "ret_20d": 4.0, "momentum": "WARMING"}},
    "stock_news": {"005930": "반도체 수요 회복"},
    "theme_news": {"반도체": ["메모리 가격 반등"]},
    "headlines": ["AI 인프라 투자 확대"],
    "calendar": {"economic_events": ["FOMC"], "earnings": []},
    "yahoo_text": "삼성전자 목표가 상향",
    "entry_signals": {
        "005930": {
            "name": "삼성전자",
            "signal": "적정",
            "emoji": "🟢",
            "score": 82,
            "rsi": 55,
            "bb_position": 48,
            "reasons": ["RSI 안정", "거래량 양호"],
            "suggested_entry": 69500,
        }
    },
    "collected_at": "2026-05-12T16:00:00+09:00",
}


class AgentAdapterTests(unittest.TestCase):
    def test_report_request_uses_agent_mode_without_ai_call(self):
        from src.agent_adapter import build_report_request

        request = build_report_request(context=SAMPLE_CONTEXT)

        self.assertEqual(request["mode"], "agent")
        self.assertEqual(request["task"], "investment_report")
        self.assertIn("system_prompt", request)
        self.assertIn("user_prompt", request)
        self.assertIn("사람이 읽을 수 있는 한국어 Markdown 리포트", request["user_prompt"])
        self.assertIn("원시 JSON만 출력하지 마세요", request["user_prompt"])
        self.assertNotIn("strict JSON", request["expected_output"])
        self.assertIn("삼성전자", request["user_prompt"])
        self.assertEqual(request["data"]["collected_at"], SAMPLE_CONTEXT["collected_at"])

    def test_chat_request_exposes_prompts_for_external_agent(self):
        from src.agent_adapter import build_chat_request

        request = build_chat_request("오늘 리포트 요약해줘", context="KOSPI 상승")

        self.assertEqual(request["mode"], "agent")
        self.assertEqual(request["task"], "investment_chat")
        self.assertIn("현재 기준 시각", request["system_prompt"])
        self.assertIn("질문: 오늘 리포트 요약해줘", request["user_prompt"])
        self.assertIn("KOSPI 상승", request["user_prompt"])

    def test_position_review_request_includes_rule_baseline(self):
        from src.agent_adapter import build_position_review_request

        pos = {
            "id": 1,
            "direction": "long",
            "name": "NVIDIA",
            "ticker": "NVDA",
            "market": "US",
            "entry_price": 100.0,
            "quantity": 2.0,
            "currency": "USD",
        }
        request = build_position_review_request(pos, current_price=112.0)

        self.assertEqual(request["task"], "position_review")
        self.assertEqual(request["data"]["pnl"]["pnl_pct"], 12.0)
        self.assertEqual(request["data"]["rule_baseline"]["source"], "rule")
        self.assertIn("JSON만 답변", request["user_prompt"])


if __name__ == "__main__":
    unittest.main()
