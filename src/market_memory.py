"""
market_memory.py — 계층적 시장 기억 시스템

시간 단위별 데이터를 축적하고, 상위 단위로 압축·요약하여 저장한다.
디테일 → 요약 방향으로 계층적으로 관리하며, 볼륨 최적화를 위해
상위 단위 저장 시 하위 단위를 삭제한다.

계층 구조:
┌─────────────────────────────────────────────────────────┐
│ 매시(Hourly)  │ 원시 스냅샷 (지수, 종목, 뉴스, 레짐)      │
│ 매일(Daily)   │ 하루 흐름 요약 (매일 07:00 생성, 매시 삭제) │
│ 매주(Weekly)  │ 주간 트렌드 요약 (토 07:00 생성, 매일 삭제) │
│ 매달(Monthly) │ 월간 레짐/변곡 요약 (말일 생성, 매주 삭제)  │
└─────────────────────────────────────────────────────────┘

최종적으로 DB에는:
- 현재 진행 중인 기간의 상세 데이터
- 과거 월간 요약본
만 남아 용량이 최적화된다.
"""
import json
import logging
import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from .config import KST, DB_PATH

logger = logging.getLogger(__name__)

MEMORY_DB = Path(DB_PATH).parent / "market_memory.db"

# ── DB 스키마 ────────────────────────────────────────
MEMORY_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    
    -- 시장 레짐
    regime_kr TEXT,
    regime_us TEXT,
    regime_score_kr REAL,
    regime_score_us REAL,
    
    -- 지수 요약 (시가, 종가, 고가, 저가, 변화율)
    indices_summary_json TEXT,
    
    -- 섹터 모멘텀 요약
    sector_summary_json TEXT,
    
    -- 주요 이벤트/시그널
    key_events_json TEXT,
    
    -- 뉴스 테마 요약
    news_themes_json TEXT,
    
    -- 모멘텀 변곡 감지
    inflection_signals_json TEXT,
    
    -- 하루 종합 내러티브 (AI 요약 또는 규칙 기반 요약)
    narrative TEXT,
    
    -- 메타
    hourly_count INTEGER,
    data_quality_score REAL
);

CREATE TABLE IF NOT EXISTS weekly_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    week_start TEXT NOT NULL,
    week_end TEXT NOT NULL,
    created_at TEXT NOT NULL,
    
    -- 주간 레짐 변화
    regime_flow_json TEXT,
    
    -- 주간 지수 성과
    indices_weekly_json TEXT,
    
    -- 섹터 로테이션 (주간)
    sector_rotation_json TEXT,
    
    -- 주간 핵심 이벤트
    key_events_json TEXT,
    
    -- 모멘텀 변곡 요약
    inflection_summary_json TEXT,
    
    -- 주간 종합 내러티브
    narrative TEXT,
    
    -- 메타
    daily_count INTEGER,
    
    UNIQUE(week_start, week_end)
);

CREATE TABLE IF NOT EXISTS monthly_summaries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    year_month TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    
    -- 월간 레짐 변화 흐름
    regime_flow_json TEXT,
    
    -- 월간 지수 성과
    indices_monthly_json TEXT,
    
    -- 월간 섹터 성과 및 로테이션
    sector_monthly_json TEXT,
    
    -- 월간 핵심 국제정세/이슈
    global_events_json TEXT,
    
    -- 모멘텀 대변곡 기록
    major_inflections_json TEXT,
    
    -- 월간 종합 내러티브
    narrative TEXT,
    
    -- 포트폴리오 성과 (있을 경우)
    portfolio_performance_json TEXT,
    
    -- 메타
    weekly_count INTEGER
);

