"""
otp_admin.py — TG Panel (OTP admin) merged into Store bot.

All buttons and message-text are premium-emoji ready:
  • button labels contain NO leading emoji glyph — icons come from
    emoji_config.BTN_EMOJI / BTN_EMOJI_PREFIX (auto-resolved by callback_data)
  • message-text emojis auto-convert via EC.apply_premium_emoji()

Features (mirrors OTP.py admin panel; skips features Store bot already has):
  • Add Stock       — upload .zip of .session files (bulk)
  • Manage Stock    — per-country actions (view / delete / mark available)
  • Auto-Price      — set INR price per (country, year)
  • Test Sessions   — Telethon health-check all `available=1` stock
  • Delete Dead     — remove dead sessions in one click
  • OTP Stats       — today / week / all-time revenue in INR
  • 2FA Manager     — view/edit per-phone 2FA password
  • Sessions Folder — dump raw folder listing
"""
from dotenv import load_dotenv
load_dotenv()
import os, re, time, zipfile, tempfile, logging, asyncio
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, InputFile
from telegram.ext import (
    Application, CallbackQueryHandler, MessageHandler, CommandHandler,
    ConversationHandler, ContextTypes, filters,
)

import database as db
import styled_api
import emoji_config as EC
import otp_module as om
from mongo_client import col, next_id, now_iso

logger = logging.getLogger("otp_admin")


# ── MongoDB collection helpers (replaces the old raw-SQL call sites) ─────────
def _stock():
    return col("otp_stock")


def _orders():
    return col("otp_orders")


def _prices():
    return col("otp_auto_prices")


def _countries():
    return col("otp_custom_countries")


def _sum_price(match=None):
    """Returns (count, revenue) for otp_orders matching `match`."""
    pipeline = []
    if match:
        pipeline.append({"$match": match})
    pipeline.append({"$group": {"_id": None, "cnt": {"$sum": 1},
                                "rev": {"$sum": "$price"}}})
    for g in _orders().aggregate(pipeline):
        return int(g.get("cnt") or 0), int(g.get("rev") or 0)
    return 0, 0


def _auto_price(country, year):
    """Auto-price lookup: exact year first, then the 'Common'/'*' wildcard."""
    doc = _prices().find_one({"country": country, "year": str(year)})
    if not doc:
        doc = _prices().find_one({"country": country,
                                  "year": {"$in": ["Common", "*"]}})
    return int(doc["price"]) if doc and doc.get("price") is not None else None


def _upsert_stock(phone, session_file, c_name, c_icon, year, price, twofa):
    _stock().update_one(
        {"phone": phone},
        {"$set": {"session_file": session_file, "country_name": c_name,
                  "country_icon": c_icon, "account_year": int(year),
                  "category": "Good", "price": int(price), "available": 1,
                  "twofa": twofa or "None"},
         "$setOnInsert": {"id": next_id("otp_stock"), "added_at": now_iso()}},
        upsert=True)


def _live_count(country, year):
    return _stock().count_documents(
        {"country_name": country, "account_year": int(year), "available": 1})

# ── permission ────────────────────────────────────────────────────────────────
def _is_admin(uid):
    try:
        from bot import is_admin
        return is_admin(uid)
    except Exception:
        return uid == om.OTP_ADMIN_ID

# ── styled helpers (premium-emoji aware) ─────────────────────────────────────
def _btn(text, cb=None, *, url=None, style=None, emoji_id=None):
    eid = emoji_id if emoji_id else EC.get_btn_emoji(cb or "")
    return styled_api.btn(text, cb, url=url, style=style, emoji_id=eid or None)

async def _se(q, text, rows, parse_mode="HTML"):
    r = await styled_api.edit(q.message.chat_id, q.message.message_id, text, rows, parse_mode)
    if r.get("ok"): return
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(b["text"],
        callback_data=b.get("callback_data","noop"), url=b.get("url"))
        for b in row] for row in rows])
    try:
        await q.edit_message_text(EC.apply_premium_emoji(text), reply_markup=kb, parse_mode=parse_mode)
    except Exception as e:
        logger.warning(f"_se failed: {e}")

def _back_row():
    return [_btn("Back to TG Panel", "tg_panel", style="primary"),
            _btn("Back to Admin",    "admin",    style="danger")]

# ── TG PANEL HOME ────────────────────────────────────────────────────────────
async def tg_panel_cb(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id):
        return await q.answer("Access denied.", show_alert=True)
    await q.answer()
    stock_cnt = _stock().count_documents({"available": 1})
    dead_cnt  = _stock().count_documents({"available": 0})
    sold_cnt, rev = _sum_price()
    countries = len(_stock().distinct("country_name", {"available": 1}))

    text = (f"📱 <b>TG PANEL — OTP Section</b>\n"
            f"────────────────────────────\n\n"
            f"📦 <b>Available stock:</b> {stock_cnt}   "
            f"({countries} country/countries)\n"
            f"🔒 <b>Reserved/sold:</b> {dead_cnt}\n"
            f"🛒 <b>Total sold:</b> {sold_cnt}\n"
            f"💰 <b>Revenue:</b> ₹{rev}\n\n"
            f"<i>Store bot ke pehle se maujud features "
            f"(Broadcast / Ban / Users / Deposit approve / Stats) yahan "
            f"dobara nahi diye — wahi Admin Panel se use hote hain.</i>")
    rows = [
        [_btn("Add Stock",       "tgp_addstock",   style="success"),
         _btn("Manage Stock",    "tgp_manage",     style="primary")],
        [_btn("Auto-Price",      "tgp_prices",     style="primary"),
         _btn("2FA Manager",     "tgp_2fa",        style="primary")],
        [_btn("Test Sessions",   "tgp_test",       style="primary"),
         _btn("Delete Dead",     "tgp_del_dead",   style="danger")],
        [_btn("OTP Stats",       "tgp_stats",      style="primary"),
         _btn("Sessions Folder", "tgp_folder",     style="primary")],
        [_btn("USDT⇄INR Rate",   "tgp_rate",       style="primary")],
        [_btn("Back to Admin",   "admin",          style="danger")],
    ]
    await _se(q, text, rows)

# ── ADD STOCK (upload zip of .session files) ─────────────────────────────────
AS_META = 8501   # waiting for country|year|price meta reply
AS_ZIP  = 8502   # waiting for zip document

# ══════════════════════════════════════════════════════════════════════════════
# ADD STOCK — OTP.py jaisa full interactive flow
#   Single Account:  Phone → OTP → 2FA (if needed) → auto-country
#                    (if Unknown → ask flag + name) → Year (detected+confirm)
#                    → Price (auto-price / existing / ask)
#   ZIP Bulk:        upload .zip of .session files → scan each →
#                    per unknown group ask flag+name → per has_2fa group
#                    ask 2fa password → per group auto/ask price → save
# ══════════════════════════════════════════════════════════════════════════════

