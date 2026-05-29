"""
news_classifier.py — financial news classification by economic transmission.

The classifier is deterministic by default. It deliberately prefers macro and
economic channels over broad keyword tags so oil/Fed/consumer stories do not
collapse into generic AI or semiconductor labels just because those words appear
in the text.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any

from .config import KST


PRIMARY_THEMES = [
    "rates_policy",
    "energy_inflation",
    "geopolitical_risk",
    "semiconductor",
    "ai_infrastructure",
    "earnings_guidance",
    "defense",
    "shipbuilding_industrials",
    "power_grid_electrification",
    "currency_fx",
    "macro_growth",
    "market_flow",
    "consumer",
    "autos",
    "battery",
    "bio_healthcare",
    "financials",
    "legal_regulatory",
    "company_specific",
    "market_schedule",
    "irrelevant_market_news",
]


THEME_IMPLICATIONS = {
    "rates_policy": "금리와 할인율 변화는 성장주, 채권, 달러, 금융주, 부동산 섹터에 영향을 줍니다.",
    "energy_inflation": "유가 상승은 인플레이션 압력, 소비 둔화, 금리 상승 우려를 통해 성장주에는 부담이 될 수 있고 에너지 섹터에는 우호적일 수 있습니다.",
    "geopolitical_risk": "지정학 리스크는 위험자산 전반에는 부담이지만 방산·에너지 섹터에는 상대 강도 요인이 될 수 있습니다.",
    "semiconductor": "반도체 뉴스는 HBM, 메모리, 장비, 파운드리, AI 서버 체인과 한국/미국 기술주 수급에 영향을 줍니다.",
    "ai_infrastructure": "AI 인프라 뉴스는 GPU, 데이터센터, 전력망, HBM, 네트워크 장비, 클라우드 투자 사이클에 영향을 줍니다.",
    "earnings_guidance": "실적과 가이던스 변화는 해당 기업 및 동종 업종의 이익 추정치와 밸류에이션에 영향을 줍니다.",
    "defense": "방산 뉴스는 수주 기대, 국방비 확대, 지정학 프리미엄과 연결됩니다.",
    "shipbuilding_industrials": "조선·산업재 뉴스는 수주잔고, 선가, 원가, 환율, 글로벌 물동량 기대와 연결됩니다.",
    "power_grid_electrification": "전력 인프라 뉴스는 데이터센터 전력 수요, 송배전 투자, 발전설비, 전력기기 밸류에이션에 영향을 줍니다.",
    "currency_fx": "환율 뉴스는 외국인 수급, 수입물가, 수출주 마진, 달러 자산 선호에 영향을 줍니다.",
    "macro_growth": "성장률과 경기 지표는 실적 전망, 금리 경로, 위험자산 선호에 영향을 줍니다.",
    "market_flow": "시장 수급과 지수 흐름은 단기 리스크 선호와 섹터 로테이션 판단에 영향을 줍니다.",
    "consumer": "소비재 뉴스는 주로 해당 기업과 소비 섹터에 영향을 주며, 시장 전체 영향은 제한적일 수 있습니다.",
    "autos": "자동차 뉴스는 완성차, 부품, 배터리, 자율주행 밸류체인에 영향을 줍니다.",
    "battery": "배터리 뉴스는 2차전지 소재, 셀, 장비, 전기차 수요 전망에 영향을 줍니다.",
    "bio_healthcare": "바이오·헬스케어 뉴스는 규제 승인, 임상, 약가, 방어적 수요와 연결됩니다.",
    "financials": "금융 뉴스는 금리, 신용, 자본비율, 경기 민감도와 연결됩니다.",
    "legal_regulatory": "법률·규제 뉴스는 해당 기업과 관련 산업의 비용, 사업모델, 밸류에이션에 영향을 줄 수 있습니다.",
    "company_specific": "개별 기업 뉴스로 시장 전체보다는 해당 종목 중심의 영향이 큽니다.",
    "market_schedule": "휴장·개장 일정 뉴스는 핵심 투자 뉴스가 아니라 가격 신선도와 주문 가능 시간 점검에 사용합니다.",
    "irrelevant_market_news": "투자 리포트 핵심 뉴스로 보기 어렵습니다.",
}


SOURCE_SCORES = {
    "Reuters_Biz": 95,
    "Reuters": 95,
    "Bloomberg": 95,
    "Fed": 100,
    "BOK": 100,
    "CNBC_Top": 82,
    "CNBC": 82,
    "MarketWatch": 78,
    "Yahoo_Finance": 75,
    "Yonhap": 88,
}


OIL_TERMS = ["oil", "crude", "brent", "wti", "opec", "유가", "원유", "브렌트"]
OIL_FALSE_POSITIVE_TERMS = ["oil change", "oil changes"]
OIL_MARKET_TERMS = ["crude", "brent", "wti", "opec", "oil price", "oil prices", "oil market", "원유", "유가", "브렌트"]
FED_TERMS = ["fed", "fomc", "interest rate", "interest rates", "rate", "rates", "yield", "yields", "bond", "bonds", "treasury", "treasuries", "연준", "금리", "국채", "채권"]
CHIP_TERMS = ["semiconductor", "chip", "hbm", "memory", "foundry", "gpu", "wafer", "반도체", "메모리", "파운드리"]
AI_INFRA_TERMS = ["data center", "datacenter", "gpu", "hbm", "ai server", "ai demand", "cloud capex", "inference", "coreweave", "전력망", "데이터센터"]
CONSUMER_TERMS = ["lululemon", "retail", "apparel", "consumer", "소비재", "의류"]
COMPANY_FINANCING_TERMS = ["bond sale", "debt sale", "stock sale", "company stock", "stock rating", "price target", "board letter"]
MARKET_SCHEDULE_TERMS = ["memorial day", "market holiday", "market closed", "stock market open", "stock market closed", "휴장", "개장 일정"]


def classify_news_items(news_items: list[dict[str, Any]], context: dict | None = None) -> list[dict[str, Any]]:
    return [classify_news_item(item, context=context) for item in news_items or []]


def classify_news_item(item: dict[str, Any], context: dict | None = None) -> dict[str, Any]:
    headline = str(item.get("headline") or item.get("title") or "").strip()
    summary = str(item.get("summary") or "").strip()
    source = str(item.get("source") or item.get("feed_name") or "").strip()
    text = f"{headline} {summary}".lower()

    primary, secondary, why_not = _choose_theme(text)
    validation_errors = validate_news_classification(text, primary, secondary)
    if validation_errors:
        primary = _fallback_theme_for_validation(text, primary)
        secondary = [theme for theme in secondary if theme != primary][:3]
        validation_errors = validate_news_classification(text, primary, secondary)

    market_relevance = _market_relevance(primary, text)
    event_type = _event_type(primary)
    event_status = _event_status(text)
    sentiment = _sentiment(text, primary)
    affected_assets = _affected_assets(primary, secondary, text)
    oil_direction = _oil_direction_summary(text) if primary == "energy_inflation" else None
    confidence = _confidence(primary, secondary, validation_errors, text)
    novelty = _novelty_score(text, event_status)
    urgency = _urgency_score(primary, text)

    validation_status = "FAIL" if validation_errors else "PASS"
    report_priority = _report_priority(primary, market_relevance, validation_status, text)
    should_include = validation_status != "FAIL" and report_priority != "exclude"

    result = dict(item)
    result.update({
        "news_id": item.get("news_id") or _news_id(headline, item.get("link") or item.get("url") or ""),
        "headline": headline,
        "title": headline,
        "source": source,
        "published_at_kst": _published_at_kst(item),
        "url": item.get("url") or item.get("link") or "",
        "is_market_relevant": market_relevance >= 40,
        "market_relevance_score": market_relevance,
        "primary_theme": primary,
        "secondary_themes": secondary[:3],
        "event_type": event_type,
        "event_status": event_status,
        "sentiment": sentiment,
        "novelty_score": novelty,
        "urgency_score": urgency,
        "affected_assets": affected_assets,
        "oil_direction": oil_direction,
        "investment_implication": THEME_IMPLICATIONS[primary],
        "why_not_other_themes": why_not,
        "should_include_in_report": should_include,
        "report_priority": report_priority,
        "confidence": confidence,
        "classification_confidence": round(confidence / 100, 2),
        "validation_status": validation_status,
        "validation_errors": validation_errors,
        "themes": [primary, *secondary[:3]],
        "why": THEME_IMPLICATIONS[primary],
    })
    return result


def validate_news_classification(text: str, primary_theme: str, secondary_themes: list[str]) -> list[str]:
    errors = []
    themes = [primary_theme, *secondary_themes]
    has_oil_terms = _has_any(text, OIL_TERMS) and not _is_oil_change_consumer_story(text)
    has_fed_terms = _has_any(text, FED_TERMS)
    has_company_financing_terms = _has_any(text, COMPANY_FINANCING_TERMS)

    if has_oil_terms and primary_theme not in {"energy_inflation", "geopolitical_risk", "currency_fx"}:
        errors.append("oil_news_wrong_theme")
    fed_allowed = {"rates_policy", "macro_growth", "currency_fx"}
    if has_oil_terms:
        fed_allowed.update({"energy_inflation", "geopolitical_risk"})
    if has_company_financing_terms:
        fed_allowed.update({"company_specific", "financials"})
    if has_fed_terms and primary_theme not in fed_allowed:
        errors.append("fed_news_wrong_theme")
    if "lululemon" in text and primary_theme in {"semiconductor", "ai_infrastructure"}:
        errors.append("consumer_news_misclassified_as_tech")
    if primary_theme == "semiconductor" and not _has_any(text, CHIP_TERMS):
        errors.append("semiconductor_without_chip_evidence")
    if primary_theme == "ai_infrastructure" and not _has_any(text, AI_INFRA_TERMS):
        errors.append("ai_without_infrastructure_evidence")
    if primary_theme not in PRIMARY_THEMES:
        errors.append("unknown_primary_theme")
    if len(secondary_themes) > 3:
        errors.append("too_many_secondary_themes")
    if themes.count(primary_theme) > 1:
        errors.append("primary_duplicated_in_secondary")
    return errors


def _choose_theme(text: str) -> tuple[str, list[str], list[str]]:
    why_not = []
    secondary = []

    if _has_any(text, MARKET_SCHEDULE_TERMS):
        return "market_schedule", secondary, why_not

    if _is_oil_change_consumer_story(text):
        why_not.extend(["auto_service_news_not_energy_inflation", "auto_service_news_not_ai"])
        return "autos", _append_unique(secondary, ["consumer"]), why_not

    if _has_any(text, ["gold", "금"]) and _has_any(text, ["dollar", "달러", "usd"]):
        return "currency_fx", _append_unique(secondary, ["energy_inflation" if _has_any(text, OIL_TERMS) else ""]), why_not

    if _has_any(text, OIL_TERMS):
        why_not.extend(["oil_news_not_ai", "oil_news_not_semiconductor"])
        secondary = _append_unique(secondary, ["geopolitical_risk" if _has_geo(text) else "", "rates_policy"])
        return "energy_inflation", secondary, why_not

    if _has_any(text, COMPANY_FINANCING_TERMS):
        return "company_specific", _append_unique(secondary, ["financials"]), why_not

    if _has_any(text, FED_TERMS) or _has_any(text, ["cpi", "ppi", "inflation", "인플레이션", "물가", "고용"]):
        why_not.extend(["fed_news_not_ai", "fed_news_not_semiconductor"])
        secondary = _append_unique(secondary, ["currency_fx", "market_flow"])
        return "rates_policy", secondary, why_not

    if _has_any(text, CONSUMER_TERMS):
        secondary = _append_unique(secondary, ["company_specific", "legal_regulatory" if _has_legal(text) else ""])
        why_not.extend(["consumer_news_not_ai", "consumer_news_not_semiconductor"])
        return "consumer", secondary, why_not

    if _has_geo(text):
        secondary = _append_unique(secondary, ["energy_inflation", "defense"])
        return "geopolitical_risk", secondary, why_not

    if _has_any(text, ["earnings", "guidance", "revenue", "profit", "실적", "가이던스"]):
        return "earnings_guidance", _append_unique(secondary, ["company_specific"]), why_not

    if _has_any(text, ["defense", "missile", "aerospace", "방산", "국방"]):
        return "defense", _append_unique(secondary, ["geopolitical_risk"]), why_not

    if _has_any(text, ["shipbuilding", "shipyard", "industrial", "조선", "산업재"]):
        return "shipbuilding_industrials", secondary, why_not

    if _has_any(text, ["grid", "electricity", "power", "transformer", "전력", "송배전"]):
        return "power_grid_electrification", _append_unique(secondary, ["ai_infrastructure"] if "data center" in text else []), why_not

    if _has_any(text, CHIP_TERMS):
        return "semiconductor", _append_unique(secondary, ["ai_infrastructure"] if _has_any(text, AI_INFRA_TERMS) else []), why_not

    if _has_any(text, AI_INFRA_TERMS):
        return "ai_infrastructure", _append_unique(secondary, ["semiconductor"] if _has_any(text, CHIP_TERMS) else []), why_not

    if _has_any(text, ["currency", "dollar", "yen", "won", "usd/krw", "환율", "달러", "엔화", "원화"]):
        return "currency_fx", _append_unique(secondary, ["rates_policy"]), why_not

    if _has_any(text, ["gdp", "growth", "recession", "manufacturing", "성장", "경기", "침체"]):
        return "macro_growth", secondary, why_not

    if _has_any(text, ["nasdaq", "s&p", "kospi", "kosdaq", "stocks", "증시", "수급"]):
        return "market_flow", secondary, why_not

    if _has_legal(text):
        return "legal_regulatory", _append_unique(secondary, ["company_specific"]), why_not

    return "irrelevant_market_news", secondary, why_not


def _affected_assets(primary: str, secondary: list[str], text: str) -> list[dict[str, Any]]:
    oil_direction = _oil_direction_summary(text)
    mapping = {
        "energy_inflation": [
            ("BRENT", "commodity", "direct", oil_direction["short_term_direction"], 88, oil_direction["short_term_channel"]),
            ("energy_risk", "macro_factor", "indirect", oil_direction["structural_risk_direction"], 72, oil_direction["structural_risk_channel"]),
            ("XLE", "sector", "direct", oil_direction["energy_equity_direction"], 68, "energy equity readthrough depends on short-term oil price and supply-risk premium"),
            ("NASDAQ", "index", "indirect", "negative", 68, "inflation pressure -> higher rates -> growth valuation pressure"),
            ("consumer", "sector", "indirect", "negative", 55, "higher fuel and input costs pressure discretionary spending"),
        ],
        "rates_policy": [
            ("US10Y", "rate", "direct", "uncertain", 82, "policy repricing changes the yield curve"),
            ("NASDAQ", "index", "indirect", "negative", 70, "higher discount rates pressure long-duration growth stocks"),
            ("USD/KRW", "currency", "indirect", "uncertain", 62, "rate differential changes dollar and won demand"),
        ],
        "currency_fx": _currency_fx_assets(text),
        "geopolitical_risk": [
            ("BRENT", "commodity", "direct", "positive", 70, "supply risk premium"),
            ("defense", "sector", "direct", "positive", 75, "defense spending and risk premium"),
            ("risk_assets", "sector", "indirect", "negative", 60, "risk-off positioning"),
        ],
        "semiconductor": [
            ("SOXX", "sector", "direct", "uncertain", 78, "semiconductor supply-demand repricing"),
            ("NVDA", "ticker", "direct", "uncertain", 70, "AI server and GPU value-chain sensitivity"),
            ("005930.KS", "ticker", "direct", "uncertain", 65, "memory and foundry readthrough"),
            ("000660.KS", "ticker", "direct", "uncertain", 68, "HBM and memory readthrough"),
        ],
        "ai_infrastructure": [
            ("NVDA", "ticker", "direct", "positive", 78, "GPU and accelerator demand"),
            ("GEV", "ticker", "indirect", "positive", 65, "data-center power demand"),
            ("267260.KS", "ticker", "indirect", "positive", 62, "power equipment demand"),
            ("010120.KS", "ticker", "indirect", "positive", 62, "grid investment demand"),
        ],
        "consumer": [
            ("consumer", "sector", "direct", "uncertain", 45, "consumer company-specific sentiment"),
        ],
        "autos": [
            ("autos", "sector", "direct", "uncertain", 45, "auto service and discretionary spending readthrough"),
            ("consumer", "sector", "indirect", "uncertain", 35, "consumer maintenance cost pressure"),
        ],
        "market_schedule": [
            ("US_market_session", "market_schedule", "direct", "neutral", 90, "holiday/open schedule affects tradability and price freshness"),
        ],
    }
    items = mapping.get(primary, [("market", "index", "indirect", "uncertain", 40, "limited market readthrough")])
    return [
        {
            "asset": asset,
            "asset_type": asset_type,
            "impact_scope": scope,
            "direction": direction,
            "impact_strength": strength,
            "channel": channel,
        }
        for asset, asset_type, scope, direction, strength, channel in items
    ]


def _currency_fx_assets(text: str) -> list[tuple[str, str, str, str, int, str]]:
    dollar_direction = "negative" if _dollar_is_easing(text) else "uncertain"
    gold_direction = "positive" if _has_any(text, ["gold rises", "gold climbs", "gold gains", "금 상승"]) else "uncertain"
    oil_direction = _oil_direction_summary(text)["short_term_direction"] if _has_any(text, OIL_TERMS) else "uncertain"
    return [
        ("GOLD", "commodity", "direct", gold_direction, 78, "gold price move reflects dollar and safe-haven repricing"),
        ("USD", "currency", "direct", dollar_direction, 74, "dollar move changes FX and commodity translation"),
        ("BRENT", "commodity", "direct", oil_direction, 55, "oil leg is separated from the FX/gold headline"),
    ]


def _dollar_is_easing(text: str) -> bool:
    if _has_any(text, ["dollar ease", "dollar eases", "dollar falls", "dollar weakens", "달러 약세"]):
        return True
    if not _has_any(text, ["dollar", "usd", "greenback", "달러"]):
        return False
    return _has_any(text, ["eased", "ease", "lower", "falls", "fell", "weakens", "weakened", "softens"])


def _oil_direction_summary(text: str) -> dict[str, str]:
    explicit_oil_down = _has_any(text, [
        "oil ease",
        "oil eases",
        "oil eased",
        "crude lower",
        "crude oil eased",
        "crude oil eases",
        "crude edges lower",
        "oil edges lower",
    ])
    down_terms = [
        "edges lower",
        "edge lower",
        "lower",
        "falls",
        "drops",
        "slips",
        "declines",
        "ease",
        "eased",
        "eases",
        "retreats",
        "하락",
        "약세",
    ]
    up_terms = [
        "jumps",
        "spikes",
        "surges",
        "rises",
        "higher",
        "climbs",
        "급등",
        "상승",
        "강세",
    ]
    structural_terms = [
        "supply risk",
        "middle east",
        "iran",
        "opec",
        "tipping point",
        "geopolitical",
        "공급 차질",
        "중동",
        "지정학",
    ]
    has_down = _has_any(text, down_terms)
    has_up = _has_any(text, up_terms)
    has_structural = _has_any(text, structural_terms)

    if explicit_oil_down:
        short_term = "negative"
        short_channel = "short-term crude price is lower or fading"
        energy_equity = "neutral"
    elif has_down and not has_up:
        short_term = "negative"
        short_channel = "short-term crude price is lower or fading"
        energy_equity = "neutral"
    elif has_up and not has_down:
        short_term = "positive"
        short_channel = "short-term crude price momentum is higher"
        energy_equity = "positive"
    else:
        short_term = "neutral"
        short_channel = "short-term crude price direction is mixed or unconfirmed"
        energy_equity = "uncertain"

    return {
        "short_term_direction": short_term,
        "short_term_label": {
            "negative": "하락",
            "positive": "상승",
            "neutral": "중립",
        }.get(short_term, "중립"),
        "short_term_channel": short_channel,
        "structural_risk_direction": "positive" if has_structural else "uncertain",
        "structural_risk_label": "상승" if has_structural else "확인 필요",
        "structural_risk_channel": "structural supply/geopolitical risk premium remains relevant" if has_structural else "structural energy risk is not confirmed by the headline",
        "energy_equity_direction": energy_equity,
    }


def _fallback_theme_for_validation(text: str, current: str) -> str:
    if _has_any(text, MARKET_SCHEDULE_TERMS):
        return "market_schedule"
    if _is_oil_change_consumer_story(text):
        return "autos"
    if _has_any(text, OIL_TERMS):
        return "energy_inflation"
    if _has_any(text, COMPANY_FINANCING_TERMS):
        return "company_specific"
    if _has_any(text, FED_TERMS):
        return "rates_policy"
    if "lululemon" in text:
        return "consumer"
    if current == "semiconductor" and not _has_any(text, CHIP_TERMS):
        return "company_specific"
    if current == "ai_infrastructure" and not _has_any(text, AI_INFRA_TERMS):
        return "market_flow"
    return current


def _market_relevance(primary: str, text: str) -> int:
    if primary in {"rates_policy", "energy_inflation", "geopolitical_risk", "macro_growth", "currency_fx", "market_flow"}:
        return 80
    if primary in {"semiconductor", "ai_infrastructure", "defense", "power_grid_electrification", "financials"}:
        return 70
    if primary in {"earnings_guidance", "autos", "battery", "bio_healthcare"}:
        return 58
    if primary in {"consumer", "company_specific", "legal_regulatory"}:
        return 42 if _has_any(text, ["lululemon", "lawsuit", "dispute", "letter"]) else 50
    if primary == "market_schedule":
        return 45
    return 20


def _is_oil_change_consumer_story(text: str) -> bool:
    return _has_any(text, OIL_FALSE_POSITIVE_TERMS) and not _has_any(text, OIL_MARKET_TERMS)


def _event_type(primary: str) -> str:
    if primary in {"rates_policy", "energy_inflation", "macro_growth", "currency_fx"}:
        return "macro_risk"
    if primary in {"geopolitical_risk", "defense"}:
        return "geopolitical_event"
    if primary in {"semiconductor", "ai_infrastructure", "power_grid_electrification"}:
        return "sector_catalyst"
    if primary in {"earnings_guidance"}:
        return "earnings"
    return "company_or_market_news"


def _event_status(text: str) -> str:
    if _has_any(text, ["could", "may", "opinion", "analysis", "전망", "가능성"]):
        return "analysis"
    if _has_any(text, ["rumor", "소문"]):
        return "rumor"
    return "confirmed"


def _sentiment(text: str, primary: str) -> str:
    negative = ["risk", "pressure", "falls", "drops", "lawsuit", "dispute", "우려", "압박", "급락", "하락"]
    positive = ["beats", "surges", "jumps", "contract", "approval", "상승", "수주", "승인", "호실적"]
    if primary in {"energy_inflation", "rates_policy", "geopolitical_risk"}:
        return "mixed"
    if _has_any(text, negative) and _has_any(text, positive):
        return "mixed"
    if _has_any(text, negative):
        return "negative"
    if _has_any(text, positive):
        return "positive"
    return "neutral"


def _confidence(primary: str, secondary: list[str], errors: list[str], text: str) -> int:
    score = 72
    if primary in {"rates_policy", "energy_inflation"}:
        score += 8
    if secondary:
        score += min(len(secondary) * 3, 8)
    if errors:
        score -= 25
    if primary == "irrelevant_market_news":
        score = 55
    return max(0, min(100, score))


def _novelty_score(text: str, event_status: str) -> int:
    score = 58
    if _has_any(text, ["announces", "reported", "decision", "approval", "공시", "발표", "결정"]):
        score += 18
    if event_status in {"analysis", "market_commentary"}:
        score -= 8
    return max(0, min(100, score))


def _urgency_score(primary: str, text: str) -> int:
    score = 55
    if primary in {"rates_policy", "energy_inflation", "geopolitical_risk"}:
        score += 15
    if _has_any(text, ["spikes", "surges", "urgent", "breaking", "급등", "급락"]):
        score += 12
    return max(0, min(100, score))


def _report_priority(primary: str, relevance: int, validation_status: str, text: str) -> str:
    if validation_status == "FAIL" or primary in {"irrelevant_market_news", "market_schedule"}:
        return "exclude"
    if primary in {"consumer", "company_specific", "legal_regulatory"} and relevance < 55:
        return "exclude"
    if relevance >= 75:
        return "high"
    if relevance >= 55:
        return "medium"
    return "low"


def _published_at_kst(item: dict[str, Any]) -> str:
    raw = item.get("pub_dt") or item.get("published_at_kst") or item.get("published")
    if isinstance(raw, datetime):
        dt = raw.astimezone(KST) if raw.tzinfo else KST.localize(raw)
        return dt.strftime("%Y-%m-%d %H:%M")
    return str(raw or "")


def _news_id(headline: str, url: str) -> str:
    digest = hashlib.sha1(f"{headline}|{url}".encode("utf-8")).hexdigest()[:12]
    return f"news_{digest}"


def _has_any(text: str, terms: list[str]) -> bool:
    for term in terms:
        needle = term.lower().strip()
        if not needle:
            continue
        if _requires_word_boundary(needle):
            pattern = r"(?<![a-z0-9])" + re.escape(needle) + r"(?![a-z0-9])"
            if re.search(pattern, text):
                return True
        elif needle in text:
            return True
    return False


def _requires_word_boundary(term: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9]+(?: [a-z0-9]+)*", term))


def _has_geo(text: str) -> bool:
    return _has_any(text, ["war", "middle east", "sanction", "attack", "conflict", "중동", "전쟁", "제재", "분쟁"])


def _has_legal(text: str) -> bool:
    return _has_any(text, ["lawsuit", "legal", "regulatory", "probe", "dispute", "governance", "소송", "규제", "조사", "분쟁"])


def _append_unique(items: list[str], additions: list[str]) -> list[str]:
    result = list(items)
    for item in additions:
        if item and item not in result:
            result.append(item)
    return result[:3]
