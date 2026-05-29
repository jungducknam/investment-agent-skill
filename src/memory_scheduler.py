"""
memory_scheduler.py — 계층적 시장 기억 스케줄러

봇 시작 시 asyncio.create_task로 실행되어, 정해진 시간에
매일/매주/매달 요약을 자동 생성하고 하위 데이터를 삭제한다.

스케줄:
- 매일 07:00 KST → 전일 매시 → 매일 요약 생성 → 전일 매시 삭제
- 매주 토요일 07:00 KST → 해당 주 매일 → 매주 요약 생성 → 해당 주 매일 삭제
- 매월 말일 23:50 KST → 해당 월 매주 → 매달 요약 생성 → 해당 월 매주 삭제
"""
import asyncio
import calendar
import logging
from datetime import datetime, timedelta

from .config import KST
from .market_memory import (
    init_memory_db,
    generate_daily_summary,
    generate_weekly_summary,
    generate_monthly_summary,
    cleanup_hourly_after_daily,
    cleanup_daily_after_weekly,
    cleanup_weekly_after_monthly,
)

logger = logging.getLogger(__name__)


def _seconds_until(hour: int, minute: int = 0) -> int:
    """다음 지정 시각까지 남은 초"""
    now = datetime.now(KST)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return int((target - now).total_seconds())


def _is_last_day_of_month() -> bool:
    """오늘이 이번 달 마지막 날인지"""
    today = datetime.now(KST).date()
    last_day = calendar.monthrange(today.year, today.month)[1]
    return today.day == last_day


async def daily_summary_job():
    """
    매일 07:00 KST 실행.
    전일의 매시 스냅샷을 종합하여 매일 요약 생성 후, 전일 매시 삭제.
    """
    while True:
        # 다음 07:00까지 대기
        wait_sec = _seconds_until(7, 0)
        logger.info(f"[매일 요약] 다음 실행까지 {wait_sec//3600}시간 {(wait_sec%3600)//60}분 대기")
        await asyncio.sleep(wait_sec)
        
        try:
            yesterday = (datetime.now(KST) - timedelta(days=1)).strftime("%Y-%m-%d")
            logger.info(f"[매일 요약] {yesterday} 요약 생성 시작")
            
            # 1. 매일 요약 생성
            result = generate_daily_summary(yesterday)
            
            if result.get("status") != "no_data":
                # 2. 전일 매시 데이터 삭제 (용량 최적화)
                cleanup_hourly_after_daily(yesterday)
                logger.info(f"[매일 요약] {yesterday} 완료 — 매시 데이터 삭제됨")
            else:
                logger.warning(f"[매일 요약] {yesterday} 데이터 없음, 스킵")
        
        except Exception as e:
            logger.error(f"[매일 요약] 오류: {e}", exc_info=True)
        
        # 최소 23시간 대기 (중복 실행 방지)
        await asyncio.sleep(82800)


async def weekly_summary_job():
    """
    매주 토요일 07:00 KST 실행.
    해당 주의 매일 요약을 종합하여 매주 요약 생성 후, 해당 주 매일 삭제.
    """
    while True:
        # 다음 토요일 07:00까지 대기
        now = datetime.now(KST)
        days_until_saturday = (5 - now.weekday()) % 7
        if days_until_saturday == 0 and now.hour >= 7:
            days_until_saturday = 7
        
        target = now.replace(hour=7, minute=0, second=0, microsecond=0) + timedelta(days=days_until_saturday)
        wait_sec = int((target - now).total_seconds())
        
        logger.info(f"[매주 요약] 다음 실행까지 {wait_sec//86400}일 {(wait_sec%86400)//3600}시간 대기")
        await asyncio.sleep(wait_sec)
        
        try:
            # 이번 주 금요일
            friday = (datetime.now(KST) - timedelta(days=1)).strftime("%Y-%m-%d")
            logger.info(f"[매주 요약] 주간 요약 생성 시작 (금요일: {friday})")
            
            # 1. 매주 요약 생성
            result = generate_weekly_summary(friday)
            
            if result.get("status") != "no_data":
                # 2. 해당 주 매일 데이터 삭제
                week_start = result.get("week_start")
                week_end = result.get("week_end", friday)
                if week_start:
                    cleanup_daily_after_weekly(week_start, week_end)
                    logger.info(f"[매주 요약] {week_start}~{week_end} 완료 — 매일 데이터 삭제됨")
            else:
                logger.warning("[매주 요약] 데이터 없음, 스킵")
        
        except Exception as e:
            logger.error(f"[매주 요약] 오류: {e}", exc_info=True)
        
        # 최소 6일 대기 (중복 실행 방지)
        await asyncio.sleep(518400)


async def monthly_summary_job():
    """
    매월 말일 23:50 KST 실행.
    해당 월의 매주 요약을 종합하여 매달 요약 생성 후, 해당 월 매주 삭제.
    """
    while True:
        # 다음 말일 23:50까지 대기
        now = datetime.now(KST)
        today = now.date()
        last_day = calendar.monthrange(today.year, today.month)[1]
        
        if today.day == last_day and now.hour < 23:
            # 오늘이 말일이고 아직 23:50 전
            target = now.replace(hour=23, minute=50, second=0, microsecond=0)
        else:
            # 다음 달 말일
            if today.month == 12:
                next_month = today.replace(year=today.year + 1, month=1, day=1)
            else:
                next_month = today.replace(month=today.month + 1, day=1)
            next_last_day = calendar.monthrange(next_month.year, next_month.month)[1]
            target_date = next_month.replace(day=next_last_day)
            target = datetime(target_date.year, target_date.month, target_date.day, 23, 50, tzinfo=KST)
        
        wait_sec = int((target - now).total_seconds())
        if wait_sec < 0:
            wait_sec = 86400  # 안전장치
        
        logger.info(f"[매달 요약] 다음 실행까지 {wait_sec//86400}일 대기")
        await asyncio.sleep(wait_sec)
        
        try:
            year_month = datetime.now(KST).strftime("%Y-%m")
            logger.info(f"[매달 요약] {year_month} 요약 생성 시작")
            
            # 1. 매달 요약 생성
            result = generate_monthly_summary(year_month)
            
            if result.get("status") != "no_data":
                # 2. 해당 월 매주 데이터 삭제
                cleanup_weekly_after_monthly(year_month)
                logger.info(f"[매달 요약] {year_month} 완료 — 매주 데이터 삭제됨")
            else:
                logger.warning(f"[매달 요약] {year_month} 데이터 없음, 스킵")
        
        except Exception as e:
            logger.error(f"[매달 요약] 오류: {e}", exc_info=True)
        
        # 최소 27일 대기 (중복 실행 방지)
        await asyncio.sleep(2332800)


async def memory_scheduler_loop():
    """
    모든 계층적 요약 스케줄러를 동시에 실행.
    bot.py의 post_init에서 asyncio.create_task로 호출.
    """
    init_memory_db()
    logger.info("계층적 시장 기억 스케줄러 시작 (매일/매주/매달)")
    
    # 3개의 스케줄러를 동시에 실행
    await asyncio.gather(
        daily_summary_job(),
        weekly_summary_job(),
        monthly_summary_job(),
    )
