"use client";

import { useState, useEffect } from "react";
import Header from "./components/Header";
import SectorTabs from "./components/SectorTabs";
import ProStockCard from "./components/ProStockCard";

export default function Home() {
  // Professional Scanner state
  const [proStocks, setProStocks] = useState([]);
  const [proScanning, setProScanning] = useState(false);
  const [proProgress, setProProgress] = useState("");
  const [proInfo, setProInfo] = useState(null);
  const [proAdvanced, setProAdvanced] = useState(true);
  const [priceMin, setPriceMin] = useState("");
  const [priceMax, setPriceMax] = useState("");

  // Sector filter
  const [activeSector, setActiveSector] = useState("all");

  const filteredStocks =
    activeSector === "all"
      ? proStocks
      : proStocks.filter((s) => s.sectorId === activeSector);

  // ─── Load cached results on mount ─────────────────────────────
  useEffect(() => {
    fetch("/api/scanner/pro")
      .then((r) => r.json())
      .then((data) => {
        if (data.success && data.stocks?.length > 0) {
          setProStocks(mapProResults(data));
          setProInfo(data);
        }
      })
      .catch(() => {});
  }, []);

  // ─── Mappers ──────────────────────────────────────────────────
  const mapProResults = (data) =>
    (data.stocks || []).map((s) => ({
      ...s,
      sectorId: (s.sector || "other").toLowerCase().replace(/\s+/g, "_"),
    }));

  // ─── Scan Handler ─────────────────────────────────────────────
  const handleProScan = async () => {
    setProScanning(true);
    setProProgress(proAdvanced
      ? "Running Professional Scanner + 5M Filters… ~3 min"
      : "Running Professional Scanner… ~3 min");
    try {
      const res = await fetch("/api/scanner/pro", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          advanced: proAdvanced,
          priceMin: priceMin || null,
          priceMax: priceMax || null,
        }),
      });
      const data = await res.json();
      if (!data.success) throw new Error(data.error);
      setProStocks(mapProResults(data));
      setProInfo(data);
      setProProgress(`${data.totalScanned} scanned → ${data.passedCount} passed`);
      setTimeout(() => setProProgress(""), 5000);
    } catch (err) {
      setProProgress(`Error: ${err.message}`);
      setTimeout(() => setProProgress(""), 5000);
    } finally {
      setProScanning(false);
    }
  };

  const subtitle = proProgress
    ? proProgress
    : proInfo
      ? `${proInfo.passedCount} results · ${proInfo.totalScanned} scanned · ${proInfo.marketStatus || ""}${proInfo.duration ? " · " + proInfo.duration.split(".")[0] : ""}`
      : "Run scanner to find today's tradeable stocks";

  return (
    <>
      <Header />
      <main className="dashboard">
        {/* Controls */}
        <section className="scanner-controls">
          <div className="scanner-controls__left">
            <h2 className="scanner-controls__title">Professional Day Trading</h2>
            <p className="scanner-controls__subtitle">{subtitle}</p>
            {proInfo?.marketStatus === "weekend" && (
              <p className="scanner-controls__warning">
                ⚠ Weekend mode — RVOL relaxed to &gt;1.0, using Friday data
              </p>
            )}
          </div>
          <div className="scanner-controls__right">
            <div className="pro-filters">
              <div className="price-inputs">
                <span className="price-inputs__label">Price $</span>
                <input
                  type="number"
                  className="price-input"
                  placeholder="Min"
                  value={priceMin}
                  onChange={(e) => setPriceMin(e.target.value)}
                  min="0"
                />
                <span className="price-inputs__sep">–</span>
                <input
                  type="number"
                  className="price-input"
                  placeholder="Max"
                  value={priceMax}
                  onChange={(e) => setPriceMax(e.target.value)}
                  min="0"
                />
              </div>
              <label className="toggle-row">
                <span className="toggle-label">5M Filters</span>
                <div
                  className={`toggle-switch ${proAdvanced ? "toggle-switch--on" : ""}`}
                  onClick={() => setProAdvanced(!proAdvanced)}
                  role="switch"
                  aria-checked={proAdvanced}
                >
                  <div className="toggle-knob" />
                </div>
              </label>
            </div>
            <button
              className={`scan-btn ${proScanning ? "scan-btn--scanning" : ""}`}
              onClick={handleProScan}
              disabled={proScanning}
            >
              {proScanning ? (
                <>
                  <span className="scan-btn__spinner" />
                  Scanning…
                </>
              ) : (
                <>
                  <span className="scan-btn__icon">⟳</span>
                  Run Scanner
                </>
              )}
            </button>
          </div>
        </section>

        {/* Sector Tabs */}
        <section className="sector-section">
          <SectorTabs
            activeSector={activeSector}
            onSectorChange={setActiveSector}
            stocks={proStocks}
          />
        </section>

        {/* Results Count */}
        <div className="results-info">
          <span className="results-info__count">
            {filteredStocks.length} stock{filteredStocks.length !== 1 ? "s" : ""}
          </span>
          {activeSector !== "all" && (
            <span className="results-info__filter">
              in {activeSector.replace(/_/g, " ")}
            </span>
          )}
        </div>

        {/* Stock Grid */}
        <section className="stock-grid">
          {filteredStocks.map((stock) => (
            <ProStockCard key={stock.symbol} stock={stock} />
          ))}
          {filteredStocks.length === 0 && !proScanning && (
            <div className="empty-state">
              <div className="empty-state__content">
                <span className="empty-state__icon">⚡</span>
                <p className="empty-state__title">No stocks loaded</p>
                <p className="empty-state__desc">
                  Click <strong>Run Scanner</strong> to discover
                  today&apos;s top tradeable stocks
                </p>
              </div>
            </div>
          )}
        </section>
      </main>

      <style jsx>{`
        .dashboard {
          max-width: 1280px;
          margin: 0 auto;
          padding: 24px 28px 60px;
        }

        /* Controls */
        .scanner-controls {
          display: flex;
          align-items: center;
          justify-content: space-between;
          margin-bottom: 24px;
        }
        .scanner-controls__right {
          display: flex;
          align-items: center;
          gap: 16px;
        }

        /* Pro Filters Row */
        .pro-filters {
          display: flex;
          align-items: center;
          gap: 16px;
        }
        .price-inputs {
          display: flex;
          align-items: center;
          gap: 6px;
        }
        .price-inputs__label {
          font-size: 0.75rem;
          font-weight: 600;
          color: var(--text-secondary);
          letter-spacing: 0.3px;
        }
        .price-inputs__sep {
          font-size: 0.8rem;
          color: var(--text-muted);
        }
        .price-input {
          width: 64px;
          padding: 5px 8px;
          border-radius: 8px;
          border: 1px solid var(--border-color);
          background: var(--bg-secondary);
          color: var(--text-primary);
          font-family: inherit;
          font-size: 0.78rem;
          text-align: center;
          outline: none;
          transition: border-color 0.2s ease;
        }
        .price-input:focus {
          border-color: var(--accent-blue);
        }
        .price-input::placeholder {
          color: var(--text-muted);
          font-size: 0.72rem;
        }
        /* Hide number spinners */
        .price-input::-webkit-outer-spin-button,
        .price-input::-webkit-inner-spin-button {
          -webkit-appearance: none;
          margin: 0;
        }

        /* Toggle */
        .toggle-row {
          display: flex;
          align-items: center;
          gap: 8px;
          cursor: pointer;
          user-select: none;
        }
        .toggle-label {
          font-size: 0.75rem;
          font-weight: 600;
          color: var(--text-secondary);
          letter-spacing: 0.3px;
        }
        .toggle-switch {
          width: 40px;
          height: 22px;
          border-radius: 11px;
          background: var(--bg-secondary);
          border: 1px solid var(--border-color);
          position: relative;
          cursor: pointer;
          transition: all 0.25s ease;
        }
        .toggle-switch--on {
          background: linear-gradient(135deg, #448aff, #00b0ff);
          border-color: #448aff;
        }
        .toggle-knob {
          width: 16px;
          height: 16px;
          border-radius: 50%;
          background: #fff;
          position: absolute;
          top: 2px;
          left: 2px;
          transition: transform 0.25s ease;
          box-shadow: 0 1px 3px rgba(0,0,0,0.2);
        }
        .toggle-switch--on .toggle-knob {
          transform: translateX(18px);
        }
        .scanner-controls__title {
          font-size: 1.4rem;
          font-weight: 700;
          color: var(--text-primary);
        }
        .scanner-controls__subtitle {
          font-size: 0.8rem;
          color: var(--text-muted);
          margin-top: 4px;
        }
        .scanner-controls__warning {
          font-size: 0.75rem;
          color: #f59e0b;
          margin-top: 4px;
        }

        .scan-btn {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 10px 24px;
          border-radius: var(--radius-md);
          border: none;
          font-family: inherit;
          font-size: 0.85rem;
          font-weight: 600;
          cursor: pointer;
          transition: all var(--transition-fast);
          background: linear-gradient(135deg, #448aff, #00b0ff);
          color: #fff;
          box-shadow: 0 4px 16px rgba(68, 138, 255, 0.25);
        }
        .scan-btn:hover:not(:disabled) {
          transform: translateY(-1px);
          box-shadow: 0 6px 24px rgba(68, 138, 255, 0.35);
        }
        .scan-btn:disabled {
          opacity: 0.7;
          cursor: not-allowed;
        }
        .scan-btn--scanning {
          background: var(--bg-card);
          border: 1px solid var(--border-color);
          box-shadow: none;
          color: var(--text-secondary);
        }
        .scan-btn__icon { font-size: 1.1rem; }
        .scan-btn__spinner {
          width: 14px; height: 14px;
          border: 2px solid var(--text-muted);
          border-top-color: var(--accent-blue);
          border-radius: 50%;
          animation: spin 0.8s linear infinite;
        }

        .sector-section { margin-bottom: 20px; }

        .results-info {
          display: flex;
          align-items: center;
          gap: 6px;
          margin-bottom: 16px;
          font-size: 0.78rem;
          color: var(--text-muted);
        }
        .results-info__count {
          font-weight: 600;
          color: var(--text-secondary);
        }

        .stock-grid {
          display: grid;
          grid-template-columns: repeat(auto-fill, minmax(300px, 1fr));
          gap: 16px;
        }

        .empty-state {
          grid-column: 1 / -1;
          text-align: center;
          padding: 80px 20px;
          color: var(--text-muted);
          background: var(--bg-card);
          border: 1px dashed var(--border-color);
          border-radius: var(--radius-lg);
        }
        .empty-state__content {
          display: flex;
          flex-direction: column;
          align-items: center;
          gap: 8px;
        }
        .empty-state__icon { font-size: 2.5rem; opacity: 0.5; }
        .empty-state__title {
          font-size: 1rem;
          font-weight: 600;
          color: var(--text-secondary);
        }
        .empty-state__desc {
          font-size: 0.85rem;
          max-width: 300px;
        }

        @keyframes spin { to { transform: rotate(360deg); } }

        @media (max-width: 640px) {
          .dashboard { padding: 16px; }
          .scanner-controls {
            flex-direction: column;
            align-items: flex-start;
            gap: 16px;
          }
          .scan-btn { width: 100%; justify-content: center; }
          .stock-grid { grid-template-columns: 1fr; }
        }
      `}</style>
    </>
  );
}
