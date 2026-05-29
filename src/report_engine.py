"""
report_engine.py — 리포트 생성 엔진 (v2: 흐름 분석 통합)

개선 사항:
1. 축적된 스냅샷 데이터 기반 "연속적 흐름" 컨텍스트 전달
2. 시장 레짐 판단 결과를 프롬프트에 포함
3. 종목별 진입 적정도(RSI/BB/거래량) 필터 적용
4. "모멘텀이 강하다 = 지금 사야 한다"는 오류 방지 지시문 추가
5. 과매수 종목 추격매수 금지 규칙 명시
"""
import json
import logging
import math
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import numpy as np

from .config import (
    KST, MODEL_REPORT,
    GEMINI_API_KEY, GEMINI_MODEL_REPORT, GEMINI_REPORT_MAX_OUTPUT_TOKENS,
)
from .ai_client import generate_gemini_report_text
from .data_market import (
    fetch_indices, fetch_stock_prices, fetch_sector_momentum,
    get_regular_history, is_valid_number
)
from .data_news import (
    fetch_all_news, build_stock_news_context,
    build_theme_context, get_market_headlines, THEME_KEYWORDS
)
from .data_calendar import build_event_context
from .data_yahoo import get_multi_stock_insights, format_insights_for_prompt
from .calendar_verifier import build_event_display_list, build_event_sections, infer_report_type, report_policy, verify_calendar_events
from .data_quality_engine import build_data_quality_lookup, build_data_quality_table
from .market_regime_engine import calculate_market_regime
from .market_session import annotate_price_session, build_market_session_status
from .news_classifier import classify_news_items
from .news_event_store import save_news_events
from .news_event_study import find_similar_event_summary
from .news_impact_engine import calculate_news_impact
from .news_report_selector import select_report_news
from .news_translator import translate_report_news_items
from .recommendation_safety import apply_recommendation_safety_controls, calc_atr

logger = logging.getLogger(__name__)


IMPORTANT_NEWS_KEYWORDS = (
    "fed", "fomc", "rate", "inflation", "cpi", "ppi", "tariff", "export",
    "earnings", "guidance", "ai", "gpu", "hbm", "semiconductor", "chip",
    "data center", "nvidia", "tsmc", "samsung", "sk hynix", "defense",
    "금리", "연준", "물가", "인플레이션", "관세", "수출", "실적", "가이던스",
    "반도체", "엔비디아", "삼성전자", "하이닉스", "데이터센터", "전력",
    "방산", "미중", "환율", "유가",
)

NEWS_IMPORTANCE_REASON_BY_THEME = {
    "반도체": "한국/미국 기술주와 HBM·장비주 수급에 직접 연결됩니다.",
    "AI": "AI 인프라 투자와 빅테크 밸류에이션 판단에 영향을 줍니다.",
    "피지컬AI": "로봇·자율주행·자동화 테마의 중기 모멘텀을 확인할 수 있습니다.",
    "전력인프라": "데이터센터 전력 수요와 전력기기 수주 기대에 연결됩니다.",
    "방산": "국내 방산주 수주·정책 모멘텀에 영향을 줄 수 있습니다.",
    "미중무역": "관세·수출 규제 리스크가 반도체와 글로벌 제조업에 번질 수 있습니다.",
    "금리": "할인율과 성장주 멀티플, 환율 방향을 함께 움직이는 핵심 변수입니다.",
    "에너지": "유가와 인플레이션, 운송·화학·정유 업종 수익성에 연결됩니다.",
    "자동차": "전기차·자율주행 밸류체인과 국내 완성차/부품주에 영향을 줍니다.",
    "거시": "지수 방향, 환율, 위험자산 선호를 판단하는 상위 변수입니다.",
}


