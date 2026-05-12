"""
entry_filter.py — 종목 진입 타이밍 필터

모멘텀이 강한 종목이라도 "지금 사야 하는가"를 판단하는 기술적 필터.
과매수 구간 추격매수를 방지하고, 적정 진입 시점을 식별한다.

진입 적정도:
- 🟢 GOOD: 즉시 진입 적합
- 🟡 WAIT: 조정 대기 후 진입
- 🔴 AVOID: 과열 — 추격 금지
"""
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


class EntrySignal(Enum):
    GOOD = "적정"       # 즉시 진입 가능
    WAIT = "대기"       # 조정 후 진입 권고
    AVOID = "과열"      # 추격 금지
    OVERSOLD = "반등"   # 과매도 반등 기대


@dataclass
class EntryFilterResult:
    signal: EntrySignal
    score: float  # 0~100 (높을수록 진입 적합)
    rsi: float
    bb_position: float
    volume_signal: str
    adx: Optional[float]
    reasons: list[str]
    suggested_entry: Optional[float]  # 제안 진입가 (조정 시)
    risk_reward: float  # 리스크/리워드 비율


def calc_entry_filter(
    prices: list[float],
    highs: list[float] = None,
    lows: list[float] = None,
    volumes: list[float] = None,
    target_price: float = None,
    stop_loss: float = None,
) -> EntryFilterResult:
    """
    종목 진입 적정도 판단
    
    Args:
        prices: 최근 60일+ 종가 리스트
        highs: 고가 리스트 (선택)
        lows: 저가 리스트 (선택)
        volumes: 거래량 리스트 (선택)
        target_price: 목표가 (리스크/리워드 계산용)
        stop_loss: 손절가
    
    Returns:
        EntryFilterResult
    """
    current = prices[-1]
    reasons = []
    score = 50.0  # 기본 중립
    
    # ── 1. RSI 계산 및 판단 ──────────────────────────────
    rsi = _calc_rsi(prices, 14)
    
    if rsi >= 80:
        score -= 30
        reasons.append(f"RSI {rsi:.0f} — 극단 과매수 (추격 금지)")
    elif rsi >= 70:
        score -= 15
        reasons.append(f"RSI {rsi:.0f} — 과매수 구간 (조정 대기)")
    elif rsi <= 30:
        score += 20
        reasons.append(f"RSI {rsi:.0f} — 과매도 (반등 기대)")
    elif rsi <= 40:
        score += 10
        reasons.append(f"RSI {rsi:.0f} — 저평가 구간")
    else:
        score += 5
        reasons.append(f"RSI {rsi:.0f} — 중립")
    
    # ── 2. 볼린저 밴드 위치 ──────────────────────────────
    bb_pos = _calc_bb_position(prices, 20)
    
    if bb_pos > 110:
        score -= 25
        reasons.append(f"BB {bb_pos:.0f}% — 밴드 완전 이탈 (과열 극단)")
    elif bb_pos > 95:
        score -= 15
        reasons.append(f"BB {bb_pos:.0f}% — 상단 돌파 (단기 조정 임박)")
    elif bb_pos > 80:
        score -= 5
        reasons.append(f"BB {bb_pos:.0f}% — 상단 근접")
    elif bb_pos < 10:
        score += 15
        reasons.append(f"BB {bb_pos:.0f}% — 하단 근접 (반등 기대)")
    elif bb_pos < 30:
        score += 10
        reasons.append(f"BB {bb_pos:.0f}% — 하단 영역")
    elif 40 <= bb_pos <= 60:
        score += 10
        reasons.append(f"BB {bb_pos:.0f}% — 중심선 부근 (적정 진입)")
    
    # ── 3. 단기 급등 여부 (5일 수익률) ───────────────────
    if len(prices) >= 5:
        ret_5d = (current - prices[-5]) / prices[-5] * 100
        if ret_5d > 20:
            score -= 20
            reasons.append(f"5일 +{ret_5d:.1f}% — 급등 직후 (추격 위험)")
        elif ret_5d > 10:
            score -= 10
            reasons.append(f"5일 +{ret_5d:.1f}% — 단기 과열")
        elif ret_5d < -10:
            score += 10
            reasons.append(f"5일 {ret_5d:.1f}% — 급락 후 반등 기대")
        elif -5 <= ret_5d <= 5:
            score += 5
            reasons.append(f"5일 {ret_5d:+.1f}% — 안정적 흐름")
    
    # ── 4. 거래량 분석 ───────────────────────────────────
    volume_signal = "중립"
    if volumes and len(volumes) >= 20:
        vol_5d = np.mean(volumes[-5:])
        vol_20d = np.mean(volumes[-20:])
        vol_ratio = vol_5d / vol_20d if vol_20d > 0 else 1.0
        
        if vol_ratio > 2.0:
            # 거래량 폭증 + 상승 = 클라이맥스 가능성
            if len(prices) >= 2 and prices[-1] > prices[-2]:
                score -= 10
                volume_signal = "클라이맥스 경고"
                reasons.append(f"거래량 {vol_ratio:.1f}x — 매수 클라이맥스 의심")
            else:
                volume_signal = "패닉셀"
                score += 5
                reasons.append(f"거래량 {vol_ratio:.1f}x — 투매 후 반등 가능")
        elif vol_ratio > 1.3:
            volume_signal = "증가"
            # 상승 + 거래량 증가 = 건강한 상승
            if len(prices) >= 2 and prices[-1] > prices[-2]:
                score += 5
                reasons.append(f"거래량 {vol_ratio:.1f}x — 건강한 매수세")
        elif vol_ratio < 0.7:
            volume_signal = "감소"
            score -= 5
            reasons.append(f"거래량 {vol_ratio:.1f}x — 관심 저하")
    
    # ── 5. 추세 강도 (ADX) ───────────────────────────────
    adx = None
    if highs and lows and len(highs) >= 28:
        adx = _calc_adx(highs, lows, prices)
        if adx is not None:
            if adx > 40:
                reasons.append(f"ADX {adx:.0f} — 매우 강한 추세 (추세 추종)")
                score += 5
            elif adx > 25:
                reasons.append(f"ADX {adx:.0f} — 추세 존재")
            else:
                reasons.append(f"ADX {adx:.0f} — 추세 약함 (횡보)")
    
    # ── 6. 리스크/리워드 비율 ────────────────────────────
    risk_reward = 0.0
    if target_price and stop_loss and current > 0:
        reward = target_price - current
        risk = current - stop_loss
        if risk > 0:
            risk_reward = reward / risk
            if risk_reward >= 3:
                score += 10
                reasons.append(f"R/R {risk_reward:.1f} — 우수")
            elif risk_reward >= 2:
                score += 5
                reasons.append(f"R/R {risk_reward:.1f} — 양호")
            elif risk_reward < 1:
                score -= 10
                reasons.append(f"R/R {risk_reward:.1f} — 불리")
    
    # ── 최종 신호 결정 ───────────────────────────────────
    score = max(0, min(100, score))
    
    if score >= 60:
        signal = EntrySignal.GOOD
    elif score >= 40:
        signal = EntrySignal.WAIT
    elif rsi <= 30:
        signal = EntrySignal.OVERSOLD
    else:
        signal = EntrySignal.AVOID
    
    # ── 제안 진입가 (조정 시) ────────────────────────────
    suggested_entry = None
    if signal in (EntrySignal.WAIT, EntrySignal.AVOID):
        # 20일 이동평균 또는 볼린저 중심선 근처를 제안
        if len(prices) >= 20:
            sma20 = np.mean(prices[-20:])
            # 현재가와 SMA20 사이의 중간점
            suggested_entry = round((current + sma20) / 2, -2)  # 100원 단위 반올림
    
    return EntryFilterResult(
        signal=signal,
        score=round(score, 1),
        rsi=round(rsi, 1),
        bb_position=round(bb_pos, 1),
        volume_signal=volume_signal,
        adx=round(adx, 1) if adx else None,
        reasons=reasons,
        suggested_entry=suggested_entry,
        risk_reward=round(risk_reward, 2),
    )


