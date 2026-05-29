"""
bot.py — 텔레그램 봇 메인 서버 (OCI 최적화)

핵심 개선사항:
1. asyncio 기반 → CPU 점유 0% (기존 80% → 0%)
2. 모니터링 루프를 asyncio.create_task로 비동기 실행
3. AI 호출 분리 (ai_client.py)
4. DB 커넥션 풀링 (database.py)
5. 환경변수 기반 설정 (config.py)
"""
import asyncio
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters
)

# 프로젝트 루트를 path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, KST
from src.activity_log import append_activity
from src.database import (
    get_active_positions, add_position, close_position,
    delete_position, save_report, load_report
)
from src.position_tracker import (
    calc_pnl, rule_based_judge, format_position_summary,
    parse_position_input, is_kr_market_open, is_us_market_open,
)
from src.data_market import get_price_safe
from src.report_formatter import build_msg1, build_msg2
from src.report_engine import generate_investment_report
from src.ai_client import ai_position_judge, ask_ai_question
from src.performance_tracker import record_recommendation_snapshots
from src.monitor import monitoring_loop
from src.snapshot_collector import snapshot_collection_loop
from src.memory_scheduler import memory_scheduler_loop

# ── 로깅 설정 ────────────────────────────────────────
# httpx/httpcore 로그 억제 (토큰 유출 방지 및 가독성)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(Path(__file__).parent.parent / "data" / "bot.log", encoding="utf-8"),
    ]
)
logger = logging.getLogger("Bot")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def _preview(text: str, limit: int = 300) -> str:
    text = " ".join(text.split())
    return text[:limit]


def _message_activity_fields(message) -> dict:
    user = getattr(message, "from_user", None)
    chat = getattr(message, "chat", None)
    return {
        "chat_id": getattr(message, "chat_id", None),
        "chat_type": getattr(chat, "type", None),
        "message_id": getattr(message, "message_id", None),
        "user_id": getattr(user, "id", None),
        "username": getattr(user, "username", None),
    }


def _callback_activity_fields(query) -> dict:
    user = getattr(query, "from_user", None)
    message = getattr(query, "message", None)
    fields = _message_activity_fields(message) if message else {}
    fields.update({
        "callback_query_id": getattr(query, "id", None),
        "user_id": getattr(user, "id", fields.get("user_id")),
        "username": getattr(user, "username", fields.get("username")),
    })
    return fields


def log_activity(event: str, **fields):
    try:
        append_activity(event, **fields)
    except Exception as exc:
        logger.warning(f"activity log write failed: {exc}")


DAILY_BRIEFING_HOUR = 7
DAILY_BRIEFING_MINUTE = 0


def _as_kst(now: datetime) -> datetime:
    if now.tzinfo is None:
        return KST.localize(now)
    return now.astimezone(KST)


def is_daily_briefing_day(now: datetime) -> bool:
    """일요일을 제외한 날만 아침 브리핑을 발송한다."""
    return _as_kst(now).weekday() != 6


def next_daily_briefing_time(now: datetime | None = None) -> datetime:
    """다음 07:00 KST 브리핑 시각. 일요일은 건너뛴다."""
    now = _as_kst(now or datetime.now(KST))
    candidate = now.replace(
        hour=DAILY_BRIEFING_HOUR,
        minute=DAILY_BRIEFING_MINUTE,
        second=0,
        microsecond=0,
    )
    if candidate <= now:
        candidate += timedelta(days=1)
    while not is_daily_briefing_day(candidate):
        candidate += timedelta(days=1)
    return candidate


def seconds_until_next_daily_briefing(now: datetime | None = None) -> float:
    now = _as_kst(now or datetime.now(KST))
    return max((next_daily_briefing_time(now) - now).total_seconds(), 0)


# ── 상태 관리 ────────────────────────────────────────
_awaiting_position_input = set()


