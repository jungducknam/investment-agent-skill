"""
report_engine.py — 온디맨드 리포트 생성 엔진

텔레그램 독립 실행 모드에서는 이 모듈이 직접 AI API를 호출한다.
에이전트 명령형 모드에서는 build_report_prompt()만 사용해 외부 에이전트가
자기 모델로 최종 리포트를 생성한다.
"""
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from .config import KST
from .data_market import fetch_indices, fetch_stock_prices, fetch_sector_momentum
from .data_news import (
    fetch_all_news, build_stock_news_context,
    build_theme_context, get_market_headlines
)
from .data_calendar import build_event_context
from .data_yahoo import get_multi_stock_insights, format_insights_for_prompt

logger = logging.getLogger(__name__)


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



    def _fetch_entry_signals():
        """종목별 진입 적정도 계산"""
        try:
            import yfinance as yf
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
                    t = yf.Ticker(ticker)
                    hist = t.history(period="3mo")
                    if len(hist) < 20:
                        continue
                    
                    prices = hist["Close"].tolist()
                    highs = hist["High"].tolist()
                    lows = hist["Low"].tolist()
                    volumes = hist["Volume"].tolist()
                    
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
                        "rsi": result.rsi,
                        "bb_position": result.bb_position,
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
            ex.submit(_fetch_entry_signals): "entry",
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
    except Exception:
        stock_news, theme_news, headlines = {}, {}, []

    calendar = results.get("calendar", {}).get("calendar", {"economic_events": [], "earnings": []})
    yahoo_data = results.get("yahoo", {})
    entry_data = results.get("entry", {})

    return {
        "indices": indices,
        "stock_prices": stock_prices,
        "sector_mom": sector_mom,
        "stock_news": stock_news,
        "theme_news": theme_news,
        "headlines": headlines,
        "calendar": calendar,
        "yahoo_insights": yahoo_data.get("yahoo_insights", {}),
        "yahoo_text": yahoo_data.get("yahoo_text", ""),
        "entry_signals": entry_data.get("entry_signals", {}),
        "collected_at": datetime.now(KST).isoformat(),
    }


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


def _fmt_stock_universe(stock_prices: dict, stock_news: dict) -> str:
    lines = []
    for ticker, info in stock_prices.items():
        price = info.get("price") or info.get("current_price")
        chg = info.get("change_pct")
        news = stock_news.get(ticker, "관련 뉴스 없음")
        cs = "₩" if info.get("market") == "KR" else "$"
        price_s = f"{cs}{price:,}" if price else "N/A"
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
        reasons = " | ".join(data["reasons"][:2])
        suggested = f" → 제안진입가: {data['suggested_entry']:,.0f}" if data.get("suggested_entry") else ""
        lines.append(f"{emoji} [{ticker}] {name}: {signal}({score:.0f}점) | RSI {rsi:.0f} | BB {bb:.0f}%{suggested}")
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
        "calendar": {"economic_events": [], "earnings": []},
        "yahoo_insights": {},
        "yahoo_text": "",
        "entry_signals": {},
        "collected_at": datetime.now(KST).isoformat(),
        "extra_context": extra_context,
    }


# ── 강화된 시스템 프롬프트 ────────────────────────────
SYSTEM_PROMPT_V2 = """당신은 20년 경력의 글로벌 투자 전략가입니다.
한국(KOSPI/KOSDAQ)과 미국(NYSE/NASDAQ) 시장을 전문으로 분석합니다.

━━━ 핵심 분석 원칙 ━━━

1. 모멘텀 + 펀더멘털 + 매크로 + 기술적 분석 4중 검증
2. 장기(3개월+) / 스윙(1~4주) / 단타(1~5일) 구분
3. 매수가, 목표가1(단기), 목표가2(중기), 손절가 명확히 제시
4. 포지션 크기 제안 (전체 포트폴리오 대비 %)
5. 투자 근거 3가지 이상 — 반드시 수집된 실제 뉴스/데이터 기반
6. 리스크 요인 명시
7. 응답은 반드시 JSON 형식

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

⚠️ 규칙 4: 변화의 방향과 가속도 고려
- 단순 현재 스냅샷이 아닌, 모멘텀의 방향과 가속도를 반드시 고려
- 레짐 전환이 감지되면 해당 방향으로 전략 조정

⚠️ 규칙 5: 한국/미국 시장 독립 판단
- 한국 시장이 하락 중이면 한국 종목 매수 추천을 줄이거나 "조정 대기" 표시
- 미국 섹터 모멘텀을 한국 종목에 직접 적용하지 말 것 (각 시장 독립 판단)

━━━ 종목 추천 시 필수 포함 정보 ━━━

각 종목에 반드시 포함:
- entry_signal: "적정"/"대기"/"과열" (진입 적정도 데이터 참조)
- entry_condition: 즉시 매수 가능 조건 또는 "XX원 이하 조정 시 매수" 조건
- momentum_quality: 1~100 (단순 수익률이 아닌 "건강한 모멘텀" 점수)
"""


