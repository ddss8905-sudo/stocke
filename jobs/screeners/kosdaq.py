import re
from datetime import date, timedelta
from typing import Dict, List
from urllib.request import Request, urlopen

import pandas as pd
from pykrx import stock

from .common import (
    MarketConfig,
    add_technical_features,
    build_market_regime,
    build_candidates,
    latest_feature_row,
    score_universe,
    start_date,
)


OHLCV_COLUMNS = ["open", "high", "low", "close", "volume"]
SELECTED_COLUMNS = ["ticker", "security_name", "close", "adv"]
EXCLUDE_NAME_KEYWORDS = [
    "우", "우선주", "리츠", "스팩", "인버스", "레버리지", "ETF", "ETN",
    "KODEX", "TIGER", "ACE", "KBSTAR", "SOL", "HANARO", "KOSEF",
    "ARIRANG", "TIMEFOLIO", "PLUS", "RISE", "WON", "히어로즈", "마이티",
]

BASE_KOSDAQ_TICKERS = [
    ("196170", "알테오젠"),
    ("028300", "HLB"),
    ("247540", "에코프로비엠"),
    ("086520", "에코프로"),
    ("277810", "레인보우로보틱스"),
    ("214450", "파마리서치"),
    ("141080", "리가켐바이오"),
    ("039030", "이오테크닉스"),
    ("058470", "리노공업"),
    ("068760", "셀트리온제약"),
    ("145020", "휴젤"),
    ("041510", "에스엠"),
    ("035900", "JYP Ent."),
    ("112040", "위메이드"),
    ("293490", "카카오게임즈"),
    ("263750", "펄어비스"),
    ("067310", "하나마이크론"),
    ("240810", "원익IPS"),
    ("005290", "동진쎄미켐"),
    ("095340", "ISC"),
    ("036540", "SFA반도체"),
    ("078600", "대주전자재료"),
    ("222800", "심텍"),
    ("357780", "솔브레인"),
    ("036930", "주성엔지니어링"),
    ("086900", "메디톡스"),
    ("101490", "에스앤에스텍"),
    ("122870", "와이지엔터테인먼트"),
    ("033640", "네오위즈"),
    ("108320", "LX세미콘"),
    ("403870", "HPSP"),
    ("195940", "HK이노엔"),
    ("348370", "엔켐"),
    ("214370", "케어젠"),
    ("098460", "고영"),
    ("178320", "서진시스템"),
    ("064550", "바이오니아"),
    ("089030", "테크윙"),
    ("060250", "NHN KCP"),
    ("166090", "하나머티리얼즈"),
    ("036830", "솔브레인홀딩스"),
    ("084370", "유진테크"),
    ("039200", "오스코텍"),
    ("048410", "현대바이오"),
    ("041190", "우리기술투자"),
    ("064760", "티씨케이"),
    ("025900", "동화기업"),
    ("025980", "아난티"),
    ("137400", "피엔티"),
    ("290650", "엘앤씨바이오"),
    ("237690", "에스티팜"),
    ("226950", "올릭스"),
    ("140860", "파크시스템스"),
    ("215200", "메가스터디교육"),
    ("230360", "에코마케팅"),
    ("032190", "다우데이타"),
    ("074600", "원익QnC"),
    ("272290", "이녹스첨단소재"),
    ("030520", "한글과컴퓨터"),
    ("095700", "제넥신"),
    ("253450", "스튜디오드래곤"),
    ("064290", "인텍플러스"),
    ("108860", "셀바스AI"),
    ("376300", "디어유"),
    ("131970", "두산테스나"),
    ("035760", "CJ ENM"),
    ("078340", "컴투스"),
    ("194480", "데브시스터즈"),
    ("089970", "에이피티씨"),
    ("183300", "코미코"),
    ("036200", "유니셈"),
    ("033500", "동성화인텍"),
    ("080220", "제주반도체"),
    ("121600", "나노신소재"),
    ("382840", "원준"),
    ("052400", "코나아이"),
    ("060280", "큐렉소"),
    ("205470", "휴마시스"),
    ("053800", "안랩"),
    ("417200", "LS머트리얼즈"),
    ("064260", "다날"),
    ("319660", "피에스케이"),
    ("319400", "현대무벡스"),
    ("222080", "씨아이에스"),
    ("018290", "브이티"),
    ("060720", "KH바텍"),
    ("084850", "아이티엠반도체"),
    ("114810", "한솔아이원스"),
    ("099320", "쎄트렉아이"),
    ("044340", "위닉스"),
    ("122640", "예스티"),
    ("053610", "프로텍"),
    ("136540", "윈스"),
    ("053030", "바이넥스"),
    ("090360", "로보스타"),
    ("049070", "인탑스"),
    ("032300", "한국파마"),
    ("041960", "코미팜"),
]