# ── 메인 키보드 ──────────────────────────────────────
def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📋 포지션 현황", callback_data="positions"),
            InlineKeyboardButton("📊 오늘 리포트", callback_data="report"),
        ],
        [
            InlineKeyboardButton("⚡ 즉시 체크", callback_data="quick_check"),
            InlineKeyboardButton("✨ 포지션 등록", callback_data="add_position"),
        ],
    ])


# ── /start 명령어 ────────────────────────────────────
async def cmd_start(update: Update, context):
    log_activity(
        "command_received",
        route="start",
        text="/start",
        **_message_activity_fields(update.message),
    )
    await update.message.reply_text(
        "🤖 *Manus Investment Agent* 활성화\n\n"
        "아래 버튼으로 기능을 사용하세요:",
        parse_mode="Markdown",
        reply_markup=main_keyboard(),
    )


# ── 콜백 핸들러 ──────────────────────────────────────
async def callback_handler(update: Update, context):
    query = update.callback_query
    await query.answer()
    data = query.data
    log_activity(
        "callback_received",
        route=data,
        callback_data=data,
        **_callback_activity_fields(query),
    )

    if data == "positions":
        await handle_positions(query)
    elif data == "report":
        await handle_report(query)
    elif data == "quick_check":
        await handle_quick_check(query)
    elif data == "add_position":
        await handle_add_position_prompt(query)
    elif data.startswith("close_"):
        pid = int(data.split("_")[1])
        await handle_close_position(query, pid)
    elif data.startswith("delete_"):
        pid = int(data.split("_")[1])
        await handle_delete_position(query, pid)


# ── 포지션 현황 ──────────────────────────────────────
async def send_positions(message):
    positions = get_active_positions()
    if not positions:
        await message.reply_text("📭 등록된 포지션이 없습니다.\n✨ 포지션 등록 버튼을 눌러 추가하세요.")
        return

    msg_lines = [f"📋 *보유 포지션* ({len(positions)}개)\n"]
    buttons = []

    for pos in positions:
        current_price = get_price_safe(pos["ticker"], pos["market"], pos["entry_price"])
        pnl = calc_pnl(pos, current_price)
        judge = rule_based_judge(pos, current_price, pnl)
        msg_lines.append(format_position_summary(pos, current_price, pnl, judge))
        buttons.append([
            InlineKeyboardButton(f"🔒 청산 #{pos['id']} {pos['name']}", callback_data=f"close_{pos['id']}"),
            InlineKeyboardButton(f"🗑 삭제 #{pos['id']}", callback_data=f"delete_{pos['id']}"),
        ])

    msg_lines.append("\n💡 _청산/삭제 버튼으로 포지션을 관리하세요_")
    keyboard = InlineKeyboardMarkup(buttons) if buttons else None

    text = "\n".join(msg_lines)
    # 텔레그램 메시지 길이 제한 (4096자)
    if len(text) > 4000:
        chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for chunk in chunks:
            await message.reply_text(chunk, parse_mode="Markdown")
        if keyboard:
            await message.reply_text("포지션 관리:", reply_markup=keyboard)
    else:
        await message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


async def handle_positions(query):
    await send_positions(query.message)


def _compact_menu_text(text: str) -> str:
    return "".join(ch.lower() for ch in text if ch.isalnum())


def menu_text_route(text: str) -> str | None:
    compact = _compact_menu_text(text)
    route_map = {
        "리포트": "report",
        "오늘리포트": "report",
        "오늘의리포트": "report",
        "투자리포트": "report",
        "데일리리포트": "report",
        "report": "report",
        "dailyreport": "report",
        "포지션현황": "positions",
        "보유포지션": "positions",
        "positions": "positions",
        "포지션등록": "add_position",
        "포지션추가": "add_position",
        "addposition": "add_position",
        "즉시체크": "quick_check",
        "quickcheck": "quick_check",
    }
    return route_map.get(compact)


