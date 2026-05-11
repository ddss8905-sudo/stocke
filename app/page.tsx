import { Activity, ArrowDownRight, ArrowUpRight, Clock, Database, Filter } from "lucide-react";
import { getDashboardData } from "@/lib/data";
import type { Market, ScreeningResult } from "@/lib/types";

type PageProps = {
  searchParams?: Promise<{ market?: string }>;
};

const markets: Market[] = ["NASDAQ", "KOSDAQ"];

function asMarket(value: string | undefined): Market {
  return value === "KOSDAQ" ? "KOSDAQ" : "NASDAQ";
}

function number(value: number | null | undefined, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return Number(value).toLocaleString("ko-KR", { maximumFractionDigits: digits });
}

function percent(value: number | null | undefined) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return `${(Number(value) * 100).toFixed(1)}%`;
}

function MarketTabs({ active }: { active: Market }) {
  return (
    <div className="tabs" aria-label="시장 선택">
      {markets.map((market) => (
        <a className={market === active ? "tab active" : "tab"} href={`/?market=${market}`} key={market}>
          {market}
        </a>
      ))}
    </div>
  );
}

function Stat({ label, value, icon }: { label: string; value: string; icon: React.ReactNode }) {
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

function ResultTable({ rows, compact = false }: { rows: ScreeningResult[]; compact?: boolean }) {
  return (
    <div className="tableWrap">
      <table>
        <thead>
          <tr>
            <th>종목</th>
            <th>종목명</th>
            <th>종가</th>
            <th>최종</th>
            <th>RS</th>
            <th>추세</th>
            <th>돌파</th>
            <th>거래대금</th>
            {!compact && <th>손절가</th>}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.ticker}-${row.final_score}`}>
              <td className="mono">
                <span className={row.entry_trigger ? "triggerDot on" : "triggerDot"} />
                {row.ticker}
              </td>
              <td className="nameCell">{row.security_name ?? "-"}</td>
              <td>{number(row.close, 0)}</td>
              <td className="strong">{number(row.final_score, 1)}</td>
              <td>{number(row.rs_rank, 1)}</td>
              <td>{number(row.trend_score, 1)}</td>
              <td>{number(row.breakout_score, 1)}</td>
              <td>{number(row.adv20, 0)}</td>
              {!compact && <td>{number(row.stop_price, 0)}</td>}
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td className="empty" colSpan={compact ? 8 : 9}>표시할 결과가 없습니다.</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

export default async function Page({ searchParams }: PageProps) {
  const params = await searchParams;
  const market = asMarket(params?.market);
  const data = await getDashboardData(market);
  const nextRun = market === "NASDAQ" ? "매일 21:00 KST" : "매일 15:30 KST";

  return (
    <main>
      <section className="topbar">
        <div>
          <p className="eyebrow">Trend Following Screener</p>
          <h1>NASDAQ / KOSDAQ 자동 스크리닝</h1>
        </div>
        <MarketTabs active={market} />
      </section>

      {data.usingSampleData && (
        <div className="notice">
          Supabase 환경변수가 아직 없어서 샘플 데이터를 표시 중입니다. DB 연결 후 최신 스크리닝 결과가 자동으로 표시됩니다.
        </div>
      )}

      <section className="stats">
        <Stat label="선택 시장" value={market} icon={<Database size={18} />} />
        <Stat label="최근 실행일" value={data.run?.run_date ?? "-"} icon={<Clock size={18} />} />
        <Stat label="다음 자동 실행" value={nextRun} icon={<Activity size={18} />} />
        <Stat label="후보 수" value={String(data.run?.candidate_count ?? data.candidates.length)} icon={<Filter size={18} />} />
        <Stat
          label="시장 상태"
          value={data.run ? (data.run.market_bullish ? "상승 우위" : "방어 필요") : "-"}
          icon={data.run?.market_bullish ? <ArrowUpRight size={18} /> : <ArrowDownRight size={18} />}
        />
      </section>

      <section className="section">
        <div className="sectionHead">
          <div>
            <h2>오늘의 후보</h2>
            <p>최종 점수, 상대강도, 유동성, 고점 근접도, 손절폭 조건을 통과한 종목입니다.</p>
          </div>
        </div>
        <ResultTable rows={data.candidates} />
      </section>

      <section className="section">
        <div className="sectionHead">
          <div>
            <h2>전체 스코어</h2>
            <p>거래대금 상위 200개 대상의 종합 점수표입니다.</p>
          </div>
        </div>
        <ResultTable rows={data.scored} compact />
      </section>
    </main>
  );
}
