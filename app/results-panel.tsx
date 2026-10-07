"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CandlestickChart, ExternalLink, X } from "lucide-react";
import { CandlestickSeries, ColorType, createChart, HistogramSeries, LineSeries } from "lightweight-charts";
import type { CandlestickData, HistogramData, Time } from "lightweight-charts";
import type { Market, ReversalAnalysis, ReversalStatus, ScreeningResult, SectorStrength, Strategy, Timeframe } from "@/lib/types";

type Period = 5 | 10 | 21;
const periods: { days: Period; label: string }[] = [
  { days: 5, label: "1주" },
  { days: 10, label: "2주" },
  { days: 21, label: "한 달" },
];

function number(value: number | null | undefined, digits = 1) {
  return value == null || !Number.isFinite(Number(value))
    ? "-"
    : Number(value).toLocaleString("ko-KR", { maximumFractionDigits: digits });
}

function percent(value: number | null | undefined, digits = 1) {
  return value == null || !Number.isFinite(Number(value)) ? "-" : `${number(value * 100, digits)}%`;
}

function signalLabel(row: ScreeningResult) {
  if (row.entry_signal === "wait_risk") return "위험 초과";
  if (row.entry_signal === "buy_breakout") return "Buy breakout";
  if (row.entry_signal === "wait_extended") return "Wait";
  return row.entry_trigger ? "Buy" : "Watch";
}

function tradingViewSymbol(market: Market, ticker: string) {
  return `${market === "NASDAQ" ? "NASDAQ" : "KRX"}:${ticker.toUpperCase()}`;
}

type ChartCandle = { time: string; open: number; high: number; low: number; close: number; volume: number };
const reversalLabels: Record<ReversalStatus, string> = { preparing: "반등 준비", confirmed: "돌파 확인", tracking: "돌파 추적", volume_wait: "확인 대기", extended: "추격 주의", risk_high: "위험 초과", market_wait: "시장 대기" };

function DomesticChart({ market, ticker, runId, row, timeframe, onSource }: { market: Market; ticker: string; runId: string | null; row: ScreeningResult; timeframe: Timeframe; onSource: (source: string) => void }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [candles, setCandles] = useState<ChartCandle[] | null>(null);
  const [error, setError] = useState(false);
  const [isSnapshot, setIsSnapshot] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    setCandles(null);
    setError(false);
    setIsSnapshot(false);
    onSource("");
    const params = new URLSearchParams({ market, ticker, timeframe });
    if (runId) params.set("run", runId);
    if (row.as_of) params.set("asof", row.as_of);
    fetch(`/api/chart?${params}`, { signal: controller.signal })
      .then(async (response) => {
        if (!response.ok) throw new Error("Chart request failed");
        const data = await response.json() as { candles: ChartCandle[]; source: string; snapshot?: boolean };
        if (!data.candles.length) throw new Error("Empty chart");
        setCandles(data.candles);
        setIsSnapshot(data.snapshot === true);
        onSource(data.source);
      })
      .catch(() => { if (!controller.signal.aborted) setError(true); });
    return () => controller.abort();
  }, [market, ticker, runId, timeframe, row.as_of, onSource]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container || !candles) return;
    const chart = createChart(container, {
      width: container.clientWidth,
      height: container.clientHeight,
      layout: { textColor: "#596258", background: { type: ColorType.Solid, color: "#ffffff" }, attributionLogo: false },
      grid: { vertLines: { color: "#f2f5f0" }, horzLines: { color: "#edf0eb" } },
      timeScale: { borderColor: "#dfe4dc", timeVisible: false },
      rightPriceScale: { borderColor: "#dfe4dc" },
      crosshair: { mode: 1 },
    });
    const prices = chart.addSeries(CandlestickSeries, {
      upColor: "#d94848", downColor: "#2474b5", borderVisible: false,
      wickUpColor: "#d94848", wickDownColor: "#2474b5",
      priceFormat: { type: "price", precision: market === "NASDAQ" ? 2 : 0, minMove: market === "NASDAQ" ? 0.01 : 1 },
    });
    prices.setData(candles.map(({ time, open, high, low, close }) => ({ time, open, high, low, close })) as CandlestickData<Time>[]);
    const volumes = chart.addSeries(HistogramSeries, {
      priceScaleId: "volume", priceFormat: { type: "volume" },
    });
    volumes.priceScale().applyOptions({ scaleMargins: { top: 0.83, bottom: 0 } });
    volumes.setData(candles.map(({ time, volume, open, close }) => ({
      time, value: volume, color: close >= open ? "#e9abab" : "#a7c9e3",
    })) as HistogramData<Time>[]);
    if (timeframe === "weekly") {
      for (const [weeks, color] of [[10, "#218773"], [30, "#c69429"], [40, "#596879"]] as const) {
        const average = chart.addSeries(LineSeries, { color, lineWidth: 1, priceLineVisible: false, lastValueVisible: false, title: `MA${weeks}W` });
        let sum = 0;
        const points: { time: Time; value: number }[] = [];
        candles.forEach((candle, index) => {
          sum += candle.close;
          if (index >= weeks) sum -= candles[index - weeks].close;
          if (index >= weeks - 1) points.push({ time: candle.time as Time, value: sum / weeks });
        });
        average.setData(points);
      }
    }
    if (isSnapshot && row.strategy === "reversal" && row.trendline_anchors?.length === 2 && row.trendline_price != null) {
      const [first, second] = row.trendline_anchors;
      const start = candles.findIndex((candle) => candle.time.slice(0, 10) === first.time);
      const end = candles.findIndex((candle) => candle.time.slice(0, 10) === second.time);
      if (start >= 0 && end > start) {
        const slope = (second.price - first.price) / (end - start);
        const resistance = chart.addSeries(LineSeries, { color: "#9264a7", lineWidth: 2, priceLineVisible: false, lastValueVisible: false });
        resistance.setData(candles.slice(start).map((candle, index) => ({ time: candle.time as Time, value: first.price + slope * index })));
      }
      if (row.stop_price != null) prices.createPriceLine({ price: row.stop_price, color: "#cf5656", lineWidth: 1, lineStyle: 2, axisLabelVisible: true, title: "Stop" });
    }
    chart.timeScale().fitContent();
    const observer = new ResizeObserver(() => chart.resize(container.clientWidth, container.clientHeight));
    observer.observe(container);
    return () => { observer.disconnect(); chart.remove(); };
  }, [candles, isSnapshot, market, row, timeframe]);

  return <div className="chartWidget domesticChart">
    <div ref={containerRef} className="chartCanvas" />
    {!candles && <div className="chartStatus">{error ? "차트 데이터를 불러올 수 없습니다. 외부 차트를 이용해 주세요." : "차트를 불러오는 중..."}</div>}
  </div>;
}

