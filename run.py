"""
run.py — single entrypoint for Render (or any host that only exposes ONE port).

Runs three things together:
  1. The Telegram bot (bot.py's build_app().run_polling()) in a background thread.
  2. The Flask web admin panel (webapp/app.py) on the main thread, bound to $PORT.
  3. A self-ping thread that hits this service's own /ping route every 2 minutes,
     so Render's free tier doesn't spin the instance down from inactivity.

Local development:
    python run.py
    → open http://localhost:10000  (or $PORT)

Render:
    Start Command:  python run.py
    (Render sets $PORT automatically; RENDER_EXTERNAL_URL is also auto-set.)
"""
import os
import sys
import time
import asyncio
import logging
import threading

import requests

import database as db
import bot as botmodule
from webapp.app import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("run")


def _validate_token_and_clear_webhook():
    """Quick synchronous sanity check before starting polling:
       1. Confirms BOT_TOKEN actually works (calls getMe).
       2. Deletes any leftover webhook — if a webhook is set, run_polling()
          fails with a Conflict error forever without a clear reason."""
    token = getattr(botmodule.config, "BOT_TOKEN", "")
    base = f"https://api.telegram.org/bot{token}"
    try:
        me = requests.get(f"{base}/getMe", timeout=15).json()
        if not me.get("ok"):
            logger.error(f"❌ BOT_TOKEN rejected by Telegram: {me}. "
                         f"Double-check the BOT_TOKEN env var on Render matches your bot's "
                         f"current token from @BotFather (a revoked token will fail here).")
            return False
        logger.info(f"✅ Token OK — bot is @{me['result'].get('username')}")
    except Exception as exc:
        logger.error(f"❌ Could not reach Telegram API to verify BOT_TOKEN: {exc}")
        return False

    try:
        wh = requests.get(f"{base}/deleteWebhook", params={"drop_pending_updates": True}, timeout=15).json()
        logger.info(f"🧹 Webhook cleared (was needed if the bot was ever run in webhook mode): {wh.get('ok')}")
    except Exception as exc:
        logger.warning(f"Could not clear webhook (usually harmless): {exc}")
    return True


def run_bot():
    """Runs the Telegram bot's polling loop in this thread forever."""
    # This function runs in a background thread. Only the MAIN thread gets an
    # asyncio event loop automatically — python-telegram-bot's run_polling()
    # needs one to exist for *this* thread, so we create and register a fresh
    # one on every attempt (PTB closes the loop when polling stops/crashes).
    backoff = 10
    while True:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            if not _validate_token_and_clear_webhook():
                logger.error(f"⏳ Retrying token check in {backoff}s…")
                time.sleep(backoff)
                continue
            logger.info("🤖 Starting Telegram bot polling…")
            application = botmodule.build_app()
            # stop_signals=None → don't try to install OS signal handlers,
            # which only works on the main thread. This thread is a worker.
            application.run_polling(drop_pending_updates=True, stop_signals=None)
        except Exception:
            logger.exception(f"❌ Bot polling crashed — restarting in {backoff}s")
            time.sleep(backoff)


def self_ping():
    """Pings this service's own /ping URL every 2 minutes to prevent the
    Render free-tier instance from going to sleep due to inactivity."""
    url = (
        os.environ.get("SELF_URL")
        or os.environ.get("RENDER_EXTERNAL_URL")
        or (f"https://{os.environ['RENDER_EXTERNAL_HOSTNAME']}" if os.environ.get("RENDER_EXTERNAL_HOSTNAME") else "")
    ).rstrip("/")
    if not url:
        logger.warning("SELF_URL / RENDER_EXTERNAL_URL not set — self-ping disabled. "
                        "Set SELF_URL to your Render URL to keep the free tier awake 24/7.")
        return
    ping_url = f"{url}/ping"
    logger.info(f"🔁 Self-ping enabled → {ping_url} every 2 minutes")
    while True:
        time.sleep(120)
        try:
            requests.get(ping_url, timeout=15)
        except Exception as exc:
            logger.warning(f"Self-ping failed: {exc}")


def main():
    db.init_db()

    if not getattr(botmodule.config, "BOT_TOKEN", ""):
        logger.error("BOT_TOKEN is not set. Add it in Render → Environment.")
        sys.exit(1)

    threading.Thread(target=run_bot, daemon=True, name="tg-bot").start()
    threading.Thread(target=self_ping, daemon=True, name="self-ping").start()

    app = create_app()
    port = int(os.environ.get("PORT", 10000))

    try:
        from waitress import serve
        logger.info(f"🌐 Web admin panel starting (waitress) on 0.0.0.0:{port}")
        serve(app, host="0.0.0.0", port=port)
    except ImportError:
        logger.warning("waitress not installed — falling back to Flask's dev server "
                        "(fine for local testing, NOT recommended for production).")
        app.run(host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