CREATE INDEX IF NOT EXISTS idx_daily_date ON daily_summaries(date);
CREATE INDEX IF NOT EXISTS idx_weekly_start ON weekly_summaries(week_start);
CREATE INDEX IF NOT EXISTS idx_monthly_ym ON monthly_summaries(year_month);
"""


def init_memory_db():
    """메모리 DB 초기화"""
    MEMORY_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(MEMORY_DB))
    conn.executescript(MEMORY_SCHEMA)
    conn.close()
    logger.info(f"시장 기억 DB 초기화 완료: {MEMORY_DB}")


def _get_mem_conn():
    conn = sqlite3.connect(str(MEMORY_DB))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def _get_snap_conn():
    """스냅샷 DB 연결"""
    from .snapshot_collector import SNAPSHOT_DB
    conn = sqlite3.connect(str(SNAPSHOT_DB))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.row_factory = sqlite3.Row
    return conn


def _valid_regime_scores(regimes: list[tuple[str, float | int | None]]) -> list[float]:
    """레짐 라벨은 남기되, 평균 계산에는 숫자인 점수만 사용한다."""
    scores = []
    for _, score in regimes:
        if isinstance(score, (int, float)) and math.isfinite(score):
            scores.append(float(score))
    return scores


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 1. 매일 요약 생성 (매시 → 매일)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def generate_daily_summary(target_date: Optional[str] = None) -> dict:
    """
    전일(또는 지정일)의 매시 스냅샷을 종합하여 매일 요약 생성.
    매일 07:00 KST에 호출된다.
    
    Args:
        target_date: "YYYY-MM-DD" 형식. None이면 전일.
    Returns:
        생성된 요약 dict
    """
    if target_date is None:
        target_date = (datetime.now(KST) - timedelta(days=1)).strftime("%Y-%m-%d")
    
    logger.info(f"매일 요약 생성 시작: {target_date}")
    
    # 해당 날짜의 스냅샷 조회
    snap_conn = _get_snap_conn()
    try:
        rows = snap_conn.execute("""
            SELECT * FROM market_snapshots
            WHERE timestamp LIKE ? AND snapshot_type = 'hourly'
            ORDER BY timestamp ASC
        """, (f"{target_date}%",)).fetchall()
        snapshots = [dict(r) for r in rows]
        
        # 해당 날짜의 뉴스 조회
        news_rows = snap_conn.execute("""
            SELECT * FROM news_archive
            WHERE timestamp LIKE ?
            ORDER BY timestamp ASC
        """, (f"{target_date}%",)).fetchall()
        news = [dict(r) for r in news_rows]
        
        # 해당 날짜의 시그널 조회
        signal_rows = snap_conn.execute("""
            SELECT * FROM flow_signals
            WHERE timestamp LIKE ?
            ORDER BY timestamp ASC
        """, (f"{target_date}%",)).fetchall()
        signals = [dict(r) for r in signal_rows]
    finally:
        snap_conn.close()
    
    if not snapshots:
        logger.warning(f"{target_date}: 스냅샷 없음, 요약 생성 스킵")
        return {"status": "no_data", "date": target_date}
    
    # ── 지수 요약 (시가/종가/고가/저가/변화율) ──
    indices_summary = _summarize_indices(snapshots)
    
    # ── 섹터 모멘텀 요약 ──
    sector_summary = _summarize_sectors(snapshots)
    
    # ── 레짐 요약 ──
    regimes_kr = [(s["regime_kr"], s["regime_score_kr"]) for s in snapshots if s.get("regime_kr")]
    regimes_us = [(s["regime_us"], s["regime_score_us"]) for s in snapshots if s.get("regime_us")]
    
    kr_scores = _valid_regime_scores(regimes_kr)
    us_scores = _valid_regime_scores(regimes_us)
    avg_kr_score = sum(kr_scores) / len(kr_scores) if kr_scores else 0
    avg_us_score = sum(us_scores) / len(us_scores) if us_scores else 0
    dominant_kr = max(set(r[0] for r in regimes_kr), key=lambda x: sum(1 for r in regimes_kr if r[0] == x)) if regimes_kr else "unknown"
    dominant_us = max(set(r[0] for r in regimes_us), key=lambda x: sum(1 for r in regimes_us if r[0] == x)) if regimes_us else "unknown"
    
    # ── 뉴스 테마 요약 ──
    news_themes = {}
    for n in news:
        theme = n.get("theme", "general")
        if theme not in news_themes:
            news_themes[theme] = {"count": 0, "headlines": []}
        news_themes[theme]["count"] += 1
        if len(news_themes[theme]["headlines"]) < 3:
            news_themes[theme]["headlines"].append(n["headline"])
    
    # ── 주요 이벤트 (시그널 기반) ──
    key_events = []
    for sig in signals:
        key_events.append({
            "time": sig["timestamp"][11:16],
            "type": sig["signal_type"],
            "description": sig["description"],
            "strength": sig.get("strength", 0),
        })
    key_events.sort(key=lambda x: x.get("strength", 0), reverse=True)
    key_events = key_events[:10]  # 상위 10개
    
    # ── 모멘텀 변곡 감지 ──
    inflections = _detect_daily_inflections(snapshots)
    
    # ── 내러티브 생성 ──
    narrative = _build_daily_narrative(
        target_date, indices_summary, sector_summary,
        dominant_kr, dominant_us, avg_kr_score, avg_us_score,
        key_events, news_themes, inflections
    )
    
    # ── DB 저장 ──
    summary = {
        "date": target_date,
        "regime_kr": dominant_kr,
        "regime_us": dominant_us,
        "regime_score_kr": round(avg_kr_score, 1),
        "regime_score_us": round(avg_us_score, 1),
        "indices_summary": indices_summary,
        "sector_summary": sector_summary,
        "key_events": key_events,
        "news_themes": news_themes,
        "inflections": inflections,
        "narrative": narrative,
        "hourly_count": len(snapshots),
    }
    
    mem_conn = _get_mem_conn()
    try:
        mem_conn.execute("""
            INSERT OR REPLACE INTO daily_summaries
            (date, created_at, regime_kr, regime_us, regime_score_kr, regime_score_us,
             indices_summary_json, sector_summary_json, key_events_json,
             news_themes_json, inflection_signals_json, narrative, hourly_count, data_quality_score)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            target_date,
            datetime.now(KST).isoformat(),
            dominant_kr, dominant_us,
            round(avg_kr_score, 1), round(avg_us_score, 1),
            json.dumps(indices_summary, ensure_ascii=False),
            json.dumps(sector_summary, ensure_ascii=False),
            json.dumps(key_events, ensure_ascii=False),
            json.dumps(news_themes, ensure_ascii=False),
            json.dumps(inflections, ensure_ascii=False),
            narrative,
            len(snapshots),
            min(len(snapshots) / 12.0, 1.0),  # 12개 이상이면 품질 1.0
        ))
        mem_conn.commit()
    finally:
        mem_conn.close()
    
    logger.info(f"매일 요약 생성 완료: {target_date} (스냅샷 {len(snapshots)}개 → 1개 요약)")
    return summary