def collect_realtime_data() -> dict:
    """모든 실시간 데이터를 병렬로 수집"""
    logger.info("실시간 데이터 병렬 수집 시작...")
    results = {}

    def _fetch_market():
        return {
            "indices": fetch_indices(),
            "stock_prices": fetch_stock_prices(),
            "sector_mom": fetch_sector_momentum(),
        }

    def _fetch_news():
        return {"all_news": fetch_all_news(max_per_feed=12)}

    def _fetch_calendar():
        return {"calendar": build_event_context()}

    def _fetch_yahoo():
        key_tickers = [
            ("005930.KS", "KR"), ("000660.KS", "KR"),
            ("373220.KS", "KR"), ("012450.KS", "KR"), ("035420.KS", "KR"),
            ("NVDA", "US"), ("AMD", "US"), ("MSFT", "US"),
            ("PLTR", "US"), ("AMAT", "US"),
        ]
        insights = get_multi_stock_insights(key_tickers)
        return {"yahoo_insights": insights, "yahoo_text": format_insights_for_prompt(insights)}

    def _fetch_flow():
        """축적된 흐름 데이터 조회"""
        try:
            from .flow_analyzer import generate_flow_summary
            return {"flow_summary": generate_flow_summary(hours=24)}
        except Exception as e:
            logger.warning(f"흐름 분석 실패 (데이터 축적 중일 수 있음): {e}")
            return {"flow_summary": ""}

    def _fetch_memory_context():
        """계층적 시장 기억 컨텍스트 조회 (매일+매주+매달)"""
        try:
            from .market_memory import get_memory_context_for_report
            return {"memory_context": get_memory_context_for_report()}
        except Exception as e:
            logger.warning(f"시장 기억 조회 실패: {e}")
            return {"memory_context": ""}

    def _fetch_historical_news_context():
        """최근 raw 뉴스와 일간 요약에 남은 이전 중요 뉴스 조회"""
        try:
            return {"historical_news_context": get_historical_news_context(days=7, max_items=14)}
        except Exception as e:
            logger.warning(f"이전 중요 뉴스 조회 실패: {e}")
            return {"historical_news_context": ""}

    def _fetch_entry_signals():
        """종목별 진입 적정도 계산"""
        try:
            from .entry_filter import calc_entry_filter, EntrySignal
            
            # 주요 관심 종목
            targets = {
                "000660.KS": "SK하이닉스",
                "005930.KS": "삼성전자",
                "267260.KS": "HD현대일렉트릭",
                "009150.KS": "삼성전기",
                "079550.KS": "LIG넥스원",
                "NVDA": "NVIDIA",
                "AMD": "AMD",
                "INTC": "Intel",
                "MU": "Micron",
                "GEV": "GE Vernova",
                "GLW": "Corning",
                "PLTR": "Palantir",
                "AMAT": "AMAT",
            }
            
            entry_results = {}
            for ticker, name in targets.items():
                try:
                    hist = get_regular_history(
                        ticker,
                        period="3mo",
                        required_columns=("Close", "High", "Low", "Volume"),
                    )
                    if len(hist) < 20:
                        continue
                    
                    prices = hist["Close"].tolist()
                    highs = hist["High"].tolist()
                    lows = hist["Low"].tolist()
                    volumes = hist["Volume"].tolist()
                    atr_14 = calc_atr(highs, lows, prices)
                    support_20d = min(lows[-20:]) if len(lows) >= 20 else None
                    resistance_20d = max(highs[-20:]) if len(highs) >= 20 else None
                    
                    result = calc_entry_filter(
                        prices=prices,
                        highs=highs,
                        lows=lows,
                        volumes=volumes,
                    )
                    
                    signal_emoji = {
                        EntrySignal.GOOD: "🟢",
                        EntrySignal.WAIT: "🟡",
                        EntrySignal.AVOID: "🔴",
                        EntrySignal.OVERSOLD: "🔵",
                    }
                    
                    entry_results[ticker] = {
                        "name": name,
                        "signal": result.signal.value,
                        "emoji": signal_emoji.get(result.signal, "⚪"),
                        "score": result.score,
                        "current_price": prices[-1],
                        "rsi": result.rsi,
                        "bb_position": result.bb_position,
                        "atr_14": atr_14,
                        "support_20d": support_20d,
                        "resistance_20d": resistance_20d,
                        "reasons": result.reasons[:3],
                        "suggested_entry": result.suggested_entry,
                    }
                except Exception as e:
                    logger.debug(f"진입 필터 계산 실패 [{ticker}]: {e}")
            
            return {"entry_signals": entry_results}
        except Exception as e:
            logger.warning(f"진입 시그널 계산 전체 실패: {e}")
            return {"entry_signals": {}}

    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {
            ex.submit(_fetch_market): "market",
            ex.submit(_fetch_news): "news",
            ex.submit(_fetch_calendar): "calendar",
            ex.submit(_fetch_yahoo): "yahoo",
            ex.submit(_fetch_flow): "flow",
            ex.submit(_fetch_entry_signals): "entry",
            ex.submit(_fetch_memory_context): "memory",
            ex.submit(_fetch_historical_news_context): "historical_news",
        }
        for fut in as_completed(futures, timeout=180):
            key = futures[fut]
            try:
                results[key] = fut.result()
                logger.info(f"  [{key}] 수집 완료")
            except Exception as e:
                logger.error(f"  [{key}] 수집 실패: {e}")
                results[key] = {}

    market = results.get("market", {})
    indices = market.get("indices", {})
    stock_prices = market.get("stock_prices", {})
    sector_mom = market.get("sector_mom", {})

    all_news = results.get("news", {}).get("all_news", [])
    tickers = list(stock_prices.keys())
    try:
        stock_news = build_stock_news_context(all_news, tickers)
        theme_news = build_theme_context(all_news)
        headlines = get_market_headlines(all_news, 10)
        detailed_news = _build_detailed_news_items(all_news, max_items=24)
    except Exception:
        stock_news, theme_news, headlines, detailed_news = {}, {}, [], []

    calendar = results.get("calendar", {}).get("calendar", {"economic_events": [], "earnings": []})
    yahoo_data = results.get("yahoo", {})
    flow_data = results.get("flow", {})
    entry_data = results.get("entry", {})
    memory_data = results.get("memory", {})
    historical_news_data = results.get("historical_news", {})

    ctx = {
        "indices": indices,
        "stock_prices": stock_prices,
        "sector_mom": sector_mom,
        "stock_news": stock_news,
        "theme_news": theme_news,
        "headlines": headlines,
        "detailed_news": detailed_news,
        "calendar": calendar,
        "yahoo_insights": yahoo_data.get("yahoo_insights", {}),
        "yahoo_text": yahoo_data.get("yahoo_text", ""),
        "flow_summary": flow_data.get("flow_summary", ""),
        "memory_context": memory_data.get("memory_context", ""),
        "historical_news_context": historical_news_data.get("historical_news_context", ""),
        "entry_signals": entry_data.get("entry_signals", {}),
        "collected_at": datetime.now(KST).isoformat(),
    }
    return enrich_execution_context(ctx, persist_news=True)


def enrich_execution_context(ctx: dict, persist_news: bool = False) -> dict:
    """Attach deterministic execution-layer tables to a collected context."""
    ctx = dict(ctx or {})
    ctx.setdefault("calendar", {"economic_events": [], "earnings": []})
    ctx.setdefault("detailed_news", [])
    now_kst = _context_time(ctx)
    ctx["market_session_status"] = ctx.get("market_session_status") or build_market_session_status(now_kst)
    ctx["stock_prices"] = _annotate_stock_price_sessions(ctx.get("stock_prices") or {}, ctx["market_session_status"], now_kst)
    ctx["report_type"] = ctx.get("report_type") or infer_report_type(now_kst)
    ctx["report_policy"] = report_policy(ctx["report_type"])

    event_tables = verify_calendar_events(ctx.get("calendar"), now_kst=now_kst)
    ctx["verified_events"] = ctx.get("verified_events") or event_tables["verified_events"]
    ctx["tentative_events"] = ctx.get("tentative_events") or event_tables["tentative_events"]
    ctx["market_regime"] = ctx.get("market_regime") or calculate_market_regime(ctx)
    ctx["data_quality"] = ctx.get("data_quality") or build_data_quality_table(ctx, now_kst=now_kst)
    ctx["data_quality_lookup"] = ctx.get("data_quality_lookup") or build_data_quality_lookup(ctx, now_kst=now_kst)
    classified_news = []
    for item in classify_news_items(ctx.get("detailed_news") or [], context=ctx):
        historical_summary = _historical_news_reaction_for_item(item)
        classified_news.append(calculate_news_impact(item, context=ctx, historical_summary=historical_summary))
    ctx["news_impact_table"] = classified_news
    ctx["schedule_news"] = [item for item in classified_news if item.get("primary_theme") == "market_schedule"]
    ctx["detailed_news"] = select_report_news(classified_news, max_items=8)
    ctx["detailed_news"] = translate_report_news_items(ctx["detailed_news"])
    if persist_news and classified_news:
        try:
            save_news_events(classified_news)
        except Exception as exc:
            logger.warning("뉴스 이벤트 저장 실패: %s", exc)
    return ctx


