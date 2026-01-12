import os
import requests
from datetime import datetime, timezone, timedelta

# =========================
# Telegram Config
# =========================
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

def send(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    r = requests.post(
        url,
        json={"chat_id": CHAT_ID, "text": text, "disable_web_page_preview": True}
    )
    r.raise_for_status()

# =========================
# Kryptos (EUR)
# =========================
MY_CRYPTOS = {
    "SOL": "solana",
    "ADA": "cardano",
    "SUI": "sui",
    "XRP": "ripple",
    "SHIB": "shiba-inu",
    "FET": "fetch-ai",
    "RNDR": "render-token"
}

def my_crypto_lines():
    ids = ",".join(MY_CRYPTOS.values())
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "eur",
        "ids": ids,
        "order": "market_cap_desc",
        "per_page": 50,
        "page": 1,
        "price_change_percentage": "24h"
    }
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()
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
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "eur",
        "order": "market_cap_desc",
        "per_page": 15,
        "page": 1,
        "price_change_percentage": "24h"
    }
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()

    lines = ["🪙 Top 15 Kryptowährungen (Market Cap)"]
    for c in data:
        name = c["name"]
        sym = c["symbol"].upper()
        chg = c.get("price_change_percentage_24h", 0)
        lines.append(f"• {name} ({sym}): {chg:+.2f}% (24h)")
    return lines

# =========================
# Aktien (Yahoo) – 1 Request
# =========================
TOP25 = [
    "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","BRK-B","LLY","AVGO",
    "JPM","V","WMT","XOM","UNH","MA","PG","JNJ","HD","ORCL",
    "COST","MRK","BAC","KO","PEP"
]
MY_STOCKS = ["UAA"]

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

def under_armour_lines_from_quotes(quotes):
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
        lines.append(f"• Under Armour (UAA): €{price:.2f} | {chg:+.2f}% (24h)")
    return lines

def top25_stock_lines_from_quotes(quotes):
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
# Main
# =========================
def main():
    now = datetime.now(timezone.utc) + timedelta(hours=1)  # MEZ
    hour = now.hour

    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    manual_run = event_name == "workflow_dispatch"

    # ✅ Manuelle Runs: KEIN Yahoo, kein CoinGecko-Spam – nur Status
    if manual_run:
        send("✅ Workflow läuft.\n\n(Manueller Test: keine Markt-Abfragen, damit keine Rate-Limits entstehen.)")
        return

    try:
        # Cron-Runs: echte Logik
        if hour in (12, 18):
            quotes = yahoo_quotes(TOP25 + MY_STOCKS)

            title = "🕛 Markt-Mittagsupdate (12:00)" if hour == 12 else "🕕 Tagesabschluss (18:00)"
            lines = [title, ""]
            lines += my_crypto_lines()
            lines.append("")
            lines += under_armour_lines_from_quotes(quotes)
            lines.append("")
            lines += top15_crypto_lines()
            lines.append("")
            lines += top25_stock_lines_from_quotes(quotes)
            send("\n".join(lines))

        elif hour == 15:
            send("🧠 Geschäftspartner-Update (15:00)\n\n📌 Als Nächstes: Ideen + kurzes Research")

        else:
            return

    except Exception as e:
        send(f"⚠️ Bot-Fehler:\n{type(e).__name__}: {e}")

if __name__ == "__main__":
    main()