def _summarize_indices(snapshots: list) -> dict:
    """스냅샷 리스트에서 지수별 시가/종가/고가/저가/변화율 추출"""
    index_names = ["KOSPI", "KOSDAQ", "SP500", "NASDAQ", "VIX", "USD_KRW", "US10Y", "GOLD", "BRENT"]
    result = {}
    
    for name in index_names:
        prices = []
        for s in snapshots:
            idx_data = json.loads(s.get("indices_json", "{}"))
            price = idx_data.get(name, {}).get("price")
            if price and price > 0:
                prices.append(price)
        
        if not prices:
            continue
        
        result[name] = {
            "open": prices[0],
            "close": prices[-1],
            "high": max(prices),
            "low": min(prices),
            "change_pct": round((prices[-1] - prices[0]) / prices[0] * 100, 2),
            "range_pct": round((max(prices) - min(prices)) / prices[0] * 100, 2),
            "data_points": len(prices),
        }
    
    return result


def _summarize_sectors(snapshots: list) -> dict:
    """섹터 모멘텀 변화 요약"""
    if len(snapshots) < 2:
        return {}
    
    first_sectors = json.loads(snapshots[0].get("sector_momentum_json", "{}"))
    last_sectors = json.loads(snapshots[-1].get("sector_momentum_json", "{}"))
    
    result = {}
    for sector in set(list(first_sectors.keys()) + list(last_sectors.keys())):
        first = first_sectors.get(sector, {})
        last = last_sectors.get(sector, {})
        
        first_5d = first.get("ret_5d", 0)
        last_5d = last.get("ret_5d", 0)
        momentum_change = last_5d - first_5d
        
        result[sector] = {
            "start_momentum": first_5d,
            "end_momentum": last_5d,
            "momentum_change": round(momentum_change, 2),
            "direction": "강화" if momentum_change > 0.5 else ("약화" if momentum_change < -0.5 else "유지"),
            "ret_5d": last.get("ret_5d", 0),
            "ret_20d": last.get("ret_20d", 0),
        }
    
    return result


