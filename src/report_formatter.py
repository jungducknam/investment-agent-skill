"""
report_formatter.py — 텔레그램 리포트 메시지 포맷팅
"""
from datetime import datetime
from .config import KST

_STOCK_NAME_LOOKUP = None


def _report_time_label(report: dict) -> str:
    collected_at = report.get("_meta", {}).get("collected_at")
    if not collected_at:
        return "07:00 KST"
    try:
        return datetime.fromisoformat(collected_at).astimezone(KST).strftime("%H:%M KST")
    except Exception:
        return "07:00 KST"


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

    sentiment_score = _format_sentiment_score(ms.get("sentiment_score"))
    sentiment_label = _format_sentiment_label(ms.get("overall_sentiment"), ms.get("sentiment_score"))
    events_str = _format_event_sections(report)
    news_str = _build_news_summary(report)
    data_quality = _safe_md_text(_short_text(ms.get("data_quality_notes", ""), 140))
    data_quality_block = f"\n\n*🧪 데이터 품질*\n{data_quality}" if data_quality else ""

    return f"""📊 *글로벌 투자 리포트* | {report['report_date']} {_report_time_label(report)}
━━━━━━━━━━━━━━━━━━━━

{sentiment_emoji} *시장 심리 {sentiment_score}/10 — {sentiment_label}*
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

*📰 핵심 뉴스 요약*
{news_str}

*🔄 섹터 로테이션*
{ms['sector_rotation']}

*⚠️ 주요 리스크*
{chr(10).join(f'• {r}' for r in ms['risk_factors'])}{data_quality_block}

*📅 이벤트/일정*
{events_str}

*💼 포트폴리오 전략*
• 현금 보유 권장: *{ps['cash_reserve_pct']}%*
• 장기: {ps['long_term_allocation']}
• 스윙: {ps['swing_strategy']}
• 단타: {ps['daytrading_focus']}

💬 *{ps['overall_advice']}*"""


def build_msg2(report: dict) -> str:
    """메시지 2: 실행 위험을 숨기지 않는 액션 플랜."""
    recs = report.get("recommendations") or []
    executable = [rec for rec in recs if _action_bucket(rec) == "executable"]
    conditional = [rec for rec in recs if _action_bucket(rec) == "conditional"]
    waiting = [rec for rec in recs if _action_bucket(rec) == "waiting"]
    waiting.extend(report.get("waiting_list") or [])
    rejected = list(report.get("rejected_candidates") or [])
    watchlist = list(report.get("watchlist") or [])

    visible_keys = _item_key_set(executable + conditional)
    rejected = _dedupe_items_for_display(rejected, seen_keys=visible_keys)
    visible_keys |= _item_key_set(rejected)
    waiting = _dedupe_items_for_display(waiting, seen_keys=visible_keys)
    visible_keys |= _item_key_set(waiting)
    watchlist = _dedupe_items_for_display(watchlist, seen_keys=visible_keys)

    lines = []
    positions = report.get("_positions") or []
    if positions:
        lines.extend(_format_position_management(positions))
        lines.append("")

    lines.extend([
        "🎯 *오늘의 액션 플랜*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"🟢 실행 가능: {len(executable)}개",
        f"🟡 조건부 진입: {len(conditional)}개",
        f"🔵 관심/대기: {len(waiting) + len(watchlist)}개",
        f"🔴 실행금지: {len(rejected)}개",
        "",
    ])

    if executable:
        lines.append("🟢 *실행 가능*")
        for rec in executable[:5]:
            lines.extend(_format_action_rec(rec, "🟢"))
        lines.append("")

    if conditional:
        lines.append("🟡 *조건부 진입*")
        for rec in conditional[:6]:
            lines.extend(_format_action_rec(rec, "🟡"))
        lines.append("")

    if waiting or watchlist:
        lines.append("🔵 *관심/대기*")
        for item in waiting[:6]:
            lines.append(_format_waiting_item(item))
        for item in watchlist[:8]:
            lines.append(f"• {_format_watchlist_item(item)}")
        lines.append("")

    if rejected:
        lines.append("🔴 *실행금지*")
        for item in rejected[:6]:
            lines.extend(_format_rejected_item(item))
        lines.append("")

    if report.get("briefing_only"):
        lines.append("⚠️ 추천 JSON 검증 실패로 브리핑만 제공합니다.")
    lines.append("\n⚠️ _투자 판단은 본인 책임 | Manus Investment Agent_")
    return "\n".join(lines)


