import os
import json
import time
import hashlib
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

# =========================
# ENV
# =========================
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
FINNHUB_KEY = os.environ.get("FINNHUB_API_KEY", "")

TZ = ZoneInfo("Europe/Berlin")

STATE_FILE = "state.json"
COOLDOWN_HOURS = 6

# Nur "confirmed" – Gerüchte blocken
RUMOR_WORDS = [
    "rumor", "reportedly", "in talks", "considering", "may", "could",
    "angeblich", "gerücht", "soll", "könnte", "in gesprächen", "erwägt", "insider",
    "sources said", "people familiar", "unconfirmed"
]

# Deine Assets
MY_CRYPTOS = {
    "SOL": "solana",
    "ADA": "cardano",
    "SUI": "sui",
    "XRP": "ripple",
    "SHIB": "shiba-inu",
    "FET": "fetch-ai",
    "RNDR": "render-token"
}
MY_STOCKS = ["UAA"]

# Top-25 Aktien (praktisch: US Big Caps)
TOP25 = [
    "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","BRK-B","LLY","AVGO",
    "JPM","V","WMT","XOM","UNH","MA","PG","JNJ","HD","ORCL",
    "COST","MRK","BAC","KO","PEP"
]

# Schwellen: nur wenn Markt sichtbar reagiert
STOCK_POS = 7.0
STOCK_NEG = -7.0
CRYPTO_POS = 6.0
CRYPTO_NEG = -6.0

# =========================
# Telegram
# =========================
def send(text: str):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": True
    }, timeout=20)
    r.raise_for_status()

# =========================
# State (Cooldown + Dedupe)
# =========================
def load_state():
    if not os.path.exists(STATE_FILE):
        return {"last_alert": {}, "seen_news": {}}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"last_alert": {}, "seen_news": {}}

def save_state(state):
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False)

def now_ts():
    return int(time.time())

def cooldown_ok(state, key: str):
    last = state["last_alert"].get(key, 0)
    return (now_ts() - last) >= int(COOLDOWN_HOURS * 3600)

def mark_alert(state, key: str):
    state["last_alert"][key] = now_ts()

def news_fingerprint(headline: str, url: str):
    base = (headline or "") + "|" + (url or "")
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:20]

def is_rumor(text: str):
    t = (text or "").lower()
    return any(w in t for w in RUMOR_WORDS)

# =========================
# CoinGecko (EUR)
# =========================
def coingecko_markets(vs="eur", per_page=15, ids=None):
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": vs,
        "order": "market_cap_desc",
        "per_page": per_page,
        "page": 1,
        "price_change_percentage": "24h"
    }
    if ids:
        params["ids"] = ",".join(ids)
        params["per_page"] = max(per_page, len(ids))
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def my_crypto_lines():
    data = coingecko_markets(vs="eur", per_page=50, ids=list(MY_CRYPTOS.values()))
    by_id = {c["id"]: c for c in data}
    lines = ["📦 Deine Kryptos"]
    for sym, cid in MY_CRYPTOS.items():
        c = by_id.get(cid)
        if not c:
            lines.append(f"• {sym}: keine Daten")
            continue
        price = c.get("current_price", 0)
        chg = c.get("price_change_percentage_24h", 0)
        lines.append(f"• {sym}: €{price:.4f} | {chg:+.2f}% (24h)")
    return lines, by_id

def top15_crypto_lines():
    data = coingecko_markets(vs="eur", per_page=15)
    lines = ["🪙 Top 15 Kryptowährungen (Market Cap)"]
    for c in data:
        lines.append(f"• {c['name']} ({c['symbol'].upper()}): {c.get('price_change_percentage_24h',0):+.2f}% (24h)")
    return lines, data

# =========================
# Yahoo Finance (Quotes)
# =========================
def yahoo_quotes(symbols):
    url = "https://query1.finance.yahoo.com/v7/finance/quote"
    params = {"symbols": ",".join(symbols)}
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()["quoteResponse"]["result"]

def usd_to_eur(usd):
    fx = requests.get(
        "https://api.exchangerate.host/latest",
        params={"base": "USD", "symbols": "EUR"},
        timeout=20
    ).json()["rates"]["EUR"]
    return usd * fx

def my_stock_lines_from_quotes(quotes):
    lines = ["📦 Deine Aktie"]
    q = next((x for x in quotes if x.get("symbol") == "UAA"), None)
    if not q:
        lines.append("• Under Armour (UAA): keine Daten")
        return lines
    price = q.get("regularMarketPrice")
    chg = q.get("regularMarketChangePercent") or 0.0
    currency = q.get("currency", "USD")
    if currency == "USD" and price is not None:
        price = usd_to_eur(price)
    if price is None:
        lines.append("• Under Armour (UAA): keine Daten")
    else:
        lines.append(f"• Under Armour (UAA): €{price:.2f} | {chg:+.2f}%")
    return lines

def top25_stock_highlights(quotes):
    lines = ["🏢 Top 25 Aktien – Highlights"]
    filtered = [q for q in quotes if q.get("symbol") in TOP25]
    movers = sorted(filtered, key=lambda x: abs(x.get("regularMarketChangePercent") or 0), reverse=True)[:5]
    for q in movers:
        sym = q.get("symbol")
        name = q.get("shortName", sym)
        chg = q.get("regularMarketChangePercent") or 0.0
        lines.append(f"• {name} ({sym}): {chg:+.2f}%")
    return lines

# =========================
# Finnhub News (confirmed filter)
# =========================
def finnhub_market_news():
    if not FINNHUB_KEY:
        return []
    url = "https://finnhub.io/api/v1/news"
    params = {"category": "general", "token": FINNHUB_KEY}
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()