def _detect_daily_inflections(snapshots: list) -> list:
    """하루 내 모멘텀 변곡점 감지"""
    inflections = []
    
    if len(snapshots) < 4:
        return inflections
    
    # 지수별 방향 전환 감지
    index_names = ["KOSPI", "KOSDAQ", "SP500", "NASDAQ"]
    for name in index_names:
        prices = []
        for s in snapshots:
            idx_data = json.loads(s.get("indices_json", "{}"))
            price = idx_data.get(name, {}).get("price")
            if price:
                prices.append(price)
        
        if len(prices) < 4:
            continue
        
        # 3구간으로 나눠서 방향 전환 감지
        third = len(prices) // 3
        seg1 = prices[third] - prices[0]
        seg2 = prices[2*third] - prices[third]
        seg3 = prices[-1] - prices[2*third]
        
        # V자 반등 감지
        if seg1 < 0 and seg3 > 0 and abs(seg3) > abs(seg1) * 0.5:
            inflections.append({
                "type": "V_reversal",
                "target": name,
                "description": f"{name} V자 반등 (하락 후 반등)",
                "magnitude": round((prices[-1] - min(prices)) / min(prices) * 100, 2),
            })
        
        # 역V자 (고점 후 하락)
        elif seg1 > 0 and seg3 < 0 and abs(seg3) > abs(seg1) * 0.5:
            inflections.append({
                "type": "inverted_V",
                "target": name,
                "description": f"{name} 역V자 (상승 후 하락 전환)",
                "magnitude": round((max(prices) - prices[-1]) / max(prices) * 100, 2),
            })
    
    return inflections


