"""
position_tracker.py — 포지션 추적 엔진 (OCI 최적화)
- AI 호출은 ai_client.py로 분리
- 규칙 기반 판단은 로컬에서 즉시 처리
- 캐시 메커니즘으로 AI 호출 최소화
"""
import re
from datetime import datetime, time

from .config import KST, ET, ALERT_THRESHOLD_PCT
from .database import (
    add_position, get_active_positions, close_position,
    delete_position, save_alert
)
from .data_market import get_price_safe

# Re-export for backward compatibility
__all__ = [
    "add_position", "get_active_positions", "close_position",
    "delete_position", "save_alert", "get_price_safe",
    "calc_pnl", "rule_based_judge", "format_position_summary",
    "parse_position_input", "is_kr_market_open", "is_us_market_open",
    "is_kr_opening_hour", "is_us_opening_hour",
]


# ── 손익 계산 ─────────────────────────────────────────
def calc_pnl(pos: dict, current_price: float) -> dict:
    entry = pos["entry_price"]
    qty = pos["quantity"]
    direction = pos.get("direction", "long")

    if direction == "long":
        pnl_amount = (current_price - entry) * qty
        pnl_pct = (current_price - entry) / entry * 100
    else:
        pnl_amount = (entry - current_price) * qty
        pnl_pct = (entry - current_price) / entry * 100

    cs = "₩" if pos["currency"] == "KRW" else "$"
    sign = "+" if pnl_pct >= 0 else ""
    return {
        "current_price": current_price,
        "pnl_amount": pnl_amount,
        "pnl_pct": pnl_pct,
        "pnl_str": f"{cs}{abs(pnl_amount):,.0f} ({sign}{pnl_pct:.2f}%)",
        "is_profit": pnl_pct >= 0,
        "cs": cs,
    }


# ── 규칙 기반 판단 (AI 호출 없음) ─────────────────────
def rule_based_judge(pos: dict, current_price: float, pnl: dict) -> dict:
    """
    손익률 기준 규칙 판단:
      +25% 이상 → 전량청산 고려
      +15% 이상 → 부분청산 고려
      -8% 이하  → 손절 urgent
      -5% 이하  → warning
      그 외      → 홀딩
    """
    pct = pnl["pnl_pct"]
    entry = pos["entry_price"]
    cs = pnl["cs"]

    if pct >= 25:
        return {
            "action": "전량청산",
            "confidence": 8,
            "reason": f"목표 수익률 +{pct:.1f}% 달성. 차익 실현 권장.",
            "target_price": current_price,
            "stop_loss": round(entry * 0.92, 2),
            "alert_level": "warning",
            "source": "rule",
        }
    elif pct >= 15:
        return {
            "action": "부분청산",
            "confidence": 7,
            "reason": f"+{pct:.1f}% 수익 중. 절반 차익 실현 후 나머지 홀딩 고려.",
            "target_price": round(current_price * 1.1, 2),
            "stop_loss": round(entry * 0.95, 2),
            "alert_level": "info",
            "source": "rule",
        }
    elif pct <= -8:
        return {
            "action": "전량청산",
            "confidence": 9,
            "reason": f"손절 기준 -{abs(pct):.1f}% 도달. 손실 확대 방지 권장.",
            "target_price": round(entry * 1.1, 2),
            "stop_loss": current_price,
            "alert_level": "urgent",
            "source": "rule",
        }
    elif pct <= -5:
        return {
            "action": "홀딩",
            "confidence": 6,
            "reason": f"-{abs(pct):.1f}% 손실 중. 손절선({cs}{entry * 0.92:,.0f}) 주시 필요.",
            "target_price": round(entry * 1.1, 2),
            "stop_loss": round(entry * 0.92, 2),
            "alert_level": "warning",
            "source": "rule",
        }
    else:
        return {
            "action": "홀딩",
            "confidence": 6,
            "reason": f"현재 {'+' if pct >= 0 else ''}{pct:.1f}%. 추세 유지 중.",
            "target_price": round(entry * 1.15, 2),
            "stop_loss": round(entry * 0.92, 2),
            "alert_level": "info",
            "source": "rule",
        }


