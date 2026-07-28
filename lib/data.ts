import type { DashboardData, Market, ScreeningResult, ScreeningRun } from "./types";

const SUPABASE_URL = process.env.SUPABASE_URL;
const SUPABASE_KEY = process.env.SUPABASE_SERVICE_ROLE_KEY;

const sampleRows: Record<Market, ScreeningResult[]> = {
  NASDAQ: [
    { ticker: "NVDA", security_name: "NVIDIA Corporation", close: 132.4, adv20: 18500000000, final_score: 91.2, rs_rank: 96.8, trend_score: 94.0, momentum_score: 91.0, breakout_score: 88.0, accumulation_score: 76.0, vcp_score: 71.0, entry_trigger: true, entry_setup: "breakout", entry_signal: "buy_breakout", buy_zone_low: 128.1, buy_zone_high: 134.5, initial_stop_price: 121.81, stop_price: 121.81, risk_to_stop: 0.08, two_r_price: 153.58, position_size_pct: 0.063, is_candidate: true },
    { ticker: "MSFT", security_name: "Microsoft Corporation", close: 438.7, adv20: 9600000000, final_score: 84.5, rs_rank: 88.1, trend_score: 86.0, momentum_score: 80.0, breakout_score: 79.0, accumulation_score: 70.0, vcp_score: 68.0, entry_trigger: false, entry_setup: "watchlist", entry_signal: "watch_setup", buy_zone_low: 452.0, buy_zone_high: 474.6, initial_stop_price: 403.60, stop_price: 403.60, risk_to_stop: 0.08, two_r_price: 508.9, position_size_pct: 0.063, is_candidate: true },
  ],
  KOSDAQ: [
    { ticker: "028300", security_name: "HLB", close: 62500, adv20: 148000000000, final_score: 78.4, rs_rank: 91.2, trend_score: 82.0, momentum_score: 80.0, breakout_score: 76.0, accumulation_score: 72.0, vcp_score: 69.0, entry_trigger: true, entry_setup: "pullback", entry_signal: "buy_pullback", buy_zone_low: 61000, buy_zone_high: 64050, initial_stop_price: 56250, stop_price: 56250, risk_to_stop: 0.10, two_r_price: 75000, position_size_pct: 0.05, is_candidate: true },
    { ticker: "196170", security_name: "Alteogen", close: 214000, adv20: 212000000000, final_score: 76.8, rs_rank: 89.4, trend_score: 80.0, momentum_score: 83.0, breakout_score: 74.0, accumulation_score: 71.0, vcp_score: 67.0, entry_trigger: false, entry_setup: "watchlist", entry_signal: "watch_setup", buy_zone_low: 226000, buy_zone_high: 237300, initial_stop_price: 192600, stop_price: 192600, risk_to_stop: 0.10, two_r_price: 256800, position_size_pct: 0.05, is_candidate: true },
  ],
  KOSPI_API: [
    { ticker: "005930", security_name: "Samsung Electronics", close: 78000, adv20: 980000000000, final_score: 76.4, rs_rank: 82.2, trend_score: 79.0, momentum_score: 74.0, breakout_score: 72.0, accumulation_score: 70.0, vcp_score: 66.0, entry_trigger: true, entry_setup: "breakout", entry_signal: "buy_breakout", buy_zone_low: 76000, buy_zone_high: 79800, initial_stop_price: 70200, stop_price: 70200, risk_to_stop: 0.10, two_r_price: 93600, position_size_pct: 0.05, is_candidate: true },
    { ticker: "000660", security_name: "SK hynix", close: 186000, adv20: 720000000000, final_score: 81.8, rs_rank: 94.4, trend_score: 86.0, momentum_score: 89.0, breakout_score: 78.0, accumulation_score: 75.0, vcp_score: 70.0, entry_trigger: false, entry_setup: "extended_watch", entry_signal: "wait_extended", buy_zone_low: 168000, buy_zone_high: 176400, initial_stop_price: 167400, stop_price: 167400, risk_to_stop: 0.10, two_r_price: 223200, position_size_pct: 0.05, is_candidate: true },
  ],
};

function sampleDashboardData(market: Market): DashboardData {
  return {
    market,
    run: null,
    candidates: sampleRows[market],
    scored: sampleRows[market],
    usingSampleData: true,
  };
}

function errorDetails(error: unknown) {
  if (error instanceof Error) {
    const cause = error.cause instanceof Error
      ? { name: error.cause.name, message: error.cause.message }
      : error.cause;
    return { name: error.name, message: error.message, cause };
  }
  return { message: String(error) };
}

function supabaseBaseUrl() {
  if (!SUPABASE_URL) return null;
  try {
    return new URL(SUPABASE_URL).origin;
  } catch (error) {
    console.error("[supabase] invalid SUPABASE_URL", { error });
    return null;
  }
}

function headers() {
  return {
    apikey: SUPABASE_KEY ?? "",
    Authorization: `Bearer ${SUPABASE_KEY ?? ""}`,
  };
}

async function supabaseGet<T>(path: string): Promise<T> {
  const baseUrl = supabaseBaseUrl();
  if (!baseUrl || !SUPABASE_KEY) {
    throw new Error("Supabase environment is not configured");
  }

  const response = await fetch(`${baseUrl}/rest/v1/${path}`, {
    headers: headers(),
    next: { revalidate: 300 },
  });
  if (!response.ok) {
    throw new Error(`Supabase request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function getDashboardData(market: Market): Promise<DashboardData> {
  if (!SUPABASE_URL || !SUPABASE_KEY) {
    return sampleDashboardData(market);
  }

  try {
    const runs = await supabaseGet<ScreeningRun[]>(
      `screening_runs?market=eq.${market}&order=run_date.desc,created_at.desc&limit=1`
    );
    const run = runs[0] ?? null;
    if (!run) {
      return { market, run: null, candidates: [], scored: [], usingSampleData: false };
    }

    const base = `screening_results?run_id=eq.${run.id}`;
    const candidates = await supabaseGet<ScreeningResult[]>(
      `${base}&is_candidate=eq.true&order=entry_trigger.desc,final_score.desc&limit=50`
    );
    const scored = await supabaseGet<ScreeningResult[]>(
      `${base}&order=final_score.desc&limit=200`
    );

    return { market, run, candidates, scored, usingSampleData: false };
  } catch (error) {
    console.error("[supabase] dashboard data fetch failed", {
      market,
      region: process.env.VERCEL_REGION,
      supabaseHost: supabaseBaseUrl(),
      error: errorDetails(error),
    });
    return sampleDashboardData(market);
  }
}
