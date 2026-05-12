"""
momentum_inflection.py — 모멘텀 변곡 판단 엔진

모멘텀의 "변곡점"을 감지하여 이익을 극대화한다.
변곡점 = 모멘텀이 가속→감속, 상승→하락(또는 반대)으로 전환되는 시점.

핵심 원리:
- 가격 자체가 아닌 "가격 변화의 변화"(2차 미분)를 추적
- 모멘텀이 최고점에서 꺾이기 시작하면 → 이익 실현 시그널
- 모멘텀이 최저점에서 반등하기 시작하면 → 매수 시그널
- 섹터 단위 로테이션 변곡도 감지

활용:
1. 리포트에서 "지금 이 종목/섹터의 모멘텀이 어디쯤인지" 판단
2. 포지션 모니터링에서 "이익 실현 타이밍" 알림
"""
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional

import numpy as np

from .config import KST

logger = logging.getLogger(__name__)


class MomentumPhase(Enum):
    """모멘텀 단계"""
    ACCELERATING_UP = "가속상승"      # 상승 모멘텀 강화 중 → 보유
    DECELERATING_UP = "감속상승"      # 상승 모멘텀 약화 중 → 이익실현 준비
    PEAK = "정점"                     # 모멘텀 정점 → 이익실현
    ACCELERATING_DOWN = "가속하락"    # 하락 모멘텀 강화 중 → 관망
    DECELERATING_DOWN = "감속하락"    # 하락 모멘텀 약화 중 → 매수 준비
    TROUGH = "저점"                   # 모멘텀 저점 → 매수
    NEUTRAL = "중립"                  # 방향성 없음


@dataclass
class InflectionResult:
    """변곡 분석 결과"""
    phase: MomentumPhase
    confidence: float           # 0~1 확신도
    momentum_value: float       # 현재 모멘텀 값
    momentum_slope: float       # 모멘텀의 기울기 (2차 미분)
    days_since_inflection: int  # 마지막 변곡점 이후 경과일
    signal: str                 # "보유"/"이익실현"/"매수"/"관망"/"대기"
    description: str            # 설명


def analyze_momentum_inflection(
    prices: list[float],
    period: int = 10,
    smoothing: int = 3,
) -> InflectionResult:
    """
    가격 시계열에서 모멘텀 변곡점을 분석한다.
    
    Args:
        prices: 가격 리스트 (오래된 순 → 최신 순)
        period: 모멘텀 계산 기간 (기본 10일)
        smoothing: 노이즈 제거용 이동평균 기간
    
    Returns:
        InflectionResult
    """
    if len(prices) < period + smoothing + 5:
        return InflectionResult(
            phase=MomentumPhase.NEUTRAL,
            confidence=0.0,
            momentum_value=0.0,
            momentum_slope=0.0,
            days_since_inflection=0,
            signal="데이터부족",
            description="분석에 필요한 데이터 부족",
        )
    
    prices = np.array(prices, dtype=float)
    
    # 1. 모멘텀 계산 (Rate of Change)
    roc = np.zeros(len(prices))
    for i in range(period, len(prices)):
        if prices[i - period] > 0:
            roc[i] = (prices[i] - prices[i - period]) / prices[i - period] * 100
    
    roc = roc[period:]  # 유효 구간만
    
    # 2. 스무딩 (노이즈 제거)
    if len(roc) >= smoothing:
        kernel = np.ones(smoothing) / smoothing
        roc_smooth = np.convolve(roc, kernel, mode='valid')
    else:
        roc_smooth = roc
    
    if len(roc_smooth) < 5:
        return InflectionResult(
            phase=MomentumPhase.NEUTRAL,
            confidence=0.0,
            momentum_value=float(roc[-1]) if len(roc) > 0 else 0.0,
            momentum_slope=0.0,
            days_since_inflection=0,
            signal="데이터부족",
            description="스무딩 후 데이터 부족",
        )
    
    # 3. 모멘텀의 기울기 (1차 미분 of 모멘텀 = 가격의 2차 미분)
    momentum_slope = roc_smooth[-1] - roc_smooth[-2]
    momentum_slope_prev = roc_smooth[-2] - roc_smooth[-3]
    
    # 4. 현재 모멘텀 값과 기울기
    current_momentum = float(roc_smooth[-1])
    
    # 5. 변곡점 감지 (기울기 부호 전환)
    days_since = 0
    for i in range(len(roc_smooth) - 2, 0, -1):
        slope_i = roc_smooth[i] - roc_smooth[i - 1]
        slope_next = roc_smooth[i + 1] - roc_smooth[i] if i + 1 < len(roc_smooth) else slope_i
        if slope_i * slope_next < 0:  # 부호 전환 = 변곡점
            days_since = len(roc_smooth) - 1 - i
            break
    
    # 6. 단계 판단
    phase, signal, confidence = _determine_phase(
        current_momentum, momentum_slope, momentum_slope_prev, roc_smooth
    )
    
    # 7. 설명 생성
    description = _build_inflection_description(phase, current_momentum, momentum_slope, days_since)
    
    return InflectionResult(
        phase=phase,
        confidence=confidence,
        momentum_value=round(current_momentum, 2),
        momentum_slope=round(momentum_slope, 3),
        days_since_inflection=days_since,
        signal=signal,
        description=description,
    )


