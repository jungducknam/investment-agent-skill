"""
risk_gate.py — hard execution gates for deterministic recommendations.
"""
from __future__ import annotations

import re
from typing import Any

from .data_quality_engine import MIN_RECOMMENDABLE_SCORE, MIN_WATCH_ONLY_SCORE


def evaluate_risk_gate(
    rec: dict,
    signal: dict,
    quality: dict,
    regime: dict,
    event_gate: dict | None,
    report_type: str,
    extra_context: dict | None = None,
) -> dict[str, Any]:
    failed = []
    warnings = []
    blockers = []
    extra_context = extra_context or {}

    if rec.get("price_source") == "unavailable" or rec.get("current_price") is None:
        failed.append("price_unavailable")
    quality_score = float(quality.get("data_quality_score", quality.get("score", 0.0)) or 0.0)
    if quality_score < MIN_WATCH_ONLY_SCORE:
        failed.append("data_quality_fail")
    elif quality_score < MIN_RECOMMENDABLE_SCORE:
        blockers.append("data_quality_watch_only")

    if event_gate and event_gate.get("status") == "FAIL":
        failed.append("event_risk_48h")
        blockers.append(event_gate.get("reason") or "event_risk_48h")

    evidence_ids = rec.get("evidence_ids") or []
    if len(evidence_ids) < 2:
        failed.append("missing_evidence")
    if rec.get("action") == "buy_zone":
        evidence_types = set(str(item) for item in rec.get("evidence_types") or [])
        if not (evidence_types & {"news", "event", "earnings"}):
            blockers.append("missing_catalyst_evidence")

    rsi = _num(signal.get("rsi"))
    bb_pos = _num(signal.get("bb_position"))
    signal_label = str(signal.get("signal") or rec.get("entry_signal") or "")
    if signal_label == "과열" or (rsi is not None and rsi >= 80) or (bb_pos is not None and bb_pos > 100):
        failed.append("overbought")

    rr = _num(rec.get("risk_reward_1")) or 0.0
    if rr < 1.5:
        failed.append("poor_rr")
    elif rr > 10:
        failed.append("risk_reward_abnormally_high")
    elif rr > 5:
        warnings.append("manual_review_required")
        warnings.append("risk_reward_high")

    current = _num(rec.get("current_price"))
    entry = _num(rec.get("entry_price"))
    if current and entry and current > entry * 1.03:
        failed.append("above_entry")

    if _stop_invalidation_conflict(rec):
        failed.append("stop_invalidation_conflict")

    if (regime or {}).get("global_regime") == "crisis":
        failed.append("crisis_no_new_buy")

    if report_type == "US_INTRADAY":
        blockers.append("intraday_report_close_confirmation_required")
        warnings.append("close_confirmation_required")

    market = str(rec.get("market") or "").upper()
    if market == "US" and (
        quality.get("regular_open_confirmation_required")
        or quality.get("staleness_reason") == "us_regular_open_confirmation_required"
    ):
        blockers.append("us_regular_open_confirmation_required")
        if quality.get("session_status") in {"holiday_closed", "pre_open_after_holiday"}:
            blockers.append("us_market_holiday_stale_price")

    fundamentals = extra_context.get("fundamentals") or {}
    per = _num(fundamentals.get("per"))
    us10y = extra_context.get("us10y") or {}
    us10y_price = _num(us10y.get("price"))
    us10y_change = _num(us10y.get("change_pct"))
    if market == "US" and per is not None and per >= 100 and (
        (us10y_price is not None and us10y_price >= 4.5)
        or (us10y_change is not None and us10y_change > 0)
    ):
        blockers.append("high_valuation_rate_sensitive")

    if failed:
        status = "FAIL"
        allowed_action = "blocked"
        action_status = "blocked"
        is_executable = False
    elif blockers:
        status = "PASS_WITH_CONDITIONS"
        allowed_action = "watch_only" if "data_quality_watch_only" in blockers else "conditional_entry"
        action_status = "watch_wait" if allowed_action == "watch_only" else "conditional_entry"
        is_executable = False
    elif rec.get("action") == "buy_zone":
        status = "PASS"
        allowed_action = "buy"
        action_status = "executable"
        is_executable = True
    else:
        status = "PASS_WITH_CONDITIONS"
        allowed_action = "conditional_entry"
        action_status = "conditional_entry"
        is_executable = False

    return {
        "risk_gate_status": status,
        "failed_rules": failed,
        "warning_rules": _dedupe(warnings),
        "execution_blockers": _dedupe([str(item) for item in blockers if item]),
        "allowed_action": allowed_action,
        "action_status": action_status,
        "is_executable": is_executable,
    }


def primary_reject_reason(reasons: list[str]) -> str:
    priority = [
        "event_risk_48h",
        "stop_invalidation_conflict",
        "risk_reward_abnormally_high",
        "overbought",
        "poor_rr",
        "data_quality_fail",
        "data_quality_watch_only",
        "price_unavailable",
        "missing_evidence",
        "missing_catalyst_evidence",
        "us_market_holiday_stale_price",
        "us_regular_open_confirmation_required",
        "high_valuation_rate_sensitive",
        "above_entry",
        "crisis_no_new_buy",
    ]
    for reason in priority:
        if reason in reasons:
            return reason
    return reasons[0] if reasons else "risk_gate_failed"


def waiting_reason(reason: str, details: list[str]) -> str:
    labels = {
        "event_risk_48h": "주요 이벤트 전후 48시간 고변동 구간",
        "stop_invalidation_conflict": "손절가와 무효화 조건 충돌",
        "risk_reward_abnormally_high": "손익비 10R 초과로 비현실 가능성",
        "overbought": "과열 신호로 대기",
        "poor_rr": "손익비 기준 미달로 대기",
        "data_quality_fail": "데이터 품질 기준 미달",
        "data_quality_watch_only": "데이터 품질이 낮아 관찰만 가능",
        "price_unavailable": "가격 근거 부족",
        "missing_evidence": "근거 ID 부족",
        "missing_catalyst_evidence": "뉴스/이벤트 촉매 근거 부족",
        "us_market_holiday_stale_price": "미국장 휴장 후 최신 정규장 가격 미확인",
        "us_regular_open_confirmation_required": "미국장 개장 후 가격 확인 필요",
        "high_valuation_rate_sensitive": "고PER 성장주로 금리 민감도 높음",
        "above_entry": "현재가가 계산 진입가보다 높음",
        "crisis_no_new_buy": "위기 레짐 신규 매수 금지",
    }
    suffix = f" ({', '.join(details[:3])})" if details else ""
    return labels.get(reason, "리스크 게이트 미통과") + suffix


def _stop_invalidation_conflict(rec: dict) -> bool:
    stop = _num(rec.get("stop_loss"))
    if stop is None:
        return False
    text = str(rec.get("invalidation_condition") or "")
    numbers = []
    for raw in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        value = _num(raw.replace(",", ""))
        if value:
            numbers.append(value)
    if not numbers:
        return False
    closest = min(numbers, key=lambda value: abs(value - stop))
    return abs(closest - stop) / max(stop, 1.0) > 0.05


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        if item and item not in seen:
            seen.add(item)
            result.append(item)
    return result
