"""
otp_module.py — Buy Account (OTP) feature merged into Store bot.

Phase 1: user-facing Buy Account flow (single + bulk).
Phase 2: full TG Panel admin (see otp_admin.py).

DB: shared MongoDB database. Balance stored in USDT on `users.balance`;
OTP flow internally uses INR via inr_to_usdt helper.

Button labels intentionally contain NO leading unicode emoji — icons come
from Premium Custom Emoji IDs mapped in emoji_config.BTN_EMOJI /
BTN_EMOJI_PREFIX (auto-resolved by callback_data). Message-text emojis
also route through EC.apply_premium_emoji() when parse_mode='HTML'.
"""
import asyncio, os, re, time, zipfile, logging
from dotenv import load_dotenv
load_dotenv()
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    Application, CallbackQueryHandler, MessageHandler, CommandHandler,
    ConversationHandler, ContextTypes, filters,
)

import database as db
import styled_api
import emoji_config as EC

logger = logging.getLogger("otp_module")

# ── env / config ─────────────────────────────────────────────────────────────
def _int_env(name, default=0):
    v = os.getenv(name, "").strip()
    try: return int(v) if v else default
    except: return default

# Same working Telegram API pair/fallback as original OTP.py.
# IMPORTANT: The Store bot/Replit environment may already contain generic
# API_ID/API_HASH values for some other app. A syntactically valid but wrong
# pair still crashes Telethon with:
#   "api_id/api_hash combination is invalid (caused by SendCodeRequest)"
# So the merged OTP login uses OTP.py's known working pair by default and only
# allows override when BOTH OTP_API_ID and OTP_API_HASH are explicitly set.
_DEFAULT_API_ID = 32208414
_DEFAULT_API_HASH = "628f11c05a44c8dda4b006e66f4bf7df"
_PLACEHOLDER_HASHES = {
    "",
    "your_my_telegram_org_api_hash",
    "your_api_hash",
    "api_hash",
    "none",
    "null",
}

def _load_telegram_api_pair():
    raw_id = (os.getenv("OTP_API_ID") or "").strip()
    raw_hash = (os.getenv("OTP_API_HASH") or "").strip()

    if not raw_id and not raw_hash:
        return _DEFAULT_API_ID, _DEFAULT_API_HASH, "otp.py default"

    try:
        api_id = int(raw_id)
    except ValueError:
        return _DEFAULT_API_ID, _DEFAULT_API_HASH, "otp.py default (bad OTP_API_ID ignored)"

    api_hash = raw_hash
    if (
        api_id <= 0
        or api_hash.lower() in _PLACEHOLDER_HASHES
        or not re.fullmatch(r"[0-9a-fA-F]{32}", api_hash)
    ):
        return _DEFAULT_API_ID, _DEFAULT_API_HASH, "otp.py default (bad OTP_API_HASH ignored)"

    return api_id, api_hash, "OTP_API env override"

API_ID, API_HASH, API_SOURCE = _load_telegram_api_pair()
logger.info("OTP Telethon API loaded from %s (api_id=%s)", API_SOURCE, API_ID)
OTP_ADMIN_ID = _int_env("OTP_ADMIN_ID", _int_env("ADMIN_ID", 0))
OTP_LOG_CHANNEL = _int_env("OTP_LOG_CHANNEL_ID", 0)
SESSIONS_DIR = os.getenv("OTP_SESSIONS_DIR", "sessions")
os.makedirs(SESSIONS_DIR, exist_ok=True)

OTP_REGEX = r"\b\d{4,8}\b"
AUTO_CANCEL_SECONDS = 600

# ── db bootstrap (MongoDB) ───────────────────────────────────────────────────
from mongo_client import col, next_id, now_iso, ensure_indexes, strip_id, strip_ids


def init_schema():
    """Collections are schemaless; we only need the indexes."""
    ensure_indexes()
    recover_stale_orders()


def recover_stale_orders():
    """Refund OTP reservations left behind by a process restart."""
    cutoff = (datetime.now() - timedelta(seconds=AUTO_CANCEL_SECONDS)).isoformat()
    for order in _orders().find({"status": "pending", "created_at": {"$lt": cutoff}}):
        changed = _orders().update_one(
            {"id": order["id"], "status": "pending"},
            {"$set": {"status": "refunded", "refunded_at": now_iso(),
                      "refund_reason": "process_restart"}})
        if changed.modified_count:
            credit_inr(order["user_id"], order["price"])
            _release_stock(order.get("phone"))


# ── helpers ──────────────────────────────────────────────────────────────────
def _stock():
    return col("otp_stock")


def _orders():
    return col("otp_orders")