CFG = MarketConfig(
    market="KOSDAQ",
    lookback_days=420,
    universe_size=200,
    min_price=1_000.0,
    min_adv20=2_000_000_000.0,
    min_final_score=70.0,
    min_rs_rank=70.0,
    min_close_to_52w_high_ratio=0.80,
    entry_volume_multiplier=1.4,
    pullback_volume_multiplier=1.0,
    fixed_stop_pct=0.10,
    max_risk_to_stop=0.10,
    max_atr_pct=0.12,
    max_close_to_ma50_ratio=1.35,
    max_entry_extension_pct=0.05,
    stop_atr_multiple=2.0,
    structure_stop_atr_buffer=0.5,
    trailing_atr_multiple=2.5,
    min_market_regime_score=55.0,
    benchmark_tickers=["229200", "232080"],
)


def krx_date(value: str) -> str:
    return value.replace("-", "")


def resolve_latest_trading_date(end_date: str) -> str:
    current = date.fromisoformat(end_date)
    for _ in range(1095):
        try:
            df = stock.get_market_ohlcv_by_date(krx_date(current.isoformat()), krx_date(current.isoformat()), "035720")
        except Exception:
            df = pd.DataFrame()
        if not df.empty:
            return current.isoformat()
        current = current - timedelta(days=1)
    raise RuntimeError(f"No available KRX trading date found before {end_date}.")


def fetch_naver_listing() -> pd.DataFrame:
    rows = []
    seen = set()
    patterns = [
        re.compile(r'href=["\'][^"\']*/item/main\.naver\?code=(\d{6})[^"\']*["\'][^>]*>([^<]+)</a>'),
        re.compile(r'"itemCode"\s*:\s*"(\d{6})".{0,500}?"stockName"\s*:\s*"([^"]+)"'),
        re.compile(r'"itemcode"\s*:\s*"(\d{6})".{0,500}?"itemname"\s*:\s*"([^"]+)"'),
    ]
    headers = {"User-Agent": "Mozilla/5.0"}

    for page in range(1, 80):
        url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok=1&page={page}"
        try:
            with urlopen(Request(url, headers=headers), timeout=30) as response:
                html = response.read().decode("utf-8", errors="ignore")
        except Exception as exc:
            print(f"[WARN] failed to fetch Naver KOSDAQ page {page}: {exc}")
            continue

        matches = []
        for pattern in patterns:
            matches = pattern.findall(html)
            if matches:
                break
        if not matches:
            break
        for ticker, name in matches:
            name = name.strip()
            if ticker in seen:
                continue
            if any(keyword.lower() in name.lower() for keyword in EXCLUDE_NAME_KEYWORDS):
                continue
            seen.add(ticker)
            rows.append({"ticker": ticker, "security_name": name})

    return pd.DataFrame(rows, columns=["ticker", "security_name"])


def fetch_base_listing() -> pd.DataFrame:
    print("[WARN] KOSDAQ listing providers returned no rows; using built-in liquid ticker fallback.")
    return pd.DataFrame(
        [{"ticker": ticker, "security_name": name} for ticker, name in BASE_KOSDAQ_TICKERS],
        columns=["ticker", "security_name"],
    ).drop_duplicates("ticker")


def fetch_universe(end_date: str) -> pd.DataFrame:
    tickers = stock.get_market_ticker_list(krx_date(end_date), market="KOSDAQ")
    if not tickers:
        print("[WARN] pykrx KOSDAQ ticker list is empty; using Naver Finance listing fallback.")
        naver = fetch_naver_listing()
        if not naver.empty:
            return naver
        return fetch_base_listing()

    rows = []
    for ticker in tickers:
        name = stock.get_market_ticker_name(ticker)
        if any(keyword.lower() in name.lower() for keyword in EXCLUDE_NAME_KEYWORDS):
            continue
        rows.append({"ticker": ticker, "security_name": name})
    if not rows:
        return fetch_base_listing()
    return pd.DataFrame(rows, columns=["ticker", "security_name"])


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame(columns=OHLCV_COLUMNS + ["value"])
    renamed = df.rename(columns={
        "시가": "open",
        "고가": "high",
        "저가": "low",
        "종가": "close",
        "거래량": "volume",
        "거래대금": "value",
    }).copy()
    keep = [c for c in OHLCV_COLUMNS + ["value"] if c in renamed.columns]
    return renamed[keep].dropna()


