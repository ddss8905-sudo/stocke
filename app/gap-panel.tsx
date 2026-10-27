"use client";

import { useCallback, useState } from "react";
import { CandlestickChart } from "lucide-react";
import { ChartDialog } from "./results-panel";
import type { GapEvent, Market, ScreeningResult } from "@/lib/types";

function number(value: number | undefined, digits: number) {
  return value == null ? "-" : value.toLocaleString("ko-KR", { maximumFractionDigits: digits });
}

export function GapPanel({ market, events }: { market: Market; events: GapEvent[] }) {
  const [selected, setSelected] = useState<ScreeningResult | null>(null);
  const close = useCallback(() => setSelected(null), []);
  const digits = market === "NASDAQ" ? 2 : 0;
  const open = (event: GapEvent) => setSelected({ ...event, strategy: "gap224", timeframe: "daily", as_of: event.session_date,
    close: event.detected_price ?? event.open_price, adv20: null, final_score: null, rs_rank: null, trend_score: null,
    momentum_score: null, breakout_score: null, accumulation_score: null, vcp_score: null,
    entry_trigger: false, stop_price: null, risk_to_stop: null, is_candidate: false });
  return <>
    <div className="tableWrap"><table>
      <thead><tr><th>종목</th><th>상태</th><th>업종</th><th>전일 종가</th><th>시초가</th><th>시초가 기준 224일선</th><th>갭률</th><th>돌파 폭</th><th>조회 가격</th><th>발견 시각</th></tr></thead>
      <tbody>{events.map(event => <tr key={event.ticker}>
        <td><button className="chartLink" type="button" title={`${event.ticker} 검출 당시 차트`} onClick={() => open(event)}>{event.security_name} <span className="mono">{event.ticker}</span><CandlestickChart size={14} /></button></td>
        <td><span className="signalPill">{event.validation === "corrected" ? "시가 정정" : event.validation === "review" ? "데이터 재확인" : event.state === "below" ? "기준선 이탈" : "기준선 유지"}</span>{event.late_detection && <span className="gapLate">지연 확인</span>}</td>
        <td>{event.sector_name || "-"}</td><td>{number(event.previous_close, digits)}</td><td className="strong">{number(event.open_price, digits)}</td>
        <td>{number(event.ma224_open, 2)}</td><td>{number(event.gap_pct * 100, 2)}%</td><td>{number(event.breakout_pct * 100, 2)}%</td>
        <td>{number(event.current_price, digits)}</td><td>{new Intl.DateTimeFormat("ko-KR", { timeZone: market === "NASDAQ" ? "America/New_York" : "Asia/Seoul", hour: "2-digit", minute: "2-digit", hour12: false }).format(new Date(event.detected_at))}{market === "NASDAQ" ? " ET" : " KST"}</td>
      </tr>)}{events.length === 0 && <tr><td className="empty" colSpan={10}>표시할 갭 돌파 기록이 없습니다.</td></tr>}</tbody>
    </table></div>
    {selected && <ChartDialog row={selected} market={market} runId={null} timeframe="daily" onClose={close} />}
  </>;
}
