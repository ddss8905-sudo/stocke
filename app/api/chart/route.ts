import { NextRequest, NextResponse } from "next/server";

type YahooChart = {
  chart?: {
    result?: Array<{
      timestamp?: number[];
      indicators?: { quote?: Array<{ open?: Array<number | null>; high?: Array<number | null>; low?: Array<number | null>; close?: Array<number | null>; volume?: Array<number | null> }> };
    }>;
  };
};

export async function GET(request: NextRequest) {
  const market = request.nextUrl.searchParams.get("market");
  const ticker = request.nextUrl.searchParams.get("ticker");
  if ((market !== "KOSDAQ" && market !== "KOSPI_API") || !ticker || !/^\d{6}$/.test(ticker)) {
    return NextResponse.json({ error: "잘못된 종목 코드입니다." }, { status: 400 });
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
    return NextResponse.json({ candles });
  } catch (error) {
    console.error("Chart request failed", market, ticker, error);
    return NextResponse.json({ error: "가격 차트를 불러오지 못했습니다." }, { status: 502 });
  }
}

