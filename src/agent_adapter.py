"""
agent_adapter.py — Codex/Agent AI 명령형 사용 어댑터

텔레그램 봇을 실행하지 않고, 사용자가 에이전트에게 명령한 순간 필요한
시장 컨텍스트와 프롬프트를 만들어 외부 에이전트가 자기 모델로 답하게 한다.
"""
from __future__ import annotations

from typing import Any

from .ai_client import build_chat_prompt, build_position_judge_prompt
from .data_market import get_price_safe
from .position_tracker import calc_pnl, rule_based_judge
from .report_engine import build_report_prompt, collect_realtime_data


AGENT_REPORT_OUTPUT_INSTRUCTIONS = """
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
🧾 에이전트 출력 형식
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
위 데이터와 규칙을 바탕으로 사람이 읽을 수 있는 한국어 Markdown 리포트를 작성하세요.

원시 JSON만 출력하지 마세요. 일반 사용자가 바로 읽을 수 있어야 합니다.
내부적으로 JSON 스키마 항목을 참고하되, 최종 답변은 아래 순서의 텍스트 리포트로 변환하세요.

필수 섹션:
1. 한 줄 결론: 오늘 시장을 한 문장으로 요약
2. 시장 현황: 한국/미국 지수, 환율, 금리, VIX, 유가 흐름
3. 핵심 테마와 섹터: 강한 섹터와 약한 섹터를 구분
4. 진입 가능 후보: 진입 시그널이 적정인 종목과 근거
5. 대기/추격 금지 후보: 과열 종목, 조정 대기 가격, 이유
6. 포트폴리오 전략: 현금 비중, 장기/스윙/단타 접근
7. 주요 리스크: FOMC, CPI, 환율, 데이터 누락 등
8. 데이터 품질 주의: 비어 있거나 이상해 보이는 데이터 명시
9. 면책 문구: 투자 판단은 사용자 책임

표를 적극적으로 사용하고, 가격/등락률/RSI/BB/목표가 같은 수치는 가능한 한 그대로 보여주세요.
"""


def _request(
    task: str,
    system_prompt: str,
    user_prompt: str,
    data: dict[str, Any] | None = None,
    expected_output: str = "",
) -> dict[str, Any]:
    return {
        "mode": "agent",
        "task": task,
        "system_prompt": system_prompt,
        "user_prompt": user_prompt,
        "data": data or {},
        "expected_output": expected_output,
    }


def build_report_request(
    context: dict[str, Any] | None = None,
    *,
    collect: bool = True,
) -> dict[str, Any]:
    """
    투자 리포트 생성 요청을 만든다.

    context가 없고 collect=True이면 명령 시점의 실시간 데이터를 수집한다.
    Telegram 토큰이나 봇 루프는 사용하지 않는다.
    """
    data = context if context is not None else (collect_realtime_data() if collect else {})
    system_prompt, user_prompt = build_report_prompt(data)
    user_prompt = f"{user_prompt}\n{AGENT_REPORT_OUTPUT_INSTRUCTIONS}"
    return _request(
        "investment_report",
        system_prompt,
        user_prompt,
        data=data,
        expected_output="Return a human-readable Korean Markdown investment report, not raw JSON.",
    )


def build_chat_request(
    question: str,
    *,
    context: str = "",
    yahoo_ctx: str = "",
) -> dict[str, Any]:
    """자유 투자 질문을 외부 에이전트가 답할 수 있는 프롬프트 요청으로 만든다."""
    system_prompt, user_prompt = build_chat_prompt(question, context=context, yahoo_ctx=yahoo_ctx)
    return _request(
        "investment_chat",
        system_prompt,
        user_prompt,
        data={"question": question, "context": context, "yahoo_ctx": yahoo_ctx},
        expected_output="Answer in Korean with concrete prices, risks, and a clear disclaimer.",
    )


def build_position_review_request(
    position: dict[str, Any],
    *,
    current_price: float | None = None,
) -> dict[str, Any]:
    """단일 포지션 점검 요청을 만든다."""
    price = current_price
    if price is None:
        price = get_price_safe(position["ticker"], position["market"], position["entry_price"])

    pnl = calc_pnl(position, price)
    rule_baseline = rule_based_judge(position, price, pnl)
    user_prompt = build_position_judge_prompt(position, price, pnl)
    system_prompt = (
        "당신은 리스크 관리를 우선하는 투자 포지션 리뷰 에이전트입니다. "
        "사용자의 포지션, 현재가, 손익률, 규칙 기반 기준 판단을 참고하되 "
        "최종 판단은 JSON으로만 답하세요."
    )

    return _request(
        "position_review",
        system_prompt,
        user_prompt,
        data={
            "position": position,
            "current_price": price,
            "pnl": pnl,
            "rule_baseline": rule_baseline,
        },
        expected_output='Strict JSON: {"action","confidence","reason","target_price","stop_loss","alert_level"}',
    )


def get_openai_agent_tools():
    """
    OpenAI Agents SDK가 설치된 환경에서 function tools를 반환한다.

    이 저장소는 SDK를 필수 의존성으로 두지 않는다. 에이전트 런타임에서만
    openai-agents를 설치하고 이 함수를 호출하면 된다.
    """
    try:
        from agents import function_tool
    except ImportError as exc:
        raise RuntimeError("Install openai-agents to expose function tools.") from exc

    @function_tool
    def investment_report_request() -> dict[str, Any]:
        return build_report_request()

    @function_tool
    def investment_chat_request(question: str, context: str = "") -> dict[str, Any]:
        return build_chat_request(question, context=context)

    @function_tool
    def investment_position_review_request(
        position: dict[str, Any],
        current_price: float | None = None,
    ) -> dict[str, Any]:
        return build_position_review_request(position, current_price=current_price)

    return [
        investment_report_request,
        investment_chat_request,
        investment_position_review_request,
    ]
