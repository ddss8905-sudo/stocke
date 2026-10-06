import { NextRequest, NextResponse } from "next/server";

type YahooChart = {
  chart?: {
    result?: Array<{
      timestamp?: number[];
      indicators?: { quote?: Array<{ open?: Array<number | null>; high?: Array<number | null>; low?: Array<number | null>; close?: Array<number | null>; volume?: Array<number | null> }> };
    }>;
  };
};

type Candle = { time: string; open: number; high: number; low: number; close: number; volume: number };

async function kisCandles(market: string, runId: string, ticker: string): Promise<Candle[] | null> {
  const base = process.env.SUPABASE_URL;
  const key = process.env.SUPABASE_SERVICE_ROLE_KEY;
  if (!base || !key) return null;
  const url = `${base.replace(/\/$/, "")}/storage/v1/object/authenticated/stocke-sector-strength/${market}/${runId}-charts.json`;
  const response = await fetch(url, {
    headers: { apikey: key, Authorization: `Bearer ${key}` },
    cache: "no-store",
  });
  if (!response.ok) return null;
  const histories = await response.json() as Record<string, Candle[]>;
  return histories[ticker] ?? null;
}

export async function GET(request: NextRequest) {
  const market = request.nextUrl.searchParams.get("market");
  const ticker = request.nextUrl.searchParams.get("ticker");
  const runId = request.nextUrl.searchParams.get("run");
  if ((market !== "KOSDAQ" && market !== "KOSPI_API") || !ticker || !/^\d{6}$/.test(ticker)) {
    return NextResponse.json({ error: "잘못된 종목 코드입니다." }, { status: 400 });
  }
  if (runId && !/^[0-9a-f-]{36}$/i.test(runId)) {
    return NextResponse.json({ error: "잘못된 실행 ID입니다." }, { status: 400 });
  }

  if (market === "KOSPI_API" && runId) {
    try {
      const candles = await kisCandles(market, runId, ticker);
      if (candles?.length) return NextResponse.json({ candles, source: "KIS" });
    } catch (error) {
      console.error("KIS chart snapshot request failed", market, ticker, error);
    }
  }

  const suffix = market === "KOSDAQ" ? "KQ" : "KS";
  const url = `https://query1.finance.yahoo.com/v8/finance/chart/${ticker}.${suffix}?range=6mo&interval=1d`;
  try {
    const response = await fetch(url, {
      headers: { "User-Agent": "Mozilla/5.0 Stocke/1.0" },
      next: { revalidate: 300 },
    });
    if (!response.ok) throw new Error(`Yahoo chart HTTP ${response.status}`);
    const payload = await response.json() as YahooChart;
    const result = payload.chart?.result?.[0];
    const quote = result?.indicators?.quote?.[0];
    if (!result?.timestamp || !quote) throw new Error("No chart data");

    const dateFormatter = new Intl.DateTimeFormat("en-CA", {
      timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit",
    });
    const candles = result.timestamp.flatMap((timestamp, index) => {
      const open = quote.open?.[index];
      const high = quote.high?.[index];
      const low = quote.low?.[index];
      const close = quote.close?.[index];
      if (![open, high, low, close].every((value) => typeof value === "number" && Number.isFinite(value))) return [];
      return [{
        time: dateFormatter.format(new Date(timestamp * 1000)),
        open: open as number,
        high: high as number,
        low: low as number,
        close: close as number,
        volume: quote.volume?.[index] ?? 0,
      }];
    });
    if (!candles.length) throw new Error("No valid candles");
    return NextResponse.json({ candles, source: "Yahoo Finance" });
  } catch (error) {
    console.error("Chart request failed", market, ticker, error);
    return NextResponse.json({ error: "가격 차트를 불러오지 못했습니다." }, { status: 502 });
  }
}

