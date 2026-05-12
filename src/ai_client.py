"""
ai_client.py — AI 호출 전용 클라이언트
OCI에서 Manus API를 통해 AI 기능 호출
- 리포트 생성 (gemini-2.5-flash)
- 포지션 AI 판단 (gpt-4.1-nano)
- 자유 질문 답변 (gpt-4.1-mini)
"""
import json
import logging
from datetime import datetime

from openai import OpenAI

from .config import (
    OPENAI_API_KEY, OPENAI_BASE_URL,
    MODEL_REPORT, MODEL_POSITION, MODEL_CHAT, KST
)

logger = logging.getLogger(__name__)

# ── OpenAI 호환 클라이언트 (Manus proxy) ──────────────
_client: OpenAI | None = None


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=OPENAI_API_KEY,
            base_url=OPENAI_BASE_URL,
        )
    return _client


# ── AI 판단 캐시 ─────────────────────────────────────
_judge_cache: dict[int, dict] = {}
_CACHE_TTL_MIN = 30
_AI_TRIGGER_PCT = 3.0


def ai_position_judge(pos: dict, current_price: float, pnl: dict,
                      force: bool = False) -> dict:
    """
    AI 포지션 판단 (크레딧 최적화):
    - force=False: 캐시/규칙 우선
    - force=True: 항상 AI 호출
    """
    from .position_tracker import rule_based_judge

    pid = pos["id"]
    pct = abs(pnl["pnl_pct"])

    # 캐시 확인
    if not force and pid in _judge_cache:
        cached = _judge_cache[pid]
        age_min = (datetime.now(KST) - cached["cached_at"]).total_seconds() / 60
        price_change = abs(current_price - cached["price"]) / max(cached["price"], 1) * 100
        if age_min < _CACHE_TTL_MIN and price_change < _AI_TRIGGER_PCT:
            return cached["result"]

    # 규칙 기반 1차 판단
    rule_result = rule_based_judge(pos, current_price, pnl)

    # AI 호출 조건
    should_call_ai = (
        force or
        pct >= 5.0 or
        rule_result["alert_level"] in ("warning", "urgent")
    )

    if not should_call_ai:
        _judge_cache[pid] = {
            "result": rule_result,
            "cached_at": datetime.now(KST),
            "price": current_price,
        }
        return rule_result

    # AI 호출
    direction_kor = "롱" if pos.get("direction", "long") == "long" else "숏"
    cs = pnl["cs"]
    prompt = (
        f"{pos['name']}({pos['market']}) {direction_kor} "
        f"진입{cs}{pos['entry_price']:,} 현재{cs}{current_price:,} "
        f"손익{pnl['pnl_str']}\n"
        'JSON만 답변: {"action":"홀딩|부분청산|전량청산",'
        '"confidence":1~10,"reason":"1문장",'
        '"target_price":숫자,"stop_loss":숫자,'
        '"alert_level":"info|warning|urgent"}'
    )

    try:
        client = get_client()
        resp = client.chat.completions.create(
            model=MODEL_POSITION,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=150,
            response_format={"type": "json_object"},
        )
        result = json.loads(resp.choices[0].message.content)
        result["source"] = "ai"
    except Exception as e:
        logger.warning(f"AI 판단 실패, 규칙 기반 폴백: {e}")
        result = rule_result
        result["source"] = "rule_fallback"

    _judge_cache[pid] = {
        "result": result,
        "cached_at": datetime.now(KST),
        "price": current_price,
    }
    return result


def ask_ai_question(question: str, context: str = "", yahoo_ctx: str = "") -> str:
    """자유 질문 AI 답변"""
    now_kst = datetime.now(KST).strftime("%Y년 %m월 %d일 %H:%M KST")
    system_prompt = f"""당신은 Manus Investment Agent입니다. 20년 경력의 글로벌 투자 전략가로서 한국(KOSPI/KOSDAQ)과 미국(NYSE/NASDAQ) 시장을 전문으로 분석합니다.

현재 기준 시각: {now_kst}

특기 분야: 피지컬AI, 반도체, 전력인프라, AI 데이터센터 인프라

답변 원칙:
1. 구체적인 수치(진입가, 목표가, 손절가, 포지션 크기)를 항상 제시
2. 투자 근거를 3가지 이상 명확히 설명
3. 리스크 요인도 반드시 언급
4. 장기/스윙/단타 중 적합한 전략 구분
5. 한국어로 답변, 전문적이되 이해하기 쉽게
6. 텔레그램 마크다운 형식 사용 (*굵게*, `코드`, 이모지)
7. 오늘/현재 날짜를 언급할 때는 반드시 현재 기준 시각의 날짜를 사용하고, 과거 날짜를 임의로 만들지 않음

⚠️ 면책: 본 답변은 투자 참고용이며 최종 투자 판단은 투자자 본인 책임입니다."""

    user_content = f"{context}{yahoo_ctx}\n\n질문: {question}" if context or yahoo_ctx else question

    try:
        client = get_client()
        resp = client.chat.completions.create(
            model=MODEL_CHAT,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.4,
            max_tokens=1500,
        )
        return resp.choices[0].message.content
    except Exception as e:
        logger.error(f"AI 질문 답변 실패: {e}")
        return f"⚠️ AI 응답 오류: {e}\n잠시 후 다시 시도해주세요."


def generate_report_via_ai(data_context: str) -> dict:
    """리포트 생성 (gemini-2.5-flash)"""
    from .report_engine import build_report_prompt

    system_prompt, user_prompt = build_report_prompt(data_context)

    try:
        client = get_client()
        resp = client.chat.completions.create(
            model=MODEL_REPORT,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.3,
            max_tokens=8000,
        )
        raw = resp.choices[0].message.content
        if "```json" in raw:
            raw = raw.split("```json")[1].split("```")[0].strip()
        elif "```" in raw:
            raw = raw.split("```")[1].split("```")[0].strip()
        return json.loads(raw)
    except Exception as e:
        logger.error(f"리포트 생성 실패: {e}")
        raise