def is_report_text(text: str) -> bool:
    return menu_text_route(text) == "report"


def is_add_position_text(text: str) -> bool:
    return menu_text_route(text) == "add_position"


def is_positions_text(text: str) -> bool:
    return menu_text_route(text) == "positions"


def is_quick_check_text(text: str) -> bool:
    return menu_text_route(text) == "quick_check"


# ── 리포트 ───────────────────────────────────────────
async def send_report(message, source: str = "unknown"):
    today = datetime.now(KST).strftime("%Y%m%d")
    log_activity(
        "report_requested",
        source=source,
        report_date=today,
        **_message_activity_fields(message),
    )
    cached = load_report(today)
    cache_hit = bool(cached)

    if cached:
        logger.info(f"리포트 캐시 히트 ({today})")
        msg1 = cached["msg1"] or build_msg1(cached["report"])
        msg2 = cached["msg2"] or build_msg2(cached["report"])
    else:
        await message.reply_text("🔄 리포트 생성 중... (1~2분 소요)")
        try:
            report = generate_investment_report()
            msg1 = build_msg1(report)
            msg2 = build_msg2(report)
            save_report(today, report, msg1, msg2)
            record_recommendation_snapshots(today, report)
            logger.info(f"리포트 생성 및 저장 완료 ({today})")
        except Exception as e:
            logger.error(f"리포트 생성 실패: {e}")
            await message.reply_text(f"⚠️ 리포트 생성 실패: {e}")
            return

    await message.reply_text(msg1, parse_mode="Markdown")
    await asyncio.sleep(0.5)
    await message.reply_text(msg2, parse_mode="Markdown")
    log_activity(
        "report_sent",
        source=source,
        report_date=today,
        cache_hit=cache_hit,
        msg1_length=len(msg1),
        msg2_length=len(msg2),
        **_message_activity_fields(message),
    )


async def handle_report(query):
    await send_report(query.message, source="callback")


# ── 즉시 체크 ────────────────────────────────────────
async def send_quick_check(message):
    positions = get_active_positions()
    if not positions:
        await message.reply_text("📭 등록된 포지션이 없습니다.")
        return

    await message.reply_text("⚡ AI 분석 중...")

    msg_lines = [f"⚡ *즉시 체크* | {datetime.now(KST).strftime('%H:%M')}\n"]
    for pos in positions:
        current_price = get_price_safe(pos["ticker"], pos["market"], pos["entry_price"])
        pnl = calc_pnl(pos, current_price)
        judge = ai_position_judge(pos, current_price, pnl, force=True)
        msg_lines.append(format_position_summary(pos, current_price, pnl, judge))

    await message.reply_text("\n".join(msg_lines), parse_mode="Markdown")


async def handle_quick_check(query):
    await send_quick_check(query.message)


# ── 포지션 등록 ──────────────────────────────────────
async def send_add_position_prompt(message):
    chat_id = message.chat_id
    _awaiting_position_input.add(chat_id)
    await message.reply_text(
        "✨ *포지션 등록*\n\n"
        "아래 형식으로 입력해주세요:\n"
        "`종목명 진입가 수량`\n\n"
        "예시:\n"
        "• `삼성전자 55000 100`\n"
        "• `숏 AMD 140 50`\n"
        "• `NVDA 950 10`\n\n"
        "💡 수량 생략 시 1주로 등록됩니다.",
        parse_mode="Markdown",
    )


async def handle_add_position_prompt(query):
    await send_add_position_prompt(query.message)


async def handle_close_position(query, pid: int):
    positions = get_active_positions()
    pos = next((p for p in positions if p["id"] == pid), None)
    if not pos:
        await query.message.reply_text("⚠️ 포지션을 찾을 수 없습니다.")
        return
    current_price = get_price_safe(pos["ticker"], pos["market"], pos["entry_price"])
    close_position(pid, current_price)
    pnl = calc_pnl(pos, current_price)
    await query.message.reply_text(
        f"🔒 *포지션 청산 완료*\n"
        f"{pos['name']} ({pos['ticker']})\n"
        f"최종 손익: *{pnl['pnl_str']}*",
        parse_mode="Markdown",
    )


