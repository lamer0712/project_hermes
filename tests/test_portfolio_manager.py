import pytest
import os
import json
from src.core.portfolio_manager import PortfolioManager
from src.data.db import DatabaseManager

@pytest.fixture
def temp_db():
    db_path = "tests/test_portfolio.db"
    
    # Ensure fresh DB state
    if os.path.exists(db_path):
        os.remove(db_path)
        
    db = DatabaseManager(db_path)
    yield db_path
    
    # Cleanup after test
    if os.path.exists(db_path):
        os.remove(db_path)

@pytest.fixture
def pm(temp_db):
    return PortfolioManager(total_capital=1000000, db_path=temp_db)

def test_allocation(pm):
    # Allocate funds to an agent
    pm.allocate("agent_a", 50000)
    assert pm.get_available_cash("agent_a") == 50000
    
    # Check repeated allocation adds up
    pm.allocate("agent_a", 25000)
    assert pm.get_available_cash("agent_a") == 75000
    assert pm.portfolios["agent_a"]["initial_capital"] == 75000

def test_record_buy(pm):
    pm.allocate("agent_a", 100000)
    
    # Buy 1000 KRW worth
    success = pm.record_buy("agent_a", "KRW-BTC", volume=0.01, price=100000, executed_funds=1000, paid_fee=1.0)
    
    assert success is True
    assert pm.get_available_cash("agent_a") == 100000 - 1001.0
    
    holdings = pm.get_holdings("agent_a")
    assert "KRW-BTC" in holdings
    assert holdings["KRW-BTC"]["volume"] == 0.01
    assert holdings["KRW-BTC"]["avg_price"] == 100000
    assert holdings["KRW-BTC"]["total_cost"] == 1000

def test_record_sell(pm):
    pm.allocate("agent_a", 100000)
    
    # Setup holding
    pm.record_buy("agent_a", "KRW-BTC", volume=0.01, price=100000, executed_funds=1000, paid_fee=1.0)
    
    # Partial sell
    success = pm.record_sell("agent_a", "KRW-BTC", volume=0.005, price=200000, executed_funds=1000, paid_fee=1.0)
    
    assert success is True
    # Cash should be (100000 - 1001) + (1000 - 1)
    assert pm.get_available_cash("agent_a") == 98999.0 + 999.0
    
    holdings = pm.get_holdings("agent_a")
    assert holdings["KRW-BTC"]["volume"] == 0.005
    assert holdings["KRW-BTC"]["total_cost"] == 500.0

def test_db_persistence(temp_db):
    pm1 = PortfolioManager(total_capital=1000000, db_path=temp_db)
    pm1.allocate("agent_a", 100000)
    pm1.record_buy("agent_a", "KRW-BTC", volume=0.01, price=100000, executed_funds=1000, paid_fee=1.0)
    
    # Create new instance pointed at same DB to verify loading
    pm2 = PortfolioManager(total_capital=1000000, db_path=temp_db)
    
    assert "agent_a" in pm2.portfolios
    assert pm2.get_available_cash("agent_a") == 100000 - 1001.0
    assert "KRW-BTC" in pm2.get_holdings("agent_a")
