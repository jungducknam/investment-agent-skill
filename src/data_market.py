"""
data_market.py — yfinance 기반 시장 데이터 수집 (최적화)
- ThreadPoolExecutor 병렬 수집
- 캐시 메커니즘으로 불필요한 API 호출 방지
- 에러 핸들링 강화
"""
import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from functools import lru_cache

import yfinance as yf

from .config import KST
from .data_kis import get_domestic_index_quote, get_domestic_stock_price, get_domestic_stock_quote
from .market_session import annotate_price_session, build_market_session_status

logger = logging.getLogger(__name__)
KIS_STOCK_QUOTE_INTERVAL_SEC = 0.0

# ── 지수 티커 ─────────────────────────────────────────
INDEX_TICKERS = {
    "KOSPI":   "^KS11",
    "KOSDAQ":  "^KQ11",
    "SP500":   "^GSPC",
    "NASDAQ":  "^IXIC",
    "DOW":     "^DJI",
    "VIX":     "^VIX",
    "USD_KRW": "KRW=X",
    "US10Y":   "^TNX",
    "BRENT":   "BZ=F",
    "GOLD":    "GC=F",
}

# ── 종목 유니버스 ─────────────────────────────────────
STOCK_UNIVERSE = [
    # 한국
    {"name": "삼성전자",       "ticker": "005930", "yf": "005930.KS", "market": "KR", "sector": "반도체"},
    {"name": "SK하이닉스",     "ticker": "000660", "yf": "000660.KS", "market": "KR", "sector": "반도체"},
    {"name": "한미반도체",     "ticker": "042700", "yf": "042700.KS", "market": "KR", "sector": "반도체"},
    {"name": "삼성전기",       "ticker": "009150", "yf": "009150.KS", "market": "KR", "sector": "반도체"},
    {"name": "현대차",         "ticker": "005380", "yf": "005380.KS", "market": "KR", "sector": "자동차"},
    {"name": "LG에너지솔루션", "ticker": "373220", "yf": "373220.KS", "market": "KR", "sector": "2차전지"},
    {"name": "한화에어로",     "ticker": "012450", "yf": "012450.KS", "market": "KR", "sector": "방산"},
    {"name": "현대로보틱스",   "ticker": "267250", "yf": "267250.KS", "market": "KR", "sector": "로봇"},
    {"name": "LS일렉트릭",    "ticker": "010120", "yf": "010120.KS", "market": "KR", "sector": "전력인프라"},
    {"name": "HD현대일렉트릭", "ticker": "267260", "yf": "267260.KS", "market": "KR", "sector": "전력인프라"},
    {"name": "NAVER",          "ticker": "035420", "yf": "035420.KS", "market": "KR", "sector": "IT/플랫폼"},
    {"name": "카카오",         "ticker": "035720", "yf": "035720.KS", "market": "KR", "sector": "IT/플랫폼"},
    {"name": "셀트리온",       "ticker": "068270", "yf": "068270.KS", "market": "KR", "sector": "바이오"},
    {"name": "포스코홀딩스",   "ticker": "005490", "yf": "005490.KS", "market": "KR", "sector": "철강/소재"},
    {"name": "LIG넥스원",      "ticker": "079550", "yf": "079550.KS", "market": "KR", "sector": "방산"},
    {"name": "에코프로비엠",   "ticker": "247540", "yf": "247540.KQ", "market": "KR", "sector": "2차전지"},
    {"name": "두산에너빌리티", "ticker": "034020", "yf": "034020.KS", "market": "KR", "sector": "원전/에너지"},
    {"name": "현대로템",       "ticker": "064350", "yf": "064350.KS", "market": "KR", "sector": "방산/철도"},
    # 미국
    {"name": "NVIDIA",    "ticker": "NVDA", "yf": "NVDA", "market": "US", "sector": "반도체/AI"},
    {"name": "AMD",       "ticker": "AMD",  "yf": "AMD",  "market": "US", "sector": "반도체"},
    {"name": "Intel",     "ticker": "INTC", "yf": "INTC", "market": "US", "sector": "반도체"},
    {"name": "Broadcom",  "ticker": "AVGO", "yf": "AVGO", "market": "US", "sector": "반도체"},
    {"name": "Micron",    "ticker": "MU",   "yf": "MU",   "market": "US", "sector": "반도체/메모리"},
    {"name": "AMAT",      "ticker": "AMAT", "yf": "AMAT", "market": "US", "sector": "반도체장비"},
    {"name": "Palantir",  "ticker": "PLTR", "yf": "PLTR", "market": "US", "sector": "AI/데이터"},
    {"name": "Tesla",     "ticker": "TSLA", "yf": "TSLA", "market": "US", "sector": "EV/자율주행"},
    {"name": "Microsoft", "ticker": "MSFT", "yf": "MSFT", "market": "US", "sector": "빅테크/AI"},
    {"name": "Apple",     "ticker": "AAPL", "yf": "AAPL", "market": "US", "sector": "빅테크"},
    {"name": "Alphabet",  "ticker": "GOOGL","yf": "GOOGL","market": "US", "sector": "빅테크/AI"},
    {"name": "Meta",      "ticker": "META", "yf": "META", "market": "US", "sector": "빅테크/AI"},
    {"name": "Amazon",    "ticker": "AMZN", "yf": "AMZN", "market": "US", "sector": "빅테크/클라우드"},
    {"name": "ARM",       "ticker": "ARM",  "yf": "ARM",  "market": "US", "sector": "반도체설계"},
    {"name": "Corning",   "ticker": "GLW",  "yf": "GLW",  "market": "US", "sector": "광통신/소재"},
    {"name": "Lockheed",  "ticker": "LMT",  "yf": "LMT",  "market": "US", "sector": "방산"},
    {"name": "NextEra",   "ticker": "NEE",  "yf": "NEE",  "market": "US", "sector": "전력/에너지"},
    {"name": "GE Vernova","ticker": "GEV",  "yf": "GEV",  "market": "US", "sector": "전력인프라"},
    {"name": "Lam Research","ticker":"LRCX","yf": "LRCX", "market": "US", "sector": "반도체장비"},
]