def build_report_user_prompt(ctx: dict, extra_context: str = "") -> str:
    """수집된 데이터로 리포트 생성용 사용자 프롬프트를 만든다."""
    ctx = {**_empty_report_context(), **(ctx or {})}
    indices = ctx.get("indices", {})
    calendar = ctx.get("calendar") or {"economic_events": [], "earnings": []}
    theme_news = ctx.get("theme_news", {})
    today_str = datetime.now(KST).strftime("%Y년 %m월 %d일")
    extra = extra_context or ctx.get("extra_context", "")
    extra_section = ""
    if extra:
        extra_section = f"""
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📎 추가 컨텍스트
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{extra}
"""

    return f"""
오늘 날짜: {today_str}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 실시간 주요 지수
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{_fmt_index(indices, 'KOSPI')}
{_fmt_index(indices, 'KOSDAQ')}
{_fmt_index(indices, 'SP500')}
{_fmt_index(indices, 'NASDAQ')}
{_fmt_index(indices, 'DOW')}
VIX: {indices.get('VIX', {}).get('price', 'N/A')}
USD/KRW: {indices.get('USD_KRW', {}).get('price', 'N/A')}
미국 10년물: {indices.get('US10Y', {}).get('price', 'N/A')}%
브렌트유: ${indices.get('BRENT', {}).get('price', 'N/A')}
금: ${indices.get('GOLD', {}).get('price', 'N/A')}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 종목별 진입 적정도 (기술적 필터 결과)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{_fmt_entry_signals(ctx.get('entry_signals', {}))}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔥 섹터 모멘텀
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{_fmt_sector(ctx.get('sector_mom', {}))}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📰 주요 헤드라인
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{chr(10).join('- ' + h for h in ctx.get('headlines', [])[:8]) or '헤드라인 없음'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏷️ 테마별 뉴스
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
반도체: {' / '.join(theme_news.get('반도체', [])[:3]) or '없음'}
AI: {' / '.join(theme_news.get('AI', [])[:3]) or '없음'}
피지컬AI: {' / '.join(theme_news.get('피지컬AI', [])[:2]) or '없음'}
전력인프라: {' / '.join(theme_news.get('전력인프라', [])[:2]) or '없음'}
방산: {' / '.join(theme_news.get('방산', [])[:2]) or '없음'}
미중무역: {' / '.join(theme_news.get('미중무역', [])[:2]) or '없음'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📅 경제 이벤트
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{chr(10).join('- ' + e for e in calendar.get('economic_events', [])[:6]) or '주요 이벤트 없음'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 종목 유니버스
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{_fmt_stock_universe(ctx.get('stock_prices', {}), ctx.get('stock_news', {})) or '종목 데이터 없음'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Yahoo Finance 인사이트
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{ctx.get('yahoo_text') or 'Yahoo 인사이트 없음'}
{extra_section}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📝 지시사항
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
위 데이터 기반으로 JSON 투자 리포트를 생성하세요.

중요:
- 진입 적정도가 🔴(과열)인 종목은 추천하지 마세요. 🟢(적정) 우선, 🟡(대기)는 조건부.
- 시장 레짐이 약세이면 해당 시장 종목 추천을 줄이세요.
- 10개 종목 추천: 한국 5개 + 미국 5개 | 장기 3개 + 스윙 4개 + 단타 3개
- 단, 과열 종목이 많으면 추천 수를 줄이고 "대기 종목"으로 분류하세요.

JSON 형식:
{{
  "report_date": "{today_str}",
  "market_summary": {{
    "overall_sentiment": "강세/약세/중립",
    "sentiment_score": 1~10,
    "key_theme": "핵심 테마",
    "macro_analysis": "매크로 분석",
    "sector_rotation": "섹터 로테이션 분석",
    "market_regime_kr": "한국 시장 레짐 판단",
    "market_regime_us": "미국 시장 레짐 판단",
    "flow_analysis": "온디맨드 데이터 기반 종합 판단",
    "risk_factors": ["리스크1", "리스크2", "리스크3"]
  }},
  "recommendations": [
    {{
      "rank": 1, "name": "종목명", "ticker": "티커", "market": "KR/US",
      "sector": "섹터", "currency": "KRW/USD", "style": "장기/스윙/단타",
      "holding_period": "기간", "current_price": 숫자,
      "entry_price": 숫자, "entry_signal": "적정/대기/과열",
      "entry_condition": "즉시 매수 가능" 또는 "XX원 이하 조정 시 매수",
      "target_price_1": 숫자, "target_price_2": 숫자,
      "stop_loss": 숫자, "upside_pct": 숫자, "position_size_pct": 숫자,
      "investment_rationale": ["근거1", "근거2", "근거3"],
      "risk_factors": ["리스크1", "리스크2"],
      "momentum_quality": 1~100,
      "technical_status": "RSI/BB 상태 요약"
    }}
  ],
  "waiting_list": [
    {{
      "name": "종목명", "ticker": "티커", "reason": "대기 사유",
      "target_entry": 숫자, "condition": "진입 조건"
    }}
  ],
  "portfolio_strategy": {{
    "cash_reserve_pct": 숫자,
    "regime_based_allocation": "레짐 기반 배분 설명",
    "long_term_allocation": "설명",
    "swing_strategy": "설명",
    "daytrading_focus": "설명",
    "overall_advice": "핵심 조언"
  }},
  "watchlist": ["종목1", "종목2", "종목3"]
}}"""


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
    return SYSTEM_PROMPT_V2, build_report_user_prompt(ctx, extra_context=extra_context)


