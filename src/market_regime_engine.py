"""
market_regime_engine.py — score-based market regime and risk budget.
"""
from __future__ import annotations

from typing import Any


RISK_BUDGETS = {
    "risk_on": {
        "max_total_new_exposure_pct": 25,
        "max_single_position_pct": 5,
        "cash_min_pct": 15,
        "max_executable_recommendations": 5,
        "per_trade_risk_pct": 0.60,
    },
    "neutral": {
        "max_total_new_exposure_pct": 12,
        "max_single_position_pct": 3,
        "cash_min_pct": 30,
        "max_executable_recommendations": 3,
        "per_trade_risk_pct": 0.40,
    },
    "neutral_risk_off": {
        "max_total_new_exposure_pct": 8,
        "max_single_position_pct": 2,
        "cash_min_pct": 40,
        "max_executable_recommendations": 2,
        "per_trade_risk_pct": 0.25,
    },
    "risk_off": {
        "max_total_new_exposure_pct": 4,
        "max_single_position_pct": 1,
        "cash_min_pct": 55,
        "max_executable_recommendations": 1,
        "per_trade_risk_pct": 0.15,
    },
    "crisis": {
        "max_total_new_exposure_pct": 0,
        "max_single_position_pct": 0,
        "cash_min_pct": 70,
        "max_executable_recommendations": 0,
        "per_trade_risk_pct": 0.0,
    },
}


def calculate_market_regime(ctx: dict) -> dict[str, Any]:
    indices = ctx.get("indices") or {}
    sectors = ctx.get("sector_mom") or {}

    score = 50.0
    reasons = []

    score += _index_adjust(indices, "SP500", "S&P500", reasons, weight=1.3)
    score += _index_adjust(indices, "NASDAQ", "NASDAQ", reasons, weight=1.5)
    score += _index_adjust(indices, "KOSPI", "KOSPI", reasons, weight=1.1)
    score += _index_adjust(indices, "KOSDAQ", "KOSDAQ", reasons, weight=1.1)

    vix = _num((indices.get("VIX") or {}).get("price"))
    if vix is not None:
        if vix >= 30:
            score -= 18
            reasons.append("VIX 30 이상")
        elif vix >= 24:
            score -= 10
            reasons.append("VIX 고위험 구간")
        elif vix < 16:
            score += 7
            reasons.append("VIX 안정")

    us10y = _num((indices.get("US10Y") or {}).get("change_pct"))
    if us10y is not None and us10y > 1.0:
        score -= 6
        reasons.append("US10Y 상승")

    usdkrw = _num((indices.get("USD_KRW") or {}).get("change_pct"))
    if usdkrw is not None and usdkrw > 0.7:
        score -= 7
        reasons.append("USD/KRW 상승")

    brent = _num((indices.get("BRENT") or {}).get("change_pct"))
    if brent is not None and brent > 2.0:
        score -= 6
        reasons.append("브렌트유 급등")

    semiconductor_weak = any(
        ("반도체" in sector and (data or {}).get("ret_5d", 0) < -2)
        for sector, data in sectors.items()
    )
    if semiconductor_weak:
        score -= 7
        reasons.append("반도체 섹터 약세")

    score = round(max(0.0, min(100.0, score)), 1)
    global_regime = _regime_for_score(score)
    kr_regime = _regional_regime(indices, ("KOSPI", "KOSDAQ"))
    us_regime = _regional_regime(indices, ("SP500", "NASDAQ"))
    budget = dict(RISK_BUDGETS[global_regime])

    return {
        "global_regime": global_regime,
        "kr_regime": kr_regime,
        "us_regime": us_regime,
        "risk_score": score,
        "risk_budget": budget,
        "regime_reasons": reasons or ["주요 위험 지표 중립"],
    }


def _index_adjust(indices: dict, key: str, label: str, reasons: list[str], weight: float = 1.0) -> float:
    chg = _num((indices.get(key) or {}).get("change_pct"))
    if chg is None:
        return 0.0
    if chg <= -2.0:
        reasons.append(f"{label} 급락")
        return -8.0 * weight
    if chg <= -1.0:
        reasons.append(f"{label} 약세")
        return -5.0 * weight
    if chg >= 1.0:
        reasons.append(f"{label} 강세")
        return 4.0 * weight
    return 0.0


def _regional_regime(indices: dict, keys: tuple[str, str]) -> str:
    changes = [_num((indices.get(key) or {}).get("change_pct")) for key in keys]
    changes = [item for item in changes if item is not None]
    if not changes:
        return "unknown"
    avg = sum(changes) / len(changes)
    if avg > 0.7:
        return "strong"
    if avg < -1.0:
        return "weak"
    if avg < -0.3:
        return "soft"
    return "neutral"


def _regime_for_score(score: float) -> str:
    if score >= 65:
        return "risk_on"
    if score >= 50:
        return "neutral"
    if score >= 35:
        return "neutral_risk_off"
    if score >= 20:
        return "risk_off"
    return "crisis"


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