def detect_big_news_alerts(state, stock_quotes, my_crypto_by_id, top_crypto_list):
    alerts = []

    # Build quick maps for % moves
    stock_move = {q.get("symbol"): (q.get("regularMarketChangePercent") or 0.0) for q in stock_quotes}
    # crypto maps by symbol
    top_crypto_move = {(c.get("symbol") or "").upper(): (c.get("price_change_percentage_24h") or 0.0) for c in top_crypto_list}

    # News scan
    news = finnhub_market_news()[:50]
    for item in news:
        headline = item.get("headline", "")
        url = item.get("url", "")
        source = item.get("source", "")
        if is_rumor(headline):
            continue

        fp = news_fingerprint(headline, url)
        if state["seen_news"].get(fp):
            continue  # already processed

        text = (headline or "").lower()

        # crude mention check: only alert if it mentions something we track (Top25 + your stocks)
        mentioned = None
        for sym in (MY_STOCKS + TOP25):
            if sym.lower() in text:
                mentioned = sym
                break

        # also check big crypto by ticker mentions (BTC, ETH etc.)
        if not mentioned:
            for sym in list(top_crypto_move.keys()):
                if sym.lower() in text:
                    mentioned = sym
                    break

        if not mentioned:
            # ignore random news not obviously tied to tracked universe
            continue

        # impact rule: must have strong move
        impact_ok = False
        move_val = 0.0

        if mentioned in stock_move:
            move_val = stock_move.get(mentioned, 0.0)
            impact_ok = (move_val >= STOCK_POS) or (move_val <= STOCK_NEG)
        else:
            # crypto mention
            move_val = top_crypto_move.get(mentioned, 0.0)
            impact_ok = (move_val >= CRYPTO_POS) or (move_val <= CRYPTO_NEG)

        if not impact_ok:
            continue

        # cooldown per mentioned symbol
        key = f"news:{mentioned}"
        if not cooldown_ok(state, key):
            continue

        direction = "📈" if move_val > 0 else "📉"
        alerts.append(
            f"🚨 BIG NEWS (confirmed)\n"
            f"{direction} {mentioned}: {move_val:+.2f}%\n"
            f"{headline}\n"
            f"Quelle: {source}\n"
            f"{url}"
        )

        mark_alert(state, key)
        state["seen_news"][fp] = now_ts()

    # keep seen_news from growing forever
    # purge older than 48h
    cutoff = now_ts() - 48 * 3600
    state["seen_news"] = {k: v for k, v in state["seen_news"].items() if v >= cutoff}

    return alerts

# =========================
# Daily Reports
# =========================
def report_midday():
    lines = ["🕛 Markt-Mittagsupdate (12:00)", ""]
    my_lines, _ = my_crypto_lines()
    lines += my_lines
    lines.append("")

    # Stocks (1 request)
    quotes = yahoo_quotes(TOP25 + MY_STOCKS)
    lines += my_stock_lines_from_quotes(quotes)
    lines.append("")

    top_lines, _ = top15_crypto_lines()
    lines += top_lines
    lines.append("")

    lines += top25_stock_highlights(quotes)
    send("\n".join(lines))

def report_evening():
    lines = ["🕕 Tagesabschluss (18:00)", ""]
    my_lines, _ = my_crypto_lines()
    lines += my_lines
    lines.append("")

    quotes = yahoo_quotes(TOP25 + MY_STOCKS)
    lines += my_stock_lines_from_quotes(quotes)
    lines.append("")

    top_lines, _ = top15_crypto_lines()
    lines += top_lines
    lines.append("")

    lines += top25_stock_highlights(quotes)
    send("\n".join(lines))

def report_partner():
    send("🧠 Geschäftspartner-Update (15:00)\n\n📌 Als Nächstes: 1–3 Ideen + kurze Begründung")

# =========================
# Main
# =========================
def main():
    event = os.environ.get("GITHUB_EVENT_NAME", "")
    manual_run = event == "workflow_dispatch"

    # Manuelle Runs: nur Status, keine Datenabfragen
    if manual_run:
        send("✅ Workflow läuft.\n\n(Manueller Test: keine Markt-Abfragen, damit keine Rate-Limits entstehen.)")
        return

    if not FINNHUB_KEY:
        send("⚠️ FINNHUB_API_KEY fehlt.\nBitte als GitHub Secret setzen: FINNHUB_API_KEY")
        return

    state = load_state()

    now = datetime.now(TZ)
    hour = now.hour
    minute = now.minute

    try:
        # 12 / 15 / 18 Uhr
        if hour == 12 and minute == 0:
            report_midday()
        elif hour == 15 and minute == 0:
            report_partner()
        elif hour == 18 and minute == 0:
            report_evening()

        # Alle 15 Minuten: Big-News-Check
        if minute % 15 == 0:
            # Quotes (1x) + Crypto (Top15 + deine) (2 calls)
            quotes = yahoo_quotes(TOP25 + MY_STOCKS)
            _, my_by_id = my_crypto_lines()
            _, top_crypto = top15_crypto_lines()

            alerts = detect_big_news_alerts(state, quotes, my_by_id, top_crypto)
            if alerts:
                # wenn mehrere, bündeln
                msg = "🚨 BIG NEWS ALERTS\n\n" + "\n\n---\n\n".join(alerts[:3])
                send(msg)

        save_state(state)

    except Exception as e:
        send(f"⚠️ Bot-Fehler:\n{type(e).__name__}: {e}")

if __name__ == "__main__":
    main()