def _action_bucket(rec: dict) -> str:
    status = rec.get("action_status")
    if status == "executable" and rec.get("is_executable"):
        return "executable"
    if status == "conditional_entry":
        return "conditional"
    action = rec.get("action")
    if action == "buy_zone" and rec.get("is_executable"):
        return "executable"
    if action == "conditional_buy" or rec.get("risk_gate_status") == "PASS_WITH_CONDITIONS":
        return "conditional"
    return "waiting"


def _format_action_rec(rec: dict, icon: str) -> list[str]:
    currency = rec.get("currency", "")
    entry = _format_price(rec.get("entry_price") or rec.get("current_price"), currency)
    t1 = _format_price(rec.get("target_price_1"), currency)
    sl = _format_price(rec.get("stop_loss"), currency)
    pos = rec.get("position_size_pct", "N/A")
    rr = _format_risk_reward(rec.get("risk_reward_1"))
    condition = _safe_md_text(_short_text(rec.get("entry_condition", ""), 52))
    warning = _format_warning_rules(rec.get("warning_rules") or [])
    name = _safe_md_text(rec.get("name", "N/A"))
    ticker = _safe_md_text(rec.get("ticker", "N/A"))

    lines = [f"{icon} *{rec.get('rank', '-')}. {name}* `{ticker}`"]
    lines.append(f"진입: {entry} · 손절: {sl} · 목표: {t1}")
    rr_text = f" · 손익비: {rr}" if rr else ""
    lines.append(f"비중: {pos}%{rr_text}")
    if condition:
        lines.append(f"조건: {condition}")
    if warning:
        lines.append(f"주의: {warning}")
    return lines


def _format_waiting_item(item: dict) -> str:
    ticker = item.get("ticker", "N/A")
    name = item.get("name")
    label = f"{_safe_md_text(name)} `{ticker}`" if name else f"`{ticker}`"
    reason = _safe_md_text(_short_text(_sanitize_waiting_language(item.get("reason", "")), 58))
    condition = _safe_md_text(_short_text(_sanitize_waiting_language(item.get("condition", "")), 42))
    suffix = f" · 조건: {condition}" if condition else ""
    return f"• {label}: {reason}{suffix}"


def _format_position_management(positions: list[dict]) -> list[str]:
    lines = ["📌 *보유종목 관리*", "━━━━━━━━━━━━━━━━━━━━"]
    for pos in positions[:5]:
        currency = pos.get("currency") or ("KRW" if pos.get("market") == "KR" else "USD")
        name = _safe_md_text(pos.get("name") or "N/A")
        ticker = _safe_md_text(pos.get("ticker") or "N/A")
        current = _format_price(pos.get("current_price"), currency)
        entry = _format_price(pos.get("entry_price"), currency)
        defense = _format_price(pos.get("defense_line"), currency)
        qty = pos.get("quantity", "N/A")
        pnl = _format_pct(pos.get("pnl_pct"), signed=True)
        action = _safe_md_text(pos.get("action") or "관찰")
        add_buy = _safe_md_text(pos.get("add_buy_policy") or "보류")
        lines.append(f"• *{name}* `{ticker}` · {qty}주 · {action}")
        lines.append(f"  평단 {entry} · 현재 {current} · 손익 {pnl}")
        lines.append(f"  방어선 {defense} · 추가매수 {add_buy}")
    return lines


def _sanitize_waiting_language(text: str) -> str:
    cleaned = str(text or "")
    replacements = {
        "조정 시 매수": "관찰가 도달 시 재검토",
        "지지 확인 시 진입": "지지 확인 후 재검토",
        "진입 고려": "재검토",
        "진입": "재검토",
        "매수 관점 접근": "관찰 관점 유지",
        "매수": "재검토",
        "접근": "관찰",
    }
    for src, dst in replacements.items():
        cleaned = cleaned.replace(src, dst)
    return cleaned