function ChartDialog({ row, market, runId, timeframe, onClose }: { row: ScreeningResult; market: Market; runId: string | null; timeframe: Timeframe; onClose: () => void }) {
  const chartRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const [chartSource, setChartSource] = useState("");
  const priceDigits = market === "NASDAQ" ? 2 : 0;
  const symbol = tradingViewSymbol(market, row.ticker);
  const chartUrl = market === "NASDAQ"
    ? `https://www.tradingview.com/chart/?symbol=${encodeURIComponent(symbol)}&interval=${timeframe === "weekly" ? "W" : "D"}`
    : `https://finance.yahoo.com/quote/${row.ticker}.${market === "KOSDAQ" ? "KQ" : "KS"}/chart/`;

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    closeRef.current?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKeyDown);
      previousFocus?.focus();
    };
  }, [onClose]);

  useEffect(() => {
    const container = chartRef.current;
    if (!container || market !== "NASDAQ" || row.strategy === "reversal" || timeframe === "weekly") return;
    const script = document.createElement("script");
    script.src = "https://s3.tradingview.com/external-embedding/embed-widget-advanced-chart.js";
    script.async = true;
    script.textContent = JSON.stringify({
      autosize: true,
      symbol,
      interval: "D",
      timezone: "exchange",
      theme: "light",
      style: "1",
      locale: "ko",
      withdateranges: true,
      hide_volume: false,
      allow_symbol_change: false,
      support_host: "https://www.tradingview.com",
    });
    container.replaceChildren(script);
    return () => container.replaceChildren();
  }, [market, symbol, row.strategy, timeframe]);

  return (
    <div className="chartBackdrop" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div className="chartDialog" role="dialog" aria-modal="true" aria-labelledby="chart-title">
        <div className="chartHeader">
          <div>
            <h2 id="chart-title">{row.security_name || row.ticker} <span className="chartTicker">{row.ticker}</span></h2>
            <p>{market} · {row.sector_name || "업종 미분류"} · {timeframe === "weekly" ? "주봉" : "일봉"}{row.as_of ? ` · ${row.as_of}` : ""}</p>
          </div>
          <button ref={closeRef} className="iconButton" type="button" onClick={onClose} aria-label="차트 닫기" title="차트 닫기"><X size={20} /></button>
        </div>
        <div className="chartBody">
          {market === "NASDAQ" && row.strategy !== "reversal" && timeframe === "daily" ? <div className="chartWidget" ref={chartRef} /> : <DomesticChart market={market} ticker={row.ticker} runId={runId} row={row} timeframe={timeframe} onSource={setChartSource} />}
          <div className="chartDetails">
            <span>종가 <strong>{number(row.close, priceDigits)}</strong></span>
            <span>{row.strategy === "reversal" ? "돌파 구간" : "매수 구간"} <strong>{row.buy_zone_low == null || row.buy_zone_high == null ? "-" : `${number(row.buy_zone_low, priceDigits)}–${number(row.buy_zone_high, priceDigits)}`}</strong></span>
            <span>초기 손절 <strong>{number(row.initial_stop_price ?? row.stop_price, priceDigits)}</strong></span>
            <span>2R <strong>{number(row.two_r_price, priceDigits)}</strong></span>
            {timeframe === "weekly" && <span className="weeklyLegend"><i className="ma10Key" />MA10W <i className="ma30Key" />MA30W <i className="ma40Key" />MA40W</span>}
            {row.strategy === "reversal" && <><span>하락 추세선 <strong>{number(row.trendline_price, market === "NASDAQ" ? 2 : 0)}</strong></span><span>거래량 <strong>{number(row.volume_ratio, 2)}×</strong></span><span>상태 <strong>{row.reversal_status ? reversalLabels[row.reversal_status] : "-"}</strong></span><span>재료·주식 구조 <strong>미검증</strong></span></>}
            <a href={chartUrl} target="_blank" rel="noopener noreferrer"><ExternalLink size={15} /> 외부 차트</a>
          </div>
        </div>
        <div className="chartAttribution">{market === "NASDAQ" && row.strategy !== "reversal" && timeframe === "daily" ? "Chart by " : `Prices: ${chartSource || "Loading"} · Chart by `}<a href="https://www.tradingview.com/" target="_blank" rel="noopener noreferrer">TradingView</a></div>
      </div>
    </div>
  );
}