async def handle_delete_position(query, pid: int):
    delete_position(pid)
    await query.message.reply_text(f"🗑 포지션 #{pid} 삭제 완료")


# ── 텍스트 메시지 핸들러 ─────────────────────────────
async def text_handler(update: Update, context):
    chat_id = update.message.chat_id
    text = update.message.text.strip()
    message_fields = _message_activity_fields(update.message)
    log_activity(
        "message_received",
        text_preview=_preview(text),
        text_length=len(text),
        **message_fields,
    )

    # 포지션 입력 대기 중
    if chat_id in _awaiting_position_input:
        log_activity("message_routed", route="position_input", **message_fields)
        _awaiting_position_input.discard(chat_id)
        parsed = parse_position_input(text)
        if not parsed:
            await update.message.reply_text(
                "⚠️ 입력 형식을 인식할 수 없습니다.\n"
                "예: `삼성전자 55000 100` 또는 `숏 AMD 140 50`",
                parse_mode="Markdown",
            )
            return

        pid = add_position(
            direction=parsed["direction"],
            name=parsed["name"],
            ticker=parsed["ticker"],
            market=parsed["market"],
            entry_price=parsed["entry_price"],
            quantity=parsed["quantity"],
            currency=parsed["currency"],
        )
        direction_kor = "🟢 롱" if parsed["direction"] == "long" else "🔴 숏"
        cs = "₩" if parsed["currency"] == "KRW" else "$"
        await update.message.reply_text(
            f"✅ *포지션 등록 완료* (#{pid})\n\n"
            f"{direction_kor} {parsed['name']} (`{parsed['ticker']}`)\n"
            f"진입가: {cs}{parsed['entry_price']:,} | 수량: {parsed['quantity']:,}\n"
            f"시장: {parsed['market']}",
            parse_mode="Markdown",
            reply_markup=main_keyboard(),
        )
        return

    route = menu_text_route(text)
    if route == "report":
        log_activity("message_routed", route="report_text", **message_fields)
        await send_report(update.message, source="text")
        return
    if route == "positions":
        log_activity("message_routed", route="positions_text", **message_fields)
        await send_positions(update.message)
        return
    if route == "add_position":
        log_activity("message_routed", route="add_position_text", **message_fields)
        await send_add_position_prompt(update.message)
        return
    if route == "quick_check":
        log_activity("message_routed", route="quick_check_text", **message_fields)
        await send_quick_check(update.message)
        return

    # 자유 질문 → AI 답변
    log_activity("message_routed", route="free_question", **message_fields)
    await update.message.reply_text("🤔 분석 중...")
    answer = ask_ai_question(text)
    await update.message.reply_text(answer, parse_mode="Markdown", reply_markup=main_keyboard())
    log_activity(
        "response_sent",
        route="free_question",
        response_length=len(answer),
        **message_fields,
    )


# ── 텔레그램 메시지 발송 유틸 (모니터링용) ────────────
async def send_telegram_message(text: str):
    """모니터링 루프에서 사용하는 발송 함수"""
    from telegram import Bot
    bot = Bot(token=TELEGRAM_BOT_TOKEN)
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    for chunk in chunks:
        try:
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=chunk, parse_mode="Markdown")
        except BadRequest as exc:
            logger.warning("Markdown 텔레그램 전송 실패, 일반 텍스트로 재전송: %s", exc)
            await bot.send_message(chat_id=TELEGRAM_CHAT_ID, text=chunk)
        await asyncio.sleep(0.3)


