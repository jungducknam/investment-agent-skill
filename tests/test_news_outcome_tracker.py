import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from src.news_event_store import ensure_news_event_tables
from src.news_outcome_tracker import (
    calculate_asset_outcome,
    parse_event_time,
    resolve_asset_symbol,
    update_news_event_outcomes,
)


class NewsOutcomeTrackerTest(unittest.TestCase):
    def test_calculate_asset_outcome_from_hourly_and_daily_frames(self):
        event_dt = parse_event_time("2026-05-20T10:00:00+09:00")
        hourly = pd.DataFrame(
            {"Close": [100.0, 102.0, 103.0]},
            index=pd.to_datetime([
                "2026-05-20T10:00:00+09:00",
                "2026-05-20T11:00:00+09:00",
                "2026-05-20T12:00:00+09:00",
            ]),
        )
        daily = pd.DataFrame(
            {
                "Close": [104.0, 108.0, 111.0, 115.0, 118.0, 120.0],
                "Volume": [100, 110, 120, 130, 140, 150],
            },
            index=pd.to_datetime([
                "2026-05-20T00:00:00+09:00",
                "2026-05-21T00:00:00+09:00",
                "2026-05-22T00:00:00+09:00",
                "2026-05-25T00:00:00+09:00",
                "2026-05-26T00:00:00+09:00",
                "2026-05-27T00:00:00+09:00",
            ]),
        )

        outcome = calculate_asset_outcome("TEST", event_dt, history_cache={
            ("TEST", "60d", "1h"): hourly,
            ("TEST", "6mo", "1d"): daily,
        })

        self.assertEqual(outcome.price_at_event, 100.0)
        self.assertEqual(outcome.price_1h, 102.0)
        self.assertEqual(outcome.return_1h, 2.0)
        self.assertEqual(outcome.return_0d, 4.0)
        self.assertEqual(outcome.return_1d, 8.0)
        self.assertEqual(outcome.return_5d, 20.0)

    def test_update_news_event_outcomes_upserts_returns(self):
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "events.db"
            ensure_news_event_tables(db_path)
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO news_events (
                        news_id, published_at_kst, source, headline, primary_theme,
                        secondary_themes, event_type, event_status, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "news_test",
                        "2026-05-20T10:00:00+09:00",
                        "test",
                        "Brent crude test",
                        "energy_inflation",
                        "[]",
                        "macro_risk",
                        "confirmed",
                        "2026-05-20T10:00:00+09:00",
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO news_asset_impacts (
                        news_id, asset, asset_type, impact_scope, expected_direction,
                        impact_strength, channel
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("news_test", "BRENT", "commodity", "direct", "positive", 90, "oil price momentum"),
                )
                conn.commit()

            hourly = pd.DataFrame(
                {"Close": [100.0, 102.0]},
                index=pd.to_datetime(["2026-05-20T10:00:00+09:00", "2026-05-20T11:00:00+09:00"]),
            )
            daily = pd.DataFrame(
                {"Close": [104.0, 108.0, 110.0, 112.0, 114.0, 116.0], "Volume": [100, 110, 120, 130, 140, 150]},
                index=pd.to_datetime([
                    "2026-05-20T00:00:00+09:00",
                    "2026-05-21T00:00:00+09:00",
                    "2026-05-22T00:00:00+09:00",
                    "2026-05-25T00:00:00+09:00",
                    "2026-05-26T00:00:00+09:00",
                    "2026-05-27T00:00:00+09:00",
                ]),
            )

            from src import news_outcome_tracker

            original_history = news_outcome_tracker._history
            try:
                def fake_history(symbol, period, interval, cache):
                    frame = hourly if interval == "1h" else daily
                    cache[(symbol, period, interval)] = frame
                    return frame

                news_outcome_tracker._history = fake_history
                result = update_news_event_outcomes(db_path=db_path, limit=10, report_only=False)
            finally:
                news_outcome_tracker._history = original_history

            self.assertEqual(result["updated"], 1)
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute("SELECT * FROM news_event_outcomes").fetchone()
                self.assertEqual(row["news_id"], "news_test")
                self.assertEqual(row["asset"], "BRENT")
                self.assertEqual(row["return_1h"], 2.0)
                self.assertEqual(row["return_1d"], 8.0)
                self.assertEqual(row["direction_hit"], 1)
                self.assertEqual(row["outcome_status"], "updated")

    def test_recently_checked_partial_outcome_is_throttled(self):
        with TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "events.db"
            ensure_news_event_tables(db_path)
            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    """
                    INSERT INTO news_events (
                        news_id, published_at_kst, source, headline, primary_theme,
                        secondary_themes, event_type, event_status, should_include_in_report, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "news_partial",
                        "2026-05-01T10:00:00+09:00",
                        "test",
                        "Brent crude partial",
                        "energy_inflation",
                        "[]",
                        "macro_risk",
                        "confirmed",
                        1,
                        "2026-05-01T10:00:00+09:00",
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO news_asset_impacts (
                        news_id, asset, asset_type, impact_scope, expected_direction,
                        impact_strength, channel
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    ("news_partial", "BRENT", "commodity", "direct", "positive", 90, "oil price momentum"),
                )
                conn.commit()

            hourly = pd.DataFrame(
                {"Close": [100.0, 102.0]},
                index=pd.to_datetime(["2026-05-01T10:00:00+09:00", "2026-05-01T11:00:00+09:00"]),
            )
            daily = pd.DataFrame(
                {"Close": [104.0, 108.0], "Volume": [100, 110]},
                index=pd.to_datetime(["2026-05-01T00:00:00+09:00", "2026-05-04T00:00:00+09:00"]),
            )

            from src import news_outcome_tracker

            original_history = news_outcome_tracker._history
            try:
                def fake_history(symbol, period, interval, cache):
                    frame = hourly if interval == "1h" else daily
                    cache[(symbol, period, interval)] = frame
                    return frame

                news_outcome_tracker._history = fake_history
                first = update_news_event_outcomes(db_path=db_path, limit=10, report_only=True)
                second = update_news_event_outcomes(db_path=db_path, limit=10, report_only=True)
            finally:
                news_outcome_tracker._history = original_history

            self.assertEqual(first["updated"], 1)
            self.assertEqual(second["checked"], 0)
            with sqlite3.connect(db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute("SELECT * FROM news_event_outcomes").fetchone()
                self.assertEqual(row["return_1d"], 8.0)
                self.assertIsNone(row["return_5d"])

    def test_symbol_mapping_and_time_parsing(self):
        self.assertEqual(resolve_asset_symbol("NASDAQ", "index"), "^IXIC")
        self.assertEqual(resolve_asset_symbol("005930.KS", "ticker"), "005930.KS")
        self.assertIsNone(resolve_asset_symbol("market", "index"))
        self.assertEqual(parse_event_time("2026-05-20T10:00:00+09:00").tzinfo.zone, "Asia/Seoul")


if __name__ == "__main__":
    unittest.main()