def _context_time(ctx: dict) -> datetime:
    raw = ctx.get("collected_at")
    if raw:
        try:
            parsed = datetime.fromisoformat(str(raw))
            if parsed.tzinfo is None:
                return KST.localize(parsed)
            return parsed.astimezone(KST)
        except ValueError:
            pass
    return datetime.now(KST)


def _annotate_stock_price_sessions(stock_prices: dict, sessions: dict, now_kst: datetime) -> dict:
    annotated = {}
    for ticker, info in (stock_prices or {}).items():
        payload = dict(info or {})
        market = str(payload.get("market") or ("KR" if str(ticker).isdigit() else "US")).upper()
        annotated[ticker] = annotate_price_session(payload, market, sessions.get(market), now_kst=now_kst)
    return annotated


def _normal_headline(headline: str) -> str:
    return " ".join(str(headline or "").lower().split())


def _news_importance_score(headline: str, theme: str = "", relevance: float = 0.5, theme_count: int = 0) -> float:
    text = f"{headline} {theme}".lower()
    keyword_hits = sum(1 for keyword in IMPORTANT_NEWS_KEYWORDS if keyword.lower() in text)
    return float(relevance or 0) + min(keyword_hits, 4) * 0.2 + min(theme_count, 10) * 0.03


def _detect_news_themes(item: dict) -> list[str]:
    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    themes = []
    for theme, keywords in THEME_KEYWORDS.items():
        if any(keyword.lower() in text for keyword in keywords):
            themes.append(theme)
    return themes[:4]


def _news_item_score(item: dict) -> float:
    title = item.get("title", "")
    summary = item.get("summary", "")
    themes = _detect_news_themes(item)
    text = f"{title} {summary} {' '.join(themes)}".lower()
    keyword_hits = sum(1 for keyword in IMPORTANT_NEWS_KEYWORDS if keyword.lower() in text)
    source_bonus = 0.15 if item.get("feed_name") in {"Reuters_Biz", "CNBC_Top", "MarketWatch", "Yahoo_Finance"} else 0.05
    link_bonus = 0.1 if item.get("link") else 0
    return len(themes) * 0.35 + min(keyword_hits, 5) * 0.2 + source_bonus + link_bonus


def _news_importance_reason(item: dict, themes: list[str]) -> str:
    if themes:
        theme = themes[0]
        reason = NEWS_IMPORTANCE_REASON_BY_THEME.get(theme)
        if reason:
            return f"{theme} 이슈입니다. {reason}"

    text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
    hit = next((kw for kw in IMPORTANT_NEWS_KEYWORDS if kw.lower() in text), "")
    if hit:
        return f"'{hit}' 키워드가 포함되어 시장 가격 변수와 연결될 수 있습니다."
    return "상위 최신 헤드라인으로 시장 심리와 당일 리스크 점검에 필요합니다."


def _build_detailed_news_items(news_list: list[dict], max_items: int = 8) -> list[dict]:
    seen = set()
    ranked = sorted(news_list, key=_news_item_score, reverse=True)
    items = []
    for news in ranked:
        title = " ".join(str(news.get("title") or "").split())
        if not title:
            continue
        key = _normal_headline(title)
        if key in seen:
            continue
        seen.add(key)
        themes = _detect_news_themes(news)
        pub_dt = news.get("pub_dt")
        if hasattr(pub_dt, "strftime"):
            published = pub_dt.strftime("%m/%d %H:%M")
        else:
            published = ""
        items.append({
            "title": title,
            "summary": " ".join(str(news.get("summary") or "").split())[:180],
            "link": news.get("link") or "",
            "source": news.get("feed_name") or news.get("source") or "",
            "published": published,
            "themes": themes,
            "importance": round(_news_item_score(news), 2),
            "why": _news_importance_reason(news, themes),
        })
        if len(items) >= max_items:
            break
    return items


def _load_recent_archived_news(days: int, now: datetime) -> list[dict]:
    from .snapshot_collector import SNAPSHOT_DB

    cutoff = (now - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(str(SNAPSHOT_DB))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT timestamp, headline, source, theme, relevance_score
            FROM news_archive
            WHERE timestamp >= ?
            ORDER BY relevance_score DESC, timestamp DESC
            LIMIT 80
            """,
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    items = []
    for row in rows:
        headline = row["headline"]
        theme = row["theme"] or "general"
        items.append({
            "date": row["timestamp"][:10],
            "headline": headline,
            "theme": theme,
            "source": row["source"] or "",
            "score": _news_importance_score(headline, theme, row["relevance_score"] or 0.5),
        })
    return items


def _load_daily_summary_news(days: int, now: datetime) -> list[dict]:
    from .market_memory import MEMORY_DB

    cutoff = (now - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = sqlite3.connect(str(MEMORY_DB))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT date, news_themes_json
            FROM daily_summaries
            WHERE date >= ?
            ORDER BY date DESC
            LIMIT 14
            """,
            (cutoff,),
        ).fetchall()
    finally:
        conn.close()

    items = []
    for row in rows:
        try:
            themes = json.loads(row["news_themes_json"] or "{}")
        except json.JSONDecodeError:
            continue
        for theme, payload in themes.items():
            count = int(payload.get("count", 0) or 0)
            for headline in (payload.get("headlines") or [])[:3]:
                items.append({
                    "date": row["date"],
                    "headline": headline,
                    "theme": theme,
                    "source": "daily_summary",
                    "score": _news_importance_score(headline, theme, 0.65, count),
                })
    return items


def _format_historical_news_items(items: list[dict], max_items: int) -> str:
    seen = set()
    deduped = []
    for item in sorted(items, key=lambda x: (x.get("score", 0), x.get("date", "")), reverse=True):
        key = _normal_headline(item.get("headline", ""))
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
        if len(deduped) >= max_items:
            break

    if not deduped:
        return "이전 중요 뉴스 없음"

    lines = ["━━━ 이전 중요 뉴스 ━━━"]
    for item in deduped:
        source = f" · {item['source']}" if item.get("source") and item.get("source") != "daily_summary" else ""
        lines.append(f"• [{item['date']}][{item.get('theme') or 'general'}{source}] {item['headline']}")
    return "\n".join(lines)


def get_historical_news_context(days: int = 7, max_items: int = 14, now: datetime | None = None) -> str:
    """브리핑용 이전 중요 뉴스 컨텍스트. raw 아카이브와 일간 요약 압축본을 함께 사용한다."""
    now = now or datetime.now(KST)
    if now.tzinfo is None:
        now = KST.localize(now)
    items = []
    items.extend(_load_recent_archived_news(days, now))
    items.extend(_load_daily_summary_news(days, now))
    return _format_historical_news_items(items, max_items)


