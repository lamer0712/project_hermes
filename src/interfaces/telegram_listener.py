import os
import re
import sys
import json


# 프로젝트 루트(investment_firm_alpha)를 sys.path에 추가하여 src 모듈을 임포트할 수 있도록 함
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# Load environment variables first
from dotenv import load_dotenv
dotenv_path = os.path.join(project_root, '.env')
load_dotenv(dotenv_path)

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes
from src.agents.manager import ManagerAgent
from src.utils.command_queue import CommandQueue
from src.utils.logger import logger
from telegram.error import NetworkError

# Initialize Manager Agent
manager = ManagerAgent()
AUTHORIZED_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

# 대기 중인 확인 요청 (chat_id → {action, commands, params})
_pending_confirm = {}

# 대기 중인 에이전트 선택 요청 (chat_id → {ticker, price, agents: list})
_pending_agent_select = {}


async def cmd_rebalance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id != AUTHORIZED_CHAT_ID: return
    CommandQueue.push("rebalance", {})
    await context.bot.send_message(chat_id=chat_id, text="✅ **rebalance** 명령 접수\n⏳ 다음 스케줄러 주기에 실행됩니다 (최대 30초 내)")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    logger.info(f"[Debug] cmd_status called. incoming_chat_id: '{chat_id}', AUTHORIZED_CHAT_ID: '{AUTHORIZED_CHAT_ID}'")
    if chat_id != AUTHORIZED_CHAT_ID:
        logger.info(f"[Debug] Rejected: chat_id '{chat_id}' != '{AUTHORIZED_CHAT_ID}'")
        return
        
    params = {}
    if context.args:
        agent_raw = context.args[0].lower()
        agent_name = f"agent_{agent_raw}" if not agent_raw.startswith("agent_") else agent_raw
        params["agent"] = agent_name
        
    CommandQueue.push("status", params)
    await context.bot.send_message(chat_id=chat_id, text="✅ **status** 명령 접수\n⏳ 다음 스케줄러 주기에 실행됩니다 (최대 30초 내)")


async def cmd_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id != AUTHORIZED_CHAT_ID: return
    _pending_confirm[chat_id] = {
        "action": "restart_only",
        "commands": [],
        "params": {},
    }
    await context.bot.send_message(chat_id=chat_id, text="🔄 시스템을 재시작하시겠습니까?\n\n'확인' 또는 '취소'로 응답해주세요.")


async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id != AUTHORIZED_CHAT_ID: return
    _pending_confirm[chat_id] = {
        "action": "execute_and_restart",
        "commands": ["sync"],
        "params": {},
    }
    await context.bot.send_message(chat_id=chat_id, text="📋 동기화를 실행하고 시스템을 재시작합니다.\n\n'확인' 또는 '취소'로 응답해주세요.")


