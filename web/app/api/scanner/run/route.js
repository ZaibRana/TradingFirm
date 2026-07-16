/**
 * API Route: /api/scanner/run
 *
 * GET  → Returns latest scan results from results.json
 * POST → Runs the Python scanner, then returns results
 */
import { NextResponse } from "next/server";
import { readFile } from "fs/promises";
import { exec } from "child_process";
import path from "path";

const SCANNER_DIR = path.join(process.cwd(), "..", "scanner");
const RESULTS_FILE = path.join(SCANNER_DIR, "results.json");

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
      error: "No scan results yet. Click 'Run Scanner' to start.",
    });
  }
  return NextResponse.json({ success: true, ...data });
}

export async function POST() {
  return new Promise((resolve) => {
    const pythonCmd = `cd "${SCANNER_DIR}" && source venv/bin/activate && python3 scan.py`;

    const child = exec(pythonCmd, { shell: "/bin/zsh", timeout: 300000 }, async (error) => {
      if (error) {
        resolve(
          NextResponse.json(
            { success: false, error: `Scanner failed: ${error.message}` },
            { status: 500 }
          )
        );
        return;
      }

      const data = await getResults();
      if (!data) {
        resolve(
          NextResponse.json(
            { success: false, error: "Scanner completed but no results file found" },
            { status: 500 }
          )
        );
        return;
      }

      resolve(NextResponse.json({ success: true, ...data }));
    });
  });
}
