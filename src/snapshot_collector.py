"""
snapshot_collector.py — 주기적 시장 스냅샷 수집기

OCI 서버에서 1~2시간 간격으로 실행되어 시장 상태를 DB에 축적한다.
축적된 데이터는 리포트 생성 시 "연속적 흐름"으로 AI에게 전달된다.

수집 항목:
1. 주요 지수 (KOSPI, KOSDAQ, S&P500, NASDAQ, VIX, USD/KRW 등)
2. 관심 종목 가격 + 기술적 지표 (RSI, BB, 거래량)
3. 섹터 모멘텀 스냅샷
4. 주요 뉴스 헤드라인 (중복 제거)
5. 시장 레짐 판단 결과

수집 주기:
- 장중: 1시간 간격
- 장 외: 2시간 간격
- 주말: 6시간 간격 (뉴스만)
"""
import asyncio
import json
import logging
import sqlite3
import hashlib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import numpy as np

from .config import KST, DB_PATH
from .data_market import fetch_indices, fetch_stock_prices, fetch_sector_momentum
from .data_news import fetch_all_news, get_market_headlines, build_theme_context
from .position_tracker import is_kr_market_open, is_us_market_open

logger = logging.getLogger(__name__)

# ── DB 스키마 ────────────────────────────────────────
SNAPSHOT_DB = Path(DB_PATH).parent / "snapshots.db"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    snapshot_type TEXT NOT NULL DEFAULT 'hourly',
    
    -- 지수 데이터 (JSON)
    indices_json TEXT,
    
    -- 섹터 모멘텀 (JSON)
    sector_momentum_json TEXT,
    
    -- 종목 가격 (JSON)
    stock_prices_json TEXT,
    
    -- 시장 레짐 판단 결과
    regime_kr TEXT,
    regime_us TEXT,
    regime_score_kr REAL,
    regime_score_us REAL,
    cash_recommendation INTEGER,
    
    -- 메타
    collection_duration_sec REAL,
    error_count INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS news_archive (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    headline TEXT NOT NULL,
    source TEXT,
    theme TEXT,
    hash TEXT UNIQUE,
    sentiment TEXT,
    relevance_score REAL DEFAULT 0.5,
    primary_theme TEXT,
    secondary_themes_json TEXT,
    market_relevance_score REAL,
    novelty_score REAL,
    urgency_score REAL,
    confidence_score REAL,
    impact_score REAL,
    historical_impact_score REAL,
    current_market_reaction_score REAL,
    report_priority TEXT,
    should_include_in_report INTEGER,
    validation_status TEXT,
    validation_errors_json TEXT,
    affected_assets_json TEXT,
    impact_channel TEXT,
    investment_implication TEXT,
    scored_at TEXT
);

CREATE TABLE IF NOT EXISTS flow_signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    target TEXT NOT NULL,
    direction TEXT,
    strength REAL,
    description TEXT,
    data_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_snapshot_ts ON market_snapshots(timestamp);
