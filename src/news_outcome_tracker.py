"""
news_outcome_tracker.py — fill post-news price outcomes for event studies.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yfinance as yf

from .config import DB_PATH, KST
from .news_event_store import ensure_news_event_tables
from .news_event_study import abnormal_return_simple

logger = logging.getLogger(__name__)


ASSET_SYMBOLS = {
    "NASDAQ": "^IXIC",
    "SP500": "^GSPC",
    "S&P500": "^GSPC",
    "US10Y": "^TNX",
    "USD/KRW": "KRW=X",
    "BRENT": "BZ=F",
    "XLE": "XLE",
    "SOXX": "SOXX",
    "consumer": "XLY",
    "defense": "ITA",
    "risk_assets": "QQQ",
    "NVDA": "NVDA",
    "GEV": "GEV",
    "005930.KS": "005930.KS",
    "000660.KS": "000660.KS",
    "267260.KS": "267260.KS",
    "010120.KS": "010120.KS",
}

INDEX_BENCHMARKS = {
    "KR": "^KS11",
    "US": "SPY",
    "GLOBAL": "SPY",
}

SECTOR_BENCHMARKS = {
    "NVDA": "SOXX",
    "SOXX": "QQQ",
    "005930.KS": "091160.KS",
    "000660.KS": "091160.KS",
    "267260.KS": "XLU",
    "010120.KS": "XLU",
    "GEV": "XLU",
    "XLE": "SPY",
    "consumer": "XLY",
    "defense": "ITA",
    "BRENT": "XLE",
}


@dataclass
class PriceOutcome:
    price_at_event: float | None
    price_1h: float | None
    price_close: float | None
    price_next_close: float | None
    price_5d: float | None
    return_1h: float | None
    return_0d: float | None
    return_1d: float | None
    return_5d: float | None
    volume_zscore_1d: float | None


def update_news_event_outcomes(
    db_path: Path | str = DB_PATH,
    limit: int | None = 200,
    report_only: bool = True,
    directional_only: bool = True,
    retry_after_hours: int = 12,
) -> dict[str, int]:
    ensure_news_event_tables(db_path)
    rows = _load_pending_impacts(
        db_path,
        limit=limit,
        report_only=report_only,
        directional_only=directional_only,
        retry_after_hours=retry_after_hours,
    )
    history_cache: dict[tuple[str, str, str], Any] = {}
    updated = 0
    skipped = 0
    failed = 0
    checked_at = datetime.now(KST).isoformat()

    with sqlite3.connect(str(db_path)) as conn:
        for row in rows:
            asset = row["asset"]
            symbol = resolve_asset_symbol(asset, row["asset_type"])
            event_dt = parse_event_time(row["published_at_kst"])
            if not symbol or not event_dt:
                _mark_outcome_checked(conn, row["news_id"], asset, checked_at, "skipped_unresolved_asset")
                skipped += 1
                continue
            try:
                outcome = calculate_asset_outcome(symbol, event_dt, history_cache=history_cache)
                if not outcome or outcome.price_at_event is None:
                    _mark_outcome_checked(conn, row["news_id"], asset, checked_at, "skipped_no_price")
                    skipped += 1
                    continue

                sector_return = _benchmark_return(_sector_benchmark(asset), event_dt, history_cache)
                index_return = _benchmark_return(_index_benchmark(symbol), event_dt, history_cache)
                abnormal_1d = _abnormal(outcome.return_1d, sector_return, index_return)
                abnormal_5d = _abnormal(outcome.return_5d, sector_return, index_return)
                realized_direction = _realized_direction(outcome.return_1d)
                direction_hit = _direction_hit(row["expected_direction"], outcome.return_1d)

                _upsert_outcome(
                    conn,
                    news_id=row["news_id"],
                    asset=asset,
                    outcome=outcome,
                    sector_return_1d=sector_return,
                    index_return_1d=index_return,
                    abnormal_return_1d=abnormal_1d,
                    abnormal_return_5d=abnormal_5d,
                    realized_direction=realized_direction,
                    direction_hit=direction_hit,
                    checked_at=checked_at,
                    status="updated",
                )
                updated += 1
            except Exception as exc:
                _mark_outcome_checked(conn, row["news_id"], asset, checked_at, "failed")
                failed += 1
                logger.debug("뉴스 outcome 계산 실패 (%s/%s): %s", row["news_id"], asset, exc)
        conn.commit()

    return {"checked": len(rows), "updated": updated, "skipped": skipped, "failed": failed}


def calculate_asset_outcome(symbol: str, event_dt: datetime, history_cache: dict | None = None) -> PriceOutcome | None:
    cache = history_cache if history_cache is not None else {}
    hourly = _history(symbol, "60d", "1h", cache)
    daily = _history(symbol, "6mo", "1d", cache)
    if (hourly is None or hourly.empty) and (daily is None or daily.empty):
        return None

    price_at_event, event_bar_ts = _first_close_at_or_after(hourly, event_dt)
    if price_at_event is None:
        price_at_event, event_bar_ts = _first_close_at_or_after(daily, event_dt)
    if price_at_event is None or event_bar_ts is None:
        return None

    price_1h, _ = _first_close_at_or_after(hourly, event_dt + timedelta(hours=1))
    daily_rows = _daily_rows_from_event(daily, event_bar_ts)
    price_close = _row_close(daily_rows, 0)
    price_next_close = _row_close(daily_rows, 1)
    price_5d = _row_close(daily_rows, 5)
    volume_z = _volume_zscore(daily, daily_rows[0].name) if daily_rows else None

    return PriceOutcome(
        price_at_event=price_at_event,
        price_1h=price_1h,
        price_close=price_close,
        price_next_close=price_next_close,
        price_5d=price_5d,
        return_1h=_pct_return(price_1h, price_at_event),
        return_0d=_pct_return(price_close, price_at_event),
        return_1d=_pct_return(price_next_close, price_at_event),
        return_5d=_pct_return(price_5d, price_at_event),
        volume_zscore_1d=volume_z,
    )


def resolve_asset_symbol(asset: str | None, asset_type: str | None = None) -> str | None:
    if not asset:
        return None
    if asset in ASSET_SYMBOLS:
        return ASSET_SYMBOLS[asset]
    if asset == "market":
        return None
    if asset_type == "ticker":
        return asset
    if asset_type == "sector" and asset in ASSET_SYMBOLS:
        return ASSET_SYMBOLS[asset]
    return None


def parse_event_time(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        for fmt in ("%Y-%m-%d %H:%M", "%m/%d %H:%M"):
            try:
                dt = datetime.strptime(raw, fmt)
                if fmt.startswith("%m"):
                    dt = dt.replace(year=datetime.now(KST).year)
                break
            except ValueError:
                dt = None
        if dt is None:
            return None
    if dt.tzinfo is None:
        return KST.localize(dt)
    return dt.astimezone(KST)


def _load_pending_impacts(
    db_path: Path | str,
    limit: int | None = None,
    report_only: bool = True,
    directional_only: bool = True,
    retry_after_hours: int = 12,
) -> list[sqlite3.Row]:
    limit_sql = "LIMIT ?" if limit else ""
    params: tuple[Any, ...] = (int(limit),) if limit else ()
    report_filter = "AND e.should_include_in_report = 1" if report_only else ""
    direction_filter = "AND a.expected_direction IN ('positive', 'negative')" if directional_only else ""
    five_day_cutoff = (datetime.now(KST) - timedelta(days=7)).isoformat()
    retry_cutoff = (datetime.now(KST) - timedelta(hours=retry_after_hours)).isoformat()
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        return conn.execute(
            f"""
            SELECT e.news_id, e.published_at_kst, e.primary_theme,
                   a.asset, a.asset_type, a.expected_direction, a.impact_scope
            FROM news_events e
            JOIN news_asset_impacts a ON a.news_id = e.news_id
            LEFT JOIN news_event_outcomes o ON o.news_id = a.news_id AND o.asset = a.asset
            WHERE a.asset != 'market'
              {report_filter}
              {direction_filter}
              AND (
                    o.news_id IS NULL
                    OR (
                        o.return_1d IS NULL
                        AND (o.outcome_checked_at IS NULL OR o.outcome_checked_at <= ?)
                    )
                    OR (
                        o.return_5d IS NULL
                        AND e.published_at_kst <= ?
                        AND (o.outcome_checked_at IS NULL OR o.outcome_checked_at <= ?)
                    )
                  )
            ORDER BY e.published_at_kst DESC
            {limit_sql}
            """,
            (retry_cutoff, five_day_cutoff, retry_cutoff, *params),
        ).fetchall()


def _history(symbol: str, period: str, interval: str, cache: dict[tuple[str, str, str], Any]):
    key = (symbol, period, interval)
    if key not in cache:
        cache[key] = yf.Ticker(symbol).history(period=period, interval=interval)
    return cache[key]


def _first_close_at_or_after(frame, target_dt: datetime) -> tuple[float | None, datetime | None]:
    if frame is None or frame.empty or "Close" not in frame.columns:
        return None, None
    idx = frame.index
    tz = getattr(idx, "tz", None)
    target = target_dt.astimezone(tz) if tz is not None else target_dt.replace(tzinfo=None)
    for ts, row in frame.sort_index().iterrows():
        if ts >= target:
            price = _num(row.get("Close"))
            if price is not None:
                ts_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
                return price, ts_dt
    return None, None


def _daily_rows_from_event(daily, event_bar_ts: datetime) -> list[Any]:
    if daily is None or daily.empty:
        return []
    idx = daily.index
    tz = getattr(idx, "tz", None)
    local_event = event_bar_ts.astimezone(tz) if tz is not None and event_bar_ts.tzinfo else event_bar_ts
    event_date = local_event.date()
    rows = []
    for ts, row in daily.sort_index().iterrows():
        ts_dt = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        if ts_dt.date() >= event_date:
            rows.append(row)
    return rows


def _row_close(rows: list[Any], offset: int) -> float | None:
    if len(rows) <= offset:
        return None
    return _num(rows[offset].get("Close"))


def _volume_zscore(daily, close_ts) -> float | None:
    if daily is None or daily.empty or "Volume" not in daily.columns:
        return None
    try:
        pos = list(daily.index).index(close_ts)
    except ValueError:
        return None
    if pos <= 0:
        return None
    window = daily.iloc[max(0, pos - 20):pos]["Volume"].dropna()
    if len(window) < 5:
        return None
    std = float(window.std())
    if std <= 0:
        return None
    current = _num(daily.iloc[pos].get("Volume"))
    if current is None:
        return None
    return round((current - float(window.mean())) / std, 2)


def _benchmark_return(symbol: str | None, event_dt: datetime, history_cache: dict) -> float | None:
    if not symbol:
        return None
    outcome = calculate_asset_outcome(symbol, event_dt, history_cache=history_cache)
    return outcome.return_1d if outcome else None


def _sector_benchmark(asset: str) -> str | None:
    return SECTOR_BENCHMARKS.get(asset)


def _index_benchmark(symbol: str) -> str:
    if symbol.endswith(".KS") or symbol.endswith(".KQ"):
        return INDEX_BENCHMARKS["KR"]
    return INDEX_BENCHMARKS["US"]


def _abnormal(asset_return: float | None, sector_return: float | None, index_return: float | None) -> float | None:
    if asset_return is None:
        return None
    return round(abnormal_return_simple(asset_return, sector_return or 0.0, index_return or 0.0), 2)


def _realized_direction(return_1d: float | None) -> str | None:
    if return_1d is None:
        return None
    if return_1d > 0:
        return "positive"
    if return_1d < 0:
        return "negative"
    return "neutral"


def _direction_hit(expected_direction: str | None, return_1d: float | None) -> int | None:
    if expected_direction not in {"positive", "negative"} or return_1d is None:
        return None
    if expected_direction == "positive":
        return 1 if return_1d > 0 else 0
    return 1 if return_1d < 0 else 0


def _upsert_outcome(
    conn: sqlite3.Connection,
    news_id: str,
    asset: str,
    outcome: PriceOutcome,
    sector_return_1d: float | None,
    index_return_1d: float | None,
    abnormal_return_1d: float | None,
    abnormal_return_5d: float | None,
    realized_direction: str | None,
    direction_hit: int | None,
    checked_at: str,
    status: str,
) -> None:
    conn.execute(
        """
        INSERT INTO news_event_outcomes (
            news_id, asset, price_at_event, price_1h, price_close, price_next_close, price_5d,
            return_1h, return_0d, return_1d, return_5d,
            sector_return_1d, index_return_1d, abnormal_return_1d, abnormal_return_5d,
            volume_zscore_1d, realized_direction, direction_hit, outcome_checked_at, outcome_status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(news_id, asset) DO UPDATE SET
            price_at_event = excluded.price_at_event,
            price_1h = COALESCE(excluded.price_1h, news_event_outcomes.price_1h),
            price_close = COALESCE(excluded.price_close, news_event_outcomes.price_close),
            price_next_close = COALESCE(excluded.price_next_close, news_event_outcomes.price_next_close),
            price_5d = COALESCE(excluded.price_5d, news_event_outcomes.price_5d),
            return_1h = COALESCE(excluded.return_1h, news_event_outcomes.return_1h),
            return_0d = COALESCE(excluded.return_0d, news_event_outcomes.return_0d),
            return_1d = COALESCE(excluded.return_1d, news_event_outcomes.return_1d),
            return_5d = COALESCE(excluded.return_5d, news_event_outcomes.return_5d),
            sector_return_1d = COALESCE(excluded.sector_return_1d, news_event_outcomes.sector_return_1d),
            index_return_1d = COALESCE(excluded.index_return_1d, news_event_outcomes.index_return_1d),
            abnormal_return_1d = COALESCE(excluded.abnormal_return_1d, news_event_outcomes.abnormal_return_1d),
            abnormal_return_5d = COALESCE(excluded.abnormal_return_5d, news_event_outcomes.abnormal_return_5d),
            volume_zscore_1d = COALESCE(excluded.volume_zscore_1d, news_event_outcomes.volume_zscore_1d),
            realized_direction = COALESCE(excluded.realized_direction, news_event_outcomes.realized_direction),
            direction_hit = COALESCE(excluded.direction_hit, news_event_outcomes.direction_hit),
            outcome_checked_at = excluded.outcome_checked_at,
            outcome_status = excluded.outcome_status
        """,
        (
            news_id,
            asset,
            outcome.price_at_event,
            outcome.price_1h,
            outcome.price_close,
            outcome.price_next_close,
            outcome.price_5d,
            outcome.return_1h,
            outcome.return_0d,
            outcome.return_1d,
            outcome.return_5d,
            sector_return_1d,
            index_return_1d,
            abnormal_return_1d,
            abnormal_return_5d,
            outcome.volume_zscore_1d,
            realized_direction,
            direction_hit,
            checked_at,
            status,
        ),
    )


def _mark_outcome_checked(
    conn: sqlite3.Connection,
    news_id: str,
    asset: str,
    checked_at: str,
    status: str,
) -> None:
    conn.execute(
        """
        INSERT INTO news_event_outcomes (news_id, asset, outcome_checked_at, outcome_status)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(news_id, asset) DO UPDATE SET
            outcome_checked_at = excluded.outcome_checked_at,
            outcome_status = excluded.outcome_status
        """,
        (news_id, asset, checked_at, status),
    )


def _pct_return(price: float | None, base: float | None) -> float | None:
    if price is None or base is None or base == 0:
        return None
    return round((price / base - 1) * 100, 2)


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
