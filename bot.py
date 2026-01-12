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
FINNHUB_KEY = os.environ["FINNHUB_API_KEY"]
EVENT_NAME = os.environ.get("GITHUB_EVENT_NAME", "")

TZ = ZoneInfo("Europe/Berlin")

STATE_FILE = "state.json"
COOLDOWN_HOURS = 6

# confirmed-only: Gerüchte blocken
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
MY_STOCKS = ["UAA"]  # Under Armour

# Top-25 Aktien (praktisch & stabil)
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

# GitHub Actions läuft alle 5 Minuten → Trigger-Fenster
TRIGGER_WINDOW_MINUTES = 5  # 0-4 nach voller Stunde

# =========================
# Telegram
# =========================
def send(text: str):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    r = requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": True
    }, timeout=25)
    r.raise_for_status()

# =========================
# State (Cooldown + Dedupe + "nur einmal pro Uhrzeit")
# =========================
def load_state():
    if not os.path.exists(STATE_FILE):
        return {"last_alert": {}, "seen_news": {}, "last_run_marker": {}}
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"last_alert": {}, "seen_news": {}, "last_run_marker": {}}

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

def purge_seen_news(state, ttl_hours=48):
    cutoff = now_ts() - ttl_hours * 3600
    state["seen_news"] = {k: v for k, v in state.get("seen_news", {}).items() if v >= cutoff}

def run_marker_key(tag: str, dt_local: datetime):
    # example: "midday:2026-01-12"
    return f"{tag}:{dt_local.strftime('%Y-%m-%d')}"

def already_ran(state, marker: str):
    return state.get("last_run_marker", {}).get(marker) is True

def mark_ran(state, marker: str):
    state.setdefault("last_run_marker", {})[marker] = True

# =========================
# HTTP helpers (retry)
# =========================
def get_with_retry(url, params=None, timeout=25, tries=3, backoff=2):
    last_exc = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code == 429:
                # Rate limit → warten und nochmal
                time.sleep(backoff * (i + 1))
                continue
            r.raise_for_status()
            return r
        except Exception as e:
            last_exc = e
            time.sleep(backoff * (i + 1))
    raise last_exc

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
    r = get_with_retry(url, params=params, timeout=25, tries=3, backoff=2)
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
    lines = ["🪙 Top 15 Krypto (Market Cap)"]
    for c in data:
        lines.append(f"• {c['name']} ({c['symbol'].upper()}): {c.get('price_change_percentage_24h',0):+.2f}% (24h)")
    return lines, data

# =========================
# Yahoo Finance (1 Request: Top25 + UAA)
# =========================
def yahoo_quotes(symbols):
    url = "https://query1.finance.yahoo.com/v7/finance/quote"
    params = {"symbols": ",".join(symbols)}
    r = get_with_retry(url, params=params, timeout=25, tries=3, backoff=3)
    return r.json()["quoteResponse"]["result"]

def usd_to_eur(usd):
    fxr = get_with_retry(
        "https://api.exchangerate.host/latest",
        params={"base": "USD", "symbols": "EUR"},
        timeout=25,
        tries=3,
        backoff=2
    ).json()
    return usd * fxr["rates"]["EUR"]

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
# Finnhub News
# =========================
def finnhub_market_news():
    url = "https://finnhub.io/api/v1/news"
    params = {"category": "general", "token": FINNHUB_KEY}
    r = get_with_retry(url, params=params, timeout=25, tries=3, backoff=2)
    return r.json()