def _fmt_index(data: dict, name: str) -> str:
    d = data.get(name, {})
    if not d or d.get("price") is None:
        return f"{name}: 데이터 없음"
    price = d["price"]
    chg = d.get("change_pct", 0) or 0
    return f"{name}: {price:,} ({chg:+.2f}%)"


def _fmt_sector(sector_mom: dict) -> str:
    if not sector_mom:
        return "섹터 데이터 없음"
    lines_kr, lines_us = [], []
    for sector, d in sorted(sector_mom.items(), key=lambda x: x[1].get("ret_5d", 0), reverse=True):
        r5 = d.get("ret_5d", 0)
        r20 = d.get("ret_20d", 0)
        tag = d.get("momentum", "")
        emoji = "🔥" if tag == "HOT" else ("🌡" if tag == "WARMING" else "❄️")
        line = f"{emoji} {sector}: 5일 {r5:+.1f}% / 20일 {r20:+.1f}%"
        if "(KR)" in sector:
            lines_kr.append(line)
        else:
            lines_us.append(line)
    result = []
    if lines_kr:
        result.append("[한국 섹터]")
        result.extend(lines_kr)
    if lines_us:
        result.append("[미국 섹터]")
        result.extend(lines_us)
    return "\n".join(result)


def _fmt_detailed_news(news_items: list[dict]) -> str:
    if not news_items:
        return "상세 뉴스 데이터 없음"

    lines = []
    for idx, item in enumerate(news_items[:8], 1):
        title = item.get("title", "")
        source = item.get("source") or "unknown"
        published = item.get("published") or ""
        themes = ", ".join([item.get("primary_theme", "")] + (item.get("secondary_themes") or []))
        themes = themes.strip(", ") or ", ".join(item.get("themes") or []) or "general"
        summary = item.get("summary") or ""
        why = item.get("investment_implication") or item.get("why") or ""
        link = item.get("link") or ""
        lines.append(f"{idx}. {title}")
        lines.append(f"   source={source} time={published} theme={themes} impact={item.get('impact_score', 'N/A')}")
        if summary:
            lines.append(f"   summary={summary}")
        if why:
            lines.append(f"   investment_implication={why}")
        if item.get("impact_channel"):
            lines.append(f"   channel={item['impact_channel']}")
        direct = _fmt_impact_assets(item.get("affected_assets") or [], "direct")
        indirect = _fmt_impact_assets(item.get("affected_assets") or [], "indirect")
        if direct:
            lines.append(f"   direct_impact={direct}")
        if indirect:
            lines.append(f"   indirect_impact={indirect}")
        hist = item.get("historical_reaction") or {}
        if hist:
            lines.append(
                "   historical_reaction=count:{count} ar1d:{ar1d:+.2f}% hit:{hit:.0f}%".format(
                    count=hist.get("similar_event_count", 0),
                    ar1d=float(hist.get("median_abnormal_return_1d", 0) or 0),
                    hit=float(hist.get("directional_hit_rate", 0) or 0) * 100,
                )
            )
        if link:
            lines.append(f"   link={link}")
    return "\n".join(lines)


def _historical_news_reaction_for_item(item: dict) -> dict | None:
    assets = item.get("affected_assets") or []
    if not assets:
        return None

    selected = next(
        (asset for asset in assets if asset.get("impact_scope") == "direct" and asset.get("direction") != "uncertain"),
        None,
    )
    selected = selected or next((asset for asset in assets if asset.get("direction") != "uncertain"), None)
    selected = selected or assets[0]
    try:
        return find_similar_event_summary(
            primary_theme=item.get("primary_theme") or "",
            asset=selected.get("asset") or "",
            expected_direction=selected.get("direction") or "uncertain",
        )
    except Exception as exc:
        logger.warning("과거 뉴스 반응 조회 실패: %s", exc)
        return None


def _fmt_impact_assets(assets: list[dict], scope: str) -> str:
    labels = []
    for asset in assets:
        if asset.get("impact_scope") != scope:
            continue
        direction = asset.get("direction", "uncertain")
        symbol = {"positive": "+", "negative": "-", "neutral": "0", "uncertain": "?"}.get(direction, "?")
        labels.append(f"{asset.get('asset')}({symbol})")
    return ", ".join(labels[:5])


def _fmt_json_table(value) -> str:
    if not value:
        return "[]"
    return json.dumps(value, ensure_ascii=False, default=str, indent=2)[:7000]


def _fmt_stock_universe(stock_prices: dict, stock_news: dict) -> str:
    lines = []
    for ticker, info in stock_prices.items():
        price = info.get("price") or info.get("current_price")
        chg = info.get("change_pct")
        news = stock_news.get(ticker, "관련 뉴스 없음")
        cs = "₩" if info.get("market") == "KR" else "$"
        price_s = f"{cs}{price:,}" if is_valid_number(price) else "N/A"
        chg_s = f"({chg:+.2f}%)" if chg is not None else ""
        lines.append(f"[{ticker}] {info['name']} | {info['sector']} | {price_s} {chg_s}\n  뉴스: {news}")
    return "\n".join(lines)


def _fmt_entry_signals(entry_signals: dict) -> str:
    """진입 적정도 포맷팅"""
    if not entry_signals:
        return "진입 시그널 데이터 없음"
    lines = []
    for ticker, data in sorted(entry_signals.items(), key=lambda x: x[1]["score"], reverse=True):
        emoji = data["emoji"]
        name = data["name"]
        signal = data["signal"]
        score = data["score"]
        rsi = data["rsi"]
        bb = data["bb_position"]
        current_price = data.get("current_price")
        price_s = _format_prompt_price(current_price, ticker)
        reasons = " | ".join(data["reasons"][:2])
        suggested = f" → 제안진입가: {data['suggested_entry']:,.0f}" if data.get("suggested_entry") else ""
        lines.append(f"{emoji} [{ticker}] {name}: {signal}({score:.0f}점) | 종가 {price_s} | RSI {rsi:.0f} | BB {bb:.0f}%{suggested}")
        lines.append(f"   근거: {reasons}")
    return "\n".join(lines)


def _empty_report_context(extra_context: str = "") -> dict:
    return {
        "indices": {},
        "stock_prices": {},
        "sector_mom": {},
        "stock_news": {},
        "theme_news": {},
        "headlines": [],
        "detailed_news": [],
        "calendar": {"economic_events": [], "earnings": []},
        "yahoo_insights": {},
        "yahoo_text": "",
        "flow_summary": "",
        "memory_context": "",
        "historical_news_context": "",
        "entry_signals": {},
        "report_type": "US_POST_CLOSE",
        "report_policy": {},
        "verified_events": [],
        "tentative_events": [],
        "data_quality": [],
        "market_regime": {},
        "collected_at": datetime.now(KST).isoformat(),
        "extra_context": extra_context,
    }


