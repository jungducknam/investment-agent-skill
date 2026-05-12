import unittest

from tests.test_agent_adapter import SAMPLE_CONTEXT


class ReportPromptTests(unittest.TestCase):
    def test_build_report_prompt_returns_system_and_user_prompt(self):
        from src.report_engine import build_report_prompt

        system_prompt, user_prompt = build_report_prompt(SAMPLE_CONTEXT)

        self.assertIn("글로벌 투자 전략가", system_prompt)
        self.assertIn("실시간 주요 지수", user_prompt)
        self.assertIn("AI 인프라 투자 확대", user_prompt)
        self.assertIn('"recommendations"', user_prompt)

    def test_build_report_prompt_accepts_extra_context_string(self):
        from src.report_engine import build_report_prompt

        _, user_prompt = build_report_prompt("추가 관찰: 반도체 수급 개선")

        self.assertIn("추가 관찰: 반도체 수급 개선", user_prompt)
        self.assertIn("JSON 투자 리포트", user_prompt)


if __name__ == "__main__":
    unittest.main()