def generate_investment_report() -> dict:
    """전체 리포트 생성 파이프라인 (v2: 흐름 분석 통합)"""
    ctx = collect_realtime_data()
    today_str = datetime.now(KST).strftime("%Y년 %m월 %d일")

    user_prompt = f"""
오늘 날짜: {today_str}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📊 실시간 주요 지수
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{_fmt_index(ctx['indices'], 'KOSPI')}
{_fmt_index(ctx['indices'], 'KOSDAQ')}
{_fmt_index(ctx['indices'], 'SP500')}
{_fmt_index(ctx['indices'], 'NASDAQ')}
{_fmt_index(ctx['indices'], 'DOW')}
VIX: {ctx['indices'].get('VIX', {}).get('price', 'N/A')}
USD/KRW: {ctx['indices'].get('USD_KRW', {}).get('price', 'N/A')}
미국 10년물: {ctx['indices'].get('US10Y', {}).get('price', 'N/A')}%
브렌트유: ${ctx['indices'].get('BRENT', {}).get('price', 'N/A')}
금: ${ctx['indices'].get('GOLD', {}).get('price', 'N/A')}



━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🎯 종목별 진입 적정도 (기술적 필터 결과)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{_fmt_entry_signals(ctx['entry_signals'])}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🔥 섹터 모멘텀
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{_fmt_sector(ctx['sector_mom'])}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📰 주요 헤드라인
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{chr(10).join('- ' + h for h in ctx['headlines'][:8])}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🏷️ 테마별 뉴스
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
반도체: {' / '.join(ctx['theme_news'].get('반도체', [])[:3]) or '없음'}
AI: {' / '.join(ctx['theme_news'].get('AI', [])[:3]) or '없음'}
피지컬AI: {' / '.join(ctx['theme_news'].get('피지컬AI', [])[:2]) or '없음'}
전력인프라: {' / '.join(ctx['theme_news'].get('전력인프라', [])[:2]) or '없음'}
방산: {' / '.join(ctx['theme_news'].get('방산', [])[:2]) or '없음'}
미중무역: {' / '.join(ctx['theme_news'].get('미중무역', [])[:2]) or '없음'}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📅 경제 이벤트
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{chr(10).join('- ' + e for e in ctx['calendar'].get('economic_events', [])[:6])}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📋 종목 유니버스
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{_fmt_stock_universe(ctx['stock_prices'], ctx['stock_news'])}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
💡 Yahoo Finance 인사이트
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{ctx['yahoo_text']}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
📝 지시사항
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
위 데이터 기반으로 JSON 투자 리포트를 생성하세요.

중요: 
- 진입 적정도가 🔴(과열)인 종목은 추천하지 마세요. 🟢(적정) 우선, 🟡(대기)는 조건부.
- 시장 흐름에서 레짐이 "약세"이면 해당 시장 종목 추천을 줄이세요.
- 10개 종목 추천: 한국 5개 + 미국 5개 | 장기 3개 + 스윙 4개 + 단타 3개
- 단, 과열 종목이 많으면 추천 수를 줄이고 "대기 종목"으로 분류하세요.

JSON 형식:
{{
  "report_date": "{today_str}",
  "market_summary": {{
    "overall_sentiment": "강세/약세/중립",
    "sentiment_score": 1~10,
    "key_theme": "핵심 테마",
    "macro_analysis": "매크로 분석",
    "sector_rotation": "섹터 로테이션 분석",
    "market_regime_kr": "한국 시장 레짐 판단",
    "market_regime_us": "미국 시장 레짐 판단",
    "flow_analysis": "24시간 흐름 기반 종합 판단",
    "risk_factors": ["리스크1", "리스크2", "리스크3"]
  }},
  "recommendations": [
    {{
      "rank": 1, "name": "종목명", "ticker": "티커", "market": "KR/US",
      "sector": "섹터", "currency": "KRW/USD", "style": "장기/스윙/단타",
      "holding_period": "기간", "current_price": 숫자,
      "entry_price": 숫자, "entry_signal": "적정/대기/과열",
      "entry_condition": "즉시 매수 가능" 또는 "XX원 이하 조정 시 매수",
      "target_price_1": 숫자, "target_price_2": 숫자,
      "stop_loss": 숫자, "upside_pct": 숫자, "position_size_pct": 숫자,
      "investment_rationale": ["근거1", "근거2", "근거3"],
      "risk_factors": ["리스크1", "리스크2"],
      "momentum_quality": 1~100,
      "technical_status": "RSI/BB 상태 요약"
    }}
  ],
  "waiting_list": [
    {{
      "name": "종목명", "ticker": "티커", "reason": "대기 사유",
      "target_entry": 숫자, "condition": "진입 조건"
    }}
  ],
  "portfolio_strategy": {{
    "cash_reserve_pct": 숫자,
    "regime_based_allocation": "레짐 기반 배분 설명",
    "long_term_allocation": "설명",
    "swing_strategy": "설명",
    "daytrading_focus": "설명",
    "overall_advice": "핵심 조언"
  }},
  "watchlist": ["종목1", "종목2", "종목3"]
}}"""

    # AI 호출. 프롬프트 생성은 에이전트 명령형 모드와 공유한다.
    system_prompt, user_prompt = build_report_prompt(ctx)
    from .ai_client import get_client
    client = get_client()

    logger.info("AI 리포트 생성 중 (v2: 흐름 분석 통합)...")
    resp = client.chat.completions.create(
        model="gemini-2.5-flash",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=10000,
    )

    raw = resp.choices[0].message.content
    if "```json" in raw:
        raw = raw.split("```json")[1].split("```")[0].strip()
    elif "```" in raw:
        raw = raw.split("```")[1].split("```")[0].strip()

    report = json.loads(raw)
    report["_meta"] = {
        "collected_at": ctx["collected_at"],
        "data_source": "realtime",
        "version": "v2",
        "entry_signals_count": len(ctx["entry_signals"]),
        "flow_data_available": bool(ctx.get("flow_summary")),
    }
    report["_indices"] = ctx["indices"]
    report["_events"] = ctx["calendar"].get("economic_events", []) + ctx["calendar"].get("earnings", [])
    report["_entry_signals"] = ctx["entry_signals"]

    logger.info("리포트 생성 완료 (v2)")
    return report
