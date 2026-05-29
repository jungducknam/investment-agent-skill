"""
recommendation_safety.py — deterministic recommendation safety controls.

LLM output is treated as candidate commentary. Executable prices, risk/reward,
position size, evidence links, and hard risk gates are calculated here before
Telegram output.
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any

from .calendar_verifier import event_risk_gate, report_policy
from .config import KST
from .data_quality_engine import build_data_quality_lookup, build_data_quality_table, summarize_data_quality
from .market_regime_engine import calculate_market_regime
from .market_session import annotate_price_session, build_market_session_status
from .price_engine import calculate_price_plan
from .recommendation_validator import briefing_only_report, validate_report_schema
from .risk_gate import evaluate_risk_gate, primary_reject_reason, waiting_reason


MAX_SINGLE_STOCK_PCT = 4.0
MAX_PORTFOLIO_LOSS_PCT = 0.5
MIN_DATA_QUALITY_SCORE = 0.80


def calc_atr(highs: list[float], lows: list[float], closes: list[float], period: int = 14) -> float | None:
    """Average true range using the most recent regular-session rows."""
    if not highs or not lows or not closes:
        return None
    row_count = min(len(highs), len(lows), len(closes))
    if row_count < period + 1:
        return None

    true_ranges = []
    for idx in range(row_count - period, row_count):
        high = _normal_price(highs[idx])
        low = _normal_price(lows[idx])
        prev_close = _normal_price(closes[idx - 1])
        if high is None or low is None or prev_close is None:
            continue
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))

    if not true_ranges:
        return None
    return sum(true_ranges) / len(true_ranges)


def apply_recommendation_safety_controls(report: dict, ctx: dict) -> dict:
    """
    Calculate executable recommendation fields and demote candidates that fail
    deterministic gates.
    """
    ctx = ctx or {}
    report_type = ctx.get("report_type") or "US_POST_CLOSE"
    policy = report_policy(report_type)
    regime = ctx.get("market_regime") or calculate_market_regime(ctx)
    now_kst = _context_time(ctx)
    ctx["market_session_status"] = ctx.get("market_session_status") or build_market_session_status(now_kst)
    ctx["stock_prices"] = _annotate_stock_prices(ctx.get("stock_prices") or {}, ctx["market_session_status"], now_kst)
    verified_events = ctx.get("verified_events") or []

    _normalize_market_summary(report, regime)

    evidence = _build_evidence_table(ctx)
    report["evidence"] = evidence
    report["report_type"] = report_type
    report["execution_policy"] = policy["execution_policy"]
    report["market_regime"] = regime

    data_quality_rows = ctx.get("data_quality") or build_data_quality_table(ctx, now_kst=now_kst)
    data_quality_by_key = ctx.get("data_quality_lookup") or build_data_quality_lookup(ctx, now_kst=now_kst)
    safe_recommendations = []
    rejected = list(report.get("rejected_candidates") or [])
    waiting = list(report.get("waiting_list") or [])
    executable_count = 0
    max_executable = min(
        int(policy.get("max_executable_recommendations", 0) or 0),
        int((regime.get("risk_budget") or {}).get("max_executable_recommendations", 0) or 0),
    )

    for raw_rec in list(report.get("recommendations") or []):
        rec = dict(raw_rec)
        ticker = str(rec.get("ticker") or "").strip()
        signal = _find_by_ticker(ctx.get("entry_signals") or {}, ticker) or {}
        quality = _find_by_ticker(data_quality_by_key, ticker) or _empty_quality(ticker)
        if not rec.get("name") and signal.get("name"):
            rec["name"] = signal["name"]

        evidence_ids = _candidate_evidence_ids(ticker, evidence, rec)
        rec["evidence_ids"] = evidence_ids
        rec["evidence_types"] = _candidate_evidence_types(evidence_ids, evidence)
        rec["data_quality_score"] = quality.get("data_quality_score", quality.get("score", 0.0))
        rec["data_quality_warnings"] = quality.get("warnings", [])

        price_result = calculate_price_plan(rec, signal, quality, regime)
        rec.update(price_result)

        event_gate = event_risk_gate(ticker, now_kst, verified_events)
        gate = evaluate_risk_gate(
            rec,
            signal,
            quality,
            regime,
            event_gate,
            report_type,
            extra_context=_risk_extra_context(ctx, ticker),
        )
        if gate["is_executable"] and executable_count >= max_executable:
            gate["risk_gate_status"] = "PASS_WITH_CONDITIONS"
            gate["is_executable"] = False
            gate["action_status"] = "conditional_entry"
            gate["allowed_action"] = "conditional_entry"
            gate["execution_blockers"] = _dedupe((gate.get("execution_blockers") or []) + ["regime_executable_limit"])
            gate["warning_rules"] = _dedupe((gate.get("warning_rules") or []) + ["regime_executable_limit"])
        rec.update(gate)
        rec["review_after"] = "US market close" if report_type == "US_INTRADAY" else rec.get("review_after", "")

        if gate["is_executable"]:
            executable_count += 1
            rec["rank"] = len(safe_recommendations) + 1
            safe_recommendations.append(rec)
            continue

        if gate["action_status"] == "conditional_entry":
            rec["rank"] = len(safe_recommendations) + 1
            safe_recommendations.append(rec)
            continue

        if gate["action_status"] == "watch_wait":
            detail_rules = gate.get("execution_blockers") or []
            reason = primary_reject_reason(detail_rules)
            waiting.append({
                "name": rec.get("name", ticker),
                "ticker": ticker,
                "reason": waiting_reason(reason, detail_rules),
                "target_entry": rec.get("entry_price"),
                "condition": rec.get("entry_condition") or "리스크 게이트 재통과 시 재검토",
                "action_status": "watch_wait",
            })
            continue

        detail_rules = gate.get("failed_rules") or gate.get("execution_blockers") or []
        reason = primary_reject_reason(detail_rules)
        rejected.append({
            "ticker": ticker,
            "name": rec.get("name", ticker),
            "reason": reason,
            "details": "; ".join(detail_rules),
            "failed_rules": gate.get("failed_rules") or [],
            "execution_blockers": gate.get("execution_blockers") or [],
            "action_status": "blocked",
        })

    rejected = _enrich_existing_rejections(
        rejected,
        ctx=ctx,
        data_quality_by_key=data_quality_by_key,
        regime=regime,
        verified_events=verified_events,
        now_kst=now_kst,
        report_type=report_type,
    )

    report["recommendations"] = _dedupe_items_by_ticker(safe_recommendations)
    seen_for_rejected = _item_key_set(report["recommendations"])
    report["rejected_candidates"] = _dedupe_items_by_ticker(rejected, seen_keys=seen_for_rejected)
    seen_for_waiting = seen_for_rejected | _item_key_set(report["rejected_candidates"])
    report["waiting_list"] = _dedupe_items_by_ticker(waiting, seen_keys=seen_for_waiting)
    seen_for_watchlist = seen_for_waiting | _item_key_set(report["waiting_list"])
    report["watchlist"] = _dedupe_items_by_ticker(report.get("watchlist") or [], seen_keys=seen_for_watchlist)
    report["data_quality_summary"] = summarize_data_quality(data_quality_rows)
    try:
        validate_report_schema(report)
    except Exception as exc:
        original = dict(report)
        report.clear()
        report.update(briefing_only_report(original, exc))
    return report


def _normalize_market_summary(report: dict, regime: dict) -> None:
    market_summary = report.get("market_summary")
    if not isinstance(market_summary, dict):
        return

    score = _normal_price(market_summary.get("sentiment_score"))
    if score is None:
        score = _normal_price((regime or {}).get("risk_score"))
    if score is None:
        return

    if score > 10:
        score = score / 10.0
    score = max(0.0, min(10.0, score))
    market_summary["sentiment_score"] = round(score, 1)


def _calculate_price_engine_fields(rec: dict, signal: dict, quality: dict) -> dict:
    ticker = str(rec.get("ticker") or "")
    currency = rec.get("currency") or ("KRW" if rec.get("market") == "KR" else "USD")
    current = _first_price(
        quality.get("price"),
        signal.get("current_price"),
        rec.get("current_price"),
    )
    if current is None:
        return {
            "current_price": None,
            "entry_price": None,
            "target_price_1": None,
            "target_price_2": None,
            "stop_loss": None,
            "upside_pct": None,
            "risk_reward_1": 0.0,
            "position_size_pct": 0.0,
            "max_portfolio_loss_pct": MAX_PORTFOLIO_LOSS_PCT,
            "price_source": "unavailable",
            "price_confidence": 0.0,
            "price_basis": [],
            "entry_type": "unavailable",
            "action": "avoid",
        }

    signal_label = str(signal.get("signal") or rec.get("entry_signal") or "")
    suggested_entry = _normal_price(signal.get("suggested_entry"))
    if signal_label == "대기" and suggested_entry and suggested_entry < current:
        entry = suggested_entry
        entry_type = "pullback"
        action = "conditional_buy"
    else:
        entry = current
        entry_type = "limit"
        action = "buy_zone" if signal_label == "적정" else "conditional_buy"

    atr = _normal_price(signal.get("atr_14"))
    support = _normal_price(signal.get("support_20d"))
    resistance = _normal_price(signal.get("resistance_20d"))

    stop_candidates = [entry * 0.88]
    price_basis = ["current_price_lookup"]
    if atr and atr > 0:
        stop_candidates.append(entry - 1.5 * atr)
        price_basis.append("ATR_14")
    if support and 0 < support < entry:
        stop_candidates.append(support * 0.99)
        price_basis.append("support_20d")
    stop_loss = max(candidate for candidate in stop_candidates if candidate < entry)

    risk_per_share = entry - stop_loss
    target_candidates = [entry + 2.0 * risk_per_share]
    if resistance and resistance > entry:
        target_candidates.append(resistance)
        price_basis.append("resistance_20d")
    target_1 = max(target_candidates)
    target_2 = max(entry + 3.0 * risk_per_share, target_1 * 1.05)

    risk_reward = (target_1 - entry) / risk_per_share if risk_per_share > 0 else 0.0
    risk_pct = risk_per_share / entry * 100 if entry > 0 else 0.0
    if risk_pct > 0:
        position_size = min(MAX_SINGLE_STOCK_PCT, MAX_PORTFOLIO_LOSS_PCT / risk_pct * 100)
    else:
        position_size = 0.0

    return {
        "current_price": _round_price(current, currency),
        "entry_price": _round_price(entry, currency),
        "entry_type": entry_type,
        "entry_signal": signal_label or rec.get("entry_signal") or "대기",
        "entry_condition": _entry_condition(action, entry, currency),
        "target_price_1": _round_price(target_1, currency),
        "target_price_2": _round_price(target_2, currency),
        "stop_loss": _round_price(stop_loss, currency),
        "upside_pct": round((target_1 / current - 1) * 100, 1) if current > 0 else None,
        "risk_reward_1": round(risk_reward, 2),
        "position_size_pct": round(position_size, 2),
        "max_portfolio_loss_pct": MAX_PORTFOLIO_LOSS_PCT,
        "price_source": "calculated_by_rule_engine",
        "price_confidence": quality["score"],
        "price_basis": price_basis,
        "action": action,
    }


def _risk_gate(rec: dict, signal: dict, quality: dict) -> dict:
    reasons = []
    if rec.get("price_source") == "unavailable" or rec.get("current_price") is None:
        reasons.append("price_unavailable")
    if quality["score"] < MIN_DATA_QUALITY_SCORE:
        reasons.append("data_low_quality")
    if len(rec.get("evidence_ids") or []) < 2:
        reasons.append("missing_evidence")

    rsi = _normal_price(signal.get("rsi"))
    bb_pos = _normal_price(signal.get("bb_position"))
    signal_label = str(signal.get("signal") or rec.get("entry_signal") or "")
    if signal_label == "과열" or (rsi is not None and rsi >= 80) or (bb_pos is not None and bb_pos > 100):
        reasons.append("overbought")

    risk_reward = _normal_price(rec.get("risk_reward_1")) or 0.0
    if risk_reward < MIN_RISK_REWARD:
        reasons.append("poor_rr")

    current = _normal_price(rec.get("current_price"))
    entry = _normal_price(rec.get("entry_price"))
    if current and entry and current > entry * 1.03:
        reasons.append("above_entry")

    return {"status": "FAIL" if reasons else "PASS", "reasons": reasons}


def _build_evidence_table(ctx: dict) -> list[dict[str, Any]]:
    evidence = []
    for ticker, info in (ctx.get("stock_prices") or {}).items():
        price = _normal_price((info or {}).get("price") or (info or {}).get("current_price"))
        if price is None:
            continue
        evidence.append({
            "evidence_id": _evidence_id("price", ticker),
            "type": "price",
            "source": (info or {}).get("source") or "market_data",
            "summary": f"{ticker} current price {price}",
            "affected_assets": [str(ticker)],
        })

    for ticker, signal in (ctx.get("entry_signals") or {}).items():
        evidence.append({
            "evidence_id": _evidence_id("technical", ticker),
            "type": "technical",
            "source": "entry_filter",
            "summary": " | ".join(str(item) for item in (signal or {}).get("reasons", [])[:3]),
            "affected_assets": [str(ticker)],
        })

    for idx, item in enumerate((ctx.get("detailed_news") or [])[:12], 1):
        evidence.append({
            "evidence_id": f"news_{idx:03d}",
            "type": "news",
            "source": item.get("source") or "news",
            "summary": item.get("title") or "",
            "affected_assets": item.get("affected_assets") or item.get("themes") or [],
            "url": item.get("link") or "",
        })

    if ctx.get("indices"):
        evidence.append({
            "evidence_id": "macro_indices",
            "type": "macro",
            "source": "market_indices",
            "summary": "Major index, rate, FX, oil snapshot",
            "affected_assets": ["KR", "US"],
        })
    return evidence


def _candidate_evidence_ids(ticker: str, evidence: list[dict[str, Any]], rec: dict) -> list[str]:
    ids = [str(item) for item in rec.get("evidence_ids") or [] if item]
    lookup = _lookup_keys(ticker)
    for item in evidence:
        evidence_id = item.get("evidence_id")
        affected = {str(asset).upper() for asset in item.get("affected_assets") or []}
        if item.get("type") in {"price", "technical"} and affected & lookup:
            ids.append(evidence_id)
        elif item.get("type") == "news" and len(ids) < 3:
            ids.append(evidence_id)
    if "macro_indices" not in ids and any(item.get("evidence_id") == "macro_indices" for item in evidence):
        ids.append("macro_indices")
    return _dedupe(ids)


def _candidate_evidence_types(evidence_ids: list[str], evidence: list[dict[str, Any]]) -> list[str]:
    by_id = {str(item.get("evidence_id")): str(item.get("type") or "") for item in evidence}
    return _dedupe([by_id.get(str(evidence_id), "") for evidence_id in evidence_ids])


def _risk_extra_context(ctx: dict, ticker: str) -> dict[str, Any]:
    insights = ctx.get("yahoo_insights") or {}
    return {
        "fundamentals": _find_by_ticker(insights, ticker) or {},
        "us10y": (ctx.get("indices") or {}).get("US10Y") or {},
        "market_session_status": ctx.get("market_session_status") or {},
    }


def _build_data_quality_lookup(ctx: dict) -> dict[str, dict[str, Any]]:
    lookup = {}
    entry_signals = ctx.get("entry_signals") or {}
    for ticker, info in (ctx.get("stock_prices") or {}).items():
        price = _normal_price((info or {}).get("price") or (info or {}).get("current_price"))
        source = str((info or {}).get("source") or "market_data")
        score = 0.95 if source.upper() == "KIS" else 0.86 if source else 0.82
        warnings = []
        signal = _find_by_ticker(entry_signals, ticker)
        if not signal:
            score -= 0.08
            warnings.append("technical_signal_missing")
        if price is None:
            score = 0.0
            warnings.append("price_missing")
        payload = {
            "ticker": str(ticker),
            "price": price,
            "source_primary": source,
            "score": max(0.0, min(1.0, round(score, 2))),
            "warnings": warnings,
        }
        for key in _lookup_keys(ticker):
            lookup[key] = payload
    return lookup


def _build_data_quality_summary(lookup: dict[str, dict[str, Any]]) -> dict[str, Any]:
    unique = {}
    for item in lookup.values():
        unique[item["ticker"]] = item
    scores = [item["score"] for item in unique.values()]
    stale_or_low = [item["ticker"] for item in unique.values() if item["score"] < MIN_DATA_QUALITY_SCORE]
    warnings = []
    for item in unique.values():
        warnings.extend(f"{item['ticker']}:{warning}" for warning in item["warnings"])
    return {
        "overall_score": round(sum(scores) / len(scores), 2) if scores else 0.0,
        "critical_warnings": warnings,
        "stale_data": stale_or_low,
        "source_conflicts": [],
    }


def _context_time(ctx: dict) -> datetime:
    raw = ctx.get("collected_at")
    if raw:
        try:
            parsed = datetime.fromisoformat(str(raw))
            if parsed.tzinfo is None:
                return KST.localize(parsed)
            return parsed.astimezone(KST)
        except ValueError:
            pass
    return datetime.now(KST)


def _annotate_stock_prices(stock_prices: dict, sessions: dict, now_kst: datetime) -> dict:
    annotated = {}
    for ticker, info in (stock_prices or {}).items():
        payload = dict(info or {})
        market = str(payload.get("market") or ("KR" if str(ticker).isdigit() else "US")).upper()
        annotated[ticker] = annotate_price_session(payload, market, sessions.get(market), now_kst=now_kst)
    return annotated


def _find_by_ticker(mapping: dict, ticker: str) -> Any:
    if not mapping:
        return None
    for key in _lookup_keys(ticker):
        if key in mapping:
            return mapping[key]
    return None


def _lookup_keys(ticker: str) -> set[str]:
    raw = str(ticker or "").strip()
    if not raw:
        return set()
    keys = {raw, raw.upper()}
    if raw.endswith((".KS", ".KQ")):
        stem = raw.split(".")[0]
        keys.update({stem, stem.zfill(6), stem.upper()})
    elif raw.isdigit():
        stem = raw.zfill(6)
        keys.update({stem, f"{stem}.KS", f"{stem}.KQ"})
    return {key.upper() for key in keys}


def _evidence_id(prefix: str, ticker: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9]+", "_", str(ticker).upper()).strip("_").lower()
    return f"{prefix}_{normalized}"


def _normal_price(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _first_price(*values) -> float | None:
    for value in values:
        price = _normal_price(value)
        if price is not None:
            return price
    return None


def _round_price(value: float, currency: str) -> float:
    if currency == "KRW":
        return float(round(value, -2))
    return round(value, 2)


def _entry_condition(action: str, entry: float, currency: str) -> str:
    unit = "원" if currency == "KRW" else "USD"
    if action == "buy_zone":
        return f"{entry:,.0f}{unit} 부근 분할 접근"
    return f"{entry:,.0f}{unit} 이하 조정 시 접근"


def _primary_reject_reason(reasons: list[str]) -> str:
    priority = [
        "overbought",
        "poor_rr",
        "data_low_quality",
        "price_unavailable",
        "missing_evidence",
        "above_entry",
    ]
    for reason in priority:
        if reason in reasons:
            return reason
    return reasons[0] if reasons else "risk_gate_failed"


def _waiting_reason(reason: str, signal: dict, reasons: list[str]) -> str:
    if reason == "overbought":
        return f"과열 신호로 대기 ({', '.join(reasons)})"
    if reason == "poor_rr":
        return "손익비 기준 미달로 대기"
    if reason == "data_low_quality":
        return "데이터 품질 기준 미달로 대기"
    if reason == "price_unavailable":
        return "가격 근거 부족으로 대기"
    if reason == "above_entry":
        return "현재가가 계산 진입가보다 높아 대기"
    if signal.get("reasons"):
        return " / ".join(str(item) for item in signal.get("reasons", [])[:2])
    return "리스크 게이트 미통과로 대기"


def _empty_quality(ticker: str) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "price": None,
        "source_primary": "",
        "score": 0.0,
        "warnings": ["price_missing"],
    }


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        deduped.append(item)
    return deduped


def _dedupe_items_by_ticker(items: list[Any], seen_keys: set[str] | None = None) -> list[Any]:
    seen = set(seen_keys or set())
    result = []
    for item in items or []:
        keys = _item_identity_keys(item)
        if keys and seen & keys:
            continue
        if keys:
            seen.update(keys)
        result.append(item)
    return result


def _enrich_existing_rejections(
    rejected: list[Any],
    ctx: dict,
    data_quality_by_key: dict,
    regime: dict,
    verified_events: list[dict[str, Any]],
    now_kst: datetime,
    report_type: str,
) -> list[Any]:
    enriched_items = []
    entry_signals = ctx.get("entry_signals") or {}
    for item in rejected or []:
        if not isinstance(item, dict) or _has_specific_reject_detail(item):
            enriched_items.append(item)
            continue

        enriched = dict(item)
        ticker = str(enriched.get("ticker") or enriched.get("symbol") or "").strip()
        if not ticker:
            enriched_items.append(enriched)
            continue

        signal = _find_by_ticker(entry_signals, ticker) or {}
        quality = _find_by_ticker(data_quality_by_key, ticker) or _empty_quality(ticker)
        if not enriched.get("name") and signal.get("name"):
            enriched["name"] = signal["name"]

        rec_for_gate = dict(enriched)
        rec_for_gate.update(calculate_price_plan(rec_for_gate, signal, quality, regime))
        event_gate = event_risk_gate(ticker, now_kst, verified_events)
        gate = evaluate_risk_gate(
            rec_for_gate,
            signal,
            quality,
            regime,
            event_gate,
            report_type,
            extra_context=_risk_extra_context(ctx, ticker),
        )
        rules = _dedupe((gate.get("failed_rules") or []) + (gate.get("execution_blockers") or []))
        if rules:
            enriched["failed_rules"] = gate.get("failed_rules") or []
            enriched["execution_blockers"] = gate.get("execution_blockers") or []
            enriched["reason"] = primary_reject_reason(rules)
            enriched["details"] = "; ".join(rules)
        enriched_items.append(enriched)
    return enriched_items


def _has_specific_reject_detail(item: dict) -> bool:
    if item.get("failed_rules") or item.get("execution_blockers"):
        return True
    details = str(item.get("details") or "").strip()
    if not details:
        return False
    generic = {"리스크 게이트 미통과", "risk_gate_failed", "blocked"}
    return details not in generic


def _item_key_set(items: list[Any]) -> set[str]:
    keys: set[str] = set()
    for item in items or []:
        keys.update(_item_identity_keys(item))
    return keys


def _item_identity_keys(item: Any) -> set[str]:
    if isinstance(item, dict):
        value = item.get("ticker") or item.get("symbol") or item.get("name")
    else:
        value = item
    if value is None:
        return set()
    return _lookup_keys(str(value))