def download_one(ticker: str, start: str, end: str) -> pd.DataFrame:
    try:
        df = stock.get_market_ohlcv_by_date(krx_date(start), krx_date(end), ticker, adjusted=True)
    except TypeError:
        df = stock.get_market_ohlcv_by_date(krx_date(start), krx_date(end), ticker)
    return normalize_ohlcv(df)


def download_ohlcv(tickers: List[str], start: str, end: str) -> Dict[str, pd.DataFrame]:
    result: Dict[str, pd.DataFrame] = {}
    for ticker in sorted(set(tickers)):
        try:
            df = download_one(ticker, start, end)
        except Exception as exc:
            print(f"[WARN] failed to download {ticker}: {exc}")
            continue
        if all(c in df.columns for c in OHLCV_COLUMNS) and len(df) > 0:
            result[ticker] = df
    return result


def select_top_by_adv(end_date: str) -> pd.DataFrame:
    effective_end_date = resolve_latest_trading_date(end_date)
    universe = fetch_universe(effective_end_date)
    start = (date.fromisoformat(effective_end_date) - timedelta(days=45)).isoformat()

    rows = []
    names = universe.set_index("ticker")["security_name"].to_dict()
    tickers = universe["ticker"].tolist()
    for index in range(0, len(tickers), 50):
        chunk = tickers[index:index + 50]
        print(f"[INFO] KOSDAQ download chunk {index // 50 + 1}: {len(chunk)}")
        recent = download_ohlcv(chunk, start, effective_end_date)
        for ticker, df in recent.items():
            if len(df) < 20:
                continue
            tail = df.tail(20)
            trading_value = tail["value"] if "value" in tail.columns else tail["close"] * tail["volume"]
            adv = trading_value.mean()
            last_close = tail["close"].iloc[-1]
            if pd.isna(adv) or pd.isna(last_close) or last_close < CFG.min_price:
                continue
            rows.append({"ticker": ticker, "security_name": names.get(ticker, ""), "close": float(last_close), "adv": float(adv)})

    if not rows:
        raise RuntimeError(
            "No KOSDAQ symbols had usable recent OHLCV/ADV data. "
            f"trading_date={effective_end_date} universe_size={len(tickers)}"
        )

    return pd.DataFrame(rows, columns=SELECTED_COLUMNS).sort_values("adv", ascending=False).head(CFG.universe_size).reset_index(drop=True)


def run(end_date: str) -> dict:
    effective_end_date = resolve_latest_trading_date(end_date)
    selected = select_top_by_adv(effective_end_date)
    tickers = selected["ticker"].tolist()
    names = selected.set_index("ticker")["security_name"].to_dict()
    all_tickers = sorted(set(tickers + CFG.benchmark_tickers))

    ohlcv = download_ohlcv(all_tickers, start_date(effective_end_date, CFG.lookback_days), effective_end_date)
    missing_benchmarks = [ticker for ticker in CFG.benchmark_tickers if ticker not in ohlcv]
    if missing_benchmarks:
        raise RuntimeError(f"KOSDAQ benchmark data is missing: {', '.join(missing_benchmarks)}")

    primary = add_technical_features(ohlcv[CFG.benchmark_tickers[0]])
    secondary = add_technical_features(ohlcv[CFG.benchmark_tickers[1]])

    rows = []
    for ticker in tickers:
        if ticker not in ohlcv:
            continue
        df = add_technical_features(ohlcv[ticker])
        if len(df) < 260:
            continue
        last = df.iloc[-1]
        if last["close"] < CFG.min_price or last["adv20"] < CFG.min_adv20:
            continue
        row = latest_feature_row(ticker, names.get(ticker, ""), df, primary, secondary, CFG)
        if row:
            rows.append(row)

    scored = score_universe(pd.DataFrame(rows)) if rows else pd.DataFrame()
    market_regime = build_market_regime(primary, secondary, scored)
    candidates = build_candidates(scored, CFG, market_regime) if not scored.empty else pd.DataFrame()
    return {
        "market": CFG.market,
        "run_date": effective_end_date,
        "selected": selected,
        "scored": scored,
        "candidates": candidates,
        "market_bullish": market_regime["market_bullish"],
        "market_regime_score": market_regime["score"],
        "market_exposure": market_regime["exposure"],
    }