function ResultTable({ rows, compact, digits = 0, onOpen }: { rows: ScreeningResult[]; compact?: boolean; digits?: number; onOpen: (row: ScreeningResult) => void }) {
  return (
    <div className="tableWrap">
      <table>
        <thead><tr>
          <th>Ticker</th>
          {!compact && <th>Signal</th>}
          {!compact && <th>Setup</th>}
          <th>Name</th><th>Sector</th><th>Close</th><th>Final</th><th>RS</th><th>Trend</th>
          {compact ? <><th>Breakout</th><th>ADV20</th></> : <><th>Buy Zone</th><th>Risk</th><th>Stop</th><th>2R</th><th>Size</th></>}
        </tr></thead>
        <tbody>
          {rows.map((row) => (
            <tr key={`${row.ticker}-${row.final_score}`}>
              <td className="mono"><span className={row.entry_trigger ? "triggerDot on" : "triggerDot"} /><button className="chartLink" type="button" title={`${row.ticker} 차트 열기`} onClick={() => onOpen(row)}>{row.ticker} <CandlestickChart size={14} /></button></td>
              {!compact && <td title={row.entry_reason ?? undefined}><span className={row.entry_trigger ? "signalPill buy" : row.entry_signal === "wait_extended" ? "signalPill wait" : "signalPill"}>{signalLabel(row)}</span></td>}
              {!compact && <td>{row.entry_setup === "breakout" ? "Breakout" : row.entry_setup === "risk_watch" ? "Risk watch" : row.entry_setup === "extended_watch" ? "Extended" : "Watch"}</td>}
              <td className="nameCell"><button className="chartLink" type="button" onClick={() => onOpen(row)}>{row.security_name || "-"}</button></td>
              <td>{row.sector_name || "-"}</td>
              <td>{number(row.close, digits)}</td><td className="strong">{number(row.final_score)}</td><td>{number(row.rs_rank)}</td><td>{number(row.trend_score)}</td>
              {compact ? <><td>{number(row.breakout_score)}</td><td>{number(row.adv20, 0)}</td></> : <>
                <td>{row.buy_zone_low == null || row.buy_zone_high == null ? "-" : `${number(row.buy_zone_low, digits)}–${number(row.buy_zone_high, digits)}`}</td>
                <td>{percent(row.risk_to_stop)}</td><td title={row.exit_plan ?? undefined}>{number(row.initial_stop_price ?? row.stop_price, digits)}</td><td>{number(row.two_r_price, digits)}</td><td>{percent(row.position_size_pct)}</td>
              </>}
            </tr>
          ))}
          {rows.length === 0 && <tr><td className="empty" colSpan={compact ? 10 : 14}>표시할 종목이 없습니다.</td></tr>}
        </tbody>
      </table>
    </div>
  );
}

