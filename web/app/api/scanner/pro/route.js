/**
 * API Route: /api/scanner/pro
 * Proxies to FastAPI backend at localhost:8001
 *
 * GET  → Returns latest scan results from backend
 * POST → Triggers a new scan, polls until complete, returns results
 */
import { NextResponse } from "next/server";

const BACKEND = process.env.DATA_ENGINE_URL || "http://localhost:8001";

/** Convert durationSeconds (float) to human-readable string like "6m 17s" */
function enrichResponse(data) {
  if (data.durationSeconds) {
    const mins = Math.floor(data.durationSeconds / 60);
    const secs = Math.round(data.durationSeconds % 60);
    data.duration = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
  }
  return data;
}

export async function GET() {
  try {
    const res = await fetch(`${BACKEND}/scan/results`, {
      cache: "no-store",
    });
    const data = await res.json();

    if (!data.stocks || data.stocks.length === 0) {
      return NextResponse.json({
        success: false,
        error: "No scan results yet. Click 'Run Scanner' to start.",
      });
    }

    return NextResponse.json({
      success: true,
      ...enrichResponse(data),
    });
  } catch (err) {
    return NextResponse.json({
      success: false,
      error: `Backend unavailable: ${err.message}. Is the data engine running on port 8001?`,
    });
  }
}

export async function POST(request) {
  // Parse request body for price filters
  let priceMin = 10.0;
  let priceMax = 40.0;
  let advanced = false;
  try {
    const body = await request.json();
    if (body.priceMin && !isNaN(body.priceMin)) priceMin = Number(body.priceMin);
    if (body.priceMax && !isNaN(body.priceMax)) priceMax = Number(body.priceMax);
    advanced = !!body.advanced;
  } catch {
    // No body or invalid JSON — use defaults
  }

  try {
    // 1. Trigger the scan
    const triggerRes = await fetch(`${BACKEND}/scan/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        price_min: priceMin,
        price_max: priceMax,
        advanced: advanced,
      }),
    });
    const triggerData = await triggerRes.json();

    // Handle cooldown or already running
    if (triggerData.status === "cooldown") {
      return NextResponse.json({
        success: false,
        error: triggerData.message,
      });
    }

    if (triggerData.status === "already_running") {
      // Fall through to polling
    }

    // 2. Poll /scan/status until complete (max 15 minutes)
    const maxWait = 15 * 60 * 1000; // 15 minutes
    const pollInterval = 3000; // 3 seconds
    const startTime = Date.now();

    while (Date.now() - startTime < maxWait) {
      await new Promise((r) => setTimeout(r, pollInterval));

      const statusRes = await fetch(`${BACKEND}/scan/status`, {
        cache: "no-store",
      });
      const statusData = await statusRes.json();

      if (statusData.status === "completed") {
        break;
      }
      if (statusData.status === "failed") {
        return NextResponse.json({
          success: false,
          error: `Scan failed: ${statusData.message}`,
        });
      }
      // Still running — continue polling
    }

    // 3. Fetch results
    const resultsRes = await fetch(`${BACKEND}/scan/results`, {
      cache: "no-store",
    });
    const resultsData = await resultsRes.json();

    if (!resultsData.stocks || resultsData.stocks.length === 0) {
      return NextResponse.json({
        success: false,
        error: "Scan completed but returned no stocks.",
      });
    }

    return NextResponse.json({
      success: true,
      ...enrichResponse(resultsData),
    });
  } catch (err) {
    return NextResponse.json(
      {
        success: false,
        error: `Backend error: ${err.message}. Is the data engine running on port 8001?`,
      },
      { status: 500 }
    );
  }
}