CREATE INDEX IF NOT EXISTS idx_news_ts ON news_archive(timestamp);
CREATE INDEX IF NOT EXISTS idx_news_hash ON news_archive(hash);
CREATE INDEX IF NOT EXISTS idx_news_impact ON news_archive(impact_score);
CREATE INDEX IF NOT EXISTS idx_news_primary_theme ON news_archive(primary_theme);
CREATE INDEX IF NOT EXISTS idx_flow_ts ON flow_signals(timestamp);
CREATE INDEX IF NOT EXISTS idx_flow_type ON flow_signals(signal_type);
"""

NEWS_ARCHIVE_SCORE_COLUMNS = {
    "primary_theme": "TEXT",
    "secondary_themes_json": "TEXT",
    "market_relevance_score": "REAL",
    "novelty_score": "REAL",
    "urgency_score": "REAL",
    "confidence_score": "REAL",
    "impact_score": "REAL",
    "historical_impact_score": "REAL",
    "current_market_reaction_score": "REAL",
    "report_priority": "TEXT",
    "should_include_in_report": "INTEGER",
    "validation_status": "TEXT",
    "validation_errors_json": "TEXT",
    "affected_assets_json": "TEXT",
    "impact_channel": "TEXT",
    "investment_implication": "TEXT",
    "scored_at": "TEXT",
}


def init_snapshot_db():
    """스냅샷 DB 초기화"""
    SNAPSHOT_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(SNAPSHOT_DB))
    conn.executescript(SCHEMA_SQL)
    _ensure_news_archive_score_columns(conn)
    conn.close()
    logger.info(f"스냅샷 DB 초기화 완료: {SNAPSHOT_DB}")


def _get_conn():
    conn = sqlite3.connect(str(SNAPSHOT_DB))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def _news_hash(headline: str) -> str:
    """뉴스 중복 방지용 해시"""
    normalized = headline.strip().lower()[:100]
    return hashlib.md5(normalized.encode()).hexdigest()


# ── 스냅샷 수집 ──────────────────────────────────────
def collect_snapshot() -> dict:
    """
    시장 스냅샷 1회 수집 및 DB 저장
    Returns: 수집 결과 요약
    """
    import time
    start = time.time()
    now = datetime.now(KST)
    errors = 0
    
    # 1. 지수 수집
    try:
        indices = fetch_indices()
    except Exception as e:
        logger.error(f"지수 수집 실패: {e}")
        indices = {}
        errors += 1
    
    # 2. 종목 가격 수집
    try:
        stock_prices = fetch_stock_prices()
    except Exception as e:
        logger.error(f"종목 가격 수집 실패: {e}")
        stock_prices = {}
        errors += 1
    
    # 3. 섹터 모멘텀
    try:
        sector_mom = fetch_sector_momentum()
    except Exception as e:
        logger.error(f"섹터 모멘텀 수집 실패: {e}")
        sector_mom = {}
        errors += 1
    
    # 4. 뉴스 수집 및 아카이빙
    news_count = 0
    try:
        all_news = fetch_all_news(max_per_feed=8)
        headlines = get_market_headlines(all_news, 15)
        themes = build_theme_context(all_news)
        news_count = _archive_news(now, headlines, themes)
    except Exception as e:
        logger.error(f"뉴스 수집 실패: {e}")
        errors += 1
    
    # 5. 시장 레짐 판단
    regime_kr, regime_us = "unknown", "unknown"
    score_kr, score_us = 0.0, 0.0
    cash_rec = 15
    try:
        regime_result = _calc_regime_from_snapshot(indices, stock_prices)
        regime_kr = regime_result.get("kr_regime", "unknown")
        regime_us = regime_result.get("us_regime", "unknown")
        score_kr = regime_result.get("kr_score", 0)
        score_us = regime_result.get("us_score", 0)
        cash_rec = regime_result.get("cash_pct", 15)
    except Exception as e:
        logger.error(f"레짐 판단 실패: {e}")
        errors += 1
    
    # 6. DB 저장
    duration = time.time() - start
    conn = _get_conn()
    try:
        conn.execute("""
            INSERT INTO market_snapshots 
            (timestamp, snapshot_type, indices_json, sector_momentum_json, 
             stock_prices_json, regime_kr, regime_us, regime_score_kr, 
             regime_score_us, cash_recommendation, collection_duration_sec, error_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            now.isoformat(),
            "hourly",
            json.dumps(indices, ensure_ascii=False),
            json.dumps(sector_mom, ensure_ascii=False),
            json.dumps(stock_prices, ensure_ascii=False),
            regime_kr, regime_us, score_kr, score_us,
            cash_rec, round(duration, 2), errors,
        ))
        conn.commit()
    finally:
        conn.close()
    
    # 7. 흐름 시그널 감지
    try:
        _detect_flow_signals(now, indices, stock_prices, sector_mom)
    except Exception as e:
        logger.error(f"흐름 시그널 감지 실패: {e}")

    outcome_count = 0
    try:
        from .news_outcome_tracker import update_news_event_outcomes
        outcome_result = update_news_event_outcomes(limit=120)
        outcome_count = outcome_result.get("updated", 0)
    except Exception as e:
        logger.error(f"뉴스 성과 추적 실패: {e}")
    
    result = {
        "timestamp": now.isoformat(),
        "indices_count": len(indices),
        "stocks_count": len(stock_prices),
        "news_archived": news_count,
        "news_outcomes_updated": outcome_count,
        "regime_kr": regime_kr,
        "regime_us": regime_us,
        "duration_sec": round(duration, 2),
        "errors": errors,
    }
    logger.info(f"스냅샷 수집 완료: {json.dumps(result, ensure_ascii=False)}")
    return result


