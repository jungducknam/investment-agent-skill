"""
news_event_study.py — lightweight similar-event reaction summaries.
"""
from __future__ import annotations

import math
import sqlite3
from pathlib import Path
from statistics import median
from typing import Any

from .config import DB_PATH
from .news_event_store import ensure_news_event_tables


def abnormal_return_simple(asset_return: float, sector_return: float = 0.0, index_return: float = 0.0) -> float:
    return asset_return - 0.5 * sector_return - 0.5 * index_return


def find_similar_event_summary(
    primary_theme: str,
    asset: str,
    expected_direction: str,
    market_regime: str | None = None,
    db_path: Path | str = DB_PATH,
) -> dict[str, Any]:
    ensure_news_event_tables(db_path)
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT o.abnormal_return_1d, o.abnormal_return_5d, o.volume_zscore_1d, o.direction_hit
            FROM news_events e
            JOIN news_asset_impacts a ON a.news_id = e.news_id
            JOIN news_event_outcomes o ON o.news_id = e.news_id AND o.asset = a.asset
            WHERE e.primary_theme = ?
              AND a.asset = ?
              AND a.expected_direction = ?
            ORDER BY e.published_at_kst DESC
            LIMIT 80
            """,
            (primary_theme, asset, expected_direction),
        ).fetchall()

    abnormal_1d = [_num(row["abnormal_return_1d"]) for row in rows if _num(row["abnormal_return_1d"]) is not None]
    abnormal_5d = [_num(row["abnormal_return_5d"]) for row in rows if _num(row["abnormal_return_5d"]) is not None]
    hit_values = [_num(row["direction_hit"]) for row in rows if _num(row["direction_hit"]) is not None]
    volume_values = [_num(row["volume_zscore_1d"]) for row in rows if _num(row["volume_zscore_1d"]) is not None]

    count = len(rows)
    hit_rate = sum(hit_values) / len(hit_values) if hit_values else 0.0
    avg_volume = sum(volume_values) / len(volume_values) if volume_values else 0.0
    median_1d = median(abnormal_1d) if abnormal_1d else 0.0
    median_5d = median(abnormal_5d) if abnormal_5d else 0.0
    return {
        "similar_event_count": count,
        "median_abnormal_return_1d": round(median_1d, 2),
        "median_abnormal_return_5d": round(median_5d, 2),
        "directional_hit_rate": round(hit_rate, 2),
        "avg_volume_zscore": round(avg_volume, 2),
        "historical_impact_score": calc_historical_impact_score(count, abnormal_1d, hit_rate, avg_volume),
    }


def calc_historical_impact_score(
    similar_event_count: int,
    abnormal_returns_1d: list[float],
    directional_hit_rate: float,
    avg_volume_zscore: float,
) -> int:
    if similar_event_count < 5:
        base = 50
    else:
        median_abs_car = median(abs(value) for value in abnormal_returns_1d) if abnormal_returns_1d else 0.0
        base = 50
        base += min(median_abs_car * 10, 25)
        base += (directional_hit_rate - 0.5) * 50
        base += min(avg_volume_zscore * 5, 15)
        base += min(math.log(max(similar_event_count, 1)) * 3, 10)
    return int(max(0, min(100, round(base))))


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
