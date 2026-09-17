import argparse
import math
import os
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
from openpyxl import Workbook, load_workbook

from export_excel_report import (
    KiwoomClient,
    is_excluded_name,
    is_valid_domestic_ticker,
    load_env_file,
    pykrx_download_one,
    style_sheet,
    write_table,
)
from screeners import nasdaq


CANDIDATE_SHEETS = ["Daily_Candidates", "Weekly_Candidates"]
DETAIL_COLUMNS = [
    "report_date",
    "entry_date",
    "backtest_date",
    "backtest_price_date",
    "holding_days",
    "market",
    "timeframe",
    "ticker",
    "security_name",
    "signal_close",
    "entry_price",
    "backtest_close",
    "exit_date",
    "exit_price",
    "exit_reason",
    "stop_loss_pct",
    "stop_loss_price",
    "return_pct",
    "entry_setup",
    "entry_trigger",
    "final_score",
    "rs_rank",
    "atr_pct",
    "stop_atr_multiple",
    "stop_price",
    "risk_to_stop",
    "market_regime_score",
    "market_exposure",
    "signal_data_source",
    "backtest_price_source",
    "source_sheet",
    "source_file",
]
SUMMARY_COLUMNS = [
    "scope",
    "market",
    "timeframe",
    "signals",
    "priced",
    "avg_return_pct",
    "median_return_pct",
    "win_rate_pct",
    "best_return_pct",
    "worst_return_pct",
    "avg_holding_days",
]


def parse_iso_date(value: str) -> date:
    return date.fromisoformat(str(value)[:10])


def is_true(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "1", "yes", "y"}


def report_date_from_workbook(wb, path: Path) -> Optional[str]:
    if "Summary" in wb.sheetnames:
        ws = wb["Summary"]
        headers = [cell.value for cell in ws[3]]
        if "run_date" in headers:
            col = headers.index("run_date")
            for row in ws.iter_rows(min_row=4, values_only=True):
                if len(row) > col and row[col]:
                    return str(row[col])[:10]

    match = re.search(r"stock_trend_report_(\d{4}-\d{2}-\d{2})", path.name)
    return match.group(1) if match else None


def sheet_records(wb, sheet_name: str) -> List[dict]:
    if sheet_name not in wb.sheetnames:
        return []
    ws = wb[sheet_name]
    headers = [cell.value for cell in ws[1]]
    if not headers or "ticker" not in headers:
        return []

    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not any(value is not None for value in row):
            continue
        item = {headers[index]: row[index] if index < len(row) else None for index in range(len(headers))}
        if item.get("ticker"):
            rows.append(item)
    return rows


def discover_report_files(reports_root: Path, extra_roots: Iterable[Path]) -> List[Path]:
    roots = [reports_root, *extra_roots]
    files_by_name: Dict[str, Path] = {}
    for root in roots:
        if not root.exists():
            continue
        report_paths = set(root.rglob("stock_trend_report_*.xlsx"))
        report_paths.update(root.rglob("*trend_report_*.xlsx"))
        for path in report_paths:
            current = files_by_name.get(path.name)
            if current is None or prefer_report_file(path, current):
                files_by_name[path.name] = path
    return sorted(files_by_name.values())


def prefer_report_file(candidate: Path, current: Path) -> bool:
    candidate_parts = {part.lower() for part in candidate.parts}
    current_parts = {part.lower() for part in current.parts}
    if "results" in candidate_parts and "results" not in current_parts:
        return True
    if "results" not in candidate_parts and "results" in current_parts:
        return False
    return candidate.stat().st_mtime > current.stat().st_mtime


def load_signals(reports_root: Path, extra_roots: Iterable[Path], backtest_date: str) -> Tuple[pd.DataFrame, pd.DataFrame]:
    target = parse_iso_date(backtest_date)
    signals = []
    source_rows = []

    for path in discover_report_files(reports_root, extra_roots):
        if path.name.endswith("_test.xlsx") or path.name.endswith("_nasdaq_check.xlsx"):
            continue
        try:
            wb = load_workbook(path, read_only=True, data_only=True)
        except Exception as exc:
            source_rows.append({"source_file": str(path.resolve()), "report_date": None, "included": False, "rows": 0, "note": f"open_failed: {exc}"})
            continue

        report_date = report_date_from_workbook(wb, path)
        included = bool(report_date and parse_iso_date(report_date) < target)
        row_count = 0

        if included:
            for sheet_name in CANDIDATE_SHEETS:
                for item in sheet_records(wb, sheet_name):
                    if not is_true(item.get("entry_trigger")):
                        continue
                    market = str(item.get("market") or "")
                    ticker = str(item.get("ticker") or "")
                    name = str(item.get("security_name") or "")
                    if market in {"KOSPI", "KOSDAQ"} and (
                        not is_valid_domestic_ticker(ticker) or is_excluded_name(name)
                    ):
                        continue
                    row_count += 1
                    item["report_date"] = report_date
                    item["source_sheet"] = sheet_name
                    item["source_file"] = str(path.resolve())
                    signals.append(item)

        source_rows.append({
            "source_file": str(path.resolve()),
            "report_date": report_date,
            "included": included,
            "rows": row_count,
            "note": "included" if included else "report_date is not before backtest_date",
        })

    return pd.DataFrame(signals), pd.DataFrame(source_rows)


