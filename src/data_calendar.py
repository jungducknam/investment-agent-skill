"""
data_calendar.py — 경제 캘린더 이벤트 수집
"""
import logging
from datetime import datetime

import feedparser
import pytz
import yfinance as yf

from .config import KST

logger = logging.getLogger(__name__)


def get_upcoming_events() -> list[str]:
    """경제 이벤트 수집 (RSS + 고정 일정)"""
    events = []

    # RSS 시도
    try:
        feed = feedparser.parse("https://www.investing.com/rss/economic_calendar.rss")
        for entry in feed.entries[:8]:
            title = entry.get("title", "").strip()
            if title:
                events.append(title)
    except Exception:
        pass

    # 월별 고정 이벤트 보충
    now = datetime.now(KST)
    month = now.month
    fixed_events = {
        1: ["미국 CPI 발표", "미국 NFP 고용지표", "한국은행 금리결정"],
        2: ["미국 PPI 발표", "FOMC 의사록 공개"],
        3: ["FOMC 금리결정", "미국 소매판매"],
        4: ["미국 CPI 발표", "1분기 실적 시즌 시작"],
        5: ["FOMC 금리결정", "미국 CPI 발표", "한국 1분기 GDP"],
        6: ["미국 NFP 고용지표", "FOMC 금리결정"],
        7: ["미국 CPI 발표", "2분기 실적 시즌 시작"],
        8: ["잭슨홀 심포지엄", "미국 PPI 발표"],
        9: ["FOMC 금리결정", "미국 CPI 발표"],
        10: ["미국 NFP 고용지표", "3분기 실적 시즌 시작"],
        11: ["FOMC 금리결정", "미국 CPI 발표"],
        12: ["FOMC 금리결정", "미국 소매판매"],
    }
    manual_events = _manual_event_supplements(now)
    events = _dedupe([*manual_events, *events])
    if not events:
        events = fixed_events.get(month, ["주요 경제 이벤트 확인 필요"])

    return events[:6]


def get_earnings_calendar() -> list[str]:
    """주요 기업 실적 발표 일정"""
    mega_caps = ["NVDA", "AAPL", "MSFT", "GOOGL", "META", "AMZN", "TSLA"]
    earnings = []
    for ticker in mega_caps:
        try:
            cal = yf.Ticker(ticker).calendar
            if cal is not None and not cal.empty:
                date = cal.iloc[0, 0] if hasattr(cal, "iloc") else str(cal)
                earnings.append(f"{ticker} 실적발표: {date}")
        except Exception:
            pass
    return earnings[:5]


def build_event_context() -> dict:
    """이벤트 컨텍스트 빌드"""
    return {
        "economic_events": get_upcoming_events(),
        "earnings": get_earnings_calendar(),
    }


def _manual_event_supplements(now: datetime) -> list[str]:
    if now.year == 2026 and now.month == 5 and 18 <= now.day <= 24:
        return [
            "FOMC 의사록 공개",
            "Nvidia 실적 발표 대기",
            "삼성전자 노조 파업 예정",
        ]
    return []


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    result = []
    for item in items:
        item = str(item or "").strip()
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
