import unittest
import json

from tests.test_agent_adapter import SAMPLE_CONTEXT


class ReportPromptTests(unittest.TestCase):
    def test_build_report_prompt_returns_system_and_user_prompt(self):
        from src.report_engine import build_report_prompt

        system_prompt, user_prompt = build_report_prompt(SAMPLE_CONTEXT)

        self.assertIn("투자 브리핑 작성자", system_prompt)
        self.assertIn("REPORT_INPUT_JSON", user_prompt)
        payload = self._extract_report_payload(user_prompt)
        self.assertEqual(payload["metadata"]["prompt_version"], "v4_structured_json_input")
        self.assertIn("deterministic_layer", payload)
        self.assertIn("market_data", payload)
        self.assertIn("output_schema", payload)
        self.assertIn("recommendations", payload["output_schema"])

    def test_build_report_prompt_accepts_extra_context_string(self):
        from src.report_engine import build_report_prompt

        _, user_prompt = build_report_prompt("추가 관찰: 반도체 수급 개선")

        payload = self._extract_report_payload(user_prompt)
        self.assertEqual(payload["market_context"]["extra_context"], "추가 관찰: 반도체 수급 개선")
        self.assertTrue(payload["instructions"]["do_not_generate_execution_numbers"])

    def _extract_report_payload(self, prompt: str) -> dict:
        marker = "REPORT_INPUT_JSON:"
        self.assertIn(marker, prompt)
        raw = prompt.split(marker, 1)[1].lstrip()
        payload, _ = json.JSONDecoder().raw_decode(raw)
        return payload


if __name__ == "__main__":
    unittest.main()