# ── single-flow conversation states ──────────────────────────────────────────
AS_MENU   = 8500
AS_PHONE  = 8501
AS_OTP    = 8502
AS_2FA    = 8503
AS_CFLAG  = 8504
AS_CNAME  = 8505
AS_YEAR   = 8506
AS_PRICE  = 8507

# ── zip-flow conversation states ─────────────────────────────────────────────
AZ_WAIT   = 8510   # waiting for .zip document
AZ_FLAG   = 8511   # unknown-country → ask flag emoji
AZ_NAME   = 8512   # unknown-country → ask country name
AZ_2FA    = 8513   # has-2fa group  → ask password
AZ_PRICE  = 8514   # no auto-price  → ask INR price

# ── bulk-phones-flow conversation state ──────────────────────────────────────
AB_LIST   = 8520   # waiting for pasted list of phone numbers

def _tg_client(session_path):
    """Lazy import Telethon so PTB-only environments boot without it."""
    from telethon import TelegramClient
    return TelegramClient(session_path, om.API_ID, om.API_HASH)

async def _reply(update, text, kb=None):
    await update.message.reply_html(
        EC.apply_premium_emoji(text),
        reply_markup=(InlineKeyboardMarkup([[InlineKeyboardButton(b["text"],
            callback_data=b.get("callback_data","noop"), url=b.get("url"))
            for b in row] for row in kb]) if kb else None))

# ── entry: show Add Stock menu ───────────────────────────────────────────────
async def addstock_start(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id):
        return await q.answer("Access denied.", show_alert=True)
    await q.answer()
    await _se(q,
        "<b>Add Stock</b>\n\n"
        "Kaise add karna hai?\n\n"
        "• <b>Add Single Acc</b> — ek account login karke add karo "
        "(phone → OTP → 2FA → year auto-detect)\n"
        "• <b>Add Bulk Phones</b> — phone numbers ka list paste karo "
        "(CSV / one per line) — har ek ke liye bot OTP + 2FA maangega\n"
        "• <b>Add ZIP</b> — <code>.session</code> files ka <code>.zip</code> "
        "upload karo, sab kuch auto-detect ho jayega\n\n"
        "<i>/cancel to abort anytime.</i>",
        [[_btn("Add Single Acc",   "tgp_add_single", style="success"),
          _btn("Add Bulk Phones",  "tgp_add_bulk",   style="primary")],
         [_btn("Add ZIP",          "tgp_add_zip",    style="primary")],
         [_btn("Cancel",           "tg_panel",       style="danger")]])
    return AS_MENU

# ══════════════════════════════════════════════════════════════════════════════
# SINGLE ACCOUNT FLOW
# ══════════════════════════════════════════════════════════════════════════════
async def single_start(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id):
        return await q.answer("Access denied.", show_alert=True)
    await q.answer()
    ctx.user_data["tgp_single"] = {}
    await _se(q,
        "<b>Add Single Account — Step 1</b>\n\n"
        "📱 <b>Phone number bhejo</b>  (international format)\n"
        "<i>Example: <code>+919999999999</code></i>\n\n"
        "/cancel to abort.",
        [[_btn("Cancel", "tg_panel", style="danger")]])
    return AS_PHONE

async def single_phone(update, ctx):
    if not _is_admin(update.effective_user.id): return ConversationHandler.END
    phone = (update.message.text or "").strip().replace(" ", "").lstrip("+")
    if not phone.isdigit() or len(phone) < 6:
        await _reply(update, "❌ Invalid phone. Send digits only (with country code).")
        return AS_PHONE
    # existing?
    if _stock().find_one({"phone": phone}, {"_id": 1}):
        await _reply(update, f"⚠️ <b>+{phone}</b> already in stock.")
        return ConversationHandler.END

    sp = f"{om.SESSIONS_DIR}/{phone}"
    client = _tg_client(sp)
    await client.connect()
    try:
        sreq = await client.send_code_request(phone)
    except Exception as e:
        try: await client.disconnect()
        except: pass
        om._delete_session_files_by_phone(phone)
        await _reply(update, f"❌ <b>Login failed:</b> <code>{e}</code>")
        return ConversationHandler.END

    ctx.user_data["tgp_single"] = {"phone": phone, "client": client,
                                    "phone_code_hash": sreq.phone_code_hash}
    await _reply(update,
        f"✅ OTP sent to <b>+{phone}</b>\n\n"
        f"🔢 <b>Reply with the OTP code</b> you received.\n"
        f"<i>Tip: agar Telegram OTP intercept karega to hyphens laga sakte ho e.g. 1-2-3-4-5</i>\n\n"
        f"/cancel to abort.")
    return AS_OTP

async def _bulk_fail_or_end(update, ctx):
    """If a bulk-phones queue is running, count a failure and move on."""
    ctx.user_data.pop("tgp_single", None)
    if ctx.user_data.get("tgp_bulk_queue") is not None or ctx.user_data.get("tgp_bulk_stats"):
        stats = ctx.user_data.get("tgp_bulk_stats") or {"added":0,"failed":0,"total":0}
        stats["failed"] = stats.get("failed", 0) + 1
        ctx.user_data["tgp_bulk_stats"] = stats
        if ctx.user_data.get("tgp_bulk_queue"):
            return await _bulk_next(update, ctx)
    return ConversationHandler.END

async def single_otp(update, ctx):
    if not _is_admin(update.effective_user.id): return ConversationHandler.END
    st = ctx.user_data.get("tgp_single") or {}
    code = (update.message.text or "").strip()
    if not code:
        await _reply(update, "❌ Send the OTP digits.")
        return AS_OTP
    client = st.get("client")
    if not client:
        await _reply(update, "❌ Session expired. /cancel karo aur dobara shuru karo.")
        return await _bulk_fail_or_end(update, ctx)
    from telethon.errors import SessionPasswordNeededError, PhoneCodeExpiredError
    try:
        if not client.is_connected():
            await client.connect()
            # old phone_code_hash is stale after reconnect — request fresh OTP
            sreq = await client.send_code_request(st["phone"])
            st["phone_code_hash"] = sreq.phone_code_hash
            await _reply(update, "⚠️ Fresh OTP bhej diya — naya OTP use bhejo.")
            return AS_OTP
        await client.sign_in(st["phone"], code, phone_code_hash=st["phone_code_hash"])
    except SessionPasswordNeededError:
        await _reply(update,
            "🔐 <b>2FA password required</b>\n\nReply with the account's 2FA password.")
        return AS_2FA
    except PhoneCodeExpiredError:
        try:
            if not client.is_connected(): await client.connect()
            sreq = await client.send_code_request(st["phone"])
            st["phone_code_hash"] = sreq.phone_code_hash
            await _reply(update, "⚠️ OTP expire ho gaya — fresh OTP bhej diya, naya code bhejo.")
            return AS_OTP
        except Exception as e:
            try: await client.disconnect()
            except: pass
            om._delete_session_files_by_phone(st["phone"])
            await _reply(update, f"❌ <b>Resend failed:</b> <code>{e}</code>")
            return await _bulk_fail_or_end(update, ctx)
    except Exception as e:
        try: await client.disconnect()
        except: pass
        om._delete_session_files_by_phone(st["phone"])
        await _reply(update, f"❌ <b>Sign-in failed:</b> <code>{e}</code>")
        return await _bulk_fail_or_end(update, ctx)
    st["twofa"] = "None"
    return await _single_after_login(update, ctx)

