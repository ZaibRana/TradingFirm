/**
 * Sector classification for stocks.
 * Uses Yahoo Finance's real sector/industry data, then maps to clean tab names.
 * Falls back to keyword matching for stocks without Yahoo data.
 */

/**
 * Map Yahoo Finance sector → our clean tab ID and label.
 * If Yahoo gives a sector we don't have a special mapping for,
 * we use it directly as a new tab.
 */
const YAHOO_SECTOR_MAP = {
  "Technology": { id: "tech", label: "Tech" },
  "Healthcare": { id: "health", label: "Health" },
  "Financial Services": { id: "finance", label: "Finance" },
  "Communication Services": { id: "telecom", label: "Telecom" },
  "Consumer Cyclical": { id: "consumer", label: "Consumer" },
  "Consumer Defensive": { id: "consumer", label: "Consumer" },
  "Energy": { id: "energy", label: "Energy" },
  "Industrials": { id: "industrial", label: "Industrial" },
  "Basic Materials": { id: "materials", label: "Materials" },
  "Real Estate": { id: "property", label: "Property" },
  "Utilities": { id: "utilities", label: "Utilities" },
};

/**
 * Sub-classify Technology stocks using their industry name.
 * E.g., "Semiconductors" → Chips, "Software—Infrastructure" → AI
 */
const TECH_INDUSTRY_MAP = [
  { keywords: ["semiconductor", "chip", "silicon", "foundry", "memory", "wafer", "integrated circuit"], id: "chips", label: "Chips" },
  { keywords: ["quantum"], id: "quantum", label: "Quantum" },
  { keywords: ["artificial intelligence", "machine learning", "cloud", "software", "saas", "cyber", "data", "information technology"], id: "ai", label: "AI" },
  { keywords: ["robot", "drone", "aerospace", "space", "defense", "autonomous"], id: "robo", label: "Robotics" },
  { keywords: ["solar", "wind", "battery", "clean energy", "electric vehicle"], id: "energy", label: "Energy" },
  { keywords: ["telecom", "wireless", "communication equipment", "networking"], id: "telecom", label: "Telecom" },
];

// Icons for each sector tab
const SECTOR_ICONS = {
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

/**
 * Classify a stock using Yahoo's sector + industry data.
 * @param {string} yahooSector - From quoteSummary assetProfile.sector
 * @param {string} yahooIndustry - From quoteSummary assetProfile.industry
 * @returns {{ sectorId: string, sector: string }}
 */
export function classifyFromYahoo(yahooSector, yahooIndustry) {
  if (!yahooSector) {
    return { sectorId: "other", sector: "Other" };
  }

  // Check if it's a Technology stock → sub-classify by industry
  if (yahooSector === "Technology" && yahooIndustry) {
    const industryLower = yahooIndustry.toLowerCase();
    for (const rule of TECH_INDUSTRY_MAP) {
      for (const keyword of rule.keywords) {
        if (industryLower.includes(keyword)) {
          return { sectorId: rule.id, sector: rule.label };
        }
      }
    }
    // Generic tech if no sub-category matches
    return { sectorId: "tech", sector: "Tech" };
  }

  // Map Yahoo sector to our tab
  const mapped = YAHOO_SECTOR_MAP[yahooSector];
  if (mapped) {
    return { sectorId: mapped.id, sector: mapped.label };
  }

  // Unknown Yahoo sector — use it directly as a new tab
  const id = yahooSector.toLowerCase().replace(/\s+/g, "-");
  return { sectorId: id, sector: yahooSector };
}

/**
 * Get icon for a sector ID.
 */
export function getSectorIcon(sectorId) {
  return SECTOR_ICONS[sectorId] || "📦";
}
