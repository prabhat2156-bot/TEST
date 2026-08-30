"""
webapp/tg.py — tiny synchronous Telegram notifier used ONLY by the web admin
panel (the bot itself keeps using python-telegram-bot as normal).

Kept deliberately dependency-light (just `requests`) so the Flask process
never has to share an event loop with the bot's asyncio Application.
"""
import requests
import config

API = "https://api.telegram.org/bot{token}/{method}"


def _token():
    return getattr(config, "BOT_TOKEN", "")


def send_message(chat_id, text, parse_mode="HTML", buttons=None):
    """Fire-and-forget message send. `buttons` is an optional list of
    [{"text": "...", "callback_data": "..."}] rows. Returns True/False, never raises."""
    token = _token()
    if not token or not chat_id:
        return False
    payload = {"chat_id": chat_id, "text": text, "parse_mode": parse_mode}
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    try:
        r = requests.post(
            API.format(token=token, method="sendMessage"),
            json=payload,
            timeout=10,
        )
        return r.ok and r.json().get("ok", False)
    except Exception:
        return False


def broadcast(user_ids, text, parse_mode="HTML"):
    """Send to many users. Returns (sent_count, failed_count)."""
    sent, failed = 0, 0
    for uid in user_ids:
        if send_message(uid, text, parse_mode=parse_mode):
            sent += 1
        else:
            failed += 1
    return sent, failed
