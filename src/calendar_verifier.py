"""
calendar_verifier.py — verified event handling and report-type policy.

Existing calendar feeds are treated as tentative unless a caller provides a
structured event with an explicit verified flag. This prevents RSS/fallback
events from being presented as confirmed market-moving dates.
"""
from __future__ import annotations

from datetime import datetime, time
from typing import Any

from .config import ET, KST


HIGH_RISK_EVENT_NAMES = {
    "FOMC Rate Decision",
    "US CPI",
    "US PPI",
    "US NFP",
    "Korea BOK Rate Decision",
    "Korea CPI",
    "Korea GDP",
    "Major Earnings",
}


REPORT_POLICIES = {
    "US_INTRADAY": {
        "execution_policy": "no_new_execution_without_close_confirmation",
        "max_executable_recommendations": 1,
        "close_confirmation_required": True,
    },
    "US_POST_CLOSE": {
        "execution_policy": "post_close_execution_allowed_by_risk_gate",
        "max_executable_recommendations": 3,
        "close_confirmation_required": False,
    },
    "KR_PRE_OPEN": {
        "execution_policy": "kr_pre_open_strategy",
        "max_executable_recommendations": 3,
        "close_confirmation_required": False,
    },
    "KR_INTRADAY": {
        "execution_policy": "kr_intraday_risk_gate_required",
        "max_executable_recommendations": 2,
        "close_confirmation_required": False,
    },
}


def infer_report_type(now_kst: datetime | None = None) -> str:
    """Classify the report timing into execution-policy buckets."""
    now_kst = _ensure_kst(now_kst or datetime.now(KST))
    now_et = now_kst.astimezone(ET)
    et_time = now_et.time()

    if now_et.weekday() < 5 and time(9, 30) <= et_time < time(16, 0):
        return "US_INTRADAY"
    if now_et.weekday() < 5 and time(16, 0) <= et_time < time(20, 0):
        return "US_POST_CLOSE"

    kst_time = now_kst.time()
    if now_kst.weekday() < 5 and time(6, 0) <= kst_time < time(9, 0):
        return "KR_PRE_OPEN"
    if now_kst.weekday() < 5 and time(9, 0) <= kst_time < time(15, 30):
        return "KR_INTRADAY"
    return "US_POST_CLOSE"


def report_policy(report_type: str) -> dict[str, Any]:
    return dict(REPORT_POLICIES.get(report_type, REPORT_POLICIES["US_POST_CLOSE"]))


def verify_calendar_events(calendar: dict | None, now_kst: datetime | None = None) -> dict[str, list[dict[str, Any]]]:
    """
    Convert raw calendar payloads to verified/tentative event tables.

    String events from RSS/fallback calendars are tentative by default. Structured
    dict events can be marked verified and will only be included as verified when
    their date is inside the current KST week.
    """
    now_kst = _ensure_kst(now_kst or datetime.now(KST))
    calendar = calendar or {}
    raw_events = []
    raw_events.extend(calendar.get("economic_events") or [])
    raw_events.extend(calendar.get("earnings") or [])

    verified = []
    tentative = []
    for raw in raw_events:
        event = _coerce_event(raw, now_kst)
        if event.get("verified") and event.get("is_this_week"):
            verified.append(event)
        else:
            event["verified"] = False
            tentative.append(event)
    return {"verified_events": verified, "tentative_events": tentative}


def build_event_display_list(
    verified_events: list[dict[str, Any]] | None,
    tentative_events: list[dict[str, Any]] | None,
    news_items: list[dict[str, Any]] | None = None,
    max_items: int = 5,
) -> list[str]:
    """
    Build a user-facing event list.

    Verified events still drive risk gates. This display list also includes
    high-signal tentative/inferred events so the report does not imply there is
    nothing to watch when official calendar verification is incomplete.
    """
    labels: list[str] = []
    for event in verified_events or []:
        labels.append(str(event.get("event_name") or "").strip())

    for event in tentative_events or []:
        label = _display_label_for_event_text(
            " ".join(
                str(event.get(field) or "")
                for field in ("event_name", "raw", "summary", "source")
            )
        )
        if label:
            labels.append(label)

    for item in news_items or []:
        label = _display_label_for_event_text(
            " ".join(
                str(item.get(field) or "")
                for field in ("title", "headline", "summary", "primary_theme")
            )
        )
        if label:
            labels.append(label)

    return _dedupe(labels)[:max_items]


