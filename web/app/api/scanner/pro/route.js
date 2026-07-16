/**
 * API Route: /api/scanner/pro
 * GET  → Returns latest pro_results.json
 * POST → Runs pro_scan.py, returns fresh results
 */
import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { exec } from "child_process";
import path from "path";

const SCANNER_DIR = path.join(process.cwd(), "..", "scanner");
const RESULTS_FILE = path.join(SCANNER_DIR, "pro_results.json");

async function getResults() {
  try {
    const raw = await readFile(RESULTS_FILE, "utf-8");
    return JSON.parse(raw);
  } catch {
    return null;
  }
}

export async function GET() {
  const data = await getResults();
  if (!data) {
    return NextResponse.json({
      success: false,
      error: "No pro scan results yet. Click 'Run Scanner' to start.",
    });
  }
  return NextResponse.json({ success: true, ...data });
}

export async function POST(request) {
  let advanced = false;
  let priceMin = null;
  let priceMax = null;
  try {
    const body = await request.json();
    advanced = !!body.advanced;
    if (body.priceMin && !isNaN(body.priceMin)) priceMin = Number(body.priceMin);
    if (body.priceMax && !isNaN(body.priceMax)) priceMax = Number(body.priceMax);
  } catch {
    // No body or invalid JSON — defaults
  }

  return new Promise((resolve) => {
    let flags = advanced ? " --advanced" : "";
    if (priceMin) flags += ` --price-min ${priceMin}`;
    if (priceMax) flags += ` --price-max ${priceMax}`;
    const cmd = `cd "${SCANNER_DIR}" && source venv/bin/activate && python3 pro_scan.py${flags}`;

    exec(cmd, { shell: "/bin/zsh", timeout: 600000 }, async (error) => {
      if (error) {
        resolve(
          NextResponse.json(
            { success: false, error: `Pro scanner failed: ${error.message}` },
            { status: 500 }
          )
        );
        return;
      }
      const data = await getResults();
      if (!data) {
        resolve(
          NextResponse.json(
            { success: false, error: "Pro scanner finished but no results file" },
            { status: 500 }
          )
        );
        return;
      }
      resolve(NextResponse.json({ success: true, ...data }));
    });
  });
}
