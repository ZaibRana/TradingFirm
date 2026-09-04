"use client";

import { useState, useEffect } from "react";

const BACKEND = process.env.NEXT_PUBLIC_DATA_ENGINE_URL || "http://localhost:8001";

export default function Header() {
  const [marketStatus, setMarketStatus] = useState({ status: "unknown", display: "" });

  useEffect(() => {
    fetch(`${BACKEND}/market/status`)
      .then((r) => r.json())
      .then((data) => setMarketStatus(data))
      .catch(() => setMarketStatus({ status: "unknown", display: "Unavailable" }));

    // Refresh every 60 seconds
    const interval = setInterval(() => {
      fetch(`${BACKEND}/market/status`)
        .then((r) => r.json())
        .then((data) => setMarketStatus(data))
        .catch(() => {});
    }, 60000);
    return () => clearInterval(interval);
  }, []);

  const isOpen = marketStatus.status === "market_open";
  const statusLabel = isOpen
    ? "Market Open"
    : marketStatus.status === "pre_market"
      ? "Pre-Market"
      : marketStatus.status === "post_market"
        ? "After Hours"
        : "Market Closed";

  return (
    <header className="header">
      <div className="header__left">
        <h1 className="header__logo">
          <span className="header__logo-icon">◈</span>
          TradingFirm
        </h1>
        <span className="header__badge">SCANNER</span>
      </div>

      <div className="header__right">
        <div className="header__market-status">
          <span className={`header__dot header__dot--${isOpen ? "open" : "closed"}`} id="market-dot" />
          <span className="header__market-label">{statusLabel}</span>
          {marketStatus.display && (
            <span className="header__market-time">{marketStatus.display}</span>
          )}
        </div>
      </div>

      <style jsx>{`
        .header {
          display: flex;
          align-items: center;
          justify-content: space-between;
          padding: 14px 28px;
          background: var(--bg-secondary);
          border-bottom: 1px solid var(--border-color);
          position: sticky;
          top: 0;
          z-index: 100;
          backdrop-filter: blur(12px);
        }

        .header__left {
          display: flex;
          align-items: center;
          gap: 12px;
        }

        .header__logo {
          font-size: 1.25rem;
          font-weight: 700;
          background: linear-gradient(135deg, #448aff, #00e676);
          -webkit-background-clip: text;
          -webkit-text-fill-color: transparent;
          display: flex;
          align-items: center;
          gap: 8px;
        }

        .header__logo-icon {
          font-size: 1.4rem;
        }

        .header__badge {
          font-size: 0.6rem;
          font-weight: 700;
          letter-spacing: 0.12em;
          padding: 3px 8px;
          border-radius: 4px;
          background: var(--accent-blue-dim);
          color: var(--accent-blue);
        }

        .header__right {
          display: flex;
          align-items: center;
          gap: 20px;
        }

        .header__market-status {
          display: flex;
          align-items: center;
          gap: 6px;
          font-size: 0.8rem;
          color: var(--text-secondary);
        }

        .header__market-time {
          font-size: 0.7rem;
          color: var(--text-muted);
          margin-left: 4px;
        }

        .header__dot {
          width: 7px;
          height: 7px;
          border-radius: 50%;
        }

        .header__dot--open {
          background: var(--accent-green);
          box-shadow: 0 0 8px var(--accent-green);
          animation: pulse 2s infinite;
        }

        .header__dot--closed {
          background: var(--text-muted);
        }

        @keyframes pulse {
          0%,
          100% {
            opacity: 1;
          }
          50% {
            opacity: 0.4;
          }
        }
      `}</style>
    </header>
  );
}
