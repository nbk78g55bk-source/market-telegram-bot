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

# confirmed-only
RUMOR_WORDS = [
    "rumor", "reportedly", "in talks", "considering", "may", "could",
    "angeblich", "gerücht", "soll", "könnte", "in gesprächen", "erwägt", "insider",
    "sources said", "people familiar", "unconfirmed"
]

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

TOP25 = [
    "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","BRK-B","LLY","AVGO",
    "JPM","V","WMT","XOM","UNH","MA","PG","JNJ","HD","ORCL",
    "COST","MRK","BAC","KO","PEP"
]

STOCK_POS = 7.0
STOCK_NEG = -7.0
CRYPTO_POS = 6.0
CRYPTO_NEG = -6.0

TRIGGER_WINDOW_MINUTES = 5  # runs every 5 min → window 0-4

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
# State
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

def run_marker_key(tag: str, dt_local: datetime):
    return f"{tag}:{dt_local.strftime('%Y-%m-%d')}"

def already_ran(state, marker: str):
    return state.get("last_run_marker", {}).get(marker) is True

def mark_ran(state, marker: str):
    state.setdefault("last_run_marker", {})[marker] = True

def purge_seen_news(state, ttl_hours=48):
    cutoff = now_ts() - ttl_hours * 3600
    state["seen_news"] = {k: v for k, v in state.get("seen_news", {}).items() if v >= cutoff}

# =========================
# Helpers
# =========================
def is_rumor(text: str):
    t = (text or "").lower()
    return any(w in t for w in RUMOR_WORDS)

def news_fingerprint(headline: str, url: str):
    base = (headline or "") + "|" + (url or "")
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:20]