def _build_daily_narrative(date, indices, sectors, regime_kr, regime_us,
                           score_kr, score_us, events, news_themes, inflections) -> str:
    """하루의 종합 내러티브 생성"""
    lines = []
    lines.append(f"[{date}] 시장 일간 요약")
    lines.append(f"레짐: 한국={regime_kr}({score_kr:+.0f}) | 미국={regime_us}({score_us:+.0f})")
    
    # 주요 지수 변화
    for name in ["KOSPI", "KOSDAQ", "SP500", "NASDAQ"]:
        if name in indices:
            d = indices[name]
            emoji = "📈" if d["change_pct"] > 0 else "📉"
            lines.append(f"  {emoji} {name}: {d['open']:,.0f}→{d['close']:,.0f} ({d['change_pct']:+.2f}%)")
    
    # VIX
    if "VIX" in indices:
        lines.append(f"  VIX: {indices['VIX']['close']:.1f} ({indices['VIX']['change_pct']:+.1f}%)")
    
    # 섹터 로테이션 핵심
    if sectors:
        top_sectors = sorted(sectors.items(), key=lambda x: x[1].get("momentum_change", 0), reverse=True)[:3]
        bottom_sectors = sorted(sectors.items(), key=lambda x: x[1].get("momentum_change", 0))[:3]
        if top_sectors:
            lines.append(f"  강화 섹터: {', '.join(s[0] for s in top_sectors)}")
        if bottom_sectors:
            lines.append(f"  약화 섹터: {', '.join(s[0] for s in bottom_sectors)}")
    
    # 주요 이벤트
    if events:
        lines.append(f"  주요 이벤트 {len(events)}건: {events[0]['description']}")
    
    # 변곡점
    if inflections:
        for inf in inflections[:2]:
            lines.append(f"  ⚡ 변곡: {inf['description']}")
    
    # 뉴스 테마
    if news_themes:
        top_themes = sorted(news_themes.items(), key=lambda x: x[1]["count"], reverse=True)[:3]
        theme_str = ", ".join(f"{t[0]}({t[1]['count']}건)" for t in top_themes)
        lines.append(f"  뉴스 테마: {theme_str}")
    
    return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 2. 매주 요약 생성 (매일 → 매주)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def generate_weekly_summary(week_end_date: Optional[str] = None) -> dict:
    """
    해당 주의 매일 요약을 종합하여 매주 요약 생성.
    매주 토요일 07:00 KST에 호출된다.
    
    Args:
        week_end_date: 주 마지막 날짜 "YYYY-MM-DD". None이면 전일(금요일).
    """
    if week_end_date is None:
        # 가장 최근 금요일 찾기
        today = datetime.now(KST).date()
        days_since_friday = (today.weekday() - 4) % 7
        if days_since_friday == 0:
            days_since_friday = 7  # 토요일에 실행 시 전일 금요일
        friday = today - timedelta(days=days_since_friday)
        week_end_date = friday.strftime("%Y-%m-%d")
    
    # 주 시작일 (월요일)
    end_dt = datetime.strptime(week_end_date, "%Y-%m-%d")
    week_start_date = (end_dt - timedelta(days=end_dt.weekday())).strftime("%Y-%m-%d")
    
    logger.info(f"매주 요약 생성: {week_start_date} ~ {week_end_date}")
    
    # 해당 주의 매일 요약 조회
    mem_conn = _get_mem_conn()
    try:
        rows = mem_conn.execute("""
            SELECT * FROM daily_summaries
            WHERE date >= ? AND date <= ?
            ORDER BY date ASC
        """, (week_start_date, week_end_date)).fetchall()
        dailies = [dict(r) for r in rows]
    finally:
        mem_conn.close()
    
    if not dailies:
        logger.warning(f"주간 요약: 매일 데이터 없음 ({week_start_date}~{week_end_date})")
        return {"status": "no_data"}
    
    # ── 레짐 흐름 ──
    regime_flow = []
    for d in dailies:
        regime_flow.append({
            "date": d["date"],
            "kr": d["regime_kr"],
            "us": d["regime_us"],
            "score_kr": d["regime_score_kr"],
            "score_us": d["regime_score_us"],
        })
    
    # ── 지수 주간 성과 ──
    indices_weekly = {}
    first_day = json.loads(dailies[0].get("indices_summary_json", "{}"))
    last_day = json.loads(dailies[-1].get("indices_summary_json", "{}"))
    
    for name in ["KOSPI", "KOSDAQ", "SP500", "NASDAQ", "VIX", "USD_KRW"]:
        first = first_day.get(name, {})
        last = last_day.get(name, {})
        if first.get("open") and last.get("close"):
            weekly_chg = (last["close"] - first["open"]) / first["open"] * 100
            indices_weekly[name] = {
                "week_open": first["open"],
                "week_close": last["close"],
                "week_high": max(d.get("high", 0) for d in [json.loads(dd.get("indices_summary_json", "{}")).get(name, {}) for dd in dailies] if d),
                "week_low": min(d.get("low", float("inf")) for d in [json.loads(dd.get("indices_summary_json", "{}")).get(name, {}) for dd in dailies] if d and d.get("low")),
                "week_change_pct": round(weekly_chg, 2),
            }
    
    # ── 섹터 로테이션 주간 ──
    sector_rotation = {}
    first_sectors = json.loads(dailies[0].get("sector_summary_json", "{}"))
    last_sectors = json.loads(dailies[-1].get("sector_summary_json", "{}"))
    for sector in set(list(first_sectors.keys()) + list(last_sectors.keys())):
        first_m = first_sectors.get(sector, {}).get("ret_5d", 0)
        last_m = last_sectors.get(sector, {}).get("ret_5d", 0)
        sector_rotation[sector] = {
            "start": first_m,
            "end": last_m,
            "change": round(last_m - first_m, 2),
        }
    
    # ── 주간 핵심 이벤트 (각 일의 top 이벤트 수집) ──
    weekly_events = []
    for d in dailies:
        events = json.loads(d.get("key_events_json", "[]"))
        if events:
            weekly_events.append({
                "date": d["date"],
                "event": events[0]["description"] if events else "",
            })
    
    # ── 변곡 요약 ──
    all_inflections = []
    for d in dailies:
        infs = json.loads(d.get("inflection_signals_json", "[]"))
        for inf in infs:
            inf["date"] = d["date"]
            all_inflections.append(inf)
    
    # ── 주간 내러티브 ──
    narrative = _build_weekly_narrative(
        week_start_date, week_end_date, dailies,
        regime_flow, indices_weekly, sector_rotation,
        weekly_events, all_inflections
    )
    
    # ── DB 저장 ──
    summary = {
        "week_start": week_start_date,
        "week_end": week_end_date,
        "regime_flow": regime_flow,
        "indices_weekly": indices_weekly,
        "sector_rotation": sector_rotation,
        "key_events": weekly_events,
        "inflections": all_inflections,
        "narrative": narrative,
        "daily_count": len(dailies),
    }
    
    mem_conn = _get_mem_conn()
    try:
        mem_conn.execute("""
            INSERT OR REPLACE INTO weekly_summaries
            (week_start, week_end, created_at, regime_flow_json, indices_weekly_json,
             sector_rotation_json, key_events_json, inflection_summary_json, narrative, daily_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            week_start_date, week_end_date,
            datetime.now(KST).isoformat(),
            json.dumps(regime_flow, ensure_ascii=False),
            json.dumps(indices_weekly, ensure_ascii=False),
            json.dumps(sector_rotation, ensure_ascii=False),
            json.dumps(weekly_events, ensure_ascii=False),
            json.dumps(all_inflections, ensure_ascii=False),
            narrative,
            len(dailies),
        ))
        mem_conn.commit()
    finally:
        mem_conn.close()
    
    logger.info(f"매주 요약 생성 완료: {week_start_date}~{week_end_date} (매일 {len(dailies)}개 → 1개 요약)")
    return summary


def _build_weekly_narrative(start, end, dailies, regime_flow, indices, sectors, events, inflections) -> str:
    """주간 종합 내러티브"""
    lines = []
    lines.append(f"[{start}~{end}] 주간 시장 요약")
    
    # 레짐 변화
    if regime_flow:
        kr_regimes = [r["kr"] for r in regime_flow]
        us_regimes = [r["us"] for r in regime_flow]
        kr_change = f"{kr_regimes[0]}→{kr_regimes[-1]}" if kr_regimes[0] != kr_regimes[-1] else kr_regimes[-1]
        us_change = f"{us_regimes[0]}→{us_regimes[-1]}" if us_regimes[0] != us_regimes[-1] else us_regimes[-1]
        lines.append(f"레짐: 한국={kr_change} | 미국={us_change}")
    
    # 지수 주간 성과
    for name in ["KOSPI", "SP500", "NASDAQ"]:
        if name in indices:
            d = indices[name]
            lines.append(f"  {name}: {d.get('week_change_pct', 0):+.2f}% ({d.get('week_open', 0):,.0f}→{d.get('week_close', 0):,.0f})")
    
    # 섹터 핵심
    if sectors:
        top = sorted(sectors.items(), key=lambda x: x[1].get("change", 0), reverse=True)[:3]
        lines.append(f"  주간 강세 섹터: {', '.join(s[0] for s in top)}")
    
    # 변곡점
    if inflections:
        lines.append(f"  주간 변곡점 {len(inflections)}건")
        for inf in inflections[:3]:
            lines.append(f"    [{inf.get('date', '')}] {inf['description']}")
    
    return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 3. 매달 요약 생성 (매주 → 매달)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def generate_monthly_summary(year_month: Optional[str] = None) -> dict:
    """
    해당 월의 매주 요약을 종합하여 매달 요약 생성.
    매월 말일에 호출된다.
    
    Args:
        year_month: "YYYY-MM" 형식. None이면 전월.
    """
    if year_month is None:
        today = datetime.now(KST).date()
        first_of_month = today.replace(day=1)
        last_month = first_of_month - timedelta(days=1)
        year_month = last_month.strftime("%Y-%m")
    
    logger.info(f"매달 요약 생성: {year_month}")
    
    # 해당 월의 매주 요약 조회
    month_start = f"{year_month}-01"
    # 다음 달 첫날
    year, month = int(year_month[:4]), int(year_month[5:7])
    if month == 12:
        next_month_start = f"{year+1}-01-01"
    else:
        next_month_start = f"{year}-{month+1:02d}-01"
    
    mem_conn = _get_mem_conn()
    try:
        rows = mem_conn.execute("""
            SELECT * FROM weekly_summaries
            WHERE week_start >= ? AND week_start < ?
            ORDER BY week_start ASC
        """, (month_start, next_month_start)).fetchall()
        weeklies = [dict(r) for r in rows]
        
        # 매일 데이터도 참조 (내러티브 풍부화)
        daily_rows = mem_conn.execute("""
            SELECT * FROM daily_summaries
            WHERE date >= ? AND date < ?
            ORDER BY date ASC
        """, (month_start, next_month_start)).fetchall()
        dailies = [dict(r) for r in daily_rows]
    finally:
        mem_conn.close()
    
    if not weeklies and not dailies:
        logger.warning(f"월간 요약: 데이터 없음 ({year_month})")
        return {"status": "no_data"}
    
    # ── 월간 레짐 흐름 ──
    regime_flow = []
    for d in dailies:
        regime_flow.append({
            "date": d["date"],
            "kr": d["regime_kr"],
            "us": d["regime_us"],
            "score_kr": d["regime_score_kr"],
            "score_us": d["regime_score_us"],
        })
    
    # ── 월간 지수 성과 ──
    indices_monthly = {}
    if dailies:
        first_indices = json.loads(dailies[0].get("indices_summary_json", "{}"))
        last_indices = json.loads(dailies[-1].get("indices_summary_json", "{}"))
        for name in ["KOSPI", "KOSDAQ", "SP500", "NASDAQ", "VIX", "USD_KRW"]:
            first = first_indices.get(name, {})
            last = last_indices.get(name, {})
            if first.get("open") and last.get("close"):
                monthly_chg = (last["close"] - first["open"]) / first["open"] * 100
                indices_monthly[name] = {
                    "month_open": first["open"],
                    "month_close": last["close"],
                    "month_change_pct": round(monthly_chg, 2),
                }
    
    # ── 월간 섹터 성과 ──
    sector_monthly = {}
    if dailies:
        first_sectors = json.loads(dailies[0].get("sector_summary_json", "{}"))
        last_sectors = json.loads(dailies[-1].get("sector_summary_json", "{}"))
        for sector in set(list(first_sectors.keys()) + list(last_sectors.keys())):
            first_m = first_sectors.get(sector, {}).get("ret_5d", 0)
            last_m = last_sectors.get(sector, {}).get("ret_5d", 0)
            sector_monthly[sector] = {
                "start": first_m,
                "end": last_m,
                "monthly_change": round(last_m - first_m, 2),
            }
    
    # ── 월간 핵심 국제정세/이슈 ──
    global_events = []
    for w in weeklies:
        events = json.loads(w.get("key_events_json", "[]"))
        for e in events:
            global_events.append(e)
    # 상위 이벤트만 유지
    global_events = global_events[:15]
    
    # ── 월간 대변곡 기록 ──
    major_inflections = []
    for w in weeklies:
        infs = json.loads(w.get("inflection_summary_json", "[]"))
        for inf in infs:
            if inf.get("magnitude", 0) > 1.0:  # 1% 이상 변곡만
                major_inflections.append(inf)
    
    # ── 월간 내러티브 ──
    narrative = _build_monthly_narrative(
        year_month, regime_flow, indices_monthly,
        sector_monthly, global_events, major_inflections, len(weeklies)
    )
    
    # ── DB 저장 ──
    mem_conn = _get_mem_conn()
    try:
        mem_conn.execute("""
            INSERT OR REPLACE INTO monthly_summaries
            (year_month, created_at, regime_flow_json, indices_monthly_json,
             sector_monthly_json, global_events_json, major_inflections_json,
             narrative, weekly_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            year_month,
            datetime.now(KST).isoformat(),
            json.dumps(regime_flow, ensure_ascii=False),
            json.dumps(indices_monthly, ensure_ascii=False),
            json.dumps(sector_monthly, ensure_ascii=False),
            json.dumps(global_events, ensure_ascii=False),
            json.dumps(major_inflections, ensure_ascii=False),
            narrative,
            len(weeklies),
        ))
        mem_conn.commit()
    finally:
        mem_conn.close()
    
    logger.info(f"매달 요약 생성 완료: {year_month} (매주 {len(weeklies)}개 → 1개 요약)")
    return {"year_month": year_month, "status": "ok"}