def _format_prompt_price(value, ticker: str) -> str:
    if not is_valid_number(value):
        return "N/A"
    price = float(value)
    if ticker.endswith((".KS", ".KQ")) or ticker.isdigit():
        return f"₩{price:,.0f}"
    return f"${price:,.2f}" if price < 100 else f"${price:,.0f}"


def _fill_missing_recommendation_prices(report: dict, ctx: dict) -> None:
    """Backward-compatible wrapper for the deterministic safety pipeline."""
    apply_recommendation_safety_controls(report, ctx)


REPORT_OUTPUT_SCHEMA = {
    "report_date": "",
    "market_summary": {
        "overall_sentiment": "강세|약세|중립",
        "sentiment_score": 0,
        "key_theme": "",
        "macro_analysis": "",
        "sector_rotation": "",
        "market_regime_kr": "",
        "market_regime_us": "",
        "flow_analysis": "",
        "data_quality_notes": "",
        "base_case": "",
        "bear_case": "",
        "risk_factors": [],
    },
    "recommendations": [
        {
            "rank": 1,
            "name": "",
            "ticker": "",
            "market": "KR|US",
            "sector": "",
            "currency": "KRW|USD",
            "style": "장기|스윙|단타",
            "holding_period": "",
            "current_price": None,
            "entry_price": None,
            "entry_signal": "적정|대기|과열|반등",
            "entry_condition": "",
            "target_price_1": None,
            "target_price_2": None,
            "stop_loss": None,
            "upside_pct": None,
            "position_size_pct": None,
            "investment_rationale": [],
            "risk_factors": [],
            "momentum_quality": 0,
            "confidence_score": 0,
            "evidence_ids": [],
            "data_basis": [],
            "invalidation_condition": "",
            "technical_status": "",
        }
    ],
    "waiting_list": [
        {
            "name": "",
            "ticker": "",
            "reason": "",
            "target_entry": None,
            "condition": "",
        }
    ],
    "portfolio_strategy": {
        "cash_reserve_pct": 0,
        "regime_based_allocation": "",
        "long_term_allocation": "",
        "swing_strategy": "",
        "daytrading_focus": "",
        "overall_advice": "",
    },
    "watchlist": [],
    "rejected_candidates": [],
}


def build_report_input_payload(ctx: dict, today_str: str, extra_context: str = "") -> dict:
    """Build one structured JSON input object for the briefing model."""
    ctx = enrich_execution_context({**_empty_report_context(), **(ctx or {})})
    extra = extra_context or ctx.get("extra_context", "")
    payload = {
        "metadata": {
            "report_date": today_str,
            "timezone": "Asia/Seoul",
            "prompt_version": "v4_structured_json_input",
            "generated_at_kst": datetime.now(KST).isoformat(),
            "collected_at": ctx.get("collected_at"),
            "report_type": ctx.get("report_type"),
            "execution_policy": (ctx.get("report_policy") or {}).get("execution_policy", "unknown"),
            "response_format": "strict_json",
        },
        "instructions": {
            "role": "investment_briefing_writer",
            "use_only_provided_data": True,
            "separate_facts_inferences_actions": True,
            "do_not_generate_execution_numbers": True,
            "execution_numbers_finalized_by_rule_engine": [
                "current_price",
                "entry_price",
                "target_price_1",
                "target_price_2",
                "stop_loss",
                "upside_pct",
                "position_size_pct",
                "risk_reward",
                "action_status",
                "is_executable",
            ],
            "recommendation_rules": [
                "근거가 약한 종목은 recommendations가 아니라 waiting_list로 보낸다.",
                "진입 적정도가 과열인 종목은 추천하지 않는다.",
                "시장 레짐이 약하면 해당 시장 추천 수를 줄인다.",
                "10개 종목을 억지로 채우지 않는다.",
                "verified=false 이벤트를 확정 일정처럼 쓰지 않는다.",
                "뉴스는 primary_theme, impact_channel, affected_assets, historical_reaction을 함께 해석한다.",
            ],
        },
        "deterministic_layer": {
            "report_policy": ctx.get("report_policy") or {},
            "market_session_status": ctx.get("market_session_status") or {},
            "market_regime": ctx.get("market_regime") or {},
            "verified_events": ctx.get("verified_events") or [],
            "tentative_events": ctx.get("tentative_events") or [],
            "event_sections": build_event_sections(
                ctx.get("verified_events") or [],
                ctx.get("tentative_events") or [],
                (ctx.get("detailed_news") or []) + (ctx.get("schedule_news") or []),
                ctx.get("market_session_status") or {},
                now_kst=_context_time(ctx),
            ),
            "data_quality": ctx.get("data_quality") or [],
        },
        "market_data": {
            "indices": ctx.get("indices") or {},
            "sector_momentum": ctx.get("sector_mom") or {},
            "stock_universe": _stock_universe_payload(
                ctx.get("stock_prices") or {},
                ctx.get("stock_news") or {},
            ),
        },
        "technical_entry": {
            "entry_signals": ctx.get("entry_signals") or {},
            "signal_legend": {
                "적정": "기술적 진입 조건이 비교적 양호",
                "대기": "조건 확인 또는 더 나은 가격 필요",
                "과열": "추격 매수 금지",
                "반등": "과매도 반등 확인 필요",
            },
        },
        "news": {
            "headline_limit": 8,
            "headlines": list((ctx.get("headlines") or [])[:8]),
            "selected_report_news": _news_payload(ctx.get("detailed_news") or []),
            "news_impact_table": _news_payload((ctx.get("news_impact_table") or [])[:24]),
            "theme_news": _theme_news_payload(ctx.get("theme_news") or {}),
        },
        "market_context": {
            "flow_summary": ctx.get("flow_summary") or "흐름 데이터 축적 중",
            "memory_context": ctx.get("memory_context") or "시장 기억 데이터 축적 중",
            "historical_news": {
                "lookback_days": 7,
                "raw_text": ctx.get("historical_news_context") or "이전 중요 뉴스 없음",
                "items": _parse_historical_news_context(ctx.get("historical_news_context") or ""),
            },
            "yahoo_insights": ctx.get("yahoo_insights") or {},
            "yahoo_text": ctx.get("yahoo_text") or "",
            "extra_context": extra,
        },
        "output_schema": REPORT_OUTPUT_SCHEMA,
    }
    return _json_safe(payload)