def _archive_news(now: datetime, headlines: list, themes: dict) -> int:
    """뉴스 아카이빙 (중복 제거)"""
    conn = _get_conn()
    count = 0
    try:
        for headline in headlines:
            h = _news_hash(headline)
            try:
                conn.execute("""
                    INSERT OR IGNORE INTO news_archive (timestamp, headline, hash, theme)
                    VALUES (?, ?, ?, ?)
                """, (now.isoformat(), headline, h, "general"))
                count += 1
            except sqlite3.IntegrityError:
                pass  # 중복
        
        for theme, news_list in themes.items():
            for news in news_list[:5]:
                h = _news_hash(news)
                try:
                    conn.execute("""
                        INSERT OR IGNORE INTO news_archive (timestamp, headline, hash, theme)
                        VALUES (?, ?, ?, ?)
                    """, (now.isoformat(), news, h, theme))
                    count += 1
                except sqlite3.IntegrityError:
                    pass
        
        conn.commit()
    finally:
        conn.close()
    try:
        score_news_archive(unscored_only=True)
    except Exception as e:
        logger.error(f"뉴스 점수화 실패: {e}")
    return count


def score_news_archive(
    db_path: Path | str | None = None,
    event_db_path: Path | str = DB_PATH,
    limit: int | None = None,
    unscored_only: bool = False,
) -> dict:
    """Score archived snapshot news with the deterministic news impact engine."""
    from .news_classifier import classify_news_item
    from .news_event_store import save_news_events
    from .news_impact_engine import calculate_news_impact

    target_db = Path(db_path) if db_path else SNAPSHOT_DB
    target_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(target_db))
    conn.row_factory = sqlite3.Row
    scored_items = []
    now = datetime.now(KST).isoformat()
    try:
        _ensure_news_archive_score_columns(conn)
        where = "WHERE impact_score IS NULL" if unscored_only else ""
        limit_sql = "LIMIT ?" if limit else ""
        params = (int(limit),) if limit else ()
        rows = conn.execute(
            f"""
            SELECT id, timestamp, headline, source, theme
            FROM news_archive
            {where}
            ORDER BY timestamp DESC, id DESC
            {limit_sql}
            """,
            params,
        ).fetchall()

        for row in rows:
            classified = classify_news_item({
                "title": row["headline"],
                "summary": "",
                "source": row["source"] or "news_archive",
                "published": row["timestamp"],
            })
            impacted = calculate_news_impact(classified)
            scored_items.append(impacted)
            conn.execute(
                """
                UPDATE news_archive SET
                    theme = ?,
                    sentiment = ?,
                    relevance_score = ?,
                    primary_theme = ?,
                    secondary_themes_json = ?,
                    market_relevance_score = ?,
                    novelty_score = ?,
                    urgency_score = ?,
                    confidence_score = ?,
                    impact_score = ?,
                    historical_impact_score = ?,
                    current_market_reaction_score = ?,
                    report_priority = ?,
                    should_include_in_report = ?,
                    validation_status = ?,
                    validation_errors_json = ?,
                    affected_assets_json = ?,
                    impact_channel = ?,
                    investment_implication = ?,
                    scored_at = ?
                WHERE id = ?
                """,
                (
                    impacted.get("primary_theme") or row["theme"],
                    impacted.get("sentiment") or "",
                    _float_or_none((impacted.get("impact_score") or 0) / 100),
                    impacted.get("primary_theme") or "",
                    json.dumps(impacted.get("secondary_themes") or [], ensure_ascii=False),
                    _float_or_none(impacted.get("market_relevance_score")),
                    _float_or_none(impacted.get("novelty_score")),
                    _float_or_none(impacted.get("urgency_score")),
                    _float_or_none(impacted.get("confidence")),
                    _float_or_none(impacted.get("impact_score")),
                    _float_or_none(impacted.get("historical_impact_score")),
                    _float_or_none(impacted.get("current_market_reaction_score")),
                    impacted.get("report_priority") or "",
                    1 if impacted.get("should_include_in_report") else 0,
                    impacted.get("validation_status") or "",
                    json.dumps(impacted.get("validation_errors") or [], ensure_ascii=False),
                    json.dumps(impacted.get("affected_assets") or [], ensure_ascii=False),
                    impacted.get("impact_channel") or "",
                    impacted.get("investment_implication") or "",
                    now,
                    row["id"],
                ),
            )
        conn.commit()
    finally:
        conn.close()

    event_saved = save_news_events(scored_items, db_path=event_db_path) if scored_items else 0
    included = sum(1 for item in scored_items if item.get("should_include_in_report"))
    return {
        "scored": len(scored_items),
        "event_saved": event_saved,
        "included_candidates": included,
        "unscored_only": unscored_only,
    }


