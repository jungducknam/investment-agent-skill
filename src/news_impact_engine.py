"""
news_impact_engine.py — deterministic impact scoring for classified news.
"""
from __future__ import annotations

from typing import Any

from .news_classifier import SOURCE_SCORES, THEME_IMPLICATIONS


def calculate_news_impact(news: dict[str, Any], context: dict | None = None, historical_summary: dict | None = None) -> dict[str, Any]:
    result = dict(news)
    direction_conflicts = _direction_conflicts(result)
    if direction_conflicts:
        errors = list(result.get("validation_errors") or [])
        result["validation_errors"] = [*errors, *direction_conflicts]
    historical = historical_summary or {}
    source_score = _source_score(result.get("source"))
    affected_asset_importance = _affected_asset_importance(result.get("affected_assets") or [])
    historical_impact_score = float(historical.get("historical_impact_score", 50) or 50)
    current_market_reaction_score = _current_market_reaction_score(result, context or {})
    similar_count = int(float(historical.get("similar_event_count", 0) or 0))
    sample_sufficient = similar_count >= 5
    directional_confidence = _directional_confidence_score(historical, sample_sufficient)

    impact_score = (
        0.20 * float(result.get("market_relevance_score", 0) or 0)
        + 0.15 * source_score
        + 0.15 * float(result.get("novelty_score", 0) or 0)
        + 0.15 * affected_asset_importance
        + 0.10 * float(result.get("urgency_score", 0) or 0)
        + 0.10 * historical_impact_score
        + 0.10 * current_market_reaction_score
        + 0.05 * float(result.get("confidence", 0) or 0)
    )

    primary = result.get("primary_theme") or "irrelevant_market_news"
    result["source_score"] = round(source_score, 1)
    result["affected_asset_importance"] = round(affected_asset_importance, 1)
    result["historical_impact_score"] = round(historical_impact_score, 1)
    result["historical_reaction"] = _historical_reaction_payload(historical, sample_sufficient)
    result["current_market_reaction_score"] = round(current_market_reaction_score, 1)
    result["impact_score"] = round(max(0.0, min(100.0, impact_score)), 1)
    result["directional_confidence_score"] = directional_confidence
    result["trading_signal_strength"] = _trading_signal_strength(
        result["impact_score"],
        directional_confidence,
        current_market_reaction_score,
        sample_sufficient,
    )
    if direction_conflicts:
        result["trading_signal_strength"] = "낮음"
    result["importance_text"] = THEME_IMPLICATIONS.get(primary, THEME_IMPLICATIONS["irrelevant_market_news"])
    result["investment_implication"] = result["importance_text"]
    result["impact_channel"] = _primary_channel(result.get("affected_assets") or [])
    result["current_market_reaction"] = _reaction_text(result["current_market_reaction_score"])

    if result.get("validation_status") == "FAIL" or result["impact_score"] < 65:
        result["should_include_in_report"] = False
        if result.get("report_priority") != "exclude":
            result["report_priority"] = "low"
    elif result.get("report_priority") != "exclude":
        result["should_include_in_report"] = True
        result["report_priority"] = "high" if result["impact_score"] >= 80 else "medium"
    return result


def _direction_conflicts(news: dict[str, Any]) -> list[str]:
    text = f"{news.get('title') or news.get('headline') or ''} {news.get('summary') or ''}".lower()
    assets = {asset.get("asset"): asset.get("direction") for asset in news.get("affected_assets") or []}
    errors = []
    if any(term in text for term in ("oil ease", "oil eases", "crude lower", "edges lower")) and assets.get("BRENT") == "positive":
        errors.append("headline_oil_ease_but_brent_positive")
    if any(term in text for term in ("dollar falls", "dollar eases", "dollar weakens")) and assets.get("USD") == "positive":
        errors.append("headline_dollar_eases_but_usd_positive")
    return errors


def _source_score(source: str | None) -> float:
    if not source:
        return 55.0
    return float(SOURCE_SCORES.get(str(source), 62))


def _affected_asset_importance(assets: list[dict[str, Any]]) -> float:
    if not assets:
        return 30.0
    best = 0.0
    for asset in assets:
        strength = float(asset.get("impact_strength", 0) or 0)
        scope_bonus = 10 if asset.get("impact_scope") == "direct" else 0
        type_bonus = 10 if asset.get("asset_type") in {"ticker", "index", "commodity", "rate"} else 0
        best = max(best, min(100.0, strength + scope_bonus + type_bonus))
    return best


def _current_market_reaction_score(news: dict[str, Any], context: dict) -> float:
    indices = context.get("indices") or {}
    sectors = context.get("sector_mom") or {}
    assets = news.get("affected_assets") or []
    score = 50.0

    for asset in assets[:4]:
        name = asset.get("asset")
        direction = asset.get("direction")
        change = None
        if name in indices:
            change = (indices.get(name) or {}).get("change_pct")
        elif name == "NASDAQ" and "NASDAQ" in indices:
            change = (indices.get("NASDAQ") or {}).get("change_pct")
        elif name == "BRENT" and "BRENT" in indices:
            change = (indices.get("BRENT") or {}).get("change_pct")
        elif name == "XLE":
            change = (sectors.get("에너지(US)") or {}).get("ret_5d")
        try:
            change = float(change)
        except (TypeError, ValueError):
            continue
        if direction == "positive":
            score += change * 3
        elif direction == "negative":
            score -= change * 3
    return max(0.0, min(100.0, score))


def _primary_channel(assets: list[dict[str, Any]]) -> str:
    if not assets:
        return ""
    return str(assets[0].get("channel") or "")


def _reaction_text(score: float) -> str:
    if score >= 70:
        return "관련 자산 가격 반응이 뉴스 방향과 대체로 일치"
    if score <= 35:
        return "현재 가격 반응은 약하거나 반대 방향"
    return "현재 가격 반응은 중립 또는 확인 필요"


def _historical_reaction_payload(history: dict[str, Any], sample_sufficient: bool) -> dict[str, Any]:
    payload = dict(history) if history else _empty_historical_reaction()
    payload["sample_sufficient"] = sample_sufficient
    if not sample_sufficient:
        payload["status"] = "insufficient_sample"
    return payload


def _directional_confidence_score(history: dict[str, Any], sample_sufficient: bool) -> int | None:
    if not sample_sufficient:
        return None
    try:
        hit_rate = float(history.get("directional_hit_rate", 0) or 0)
    except (TypeError, ValueError):
        return None
    return int(max(0, min(100, round(hit_rate * 100))))


def _trading_signal_strength(
    impact_score: float,
    directional_confidence: int | None,
    current_market_reaction_score: float,
    sample_sufficient: bool,
) -> str:
    if not sample_sufficient or directional_confidence is None:
        return "낮음"
    if impact_score >= 80 and directional_confidence >= 60 and current_market_reaction_score >= 55:
        return "높음"
    if impact_score >= 65 and directional_confidence >= 45:
        return "중간"
    return "낮음"


def _empty_historical_reaction() -> dict[str, Any]:
    return {
        "similar_event_count": 0,
        "median_abnormal_return_1d": 0.0,
        "median_abnormal_return_5d": 0.0,
        "directional_hit_rate": 0.0,
        "avg_volume_zscore": 0.0,
        "historical_impact_score": 50,
        "sample_sufficient": False,
        "status": "insufficient_sample",
    }