async def cmd_kill_process(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id != AUTHORIZED_CHAT_ID: return
    _pending_confirm[chat_id] = {
        "action": "kill_main",
        "commands": ["kill"],
        "params": {},
    }
    await context.bot.send_message(chat_id=chat_id, text="⚠️ 시스템(main.py)을 완전히 종료하시겠습니까?\n이 명령을 실행하면 프로세스가 종료되어 자동 매매가 중단됩니다.\n\n'확인' 또는 '취소'로 응답해주세요.")


async def cmd_liquidate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id != AUTHORIZED_CHAT_ID: return
    
    if len(context.args) < 2:
        await context.bot.send_message(chat_id=chat_id, text="사용법: /liquidate [agent명] [코인심볼]\n예: /liquidate beta ARDR")
        return
        
    agent_raw = context.args[0].lower()
    ticker_raw = context.args[1].upper()
    
    agent_name = f"agent_{agent_raw}" if not agent_raw.startswith("agent_") else agent_raw
    ticker = f"KRW-{ticker_raw}" if not ticker_raw.startswith("KRW-") else ticker_raw
    
    CommandQueue.push("liquidate", {"agent": agent_name, "ticker": ticker})
    await context.bot.send_message(chat_id=chat_id, text=f"✅ **liquidate** 명령 접수 ({agent_name}, {ticker})\n⏳ 다음 스케줄러 주기에 실행됩니다 (최대 30초 내)")


async def unknown_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """지정되지 않은 명령어를 처리합니다."""
    chat_id = str(update.effective_chat.id)
    if chat_id != AUTHORIZED_CHAT_ID: return
    await context.bot.send_message(
        chat_id=chat_id,
        text="❌ 알 수 없는 명령어입니다.\n사용 가능한 명령어 목록을 보려면 /help 를 입력해주세요."
    )
async def cmd_halt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id != AUTHORIZED_CHAT_ID: return
    
    if not context.args:
        await context.bot.send_message(chat_id=chat_id, text="사용법: /halt [에이전트명]\n예: /halt alpha")
        return
        
    agent_raw = context.args[0].lower()
    agent_name = f"agent_{agent_raw}" if not agent_raw.startswith("agent_") else agent_raw
    
    _pending_confirm[chat_id] = {
        "action": "execute_only",
        "commands": ["halt"],
        "params": {"agent": agent_name},
    }
    await context.bot.send_message(chat_id=chat_id, text=f"⚠️ {agent_name} 에이전트의 거래를 중지하시겠습니까?\n\n'확인' 또는 '취소'로 응답해주세요.")


async def cmd_resume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id != AUTHORIZED_CHAT_ID: return
    
    if not context.args:
        await context.bot.send_message(chat_id=chat_id, text="사용법: /resume [에이전트명]\n예: /resume alpha")
        return
        
    agent_raw = context.args[0].lower()
    agent_name = f"agent_{agent_raw}" if not agent_raw.startswith("agent_") else agent_raw
    
    _pending_confirm[chat_id] = {
        "action": "execute_only",
        "commands": ["resume"],
        "params": {"agent": agent_name},
    }
    await context.bot.send_message(chat_id=chat_id, text=f"✅ {agent_name} 에이전트의 거래를 재개하시겠습니까?\n\n'확인' 또는 '취소'로 응답해주세요.")


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id != AUTHORIZED_CHAT_ID: return
    _pending_confirm[chat_id] = {
        "action": "execute_only",
        "commands": ["clear"],
        "params": {},
    }
    await context.bot.send_message(chat_id=chat_id, text="🧹 모든 로그(*.log)와 에이전트 거래 내역(trades.md)을 정리하시겠습니까?\n\n'확인' 또는 '취소'로 응답해주세요.")


async def cmd_approve_strategy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id != AUTHORIZED_CHAT_ID: return
    
    if not context.args:
        await context.bot.send_message(chat_id=chat_id, text="사용법: /approve [에이전트명]\n예: /approve alpha")
        return
        
    agent_raw = context.args[0].lower()
    agent_name = f"agent_{agent_raw}" if not agent_raw.startswith("agent_") else agent_raw
    
    # 제안 파일 존재 확인
    proposal_path = os.path.join(project_root, "agents", agent_name, "proposed_strategy.json")
    if not os.path.exists(proposal_path):
        await context.bot.send_message(chat_id=chat_id, text=f"❌ '{agent_name}'의 대기 중인 전략 제안이 없습니다.")
        return
        
    try:
        with open(proposal_path, "r", encoding="utf-8") as f:
            proposal = json.load(f)
        _pending_confirm[chat_id] = {
            "action": "execute_only",
            "commands": ["update_strategy"],
            "params": {"agent": agent_name},
        }
        await context.bot.send_message(
            chat_id=chat_id, 
            text=f"✅ {agent_name}의 다음 전략 업데이트를 승인하시겠습니까?\n\n"
                 f"*내용:* `{json.dumps(proposal.get('new_parameters'))}`\n\n"
                 f"'확인' 또는 '취소'로 응답해주세요."
        )
    except Exception as e:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ 오류 발생: {e}")


async def cmd_reject_strategy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id != AUTHORIZED_CHAT_ID: return
    
    if not context.args:
        await context.bot.send_message(chat_id=chat_id, text="사용법: /reject [에이전트명]\n예: /reject alpha")
        return
        
    agent_raw = context.args[0].lower()
    agent_name = f"agent_{agent_raw}" if not agent_raw.startswith("agent_") else agent_raw
    
    proposal_path = os.path.join(project_root, "agents", agent_name, "proposed_strategy.json")
    if os.path.exists(proposal_path):
        os.remove(proposal_path)
        await context.bot.send_message(chat_id=chat_id, text=f"✅ {agent_name}의 전략 제안을 거절하고 삭제했습니다.")
    else:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ '{agent_name}'의 대기 중인 전략 제안이 없습니다.")


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """지속적인 투표(polling) 중 발생하는 일시적인 통신 에러를 억제하고 로그를 깔끔하게 유지합니다."""
    
    if isinstance(context.error, NetworkError):
        logger.error(f"[Telegram Listener] Network Warning: {context.error} (Retrying...)")
    else:
        logger.error(f"[Telegram Listener] Unhandled error: {context.error}")


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_chat.id) != AUTHORIZED_CHAT_ID:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Unauthorized access.")
        return
    
    help_text = """안녕하세요! Project Hermes의 Manager 입니다.

명령어 안내:
/rebalance — 성과 기반 자본 재배분을 실행합니다
/status — 전체 포트폴리오 현황을 조회합니다
/status [에이전트명] — 특정 에이전트의 포트폴리오 현황을 조회합니다 (예: /status alpha)
/halt [에이전트명] — 특정 에이전트의 거래를 일시 중지합니다
/resume [에이전트명] — 중지된 에이전트의 거래를 재개합니다
/liquidate [에이전트명] [코인심볼] — 에이전트의 특정코인을 시장가로 청산합니다. 예: /liquidate beta ARDR
/sync — 업비트 실잔고와 포트폴리오를 동기화합니다
/clear — 로그 및 거래 내역을 정리합니다
/restart — 시스템을 재시작합니다
/kill — 시스템을 강제 종료합니다

명령어 이외의 대화는 Manager Agent가 자연어로 답변합니다!"""
    await context.bot.send_message(chat_id=update.effective_chat.id, text=help_text)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/help 커맨드 처리"""
    chat_id = str(update.effective_chat.id)
    logger.info(f"[Debug] help_command called. incoming_chat_id: '{chat_id}', AUTHORIZED_CHAT_ID: '{AUTHORIZED_CHAT_ID}'")
    if chat_id != AUTHORIZED_CHAT_ID:
        logger.info(f"[Debug] Rejected: chat_id '{chat_id}' != '{AUTHORIZED_CHAT_ID}'")
        return
    await start(update, context)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    if chat_id != AUTHORIZED_CHAT_ID:
        logger.info(f"[Security] Unauthorized message from {chat_id}")
        return

    user_text = update.message.text.strip()
    logger.info(f"\n[Telegram Listener] Received message: {user_text}")
    
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action='typing')
    
    # 1. 대기 중인 확인 요청이 있는지 체크
    if chat_id in _pending_confirm:
        await handle_confirm_response(update, context, user_text)
        return
        
    # 1.5 대기 중인 에이전트 선택(지정가 매도)이 있는지 체크
    if chat_id in _pending_agent_select:
        await handle_agent_select_response(update, context, user_text)
        return
    
    # 2. 지정가 매도 자연어 인터페이스 지원 ("SAHARA 지정가 5000 매도" 등)
    limit_sell_match = re.search(r'([A-Za-z0-9]+)\s+지정가\s+(\d+)\s+매도', user_text)
    if limit_sell_match:
        ticker_symbol = limit_sell_match.group(1).upper()
        price = int(limit_sell_match.group(2))
        await handle_limit_sell_interactive(update, context, {"ticker": f"KRW-{ticker_symbol}", "price": price})
        return
    
    # 3. 그 외 자연어 질문
    answer = manager.answer_query(user_text)
    await context.bot.send_message(chat_id=update.effective_chat.id, text=answer)


# 확인 응답 키워드 (위쪽에서 삭제되었으므로 재선언)
CONFIRM_WORDS = {"확인", "네", "yes", "y", "ㅇ", "응", "ok", "ㅇㅇ"}
CANCEL_WORDS = {"취소", "아니", "no", "n", "ㄴ", "아니요", "cancel"}

async def handle_confirm_response(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str):
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
                text="🔄 재시작 명령을 전송했습니다. 시스템이 곧 재시작됩니다..."
            )
            # 텔레그램 리스너도 자체 재시작
            restart_self()

        elif action == "kill_main":
            # 시스템 종료 명령 전송
            CommandQueue.push("kill", {})
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="🛑 시스템 종료 명령을 전송했습니다. 프로세스가 곧 종료됩니다..."
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
                text=f"✅ 명령 접수 완료: {cmd_desc}\n🔄 실행 후 시스템이 재시작됩니다..."
            )
            # 텔레그램 리스너도 자체 재시작
            restart_self()

        elif action == "execute_only":
            # 명령 실행만 (재시작 없음)
            for cmd in commands:
                CommandQueue.push(cmd, params)
            
            cmd_desc = ", ".join(commands)
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=f"✅ 명령 접수 완료: {cmd_desc}\n⌛ 시스템에 반영합니다..."
            )
    
    elif text_lower in CANCEL_WORDS:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ 취소되었습니다."
        )
    else:
        # 확인도 취소도 아님 → 다시 물어보기
        _pending_confirm[chat_id] = pending
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="'확인' 또는 '취소'로 응답해주세요."
        )


async def handle_limit_sell_interactive(update: Update, context: ContextTypes.DEFAULT_TYPE, params: dict):
    """지정가 매도 대화형 1단계: 보유 에이전트 탐색 후 질문"""
    ticker = params.get("ticker")
    price = params.get("price")
    
    state_file = "manager/portfolio_state.json"
    holding_agents = []
    
    if os.path.exists(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                state_data = json.load(f)
                portfolios = state_data.get("portfolios", {})
                for agent_name, portfolio in portfolios.items():
                    holdings = portfolio.get("holdings", {})
                    if ticker in holdings and holdings[ticker].get("volume", 0) > 0:
                        holding_agents.append(agent_name)
        except Exception as e:
            logger.error(f"Error reading portfolio state: {e}")
            
    if not holding_agents:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"❌ '{ticker}' 코인을 보유 중인 에이전트가 없습니다."
        )
        return
        
    chat_id = str(update.effective_chat.id)
    _pending_agent_select[chat_id] = {
        "ticker": ticker,
        "price": price,
        "agents": holding_agents
    }
    
    agents_list_str = "\n".join([f"{i+1}. {name}" for i, name in enumerate(holding_agents)])
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=f"📊 '{ticker}' 보유 에이전트 목록:\n{agents_list_str}\n\n👉 지정가 {price}원에 전량 매도할 에이전트 번호나 지정된 이름을 입력해주세요. (취소하려면 '취소' 입력)"
    )


async def handle_agent_select_response(update: Update, context: ContextTypes.DEFAULT_TYPE, user_text: str):
    """지정가 매도 대화형 2단계: 에이전트 선택 확인 및 큐 삽입"""
    chat_id = str(update.effective_chat.id)
    pending = _pending_agent_select.pop(chat_id, None)
    
    if not pending:
        return
        
    text_lower = user_text.lower().strip()
    if text_lower in CANCEL_WORDS:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="❌ 지정가 매도가 취소되었습니다.")
        return
        
    agents = pending["agents"]
    selected_agent = None
    
    # 1. 번호로 입력했는지 확인
    if text_lower.isdigit():
        idx = int(text_lower) - 1
        if 0 <= idx < len(agents):
            selected_agent = agents[idx]
    else:
        # 2. 이름 일부를 입력했는지 확인 (예: 'alpha' -> 'agent_alpha')
        for agent in agents:
            if text_lower in agent.lower():
                selected_agent = agent
                break
                
    if selected_agent:
        CommandQueue.push("limit_sell", {
            "agent": selected_agent,
            "ticker": pending["ticker"],
            "price": pending["price"]
        })
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"✅ {selected_agent} 에이전트의 {pending['ticker']} 지정가({pending['price']}원) 전량 매도 명령이 접수되었습니다.\n⏳ 스케줄러에 의해 곧 실행됩니다."
        )
    else:
        # 매칭 실패 시 다시 물어봄
        _pending_agent_select[chat_id] = pending
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"❓ 에이전트를 찾을 수 없습니다. 번호나 이름('alpha' 등)을 정확히 입력하거나 '취소'를 입력해주세요."
        )


def restart_self():
    """텔레그램 리스너 프로세스를 재시작합니다."""
    logger.info("[Telegram Listener] 🔄 자체 재시작 중...")
    os.execv(sys.executable, [sys.executable] + sys.argv)


if __name__ == '__main__':
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not bot_token or not AUTHORIZED_CHAT_ID:
        logger.info("TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID is missing in .env")
        exit(1)

    logger.info("Starting Telegram Listener...")
    application = ApplicationBuilder().token(bot_token).build()
    
    application.add_handler(CommandHandler('start', start))
    application.add_handler(CommandHandler('help', help_command))
    application.add_handler(CommandHandler('rebalance', cmd_rebalance))
    application.add_handler(CommandHandler('status', cmd_status))
    application.add_handler(CommandHandler('restart', cmd_restart))
    application.add_handler(CommandHandler('sync', cmd_sync))
    application.add_handler(CommandHandler('liquidate', cmd_liquidate))
    application.add_handler(CommandHandler('kill', cmd_kill_process))
    application.add_handler(CommandHandler('halt', cmd_halt))
    application.add_handler(CommandHandler('resume', cmd_resume))
    application.add_handler(CommandHandler('clear', cmd_clear))
    application.add_handler(CommandHandler('approve', cmd_approve_strategy))
    application.add_handler(CommandHandler('reject', cmd_reject_strategy))
    
    application.add_handler(MessageHandler(filters.COMMAND, unknown_command))
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_message))
    application.add_error_handler(error_handler)
    
    logger.info("Listening for messages... (Press Ctrl+C to stop)")
    application.run_polling()
