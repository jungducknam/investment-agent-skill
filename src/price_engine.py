"""
price_engine.py — deterministic entry, stop, target, and sizing engine.
"""
from __future__ import annotations

import math
from typing import Any


def calculate_price_plan(rec: dict, signal: dict, quality: dict, regime: dict) -> dict[str, Any]:
    ticker = str(rec.get("ticker") or "")
    currency = rec.get("currency") or ("KRW" if rec.get("market") == "KR" else "USD")
    current = _first_price(quality.get("price"), signal.get("current_price"), rec.get("current_price"))
    if current is None:
        return _unavailable_price_plan()

    entry_type, entry, action = _entry_plan(current, signal, rec)
    atr = _normal_price(signal.get("atr_14"))
    support = _normal_price(signal.get("support_20d"))
    resistance = _normal_price(signal.get("resistance_20d"))
    style = str(rec.get("style") or "")

    stop_loss, stop_basis = calculate_stop_loss(entry, atr, support, style)
    risk_per_share = entry - stop_loss
    target_1, target_2, target_basis = calculate_targets(entry, risk_per_share, resistance)
    risk_reward = validate_risk_reward(entry, stop_loss, target_1)["rr"]

    risk_budget = (regime or {}).get("risk_budget") or {}
    per_trade_risk = float(risk_budget.get("per_trade_risk_pct", 0.4) or 0.0)
    max_position = float(risk_budget.get("max_single_position_pct", 3.0) or 0.0)
    position_size = calculate_position_size(entry, stop_loss, per_trade_risk, max_position)

    price_basis = {
        "entry": _entry_basis(entry_type),
        "stop": stop_basis,
        "target": target_basis,
    }
    price_basis_list = ["current_price_lookup", price_basis["entry"], price_basis["stop"], price_basis["target"]]

    return {
        "current_price": _round_price(current, currency),
        "entry_price": _round_price(entry, currency),
        "entry_type": entry_type,
        "entry_signal": signal.get("signal") or rec.get("entry_signal") or "대기",
        "entry_condition": _entry_condition(action, entry, currency),
        "target_price_1": _round_price(target_1, currency),
        "target_price_2": _round_price(target_2, currency),
        "stop_loss": _round_price(stop_loss, currency),
        "upside_pct": round((target_1 / current - 1) * 100, 1) if current > 0 else None,
        "risk_reward": round(risk_reward, 2),
        "risk_reward_1": round(risk_reward, 2),
        "position_size_pct": round(position_size, 2),
        "max_portfolio_loss_pct": per_trade_risk,
        "price_source": "calculated_by_rule_engine",
        "price_confidence": quality.get("data_quality_score", quality.get("score", 0.0)),
        "price_basis": price_basis,
        "price_basis_codes": price_basis_list,
        "action": action,
    }


def calculate_stop_loss(entry_price: float, atr14: float | None, support: float | None, style: str) -> tuple[float, str]:
    if "단타" in style:
        multiplier = 1.0
    elif "스윙" in style:
        multiplier = 1.5
    else:
        multiplier = 2.0

    candidates = []
    if atr14 and atr14 > 0:
        atr_stop = entry_price - atr14 * multiplier
        candidates.append((atr_stop, f"{multiplier:g}ATR"))
    if support and 0 < support < entry_price:
        candidates.append((support * 0.99, "support_20d_break"))
    candidates.append((entry_price * 0.88, "12pct_failsafe"))

    valid = [(price, basis) for price, basis in candidates if 0 < price < entry_price]
    return max(valid, key=lambda item: item[0])


def calculate_targets(entry: float, risk_per_share: float, resistance: float | None) -> tuple[float, float, str]:
    target_1 = entry + 2.0 * risk_per_share
    basis = "2R_measured_move"
    if resistance and resistance > entry:
        target_1 = max(target_1, resistance)
        basis = "recent_resistance_or_2R"
    target_2 = max(entry + 3.0 * risk_per_share, target_1 * 1.05)
    return target_1, target_2, basis


def validate_risk_reward(entry: float, stop: float, target: float) -> dict[str, Any]:
    downside = entry - stop
    upside = target - entry
    if downside <= 0:
        return {"status": "FAIL", "rr": 0.0, "reason": "손절가가 진입가보다 높거나 같음"}
    rr = upside / downside
    if rr < 1.5:
        return {"status": "FAIL", "rr": rr, "reason": "손익비 1.5R 미만"}
    if rr > 10:
        return {"status": "FAIL", "rr": rr, "reason": "손익비 10R 초과, 비현실적 가능성"}
    if rr > 5:
        return {"status": "REVIEW", "rr": rr, "reason": "손익비 과도, 수동 검토 필요"}
    return {"status": "PASS", "rr": rr, "reason": "손익비 기준 통과"}


def calculate_position_size(entry: float, stop: float, per_trade_risk_pct: float, max_position_pct: float) -> float:
    stop_distance_pct = abs(entry - stop) / entry * 100 if entry > 0 else 0.0
    if stop_distance_pct <= 0 or per_trade_risk_pct <= 0 or max_position_pct <= 0:
        return 0.0
    raw_size = per_trade_risk_pct / stop_distance_pct * 100
    return max(0.0, min(raw_size, max_position_pct))


def _entry_plan(current: float, signal: dict, rec: dict) -> tuple[str, float, str]:
    signal_label = str(signal.get("signal") or rec.get("entry_signal") or "")
    suggested_entry = _normal_price(signal.get("suggested_entry"))
    if signal_label in {"대기", "과열"} and suggested_entry and suggested_entry < current:
        return "pullback_buy", suggested_entry, "conditional_buy"
    if signal_label == "반등":
        return "mean_reversion", current, "conditional_buy"
    if signal_label == "적정":
        return "trend_follow", current, "buy_zone"
    return "event_wait", current, "conditional_buy"


def _entry_basis(entry_type: str) -> str:
    return {
        "pullback_buy": "pullback_to_technical_support",
        "mean_reversion": "oversold_reversal_watch",
        "trend_follow": "current_price_trend_follow",
        "event_wait": "event_or_close_confirmation_required",
    }.get(entry_type, "current_price_lookup")


def _entry_condition(action: str, entry: float, currency: str) -> str:
    unit = "원" if currency == "KRW" else "USD"
    if action == "buy_zone":
        return f"{entry:,.0f}{unit} 부근 조건 충족 시 분할 접근"
    return f"{entry:,.0f}{unit} 이하 또는 종가 확인 후 접근"


def _unavailable_price_plan() -> dict[str, Any]:
    return {
        "current_price": None,
        "entry_price": None,
        "target_price_1": None,
        "target_price_2": None,
        "stop_loss": None,
        "upside_pct": None,
        "risk_reward": 0.0,
        "risk_reward_1": 0.0,
        "position_size_pct": 0.0,
        "max_portfolio_loss_pct": 0.0,
        "price_source": "unavailable",
        "price_confidence": 0.0,
        "price_basis": {},
        "price_basis_codes": [],
        "entry_type": "avoid",
        "action": "avoid",
    }


def _first_price(*values) -> float | None:
    for value in values:
        price = _normal_price(value)
        if price is not None:
            return price
    return None


def _normal_price(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round_price(value: float, currency: str) -> float:
    if currency == "KRW":
        return float(round(value, -2))
    return round(value, 2)