def _format_warning_rules(warnings: list[str]) -> str:
    labels = {
        "manual_review_required": "수동 검토 필요",
        "risk_reward_high": "손익비 5R 초과",
        "close_confirmation_required": "종가 확인 필요",
        "regime_executable_limit": "레짐 한도 초과",
        "us_regular_open_confirmation_required": "미국장 개장 확인 필요",
        "us_market_holiday_stale_price": "휴장 후 가격 확인 필요",
        "high_valuation_rate_sensitive": "고PER 금리 민감",
        "missing_catalyst_evidence": "뉴스 근거 부족",
    }
    rendered = [labels.get(str(item), str(item)) for item in warnings[:2]]
    return " · ".join(rendered)


def _build_news_summary(report: dict) -> str:
    detailed = report.get("_detailed_news") or []
    if detailed:
        lines = []
        for idx, item in enumerate(detailed[:6], 1):
            source = item.get("source") or "뉴스"
            published = f" · {item['published']}" if item.get("published") else ""
            themes = ", ".join([item.get("primary_theme", "")] + (item.get("secondary_themes") or []))
            themes = themes.strip(", ") or ", ".join(item.get("themes") or [])
            theme_text = f" · {themes}" if themes else ""
            original_title = str(item.get("title") or item.get("headline") or "")
            title = _safe_md_text(_short_text(item.get("translated_title") or original_title, 86))
            original_title_text = _safe_md_text(_short_text(original_title, 86))
            summary = _safe_md_text(_short_text(item.get("translated_summary") or item.get("summary", ""), 92))
            why = _safe_md_text(_short_text(item.get("investment_implication") or item.get("why", ""), 96))
            channel = _safe_md_text(_short_text(item.get("impact_channel", ""), 72))
            direct = _format_impact_assets(item.get("affected_assets") or [], "direct")
            indirect = _format_impact_assets(item.get("affected_assets") or [], "indirect")
            historical = _format_historical_reaction(item.get("historical_reaction") or {})
            directional_confidence = item.get("directional_confidence_score")
            signal_strength = item.get("trading_signal_strength")
            impact = item.get("impact_score")
            current_reaction = _safe_md_text(_short_text(item.get("current_market_reaction") or "", 72))
            oil_direction = _format_oil_direction(item.get("oil_direction"))
            link = _format_news_link(item.get("link", ""))

            lines.append(f"{idx}. {title}")
            if item.get("translated_title") and original_title_text and original_title_text != title:
                lines.append(f"원제: {original_title_text}")
            lines.append(f"출처: {source}{published}{theme_text}")
            score_bits = []
            if impact is not None:
                score_bits.append(f"영향도: {impact}/100")
            if directional_confidence is not None:
                score_bits.append(f"방향 확신도: {directional_confidence}/100")
            if signal_strength:
                score_bits.append(f"거래 신호 강도: {signal_strength}")
            if score_bits:
                lines.append(" · ".join(score_bits))
            if summary:
                lines.append(f"요약: {summary}")
            lines.append(f"영향경로: {channel or why}")
            if oil_direction:
                lines.append(f"원유방향: {oil_direction}")
            if direct:
                lines.append(f"직접영향: {direct}")
            if indirect:
                lines.append(f"간접영향: {indirect}")
            if historical:
                lines.append(f"과거반응: {historical}")
            if current_reaction:
                lines.append(f"현재반응: {current_reaction}")
            if link:
                lines.append(f"원문: {link}")
            lines.append("")
        return "\n".join(lines).rstrip()

    lines = []
    for headline in (report.get("_news_headlines") or [])[:3]:
        lines.append(f"• 오늘: {_short_text(headline, 64)}")

    theme_news = report.get("_theme_news") or {}
    for theme in ("반도체", "AI", "전력인프라", "방산", "미중무역"):
        items = theme_news.get(theme) or []
        if items:
            lines.append(f"• {theme}: {_short_text(items[0], 58)}")
        if len(lines) >= 5:
            break

    historical = _historical_news_lines(report.get("_historical_news_context", ""))
    for item in historical[:2]:
        lines.append(f"• 누적: {_short_text(item, 62)}")

    return "\n".join(lines[:6]) if lines else "• 수집 뉴스 없음"


