import os
import requests
from datetime import datetime, timezone, timedelta

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

def main():
    now = datetime.now(timezone.utc) + timedelta(hours=1)
    hour = now.hour

    manual_run = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"

    try:
        if hour == 12 or force == "midday":
            lines = ["🕛 Markt-Mittagsupdate (12:00)", ""]
            lines += top15_crypto_lines()
            send("\n".join(lines))

        elif hour == 15 or force == "partner":
            send("🧠 Geschäftspartner-Update (15:00)\n\n(kommt als nächstes)")

        elif hour == 18 or force == "evening":
            lines = ["🕕 Tagesabschluss (18:00)", ""]
            lines += top15_crypto_lines()
            send("\n".join(lines))

        else:
            return

    except Exception as e:
        send(f"⚠️ Bot-Fehler:\n{type(e).__name__}: {e}")
        return

if __name__ == "__main__":
    main()
