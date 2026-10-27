import type { GapSnapshot, Market } from "./types";

export function validGapDate(value: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
  const date = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(date.getTime()) && date.toISOString().slice(0, 10) === value;
}

export function marketDate(market: Market) {
  return new Intl.DateTimeFormat("en-CA", { timeZone: market === "NASDAQ" ? "America/New_York" : "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
}

export async function gapObject<T>(path: string): Promise<T | null> {
  const base = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!base || !key) throw new Error("Gap storage not configured");
  const response = await fetch(`${new URL(base).origin}/storage/v1/object/authenticated/stocke-sector-strength/gap224/${path}`, {
    headers: { apikey: key, Authorization: `Bearer ${key}`, "Cache-Control": "no-cache" }, cache: "no-store",
  });
  if (response.status === 404) return null;
  if (!response.ok) {
    const error = await response.json().catch(() => ({})) as { statusCode?: string | number; error?: string };
    if (String(error.statusCode) === "404" || error.error === "not_found") return null;
    throw new Error(`Gap storage HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function getGapDashboard(market: Market, requested?: string) {
  const today = marketDate(market);
  const date = requested && validGapDate(requested) && requested <= today ? requested : today;
  try {
    const snapshot = await gapObject<GapSnapshot>(`${market}/${date}/summary.json`);
    const latest = snapshot ?? (requested ? null : await gapObject<GapSnapshot>(`${market}/latest.json`));
    const valid = latest?.version === 1 && latest.market === market && Array.isArray(latest.events);
    return { date, today, snapshot: valid ? latest : null, unavailable: false, stale: valid && latest.session_date !== date };
  } catch {
    return { date, today, snapshot: null, unavailable: true, stale: false };
  }
}
