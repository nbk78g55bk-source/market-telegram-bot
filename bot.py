import os
import requests
from datetime import datetime, timezone, timedelta

def top15_crypto_lines():
    url = "https://api.coingecko.com/api/v3/coins/markets"
    params = {
        "vs_currency": "usd",
        "order": "market_cap_desc",
        "per_page": 15,
        "page": 1,
        "price_change_percentage": "24h"
    }
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    data = r.json()

    lines = ["🪙 Top 15 Krypto (Market Cap)"]
    for c in data:
        name = c.get("name")
        sym = (c.get("symbol") or "").upper()
        chg = c.get("price_change_percentage_24h") or 0.0
        lines.append(f"• {name} ({sym}): {chg:+.2f}% (24h)")
    return lines

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

def send(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": text,
        "disable_web_page_preview": True
    })

def main():
    now = datetime.now(timezone.utc) + timedelta(hours=1)  # MEZ
    hour = now.hour

    if hour == 12:
    lines = ["🕛 Markt-Mittagsupdate (12:00)", ""]
    lines += top15_crypto_lines()
    send("\n".join(lines))
    elif hour == 15:
        send("🧠 Geschäftspartner-Update\n\n(Test – Research kommt später)")
    elif hour == 18:
    lines = ["🕕 Tagesabschluss (18:00)", ""]
    lines += top15_crypto_lines()
    send("\n".join(lines))

if __name__ == "__main__":
    main()