def _build_monthly_narrative(year_month, regime_flow, indices, sectors, events, inflections, weekly_count) -> str:
    """월간 종합 내러티브"""
    lines = []
    lines.append(f"[{year_month}] 월간 시장 종합 요약")
    
    # 레짐 변화 흐름
    if regime_flow:
        kr_start = regime_flow[0]["kr"]
        kr_end = regime_flow[-1]["kr"]
        us_start = regime_flow[0]["us"]
        us_end = regime_flow[-1]["us"]
        lines.append(f"레짐 변화: 한국 {kr_start}→{kr_end} | 미국 {us_start}→{us_end}")
        
        # 레짐 전환 횟수
        kr_changes = sum(1 for i in range(1, len(regime_flow)) if regime_flow[i]["kr"] != regime_flow[i-1]["kr"])
        us_changes = sum(1 for i in range(1, len(regime_flow)) if regime_flow[i]["us"] != regime_flow[i-1]["us"])
        lines.append(f"레짐 전환: 한국 {kr_changes}회 | 미국 {us_changes}회")
    
    # 지수 월간 성과
    for name in ["KOSPI", "SP500", "NASDAQ"]:
        if name in indices:
            d = indices[name]
            lines.append(f"  {name}: {d.get('month_change_pct', 0):+.2f}%")
    
    # 섹터 월간 핵심
    if sectors:
        top = sorted(sectors.items(), key=lambda x: x[1].get("monthly_change", 0), reverse=True)[:3]
        bottom = sorted(sectors.items(), key=lambda x: x[1].get("monthly_change", 0))[:3]
        lines.append(f"  월간 강세: {', '.join(s[0] for s in top)}")
        lines.append(f"  월간 약세: {', '.join(s[0] for s in bottom)}")
    
    # 대변곡
    if inflections:
        lines.append(f"  월간 대변곡 {len(inflections)}건")
    
    # 핵심 이벤트
    if events:
        lines.append(f"  핵심 이벤트 {len(events)}건")
        for e in events[:5]:
            lines.append(f"    - {e.get('event', e.get('description', ''))}")
    
    return "\n".join(lines)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 4. 데이터 정리 (하위 계층 삭제)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def cleanup_hourly_after_daily(target_date: str):
    """
    매일 요약 저장 후, 해당 날짜의 매시 스냅샷 삭제.
    현재 날짜(오늘)의 데이터는 보존한다.
    """
    snap_conn = _get_snap_conn()
    try:
        # 해당 날짜의 스냅샷 삭제
        snap_conn.execute("""
            DELETE FROM market_snapshots
            WHERE timestamp LIKE ? AND snapshot_type = 'hourly'
        """, (f"{target_date}%",))
        
        # 해당 날짜의 뉴스 삭제
        snap_conn.execute("""
            DELETE FROM news_archive
            WHERE timestamp LIKE ?
        """, (f"{target_date}%",))
        
        # 해당 날짜의 시그널 삭제
        snap_conn.execute("""
            DELETE FROM flow_signals
            WHERE timestamp LIKE ?
        """, (f"{target_date}%",))
        
        snap_conn.commit()
        logger.info(f"매시 데이터 정리 완료: {target_date}")
    finally:
        snap_conn.close()


