from typing import Dict, List

import numpy as np
import pandas as pd
import requests
from bs4 import BeautifulSoup
from pykrx import stock


PERIODS = (5, 10, 21)


def fetch_kind_sectors(market: str) -> Dict[str, str]:
    market_type = "kosdaqMkt" if market == "KOSDAQ" else "stockMkt"
    response = requests.get(
        "https://kind.krx.co.kr/corpgeneral/corpList.do",
        params={"method": "download", "searchType": "13", "marketType": market_type},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=30,
    )
    response.raise_for_status()
    document = BeautifulSoup(response.content.decode("cp949"), "html.parser")
    sectors = {}
    for row in document.select("tr"):
        cells = row.select("td")
        if len(cells) < 4:
            continue
        ticker = cells[2].get_text(" ", strip=True)
        sector = cells[3].get_text(" ", strip=True)
        if len(ticker) == 6 and ticker.isdigit() and sector:
            sectors[ticker] = sector
    if not sectors:
        raise ValueError("KIND returned no industry classifications")
    print(f"[INFO] KIND {market} sector classifications: {len(sectors)}")
    return sectors


def fetch_krx_sectors(run_date: str, market: str) -> Dict[str, str]:
    try:
        data = stock.get_market_sector_classifications(run_date.replace("-", ""), market)
        if data.empty or "업종명" not in data.columns:
            raise ValueError("KRX returned no industry classifications")
        tickers = data["티커"] if "티커" in data.columns else data.index.to_series()
        return {
            str(ticker).zfill(6): str(sector).strip()
            for ticker, sector in zip(tickers, data["업종명"])
            if pd.notna(sector) and str(sector).strip()
        }
    except Exception as exc:
        print(f"[WARN] {market} sector classifications unavailable: {exc}")
        try:
            return fetch_kind_sectors(market)
        except Exception as fallback_exc:
            print(f"[WARN] KIND {market} sector classifications unavailable: {fallback_exc}")
            return {}


def complete_domestic_sectors(tickers: List[str], sectors: Dict[str, str], client) -> Dict[str, str]:
    result = dict(sectors)
    missing = [ticker for ticker in tickers if ticker not in result]
    for index, ticker in enumerate(missing, 1):
        try:
            sector = client.stock_sector(ticker)
            if sector:
                result[ticker] = sector
        except Exception as exc:
            if index <= 3:
                print(f"[WARN] KIS sector lookup failed for {ticker}: {exc}")
        if index % 50 == 0:
            print(f"[INFO] KIS sector lookup {index}/{len(missing)}")
    print(f"[INFO] domestic sector coverage {sum(ticker in result for ticker in tickers)}/{len(tickers)}")
    return result


def calculate_sector_strength(
    scored: pd.DataFrame,
    histories: Dict[str, pd.DataFrame],
    benchmark: pd.DataFrame,
) -> pd.DataFrame:
    columns = [
        "sector_name", "period_days", "sector_return", "benchmark_return",
        "relative_return", "breadth", "turnover_ratio", "score",
        "member_count", "valid_count", "coverage", "is_leader",
    ]
    if scored.empty or "sector_name" not in scored.columns or benchmark.empty:
        return pd.DataFrame(columns=columns)

    sector_members = scored.dropna(subset=["sector_name"])[["ticker", "sector_name"]]
    sector_members = sector_members[sector_members["sector_name"].astype(str).str.strip() != ""]
    if sector_members.empty:
        return pd.DataFrame(columns=columns)
    minimum_sample = 3 if len(sector_members) < 30 else 5

    benchmark_close = pd.to_numeric(benchmark["close"], errors="coerce").dropna()
    if len(benchmark_close) < max(PERIODS) + 1:
        return pd.DataFrame(columns=columns)
    as_of = benchmark_close.index[-1]
    rows: List[dict] = []

    for days in PERIODS:
        benchmark_return = float(benchmark_close.iloc[-1] / benchmark_close.iloc[-days - 1] - 1)
        window_start = benchmark_close.index[-days - 1]
        sector_values: Dict[str, List[dict]] = {}
        for member in sector_members.itertuples(index=False):
            history = histories.get(member.ticker)
            if history is None or history.empty or as_of not in history.index or window_start not in history.index:
                continue
            prices = pd.to_numeric(history["close"], errors="coerce")
            trading_value = pd.to_numeric(
                history["value"] if "value" in history.columns else history["close"] * history["volume"],
                errors="coerce",
            )
            current = trading_value.loc[window_start:as_of].iloc[1:]
            prior = trading_value.loc[:window_start].iloc[-20:]
            start_price = prices.loc[window_start]
            end_price = prices.loc[as_of]
            if (
                not np.isfinite(start_price) or start_price <= 0 or not np.isfinite(end_price) or
                len(current) != days or len(prior) != 20 or
                current.isna().any() or prior.isna().any() or prior.mean() <= 0
            ):
                continue
            sector_values.setdefault(member.sector_name, []).append({
                "return": float(end_price / start_price - 1),
                "turnover": float(current.mean() / prior.mean()),
            })

        for sector_name, members in sector_members.groupby("sector_name"):
            values = sector_values.get(sector_name, [])
            member_count = len(members)
            valid_count = len(values)
            coverage = valid_count / member_count
            if valid_count < minimum_sample or coverage < 0.8:
                continue
            sector_return = float(np.median([value["return"] for value in values]))
            rows.append({
                "sector_name": sector_name,
                "period_days": days,
                "sector_return": sector_return,
                "benchmark_return": benchmark_return,
                "relative_return": sector_return - benchmark_return,
                "breadth": sum(value["return"] > 0 for value in values) / valid_count,
                "turnover_ratio": float(np.median([value["turnover"] for value in values])),
                "member_count": member_count,
                "valid_count": valid_count,
                "coverage": coverage,
            })

    result = pd.DataFrame(rows)
    if result.empty:
        return pd.DataFrame(columns=columns)
    for days, group in result.groupby("period_days"):
        index = group.index
        result.loc[index, "score"] = (
            0.60 * group["relative_return"].rank(pct=True) +
            0.25 * group["breadth"].rank(pct=True) +
            0.15 * group["turnover_ratio"].rank(pct=True)
        ) * 100
        result.loc[index, "is_leader"] = (
            (result.loc[index, "score"].rank(pct=True) > 0.8) &
            (group["valid_count"] >= 5) &
            (group["sector_return"] > 0) &
            (group["relative_return"] > 0) &
            (group["breadth"] >= 0.6)
        )
    result["is_leader"] = result["is_leader"].astype(bool)
    return result.sort_values(["period_days", "score"], ascending=[True, False])[columns].reset_index(drop=True)

