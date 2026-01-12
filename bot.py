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
        json={
            "chat_id": CHAT_ID,
            "text": text,
            "disable_web_page_preview": True
        }
    )
    r.raise_for_status()

# =========================
# Deine Kryptos (EUR)
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

# =========================
# Top 15 Kryptos (EUR)
# =========================
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
# Aktien – Under Armour (EUR)
# =========================
def under_armour_lines():
    # Yahoo Finance Quote API
    url = "https://query1.finance.yahoo.com/v7/finance/quote"
    params = {"symbols": "UAA"}
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    res = r.json()["quoteResponse"]["result"]

    lines = ["📦 Deine Aktie"]
    if not res:
        lines.append("• Under Armour (UAA): keine Daten")
        return lines

    q = res[0]
    price = q.get("regularMarketPrice")
    chg = q.get("regularMarketChangePercent")
    currency = q.get("currency", "USD")

    # einfache USD→EUR Umrechnung (Fallback, falls EUR nicht geliefert wird)
    if currency == "USD":
        fx = requests.get(
            "https://api.exchangerate.host/latest",
            params={"base": "USD", "symbols": "EUR"},
            timeout=20
        ).json()["rates"]["EUR"]
        price = price * fx
        currency = "EUR"

    lines.append(f"• Under Armour (UAA): €{price:.2f} | {chg:+.2f}% (24h)")
    return lines

# =========================
# Top 25 Aktien (Market Cap – Highlights)
# =========================
TOP25 = [
    "AAPL","MSFT","NVDA","AMZN","GOOGL","META","TSLA","BRK-B","LLY","AVGO",
    "JPM","V","WMT","XOM","UNH","MA","PG","JNJ","HD","ORCL",
    "COST","MRK","BAC","KO","PEP"
]

def top25_stock_lines():
    url = "https://query1.finance.yahoo.com/v7/finance/quote"
    params = {"symbols": ",".join(TOP25)}
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    res = r.json()["quoteResponse"]["result"]

    lines = ["🏢 Top 25 Aktien – Highlights"]
    # sortiere nach absoluter Tagesbewegung
    res_sorted = sorted(
        res,
        key=lambda x: abs(x.get("regularMarketChangePercent") or 0),
        reverse=True
    )[:5]

    for q in res_sorted:
        sym = q.get("symbol")
        name = q.get("shortName", sym)
        chg = q.get("regularMarketChangePercent", 0)
        lines.append(f"• {name} ({sym}): {chg:+.2f}%")
    return lines

# =========================
# Main Logic
# =========================
def main():
    now = datetime.now(timezone.utc) + timedelta(hours=1)  # MEZ
    hour = now.hour

    # manueller Start (Handy/iPad)
    manual_run = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"

    try:
        if hour == 12 or manual_run:
            lines = ["🕛 Markt-Mittagsupdate (12:00)", ""]
            lines += my_crypto_lines()
            lines.append("")
            lines += under_armour_lines()
            lines.append("")
            lines += top15_crypto_lines()
            lines.append("")
            lines += top25_stock_lines()
            send("\n".join(lines))

        elif hour == 15:
            send(
                "🧠 Geschäftspartner-Update (15:00)\n\n"
                "📌 Als Nächstes: konkrete Aktien- & Krypto-Ideen mit Begründung"
            )

        elif hour == 18:
            lines = ["🕕 Tagesabschluss (18:00)", ""]
            lines += my_crypto_lines()
            lines.append("")
            lines += under_armour_lines()
            lines.append("")
            lines += top15_crypto_lines()
            lines.append("")
            lines += top25_stock_lines()
            send("\n".join(lines))

        else:
            return

    except Exception as e:
        send(f"⚠️ Bot-Fehler:\n{type(e).__name__}: {e}")

if __name__ == "__main__":
    main()
