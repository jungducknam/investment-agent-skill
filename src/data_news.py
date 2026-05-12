"""
data_news.py — RSS 기반 뉴스 수집 (최적화)
- 9개 피드 병렬 수집
- HTML 태그 정리
- 종목/테마 필터링
"""
import logging
import time as _time
from datetime import datetime, timedelta

import feedparser
import pytz
from bs4 import BeautifulSoup

from .config import KST

logger = logging.getLogger(__name__)

# ── RSS 피드 목록 ─────────────────────────────────────
RSS_FEEDS = {
    "연합뉴스_경제":   "https://www.yonhapnewstv.co.kr/category/economy/feed/",
    "한국경제":        "https://www.hankyung.com/feed/all-news",
    "매일경제":        "https://www.mk.co.kr/rss/30000001/",
    "Yahoo_Finance":   "https://finance.yahoo.com/news/rssindex",
    "Reuters_Biz":     "https://feeds.reuters.com/reuters/businessNews",
    "CNBC_Top":        "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114",
    "MarketWatch":     "https://feeds.marketwatch.com/marketwatch/topstories/",
    "Investing_com":   "https://www.investing.com/rss/news.rss",
    "SeekingAlpha":    "https://seekingalpha.com/market_currents.xml",
}

# ── 종목 키워드 매핑 ─────────────────────────────────
STOCK_KEYWORDS = {
    "005930": ["삼성전자", "Samsung Electronics", "삼성", "갤럭시"],
    "000660": ["SK하이닉스", "SK Hynix", "하이닉스", "HBM"],
    "042700": ["한미반도체", "Hanmi Semiconductor"],
    "373220": ["LG에너지솔루션", "LG Energy"],
    "005380": ["현대자동차", "현대차", "Hyundai Motor"],
    "012450": ["한화에어로", "Hanwha Aerospace", "한화에어로스페이스"],
    "NVDA":   ["NVIDIA", "Nvidia", "엔비디아", "Jensen"],
    "AMD":    ["AMD", "Advanced Micro", "리사수"],
    "TSLA":   ["Tesla", "테슬라", "일론"],
    "MSFT":   ["Microsoft", "마이크로소프트", "MS"],
    "AAPL":   ["Apple", "애플", "아이폰"],
    "GOOGL":  ["Google", "Alphabet", "구글"],
    "META":   ["Meta", "메타", "Facebook"],
    "PLTR":   ["Palantir", "팔란티어"],
    "AVGO":   ["Broadcom", "브로드컴"],
    "MU":     ["Micron", "마이크론"],
    "INTC":   ["Intel", "인텔"],
    "ARM":    ["ARM", "Arm Holdings"],
    "GLW":    ["Corning", "코닝"],
}

# ── 테마 키워드 ───────────────────────────────────────
THEME_KEYWORDS = {
    "반도체":    ["반도체", "HBM", "메모리", "DRAM", "semiconductor", "chip", "wafer", "TSMC"],
    "피지컬AI":  ["피지컬AI", "로봇", "robot", "humanoid", "physical AI", "자율주행", "Boston Dynamics"],
    "전력인프라": ["변압기", "전력", "데이터센터", "power grid", "transformer", "grid", "data center"],
    "AI":        ["AI", "인공지능", "LLM", "ChatGPT", "artificial intelligence", "에이전트", "OpenAI", "Gemini"],
    "광통신":    ["광통신", "광섬유", "optical", "fiber", "Corning"],
    "미중무역":  ["관세", "무역", "tariff", "trade war", "미중", "US-China", "무역협상"],
    "금리":      ["금리", "Fed", "연준", "interest rate", "FOMC", "CPI", "PCE", "인플레이션"],
    "바이오":    ["바이오", "제약", "임상", "FDA", "biotech", "pharma", "신약"],
    "방산":      ["방산", "미사일", "전투기", "defense", "military", "우크라이나", "중동"],
    "에너지":    ["에너지", "원유", "원전", "oil", "energy", "nuclear", "LNG", "천연가스"],
    "금융":      ["금리인상", "은행", "bank", "financial", "금융구제"],
    "자동차":    ["전기차", "EV", "Tesla", "현대차", "자율주행", "autonomous"],
    "거시":      ["경기침체", "GDP", "성장률", "recession", "경기", "macro", "무역수지"],
}


def _parse_rss(feed_url: str, max_items: int = 20) -> list[dict]:
    """RSS 피드 파싱 (에러 핸들링 강화)"""
    try:
        feed = feedparser.parse(feed_url)
        items = []
        for entry in feed.entries[:max_items]:
            title = entry.get("title", "").strip()
            if not title:
                continue
            summary = entry.get("summary", entry.get("description", "")).strip()
            summary = BeautifulSoup(summary, "html.parser").get_text()[:200]
            link = entry.get("link", "")
            pub_parsed = entry.get("published_parsed") or entry.get("updated_parsed")
            if pub_parsed:
                pub_dt = datetime(*pub_parsed[:6], tzinfo=pytz.utc).astimezone(KST)
            else:
                pub_dt = datetime.now(KST)
            items.append({
                "title": title,
                "summary": summary,
                "link": link,
                "pub_dt": pub_dt,
                "source": feed.feed.get("title", ""),
            })
        return items
    except Exception as e:
        logger.warning(f"RSS 파싱 실패 ({feed_url}): {e}")
        return []


def fetch_all_news(max_per_feed: int = 12) -> list[dict]:
    """모든 RSS 피드에서 뉴스 수집"""
    all_news = []
    for name, url in RSS_FEEDS.items():
        items = _parse_rss(url, max_per_feed)
        for item in items:
            item["feed_name"] = name
        all_news.extend(items)
        _time.sleep(0.2)
    all_news.sort(key=lambda x: x["pub_dt"], reverse=True)
    logger.info(f"뉴스 총 {len(all_news)}건 수집")
    return all_news


def filter_news_by_stock(news_list: list[dict], ticker: str) -> list[str]:
    """종목 관련 뉴스 헤드라인 필터링"""
    keywords = STOCK_KEYWORDS.get(ticker, [])
    if not keywords:
        return []
    results = []
    for item in news_list:
        text = (item["title"] + " " + item["summary"]).lower()
        if any(kw.lower() in text for kw in keywords):
            results.append(item["title"])
    return results[:5]


def filter_news_by_theme(news_list: list[dict], theme: str) -> list[str]:
    """테마별 뉴스 필터링"""
    keywords = THEME_KEYWORDS.get(theme, [theme])
    results = []
    for item in news_list:
        text = (item["title"] + " " + item["summary"]).lower()
        if any(kw.lower() in text for kw in keywords):
            results.append(item["title"])
    return results[:8]


def get_market_headlines(news_list: list[dict], n: int = 10) -> list[str]:
    """주요 헤드라인 상위 n개"""
    return [item["title"] for item in news_list[:n]]


def build_stock_news_context(news_list: list[dict], tickers: list[str]) -> dict:
    """종목별 뉴스 요약 딕셔너리"""
    result = {}
    for ticker in tickers:
        headlines = filter_news_by_stock(news_list, ticker)
        result[ticker] = " / ".join(headlines[:3]) if headlines else "관련 뉴스 없음"
    return result


def build_theme_context(news_list: list[dict]) -> dict:
    """테마별 뉴스 요약"""
    return {theme: filter_news_by_theme(news_list, theme)[:3] for theme in THEME_KEYWORDS}