def _determine_phase(momentum: float, slope: float, prev_slope: float, roc_series) -> tuple:
    """모멘텀 단계, 시그널, 확신도 결정"""
    
    # 모멘텀 절대값이 작으면 중립
    if abs(momentum) < 1.0 and abs(slope) < 0.3:
        return MomentumPhase.NEUTRAL, "대기", 0.3
    
    # 상승 모멘텀 영역
    if momentum > 0:
        if slope > 0.3:
            # 모멘텀 상승 중 + 기울기 양수 = 가속 상승
            confidence = min(slope / 2.0, 1.0)
            return MomentumPhase.ACCELERATING_UP, "보유", confidence
        elif slope < -0.3:
            # 모멘텀 양수이나 기울기 음수 = 감속 상승 (정점 근접)
            if prev_slope > 0 and slope < 0:
                # 기울기가 양→음 전환 = 정점!
                return MomentumPhase.PEAK, "이익실현", 0.8
            confidence = min(abs(slope) / 2.0, 0.9)
            return MomentumPhase.DECELERATING_UP, "이익실현준비", confidence
        else:
            # 기울기 거의 0 = 정점 부근
            # 최근 5개 중 현재가 최대인지 확인
            recent = roc_series[-5:]
            if momentum >= max(recent) * 0.95:
                return MomentumPhase.PEAK, "이익실현", 0.6
            return MomentumPhase.DECELERATING_UP, "이익실현준비", 0.5
    
    # 하락 모멘텀 영역
    else:
        if slope < -0.3:
            # 모멘텀 하락 중 + 기울기 음수 = 가속 하락
            confidence = min(abs(slope) / 2.0, 1.0)
            return MomentumPhase.ACCELERATING_DOWN, "관망", confidence
        elif slope > 0.3:
            # 모멘텀 음수이나 기울기 양수 = 감속 하락 (저점 근접)
            if prev_slope < 0 and slope > 0:
                # 기울기가 음→양 전환 = 저점!
                return MomentumPhase.TROUGH, "매수", 0.8
            confidence = min(slope / 2.0, 0.9)
            return MomentumPhase.DECELERATING_DOWN, "매수준비", confidence
        else:
            # 기울기 거의 0 = 저점 부근
            recent = roc_series[-5:]
            if momentum <= min(recent) * 0.95:
                return MomentumPhase.TROUGH, "매수", 0.6
            return MomentumPhase.DECELERATING_DOWN, "매수준비", 0.5


def _build_inflection_description(phase: MomentumPhase, momentum: float, slope: float, days_since: int) -> str:
    """변곡 분석 설명 생성"""
    phase_desc = {
        MomentumPhase.ACCELERATING_UP: "상승 모멘텀이 강화되고 있습니다. 추세를 따라 보유하세요.",
        MomentumPhase.DECELERATING_UP: "상승 모멘텀이 약화되기 시작했습니다. 이익실현을 준비하세요.",
        MomentumPhase.PEAK: "모멘텀이 정점에 도달했습니다. 이익실현 타이밍입니다.",
        MomentumPhase.ACCELERATING_DOWN: "하락 모멘텀이 강화되고 있습니다. 신규 매수를 피하세요.",
        MomentumPhase.DECELERATING_DOWN: "하락 모멘텀이 약화되고 있습니다. 매수 기회를 탐색하세요.",
        MomentumPhase.TROUGH: "모멘텀이 저점에서 반등하고 있습니다. 매수 타이밍입니다.",
        MomentumPhase.NEUTRAL: "뚜렷한 방향성이 없습니다. 관망하세요.",
    }
    
    base = phase_desc.get(phase, "")
    detail = f" (모멘텀: {momentum:+.1f}%, 기울기: {slope:+.2f}, 변곡 후 {days_since}일)"
    return base + detail


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 섹터 모멘텀 변곡 분석
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def analyze_sector_inflections(sector_history: list[dict]) -> dict:
    """
    섹터별 모멘텀 변곡 분석.
    sector_history: 시간순 섹터 모멘텀 딕셔너리 리스트
    
    Returns: {sector_name: InflectionResult}
    """
    if len(sector_history) < 5:
        return {}
    
    # 섹터별 모멘텀 시계열 추출
    all_sectors = set()
    for sh in sector_history:
        all_sectors.update(sh.keys())
    
    results = {}
    for sector in all_sectors:
        momentum_series = []
        for sh in sector_history:
            m = sh.get(sector, {}).get("ret_5d", None)
            if m is not None:
                momentum_series.append(m)
        
        if len(momentum_series) < 5:
            continue
        
        # 모멘텀 자체를 "가격"으로 간주하여 변곡 분석
        # (모멘텀의 모멘텀 = 가속도)
        result = _analyze_sector_momentum_phase(momentum_series)
        results[sector] = result
    
    return results