async def generate_and_send_daily_briefing(source: str = "scheduled_0700"):
    """사용자 요청 없이 오늘 리포트를 생성/캐시하고 텔레그램으로 발송한다."""
    today = datetime.now(KST).strftime("%Y%m%d")
    log_activity(
        "report_requested",
        source=source,
        report_date=today,
        chat_id=TELEGRAM_CHAT_ID,
    )

    cached = load_report(today)
    cache_hit = bool(cached)

    try:
        if cached:
            logger.info(f"자동 브리핑 캐시 히트 ({today})")
            report = cached["report"]
            msg1 = cached["msg1"] or build_msg1(report)
            msg2 = cached["msg2"] or build_msg2(report)
        else:
            logger.info(f"자동 브리핑 생성 시작 ({today})")
            report = generate_investment_report()
            msg1 = build_msg1(report)
            msg2 = build_msg2(report)
            save_report(today, report, msg1, msg2)
            record_recommendation_snapshots(today, report)
            logger.info(f"자동 브리핑 생성 및 저장 완료 ({today})")

        await send_telegram_message(msg1)
        await asyncio.sleep(0.5)
        await send_telegram_message(msg2)
        log_activity(
            "report_sent",
            source=source,
            report_date=today,
            cache_hit=cache_hit,
            msg1_length=len(msg1),
            msg2_length=len(msg2),
            chat_id=TELEGRAM_CHAT_ID,
        )
    except Exception as exc:
        logger.exception(f"자동 브리핑 실패: {exc}")
        log_activity(
            "report_failed",
            source=source,
            report_date=today,
            error=str(exc),
            chat_id=TELEGRAM_CHAT_ID,
        )
        raise


async def daily_briefing_loop():
    """매일 07:00 KST 자동 브리핑. 일요일은 다음 월요일로 넘긴다."""
    while True:
        now = datetime.now(KST)
        next_run = next_daily_briefing_time(now)
        delay = max((next_run - now).total_seconds(), 0)
        logger.info(f"다음 자동 브리핑 예정: {next_run.strftime('%Y-%m-%d %H:%M:%S KST')}")
        await asyncio.sleep(delay)

        run_at = datetime.now(KST)
        if not is_daily_briefing_day(run_at):
            logger.info("일요일 자동 브리핑 스킵")
        else:
            await generate_and_send_daily_briefing()

        await asyncio.sleep(60)


# ── 메인 ─────────────────────────────────────────────
def main():
    if not TELEGRAM_BOT_TOKEN:
        logger.error("TELEGRAM_BOT_TOKEN 환경변수가 설정되지 않았습니다!")
        sys.exit(1)

    logger.info("=" * 50)
    logger.info("Manus Investment Bot 시작")
    logger.info(f"시간: {datetime.now(KST).strftime('%Y-%m-%d %H:%M:%S KST')}")
    logger.info("=" * 50)

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # 핸들러 등록
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # 모니터링 루프를 post_init에서 시작
    async def post_init(application):
        asyncio.create_task(monitoring_loop(send_telegram_message))
        logger.info("모니터링 루프 시작됨")

        # 스냅샷 수집 루프 (1~2시간 간격 시장 데이터 축적)
        asyncio.create_task(snapshot_collection_loop())
        logger.info("스냅샷 수집 루프 시작됨 (장중 1시간 / 장외 2시간 / 주말 6시간)")

        # 매일 07:00 자동 브리핑 (일요일 제외)
        asyncio.create_task(daily_briefing_loop())
        logger.info("자동 브리핑 스케줄러 시작됨 (월~토 07:00 KST)")

        # 계층적 시장 기억 스케줄러 (매일07시/토07시/말일 자동 요약+정리)
        asyncio.create_task(memory_scheduler_loop())
        logger.info("시장 기억 스케줄러 시작됨 (매일→매주→매달 계층 압축)")

    app.post_init = post_init

    # 봇 실행 (Long Polling)
    logger.info("Long Polling 시작...")
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=Update.ALL_TYPES,
    )


if __name__ == "__main__":
    main()
