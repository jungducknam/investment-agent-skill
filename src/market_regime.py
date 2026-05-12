"""
market_regime.py — 시장 레짐(Regime) 판단 모듈

시장을 4가지 상태로 분류하고, 각 상태에 맞는 전략 파라미터를 제공한다.
- BULL (강세): 공격적 매수, 현금 10%
- CORRECTION (조정): 분할 매수 기회, 현금 20%
- SIDEWAYS (횡보): 스윙 중심, 현금 15%
- BEAR (약세): 방어적, 현금 30%+

판단 기준:
1. 지수 vs SMA50 위치
2. 단기(5일) 수익률
3. VIX 수준
4. 거래량 추세
5. 시장 폭(Breadth) — 상승 종목 비율
"""
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class MarketRegime(Enum):
    BULL = "강세"
    CORRECTION = "조정"
    SIDEWAYS = "횡보"
    BEAR = "약세"


@dataclass
class RegimeResult:
    regime: MarketRegime
    confidence: float  # 0~1
    score: float  # -100 ~ +100
    cash_pct: int  # 권장 현금 비중
    strategy: str  # 전략 요약
    details: dict  # 세부 판단 근거


def calc_sma(prices: list[float], period: int) -> Optional[float]:
    """단순 이동평균 계산"""
    if len(prices) < period:
        return None
    return np.mean(prices[-period:])


def calc_rsi(prices: list[float], period: int = 14) -> Optional[float]:
    """RSI 계산"""
    if len(prices) < period + 1:
        return None
    deltas = np.diff(prices[-(period + 1):])
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def calc_bollinger_position(prices: list[float], period: int = 20) -> Optional[float]:
    """볼린저 밴드 내 위치 (0~100, 100 초과 = 상단 돌파)"""
    if len(prices) < period:
        return None
    recent = prices[-period:]
    sma = np.mean(recent)
    std = np.std(recent)
    if std == 0:
        return 50.0
    upper = sma + 2 * std
    lower = sma - 2 * std
    current = prices[-1]
    return (current - lower) / (upper - lower) * 100


