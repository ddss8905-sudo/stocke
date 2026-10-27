"""Durable delivery claims. Ambiguous network outcomes are never blindly resent."""

import os
import time
from datetime import datetime
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import requests


def configured():
    return bool(os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"))


def event_message(market, event):
    digits = 2 if market == "NASDAQ" else 0
    price = lambda value: f"{value:,.{digits}f}"
    zone = ZoneInfo("America/New_York" if market == "NASDAQ" else "Asia/Seoul")
    timestamp = lambda value: datetime.fromisoformat(value).astimezone(zone).strftime("%m-%d %H:%M %Z")
    url = "https://stocke-five.vercel.app/?" + urlencode({"market": market, "strategy": "gap224", "date": event["session_date"]})
    return (f"[224일선 갭 돌파] {market}\n{event['security_name']} ({event['ticker']})\n"
            f"거래일 {event['session_date']} · {'지연 확인' if event['late_detection'] else '장초 발견'}\n"
            f"시가 {price(event['open_price'])} / 전일 종가 {price(event['previous_close'])}\n"
            f"시초가 기준 224일선 {price(event['ma224_open'])}\n"
            f"갭 +{event['gap_pct']:.2%} / 224일선 대비 +{event['breakout_pct']:.2%}\n"
            f"조회 가격 {price(event.get('current_price', event['open_price']))}\n"
            f"발견 {timestamp(event['detected_at'])}\n"
            f"가격 조회 {timestamp(event.get('updated_at') or event.get('quote_at') or event['detected_at'])}\n"
            f"{url}\n시초가로 체결 가능하다는 의미가 아니며 매수 주문을 실행하지 않습니다.")


def send_message(text):
    # The token is part of Telegram's URL; never log response URLs or exception text.
    url = "https://api.telegram.org/bot" + os.environ["TELEGRAM_BOT_TOKEN"] + "/sendMessage"
    try:
        response = requests.post(url, json={"chat_id": os.environ["TELEGRAM_CHAT_ID"], "text": text,
                                            "link_preview_options": {"is_disabled": True}}, timeout=(10, 20))
        body = response.json()
    except (requests.RequestException, ValueError):
        return {"status": "unknown"}
    if not isinstance(body, dict):
        return {"status": "unknown"}
    if response.ok and body.get("ok"):
        message_id = (body.get("result") or {}).get("message_id")
        return {"status": "sent", "message_id": message_id} if isinstance(message_id, int) else {"status": "unknown"}
    code = int(body.get("error_code") or response.status_code)
    return {"status": "retry" if code == 429 else "failed", "error_code": code,
            "retry_after": max(int((body.get("parameters") or {}).get("retry_after") or 30), 1)}


def deliver(store, market, event, now_epoch=None):
    if not configured():
        return "not_configured"
    epoch = time.time() if now_epoch is None else now_epoch
    path = f"{market}/{event['session_date']}/deliveries/{event['ticker']}.json"
    previous = store.get(path)
    if previous:
        if previous["status"] not in ("retry", "failed"):
            return previous["status"]
        if previous.get("next_retry", 0) > epoch:
            return previous["status"]
        if previous.get("attempts", 0) >= 5:
            return "failed"
        # Supported workflows serialize by market; create-only claims guard first sends.
        claim = {"status": "sending", "attempts": previous.get("attempts", 1) + 1}
        store.put(path, claim)
    else:
        claim = {"status": "sending", "attempts": 1}
        if not store.put(path, claim, create=True):
            return "sending"
    result = send_message(event_message(market, event))
    result.update({"attempts": claim["attempts"], "next_retry": epoch + result.get("retry_after", 300)})
    store.put(path, result)
    return result["status"]