def _format_news_link(url: str) -> str:
    url = str(url or "").strip()
    if not url:
        return ""
    safe_url = url.replace(" ", "%20").replace(")", "%29").replace("(", "%28")
    return f"[열기]({safe_url})"


def _format_impact_assets(assets: list[dict], scope: str) -> str:
    labels = []
    for asset in assets:
        if asset.get("impact_scope") != scope:
            continue
        direction = {"positive": "+", "negative": "-", "neutral": "0", "uncertain": "?"}.get(asset.get("direction"), "?")
        labels.append(f"{asset.get('asset')}({direction})")
    return ", ".join(labels[:5])


def _format_historical_reaction(history: dict) -> str:
    count = int(history.get("similar_event_count", 0) or 0)
    if count < 5 or history.get("sample_sufficient") is False:
        return "표본 부족, 참고 불가"
    ar1d = _to_float(history.get("median_abnormal_return_1d")) or 0.0
    hit = (_to_float(history.get("directional_hit_rate")) or 0.0) * 100
    return f"유사 {count}건 · 1일 초과수익 {ar1d:+.1f}% · 방향적중 {hit:.0f}%"


def _format_oil_direction(oil_direction: dict | None) -> str:
    if not isinstance(oil_direction, dict):
        return ""
    short_term = oil_direction.get("short_term_label")
    structural = oil_direction.get("structural_risk_label")
    if not short_term and not structural:
        return ""
    return f"단기 유가 {short_term or '확인 필요'} · 구조적 에너지 리스크 {structural or '확인 필요'}"


def _safe_md_text(text: str) -> str:
    return str(text).replace("[", "(").replace("]", ")").replace("*", "").replace("`", "'")


def _format_watchlist_item(item) -> str:
    if isinstance(item, dict):
        ticker = str(item.get("ticker") or item.get("symbol") or "").strip()
        name = str(item.get("name") or "").strip() or _lookup_stock_name(ticker)
    else:
        ticker = str(item or "").strip()
        name = _lookup_stock_name(ticker)

    if name and ticker and _normalize_ticker_key(name) != _normalize_ticker_key(ticker):
        return f"{_safe_md_text(name)} `{_safe_md_text(ticker)}`"
    return _safe_md_text(name or ticker or "N/A")


def _format_rejected_item(item) -> list[str]:
    if isinstance(item, dict):
        ticker = str(item.get("ticker") or "N/A")
        name = item.get("name")
        label = f"{_safe_md_text(name)} `{_safe_md_text(ticker)}`" if name else f"`{_safe_md_text(ticker)}`"
        reason_labels = _reject_reason_labels(item)
    else:
        label = _format_watchlist_item(item)
        reason_labels = []

    if not reason_labels:
        reason_labels = ["사유 미상"]
    return [f"• {label}: 리스크 게이트 미통과: {' · '.join(reason_labels[:5])}"]


def _lookup_stock_name(ticker: str) -> str:
    keys = _ticker_lookup_keys(ticker)
    if not keys:
        return ""
    lookup = _stock_name_lookup()
    for key in keys:
        if key in lookup:
            return lookup[key]
    return ""


def _stock_name_lookup() -> dict[str, str]:
    global _STOCK_NAME_LOOKUP
    if _STOCK_NAME_LOOKUP is not None:
        return _STOCK_NAME_LOOKUP

    lookup = {}
    try:
        from .data_market import STOCK_UNIVERSE
    except Exception:
        STOCK_UNIVERSE = []

    for stock in STOCK_UNIVERSE:
        name = str(stock.get("name") or "").strip()
        if not name:
            continue
        for ticker in (stock.get("ticker"), stock.get("yf")):
            for key in _ticker_lookup_keys(ticker):
                lookup[key] = name

    _STOCK_NAME_LOOKUP = lookup
    return lookup


