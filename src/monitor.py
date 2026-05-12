"""
monitor.py — 포지션 모니터링 (CPU 최적화)
기존 문제점: busy-wait 루프 → CPU 80%
해결: asyncio 기반 스케줄링 + 조건부 실행
"""
import asyncio
import logging
from datetime import datetime, timedelta

from .config import KST, ALERT_THRESHOLD_PCT, ALERT_COOLDOWN_MIN
from .database import get_active_positions, save_alert
from .data_market import get_price_safe
from .position_tracker import (
    calc_pnl, rule_based_judge,
    is_kr_market_open, is_us_market_open,
    is_kr_opening_hour, is_us_opening_hour,
    format_position_summary,
)

logger = logging.getLogger(__name__)

# 알림 쿨다운 관리
_last_alert: dict[int, datetime] = {}


def _should_alert(pid: int) -> bool:
    """쿨다운 체크 (같은 포지션에 대해 N분 내 중복 알림 방지)"""
    now = datetime.now(KST)
    last = _last_alert.get(pid)
    if last and (now - last).total_seconds() < ALERT_COOLDOWN_MIN * 60:
        return False
    return True


def _mark_alerted(pid: int):
    _last_alert[pid] = datetime.now(KST)


async def check_positions(send_fn) -> list[str]:
    """
    포지션 체크 1회 실행
    send_fn: async 함수 (텔레그램 메시지 발송용)
    반환: 알림 메시지 리스트
    """
    positions = get_active_positions()
    if not positions:
        return []

    alerts = []
    for pos in positions:
        # 해당 시장이 열려있을 때만 체크
        if pos["market"] == "KR" and not is_kr_market_open():
            continue
        if pos["market"] == "US" and not is_us_market_open():
            continue

        current_price = get_price_safe(pos["ticker"], pos["market"], pos["entry_price"])
        pnl = calc_pnl(pos, current_price)

        # 알림 조건: 손익률이 임계값 초과
        if abs(pnl["pnl_pct"]) >= ALERT_THRESHOLD_PCT:
            if _should_alert(pos["id"]):
                judge = rule_based_judge(pos, current_price, pnl)
                msg = format_position_summary(pos, current_price, pnl, judge)

                if judge["alert_level"] in ("warning", "urgent"):
                    alert_msg = f"⚡ *포지션 알림*\n{msg}"
                    alerts.append(alert_msg)
                    save_alert(pos["id"], judge["alert_level"], judge["reason"])
                    _mark_alerted(pos["id"])

    # 알림 발송
    for alert in alerts:
        try:
            await send_fn(alert)
        except Exception as e:
            logger.error(f"알림 발송 실패: {e}")

    return alerts


def get_monitoring_interval() -> int:
    """
    현재 시간대에 따른 모니터링 간격 (초)
    - 장 시작 1시간: 5분 (300초)
    - 장중: 30분 (1800초)
    - 장 외: 모니터링 안 함 (3600초 대기 후 재확인)
    """
    if is_kr_opening_hour() or is_us_opening_hour():
        return 300  # 5분
    elif is_kr_market_open() or is_us_market_open():
        return 1800  # 30분
    else:
        return 3600  # 1시간 (장 외 대기)


async def monitoring_loop(send_fn):
    """
    비동기 모니터링 루프 (CPU 최적화)
    asyncio.sleep 사용으로 CPU 점유 0%
    """
    logger.info("포지션 모니터링 루프 시작")
    while True:
        interval = get_monitoring_interval()

        if is_kr_market_open() or is_us_market_open():
            try:
                alerts = await check_positions(send_fn)
                if alerts:
                    logger.info(f"알림 {len(alerts)}건 발송")
            except Exception as e:
                logger.error(f"모니터링 체크 오류: {e}")
        else:
            logger.debug("장 외 시간 — 대기 중")

        # asyncio.sleep → CPU 점유 0% (기존 time.sleep + busy loop 대비 핵심 개선)
        await asyncio.sleep(interval)