def _stock_universe_payload(stock_prices: dict, stock_news: dict) -> dict:
    payload = {}
    for ticker, info in (stock_prices or {}).items():
        info = info or {}
        payload[str(ticker)] = {
            "ticker": str(ticker),
            "name": info.get("name"),
            "market": info.get("market"),
            "sector": info.get("sector"),
            "currency": "KRW" if info.get("market") == "KR" else "USD",
            "price": info.get("price") or info.get("current_price"),
            "change_pct": info.get("change_pct"),
            "source": info.get("source"),
            "session_status": info.get("session_status"),
            "last_trade_time": info.get("last_trade_time"),
            "staleness_hours": info.get("staleness_hours"),
            "staleness_reason": info.get("staleness_reason"),
            "regular_open_confirmation_required": info.get("regular_open_confirmation_required"),
            "related_news": stock_news.get(ticker, "관련 뉴스 없음") if stock_news else "관련 뉴스 없음",
        }
    return payload


def _news_payload(news_items: list[dict]) -> list[dict]:
    payload = []
    for item in news_items or []:
        payload.append({
            "news_id": item.get("news_id"),
            "title": item.get("title") or item.get("headline"),
            "summary": item.get("summary") or "",
            "translated_title": item.get("translated_title") or "",
            "translated_summary": item.get("translated_summary") or "",
            "translation_provider": item.get("translation_provider") or "",
            "source": item.get("source") or "",
            "published": item.get("published") or item.get("published_at_kst") or "",
            "url": item.get("url") or item.get("link") or "",
            "primary_theme": item.get("primary_theme") or _first(item.get("themes")),
            "secondary_themes": item.get("secondary_themes") or [],
            "themes": item.get("themes") or [],
            "market_relevance_score": item.get("market_relevance_score"),
            "impact_score": item.get("impact_score"),
            "novelty_score": item.get("novelty_score"),
            "urgency_score": item.get("urgency_score"),
            "confidence": item.get("confidence"),
            "directional_confidence_score": item.get("directional_confidence_score"),
            "trading_signal_strength": item.get("trading_signal_strength"),
            "report_priority": item.get("report_priority"),
            "validation_status": item.get("validation_status"),
            "validation_errors": item.get("validation_errors") or [],
            "investment_implication": item.get("investment_implication") or item.get("why") or "",
            "impact_channel": item.get("impact_channel") or "",
            "current_market_reaction": item.get("current_market_reaction") or "",
            "affected_assets": item.get("affected_assets") or [],
            "oil_direction": item.get("oil_direction"),
            "historical_reaction": item.get("historical_reaction") or {},
            "why_not_other_themes": item.get("why_not_other_themes") or [],
        })
    return payload


def _theme_news_payload(theme_news: dict) -> dict:
    return {
        str(theme): list((items or [])[:5])
        for theme, items in (theme_news or {}).items()
    }


def _parse_historical_news_context(raw: str) -> list[dict]:
    items = []
    for line in str(raw or "").splitlines():
        line = line.strip()
        if not line.startswith("•"):
            continue
        match = re.match(r"^•\s+\[([^\]]+)\]\[([^\]]+)\]\s+(.+)$", line)
        if not match:
            items.append({"raw": line.lstrip("• ").strip()})
            continue
        date, theme_source, headline = match.groups()
        if " · " in theme_source:
            theme, source = theme_source.split(" · ", 1)
        else:
            theme, source = theme_source, ""
        items.append({
            "date": date,
            "theme": theme,
            "source": source,
            "headline": headline,
        })
    return items


def _first(value) -> str:
    if isinstance(value, (list, tuple)) and value:
        return value[0]
    return ""


def _json_safe(value):
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        number = float(value)
        return number if math.isfinite(number) else None
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if value is None or isinstance(value, (str, int, bool)):
        return value
    return str(value)


