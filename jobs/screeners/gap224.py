"""Opening-gap events. Detection never uses today's close/high/low/volume."""

import math
from typing import Optional

import pandas as pd


PERIOD = 224


def above(left, right):
    return left > right and not math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-10)


def positive(value) -> bool:
    try:
        return math.isfinite(float(value)) and float(value) > 0
    except (ValueError, TypeError):
        return False


def baseline_row(ticker: str, name: str, history: pd.DataFrame, reference_date: str,
                 sector: Optional[str] = None) -> Optional[dict]:
    if history.empty or "close" not in history:
        return None
    frame = history.copy()
    frame.index = pd.to_datetime(frame.index, errors="coerce")
    if frame.index.isna().any() or frame.index.has_duplicates:
        return None
    frame = frame.sort_index().loc[lambda x: x.index.strftime("%Y-%m-%d") <= reference_date]
    if len(frame) < PERIOD or frame.index[-1].strftime("%Y-%m-%d") != reference_date:
        return None
    closes = pd.to_numeric(frame.close.tail(PERIOD), errors="coerce")
    if not all(positive(value) for value in closes):
        return None
    previous_close = float(closes.iloc[-1])
    ma = float(closes.mean())
    return {"ticker": ticker, "security_name": name, "sector_name": sector,
            "reference_date": reference_date, "previous_close": previous_close,
            "ma224_previous": ma, "sum223": float(closes.tail(PERIOD - 1).sum()),
            "previous_high": float(frame.high.iloc[-1]) if "high" in frame and positive(frame.high.iloc[-1]) else None,
            "eligible": not above(previous_close, ma)}


def detect_gap(baseline: dict, quote: dict, session_date: str, detected_at: str,
               late: bool = False) -> Optional[dict]:
    if quote.get("session_date") != session_date or not quote.get("regular_session"):
        return None
    opening = quote.get("open")
    previous = baseline["previous_close"]
    if not positive(opening) or not positive(previous):
        return None
    opening = float(opening)
    ma_open = (baseline["sum223"] + opening) / PERIOD
    if not (not above(previous, baseline["ma224_previous"]) and above(opening, previous) and above(opening, ma_open)):
        return None
    return {
        "ticker": baseline["ticker"], "security_name": baseline["security_name"],
        "sector_name": baseline.get("sector_name"), "session_date": session_date,
        "previous_close": previous, "open_price": opening,
        "ma224_previous": baseline["ma224_previous"], "ma224_open": ma_open,
        "gap_pct": opening / previous - 1, "breakout_pct": opening / ma_open - 1,
        "above_previous_high": positive(baseline.get("previous_high")) and opening > baseline["previous_high"],
        "detected_at": detected_at, "quote_at": quote.get("quote_at"),
        "source": quote.get("source"), "late_detection": late, "validation": "valid",
        "detected_price": float(quote["close"]) if positive(quote.get("close")) else opening,
    }


def current_state(event: dict, quote: dict) -> dict:
    result = dict(event)
    price = quote.get("close")
    if positive(price):
        result.update({"current_price": float(price), "state": "holding" if price > event["ma224_open"] else "below",
                       "updated_at": quote.get("quote_at")})
    return result


def session_context(market: str, now: Optional[pd.Timestamp] = None) -> dict:
    import exchange_calendars as calendars

    now = now if now is not None else pd.Timestamp.now(tz="UTC")
    timezone = "America/New_York" if market == "NASDAQ" else "Asia/Seoul"
    local = now.tz_convert(timezone)
    day = local.strftime("%Y-%m-%d")
    calendar = calendars.get_calendar("XNAS" if market == "NASDAQ" else "XKRX",
                                      start=f"{local.year - 2}-01-01", end=f"{local.year + 1}-12-31")
    if not calendar.is_session(day):
        reference = calendar.date_to_session(day, direction="previous").strftime("%Y-%m-%d")
        return {"date": day, "is_session": False, "reference_date": reference, "timezone": timezone}
    opening, closing = calendar.session_open(day), calendar.session_close(day)
    ready_at = closing + pd.Timedelta(minutes=20)
    if market != "NASDAQ":
        # Do not freeze Korean daily bars intraday when community calendars miss a delayed close.
        ready_at = max(ready_at, pd.Timestamp(day + " 16:45", tz=timezone).tz_convert("UTC"))
    reference = calendar.previous_session(day).strftime("%Y-%m-%d")
    completed = day if now >= ready_at else reference
    return {"date": day, "is_session": True, "reference_date": reference, "completed_date": completed,
            "open": opening, "close": closing, "ready_at": ready_at, "timezone": timezone,
            "minutes_since_open": (now - opening).total_seconds() / 60}
