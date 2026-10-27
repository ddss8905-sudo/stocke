"""Independent daily opening-gap workflow. Telegram and keys remain server-only."""

import argparse
import os
import sys
import time
from collections import Counter

import numpy as np
import pandas as pd

from gap_store import GapStore
from gap_telegram import configured, deliver, send_message
from screeners.gap224 import baseline_row, current_state, detect_gap, positive, session_context
from screeners.gap224_sources import fetch_quotes, prepare_sources


MARKETS = ("NASDAQ", "KOSDAQ", "KOSPI_API")


def utc_now():
    return pd.Timestamp.now(tz="UTC")


def canonical_history(history, reference_date, market):
    frame = history.copy()
    frame.index = pd.to_datetime(frame.index, errors="coerce")
    if frame.index.isna().any() or frame.index.has_duplicates:
        return None
    frame = frame.loc[frame.index.strftime("%Y-%m-%d") <= reference_date].sort_index().tail(504)
    if frame.index.isna().any() or frame.index.has_duplicates:
        return None
    columns = ["open", "high", "low", "close", "volume"]
    if not all(column in frame for column in columns):
        return None
    frame[columns] = frame[columns].apply(pd.to_numeric, errors="coerce")
    if market != "NASDAQ":
        high = frame[["open", "close"]].max(axis=1)
        low = frame[["open", "close"]].min(axis=1)
        valid_range = frame.high >= frame.low
        mask = (high - frame.high).between(0, 1) & valid_range
        frame.loc[mask, "high"] = high[mask]
        mask = (frame.low - low).between(0, 1) & valid_range
        frame.loc[mask, "low"] = low[mask]
    valid = np.isfinite(frame[columns]).all(axis=1) & (frame[columns[:4]] > 0).all(axis=1)
    valid &= (frame.volume >= 0) & (frame.high >= frame[["open", "close", "low"]].max(axis=1))
    valid &= frame.low <= frame[["open", "close"]].min(axis=1)
    return frame if valid.all() else None


def candle_records(frame):
    return [{"time": index.strftime("%Y-%m-%d"), **{key: float(row[key]) for key in ("open", "high", "low", "close", "volume")}}
            for index, row in frame.iterrows()]


def prepare(store, market, reference_date):
    path = f"{market}/baselines/{reference_date}.json"
    existing = store.get(path)
    if existing and existing.get("status") == "complete":
        return existing
    selected, histories, sectors = prepare_sources(market, reference_date)
    if selected.empty:
        raise RuntimeError("empty_universe")
    stocks, excluded, missing, valid_count = [], [], [], 0
    for stock in selected.to_dict("records"):
        ticker = stock["ticker"]
        if ticker not in histories:
            missing.append(ticker)
            continue
        frame = canonical_history(histories[ticker], reference_date, market)
        row = None if frame is None else baseline_row(ticker, stock.get("security_name", ticker), frame,
                                                      reference_date, sectors.get(ticker))
        if row is None:
            excluded.append(ticker)
            continue
        valid_count += 1
        if row["eligible"]:
            row["candles"] = candle_records(frame)
            stocks.append(row)
    if not valid_count:
        raise RuntimeError("baseline_provider_failure")
    baseline = {"version": 1, "market": market, "reference_date": reference_date,
                "prepared_at": utc_now().isoformat(), "status": "partial" if missing else "complete",
                "scope": "liquid_universe", "selected_count": len(selected), "target_count": len(stocks),
                "valid_history_count": valid_count, "excluded_count": len(excluded), "missing_count": len(missing), "stocks": stocks}
    store.put(path, baseline)
    return baseline


def publish(store, market, session_date, snapshot):
    snapshot.update({"version": 1, "market": market, "session_date": session_date, "updated_at": utc_now().isoformat()})
    # Authenticated Storage reads plus no-cache on both ends keep these small indexes fresh.
    store.put(f"{market}/{session_date}/summary.json", snapshot)
    latest = store.get(f"{market}/latest.json")
    if not latest or latest["session_date"] <= session_date:
        store.put(f"{market}/latest.json", snapshot)


