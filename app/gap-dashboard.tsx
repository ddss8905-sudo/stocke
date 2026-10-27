import { Clock, Database, Filter, Search, Send } from "lucide-react";
import { getGapDashboard } from "@/lib/gap224";
import type { GapSnapshot, Market } from "@/lib/types";
import { RunButtons } from "./run-buttons";
import { GapPanel } from "./gap-panel";

const statusLabels: Record<GapSnapshot["status"], string> = { complete: "검사 완료", partial: "일부 완료", running: "검사 중", preparing: "전일 데이터 준비 중", closed: "휴장일", waiting: "개장 대기", failed: "검사 실패" };
const errorLabels: Record<string, string> = { opening_not_ready: "시가 미확인", price_basis_changed: "가격 기준 재확인", provider_error: "공급자 조회 오류", stale_quote: "거래일 불일치" };

export async function GapDashboard({ market, date }: { market: Market; date?: string }) {
  const data = await getGapDashboard(market, date);
  const snapshot = data.snapshot;
  const status = data.unavailable ? "저장소 연결 실패" : data.stale || !snapshot ? "검사 미실행" : statusLabels[snapshot.status];
  const notification = !snapshot ? "미확인" : !snapshot.telegram_configured ? "연결 설정 필요" : snapshot.notifications?.unknown || snapshot.notifications?.sending ? "전송 확인 필요" : snapshot.notifications?.failed || snapshot.notifications?.storage_error ? "전송 실패" : snapshot.notifications?.retry ? "재시도 대기" : "설정됨";
  const stats = [
    { label: "거래일", value: data.date, icon: <Clock size={18} /> },
    { label: "검사 상태", value: status, icon: <Database size={18} /> },
    { label: "시가 확인", value: !snapshot || data.stale || snapshot.checked_count == null || snapshot.target_count == null ? "-" : `${snapshot.checked_count} / ${snapshot.target_count}`, icon: <Search size={18} /> },
    { label: "발견", value: !snapshot || data.stale || !["complete", "partial"].includes(snapshot.status) ? "-" : String(snapshot.valid_count ?? 0), icon: <Filter size={18} /> },
    { label: "텔레그램", value: notification, icon: <Send size={18} /> },
  ];
  return <>
    <section className="stats">{stats.map(stat => <div className="stat" key={stat.label}><div className="statIcon">{stat.icon}</div><div><div className="statLabel">{stat.label}</div><div className="statValue">{stat.value}</div></div></div>)}</section>
    {data.unavailable && <div className="notice">Supabase 저장소에 연결할 수 없습니다. 결과 0건이 아닌 조회 실패 상태입니다.</div>}
    {data.stale && <div className="notice">{data.date} 검사 미실행 · 아래는 마지막 저장 결과 {snapshot?.session_date}입니다.</div>}
    {snapshot?.status === "partial" && <div className="notice">시가 대기·조회 오류 {snapshot.pending_count ?? 0}종목 · 전일 이력 수집 누락 {snapshot.baseline_missing_count ?? 0}종목</div>}
    {snapshot?.status === "partial" && Object.keys(snapshot.error_reasons ?? {}).length > 0 && <p className="reversalCaution">{Object.entries(snapshot.error_reasons ?? {}).map(([reason, count]) => `${errorLabels[reason] ?? "조회 보류"} ${count}종목`).join(" · ")}</p>}
    <section className="section"><div className="sectionHead gapHead"><div><h2>224일선 갭 돌파</h2><p>일봉 · 정규장 시가 · 기존 유동성 선별 범위, 전체시장 검색 아님</p></div>
      <form className="gapDateForm" method="get"><input type="hidden" name="market" value={market} /><input type="hidden" name="strategy" value="gap224" /><input type="date" name="date" defaultValue={data.date} max={data.today} aria-label="갭 돌파 거래일" /><button type="submit" className="iconButton" title="거래일 조회" aria-label="거래일 조회"><Search size={18} /></button></form>
    </div><RunButtons market={market} strategy="gap224" timeframe="daily" date={data.date} /></section>
    <section className="section"><div className="sectionHead"><div><h2>발견 기록 · {snapshot?.session_date ?? data.date}</h2>{snapshot && <p>전일 기준 {snapshot.reference_date ?? "-"} · 선별 {snapshot.selected_count ?? "-"}종목 · 이력·품질 제외 {snapshot.excluded_count ?? "-"} · {snapshot.reconciled ? "마감 대조 완료" : "장초 검사"}</p>}</div></div>
      {!data.unavailable && snapshot ? <GapPanel key={`${market}-${snapshot.session_date}-${snapshot.updated_at}`} market={market} events={snapshot.events} /> : <p className="sectorEmpty">{data.unavailable ? "저장 결과 조회 실패" : "아직 저장된 검사 결과가 없습니다."}</p>}
      <p className="reversalCaution">시초가 돌파 발견 기록이며 매수 확정 신호가 아닙니다. 조회 가격과 발견 시가는 다를 수 있습니다.</p>
    </section>
  </>;
}

