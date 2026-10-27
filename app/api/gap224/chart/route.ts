import { NextRequest, NextResponse } from "next/server";
import { gapObject, marketDate, validGapDate } from "@/lib/gap224";
import type { Market } from "@/lib/types";

export async function GET(request: NextRequest) {
  const market = request.nextUrl.searchParams.get("market") as Market;
  const ticker = request.nextUrl.searchParams.get("ticker") ?? "";
  const date = request.nextUrl.searchParams.get("date") ?? "";
  if (!["NASDAQ", "KOSDAQ", "KOSPI_API"].includes(market) || !validGapDate(date)
    || date > marketDate(market) || !(market === "NASDAQ" ? /^[A-Z][A-Z0-9.-]{0,9}$/.test(ticker) : /^\d{6}$/.test(ticker))) {
    return NextResponse.json({ error: "잘못된 차트 요청입니다." }, { status: 400 });
  }
  try {
    const chart = await gapObject<{ candles: unknown[]; source: string }>(`${market}/${date}/charts/${ticker}.json`);
    if (!chart?.candles.length) return NextResponse.json({ error: "저장된 검출 당시 차트가 없습니다." }, { status: 404 });
    return NextResponse.json({ ...chart, snapshot: true, timeframe: "daily" });
  } catch {
    return NextResponse.json({ error: "차트 저장소 연결을 확인해 주세요." }, { status: 503 });
  }
}