def last_close_record(df: pd.DataFrame, target: str, source: str) -> Optional[dict]:
    if df.empty or "close" not in df.columns:
        return None
    x = df.copy()
    x.index = pd.to_datetime(x.index).date
    x = x[x.index <= parse_iso_date(target)].dropna(subset=["close"])
    if x.empty:
        return None
    last = x.iloc[-1]
    return {
        "backtest_close": float(last["close"]),
        "backtest_price_date": x.index[-1].isoformat(),
        "backtest_price_source": source,
    }


def price_history_record(df: pd.DataFrame, target: str, source: str) -> Optional[dict]:
    record = last_close_record(df, target, source)
    if not record:
        return None
    x = df.copy()
    x.index = pd.to_datetime(x.index).date
    x = x[x.index <= parse_iso_date(target)].dropna(subset=["close"])
    record["history"] = x
    return record


def next_session_entry(price: dict, report_date: str) -> Optional[dict]:
    history = price.get("history")
    if not isinstance(history, pd.DataFrame) or history.empty or "open" not in history.columns:
        return None
    x = history.copy()
    x.index = pd.to_datetime(x.index).date
    x = x[x.index > parse_iso_date(report_date)].sort_index()
    if x.empty:
        return None
    entry_price = to_float(x.iloc[0]["open"])
    if not entry_price or entry_price <= 0:
        return None
    return {"entry_date": x.index[0].isoformat(), "entry_price": entry_price}


def fetch_nasdaq_prices(tickers: List[str], start_date: str, backtest_date: str) -> Dict[str, dict]:
    start = (parse_iso_date(start_date) - timedelta(days=5)).isoformat()
    data = nasdaq.download_in_chunks(sorted(set(tickers)), start, backtest_date, chunk_size=50)
    prices = {}
    for ticker, df in data.items():
        record = price_history_record(df, backtest_date, "Yahoo Finance")
        if record:
            prices[ticker] = record
    return prices


def fetch_domestic_prices(
    tickers: List[str],
    start_date: str,
    backtest_date: str,
    client: Optional[KiwoomClient],
    prefer_kiwoom: bool,
) -> Dict[str, dict]:
    prices = {}
    start = (parse_iso_date(start_date) - timedelta(days=5)).isoformat()

    for index, ticker in enumerate(sorted(set(tickers)), start=1):
        print(f"[INFO] domestic backtest price {index}/{len(set(tickers))} {ticker}")
        if prefer_kiwoom and client and client.available():
            try:
                record = price_history_record(client.daily_chart(ticker, backtest_date), backtest_date, "Kiwoom REST")
                if record:
                    prices[ticker] = record
                    continue
            except Exception as exc:
                print(f"[WARN] Kiwoom backtest price failed {ticker}: {exc}")

        try:
            record = price_history_record(pykrx_download_one(ticker, start, backtest_date), backtest_date, "pykrx fallback")
        except Exception as exc:
            print(f"[WARN] pykrx backtest price failed {ticker}: {exc}")
            record = None
        if record:
            prices[ticker] = record
    return prices


def fetch_backtest_prices(signals: pd.DataFrame, backtest_date: str, client: Optional[KiwoomClient], prefer_kiwoom: bool) -> Dict[Tuple[str, str], dict]:
    lookup: Dict[Tuple[str, str], dict] = {}
    if signals.empty:
        return lookup

    for market, group in signals.groupby("market"):
        tickers = [str(ticker) for ticker in group["ticker"].dropna().unique()]
        start_date = min(parse_iso_date(value).isoformat() for value in group["report_date"].dropna())
        if market == "NASDAQ":
            prices = fetch_nasdaq_prices(tickers, start_date, backtest_date)
        else:
            prices = fetch_domestic_prices(tickers, start_date, backtest_date, client, prefer_kiwoom)
        for ticker, record in prices.items():
            lookup[(str(market), str(ticker))] = record
    return lookup