def _calc_rsi(prices: list[float], period: int = 14) -> float:
    """RSI 계산"""
    if len(prices) < period + 1:
        return 50.0
    deltas = np.diff(prices[-(period + 1):])
    gains = np.where(deltas > 0, deltas, 0)
    losses = np.where(deltas < 0, -deltas, 0)
    avg_gain = np.mean(gains)
    avg_loss = np.mean(losses)
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _calc_bb_position(prices: list[float], period: int = 20) -> float:
    """볼린저 밴드 내 위치"""
    if len(prices) < period:
        return 50.0
    recent = prices[-period:]
    sma = np.mean(recent)
    std = np.std(recent)
    if std == 0:
        return 50.0
    upper = sma + 2 * std
    lower = sma - 2 * std
    return (prices[-1] - lower) / (upper - lower) * 100


def _calc_adx(highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
    """ADX 계산"""
    if len(closes) < period * 2:
        return None
    highs = np.array(highs[-(period * 2):], dtype=float)
    lows = np.array(lows[-(period * 2):], dtype=float)
    closes = np.array(closes[-(period * 2):], dtype=float)
    
    tr = np.maximum(highs[1:] - lows[1:],
                    np.maximum(abs(highs[1:] - closes[:-1]),
                               abs(lows[1:] - closes[:-1])))
    plus_dm = np.where((highs[1:] - highs[:-1]) > (lows[:-1] - lows[1:]),
                       np.maximum(highs[1:] - highs[:-1], 0), 0)
    minus_dm = np.where((lows[:-1] - lows[1:]) > (highs[1:] - highs[:-1]),
                        np.maximum(lows[:-1] - lows[1:], 0), 0)
    
    atr = np.mean(tr[-period:])
    if atr == 0:
        return 0.0
    plus_di = np.mean(plus_dm[-period:]) / atr * 100
    minus_di = np.mean(minus_dm[-period:]) / atr * 100
    
    if (plus_di + minus_di) == 0:
        return 0.0
    dx = abs(plus_di - minus_di) / (plus_di + minus_di) * 100
    return dx


def calc_momentum_quality(
    ret_5d: float,
    ret_20d: float,
    rsi: float,
    bb_position: float,
    volume_ratio: float = 1.0,
) -> float:
    """
    모멘텀 품질 점수 (Quality Momentum)
    
    단순 수익률이 아닌, 지속 가능하고 건강한 모멘텀인지 판단.
    0~100 점수. 높을수록 "건강한 모멘텀".
    
    기존 문제: ret_20d가 높으면 무조건 높은 점수 → 과매수 추격
    개선: 과매수 상태에서는 감점, 적정 구간에서 가점
    """
    score = 50.0
    
    # 1. 20일 모멘텀 (방향성) — 가중치 30%
    if ret_20d > 30:
        mom_20 = 25  # 매우 강하지만 과열 가능성으로 캡
    elif ret_20d > 10:
        mom_20 = 30  # 최적 구간
    elif ret_20d > 0:
        mom_20 = ret_20d * 2  # 완만한 상승
    else:
        mom_20 = max(-30, ret_20d)  # 하락
    score += mom_20 * 0.3
    
    # 2. 5일 모멘텀 (단기 가속) — 가중치 20%
    if ret_5d > 15:
        mom_5 = 10  # 급등 = 캡 (과열 위험)
    elif ret_5d > 5:
        mom_5 = 20  # 적정 가속
    elif ret_5d > 0:
        mom_5 = ret_5d * 3
    else:
        mom_5 = max(-20, ret_5d * 2)
    score += mom_5 * 0.2
    
    # 3. RSI 페널티 — 가중치 25%
    if rsi > 80:
        rsi_adj = -25  # 극단 과매수 → 큰 감점
    elif rsi > 70:
        rsi_adj = -10  # 과매수
    elif rsi < 30:
        rsi_adj = -15  # 과매도 (모멘텀 부재)
    elif 40 <= rsi <= 60:
        rsi_adj = 10  # 최적 구간
    else:
        rsi_adj = 0
    score += rsi_adj * 0.25
    
    # 4. 볼린저 밴드 페널티 — 가중치 15%
    if bb_position > 100:
        bb_adj = -15  # 밴드 이탈
    elif bb_position > 85:
        bb_adj = -8
    elif bb_position < 20:
        bb_adj = -10  # 하단 = 모멘텀 부재
    elif 40 <= bb_position <= 70:
        bb_adj = 10  # 적정
    else:
        bb_adj = 0
    score += bb_adj * 0.15
    
    # 5. 거래량 확인 — 가중치 10%
    if volume_ratio > 2.0:
        vol_adj = -5  # 과열
    elif volume_ratio > 1.2:
        vol_adj = 10  # 건강한 관심
    elif volume_ratio < 0.7:
        vol_adj = -5  # 관심 저하
    else:
        vol_adj = 0
    score += vol_adj * 0.1
    
    return max(0, min(100, round(score, 1)))
