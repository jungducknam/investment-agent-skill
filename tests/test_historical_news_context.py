import json
import sqlite3
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from src import market_memory, snapshot_collector
from src.config import KST
from src.report_engine import (
    build_report_input_payload,
    build_report_user_prompt,
    get_historical_news_context,
)


class HistoricalNewsContextTest(unittest.TestCase):
    def test_context_combines_recent_archive_and_daily_summary_news(self):
        original_memory_db = market_memory.MEMORY_DB
        original_snapshot_db = snapshot_collector.SNAPSHOT_DB

        with TemporaryDirectory() as tmpdir:
            market_memory.MEMORY_DB = Path(tmpdir) / "market_memory.db"
            snapshot_collector.SNAPSHOT_DB = Path(tmpdir) / "snapshots.db"
            market_memory.init_memory_db()
            snapshot_collector.init_snapshot_db()
            self._insert_daily_summary_news()
            self._insert_archived_news()

            context = get_historical_news_context(
                days=7,
                max_items=5,
                now=KST.localize(datetime(2026, 5, 16, 7, 0)),
            )

            self.assertIn("이전 중요 뉴스", context)
            self.assertIn("엔비디아 차세대 GPU 공급 확대", context)
            self.assertIn("HBM 공급 부족 우려 완화", context)
            self.assertIn("연준 금리 인하 기대 후퇴", context)
            self.assertNotIn("한 달 전 뉴스", context)

        market_memory.MEMORY_DB = original_memory_db
        snapshot_collector.SNAPSHOT_DB = original_snapshot_db

    def test_report_prompt_uses_structured_json_payload(self):
        ctx = {
            "indices": {},
            "flow_summary": "",
            "memory_context": "",
            "entry_signals": {},
            "sector_mom": {},
            "headlines": [],
            "detailed_news": [
                {
                    "title": "엔비디아 차세대 GPU 공급 확대",
                    "source": "Reuters_Biz",
                    "published": "05/16 06:30",
                    "themes": ["AI", "반도체"],
                    "summary": "AI 서버 투자 수요가 이어진다는 내용",
                    "why": "AI 이슈입니다. 빅테크 투자 판단에 영향을 줍니다.",
                    "link": "https://example.com/nvidia",
                }
            ],
            "theme_news": {},
            "calendar": {"economic_events": [], "earnings": []},
            "stock_prices": {},
            "stock_news": {},
            "yahoo_text": "",
            "historical_news_context": "━━━ 이전 중요 뉴스 ━━━\n• [2026-05-14][반도체] HBM 공급 부족 우려 완화",
        }

        payload = build_report_input_payload(ctx, "2026년 05월 16일")
        prompt = build_report_user_prompt(ctx, "2026년 05월 16일")
        prompt_payload = self._extract_prompt_payload(prompt)

        self.assertEqual(payload["metadata"]["report_date"], "2026년 05월 16일")
        self.assertEqual(prompt_payload["metadata"]["report_date"], "2026년 05월 16일")
        self.assertIn("deterministic_layer", prompt_payload)
        self.assertIn("market_data", prompt_payload)
        self.assertIn("news", prompt_payload)
        self.assertIn("output_schema", prompt_payload)
        self.assertIn(
            "HBM 공급 부족 우려 완화",
            prompt_payload["market_context"]["historical_news"]["raw_text"],
        )
        news = prompt_payload["news"]["selected_report_news"][0]
        self.assertEqual(news["title"], "엔비디아 차세대 GPU 공급 확대")
        self.assertEqual(news["source"], "Reuters_Biz")
        self.assertEqual(news["primary_theme"], "semiconductor")
        self.assertEqual(news["investment_implication"], "반도체 뉴스는 HBM, 메모리, 장비, 파운드리, AI 서버 체인과 한국/미국 기술주 수급에 영향을 줍니다.")
        self.assertEqual(news["impact_channel"], "semiconductor supply-demand repricing")
        self.assertEqual(news["affected_assets"][0]["asset"], "SOXX")
        self.assertEqual(news["url"], "https://example.com/nvidia")
        rec_schema = prompt_payload["output_schema"]["recommendations"][0]
        self.assertIsNone(rec_schema["entry_price"])
        self.assertIsNone(rec_schema["position_size_pct"])
        self.assertIn("invalidation_condition", rec_schema)

    def _extract_prompt_payload(self, prompt: str) -> dict:
        marker = "REPORT_INPUT_JSON:"
        self.assertIn(marker, prompt)
        raw = prompt.split(marker, 1)[1].strip()
        return json.loads(raw)

    def _insert_daily_summary_news(self):
        conn = sqlite3.connect(str(market_memory.MEMORY_DB))
        try:
            news_themes = {
                "반도체": {"count": 4, "headlines": ["HBM 공급 부족 우려 완화", "AI 서버 메모리 수요 증가"]},
                "금리": {"count": 2, "headlines": ["연준 금리 인하 기대 후퇴"]},
            }
            conn.execute(
                """
                INSERT INTO daily_summaries
                (date, created_at, regime_kr, regime_us, regime_score_kr, regime_score_us,
                 news_themes_json, narrative, hourly_count, data_quality_score)
                VALUES (?, ?, '횡보', '조정', 0, -5, ?, '테스트 내러티브', 12, 1.0)
                """,
                ("2026-05-14", "2026-05-15T07:00:00+09:00", json.dumps(news_themes, ensure_ascii=False)),
            )
            conn.commit()
        finally:
            conn.close()

    def _insert_archived_news(self):
        conn = sqlite3.connect(str(snapshot_collector.SNAPSHOT_DB))
        try:
            conn.execute(
                """
                INSERT INTO news_archive
                (timestamp, headline, source, theme, hash, relevance_score)
                VALUES (?, ?, 'Reuters', 'AI', 'recent-1', 0.95)
                """,
                ("2026-05-15T22:00:00+09:00", "엔비디아 차세대 GPU 공급 확대"),
            )
            conn.execute(
                """
                INSERT INTO news_archive
                (timestamp, headline, source, theme, hash, relevance_score)
                VALUES (?, ?, 'Reuters', 'AI', 'old-1', 0.99)
                """,
                ("2026-04-01T09:00:00+09:00", "한 달 전 뉴스"),
            )
            conn.commit()
        finally:
            conn.close()


if __name__ == "__main__":
    unittest.main()