# =========================
# Big News Alerts (confirmed + impact + cooldown)
# =========================
def detect_big_news_alerts(state, stock_quotes, top_crypto_list):
    alerts = []

    stock_move = {q.get("symbol"): (q.get("regularMarketChangePercent") or 0.0) for q in stock_quotes}
    top_crypto_move = {(c.get("symbol") or "").upper(): (c.get("price_change_percentage_24h") or 0.0) for c in top_crypto_list}

    news = finnhub_market_news()[:50]
    for item in news:
        headline = item.get("headline", "") or ""
        url = item.get("url", "") or ""
        source = item.get("source", "") or ""
        if not headline.strip():
            continue
        if is_rumor(headline):
            continue

        fp = news_fingerprint(headline, url)
        if state.get("seen_news", {}).get(fp):
            continue

        text = headline.lower()

        # Mention check (Top25 + deine Aktie)
        mentioned = None
        for sym in (MY_STOCKS + TOP25):
            if sym.lower() in text:
                mentioned = sym
                break

        # Crypto mention by ticker (Top15 only)
        if not mentioned:
            for sym in list(top_crypto_move.keys()):
                if sym.lower() in text:
                    mentioned = sym
                    break

        if not mentioned:
            # nicht in unserem Universum → ignorieren
            continue

        # Impact muss deutlich sein
        move_val = 0.0
        impact_ok = False
        is_stock = mentioned in stock_move

        if is_stock:
            move_val = stock_move.get(mentioned, 0.0)
            impact_ok = (move_val >= STOCK_POS) or (move_val <= STOCK_NEG)
        else:
            move_val = top_crypto_move.get(mentioned, 0.0)
            impact_ok = (move_val >= CRYPTO_POS) or (move_val <= CRYPTO_NEG)

        if not impact_ok:
            # News ohne sichtbaren Impact → kein Alert
            state.setdefault("seen_news", {})[fp] = now_ts()
            continue

        # Cooldown pro Asset
        key = f"news:{mentioned}"
        if not cooldown_ok(state, key):
            state.setdefault("seen_news", {})[fp] = now_ts()
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
        state.setdefault("seen_news", {})[fp] = now_ts()

    purge_seen_news(state, ttl_hours=48)
    return alerts

# =========================
# 15:00 Geschäftspartner (max 3 Ideen, Risiko ~50/50)
# =========================
def partner_ideas(stock_quotes, top_crypto_list):
    # Stocks candidates: positive movers but not extreme (kein Zock)
    stocks = []
    for q in stock_quotes:
        sym = q.get("symbol")
        if sym not in TOP25 and sym not in MY_STOCKS:
            continue
        chg = q.get("regularMarketChangePercent") or 0.0
        if 1.0 <= chg <= 8.5:
            stocks.append((sym, q.get("shortName", sym), chg))
    stocks.sort(key=lambda x: x[2], reverse=True)

    # Crypto candidates: positive but not crazy
    cryptos = []
    for c in top_crypto_list:
        sym = (c.get("symbol") or "").upper()
        chg = c.get("price_change_percentage_24h") or 0.0
        if 1.0 <= chg <= 10.0:
            cryptos.append((sym, c.get("name", sym), chg))
    cryptos.sort(key=lambda x: x[2], reverse=True)

    ideas = []
    # Mix: 2 Stocks + 1 Crypto (wenn verfügbar)
    for s in stocks[:2]:
        sym, name, chg = s
        ideas.append({
            "type": "Aktie",
            "name": name,
            "ticker": sym,
            "chg": chg,
            "why": "Momentum + Markt bestätigt den Move (ohne extremen Zock).",
            "risk": "Kann nach starkem Tag konsolidieren; Positionsgröße klein halten."
        })

    if cryptos:
        sym, name, chg = cryptos[0]
        ideas.append({
            "type": "Krypto",
            "name": name,
            "ticker": sym,
            "chg": chg,
            "why": "Relative Stärke im Top-Segment + Trend im Gesamtmarkt.",
            "risk": "Krypto bleibt volatil; Stop/Plan vorher festlegen."
        })

    # Max 3
    ideas = ideas[:3]

    lines = ["🧠 Geschäftspartner-Update (15:00)", "", "⚠️ Keine Finanzberatung – nur Research/Ideen.", ""]
    if not ideas:
        lines.append("Heute keine sauberen Setups (zu wenig klare Signale ohne Zock).")
        return "\n".join(lines)

    for i, idea in enumerate(ideas, start=1):
        lines.append(f"📌 Idee {i}: {idea['name']} ({idea['ticker']}) – {idea['type']}")
        lines.append(f"• Bewegung: {idea['chg']:+.2f}%")
        lines.append(f"• Warum: {idea['why']}")
        lines.append(f"• Risiko: {idea['risk']}")
        lines.append("")
    return "\n".join(lines).strip()