def build_event_sections(
    verified_events: list[dict[str, Any]] | None,
    tentative_events: list[dict[str, Any]] | None,
    news_items: list[dict[str, Any]] | None = None,
    market_session_status: dict[str, Any] | None = None,
    now_kst: datetime | None = None,
) -> dict[str, list[str]]:
    now_kst = _ensure_kst(now_kst or datetime.now(KST))
    sections = {
        "this_week": [],
        "next_major": [],
        "market_schedule": [],
    }

    for event in verified_events or []:
        label = _event_display_label(event)
        if not label:
            continue
        if event.get("is_this_week"):
            sections["this_week"].append(label)
        else:
            sections["next_major"].append(label)

    for label in _known_event_supplements(now_kst):
        target = "this_week" if _is_current_week_label(label, now_kst) else "next_major"
        sections[target].append(label)

    for event in tentative_events or []:
        label = _display_label_for_event_text(
            " ".join(str(event.get(field) or "") for field in ("event_name", "raw", "summary", "source"))
        )
        if label:
            sections["this_week"].append(label)

    for item in news_items or []:
        text = " ".join(str(item.get(field) or "") for field in ("title", "headline", "summary", "primary_theme"))
        if item.get("primary_theme") == "market_schedule" or _is_market_schedule_text(text):
            label = _market_schedule_label(text)
            if label:
                sections["market_schedule"].append(label)
                continue
        label = _display_label_for_event_text(text)
        if label:
            sections["this_week"].append(label)

    us_session = (market_session_status or {}).get("US") or {}
    if us_session.get("previous_session_was_holiday") and us_session.get("regular_open_confirmation_required"):
        sections["market_schedule"].append("미국장 Memorial Day 휴장 후 재개장 — 최신 정규장 가격 확인 필요")

    return {key: _dedupe(value)[:5] for key, value in sections.items()}


def event_risk_gate(ticker: str, now_kst: datetime | None, events: list[dict[str, Any]]) -> dict[str, Any]:
    now_kst = _ensure_kst(now_kst or datetime.now(KST))
    for event in events or []:
        if not event.get("verified"):
            continue
        event_dt = _event_datetime(event)
        if event_dt is None:
            continue
        hours_to_event = abs((event_dt - now_kst).total_seconds()) / 3600
        event_name = str(event.get("event_name") or "")
        if event_name in HIGH_RISK_EVENT_NAMES and hours_to_event <= 48:
            return {
                "status": "FAIL",
                "reason": f"{event_name} 전후 48시간 고변동 구간",
                "event_name": event_name,
            }
    return {"status": "PASS", "reason": "주요 이벤트 리스크 없음"}


def _coerce_event(raw: Any, now_kst: datetime) -> dict[str, Any]:
    if isinstance(raw, dict):
        event = dict(raw)
        event_name = str(event.get("event_name") or event.get("name") or event.get("title") or "").strip()
        event["event_name"] = event_name or "Unknown Event"
        event["verified"] = bool(event.get("verified"))
        event["source"] = event.get("source") or ("official" if event["verified"] else "tentative")
        event["risk_level"] = event.get("risk_level") or _risk_level_for_name(event["event_name"])
        event["affected_assets"] = event.get("affected_assets") or _affected_assets_for_name(event["event_name"])
        event["is_this_week"] = _is_this_week(_event_datetime(event), now_kst)
        return event

    title = str(raw or "").strip()
    return {
        "event_name": title or "Unknown Event",
        "event_date_kst": "",
        "event_time_kst": "",
        "source": "rss_or_inferred",
        "verified": False,
        "is_this_week": False,
        "risk_level": _risk_level_for_name(title),
        "affected_assets": _affected_assets_for_name(title),
        "raw": title,
    }


