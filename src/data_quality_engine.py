"""
data_quality_engine.py — deterministic market data quality scoring.
"""
from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from .config import KST


MIN_RECOMMENDABLE_SCORE = 0.80
MIN_WATCH_ONLY_SCORE = 0.60


def build_data_quality_table(ctx: dict, now_kst: datetime | None = None) -> list[dict[str, Any]]:
    now_kst = _ensure_kst(now_kst or datetime.now(KST))
    rows = []
    entry_signals = ctx.get("entry_signals") or {}

    for ticker, info in (ctx.get("stock_prices") or {}).items():
        rows.append(score_market_item(
            ticker=str(ticker),
            info=info or {},
            signal=_find_by_ticker(entry_signals, str(ticker)) or {},
            now_kst=now_kst,
        ))
    return rows


def build_data_quality_lookup(ctx: dict, now_kst: datetime | None = None) -> dict[str, dict[str, Any]]:
    lookup = {}
    for row in build_data_quality_table(ctx, now_kst=now_kst):
        for key in ticker_lookup_keys(row["ticker"]):
            lookup[key] = row
    return lookup


def score_market_item(ticker: str, info: dict, signal: dict | None = None, now_kst: datetime | None = None) -> dict[str, Any]:
    now_kst = _ensure_kst(now_kst or datetime.now(KST))
    signal = signal or {}
    price = _normal_price(info.get("price") or info.get("current_price") or signal.get("current_price"))
    source = str(info.get("source") or "market_data")
    secondary = info.get("source_secondary")
    score = 1.0
    warnings = []

    if price is None:
        score -= 1.0
        warnings.append("price_missing")

    freshness = _freshness_minutes(info, now_kst)
    if freshness is None:
        warnings.append("freshness_unknown")
    elif freshness > 30:
        score -= 0.25
        warnings.append("stale_over_30m")

    conflict = _normal_price(info.get("source_conflict_pct"))
    if conflict is not None and conflict > 1.0:
        score -= 0.25
        warnings.append("source_conflict_gt_1pct")

    if not source or source == "market_data":
        score -= 0.08
        warnings.append("source_generic")

    if not signal:
        score -= 0.08
        warnings.append("technical_signal_missing")

    if info.get("volume") in (None, "") and not signal.get("volume_signal"):
        score -= 0.05
        warnings.append("volume_missing")

    staleness_hours = _staleness_hours(info, now_kst)
    if staleness_hours is not None and staleness_hours >= 24:
        warnings.append("stale_over_24h")
    staleness_reason = str(info.get("staleness_reason") or "")
    if staleness_reason:
        warnings.append(staleness_reason)
    regular_open_required = bool(info.get("regular_open_confirmation_required"))
    if regular_open_required:
        warnings.append("regular_open_confirmation_required")

    score = max(0.0, min(1.0, round(score, 2)))
    if score >= MIN_RECOMMENDABLE_SCORE:
        status = "PASS"
    elif score >= MIN_WATCH_ONLY_SCORE:
        status = "WATCH_ONLY"
    else:
        status = "FAIL"

    return {
        "ticker": ticker,
        "price": price,
        "source_primary": source,
        "source_secondary": secondary,
        "freshness_minutes": freshness,
        "last_trade_time": info.get("last_trade_time"),
        "staleness_hours": staleness_hours,
        "staleness_reason": staleness_reason,
        "regular_open_confirmation_required": regular_open_required,
        "price_valid": price is not None,
        "source_conflict_pct": conflict,
        "session_status": info.get("session_status") or "unknown",
        "data_quality_score": score,
        "score": score,
        "status": status,
        "warnings": warnings,
    }


def summarize_data_quality(rows: list[dict[str, Any]]) -> dict[str, Any]:
    scores = [row["data_quality_score"] for row in rows]
    warnings = []
    for row in rows:
        warnings.extend(f"{row['ticker']}:{warning}" for warning in row.get("warnings") or [])
    return {
        "overall_score": round(sum(scores) / len(scores), 2) if scores else 0.0,
        "critical_warnings": warnings,
        "stale_data": [row["ticker"] for row in rows if row["data_quality_score"] < MIN_RECOMMENDABLE_SCORE],
        "source_conflicts": [row["ticker"] for row in rows if (row.get("source_conflict_pct") or 0) > 1.0],
    }


def ticker_lookup_keys(ticker: str) -> set[str]:
    raw = str(ticker or "").strip()
    if not raw:
        return set()
    upper = raw.upper()
    keys = {upper}
    if "." in upper:
        stem = upper.split(".", 1)[0]
        keys.add(stem)
    else:
        stem = upper
    if stem.isdigit():
        padded = stem.zfill(6)
        keys.update({padded, f"{padded}.KS", f"{padded}.KQ"})
    return keys


def _find_by_ticker(mapping: dict, ticker: str) -> Any:
    for key in ticker_lookup_keys(ticker):
        if key in mapping:
            return mapping[key]
    return None


def _normal_price(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _freshness_minutes(info: dict, now_kst: datetime) -> float | None:
    raw = info.get("timestamp") or info.get("updated_at") or info.get("collected_at")
    if not raw:
        return None
    if isinstance(raw, datetime):
        dt = raw
    else:
        try:
            dt = datetime.fromisoformat(str(raw))
        except ValueError:
            return None
    dt = _ensure_kst(dt)
    return round(max(0.0, (now_kst - dt).total_seconds() / 60), 1)


def _staleness_hours(info: dict, now_kst: datetime) -> float | None:
    raw = info.get("last_trade_time") or info.get("regular_market_time")
    if not raw:
        return _hours_from_numeric(info.get("staleness_hours"))
    if isinstance(raw, datetime):
        dt = raw
    else:
        try:
            dt = datetime.fromisoformat(str(raw))
        except ValueError:
            return _hours_from_numeric(info.get("staleness_hours"))
    dt = _ensure_kst(dt)
    return round(max(0.0, (now_kst - dt).total_seconds() / 3600), 1)


def _hours_from_numeric(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number, 1) if math.isfinite(number) else None


def _ensure_kst(value: datetime) -> datetime:
    if value.tzinfo is None:
        return KST.localize(value)
    return value.astimezone(KST)