def _analyze_sector_momentum_phase(momentum_series: list[float]) -> dict:
    """섹터 모멘텀 시계열의 단계 판단"""
    series = np.array(momentum_series)
    
    # 최근 값
    current = series[-1]
    
    # 기울기 (최근 3개 기준)
    if len(series) >= 3:
        slope = (series[-1] - series[-3]) / 2
    else:
        slope = 0
    
    # 단계 판단
    if current > 5 and slope > 0:
        phase = "가속상승"
        signal = "강세지속"
    elif current > 5 and slope <= 0:
        phase = "감속상승"
        signal = "정점근접"
    elif current > 0 and slope > 0:
        phase = "회복"
        signal = "매수고려"
    elif current <= 0 and slope < 0:
        phase = "가속하락"
        signal = "회피"
    elif current <= 0 and slope >= 0:
        phase = "바닥형성"
        signal = "관찰"
    else:
        phase = "중립"
        signal = "대기"
    
    return {
        "current_momentum": round(current, 2),
        "slope": round(slope, 3),
        "phase": phase,
        "signal": signal,
    }


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 포지션 모니터링용 변곡 체크
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def check_position_inflection(ticker: str, prices: list[float]) -> Optional[dict]:
    """
    보유 포지션의 모멘텀 변곡 체크.
    이익실현 또는 손절 타이밍을 판단.
    
    Returns:
        None (변곡 없음) 또는 {"signal": "이익실현"/"손절", "description": "..."}
    """
    result = analyze_momentum_inflection(prices, period=10, smoothing=2)
    
    # 이익실현 시그널
    if result.phase in (MomentumPhase.PEAK, MomentumPhase.DECELERATING_UP):
        if result.confidence >= 0.6:
            return {
                "ticker": ticker,
                "signal": "이익실현",
                "phase": result.phase.value,
                "confidence": result.confidence,
                "description": result.description,
            }
    
    # 손절 시그널 (가속 하락)
    if result.phase == MomentumPhase.ACCELERATING_DOWN:
        if result.confidence >= 0.7:
            return {
                "ticker": ticker,
                "signal": "손절검토",
                "phase": result.phase.value,
                "confidence": result.confidence,
                "description": result.description,
            }
    
    return None


def build_inflection_context_for_report(tickers: list[str]) -> str:
    """
    리포트 생성 시 종목별 모멘텀 변곡 상태를 텍스트로 제공.
    """
    import yfinance as yf
    
    lines = []
    lines.append("━━━ 종목별 모멘텀 변곡 분석 ━━━")
    
    for ticker in tickers:
        try:
            t = yf.Ticker(ticker)
            hist = t.history(period="3mo")
            if len(hist) < 20:
                continue
            
            prices = hist["Close"].tolist()
            result = analyze_momentum_inflection(prices, period=10, smoothing=3)
            
            phase_emoji = {
                MomentumPhase.ACCELERATING_UP: "🚀",
                MomentumPhase.DECELERATING_UP: "⚠️",
                MomentumPhase.PEAK: "🔴",
                MomentumPhase.ACCELERATING_DOWN: "📉",
                MomentumPhase.DECELERATING_DOWN: "🔵",
                MomentumPhase.TROUGH: "🟢",
                MomentumPhase.NEUTRAL: "⚪",
            }
            
            emoji = phase_emoji.get(result.phase, "⚪")
            lines.append(
                f"{emoji} [{ticker}] {result.phase.value} | "
                f"모멘텀 {result.momentum_value:+.1f}% | "
                f"기울기 {result.momentum_slope:+.2f} | "
                f"시그널: {result.signal} ({result.confidence:.0%})"
            )
        except Exception as e:
            logger.debug(f"변곡 분석 실패 [{ticker}]: {e}")
    
    return "\n".join(lines) if len(lines) > 1 else "변곡 분석 데이터 없음"