# ── 포지션 요약 문자열 ────────────────────────────────
def format_position_summary(pos: dict, current_price: float, pnl: dict,
                            judge: dict | None = None) -> str:
    cs = pnl["cs"]
    direction_kor = "🟢 롱" if pos.get("direction", "long") == "long" else "🔴 숏"
    pnl_emoji = "📈" if pnl["is_profit"] else "📉"
    source_tag = ""
    if judge:
        source_tag = " 🤖" if judge.get("source") == "ai" else " 📐"

    lines = [
        "─" * 30,
        f"*{pos['name']}* `{pos['ticker']}` [{pos['market']}] {direction_kor}",
        f"진입가: {cs}{pos['entry_price']:,} | 현재가: {cs}{current_price:,}",
        f"수량: {pos['quantity']:,} | {pnl_emoji} 손익: *{pnl['pnl_str']}*",
    ]

    if judge:
        action_emoji = {"홀딩": "✋", "부분청산": "⚠️", "전량청산": "🚨"}.get(judge["action"], "•")
        level_emoji = {"info": "💬", "warning": "⚠️", "urgent": "🚨"}.get(
            judge.get("alert_level", "info"), "💬"
        )
        lines += [
            f"{level_emoji} 판단{source_tag}: *{action_emoji} {judge['action']}* (확신도: {judge['confidence']}/10)",
            f"📌 {judge['reason']}",
            f"🎯 목표가: {cs}{judge['target_price']:,.0f} | 🛑 손절가: {cs}{judge['stop_loss']:,.0f}",
        ]

    return "\n".join(lines)


# ── 텔레그램 입력 파싱 ────────────────────────────────
def parse_position_input(text: str) -> dict | None:
    """
    허용 입력 형식:
      삼성전자 55000 100        ← 롱 기본값, 티커 자동 추론
      롱 삼성전자 55000 100
      숏 AMD 340 50
      삼성전자 55000            ← 수량 생략 시 1주
    """
    text = text.strip().replace("_", " ")
    direction = "long"
    if text.startswith("숏") or text.lower().startswith("short"):
        direction = "short"
        text = re.sub(r'^(숏|short)\s*', '', text, flags=re.IGNORECASE).strip()
    elif text.startswith("롱") or text.lower().startswith("long"):
        direction = "long"
        text = re.sub(r'^(롱|long)\s*', '', text, flags=re.IGNORECASE).strip()

    parts = text.split()
    if len(parts) < 2:
        return None

    numbers = []
    name_parts = []
    ticker = None
    market = None

    for p in parts:
        p_clean = p.replace(",", "").replace("주", "").replace("원", "").replace("$", "")
        try:
            numbers.append(float(p_clean))
        except ValueError:
            if re.match(r'^\d{6}$', p):
                ticker = p
                market = "KR"
            elif p.upper() in ["KR", "US"]:
                market = p.upper()
            elif re.match(r'^[A-Z]{1,5}$', p):
                ticker = p
                market = "US"
                name_parts.append(p)
            else:
                name_parts.append(p)

    if len(numbers) < 1:
        return None

    name = " ".join(name_parts) if name_parts else "Unknown"
    entry_price = numbers[0] if len(numbers) == 1 else numbers[-2]
    quantity = 1.0 if len(numbers) == 1 else numbers[-1]

    if any('\uac00' <= c <= '\ud7a3' for c in name):
        market = "KR"
        currency = "KRW"
    else:
        guessed_ticker, guessed_market = _guess_ticker_with_market(name, None)
        if guessed_ticker and guessed_market == "KR":
            ticker = guessed_ticker
            market = guessed_market
            currency = "KRW"
        else:
            guessed_ticker, guessed_market = _guess_ticker_with_market(name, market)
            currency = "KRW" if market == "KR" else "USD"
            if guessed_ticker and not ticker:
                ticker = guessed_ticker
            if guessed_market and not market:
                market = guessed_market
        if not market:
            market = "US"

    if not ticker:
        ticker = _guess_ticker(name, market)

    return {
        "direction": direction,
        "name": name,
        "ticker": ticker or name.upper()[:6],
        "market": market or "KR",
        "entry_price": entry_price,
        "quantity": quantity,
        "currency": currency,
    }