def scan(store, market, context, reconcile=False):
    day, reference = context["date"], context["reference_date"]
    previous = store.get(f"{market}/{day}/summary.json") or {}
    events = {event["ticker"]: event for event in previous.get("events", [])}
    publish(store, market, day, {**previous, "status": "running", "events": list(events.values()),
                                "telegram_configured": configured()})
    baseline = store.get(f"{market}/baselines/{reference}.json")
    if not baseline or baseline.get("status") != "complete":
        publish(store, market, day, {"status": "preparing", "events": list(events.values()), "telegram_configured": configured()})
        baseline = prepare(store, market, reference)
    if baseline.get("reference_date") != reference:
        raise RuntimeError("stale_baseline")
    targets = {stock["ticker"]: stock for stock in baseline["stocks"]}
    quotes, errors = fetch_quotes(market, list(targets), day, reference)
    checked, notifications = 0, Counter()
    for ticker, stock in targets.items():
        quote = quotes.get(ticker)
        if quote is None:
            continue
        if quote.get("session_date") != day or not quote.get("regular_session"):
            errors[ticker] = "stale_quote"
            continue
        if market == "NASDAQ" and (not quote.get("regular_market_at") or pd.Timestamp(quote["regular_market_at"]) < context["open"]):
            errors[ticker] = "opening_not_ready"
            continue
        live_previous = quote.get("previous_close")
        tolerance = max(0.00001, stock["previous_close"] * 0.000001) if market == "NASDAQ" else 0.000001
        if not positive(live_previous) or abs(live_previous - stock["previous_close"]) > tolerance:
            errors[ticker] = "price_basis_changed"
            if ticker in events:
                events[ticker].update({"validation": "review", "state": "unverified"})
            continue
        checked += 1
        found_at = utc_now()
        event = detect_gap(stock, quote, day, found_at.isoformat(),
                           late=(found_at - context["open"]).total_seconds() > 90 * 60)
        event_path = f"{market}/{day}/events/{ticker}.json"
        if event:
            if ticker not in events:
                first = store.get(event_path)
                if first is None:
                    # Chart is durable before the event can be announced.
                    candles = stock["candles"] + [{"time": day, **{key: float(quote[key]) for key in ("open", "high", "low", "close", "volume")}}]
                    store.put(f"{market}/{day}/charts/{ticker}.json", {"candles": candles, "source": quote["source"]})
                    if store.put(event_path, event, create=True):
                        first = event
                    else:
                        first = store.get(event_path)
                if first is None:
                    raise RuntimeError("event_persistence_failed")
                events[ticker] = first
            original = events[ticker]
            if abs(original["open_price"] - event["open_price"]) > max(0.00001, original["open_price"] * .000001):
                original.update({"validation": "review", "state": "unverified"})
                continue
            events[ticker] = current_state({**original, "validation": "valid"}, quote)
            if events[ticker]["validation"] == "valid":
                try:
                    delivery = deliver(store, market, events[ticker])
                    notifications[delivery] += 1
                    if delivery == "sent":
                        time.sleep(1.05)
                except Exception:
                    notifications["storage_error"] += 1
        elif ticker in events:
            events[ticker].update({"validation": "corrected", "state": "corrected", "updated_at": quote.get("quote_at")})
    rows = sorted(events.values(), key=lambda row: row["gap_pct"], reverse=True)
    summary = {"status": "partial" if errors or baseline["missing_count"] else "complete", "reference_date": reference,
               "scope": "liquid_universe", "selected_count": baseline["selected_count"], "target_count": len(targets),
               "checked_count": checked, "excluded_count": baseline["excluded_count"],
               "pending_count": len(errors), "baseline_missing_count": baseline["missing_count"],
               "error_reasons": dict(Counter(errors.values())), "events": rows,
               "valid_count": sum(event["validation"] == "valid" for event in rows),
               "telegram_configured": configured(), "notifications": dict(notifications), "reconciled": reconcile}
    publish(store, market, day, summary)
    print(f"[INFO] gap224 {market}: {checked}/{len(targets)} checked, {summary['valid_count']} valid events, status={summary['status']}")
    if summary["error_reasons"]:
        print(f"[INFO] gap224 pending reasons: {summary['error_reasons']}")
    return summary


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--market", choices=MARKETS, required=True)
    parser.add_argument("--mode", choices=("auto", "prepare", "scan", "reconcile", "test-telegram"), default="scan")
    args = parser.parse_args()
    if args.mode == "test-telegram":
        if not configured():
            raise RuntimeError("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in GitHub Actions secrets first.")
        result = send_message("Stocke 224일선 갭 돌파 알림 연결 테스트입니다. 실제 종목 신호가 아닙니다.")
        print(f"[INFO] Telegram test: {result['status']}")
        if result["status"] != "sent":
            raise RuntimeError("Telegram test not confirmed. Check bot/chat settings; do not paste the token in logs or chat.")
        return
    context = session_context(args.market)
    store = GapStore()
    previous = store.get(f"{args.market}/{context['date']}/summary.json") or {}
    try:
        if args.mode == "prepare":
            reference = context.get("completed_date", context["reference_date"])
            baseline = prepare(store, args.market, reference)
            print(f"[INFO] gap224 prepared {args.market}: {baseline['target_count']} targets, reference={reference}")
            return
        if not context["is_session"]:
            publish(store, args.market, context["date"], {"status": "closed", "events": [], "telegram_configured": configured()})
            return
        minutes = context["minutes_since_open"]
        now = utc_now()
        if minutes < 0:
            prepare(store, args.market, context["reference_date"])
            publish(store, args.market, context["date"], {"status": "waiting", "events": [], "telegram_configured": configured()})
            return
        closing_ready = now >= context["ready_at"]
        if args.mode == "auto" and minutes > 90 and not closing_ready:
            print("[INFO] outside gap scan window; skipped")
            return
        if args.mode == "reconcile" and not closing_ready:
            raise RuntimeError("market_not_closed")
        summary = scan(store, args.market, context, reconcile=args.mode == "reconcile" or closing_ready)
        if closing_ready:
            prepare(store, args.market, context["date"])
        if not configured():
            print("[WARN] Telegram is not configured; saved events only. Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID.")
        elif any(summary["notifications"].get(key) for key in ("unknown", "sending", "failed", "storage_error")):
            print("[ERROR] Telegram delivery not confirmed; scan records were saved. Review delivery status before resending.")
            raise SystemExit(2)
    except Exception as error:
        # No exception text from provider requests: it could contain authentication data.
        latest = store.get(f"{args.market}/{context['date']}/summary.json") or previous
        publish(store, args.market, context["date"], {**latest, "status": "failed", "error": type(error).__name__,
                                                     "events": latest.get("events", []), "telegram_configured": configured()})
        print(f"[ERROR] gap224 {args.market}: {type(error).__name__}; previous records preserved")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

