"use client";

import { useState, useEffect } from "react";

export default function Home() {
  const [portfolio, setPortfolio] = useState(null);
  const [status, setStatus] = useState(null);
  const [holdings, setHoldings] = useState([]);
  const [loading, setLoading] = useState(true);

  const fetchData = async () => {
    try {
      const [pRes, sRes, hRes] = await Promise.all([
        fetch("http://localhost:8000/api/portfolio"),
        fetch("http://localhost:8000/api/status"),
        fetch("http://localhost:8000/api/holdings"),
      ]);

      const pData = await pRes.json();
      const sData = await sRes.json();
      const hData = await hRes.json();

      setPortfolio(pData);
      setStatus(sData);
      setHoldings(hData);
      setLoading(false);
    } catch (error) {
      console.error("Failed to fetch bot data:", error);
    }
  };

  useEffect(() => {
    fetchData();
    const interval = setInterval(fetchData, 5000); // 5초마다 갱신
    return () => clearInterval(interval);
  }, []);

  if (loading) {
    return (
      <div className="dashboard-container">
        <div className="header">
          <h1 className="bot-title">HERMES <span>BOT</span></h1>
          <div className="loading">데이터를 불러오는 중...</div>
        </div>
      </div>
    );
  }

  return (
    <div className="dashboard-container">
      {/* Header */}
      <div className="header">
        <div>
          <h1 className="bot-title">HERMES <span>BOT</span></h1>
          <p style={{ color: "var(--text-dim)", fontSize: "0.9rem", marginTop: "0.5rem" }}>
            Real-time Trading Intelligence
          </p>
        </div>
        <div className="status-badge">
          <div className="status-dot"></div>
          {status.is_halted ? "HALTED" : "LIVE & TRADING"} | {status.current_regime.toUpperCase()}
        </div>
      </div>

      {/* Stats Summary */}
      <div className="stats-grid">
        <div className="stat-card">
          <div className="stat-label">Total Portfolio Value</div>
          <div className="stat-value">
            {portfolio.total_value?.toLocaleString()} <span style={{fontSize: "1rem"}}>KRW</span>
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Total Return</div>
          <div className={`stat-value ${portfolio.return_rate >= 0 ? "success" : "danger"}`}>
            {portfolio.return_rate?.toFixed(2)}%
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Profit Factor</div>
          <div className="stat-value" style={{ color: "var(--primary-color)" }}>
            {portfolio.profit_factor === Infinity ? "∞" : portfolio.profit_factor?.toFixed(2)}
          </div>
        </div>
        <div className="stat-card">
          <div className="stat-label">Max Drawdown</div>
          <div className="stat-value danger">
            -{portfolio.max_drawdown?.toFixed(2)}%
          </div>
        </div>
      </div>

      {/* Active Holdings */}
      <div className="holdings-section">
        <h2 className="section-title">
          <span style={{ color: "var(--primary-color)" }}>●</span> Active Holdings
        </h2>
        {holdings.length > 0 ? (
          <table>
            <thead>
              <tr>
                <th>Ticker</th>
                <th>Strategy</th>
                <th>Volume</th>
                <th>Avg. Price</th>
                <th>Total Cost</th>
                <th>P&L</th>
              </tr>
            </thead>
            <tbody>
              {holdings.map((h) => {
                // 이 예시에서는 실시간 현재가가 없으므로 avg_price로 ROI 0 표시 (추후 강화 가능)
                return (
                  <tr key={h.ticker}>
                    <td className="ticker-name">{h.ticker}</td>
                    <td><span className="strategy-tag">{h.strategy}</span></td>
                    <td>{h.volume.toFixed(4)}</td>
                    <td>{h.avg_price.toLocaleString()}</td>
                    <td>{h.total_cost.toLocaleString()}</td>
                    <td className="success">-- %</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        ) : (
          <div style={{ padding: "2rem", textAlign: "center", color: "var(--text-dim)" }}>
            현재 보유 중인 종목이 없습니다.
          </div>
        )}
      </div>

      {/* Footer Info */}
      <div style={{ marginTop: "3rem", color: "var(--text-dim)", fontSize: "0.8rem", textAlign: "center" }}>
        Last Sync: {status.timestamp} | Agent: {status.agent_name}
      </div>
    </div>
  );
}
