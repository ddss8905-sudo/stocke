"""Provider adapters; KRX regular-session bars and US regular-session bars only."""

from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests

from .gap224 import positive


def prepare_sources(market, reference_date):
    from . import kosdaq, kospi_api, nasdaq
    from .common import start_date
    from .sectors import fetch_kind_sectors, filter_official_listing

    module = {"NASDAQ": nasdaq, "KOSDAQ": kosdaq, "KOSPI_API": kospi_api}[market]
    start = start_date(reference_date, module.CFG.lookback_days)
    if market == "NASDAQ":
        selected = nasdaq.select_top_by_adv(reference_date)
        histories = nasdaq.download_ohlcv(selected.ticker.tolist(), start, reference_date)
        sectors = selected.set_index("ticker").sector_name.to_dict()
    elif market == "KOSDAQ":
        selected = kosdaq.select_top_by_adv(reference_date)
        histories = kosdaq.download_ohlcv(selected.ticker.tolist(), start, reference_date)
        sectors = fetch_kind_sectors("KOSDAQ")
    else:
        client = kospi_api.KisClient()
        selected = filter_official_listing(kospi_api.fetch_top_by_current_value(client), "KOSPI").head(module.CFG.universe_size)
        histories = kospi_api.download_ohlcv(selected.ticker.tolist(), start, reference_date, client)
        sectors = fetch_kind_sectors("KOSPI")
    return selected, histories, sectors


def yahoo_quote(ticker, session_date, reference_date):
    response = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                            params={"range": "1mo", "interval": "1d", "includePrePost": "false"},
                            headers={"User-Agent": "Mozilla/5.0 Stocke/1.0"}, timeout=20)
    response.raise_for_status()
    result = response.json()["chart"]["result"][0]
    market_time = result.get("meta", {}).get("regularMarketTime")
    if not market_time or pd.Timestamp(market_time, unit="s", tz="UTC").tz_convert("America/New_York").strftime("%Y-%m-%d") != session_date:
        return None
    quote = result["indicators"]["quote"][0]
    rows = {}
    for i, timestamp in enumerate(result.get("timestamp") or []):
        day = pd.Timestamp(timestamp, unit="s", tz="UTC").tz_convert("America/New_York").strftime("%Y-%m-%d")
        rows[day] = {key: (quote.get(key) or [None] * (i + 1))[i] for key in ("open", "high", "low", "close", "volume")}
    today, yesterday = rows.get(session_date), rows.get(reference_date)
    if not today or not yesterday or not all(positive(today.get(k)) for k in ("open", "high", "low", "close", "volume")):
        return None
    return {**today, "session_date": session_date, "previous_close": yesterday["close"],
            "regular_session": True, "source": "Yahoo Finance", "regular_market_at": pd.Timestamp(market_time, unit="s", tz="UTC").isoformat(),
            "quote_at": pd.Timestamp.now(tz="UTC").isoformat()}


def kis_quote(client, ticker, session_date, reference_date):
    # A short range gives both actual business dates and a same-basis previous close.
    frame = client._daily_chart_chunk(ticker, reference_date, session_date)
    if frame.empty:
        return None
    frame.index = pd.to_datetime(frame.index).strftime("%Y-%m-%d")
    if session_date not in frame.index or reference_date not in frame.index:
        return None
    today = frame.loc[session_date]
    if not all(positive(today.get(k)) for k in ("open", "high", "low", "close", "volume")):
        return None
    return {**{key: float(today[key]) for key in ("open", "high", "low", "close", "volume")},
            "session_date": session_date, "previous_close": float(frame.loc[reference_date, "close"]),
            "regular_session": True, "source": "KIS / KRX", "quote_at": pd.Timestamp.now(tz="UTC").isoformat()}


def fetch_quotes(market, tickers, session_date, reference_date):
    quotes, errors = {}, {}
    if market == "NASDAQ":
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = {executor.submit(yahoo_quote, ticker, session_date, reference_date): ticker for ticker in tickers}
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    quote = future.result()
                    if quote:
                        quotes[ticker] = quote
                    else:
                        errors[ticker] = "opening_not_ready"
                except Exception:
                    errors[ticker] = "provider_error"
    elif tickers:
        from .kospi_api import KisClient
        client = KisClient()
        for ticker in tickers:
            try:
                quote = kis_quote(client, ticker, session_date, reference_date)
                if quote:
                    quotes[ticker] = quote
                else:
                    errors[ticker] = "opening_not_ready"
            except Exception:
                errors[ticker] = "provider_error"
    return quotes, errors
