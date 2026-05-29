"""
news_event_store.py — SQLite persistence for classified news events.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DB_PATH, KST


NEWS_EVENTS_SQL = """
CREATE TABLE IF NOT EXISTS news_events (
    news_id TEXT PRIMARY KEY,
    published_at_kst TEXT,
    source TEXT,
    headline TEXT,
    summary TEXT,
    url TEXT,
    primary_theme TEXT,
    secondary_themes TEXT,
    event_type TEXT,
    event_status TEXT,
    market_relevance_score REAL,
    novelty_score REAL,
    urgency_score REAL,
    confidence REAL,
    impact_score REAL,
    historical_impact_score REAL,
    current_market_reaction_score REAL,
    should_include_in_report INTEGER,
    report_priority TEXT,
    validation_status TEXT,
    validation_errors TEXT,
    created_at TEXT NOT NULL
);
"""


NEWS_ASSET_IMPACTS_SQL = """
CREATE TABLE IF NOT EXISTS news_asset_impacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    news_id TEXT,
    asset TEXT,
    asset_type TEXT,
    impact_scope TEXT,
    expected_direction TEXT,
    impact_strength REAL,
    channel TEXT,
    FOREIGN KEY(news_id) REFERENCES news_events(news_id),
    UNIQUE(news_id, asset, impact_scope)
);
"""


NEWS_EVENT_OUTCOMES_SQL = """
CREATE TABLE IF NOT EXISTS news_event_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    news_id TEXT,
    asset TEXT,
    price_at_event REAL,
    price_1h REAL,
    price_close REAL,
    price_next_close REAL,
    price_5d REAL,
    return_1h REAL,
    return_0d REAL,
    return_1d REAL,
    return_5d REAL,
    sector_return_1d REAL,
    index_return_1d REAL,
    abnormal_return_1d REAL,
    abnormal_return_5d REAL,
    volume_zscore_1d REAL,
    realized_direction TEXT,
    direction_hit INTEGER,
    outcome_checked_at TEXT,
    outcome_status TEXT,
    FOREIGN KEY(news_id) REFERENCES news_events(news_id)
);
"""

NEWS_EVENT_OUTCOMES_INDEX_SQL = """
CREATE UNIQUE INDEX IF NOT EXISTS idx_news_event_outcomes_key
ON news_event_outcomes(news_id, asset);
"""


def ensure_news_event_tables(db_path: Path | str = DB_PATH) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(NEWS_EVENTS_SQL)
        conn.execute(NEWS_ASSET_IMPACTS_SQL)
        conn.execute(NEWS_EVENT_OUTCOMES_SQL)
        conn.execute(NEWS_EVENT_OUTCOMES_INDEX_SQL)
        _ensure_columns(
            conn,
            "news_event_outcomes",
            {
                "outcome_checked_at": "TEXT",
                "outcome_status": "TEXT",
            },
        )
        conn.commit()


def save_news_events(news_items: list[dict[str, Any]], db_path: Path | str = DB_PATH) -> int:
    ensure_news_event_tables(db_path)
    now = datetime.now(KST).isoformat()
    saved = 0
    with sqlite3.connect(str(db_path)) as conn:
        for item in news_items or []:
            news_id = item.get("news_id")
            if not news_id:
                continue
            conn.execute(
                """
                INSERT INTO news_events (
                    news_id, published_at_kst, source, headline, summary, url,
                    primary_theme, secondary_themes, event_type, event_status,
                    market_relevance_score, novelty_score, urgency_score,
                    confidence, impact_score, historical_impact_score,
                    current_market_reaction_score, should_include_in_report,
                    report_priority, validation_status, validation_errors, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(news_id) DO UPDATE SET
                    impact_score = excluded.impact_score,
                    should_include_in_report = excluded.should_include_in_report,
                    report_priority = excluded.report_priority,
                    validation_status = excluded.validation_status,
                    validation_errors = excluded.validation_errors
                """,
                (
                    news_id,
                    item.get("published_at_kst") or item.get("published") or "",
                    item.get("source") or "",
                    item.get("headline") or item.get("title") or "",
                    item.get("summary") or "",
                    item.get("url") or item.get("link") or "",
                    item.get("primary_theme") or "",
                    json.dumps(item.get("secondary_themes") or [], ensure_ascii=False),
                    item.get("event_type") or "",
                    item.get("event_status") or "",
                    _float(item.get("market_relevance_score")),
                    _float(item.get("novelty_score")),
                    _float(item.get("urgency_score")),
                    _float(item.get("confidence")),
                    _float(item.get("impact_score")),
                    _float(item.get("historical_impact_score")),
                    _float(item.get("current_market_reaction_score")),
                    1 if item.get("should_include_in_report") else 0,
                    item.get("report_priority") or "",
                    item.get("validation_status") or "",
                    json.dumps(item.get("validation_errors") or [], ensure_ascii=False),
                    now,
                ),
            )
            for asset in item.get("affected_assets") or []:
                conn.execute(
                    """
                    INSERT INTO news_asset_impacts (
                        news_id, asset, asset_type, impact_scope, expected_direction,
                        impact_strength, channel
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(news_id, asset, impact_scope) DO UPDATE SET
                        expected_direction = excluded.expected_direction,
                        impact_strength = excluded.impact_strength,
                        channel = excluded.channel
                    """,
                    (
                        news_id,
                        asset.get("asset") or "",
                        asset.get("asset_type") or "",
                        asset.get("impact_scope") or "",
                        asset.get("direction") or asset.get("expected_direction") or "",
                        _float(asset.get("impact_strength")),
                        asset.get("channel") or "",
                    ),
                )
            saved += 1
        conn.commit()
    return saved


def _float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ensure_columns(conn: sqlite3.Connection, table: str, columns: dict[str, str]) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, ddl_type in columns.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}")
