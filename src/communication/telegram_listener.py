import os
import re
import sys
import json


# 프로젝트 루트(investment_firm_alpha)를 sys.path에 추가하여 src 모듈을 임포트할 수 있도록 함
project_root = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Load environment variables first
from dotenv import load_dotenv

dotenv_path = os.path.join(project_root, ".env")
load_dotenv(dotenv_path)

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)
from src.communication.command_queue import CommandQueue
from src.utils.logger import logger
from src.ai.llm_client import get_llm_client
from telegram.error import NetworkError

AUTHORIZED_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# 대기 중인 확인 요청 (chat_id → {action, commands, params})
_pending_confirm = {}


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    logger.info(
        f"[Debug] cmd_status called. incoming_chat_id: '{chat_id}, AUTHORIZED_CHAT_ID: '{AUTHORIZED_CHAT_ID}"
    )
    if chat_id != AUTHORIZED_CHAT_ID:
        logger.info(f"[Debug] Rejected: chat_id '{chat_id} != '{AUTHORIZED_CHAT_ID}")
        return

    if context.args:
        # Ignore args as we only have one agent
        pass

    CommandQueue.push("status", {})
    await context.bot.send_message(
        chat_id=chat_id,
        text="✅ **status** 명령 접수\n⏳ 다음 스케줄러 주기에 실행됩니다 (최대 2초 내)",
    )


async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id != AUTHORIZED_CHAT_ID:
        return
    _pending_confirm[chat_id] = {
        "action": "restart_only",
        "commands": [],
        "params": {},
    }
    await context.bot.send_message(
        chat_id=chat_id,
        text="🔄 시스템을 재시작하시겠습니까?\n\n'확인' 또는 '취소'로 응답해주세요.",
    )


async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id != AUTHORIZED_CHAT_ID:
        return
    _pending_confirm[chat_id] = {
        "action": "execute_and_restart",
        "commands": ["sync"],
        "params": {},
    }
    await context.bot.send_message(
        chat_id=chat_id,
        text="📋 동기화를 실행하고 시스템을 재시작합니다.\n\n'확인' 또는 '취소'로 응답해주세요.",
    )


async def cmd_kill_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id != AUTHORIZED_CHAT_ID:
        return
    _pending_confirm[chat_id] = {
        "action": "kill_main",
        "commands": ["kill"],
        "params": {},
    }
    await context.bot.send_message(
        chat_id=chat_id,
        text="⚠️ 시스템(main.py)을 완전히 종료하시겠습니까?\n이 명령을 실행하면 프로세스가 종료되어 자동 매매가 중단됩니다.\n\n'확인' 또는 '취소'로 응답해주세요.",
    )


async def cmd_liquidate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id != AUTHORIZED_CHAT_ID:
        return

    if len(context.args) < 1:
        await context.bot.send_message(
            chat_id=chat_id, text="사용법: /liquidate [코인심볼]\n예: /liquidate ARDR"
        )
        return

    ticker_raw = context.args[0].upper()

    ticker = f"KRW-{ticker_raw}" if not ticker_raw.startswith("KRW-") else ticker_raw

    CommandQueue.push("liquidate", {"ticker": ticker})
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"✅ **liquidate** 명령 접수 ({ticker})\n⏳ 다음 스케줄러 주기에 실행됩니다 (최대 2초 내)",
    )


async def cmd_eval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id != AUTHORIZED_CHAT_ID:
        return

    if len(context.args) < 1:
        await context.bot.send_message(
            chat_id=chat_id, text="사용법: /eval [코인심볼]\n예: /eval BTC"
        )
        return

    ticker_raw = context.args[0].upper()
    ticker = f"KRW-{ticker_raw}" if not ticker_raw.startswith("KRW-") else ticker_raw

    CommandQueue.push("eval", {"ticker": ticker})
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"✅ **eval** 명령 접수 ({ticker})\n⏳ 다음 스케줄러 주기에 실행됩니다 (최대 2초 내)",
    )


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """지정되지 않은 명령어를 처리합니다."""
    chat_id = str(update.effective_chat.id)
    if chat_id != AUTHORIZED_CHAT_ID:
        return
    await context.bot.send_message(
        chat_id=chat_id,
        text="❌ 알 수 없는 명령어입니다.\n사용 가능한 명령어 목록을 보려면 /help 를 입력해주세요.",
    )