_OTP_SET_CACHE = {"at": 0.0, "data": {}}
_OTP_SET_TTL = 20.0


def _setting(key, default=None):
    now = time.time()
    if now - _OTP_SET_CACHE["at"] > _OTP_SET_TTL:
        try:
            _OTP_SET_CACHE["data"] = {
                d["key"]: d.get("value")
                for d in col("otp_settings").find({}, {"_id": 0, "key": 1, "value": 1})}
            _OTP_SET_CACHE["at"] = now
        except Exception:
            pass
    val = _OTP_SET_CACHE["data"].get(key)
    return val if val is not None else default


def set_setting(key, val):
    col("otp_settings").update_one({"key": key}, {"$set": {"value": str(val)}}, upsert=True)
    _OTP_SET_CACHE["data"][key] = str(val)


def get_usdt_rate():
    try:
        rate = float(_setting("usdt_rate", "94.0"))
        return rate if rate > 0 else 94.0     # a 0 rate used to crash with ZeroDivisionError
    except (TypeError, ValueError):
        return 94.0


def inr_to_usdt(inr):
    return float(inr or 0) / get_usdt_rate()


def usdt_to_inr(u):
    return int(round(float(u or 0) * get_usdt_rate()))


def user_balance_inr(uid):
    u = db.get_user(uid)
    return usdt_to_inr(u["balance"]) if u else 0


def debit_inr(uid, inr):
    """Atomic conditional debit — balance can never go negative."""
    usdt = inr_to_usdt(inr)
    return db.debit_balance(uid, usdt, "otp_purchase", "otp", int(uid))


def credit_inr(uid, inr):
    db.update_balance(uid, inr_to_usdt(inr), "otp_refund", "otp", int(uid))


# ── premium-emoji button + edit helpers ──────────────────────────────────────
def _btn(text, cb=None, *, url=None, style=None, emoji_id=None):
    """Auto-resolve premium emoji ID from emoji_config using callback_data."""
    eid = emoji_id if emoji_id else EC.get_btn_emoji(cb or "")
    return styled_api.btn(text, cb, url=url, style=style, emoji_id=eid or None)

async def _se(q, text, rows, parse_mode="HTML"):
    """Styled edit with PTB fallback (mirrors bot.se)."""
    is_photo = bool(q.message and q.message.photo)
    method = styled_api.edit_caption if is_photo else styled_api.edit
    result = await method(q.message.chat_id, q.message.message_id, text, rows, parse_mode)
    if result.get("ok"): return
    ptb_rows = [[InlineKeyboardButton(b["text"], callback_data=b.get("callback_data","noop"),
                                      url=b.get("url")) for b in row] for row in rows]
    kb = InlineKeyboardMarkup(ptb_rows)
    etext = EC.apply_premium_emoji(text)
    try:
        if is_photo:
            await q.edit_message_caption(caption=etext, reply_markup=kb, parse_mode=parse_mode)
        else:
            await q.edit_message_text(etext, reply_markup=kb, parse_mode=parse_mode)
    except Exception as e:
        logger.warning(f"_se edit failed: {e}")
        try: await q.message.reply_html(etext, reply_markup=kb)
        except: pass

# ── country map ──────────────────────────────────────────────────────────────
COUNTRY_CODES = {
    '1':('USA/Canada','🇺🇸'),'7':('Russia','🇷🇺'),'20':('Egypt','🇪🇬'),
    '27':('South Africa','🇿🇦'),'31':('Netherlands','🇳🇱'),'32':('Belgium','🇧🇪'),
    '33':('France','🇫🇷'),'34':('Spain','🇪🇸'),'39':('Italy','🇮🇹'),
    '44':('UK','🇬🇧'),'46':('Sweden','🇸🇪'),'48':('Poland','🇵🇱'),
    '49':('Germany','🇩🇪'),'52':('Mexico','🇲🇽'),'55':('Brazil','🇧🇷'),
    '60':('Malaysia','🇲🇾'),'61':('Australia','🇦🇺'),'62':('Indonesia','🇮🇩'),
    '63':('Philippines','🇵🇭'),'66':('Thailand','🇹🇭'),'84':('Vietnam','🇻🇳'),
    '86':('China','🇨🇳'),'90':('Turkey','🇹🇷'),'91':('India','🇮🇳'),
    '92':('Pakistan','🇵🇰'),'93':('Afghanistan','🇦🇫'),'94':('Sri Lanka','🇱🇰'),
    '95':('Myanmar','🇲🇲'),'98':('Iran','🇮🇷'),'212':('Morocco','🇲🇦'),
    '234':('Nigeria','🇳🇬'),'254':('Kenya','🇰🇪'),'380':('Ukraine','🇺🇦'),
    '880':('Bangladesh','🇧🇩'),'964':('Iraq','🇮🇶'),'966':('Saudi Arabia','🇸🇦'),
    '971':('UAE','🇦🇪'),'998':('Uzbekistan','🇺🇿'),
}