def _item_key_set(items: list) -> set[str]:
    keys = set()
    for item in items or []:
        keys.update(_identity_keys_for_item(item))
    return keys


def _dedupe_items_for_display(items: list, seen_keys: set[str] | None = None) -> list:
    seen = set(seen_keys or set())
    result = []
    for item in items or []:
        keys = _identity_keys_for_item(item)
        if keys and seen & keys:
            continue
        seen.update(keys)
        result.append(item)
    return result


def _identity_keys_for_item(item) -> set[str]:
    if isinstance(item, dict):
        raw = item.get("ticker") or item.get("symbol") or item.get("name") or ""
    else:
        raw = item
    keys = _ticker_lookup_keys(str(raw or ""))
    name = _lookup_stock_name(str(raw or ""))
    if name:
        keys.add(_normalize_ticker_key(name))
    raw_norm = _normalize_ticker_key(str(raw or ""))
    lookup = _stock_name_lookup()
    for ticker_key, stock_name in lookup.items():
        if raw_norm and raw_norm == _normalize_ticker_key(stock_name):
            keys.update(_ticker_lookup_keys(ticker_key))
            keys.add(_normalize_ticker_key(stock_name))
    return {key for key in keys if key}


def _ticker_lookup_keys(ticker: str) -> set[str]:
    raw = _normalize_ticker_key(ticker)
    if not raw:
        return set()

    keys = {raw}
    base = raw.split(".", 1)[0]
    keys.add(base)
    if base.isdigit():
        keys.add(base.zfill(6))
    return keys


def _normalize_ticker_key(value: str) -> str:
    return str(value or "").strip().upper()


def _historical_news_lines(text: str) -> list[str]:
    lines = []
    for line in str(text or "").splitlines():
        cleaned = line.strip()
        if cleaned.startswith("• "):
            lines.append(cleaned[2:].strip())
    return lines


def _format_price(value, currency: str) -> str:
    price = _to_float(value)
    if price is None:
        if value in (None, ""):
            return "N/A"
        return str(value)

    if currency == "KRW":
        return f"₩{price:,.0f}"
    if price >= 100:
        return f"${price:,.0f}"
    return f"${price:,.2f}"


def _first_rationale(rec: dict) -> str:
    rationale = rec.get("investment_rationale") or []
    if isinstance(rationale, list) and rationale:
        return str(rationale[0])
    return str(rationale)


