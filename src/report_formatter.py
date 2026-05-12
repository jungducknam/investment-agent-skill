"""
report_formatter.py — 텔레그램 리포트 메시지 포맷팅
"""
from datetime import datetime
from .config import KST


def build_msg1(report: dict) -> str:
    """메시지 1: 시장 현황 + 전략 요약"""
    ms = report["market_summary"]
    ps = report["portfolio_strategy"]
    sentiment_emoji = {"강세": "🟢", "약세": "🔴", "중립": "🟡"}.get(ms["overall_sentiment"], "🟡")

    idx = report.get("_indices", {})

    def _idx(name, default="N/A"):
        d = idx.get(name, {})
        if not d or d.get("price") is None:
            return default
        p = d["price"]
        chg = d.get("change_pct", 0) or 0
        return f"{p:,.0f}  {chg:+.2f}%"

    kospi_s = _idx("KOSPI")
    kosdaq_s = _idx("KOSDAQ")
    sp500_s = _idx("SP500")
    nasdaq_s = _idx("NASDAQ")
    usdkrw_s = _idx("USD_KRW")
    us10y_s = str(idx.get("US10Y", {}).get("price", "N/A")) + "%"
    brent_s = "$" + str(idx.get("BRENT", {}).get("price", "N/A"))

    events = report.get("_events", [])
    events_str = chr(10).join(f'• {e}' for e in events[:5])

    return f"""📊 *글로벌 투자 리포트* | {report['report_date']} 06:00 KST
━━━━━━━━━━━━━━━━━━━━

{sentiment_emoji} *시장 심리 {ms['sentiment_score']}/10 — {ms['overall_sentiment']}*
💡 {ms['key_theme']}

*📈 실시간 시장 현황*
```
지수          현재가      등락
KOSPI    {kospi_s}
KOSDAQ   {kosdaq_s}
S&P500   {sp500_s}
NASDAQ   {nasdaq_s}
USD/KRW  {usdkrw_s}
미국10Y   {us10y_s}
브렌트유  {brent_s}
```

*🔄 섹터 로테이션*
{ms['sector_rotation']}

*⚠️ 주요 리스크*
{chr(10).join(f'• {r}' for r in ms['risk_factors'])}

*📅 이번 주 이벤트*
{events_str}

*💼 포트폴리오 전략*
• 현금 보유 권장: *{ps['cash_reserve_pct']}%*
• 장기: {ps['long_term_allocation']}
• 스윙: {ps['swing_strategy']}
• 단타: {ps['daytrading_focus']}

💬 *{ps['overall_advice']}*"""


def build_msg2(report: dict) -> str:
    """메시지 2: 10개 종목 압축 요약"""
    recs = report["recommendations"]
    style_icon = {"장기": "📦", "스윙": "🔄", "단타": "⚡"}
    market_icon = {"KR": "🇰🇷", "US": "🇺🇸"}

    lines = ["🎯 *오늘의 추천 종목 TOP 10*\n━━━━━━━━━━━━━━━━━━━━\n"]
    lines.append("```")
    lines.append(f"{'#':<2} {'종목':<14} {'현재가':>9} {'목표1':>8} {'목표2':>8} {'손절':>8} {'비중':>4} {'기간'}")
    lines.append("─" * 65)

    for rec in recs:
        cur = rec["current_price"]
        t1 = rec["target_price_1"]
        t2 = rec["target_price_2"]
        sl = rec["stop_loss"]
        pos = rec["position_size_pct"]
        period = rec["holding_period"].replace("개월 이상", "M+").replace("1~4주", "1-4W").replace("1~5일", "1-5D")

        if rec["currency"] == "KRW":
            cur_s = f"₩{cur // 1000}K"
            t1_s = f"₩{t1 // 1000}K"
            t2_s = f"₩{t2 // 1000}K"
            sl_s = f"₩{sl // 1000}K"
        else:
            cur_s = f"${cur}"
            t1_s = f"${t1}"
            t2_s = f"${t2}"
            sl_s = f"${sl}"

        name_short = rec["name"][:10]
        lines.append(f"{rec['rank']:<2} {name_short:<14} {cur_s:>9} {t1_s:>8} {t2_s:>8} {sl_s:>8} {pos:>3}% {period}")

    lines.append("```\n")
    lines.append("*📌 핵심 투자 근거 (한 줄 요약)*\n")

    for rec in recs:
        si = style_icon.get(rec["style"], "•")
        mi = market_icon.get(rec["market"], "")
        stop_pct = round((rec["current_price"] - rec["stop_loss"]) / rec["current_price"] * 100, 1)
        lines.append(
            f"{si}{mi} *{rec['rank']}. {rec['name']}* `{rec['ticker']}` "
            f"↑+{rec['upside_pct']}% / ↓-{stop_pct}% | {rec['investment_rationale'][0]}"
        )

    lines.append(f"\n👀 *관심 종목:* {' | '.join(report.get('watchlist', []))}")
    lines.append("\n⚠️ _투자 판단은 본인 책임 | Manus Investment Agent_")
    return "\n".join(lines)