def country_from_phone(phone):
    p = str(phone).lstrip('+')
    for code_len in (4, 3, 2, 1):
        pref = p[:code_len]
        if pref in COUNTRY_CODES:
            return COUNTRY_CODES[pref]
    return ('Unknown', '🌍')

def flag_for(name):
    for _, (n, f) in COUNTRY_CODES.items():
        if n == name: return f
    doc = col("otp_custom_countries").find_one({"name": name}, {"flag": 1})
    return (doc or {}).get("flag") or "🌍"


def flag_html(name):
    """Return premium-emoji HTML for a country flag, or empty string.
    Uses COUNTRY_EMOJI mapping (emoji_config). No premium ID => empty
    (no normal unicode emoji shown, per user's preference)."""
    eid = EC.get_country_emoji(name)
    if not eid:
        return ""
    uni = flag_for(name) or "🌍"
    return f'<tg-emoji emoji-id="{eid}">{uni}</tg-emoji>'

# ── Telethon-based account year detection (mirrors OTP.py) ────────────────────
async def detect_account_year(client):
    """Ask @TGDNAbot for account creation year; falls back to current year."""
    year = datetime.now().year
    try:
        try: await client.delete_dialog('TGDNAbot')
        except: pass
        await client.send_message('TGDNAbot', '/start')
        me = await client.get_me()
        await asyncio.sleep(1)
        await client.send_message('TGDNAbot', str(me.id))
        for _ in range(8):
            await asyncio.sleep(1.5)
            msgs = await client.get_messages('TGDNAbot', limit=3)
            for m in msgs:
                if m.text and ('Created' in m.text or 'Age' in m.text or 'Registration' in m.text):
                    match = re.search(r'(?:Created|Age|Registration)[^\d]*(\d{4})', m.text, re.IGNORECASE)
                    if match: return int(match.group(1))
    except Exception as e:
        logger.warning(f"detect_account_year: {e}")
    return year

def _delete_session_files_by_phone(phone):
    base = os.path.join(SESSIONS_DIR, str(phone))
    for ext in ('.session', '.session-wal', '.session-shm', '.session-journal'):
        try:
            if os.path.exists(base + ext): os.remove(base + ext)
        except Exception: pass

# ── Telethon login helpers (shared by TG panel + web panel) ──────────────────
# Login now runs on an in-memory StringSession so a stale/locked .session file
# on disk can never break "send OTP" again ("database is locked",
# "unauthorized key", leftover half-written sessions). Only after a fully
# successful sign-in do we materialise the real <phone>.session file.

DEVICE_KW = dict(device_model="Desktop", system_version="Windows 10",
                 app_version="4.16.8", lang_code="en", system_lang_code="en")