function ReversalTable({ rows, onOpen, digits }: { rows: ScreeningResult[]; onOpen: (row: ScreeningResult) => void; digits: number }) {
  return <div className="tableWrap"><table>
    <thead><tr><th>종목</th><th>상태</th><th>이름</th><th>업종</th><th>종가</th><th>반등 점수</th><th>하락 기간</th><th>낙폭</th><th>저점 대비</th><th>추세선</th><th>거래량</th><th>손절</th><th>위험</th><th>2R</th><th>비중</th></tr></thead>
    <tbody>{rows.map((row) => <tr key={row.ticker}>
      <td><button className="chartLink" type="button" title={`${row.ticker} 차트 열기`} onClick={() => onOpen(row)}>{row.ticker} <CandlestickChart size={14} /></button></td>
      <td><span className={row.entry_trigger ? "signalPill buy" : "signalPill"}>{row.reversal_status ? reversalLabels[row.reversal_status] : "-"}</span></td>
      <td className="nameCell"><button className="chartLink" type="button" onClick={() => onOpen(row)}>{row.security_name || row.ticker}</button></td>
      <td>{row.sector_name || "-"}</td><td>{number(row.close, digits)}</td><td className="strong">{number(row.final_score)}</td>
      <td>{row.downtrend_bars ?? row.downtrend_days ?? "-"}{row.timeframe === "weekly" ? "주" : "거래일"}</td><td>{percent(row.decline_pct)}</td><td>{percent(row.rebound_pct)}</td>
      <td>{number(row.trendline_price, digits)}</td><td>{number(row.volume_ratio, 2)}×</td><td title={row.stop_basis ?? undefined}>{number(row.stop_price, digits)}</td>
      <td>{percent(row.risk_to_stop)}</td><td>{number(row.two_r_price, digits)}</td><td>{percent(row.position_size_pct)}</td>
    </tr>)}{!rows.length && <tr><td className="empty" colSpan={15}>현재 조건에 맞는 저점 반등 후보가 없습니다.</td></tr>}</tbody>
  </table></div>;
}