def calc_adx(highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
    """ADX (Average Directional Index) 계산 — 추세 강도"""
    if len(closes) < period * 2:
        return None
    
    highs = np.array(highs[-(period * 2):], dtype=float)
    lows = np.array(lows[-(period * 2):], dtype=float)
    closes = np.array(closes[-(period * 2):], dtype=float)
    
    # True Range
    tr = np.maximum(highs[1:] - lows[1:],
                    np.maximum(abs(highs[1:] - closes[:-1]),
                               abs(lows[1:] - closes[:-1])))
    
    # +DM, -DM
    plus_dm = np.where((highs[1:] - highs[:-1]) > (lows[:-1] - lows[1:]),
                       np.maximum(highs[1:] - highs[:-1], 0), 0)
    minus_dm = np.where((lows[:-1] - lows[1:]) > (highs[1:] - highs[:-1]),
                        np.maximum(lows[:-1] - lows[1:], 0), 0)
    
    # Smoothed averages
    atr = np.mean(tr[-period:])
    plus_di = np.mean(plus_dm[-period:]) / atr * 100 if atr > 0 else 0
    minus_di = np.mean(minus_dm[-period:]) / atr * 100 if atr > 0 else 0
    
    dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100 if (plus_di + minus_di) > 0 else 0
    return dx


def determine_market_regime(
    index_prices: list[float],
    vix: float = None,
    volume_ratio: float = 1.0,
    breadth_pct: float = 50.0,
    index_highs: list = None,
    index_lows: list = None,
) -> RegimeResult:
    """
    시장 레짐 판단 메인 함수
    
    Args:
        index_prices: 최근 60일+ 종가 리스트 (최신이 마지막)
        vix: VIX 지수 (없으면 None)
        volume_ratio: 최근 5일 거래량 / 20일 평균 거래량
        breadth_pct: 상승 종목 비율 (0~100)
        index_highs: 고가 리스트 (ADX 계산용)
        index_lows: 저가 리스트 (ADX 계산용)
    
    Returns:
        RegimeResult
    """
    score = 0.0  # -100(극단 약세) ~ +100(극단 강세)
    details = {}
    
    # ── 1. 지수 vs SMA50 위치 (가중치 30%) ──────────────
    sma50 = calc_sma(index_prices, 50)
    sma20 = calc_sma(index_prices, 20)
    current = index_prices[-1]
    
    if sma50 is not None:
        pct_above_sma50 = (current - sma50) / sma50 * 100
        # +5% 이상: +30, -5% 이하: -30
        sma_score = max(-30, min(30, pct_above_sma50 * 6))
        score += sma_score
        details["sma50_pct"] = round(pct_above_sma50, 2)
        details["sma50_score"] = round(sma_score, 1)
    
    # ── 2. 단기 모멘텀 (가중치 25%) ─────────────────────
    if len(index_prices) >= 5:
        ret_5d = (current - index_prices[-5]) / index_prices[-5] * 100
        # +3% 이상: +25, -3% 이하: -25
        mom_score = max(-25, min(25, ret_5d * 8.3))
        score += mom_score
        details["ret_5d"] = round(ret_5d, 2)
        details["momentum_score"] = round(mom_score, 1)
    
    # ── 3. VIX (가중치 20%) ──────────────────────────────
    if vix is not None:
        if vix < 15:
            vix_score = 20  # 낮은 공포 = 강세
        elif vix < 20:
            vix_score = 10
        elif vix < 25:
            vix_score = -5
        elif vix < 30:
            vix_score = -15
        else:
            vix_score = -20  # 높은 공포 = 약세
        score += vix_score
        details["vix"] = vix
        details["vix_score"] = vix_score
    
    # ── 4. 거래량 추세 (가중치 10%) ──────────────────────
    if volume_ratio > 1.5:
        # 거래량 급증 + 하락 = 패닉셀, + 상승 = 강한 매수
        if len(index_prices) >= 2 and index_prices[-1] < index_prices[-2]:
            vol_score = -10  # 패닉셀
        else:
            vol_score = 10  # 강한 매수
    elif volume_ratio < 0.7:
        vol_score = -5  # 거래량 감소 = 관심 저하
    else:
        vol_score = 0
    score += vol_score
    details["volume_ratio"] = round(volume_ratio, 2)
    details["volume_score"] = vol_score
    
    # ── 5. 시장 폭(Breadth) (가중치 15%) ────────────────
    breadth_score = (breadth_pct - 50) * 0.3  # 50% 기준, 최대 ±15
    breadth_score = max(-15, min(15, breadth_score))
    score += breadth_score
    details["breadth_pct"] = breadth_pct
    details["breadth_score"] = round(breadth_score, 1)
    
    # ── 6. 추세 강도 ADX (보조) ──────────────────────────
    adx = None
    if index_highs and index_lows and len(index_highs) >= 28:
        adx = calc_adx(index_highs, index_lows, index_prices)
        details["adx"] = round(adx, 1) if adx else None
    
    # ── 레짐 결정 ────────────────────────────────────────
    if score >= 30:
        regime = MarketRegime.BULL
        cash_pct = 10
        strategy = "공격적 매수. 모멘텀 추종 + 신규 진입 적극 고려. 손절 타이트하게."
        confidence = min(1.0, score / 80)
    elif score >= 0:
        if adx and adx < 20:
            regime = MarketRegime.SIDEWAYS
            cash_pct = 15
            strategy = "방향성 부재. 스윙 트레이딩 중심. 박스권 상단 매도, 하단 매수."
            confidence = 0.5
        else:
            regime = MarketRegime.BULL
            cash_pct = 12
            strategy = "완만한 강세. 기존 포지션 유지, 신규 진입은 조정 시."
            confidence = min(1.0, score / 50)
    elif score >= -30:
        regime = MarketRegime.CORRECTION
        cash_pct = 20
        strategy = "단기 조정 구간. 분할 매수 기회 탐색. 급등 종목 추격 금지."
        confidence = min(1.0, abs(score) / 50)
    else:
        regime = MarketRegime.BEAR
        cash_pct = 30
        strategy = "약세장. 현금 비중 확대, 방어주/인버스 고려. 신규 매수 최소화."
        confidence = min(1.0, abs(score) / 80)
    
    details["total_score"] = round(score, 1)
    
    return RegimeResult(
        regime=regime,
        confidence=round(confidence, 2),
        score=round(score, 1),
        cash_pct=cash_pct,
        strategy=strategy,
        details=details,
    )


def determine_dual_regime(
    kr_prices: list[float],
    us_prices: list[float],
    vix: float = None,
    kr_breadth: float = 50.0,
    us_breadth: float = 50.0,
    kr_volume_ratio: float = 1.0,
    us_volume_ratio: float = 1.0,
) -> dict:
    """
    한국/미국 시장 각각의 레짐을 판단하고 통합 전략 제시
    """
    kr_regime = determine_market_regime(
        kr_prices, vix=None, volume_ratio=kr_volume_ratio, breadth_pct=kr_breadth
    )
    us_regime = determine_market_regime(
        us_prices, vix=vix, volume_ratio=us_volume_ratio, breadth_pct=us_breadth
    )
    
    # 통합 현금 비중 (두 시장의 평균)
    combined_cash = (kr_regime.cash_pct + us_regime.cash_pct) // 2
    
    # 시장 간 괴리 감지
    divergence = abs(kr_regime.score - us_regime.score)
    if divergence > 40:
        divergence_note = f"⚠️ 한미 시장 괴리 심화 ({divergence:.0f}pt). 강한 시장에 비중 확대 권고."
    else:
        divergence_note = ""
    
    return {
        "kr": kr_regime,
        "us": us_regime,
        "combined_cash_pct": combined_cash,
        "divergence": round(divergence, 1),
        "divergence_note": divergence_note,
    }
