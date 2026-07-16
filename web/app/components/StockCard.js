"use client";

function formatVolume(vol) {
  if (!vol) return "—";
  if (vol >= 1_000_000) return (vol / 1_000_000).toFixed(1) + "M";
  if (vol >= 1_000) return (vol / 1_000).toFixed(0) + "K";
  return vol.toString();
}

function formatMarketCap(cap) {
  if (!cap) return "—";
  if (cap >= 1e12) return "$" + (cap / 1e12).toFixed(1) + "T";
  if (cap >= 1e9) return "$" + (cap / 1e9).toFixed(1) + "B";
  if (cap >= 1e6) return "$" + (cap / 1e6).toFixed(0) + "M";
  return "$" + cap.toLocaleString();
}

export default function StockCard({ stock }) {
  const isPositive = (stock?.changePercent || 0) >= 0;

  return (
    <div className="stock-card">
      <div className="stock-card__header">
        <div className="stock-card__ticker-row">
          <span className="stock-card__ticker">{stock?.symbol || "—"}</span>
          <span
            className={`stock-card__change ${isPositive ? "stock-card__change--up" : "stock-card__change--down"}`}
          >
            {isPositive ? "▲" : "▼"}{" "}
            {Math.abs(stock?.changePercent || 0).toFixed(2)}%
          </span>
        </div>
        <p className="stock-card__name">{stock?.name || "Company Name"}</p>
      </div>

      <div className="stock-card__price-row">
        <span className="stock-card__price">
          ${(stock?.price || 0).toFixed(2)}
        </span>
        <span
          className={`stock-card__price-change ${isPositive ? "stock-card__price-change--up" : "stock-card__price-change--down"}`}
        >
          {isPositive ? "+" : ""}
          {(stock?.change || 0).toFixed(2)}
        </span>
      </div>

      <div className="stock-card__meta">
        <div className="stock-card__meta-item">
          <span className="stock-card__meta-label">ATR</span>
          <span className="stock-card__meta-value">
            ${stock?.atr?.toFixed(2) || "—"}
          </span>
        </div>
        <div className="stock-card__meta-item">
          <span className="stock-card__meta-label">ADX</span>
          <span className={`stock-card__meta-value ${
            (stock?.adx || 0) >= 30 ? "stock-card__meta-value--hot" : ""
          }`}>
            {stock?.adx?.toFixed(1) || "—"}
          </span>
        </div>
        <div className="stock-card__meta-item">
          <span className="stock-card__meta-label">Target</span>
          <span className="stock-card__meta-value">
            ${stock?.analystTargetPrice?.toFixed(0) || "—"}
          </span>
        </div>
      </div>

      {/* 52-week range bar */}
      {stock?.high52w > 0 && stock?.low52w > 0 && (
        <div className="stock-card__range">
          <div className="stock-card__range-labels">
            <span>52w: ${stock.low52w.toFixed(0)}</span>
            <span>${stock.high52w.toFixed(0)}</span>
          </div>
          <div className="stock-card__range-bar">
            <div
              className="stock-card__range-fill"
              style={{
                width: `${Math.min(100, Math.max(0, ((stock.price - stock.low52w) / (stock.high52w - stock.low52w)) * 100))}%`,
              }}
            />
          </div>
        </div>
      )}

      <div className="stock-card__badges">
        {stock?.analystRating && (
          <span
            className={`badge ${
              stock.analystRating.includes("buy")
                ? "badge--green"
                : stock.analystRating === "hold"
                  ? "badge--amber"
                  : "badge--red"
            }`}
          >
            {stock.analystRating === "strong_buy" ? "Strong Buy" :
             stock.analystRating.charAt(0).toUpperCase() + stock.analystRating.slice(1)}
          </span>
        )}
        {stock?.options?.bullish && (
          <span className="badge badge--green">🟢 Bullish Options</span>
        )}
        {stock?.industry && (
          <span className="badge badge--blue">{stock.industry}</span>
        )}
      </div>

      <style jsx>{`
        .stock-card {
          background: var(--bg-card);
          border: 1px solid var(--border-color);
          border-radius: var(--radius-lg);
          padding: 20px;
          transition: all var(--transition-normal);
          cursor: pointer;
          display: flex;
          flex-direction: column;
          gap: 12px;
        }

        .stock-card:hover {
          border-color: var(--border-color-hover);
          background: var(--bg-card-hover);
          transform: translateY(-2px);
          box-shadow: var(--shadow-card);
        }

        .stock-card__header {
          display: flex;
          flex-direction: column;
          gap: 2px;
        }

        .stock-card__ticker-row {
          display: flex;
          align-items: center;
          justify-content: space-between;
        }

        .stock-card__ticker {
          font-size: 1.1rem;
          font-weight: 700;
          color: var(--text-primary);
          letter-spacing: 0.02em;
        }

        .stock-card__change {
          font-size: 0.75rem;
          font-weight: 600;
          padding: 2px 8px;
          border-radius: 4px;
        }

        .stock-card__change--up {
          color: var(--accent-green);
          background: var(--accent-green-dim);
        }

        .stock-card__change--down {
          color: var(--accent-red);
          background: var(--accent-red-dim);
        }

        .stock-card__name {
          font-size: 0.72rem;
          color: var(--text-muted);
          white-space: nowrap;
          overflow: hidden;
          text-overflow: ellipsis;
        }

        .stock-card__price-row {
          display: flex;
          align-items: baseline;
          gap: 10px;
        }

        .stock-card__price {
          font-size: 1.5rem;
          font-weight: 700;
          color: var(--text-primary);
        }

        .stock-card__price-change {
          font-size: 0.85rem;
          font-weight: 600;
        }

        .stock-card__price-change--up {
          color: var(--accent-green);
        }

        .stock-card__price-change--down {
          color: var(--accent-red);
        }

        .stock-card__meta {
          display: grid;
          grid-template-columns: 1fr 1fr 1fr;
          gap: 8px;
        }

        .stock-card__meta-item {
          display: flex;
          flex-direction: column;
          gap: 2px;
        }

        .stock-card__meta-label {
          font-size: 0.62rem;
          color: var(--text-muted);
          text-transform: uppercase;
          letter-spacing: 0.08em;
          font-weight: 600;
        }

        .stock-card__meta-value {
          font-size: 0.85rem;
          font-weight: 600;
          color: var(--text-secondary);
        }

        .stock-card__meta-value--hot {
          color: var(--accent-amber);
        }

        .stock-card__meta-value--warm {
          color: var(--accent-green);
        }

        .stock-card__range {
          display: flex;
          flex-direction: column;
          gap: 4px;
        }

        .stock-card__range-labels {
          display: flex;
          justify-content: space-between;
          font-size: 0.6rem;
          color: var(--text-muted);
        }

        .stock-card__range-bar {
          height: 3px;
          background: var(--border-color);
          border-radius: 2px;
          overflow: hidden;
        }

        .stock-card__range-fill {
          height: 100%;
          background: linear-gradient(90deg, var(--accent-green), var(--accent-blue));
          border-radius: 2px;
          transition: width 0.5s ease;
        }

        .stock-card__badges {
          display: flex;
          gap: 6px;
          flex-wrap: wrap;
        }
      `}</style>
    </div>
  );
}
