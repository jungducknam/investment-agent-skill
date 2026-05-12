"""
database.py — SQLite DB 관리 (Thread-safe 싱글턴 커넥션)
"""
import sqlite3
import json
import threading
from datetime import datetime
from contextlib import contextmanager

from .config import DB_PATH, KST


_local = threading.local()


@contextmanager
def get_db():
    """Thread-safe DB 커넥션 컨텍스트 매니저"""
    if not hasattr(_local, "conn") or _local.conn is None:
        _local.conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA journal_mode=WAL")
        _local.conn.execute("PRAGMA busy_timeout=5000")
    try:
        yield _local.conn
        _local.conn.commit()
    except Exception:
        _local.conn.rollback()
        raise


def init_db():
    """DB 테이블 초기화"""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS positions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                direction   TEXT    NOT NULL DEFAULT 'long',
                name        TEXT    NOT NULL,
                ticker      TEXT    NOT NULL,
                market      TEXT    NOT NULL,
                entry_price REAL    NOT NULL,
                quantity    REAL    NOT NULL,
                currency    TEXT    NOT NULL DEFAULT 'KRW',
                created_at  TEXT    NOT NULL,
                is_active   INTEGER NOT NULL DEFAULT 1,
                closed_at   TEXT,
                close_price REAL
            );
            CREATE TABLE IF NOT EXISTS alerts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id INTEGER,
                alert_type  TEXT,
                message     TEXT,
                created_at  TEXT
            );
            CREATE TABLE IF NOT EXISTS reports (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                report_date  TEXT    NOT NULL UNIQUE,
                report_json  TEXT    NOT NULL,
                msg1         TEXT,
                msg2         TEXT,
                created_at   TEXT    NOT NULL
            );
        """)


# ── 포지션 CRUD ───────────────────────────────────────
def add_position(direction: str, name: str, ticker: str, market: str,
                 entry_price: float, quantity: float, currency: str) -> int:
    now = datetime.now(KST).isoformat()
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO positions (direction, name, ticker, market, entry_price, quantity, currency, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (direction, name, ticker, market, entry_price, quantity, currency, now)
        )
        return cur.lastrowid


def get_active_positions() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM positions WHERE is_active=1 ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]


def close_position(pid: int, close_price: float):
    now = datetime.now(KST).isoformat()
    with get_db() as conn:
        conn.execute(
            "UPDATE positions SET is_active=0, closed_at=?, close_price=? WHERE id=?",
            (now, close_price, pid)
        )


def delete_position(pid: int):
    with get_db() as conn:
        conn.execute("DELETE FROM positions WHERE id=?", (pid,))


# ── 알림 저장 ─────────────────────────────────────────
def save_alert(position_id: int, alert_type: str, message: str):
    now = datetime.now(KST).isoformat()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO alerts (position_id, alert_type, message, created_at) VALUES (?,?,?,?)",
            (position_id, alert_type, message, now)
        )


# ── 리포트 캐시 ──────────────────────────────────────
def save_report(report_date: str, report_json: dict, msg1: str = "", msg2: str = ""):
    now = datetime.now(KST).isoformat()
    with get_db() as conn:
        conn.execute("""
            INSERT INTO reports (report_date, report_json, msg1, msg2, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(report_date) DO UPDATE SET
                report_json = excluded.report_json,
                msg1 = excluded.msg1,
                msg2 = excluded.msg2,
                created_at = excluded.created_at
        """, (report_date, json.dumps(report_json, ensure_ascii=False), msg1, msg2, now))


def load_report(report_date: str) -> dict | None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT report_json, msg1, msg2 FROM reports WHERE report_date = ?",
            (report_date,)
        ).fetchone()
    if not row:
        return None
    return {
        "report": json.loads(row[0]),
        "msg1": row[1] or "",
        "msg2": row[2] or "",
    }


# 모듈 로드 시 DB 초기화
init_db()
