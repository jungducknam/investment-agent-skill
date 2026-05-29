import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from src import market_memory
from src import snapshot_collector


class DailySummaryTest(unittest.TestCase):
    def test_daily_summary_ignores_missing_regime_scores(self):
        original_memory_db = market_memory.MEMORY_DB
        original_snapshot_db = snapshot_collector.SNAPSHOT_DB

        with TemporaryDirectory() as tmpdir:
            market_memory.MEMORY_DB = Path(tmpdir) / "market_memory.db"
            snapshot_collector.SNAPSHOT_DB = Path(tmpdir) / "snapshots.db"
            market_memory.init_memory_db()
            snapshot_collector.init_snapshot_db()

            conn = sqlite3.connect(str(snapshot_collector.SNAPSHOT_DB))
            try:
                self._insert_snapshot(conn, "2026-05-14T08:00:00+09:00", None, -10.0)
                self._insert_snapshot(conn, "2026-05-14T10:00:00+09:00", -20.0, -15.0)
                conn.commit()
            finally:
                conn.close()

            summary = market_memory.generate_daily_summary("2026-05-14")

            self.assertEqual(summary["date"], "2026-05-14")
            self.assertEqual(summary["regime_score_kr"], -20.0)
            self.assertEqual(summary["regime_score_us"], -12.5)

        market_memory.MEMORY_DB = original_memory_db
        snapshot_collector.SNAPSHOT_DB = original_snapshot_db

    def _insert_snapshot(self, conn, timestamp, regime_score_kr, regime_score_us):
        indices = {
            "KOSPI": {"price": 2600},
            "KOSDAQ": {"price": 780},
            "SP500": {"price": 5200},
            "NASDAQ": {"price": 16500},
        }
        sectors = {
            "반도체": {"ret_5d": -1.2, "ret_20d": 2.1},
            "전력인프라": {"ret_5d": 0.8, "ret_20d": 4.5},
        }
        conn.execute(
            """
            INSERT INTO market_snapshots
            (timestamp, snapshot_type, indices_json, sector_momentum_json,
             stock_prices_json, regime_kr, regime_us, regime_score_kr,
             regime_score_us, cash_recommendation, collection_duration_sec, error_count)
            VALUES (?, 'hourly', ?, ?, '{}', '약세', '조정', ?, ?, 20, 0.1, 0)
            """,
            (
                timestamp,
                json.dumps(indices, ensure_ascii=False),
                json.dumps(sectors, ensure_ascii=False),
                regime_score_kr,
                regime_score_us,
            ),
        )


if __name__ == "__main__":
    unittest.main()
