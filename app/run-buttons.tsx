"use client";

import { useState } from "react";
import { Database, Play, RefreshCw } from "lucide-react";
import type { Market, Strategy, Timeframe } from "@/lib/types";

type RunState = {
  status: "idle" | "running" | "success" | "error";
  message: string;
};

export function RunButtons({ market, strategy, timeframe, date }: { market: Market; strategy: Strategy; timeframe: Timeframe; date?: string }) {
  const [state, setState] = useState<RunState>({ status: "idle", message: "" });

  async function runNow(action: "scan" | "prepare" = "scan") {
    setState({ status: "running", message: `Requesting ${market} workflow...` });
    try {
      const response = await fetch("/api/run-screener", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ market, strategy, action }),
      });
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data.error ?? "Failed to request workflow.");
      }
      setState({
        status: "success",
        message: `${market} workflow requested. Refresh latest results after it finishes.`,
      });
    } catch (error) {
      setState({
        status: "error",
        message: error instanceof Error ? error.message : "Unknown request error.",
      });
    }
  }

  return (
    <div className="runPanel">
      <button className="primaryButton" type="button" onClick={() => runNow()} disabled={state.status === "running"}>
        {state.status === "running" ? <RefreshCw size={16} className="spin" /> : <Play size={16} />}
        {strategy === "gap224" ? "오늘 갭 검사" : `Run ${market} now`}
      </button>
      {strategy === "gap224" && <button className="secondaryButton" type="button" onClick={() => runNow("prepare")} disabled={state.status === "running"}><Database size={16} /> 기준 데이터 준비</button>}
      <a className="secondaryButton" href={`/?market=${market}&strategy=${strategy}&timeframe=${timeframe}${date ? `&date=${encodeURIComponent(date)}` : ''}`}>
        <RefreshCw size={16} />
        Refresh latest
      </a>
      {state.message && <p className={`runMessage ${state.status}`}>{state.message}</p>}
    </div>
  );
}