def _ensure_news_archive_score_columns(conn: sqlite3.Connection):
    existing = {row[1] for row in conn.execute("PRAGMA table_info(news_archive)").fetchall()}
    for name, ddl in NEWS_ARCHIVE_SCORE_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE news_archive ADD COLUMN {name} {ddl}")
    conn.commit()


def _float_or_none(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _calc_regime_from_snapshot(indices: dict, stock_prices: dict) -> dict:
    """스냅샷 데이터로 간이 레짐 판단"""
    from .market_regime import MarketRegime
    
    # VIX 기반 간이 판단
    vix = indices.get("VIX", {}).get("price")
    kospi_chg = indices.get("KOSPI", {}).get("change_pct", 0) or 0
    nasdaq_chg = indices.get("NASDAQ", {}).get("change_pct", 0) or 0
    
    # 한국 레짐 간이 판단
    if kospi_chg > 1:
        kr_regime, kr_score = "강세", 30 + kospi_chg * 10
    elif kospi_chg > -1:
        kr_regime, kr_score = "횡보", kospi_chg * 10
    elif kospi_chg > -2:
        kr_regime, kr_score = "조정", kospi_chg * 10
    else:
        kr_regime, kr_score = "약세", kospi_chg * 10
    
    # 미국 레짐 간이 판단
    if nasdaq_chg > 1:
        us_regime, us_score = "강세", 30 + nasdaq_chg * 10
    elif nasdaq_chg > -1:
        us_regime, us_score = "횡보", nasdaq_chg * 10
    elif nasdaq_chg > -2:
        us_regime, us_score = "조정", nasdaq_chg * 10
    else:
        us_regime, us_score = "약세", nasdaq_chg * 10
    
    # 현금 비중 권고
    avg_score = (kr_score + us_score) / 2
    if avg_score > 20:
        cash = 10
    elif avg_score > 0:
        cash = 15
    elif avg_score > -15:
        cash = 20
    else:
        cash = 30
    
    return {
        "kr_regime": kr_regime,
        "us_regime": us_regime,
        "kr_score": round(kr_score, 1),
        "us_score": round(us_score, 1),
        "cash_pct": cash,
    }


def _detect_flow_signals(now: datetime, indices: dict, stock_prices: dict, sector_mom: dict):
    """
    흐름 시그널 감지 — 이전 스냅샷과 비교하여 변화 감지
    """
    conn = _get_conn()
    try:
        # 이전 스냅샷 가져오기
        prev = conn.execute("""
            SELECT indices_json, stock_prices_json, sector_momentum_json
            FROM market_snapshots
            ORDER BY timestamp DESC LIMIT 1 OFFSET 1
        """).fetchone()
        
        if not prev:
            return  # 첫 스냅샷이면 비교 불가
        
        prev_indices = json.loads(prev["indices_json"]) if prev["indices_json"] else {}
        prev_stocks = json.loads(prev["stock_prices_json"]) if prev["stock_prices_json"] else {}
        
        signals = []
        
        # 지수 급변 감지
        for name in ["KOSPI", "KOSDAQ", "SP500", "NASDAQ"]:
            curr_price = indices.get(name, {}).get("price")
            prev_price = prev_indices.get(name, {}).get("price")
            if curr_price and prev_price and prev_price > 0:
                chg = (curr_price - prev_price) / prev_price * 100
                if abs(chg) > 1.0:  # 1시간 내 1% 이상 변동
                    direction = "상승" if chg > 0 else "하락"
                    signals.append({
                        "signal_type": "index_surge",
                        "target": name,
                        "direction": direction,
                        "strength": abs(chg),
                        "description": f"{name} 1시간 내 {chg:+.2f}% {direction}",
                    })
        
        # 종목 급변 감지
        for ticker, info in stock_prices.items():
            curr_price = info.get("price") or info.get("current_price")
            prev_info = prev_stocks.get(ticker, {})
            prev_price = prev_info.get("price") or prev_info.get("current_price")
            if curr_price and prev_price and prev_price > 0:
                chg = (curr_price - prev_price) / prev_price * 100
                if abs(chg) > 3.0:  # 1시간 내 3% 이상 변동
                    direction = "급등" if chg > 0 else "급락"
                    signals.append({
                        "signal_type": "stock_surge",
                        "target": f"{info.get('name', ticker)} ({ticker})",
                        "direction": direction,
                        "strength": abs(chg),
                        "description": f"{info.get('name', ticker)} 1시간 내 {chg:+.2f}% {direction}",
                    })
        
        # VIX 급변 감지
        vix_curr = indices.get("VIX", {}).get("price")
        vix_prev = prev_indices.get("VIX", {}).get("price")
        if vix_curr and vix_prev and vix_prev > 0:
            vix_chg = (vix_curr - vix_prev) / vix_prev * 100
            if abs(vix_chg) > 10:
                signals.append({
                    "signal_type": "vix_spike",
                    "target": "VIX",
                    "direction": "공포 확대" if vix_chg > 0 else "공포 완화",
                    "strength": abs(vix_chg),
                    "description": f"VIX {vix_chg:+.1f}% — {'공포 확대' if vix_chg > 0 else '안도 랠리 가능'}",
                })
        
        # 시그널 저장
        for sig in signals:
            conn.execute("""
                INSERT INTO flow_signals (timestamp, signal_type, target, direction, strength, description)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (now.isoformat(), sig["signal_type"], sig["target"],
                  sig["direction"], sig["strength"], sig["description"]))
        
        conn.commit()
        if signals:
            logger.info(f"흐름 시그널 {len(signals)}건 감지: {[s['description'] for s in signals]}")
    
    finally:
        conn.close()


# ── 흐름 데이터 조회 (리포트용) ──────────────────────
def get_recent_snapshots(hours: int = 24) -> list[dict]:
    """최근 N시간 스냅샷 조회"""
    conn = _get_conn()
    try:
        cutoff = (datetime.now(KST) - timedelta(hours=hours)).isoformat()
        rows = conn.execute("""
            SELECT * FROM market_snapshots 
            WHERE timestamp > ? 
            ORDER BY timestamp ASC
        """, (cutoff,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_recent_news(hours: int = 24, theme: str = None) -> list[dict]:
    """최근 N시간 뉴스 조회"""
    conn = _get_conn()
    try:
        cutoff = (datetime.now(KST) - timedelta(hours=hours)).isoformat()
        if theme:
            rows = conn.execute("""
                SELECT * FROM news_archive 
                WHERE timestamp > ? AND theme = ?
                ORDER BY timestamp DESC
            """, (cutoff, theme)).fetchall()
        else:
            rows = conn.execute("""
                SELECT * FROM news_archive 
                WHERE timestamp > ? 
                ORDER BY timestamp DESC
            """, (cutoff,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_recent_signals(hours: int = 24) -> list[dict]:
    """최근 N시간 흐름 시그널 조회"""
    conn = _get_conn()
    try:
        cutoff = (datetime.now(KST) - timedelta(hours=hours)).isoformat()
        rows = conn.execute("""
            SELECT * FROM flow_signals 
            WHERE timestamp > ? 
            ORDER BY timestamp DESC
        """, (cutoff,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def build_flow_context(hours: int = 24) -> str:
    """
    리포트 AI에게 전달할 흐름 컨텍스트 생성
    단순 현재값이 아닌, 시간에 따른 변화와 방향성을 텍스트로 정리
    """
    snapshots = get_recent_snapshots(hours)
    signals = get_recent_signals(hours)
    news = get_recent_news(hours)
    
    if not snapshots:
        return "⚠️ 축적된 스냅샷 데이터 없음 (첫 실행 또는 수집 시작 직후)"
    
    lines = []
    lines.append(f"=== 시장 흐름 분석 (최근 {hours}시간, 스냅샷 {len(snapshots)}개) ===\n")
    
    # 1. 레짐 변화 추적
    regimes_kr = [(s["timestamp"][:16], s["regime_kr"], s["regime_score_kr"]) for s in snapshots if s.get("regime_kr")]
    regimes_us = [(s["timestamp"][:16], s["regime_us"], s["regime_score_us"]) for s in snapshots if s.get("regime_us")]
    
    if regimes_kr:
        first_kr, last_kr = regimes_kr[0], regimes_kr[-1]
        lines.append(f"[한국 레짐 변화] {first_kr[1]}({first_kr[2]:+.0f}) → {last_kr[1]}({last_kr[2]:+.0f})")
    if regimes_us:
        first_us, last_us = regimes_us[0], regimes_us[-1]
        lines.append(f"[미국 레짐 변화] {first_us[1]}({first_us[2]:+.0f}) → {last_us[1]}({last_us[2]:+.0f})")
    
    # 2. 지수 흐름 (시작 → 현재)
    if len(snapshots) >= 2:
        first_indices = json.loads(snapshots[0].get("indices_json", "{}"))
        last_indices = json.loads(snapshots[-1].get("indices_json", "{}"))
        
        lines.append("\n[지수 흐름 (기간 내 변화)]")
        for name in ["KOSPI", "KOSDAQ", "SP500", "NASDAQ", "VIX", "USD_KRW"]:
            first_p = first_indices.get(name, {}).get("price")
            last_p = last_indices.get(name, {}).get("price")
            if first_p and last_p and first_p > 0:
                chg = (last_p - first_p) / first_p * 100
                direction = "↑" if chg > 0 else "↓"
                lines.append(f"  {name}: {first_p:,.0f} → {last_p:,.0f} ({direction}{abs(chg):.2f}%)")
    
    # 3. 주요 흐름 시그널
    if signals:
        lines.append(f"\n[주요 시그널 ({len(signals)}건)]")
        for sig in signals[:10]:
            ts = sig["timestamp"][11:16]
            lines.append(f"  [{ts}] {sig['description']}")
    
    # 4. 뉴스 흐름 (시간대별 주요 이슈)
    if news:
        lines.append(f"\n[뉴스 흐름 ({len(news)}건 수집)]")
        # 테마별 카운트
        theme_counts = {}
        for n in news:
            t = n.get("theme", "general")
            theme_counts[t] = theme_counts.get(t, 0) + 1
        
        for theme, count in sorted(theme_counts.items(), key=lambda x: x[1], reverse=True)[:5]:
            sample = next((n["headline"] for n in news if n.get("theme") == theme), "")
            lines.append(f"  {theme} ({count}건): {sample[:60]}...")
    
    # 5. 현금 비중 권고 추이
    cash_recs = [s["cash_recommendation"] for s in snapshots if s.get("cash_recommendation")]
    if cash_recs:
        lines.append(f"\n[현금 비중 권고 추이] {cash_recs[0]}% → {cash_recs[-1]}% (평균 {sum(cash_recs)/len(cash_recs):.0f}%)")
    
    return "\n".join(lines)


# ── 스케줄러 루프 ────────────────────────────────────
def get_collection_interval() -> int:
    """
    수집 간격 결정 (초)
    - 장중: 3600초 (1시간)
    - 장 외: 7200초 (2시간)
    - 주말: 21600초 (6시간, 뉴스만)
    """
    now = datetime.now(KST)
    weekday = now.weekday()
    
    if weekday >= 5:  # 주말
        return 21600
    elif is_kr_market_open() or is_us_market_open():
        return 3600
    else:
        return 7200


async def snapshot_collection_loop():
    """
    비동기 스냅샷 수집 루프
    bot.py의 post_init에서 asyncio.create_task로 실행
    """
    init_snapshot_db()
    logger.info("스냅샷 수집 루프 시작")
    
    # 시작 시 즉시 1회 수집
    try:
        collect_snapshot()
    except Exception as e:
        logger.error(f"초기 스냅샷 수집 실패: {e}")
    
    while True:
        interval = get_collection_interval()
        logger.info(f"다음 스냅샷 수집까지 {interval//60}분 대기")
        await asyncio.sleep(interval)
        
        try:
            result = collect_snapshot()
            logger.info(f"스냅샷 수집 완료: 에러 {result['errors']}건, 뉴스 성과 {result.get('news_outcomes_updated', 0)}건")
        except Exception as e:
            logger.error(f"스냅샷 수집 루프 오류: {e}")


# ── DB 정리 (오래된 데이터 삭제) ─────────────────────
def cleanup_old_data(keep_days: int = 30):
    """30일 이상 된 데이터 정리"""
    conn = _get_conn()
    try:
        cutoff = (datetime.now(KST) - timedelta(days=keep_days)).isoformat()
        conn.execute("DELETE FROM market_snapshots WHERE timestamp < ?", (cutoff,))
        conn.execute("DELETE FROM news_archive WHERE timestamp < ?", (cutoff,))
        conn.execute("DELETE FROM flow_signals WHERE timestamp < ?", (cutoff,))
        conn.commit()
        logger.info(f"{keep_days}일 이전 데이터 정리 완료")
    finally:
        conn.close()