def cleanup_daily_after_weekly(week_start: str, week_end: str):
    """
    매주 요약 저장 후, 해당 주의 매일 요약 삭제.
    """
    mem_conn = _get_mem_conn()
    try:
        mem_conn.execute("""
            DELETE FROM daily_summaries
            WHERE date >= ? AND date <= ?
        """, (week_start, week_end))
        mem_conn.commit()
        logger.info(f"매일 데이터 정리 완료: {week_start}~{week_end}")
    finally:
        mem_conn.close()


def cleanup_weekly_after_monthly(year_month: str):
    """
    매달 요약 저장 후, 해당 월의 매주 요약 삭제.
    """
    month_start = f"{year_month}-01"
    year, month = int(year_month[:4]), int(year_month[5:7])
    if month == 12:
        next_month_start = f"{year+1}-01-01"
    else:
        next_month_start = f"{year}-{month+1:02d}-01"
    
    mem_conn = _get_mem_conn()
    try:
        mem_conn.execute("""
            DELETE FROM weekly_summaries
            WHERE week_start >= ? AND week_start < ?
        """, (month_start, next_month_start))
        mem_conn.commit()
        logger.info(f"매주 데이터 정리 완료: {year_month}")
    finally:
        mem_conn.close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 5. 리포트용 컨텍스트 조회
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def get_memory_context_for_report() -> str:
    """
    리포트 생성 시 AI에게 전달할 과거 기억 컨텍스트.
    최근 매일 + 최근 매주 + 최근 매달 내러티브를 조합.
    """
    mem_conn = _get_mem_conn()
    lines = []
    
    try:
        # 최근 매일 요약 (최대 5일)
        dailies = mem_conn.execute("""
            SELECT date, narrative FROM daily_summaries
            ORDER BY date DESC LIMIT 5
        """).fetchall()
        
        if dailies:
            lines.append("━━━ 최근 일간 기록 ━━━")
            for d in reversed(dailies):
                lines.append(d["narrative"])
                lines.append("")
        
        # 최근 매주 요약 (최대 4주)
        weeklies = mem_conn.execute("""
            SELECT week_start, week_end, narrative FROM weekly_summaries
            ORDER BY week_start DESC LIMIT 4
        """).fetchall()
        
        if weeklies:
            lines.append("━━━ 최근 주간 기록 ━━━")
            for w in reversed(weeklies):
                lines.append(w["narrative"])
                lines.append("")
        
        # 최근 매달 요약 (최대 3개월)
        monthlies = mem_conn.execute("""
            SELECT year_month, narrative FROM monthly_summaries
            ORDER BY year_month DESC LIMIT 3
        """).fetchall()
        
        if monthlies:
            lines.append("━━━ 최근 월간 기록 ━━━")
            for m in reversed(monthlies):
                lines.append(m["narrative"])
                lines.append("")
    
    finally:
        mem_conn.close()
    
    if not lines:
        return "⚠️ 과거 기억 없음 (시스템 첫 실행 — 데이터 축적 시작)"
    
    return "\n".join(lines)