# =========================
# Reports
# =========================
def build_market_report(title: str, quotes, top_crypto_list):
    my_lines, _ = my_crypto_lines()
    top_lines, _ = top15_crypto_lines()

    lines = [title, ""]
    lines += my_lines
    lines.append("")
    lines += my_stock_lines_from_quotes(quotes)
    lines.append("")
    lines += top_lines
    lines.append("")
    lines += top25_stock_highlights(quotes)
    return "\n".join(lines)

def run_full_probelauf(state):
    # Echte Abfragen (Yahoo + CoinGecko + Finnhub)
    quotes = yahoo_quotes(TOP25 + MY_STOCKS)
    _, top_crypto = top15_crypto_lines()

    # 1) Probelauf Marktbericht
    report = build_market_report("🧪 PROBELAUF – Marktbericht (wie 12/18 Uhr)", quotes, top_crypto)
    send(report)

    # 2) Probelauf Geschäftspartner (wie 15 Uhr)
    partner_msg = partner_ideas(quotes, top_crypto)
    send(partner_msg)

    # 3) Probelauf Big News Scan (wenn was passt)
    alerts = detect_big_news_alerts(state, quotes, top_crypto)
    if alerts:
        msg = "🧪 PROBELAUF – BIG NEWS ALERTS\n\n" + "\n\n---\n\n".join(alerts[:3])
        send(msg)
    else:
        send("🧪 PROBELAUF – BIG NEWS\n\nKeine passenden confirmed Big-News mit starkem Kurs-Impact im letzten Scan.")

# =========================
# Main
# =========================
def main():
    state = load_state()
    dt_local = datetime.now(TZ)
    hour = dt_local.hour
    minute = dt_local.minute

    manual_run = EVENT_NAME == "workflow_dispatch"

    try:
        # Manuell: echter Probelauf (wie du wolltest)
        if manual_run:
            run_full_probelauf(state)
            save_state(state)
            return

        # Big News Scan: alle 15 Minuten
        if minute % 15 == 0:
            quotes = yahoo_quotes(TOP25 + MY_STOCKS)
            _, top_crypto = top15_crypto_lines()
            alerts = detect_big_news_alerts(state, quotes, top_crypto)
            if alerts:
                msg = "🚨 BIG NEWS ALERTS\n\n" + "\n\n---\n\n".join(alerts[:3])
                send(msg)

        # 12:00 / 15:00 / 18:00 (Trigger-Fenster: minute 0-4)
        if minute < TRIGGER_WINDOW_MINUTES:
            if hour == 12:
                marker = run_marker_key("midday", dt_local)
                if not already_ran(state, marker):
                    quotes = yahoo_quotes(TOP25 + MY_STOCKS)
                    _, top_crypto = top15_crypto_lines()
                    send(build_market_report("🕛 Markt-Mittagsupdate (12:00)", quotes, top_crypto))
                    mark_ran(state, marker)

            elif hour == 15:
                marker = run_marker_key("partner", dt_local)
                if not already_ran(state, marker):
                    quotes = yahoo_quotes(TOP25 + MY_STOCKS)
                    _, top_crypto = top15_crypto_lines()
                    send(partner_ideas(quotes, top_crypto))
                    mark_ran(state, marker)

            elif hour == 18:
                marker = run_marker_key("evening", dt_local)
                if not already_ran(state, marker):
                    quotes = yahoo_quotes(TOP25 + MY_STOCKS)
                    _, top_crypto = top15_crypto_lines()
                    send(build_market_report("🕕 Tagesabschluss (18:00)", quotes, top_crypto))
                    mark_ran(state, marker)

        save_state(state)

    except Exception as e:
        send(f"⚠️ Bot-Fehler:\n{type(e).__name__}: {e}")
        try:
            save_state(state)
        except Exception:
            pass

if __name__ == "__main__":
    main()
