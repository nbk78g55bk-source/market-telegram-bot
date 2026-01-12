 import os
import requests
from datetime import datetime, timezone, timedelta

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
        send("🕛 Markt-Mittagsupdate\n\n(Das ist ein Test – Inhalte kommen später)")
    elif hour == 15:
        send("🧠 Geschäftspartner-Update\n\n(Test – Research kommt später)")
    elif hour == 18:
        send("🕕 Tagesabschluss\n\n(Test – Tagesrecap kommt später)")
    else:
        send("🤖 Bot-Heartbeat (alles läuft)")

if __name__ == "__main__":
    main()