# ── 강화된 시스템 프롬프트 ────────────────────────────
SYSTEM_PROMPT_V2 = """당신은 투자 브리핑 작성자입니다.
한국(KOSPI/KOSDAQ)과 미국(NYSE/NASDAQ) 시장을 전문으로 분석합니다.
당신은 매수가, 목표가, 손절가, 비중, 실행 가능 여부를 새로 생성할 수 없습니다.
이 값들은 deterministic price/risk gate engine이 최종 산출합니다.

━━━ 핵심 분석 원칙 ━━━

1. 데이터 우선: 제공된 지수, 종목 가격, 뉴스, 이벤트, 진입 시그널, 시장 기억만 근거로 사용
2. 시장 분리: 한국/미국, 지수/섹터/개별종목을 분리해 판단하고 임의로 동조화하지 않음
3. 다중 검증: 매크로 + 시장 레짐 + 섹터 로테이션 + 뉴스 촉매 + 기술적 진입 조건을 함께 확인
4. 불확실성 명시: 데이터가 부족하거나 엇갈리면 "확신 낮음", "조건부", "대기"로 표현
5. 실행 가능성: 실행 가격과 비중은 새로 만들지 말고, 근거가 약하면 null로 둠
6. 근거 투명성: 투자 근거는 실제 수집 데이터/뉴스/기술 지표에서 온 것만 사용
7. 응답은 반드시 JSON 형식이며, JSON 외 텍스트를 출력하지 않음
8. 실행 가격/목표가/손절가/비중/action_status/is_executable은 후단 rule-based engine이 최종 산출하므로 지어내지 말 것
9. verified=false 이벤트를 확정 일정처럼 쓰지 말 것
10. 장중 리포트에서는 종가 확인 전 신규 매수를 보수적으로 표현할 것

━━━ 분석 절차 (반드시 이 순서로 사고하되, 출력은 JSON만) ━━━

1. 데이터 품질 점검
- KIS/Yahoo 등 가격 출처가 섞일 수 있으므로 국장 지수와 종목 가격의 날짜/등락률이 비정상인지 확인
- N/A, NaN, 오래된 데이터, 근거 없는 수치는 리포트 근거로 강하게 쓰지 않음
- 시장 데이터와 뉴스가 충돌하면 "가격은 약세이나 뉴스 촉매는 긍정"처럼 분리해 설명

2. 시장 레짐 판정
- KOSPI/KOSDAQ, S&P500/NASDAQ, VIX, USD/KRW, 금리, 유가를 함께 보고 한국/미국 레짐을 따로 판단
- 현재 스냅샷보다 24시간 흐름과 시장 기억의 방향성을 더 중요하게 본다

3. 뉴스 촉매 판정
- 상세 뉴스의 source, theme, impact, investment_implication, channel, direct_impact, indirect_impact, historical_reaction, link 맥락을 활용해 당일 핵심 촉매를 판단
- 단순 제목이 아니라 "실적/정책/금리/수급/수출/규제/가이던스" 중 무엇이 어떤 경로로 가격에 영향을 주는지 구분

4. 후보 종목 필터링
- 먼저 제외할 종목을 정한다: 과열, 뉴스와 가격 괴리, 손절폭 과대, 약세 레짐과 충돌
- 추천은 남은 후보 중 근거가 가장 강한 종목만 한다
- 추천 수 10개를 억지로 채우지 않는다. 근거가 부족하면 추천을 줄이고 waiting_list를 늘린다

5. 시나리오 검증
- 각 추천은 기본 시나리오, 반대 시나리오, 무효화 조건을 내부적으로 점검한다
- 손절가가 합리적이지 않거나 목표가 대비 손익비가 나쁘면 추천하지 않는다

━━━ 절대 준수 규칙 (위반 시 리포트 무효) ━━━

⚠️ 규칙 1: 과매수 종목 추격매수 금지
- RSI 80 이상 또는 볼린저밴드 상단 돌파(BB > 100%) 종목은 "즉시 매수" 추천 금지
- 이런 종목은 "조정 시 매수" 조건부 추천으로만 제시 (entry_price를 현재가보다 낮게 설정)
- 5일 수익률 +20% 이상 급등 종목은 단타 추천 불가 (이미 단타 수익 구간 종료)

⚠️ 규칙 2: 시장 레짐에 따른 전략 차별화
- 약세장(레짐 점수 < -15): 현금 비중 25%+, 신규 매수 최소화, 방어주 위주
- 조정장(레짐 점수 -15~0): 분할 매수만, 추격 금지, 현금 20%
- 강세장(레짐 점수 > 20): 공격적 매수 가능, 현금 10%

⚠️ 규칙 3: "모멘텀이 강하다" ≠ "지금 사야 한다"
- 모멘텀은 "방향"을 보여줄 뿐, "진입 타이밍"은 기술적 지표로 별도 판단
- 진입 적정도(Entry Signal)가 🔴(과열)인 종목은 추천 목록에서 제외하거나 "대기" 표시
- 진입 적정도가 🟢(적정)인 종목을 우선 추천

⚠️ 규칙 4: 연속적 흐름 반영
- 아래 제공되는 "시장 흐름 분석"은 최근 24시간 동안 1시간 간격으로 수집된 연속 데이터
- 단순 현재 스냅샷이 아닌, 변화의 방향과 가속도를 반드시 고려
- 레짐 전환이 감지되면 해당 방향으로 전략 조정

⚠️ 규칙 6: 계층적 시장 기억 활용
- "시장 기억" 섹션에는 매일/매주/매달 단위의 과거 시장 흐름이 축적되어 있음
- 매일 기록: 최근 며칠간의 일간 레짐, 지수 변화, 섹터 로테이션, 변곡점
- 매주 기록: 최근 주간의 트렌드, 섹터 순환, 핵심 이벤트
- 매달 기록: 월간 레짐 변화, 대변곡, 국제정세
- 이 기억을 활용하여 "현재 모멘텀이 어디쯤인지" (초기/중기/후기/정점) 판단
- 과거 패턴과 현재를 비교하여 변곡점 접근 여부를 판단하고 리포트에 반영

⚠️ 규칙 5: 한국/미국 시장 독립 판단
- 한국 시장이 하락 중이면 한국 종목 매수 추천을 줄이거나 "조정 대기" 표시
- 미국 섹터 모멘텀을 한국 종목에 직접 적용하지 말 것 (각 시장 독립 판단)

⚠️ 규칙 7: 이전 중요 뉴스 반영
- "이전 중요 뉴스" 섹션은 최근 며칠간 누적된 핵심 이슈와 전일 요약에서 살아남은 뉴스
- 오늘 헤드라인만 보지 말고, 이전 중요 뉴스가 현재 모멘텀/리스크에 남긴 영향을 함께 판단
- 종목 추천 근거에 과거 뉴스가 영향을 주면 해당 뉴스 맥락을 명시

⚠️ 규칙 8: 사실/추론 구분
- 수치, 뉴스 제목, 이벤트는 제공 데이터에 있는 것만 사실로 쓴다
- 데이터에서 직접 확인되지 않는 해석은 "추론" 또는 "가능성" 수준으로 표현한다
- 확정되지 않은 이벤트를 단정하지 않는다

⚠️ 규칙 9: 실행 숫자와 리스크 게이트
- 추천 후보의 서술 근거는 작성하되, 실행 가능 여부는 후단 risk gate가 최종 결정한다
- price_source, risk_gate_status, evidence_ids, risk_reward_1, position_size_pct는 후단 엔진이 검증/보정한다
- 리스크 게이트를 통과하지 못한 후보는 최종 recommendations에서 제외될 수 있다
- risk_gate_status가 FAIL인 종목은 recommendations에 넣지 않는다
- is_executable=false인 종목을 실행가능이라고 표현하지 않는다
- evidence_id 없는 근거를 말하지 않는다

━━━ 종목 추천 시 필수 포함 정보 ━━━

각 종목에 반드시 포함:
- entry_signal: "적정"/"대기"/"과열" (진입 적정도 데이터 참조)
- entry_condition: 즉시 매수 가능 조건 또는 "XX원 이하 조정 시 매수" 조건
- momentum_quality: 1~100 (단순 수익률이 아닌 "건강한 모멘텀" 점수)
- investment_rationale: 3개 이상. 최소 1개는 가격/기술 지표, 최소 1개는 뉴스/이벤트, 최소 1개는 리스크 대비 보상 논리
- risk_factors: 실제 무효화 조건 중심으로 작성
- position_size_pct: 직접 산출하지 말고 null로 둔다. 후단 엔진이 손절폭과 레짐 기준으로 계산한다

━━━ 문체/정확도 기준 ━━━

- 과장 표현 금지: "무조건", "강력 매수", "확실한 상승" 같은 표현 금지
- 모바일 리포트용이므로 문장은 짧고 구체적으로 작성
- 애매한 근거("성장 기대", "모멘텀 우수")만 쓰지 말고 어떤 데이터/뉴스 때문에 그런지 명시
- 모르는 것은 모른다고 처리하고, 근거 부족 종목은 추천하지 않음
"""