async def cmd_halt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id != AUTHORIZED_CHAT_ID:
        return

    _pending_confirm[chat_id] = {
        "action": "execute_only",
        "commands": ["halt"],
        "params": {},
    }
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"⚠️ 모든 거래를 중지하시겠습니까?\n\n'확인' 또는 '취소'로 응답해주세요.",
    )


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id != AUTHORIZED_CHAT_ID:
        return

    _pending_confirm[chat_id] = {
        "action": "execute_only",
        "commands": ["resume"],
        "params": {},
    }
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"✅ 모든 거래를 재개하시겠습니까?\n\n'확인' 또는 '취소'로 응답해주세요.",
    )


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id != AUTHORIZED_CHAT_ID:
        return
    _pending_confirm[chat_id] = {
        "action": "execute_only",
        "commands": ["clear"],
        "params": {},
    }
    await context.bot.send_message(
        chat_id=chat_id,
        text="🧹 시스템 로그(*.log)를 정리하시겠습니까?\n\n'확인' 또는 '취소'로 응답해주세요.",
    )


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """지속적인 투표(polling) 중 발생하는 일시적인 통신 에러를 억제하고 로그를 깔끔하게 유지합니다."""

    if isinstance(context.error, NetworkError):
        logger.error(
            f"[Telegram Listener] Network Warning: {context.error} (Retrying...)"
        )
    else:
        logger.error(f"[Telegram Listener] Unhandled error: {context.error}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != AUTHORIZED_CHAT_ID:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text="Unauthorized access."
        )
        return

    help_text = """안녕하세요! Project Hermes의 Manager 입니다.

명령어 안내:
/status — 전체 포트폴리오 현황을 조회합니다
/halt — 전체 거래를 일시 중지합니다
/resume — 중지된 전체 거래를 재개합니다
/liquidate [코인심볼] — 특정코인을 시장가로 청산합니다. 예: /liquidate ARDR
/sync — 업비트 실잔고와 포트폴리오를 동기화합니다
/clear — 시스템 로그를 정리합니다
/restart — 시스템을 재시작합니다
/kill — 시스템을 강제 종료합니다
/eval [코인심볼] — 특정 코인의 최신 전략 및 시그널 상태를 조회합니다

명령어 이외의 대화는 Manager Agent가 자연어로 답변합니다!"""
    await context.bot.send_message(chat_id=update.effective_chat.id, text=help_text)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/help 커맨드 처리"""
    chat_id = str(update.effective_chat.id)
    logger.info(
        f"[Debug] help_command called. incoming_chat_id: '{chat_id}, AUTHORIZED_CHAT_ID: '{AUTHORIZED_CHAT_ID}"
    )
    if chat_id != AUTHORIZED_CHAT_ID:
        logger.info(f"[Debug] Rejected: chat_id '{chat_id} != '{AUTHORIZED_CHAT_ID}")
        return
    await start(update, context)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id != AUTHORIZED_CHAT_ID:
        logger.info(f"[Security] Unauthorized message from {chat_id}")
        return

    user_text = update.message.text.strip()
    logger.info(f"\n[Telegram Listener] Received message: {user_text}")

    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id, action="typing"
    )

    # 1. 대기 중인 확인 요청이 있는지 체크
    if chat_id in _pending_confirm:
        await handle_confirm_response(update, context, user_text)
        return

    # 2. 지정가 매도 자연어 인터페이스 지원 ("SAHARA 지정가 5000 매도" 등)
    limit_sell_match = re.search(r"([A-Za-z0-9]+)\s+지정가\s+(\d+)\s+매도", user_text)
    if limit_sell_match:
        ticker_symbol = limit_sell_match.group(1).upper()
        price = int(limit_sell_match.group(2))
        await handle_limit_sell_interactive(
            update, context, {"ticker": f"KRW-{ticker_symbol}", "price": price}
        )
        return

    # 3. 그 외 자연어 질문
    # ManagerAgent 대신 직접 상태를 읽어 LLM에 질의
    try:
        logger.info(f"[Telegram] 답변 생성 중... (User Query: {user_text})")
        llm = get_llm_client()
        answer = llm.generate_text("", user_text)
        if not answer:
            answer = "⚠️ AI 응답 생성 실패: API 연결을 확인해주세요."
    except Exception as e:
        logger.error(f"[Telegram] LLM Query Error: {e}")
        answer = "⚠️ 내부 오류로 인해 응답을 생성하지 못했습니다."

    await context.bot.send_message(chat_id=update.effective_chat.id, text=answer)


CONFIRM_WORDS = {"확인", "네", "yes", "y", "ㅇ", "응", "ok", "ㅇㅇ"}
CANCEL_WORDS = {"취소", "아니", "no", "n", "ㄴ", "아니요", "cancel"}


async def handle_confirm_response(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str
):
    """확인/취소 응답을 처리합니다."""
    chat_id = str(update.effective_chat.id)
    pending = _pending_confirm.pop(chat_id, None)

    if not pending:
        return

    text_lower = user_text.lower().strip()

    if text_lower in CONFIRM_WORDS:
        action = pending["action"]
        commands = pending.get("commands", [])
        params = pending.get("params", {})

        if action == "restart_only":
            # 재시작만
            CommandQueue.push("restart", {})
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="🔄 재시작 명령을 전송했습니다. 시스템이 곧 재시작됩니다...",
            )

        elif action == "kill_main":
            # 시스템 종료 명령 전송
            CommandQueue.push("kill", {})
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="🛑 시스템 종료 명령을 전송했습니다. 프로세스가 곧 종료됩니다...",
            )

        elif action == "execute_and_restart":
            # 명령 실행 + 재시작
            for cmd in commands:
                if cmd != "restart":
                    CommandQueue.push(cmd, params)
            CommandQueue.push("restart", {})

            cmd_desc = ", ".join(commands)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"✅ 명령 접수 완료: {cmd_desc}\n🔄 실행 후 시스템이 재시작됩니다...",
            )

        elif action == "execute_only":
            # 명령 실행만 (재시작 없음)
            for cmd in commands:
                CommandQueue.push(cmd, params)

            cmd_desc = ", ".join(commands)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"✅ 명령 접수 완료: {cmd_desc}\n⌛ 시스템에 반영합니다...",
            )

    elif text_lower in CANCEL_WORDS:
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text="❌ 취소되었습니다."
        )
    else:
        # 확인도 취소도 아님 → 다시 물어보기
        _pending_confirm[chat_id] = pending
        await context.bot.send_message(
            chat_id=update.effective_chat.id, text="'확인' 또는 '취소'로 응답해주세요."
        )


async def handle_limit_sell_interactive(
    update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict
):
    """지정가 매도 대화형 1단계: 바로 큐 삽입 (매니저 단일 구조이므로 에이전트 선택 생략)"""
    ticker = params.get("ticker")
    price = params.get("price")

    chat_id = str(update.effective_chat.id)

    CommandQueue.push("limit_sell", {"ticker": ticker, "price": price})
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"✅ {ticker} 지정가({price}원) 전량 매도 명령이 접수되었습니다.\n⏳ 스케줄러에 의해 곧 실행됩니다.",
    )


async def handle_agent_select_response(
    update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str
):
    """더 이상 사용되지 않음 (deprected but kept for reference)"""
    pass


def run_telegram_listener():
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not bot_token or not AUTHORIZED_CHAT_ID:
        logger.info("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing in .env")
        return

    logger.info("Starting Telegram Listener...")
    application = ApplicationBuilder().token(bot_token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("status", cmd_status))
    application.add_handler(CommandHandler("restart", cmd_restart))
    application.add_handler(CommandHandler("sync", cmd_sync))
    application.add_handler(CommandHandler("liquidate", cmd_liquidate))
    application.add_handler(CommandHandler("kill", cmd_kill_process))
    application.add_handler(CommandHandler("halt", cmd_halt))
    application.add_handler(CommandHandler("resume", cmd_resume))
    application.add_handler(CommandHandler("clear", cmd_clear))
    application.add_handler(CommandHandler("eval", cmd_eval))

    application.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    application.add_handler(
        MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message)
    )
    application.add_error_handler(error_handler)

    logger.info("Listening for messages... (Press Ctrl+C to stop)")
    application.run_polling(drop_pending_updates=True, stop_signals=())


if __name__ == "__main__":
    run_telegram_listener()