# ── 섹터 ETF ──────────────────────────────────────────
SECTOR_ETFS = {
    # 한국
    "반도체(KR)":     "091160.KS",
    "2차전지(KR)":    "305720.KS",
    "바이오(KR)":     "244580.KS",
    "자동차(KR)":     "091170.KS",
    "방산(KR)":       "004490.KS",
    "커뮤니케이션서비스(KR)": "315270.KS",
    "에너지(KR)":     "117460.KS",
    "철강(KR)":       "139220.KS",
    "건설(KR)":       "139230.KS",
    "금융(KR)":       "091180.KS",
    "로봇/자동화(KR)":"465330.KS",
    "원전(KR)":       "472150.KS",
    # 미국
    "반도체(US)":     "SOXX",
    "AI/빅테크(US)":  "QQQ",
    "에너지(US)":     "XLE",
    "금융(US)":       "XLF",
    "헬스케어(US)":   "XLV",
    "산업재(US)":     "XLI",
    "유틸리티(US)":   "XLU",
    "소비재(US)":     "XLY",
    "부동산(US)":     "XLRE",
    "소재(US)":       "XLB",
    "통신(US)":       "XLC",
    "방산(US)":       "ITA",
}


def is_valid_number(value) -> bool:
    """None/NaN/inf가 아닌 숫자만 시장 데이터로 인정한다."""
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def clean_regular_history(hist, required_columns: tuple[str, ...] = ("Close",)):
    """장외/휴장으로 생긴 빈 row를 제거하고 정규장 유효 데이터만 남긴다."""
    if hist is None or hist.empty:
        return hist

    cleaned = hist.copy()
    for col in required_columns:
        if col not in cleaned.columns:
            continue
        cleaned = cleaned[cleaned[col].map(is_valid_number)]
    return cleaned


def get_regular_history(
    ticker_yf: str,
    period: str = "5d",
    interval: str | None = None,
    required_columns: tuple[str, ...] = ("Close",),
):
    """yfinance history를 가져온 뒤 NaN row를 제거한다."""
    kwargs = {"period": period}
    if interval:
        kwargs["interval"] = interval
    hist = yf.Ticker(ticker_yf).history(**kwargs)
    return clean_regular_history(hist, required_columns=required_columns)


def last_valid_close(hist) -> float | None:
    cleaned = clean_regular_history(hist, required_columns=("Close",))
    if cleaned is None or cleaned.empty:
        return None
    close = cleaned["Close"].iloc[-1]
    return float(close) if is_valid_number(close) else None


def get_last_regular_close(ticker_yf: str, period: str = "5d") -> float | None:
    """마지막 정규장 종가. 장외/휴장 중 실시간 가격이 비면 이 값을 사용한다."""
    try:
        return last_valid_close(get_regular_history(ticker_yf, period=period))
    except Exception as e:
        logger.debug(f"마지막 정규장 종가 조회 실패 ({ticker_yf}): {e}")
        return None


def _get_price_and_change(ticker_yf: str) -> dict:
    """개별 종목/지수 가격 + 등락률 조회"""
    try:
        hist = get_regular_history(ticker_yf, period="5d")
        if hist is None or hist.empty:
            return {}
        current = float(hist["Close"].iloc[-1])
        if len(hist) >= 2:
            prev = float(hist["Close"].iloc[-2])
            change_pct = (current - prev) / prev * 100
        else:
            change_pct = 0.0
        return {
            "price": round(current, 2),
            "change_pct": round(change_pct, 2),
            "last_trade_time": _last_trade_time_iso(hist),
            "source": "Yahoo",
        }
    except Exception as e:
        logger.debug(f"가격 조회 실패 ({ticker_yf}): {e}")
        return {}