async def single_2fa(update, ctx):
    if not _is_admin(update.effective_user.id): return ConversationHandler.END
    st = ctx.user_data.get("tgp_single") or {}
    pwd = (update.message.text or "").strip()
    client = st.get("client")
    if not client:
        await _reply(update, "❌ Session expired. /cancel karo aur dobara shuru karo.")
        return await _bulk_fail_or_end(update, ctx)
    try:
        if not client.is_connected():
            await client.connect()
        await client.sign_in(password=pwd)
    except Exception as e:
        try: await client.disconnect()
        except: pass
        om._delete_session_files_by_phone(st["phone"])
        await _reply(update, f"❌ <b>2FA failed:</b> <code>{e}</code>")
        return await _bulk_fail_or_end(update, ctx)
    st["twofa"] = pwd or "None"
    return await _single_after_login(update, ctx)

async def _single_after_login(update, ctx):
    """After successful login: detect country + year, then continue."""
    st = ctx.user_data["tgp_single"]
    phone = st["phone"]
    c_name, c_icon = om.country_from_phone(phone)
    st["c_name"], st["c_icon"] = c_name, c_icon

    if c_name == "Unknown":
        await _reply(update,
            f"⚠️ <b>Country not recognized for +{phone}</b>\n\n"
            f"🏳️ Reply with the <b>country flag emoji</b>\n"
            f"<i>Example: 🇮🇳</i>")
        return AS_CFLAG

    # detect year
    await _reply(update, "⏳ <i>Detecting account year…</i>")
    try:
        auto_year = await om.detect_account_year(st["client"])
    except Exception:
        auto_year = datetime.now().year
    st["auto_year"] = auto_year
    try: await st["client"].disconnect()
    except: pass
    st["client"] = None

    await _reply(update,
        f"📅 <b>Detected Year:</b> <code>{auto_year}</code>\n\n"
        f"Reply with year to confirm or change (e.g. <code>2023</code>).\n"
        f"Send <code>ok</code> to accept the detected year.")
    return AS_YEAR

async def single_cflag(update, ctx):
    if not _is_admin(update.effective_user.id): return ConversationHandler.END
    st = ctx.user_data["tgp_single"]
    st["c_icon"] = (update.message.text or "").strip() or "🌍"
    await _reply(update,
        f"🌍 <b>Country name?</b>\n<i>Example: India, USA, Nigeria</i>")
    return AS_CNAME

async def single_cname(update, ctx):
    if not _is_admin(update.effective_user.id): return ConversationHandler.END
    st = ctx.user_data["tgp_single"]
    name = (update.message.text or "").strip()
    if not name:
        await _reply(update, "❌ Send a valid name."); return AS_CNAME
    st["c_name"] = name
    # persist custom country
    try:
        _countries().update_one({"name": name},
            {"$set": {"code": st["phone"][:3], "flag": st["c_icon"]}}, upsert=True)
    except Exception: pass

    await _reply(update, "⏳ <i>Detecting account year…</i>")
    try:
        auto_year = await om.detect_account_year(st["client"])
    except Exception:
        auto_year = datetime.now().year
    st["auto_year"] = auto_year
    try: await st["client"].disconnect()
    except: pass
    st["client"] = None
    await _reply(update,
        f"📅 <b>Detected Year:</b> <code>{auto_year}</code>\n\n"
        f"Reply with year (e.g. <code>2023</code>) or <code>ok</code> to accept.")
    return AS_YEAR

async def single_year(update, ctx):
    if not _is_admin(update.effective_user.id): return ConversationHandler.END
    st = ctx.user_data["tgp_single"]
    txt = (update.message.text or "").strip().lower()
    if txt in ("ok", "y", "yes", ""):
        year = st["auto_year"]
    else:
        try: year = int(re.sub(r"\D", "", txt))
        except: await _reply(update, "❌ Invalid year."); return AS_YEAR
    st["year"] = year

    # auto-price lookup
    ap = _auto_price(st["c_name"], year)
    if ap is not None:
        st["price"] = int(ap)
        await _reply(update,
            f"⚡ <b>Auto-Price Applied:</b> ₹{st['price']} for {st['c_name']} ({year})")
        return await _single_finish(update, ctx)

    existing = _stock().find_one({"country_name": st["c_name"]}, {"price": 1})
    if existing and existing.get("price") is not None:
        st["price"] = int(existing["price"])
        await _reply(update,
            f"⚡ <b>Auto-detected Price:</b> ₹{st['price']} (from existing {st['c_name']} stock)")
        return await _single_finish(update, ctx)

    await _reply(update,
        f"💰 <b>Price for {st['c_icon']} {st['c_name']} ({year})?</b>\n\n"
        f"Reply in ₹ (INR) — e.g. <code>15</code>")
    return AS_PRICE

async def single_price(update, ctx):
    if not _is_admin(update.effective_user.id): return ConversationHandler.END
    st = ctx.user_data["tgp_single"]
    try: st["price"] = int(re.sub(r"\D", "", update.message.text or ""))
    except: await _reply(update, "❌ Enter a number in ₹."); return AS_PRICE
    return await _single_finish(update, ctx)