def make_login_client(phone):
    """Fresh in-memory Telethon client for a brand-new login."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    _delete_session_files_by_phone(phone)
    return TelegramClient(StringSession(), API_ID, API_HASH,
                          connection_retries=3, retry_delay=1, timeout=20,
                          request_retries=3, receive_updates=False,
                          **DEVICE_KW)


def persist_login_session(client, phone):
    """Write the authorised in-memory session to <SESSIONS_DIR>/<phone>.session
    so every other part of the bot can reuse it. Returns the file path."""
    from telethon.sessions import SQLiteSession, StringSession
    path = os.path.join(SESSIONS_DIR, str(phone))
    sess = client.session
    if not isinstance(sess, StringSession):
        return path + ".session"          # already file-backed
    _delete_session_files_by_phone(phone)
    out = SQLiteSession(path)
    try:
        out.set_dc(sess.dc_id, sess.server_address, sess.port)
        out.auth_key = sess.auth_key
        out.save()
    finally:
        try: out.close()
        except Exception: pass
    return path + ".session"


def explain_login_error(exc):
    """Human-readable reason why send_code/sign_in failed."""
    name = type(exc).__name__
    text = str(exc)
    low = (name + " " + text).lower()
    if "apiid" in low or "api_id/api_hash" in low:
        return ("Telegram API ID/HASH galat hai. Render env me sahi "
                "OTP_API_ID aur OTP_API_HASH (my.telegram.org se) set karo.")
    if "flood" in low:
        secs = re.search(r"(\d+)", text)
        return f"Telegram ne rate-limit kiya (FloodWait{' ' + secs.group(1) + 's' if secs else ''}). Thodi der baad try karo."
    if "banned" in low:
        return "Ye number Telegram par banned hai."
    if "phonenumberinvalid" in low:
        return "Phone number invalid hai — country code ke saath dobara bhejo."
    if "phonenumberflood" in low:
        return "Is number par bahut jyada login attempts ho chuke — 24h baad try karo."
    if "database is locked" in low or "readonly database" in low:
        return "Session file locked thi — clear kar di, ab dobara try karo."
    if "timeout" in low or "timed out" in low or "connection" in low:
        return "Telegram se connect nahi ho paya (network/DC issue). Dobara try karo."
    if "codeinvalid" in low:
        return "OTP code galat hai."
    if "codeexpired" in low:
        return "OTP expire ho gaya — naya OTP mangao."
    if "passwordhashinvalid" in low:
        return "2FA password galat hai."
    return f"{name}: {text}" if text else name


async def send_login_code(client, phone):
    """Connect + send the login code, with an SMS fallback."""
    if not client.is_connected():
        await client.connect()
    try:
        return await client.send_code_request(phone)
    except Exception:
        return await client.send_code_request(phone, force_sms=True)

# ── active orders (in-memory) ────────────────────────────────────────────────
active_orders = {}

# ── UI: main "Buy Account" entry ─────────────────────────────────────────────
async def buy_account_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    return await _show_countries(q, page=1)

async def _show_countries(q, page=1):
    per_page = 10
    rows = [
        (g["_id"], g.get("icon") or "", g["cnt"])
        for g in _stock().aggregate([
            {"$match": {"available": 1}},
            {"$group": {"_id": "$country_name",
                        "icon": {"$first": "$country_icon"},
                        "cnt": {"$sum": 1}}},
            {"$sort": {"_id": 1}},
        ])
    ]

    total = len(rows)
    slice_ = rows[(page-1)*per_page: page*per_page]
    if not slice_:
        return await _se(q,
            "<b>Buy Telegram Account</b>\n\n<i>No stock available right now. Please check again later.</i>",
            [[_btn("Home", "home", style="danger")]])
    btns = []
    for name, icon, cnt in slice_:
        cb = f"otp_c|{name}"
        eid = EC.get_country_emoji(name)
        # No unicode flag in label — icon comes from premium emoji only.
        btns.append([_btn(f"{name}  ({cnt})", cb, style="primary", emoji_id=eid or None)])
    nav = []
    if page > 1: nav.append(_btn("Prev", f"otp_cp|{page-1}", style="primary"))
    if page*per_page < total: nav.append(_btn("Next", f"otp_cp|{page+1}", style="primary"))
    if nav: btns.append(nav)
    btns.append([_btn("Home", "home", style="danger")])
    bal_inr = user_balance_inr(q.from_user.id)
    bal_usdt = float((db.get_user(q.from_user.id) or {}).get("balance") or 0)

    txt = ("<b>Buy Telegram Account</b>\n\n"
           "Select a country. Prices in ₹ (INR).\n"
           f"💰 <b>Your balance:</b> ₹{bal_inr}  (${bal_usdt:.2f})")
    await _se(q, txt, btns)

async def otp_countries_page_cb(update, ctx):
    q = update.callback_query; await q.answer()
    page = int(q.data.split("|")[1])
    await _show_countries(q, page=page)

async def otp_pick_country_cb(update, ctx):
    q = update.callback_query; await q.answer()
    country = q.data.split("|", 1)[1]
    rows = [
        (g["_id"]["y"], g["_id"]["p"], g["cnt"])
        for g in _stock().aggregate([
            {"$match": {"country_name": country, "available": 1}},
            {"$group": {"_id": {"y": "$account_year", "p": "$price"}, "cnt": {"$sum": 1}}},
            {"$sort": {"_id.y": -1, "_id.p": 1}},
        ])
    ]

    if not rows:
        return await _se(q, "<b>Sold out.</b>",
            [[_btn("Back", "otp_buy", style="danger")]])
    btns = []
    for year, price, cnt in rows:
        btns.append([_btn(f"{year}  •  ₹{price}  •  Stock: {cnt}",
            f"otp_y|{country}|{year}|{price}", style="primary")])
    btns.append([_btn("Back", "otp_buy", style="danger")])
    flag = flag_html(country)
    prefix = (flag + " ") if flag else ""
    await _se(q, f"{prefix}<b>{country}</b>\n\nPick account year + price:", btns)

async def otp_pick_year_cb(update, ctx):
    q = update.callback_query; await q.answer()
    _, country, year, price = q.data.split("|")
    price = int(price); year = int(year)
    stock = _stock_count(country, year, price)

    if stock == 0:
        return await q.answer("Sold out!", show_alert=True)
    flag = flag_html(country)
    prefix = (flag + " ") if flag else ""
    btns = [
        [_btn(f"Buy 1  (₹{price})",  f"otp_buy1|{country}|{year}|{price}", style="success")],
        [_btn(f"Buy Bulk  (sessions zip)", f"otp_bulk|{country}|{year}|{price}", style="primary")],
        [_btn("Back", f"otp_c|{country}", style="danger")],
    ]
    await _se(q,
        f"{prefix}<b>{country} — {year}</b>\n\n"
        f"💰 Price: <b>₹{price}</b>\n"
        f"📦 Stock: <b>{stock}</b>\n\n"
        f"<b>Buy 1</b> — bot logs in, delivers phone + live OTP (10 min).\n"
        f"<b>Buy Bulk</b> — receive .session files in a zip.", btns)

# ── Buy 1 (fetch OTP) ────────────────────────────────────────────────────────
async def otp_buy1_cb(update, ctx):
    q = update.callback_query; uid = q.from_user.id
    _, country, year, price = q.data.split("|")
    price = int(price); year = int(year)
    await q.answer()

    if user_balance_inr(uid) < price:
        return await _se(q,
            f"<b>Insufficient balance!</b>\nNeeded: ₹{price}\nYou have: ₹{user_balance_inr(uid)}\n\n"
            f"Deposit karke wapas try karo.",
            [[_btn("Deposit", "deposit", style="success"),
              _btn("Back", "otp_buy", style="danger")]])

    # Charge first, then atomically reserve one unit so two buyers can never
    # get the same session (the old SELECT-then-UPDATE was a race condition).
    if not debit_inr(uid, price):
        return await _se(q, "<b>Insufficient balance!</b>", [[_btn("Back", "otp_buy", style="danger")]])

    row = _stock().find_one_and_update(
        {"country_name": country, "account_year": year, "price": price, "available": 1},
        {"$set": {"available": 0, "reserved_at": now_iso()}},
    )
    if not row:
        credit_inr(uid, price)   # refund — nothing was delivered
        return await _se(q, "<b>Just sold out.</b>", [[_btn("Back", "otp_buy", style="danger")]])
    phone = row.get("phone")
    sess = row.get("session_file") or ""
    c_icon = row.get("country_icon") or ""
    actual_year = row.get("account_year")
    twofa = row.get("twofa")
    otp_order_id = next_id("otp_orders")
    _orders().insert_one({
        "id": otp_order_id, "user_id": uid, "country": country,
        "year": actual_year, "price": price, "phone": phone, "otp": None,
        "amount_usdt": inr_to_usdt(price),
        "status": "pending", "created_at": now_iso(), "delivered_at": None,
        "refunded_at": None, "store_order_id": None,
    })


    msg = await q.edit_message_text(
        EC.apply_premium_emoji(f"🔄 <b>Connecting to +{phone}…</b>"), parse_mode="HTML")

    try:
        from telethon import TelegramClient
    except ImportError:
        _orders().update_one(
            {"id": otp_order_id, "status": "pending"},
            {"$set": {"status": "refunded", "refunded_at": now_iso(),
                      "refund_reason": "telethon_not_installed"}})
        credit_inr(uid, price); _release_stock(phone)
        return await msg.edit_text("Telethon not installed. Run `pip install telethon`.")

    clean = sess[:-8] if sess.endswith(".session") else sess
    client = TelegramClient(clean, API_ID, API_HASH)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise Exception("dead session")
    except Exception:
        try: await client.disconnect()
        except: pass
        _delete_session_files(clean)
        _stock().delete_one({"phone": phone})
        _orders().update_one(
            {"id": otp_order_id, "status": "pending"},
            {"$set": {"status": "refunded", "refunded_at": now_iso(),
                      "refund_reason": "dead_session"}})

        credit_inr(uid, price)
        return await msg.edit_text(
            EC.apply_premium_emoji(
                f"❌ <b>Session Dead.</b> Your ₹{price} has been refunded. Please buy another."),
            parse_mode="HTML")

    txt = (f"✅ <b>Order Active!</b>\n\n"
           f"📱 <b>Phone:</b> <code>+{phone}</code>\n"
           f"🏳️ <b>Country:</b> {c_icon} {country}\n\n"
           f"🔻 <b>Instructions:</b>\n"
           f"1. Open Telegram → Add Account\n"
           f"2. Enter <code>+{phone}</code>\n"
           f"3. Wait — bot will auto-fetch the OTP within a minute.\n\n"
           f"<i>10-min limit. Auto-refund if no OTP arrives.</i>")
    sent = await msg.edit_text(EC.apply_premium_emoji(txt), parse_mode="HTML")

    active_orders[phone] = {
        "uid": uid, "chat_id": q.message.chat_id, "msg_id": sent.message_id,
        "client": client, "sess": sess, "start_time": time.time(),
        "paid": False, "price": price, "country": country,
        "year": actual_year, "c_icon": c_icon, "twofa": twofa,
        "order_id": otp_order_id,
    }
    ctx.application.create_task(_auto_otp_task(ctx.application, phone))

def _stock_count(country, year, price):
    return _stock().count_documents(
        {"country_name": country, "account_year": int(year),
         "price": int(price), "available": 1})


def _release_stock(phone):
    _stock().update_one({"phone": phone},
                        {"$set": {"available": 1}, "$unset": {"reserved_at": ""}})


def _delete_session_files(base):
    if base.endswith(".session"): base = base[:-8]
    for ext in ('.session','.session-wal','.session-shm','.session-journal'):
        try:
            if os.path.exists(base+ext): os.remove(base+ext)
        except: pass

async def _auto_otp_task(app: Application, phone: str):
    if phone not in active_orders: return
    o = active_orders[phone]
    client = o["client"]; start = o["start_time"]
    uid = o["uid"]; chat = o["chat_id"]; msg_id = o["msg_id"]

    while time.time() - start < AUTO_CANCEL_SECONDS:
        if phone not in active_orders: return
        try:
            msgs = await client.get_messages(777000, limit=5)
            code = None
            for m in msgs:
                if m.date.timestamp() > start - 10 and m.message \
                    and re.search(OTP_REGEX, m.message) \
                    and "Login detected" not in m.message:
                    code = re.search(OTP_REGEX, m.message).group()
                    break
            if code:
                if not o["paid"]:
                    o["paid"] = True
                    _orders().update_one(
                        {"id": o["order_id"], "status": "pending"},
                        {"$set": {"otp": code, "status": "delivered",
                                  "delivered_at": now_iso()}})
                    _stock().delete_one({"phone": phone})

                    store_order_id = db.create_order(
                        uid, 0, f"OTP {o['country']} {o['year']} +{phone}",
                        inr_to_usdt(o["price"]))
                    _orders().update_one(
                        {"id": o["order_id"]},
                        {"$set": {"store_order_id": store_order_id}})
                    if OTP_LOG_CHANNEL:
                        try:
                            await app.bot.send_message(OTP_LOG_CHANNEL,
                                EC.apply_premium_emoji(
                                    f"🛒 <b>OTP Sale</b>\n👤 <code>{uid}</code>\n"
                                    f"{o['c_icon']} {o['country']} • {o['year']}\n"
                                    f"📱 <code>+{phone}</code>\n💰 ₹{o['price']}"),
                                parse_mode="HTML")
                        except: pass
                    # Public sale broadcast
                    try:
                        import sale_feed
                        cid = EC.get_country_emoji(o["country"]) if hasattr(EC, "get_country_emoji") else ""
                        await sale_feed.broadcast_sale(
                            app.bot, qty=1,
                            product_name=f"{o['country']} {o['year']} Account",
                            product_emoji=o["c_icon"], product_emoji_id=cid,
                            source="bot")
                    except Exception as _e:
                        logger.debug(f"sale_feed otp skip: {_e}")
                twofa_line = (f"🔐 <b>2FA:</b> <code>{o['twofa']}</code>"
                              if o['twofa'] and o['twofa'] != "None"
                              else "🔓 <b>2FA:</b> <code>None</code>")
                out = (f"✅ <b>OTP Fetched!</b>\n\n"
                       f"📱 <b>Phone:</b> <code>+{phone}</code>\n"
                       f"🏳️ <b>Country:</b> {o['c_icon']} {o['country']}\n"
                       f"🔢 <b>OTP:</b> <code>{code}</code>\n{twofa_line}")
                rows = [
                    [_btn("Get OTP Again",   f"otp_again|{phone}",  style="primary")],
                    [_btn("Finish & Logout", f"otp_logout|{phone}", style="danger")],
                ]
                try:
                    await styled_api.edit(chat, msg_id, out, rows, "HTML")
                except Exception:
                    await app.bot.send_message(chat, EC.apply_premium_emoji(out), parse_mode="HTML")
                return
        except Exception as e:
            logger.debug(f"otp poll err {phone}: {e}")
        await asyncio.sleep(6)

    if phone in active_orders and not active_orders[phone]["paid"]:
        o = active_orders.pop(phone)
        try: await o["client"].disconnect()
        except: pass
        _orders().update_one(
            {"id": o["order_id"], "status": "pending"},
            {"$set": {"status": "refunded", "refunded_at": now_iso(),
                      "refund_reason": "otp_timeout"}})
        credit_inr(uid, o["price"])
        _release_stock(phone)
        try:
            await app.bot.edit_message_text(
                EC.apply_premium_emoji(
                    f"⏰ <b>Order Expired.</b>\nNo OTP for +{phone} in 10 min. "
                    f"₹{o['price']} refunded."),
                chat_id=chat, message_id=msg_id, parse_mode="HTML")
        except: pass

async def otp_again_cb(update, ctx):
    q = update.callback_query; await q.answer("Fetching again…")
    phone = q.data.split("|")[1]
    o = active_orders.get(phone)
    if not o: return await q.answer("Order closed.", show_alert=True)
    if o.get("paid"):
        return await q.answer(
            "This order is already delivered. Use Finish & Logout.", show_alert=True)
    o["start_time"] = time.time()
    ctx.application.create_task(_auto_otp_task(ctx.application, phone))

async def otp_logout_cb(update, ctx):
    q = update.callback_query; await q.answer("Logging out…")
    phone = q.data.split("|")[1]
    o = active_orders.pop(phone, None)
    if o:
        if not o.get("paid"):
            _orders().update_one(
                {"id": o["order_id"], "status": "pending"},
                {"$set": {"status": "refunded", "refunded_at": now_iso(),
                          "refund_reason": "user_logout"}})
            credit_inr(o["uid"], o["price"])
            _release_stock(phone)
        try: await o["client"].log_out()
        except: pass
        try: await o["client"].disconnect()
        except: pass
        _delete_session_files(o["sess"])
    await _se(q, f"<b>Session terminated.</b> +{phone}",
              [[_btn("Buy Another", "otp_buy", style="success"),
                _btn("Home", "home", style="danger")]])

# ── Buy Bulk (sessions zip) ──────────────────────────────────────────────────
BULK_QTY = 9001

async def otp_bulk_cb(update, ctx):
    q = update.callback_query; uid = q.from_user.id
    _, country, year, price = q.data.split("|")
    price = int(price); year = int(year)
    stock = _stock_count(country, year, price)

    if stock == 0: return await q.answer("Sold out!", show_alert=True)
    await q.answer()
    ctx.user_data["otp_bulk"] = {"country": country, "year": year, "price": price, "stock": stock}
    await _se(q,
        f"<b>Bulk {country} {year} @ ₹{price}/session</b>\n\n"
        f"Available: <b>{stock}</b>\n\n"
        f"Reply with the <b>quantity</b> you want to buy.\n"
        f"/cancel to abort.",
        [[_btn("Cancel", "otp_buy", style="danger")]])
    return BULK_QTY

async def otp_bulk_qty(update, ctx):
    uid = update.effective_user.id
    state = ctx.user_data.get("otp_bulk")
    if not state: return ConversationHandler.END
    try:
        qty = int(update.message.text.strip()); assert qty > 0
    except:
        await update.message.reply_text("Send a positive number, or /cancel."); return BULK_QTY
    if qty > state["stock"]:
        await update.message.reply_text(f"Only {state['stock']} available."); return BULK_QTY
    total = qty * state["price"]
    if user_balance_inr(uid) < total:
        await update.message.reply_html(EC.apply_premium_emoji(
            f"❌ Need ₹{total}, you have ₹{user_balance_inr(uid)}."))
        ctx.user_data.pop("otp_bulk", None); return ConversationHandler.END
    await update.message.reply_html(EC.apply_premium_emoji("⏳ Processing…"))
    if not debit_inr(uid, total):
        await update.message.reply_text("Balance changed."); return ConversationHandler.END

    # Reserve one document at a time so concurrent bulk buyers can't grab the
    # same sessions. Anything we can't reserve gets released + refunded.
    rows = []
    for _ in range(qty):
        doc = _stock().find_one_and_update(
            {"country_name": state["country"], "account_year": state["year"],
             "price": state["price"], "available": 1},
            {"$set": {"available": 0, "reserved_at": now_iso()}},
        )
        if not doc:
            break
        rows.append((doc.get("phone"), doc.get("session_file") or "", doc.get("twofa")))

    if len(rows) < qty:
        for p, _s, _t in rows:
            _release_stock(p)
        credit_inr(uid, total)
        await update.message.reply_text("Stock changed. Refunded.")
        ctx.user_data.pop("otp_bulk", None)
        return ConversationHandler.END

    phones = [r[0] for r in rows]
    bulk_created_at = now_iso()
    bulk_order_ids = [next_id("otp_orders") for _ in phones]
    _orders().insert_many([{
        "id": order_id, "user_id": uid, "country": state["country"],
        "year": state["year"], "price": state["price"], "phone": p,
        "otp": "SESSION_FILES", "status": "completed",
        "amount_usdt": inr_to_usdt(state["price"]),
        "created_at": bulk_created_at, "delivered_at": bulk_created_at,
        "refunded_at": None, "store_order_id": None,
    } for order_id, p in zip(bulk_order_ids, phones)])


    zname = f"sessions_{uid}_{int(time.time())}.zip"
    numbers_txt = ""
    try:
        with zipfile.ZipFile(zname, "w") as zf:
            for phone, sess, twofa in rows:
                base = sess[:-8] if sess.endswith(".session") else sess
                for ext in ('.session','.session-wal','.session-shm','.session-journal'):
                    if os.path.exists(base+ext):
                        zf.write(base+ext, os.path.basename(base+ext))
                numbers_txt += f"+{phone} | pass:{twofa if twofa != 'None' else 'No_Password'}\n"
            zf.writestr("numbers.txt", numbers_txt)
        with open(zname, "rb") as fh:
            await update.message.reply_document(
                InputFile(fh, filename=zname),
                caption=EC.apply_premium_emoji(
                    f"✅ <b>Bulk Purchase Successful!</b>\n\n"
                    f"🏳️ {state['country']} — {state['year']}\n"
                    f"📦 Qty: {qty}\n💰 Paid: ₹{total}"),
                parse_mode="HTML")
        store_order_id = db.create_order(
            uid, 0, f"OTP-BULK {state['country']} {state['year']} x{qty}",
            inr_to_usdt(total))
        _orders().update_many(
            {"id": {"$in": bulk_order_ids}},
            {"$set": {"store_order_id": store_order_id}})
        if OTP_LOG_CHANNEL:
            try:
                await ctx.application.bot.send_message(OTP_LOG_CHANNEL,
                    EC.apply_premium_emoji(
                        f"📦 <b>Bulk Sale</b>\n👤 <code>{uid}</code>\n"
                        f"{state['country']} • {state['year']} × {qty}\n💰 ₹{total}"),
                    parse_mode="HTML")
            except: pass
        try:
            import sale_feed
            cid = EC.get_country_emoji(state["country"]) if hasattr(EC, "get_country_emoji") else ""
            await sale_feed.broadcast_sale(
                ctx.application.bot, qty=qty,
                product_name=f"{state['country']} {state['year']} Accounts",
                product_emoji_id=cid, source="bot")
        except Exception as _e:
            logger.debug(f"sale_feed bulk skip: {_e}")
    finally:
        if os.path.exists(zname): os.remove(zname)
    ctx.user_data.pop("otp_bulk", None)
    return ConversationHandler.END

async def otp_bulk_cancel(update, ctx):
    ctx.user_data.pop("otp_bulk", None)
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END

# ── registration ─────────────────────────────────────────────────────────────
def register_handlers(app: Application):
    bulk_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(otp_bulk_cb, pattern=r"^otp_bulk\|")],
        states={BULK_QTY: [MessageHandler(filters.TEXT & ~filters.COMMAND, otp_bulk_qty)]},
        fallbacks=[CommandHandler("cancel", otp_bulk_cancel),
                   CallbackQueryHandler(otp_bulk_cancel, pattern=r"^otp_buy$")],
        per_message=False,
    )
    app.add_handler(bulk_conv)
    app.add_handler(CallbackQueryHandler(buy_account_cb,        pattern=r"^otp_buy$"))
    app.add_handler(CallbackQueryHandler(otp_countries_page_cb, pattern=r"^otp_cp\|\d+$"))
    app.add_handler(CallbackQueryHandler(otp_pick_country_cb,   pattern=r"^otp_c\|"))
    app.add_handler(CallbackQueryHandler(otp_pick_year_cb,      pattern=r"^otp_y\|"))
    app.add_handler(CallbackQueryHandler(otp_buy1_cb,           pattern=r"^otp_buy1\|"))
    app.add_handler(CallbackQueryHandler(otp_again_cb,          pattern=r"^otp_again\|"))
    app.add_handler(CallbackQueryHandler(otp_logout_cb,         pattern=r"^otp_logout\|"))

    # Phase 2 admin TG Panel
    try:
        import otp_admin
        otp_admin.register(app)
    except Exception as e:
        logger.warning(f"otp_admin not loaded: {e}")
    logger.info("otp_module handlers registered")
