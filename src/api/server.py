import os
import json
import threading
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from datetime import datetime
from src.utils.logger import logger

app = FastAPI(title="Hermes Trading Bot API")

# CORS 설정 (Next.js 프론트엔드 연동용)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 봇 인스턴스 상태 저장용 (main.py에서 주입)
class BotState:
    manager = None
    portfolio_manager = None
    agent_name = "crypto_manager"

state = BotState()

@app.get("/api/health")
async def health():
    return {"status": "running", "server_time": datetime.now().isoformat()}

@app.get("/api/status")
async def get_status():
    """봇의 일반적인 가동 상태 및 장세 정보"""
    if not state.manager:
        return {"error": "Manager not initialized"}
    
    return {
        "agent_name": state.agent_name,
        "is_halted": state.portfolio_manager.is_halted(state.agent_name),
        "current_regime": getattr(state.manager, "last_regime", "Unknown"),
        "strategy_map": state.manager.strategy_map,
        "timestamp": datetime.now().isoformat()
    }

@app.get("/api/portfolio")
async def get_portfolio():
    """자산 총괄 및 수익률 정보"""
    if not state.portfolio_manager:
        return {"error": "PortfolioManager not initialized"}
    
    summary = state.portfolio_manager.get_portfolio_summary(state.agent_name)
    return summary

@app.get("/api/holdings")
async def get_holdings():
    """현재 보유 중인 종목 상세"""
    if not state.portfolio_manager:
        return {"error": "PortfolioManager not initialized"}
    
    holdings = state.portfolio_manager.get_holdings(state.agent_name)
    # 클라이언트 편의를 위해 리스트 형태로 변환
    holdings_list = []
    for ticker, h in holdings.items():
        h["ticker"] = ticker
        holdings_list.append(h)
    
    return sorted(holdings_list, key=lambda x: x.get("total_cost", 0), reverse=True)

@app.get("/api/optimization")
async def get_optimization_report():
    """가장 최근의 대기 중인 최적화 결과"""
    pending_path = "data/pending_optimized_params.json"
    if os.path.exists(pending_path):
        with open(pending_path, "r") as f:
            return json.load(f)
    return {"error": "No pending optimization report found"}

def run_api_server(manager, pm, agent_name="crypto_manager", host="0.0.0.0", port=8000):
    """외부에서 호출하여 서버를 백그라운드 스레드로 실행"""
    state.manager = manager
    state.portfolio_manager = pm
    state.agent_name = agent_name
    
    def start():
        logger.info(f"🌐 [API Server] Starting at http://{host}:{port}")
        uvicorn.run(app, host=host, port=port, log_level="warning")

    thread = threading.Thread(target=start, daemon=True)
    thread.start()
    return thread