async def _single_finish(update, ctx):
    st = ctx.user_data.pop("tgp_single", {})
    phone = st["phone"]
    sess_file = os.path.join(om.SESSIONS_DIR, f"{phone}.session")
    try:
        _upsert_stock(phone, sess_file, st["c_name"], st["c_icon"],
                      st["year"], st["price"], st.get("twofa", "None"))
    except Exception as e:
        await _reply(update, f"❌ <b>DB error:</b> <code>{e}</code>")
        return ConversationHandler.END
    await _reply(update,
        f"✅ <b>Added!</b>\n\n"
        f"📱 <b>Phone:</b> <code>+{phone}</code>\n"
        f"🏳️ <b>Country:</b> {(om.flag_html(st['c_name']) or '')} {st['c_name']}\n"
        f"📅 <b>Year:</b> {st['year']}\n"
        f"💰 <b>Price:</b> ₹{st['price']}\n"
        f"🔐 <b>2FA:</b> <code>{st.get('twofa','None')}</code>")
    # Public restock broadcast
    try:
        import sale_feed
        cid = EC.get_country_emoji(st["c_name"]) if hasattr(EC, "get_country_emoji") else ""
        stock_now = _live_count(st["c_name"], st["year"])
        await sale_feed.broadcast_restock(
            update.get_bot(),
            product_name=f"{st['c_name']} {st['year']} Accounts",
            stock=stock_now, price=f"₹{st['price']}",
            product_emoji=st.get("c_icon",""), product_emoji_id=cid,
            source="bot")
    except Exception as _e:
        logger.debug(f"sale_feed restock skip: {_e}")
    # If a bulk-phones queue is running, jump to the next phone
    if ctx.user_data.get("tgp_bulk_queue"):
        return await _bulk_next(update, ctx)
    return ConversationHandler.END

# ══════════════════════════════════════════════════════════════════════════════
# BULK PHONES FLOW — paste a CSV / list of phone numbers, bot iterates them
# one-by-one, asking OTP + 2FA per phone. Uses the single-account machinery.
# ══════════════════════════════════════════════════════════════════════════════
async def bulk_start(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id):
        return await q.answer("Access denied.", show_alert=True)
    await q.answer()
    ctx.user_data.pop("tgp_bulk_queue", None)
    ctx.user_data["tgp_bulk_stats"] = {"added": 0, "failed": 0, "total": 0}
    await _se(q,
        "<b>Add Bulk Phones</b>\n\n"
        "📝 Ek message me saare phone numbers bhejo — "
        "har line pe ek, ya comma-separated:\n"
        "<code>+919999999991\n+919999999992\n+919999999993</code>\n\n"
        "Bot har phone ke liye OTP request karega. Aap OTP (aur zaroorat "
        "ho to 2FA password) reply karoge. Country + year auto-detect honge.\n\n"
        "/cancel to abort anytime.",
        [[_btn("Cancel", "tg_panel", style="danger")]])
    return AB_LIST

async def bulk_list(update, ctx):
    if not _is_admin(update.effective_user.id): return ConversationHandler.END
    raw = (update.message.text or "")
    # Split by newline OR comma; strip; extract digits only
    parts = [p.strip() for p in re.split(r"[,\n;\s]+", raw) if p.strip()]
    phones = []
    for p in parts:
        d = re.sub(r"\D", "", p)
        if len(d) >= 6:
            phones.append(d)
    # De-duplicate, preserve order
    seen = set(); phones = [p for p in phones if not (p in seen or seen.add(p))]
    if not phones:
        await _reply(update, "❌ Koi valid phone nahi mila. Send phones, one per line."); return AB_LIST
    ctx.user_data["tgp_bulk_queue"] = phones
    ctx.user_data["tgp_bulk_stats"] = {"added": 0, "failed": 0, "total": len(phones)}
    await _reply(update, f"📋 <b>{len(phones)}</b> phones queued. Starting…")
    return await _bulk_next(update, ctx)

async def _bulk_next(update, ctx):
    q = ctx.user_data.get("tgp_bulk_queue") or []
    stats = ctx.user_data.get("tgp_bulk_stats") or {"added":0,"failed":0,"total":0}
    if not q:
        # done
        ctx.user_data.pop("tgp_bulk_queue", None)
        ctx.user_data.pop("tgp_bulk_stats", None)
        await _reply(update,
            f"✅ <b>Bulk Phones Complete</b>\n\n"
            f"➕ Added: <b>{stats['added']}</b>\n"
            f"❌ Failed: <b>{stats['failed']}</b>\n"
            f"📊 Total: <b>{stats['total']}</b>")
        return ConversationHandler.END
    phone = q.pop(0)
    idx = stats["total"] - len(q)
    await _reply(update,
        f"📱 <b>[{idx}/{stats['total']}]</b> Processing <code>+{phone}</code>…")
    # Emulate single_phone body
    if _stock().find_one({"phone": phone}, {"_id": 1}):
        await _reply(update, f"⚠️ <b>+{phone}</b> already in stock — skipping.")
        stats["failed"] += 1
        return await _bulk_next(update, ctx)
    sp = f"{om.SESSIONS_DIR}/{phone}"
    client = _tg_client(sp)
    try:
        await client.connect()
        sreq = await client.send_code_request(phone)
    except Exception as e:
        try: await client.disconnect()
        except: pass
        om._delete_session_files_by_phone(phone)
        await _reply(update, f"❌ <b>Login failed for +{phone}:</b> <code>{e}</code>")
        stats["failed"] += 1
        return await _bulk_next(update, ctx)
    ctx.user_data["tgp_single"] = {"phone": phone, "client": client,
                                    "phone_code_hash": sreq.phone_code_hash}
    await _reply(update,
        f"✅ OTP sent to <b>+{phone}</b>\n\n"
        f"🔢 Reply with the OTP code.  /cancel to abort bulk.")
    return AS_OTP


# ══════════════════════════════════════════════════════════════════════════════
# ZIP BULK FLOW
# ══════════════════════════════════════════════════════════════════════════════
async def zip_start(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id):
        return await q.answer("Access denied.", show_alert=True)
    await q.answer()
    ctx.user_data["tgp_zip"] = {}
    await _se(q,
        "<b>Add ZIP — Bulk Sessions</b>\n\n"
        "📦 <code>.session</code> files ka ek <code>.zip</code> upload karo.\n"
        "Bot har account ko scan karega:\n"
        "  • Phone / country / year → auto-detect\n"
        "  • Unknown country → flag + name puchega\n"
        "  • Has 2FA → password puchega\n"
        "  • Price → auto ya puchega\n\n"
        "/cancel to abort.",
        [[_btn("Cancel", "tg_panel", style="danger")]])
    return AZ_WAIT

