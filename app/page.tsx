import type { ReactNode } from "react";
import { Activity, ArrowDownRight, ArrowUpRight, Clock, Database, Filter } from "lucide-react";
import { getDashboardData } from "@/lib/data";
import type { Market, Strategy, Timeframe } from "@/lib/types";
import { RunButtons } from "./run-buttons";
import { ResultsPanel } from "./results-panel";

type PageProps = {
  searchParams?: Promise<{ market?: string; strategy?: string; timeframe?: string }>;
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

function MarketTabs({ active, strategy, timeframe }: { active: Market; strategy: Strategy; timeframe: Timeframe }) {
  return (
    <div className="tabs" aria-label="Market selector">
      {markets.map((market) => (
        <a className={market === active ? "tab active" : "tab"} href={`/?market=${market}&strategy=${strategy}&timeframe=${timeframe}`} key={market}>
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
  const strategy: Strategy = params?.strategy === "reversal" ? "reversal" : "trend";
  const timeframe: Timeframe = params?.timeframe === "weekly" ? "weekly" : "daily";
  const data = await getDashboardData(market);
  const weekly = timeframe === "weekly";
  const candidates = weekly ? data.weekly?.candidates ?? [] : data.candidates;
  const scored = weekly ? data.weekly?.scored ?? [] : data.scored;
  const reversals = weekly ? data.weekly?.reversals ?? [] : data.reversals;
  const analysis = weekly ? data.weekly?.analysis ?? null : data.reversalAnalysis;
  const sectors = weekly ? data.weekly?.sectors ?? [] : strategy === "reversal" ? data.reversalSectors : data.sectors;
  const regimeScore = weekly || strategy === "reversal" ? analysis?.regime_score : data.run?.market_regime_score;
  const latestRun = dateTime(data.run?.finished_at) || data.run?.run_date || "-";
  const regimeValue = data.run
    ? `${regimeLabel(regimeScore)} ${number(regimeScore, 0)}`
    : "-";
  const regimeTradable = Number(weekly || strategy === "reversal" ? analysis?.exposure : data.run?.market_exposure ?? 0) > 0;

  return (
    <main>
      <section className="topbar">
        <div>
          <p className="eyebrow">Trend & Reversal Screener</p>
          <h1>Market Screener</h1>
        </div>
        <MarketTabs active={market} strategy={strategy} timeframe={timeframe} />
      </section>

      {data.usingSampleData && !weekly && (
        <div className="notice">
          Supabase data is unavailable, so sample rows are shown. Resume the Supabase project and verify Vercel environment variables to display live screening results.
        </div>
      )}

      <nav className="strategyTabs" aria-label="스크리닝 전략">
        <a aria-current={strategy === "trend" ? "page" : undefined} className={strategy === "trend" ? "tab active" : "tab"} href={`/?market=${market}&strategy=trend&timeframe=${timeframe}`}>추세추종</a>
        <a aria-current={strategy === "reversal" ? "page" : undefined} className={strategy === "reversal" ? "tab active" : "tab"} href={`/?market=${market}&strategy=reversal&timeframe=${timeframe}`}>저점 반등</a>
        <div className="timeframeTabs" aria-label="봉 기준">
          {(["daily", "weekly"] as const).map((value) => <a key={value} aria-current={timeframe === value ? "page" : undefined} className={timeframe === value ? "tab active" : "tab"} href={`/?market=${market}&strategy=${strategy}&timeframe=${value}`}>{value === "weekly" ? "주봉" : "일봉"}</a>)}
        </div>
      </nav>
      {weekly && <div className="notice">{analysis ? `주봉 기준 ${analysis.as_of} · 가격 기준 ${data.weekly?.analysis.price_date} · 마감된 주만 반영` : "이 실행에는 주봉 분석 결과가 없습니다. 새 실행 완료 후 표시됩니다."}</div>}
      <section className="stats">
        <Stat label="Market" value={market} icon={<Database size={18} />} />
        <Stat label="Latest run" value={latestRun} icon={<Clock size={18} />} />
        <Stat label="Run mode" value={market === "KOSPI_API" ? "KIS API" : "On demand"} icon={<Activity size={18} />} />
        <Stat label="Candidates" value={weekly && !analysis ? "-" : strategy === "reversal" ? analysis ? String(reversals.length) : "-" : String(weekly ? candidates.length : data.run?.candidate_count ?? candidates.length)} icon={<Filter size={18} />} />
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
        <RunButtons market={market} strategy={strategy} timeframe={timeframe} />
      </section>

      <ResultsPanel key={`${market}-${strategy}-${timeframe}-${data.run?.id}`} market={market} strategy={strategy} timeframe={timeframe} weeklyAnalysis={data.weekly?.analysis ?? null} runId={data.run?.id ?? null} candidates={candidates} scored={scored} sectors={sectors} reversals={reversals} reversalAnalysis={analysis} />
    </main>
  );
}