def _get_stock_price_data(stock: dict) -> dict:
    """종목 유니버스 가격 조회. 국장은 KIS를 우선 사용한다."""
    if stock.get("market") == "KR":
        try:
            data = get_domestic_stock_quote(stock["ticker"])
            if data:
                return data
        except Exception as e:
            logger.debug(f"KIS 가격 조회 실패 ({stock['ticker']}): {e}")

    return _get_price_and_change(stock["yf"])


def _get_momentum(ticker_yf: str) -> dict:
    """ETF 5일/20일 수익률 계산"""
    try:
        hist = get_regular_history(ticker_yf, period="1mo")
        if hist is None or hist.empty or len(hist) < 5:
            return {}
        current = float(hist["Close"].iloc[-1])
        ret_5d = (current / float(hist["Close"].iloc[-5]) - 1) * 100 if len(hist) >= 5 else 0
        ret_20d = (current / float(hist["Close"].iloc[0]) - 1) * 100
        momentum = "HOT" if ret_5d > 3 else ("WARMING" if ret_5d > 0 else "COOLING")
        return {
            "ret_5d": round(ret_5d, 2),
            "ret_20d": round(ret_20d, 2),
            "momentum": momentum,
        }
    except Exception as e:
        logger.debug(f"모멘텀 조회 실패 ({ticker_yf}): {e}")
        return {}


def fetch_indices() -> dict:
    """주요 지수 병렬 조회"""
    results = {}

    for name in ("KOSPI", "KOSDAQ"):
        try:
            data = get_domestic_index_quote(name)
            if data:
                results[name] = data
        except Exception as e:
            logger.debug(f"KIS 국내 지수 조회 실패 ({name}): {e}")

    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(_get_price_and_change, yf_t): name
                   for name, yf_t in INDEX_TICKERS.items()
                   if name not in results}
        for fut in as_completed(futures, timeout=30):
            name = futures[fut]
            try:
                results[name] = fut.result()
            except Exception:
                results[name] = {}
    return results


def fetch_stock_prices() -> dict:
    """종목 유니버스 가격 병렬 조회"""
    results = {}
    now_kst = datetime.now(KST)
    sessions = build_market_session_status(now_kst)
    kr_stocks = [s for s in STOCK_UNIVERSE if s.get("market") == "KR"]
    non_kr_stocks = [s for s in STOCK_UNIVERSE if s.get("market") != "KR"]

    def _fetch_one(stock: dict) -> tuple:
        data = _get_stock_price_data(stock)
        data.update({"name": stock["name"], "market": stock["market"], "sector": stock["sector"]})
        data = annotate_price_session(data, stock["market"], sessions.get(stock["market"]), now_kst=now_kst)
        return stock["ticker"], data

    for stock in kr_stocks:
        try:
            ticker, data = _fetch_one(stock)
            if data.get("price"):
                results[ticker] = data
        except Exception:
            pass
        time.sleep(KIS_STOCK_QUOTE_INTERVAL_SEC)

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(_fetch_one, s) for s in non_kr_stocks]
        for fut in as_completed(futures, timeout=60):
            try:
                ticker, data = fut.result()
                if data.get("price"):
                    results[ticker] = data
            except Exception:
                pass
    return results


def _last_trade_time_iso(hist) -> str:
    if hist is None or hist.empty:
        return ""
    idx = hist.index[-1]
    try:
        dt = idx.to_pydatetime()
    except AttributeError:
        return ""
    if dt.tzinfo is None:
        dt = KST.localize(dt)
    return dt.astimezone(KST).isoformat()


def fetch_sector_momentum() -> dict:
    """섹터 ETF 모멘텀 병렬 조회"""
    results = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(_get_momentum, yf_t): name
                   for name, yf_t in SECTOR_ETFS.items()}
        for fut in as_completed(futures, timeout=45):
            name = futures[fut]
            try:
                data = fut.result()
                if data:
                    results[name] = data
            except Exception:
                pass
    return results


def get_realtime_price(ticker: str, market: str) -> float | None:
    """단일 종목 실시간 가격 조회 (포지션 모니터링용)"""
    try:
        if market == "KR":
            t_str = ticker.zfill(6)
            price = get_domestic_stock_price(t_str)
            if price is not None:
                return price

            for suffix in [".KS", ".KQ"]:
                try:
                    price = last_valid_close(get_regular_history(t_str + suffix, period="1d", interval="1m"))
                    if price is not None:
                        return price
                except Exception:
                    continue
            # fallback: 일봉
            for suffix in [".KS", ".KQ"]:
                try:
                    price = get_last_regular_close(t_str + suffix, period="5d")
                    if price is not None:
                        return price
                except Exception:
                    continue
        else:
            price = last_valid_close(get_regular_history(ticker, period="1d", interval="1m"))
            if price is not None:
                return price
            return get_last_regular_close(ticker, period="5d")
    except Exception as e:
        logger.debug(f"실시간 가격 조회 실패 ({ticker}): {e}")
    return None


def get_price_safe(ticker: str, market: str, fallback: float) -> float:
    """가격 조회 실패 시 fallback 반환"""
    price = get_realtime_price(ticker, market)
    return price if price is not None else fallback
