"""
performance_tracker.py — persist recommendation snapshots for later review.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import DB_PATH, KST


SNAPSHOT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS recommendation_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    report_date TEXT NOT NULL,
    report_id TEXT NOT NULL,
    snapshot_at TEXT NOT NULL,
    ticker TEXT NOT NULL,
    name TEXT,
    market TEXT,
    currency TEXT,
    bucket TEXT NOT NULL,
    rank INTEGER,
    action_status TEXT,
    is_executable INTEGER NOT NULL DEFAULT 0,
    risk_gate_status TEXT,
    entry_price REAL,
    current_price REAL,
    target_price_1 REAL,
    target_price_2 REAL,
    stop_loss REAL,
    position_size_pct REAL,
    risk_reward_1 REAL,
    confidence_score REAL,
    regime TEXT,
    evidence_ids_json TEXT,
    raw_json TEXT NOT NULL,
    return_1d REAL,
    return_3d REAL,
    return_5d REAL,
    return_10d REAL,
    return_20d REAL,
    mfe_pct REAL,
    mae_pct REAL,
    target_hit INTEGER,
    stop_hit INTEGER,
    entry_triggered INTEGER,
    UNIQUE(report_id, ticker, bucket)
);
"""


def ensure_performance_tables(db_path: Path | str = DB_PATH) -> None:
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(SNAPSHOT_TABLE_SQL)
        conn.commit()


def record_recommendation_snapshots(report_date: str, report: dict, db_path: Path | str = DB_PATH) -> int:
    ensure_performance_tables(db_path)
    snapshot_at = datetime.now(KST).isoformat()
    report_id = _report_id(report_date, report, snapshot_at)
    regime = ((report.get("market_regime") or {}).get("global_regime") or
              ((report.get("_market_regime") or {}).get("global_regime")) or "")
    rows = []
    rows.extend(("recommendation", item) for item in report.get("recommendations") or [])
    rows.extend(("waiting", item) for item in report.get("waiting_list") or [])
    rows.extend(("rejected", item) for item in report.get("rejected_candidates") or [])

    with sqlite3.connect(str(db_path)) as conn:
        inserted = 0
        for bucket, item in rows:
            item = _coerce_snapshot_item(item)
            ticker = str(item.get("ticker") or "").strip()
            if not ticker:
                continue
            conn.execute(
                """
                INSERT INTO recommendation_snapshots (
                    report_date, report_id, snapshot_at, ticker, name, market, currency,
                    bucket, rank, action_status, is_executable, risk_gate_status,
                    entry_price, current_price, target_price_1, target_price_2,
                    stop_loss, position_size_pct, risk_reward_1, confidence_score,
                    regime, evidence_ids_json, raw_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(report_id, ticker, bucket) DO UPDATE SET
                    raw_json = excluded.raw_json,
                    snapshot_at = excluded.snapshot_at
                """,
                (
                    report_date, report_id, snapshot_at, ticker, item.get("name"),
                    item.get("market"), item.get("currency"), bucket,
                    _int_or_none(item.get("rank")), item.get("action_status") or item.get("action"),
                    1 if item.get("is_executable") else 0, item.get("risk_gate_status"),
                    _float_or_none(item.get("entry_price")), _float_or_none(item.get("current_price")),
                    _float_or_none(item.get("target_price_1")), _float_or_none(item.get("target_price_2")),
                    _float_or_none(item.get("stop_loss")), _float_or_none(item.get("position_size_pct")),
                    _float_or_none(item.get("risk_reward_1")), _float_or_none(item.get("confidence_score")),
                    regime, json.dumps(item.get("evidence_ids") or [], ensure_ascii=False),
                    json.dumps(item, ensure_ascii=False),
                ),
            )
            inserted += 1
        conn.commit()
    return inserted


def _coerce_snapshot_item(item: Any) -> dict[str, Any]:
    if isinstance(item, dict):
        return item
    ticker = str(item or "").strip()
    if not ticker:
        return {}
    return {
        "ticker": ticker,
        "name": "",
        "action_status": "watch_wait",
        "risk_gate_status": "UNSTRUCTURED",
    }


def _report_id(report_date: str, report: dict, snapshot_at: str) -> str:
    collected_at = ((report.get("_meta") or {}).get("collected_at") or snapshot_at)
    suffix = collected_at.replace("-", "").replace(":", "").replace("+", "_").split(".")[0]
    return f"{report_date}_{suffix}"


def _float_or_none(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