def _short_text(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    if len(text) <= limit:
        return text
    return text[: max(limit - 3, 0)].rstrip() + "..."


def _to_float(value) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    return price


def _format_sentiment_score(value) -> str:
    score = _to_float(value)
    if score is None:
        return str(value or "N/A")
    if score > 10:
        score = score / 10.0
    score = max(0.0, min(10.0, score))
    return f"{score:.1f}".rstrip("0").rstrip(".")


def _format_sentiment_label(sentiment: str, score_value) -> str:
    score = _to_float(score_value)
    label = str(sentiment or "중립")
    if score is not None:
        if score > 10:
            score = score / 10.0
        if label == "중립" and 4.0 <= score < 5.0:
            return "중립 하단 / 관망"
    return label


def _format_event_sections(report: dict) -> str:
    sections = report.get("_event_sections") or {}
    if not sections:
        events = report.get("_events", [])
        return chr(10).join(f"• {e}" for e in events[:5]) or "• 이번 주 주요 이벤트 확인 필요"

    labels = {
        "this_week": "이번 주",
        "next_major": "다음 주요 이벤트",
        "market_schedule": "시장 일정 주의",
    }
    lines = []
    for key in ("this_week", "next_major", "market_schedule"):
        items = [str(item) for item in sections.get(key) or [] if item]
        if not items:
            continue
        lines.append(f"{labels[key]}:")
        lines.extend(f"• {item}" for item in items[:5])
    return "\n".join(lines) if lines else "• 이번 주 주요 이벤트 확인 필요"


def _format_pct(value, signed: bool = False) -> str:
    pct = _to_float(value)
    if pct is None:
        return "N/A"
    prefix = "+" if signed and pct > 0 else ""
    return f"{prefix}{pct:.1f}%"


def _format_confidence(value) -> str:
    pct = _to_float(value)
    if pct is None:
        return ""
    return f"{round(pct):.0f}/100"


def _format_action_status(rec: dict) -> str:
    action = rec.get("action")
    executable = bool(rec.get("is_executable"))
    gate_status = rec.get("risk_gate_status")
    if gate_status == "FAIL":
        return "검토필요"
    if action == "conditional_buy" and executable:
        return "조건부 실행가능"
    if action == "buy_zone" and executable:
        return "실행가능"
    if action in ("wait", "avoid"):
        return "대기"
    if executable:
        return "실행가능"
    return "대기"


def _format_risk_reward(value) -> str:
    rr = _to_float(value)
    if rr is None or rr <= 0:
        return ""
    return f"{rr:.1f}R"


def _format_reject_reason(reason: str) -> str:
    return {
        "overbought": "과열",
        "poor_rr": "손익비 미달",
        "data_low_quality": "데이터 품질 낮음",
        "price_unavailable": "가격 근거 부족",
        "missing_evidence": "근거 부족",
        "above_entry": "진입가 초과",
        "event_risk_48h": "이벤트 리스크",
        "stop_invalidation_conflict": "손절/무효화 충돌",
        "risk_reward_abnormally_high": "손익비 이상치",
        "data_quality_fail": "데이터 품질 실패",
        "crisis_no_new_buy": "위기 레짐 매수 금지",
    }.get(reason, "리스크 게이트 미통과")


def _reject_reason_labels(item: dict) -> list[str]:
    codes = []
    for key in ("reason", "failed_rules", "execution_blockers", "details", "warning_rules"):
        value = item.get(key)
        if isinstance(value, list):
            codes.extend(str(part) for part in value)
        elif value:
            codes.extend(part.strip() for part in str(value).replace(";", ",").split(","))

    labels = []
    for code in codes:
        label = _reject_code_label(code)
        if label and label not in labels:
            labels.append(label)
    if len(labels) > 1 and "리스크 게이트 미통과" in labels:
        labels = [label for label in labels if label != "리스크 게이트 미통과"]
    return labels


def _reject_code_label(code: str) -> str:
    code = str(code or "").strip()
    labels = {
        "overbought": "과열",
        "poor_rr": "손익비 부족",
        "data_low_quality": "데이터 품질 낮음",
        "data_quality_fail": "데이터 품질 낮음",
        "data_quality_watch_only": "데이터 품질 낮음",
        "price_unavailable": "가격 근거 부족",
        "missing_evidence": "근거 부족",
        "above_entry": "진입가 초과",
        "event_risk_48h": "이벤트 리스크",
        "stop_invalidation_conflict": "손절/무효화 충돌",
        "risk_reward_abnormally_high": "손익비 이상치",
        "crisis_no_new_buy": "레짐 불일치",
        "regime_executable_limit": "레짐 한도 초과",
        "intraday_report_close_confirmation_required": "종가 확인 필요",
        "us_market_holiday_stale_price": "휴장 후 가격 확인 필요",
        "us_regular_open_confirmation_required": "미국장 개장 확인 필요",
        "high_valuation_rate_sensitive": "고PER 금리 민감",
        "missing_catalyst_evidence": "뉴스 근거 부족",
        "risk_gate_failed": "리스크 게이트 미통과",
    }
    if code in labels:
        return labels[code]
    lowered = code.lower()
    if "event" in lowered or "fomc" in lowered or "cpi" in lowered or "실적" in lowered:
        return "이벤트 리스크"
    if "data" in lowered or "quality" in lowered:
        return "데이터 품질 낮음"
    if "regime" in lowered or "위기" in lowered:
        return "레짐 불일치"
    if "risk" in lowered and "reward" in lowered:
        return "손익비 문제"
    if "리스크 게이트" in code:
        return "리스크 게이트 미통과"
    return ""


def _downside_pct(current_price, stop_loss) -> str:
    current = _to_float(current_price)
    stop = _to_float(stop_loss)
    if not current or stop is None:
        return "N/A"
    return f"-{round((current - stop) / current * 100, 1)}%"
