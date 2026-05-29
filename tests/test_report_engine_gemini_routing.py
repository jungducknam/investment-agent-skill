import unittest
from unittest.mock import patch


class ReportEngineGeminiRoutingTest(unittest.TestCase):
    def test_generate_investment_report_uses_direct_gemini_when_key_configured(self):
        from src import report_engine

        ctx = {
            "collected_at": "2026-05-20T07:00:00+09:00",
            "report_type": "DAILY",
            "report_policy": {"execution_policy": "normal"},
            "entry_signals": {},
            "flow_summary": "",
            "historical_news_context": "",
            "indices": {},
            "verified_events": [],
            "headlines": [],
        }

        with patch.object(report_engine, "GEMINI_API_KEY", "test-key", create=True), \
             patch.object(report_engine, "collect_realtime_data", return_value=ctx), \
             patch.object(report_engine, "build_report_user_prompt", return_value="user prompt"), \
             patch.object(report_engine, "generate_gemini_report_text", return_value='{"recommendations": []}', create=True) as gemini_call, \
             patch.object(report_engine, "apply_recommendation_safety_controls"), \
             patch("src.ai_client.get_client", side_effect=AssertionError("Manus client should not be used")):
            report = report_engine.generate_investment_report()

        self.assertEqual(report["recommendations"], [])
        gemini_call.assert_called_once()

    def test_generate_investment_report_falls_back_when_gemini_returns_invalid_json(self):
        from src import report_engine

        class _Message:
            content = '{"recommendations": [{"ticker": "MSFT"}]}'

        class _Choice:
            message = _Message()

        class _Completions:
            def create(self, **kwargs):
                self.kwargs = kwargs
                return type("Resp", (), {"choices": [_Choice()]})()

        class _Chat:
            def __init__(self):
                self.completions = _Completions()

        class _Client:
            def __init__(self):
                self.chat = _Chat()

        ctx = {
            "collected_at": "2026-05-20T07:00:00+09:00",
            "report_type": "DAILY",
            "report_policy": {"execution_policy": "normal"},
            "entry_signals": {},
            "flow_summary": "",
            "historical_news_context": "",
            "indices": {},
            "verified_events": [],
            "headlines": [],
        }
        client = _Client()

        with patch.object(report_engine, "GEMINI_API_KEY", "test-key", create=True), \
             patch.object(report_engine, "collect_realtime_data", return_value=ctx), \
             patch.object(report_engine, "build_report_user_prompt", return_value="user prompt"), \
             patch.object(report_engine, "generate_gemini_report_text", return_value="not json", create=True), \
             patch.object(report_engine, "apply_recommendation_safety_controls"), \
             patch("src.ai_client.get_client", return_value=client):
            report = report_engine.generate_investment_report()

        self.assertEqual(report["recommendations"][0]["ticker"], "MSFT")


if __name__ == "__main__":
    unittest.main()