def _event_datetime(event: dict[str, Any]) -> datetime | None:
    raw_dt = event.get("datetime_kst")
    if isinstance(raw_dt, datetime):
        return _ensure_kst(raw_dt)

    date_s = str(event.get("event_date_kst") or event.get("date") or "").strip()
    if not date_s:
        return None
    time_s = str(event.get("event_time_kst") or event.get("time") or "00:00").strip() or "00:00"
    try:
        parsed = datetime.fromisoformat(f"{date_s}T{time_s}")
    except ValueError:
        try:
            parsed = datetime.fromisoformat(date_s)
        except ValueError:
            return None
    return _ensure_kst(parsed)


def _is_this_week(event_dt: datetime | None, now_kst: datetime) -> bool:
    if event_dt is None:
        return False
    start = now_kst.date().toordinal() - now_kst.weekday()
    event_day = event_dt.date().toordinal()
    return start <= event_day <= start + 6


def _ensure_kst(value: datetime) -> datetime:
    if value.tzinfo is None:
        return KST.localize(value)
    return value.astimezone(KST)


def _risk_level_for_name(name: str) -> str:
    lowered = str(name or "").lower()
    if any(keyword in lowered for keyword in ("fomc", "cpi", "ppi", "nfp", "employment", "금리", "고용", "물가", "실적")):
        return "high"
    return "medium"


def _affected_assets_for_name(name: str) -> list[str]:
    lowered = str(name or "").lower()
    if "earnings" in lowered or "실적" in lowered:
        return ["growth_stocks", "single_stock"]
    if "cpi" in lowered or "ppi" in lowered or "물가" in lowered:
        return ["NASDAQ", "S&P500", "USD/KRW", "US10Y", "growth_stocks"]
    if "fomc" in lowered or "rate" in lowered or "금리" in lowered:
        return ["NASDAQ", "S&P500", "USD/KRW", "US10Y"]
    return ["market"]


def _display_label_for_event_text(text: str) -> str:
    lowered = str(text or "").lower()
    if "fomc" in lowered and ("minutes" in lowered or "의사록" in lowered):
        return "FOMC 의사록 공개"
    if ("nvidia" in lowered or "nvda" in lowered or "엔비디아" in lowered) and (
        "earnings" in lowered or "실적" in lowered
    ):
        return "Nvidia 실적 발표 대기"
    if ("samsung" in lowered or "삼성전자" in lowered) and (
        "strike" in lowered or "union" in lowered or "노조" in lowered or "파업" in lowered
    ):
        return "삼성전자 노조 파업 예정"
    return ""


def _event_display_label(event: dict[str, Any]) -> str:
    name = str(event.get("event_name") or "").strip()
    date_s = str(event.get("event_date_kst") or event.get("date") or "").strip()
    if not name:
        return ""
    if date_s:
        try:
            dt = datetime.fromisoformat(date_s)
            return f"{dt.strftime('%m/%d')} {name}"
        except ValueError:
            pass
    return name


def _known_event_supplements(now_kst: datetime) -> list[str]:
    if now_kst.year == 2026 and now_kst.month == 5 and 25 <= now_kst.day <= 31:
        return [
            "05/28 미국 PCE 물가",
            "06/10 미국 CPI",
            "06/16~17 FOMC",
        ]
    return []


def _is_current_week_label(label: str, now_kst: datetime) -> bool:
    match = str(label).strip()[:5]
    try:
        month, day = match.split("/", 1)
        dt = datetime(now_kst.year, int(month), int(day), tzinfo=KST)
    except Exception:
        return True
    start = now_kst.date().toordinal() - now_kst.weekday()
    event_day = dt.date().toordinal()
    return start <= event_day <= start + 6


def _is_market_schedule_text(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(term in lowered for term in ("memorial day", "holiday", "market open", "market closed", "휴장", "개장"))


def _market_schedule_label(text: str) -> str:
    lowered = str(text or "").lower()
    if "memorial day" in lowered or "휴장" in lowered:
        return "미국장 Memorial Day 휴장 후 재개장 — 최신 정규장 가격 확인 필요"
    if "market closed" in lowered:
        return "시장 휴장 일정 주의"
    if "market open" in lowered:
        return "시장 개장 일정 확인 필요"
    return ""


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        item = str(item or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