def _guess_ticker(name: str, market: str | None) -> str | None:
    kr_map = {
        "삼성전자": "005930", "sk하이닉스": "000660", "하이닉스": "000660",
        "ls일렉트릭": "010120", "ls electric": "010120", "한미반도체": "042700",
        "hd현대일렉트릭": "267260", "현대일렉트릭": "267260",
        "현대로보틱스": "215600", "카카오": "035720", "네이버": "035420",
        "lg에너지솔루션": "373220", "삼성sdi": "006400",
        "포스코홀딩스": "005490", "현대차": "005380", "기아": "000270",
        "셀트리온": "068270", "삼성바이오로직스": "207940",
        "한화에어로": "012450", "한화에어로스페이스": "012450",
        "두산에너빌리티": "034020", "현대로템": "064350",
        "에코프로비엠": "247540", "lig넥스원": "079550",
    }
    us_map = {
        "amd": "AMD", "micron": "MU", "mu": "MU", "intel": "INTC",
        "corning": "GLW", "glw": "GLW", "nvidia": "NVDA", "nvda": "NVDA",
        "apple": "AAPL", "aapl": "AAPL", "microsoft": "MSFT", "msft": "MSFT",
        "tesla": "TSLA", "tsla": "TSLA", "meta": "META",
        "google": "GOOGL", "googl": "GOOGL", "amazon": "AMZN", "amzn": "AMZN",
        "palantir": "PLTR", "pltr": "PLTR", "arm": "ARM",
        "broadcom": "AVGO", "avgo": "AVGO", "qualcomm": "QCOM",
        "ge vernova": "GEV", "gev": "GEV", "nextera": "NEE",
        "lockheed": "LMT", "lam research": "LRCX",
    }
    n = name.lower().strip()
    if market in ("KR", None):
        for k, v in kr_map.items():
            if k in n or n in k:
                return v
    if market in ("US", None):
        for k, v in us_map.items():
            if k in n or n in k:
                return v
    return None


def _guess_ticker_with_market(name: str, market: str | None) -> tuple[str | None, str | None]:
    ticker = _guess_ticker(name, market)
    if not ticker:
        return None, None
    if market:
        return ticker, market
    n = name.lower().strip()
    kr_names = {
        "삼성전자", "sk하이닉스", "하이닉스", "ls일렉트릭", "ls electric",
        "한미반도체", "hd현대일렉트릭", "현대일렉트릭", "현대로보틱스",
        "카카오", "네이버", "lg에너지솔루션", "삼성sdi", "포스코홀딩스",
        "현대차", "기아", "셀트리온", "삼성바이오로직스", "한화에어로",
        "한화에어로스페이스", "두산에너빌리티", "현대로템", "에코프로비엠",
        "lig넥스원",
    }
    if any(k in n or n in k for k in kr_names):
        return ticker, "KR"
    return ticker, "US"


# ── 장 시간 체크 ──────────────────────────────────────
def is_kr_market_open() -> bool:
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return False
    return time(9, 0) <= now.time() <= time(15, 30)


def is_us_market_open() -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    return time(9, 30) <= now.time() <= time(16, 0)


def is_kr_opening_hour() -> bool:
    now = datetime.now(KST)
    if now.weekday() >= 5:
        return False
    return time(9, 0) <= now.time() <= time(10, 0)


def is_us_opening_hour() -> bool:
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    return time(9, 30) <= now.time() <= time(10, 30)