def get_with_retry(url, params=None, timeout=25, tries=3, backoff=2):
    last_exc = None
    last_status = None
    for i in range(tries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            last_status = r.status_code
            if r.status_code == 429:
                time.sleep(backoff * (i + 1))
                last_exc = RuntimeError(f"HTTP 429 Too Many Requests for {url}")
                continue
            r.raise_for_status()
            return r
        except Exception as e:
            last_exc = e
            time.sleep(backoff * (i + 1))
    if last_exc is None:
        raise RuntimeError(f"Request failed for {url} (last_status={last_status})")
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
    return lines

def top15_crypto_lines():
    data = coingecko_markets(vs="eur", per_page=15)
    lines = ["🪙 Top 15 Krypto (Market Cap)"]
    for c in data:
        lines.append(f"• {c['name']} ({c['symbol'].upper()}): {c.get('price_change_percentage_24h',0):+.2f}% (24h)")
    return lines, data

# =========================
# FX USD→EUR (for Finnhub US quotes)
# =========================
def usd_to_eur_rate():
    # 1) Versuch: exchangerate.host
    try:
        r = get_with_retry(
            "https://api.exchangerate.host/latest",
            params={"base": "USD", "symbols": "EUR"},
            timeout=25,
            tries=2,
            backoff=2
        ).json()
        if isinstance(r, dict) and "rates" in r and "EUR" in r["rates"]:
            return float(r["rates"]["EUR"])
    except Exception:
        pass

    # 2) Fallback: frankfurter.app (ECB-basiert)
    r2 = get_with_retry(
        "https://api.frankfurter.app/latest",
        params={"from": "USD", "to": "EUR"},
        timeout=25,
        tries=3,
        backoff=2
    ).json()

    if "rates" in r2 and "EUR" in r2["rates"]:
        return float(r2["rates"]["EUR"])

    raise RuntimeError("FX rate USD->EUR not available")

# =========================
# Finnhub Quotes (PRIMARY)
# =========================
def finnhub_quote(symbol: str):
    url = "https://finnhub.io/api/v1/quote"
    params = {"symbol": symbol, "token": FINNHUB_KEY}
    r = get_with_retry(url, params=params, timeout=25, tries=3, backoff=2).json()
    # Finnhub fields: c=current, pc=prev close
    c = r.get("c")
    pc = r.get("pc")
    if c is None or pc in (None, 0):
        return None
    chg_pct = ((c - pc) / pc) * 100.0
    return {"symbol": symbol, "price_usd": c, "chg_pct": chg_pct}

def get_stock_quotes_primary(symbols):
    # One FX fetch per run
    fx = usd_to_eur_rate()
    out = []
    for sym in symbols:
        q = finnhub_quote(sym)
        if not q:
            continue
        out.append({
            "symbol": sym,
            "price_eur": q["price_usd"] * fx,
            "chg_pct": q["chg_pct"]
        })
    return out

# =========================
# Yahoo fallback (SECONDARY)
# =========================
def yahoo_quotes(symbols):
    url = "https://query1.finance.yahoo.com/v7/finance/quote"
    params = {"symbols": ",".join(symbols)}
    r = get_with_retry(url, params=params, timeout=25, tries=2, backoff=5)
    return r.json()["quoteResponse"]["result"]

def fallback_stock_quotes(symbols):
    # returns list of dicts: symbol, price_eur?, chg_pct
    try:
        raw = yahoo_quotes(symbols)
    except Exception:
        return []

    quotes = []
    # FX only if USD
    fx = None
    for q in raw:
        sym = q.get("symbol")
        if sym not in symbols:
            continue
        price = q.get("regularMarketPrice")
        chg = q.get("regularMarketChangePercent") or 0.0
        cur = q.get("currency", "USD")
        if price is None:
            continue
        if cur == "USD":
            if fx is None:
                fx = usd_to_eur_rate()
            price = price * fx
        quotes.append({"symbol": sym, "price_eur": price, "chg_pct": chg})
    return quotes

def get_stock_quotes(symbols):
    primary = get_stock_quotes_primary(symbols)
    have = {q["symbol"] for q in primary}
    missing = [s for s in symbols if s not in have]
    if missing:
        primary += fallback_stock_quotes(missing)
    return primary

# =========================
# Stock lines / highlights
# =========================
def my_stock_lines(stock_quotes):
    lines = ["📦 Deine Aktie"]
    q = next((x for x in stock_quotes if x["symbol"] == "UAA"), None)
    if not q:
        lines.append("• Under Armour (UAA): keine Daten")
        return lines
    lines.append(f"• Under Armour (UAA): €{q['price_eur']:.2f} | {q['chg_pct']:+.2f}%")
    return lines

def top25_highlights(stock_quotes):
    lines = ["🏢 Top 25 Aktien – Highlights"]
    filtered = [q for q in stock_quotes if q["symbol"] in TOP25]
    movers = sorted(filtered, key=lambda x: abs(x["chg_pct"]), reverse=True)[:5]
    for q in movers:
        lines.append(f"• {q['symbol']}: {q['chg_pct']:+.2f}%")
    return lines

def stock_move_map(stock_quotes):
    return {q["symbol"]: q["chg_pct"] for q in stock_quotes}

# =========================
# Finnhub News
# =========================
def finnhub_market_news():
    url = "https://finnhub.io/api/v1/news"
    params = {"category": "general", "token": FINNHUB_KEY}
    r = get_with_retry(url, params=params, timeout=25, tries=3, backoff=2).json()
    return r[:50]

# =========================
# Big News Alerts
# =========================
def detect_big_news_alerts(state, stock_quotes, top_crypto_list):
    alerts = []
    stock_move = stock_move_map(stock_quotes)
    top_crypto_move = {(c.get("symbol") or "").upper(): (c.get("price_change_percentage_24h") or 0.0) for c in top_crypto_list}

    for item in finnhub_market_news():
        headline = (item.get("headline") or "").strip()
        url = (item.get("url") or "").strip()
        source = (item.get("source") or "").strip()

        if not headline:
            continue
        if is_rumor(headline):
            continue

        fp = news_fingerprint(headline, url)
        if state.get("seen_news", {}).get(fp):
            continue

        text = headline.lower()

        mentioned = None
        for sym in (MY_STOCKS + TOP25):
            if sym.lower() in text:
                mentioned = sym
                break
        if not mentioned:
            for sym in list(top_crypto_move.keys()):
                if sym.lower() in text:
                    mentioned = sym
                    break
        if not mentioned:
            continue

        move_val = 0.0
        impact_ok = False
        if mentioned in stock_move:
            move_val = stock_move.get(mentioned, 0.0)
            impact_ok = (move_val >= STOCK_POS) or (move_val <= STOCK_NEG)
        else:
            move_val = top_crypto_move.get(mentioned, 0.0)
            impact_ok = (move_val >= CRYPTO_POS) or (move_val <= CRYPTO_NEG)

        # markieren, damit wir denselben Artikel nicht immer neu scannen
        state.setdefault("seen_news", {})[fp] = now_ts()

        if not impact_ok:
            continue

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

    purge_seen_news(state, ttl_hours=48)
    return alerts

# =========================
# 15:00 Geschäftspartner (max 3 Ideen, 50/50)
# =========================
def partner_message(stock_quotes, top_crypto_list):
    lines = ["🧠 Geschäftspartner-Update (15:00)", "", "⚠️ Keine Finanzberatung – nur Research/Ideen.", ""]

    # Stocks: moderate positive movers
    stocks = [q for q in stock_quotes if (q["symbol"] in TOP25 or q["symbol"] in MY_STOCKS) and 1.0 <= q["chg_pct"] <= 8.5]
    stocks.sort(key=lambda x: x["chg_pct"], reverse=True)

    # Crypto: moderate positive movers
    cryptos = []
    for c in top_crypto_list:
        sym = (c.get("symbol") or "").upper()
        name = c.get("name", sym)
        chg = c.get("price_change_percentage_24h") or 0.0
        if 1.0 <= chg <= 10.0:
            cryptos.append((sym, name, chg))
    cryptos.sort(key=lambda x: x[2], reverse=True)

    ideas = []
    for s in stocks[:2]:
        ideas.append(("Aktie", s["symbol"], s["chg_pct"],
                      "Momentum + Markt bestätigt den Move (ohne extremen Zock).",
                      "Kann nach starkem Tag konsolidieren; Positionsgröße klein halten."))

    if cryptos:
        sym, name, chg = cryptos[0]
        ideas.append(("Krypto", sym, chg,
                      "Relative Stärke im Top-Segment + Trend im Gesamtmarkt.",
                      "Krypto bleibt volatil; Stop/Plan vorher festlegen."))

    ideas = ideas[:3]
    if not ideas:
        lines.append("Heute keine sauberen Setups (zu wenig klare Signale ohne Zock).")
        return "\n".join(lines)

    for i, (typ, sym, chg, why, risk) in enumerate(ideas, start=1):
        lines.append(f"📌 Idee {i}: {sym} – {typ}")
        lines.append(f"• Bewegung: {chg:+.2f}%")
        lines.append(f"• Warum: {why}")
        lines.append(f"• Risiko: {risk}")
        lines.append("")
    return "\n".join(lines).strip()

# =========================
# Reports
# =========================
def build_market_report(title: str, stock_quotes):
    top_lines, top_crypto = top15_crypto_lines()
    lines = [title, ""]
    lines += my_crypto_lines()
    lines.append("")
    lines += my_stock_lines(stock_quotes)
    lines.append("")
    lines += top_lines
    lines.append("")
    lines += top25_highlights(stock_quotes)
    return "\n".join(lines), top_crypto

def run_probelauf(state):
    stock_quotes = get_stock_quotes(TOP25 + MY_STOCKS)
    report, top_crypto = build_market_report("🧪 PROBELAUF – Marktbericht (wie 12/18 Uhr)", stock_quotes)
    send(report)
    send(partner_message(stock_quotes, top_crypto))
    alerts = detect_big_news_alerts(state, stock_quotes, top_crypto)
    if alerts:
        send("🧪 PROBELAUF – BIG NEWS ALERTS\n\n" + "\n\n---\n\n".join(alerts[:3]))
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

    manual = EVENT_NAME == "workflow_dispatch"

    try:
        if manual:
            run_probelauf(state)
            save_state(state)
            return

        # every 15 min: big news scan
        if minute % 15 == 0:
            stock_quotes = get_stock_quotes(TOP25 + MY_STOCKS)
            _, top_crypto = top15_crypto_lines()
            alerts = detect_big_news_alerts(state, stock_quotes, top_crypto)
            if alerts:
                send("🚨 BIG NEWS ALERTS\n\n" + "\n\n---\n\n".join(alerts[:3]))

        # 12/15/18 within minute 0-4
        if minute < TRIGGER_WINDOW_MINUTES:
            if hour == 12:
                mk = run_marker_key("midday", dt_local)
                if not already_ran(state, mk):
                    stock_quotes = get_stock_quotes(TOP25 + MY_STOCKS)
                    report, _ = build_market_report("🕛 Markt-Mittagsupdate (12:00)", stock_quotes)
                    send(report)
                    mark_ran(state, mk)

            elif hour == 15:
                mk = run_marker_key("partner", dt_local)
                if not already_ran(state, mk):
                    stock_quotes = get_stock_quotes(TOP25 + MY_STOCKS)
                    _, top_crypto = top15_crypto_lines()
                    send(partner_message(stock_quotes, top_crypto))
                    mark_ran(state, mk)

            elif hour == 18:
                mk = run_marker_key("evening", dt_local)
                if not already_ran(state, mk):
                    stock_quotes = get_stock_quotes(TOP25 + MY_STOCKS)
                    report, _ = build_market_report("🕕 Tagesabschluss (18:00)", stock_quotes)
                    send(report)
                    mark_ran(state, mk)

        save_state(state)

    except Exception as e:
        send(f"⚠️ Bot-Fehler:\n{type(e).__name__}: {e}")
        try:
            save_state(state)
        except Exception:
            pass

if __name__ == "__main__":
    main()
