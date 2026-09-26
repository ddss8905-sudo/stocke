import type { ReactNode } from "react";
import { Activity, ArrowDownRight, ArrowUpRight, Clock, Database, Filter } from "lucide-react";
import { getDashboardData } from "@/lib/data";
import type { Market } from "@/lib/types";
import { RunButtons } from "./run-buttons";
import { ResultsPanel } from "./results-panel";

type PageProps = {
  searchParams?: Promise<{ market?: string }>;
};

const markets: Market[] = ["NASDAQ", "KOSDAQ", "KOSPI_API"];

function asMarket(value: string | undefined): Market {
  if (value === "KOSPI_API") return "KOSPI_API";
  return value === "KOSDAQ" ? "KOSDAQ" : "NASDAQ";
}

function number(value: number | null | undefined, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toLocaleString("ko-KR", { maximumFractionDigits: digits });
}

function regimeLabel(score: number | null | undefined) {
  if (score === null || score === undefined || Number.isNaN(Number(score))) return "-";
  if (score >= 80) return "Bullish · 80% max";
  if (score >= 40) return "Reduced · 40% max";
  return "Risk off · 0%";
}

function dateTime(value: string | null | undefined) {
  if (!value) return "-";
  return new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(new Date(value));
}

function MarketTabs({ active }: { active: Market }) {
  return (
    <div className="tabs" aria-label="Market selector">
      {markets.map((market) => (
        <a className={market === active ? "tab active" : "tab"} href={`/?market=${market}`} key={market}>
          {market}
        </a>
      ))}
    </div>
  );
}

function Stat({ label, value, icon }: { label: string; value: string; icon: ReactNode }) {
  return (
    <div className="stat">
      <div className="statIcon">{icon}</div>
      <div>
        <div className="statLabel">{label}</div>
        <div className="statValue">{value}</div>
      </div>
    </div>
  );
}

export default async function Page({ searchParams }: PageProps) {
  const params = await searchParams;
  const market = asMarket(params?.market);
  const data = await getDashboardData(market);
  const latestRun = dateTime(data.run?.finished_at) || data.run?.run_date || "-";
  const regimeValue = data.run
    ? `${regimeLabel(data.run.market_regime_score)} ${number(data.run.market_regime_score, 0)}`
    : "-";
  const regimeTradable = Number(data.run?.market_exposure ?? 0) > 0;

  return (
    <main>
      <section className="topbar">
        <div>
          <p className="eyebrow">Trend Following Screener</p>
          <h1>Market Screener</h1>
        </div>
        <MarketTabs active={market} />
      </section>

      {data.usingSampleData && (
        <div className="notice">
          Supabase data is unavailable, so sample rows are shown. Resume the Supabase project and verify Vercel environment variables to display live screening results.
        </div>
      )}

      <section className="stats">
        <Stat label="Market" value={market} icon={<Database size={18} />} />
        <Stat label="Latest run" value={latestRun} icon={<Clock size={18} />} />
        <Stat label="Run mode" value={market === "KOSPI_API" ? "KIS API" : "On demand"} icon={<Activity size={18} />} />
        <Stat label="Candidates" value={String(data.run?.candidate_count ?? data.candidates.length)} icon={<Filter size={18} />} />
        <Stat
          label="Regime"
          value={regimeValue}
          icon={regimeTradable ? <ArrowUpRight size={18} /> : <ArrowDownRight size={18} />}
        />
      </section>

      <section className="section">
        <div className="sectionHead">
          <div>
            <h2>Run on demand</h2>
            <p>Trigger the selected market workflow in GitHub Actions, then refresh after it completes.</p>
          </div>
        </div>
        <RunButtons market={market} />
      </section>

      <ResultsPanel market={market} candidates={data.candidates} scored={data.scored} sectors={data.sectors} />
    </main>
  );
}

