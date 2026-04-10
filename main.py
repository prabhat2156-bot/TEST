import asyncio
import aiohttp
import requests

# ---------- CONFIG ----------

URLS = [
    "https://number-details.onrender.com"
]

INTERVAL = 120  # 2 minutes

BOT_TOKEN = "8348318598:AAHcde0iLs0n5pnPywfiiHx1wEJNmwYL9GI"
CHAT_ID = "-1003887512484"

# ----------------------------


def send_telegram(msg):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    data = {
        "chat_id": CHAT_ID,
        "text": msg
    }
    try:
        requests.post(url, data=data)
    except:
        pass


async def ping(session, url):
    try:
        async with session.get(url, timeout=30) as response:
            if response.status != 200:
                send_telegram(f"⚠️ Website issue: {url}\nStatus: {response.status}")
            print(f"{url} -> {response.status}")
    except Exception as e:
        send_telegram(f"🚨 Website DOWN: {url}")
        print(f"{url} -> ERROR")


async def monitor():
    while True:
        async with aiohttp.ClientSession() as session:
            tasks = [ping(session, url) for url in URLS]
            await asyncio.gather(*tasks)

        print("Waiting 2 minutes...\n")
        await asyncio.sleep(INTERVAL)


asyncio.run(monitor())