async def zip_upload(update, ctx):
    if not _is_admin(update.effective_user.id): return ConversationHandler.END
    doc = update.message.document
    if not doc or not (doc.file_name or "").lower().endswith(".zip"):
        await _reply(update, "❌ Upload a <b>.zip</b> file. /cancel to abort.")
        return AZ_WAIT

    tmpdir = tempfile.mkdtemp(prefix="tgpzip_")
    zip_path = os.path.join(tmpdir, doc.file_name)
    tg_file = await doc.get_file()
    await tg_file.download_to_drive(zip_path)

    await _reply(update, "⏳ <b>Extracting & scanning accounts…</b>")

    extracted = os.path.join(tmpdir, "ex")
    os.makedirs(extracted, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(extracted)
    except zipfile.BadZipFile:
        await _reply(update, "❌ Bad zip file.")
        ctx.user_data.pop("tgp_zip", None)
        return ConversationHandler.END

    # scan every .session
    groups = {}
    from telethon.tl.functions.account import GetPasswordRequest
    for root, _dirs, files in os.walk(extracted):
        for f in files:
            if not f.endswith(".session"): continue
            sess_path = os.path.join(root, f)
            clean = sess_path[:-8]
            try:
                cli = _tg_client(clean)
                await cli.connect()
                if not await cli.is_user_authorized():
                    await cli.disconnect(); continue
                me = await cli.get_me()
                phone = getattr(me, "phone", None)
                if not phone: await cli.disconnect(); continue
                c_name, c_icon = om.country_from_phone(phone)
                pwd = await cli(GetPasswordRequest())
                has_2fa = bool(pwd.has_password)
                year = await om.detect_account_year(cli)
                await cli.disconnect()
                key = (c_name, year, has_2fa)
                groups.setdefault(key, []).append(
                    {"phone": phone, "clean": clean, "c_icon": c_icon})
            except Exception as e:
                logger.warning(f"scan {f}: {e}")

    if not groups:
        await _reply(update, "❌ No valid authorized sessions found in zip.")
        ctx.user_data.pop("tgp_zip", None)
        return ConversationHandler.END

    ctx.user_data["tgp_zip"] = {
        "tmpdir": tmpdir, "zip_path": zip_path,
        "groups": [{"key": list(k), "accs": v} for k, v in groups.items()],
        "gi": 0, "success": 0,
    }
    return await _zip_next_group(update, ctx)

async def _zip_next_group(update, ctx):
    """Walk each scanned group; ask flag/name if Unknown, then 2FA if needed,
    then price (auto or ask), then persist & advance."""
    st = ctx.user_data["tgp_zip"]
    if st["gi"] >= len(st["groups"]):
        return await _zip_finish(update, ctx)
    grp = st["groups"][st["gi"]]
    c_name, year, has_2fa = grp["key"]

    if c_name == "Unknown":
        sample = grp["accs"][0]["phone"]
        await _reply(update,
            f"⚠️ <b>Country not recognized for +{sample}</b>\n\n"
            f"🏳️ Reply with <b>flag emoji</b> for this batch ({len(grp['accs'])} accs)")
        return AZ_FLAG

    return await _zip_ask_2fa_or_price(update, ctx)

async def zip_flag(update, ctx):
    if not _is_admin(update.effective_user.id): return ConversationHandler.END
    st = ctx.user_data["tgp_zip"]
    grp = st["groups"][st["gi"]]
    flag = (update.message.text or "").strip() or "🌍"
    for a in grp["accs"]: a["c_icon"] = flag
    grp["_flag"] = flag
    await _reply(update, "🌍 <b>Country name?</b>\n<i>Example: India</i>")
    return AZ_NAME

async def zip_name(update, ctx):
    if not _is_admin(update.effective_user.id): return ConversationHandler.END
    st = ctx.user_data["tgp_zip"]
    grp = st["groups"][st["gi"]]
    name = (update.message.text or "").strip()
    if not name:
        await _reply(update, "❌ Send a valid name."); return AZ_NAME
    grp["key"][0] = name  # replace Unknown
    try:
        _countries().update_one({"name": name},
            {"$set": {"code": grp["accs"][0]["phone"][:3],
                      "flag": grp.get("_flag") or "🌍"}}, upsert=True)
    except Exception: pass
    return await _zip_ask_2fa_or_price(update, ctx)

async def _zip_ask_2fa_or_price(update, ctx):
    st = ctx.user_data["tgp_zip"]
    grp = st["groups"][st["gi"]]
    c_name, year, has_2fa = grp["key"]
    if has_2fa and "twofa" not in grp:
        await _reply(update,
            f"🔐 <b>Enter 2FA password</b> for {len(grp['accs'])}× "
            f"<b>{c_name}</b> ({year}) accounts:")
        return AZ_2FA
    grp.setdefault("twofa", "None")

    # price lookup
    ap = _auto_price(c_name, year)
    if ap is not None:
        grp["price"] = int(ap)
        await _reply(update,
            f"⚡ <b>Auto-Price:</b> {len(grp['accs'])}× {c_name} ({year}) → ₹{grp['price']}")
        return await _zip_persist_group(update, ctx)
    existing = _stock().find_one({"country_name": c_name}, {"price": 1})
    if existing and existing.get("price") is not None:
        grp["price"] = int(existing["price"])
        await _reply(update,
            f"⚡ <b>Auto-detected Price:</b> ₹{grp['price']} for {c_name} (from DB)")
        return await _zip_persist_group(update, ctx)

    await _reply(update,
        f"📌 Found <b>{len(grp['accs'])}× {c_name} ({year})</b>\n"
        f"💰 Reply with price in ₹")
    return AZ_PRICE

async def zip_2fa(update, ctx):
    if not _is_admin(update.effective_user.id): return ConversationHandler.END
    st = ctx.user_data["tgp_zip"]
    grp = st["groups"][st["gi"]]
    grp["twofa"] = (update.message.text or "").strip() or "None"
    return await _zip_ask_2fa_or_price(update, ctx)

async def zip_price(update, ctx):
    if not _is_admin(update.effective_user.id): return ConversationHandler.END
    st = ctx.user_data["tgp_zip"]
    grp = st["groups"][st["gi"]]
    try: grp["price"] = int(re.sub(r"\D", "", update.message.text or ""))
    except: await _reply(update, "❌ Enter a number."); return AZ_PRICE
    return await _zip_persist_group(update, ctx)

async def _zip_persist_group(update, ctx):
    import shutil
    st = ctx.user_data["tgp_zip"]
    grp = st["groups"][st["gi"]]
    c_name, year, _has_2fa = grp["key"]
    twofa = grp.get("twofa", "None")
    price = grp["price"]
    for a in grp["accs"]:
        phone = a["phone"].lstrip("+")
        dest_base = os.path.join(om.SESSIONS_DIR, phone)
        try:
            for ext in ('.session', '.session-wal', '.session-shm', '.session-journal'):
                src = a["clean"] + ext
                if os.path.exists(src): shutil.move(src, dest_base + ext)
        except Exception as e:
            logger.warning(f"move {phone}: {e}"); continue
        try:
            _upsert_stock(phone, dest_base + ".session", c_name,
                          a.get("c_icon", "🌍"), year, price, twofa)
            st["success"] += 1
        except Exception as e:
            logger.warning(f"insert {phone}: {e}")
    st["gi"] += 1
    return await _zip_next_group(update, ctx)

async def _zip_finish(update, ctx):
    import shutil
    st = ctx.user_data.pop("tgp_zip", {})
    try: shutil.rmtree(st.get("tmpdir", ""), ignore_errors=True)
    except: pass
    await _reply(update,
        f"✅ <b>Bulk Upload Complete!</b>\n\n"
        f"➕ Added: <b>{st.get('success',0)}</b>")
    # Public restock broadcast — one per (country, year) group
    try:
        import sale_feed
        for grp in (st.get("groups") or []):
            c_name, year, _ = grp["key"]
            price = grp.get("price", "")
            cid = EC.get_country_emoji(c_name) if hasattr(EC, "get_country_emoji") else ""
            c_icon = ""
            if grp.get("accs"):
                c_icon = grp["accs"][0].get("c_icon", "") or ""
            stock_now = _live_count(c_name, year)
            await sale_feed.broadcast_restock(
                update.get_bot(),
                product_name=f"{c_name} {year} Accounts",
                stock=stock_now, price=(f"₹{price}" if price else ""),
                product_emoji=c_icon, product_emoji_id=cid, source="bot")
    except Exception as _e:
        logger.debug(f"sale_feed zip restock skip: {_e}")
    return ConversationHandler.END

async def addstock_cancel(update, ctx):
    for k in ("tgp_single", "tgp_zip", "tgp_addstock", "tgp_bulk_queue", "tgp_bulk_stats"):
        ctx.user_data.pop(k, None)
    if update.message:
        await update.message.reply_text("Cancelled.")
    elif update.callback_query:
        await update.callback_query.answer("Cancelled.")
    return ConversationHandler.END


# ── MANAGE STOCK ─────────────────────────────────────────────────────────────
async def manage_cb(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id): return await q.answer("Access denied.", show_alert=True)
    await q.answer()
    rows = [
        (g["_id"], g.get("icon") or "", g.get("live") or 0, g.get("used") or 0)
        for g in _stock().aggregate([
            {"$group": {"_id": "$country_name",
                        "icon": {"$first": "$country_icon"},
                        "live": {"$sum": {"$cond": [{"$eq": ["$available", 1]}, 1, 0]}},
                        "used": {"$sum": {"$cond": [{"$ne": ["$available", 1]}, 1, 0]}}}},
            {"$sort": {"_id": 1}},
        ])
    ]
    if not rows:
        return await _se(q, "<b>No stock in database.</b>",
                         [[_btn("Back to TG Panel", "tg_panel", style="primary")]])
    btns = []
    for name, icon, av, sold in rows:
        eid = EC.get_country_emoji(name)
        btns.append([_btn(f"{name}   ({av or 0} live / {sold or 0} used)",
                          f"tgp_country|{name}", style="primary", emoji_id=eid or None)])
    btns.append(_back_row())
    await _se(q, "<b>Manage Stock — pick a country</b>", btns)

async def country_cb(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id): return await q.answer("Access denied.", show_alert=True)
    await q.answer()
    country = q.data.split("|", 1)[1]
    yrs = [
        (g["_id"]["y"], g["_id"]["p"], g.get("live") or 0)
        for g in _stock().aggregate([
            {"$match": {"country_name": country}},
            {"$group": {"_id": {"y": "$account_year", "p": "$price"},
                        "live": {"$sum": {"$cond": [{"$eq": ["$available", 1]}, 1, 0]}}}},
            {"$sort": {"_id.y": -1, "_id.p": 1}},
        ])
    ]
    if not yrs:
        return await _se(q, f"<b>No stock left for {country}.</b>",
                         [[_btn("Back", "tgp_manage", style="primary")]])
    _fh = om.flag_html(country)
    text = f"{(_fh + ' ') if _fh else ''}<b>{country}</b>\n\n"
    for y, p, cnt in yrs:
        text += f"• {y}  ₹{p}  →  <b>{cnt or 0}</b> live\n"
    btns = [
        [_btn("Clear ALL for country", f"tgp_clear|{country}", style="danger")],
        [_btn("Mark all AVAILABLE",    f"tgp_reavail|{country}", style="success"),
         _btn("Mark all USED",         f"tgp_unavail|{country}", style="danger")],
        _back_row(),
    ]
    await _se(q, text, btns)

async def clear_cb(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id): return await q.answer("Access denied.", show_alert=True)
    country = q.data.split("|", 1)[1]
    for d in _stock().find({"country_name": country}, {"session_file": 1}):
        sf = d.get("session_file")
        if sf: om._delete_session_files(sf)
    _stock().delete_many({"country_name": country})
    await q.answer(f"Cleared {country}", show_alert=True)
    await manage_cb(update, ctx)

async def reavail_cb(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id): return await q.answer("Access denied.", show_alert=True)
    country = q.data.split("|", 1)[1]
    _stock().update_many({"country_name": country}, {"$set": {"available": 1}})
    await q.answer("Marked available.", show_alert=True)
    await country_cb(update, ctx)

async def unavail_cb(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id): return await q.answer("Access denied.", show_alert=True)
    country = q.data.split("|", 1)[1]
    _stock().update_many({"country_name": country}, {"$set": {"available": 0}})
    await q.answer("Marked used.", show_alert=True)
    await country_cb(update, ctx)

# ── AUTO-PRICE ───────────────────────────────────────────────────────────────
PR_INPUT = 8601

async def prices_cb(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id): return await q.answer("Access denied.", show_alert=True)
    await q.answer()
    rows = [(d.get("country"), d.get("year"), d.get("price"))
            for d in _prices().find({}, {"country": 1, "year": 1, "price": 1})
                              .sort([("country", 1), ("year", 1)])]
    if rows:
        lines = "\n".join(f"• {c} • {y} → ₹{p}" for c, y, p in rows)
    else:
        lines = "<i>No auto-prices set.</i>"
    await _se(q,
        f"<b>Auto-Price Rules</b>\n\n{lines}\n\n"
        f"Reply with a new rule:\n"
        f"<code>Country | Year | Price</code>\n"
        f"Use <code>*</code> for wildcard year, e.g. <code>India | * | 20</code>",
        [[_btn("Add / Update Rule", "tgp_price_add", style="success")],
         [_btn("Clear All",         "tgp_price_clear", style="danger")],
         _back_row()])

async def price_add_start(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id): return await q.answer("Access denied.", show_alert=True)
    await q.answer()
    await _se(q,
        "<b>Add / Update Auto-Price</b>\n\n"
        "Reply with:\n<code>Country | Year | Price</code>\n\n"
        "Year can be <code>*</code> to match any year.\n/cancel to abort.",
        [[_btn("Cancel", "tgp_prices", style="danger")]])
    return PR_INPUT

async def price_input(update, ctx):
    if not _is_admin(update.effective_user.id): return ConversationHandler.END
    parts = [p.strip() for p in (update.message.text or "").split("|")]
    if len(parts) != 3:
        await update.message.reply_text("Format: Country | Year | Price")
        return PR_INPUT
    country, year, price = parts[0], parts[1], parts[2]
    try: price = int(price)
    except:
        await update.message.reply_text("Price must be a number."); return PR_INPUT
    _prices().update_one({"country": country, "year": str(year)},
                         {"$set": {"price": int(price)}}, upsert=True)
    # apply immediately
    if year in ("*", "Common"):
        _stock().update_many({"country_name": country},
                             {"$set": {"price": int(price)}})
    else:
        try:
            yr = int(year)
        except (TypeError, ValueError):
            yr = year
        _stock().update_many({"country_name": country, "account_year": yr},
                             {"$set": {"price": int(price)}})
    await update.message.reply_html(EC.apply_premium_emoji(
        f"✅ Rule saved and applied: <b>{country} • {year} → ₹{price}</b>"))
    return ConversationHandler.END

async def price_clear(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id): return await q.answer("Access denied.", show_alert=True)
    _prices().delete_many({})
    await q.answer("Cleared.", show_alert=True)
    await prices_cb(update, ctx)

# ── TEST SESSIONS ────────────────────────────────────────────────────────────
async def test_cb(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id): return await q.answer("Access denied.", show_alert=True)
    await q.answer("Starting…")
    await _se(q, "<b>Testing all available sessions…</b>\n<i>This may take a while.</i>",
              [[_btn("Back", "tg_panel", style="primary")]])
    try:
        from telethon import TelegramClient
    except ImportError:
        return await _se(q, "<b>Telethon not installed.</b>",
                         [[_btn("Back", "tg_panel", style="primary")]])
    rows = [(d.get("phone"), d.get("session_file") or "")
            for d in _stock().find({"available": 1}, {"phone": 1, "session_file": 1})]
    alive = dead = 0
    dead_list = []
    for phone, sess in rows:
        base = sess[:-8] if sess.endswith(".session") else sess
        try:
            cli = TelegramClient(base, om.API_ID, om.API_HASH)
            await cli.connect()
            ok = await cli.is_user_authorized()
            await cli.disconnect()
        except Exception:
            ok = False
        if ok: alive += 1
        else:  dead  += 1; dead_list.append(phone)
    ctx.chat_data["tgp_dead"] = dead_list
    txt = (f"<b>Session Test Complete</b>\n\n"
           f"✅ Alive: <b>{alive}</b>\n❌ Dead:  <b>{dead}</b>\n\n"
           f"Dead sessions are still in stock. "
           f"Tap <b>Delete Dead</b> to remove them.")
    await _se(q, txt,
        [[_btn("Delete Dead now",  "tgp_del_dead", style="danger")],
         _back_row()])

async def del_dead_cb(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id): return await q.answer("Access denied.", show_alert=True)
    await q.answer("Deleting…")
    dead_list = ctx.chat_data.get("tgp_dead") or []
    if not dead_list:
        # fallback: recompute
        try:
            from telethon import TelegramClient
            rows = [(d.get("phone"), d.get("session_file") or "")
            for d in _stock().find({"available": 1}, {"phone": 1, "session_file": 1})]
            for phone, sess in rows:
                base = sess[:-8] if sess.endswith(".session") else sess
                try:
                    cli = TelegramClient(base, om.API_ID, om.API_HASH)
                    await cli.connect(); ok = await cli.is_user_authorized(); await cli.disconnect()
                except: ok = False
                if not ok: dead_list.append(phone)
        except ImportError: pass
    n = 0
    for phone in dead_list:
        row = _stock().find_one({"phone": phone}, {"session_file": 1})
        if row and row.get("session_file"):
            om._delete_session_files(row["session_file"])
        _stock().delete_one({"phone": phone})
        n += 1
    ctx.chat_data.pop("tgp_dead", None)
    await _se(q, f"<b>Deleted {n} dead session(s).</b>",
              [[_btn("Back to TG Panel", "tg_panel", style="primary")]])

# ── STATS ────────────────────────────────────────────────────────────────────
async def stats_cb(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id): return await q.answer("Access denied.", show_alert=True)
    await q.answer()
    today = datetime.now().strftime("%Y-%m-%d")
    week  = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    t_c, t_r = _sum_price({"created_at": {"$regex": f"^{today}"}})
    w_c, w_r = _sum_price({"created_at": {"$gte": week}})
    a_c, a_r = _sum_price()
    per_country = [
        (g["_id"], g.get("cnt") or 0, g.get("rev") or 0)
        for g in _orders().aggregate([
            {"$group": {"_id": "$country", "cnt": {"$sum": 1},
                        "rev": {"$sum": "$price"}}},
            {"$sort": {"rev": -1}},
            {"$limit": 8},
        ])
    ]
    tbl = "\n".join(f"• {c}: {cnt} × ₹{r or 0}" for c, cnt, r in per_country) or "<i>no data</i>"
    txt = (f"<b>OTP Stats</b>\n\n"
           f"📅 <b>Today:</b> {t_c} sales   →   ₹{t_r}\n"
           f"📊 <b>7 days:</b> {w_c} sales   →   ₹{w_r}\n"
           f"📈 <b>All time:</b> {a_c} sales   →   ₹{a_r}\n\n"
           f"<b>Top countries:</b>\n{tbl}")
    await _se(q, txt, [_back_row()])

# ── 2FA MANAGER ──────────────────────────────────────────────────────────────
TF_INPUT = 8701

async def twofa_cb(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id): return await q.answer("Access denied.", show_alert=True)
    await q.answer()
    counts = [(g["_id"], g.get("cnt") or 0)
              for g in _stock().aggregate([
                  {"$group": {"_id": "$twofa", "cnt": {"$sum": 1}}},
                  {"$sort": {"cnt": -1}},
              ])]
    lines = "\n".join(f"• <code>{t or 'None'}</code> — {c}" for t, c in counts) or "<i>no data</i>"
    await _se(q,
        f"<b>2FA Manager</b>\n\n"
        f"Current distribution:\n{lines}\n\n"
        f"To edit, reply with:\n<code>+phone | new_password</code>\n"
        f"Use <code>None</code> to clear 2FA.",
        [[_btn("Edit 2FA", "tgp_2fa_edit", style="primary")],
         _back_row()])

async def twofa_edit_start(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id): return await q.answer("Access denied.", show_alert=True)
    await q.answer()
    await _se(q, "<b>Edit 2FA</b>\n\nReply: <code>+phone | new_password</code>\n/cancel to abort.",
              [[_btn("Cancel", "tgp_2fa", style="danger")]])
    return TF_INPUT

async def twofa_edit_input(update, ctx):
    if not _is_admin(update.effective_user.id): return ConversationHandler.END
    parts = [p.strip() for p in (update.message.text or "").split("|")]
    if len(parts) != 2:
        await update.message.reply_text("Format: +phone | password"); return TF_INPUT
    phone = parts[0].lstrip("+")
    pwd = parts[1] or "None"
    changed = _stock().update_many({"phone": phone},
                                   {"$set": {"twofa": pwd}}).matched_count
    await update.message.reply_html(EC.apply_premium_emoji(
        f"{'✅' if changed else '❌'} <b>{'Updated' if changed else 'Not found'}:</b> +{phone}"))
    return ConversationHandler.END

# ── SESSIONS FOLDER ──────────────────────────────────────────────────────────
async def folder_cb(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id): return await q.answer("Access denied.", show_alert=True)
    await q.answer()
    files = sorted(f for f in os.listdir(om.SESSIONS_DIR) if f.endswith(".session"))
    total = len(files)
    show = files[:30]
    lines = "\n".join(f"• <code>{f}</code>" for f in show) or "<i>empty</i>"
    more  = f"\n\n<i>... and {total-30} more</i>" if total > 30 else ""
    await _se(q,
        f"<b>Sessions Folder</b>  <code>{om.SESSIONS_DIR}/</code>\n\n"
        f"Total: <b>{total}</b>\n\n{lines}{more}",
        [_back_row()])

# ── USDT⇄INR RATE ────────────────────────────────────────────────────────────
RT_INPUT = 8801

async def rate_cb(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id): return await q.answer("Access denied.", show_alert=True)
    await q.answer()
    r = om.get_usdt_rate()
    await _se(q,
        f"<b>USDT ⇄ INR Rate</b>\n\n"
        f"Current: <b>1 USDT = ₹{r}</b>\n\n"
        f"Ye rate OTP prices (₹) ko user ke USDT wallet se debit/credit karne me use hota hai.",
        [[_btn("Change Rate", "tgp_rate_set", style="primary")],
         _back_row()])

async def rate_set_start(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id): return await q.answer("Access denied.", show_alert=True)
    await q.answer()
    await _se(q, "<b>New USDT→INR rate</b>\n\nReply with a number (e.g. <code>94.5</code>).\n/cancel to abort.",
              [[_btn("Cancel", "tgp_rate", style="danger")]])
    return RT_INPUT

async def rate_set_input(update, ctx):
    if not _is_admin(update.effective_user.id): return ConversationHandler.END
    try:
        v = float((update.message.text or "").strip())
        assert v > 0
    except:
        await update.message.reply_text("Send a positive number."); return RT_INPUT
    om.set_setting("usdt_rate", v)
    await update.message.reply_html(EC.apply_premium_emoji(f"✅ Rate updated to <b>₹{v}</b> per USDT."))
    return ConversationHandler.END

# ── REGISTRATION ─────────────────────────────────────────────────────────────
def register(app: Application):
    # conversations first (they take priority for text messages)
    addstock_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(addstock_start, pattern=r"^tgp_addstock$")],
        states={
            AS_MENU:  [CallbackQueryHandler(single_start, pattern=r"^tgp_add_single$"),
                       CallbackQueryHandler(bulk_start,   pattern=r"^tgp_add_bulk$"),
                       CallbackQueryHandler(zip_start,    pattern=r"^tgp_add_zip$")],
            AB_LIST:  [MessageHandler(filters.TEXT & ~filters.COMMAND, bulk_list)],
            AS_PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, single_phone)],
            AS_OTP:   [MessageHandler(filters.TEXT & ~filters.COMMAND, single_otp)],
            AS_2FA:   [MessageHandler(filters.TEXT & ~filters.COMMAND, single_2fa)],
            AS_CFLAG: [MessageHandler(filters.TEXT & ~filters.COMMAND, single_cflag)],
            AS_CNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, single_cname)],
            AS_YEAR:  [MessageHandler(filters.TEXT & ~filters.COMMAND, single_year)],
            AS_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, single_price)],
            AZ_WAIT:  [MessageHandler(filters.Document.ALL, zip_upload)],
            AZ_FLAG:  [MessageHandler(filters.TEXT & ~filters.COMMAND, zip_flag)],
            AZ_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, zip_name)],
            AZ_2FA:   [MessageHandler(filters.TEXT & ~filters.COMMAND, zip_2fa)],
            AZ_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, zip_price)],
        },
        fallbacks=[CommandHandler("cancel", addstock_cancel),
                   CallbackQueryHandler(addstock_cancel, pattern=r"^tg_panel$")],
        per_message=False, name="tgp_addstock_conv",
    )

    price_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(price_add_start, pattern=r"^tgp_price_add$")],
        states={PR_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, price_input)]},
        fallbacks=[CommandHandler("cancel", addstock_cancel),
                   CallbackQueryHandler(addstock_cancel, pattern=r"^tgp_prices$")],
        per_message=False, name="tgp_price_conv",
    )
    twofa_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(twofa_edit_start, pattern=r"^tgp_2fa_edit$")],
        states={TF_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, twofa_edit_input)]},
        fallbacks=[CommandHandler("cancel", addstock_cancel),
                   CallbackQueryHandler(addstock_cancel, pattern=r"^tgp_2fa$")],
        per_message=False, name="tgp_2fa_conv",
    )
    rate_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(rate_set_start, pattern=r"^tgp_rate_set$")],
        states={RT_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND, rate_set_input)]},
        fallbacks=[CommandHandler("cancel", addstock_cancel),
                   CallbackQueryHandler(addstock_cancel, pattern=r"^tgp_rate$")],
        per_message=False, name="tgp_rate_conv",
    )
    app.add_handler(addstock_conv)
    app.add_handler(price_conv)
    app.add_handler(twofa_conv)
    app.add_handler(rate_conv)

    for pat, fn in [
        (r"^tg_panel$",           tg_panel_cb),
        (r"^tgp_manage$",         manage_cb),
        (r"^tgp_country\|",       country_cb),
        (r"^tgp_clear\|",         clear_cb),
        (r"^tgp_reavail\|",       reavail_cb),
        (r"^tgp_unavail\|",       unavail_cb),
        (r"^tgp_prices$",         prices_cb),
        (r"^tgp_price_clear$",    price_clear),
        (r"^tgp_test$",           test_cb),
        (r"^tgp_del_dead$",       del_dead_cb),
        (r"^tgp_stats$",          stats_cb),
        (r"^tgp_2fa$",            twofa_cb),
        (r"^tgp_folder$",         folder_cb),
        (r"^tgp_rate$",           rate_cb),
    ]:
        app.add_handler(CallbackQueryHandler(fn, pattern=pat))
    logger.info("otp_admin (TG Panel) handlers registered")
