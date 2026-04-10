import asyncio
import aiohttp
from telegram import Bot

BOT_TOKEN = "8348318598:AAHcde0iLs0n5pnPywfiiHx1wEJNmwYL9GI"
CHAT_ID = -1003887512484

URLS = [
    "https://number-details.onrender.com"
]

INTERVAL = 120  # 2 minutes


async def check_sites():
    bot = Bot(token=BOT_TOKEN)

    while True:
        async with aiohttp.ClientSession() as session:

            for url in URLS:
                try:
                    async with session.get(url, timeout=30) as response:

                        message = f"🌐 Website Check\n\n{url}\nStatus: {response.status}"

                        await bot.send_message(chat_id=CHAT_ID, text=message)

                except Exception as e:

                    await bot.send_message(
                        chat_id=CHAT_ID,
                        text=f"🚨 Website DOWN\n{url}"
                    )

        await asyncio.sleep(INTERVAL)


asyncio.run(check_sites())
