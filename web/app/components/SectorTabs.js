"use client";

const SECTOR_ICONS = {
  all: "◎",
  ai: "🤖",
  chips: "⚡",
  quantum: "⚛️",
  energy: "🔋",
  robo: "🦾",
  health: "🏥",
  finance: "🏦",
  telecom: "📡",
  consumer: "🛍️",
  industrial: "🏭",
  materials: "⛏️",
  property: "🏗️",
  utilities: "💡",
  tech: "💻",
  other: "📦",
};

export default function SectorTabs({ activeSector, onSectorChange, stocks }) {
  // Build tabs dynamically from actual stock data
  const sectorCounts = {};
  (stocks || []).forEach((stock) => {
    const id = stock.sectorId || "other";
    const label = stock.sector || "Other";
    if (!sectorCounts[id]) {
      sectorCounts[id] = { id, label, count: 0 };
    }
    sectorCounts[id].count++;
  });

  // Sort sectors by count (most stocks first), then alphabetically
  const sectors = Object.values(sectorCounts).sort(
    (a, b) => b.count - a.count || a.label.localeCompare(b.label)
  );

  // Only show "All" + sector tabs if we have stocks
  const tabs = stocks?.length
    ? [{ id: "all", label: "All", count: stocks.length }, ...sectors]
    : [];

  if (tabs.length === 0) return null;

  return (
    <div className="sector-tabs">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          className={`sector-tab ${activeSector === tab.id ? "sector-tab--active" : ""}`}
          onClick={() => onSectorChange(tab.id)}
        >
          <span className="sector-tab__icon">
            {SECTOR_ICONS[tab.id] || "📦"}
          </span>
          <span className="sector-tab__label">{tab.label}</span>
          <span className="sector-tab__count">{tab.count}</span>
        </button>
      ))}

      <style jsx>{`
        .sector-tabs {
          display: flex;
          gap: 6px;
          padding: 4px;
          overflow-x: auto;
          scrollbar-width: none;
        }

        .sector-tabs::-webkit-scrollbar {
          display: none;
        }

        .sector-tab {
          display: flex;
          align-items: center;
          gap: 6px;
          padding: 8px 16px;
          border-radius: 100px;
          border: 1px solid var(--border-color);
          background: transparent;
          color: var(--text-secondary);
          font-size: 0.8rem;
          font-weight: 500;
          cursor: pointer;
          white-space: nowrap;
          transition: all var(--transition-fast);
          font-family: inherit;
        }

        .sector-tab:hover {
          border-color: var(--border-color-hover);
          color: var(--text-primary);
          background: var(--bg-card);
        }

        .sector-tab--active {
          background: var(--accent-blue-dim);
          border-color: var(--accent-blue);
          color: var(--accent-blue);
        }

        .sector-tab--active:hover {
          background: var(--accent-blue-dim);
          color: var(--accent-blue);
        }

        .sector-tab__icon {
          font-size: 0.9rem;
        }

        .sector-tab__count {
          font-size: 0.65rem;
          font-weight: 700;
          background: var(--bg-secondary);
          padding: 1px 6px;
          border-radius: 100px;
          color: var(--text-muted);
        }

        .sector-tab--active .sector-tab__count {
          background: rgba(68, 138, 255, 0.2);
          color: var(--accent-blue);
        }
      `}</style>
    </div>
  );
}
