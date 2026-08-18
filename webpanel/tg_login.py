"""
webpanel/tg_login.py
────────────────────
Telethon login helper for the WEB admin panel.

Adds a Telegram account to `otp_stock` exactly like otp_admin.py does, but
driven from the browser:

    start_login(phone)  ->  send OTP to the number
    submit_code(token, code)      -> 'ok' | 'need_2fa' | error
    submit_password(token, pwd)   -> 'ok' | error
    finish(token, country, icon, year, price, twofa)  -> saves the stock row

A single background asyncio loop keeps every Telethon client alive between
HTTP requests (Flask is synchronous, Telethon is async).
"""

import asyncio
import logging
import os
import secrets
import threading
import time

from mongo_client import col, next_id, now_iso
import otp_module as om

logger = logging.getLogger("webpanel.tg_login")

# ── background event loop ────────────────────────────────────────────────────
_loop = None
_loop_lock = threading.Lock()


def _get_loop():
    global _loop
    if _loop is not None:
        return _loop
    with _loop_lock:
        if _loop is None:
            loop = asyncio.new_event_loop()
            threading.Thread(target=loop.run_forever, name="webpanel-telethon",
                             daemon=True).start()
            _loop = loop
    return _loop


def run(coro, timeout=90):
    return asyncio.run_coroutine_threadsafe(coro, _get_loop()).result(timeout)


# ── pending logins ───────────────────────────────────────────────────────────
# token -> {phone, client, phone_code_hash, created}
_pending = {}
_PENDING_TTL = 900


def _gc():
    now = time.time()
    for tok in [t for t, s in _pending.items() if now - s["created"] > _PENDING_TTL]:
        st = _pending.pop(tok, None)
        if st:
            try:
                run(st["client"].disconnect(), timeout=20)
            except Exception:
                pass


def _client(phone):
    """In-memory login client — no .session file lock issues."""
    return om.make_login_client(phone)


def stock():
    return col("otp_stock")


def _persist(st):
    """Materialise the authorised in-memory session as <phone>.session."""
    try:
        st["session_file"] = om.persist_login_session(st["client"], st["phone"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("persist session %s failed: %s", st.get("phone"), exc)


def start_login(phone: str):
    """Send the login code. Returns (token, error)."""
    _gc()
    phone = (phone or "").strip().replace(" ", "").lstrip("+")
    if not phone.isdigit() or len(phone) < 6:
        return None, "Invalid phone number — digits only, with country code."
    if stock().find_one({"phone": phone}, {"_id": 1}):
        return None, f"+{phone} is already in stock."

    client = _client(phone)

    try:
        sreq = run(om.send_login_code(client, phone), timeout=60)
    except Exception as exc:  # noqa: BLE001
        logger.warning("send_code_request(%s) failed: %s", phone, exc)
        try:
            run(client.disconnect(), timeout=20)
        except Exception:
            pass
        om._delete_session_files_by_phone(phone)
        return None, f"OTP send failed — {om.explain_login_error(exc)}"

    token = secrets.token_urlsafe(16)
    _pending[token] = {"phone": phone, "client": client,
                       "phone_code_hash": sreq.phone_code_hash,
                       "created": time.time()}
    return token, None


def submit_code(token: str, code: str):
    """Returns ('ok'|'need_2fa'|'error', message)."""
    st = _pending.get(token)
    if not st:
        return "error", "Session expired — start again."
    code = "".join(ch for ch in (code or "") if ch.isdigit())
    if not code:
        return "error", "Enter the OTP digits."

    from telethon.errors import SessionPasswordNeededError

    async def _go():
        client = st["client"]
        if not client.is_connected():
            await client.connect()
            sreq = await om.send_login_code(client, st["phone"])
            st["phone_code_hash"] = sreq.phone_code_hash
            raise RuntimeError("Reconnected — a fresh OTP was sent, enter the new code.")
        await client.sign_in(st["phone"], code, phone_code_hash=st["phone_code_hash"])

    try:
        run(_go())
    except SessionPasswordNeededError:
        return "need_2fa", "This account has 2FA enabled — enter the password."
    except Exception as exc:  # noqa: BLE001
        return "error", om.explain_login_error(exc)
    _persist(st)
    return "ok", "Logged in."


def submit_password(token: str, password: str):
    st = _pending.get(token)
    if not st:
        return "error", "Session expired — start again."
    if not password:
        return "error", "Enter the 2FA password."

    async def _go():
        await st["client"].sign_in(password=password)

    try:
        run(_go())
    except Exception as exc:  # noqa: BLE001
        return "error", om.explain_login_error(exc)
    _persist(st)
    st["twofa"] = password
    return "ok", "2FA accepted."


def cancel(token: str):
    st = _pending.pop(token, None)
    if not st:
        return
    try:
        run(st["client"].disconnect(), timeout=20)
    except Exception:
        pass
    om._delete_session_files_by_phone(st["phone"])


def finish(token: str, country: str, icon: str, year, price, twofa: str = ""):
    """Disconnect the client and store the account in otp_stock."""
    st = _pending.get(token)
    if not st:
        return "Session expired — start again."
    phone = st["phone"]
    try:
        run(st["client"].disconnect(), timeout=30)
    except Exception:
        pass
    _pending.pop(token, None)

    try:
        year = int(year)
        price = int(price)
    except (TypeError, ValueError):
        return "Year and price must be numbers."

    session_file = st.get("session_file") or (os.path.join(om.SESSIONS_DIR, phone) + ".session")
    stock().update_one(
        {"phone": phone},
        {"$set": {"session_file": session_file,
                  "country_name": country or "Unknown",
                  "country_icon": icon or "🌍",
                  "account_year": year,
                  "category": "Good",
                  "price": price,
                  "available": 1,
                  "twofa": (twofa or st.get("twofa") or "None")},
         "$setOnInsert": {"id": next_id("otp_stock"), "added_at": now_iso()}},
        upsert=True)
    return None


def pending_state(token: str):
    st = _pending.get(token)
    if not st:
        return None
    return {"phone": st["phone"], "twofa": st.get("twofa", "")}
