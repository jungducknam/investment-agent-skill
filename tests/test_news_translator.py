import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from src.news_translator import (
    ensure_translation_cache,
    should_translate_news,
    translate_report_news_items,
)
from src.report_formatter import build_msg1


class NewsTranslatorTest(unittest.TestCase):
    def test_should_translate_only_foreign_news(self):
        self.assertTrue(should_translate_news("Crude oil edges lower", "Traders await talks"))
        self.assertFalse(should_translate_news("삼성전자 노조 파업 예정", "반도체 생산 차질 우려"))

    def test_translate_report_news_items_uses_cache(self):
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "translations.db"
            calls = []

            def fake_translate(texts, **kwargs):
                calls.append(texts)
                return ["원유 가격이 소폭 하락했다", "거래자들은 협상 결과를 기다리고 있다"]

            with patch("src.news_translator.deepl_translate_texts", side_effect=fake_translate):
                first = translate_report_news_items(
                    [
                        {
                            "title": "Crude oil edges lower",
                            "summary": "Traders await clarity.",
                            "source": "Reuters",
                        }
                    ],
                    db_path=db_path,
                    api_key="test-key",
                )
                second = translate_report_news_items(
                    [
                        {
                            "title": "Crude oil edges lower",
                            "summary": "Traders await clarity.",
                            "source": "Reuters",
                        }
                    ],
                    db_path=db_path,
                    api_key="test-key",
                )

            self.assertEqual(first[0]["translated_title"], "원유 가격이 소폭 하락했다")
            self.assertEqual(second[0]["translated_title"], "원유 가격이 소폭 하락했다")
            self.assertEqual(len(calls), 1)

            ensure_translation_cache(db_path)
            with sqlite3.connect(db_path) as conn:
                count = conn.execute("SELECT COUNT(*) FROM news_translation_cache").fetchone()[0]
            self.assertEqual(count, 1)

    def test_report_formatter_prefers_translated_news_title(self):
        report = {
            "report_date": "2026년 05월 20일",
            "market_summary": {
                "overall_sentiment": "약세",
                "sentiment_score": 1.8,
                "key_theme": "금리 부담",
                "sector_rotation": "방어적 접근.",
                "risk_factors": ["금리 상승"],
            },
            "portfolio_strategy": {
                "cash_reserve_pct": 70,
                "long_term_allocation": "대기",
                "swing_strategy": "대기",
                "daytrading_focus": "제한",
                "overall_advice": "신규 매수 금지",
            },
            "_indices": {},
            "_events": [],
            "_detailed_news": [
                {
                    "title": "Crude oil edges lower",
                    "translated_title": "원유 가격이 소폭 하락했다",
                    "summary": "Traders await clarity.",
                    "translated_summary": "거래자들은 협상 결과를 기다리고 있다.",
                    "source": "Reuters",
                    "primary_theme": "energy_inflation",
                    "secondary_themes": ["rates_policy"],
                    "impact_score": 72,
                }
            ],
        }

        message = build_msg1(report)

        self.assertIn("원유 가격이 소폭 하락했다", message)
        self.assertIn("원제: Crude oil edges lower", message)
        self.assertIn("요약: 거래자들은 협상 결과를 기다리고 있다.", message)


if __name__ == "__main__":
    unittest.main()