export function ResultsPanel({ market, strategy, timeframe, weeklyAnalysis, runId, candidates, scored, sectors, reversals, reversalAnalysis }: { market: Market; strategy: Strategy; timeframe: Timeframe; weeklyAnalysis: ReversalAnalysis | null; runId: string | null; candidates: ScreeningResult[]; scored: ScreeningResult[]; sectors: SectorStrength[]; reversals: ScreeningResult[]; reversalAnalysis: ReversalAnalysis | null }) {
  const [period, setPeriod] = useState<Period>(5);
  const [selectedSector, setSelectedSector] = useState<string | null>(null);
  const [chartRow, setChartRow] = useState<ScreeningResult | null>(null);
  const closeChart = useCallback(() => setChartRow(null), []);
  const activeSectors = sectors.filter((sector) => sector.period_days === period).sort((a, b) => b.score - a.score);
  const sectorReturns = new Map(sectors.map((sector) => [`${sector.sector_name}:${sector.period_days}`, sector.sector_return]));
  const visibleCandidates = selectedSector ? candidates.filter((row) => row.sector_name === selectedSector) : candidates;
  const visibleScored = selectedSector ? scored.filter((row) => row.sector_name === selectedSector) : scored;
  const visibleReversals = selectedSector ? reversals.filter((row) => row.sector_name === selectedSector) : reversals;
  const highScore = market === "KOSPI_API" ? scored.filter((row) => (row.final_score ?? 0) >= 80) : [];
  const compactBase = highScore.filter((row) => row.base_depth_pct != null && row.base_depth_pct <= 0.45);

  return <>
    <section className="section">
      <div className="sectionHead sectorHead">
        <div><h2>주도섹터</h2><p>분석 대상 업종별 강도 · 시장 대비 수익률, 상승 종목 비율, 거래 활성도 기준</p></div>
        <div className="periodTabs" role="group" aria-label="섹터 분석 기간">
          {periods.map(({ days, label }) => <button key={days} type="button" className={period === days ? "periodTab active" : "periodTab"} aria-pressed={period === days} onClick={() => setPeriod(days)}>{label}</button>)}
        </div>
      </div>
      {activeSectors.length ? <div className="tableWrap"><table className="sectorTable">
        <thead><tr><th>업종</th><th>1주</th><th>2주</th><th>한 달</th><th>시장 대비</th><th>상승 비율</th><th>거래 활성</th><th>점수</th><th>종목</th></tr></thead>
        <tbody>{activeSectors.map((sector) => <tr key={sector.sector_name} className={selectedSector === sector.sector_name ? "selectedSectorRow" : ""}>
          <td><button className="sectorLink" type="button" aria-pressed={selectedSector === sector.sector_name} onClick={() => setSelectedSector(selectedSector === sector.sector_name ? null : sector.sector_name)}>{sector.sector_name}</button>{sector.is_leader && <span className="leaderTag">주도</span>}</td>
          {periods.map(({ days }) => <td key={days}>{percent(sectorReturns.get(`${sector.sector_name}:${days}`))}</td>)}
          <td className={sector.relative_return > 0 ? "positive" : ""}>{percent(sector.relative_return)}</td><td>{percent(sector.breadth, 0)}</td><td>{number(sector.turnover_ratio, 2)}×</td><td className="strong">{number(sector.score, 0)}</td><td title={`유효 ${sector.valid_count} / ${sector.member_count}`}>{sector.valid_count}/{sector.member_count}</td>
        </tr>)}</tbody>
      </table></div> : <p className="sectorEmpty">이 실행에서는 표시할 업종 데이터가 없습니다. 업종 분류나 가격 이력이 부족할 수 있습니다.</p>}
    </section>

    {strategy === "reversal" ? <section className="section">
      <div className="sectionHead"><div><h2>저점 반등 · {timeframe === "weekly" ? "주봉" : "일봉"}{selectedSector ? ` · ${selectedSector}` : ""}</h2><p>{reversalAnalysis ? `${reversalAnalysis.as_of} · ${reversalAnalysis.scanned_count}종목 분석 · 후보 ${reversals.length} · 돌파 확인 ${reversals.filter((row) => row.entry_trigger).length}` : "이 실행에는 저점 반등 분석 결과가 없습니다. 새 실행이 필요합니다."}</p></div>{selectedSector && <button className="clearFilter" type="button" onClick={() => setSelectedSector(null)}><X size={15} /> 필터 해제</button>}</div>
      {reversalAnalysis ? <ReversalTable rows={visibleReversals} onOpen={setChartRow} digits={market === "NASDAQ" ? 2 : 0} /> : <p className="sectorEmpty">분석 미실행 또는 결과를 불러오지 못한 상태입니다.</p>}
      <p className="reversalCaution">제프리 뉴먼의 가격 패턴을 참고한 기술적 후보입니다. 사업 재료·희석 가능성은 미검증이며, 돌파 확인도 수익을 보장하지 않습니다.</p>
    </section> : <>
    <section className="section">
      <div className="sectionHead"><div><h2>Candidate List · {timeframe === "weekly" ? "주봉" : "일봉"}{selectedSector ? ` · ${selectedSector}` : ""}</h2><p>{timeframe === "weekly" ? weeklyAnalysis ? `${weeklyAnalysis.as_of} · ${scored.length}종목 분석 · 후보 ${candidates.length}` : "주봉 분석 미실행 또는 결과를 불러오지 못한 상태입니다." : market === "KOSPI_API" ? `${scored.length}종목 분석 · 80점 이상 ${highScore.length} · 박스폭 45% 이하 ${compactBase.length} · 최종 후보 ${candidates.length}` : "후보 종목을 누르면 차트가 열립니다."}</p></div>{selectedSector && <button className="clearFilter" type="button" onClick={() => setSelectedSector(null)}><X size={15} /> 필터 해제</button>}</div>
      {(timeframe === "daily" || weeklyAnalysis) && <ResultTable rows={visibleCandidates} digits={timeframe === "weekly" && market === "NASDAQ" ? 2 : 0} onOpen={setChartRow} />}
    </section>

    {(timeframe === "daily" || weeklyAnalysis) && <section className="section">
      <div className="sectionHead"><div><h2>Full Scoreboard</h2><p>점수화된 {scored.length}종목</p></div></div>
      <ResultTable rows={visibleScored} compact digits={timeframe === "weekly" && market === "NASDAQ" ? 2 : 0} onOpen={setChartRow} />
    </section>}
    </>}
    {chartRow && <ChartDialog row={chartRow} market={market} runId={runId} timeframe={timeframe} onClose={closeChart} />}
  </>;
}