def build_report_user_prompt(ctx: dict, today_str: str, extra_context: str = "") -> str:
    """AI에게 전달할 브리핑 자료 프롬프트."""
    payload = build_report_input_payload(ctx, today_str, extra_context=extra_context)
    return (
        "아래 REPORT_INPUT_JSON 객체만 근거로 JSON 투자 리포트를 생성하세요.\n"
        "출력은 output_schema 형태를 지키되 JSON 외 텍스트를 쓰지 마세요.\n"
        "REPORT_INPUT_JSON:\n"
        f"{json.dumps(payload, ensure_ascii=False, indent=2)}"
    )


def build_report_prompt(context: dict | str | None = None) -> tuple[str, str]:
    """AI 런타임이 달라도 재사용할 수 있는 리포트 프롬프트를 반환한다."""
    if isinstance(context, dict):
        ctx = context
        extra_context = ""
    elif isinstance(context, str):
        ctx = _empty_report_context(context)
        extra_context = context
    elif context is None:
        ctx = collect_realtime_data()
        extra_context = ""
    else:
        raise TypeError("context must be dict, str, or None")

    today_str = datetime.now(KST).strftime("%Y년 %m월 %d일")
    return SYSTEM_PROMPT_V2, build_report_user_prompt(ctx, today_str, extra_context=extra_context)


def _strip_report_json_text(raw: str) -> str:
    if "```json" in raw:
        return raw.split("```json")[1].split("```")[0].strip()
    if "```" in raw:
        return raw.split("```")[1].split("```")[0].strip()
    return raw.strip()


def _parse_report_json(raw: str) -> dict:
    return json.loads(_strip_report_json_text(raw))


def _generate_report_ai_text(system_prompt: str, user_prompt: str) -> str:
    if GEMINI_API_KEY:
        try:
            logger.info(f"Gemini 직접 API로 AI 리포트 생성 중 ({GEMINI_MODEL_REPORT})...")
            raw = generate_gemini_report_text(
                system_prompt,
                user_prompt,
                max_output_tokens=GEMINI_REPORT_MAX_OUTPUT_TOKENS,
            )
            _parse_report_json(raw)
            return raw
        except Exception as exc:
            logger.warning(f"Gemini 직접 리포트 생성 실패, Manus proxy로 폴백: {exc}")

    from .ai_client import get_client
    client = get_client()
    logger.info(f"Manus proxy로 AI 리포트 생성 중 ({MODEL_REPORT})...")
    resp = client.chat.completions.create(
        model=MODEL_REPORT,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=10000,
    )
    return resp.choices[0].message.content


def generate_investment_report() -> dict:
    """전체 리포트 생성 파이프라인 (v2: 흐름 분석 통합)"""
    ctx = collect_realtime_data()
    today_str = datetime.now(KST).strftime("%Y년 %m월 %d일")
    user_prompt = build_report_user_prompt(ctx, today_str)

    logger.info("AI 리포트 생성 중 (v2: 흐름 분석 통합)...")
    raw = _generate_report_ai_text(SYSTEM_PROMPT_V2, user_prompt)
    report = _parse_report_json(raw)
    apply_recommendation_safety_controls(report, ctx)
    report["_positions"] = _build_position_management()
    report["_meta"] = {
        "collected_at": ctx["collected_at"],
        "data_source": "realtime+flow",
        "version": "v3_execution_layer",
        "report_type": ctx.get("report_type"),
        "execution_policy": ctx.get("report_policy", {}).get("execution_policy"),
        "entry_signals_count": len(ctx["entry_signals"]),
        "flow_data_available": bool(ctx["flow_summary"]),
        "historical_news_available": bool(ctx.get("historical_news_context")),
    }
    report["_indices"] = ctx["indices"]
    report["_events"] = build_event_display_list(
        ctx.get("verified_events", []),
        ctx.get("tentative_events", []),
        (ctx.get("detailed_news", []) or []) + (ctx.get("schedule_news", []) or []),
    )
    report["_event_sections"] = build_event_sections(
        ctx.get("verified_events", []),
        ctx.get("tentative_events", []),
        (ctx.get("detailed_news", []) or []) + (ctx.get("schedule_news", []) or []),
        ctx.get("market_session_status", {}),
        now_kst=_context_time(ctx),
    )
    report["_entry_signals"] = ctx["entry_signals"]
    report["_news_headlines"] = ctx.get("headlines", [])
    report["_theme_news"] = ctx.get("theme_news", {})
    report["_detailed_news"] = ctx.get("detailed_news", [])
    report["_schedule_news"] = ctx.get("schedule_news", [])
    report["_historical_news_context"] = ctx.get("historical_news_context", "")
    report["_market_regime"] = ctx.get("market_regime", {})
    report["_market_session_status"] = ctx.get("market_session_status", {})
    report["_verified_events"] = ctx.get("verified_events", [])
    report["_tentative_events"] = ctx.get("tentative_events", [])

    logger.info("리포트 생성 완료 (v2)")
    return report


def _build_position_management() -> list[dict]:
    try:
        from .database import get_active_positions
        from .data_market import get_price_safe
        from .position_tracker import calc_pnl, rule_based_judge
    except Exception as exc:
        logger.warning("보유종목 관리 모듈 로드 실패: %s", exc)
        return []

    rows = []
    try:
        positions = get_active_positions()
    except Exception as exc:
        logger.warning("보유종목 조회 실패: %s", exc)
        return []

    for pos in positions:
        try:
            current_price = get_price_safe(pos["ticker"], pos["market"], pos["entry_price"])
            pnl = calc_pnl(pos, current_price)
            judge = rule_based_judge(pos, current_price, pnl)
            defense_line = judge.get("stop_loss") or round(float(pos["entry_price"]) * 0.92, 2)
            rows.append({
                "name": pos.get("name"),
                "ticker": pos.get("ticker"),
                "market": pos.get("market"),
                "currency": pos.get("currency"),
                "entry_price": pos.get("entry_price"),
                "current_price": current_price,
                "quantity": pos.get("quantity"),
                "pnl_amount": pnl.get("pnl_amount"),
                "pnl_pct": pnl.get("pnl_pct"),
                "pnl_str": pnl.get("pnl_str"),
                "action": judge.get("action"),
                "defense_line": defense_line,
                "add_buy_policy": "보류" if pnl.get("pnl_pct", 0) < 5 else "추가매수 금지, 수익 방어 우선",
                "summary": judge.get("reason"),
            })
        except Exception as exc:
            logger.warning("보유종목 관리 산출 실패 [%s]: %s", pos.get("ticker"), exc)
    return rows
