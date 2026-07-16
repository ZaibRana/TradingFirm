/**
 * ProStockCard — Card component for Professional Scanner results.
 * Shows: ATRP, RVOL, Float, 52w position, expandable clickable news.
 */
"use client";
import { useState } from "react";

export default function ProStockCard({ stock }) {
  const [newsOpen, setNewsOpen] = useState(false);

  if (!stock) return null;

  const pos52w = stock.pos52w || 50;
  const hasNews = stock.news && stock.news.length > 0;

  const rvolClass =
    stock.rvol >= 2.0
      ? "pro-card__val--fire"
      : stock.rvol >= 1.5
        ? "pro-card__val--hot"
        : "";

  return (
    <div className="pro-card">
      {/* Header */}
      <div className="pro-card__header">
        <div>
          <span className="pro-card__symbol">{stock.symbol}</span>
          <span className="pro-card__name">{stock.name}</span>
        </div>
        <span className="pro-card__price">${stock.price?.toFixed(2)}</span>
      </div>

      {/* Key Metrics */}
      <div className="pro-card__metrics">
        <div className="pro-card__metric">
          <span className="pro-card__label">ATRP</span>
          <span className="pro-card__val">{stock.atrp?.toFixed(1)}%</span>
        </div>
        <div className="pro-card__metric">
          <span className="pro-card__label">RVOL</span>
          <span className={`pro-card__val ${rvolClass}`}>
            {stock.rvol?.toFixed(2)}x
          </span>
        </div>
        <div className="pro-card__metric">
          <span className="pro-card__label">Float</span>
          <span className="pro-card__val">{stock.floatStr || "N/A"}</span>
        </div>
        <div className="pro-card__metric">
          <span className="pro-card__label">5M Move</span>
          <span className="pro-card__val pro-card__val--hot">
            {stock.bigBodyPct ? `${stock.bigBodyPct.toFixed(0)}%` : "—"}
          </span>
        </div>
        <div className="pro-card__metric">
          <span className="pro-card__label">5M Range</span>
          <span className="pro-card__val">
            {stock.med5mRange ? `$${stock.med5mRange.toFixed(2)}` : "—"}
          </span>
        </div>
      </div>

      {/* 52-Week Range */}
      <div className="pro-card__range">
        <div className="pro-card__range-labels">
          <span>52w: ${stock.fiftyTwoWeekLow?.toFixed(0) ?? "—"}</span>
          <span>{pos52w}%</span>
          <span>${stock.fiftyTwoWeekHigh?.toFixed(0) ?? "—"}</span>
        </div>
        <div className="pro-card__range-bar">
          <div
            className="pro-card__range-fill"
            style={{ width: `${Math.min(100, Math.max(0, pos52w))}%` }}
          />
        </div>
      </div>

      {/* Badges */}
      <div className="pro-card__badges">
        {stock.industry && (
          <span className="badge badge--blue">{stock.industry}</span>
        )}
        {stock.rvol >= 2.0 && (
          <span className="badge badge--amber">🔥 High RVOL</span>
        )}
      </div>

      {/* News — expandable */}
      {hasNews && (
        <div className="pro-card__news">
          <button
            className="pro-card__news-toggle"
            onClick={() => setNewsOpen(!newsOpen)}
          >
            <span>📰 News ({stock.news.length})</span>
            <span className={`pro-card__chevron ${newsOpen ? "pro-card__chevron--open" : ""}`}>
              ▾
            </span>
          </button>
          {newsOpen && (
            <div className="pro-card__news-list">
              {stock.news.map((item, i) => {
                const title = typeof item === "string" ? item : item.title;
                const url = typeof item === "string" ? null : item.url;
                const publisher = typeof item === "string" ? null : item.publisher;
                return (
                  <div key={i} className="pro-card__news-item">
                    {url ? (
                      <a
                        href={url}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="pro-card__news-link"
                      >
                        {title}
                      </a>
                    ) : (
                      <span className="pro-card__news-text">{title}</span>
                    )}
                    {publisher && (
                      <span className="pro-card__news-pub">{publisher}</span>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      <style jsx>{`
        .pro-card {
          background: var(--bg-card);
          border: 1px solid var(--border-color);
          border-radius: 16px;
          padding: 20px;
          transition: all 0.25s ease;
          display: flex;
          flex-direction: column;
          gap: 14px;
        }
        .pro-card:hover {
          border-color: var(--accent);
          transform: translateY(-2px);
          box-shadow: 0 8px 24px rgba(0, 0, 0, 0.2);
        }

        .pro-card__header {
          display: flex;
          justify-content: space-between;
          align-items: flex-start;
        }
        .pro-card__symbol {
          font-size: 1.2rem;
          font-weight: 700;
          color: var(--text-primary);
          margin-right: 8px;
        }
        .pro-card__name {
          font-size: 0.78rem;
          color: var(--text-secondary);
          display: block;
          margin-top: 2px;
        }
        .pro-card__price {
          font-size: 1.3rem;
          font-weight: 700;
          color: var(--text-primary);
        }

        .pro-card__metrics {
          display: grid;
          grid-template-columns: repeat(5, 1fr);
          gap: 8px;
        }
        .pro-card__metric {
          text-align: center;
          background: var(--bg-secondary);
          border-radius: 10px;
          padding: 8px 4px;
        }
        .pro-card__label {
          display: block;
          font-size: 0.65rem;
          text-transform: uppercase;
          letter-spacing: 0.5px;
          color: var(--text-secondary);
          margin-bottom: 2px;
        }
        .pro-card__val {
          font-size: 0.9rem;
          font-weight: 600;
          color: var(--text-primary);
        }
        .pro-card__val--hot { color: #f59e0b; }
        .pro-card__val--fire { color: #ef4444; }

        .pro-card__range { margin-top: 2px; }
        .pro-card__range-labels {
          display: flex;
          justify-content: space-between;
          font-size: 0.7rem;
          color: var(--text-secondary);
          margin-bottom: 4px;
        }
        .pro-card__range-bar {
          height: 5px;
          background: var(--bg-secondary);
          border-radius: 3px;
          overflow: hidden;
        }
        .pro-card__range-fill {
          height: 100%;
          background: linear-gradient(90deg, #ef4444, #f59e0b, #22c55e);
          border-radius: 3px;
          transition: width 0.4s ease;
        }

        .pro-card__badges {
          display: flex;
          flex-wrap: wrap;
          gap: 6px;
        }

        /* News */
        .pro-card__news {
          border-top: 1px solid var(--border-color);
          padding-top: 10px;
          margin-top: 2px;
        }
        .pro-card__news-toggle {
          display: flex;
          align-items: center;
          justify-content: space-between;
          width: 100%;
          background: none;
          border: none;
          padding: 4px 0;
          cursor: pointer;
          font-family: inherit;
          font-size: 0.75rem;
          font-weight: 600;
          color: var(--text-secondary);
          letter-spacing: 0.3px;
        }
        .pro-card__news-toggle:hover { color: var(--text-primary); }
        .pro-card__chevron {
          font-size: 0.8rem;
          transition: transform 0.2s ease;
        }
        .pro-card__chevron--open { transform: rotate(180deg); }
        .pro-card__news-list {
          margin-top: 8px;
          display: flex;
          flex-direction: column;
          gap: 8px;
        }
        .pro-card__news-item {
          display: flex;
          flex-direction: column;
          gap: 2px;
        }
        .pro-card__news-link {
          font-size: 0.75rem;
          color: var(--accent-blue, #448aff);
          text-decoration: none;
          line-height: 1.35;
          transition: color 0.15s ease;
        }
        .pro-card__news-link:hover {
          color: #66b3ff;
          text-decoration: underline;
        }
        .pro-card__news-text {
          font-size: 0.75rem;
          color: var(--text-secondary);
          line-height: 1.35;
        }
        .pro-card__news-pub {
          font-size: 0.6rem;
          color: var(--text-muted);
          text-transform: uppercase;
          letter-spacing: 0.3px;
        }
      `}</style>
    </div>
  );
}