def to_float(value) -> Optional[float]:
    if value is None:
        return None
    number = pd.to_numeric(value, errors="coerce")
    if pd.isna(number):
        return None
    return float(number)


def normalize_stop_loss_pct(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    pct = float(value)
    if pct <= 0:
        return None
    return pct / 100 if pct > 1 else pct


def normalize_stop_atr_multiple(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    multiple = float(value)
    return multiple if multiple > 0 else None


def apply_stop_loss(
    signal: pd.Series,
    price: dict,
    entry_price: Optional[float],
    entry_date: Optional[str],
    backtest_close: Optional[float],
    backtest_date: str,
    stop_loss_pct: Optional[float],
    stop_atr_multiple: Optional[float],
) -> dict:
    price_date = price.get("backtest_price_date")
    exit_date = price_date
    exit_price = backtest_close
    exit_reason = "hold_to_backtest_date" if backtest_close else None
    stop_loss_price = None
    effective_stop_loss_pct = stop_loss_pct
    stop_reason_prefix = "stop_loss"

    atr_pct = to_float(signal.get("atr_pct"))
    if stop_atr_multiple and atr_pct and atr_pct > 0:
        atr_stop_pct = atr_pct * stop_atr_multiple
        if stop_loss_pct:
            effective_stop_loss_pct = min(atr_stop_pct, stop_loss_pct)
            stop_reason_prefix = f"atr_stop_{stop_atr_multiple:g}x_cap"
        else:
            effective_stop_loss_pct = atr_stop_pct
            stop_reason_prefix = f"atr_stop_{stop_atr_multiple:g}x"

    if not entry_price or not entry_date or not backtest_close or not effective_stop_loss_pct:
        return {
            "exit_date": exit_date,
            "exit_price": exit_price,
            "exit_reason": exit_reason,
            "stop_loss_pct": effective_stop_loss_pct,
            "stop_loss_price": stop_loss_price,
        }

    stop_loss_price = max(0.0, entry_price * (1 - effective_stop_loss_pct))
    history = price.get("history")
    if stop_loss_price > 0 and isinstance(history, pd.DataFrame) and not history.empty and "low" in history.columns:
        position_entry_date = parse_iso_date(entry_date)
        last_date = parse_iso_date(price_date or backtest_date)
        x = history.copy()
        x.index = pd.to_datetime(x.index).date
        x = x[(x.index >= position_entry_date) & (x.index <= last_date)].copy()
        lows = pd.to_numeric(x["low"], errors="coerce")
        hits = x[lows <= stop_loss_price]
        if not hits.empty:
            exit_date = hits.index[0].isoformat()
            hit = hits.iloc[0]
            hit_open = to_float(hit.get("open"))
            if hit_open and hit_open <= stop_loss_price:
                exit_price = hit_open
                exit_reason = f"gap_{stop_reason_prefix}_{effective_stop_loss_pct:.2%}"
            else:
                exit_price = stop_loss_price
                exit_reason = f"{stop_reason_prefix}_{effective_stop_loss_pct:.2%}"

    return {
        "exit_date": exit_date,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "stop_loss_pct": effective_stop_loss_pct,
        "stop_loss_price": stop_loss_price,
    }


def build_detail(
    signals: pd.DataFrame,
    backtest_date: str,
    prices: Dict[Tuple[str, str], dict],
    stop_loss_pct: Optional[float] = None,
    stop_atr_multiple: Optional[float] = None,
) -> pd.DataFrame:
    rows = []
    stop_loss_pct = normalize_stop_loss_pct(stop_loss_pct)
    stop_atr_multiple = normalize_stop_atr_multiple(stop_atr_multiple)
    for _, signal in signals.iterrows():
        market = str(signal.get("market") or "")
        ticker = str(signal.get("ticker") or "")
        price = prices.get((market, ticker), {})
        signal_close = to_float(signal.get("close"))
        entry = next_session_entry(price, str(signal.get("report_date")))
        if not signal_close or not entry:
            continue
        entry_price = entry["entry_price"]
        entry_pivot = to_float(signal.get("entry_pivot"))
        if entry_price > signal_close * 1.03 or (entry_pivot and entry_price < entry_pivot):
            continue
        backtest_close = price.get("backtest_close")
        exit = apply_stop_loss(
            signal,
            price,
            entry_price,
            entry["entry_date"],
            backtest_close,
            backtest_date,
            stop_loss_pct,
            stop_atr_multiple,
        )
        exit_price = exit.get("exit_price")
        return_pct = None
        if entry_price and exit_price:
            return_pct = exit_price / entry_price - 1

        price_date = price.get("backtest_price_date")
        holding_days = None
        if exit.get("exit_date") and entry.get("entry_date"):
            holding_days = (parse_iso_date(exit.get("exit_date")) - parse_iso_date(entry.get("entry_date"))).days

        rows.append({
            "report_date": signal.get("report_date"),
            "entry_date": entry.get("entry_date"),
            "backtest_date": backtest_date,
            "backtest_price_date": price_date,
            "holding_days": holding_days,
            "market": market,
            "timeframe": signal.get("timeframe"),
            "ticker": ticker,
            "security_name": signal.get("security_name"),
            "signal_close": signal_close,
            "entry_price": entry_price,
            "backtest_close": backtest_close,
            "exit_date": exit.get("exit_date"),
            "exit_price": exit_price,
            "exit_reason": exit.get("exit_reason"),
            "stop_loss_pct": exit.get("stop_loss_pct"),
            "stop_loss_price": exit.get("stop_loss_price"),
            "return_pct": return_pct,
            "entry_setup": signal.get("entry_setup"),
            "entry_trigger": signal.get("entry_trigger"),
            "final_score": to_float(signal.get("final_score")),
            "rs_rank": to_float(signal.get("rs_rank")),
            "atr_pct": to_float(signal.get("atr_pct")),
            "stop_atr_multiple": stop_atr_multiple,
            "stop_price": to_float(signal.get("stop_price")),
            "risk_to_stop": to_float(signal.get("risk_to_stop")),
            "market_regime_score": to_float(signal.get("market_regime_score")),
            "market_exposure": to_float(signal.get("market_exposure")),
            "signal_data_source": signal.get("data_source"),
            "backtest_price_source": price.get("backtest_price_source"),
            "source_sheet": signal.get("source_sheet"),
            "source_file": signal.get("source_file"),
        })
    return pd.DataFrame(rows, columns=DETAIL_COLUMNS)


def summarize_group(scope: str, market: str, timeframe: str, df: pd.DataFrame) -> dict:
    priced = df.dropna(subset=["return_pct"])
    return {
        "scope": scope,
        "market": market,
        "timeframe": timeframe,
        "signals": len(df),
        "priced": len(priced),
        "avg_return_pct": priced["return_pct"].mean() if not priced.empty else None,
        "median_return_pct": priced["return_pct"].median() if not priced.empty else None,
        "win_rate_pct": (priced["return_pct"] > 0).mean() if not priced.empty else None,
        "best_return_pct": priced["return_pct"].max() if not priced.empty else None,
        "worst_return_pct": priced["return_pct"].min() if not priced.empty else None,
        "avg_holding_days": priced["holding_days"].mean() if not priced.empty else None,
    }


def build_summary(detail: pd.DataFrame) -> pd.DataFrame:
    rows = [summarize_group("All", "ALL", "ALL", detail)]
    if not detail.empty:
        for market, group in detail.groupby("market", dropna=False):
            rows.append(summarize_group("Market", str(market), "ALL", group))
        for (market, timeframe), group in detail.groupby(["market", "timeframe"], dropna=False):
            rows.append(summarize_group("Market/Timeframe", str(market), str(timeframe), group))
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def clean_cell(value):
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if pd.isna(value):
        return None
    return value


def write_dataframe_sheet(wb: Workbook, name: str, df: pd.DataFrame) -> None:
    ws = wb.create_sheet(name)
    if df.empty:
        df = pd.DataFrame(columns=df.columns)
    rows = [df.columns.tolist()] + df.values.tolist()
    for row_index, row in enumerate(rows, start=1):
        for col_index, value in enumerate(row, start=1):
            ws.cell(row=row_index, column=col_index, value=clean_cell(value))
    style_sheet(ws, 1, max(len(rows), 1), max(len(rows[0]) if rows else 1, 1))


def dataframe_rows(df: pd.DataFrame) -> List[List]:
    return [df.columns.tolist()] + [[clean_cell(value) for value in row] for row in df.values.tolist()]


def write_backtest_workbook(
    detail: pd.DataFrame,
    source_files: pd.DataFrame,
    output_path: Path,
    backtest_date: str,
    reports_root: Path,
    env_source: Optional[Path],
    kiwoom_status: str,
    kiwoom_detail: str,
    stop_loss_pct: Optional[float],
    stop_atr_multiple: Optional[float],
) -> Path:
    wb = Workbook()
    ws = wb.active
    ws.title = "Summary"
    summary = build_summary(detail)
    title = f"Backtest to {backtest_date}"
    if stop_loss_pct and not stop_atr_multiple:
        title += f" with {stop_loss_pct:.2%} stop loss"
    if stop_atr_multiple and stop_loss_pct:
        title += f" with ATR {stop_atr_multiple:g}x stop capped at {stop_loss_pct:.2%}"
    elif stop_atr_multiple:
        title += f" with ATR {stop_atr_multiple:g}x stop"
    write_table(ws, dataframe_rows(summary), title=title)

    status = wb.create_sheet("Data_Source_Status")
    write_table(status, [
        ["item", "value"],
        ["generated_at", datetime.now().isoformat(timespec="seconds")],
        ["backtest_date", backtest_date],
        ["reports_root", str(reports_root.resolve())],
        ["env_file_loaded", str(env_source) if env_source else ""],
        ["kiwoom_key_file_source", os.environ.get("KIWOOM_KEY_FILE_SOURCE", "")],
        ["kiwoom_status", kiwoom_status],
        ["kiwoom_detail", kiwoom_detail],
        ["rule", "Each report-date signal remains a separate row; tickers are not de-duplicated across dates."],
        ["stop_loss_rule", f"First post-signal daily low <= signal_close * (1 - {stop_loss_pct:.2%}); exit at stop price." if stop_loss_pct and not stop_atr_multiple else ""],
        ["stop_atr_rule", f"First post-signal daily low <= signal_close * (1 - atr_pct * {stop_atr_multiple:g}); exit at ATR stop price." if stop_atr_multiple and not stop_loss_pct else ""],
        ["stop_atr_cap_rule", f"First post-signal daily low <= signal_close * (1 - min(atr_pct * {stop_atr_multiple:g}, {stop_loss_pct:.2%})); exit at capped ATR stop price." if stop_atr_multiple and stop_loss_pct else ""],
    ], title="Source status")

    write_dataframe_sheet(wb, "Backtest_Detail", detail)
    write_dataframe_sheet(wb, "Source_Files", source_files)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--backtest-date", default=date.today().isoformat())
    parser.add_argument("--reports-root", type=Path, default=Path("reports"))
    parser.add_argument("--extra-report-dir", type=Path, action="append", default=[])
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--no-kiwoom", action="store_true")
    parser.add_argument("--stop-loss-pct", type=float, help="Optional stop loss as decimal or percent, e.g. 0.07 or 7.")
    parser.add_argument("--stop-atr-multiple", type=float, help="Optional ATR-based stop multiple, e.g. 3 means close - 3 * ATR.")
    args = parser.parse_args()
    stop_loss_pct = normalize_stop_loss_pct(args.stop_loss_pct)
    stop_atr_multiple = normalize_stop_atr_multiple(args.stop_atr_multiple)

    env_source = load_env_file(args.env_file)
    client = None if args.no_kiwoom else KiwoomClient()
    prefer_kiwoom = bool(client and client.available())
    kiwoom_status = client.status if client else "disabled"
    kiwoom_detail = client.status_detail if client else "--no-kiwoom"

    signals, source_files = load_signals(args.reports_root, args.extra_report_dir, args.backtest_date)
    print(f"[INFO] loaded signals={len(signals)} from reports_root={args.reports_root}")
    prices = fetch_backtest_prices(signals, args.backtest_date, client, prefer_kiwoom)
    detail = build_detail(signals, args.backtest_date, prices, stop_loss_pct, stop_atr_multiple)

    suffix = ""
    if stop_atr_multiple:
        suffix = f"_atr{str(round(stop_atr_multiple, 4)).replace('.', '_')}x"
        if stop_loss_pct:
            suffix += f"_cap{str(round(stop_loss_pct * 100, 4)).replace('.', '_')}pct"
    elif stop_loss_pct:
        suffix = f"_stop{str(round(stop_loss_pct * 100, 4)).replace('.', '_')}pct"
    output = args.output or Path("reports") / "results" / args.backtest_date / f"backtest_to_{args.backtest_date}{suffix}.xlsx"
    path = write_backtest_workbook(
        detail,
        source_files,
        output,
        args.backtest_date,
        args.reports_root,
        env_source,
        kiwoom_status,
        kiwoom_detail,
        stop_loss_pct,
        stop_atr_multiple,
    )
    print(f"[INFO] Backtest report saved: {path.resolve()}")


if __name__ == "__main__":
    main()

