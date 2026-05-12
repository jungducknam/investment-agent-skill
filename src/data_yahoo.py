"""
data_yahoo.py — Yahoo Finance 심층 인사이트 (RSI, 목표가, 펀더멘털, 내부자거래)
AI 호출 없이 순수 데이터 수집/계산
"""
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import yfinance as yf

logger = logging.getLogger(__name__)


def get_stock_insights(ticker_yf: str, market: str = "US") -> dict:
    """개별 종목 심층 인사이트"""
    try:
        t = yf.Ticker(ticker_yf)
        info = t.info or {}

        # 기본 정보
        name = info.get("shortName", ticker_yf)
        current_price = info.get("currentPrice") or info.get("regularMarketPrice", 0)
        currency = "KRW" if market == "KR" else "USD"

        # 52주 범위
        high_52w = info.get("fiftyTwoWeekHigh", 0)
        low_52w = info.get("fiftyTwoWeekLow", 0)
        pos_52w = 0
        if high_52w and low_52w and high_52w != low_52w:
            pos_52w = round((current_price - low_52w) / (high_52w - low_52w) * 100, 1)

        # 이동평균 트렌드
        ma50 = info.get("fiftyDayAverage", 0)
        ma200 = info.get("twoHundredDayAverage", 0)
        if ma50 and ma200:
            if current_price > ma50 > ma200:
                trend = "강세 (가격>MA50>MA200)"
            elif current_price < ma50 < ma200:
                trend = "약세 (가격<MA50<MA200)"
            else:
                trend = "중립/혼조"
        else:
            trend = "데이터 부족"

        # 애널리스트 목표가
        target_mean = info.get("targetMeanPrice", 0)
        target_high = info.get("targetHighPrice", 0)
        target_low = info.get("targetLowPrice", 0)
        recommendation = info.get("recommendationKey", "N/A")
        num_analysts = info.get("numberOfAnalystOpinions", 0)
        upside = round((target_mean / current_price - 1) * 100, 1) if target_mean and current_price else 0

        # 펀더멘털
        per = info.get("trailingPE") or info.get("forwardPE", 0)
        pbr = info.get("priceToBook", 0)
        roe = info.get("returnOnEquity", 0)
        eps = info.get("trailingEps", 0)
        revenue_growth = info.get("revenueGrowth", 0)
        earnings_growth = info.get("earningsGrowth", 0)
        debt_to_equity = info.get("debtToEquity", 0)
        free_cashflow = info.get("freeCashflow", 0)

        # 배당
        dividend_yield = info.get("dividendYield", 0)

        # 내부자/기관 보유
        insider_pct = info.get("heldPercentInsiders", 0)
        institution_pct = info.get("heldPercentInstitutions", 0)

        # RSI 계산 (14일)
        rsi = _calc_rsi(ticker_yf)

        return {
            "ticker": ticker_yf,
            "name": name,
            "market": market,
            "currency": currency,
            "current_price": current_price,
            "52w_high": high_52w,
            "52w_low": low_52w,
            "52w_position": pos_52w,
            "trend": trend,
            "ma50": ma50,
            "ma200": ma200,
            "rsi_14": rsi,
            "analyst_target_mean": target_mean,
            "analyst_target_high": target_high,
            "analyst_target_low": target_low,
            "analyst_recommendation": recommendation,
            "analyst_count": num_analysts,
            "upside_pct": upside,
            "per": round(per, 2) if per else 0,
            "pbr": round(pbr, 2) if pbr else 0,
            "roe": round(roe * 100, 1) if roe else 0,
            "eps": eps,
            "revenue_growth": round(revenue_growth * 100, 1) if revenue_growth else 0,
            "earnings_growth": round(earnings_growth * 100, 1) if earnings_growth else 0,
            "debt_to_equity": round(debt_to_equity, 1) if debt_to_equity else 0,
            "free_cashflow": free_cashflow,
            "dividend_yield": round(dividend_yield * 100, 2) if dividend_yield else 0,
            "insider_pct": round(insider_pct * 100, 1) if insider_pct else 0,
            "institution_pct": round(institution_pct * 100, 1) if institution_pct else 0,
        }
    except Exception as e:
        logger.warning(f"Yahoo 인사이트 실패 ({ticker_yf}): {e}")
        return {"ticker": ticker_yf, "error": str(e)}


def _calc_rsi(ticker_yf: str, period: int = 14) -> float:
    """RSI 14일 계산"""
    try:
        hist = yf.Ticker(ticker_yf).history(period="1mo")
        if hist.empty or len(hist) < period + 1:
            return 50.0
        closes = hist["Close"].values
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        avg_gain = np.mean(gains[-period:])
        avg_loss = np.mean(losses[-period:])
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - (100 / (1 + rs)), 1)
    except Exception:
        return 50.0


def get_multi_stock_insights(tickers: list[tuple]) -> dict:
    """여러 종목 병렬 인사이트 수집"""
    results = {}
    with ThreadPoolExecutor(max_workers=5) as ex:
        futures = {ex.submit(get_stock_insights, t, m): t for t, m in tickers}
        for fut in as_completed(futures, timeout=60):
            ticker = futures[fut]
            try:
                results[ticker] = fut.result()
            except Exception:
                pass
    return results


def format_insights_for_prompt(insights: dict) -> str:
    """AI 프롬프트용 인사이트 포맷팅"""
    lines = []
    for ticker, data in insights.items():
        if "error" in data:
            continue
        cs = "₩" if data.get("currency") == "KRW" else "$"
        lines.append(
            f"[{data.get('name', ticker)}] RSI:{data.get('rsi_14', 'N/A')} | "
            f"트렌드:{data.get('trend', 'N/A')} | "
            f"목표가:{cs}{data.get('analyst_target_mean', 0):,.0f} "
            f"(상승여력:{data.get('upside_pct', 0):+.1f}%, {data.get('analyst_recommendation', 'N/A')}) | "
            f"PER:{data.get('per', 0)} PBR:{data.get('pbr', 0)} ROE:{data.get('roe', 0)}% | "
            f"매출성장:{data.get('revenue_growth', 0)}% 이익성장:{data.get('earnings_growth', 0)}% | "
            f"내부자:{data.get('insider_pct', 0)}% 기관:{data.get('institution_pct', 0)}%"
        )
    return "\n".join(lines) if lines else "Yahoo 데이터 없음"


def get_support_resistance(ticker_yf: str) -> dict | None:
    """지지/저항선 + 피보나치 계산"""
    try:
        hist = yf.Ticker(ticker_yf).history(period="3mo")
        if hist.empty or len(hist) < 20:
            return None
        highs = hist["High"].values
        lows = hist["Low"].values
        closes = hist["Close"].values
        current = closes[-1]
        recent_high = float(np.max(highs[-20:]))
        recent_low = float(np.min(lows[-20:]))
        diff = recent_high - recent_low
        return {
            "support": round(recent_low, 2),
            "resistance": round(recent_high, 2),
            "fib_23.6": round(recent_high - diff * 0.236, 2),
            "fib_38.2": round(recent_high - diff * 0.382, 2),
            "fib_50.0": round(recent_high - diff * 0.5, 2),
            "fib_61.8": round(recent_high - diff * 0.618, 2),
        }
    except Exception:
        return None
