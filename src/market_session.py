"""
market_session.py — deterministic market session and stale-price context.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any

from .config import ET, KST


US_MARKET_HOLIDAYS_2026 = {
    date(2026, 1, 1),   # New Year's Day
    date(2026, 1, 19),  # Martin Luther King Jr. Day
    date(2026, 2, 16),  # Washington's Birthday
    date(2026, 4, 3),   # Good Friday
    date(2026, 5, 25),  # Memorial Day
    date(2026, 6, 19),  # Juneteenth
    date(2026, 7, 3),   # Independence Day observed
    date(2026, 9, 7),   # Labor Day
    date(2026, 11, 26), # Thanksgiving
    date(2026, 12, 25), # Christmas
}


KR_MARKET_HOLIDAYS_2026: set[date] = set()


def build_market_session_status(now_kst: datetime | None = None) -> dict[str, dict[str, Any]]:
    now_kst = _ensure_kst(now_kst or datetime.now(KST))
    return {
        "US": _market_status(
            now_kst=now_kst,
            market="US",
            timezone=ET,
            holidays=US_MARKET_HOLIDAYS_2026,
            open_time=time(9, 30),
            close_time=time(16, 0),
        ),
        "KR": _market_status(
            now_kst=now_kst,
            market="KR",
            timezone=KST,
            holidays=KR_MARKET_HOLIDAYS_2026,
            open_time=time(9, 0),
            close_time=time(15, 30),
        ),
    }


def annotate_price_session(info: dict, market: str, session_status: dict | None = None, now_kst: datetime | None = None) -> dict:
    payload = dict(info or {})
    market = str(market or payload.get("market") or "").upper()
    now_kst = _ensure_kst(now_kst or datetime.now(KST))
    session = session_status or build_market_session_status(now_kst).get(market, {})
    last_trade = _parse_dt(payload.get("last_trade_time") or payload.get("regular_market_time"))
    staleness_hours = None
    if last_trade is not None:
        staleness_hours = round(max(0.0, (now_kst - last_trade.astimezone(KST)).total_seconds() / 3600), 1)

    stale_reason = ""
    if market == "US" and session.get("regular_open_confirmation_required"):
        stale_reason = "us_regular_open_confirmation_required"
    elif staleness_hours is not None and staleness_hours >= 24:
        stale_reason = "stale_over_24h"

    payload["market"] = market or payload.get("market")
    payload["session_status"] = session.get("status") or payload.get("session_status") or "unknown"
    payload["staleness_hours"] = staleness_hours
    payload["staleness_reason"] = stale_reason
    payload["regular_open_confirmation_required"] = bool(session.get("regular_open_confirmation_required"))
    return payload


def _market_status(
    now_kst: datetime,
    market: str,
    timezone,
    holidays: set[date],
    open_time: time,
    close_time: time,
) -> dict[str, Any]:
    local_now = now_kst.astimezone(timezone)
    local_date = local_now.date()
    local_time = local_now.time()
    trading_today = _is_trading_day(local_date, holidays)
    next_open_local = _next_open(local_now, holidays, open_time, close_time)
    prev_holiday = _holiday_between(_previous_trading_day(next_open_local.date(), holidays), next_open_local.date(), holidays)

    if trading_today and open_time <= local_time < close_time:
        status = "open"
    elif not trading_today and local_time < close_time:
        status = "holiday_closed"
    elif prev_holiday and local_now < next_open_local:
        status = "pre_open_after_holiday"
    elif trading_today and local_time < open_time:
        status = "pre_open"
    else:
        status = "post_close"

    confirmation_required = market == "US" and status in {"holiday_closed", "pre_open_after_holiday", "pre_open"}
    return {
        "market": market,
        "status": status,
        "is_open": status == "open",
        "previous_session_was_holiday": bool(prev_holiday),
        "next_open_kst": next_open_local.astimezone(KST).isoformat(),
        "regular_open_confirmation_required": confirmation_required,
    }


def _next_open(local_now: datetime, holidays: set[date], open_time: time, close_time: time) -> datetime:
    candidate_day = local_now.date()
    if _is_trading_day(candidate_day, holidays) and local_now.time() < open_time:
        return local_now.replace(hour=open_time.hour, minute=open_time.minute, second=0, microsecond=0)
    if _is_trading_day(candidate_day, holidays) and local_now.time() < close_time:
        return local_now.replace(hour=open_time.hour, minute=open_time.minute, second=0, microsecond=0)
    candidate_day += timedelta(days=1)
    while not _is_trading_day(candidate_day, holidays):
        candidate_day += timedelta(days=1)
    return local_now.replace(
        year=candidate_day.year,
        month=candidate_day.month,
        day=candidate_day.day,
        hour=open_time.hour,
        minute=open_time.minute,
        second=0,
        microsecond=0,
    )


def _previous_trading_day(day: date, holidays: set[date]) -> date:
    candidate = day - timedelta(days=1)
    while not _is_trading_day(candidate, holidays):
        candidate -= timedelta(days=1)
    return candidate


def _holiday_between(start_exclusive: date, end_inclusive: date, holidays: set[date]) -> bool:
    day = start_exclusive + timedelta(days=1)
    while day <= end_inclusive:
        if day in holidays:
            return True
        day += timedelta(days=1)
    return False


def _is_trading_day(day: date, holidays: set[date]) -> bool:
    return day.weekday() < 5 and day not in holidays


def _parse_dt(value) -> datetime | None:
    if isinstance(value, datetime):
        return _ensure_kst(value)
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return _ensure_kst(parsed)


def _ensure_kst(value: datetime) -> datetime:
    if value.tzinfo is None:
        return KST.localize(value)
    return value.astimezone(KST)
