"""
BASS TG STORE — v5.1
Features:
  - Multi-language: EN / HI / ID / VI  (selected on FIRST start before anything)
  - Force-join up to 5 channels (admin-configurable)
  - BEP20 + TRC20 + Binance Pay auto-deposit (100+ concurrent users supported)
  - Collision-free unique offset system (99+ slots per amount)
  - QR code shown for deposit address
  - Binance Pay ID deposit: users send directly to store's Binance Pay ID
  - Logs channel: stock added, deposits, buys
  - Full admin panel: today/alltime stats, broadcast, manual deposit, user history
"""

import asyncio
import io
import logging
import os
import random
import urllib.parse
from datetime import datetime, timedelta

import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, filters
)

import config
import database as db
import features as feat
from lang import T, LANG_OPTIONS
import styled_api
import emoji_config as EC

logging.basicConfig(format="%(asctime)s %(levelname)s %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

# ── GLOBAL PREMIUM EMOJI PATCH ────────────────────────────────────────────────
# Applies to EVERY outgoing message/caption in the whole file automatically —
# no need to edit each of the 200+ call sites individually. Text/captions get
# wrapped via EC.apply_premium_emoji(). No-op until IDs are filled in inside
# emoji_config.py.
#
# NOTE: injecting icon_custom_emoji_id into raw InlineKeyboardButton objects
# was removed — on this PTB version, Telegram echoes that field back inside
# the callback_query's message.reply_markup on every button tap, and PTB has
# to reconstruct (de_json) that button again to process the tap. That
# reconstruction crashed or silently dropped the update, which is why
# Binance Pay (and other buttons) stopped responding. Button icons for the
# MAIN MENU still work fine because those go through styled_api's raw HTTP
# call (see sb() / styled_api.btn()), which is a separate, safe path.
import functools as _functools
import inspect as _inspect

def _premium_text_wrap(*field_names):
    def decorator(func):
        sig = _inspect.signature(func)
        @_functools.wraps(func)
        async def wrapper(*args, **kwargs):
            try:
                bound = sig.bind_partial(*args, **kwargs)
                bound.apply_defaults()
                pm = bound.arguments.get("parse_mode")
                pm = str(pm).upper() if pm else ""
                if "HTML" in pm:
                    for f in field_names:
                        if f in bound.arguments and bound.arguments[f]:
                            bound.arguments[f] = EC.apply_premium_emoji(bound.arguments[f])
                args, kwargs = bound.args, bound.kwargs
            except Exception:
                pass
            return await func(*args, **kwargs)
        return wrapper
    return decorator

from telegram import Bot as _Bot
_Bot.send_message        = _premium_text_wrap("text")(_Bot.send_message)
_Bot.edit_message_text   = _premium_text_wrap("text")(_Bot.edit_message_text)
_Bot.edit_message_caption = _premium_text_wrap("caption")(_Bot.edit_message_caption)
_Bot.send_photo          = _premium_text_wrap("caption")(_Bot.send_photo)
# ── END GLOBAL PREMIUM EMOJI PATCH ────────────────────────────────────────────

# ── Conversation States ───────────────────────────────────────────────────────
(
    DEP_NETWORK,
    DEP_AMOUNT,
    TICKET_MSG,
    TICKET_REPLY,
    ADM_CAT_NAME,   ADM_CAT_EMOJI,
    ADM_PRD_CAT,    ADM_PRD_NAME,   ADM_PRD_EMOJI,
    ADM_PRD_DESC,   ADM_PRD_PRICE,  ADM_PRD_DUR,    ADM_PRD_EMOJI_ID,
    ADM_STOCK_PID,  ADM_STOCK_DATA,
    ADM_BROADCAST,
    ADM_ADDBAL_UID, ADM_ADDBAL_AMT,
    ADM_REMBAL_UID, ADM_REMBAL_AMT,
    ADM_WELCOME,
    ADM_BOT_NAME,   ADM_BOT_EMOJI,
    ADM_USDT_TRC20, ADM_USDT_BEP20,
    ADM_ADD_CH,     ADM_ADD_CH_URL,
    ADM_ADD_ADMIN,  ADM_REM_ADMIN,
    ADM_SEARCH_USER,
    ADM_LOG_CH,
    ADM_MIN_DEP,
    ADM_LOW_STOCK,
    ADM_REAL_BROADCAST,
    ADM_MANUAL_DEP_UID, ADM_MANUAL_DEP_AMT, ADM_MANUAL_DEP_TXID,
    ADM_USER_HIST,
    DEP_PAY_AMOUNT,       # Binance Pay deposit amount input
    ADM_BINANCE_PAY_ID,   # Admin sets store's Binance Pay ID
    USER_REFUND_REASON,   # User types refund reason
    DEP_TX_HASH,          # User submits blockchain TX hash
    ADM_TRC20_QR,         # Admin sets TRC20 QR image URL
    ADM_BEP20_QR,         # Admin sets BEP20 QR image URL
    ADM_PAY_QR,           # Admin sets Binance Pay QR image URL
    ADM_DEP_LOG_CH,       # Admin sets deposit-only log channel ID
    ADM_FREE_NAME,        # Admin: new free item name
    ADM_FREE_EMOJI,       # Admin: new free item emoji
    ADM_FREE_STOCK,       # Admin: paste free item stock/codes
    ADM_DAILY_DATE,       # Admin: type a custom date for the daily report
    CART_COUPON_CODE,     # User: types a coupon code to apply at checkout
    USER_REFUND_REPLY,    # User: sends a follow-up chat message about a refund request
    ADM_REFUND_REPLY,     # Admin: sends a chat message about a refund request
    FIND_ORDER_CODE,      # User: types an Order ID to look up (Find Order feature)
) = range(54)

AE = {"allow_reentry": True, "conversation_timeout": 300}   # 5 min — auto-clear abandoned conversations

# Only matches text that looks like a plain number (optionally with $ , .) —
# used for every "enter an amount" conversation state below. Without this,
# these states used a blanket filters.TEXT, which meant that if this
# conversation was still technically "active" for a user (e.g. they never
# finished it), ANY text they typed afterwards for a COMPLETELY DIFFERENT
# flow (a ticket description, a category name, etc.) got swallowed here
# first and shown "Invalid amount" instead of reaching the flow they
# actually wanted — this was the root cause of "sab kuch ulta ho raha hai".
NUM_FILTER = filters.Regex(r'^\s*\$?[\d][\d,]*\.?\d*\s*$')

# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def is_admin(uid: int) -> bool:
    return uid in config.ADMIN_IDS or uid in db.get_extra_admins()

def bot_name()  -> str: return db.get_setting("bot_name",  config.BOT_NAME)
def bot_emoji() -> str: return db.get_setting("bot_emoji", "💎")

def usdt_trc20() -> str:
    return db.get_setting("usdt_trc20", "") or getattr(config, "USDT_TRC20_ADDRESS", "") or getattr(config, "USDT_DEPOSIT_ADDRESS", "")

def usdt_bep20() -> str:
    return db.get_setting("usdt_bep20", "") or getattr(config, "USDT_BEP20_ADDRESS", "")

def binance_pay_id() -> str:
    """Return the store's Binance Pay ID (numeric UID) if configured."""
    return db.get_setting("binance_pay_id", "") or getattr(config, "BINANCE_PAY_ID", "")

def trc20_qr_url() -> str:
    return db.get_setting("trc20_qr_url", "")

def bep20_qr_url() -> str:
    return db.get_setting("bep20_qr_url", "")

def pay_qr_url() -> str:
    return db.get_setting("pay_qr_url", "")

def get_lang(uid: int) -> str:
    return db.get_user_language(uid) or "en"

def fmt(amount: float) -> str:
    return f"${amount:.2f} USDT"

def sep() -> str:
    return "━━━━━━━━━━━━━━━━━━━━━━"

def loyalty_tier(spent: float):
    if spent >= config.TIER_GOLD_MIN:   return "Gold",   config.TIER_GOLD_DISC,   "🥇"
    if spent >= config.TIER_SILVER_MIN: return "Silver", config.TIER_SILVER_DISC, "🥈"
    return "Bronze", 0, "🥉"

def effective_disc(u: dict) -> int:
    _, td, _ = loyalty_tier(u.get("total_spent_usdt", 0))
    vd = config.VIP_DISCOUNT_PERCENT if u.get("is_vip") else 0
    return max(td, vd)

def apply_disc(price: float, pct: int) -> float:
    return round(price * (1 - pct / 100), 6)

def user_display(u: dict) -> str:
    name = u.get("full_name") or u.get("username") or str(u.get("user_id", "?"))
    uname = f"@{u['username']}" if u.get("username") else "—"
    return name, uname

async def log_ch(bot, text: str):
    """Send a message to the admin log channel."""
    cid = db.get_setting("log_channel_id", "") or getattr(config, "LOG_CHANNEL_ID", None)
    if not cid:
        return
    try:
        cid = int(cid)
        await bot.send_message(cid, text, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"log_ch error: {e}")

async def log_dep(bot, text: str):
    """Send a message to the DEPOSIT-ONLY log channel/group (falls back to the
    general log channel if a dedicated one isn't configured)."""
    cid = db.get_setting("deposit_log_channel_id", "") or db.get_setting("log_channel_id", "") \
          or getattr(config, "LOG_CHANNEL_ID", None)
    if not cid:
        return
    try:
        cid = int(cid)
        await bot.send_message(cid, text, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"log_dep error: {e}")

# ─────────────────────────────────────────────────────────────────────────────
#  STYLED BUTTON HELPERS  (coloured buttons + premium emoji icons)
# ─────────────────────────────────────────────────────────────────────────────

#  NOTE: Telegram's Bot API has no official way to change an inline button's
#  background colour — the "style"/"icon_custom_emoji_id" fields we still send
#  to the raw HTTP endpoint are NOT part of the real Bot API and Telegram just
#  ignores them. The only colour Telegram actually renders on a button is
#  whatever's inside the button TEXT. So real "colourful buttons" are done
#  here by auto-prefixing a coloured circle emoji (🟢/🔵/🔴) based on `style`,
#  unless the text already starts with its own emoji/symbol.
_STYLE_EMOJI = {"success": "🟢", "primary": "🔵", "danger": "🔴"}

def sb(text: str, callback_data: str = None, *,
       url: str = None, style: str = None, emoji_id: str = None) -> dict:
    """
    Build one colourful inline-keyboard button dict.

    style    : "success" (green) | "primary" (blue) | "danger" (red)
               → auto-prefixes a coloured circle emoji if text has none yet.
    emoji_id : premium custom emoji ID from emoji_config.py  (leave "" to skip)

    Example:
        sb("Buy Now",  "buy_123",  style="success",  emoji_id=EC.E_BUY)
        sb("Back",     "home",     style="danger",   emoji_id=EC.E_HOME)
    """
    # Coloured circle auto-prefix disabled — buttons show only the premium
    # emoji_id icon (if configured in emoji_config.py) or plain text (if not).
    resolved_emoji = emoji_id if emoji_id else EC.get_btn_emoji(callback_data)
    return styled_api.btn(text, callback_data, url=url,
                          style=style or None,
                          emoji_id=resolved_emoji if resolved_emoji else None)

async def se(q, text: str, rows: list, parse_mode: str = "HTML"):
    """
    Styled Edit — replaces q.edit_message_text() with coloured buttons.
    Detects whether the message being edited is a PHOTO (e.g. a QR-code
    deposit screen) or plain text, and uses the matching Telegram method —
    editMessageText fails with "there is no text in the message to edit" on
    photo messages, which was silently swallowing button taps like
    "Back to Wallet" / "Binance Pay" on those screens.
    Falls back to plain PTB keyboard if the styled API call fails, and as a
    last resort sends a brand-new message instead of doing nothing.
    """
    is_photo = bool(q.message and q.message.photo)
    if is_photo:
        result = await styled_api.edit_caption(
            q.message.chat_id, q.message.message_id, text, rows, parse_mode)
    else:
        result = await styled_api.edit(
            q.message.chat_id, q.message.message_id, text, rows, parse_mode)

    if not result.get("ok"):
        ptb_rows = [
            [InlineKeyboardButton(b["text"],
                                  callback_data=b.get("callback_data", "noop"),
                                  url=b.get("url"))
             for b in row]
            for row in rows
        ]
        kb = InlineKeyboardMarkup(ptb_rows)
        etext = EC.apply_premium_emoji(text)
        try:
            if is_photo:
                await q.edit_message_caption(caption=etext, reply_markup=kb, parse_mode=parse_mode)
            else:
                await q.edit_message_text(etext, reply_markup=kb, parse_mode=parse_mode)
        except Exception as e:
            logger.warning(f"se() edit failed, sending new message instead: {e}")
            try:
                await q.message.reply_html(etext, reply_markup=kb)
            except Exception:
                pass

async def safe_edit(q, text: str, reply_markup=None, parse_mode: str = "HTML"):
    """Edit the callback's message whether it's a plain text message OR a
    photo message (e.g. a QR-code caption) — Telegram's edit_message_text()
    throws 'there is no text in the message to edit' on photo messages, which
    was silently swallowing the user-facing confirmation after 'I Have Paid'.
    Falls back to sending a brand-new message if editing isn't possible at all."""
    text = EC.apply_premium_emoji(text)
    try:
        if q.message and q.message.photo:
            await q.edit_message_caption(caption=text, reply_markup=reply_markup, parse_mode=parse_mode)
        else:
            await q.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception as e:
        logger.warning(f"safe_edit fallback (sending new message): {e}")
        try:
            await q.message.reply_html(text, reply_markup=reply_markup)
        except Exception:
            pass

async def ss(message, text: str, rows: list, parse_mode: str = "HTML"):
    """
    Styled Send — replaces message.reply_html() with coloured buttons.
    Falls back to plain PTB keyboard if the styled API call fails.
    """
    result = await styled_api.send(message.chat_id, text, rows, parse_mode)
    if not result.get("ok"):
        ptb_rows = [
            [InlineKeyboardButton(b["text"],
                                  callback_data=b.get("callback_data", "noop"),
                                  url=b.get("url"))
             for b in row]
            for row in rows
        ]
        await message.reply_html(text, reply_markup=InlineKeyboardMarkup(ptb_rows))

# ── Force-Join Channels ───────────────────────────────────────────────────────
def get_force_channels():
    """Returns list of (handle, url) tuples for active force-join channels (up to 5)."""
    channels = []
    for n in range(1, 6):
        h = db.get_setting(f"force_join_ch{n}", "").strip()
        u = db.get_setting(f"force_join_url{n}", "").strip()
        if h:
            channels.append((h, u or f"https://t.me/{h.lstrip('@')}"))
    return channels

async def check_force_join(uid: int, bot) -> bool:
    """Returns True if user has joined all required channels (or no channels required)."""
    channels = get_force_channels()
    if not channels:
        return True
    for handle, _ in channels:
        try:
            m = await bot.get_chat_member(handle, uid)
            if m.status in ("left", "kicked"):
                return False
        except Exception:
            pass  # If we can't check, assume joined (avoids blocking users)
    return True

# ── Credential Formatter ──────────────────────────────────────────────────────
def parse_credential(data: str, lang: str) -> str:
    """Format credential for display with spoiler protection (tap-to-reveal)."""
    if ":" in data:
        parts = data.split(":", 1)
        return (f"📧 <b>Email:</b> <tg-spoiler><code>{parts[0]}</code></tg-spoiler>\n"
                f"🔑 <b>Password:</b> <tg-spoiler><code>{parts[1]}</code></tg-spoiler>")
    if data.startswith("http"):
        return f"🔗 <b>Activation Link:</b>\n<tg-spoiler><code>{data}</code></tg-spoiler>"
    return f"📦 <b>Data:</b>\n<tg-spoiler><code>{data}</code></tg-spoiler>"

# ─────────────────────────────────────────────────────────────────────────────
#  LANGUAGE PICKER
# ─────────────────────────────────────────────────────────────────────────────

def lang_kb() -> InlineKeyboardMarkup:
    rows = []
    for i in range(0, len(LANG_OPTIONS), 2):
        row = []
        for code, label in LANG_OPTIONS[i:i+2]:
            row.append(InlineKeyboardButton(label, callback_data=f"setlang_{code}"))
        rows.append(row)
    return InlineKeyboardMarkup(rows)

async def show_language_picker(update: Update, ctx: ContextTypes.DEFAULT_TYPE, edit=False):
    text = (
        "🌐 <b>SELECT YOUR LANGUAGE</b>\n"
        f"{sep()}\n\n"
        "Please choose your preferred language to continue:\n\n"
        "🇬🇧 English | 🇮🇳 हिंदी | 🇮🇩 Indonesia | 🇻🇳 Tiếng Việt"
    )
    kb = lang_kb()
    if edit and update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=kb, parse_mode="HTML")
    elif update.callback_query:
        await update.callback_query.message.reply_html(text, reply_markup=kb)
    elif update.message:
        await update.message.reply_html(text, reply_markup=kb)

async def setlang_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    code = q.data.split("_")[1]
    uid  = q.from_user.id
    await q.answer()

    # Save language
    db.set_user_language(uid, code)
    lang = code

    # If user doesn't exist yet, create them now
    if not db.user_exists(uid):
        tg   = q.from_user
        uname = tg.username or ""
        fname = tg.full_name or ""
        # Handle referral if stored in context
        ref_code = ctx.user_data.pop("pending_ref", None)
        user = db.get_or_create_user(uid, uname, fname)
        if ref_code:
            ref_user = db.get_user_by_referral_code(ref_code)
            if ref_user and ref_user["user_id"] != uid:
                db.set_referred_by(uid, ref_user["user_id"])
                db.promote_vip(ref_user["user_id"])
    else:
        user = db.get_user(uid)

    # Show success then check force-join
    await q.edit_message_text(T(lang, "language_set"), parse_mode="HTML")
    await asyncio.sleep(0.5)

    # Check force join
    channels = get_force_channels()
    if channels and not await check_force_join(uid, ctx.bot):
        btns = []
        for i, (handle, url) in enumerate(channels):
            btns.append([InlineKeyboardButton(f"📢 {handle}", url=url)])
        btns.append([InlineKeyboardButton(T(lang, "join_verify"), callback_data="check_join")])
        await q.message.reply_html(T(lang, "force_join_msg"), reply_markup=InlineKeyboardMarkup(btns))
    else:
        await show_main_menu_msg(q.message, uid, lang)

async def language_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    await show_language_picker(update, ctx, edit=True)

# ─────────────────────────────────────────────────────────────────────────────
#  MAIN MENU
# ─────────────────────────────────────────────────────────────────────────────

def main_menu_text(user: dict, lang: str) -> str:
    name, _ = user_display(user)
    tagline  = db.get_setting("bot_tagline", config.BOT_TAGLINE)
    return T(lang, "welcome",
             emoji=bot_emoji(), bot_name=bot_name(),
             name=name, tagline=tagline)

def main_menu_rows(uid: int, lang: str) -> list:
    """Styled button rows for the main menu (used by se/ss helpers)."""
    rows = [
        [sb(T(lang,"btn_shop"),    "shop",    style="success", emoji_id=EC.E_SHOP),
         sb(T(lang,"btn_deposit"), "deposit", style="primary", emoji_id=EC.E_DEPOSIT)],
        [sb(T(lang,"btn_profile"), "profile", style="primary", emoji_id=EC.E_PROFILE),
         sb(T(lang,"btn_support"), "support", style="primary", emoji_id=EC.E_SUPPORT)],
    ]
    if db.get_setting("referral_on") == "1":
        rows.append([sb(T(lang,"btn_referral"), "referral", style="success", emoji_id=EC.E_REFERRAL)])
    if db.get_free_items(active_only=True):
        rows.append([sb(T(lang,"btn_free_item"), "free_items", style="success")])
    rows.append([sb(T(lang,"btn_cart"),     "cart",     style="primary", emoji_id=EC.E_CART),
                 sb(T(lang,"btn_language"), "language", style="primary", emoji_id=EC.E_LANGUAGE)])
    if is_admin(uid):
        rows.append([sb(T(lang,"btn_admin"), "admin", style="danger", emoji_id=EC.E_ADMIN)])
    return rows

def main_menu_kb(uid: int, lang: str) -> InlineKeyboardMarkup:
    """PTB fallback keyboard — keeps backward compat with any remaining plain sends."""
    rows = main_menu_rows(uid, lang)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(b["text"], callback_data=b.get("callback_data","noop"))
         for b in row]
        for row in rows
    ])

async def show_main_menu_msg(message, uid: int, lang: str):
    user = db.get_user(uid)
    if not user:
        return
    await ss(message, main_menu_text(user, lang), main_menu_rows(uid, lang))

async def show_main_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE, user: dict):
    lang = get_lang(user["user_id"])
    rows = main_menu_rows(user["user_id"], lang)
    text = main_menu_text(user, lang)
    if update.callback_query:
        await se(update.callback_query, text, rows)
    else:
        await ss(update.message, text, rows)

async def home(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q  = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    user = db.get_user(uid)
    if user:
        await show_main_menu(update, ctx, user)

# ─────────────────────────────────────────────────────────────────────────────
#  /START
# ─────────────────────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tg    = update.effective_user
    uid   = tg.id
    args  = ctx.args or []

    # Check maintenance (allow admins through)
    if db.get_setting("maintenance") == "1" and not is_admin(uid):
        # Use user's stored language if available, else English
        _maint_lang = (db.get_user_language(uid) if db.user_exists(uid) else "") or "en"
        await update.message.reply_html(T(_maint_lang, "maintenance"))
        return

    # If user is banned
    existing = db.get_user(uid) if db.user_exists(uid) else None
    if existing and existing.get("is_banned"):
        _ban_lang = db.get_user_language(uid) or "en"
        await update.message.reply_html(T(_ban_lang, "maintenance").replace("MAINTENANCE", "BANNED").replace("Maintenance", "Banned"))
        await update.message.reply_text(T(_ban_lang, "banned_plain"))
        return

    # Store referral for after language selection
    if args and args[0].startswith("ref_"):
        ctx.user_data["pending_ref"] = args[0][4:]

    # If user has NO language set → show language picker first
    current_lang = db.get_user_language(uid) if db.user_exists(uid) else ""
    if not current_lang:
        await update.message.reply_html(
            "🌐 <b>SELECT YOUR LANGUAGE</b>\n"
            f"{sep()}\n\n"
            "Please choose your preferred language to continue:\n\n"
            "🇬🇧 English | 🇮🇳 हिंदी | 🇮🇩 Indonesia | 🇻🇳 Tiếng Việt",
            reply_markup=lang_kb()
        )
        return

    # User exists and has language — normal start
    lang = current_lang
    uname = tg.username or ""
    fname = tg.full_name or ""
    user  = db.get_or_create_user(uid, uname, fname)

    # Check force join
    channels = get_force_channels()
    if channels and not await check_force_join(uid, ctx.bot):
        btns = []
        for handle, url in channels:
            btns.append([InlineKeyboardButton(f"📢 {handle}", url=url)])
        btns.append([InlineKeyboardButton(T(lang,"join_verify"), callback_data="check_join")])
        await update.message.reply_html(T(lang,"force_join_msg"), reply_markup=InlineKeyboardMarkup(btns))
        return

    await show_main_menu(update, ctx, user)

async def check_join_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    uid  = q.from_user.id
    lang = get_lang(uid)
    await q.answer()
    if await check_force_join(uid, ctx.bot):
        user = db.get_user(uid)
        if not user:
            user = db.get_or_create_user(uid, q.from_user.username or "", q.from_user.full_name or "")
        name = user.get("full_name") or user.get("username") or str(uid)
        await q.edit_message_text(
            T(lang, "join_success", bot_name=bot_name()),
            parse_mode="HTML"
        )
        await asyncio.sleep(0.5)
        await show_main_menu_msg(q.message, uid, lang)
    else:
        await q.answer(T(lang,"join_not_done"), show_alert=True)

# ─────────────────────────────────────────────────────────────────────────────
#  /WALLET  (balance + payment/deposit history, one place)
# ─────────────────────────────────────────────────────────────────────────────

async def wallet_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    lang = get_lang(uid)
    user = db.get_user(uid)
    if not user:
        await update.message.reply_html(T(lang, "wallet_not_started")); return
    tier_name, disc, tier_emoji = loyalty_tier(user.get("total_spent_usdt", 0))
    await ss(update.message,
        T(lang, "wallet_title",
          balance=fmt(user['balance']),
          tier_emoji=tier_emoji, tier=tier_name, disc=disc,
          orders=user['total_orders'],
          spent=fmt(user.get('total_spent_usdt', 0))),
        [
            [sb(T(lang,"btn_payment_history"), "history",     style="primary"),
             sb(T(lang,"btn_dep_history_wallet"), "dep_history", style="primary")],
            [sb(T(lang,"btn_add_funds"), "deposit", style="success")],
            [sb(T(lang,"btn_home"), "home", style="danger", emoji_id=EC.E_HOME)],
        ]
    )

# ─────────────────────────────────────────────────────────────────────────────
#  PROFILE
# ─────────────────────────────────────────────────────────────────────────────

async def profile_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    lang = get_lang(uid)
    user = db.get_user(uid)
    if not user: return
    tier_name, disc, tier_emoji = loyalty_tier(user.get("total_spent_usdt",0))
    vip_badge = T(lang,"vip_badge") if user.get("is_vip") else ""
    name, uname = user_display(user)
    await se(q,
        T(lang,"profile_body",
          uid=uid, uname=uname, balance=user["balance"],
          tier_emoji=tier_emoji, tier=tier_name, disc=disc,
          vip_badge=vip_badge, orders=user["total_orders"],
          spent=user.get("total_spent_usdt",0)),
        [
            [sb(T(lang,"btn_history"),  "history", style="primary", emoji_id=EC.E_HISTORY),
             sb(T(lang,"btn_deposit2"), "deposit", style="success", emoji_id=EC.E_DEPOSIT2)],
            [sb(T(lang,"btn_home"),     "home",    style="danger",  emoji_id=EC.E_HOME)],
        ]
    )

async def history_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    lang = get_lang(uid)
    parts = q.data.split("_")
    page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    PAGE_SIZE = 10
    total_count = db.get_user_order_count(uid)
    orders = db.get_user_orders(uid, limit=PAGE_SIZE, offset=page * PAGE_SIZE)
    if not orders:
        await q.edit_message_text(
            T(lang,"history_empty"),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(T(lang,"btn_back"), callback_data="profile")]]),
            parse_mode="HTML")
        return
    total_pages = max(1, (total_count + PAGE_SIZE - 1) // PAGE_SIZE)
    lines = [f"{T(lang,'history_title')}  (page {page+1}/{total_pages}, {total_count} total)\n{sep()}\n"]
    for o in orders:
        icon = "✅" if not o.get("refunded") else "♻️"
        code = o.get("order_code") or f"{o['id']:04d}"
        lines.append(f"{icon} #{code} — <b>{o['product_name']}</b> — {fmt(o['amount_usdt'])} — {(o['created_at'] or '')[:10]}")
    lines.append(T(lang,"history_tap_hint"))
    btns  = [[sb(T(lang,"btn_view_details_row", code=(o.get("order_code") or f"{o['id']:04d}")), f"order_{o.get('order_code') or o['id']}", style="primary")]
              for o in orders]
    nav = []
    if page > 0:
        nav.append(sb(T(lang,"btn_prev"), f"history_{page-1}"))
    if (page + 1) * PAGE_SIZE < total_count:
        nav.append(sb(T(lang,"btn_next"), f"history_{page+1}"))
    if nav: btns.append(nav)
    btns.append([sb(T(lang,"btn_find_order"), "find_order_start", style="primary")])
    btns.append([sb(T(lang,"btn_back"), "profile", style="danger", emoji_id=EC.E_BACK)])
    await se(q, "\n".join(lines), btns)

def _build_order_detail_view(uid: int, lang: str, order: dict):
    """Shared renderer used by both the history 'View Details' button and the
    'Find Order' text-search flow, so both show identical info."""
    oid = order["id"]
    display_oid = order.get("order_code") or oid
    item_row = db.get_stock_item_by_order(oid)
    cred_text = parse_credential(item_row["data"], lang) if item_row else T(lang,"order_no_credential")
    status = T(lang,"order_status_completed") if not order.get("refunded") else T(lang,"order_status_refunded")

    btns = []
    if not order.get("refunded"):
        existing_req = db.get_user_refund_request_for_order(uid, oid)
        if not existing_req:
            btns.append([sb(T(lang,"btn_request_refund"), f"refund_req_{oid}", style="danger")])
        elif existing_req["status"] == "pending":
            btns.append([sb(T(lang,"btn_refund_pending"), "noop")])
    btns.append([sb(T(lang,"btn_back"), "history", style="danger", emoji_id=EC.E_BACK)])

    text = T(lang,"order_detail_body",
             oid=display_oid,
             product_name=order['product_name'],
             amount=fmt(order['amount_usdt']),
             status=status,
             date=(order['created_at'] or '')[:16],
             cred_text=cred_text)
    return text, btns


async def order_detail_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    lang = get_lang(uid)
    code = q.data.split("_", 1)[1]   # order_{order_code}
    order = db.get_order_by_code(code)
    if not order and code.isdigit():
        order = db.get_order(int(code))   # fallback for orders created before order_code existed
    if not order or order["user_id"] != uid:
        await q.answer(T(lang,"order_not_found"), show_alert=True); return
    text, btns = _build_order_detail_view(uid, lang, order)
    await se(q, text, btns)


async def find_order_start_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """User taps '🔎 Find Order' — ask them to type an Order ID."""
    q = update.callback_query
    await q.answer()
    lang = get_lang(q.from_user.id)
    await q.edit_message_text(T(lang,"find_order_prompt"), parse_mode="HTML")
    return FIND_ORDER_CODE


async def find_order_code_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """User typed an Order ID — look it up (only among their own orders) and show details."""
    uid  = update.effective_user.id
    lang = get_lang(uid)
    code = update.message.text.strip().lstrip("#")
    if not code.isdigit():
        await update.message.reply_html(T(lang,"find_order_invalid"))
        return FIND_ORDER_CODE
    order = db.get_order_by_code(code) or db.get_order(int(code))
    if not order or order["user_id"] != uid:
        await update.message.reply_html(T(lang,"find_order_not_found"))
        return FIND_ORDER_CODE
    text, btns = _build_order_detail_view(uid, lang, order)
    await ss(update.message, text, btns)
    return ConversationHandler.END

# ─────────────────────────────────────────────────────────────────────────────
#  REFUND REQUEST FLOW  (user-initiated)
# ─────────────────────────────────────────────────────────────────────────────

async def user_refund_start_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """User clicks '🔄 Request Refund' on order detail screen."""
    q    = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    lang = get_lang(uid)
    oid  = int(q.data.split("_")[2])   # refund_req_{oid}
    order = db.get_order(oid)
    if not order or order["user_id"] != uid:
        await q.answer(T(lang,"refund_order_not_found"), show_alert=True)
        return ConversationHandler.END
    if order.get("refunded"):
        await q.answer(T(lang,"refund_already_refunded"), show_alert=True)
        return ConversationHandler.END
    existing = db.get_user_refund_request_for_order(uid, oid)
    if existing:
        await q.answer(T(lang,"refund_already_pending"), show_alert=True)
        return ConversationHandler.END
    ctx.user_data["refund_order_id"] = oid
    await q.edit_message_text(
        T(lang,"refund_prompt",
          oid=order.get("order_code") or oid,
          product_name=order['product_name'],
          amount=fmt(order['amount_usdt'])),
        parse_mode="HTML"
    )
    return USER_REFUND_REASON

async def user_refund_reason_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """User typed a refund reason — save it and notify admin."""
    uid    = update.effective_user.id
    lang   = get_lang(uid)
    reason = update.message.text.strip()
    oid    = ctx.user_data.get("refund_order_id")
    if not oid:
        await update.message.reply_text(T(lang,"refund_error"))
        return ConversationHandler.END
    order = db.get_order(oid)
    if not order:
        await update.message.reply_text(T(lang,"refund_order_missing"))
        return ConversationHandler.END
    if len(reason) < 5:
        await update.message.reply_text(T(lang,"refund_too_short"))
        return USER_REFUND_REASON

    rid = db.create_refund_request(uid, oid, reason)
    user = db.get_user(uid)
    uname = f"@{user['username']}" if user.get("username") else user.get("full_name", str(uid))
    stock_row = db.get_stock_item_by_order(oid)
    cred = stock_row["data"] if stock_row else "—"
    display_oid = order.get("order_code") or oid

    # Confirm to user
    await ss(
        update.message,
        T(lang,"refund_submitted",
          oid=display_oid,
          product_name=order['product_name'],
          reason=reason),
        [
            [sb("💬 Message Admin", f"refund_msg_{rid}", style="primary", emoji_id=EC.E_REPLY)],
            [sb(T(lang,"btn_home"), "home", style="danger", emoji_id=EC.E_HOME)]
        ]
    )

    # Notify all admins
    admin_text = (
        f"🔔 <b>New Refund Request #{rid}</b>\n{sep()}\n\n"
        f"👤 User: {uname} (<code>{uid}</code>)\n"
        f"📦 Order #{display_oid} — {order['product_name']}\n"
        f"🔑 Credential: <code>{cred}</code>\n"
        f"💰 Amount: {fmt(order['amount_usdt'])}\n"
        f"📅 Ordered: {(order['created_at'] or '')[:16]}\n\n"
        f"💬 <b>Reason:</b>\n<i>{reason}</i>"
    )
    admin_rows = [
        [
            sb("✅ Approve Refund", f"adm_rreq_ok_{rid}", style="success", emoji_id=EC.E_SUCCESS),
            sb("❌ Reject",          f"adm_rreq_no_{rid}", style="danger",  emoji_id=EC.E_ERROR),
        ],
        [sb("💬 Chat with user", f"refund_msg_{rid}", style="primary", emoji_id=EC.E_REPLY)]
    ]
    for adm_id in list(config.ADMIN_IDS) + db.get_extra_admins():
        try:
            await styled_api.send(adm_id, admin_text, admin_rows, "HTML")
        except Exception:
            pass

    ctx.user_data.pop("refund_order_id", None)
    return ConversationHandler.END

async def adm_rreq_ok_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Admin approves a user refund request."""
    q   = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if not is_admin(uid):
        await q.answer("Admins only.", show_alert=True); return
    rid = int(q.data.split("_")[3])   # adm_rreq_ok_{rid}
    req, order = db.approve_refund_request(rid)
    if not req:
        await q.edit_message_text("⚠️ Request not found or already resolved."); return
    # Notify the user
    try:
        _ulang = db.get_user_language(req["user_id"]) or "en"
        await styled_api.send(
            req["user_id"],
            T(_ulang,"refund_approved",
              oid=order.get("order_code") or order['id'],
              product_name=order['product_name'],
              amount=fmt(order['amount_usdt'])),
            [[sb(T(_ulang,"btn_my_profile"), "profile", style="primary", emoji_id=EC.E_PROFILE)]],
            "HTML"
        )
    except Exception:
        pass
    # Update admin message
    await q.edit_message_text(
        q.message.text + f"\n\n✅ <b>APPROVED</b> by admin <code>{uid}</code>",
        parse_mode="HTML"
    )

async def adm_rreq_no_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Admin rejects a user refund request."""
    q   = update.callback_query
    await q.answer()
    uid = q.from_user.id
    if not is_admin(uid):
        await q.answer("Admins only.", show_alert=True); return
    rid = int(q.data.split("_")[3])   # adm_rreq_no_{rid}
    req = db.reject_refund_request(rid, admin_note="Rejected by admin")
    if not req:
        await q.edit_message_text("⚠️ Request not found or already resolved."); return
    # Notify the user
    order = db.get_order(req["order_id"])
    try:
        _ulang = db.get_user_language(req["user_id"]) or "en"
        product_part = f" — {order['product_name']}" if order else ""
        await styled_api.send(
            req["user_id"],
            T(_ulang,"refund_rejected",
              oid=(order.get("order_code") if order else None) or req['order_id'],
              product_part=product_part),
            [[sb(T(_ulang,"btn_support_ticket"), "support", style="primary", emoji_id=EC.E_SUPPORT)]],
            "HTML"
        )
    except Exception:
        pass
    # Update admin message
    await q.edit_message_text(
        q.message.text + f"\n\n❌ <b>REJECTED</b> by admin <code>{uid}</code>",
        parse_mode="HTML"
    )

# ── Refund chat (two-way, also visible/usable from the web admin panel) ───────

async def refund_msg_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Entry point for both admin and user tapping '💬' on a refund request."""
    q   = update.callback_query
    await q.answer()
    uid = q.from_user.id
    rid = int(q.data.split("_")[2])   # refund_msg_{rid}
    req = db.get_refund_request(rid)
    if not req:
        await q.answer(T(get_lang(uid),"refund_request_not_found_alert"), show_alert=True)
        return ConversationHandler.END
    ctx.user_data["refund_chat_id"] = rid
    if is_admin(uid):
        await ctx.bot.send_message(uid, f"💬 <b>Reply to refund #{rid} (user)</b>\n\nType your message:\n\n/cancel to abort", parse_mode="HTML")
        return ADM_REFUND_REPLY
    else:
        if req["user_id"] != uid:
            return ConversationHandler.END
        await ctx.bot.send_message(uid, T(get_lang(uid),"refund_msg_user_prompt", rid=rid), parse_mode="HTML")
        return USER_REFUND_REPLY

async def refund_msg_admin_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    admin_id = update.effective_user.id
    rid = ctx.user_data.pop("refund_chat_id", None)
    if not rid: return ConversationHandler.END
    msg = update.message.text.strip()
    db.add_refund_message(rid, admin_id, msg, is_admin=True)
    req = db.get_refund_request(rid)
    if req:
        try:
            user_lang = db.get_user_language(req["user_id"])
            await styled_api.send(
                req["user_id"],
                T(user_lang, "refund_msg_from_admin", rid=rid, msg=msg),
                [[sb(T(user_lang,"btn_reply"), f"refund_msg_{rid}", style="primary", emoji_id=EC.E_REPLY)]],
                "HTML"
            )
        except Exception:
            pass
    await update.message.reply_text("✅ Sent to user.")
    return ConversationHandler.END

async def refund_msg_user_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    rid = ctx.user_data.pop("refund_chat_id", None)
    if not rid: return ConversationHandler.END
    msg = update.message.text.strip()
    db.add_refund_message(rid, uid, msg, is_admin=False)
    for admin_id in list(config.ADMIN_IDS) + db.get_extra_admins():
        try:
            await styled_api.send(
                admin_id,
                f"👤 <b>User (refund #{rid}):</b>\n{msg}",
                [[sb("💬 Reply", f"refund_msg_{rid}", style="primary", emoji_id=EC.E_REPLY)]],
                "HTML"
            )
        except Exception:
            pass
    await update.message.reply_text(T(get_lang(uid),"refund_sent_to_admin"))
    return ConversationHandler.END

# ─────────────────────────────────────────────────────────────────────────────
#  REFERRAL
# ─────────────────────────────────────────────────────────────────────────────

async def referral_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    lang = get_lang(uid)
    user = db.get_user(uid)
    if not user: return
    count = db.get_referral_count(uid)
    vip   = "🌟 VIP" if user.get("is_vip") else "🥉 Not VIP"
    bu    = db.get_setting("bot_username", config.BOT_USERNAME)
    await q.edit_message_text(
        T(lang,"referral_title",
          bot=bu, code=user["referral_code"],
          count=count, needed=config.VIP_REFERRALS_NEEDED,
          vip=vip, bonus=config.REFERRAL_BONUS_USDT),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(T(lang,"btn_home"), callback_data="home")]]),
        parse_mode="HTML"
    )

# ─────────────────────────────────────────────────────────────────────────────
#  SHOP
# ─────────────────────────────────────────────────────────────────────────────

def build_shop_view(lang: str):
    """Flat product list across ALL active categories — no category step.
    Returns (text, rows) used by both the /shop command and the 'shop' button."""
    prods = db.get_products(active_only=True)
    if not prods:
        return T(lang, "shop_empty"), [[sb(T(lang, "btn_home"), "home", style="danger", emoji_id=EC.E_HOME)]]
    btns = []
    for p in prods:
        stock = db.get_stock_count(p["id"])
        tag = f"✅ {stock}" if stock > 0 else "❌"
        btns.append([sb(
            f"{p['emoji']} {p['name']} — {fmt(p['price_usdt'])} [{tag}]",
            f"product_{p['id']}", style="primary",
            emoji_id=p.get("emoji_id") or None
        )])
    btns.append([sb(T(lang, "btn_home"), "home", style="danger", emoji_id=EC.E_HOME)])
    return T(lang, "shop_title"), btns


async def shop_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    lang = get_lang(uid)
    text, rows = build_shop_view(lang)
    await se(q, text, rows)

async def shop_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/shop — same flat product list, sent as a fresh message."""
    uid  = update.effective_user.id
    if not db.user_exists(uid):
        await update.message.reply_text(T(get_lang(uid),"please_start_first"))
        return
    lang = get_lang(uid)
    text, rows = build_shop_view(lang)
    await ss(update.message, text, rows)

async def orders_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/orders — user's own order history, sent as a fresh message."""
    uid  = update.effective_user.id
    if not db.user_exists(uid):
        await update.message.reply_text(T(get_lang(uid),"please_start_first"))
        return
    lang = get_lang(uid)
    PAGE_SIZE = 10
    total_count = db.get_user_order_count(uid)
    orders = db.get_user_orders(uid, limit=PAGE_SIZE, offset=0)
    if not orders:
        await update.message.reply_html(
            T(lang, "history_empty"),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(T(lang,"btn_back"), callback_data="profile")]]))
        return
    total_pages = max(1, (total_count + PAGE_SIZE - 1) // PAGE_SIZE)
    lines = [f"{T(lang,'history_title')}  (page 1/{total_pages}, {total_count} total)\n{sep()}\n"]
    for o in orders:
        icon = "✅" if not o.get("refunded") else "♻️"
        code = o.get("order_code") or f"{o['id']:04d}"
        lines.append(f"{icon} #{code} — <b>{o['product_name']}</b> — {fmt(o['amount_usdt'])} — {(o['created_at'] or '')[:10]}")
    btns = [[InlineKeyboardButton(T(lang,"btn_view_details_row", code=(o.get("order_code") or f"{o['id']:04d}")), callback_data=f"order_{o.get('order_code') or o['id']}")] for o in orders]
    if (0 + 1) * PAGE_SIZE < total_count:
        btns.append([InlineKeyboardButton(T(lang,"btn_next"), callback_data="history_1")])
    btns.append([InlineKeyboardButton(T(lang,"btn_find_order"), callback_data="find_order_start")])
    btns.append([InlineKeyboardButton(T(lang,"btn_back"), callback_data="profile")])
    await update.message.reply_html("\n".join(lines), reply_markup=InlineKeyboardMarkup(btns))

async def profile_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/profile — fresh message version of the profile screen."""
    uid  = update.effective_user.id
    if not db.user_exists(uid):
        await update.message.reply_text(T(get_lang(uid),"please_start_first"))
        return
    lang = get_lang(uid)
    user = db.get_user(uid)
    tier_name, disc, tier_emoji = loyalty_tier(user.get("total_spent_usdt", 0))
    vip_badge = T(lang,"vip_badge") if user.get("is_vip") else ""
    name, uname = user_display(user)
    text = T(lang,"profile_body",
             uid=uid, uname=uname, balance=user["balance"],
             tier_emoji=tier_emoji, tier=tier_name, disc=disc,
             vip_badge=vip_badge, orders=user["total_orders"],
             spent=user.get("total_spent_usdt", 0))
    rows = [
        [sb(T(lang,"btn_history"),  "history", style="primary", emoji_id=EC.E_HISTORY),
         sb(T(lang,"btn_deposit2"), "deposit", style="success", emoji_id=EC.E_DEPOSIT2)],
        [sb(T(lang,"btn_home"),     "home",    style="danger",  emoji_id=EC.E_HOME)],
    ]
    await ss(update.message, text, rows)

async def support_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/support — fresh message version of the support screen."""
    uid  = update.effective_user.id
    if not db.user_exists(uid):
        await update.message.reply_text(T(get_lang(uid),"please_start_first"))
        return
    lang = get_lang(uid)
    rows = [
        [sb(T(lang,"btn_new_ticket"), "ticket_new",  style="success", emoji_id=EC.E_NEW_TICKET)],
        [sb(T(lang,"btn_my_tickets"), "ticket_list", style="primary", emoji_id=EC.E_MY_TICKETS)],
        [sb(T(lang,"btn_home"),       "home",        style="danger",  emoji_id=EC.E_HOME)],
    ]
    await ss(update.message, T(lang,"support_title"), rows)

async def cat_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    lang = get_lang(uid)
    cid  = int(q.data.split("_")[1])
    cat  = db.get_category(cid)
    prods = db.get_products(category_id=cid, active_only=True)
    if not prods:
        await q.answer(T(lang,"cat_no_products"), show_alert=True); return
    btns = []
    for p in prods:
        stock = db.get_stock_count(p["id"])
        tag   = f"✅ {stock}" if stock > 0 else "❌"
        btns.append([sb(
            f"{p['emoji']} {p['name']} — {fmt(p['price_usdt'])} [{tag}]",
            f"product_{p['id']}", style="primary"
        )])
    btns.append([sb(T(lang,"btn_back"), "shop", style="danger", emoji_id=EC.E_BACK)])
    cat_label = f"{cat['emoji']} {cat['name']}" if cat else "Products"
    await se(q, f"📂 <b>{cat_label}</b>\n{sep()}\n\n{T(lang,'cat_choose_product')}", btns)

async def render_product_view(q, uid: int, lang: str, pid: int, qty: int = 1):
    p = db.get_product(pid)
    if not p: await q.answer(T(lang,"product_not_found_alert"), show_alert=True); return
    u     = db.get_user(uid)
    disc  = effective_disc(u)
    unit  = apply_disc(p["price_usdt"], disc) if disc else p["price_usdt"]
    reseller_line = ""
    if db.is_reseller(uid):
        unit = feat.reseller_price(p["price_usdt"])
        s = feat.reseller_settings()
        reseller_line = f"\n🏷 <b>Reseller price:</b> {s['discount']}% off applied!"
    stock = db.get_stock_count(pid)
    qty   = max(1, min(qty, stock)) if stock > 0 else 1
    total = round(unit * qty, 6)
    disc_line = (T(lang,"disc_line", disc=disc, final=unit) if disc else "") + reseller_line
    stock_line = f"\n📦 {'Only ' + str(stock) + ' left!' if 0 < stock <= 5 else ('In stock ✅' if stock > 0 else '❌ Out of stock')}"
    text  = T(lang,"product_detail",
              emoji=p["emoji"], name=p["name"],
              desc=EC.upgrade_description_emojis(p.get("description")) or "Premium digital subscription.",
              duration=p["duration"], price=p["price_usdt"],
              disc_line=disc_line + stock_line,
              balance=u["balance"],
              stock_label=T(lang,"in_stock") if stock > 0 else T(lang,"out_of_stock"))
    if stock > 0:
        text += f"\n\n🔢 Quantity: <b>{qty}</b> × {fmt(unit)} = <b>{fmt(total)}</b>"
    rows = []
    if stock > 0:
        rows.append([
            sb("➖", f"qtydec_{pid}_{qty}", emoji_id=EC.E_QTY_MINUS),
            sb(f"{qty}", "noop"),
            sb("➕", f"qtyinc_{pid}_{qty}", emoji_id=EC.E_QTY_PLUS),
        ])
        if u["balance"] >= total:
            buy_cbdata = f"buy_{pid}" if qty == 1 else f"buyqty_{pid}_{qty}"
            rows.append([sb(T(lang,"btn_buy"), buy_cbdata, style="success", emoji_id=EC.E_BUY)])
        else:
            rows.append([sb(T(lang,"btn_top_up", needed=round(total-u["balance"],2)),
                            "deposit", style="danger", emoji_id=EC.E_TOP_UP)])
        rows.append([sb(T(lang,"btn_add_cart"), f"cartadd_{pid}_{qty}", style="primary", emoji_id=EC.E_ADD_CART)])
    else:
        rows.append([sb("🔔 Request Restock", f"restock_{pid}", style="primary")])
    rows.append([sb(T(lang,"btn_back"), "shop", style="danger", emoji_id=EC.E_BACK)])
    await se(q, text, rows)

async def product_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    lang = get_lang(uid)
    pid  = int(q.data.split("_")[1])
    await render_product_view(q, uid, lang, pid)

async def restock_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """User tapped '🔔 Request Restock' on an out-of-stock product."""
    q    = update.callback_query
    uid  = q.from_user.id
    lang = get_lang(uid)
    pid  = int(q.data.split("_")[1])
    p    = db.get_product(pid)
    if not p:
        await q.answer(T(lang,"product_not_found_alert"), show_alert=True)
        return
    user = db.get_user(uid)
    name, uname = user_display(user) if user else (str(uid), "")
    db.add_restock_request(pid, uid)
    notify_text = (
        f"🔔 <b>Restock Requested</b>\n{sep()}\n\n"
        f"Product: <b>{p['emoji']} {p['name']}</b>\n"
        f"Requested by: {name} ({uname or uid})\n"
        f"User ID: <code>{uid}</code>"
    )
    for admin_id in set(list(config.ADMIN_IDS) + db.get_extra_admins()):
        try:
            await ctx.bot.send_message(admin_id, notify_text, parse_mode="HTML")
        except Exception:
            pass
    await q.answer(T(lang,"restock_request_sent_alert"), show_alert=True)

async def qty_change_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    lang = get_lang(uid)
    parts = q.data.split("_")
    inc  = parts[0] == "qtyinc"
    pid, qty = int(parts[1]), int(parts[2])
    stock = db.get_stock_count(pid)
    qty   = min(stock, qty + 1) if inc else max(1, qty - 1)
    await render_product_view(q, uid, lang, pid, qty)

async def cartadd_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    uid  = q.from_user.id
    lang = get_lang(uid)
    parts = q.data.split("_")
    pid, qty = int(parts[1]), int(parts[2]) if len(parts) > 2 else 1
    db.add_to_cart(uid, pid, qty)
    await q.answer(f"✅ {T(lang,'btn_cart_added')}", show_alert=True)

async def noop_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()

# ── Buy ───────────────────────────────────────────────────────────────────────

async def do_purchase(q, uid: int, lang: str, pid: int, qty: int = 1):
    p = db.get_product(pid)
    if not p:
        await q.answer(T(lang,"product_not_found_alert"), show_alert=True); return
    u     = db.get_user(uid)
    disc  = effective_disc(u)
    unit  = apply_disc(p["price_usdt"], disc) if disc else p["price_usdt"]
    is_res = db.is_reseller(uid)
    if is_res:
        unit = feat.reseller_price(p["price_usdt"])
    total = round(unit * qty, 6)
    if u["balance"] < total:
        await q.answer(T(lang,"insufficient",needed=round(total-u["balance"],2)), show_alert=True); return
    items = db.pop_stock(pid, qty)
    if len(items) < qty:
        await q.answer(T(lang,"out_of_stock_buy"), show_alert=True); return

    # Deduct balance
    db.update_balance(uid, -total)

    # Create order(s) and mark stock
    cred_parts = []
    last_oid   = None
    for item in items:
        oid = db.create_order(uid, pid, p["name"], unit, item["id"],
                              is_reseller_sale=is_res)
        db.mark_stock_sold(item["id"], uid, oid)
        cred_parts.append(parse_credential(item["data"], lang))
        last_oid = oid

    u2 = db.get_user(uid)
    name, uname = user_display(u)
    uname_display = f"@{u.get('username')}" if u.get("username") else f"ID:{uid}"

    if qty == 1:
        await q.edit_message_text(
            T(lang,"purchased", emoji=p["emoji"], name=p["name"],
              cred=items[0]["data"], amount=total, balance=u2["balance"], oid=last_oid),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(T(lang,"btn_shop_more"), callback_data="shop"),
                 InlineKeyboardButton(T(lang,"btn_home"), callback_data="home")]
            ]),
            parse_mode="HTML"
        )
    else:
        # Telegram messages are capped at ~4096 chars. A big quantity (e.g.
        # 50 items) can easily blow past that if all credentials go in one
        # message — which would fail to send AFTER the balance was already
        # deducted and stock already marked sold, leaving the user with no
        # visible confirmation of what they paid for. Split into chunks
        # instead, so delivery always succeeds no matter the quantity.
        CHUNK = 15
        chunks = [cred_parts[i:i+CHUNK] for i in range(0, len(cred_parts), CHUNK)]
        header = T(lang,"multi_purchased_header", emoji=p["emoji"], name=p["name"],
                   qty=qty, amount=total, balance=u2["balance"])
        try:
            await q.edit_message_text(
                header + "\n\n" + "\n\n".join(chunks[0]) +
                (f"\n\n<i>({len(chunks)} messages — {len(cred_parts)} items total)</i>" if len(chunks) > 1 else ""),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.warning(f"Purchase confirmation edit failed, sending fresh instead: {e}")
            await q.message.reply_html(header + "\n\n" + "\n\n".join(chunks[0]))
        for extra_chunk in chunks[1:]:
            try:
                await q.message.reply_html("\n\n".join(extra_chunk))
            except Exception as e:
                logger.exception(f"Failed to deliver credential chunk for order near {last_oid}: {e}")
        await q.message.reply_html(
            "✅ Delivery complete.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(T(lang,"btn_shop_more"), callback_data="shop"),
                 InlineKeyboardButton(T(lang,"btn_home"), callback_data="home")]
            ])
        )

    # Log the purchase
    await log_ch(q.message.bot,
        f"🛒 <b>Purchase</b>\n{sep()}\n"
        f"👤 {uname_display} (<code>{uid}</code>)\n"
        f"📦 {p['emoji']} {p['name']} × {qty}\n"
        f"💰 {fmt(total)}\n"
        f"📋 Order #{last_oid:04d}")

    # Check low stock
    await feat.check_low_stock(q.message.bot, pid, p["name"], p["emoji"])

    # Handle reseller earnings
    if is_res:
        margin = round((p["price_usdt"] - unit) * qty, 6)
        s = feat.reseller_settings()
        owner_cut    = round(margin * s["commission"] / 100, 6)
        reseller_cut = round(margin - owner_cut, 6)
        if margin > 0:
            db.add_reseller_earning(uid, last_oid, margin, owner_cut, reseller_cut)

async def buy_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    lang = get_lang(uid)
    pid  = int(q.data.split("_")[1])
    await do_purchase(q, uid, lang, pid, 1)

async def buyqty_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    lang = get_lang(uid)
    parts = q.data.split("_")
    pid, qty = int(parts[1]), int(parts[2])
    await do_purchase(q, uid, lang, pid, qty)

# ── Cart ──────────────────────────────────────────────────────────────────────

def _cart_item_pricing(uid, u, price_usdt):
    disc = effective_disc(u)
    if db.is_reseller(uid):
        return feat.reseller_price(price_usdt)
    return apply_disc(price_usdt, disc) if disc else price_usdt

async def cart_view_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    lang = get_lang(uid)
    u    = db.get_user(uid)
    cart = db.get_cart(uid)
    if not cart:
        ctx.user_data.pop("cart_coupon", None)
        await se(q, T(lang,"cart_empty"),
                 [[sb(T(lang,"btn_home"), "home", style="danger", emoji_id=EC.E_HOME)]])
        return

    tier_name, tier_disc, tier_emoji = loyalty_tier(u.get("total_spent_usdt", 0))
    lines = [f"{T(lang,'cart_view_title')}\n{sep()}"]
    total = 0.0
    btns  = []
    for idx, item in enumerate(cart, start=1):
        unit  = _cart_item_pricing(uid, u, item["price_usdt"])
        sub   = round(unit * item["quantity"], 6)
        total = round(total + sub, 6)
        stock = db.get_stock_count(item["product_id"])
        avail = "✅" if stock >= item["quantity"] else "⚠️ Low stock"
        lines.append(
            f"\n<b>{idx}. {item['emoji']} {item['name']}</b>  {avail}\n"
            f"   {item['quantity']} × {fmt(unit)}  =  <b>{fmt(sub)}</b>"
        )
        btns.append([
            sb("➖", f"cartqty_{item['product_id']}_dec", emoji_id=EC.E_QTY_MINUS),
            sb(f"{item['name'][:16]} ×{item['quantity']}", "noop"),
            sb("➕", f"cartqty_{item['product_id']}_inc", emoji_id=EC.E_QTY_PLUS),
            sb("🗑️", f"cartrem_{item['product_id']}", emoji_id=EC.E_TRASH),
        ])

    lines.append(f"\n{sep()}")
    coupon = ctx.user_data.get("cart_coupon")
    final_total = total
    if coupon:
        discount_amt = round(total * coupon["discount"] / 100, 6)
        final_total  = round(total - discount_amt, 6)
        lines.append(f"{T(lang,'cart_total_line',total=fmt(total))}")
        lines.append(f"🎟️ Coupon <b>{coupon['code']}</b>: −{coupon['discount']}%  (−{fmt(discount_amt)})")
        lines.append(f"💵 <b>Payable: {fmt(final_total)}</b>")
    else:
        lines.append(f"{T(lang,'cart_total_line',total=fmt(final_total))}")
        if tier_disc:
            lines.append(f"{tier_emoji} {tier_name} perk already applied: −{tier_disc}%")
    lines.append(f"\n⭐ {T(lang,'cart_balance_line',balance=fmt(u['balance']))}")

    # ── Premium / VIP highlight button ──────────────────────────────────────
    if u.get("is_vip"):
        btns.append([sb("⭐ VIP Active — View Perks", "cart_vip", style="success", emoji_id=EC.E_STAR)])
    else:
        btns.append([sb("⭐ Unlock Premium / VIP Perks", "cart_vip", style="primary", emoji_id=EC.E_STAR)])

    if coupon:
        btns.append([sb("🗑️ Remove Coupon", "cart_coupon_rm", style="danger", emoji_id=EC.E_TRASH)])
    else:
        btns.append([sb("🎟️ Apply Coupon Code", "cart_coupon_start", style="primary", emoji_id=EC.E_TICKET_STUB)])

    if u["balance"] >= final_total:
        btns.append([sb(T(lang,"btn_checkout"), "cart_checkout", style="success", emoji_id=EC.E_CHECKOUT)])
    else:
        btns.append([sb(T(lang,"btn_top_up", needed=round(final_total-u["balance"],2)),
                        "deposit", style="danger", emoji_id=EC.E_TOP_UP)])
    btns.append([sb(T(lang,"btn_clear_cart"), "cart_clear", style="danger", emoji_id=EC.E_TRASH),
                 sb(T(lang,"btn_home"),       "home",       style="danger", emoji_id=EC.E_HOME)])

    await se(q, "\n".join(lines), btns)

async def cart_vip_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Premium/VIP perks popup, shown as an inline button on the cart screen."""
    q    = update.callback_query
    uid  = q.from_user.id
    lang = get_lang(uid)
    u    = db.get_user(uid)
    if u and u.get("is_vip"):
        msg = (
            f"⭐ You're a VIP member!\n\n"
            f"• {config.VIP_DISCOUNT_PERCENT}% off every order\n"
            f"• {config.VIP_DEPOSIT_BONUS_PCT}% bonus on deposits ≥ {fmt(config.VIP_MIN_BONUS_DEPOSIT)}\n"
            f"• Priority support"
        )
    else:
        referred = db.get_referral_count(uid) if u else 0
        left = max(0, config.VIP_REFERRALS_NEEDED - referred)
        msg = (
            f"⭐ Premium / VIP Perks\n\n"
            f"• {config.VIP_DISCOUNT_PERCENT}% off every order\n"
            f"• {config.VIP_DEPOSIT_BONUS_PCT}% bonus on deposits ≥ {fmt(config.VIP_MIN_BONUS_DEPOSIT)}\n"
            f"• Priority support\n\n"
            f"🎁 Invite {left} more friend(s) to unlock VIP!"
        )
    await q.answer(msg, show_alert=True)

async def cart_coupon_start_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    lang = get_lang(q.from_user.id)
    await q.edit_message_text(
        "🎟️ <b>Apply Coupon</b>\n" + sep() + "\n\nSend the coupon code:\n(/cancel to abort)",
        parse_mode="HTML")
    return CART_COUPON_CODE

async def cart_coupon_code_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    lang = get_lang(uid)
    code = update.message.text.strip()
    c, status = db.validate_coupon(code, uid)
    if status == "not_found":
        await update.message.reply_text(T(lang,"coupon_invalid"))
        return CART_COUPON_CODE
    if status == "exhausted":
        await update.message.reply_text(T(lang,"coupon_exhausted"))
        return CART_COUPON_CODE
    if status == "already_used":
        await update.message.reply_text(T(lang,"coupon_already_used"))
        return CART_COUPON_CODE
    ctx.user_data["cart_coupon"] = {"id": c["id"], "code": c["code"], "discount": c["discount"]}
    await update.message.reply_html(
        T(lang,"coupon_applied", code=c["code"], discount=c["discount"]),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(T(lang,"btn_cart"), callback_data="cart")]]))
    return ConversationHandler.END

async def cart_coupon_remove_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    lang = get_lang(q.from_user.id)
    await q.answer(T(lang,"coupon_removed_alert"))
    ctx.user_data.pop("cart_coupon", None)
    await cart_view_cb(update, ctx)



async def cart_qty_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    parts = q.data.split("_")
    pid   = int(parts[1])
    inc   = parts[2] == "inc"
    item  = db.get_cart_item(uid, pid)
    if not item: return
    new_qty = item["quantity"] + (1 if inc else -1)
    db.set_cart_qty(uid, pid, new_qty)
    await cart_view_cb(update, ctx)

async def cart_remove_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    pid  = int(q.data.split("_")[1])
    db.remove_cart_item(uid, pid)
    await cart_view_cb(update, ctx)

async def cart_clear_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    db.clear_cart(uid)
    await cart_view_cb(update, ctx)

async def cart_checkout_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    lang = get_lang(uid)
    u    = db.get_user(uid)
    cart = db.get_cart(uid)
    if not cart:
        await q.answer(T(lang,"cart_empty"), show_alert=True); return
    coupon = ctx.user_data.get("cart_coupon")
    disc_mult = (1 - coupon["discount"] / 100) if coupon else 1.0
    total = sum(_cart_item_pricing(uid, u, i["price_usdt"]) * i["quantity"] for i in cart) * disc_mult
    total = round(total, 6)
    if u["balance"] < total:
        await q.answer(T(lang,"insufficient",needed=round(total-u["balance"],2)), show_alert=True); return
    results = []
    for item in cart:
        unit  = round(_cart_item_pricing(uid, u, item["price_usdt"]) * disc_mult, 6)
        qty   = item["quantity"]
        items = db.pop_stock(item["product_id"], qty)
        if len(items) < qty:
            await q.answer(T(lang,"cart_item_oos",name=item['name']), show_alert=True); return
        sub = round(unit * qty, 6)
        db.update_balance(uid, -sub)
        for si in items:
            oid = db.create_order(uid, item["product_id"], item["name"], unit, si["id"])
            db.mark_stock_sold(si["id"], uid, oid)
            results.append((item, si, oid))
    db.clear_cart(uid)
    if coupon:
        db.apply_coupon(coupon["id"], uid)
        ctx.user_data.pop("cart_coupon", None)
    u2 = db.get_user(uid)
    lines = [f"{T(lang,'cart_checkout_done')}\n{sep()}\n"]
    for item, si, oid in results:
        lines.append(f"✅ {item['emoji']} {item['name']}\n<code>{si['data']}</code>")
    if coupon:
        lines.append(f"\n🎟️ Coupon <b>{coupon['code']}</b> applied (−{coupon['discount']}%)")
    lines.append(f"\n{T(lang,'cart_checkout_total',total=fmt(total),balance=fmt(u2['balance']))}")
    await q.edit_message_text("\n".join(lines),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(T(lang,"btn_home"), callback_data="home")]]),
        parse_mode="HTML")

# ─────────────────────────────────────────────────────────────────────────────
#  DEPOSIT — TRC20 / BEP20 (TX hash verify) + Binance Pay (manual approve)
# ─────────────────────────────────────────────────────────────────────────────

def _gen_note() -> str:
    """8-char unique note for Binance Pay manual deposits."""
    import string as _str
    return ''.join(random.choices(_str.ascii_uppercase + _str.digits, k=8))

async def deposit_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    lang = get_lang(uid)
    user = db.get_user(uid)
    bal  = user["balance"] if user else 0.0
    spent= user.get("total_spent_usdt", 0) if user else 0
    trc20  = usdt_trc20()
    bep20  = usdt_bep20()
    min_dep = db.get_setting_float("min_deposit_usdt", config.MIN_DEPOSIT_USDT)

    tier_name, _, tier_icon = loyalty_tier(spent)
    text = T(lang, "deposit_wallet_text", bal=bal, spent=spent, tier_icon=tier_icon, tier_name=tier_name)
    btns = []
    row2 = []
    if trc20: row2.append(sb(T(lang,"btn_usdt_trc20"), "dep_net_TRC20", style="primary"))
    if bep20: row2.append(sb(T(lang,"btn_usdt_bep20"), "dep_net_BEP20", style="primary"))
    if row2: btns.append(row2)
    btns.append([sb(T(lang,"btn_tx_history"), "dep_history", style="primary")])
    btns.append([sb(T(lang,"btn_back"), "home", style="danger")])

    if q.message and q.message.photo:
        # Coming back from a QR-code deposit screen — delete the photo
        # message instead of just editing its caption, so the QR image
        # doesn't stay stuck on screen, then send the wallet menu fresh.
        try:
            await q.message.delete()
        except Exception:
            pass
        await ss(q.message, text, btns)
    else:
        await se(q, text, btns)

async def deposit_start_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Entry point for dep_conv — immediately redirect to deposit_cb wallet view."""
    q = update.callback_query
    await q.answer()
    # Reuse deposit_cb to show the wallet/method screen
    await deposit_cb(update, ctx)
    return ConversationHandler.END

async def dep_net_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    lang = get_lang(uid)
    net  = q.data.split("_", 2)[2]   # TRC20, BEP20, PAY
    ctx.user_data["dep_network"] = net
    min_dep = db.get_setting_float("min_deposit_usdt", config.MIN_DEPOSIT_USDT)

    back_kb = InlineKeyboardMarkup([[InlineKeyboardButton(T(lang,"btn_back_wallet"), callback_data="deposit")]])
    if net == "PAY":
        pay_id = binance_pay_id()
        await safe_edit(q, T(lang, "dep_net_pay", pay_id=pay_id, min_dep=min_dep), reply_markup=back_kb)
    elif net == "TRC20":
        addr = usdt_trc20()
        await safe_edit(q, T(lang, "dep_net_trc20", addr=addr or T(lang,"dep_not_configured"), min_dep=min_dep), reply_markup=back_kb)
    else:  # BEP20
        addr = usdt_bep20()
        await safe_edit(q, T(lang, "dep_net_bep20", addr=addr or T(lang,"dep_not_configured"), min_dep=min_dep), reply_markup=back_kb)
    return DEP_AMOUNT

async def deposit_amount_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    lang = get_lang(uid)
    txt  = update.message.text.strip().replace("$","").replace(",","")
    try:
        amount = float(txt)
    except ValueError:
        await update.message.reply_html(T(lang,"dep_invalid")); return DEP_AMOUNT
    min_dep = db.get_setting_float("min_deposit_usdt", config.MIN_DEPOSIT_USDT)
    if amount < min_dep:
        await update.message.reply_html(T(lang,"dep_too_small", min=min_dep)); return DEP_AMOUNT

    network = ctx.user_data.pop("dep_network", "TRC20")
    expected = db.get_unique_expected_amount(amount, network)
    expires  = (datetime.now() + timedelta(minutes=config.DEPOSIT_TIMEOUT_MINS)).isoformat()

    if network == "PAY":
        # ── Binance Pay: manual owner-approval flow ──────────────────────────
        try:
            pay_id   = binance_pay_id()
            note     = _gen_note()
            dep_id   = db.create_deposit_request(uid, amount, expected, expires,
                                                 network="PAY", deposit_type="pay", dep_note=note)
            caption  = T(lang, "dep_pay_caption", pay_id=pay_id or "Not set", expected=expected, note=note)
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton(T(lang,"btn_i_have_paid"), callback_data=f"dep_pay_ipaid_{dep_id}")],
                [InlineKeyboardButton(T(lang,"btn_back_wallet"), callback_data="deposit")],
            ])
            if not pay_id:
                # Nothing configured at all — admin needs to set this in Settings.
                await update.message.reply_html(caption, reply_markup=kb)
                return ConversationHandler.END
            # Use admin-set QR image if available, else auto-generate
            admin_qr = pay_qr_url()
            photo = admin_qr or f"https://api.qrserver.com/v1/create-qr-code/?data={urllib.parse.quote(pay_id)}&size=280x280&bgcolor=ffffff"
            try:
                await update.message.reply_photo(photo=photo, caption=caption, parse_mode="HTML", reply_markup=kb)
            except Exception as e:
                logger.warning(f"Binance Pay QR photo failed, falling back to text: {e}")
                await update.message.reply_html(caption, reply_markup=kb)
        except Exception as e:
            logger.exception(f"Binance Pay deposit-amount step failed for user {uid}: {e}")
            await update.message.reply_html(
                "⚠️ Something went wrong starting your Binance Pay deposit. Please try again, or contact support.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(T(lang,"btn_back_wallet"), callback_data="deposit")]]))
        return ConversationHandler.END

    else:
        # ── TRC20 / BEP20: on-chain TX hash verification ─────────────────────
        addr     = usdt_trc20() if network == "TRC20" else usdt_bep20()
        dep_id   = db.create_deposit_request(uid, amount, expected, expires,
                                             network=network, deposit_type="address")
        net_icon = "🔴" if network == "TRC20" else "🟡"
        explorer = "TronScan" if network == "TRC20" else "BSCScan"
        caption  = T(lang, "dep_chain_caption",
                     net_icon=net_icon, network=network,
                     addr=addr or T(lang,"dep_not_configured"),
                     expected=expected, explorer=explorer)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton(T(lang,"btn_submit_tx_hash"), callback_data=f"dep_submit_hash_{dep_id}")],
            [InlineKeyboardButton(T(lang,"btn_back_wallet"), callback_data="deposit")],
        ])
        # Use admin-set QR image if available, else auto-generate from address
        admin_qr = trc20_qr_url() if network == "TRC20" else bep20_qr_url()
        if admin_qr:
            try:
                await update.message.reply_photo(photo=admin_qr, caption=caption, parse_mode="HTML", reply_markup=kb)
            except Exception:
                await update.message.reply_html(caption, reply_markup=kb)
        elif addr:
            qr_url = f"https://api.qrserver.com/v1/create-qr-code/?data={urllib.parse.quote(addr)}&size=280x280&bgcolor=ffffff"
            try:
                await update.message.reply_photo(photo=qr_url, caption=caption, parse_mode="HTML", reply_markup=kb)
            except Exception:
                await update.message.reply_html(caption, reply_markup=kb)
        else:
            await update.message.reply_html(caption, reply_markup=kb)
        return ConversationHandler.END

async def dep_submit_hash_start_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """User clicks 'Submit TX Hash' — start hash input conversation."""
    q      = update.callback_query
    await q.answer()
    _lang_hash = get_lang(q.from_user.id)
    dep_id = int(q.data.split("_")[3])
    dep    = db.get_deposit(dep_id)
    if not dep or dep["user_id"] != q.from_user.id:
        await q.answer(T(_lang_hash,"alert_not_found"), show_alert=True); return ConversationHandler.END
    if dep["status"] != "pending":
        await q.answer(T(_lang_hash,"dep_not_pending_alert"), show_alert=True); return ConversationHandler.END
    ctx.user_data["dep_hash_id"] = dep_id
    await safe_edit(q, T(_lang_hash, "dep_submit_hash_prompt"))
    return DEP_TX_HASH

_VERIFY_FRAMES = ["🔎 Verifying", "🔎 Verifying.", "🔎 Verifying..", "🔎 Verifying...",
                  "⛓️ Reading blockchain", "⛓️ Reading blockchain.", "⛓️ Reading blockchain..",
                  "🔒 Checking wallet + amount + time", "🔒 Checking wallet + amount + time."]

async def _live_verify(msg, label: str, verify_coro):
    """Runs verify_coro while animating `msg` with a live progress text, like a
    real-time 'checking the blockchain' indicator. Returns verify_coro's result."""
    stop = asyncio.Event()

    async def _animate():
        i = 0
        while not stop.is_set():
            frame = _VERIFY_FRAMES[i % len(_VERIFY_FRAMES)]
            try:
                await msg.edit_text(
                    f"{frame}\n\n<i>Checking {label} — amount, wallet address & time…</i>",
                    parse_mode="HTML"
                )
            except Exception:
                pass
            i += 1
            try:
                await asyncio.wait_for(stop.wait(), timeout=1.4)
            except asyncio.TimeoutError:
                pass

    anim_task = asyncio.create_task(_animate())
    try:
        return await verify_coro
    finally:
        stop.set()
        try:
            await anim_task
        except Exception:
            pass

async def dep_tx_hash_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """User typed their TX hash — verify on-chain and credit or show error."""
    uid    = update.effective_user.id
    lang   = get_lang(uid)
    tx     = update.message.text.strip()
    dep_id = ctx.user_data.get("dep_hash_id")

    if not dep_id:
        await update.message.reply_text(T(lang,"dep_session_expired"))
        return ConversationHandler.END

    dep = db.get_deposit(dep_id)
    if not dep or dep["user_id"] != uid or dep["status"] != "pending":
        await update.message.reply_text(T(lang,"dep_not_found"))
        return ConversationHandler.END

    # Basic TX hash format check
    clean_tx = tx.strip()
    if len(clean_tx) < 20:
        await update.message.reply_text(T(lang,"dep_invalid_hash"))
        return DEP_TX_HASH

    # ── Anti-double-spend: this exact tx hash can only ever credit ONE deposit ──
    dupe = db.get_completed_deposit_by_txhash(clean_tx)
    if dupe:
        db.set_deposit_tx_hash(dep_id, clean_tx)
        db.mark_deposit_failed(dep_id, "duplicate_tx_hash")
        ctx.user_data.pop("dep_hash_id", None)
        await update.message.reply_html(
            T(lang,"dep_duplicate_hash"),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(T(lang,"btn_notify_admin"), callback_data=f"dep_notify_adm_{dep_id}")],
                [InlineKeyboardButton(T(lang,"btn_back_wallet"), callback_data="deposit")],
            ])
        )
        return ConversationHandler.END

    # Save hash to DB
    db.set_deposit_tx_hash(dep_id, clean_tx)
    ctx.user_data.pop("dep_hash_id", None)

    net  = dep.get("network", "TRC20")
    addr = usdt_trc20() if net == "TRC20" else usdt_bep20()

    wait_msg = await update.message.reply_html(
        f"🔎 Verifying\n\n<i>Checking {('TronScan' if net=='TRC20' else 'BSCScan')} — amount, wallet address & time…</i>"
    )

    # On-chain verification (with a live-updating progress message)
    ok, err = False, "timeout"
    try:
        if net == "TRC20":
            ok, err = await _live_verify(wait_msg, "TronScan",
                                          _verify_trc20_tx(clean_tx, addr, dep["expected_usdt"], dep.get("created_at")))
        else:
            ok, err = await _live_verify(wait_msg, "BSCScan",
                                          _verify_bep20_tx(clean_tx, addr, dep["expected_usdt"], dep.get("created_at")))
    except Exception as e:
        logger.warning(f"TX verify error dep#{dep_id}: {e}")
        ok, err = False, "exception"

    if err == "tx_too_old":
        try:
            await wait_msg.delete()
        except Exception:
            pass
        await update.message.reply_html(
            T(lang,"dep_tx_too_old"),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(T(lang,"btn_notify_admin"), callback_data=f"dep_notify_adm_{dep_id}")],
                [InlineKeyboardButton(T(lang,"btn_back_wallet"), callback_data="deposit")],
            ])
        )
        return ConversationHandler.END

    if ok:
        # ── VERIFIED — credit balance ─────────────────────────────────────────
        await _credit_deposit_manual(ctx, dep, clean_tx, dep["requested_usdt"],
                                     f"{net} (hash:{clean_tx[:12]})")
        try:
            await wait_msg.delete()
        except Exception:
            pass
        u = db.get_user(uid)
        new_bal = u["balance"] if u else 0
        await update.message.reply_html(
            T(lang,"dep_verified_credited", amount=fmt(dep['requested_usdt']), new_bal=new_bal),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(T(lang,"btn_open_shop"), callback_data="shop")]])
        )
    else:
        # ── NOT VERIFIED ─────────────────────────────────────────────────────
        try:
            await wait_msg.delete()
        except Exception:
            pass
        await update.message.reply_html(
            T(lang,"dep_not_verified"),
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton(T(lang,"btn_check_again"),  callback_data=f"dep_check_{dep_id}_{clean_tx[:40]}")],
                [InlineKeyboardButton(T(lang,"btn_notify_admin"), callback_data=f"dep_notify_adm_{dep_id}")],
                [InlineKeyboardButton(T(lang,"btn_back_wallet"),  callback_data="deposit")],
            ])
        )
    return ConversationHandler.END

async def dep_check_again_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Re-verify the stored TX hash."""
    q      = update.callback_query
    uid    = q.from_user.id
    lang   = get_lang(uid)
    await q.answer(T(lang,"dep_recheck_rechecking"))
    parts  = q.data.split("_")   # dep_check_{dep_id}_{tx}
    dep_id = int(parts[2])
    tx     = parts[3] if len(parts) > 3 else ""

    dep = db.get_deposit(dep_id)
    if not dep or dep["user_id"] != uid:
        await q.answer(T(lang,"dep_not_found"), show_alert=True); return
    if dep["status"] != "pending":
        await q.edit_message_text(T(lang,"dep_already_processed")); return

    net  = dep.get("network", "TRC20")
    addr = usdt_trc20() if net == "TRC20" else usdt_bep20()
    stored_tx = dep.get("tx_hash") or tx

    # ── Anti-double-spend: re-check in case this hash got claimed elsewhere meanwhile ──
    dupe = db.get_completed_deposit_by_txhash(stored_tx)
    if dupe:
        db.mark_deposit_failed(dep_id, "duplicate_tx_hash")
        await q.edit_message_text(T(lang,"dep_duplicate_recheck"), parse_mode="HTML")
        return

    ok, err = False, "timeout"
    try:
        if net == "TRC20":
            ok, err = await _live_verify(q.message, "TronScan",
                                          _verify_trc20_tx(stored_tx, addr, dep["expected_usdt"], dep.get("created_at")))
        else:
            ok, err = await _live_verify(q.message, "BSCScan",
                                          _verify_bep20_tx(stored_tx, addr, dep["expected_usdt"], dep.get("created_at")))
    except Exception as e:
        logger.warning(f"Re-check error dep#{dep_id}: {e}")

    if err == "tx_too_old":
        await q.edit_message_text(T(lang,"dep_tx_too_old_recheck"), parse_mode="HTML")
        return

    if ok:
        await _credit_deposit_manual(ctx, dep, stored_tx, dep["requested_usdt"], net)
        u = db.get_user(uid)
        new_bal = u["balance"] if u else 0
        await q.edit_message_text(
            T(lang,"dep_verified_recheck", amount=fmt(dep['requested_usdt']), new_bal=new_bal),
            parse_mode="HTML"
        )
    else:
        try:
            await q.edit_message_text(
                T(lang,"dep_still_not_found"),
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton(T(lang,"btn_check_again"),  callback_data=f"dep_check_{dep_id}_{stored_tx[:40]}")],
                    [InlineKeyboardButton(T(lang,"btn_notify_admin"), callback_data=f"dep_notify_adm_{dep_id}")],
                ])
            )
        except Exception:
            await q.answer(T(lang,"dep_check_failed_alert"), show_alert=True)

async def dep_notify_admin_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """User requests admin manual review for a failed TX verification."""
    q      = update.callback_query
    uid    = q.from_user.id
    lang   = get_lang(uid)
    await q.answer(T(lang,"admin_notified_alert"))
    dep_id = int(q.data.split("_")[3])
    dep    = db.get_deposit(dep_id)
    if not dep:
        await q.answer(T(lang,"alert_not_found"), show_alert=True); return

    user  = db.get_user(uid)
    uname = f"@{user['username']}" if user and user.get("username") else str(uid)
    net   = dep.get("network", "TRC20")
    tx    = dep.get("tx_hash", "Not submitted")

    admin_text = (
        f"⚠️ <b>Manual Deposit Review Needed</b>\n{sep()}\n\n"
        f"👤 {uname} (<code>{uid}</code>)\n"
        f"🔗 Network: <b>{net}</b>\n"
        f"💵 Amount: <b>{fmt(dep['expected_usdt'])}</b>\n"
        f"🔑 TX Hash: <code>{tx}</code>\n"
        f"📅 Created: {(dep.get('created_at') or '')[:16]}\n\n"
        f"User could not verify automatically. Please check and approve/reject."
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve",  callback_data=f"adm_dep_ok_{dep_id}"),
            InlineKeyboardButton("❌ Reject",   callback_data=f"adm_dep_no_{dep_id}"),
        ]
    ])
    for adm_id in list(config.ADMIN_IDS) + db.get_extra_admins():
        try:
            await ctx.application.bot.send_message(adm_id, admin_text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass

    _lang_notify = get_lang(uid)
    await q.edit_message_text(
        T(_lang_notify,"dep_admin_notified", dep_id=dep_id, amount=fmt(dep['expected_usdt'])),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(T(_lang_notify,"btn_back_wallet"), callback_data="deposit")]])
    )

async def dep_pay_ipaid_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """User tapped 'I Have Paid' for Binance Pay — notify admin."""
    q      = update.callback_query
    await q.answer()
    uid    = q.from_user.id
    lang   = get_lang(uid)
    dep_id = int(q.data.split("_")[3])
    dep    = db.get_deposit(dep_id)
    if not dep or dep["user_id"] != uid:
        await q.answer(T(lang,"dep_not_found"), show_alert=True); return
    if dep["status"] != "pending":
        await q.answer(T(lang,"dep_already_processed"), show_alert=True); return

    user  = db.get_user(uid)
    uname = f"@{user['username']}" if user and user.get("username") else str(uid)
    note  = dep.get("dep_note", "N/A")

    admin_text = (
        f"✦ <b>New Binance Pay Deposit</b>\n{sep()}\n\n"
        f"👤 User: {uname} (<code>{uid}</code>)\n"
        f"💵 Amount: <b>{fmt(dep['expected_usdt'])}</b>\n"
        f"📝 Note: <code>{note}</code>\n"
        f"📅 Time: {(dep.get('created_at') or '')[:16]}\n\n"
        f"Check your Binance Pay history for payment with note <b>{note}</b> "
        f"and amount <b>{dep['expected_usdt']:.3f} USDT</b>, then approve or reject."
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Approve", callback_data=f"adm_dep_ok_{dep_id}"),
            InlineKeyboardButton("❌ Reject",  callback_data=f"adm_dep_no_{dep_id}"),
        ]
    ])
    for adm_id in list(config.ADMIN_IDS) + db.get_extra_admins():
        try:
            await ctx.application.bot.send_message(adm_id, admin_text, parse_mode="HTML", reply_markup=kb)
        except Exception:
            pass

    await safe_edit(q,
        T(lang,"dep_pay_submitted", dep_id=dep_id, amount=fmt(dep['expected_usdt'])),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(T(lang,"btn_back_wallet"), callback_data="deposit")]])
    )

async def adm_dep_ok_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Admin approves a pending deposit (Pay or failed TX)."""
    q   = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.answer("Admins only.", show_alert=True); return
    dep_id = int(q.data.split("_")[3])
    dep    = db.get_deposit(dep_id)
    if not dep or dep["status"] != "pending":
        await q.edit_message_text(q.message.text + "\n\n⚠️ Already resolved."); return

    await _credit_deposit_manual(ctx, dep, dep.get("tx_hash") or "ADMIN", dep["requested_usdt"],
                                  f"{dep.get('network','PAY')} (admin approved)")
    await q.edit_message_text(
        q.message.text + f"\n\n✅ <b>APPROVED</b> by <code>{q.from_user.id}</code>",
        parse_mode="HTML"
    )

async def adm_dep_no_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Admin rejects a pending deposit."""
    q   = update.callback_query
    await q.answer()
    if not is_admin(q.from_user.id):
        await q.answer("Admins only.", show_alert=True); return
    dep_id = int(q.data.split("_")[3])
    dep    = db.get_deposit(dep_id)
    if not dep or dep["status"] != "pending":
        await q.edit_message_text(q.message.text + "\n\n⚠️ Already resolved."); return

    db.set_deposit_status(dep_id, "cancelled")

    try:
        lang_u = get_lang(dep["user_id"])
        await ctx.application.bot.send_message(
            dep["user_id"],
            T(lang_u,"dep_rejected_user", amount=fmt(dep['expected_usdt'])),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(T(lang_u,"btn_support_ticket"), callback_data="support")]])
        )
    except Exception:
        pass
    await q.edit_message_text(
        q.message.text + f"\n\n❌ <b>REJECTED</b> by <code>{q.from_user.id}</code>",
        parse_mode="HTML"
    )

async def dep_status_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q      = update.callback_query
    await q.answer()
    uid    = q.from_user.id
    lang   = get_lang(uid)
    dep_id = int(q.data.split("_")[2])
    dep    = db.get_deposit(dep_id)
    if not dep: await q.answer(T(lang,"alert_not_found"), show_alert=True); return
    icons = {"pending":"⏳","completed":"✅","expired":"❌","cancelled":"🚫"}
    status_key_map = {"pending":"dep_status_pending","completed":"dep_status_completed",
                      "expired":"dep_status_expired","cancelled":"dep_status_cancelled"}
    st  = dep["status"]
    net = dep.get("network","TRC20")
    tx  = dep.get("tx_hash") or dep.get("binance_txid") or "—"
    await q.edit_message_text(
        T(lang,"dep_status_title",
          dep_id=dep_id, net=net,
          amount=fmt(dep['expected_usdt']),
          status_icon=icons.get(st,'•'),
          status_label=T(lang, status_key_map.get(st,"dep_status_pending")),
          tx=str(tx)[:30],
          created_at=(dep.get('created_at') or '')[:16]),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(T(lang,"btn_refresh"), callback_data=f"dep_status_{dep_id}")],
            [InlineKeyboardButton(T(lang,"btn_back"),    callback_data="deposit")],
        ]),
        parse_mode="HTML"
    )

async def dep_history_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    lang = get_lang(uid)
    deps = db.get_user_deposits(uid)
    icons = {"completed":"✅","pending":"⏳","expired":"❌","cancelled":"🚫"}
    if not deps:
        text = T(lang,"dep_history_empty")
    else:
        lines = [f"{T(lang,'dep_history_title')}\n{sep()}\n"]
        for d in deps:
            net = d.get("network","TRC20")
            lines.append(f"{icons.get(d['status'],'•')} #{d['id']:04d} [{net}] — {fmt(d['requested_usdt'])} — {(d['created_at'] or '')[:10]}")
        text = "\n".join(lines)
    await q.edit_message_text(text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(T(lang,"btn_back"), callback_data="deposit")]]),
        parse_mode="HTML")

# ─────────────────────────────────────────────────────────────────────────────
#  SUPPORT TICKETS
# ─────────────────────────────────────────────────────────────────────────────

async def support_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    lang = get_lang(uid)
    await se(q, T(lang,"support_title"), [
        [sb(T(lang,"btn_new_ticket"), "ticket_new",  style="success", emoji_id=EC.E_NEW_TICKET)],
        [sb(T(lang,"btn_my_tickets"), "ticket_list", style="primary", emoji_id=EC.E_MY_TICKETS)],
        [sb(T(lang,"btn_home"),       "home",        style="danger",  emoji_id=EC.E_HOME)],
    ])

async def ticket_new_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    lang = get_lang(uid)
    await q.edit_message_text(T(lang,"ticket_create_msg"), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(T(lang,"btn_cancel"), callback_data="support")]]))
    return TICKET_MSG

async def ticket_msg_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    lang = get_lang(uid)
    subject = update.message.text.strip()
    if not subject:
        await update.message.reply_text(T(lang,"ticket_describe_issue")); return TICKET_MSG
    tid = db.create_ticket(uid, subject)
    db.add_ticket_message(tid, uid, subject, is_admin=False)
    # Notify admins
    u = db.get_user(uid)
    name, uname = user_display(u) if u else ("?", "?")
    for admin_id in list(config.ADMIN_IDS) + db.get_extra_admins():
        try:
            await ctx.bot.send_message(admin_id,
                f"🎧 <b>New Support Ticket #{tid:04d}</b>\n{sep()}\n\n"
                f"👤 {name} ({uname})\n\n📝 {subject}",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton(f"📩 Reply #{tid:04d}", callback_data=f"adm_ticket_{tid}")
                ]]),
                parse_mode="HTML")
        except Exception:
            pass
    await update.message.reply_html(T(lang,"ticket_created",tid=tid,subject=subject[:50]),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(T(lang,"btn_home"), callback_data="home")]]))
    return ConversationHandler.END

async def ticket_list_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    lang = get_lang(uid)
    tickets = db.get_user_tickets(uid)
    if not tickets:
        await q.edit_message_text(T(lang,"no_open_tickets"),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(T(lang,"btn_back"), callback_data="support")]]),
            parse_mode="HTML"); return
    btns = []
    for t in tickets:
        icon = "🟢" if t["status"] == "open" else "⚫"
        btns.append([InlineKeyboardButton(f"{icon} #{t['id']:04d} {t['subject'][:25]}", callback_data=f"ticket_view_{t['id']}")])
    btns.append([InlineKeyboardButton(T(lang,"btn_back"), callback_data="support")])
    await q.edit_message_text(T(lang,"ticket_list_title"), reply_markup=InlineKeyboardMarkup(btns), parse_mode="HTML")

async def ticket_view_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    lang = get_lang(uid)
    tid  = int(q.data.split("_")[2])
    t    = db.get_ticket(tid)
    if not t or t["user_id"] != uid: await q.answer(T(lang,"alert_not_found"), show_alert=True); return
    msgs = db.get_ticket_messages(tid)
    lines = [f"📋 <b>Ticket #{tid:04d}</b>\n{sep()}\n"]
    for m in msgs[-5:]:
        who = "🔧 Admin" if m["is_admin"] else "👤 You"
        lines.append(f"{who}: {m['message'][:100]}")
    btns = []
    if t["status"] == "open":
        btns.append([InlineKeyboardButton(T(lang,"btn_send_reply"),  callback_data=f"ticket_reply_{tid}"),
                     InlineKeyboardButton(T(lang,"btn_close_ticket"), callback_data=f"ticket_close_{tid}")])
    btns.append([InlineKeyboardButton(T(lang,"btn_back"), callback_data="ticket_list")])
    await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(btns), parse_mode="HTML")

async def ticket_reply_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    lang = get_lang(uid)
    tid  = int(q.data.split("_")[2])
    ctx.user_data["reply_tid"] = tid
    await q.edit_message_text(T(lang,"ticket_reply_prompt",tid=tid), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(T(lang,"btn_cancel"), callback_data=f"ticket_view_{tid}")]]))
    return TICKET_REPLY

async def ticket_reply_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    lang = get_lang(uid)
    tid  = ctx.user_data.pop("reply_tid", None)
    if not tid: return ConversationHandler.END
    msg = update.message.text.strip()
    db.add_ticket_message(tid, uid, msg, is_admin=False)
    t = db.get_ticket(tid)
    for admin_id in list(config.ADMIN_IDS) + db.get_extra_admins():
        try:
            await ctx.bot.send_message(admin_id,
                f"📩 <b>Ticket #{tid:04d} Reply</b>\n\n{msg}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"📩 Reply", callback_data=f"adm_ticket_{tid}")]]),
                parse_mode="HTML")
        except Exception:
            pass
    await update.message.reply_html(T(lang,"ticket_reply_sent",tid=tid),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(T(lang,"btn_home"), callback_data="home")]]))
    return ConversationHandler.END

async def ticket_close_user_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    lang = get_lang(uid)
    tid  = int(q.data.split("_")[2])
    db.close_ticket(tid)
    await q.edit_message_text(T(lang,"ticket_closed_user",tid=tid), parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(T(lang,"btn_home"), callback_data="home")]]))

# ── Admin ticket reply ────────────────────────────────────────────────────────

async def adm_ticket_view_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    tid = int(q.data.split("_")[2])
    t   = db.get_ticket(tid)
    if not t: return
    msgs = db.get_ticket_messages(tid)
    u    = db.get_user(t["user_id"])
    name, uname = user_display(u) if u else ("?","?")
    lines = [f"🎧 <b>Ticket #{tid:04d}</b>\n{sep()}\n👤 {name} ({uname})\n"]
    for m in msgs[-10:]:
        who = "🔧 Admin" if m["is_admin"] else "👤 User"
        lines.append(f"{who}: {m['message'][:150]}")
    btns = []
    if t["status"] == "open":
        btns.append([InlineKeyboardButton("📩 Reply",       callback_data=f"adm_reply_ticket_{tid}"),
                     InlineKeyboardButton("✅ Close",        callback_data=f"adm_close_ticket_{tid}")])
    btns.append([InlineKeyboardButton("⚙️ Admin", callback_data="admin")])
    await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(btns), parse_mode="HTML")

async def adm_reply_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    tid = int(q.data.split("_")[3])
    ctx.user_data["admin_reply_tid"] = tid
    await q.edit_message_text(f"📩 <b>Reply to Ticket #{tid:04d}</b>\n\nType your reply:\n\n/cancel to abort",
                               parse_mode="HTML")
    return ADM_BROADCAST  # reuse state name

async def adm_reply_received(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    tid = ctx.user_data.pop("admin_reply_tid", None)
    if not tid: return ConversationHandler.END
    msg = update.message.text.strip()
    db.add_ticket_message(tid, update.effective_user.id, msg, is_admin=True)
    t = db.get_ticket(tid)
    try:
        lang_u = db.get_user_language(t["user_id"])
        await ctx.bot.send_message(t["user_id"],
            T(lang_u,"ticket_admin_reply",tid=tid,msg=msg),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(T(lang_u,"btn_view_ticket",tid=tid), callback_data=f"ticket_view_{tid}")
            ]]), parse_mode="HTML")
    except Exception:
        pass
    await update.message.reply_html(f"✅ Reply sent for Ticket <b>#{tid:04d}</b>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Admin", callback_data="admin")]]))
    return ConversationHandler.END

async def adm_close_ticket_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    tid = int(q.data.split("_")[3])
    db.close_ticket(tid)
    t   = db.get_ticket(tid)
    if t:
        try:
            lang_u = db.get_user_language(t["user_id"])
            await ctx.bot.send_message(t["user_id"],
                T(lang_u,"ticket_closed_user",tid=tid), parse_mode="HTML")
        except Exception:
            pass
    await q.edit_message_text(f"✅ Ticket #{tid:04d} closed.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎧 Tickets", callback_data="adm_tickets")]]),
        parse_mode="HTML")

# ─────────────────────────────────────────────────────────────────────────────
#  BLOCKCHAIN TX VERIFICATION  (TronScan + BSCScan)
# ─────────────────────────────────────────────────────────────────────────────

def _tx_too_old(tx_epoch_seconds: float, dep_created_at: str, grace_minutes: int = 30) -> bool:
    """True if the on-chain tx timestamp is clearly from BEFORE this deposit request
    was even created (minus a small clock-skew grace window) — i.e. someone is
    reusing an old transaction hash that has nothing to do with this deposit."""
    if not tx_epoch_seconds or not dep_created_at:
        return False
    try:
        created = datetime.fromisoformat(dep_created_at)
    except Exception:
        return False
    tx_time = datetime.fromtimestamp(tx_epoch_seconds)
    return tx_time < (created - timedelta(minutes=grace_minutes))

async def _verify_trc20_tx(tx_hash: str, to_addr: str, expected: float, dep_created_at: str = None):
    """Verify a TRC20 USDT transfer via TronScan's free public explorer API
    (no API key required — same endpoint tronscan.org itself uses). Returns (ok, err_str)."""
    if not to_addr:
        return False, "no_address"
    url = f"https://apilist.tronscanapi.com/api/transaction-info?hash={tx_hash}"
    tron_key = db.get_setting("tronscan_api_key", "") or getattr(config, "TRONSCAN_API_KEY", "")
    headers = {"TRON-PRO-API-KEY": tron_key} if tron_key else {}
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=15)) as r:
                if r.status != 200:
                    return False, "api_error"
                data = await r.json()
    except Exception:
        return False, "timeout"
    if not data:
        return False, "not_found"
    if not data.get("confirmed"):
        return False, "unconfirmed"
    ts_ms = data.get("timestamp") or data.get("date_created") or 0
    if ts_ms and _tx_too_old(ts_ms / 1000, dep_created_at):
        return False, "tx_too_old"
    for t in data.get("trc20TransferInfo", []):
        if t.get("symbol", "").upper() != "USDT":
            continue
        if t.get("to_address", "").upper() != to_addr.upper():
            continue
        try:
            amt = float(t.get("amount_str", "0")) / 1e6
        except Exception:
            continue
        if abs(amt - expected) < 0.02:
            return True, ""
    return False, "not_found"

# Public BSC full-node RPC endpoints — no API key needed, same data BscScan itself reads from.
_BSC_PUBLIC_RPC = [
    "https://bsc-dataseed.binance.org/",
    "https://bsc-dataseed1.defibit.io/",
    "https://bsc-dataseed1.ninicoin.io/",
    "https://bsc.publicnode.com/",
]

async def _bsc_rpc(method: str, params: list):
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    for endpoint in _BSC_PUBLIC_RPC:
        try:
            async with aiohttp.ClientSession() as s:
                async with s.post(endpoint, json=payload, timeout=aiohttp.ClientTimeout(total=12)) as r:
                    if r.status != 200:
                        continue
                    data = await r.json()
                    if "result" in data:
                        return data["result"]
        except Exception:
            continue
    return None

async def _verify_bep20_tx(tx_hash: str, to_addr: str, expected: float, dep_created_at: str = None):
    """Verify a BEP20 USDT transfer directly against BSC's public RPC network —
    no BscScan account or API key required at all, and works the same as checking
    the transaction on bscscan.com by hand (status, amount, destination wallet, time)."""
    if not to_addr:
        return False, "no_address"
    BSC_USDT   = "0x55d398326f99059ff775485246999027b3197955"
    TRANSFER_T = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

    result = await _bsc_rpc("eth_getTransactionReceipt", [tx_hash])
    if not result:
        return False, "not_found"
    if result.get("status") != "0x1":
        return False, "failed_tx"

    # Time check — pull the block's timestamp to confirm the tx isn't an old/replayed hash.
    block_no = result.get("blockNumber")
    if block_no and dep_created_at:
        block = await _bsc_rpc("eth_getBlockByNumber", [block_no, False])
        if block and block.get("timestamp"):
            ts = int(block["timestamp"], 16)
            if _tx_too_old(ts, dep_created_at):
                return False, "tx_too_old"

    to_padded = "0x000000000000000000000000" + to_addr.lower().replace("0x", "")
    for log in result.get("logs", []):
        topics = log.get("topics", [])
        if (log.get("address", "").lower() == BSC_USDT and
                len(topics) >= 3 and
                topics[0].lower() == TRANSFER_T and
                topics[2].lower() == to_padded):
            try:
                amt = int(log["data"], 16) / 1e18   # BSC USDT = 18 decimals
                if abs(amt - expected) < 0.02:
                    return True, ""
            except Exception:
                pass
    return False, "amount_mismatch"

async def _credit_deposit_manual(context, dep: dict, txid: str, credited: float, network_label: str):
    """Credit a deposit: mark complete, update balance, VIP/referral bonuses, notify user + log channel."""
    # Final race-condition guard: never let the same on-chain tx hash credit twice.
    if txid and txid != "ADMIN":
        existing = db.get_completed_deposit_by_txhash(txid)
        if existing and existing["id"] != dep["id"]:
            db.mark_deposit_failed(dep["id"], "duplicate_tx_hash")
            logger.warning(f"Blocked duplicate credit attempt: tx {txid} already used by deposit #{existing['id']}")
            return
    db.complete_deposit(dep["id"], txid)
    u = db.get_user(dep["user_id"])

    vip_bonus = 0.0
    if u and u.get("is_vip") and credited >= config.VIP_MIN_BONUS_DEPOSIT:
        vip_bonus = round(credited * config.VIP_DEPOSIT_BONUS_PCT / 100, 6)
    db.update_balance(dep["user_id"], credited + vip_bonus)

    # Referral bonus
    ref_id, ref_bonus = db.credit_referral_deposit_bonus(dep["user_id"], credited)
    if ref_id and ref_bonus > 0:
        try:
            ref_lang = get_lang(ref_id)
            await context.bot.send_message(ref_id,
                T(ref_lang,"referral_bonus_msg", amount=fmt(credited), bonus=fmt(ref_bonus)),
                parse_mode="HTML")
        except Exception:
            pass

    u2 = db.get_user(dep["user_id"])
    new_bal  = u2["balance"] if u2 else credited
    lang     = get_lang(dep["user_id"])
    vip_line = T(lang, "vip_dep_line", bonus=vip_bonus) if vip_bonus else ""
    try:
        await context.bot.send_message(dep["user_id"],
            T(lang, "dep_confirmed", amount=credited, vip_line=vip_line, balance=new_bal),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton(T(lang, "btn_shop"), callback_data="shop")
            ]]), parse_mode="HTML")
    except Exception:
        pass

    name_d = ""
    if u2:
        name_d = f"@{u2['username']}" if u2.get("username") else u2.get("full_name", str(dep["user_id"]))
    await log_dep(context.bot,
        f"💰 <b>Deposit Credited</b>\n{sep()}\n"
        f"👤 {name_d} (<code>{dep['user_id']}</code>)\n"
        f"💵 {fmt(credited)}\n"
        f"🔗 Method: {network_label}\n"
        f"🔑 TX: <code>{(txid or 'N/A')[:40]}</code>")

# ─────────────────────────────────────────────────────────────────────────────
#  FREE ITEMS  (owner gives away items — each user may claim ONE, ever)
# ─────────────────────────────────────────────────────────────────────────────

async def free_items_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """User-facing: list claimable free items."""
    q   = update.callback_query
    await q.answer()
    uid = q.from_user.id
    already = db.has_user_claimed_free_item(uid)
    items = db.get_free_items(active_only=True)
    avail = [(it, db.get_free_stock_count(it["id"])) for it in items]
    avail = [(it, n) for it, n in avail if n > 0]

    lang = get_lang(uid)
    if already:
        await se(q,
            T(lang,"free_item_already_claimed"),
            [[sb(T(lang,"btn_home"), "home", style="danger", emoji_id=EC.E_HOME)]]
        )
        return

    if not avail:
        await se(q,
            T(lang,"free_item_none_available"),
            [[sb(T(lang,"btn_home"), "home", style="danger", emoji_id=EC.E_HOME)]]
        )
        return

    rows = []
    lines = [T(lang,"free_item_list_header")]
    for it, n in avail:
        lines.append(T(lang,"free_item_stock_line",emoji=it['emoji'],name=it['name'],count=n))
        rows.append([sb(T(lang,"btn_claim_free",emoji=it['emoji'],name=it['name']), f"free_claim_{it['id']}", style="success")])
    rows.append([sb(T(lang,"btn_home"), "home", style="danger", emoji_id=EC.E_HOME)])
    await se(q, "\n".join(lines), rows)

async def free_item_claim_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q    = update.callback_query
    uid  = q.from_user.id
    lang = get_lang(uid)
    fid  = int(q.data.split("_")[2])
    item = db.get_free_item(fid)
    if not item or not item.get("is_active"):
        await q.answer(T(lang,"free_item_not_available"), show_alert=True); return
    ok, result = db.claim_free_item(uid, fid)
    if not ok:
        if result == "already_claimed":
            await q.answer(T(lang,"free_item_already_alert"), show_alert=True)
        else:
            await q.answer(T(lang,"free_item_oos_alert"), show_alert=True)
        return
    await q.answer(T(lang,"free_item_claimed_alert"))
    await se(q,
        T(lang,"free_item_claimed_msg",emoji=item['emoji'],name=item['name'],data=result),
        [[sb(T(lang,"btn_home"), "home", style="danger", emoji_id=EC.E_HOME)]]
    )
    user = db.get_user(uid)
    uname = f"@{user['username']}" if user and user.get("username") else str(uid)
    await log_ch(ctx.bot,
        f"🎁 <b>Free Item Claimed</b>\n{sep()}\n"
        f"👤 {uname} (<code>{uid}</code>)\n"
        f"📦 {item['emoji']} {item['name']}")

# ── Admin: manage free items ──────────────────────────────────────────────────

async def adm_free_menu_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    items = db.get_free_items(active_only=False)
    lines = [f"🎁 <b>FREE ITEMS</b>\n{sep()}\n"]
    rows = []
    if not items:
        lines.append("No free items yet. Tap below to add one.")
    for it in items:
        n = db.get_free_stock_count(it["id"])
        status = "🟢" if it["is_active"] else "🔴"
        lines.append(f"{status} {it['emoji']} <b>{it['name']}</b> — 📦 {n} left")
        rows.append([
            sb(f"{it['emoji']} {it['name']} ({n})", "noop"),
        ])
        rows.append([
            sb("📥 Add Stock", f"adm_free_addstock_{it['id']}", style="success"),
            sb("🔴 Disable" if it["is_active"] else "🟢 Enable",
               f"adm_free_toggle_{it['id']}", style="primary"),
            sb("🗑️", f"adm_free_del_{it['id']}", style="danger"),
        ])
    rows.append([sb("➕ Add New Free Item", "adm_free_add", style="success")])
    rows.append([sb("« Admin", "admin", style="danger")])
    await se(q, "\n".join(lines), rows)

async def adm_free_add_start(u, c):
    return await _simple_start(u, c, "🎁 <b>New Free Item</b>\n\nSend the item's name (e.g. \"1 Month Netflix\"):\n(/cancel to abort)", ADM_FREE_NAME)

async def adm_free_name_save(u, c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    c.user_data["free_name"] = u.message.text.strip()
    await u.message.reply_html("✨ Now send an emoji for this item (e.g. 🎁, 🎬, 💳):")
    return ADM_FREE_EMOJI

async def adm_free_emoji_save(u, c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    emoji = u.message.text.strip()[:8] or "🎁"
    name  = c.user_data.pop("free_name", "Free Item")
    fid   = db.create_free_item(name, emoji)
    c.user_data["free_stock_id"] = fid
    await u.message.reply_html(
        f"✅ Created <b>{emoji} {name}</b>.\n\n"
        f"Now send the stock — one item/code per line (this is what users receive when they claim):\n"
        f"(/cancel to abort — item stays saved, add stock anytime from 🎁 Free Items)")
    return ADM_FREE_STOCK

async def adm_free_addstock_start_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    fid = int(q.data.split("_")[3])
    item = db.get_free_item(fid)
    if not item:
        await q.answer("Not found.", show_alert=True); return
    ctx.user_data["free_stock_id"] = fid
    await q.message.reply_html(
        f"📥 <b>Add Stock — {item['emoji']} {item['name']}</b>\n\n"
        f"Send one item/code per line:\n(/cancel to abort)")
    return ADM_FREE_STOCK

async def _notify_free_item_available(bot, item):
    """Announce a newly-stocked free item to every user AND to the log channels."""
    users = db.get_all_users()
    sent = failed = 0
    for u in users:
        try:
            _lang = db.get_user_language(u["user_id"]) or "en"
            _text = T(_lang, "free_item_broadcast", emoji=item['emoji'], name=item['name'])
            _kb   = InlineKeyboardMarkup([[InlineKeyboardButton(
                T(_lang,"btn_claim_now",emoji=item['emoji']),
                callback_data=f"free_claim_{item['id']}"
            )]])
            await bot.send_message(u["user_id"], _text, parse_mode="HTML", reply_markup=_kb)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.04)
    for send_fn in (log_ch, log_dep):
        try:
            await send_fn(bot, f"🎁 <b>New Free Item Live</b>\n{sep()}\n{item['emoji']} {item['name']}\n📨 Announced to {sent} user(s) ({failed} failed)")
        except Exception:
            pass

async def adm_free_stock_save(u, c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    fid = c.user_data.pop("free_stock_id", None)
    item = db.get_free_item(fid) if fid else None
    if not item:
        await u.message.reply_text("❌ Session expired."); return ConversationHandler.END
    was_empty = db.get_free_stock_count(fid) == 0
    lines = [l for l in u.message.text.strip().splitlines() if l.strip()]
    n = db.add_free_stock(fid, lines)
    new_count = db.get_free_stock_count(fid)
    await u.message.reply_html(
        f"✅ Added <b>{n}</b> item(s) to <b>{item['emoji']} {item['name']}</b>.\n"
        f"📦 Total left: <b>{new_count}</b>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎁 Free Items", callback_data="adm_free_menu")]]))
    if was_empty and new_count > 0 and item.get("is_active"):
        await u.message.reply_html("📢 Notifying all users & log channels that this item is now available…")
        await _notify_free_item_available(c.bot, item)
    return ConversationHandler.END

async def adm_free_toggle_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    fid = int(q.data.split("_")[3])
    db.toggle_free_item(fid)
    item = db.get_free_item(fid)
    await q.answer("Updated.")
    if item and item.get("is_active") and db.get_free_stock_count(fid) > 0:
        await q.message.reply_html("📢 Notifying all users & log channels that this item is now available…")
        await _notify_free_item_available(ctx.bot, item)
    await adm_free_menu_cb(update, ctx)

async def adm_free_del_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    db.delete_free_item(int(q.data.split("_")[3]))
    await q.answer("Deleted.")
    await adm_free_menu_cb(update, ctx)

# ─────────────────────────────────────────────────────────────────────────────
#  ADMIN — DAILY HISTORY (browse any day: deposits, orders, wallet snapshot)
# ─────────────────────────────────────────────────────────────────────────────

async def adm_daily_menu_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    rows = []
    for i in range(7):
        d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
        label = "Today" if i == 0 else ("Yesterday" if i == 1 else d)
        rows.append([sb(f"📅 {label}", f"adm_daily_{d}", style="primary")])
    rows.append([sb("✏️ Type a date (YYYY-MM-DD)", "adm_daily_custom", style="success")])
    rows.append([sb("« Admin", "admin", style="danger")])
    await se(q, f"📅 <b>DAILY HISTORY</b>\n{sep()}\n\nPick a date to see that day's deposits, orders & wallet snapshot:", rows)

async def adm_daily_custom_start(u, c):
    return await _simple_start(u, c, "✏️ <b>Type a date</b>\n\nFormat: <code>YYYY-MM-DD</code> (e.g. 2026-07-10)\n\n(/cancel to abort)", ADM_DAILY_DATE)

async def adm_daily_custom_save(u, c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    date_str = u.message.text.strip()
    try:
        datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        await u.message.reply_html("❌ Invalid format. Use <code>YYYY-MM-DD</code>, e.g. 2026-07-10. Try again or /cancel.")
        return ADM_DAILY_DATE
    await _send_daily_report(u.message, date_str)
    return ConversationHandler.END

async def _send_daily_report(message, date_str):
    r = db.get_daily_report(date_str)
    lines = [
        f"📅 <b>DAILY REPORT — {date_str}</b>\n{sep()}\n",
        f"🛒 Orders: <b>{r['ord_count']}</b>  →  {fmt(r['ord_amount'])}",
        f"✅ Deposits Credited: <b>{r['dep_count']}</b>  →  {fmt(r['dep_amount'])}",
        f"❌ Deposits Failed/Expired: <b>{r['dep_failed']}</b>",
        f"🆕 New Users Joined: <b>{r['new_users']}</b>\n",
        f"👥 <b>Current Total Users:</b> {r['total_users_now']}",
        f"👛 <b>Current Total Wallet Balance (all users):</b> {fmt(r['total_balance_now'])}\n",
    ]
    if r["deposits"]:
        lines.append(f"💰 <b>Deposits that day:</b>")
        for d in r["deposits"][:15]:
            uname = f"@{d['username']}" if d.get("username") else str(d["user_id"])
            lines.append(f"  • #{d['id']:04d} {uname} — {fmt(d['requested_usdt'])} [{d.get('network','')}]")
        if len(r["deposits"]) > 15:
            lines.append(f"  …and {len(r['deposits'])-15} more")
    if r["orders"]:
        lines.append(f"\n🛍️ <b>Orders that day:</b>")
        for o in r["orders"][:15]:
            uname = f"@{o['username']}" if o.get("username") else str(o["user_id"])
            lines.append(f"  • #{o['id']:04d} {uname} — {o['product_name']} — {fmt(o['amount_usdt'])}")
        if len(r["orders"]) > 15:
            lines.append(f"  …and {len(r['orders'])-15} more")
    await message.reply_html("\n".join(lines),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📅 Daily History", callback_data="adm_daily_menu")]]))

async def adm_daily_view_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    date_str = q.data[len("adm_daily_"):]
    await _send_daily_report(q.message, date_str)

# ─────────────────────────────────────────────────────────────────────────────
#  ADMIN PANEL
# ─────────────────────────────────────────────────────────────────────────────

def _admin_panel_content():
    s   = db.get_stats()
    ts  = db.get_today_stats()
    dts = db.get_today_deposit_stats()
    ats = db.get_all_time_deposit_stats()
    now = datetime.now().strftime("%d %b %Y")
    text = (
        f"⚙️ <b>ADMIN PANEL — {bot_name()}</b>\n{sep()}\n\n"
        f"📅 <b>Today ({now})</b>\n"
        f"  🛒 Orders: <b>{ts['ord_count']}</b>  →  {fmt(ts['ord_amount'])}\n"
        f"  ✅ Deposits OK: <b>{dts['success_count']}</b>  →  {fmt(dts['success_amt'])}\n"
        f"  ❌ Failed/Expired: <b>{dts['failed_count']}</b>\n"
        f"  ⏳ Pending: <b>{dts['pending_count']}</b>\n\n"
        f"📈 <b>All Time</b>\n"
        f"  👥 Users: <b>{s['users']}</b>\n"
        f"  📦 Orders: <b>{s['orders']}</b>  →  {fmt(s['revenue'])}\n"
        f"  ✅ Deposits OK: <b>{ats['success_count']}</b>  →  {fmt(ats['success_amt'])}\n"
        f"  ❌ Failed/Expired: <b>{ats['failed_count']}</b>"
    )
    btns = [
        [sb("Today Orders",    "adm_today_orders", style="primary"),
         sb("Today Deposits",  "adm_today_deps",   style="primary")],
        [sb("All Orders TXT",  "adm_dl_all_orders", style="primary"),
         sb("All Deposits TXT","adm_dl_all_deps",   style="primary")],
        [sb("Categories",      "adm_cats", style="primary"),
         sb("Products",        "adm_prds", style="primary")],
        [sb("Add Stock",       "adm_stock_menu",      style="success"),
         sb("View Stock",      "adm_view_stock_menu", style="primary")],
        [sb("Users",           "adm_users",      style="primary"),
         sb("User History",    "adm_user_hist",  style="primary")],
        [sb("All Tickets",     "adm_all_tickets", style="primary"),
         sb("Coupons",         "adm_coupons",     style="primary")],
        [sb("Gift Balance",    "gift_start",      style="success"),
         sb("Manual Deposit",  "adm_manual_dep",  style="success")],
        [sb("Broadcast",       "adm_broadcast",    style="primary"),
         sb("Admins",          "adm_view_admins",  style="primary")],
        [sb("Free Items",      "adm_free_menu", style="success")],
        [sb("Daily History",   "adm_daily_menu", style="primary")],
        [sb("Settings",        "adm_settings",  style="primary")],
        [sb("Home",            "home",          style="danger")],
    ]
    return text, btns

async def admin_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): await q.answer("⛔ Access denied.", show_alert=True); return
    await q.answer()
    text, btns = _admin_panel_content()
    await se(q, text, btns)

# ── Settings ──────────────────────────────────────────────────────────────────

async def adm_settings_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    maint   = db.get_setting("maintenance") == "1"
    ref_on  = db.get_setting("referral_on") == "1"
    bn      = bot_name()
    em      = bot_emoji()
    trc20   = usdt_trc20()
    bep20   = usdt_bep20()
    pay_id  = binance_pay_id()
    t20_d   = (trc20[:20]+"…") if len(trc20)>22 else trc20 or "❌ Not set"
    b20_d   = (bep20[:20]+"…") if len(bep20)>22 else bep20 or "❌ Not set"
    pay_d   = pay_id or "❌ Not set"
    min_dep = db.get_setting_float("min_deposit_usdt", config.MIN_DEPOSIT_USDT)
    low_stk = db.get_setting_int("low_stock_threshold", config.LOW_STOCK_THRESHOLD)
    log_cid = db.get_setting("log_channel_id","") or str(getattr(config,"LOG_CHANNEL_ID","")) or "❌ Not set"
    dep_log_cid = db.get_setting("deposit_log_channel_id","") or "❌ Not set (using general log channel)"
    channels = get_force_channels()
    ch_list  = "\n".join([f"  📢 {h}" for h,_ in channels]) if channels else "  None"
    text = (
        f"⚙️ <b>SETTINGS</b>\n{sep()}\n\n"
        f"🤖 Bot Name: <b>{bn}</b>\n"
        f"✨ Bot Emoji: <b>{em}</b>\n\n"
        f"🟡 TRC20 Address: <code>{t20_d}</code>\n"
        f"🟠 BEP20 Address: <code>{b20_d}</code>\n\n"
        f"📢 Force-Join Channels:\n{ch_list}\n\n"
        f"📣 Log Channel ID: <code>{log_cid}</code>\n"
        f"💰 Deposit Log Group: <code>{dep_log_cid}</code>\n\n"
        f"💵 Min Deposit: <b>${min_dep:.2f}</b>\n"
        f"⚠️ Low Stock Alert: <b>{low_stk}</b>\n\n"
        f"🔧 Maintenance: {'🔴 ON' if maint else '🟢 OFF'}\n"
        f"👥 Referral: {'🟢 ON' if ref_on else '🔴 OFF'}"
    )
    btns = [
        [sb("Bot Name",         "adm_set_botname",  style="primary"),
         sb("Bot Emoji",        "adm_set_botemoji", style="primary")],
        [sb("TRC20 Address",    "adm_set_trc20",    style="primary"),
         sb("TRC20 QR",         "adm_set_trc20_qr", style="primary")],
        [sb("BEP20 Address",    "adm_set_bep20",    style="primary"),
         sb("BEP20 QR",         "adm_set_bep20_qr", style="primary")],
        [sb("Add Join Channel", "adm_add_channel",  style="success"),
         sb("Remove Channel",   "adm_rem_channel",  style="danger")],
        [sb("Set Log Channel",  "adm_set_logch",     style="primary"),
         sb("Set Deposit Log",  "adm_set_deplogch",  style="primary")],
        [sb(f"Min Deposit (${min_dep:.2f})", "adm_set_min_dep", style="primary")],
        [sb(f"Low Stock ({low_stk})",        "adm_set_low_stock", style="primary")],
        [sb("Maintenance", "adm_tog_maintenance",  style="danger" if maint else "success"),
         sb("Referral",    "adm_tog_referral_on",  style="success" if ref_on else "danger")],
        [sb("« Admin", "admin", style="danger")],
    ]
    await se(q, text, btns)

async def adm_toggle_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    key = q.data.replace("adm_tog_", "")
    db.set_setting(key, "0" if db.get_setting(key) == "1" else "1")
    await adm_settings_cb(update, ctx)

async def _simple_start(update, ctx, text, state):
    q = update.callback_query
    if not is_admin(q.from_user.id): return ConversationHandler.END
    await q.answer()
    await q.edit_message_text(text, parse_mode="HTML")
    return state

async def adm_set_botname_start(u, c): return await _simple_start(u, c, f"✏️ <b>Change Bot Name</b>\n\nCurrent: <b>{bot_name()}</b>\n\nSend new name:\n(/cancel to abort)", ADM_BOT_NAME)
async def adm_set_botemoji_start(u, c): return await _simple_start(u, c, f"✨ <b>Change Bot Emoji</b>\n\nCurrent: {bot_emoji()}\n\nSend new emoji:\n(/cancel to abort)", ADM_BOT_EMOJI)
async def adm_set_trc20_start(u, c): return await _simple_start(u, c, f"🟡 <b>Set TRC20 (TRON) Address</b>\n\nCurrent: <code>{usdt_trc20() or 'Not set'}</code>\n\nSend new TRC20 USDT address:\n(/cancel to abort)", ADM_USDT_TRC20)
async def adm_set_bep20_start(u, c): return await _simple_start(u, c, f"🟠 <b>Set BEP20 (BSC) Address</b>\n\nCurrent: <code>{usdt_bep20() or 'Not set'}</code>\n\nSend new BEP20 USDT address:\n(/cancel to abort)", ADM_USDT_BEP20)
async def adm_set_payid_start(u, c): return await _simple_start(u, c, f"💛 <b>Set Binance Pay ID</b>\n\nCurrent: <code>{binance_pay_id() or 'Not set'}</code>\n\nSend your Binance Pay ID (numeric UID found in Binance → Profile → Pay).\nSend <code>CLEAR</code> to remove it.\n(/cancel to abort)", ADM_BINANCE_PAY_ID)
async def adm_set_logch_start(u, c): return await _simple_start(u, c, "📣 <b>Set Log Channel ID</b>\n\nSend the channel ID (e.g. -1001234567890).\nThe bot must be an admin in that channel!\n\n(/cancel to abort)", ADM_LOG_CH)
async def adm_set_deplogch_start(u, c): return await _simple_start(u, c,
    "💰 <b>Set Deposit Log Group</b>\n\n"
    "Only deposit events (credited deposits) will be posted here — separate "
    "from the general log channel above.\n\n"
    "Send the group/channel ID (e.g. -1001234567890). The bot must be an admin there!\n"
    "Send <code>CLEAR</code> to remove and fall back to the general log channel.\n\n"
    "(/cancel to abort)", ADM_DEP_LOG_CH)
async def adm_add_channel_start(u, c): return await _simple_start(u, c, "📢 <b>Add Force-Join Channel</b>\n\nSend channel username (e.g. @mychannel)\nMax 5 channels.\n(/cancel to abort)", ADM_ADD_CH)

async def adm_set_botname_save(u, c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    db.set_setting("bot_name", u.message.text.strip())
    await u.message.reply_html(f"✅ Bot name: <b>{bot_name()}</b>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Settings", callback_data="adm_settings")]])); return ConversationHandler.END

async def adm_set_botemoji_save(u, c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    db.set_setting("bot_emoji", u.message.text.strip())
    await u.message.reply_html(f"✅ Emoji: {bot_emoji()}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Settings", callback_data="adm_settings")]])); return ConversationHandler.END

async def adm_set_trc20_save(u, c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    addr = u.message.text.strip()
    db.set_setting("usdt_trc20", addr)
    await u.message.reply_html(f"✅ TRC20 Address:\n<code>{addr}</code>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Settings", callback_data="adm_settings")]])); return ConversationHandler.END

async def adm_set_bep20_save(u, c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    addr = u.message.text.strip()
    db.set_setting("usdt_bep20", addr)
    await u.message.reply_html(f"✅ BEP20 Address:\n<code>{addr}</code>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Settings", callback_data="adm_settings")]])); return ConversationHandler.END

async def adm_set_payid_save(u, c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    val = u.message.text.strip()
    if val.upper() == "CLEAR":
        db.set_setting("binance_pay_id", "")
        await u.message.reply_html("✅ Binance Pay ID cleared.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Settings", callback_data="adm_settings")]])); return ConversationHandler.END
    db.set_setting("binance_pay_id", val)
    await u.message.reply_html(
        f"✅ Binance Pay ID set to:\n<code>{val}</code>\n\n"
        f"Users can now deposit via <b>Binance Pay</b> to this ID.\n"
        f"Make sure your Binance API key has <b>Enable Reading</b> permission.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Settings", callback_data="adm_settings")]])); return ConversationHandler.END

async def adm_set_logch_save(u, c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    val = u.message.text.strip()
    db.set_setting("log_channel_id", val)
    await u.message.reply_html(f"✅ Log channel set to: <code>{val}</code>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Settings", callback_data="adm_settings")]])); return ConversationHandler.END

async def adm_set_deplogch_save(u, c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    val = u.message.text.strip()
    if val.upper() == "CLEAR":
        db.set_setting("deposit_log_channel_id", "")
        await u.message.reply_html("✅ Deposit log group cleared — falling back to the general log channel.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Settings", callback_data="adm_settings")]])); return ConversationHandler.END
    db.set_setting("deposit_log_channel_id", val)
    await u.message.reply_html(f"✅ Deposit log group set to: <code>{val}</code>\n\nOnly deposit events will be posted here.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Settings", callback_data="adm_settings")]])); return ConversationHandler.END

# ── QR URL start handlers ────────────────────────────────────────────────────
async def adm_set_trc20_qr_start(u, c):
    return await _simple_start(u, c,
        f"🖼 <b>Set TRC20 QR Code Image</b>\n\n"
        f"Current: {'✅ Set' if trc20_qr_url() else '❌ Not set'}\n\n"
        f"📷 <b>Send the QR photo directly</b> (just upload the image), "
        f"or send an image URL (https://...).\n"
        f"Send <code>CLEAR</code> to remove.\n(/cancel to abort)",
        ADM_TRC20_QR)

async def adm_set_bep20_qr_start(u, c):
    return await _simple_start(u, c,
        f"🖼 <b>Set BEP20 QR Code Image</b>\n\n"
        f"Current: {'✅ Set' if bep20_qr_url() else '❌ Not set'}\n\n"
        f"📷 <b>Send the QR photo directly</b> (just upload the image), "
        f"or send an image URL (https://...).\n"
        f"Send <code>CLEAR</code> to remove.\n(/cancel to abort)",
        ADM_BEP20_QR)

async def adm_set_pay_qr_start(u, c):
    return await _simple_start(u, c,
        f"🖼 <b>Set Binance Pay QR Code Image</b>\n\n"
        f"Current: {'✅ Set' if pay_qr_url() else '❌ Not set'}\n\n"
        f"📷 <b>Send the QR photo directly</b> (just upload the image), "
        f"or send an image URL (https://...).\n"
        f"Send <code>CLEAR</code> to remove.\n(/cancel to abort)",
        ADM_PAY_QR)

# ── QR save handlers — accept EITHER a photo upload OR a URL/CLEAR text ─────
async def _save_qr(u, c, setting_key: str, label: str):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    if u.message.photo:
        # Use Telegram's own file_id — works directly with send_photo, no hosting needed.
        val = u.message.photo[-1].file_id
        db.set_setting(setting_key, val)
        await u.message.reply_html(
            f"✅ {label} QR set from your uploaded photo.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Settings", callback_data="adm_settings")]]))
        return ConversationHandler.END
    val = (u.message.text or "").strip()
    if not val:
        await u.message.reply_html("⚠️ Please send a photo, an image URL, or CLEAR.\n(/cancel to abort)")
        return None  # stay in the same state
    db.set_setting(setting_key, "" if val.upper() == "CLEAR" else val)
    await u.message.reply_html(
        f"✅ {label} QR cleared." if val.upper() == "CLEAR" else f"✅ {label} QR set.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Settings", callback_data="adm_settings")]]))
    return ConversationHandler.END

async def adm_set_trc20_qr_save(u, c):
    r = await _save_qr(u, c, "trc20_qr_url", "TRC20")
    return r if r is not None else ADM_TRC20_QR

async def adm_set_bep20_qr_save(u, c):
    r = await _save_qr(u, c, "bep20_qr_url", "BEP20")
    return r if r is not None else ADM_BEP20_QR

async def adm_set_pay_qr_save(u, c):
    r = await _save_qr(u, c, "pay_qr_url", "Binance Pay")
    return r if r is not None else ADM_PAY_QR

async def adm_add_channel_save(u, c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    ch = u.message.text.strip()
    if ch == "-": ch = ""
    # Find first empty slot
    for n in range(1, 6):
        existing = db.get_setting(f"force_join_ch{n}", "")
        if not existing:
            db.set_setting(f"force_join_ch{n}", ch)
            c.user_data["adding_ch_slot"] = n
            await u.message.reply_html(
                f"✅ Channel {n}: <b>{ch or 'Disabled'}</b>\n\nNow send the join URL (e.g. https://t.me/mychannel):",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Settings", callback_data="adm_settings")]]))
            return ADM_ADD_CH_URL
    await u.message.reply_text("❌ Maximum 5 channels reached! Remove one first.")
    return ConversationHandler.END

async def adm_add_channel_url_save(u, c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    n = c.user_data.pop("adding_ch_slot", 1)
    db.set_setting(f"force_join_url{n}", u.message.text.strip())
    await u.message.reply_html("✅ Channel URL saved!",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Settings", callback_data="adm_settings")]])); return ConversationHandler.END

async def adm_rem_channel_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    channels = get_force_channels()
    if not channels:
        await q.answer("No channels configured!", show_alert=True); return
    btns = []
    for n in range(1, 6):
        h = db.get_setting(f"force_join_ch{n}", "")
        if h:
            btns.append([InlineKeyboardButton(f"🗑️ Remove {h}", callback_data=f"adm_remch_{n}")])
    btns.append([InlineKeyboardButton("« Back", callback_data="adm_settings")])
    await q.edit_message_text("📢 <b>Remove a Force-Join Channel</b>", reply_markup=InlineKeyboardMarkup(btns), parse_mode="HTML")

async def adm_remch_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    n = int(q.data.split("_")[2])
    db.set_setting(f"force_join_ch{n}", "")
    db.set_setting(f"force_join_url{n}", "")
    await q.answer(f"✅ Channel {n} removed.", show_alert=True)
    await adm_settings_cb(update, ctx)

async def adm_set_min_dep_start(u, c): return await _simple_start(u, c, f"💵 <b>Set Minimum Deposit</b>\n\nCurrent: <b>${db.get_setting_float('min_deposit_usdt', config.MIN_DEPOSIT_USDT):.2f}</b>\n\nSend new minimum (e.g. 0.1):\n(/cancel to abort)", ADM_MIN_DEP)

async def adm_set_min_dep_save(u, c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    try:
        v = float(u.message.text.strip())
        if v < 0: raise ValueError
    except ValueError:
        await u.message.reply_text("❌ Enter a positive number."); return ADM_MIN_DEP
    db.set_setting("min_deposit_usdt", str(v))
    await u.message.reply_html(f"✅ Min deposit set to <b>${v:.2f} USDT</b>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Settings", callback_data="adm_settings")]])); return ConversationHandler.END

async def adm_set_low_stock_start(u, c): return await _simple_start(u, c, f"⚠️ <b>Set Low Stock Threshold</b>\n\nCurrent: <b>{db.get_setting_int('low_stock_threshold', config.LOW_STOCK_THRESHOLD)}</b>\n\nSend new threshold:\n(/cancel to abort)", ADM_LOW_STOCK)

async def adm_set_low_stock_save(u, c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    try: v = int(u.message.text.strip())
    except ValueError:
        await u.message.reply_text("❌ Enter a number."); return ADM_LOW_STOCK
    db.set_setting("low_stock_threshold", str(v))
    await u.message.reply_html(f"✅ Low stock alert set to <b>{v}</b>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Settings", callback_data="adm_settings")]])); return ConversationHandler.END

# ── Categories ────────────────────────────────────────────────────────────────

async def adm_cats_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    cats = db.get_categories(active_only=False)
    btns = []
    for c in cats:
        st = "🟢" if c["is_active"] else "🔴"
        btns.append([
            InlineKeyboardButton(f"{st} {c['emoji']} {c['name']}", callback_data=f"adm_toggle_cat_{c['id']}"),
            InlineKeyboardButton("🗑️", callback_data=f"adm_del_cat_{c['id']}")
        ])
    btns.append([InlineKeyboardButton("➕ Add Category", callback_data="adm_add_cat")])
    btns.append([InlineKeyboardButton("« Admin", callback_data="admin")])
    await q.edit_message_text(f"📂 <b>CATEGORIES</b>\n{sep()}", reply_markup=InlineKeyboardMarkup(btns), parse_mode="HTML")

async def adm_toggle_cat_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    cid = int(q.data.split("_")[3])
    db.toggle_category(cid)
    await adm_cats_cb(update, ctx)

async def adm_del_cat_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    cid = int(q.data.split("_")[3])
    ok  = db.delete_category(cid, force=False)
    if not ok:
        await q.edit_message_text(
            f"⚠️ Category has products. Force delete?",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🗑️ Force Delete", callback_data=f"adm_delcat_force_{cid}"),
                 InlineKeyboardButton("« Cancel",        callback_data="adm_cats")]
            ]),
            parse_mode="HTML"
        )
    else:
        await adm_cats_cb(update, ctx)

async def adm_del_cat_force_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    cid = int(q.data.split("_")[3])
    db.delete_category(cid, force=True)
    await adm_cats_cb(update, ctx)

async def adm_add_cat_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    await q.edit_message_text("📂 <b>Add Category</b>\n\nSend category name:\n(/cancel to abort)", parse_mode="HTML")
    return ADM_CAT_NAME

async def adm_cat_name(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    ctx.user_data["cat_name"] = update.message.text.strip()
    await update.message.reply_text("Send an emoji for this category:")
    return ADM_CAT_EMOJI

async def adm_cat_emoji(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    db.add_category(ctx.user_data["cat_name"], update.message.text.strip())
    await update.message.reply_html(f"✅ Category <b>{ctx.user_data['cat_name']}</b> added!",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📂 Categories", callback_data="adm_cats")]])); return ConversationHandler.END

# ── Products ──────────────────────────────────────────────────────────────────

async def adm_prds_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    prods = db.get_products(active_only=False)
    btns  = [[InlineKeyboardButton(f"{p['emoji']} {p['name']}", callback_data="noop"),
               InlineKeyboardButton("🗑️", callback_data=f"adm_del_prd_{p['id']}")] for p in prods]
    btns.append([InlineKeyboardButton("➕ Add Product", callback_data="adm_add_prd")])
    btns.append([InlineKeyboardButton("« Admin", callback_data="admin")])
    await q.edit_message_text(f"📦 <b>PRODUCTS</b>\n{sep()}", reply_markup=InlineKeyboardMarkup(btns), parse_mode="HTML")

async def adm_del_prd_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    pid = int(q.data.split("_")[3])
    db.delete_product(pid)
    await adm_prds_cb(update, ctx)

async def adm_add_prd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    cats = db.get_categories(active_only=True)
    if not cats:
        await q.answer("Add a category first!", show_alert=True); return
    btns = [[InlineKeyboardButton(f"{c['emoji']} {c['name']}", callback_data=f"prd_cat_{c['id']}")] for c in cats]
    await q.edit_message_text("📦 <b>Add Product</b>\n\nSelect category:", reply_markup=InlineKeyboardMarkup(btns), parse_mode="HTML")
    return ADM_PRD_CAT

async def adm_prd_cat(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    ctx.user_data["prd_cat"] = int(q.data.split("_")[2])
    await q.edit_message_text("Product name:"); return ADM_PRD_NAME

async def adm_prd_name(u, c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    c.user_data["prd_name"] = u.message.text.strip()
    await u.message.reply_text("Emoji for this product:"); return ADM_PRD_EMOJI

async def adm_prd_emoji(u, c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    c.user_data["prd_emoji"] = u.message.text.strip()
    await u.message.reply_text("Description (or skip with /skip):"); return ADM_PRD_DESC

async def adm_prd_desc(u, c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    c.user_data["prd_desc"] = "" if u.message.text.strip() == "/skip" else u.message.text.strip()
    await u.message.reply_text("Price in USDT (e.g. 3.5):"); return ADM_PRD_PRICE

async def adm_prd_price(u, c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    try:
        c.user_data["prd_price"] = float(u.message.text.strip())
    except ValueError:
        await u.message.reply_text("❌ Enter a number like 3.5:"); return ADM_PRD_PRICE
    await u.message.reply_text("Duration (e.g. 1 Month, 3 Months, 1 Year):"); return ADM_PRD_DUR

async def adm_prd_dur(u, c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    c.user_data["prd_dur"] = u.message.text.strip()
    await u.message.reply_html(
        "🌟 <b>Premium Emoji ID</b> (optional)\n\n"
        "Paste a custom emoji ID to show it on this product's button, or send /skip.\n"
        "To get an ID: send yourself a premium emoji, forward that message to @getidsbot, "
        "and it will reply with the numeric ID."
    )
    return ADM_PRD_EMOJI_ID

async def adm_prd_emoji_id(u, c):
    if not is_admin(u.effective_user.id): return ConversationHandler.END
    d = c.user_data
    txt = u.message.text.strip()
    emoji_id = "" if txt == "/skip" else txt
    db.add_product(d["prd_cat"], d["prd_name"], d["prd_emoji"], d.get("prd_desc",""),
                    d["prd_price"], d["prd_dur"], emoji_id=emoji_id)
    await u.message.reply_html(f"✅ Product <b>{d['prd_name']}</b> added!",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Admin", callback_data="admin")]])); return ConversationHandler.END

# ── Stock ─────────────────────────────────────────────────────────────────────

async def adm_stock_menu_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    prods = db.get_products()
    if not prods: await q.answer("No products yet!", show_alert=True); return
    btns = [[InlineKeyboardButton(
        f"{p['emoji']} {p['name']} [{db.get_stock_count(p['id'])} left]",
        callback_data=f"adm_addstock_{p['id']}"
    )] for p in prods]
    btns.append([InlineKeyboardButton("« Admin", callback_data="admin")])
    await q.edit_message_text("📥 <b>Add Stock</b>\n\nSelect product:",
        reply_markup=InlineKeyboardMarkup(btns), parse_mode="HTML")

async def adm_addstock_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q   = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    pid = int(q.data.split("_")[2])
    p   = db.get_product(pid)
    ctx.user_data["stock_pid"] = pid
    await q.edit_message_text(
        f"📥 <b>Add Stock — {p['emoji']} {p['name']}</b>\n\n"
        f"Send credentials, one per line:\n"
        f"<code>email@gmail.com:password\nemail2:pass2</code>\n\n"
        f"OR activation links, one per line.\n\n/cancel to abort.", parse_mode="HTML")
    return ADM_STOCK_DATA

async def adm_stock_data(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    pid   = ctx.user_data.get("stock_pid")
    items = [l for l in update.message.text.strip().splitlines() if l.strip()]
    result = db.add_stock(pid, items)
    p = db.get_product(pid)
    dup_note = ""
    if result["duplicates"]:
        dup_note = f"\n\n⚠️ Skipped <b>{result['duplicates']}</b> duplicate(s)."
    total_stock = db.get_stock_count(pid)
    await update.message.reply_html(
        f"✅ Added <b>{result['added']}</b> new item(s) to <b>{p['emoji']} {p['name']}</b>{dup_note}\n"
        f"📦 Total stock: <b>{total_stock}</b>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Admin", callback_data="admin")]]))
    # Log stock addition to channel
    if result["added"] > 0:
        await log_ch(update.message.bot,
            f"📥 <b>Stock Added</b>\n{sep()}\n"
            f"📦 {p['emoji']} {p['name']}\n"
            f"✅ Added: <b>{result['added']}</b> items\n"
            f"📊 Total now: <b>{total_stock}</b>")
    return ConversationHandler.END

# ── View / Remove Stock ───────────────────────────────────────────────────────

async def adm_view_stock_menu_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    prods = db.get_products()
    if not prods: await q.answer("No products!", show_alert=True); return
    btns = [[InlineKeyboardButton(
        f"{p['emoji']} {p['name']} [{db.get_stock_count(p['id'])}]",
        callback_data=f"adm_viewstock_{p['id']}"
    )] for p in prods]
    btns.append([InlineKeyboardButton("« Admin", callback_data="admin")])
    await q.edit_message_text("👁 <b>View Stock</b>\n\nSelect product:",
        reply_markup=InlineKeyboardMarkup(btns), parse_mode="HTML")

async def adm_viewstock_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    pid   = int(q.data.split("_")[2])
    p     = db.get_product(pid)
    items = db.get_stock_items(pid, include_sold=False, limit=20)
    lines = [f"👁 <b>{p['emoji']} {p['name']}</b>\n{sep()}\n📦 Stock: {len(items)}\n"]
    for i in items:
        lines.append(f"• <code>{i['data'][:50]}</code>")
    btns = [
        [InlineKeyboardButton("🗑️ Remove All", callback_data=f"adm_clearstock_{pid}")],
        [InlineKeyboardButton("« Back", callback_data="adm_view_stock_menu")],
    ]
    await q.edit_message_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(btns), parse_mode="HTML")

async def adm_remstock_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    iid = int(q.data.split("_")[2])
    db.remove_stock_item(iid)
    await q.answer("✅ Item removed.", show_alert=True)

async def adm_clearstock_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    pid = int(q.data.split("_")[2])
    db.clear_stock(pid)
    await q.answer("✅ All stock cleared.", show_alert=True)
    await adm_view_stock_menu_cb(update, ctx)

# ── Users ─────────────────────────────────────────────────────────────────────

async def adm_users_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    users = db.get_all_users()
    total = len(users)
    banned = sum(1 for u in users if u.get("is_banned"))
    await q.edit_message_text(
        f"👥 <b>USERS</b>\n{sep()}\n\n"
        f"Total: <b>{total}</b>\n"
        f"Banned: <b>{banned}</b>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔍 Search User", callback_data="adm_search_user")],
            [InlineKeyboardButton("💰 Add Balance", callback_data="adm_addbal")],
            [InlineKeyboardButton("💸 Remove Balance", callback_data="adm_rembal")],
            [InlineKeyboardButton("« Admin", callback_data="admin")],
        ]),
        parse_mode="HTML"
    )

async def adm_search_user_start_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    await q.edit_message_text("🔍 Send user ID, username, or name to search:")
    return ADM_SEARCH_USER

async def adm_search_user_result(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    query = update.message.text.strip()
    users = db.search_users(query)
    if not users:
        await update.message.reply_text("❌ No users found."); return ConversationHandler.END
    lines = [f"🔍 <b>Search: {query}</b>\n{sep()}\n"]
    btns  = []
    for u in users[:10]:
        name = u.get("full_name") or u.get("username") or str(u["user_id"])
        uname = f"@{u['username']}" if u.get("username") else ""
        status = "🚫" if u.get("is_banned") else ("🌟" if u.get("is_vip") else "👤")
        lines.append(f"{status} <code>{u['user_id']}</code> {name} {uname} — {fmt(u['balance'])}")
        btns.append([
            InlineKeyboardButton(f"{'Unban' if u.get('is_banned') else 'Ban'} {name[:15]}", callback_data=f"adm_ban_{u['user_id']}"),
        ])
    await update.message.reply_html("\n".join(lines), reply_markup=InlineKeyboardMarkup(btns + [[InlineKeyboardButton("« Admin", callback_data="admin")]]))
    return ConversationHandler.END

async def adm_ban_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    uid = int(q.data.split("_")[2])
    u   = db.get_user(uid)
    if not u: return
    new_ban = not bool(u.get("is_banned"))
    db.ban_user(uid, new_ban)
    await q.answer(f"{'🚫 Banned' if new_ban else '✅ Unbanned'} user {uid}", show_alert=True)

# ── Balance Management ────────────────────────────────────────────────────────

async def adm_addbal_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    return await _simple_start(update, ctx, "💰 <b>Add Balance</b>\n\nSend user ID:\n(/cancel to abort)", ADM_ADDBAL_UID)

async def adm_addbal_uid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    try: uid = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid ID."); return ADM_ADDBAL_UID
    u = db.get_user(uid)
    if not u:
        await update.message.reply_text("❌ User not found."); return ADM_ADDBAL_UID
    ctx.user_data["addbal_uid"] = uid
    name = u.get("full_name") or u.get("username") or str(uid)
    await update.message.reply_text(f"User: <b>{name}</b>\nBalance: <b>{fmt(u['balance'])}</b>\n\nAmount to add:", parse_mode="HTML")
    return ADM_ADDBAL_AMT

async def adm_addbal_amt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    try: amt = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Enter a number."); return ADM_ADDBAL_AMT
    uid = ctx.user_data.pop("addbal_uid")
    db.update_balance(uid, amt)
    u2 = db.get_user(uid)
    await update.message.reply_html(
        f"✅ Added <b>{fmt(amt)}</b> to user <code>{uid}</code>\nNew balance: <b>{fmt(u2['balance'])}</b>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👥 Users", callback_data="adm_users")]]))
    await log_ch(update.message.bot, f"💰 <b>Admin Added Balance</b>\n👤 <code>{uid}</code> +{fmt(amt)}")
    try:
        lang_u = db.get_user_language(uid)
        await update.message.bot.send_message(uid, f"💰 <b>Balance Added!</b>\n\n+{fmt(amt)} credited by admin.\nNew balance: {fmt(u2['balance'])}", parse_mode="HTML")
    except Exception: pass
    return ConversationHandler.END

async def adm_rembal_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    return await _simple_start(update, ctx, "💸 <b>Remove Balance</b>\n\nSend user ID:\n(/cancel to abort)", ADM_REMBAL_UID)

async def adm_rembal_uid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    try: uid = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid ID."); return ADM_REMBAL_UID
    u = db.get_user(uid)
    if not u:
        await update.message.reply_text("❌ User not found."); return ADM_REMBAL_UID
    ctx.user_data["rembal_uid"] = uid
    name = u.get("full_name") or u.get("username") or str(uid)
    await update.message.reply_text(f"User: <b>{name}</b>\nBalance: <b>{fmt(u['balance'])}</b>\n\nAmount to remove:", parse_mode="HTML")
    return ADM_REMBAL_AMT

async def adm_rembal_amt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    try: amt = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Enter a number."); return ADM_REMBAL_AMT
    uid = ctx.user_data.pop("rembal_uid")
    db.update_balance(uid, -amt)
    u2 = db.get_user(uid)
    await update.message.reply_html(
        f"✅ Removed <b>{fmt(amt)}</b> from user <code>{uid}</code>\nNew balance: <b>{fmt(u2['balance'])}</b>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👥 Users", callback_data="adm_users")]]))
    return ConversationHandler.END

# ── Admins ────────────────────────────────────────────────────────────────────

async def adm_view_admins_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    extras = db.get_extra_admins()
    lines  = [f"👮 <b>ADMINS</b>\n{sep()}\n"]
    for aid in config.ADMIN_IDS:
        lines.append(f"🔑 <code>{aid}</code> (owner)")
    for aid in extras:
        lines.append(f"👮 <code>{aid}</code>")
    await q.edit_message_text("\n".join(lines),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Admin",    callback_data="adm_add_admin")],
            [InlineKeyboardButton("➖ Remove Admin", callback_data="adm_rem_admin_start")],
            [InlineKeyboardButton("« Admin",         callback_data="admin")],
        ]),
        parse_mode="HTML")

async def adm_add_admin_start_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    await q.edit_message_text("➕ <b>Add Admin</b>\n\nSend user ID to promote:\n(/cancel to abort)", parse_mode="HTML")
    return ADM_ADD_ADMIN

async def adm_add_admin_receive(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    try: uid = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid ID."); return ADM_ADD_ADMIN
    db.add_extra_admin(uid)
    await update.message.reply_html(f"✅ User <code>{uid}</code> is now an admin.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("👮 Admins", callback_data="adm_view_admins")]]))
    return ConversationHandler.END

async def adm_rem_admin_start_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    extras = db.get_extra_admins()
    if not extras:
        await q.answer("No extra admins to remove!", show_alert=True); return
    btns = [[InlineKeyboardButton(f"❌ Remove {aid}", callback_data=f"adm_do_rem_admin_{aid}")] for aid in extras]
    btns.append([InlineKeyboardButton("« Back", callback_data="adm_view_admins")])
    await q.edit_message_text("➖ <b>Remove Admin</b>", reply_markup=InlineKeyboardMarkup(btns), parse_mode="HTML")

async def adm_do_rem_admin_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    uid = int(q.data.split("_")[4])
    db.remove_extra_admin(uid)
    await q.answer(f"✅ Admin {uid} removed.", show_alert=True)
    await adm_view_admins_cb(update, ctx)

# ── Support Tickets (admin view) ──────────────────────────────────────────────

async def adm_tickets_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    tickets = db.get_open_tickets()
    if not tickets:
        await q.edit_message_text("🎧 No open tickets.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Admin", callback_data="admin")]]),
            parse_mode="HTML"); return
    btns = []
    for t in tickets:
        name = t.get("full_name") or t.get("username") or str(t["user_id"])
        btns.append([InlineKeyboardButton(f"#{t['id']:04d} {name[:20]} — {t['subject'][:20]}", callback_data=f"adm_ticket_{t['id']}")])
    btns.append([InlineKeyboardButton("« Admin", callback_data="admin")])
    await q.edit_message_text(f"🎧 <b>Open Tickets ({len(tickets)})</b>",
        reply_markup=InlineKeyboardMarkup(btns), parse_mode="HTML")

# ─────────────────────────────────────────────────────────────────────────────
#  ADMIN — BROADCAST
# ─────────────────────────────────────────────────────────────────────────────

async def adm_broadcast_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    await q.edit_message_text(
        "📢 <b>BROADCAST MESSAGE</b>\n\n"
        "Send the message you want to send to <b>ALL users</b>.\n"
        "HTML formatting supported (<code>bold</code>, <i>italic</i>, etc.)\n\n"
        "/cancel to abort",
        parse_mode="HTML"
    )
    return ADM_REAL_BROADCAST

async def adm_broadcast_do(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    raw   = update.message.text_html if update.message.text_html else (update.message.text or "")
    users = db.get_all_users()
    total = len(users)
    sent  = failed = 0
    prog  = await update.message.reply_html(f"📢 <b>Broadcasting…</b>\n⏳ 0 / {total}")
    for i, u in enumerate(users):
        try:
            await ctx.bot.send_message(u["user_id"],
                f"📢 <b>Announcement from {bot_name()}</b>\n{sep()}\n\n{raw}",
                parse_mode="HTML")
            sent += 1
        except Exception:
            failed += 1
        if (i + 1) % 30 == 0:
            try:
                await prog.edit_text(f"📢 <b>Broadcasting…</b>\n⏳ {i+1} / {total}")
            except Exception:
                pass
        await asyncio.sleep(0.04)  # ~25 msgs/sec — safe rate
    await prog.edit_text(
        f"✅ <b>Broadcast Complete!</b>\n\n"
        f"📨 Sent:   <b>{sent}</b>\n"
        f"❌ Failed: <b>{failed}</b>\n"
        f"👥 Total:  <b>{total}</b>",
        parse_mode="HTML"
    )
    return ConversationHandler.END

# ─────────────────────────────────────────────────────────────────────────────
#  ADMIN — TODAY ORDERS (detailed)
# ─────────────────────────────────────────────────────────────────────────────

async def adm_today_orders_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    orders    = db.get_today_orders(limit=10000)
    total_amt = sum(o["amount_usdt"] for o in orders)
    today_str = datetime.now().strftime("%d %b %Y")
    if not orders:
        await q.edit_message_text(
            f"📊 <b>Today's Orders — {today_str}</b>\n\nNo orders today.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Admin", callback_data="admin")]]),
            parse_mode="HTML"); return
    lines = [
        f"📊 <b>TODAY'S ORDERS — {today_str}</b>\n{sep()}",
        f"Total: <b>{len(orders)}</b> orders  →  <b>{fmt(total_amt)}</b>\n{sep()}\n"
    ]
    for o in orders[:25]:
        uname = f"@{o['username']}" if o.get("username") else f"ID:{o['user_id']}"
        dt    = (o["created_at"] or "")[:16].replace("T", " ")
        tag   = "♻️" if o.get("refunded") else "✅"
        cred  = o.get("cred_data") or "—"
        lines.append(f"{tag} <code>{o['user_id']}</code> {uname}\n"
                     f"   📦 {o['product_name'][:25]}  💵 {fmt(o['amount_usdt'])}\n"
                     f"   🔑 <code>{cred}</code>\n"
                     f"   🕐 {dt}  #ord{o['id']:04d}")
    if len(orders) > 25:
        lines.append(f"\n<i>…{len(orders)-25} more — download full list below</i>")
    await q.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 Download Full TXT", callback_data="adm_dl_today_orders")],
            [InlineKeyboardButton("« Admin",              callback_data="admin")],
        ]),
        parse_mode="HTML"
    )

async def adm_dl_today_orders_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer("⏳ Generating…")
    orders    = db.get_today_orders(limit=100000)
    total_amt = sum(o["amount_usdt"] for o in orders)
    today_str = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"BASS TG STORE — TODAY'S ORDERS ({today_str})",
        f"Total: {len(orders)} orders  |  Revenue: ${total_amt:.4f} USDT",
        "=" * 70,
        f"{'#':>6}  {'Date/Time':<17}  {'UserID':<12}  {'Username':<18}  {'Product':<25}  {'Amount':>10}  {'Status':<10}  Credential",
        "-" * 100
    ]
    for o in orders:
        uname = f"@{o.get('username','')}" if o.get("username") else "-"
        dt    = (o["created_at"] or "")[:16].replace("T", " ")
        st    = "REFUNDED" if o.get("refunded") else "OK"
        cred  = o.get("cred_data") or "-"
        lines.append(f"{o['id']:>6}  {dt:<17}  {o['user_id']:<12}  {uname:<18}  {o['product_name'][:25]:<25}  ${o['amount_usdt']:>9.4f}  {st:<10}  {cred}")
    content = "\n".join(lines).encode("utf-8")
    await ctx.bot.send_document(
        q.from_user.id,
        document=io.BytesIO(content),
        filename=f"orders_today_{today_str}.txt",
        caption=f"📊 Today's Orders — {today_str}\n{len(orders)} orders | {fmt(total_amt)}"
    )

# ─────────────────────────────────────────────────────────────────────────────
#  ADMIN — TODAY DEPOSITS (success + failed both)
# ─────────────────────────────────────────────────────────────────────────────

async def adm_today_deps_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    deps      = db.get_today_deposits_all()
    stats     = db.get_today_deposit_stats()
    today_str = datetime.now().strftime("%d %b %Y")
    icons     = {"completed":"✅","pending":"⏳","expired":"❌","cancelled":"🚫"}
    if not deps:
        await q.edit_message_text(
            f"💰 <b>Today's Deposits — {today_str}</b>\n\nNo deposits today.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Admin", callback_data="admin")]]),
            parse_mode="HTML"); return
    lines = [
        f"💰 <b>TODAY'S DEPOSITS — {today_str}</b>\n{sep()}",
        f"✅ Success: <b>{stats['success_count']}</b>  →  <b>{fmt(stats['success_amt'])}</b>",
        f"❌ Failed/Expired: <b>{stats['failed_count']}</b>",
        f"⏳ Pending: <b>{stats['pending_count']}</b>\n{sep()}\n"
    ]
    for d in deps[:30]:
        uname = f"@{d['username']}" if d.get("username") else f"ID:{d['user_id']}"
        dt    = (d["created_at"] or "")[:16].replace("T", " ")
        net   = d.get("network","TRC20")
        ic    = icons.get(d["status"],"•")
        lines.append(f"{ic} <code>{d['user_id']}</code> {uname}\n"
                     f"   💵 {fmt(d['requested_usdt'])} [{net}]  🕐 {dt}  #{d['id']:04d}")
    if len(deps) > 30:
        lines.append(f"\n<i>…{len(deps)-30} more — download for full list</i>")
    await q.edit_message_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📥 Download Full TXT", callback_data="adm_dl_today_deps")],
            [InlineKeyboardButton("« Admin",              callback_data="admin")],
        ]),
        parse_mode="HTML"
    )

async def adm_dl_today_deps_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer("⏳ Generating…")
    deps      = db.get_today_deposits_all()
    today_str = datetime.now().strftime("%Y-%m-%d")
    stats     = db.get_today_deposit_stats()
    lines = [
        f"BASS TG STORE — TODAY'S DEPOSITS ({today_str})",
        f"Success: {stats['success_count']} (${stats['success_amt']:.4f})  |  Failed: {stats['failed_count']}  |  Pending: {stats['pending_count']}",
        "=" * 80,
        f"{'#':>6}  {'Date/Time':<17}  {'UserID':<12}  {'Username':<18}  {'Amount':>10}  {'Network':<8}  Status",
        "-" * 90
    ]
    for d in deps:
        uname = f"@{d.get('username','')}" if d.get("username") else "-"
        dt    = (d["created_at"] or "")[:16].replace("T", " ")
        net   = d.get("network","TRC20")
        lines.append(f"{d['id']:>6}  {dt:<17}  {d['user_id']:<12}  {uname:<18}  ${d['requested_usdt']:>9.4f}  {net:<8}  {d['status'].upper()}")
    content = "\n".join(lines).encode("utf-8")
    await ctx.bot.send_document(
        q.from_user.id,
        document=io.BytesIO(content),
        filename=f"deposits_today_{today_str}.txt",
        caption=f"💰 Today's Deposits — {today_str}\n✅ {stats['success_count']} success | ❌ {stats['failed_count']} failed"
    )

# ─────────────────────────────────────────────────────────────────────────────
#  ADMIN — ALL TIME DOWNLOADS (TXT)
# ─────────────────────────────────────────────────────────────────────────────

async def adm_dl_all_orders_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer("⏳ Generating full report…")
    orders    = db.get_all_orders()
    total_amt = sum(o["amount_usdt"] for o in orders)
    date_str  = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"BASS TG STORE — ALL TIME ORDERS (as of {date_str})",
        f"Total: {len(orders)} orders  |  Revenue: ${total_amt:.4f} USDT",
        "=" * 100,
        f"{'#':>6}  {'Date/Time':<17}  {'UserID':<12}  {'Username':<18}  {'Product':<28}  {'Amount':>10}  {'Status':<10}  Credential",
        "-" * 100
    ]
    for o in orders:
        uname = f"@{o.get('username','')}" if o.get("username") else "-"
        dt    = (o["created_at"] or "")[:16].replace("T", " ")
        st    = "REFUNDED" if o.get("refunded") else "OK"
        cred  = o.get("cred_data") or "-"
        lines.append(f"{o['id']:>6}  {dt:<17}  {o['user_id']:<12}  {uname:<18}  {o['product_name'][:28]:<28}  ${o['amount_usdt']:>9.4f}  {st:<10}  {cred}")
    content = "\n".join(lines).encode("utf-8")
    await ctx.bot.send_document(
        q.from_user.id,
        document=io.BytesIO(content),
        filename=f"all_orders_{date_str}.txt",
        caption=f"📈 All Time Orders\n{len(orders)} orders | {fmt(total_amt)} revenue"
    )

async def adm_dl_all_deps_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer("⏳ Generating full report…")
    deps     = db.get_all_deposits_list()
    stats    = db.get_all_time_deposit_stats()
    date_str = datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"BASS TG STORE — ALL TIME DEPOSITS (as of {date_str})",
        f"Success: {stats['success_count']} (${stats['success_amt']:.4f})  |  Failed: {stats['failed_count']}  |  Pending: {stats['pending_count']}",
        "=" * 95,
        f"{'#':>6}  {'Date/Time':<17}  {'UserID':<12}  {'Username':<18}  {'Amount':>10}  {'Network':<8}  {'TxID':<35}  Status",
        "-" * 110
    ]
    for d in deps:
        uname = f"@{d.get('username','')}" if d.get("username") else "-"
        dt    = (d["created_at"] or "")[:16].replace("T", " ")
        net   = d.get("network", "TRC20")
        txid  = (d.get("binance_txid") or "-")[:35]
        lines.append(f"{d['id']:>6}  {dt:<17}  {d['user_id']:<12}  {uname:<18}  ${d['requested_usdt']:>9.4f}  {net:<8}  {txid:<35}  {d['status'].upper()}")
    content = "\n".join(lines).encode("utf-8")
    await ctx.bot.send_document(
        q.from_user.id,
        document=io.BytesIO(content),
        filename=f"all_deposits_{date_str}.txt",
        caption=f"📋 All Time Deposits\n✅ {stats['success_count']} success (${stats['success_amt']:.2f}) | ❌ {stats['failed_count']} failed"
    )

# ─────────────────────────────────────────────────────────────────────────────
#  ADMIN — USER HISTORY (search + download)
# ─────────────────────────────────────────────────────────────────────────────

async def adm_user_hist_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    await q.edit_message_text(
        "🔍 <b>User History</b>\n\nSend user ID, @username, or name to look up:\n\n/cancel to abort",
        parse_mode="HTML"
    )
    return ADM_USER_HIST

async def adm_user_hist_search(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    query = update.message.text.strip()
    users = db.search_users(query)
    if not users:
        await update.message.reply_html("❌ No users found.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Admin", callback_data="admin")]]))
        return ConversationHandler.END
    if len(users) == 1:
        await _send_user_history(update, ctx, users[0]["user_id"])
        return ConversationHandler.END
    btns = []
    for u in users[:10]:
        name  = u.get("full_name") or u.get("username") or str(u["user_id"])
        uname = f"@{u['username']}" if u.get("username") else f"ID:{u['user_id']}"
        btns.append([InlineKeyboardButton(f"{name[:20]} ({uname})", callback_data=f"adm_uhist_{u['user_id']}")])
    btns.append([InlineKeyboardButton("« Admin", callback_data="admin")])
    await update.message.reply_html("🔍 Multiple users found — select one:",
        reply_markup=InlineKeyboardMarkup(btns))
    return ConversationHandler.END

async def _send_user_history(update_or_q, ctx, uid: int):
    """Send full history for a user (deposits + orders) with download option."""
    u = db.get_user(uid)
    if not u:
        msg = "❌ User not found."
        if hasattr(update_or_q, "message"):
            await update_or_q.message.reply_text(msg)
        return
    name, uname_str = user_display(u)
    hist   = db.get_user_full_history(uid)
    deps   = [h for h in hist if h["type"] == "deposit"]
    ords   = [h for h in hist if h["type"] == "order"]
    ok_dep = sum(d["amount"] for d in deps if d["status"] == "completed")
    rev    = sum(o["amount"] for o in ords if not o.get("status","") == "refunded")
    lines  = [
        f"👤 <b>User History</b>\n{sep()}",
        f"🆔 ID: <code>{uid}</code>",
        f"👤 {name}  {uname_str if uname_str else ''}",
        f"💼 Balance: <b>{fmt(u['balance'])}</b>",
        f"💰 Total Deposited: <b>{fmt(ok_dep)}</b>",
        f"🛒 Total Spent: <b>{fmt(rev)}</b>",
        f"📦 Orders: <b>{len(ords)}</b>\n{sep()}\n"
    ]
    icons_dep  = {"completed":"💚","pending":"⏳","expired":"🔴","cancelled":"🚫"}
    for h in hist[:30]:
        dt = (h.get("created_at","") or "")[:16].replace("T"," ")
        if h["type"] == "deposit":
            ic = icons_dep.get(h["status"],"•")
            lines.append(f"{ic} DEPOSIT  {fmt(h['amount'])} [{h.get('extra','?')}]\n   🕐 {dt}")
        else:
            tag = "♻️" if h.get("status","") == "refunded" else "🛒"
            lines.append(f"{tag} ORDER  {h['product_name'][:22]}  {fmt(h['amount'])}\n   🕐 {dt}")
    if len(hist) > 30:
        lines.append(f"\n<i>…{len(hist)-30} older entries — download for full history</i>")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📥 Download Full History TXT", callback_data=f"adm_dl_uhist_{uid}")],
        [InlineKeyboardButton("💰 Add Balance",  callback_data="adm_addbal"),
         InlineKeyboardButton("🚫 Ban/Unban",   callback_data=f"adm_ban_{uid}")],
        [InlineKeyboardButton("« Admin", callback_data="admin")],
    ])
    if hasattr(update_or_q, "message"):
        await update_or_q.message.reply_html("\n".join(lines), reply_markup=kb)
    else:
        await update_or_q.edit_message_text("\n".join(lines), reply_markup=kb, parse_mode="HTML")

async def adm_uhist_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    uid = int(q.data.split("_")[2])
    await _send_user_history(q, ctx, uid)

async def adm_dl_uhist_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer("⏳ Generating…")
    uid  = int(q.data.split("_")[3])
    u    = db.get_user(uid)
    hist = db.get_user_full_history(uid)
    date_str = datetime.now().strftime("%Y-%m-%d")
    name = (u.get("full_name") or u.get("username") or str(uid)) if u else str(uid)
    uname = f"@{u['username']}" if u and u.get("username") else "-"
    lines = [
        f"BASS TG STORE — USER HISTORY",
        f"User: {name}  ({uname})  ID: {uid}",
        f"Balance: ${u['balance']:.4f} USDT" if u else "",
        f"Generated: {date_str}",
        "=" * 80,
        f"{'Type':<10}  {'Date/Time':<17}  {'Amount':>10}  {'Extra':<12}  Details",
        "-" * 80
    ]
    for h in hist:
        dt    = (h.get("created_at","") or "")[:16].replace("T"," ")
        extra = h.get("extra","") or h.get("status","")
        det   = h.get("product_name","") or h.get("status","")
        lines.append(f"{h['type'].upper():<10}  {dt:<17}  ${h['amount']:>9.4f}  {extra[:12]:<12}  {det[:35]}")
    content = "\n".join(lines).encode("utf-8")
    await ctx.bot.send_document(
        q.from_user.id,
        document=io.BytesIO(content),
        filename=f"user_{uid}_history_{date_str}.txt",
        caption=f"👤 Full history for {name} (ID: {uid})\n{len(hist)} entries"
    )

# ─────────────────────────────────────────────────────────────────────────────
#  ADMIN — MANUAL DEPOSIT (credit by TxID or amount)
# ─────────────────────────────────────────────────────────────────────────────

async def adm_manual_dep_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    await q.edit_message_text(
        "💳 <b>Manual Deposit Credit</b>\n\n"
        "Send the <b>User ID</b> of the user to credit:\n\n/cancel to abort",
        parse_mode="HTML"
    )
    return ADM_MANUAL_DEP_UID

async def adm_manual_dep_uid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    try: uid = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID."); return ADM_MANUAL_DEP_UID
    u = db.get_user(uid)
    if not u:
        await update.message.reply_text("❌ User not found in database."); return ADM_MANUAL_DEP_UID
    ctx.user_data["mdep_uid"] = uid
    name = u.get("full_name") or u.get("username") or str(uid)
    await update.message.reply_html(
        f"👤 <b>{name}</b>  (<code>{uid}</code>)\n💼 Balance: <b>{fmt(u['balance'])}</b>\n\n"
        f"Now send the <b>USDT amount</b> to credit (e.g. <code>5.00</code>):"
    )
    return ADM_MANUAL_DEP_AMT

async def adm_manual_dep_amt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    try: amt = float(update.message.text.strip().replace("$",""))
    except ValueError:
        await update.message.reply_text("❌ Enter a valid number."); return ADM_MANUAL_DEP_AMT
    if amt <= 0:
        await update.message.reply_text("❌ Amount must be positive."); return ADM_MANUAL_DEP_AMT
    ctx.user_data["mdep_amt"] = amt
    await update.message.reply_html(
        f"💵 Amount: <b>{fmt(amt)}</b>\n\n"
        f"Now send the <b>Binance TxID</b> (or type <code>MANUAL</code> if no TxID):"
    )
    return ADM_MANUAL_DEP_TXID

async def adm_manual_dep_txid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    uid  = ctx.user_data.pop("mdep_uid", None)
    amt  = ctx.user_data.pop("mdep_amt", None)
    txid = update.message.text.strip()
    if not uid or not amt: return ConversationHandler.END
    did  = db.manual_credit_deposit(uid, amt, txid=txid, network="MANUAL")
    u2   = db.get_user(uid)
    name = u2.get("full_name") or u2.get("username") or str(uid) if u2 else str(uid)
    await update.message.reply_html(
        f"✅ <b>Manual Deposit #{did:04d} Credited!</b>\n\n"
        f"👤 User: <b>{name}</b> (<code>{uid}</code>)\n"
        f"💵 Amount: <b>{fmt(amt)}</b>\n"
        f"🔑 TxID: <code>{txid}</code>\n"
        f"💼 New Balance: <b>{fmt(u2['balance']) if u2 else '?'}</b>",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Admin", callback_data="admin")]]))
    await log_dep(update.message.bot,
        f"💳 <b>Manual Deposit Credited</b>\n{sep()}\n"
        f"👤 <code>{uid}</code> {name}\n"
        f"💵 {fmt(amt)}\n"
        f"🔑 {txid}\n"
        f"👮 By admin <code>{update.effective_user.id}</code>")
    try:
        lang_u = db.get_user_language(uid)
        await update.message.bot.send_message(uid,
            f"💰 <b>Deposit Credited!</b>\n\n"
            f"✅ {fmt(amt)} has been added to your wallet by admin.\n"
            f"💼 New balance: <b>{fmt(u2['balance']) if u2 else '?'}</b>",
            parse_mode="HTML")
    except Exception: pass
    return ConversationHandler.END

# ─────────────────────────────────────────────────────────────────────────────
#  ADMIN — ALL TICKETS (open + closed)
# ─────────────────────────────────────────────────────────────────────────────

async def adm_all_tickets_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    tickets = db.get_all_tickets()
    if not tickets:
        await q.edit_message_text("🎧 No tickets found.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Admin", callback_data="admin")]]),
            parse_mode="HTML"); return
    open_t  = [t for t in tickets if t["status"] == "open"]
    closed  = [t for t in tickets if t["status"] != "open"]
    btns    = []
    if open_t:
        btns.append([InlineKeyboardButton(f"── 🟢 Open ({len(open_t)}) ──", callback_data="noop")])
        for t in open_t[:15]:
            name = t.get("full_name") or t.get("username") or str(t["user_id"])
            btns.append([InlineKeyboardButton(f"🟢 #{t['id']:04d} {name[:15]} — {t['subject'][:18]}", callback_data=f"adm_ticket_{t['id']}")])
    if closed:
        btns.append([InlineKeyboardButton(f"── ⚫ Closed ({len(closed)}) ──", callback_data="noop")])
        for t in closed[:10]:
            name = t.get("full_name") or t.get("username") or str(t["user_id"])
            btns.append([InlineKeyboardButton(f"⚫ #{t['id']:04d} {name[:15]} — {t['subject'][:18]}", callback_data=f"adm_ticket_{t['id']}")])
    btns.append([InlineKeyboardButton("« Admin", callback_data="admin")])
    await q.edit_message_text(
        f"🎧 <b>ALL TICKETS</b>\n{sep()}\n🟢 Open: <b>{len(open_t)}</b>  |  ⚫ Closed: <b>{len(closed)}</b>",
        reply_markup=InlineKeyboardMarkup(btns), parse_mode="HTML"
    )

# ── /cancel ───────────────────────────────────────────────────────────────────

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    lang = get_lang(update.effective_user.id)
    await update.message.reply_text(T(lang,"cancelled"))
    return ConversationHandler.END

async def dep_cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/cancel while inside the deposit flow (network/amount step, incl. Binance
    Pay) — instead of showing the plain 'Cancelled' message, go back to the
    previous step (the deposit / wallet menu)."""
    ctx.user_data.pop("dep_network", None)
    uid  = update.effective_user.id
    lang = get_lang(uid)
    user = db.get_user(uid)
    bal  = user["balance"] if user else 0.0
    spent= user.get("total_spent_usdt", 0) if user else 0
    trc20  = usdt_trc20()
    bep20  = usdt_bep20()

    tier_name, _, tier_icon = loyalty_tier(spent)
    text = T(lang, "deposit_wallet_text", bal=bal, spent=spent, tier_icon=tier_icon, tier_name=tier_name)
    btns = []
    row2 = []
    if trc20: row2.append(InlineKeyboardButton(T(lang,"btn_usdt_trc20"), callback_data="dep_net_TRC20"))
    if bep20: row2.append(InlineKeyboardButton(T(lang,"btn_usdt_bep20"), callback_data="dep_net_BEP20"))
    if row2: btns.append(row2)
    btns.append([InlineKeyboardButton(T(lang,"btn_tx_history"), callback_data="dep_history")])
    btns.append([InlineKeyboardButton(T(lang,"btn_back"), callback_data="home")])
    await update.message.reply_html(text, reply_markup=InlineKeyboardMarkup(btns))
    return ConversationHandler.END

async def cancel_to_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Universal escape hatch: /start now works even while a conversation
    (Add Category, Deposit amount, etc.) is stuck waiting for input. It
    force-ends whatever conversation was active for this chat, then shows
    the normal /start screen — instead of silently doing nothing or, worse,
    swallowing the next unrelated text message as if it belonged to the
    stuck conversation.
    """
    await start(update, ctx)
    return ConversationHandler.END

async def cancel_to_admin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """/cancel while inside an admin data-entry flow (Manual Deposit, Add
    Category/Product, Broadcast, etc.) — goes back to the Admin Panel
    instead of showing just a bare 'Cancelled' message."""
    uid = update.effective_user.id
    if not is_admin(uid):
        return await cancel(update, ctx)
    text, btns = _admin_panel_content()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(b["text"], callback_data=b.get("callback_data","noop"), url=b.get("url")) for b in row] for row in btns])
    await update.message.reply_html(f"❌ Cancelled.\n\n{text}", reply_markup=kb)
    return ConversationHandler.END

# ── Error handler ─────────────────────────────────────────────────────────────

async def error_handler(update: object, ctx: ContextTypes.DEFAULT_TYPE):
    from telegram.error import BadRequest, NetworkError, TimedOut
    err = ctx.error
    if isinstance(err, (BadRequest,)) and "message is not modified" in str(err).lower():
        return
    if isinstance(err, (NetworkError, TimedOut)):
        return
    logger.error(f"Update {update} caused error: {err}", exc_info=err)

# ─────────────────────────────────────────────────────────────────────────────
#  BUILD APP
# ─────────────────────────────────────────────────────────────────────────────

def build_app() -> Application:
    token = getattr(config, "BOT_TOKEN", "")
    if not token:
        raise ValueError("BOT_TOKEN not set! Add it to Replit Secrets.")

    app = Application.builder().token(token).build()

    # ── Conversation handlers ─────────────────────────────────────────────────

    # Deposit: network → amount (TRC20/BEP20/Pay)
    dep_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(dep_net_cb, pattern=r"^dep_net_(TRC20|BEP20|PAY)$"),
            CallbackQueryHandler(deposit_start_cb, pattern="^deposit_start$"),
        ],
        states={
            DEP_NETWORK: [CallbackQueryHandler(dep_net_cb, pattern=r"^dep_net_(TRC20|BEP20|PAY)$")],
            DEP_AMOUNT:  [MessageHandler(NUM_FILTER, deposit_amount_received)],
        },
        fallbacks=[CommandHandler("cancel", dep_cancel), CallbackQueryHandler(deposit_cb, pattern="^deposit$"), CommandHandler("start", cancel_to_start)],
        **AE
    )

    # TX hash submission (TRC20/BEP20 on-chain verify)
    dep_hash_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(dep_submit_hash_start_cb, pattern=r"^dep_submit_hash_\d+$")],
        states={
            DEP_TX_HASH: [MessageHandler(filters.TEXT & ~filters.COMMAND, dep_tx_hash_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", cancel_to_start)],
        **AE
    )

    # Ticket: new message
    ticket_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(ticket_new_cb, pattern="^ticket_new$")],
        states={TICKET_MSG: [MessageHandler(filters.TEXT & ~filters.COMMAND, ticket_msg_received)]},
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(support_cb, pattern="^support$"), CommandHandler("start", cancel_to_start)],
        **AE
    )

    # Ticket: user reply
    ticket_reply_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(ticket_reply_start, pattern=r"^ticket_reply_\d+$")],
        states={TICKET_REPLY: [MessageHandler(filters.TEXT & ~filters.COMMAND, ticket_reply_received)]},
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", cancel_to_start)],
        **AE
    )

    # Admin reply to ticket
    adm_reply_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(adm_reply_start, pattern=r"^adm_reply_ticket_\d+$")],
        states={ADM_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_reply_received)]},
        fallbacks=[CommandHandler("cancel", cancel_to_admin), CommandHandler("start", cancel_to_start)],
        **AE
    )

    # Add stock
    stock_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(adm_addstock_start, pattern=r"^adm_addstock_\d+$")],
        states={ADM_STOCK_DATA: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_stock_data)]},
        fallbacks=[CommandHandler("cancel", cancel_to_admin), CommandHandler("start", cancel_to_start)],
        **AE
    )

    # Daily history — custom date entry
    daily_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(adm_daily_custom_start, pattern="^adm_daily_custom$")],
        states={ADM_DAILY_DATE: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_daily_custom_save)]},
        fallbacks=[CommandHandler("cancel", cancel_to_admin), CommandHandler("start", cancel_to_start)],
        **AE
    )

    # Free items — add new / add stock to existing
    free_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(adm_free_add_start,          pattern="^adm_free_add$"),
            CallbackQueryHandler(adm_free_addstock_start_cb,  pattern=r"^adm_free_addstock_\d+$"),
        ],
        states={
            ADM_FREE_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_free_name_save)],
            ADM_FREE_EMOJI: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_free_emoji_save)],
            ADM_FREE_STOCK: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_free_stock_save)],
        },
        fallbacks=[CommandHandler("cancel", cancel_to_admin), CommandHandler("start", cancel_to_start)],
        **AE
    )

    # Add category
    cat_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(adm_add_cat_start, pattern="^adm_add_cat$")],
        states={
            ADM_CAT_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_cat_name)],
            ADM_CAT_EMOJI: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_cat_emoji)],
        },
        fallbacks=[CommandHandler("cancel", cancel_to_admin), CommandHandler("start", cancel_to_start)],
        **AE
    )

    # Add product
    prd_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(adm_add_prd_start, pattern="^adm_add_prd$")],
        states={
            ADM_PRD_CAT:   [CallbackQueryHandler(adm_prd_cat, pattern=r"^prd_cat_\d+$")],
            ADM_PRD_NAME:  [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_prd_name)],
            ADM_PRD_EMOJI: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_prd_emoji)],
            ADM_PRD_DESC:  [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_prd_desc)],
            ADM_PRD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_prd_price)],
            ADM_PRD_DUR:   [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_prd_dur)],
            ADM_PRD_EMOJI_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_prd_emoji_id)],
        },
        fallbacks=[CommandHandler("cancel", cancel_to_admin), CommandHandler("start", cancel_to_start)],
        **AE
    )

    # Settings conversations
    settings_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(adm_set_botname_start,   pattern="^adm_set_botname$"),
            CallbackQueryHandler(adm_set_botemoji_start,  pattern="^adm_set_botemoji$"),
            CallbackQueryHandler(adm_set_trc20_start,     pattern="^adm_set_trc20$"),
            CallbackQueryHandler(adm_set_bep20_start,     pattern="^adm_set_bep20$"),
            CallbackQueryHandler(adm_set_payid_start,     pattern="^adm_set_payid$"),
            CallbackQueryHandler(adm_set_trc20_qr_start,  pattern="^adm_set_trc20_qr$"),
            CallbackQueryHandler(adm_set_bep20_qr_start,  pattern="^adm_set_bep20_qr$"),
            CallbackQueryHandler(adm_set_pay_qr_start,    pattern="^adm_set_pay_qr$"),
            CallbackQueryHandler(adm_set_logch_start,     pattern="^adm_set_logch$"),
            CallbackQueryHandler(adm_set_deplogch_start,  pattern="^adm_set_deplogch$"),
            CallbackQueryHandler(adm_set_min_dep_start,   pattern="^adm_set_min_dep$"),
            CallbackQueryHandler(adm_set_low_stock_start, pattern="^adm_set_low_stock$"),
            CallbackQueryHandler(adm_add_channel_start,   pattern="^adm_add_channel$"),
        ],
        states={
            ADM_BOT_NAME:        [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_set_botname_save)],
            ADM_BOT_EMOJI:       [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_set_botemoji_save)],
            ADM_USDT_TRC20:      [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_set_trc20_save)],
            ADM_USDT_BEP20:      [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_set_bep20_save)],
            ADM_BINANCE_PAY_ID:  [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_set_payid_save)],
            ADM_TRC20_QR:        [MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, adm_set_trc20_qr_save)],
            ADM_BEP20_QR:        [MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, adm_set_bep20_qr_save)],
            ADM_PAY_QR:          [MessageHandler((filters.TEXT | filters.PHOTO) & ~filters.COMMAND, adm_set_pay_qr_save)],
            ADM_LOG_CH:          [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_set_logch_save)],
            ADM_DEP_LOG_CH:      [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_set_deplogch_save)],
            ADM_MIN_DEP:         [MessageHandler(NUM_FILTER, adm_set_min_dep_save)],
            ADM_LOW_STOCK:       [MessageHandler(NUM_FILTER, adm_set_low_stock_save)],
            ADM_ADD_CH:          [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_add_channel_save)],
            ADM_ADD_CH_URL:      [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_add_channel_url_save)],
        },
        fallbacks=[CommandHandler("cancel", cancel_to_admin), CommandHandler("start", cancel_to_start)],
        **AE
    )

    # Add balance
    addbal_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(adm_addbal_start, pattern="^adm_addbal$")],
        states={
            ADM_ADDBAL_UID: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_addbal_uid)],
            ADM_ADDBAL_AMT: [MessageHandler(NUM_FILTER, adm_addbal_amt)],
        },
        fallbacks=[CommandHandler("cancel", cancel_to_admin), CommandHandler("start", cancel_to_start)],
        **AE
    )

    # Remove balance
    rembal_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(adm_rembal_start, pattern="^adm_rembal$")],
        states={
            ADM_REMBAL_UID: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_rembal_uid)],
            ADM_REMBAL_AMT: [MessageHandler(NUM_FILTER, adm_rembal_amt)],
        },
        fallbacks=[CommandHandler("cancel", cancel_to_admin), CommandHandler("start", cancel_to_start)],
        **AE
    )

    # Add admin
    addadmin_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(adm_add_admin_start_cb, pattern="^adm_add_admin$")],
        states={ADM_ADD_ADMIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_add_admin_receive)]},
        fallbacks=[CommandHandler("cancel", cancel_to_admin), CommandHandler("start", cancel_to_start)],
        **AE
    )

    # Search user
    search_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(adm_search_user_start_cb, pattern="^adm_search_user$")],
        states={ADM_SEARCH_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_search_user_result)]},
        fallbacks=[CommandHandler("cancel", cancel_to_admin), CommandHandler("start", cancel_to_start)],
        **AE
    )

    # Coupon
    coupon_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(feat.adm_add_coupon_start, pattern="^adm_add_coupon$")],
        states={
            feat.FEAT_COUPON_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, feat.feat_coupon_code)],
            feat.FEAT_COUPON_DISC: [MessageHandler(filters.TEXT & ~filters.COMMAND, feat.feat_coupon_disc)],
            feat.FEAT_COUPON_USES: [MessageHandler(filters.TEXT & ~filters.COMMAND, feat.feat_coupon_uses)],
        },
        fallbacks=[CommandHandler("cancel", cancel_to_admin), CommandHandler("start", cancel_to_start)],
        **AE
    )

    # Gift balance
    gift_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(feat.gift_start_cb, pattern="^gift_start$")],
        states={
            feat.FEAT_GIFT_UID: [MessageHandler(filters.TEXT & ~filters.COMMAND, feat.feat_gift_uid)],
            feat.FEAT_GIFT_AMT: [MessageHandler(NUM_FILTER, feat.feat_gift_amt)],
        },
        fallbacks=[CommandHandler("cancel", cancel_to_admin), CommandHandler("start", cancel_to_start)],
        **AE
    )

    # Broadcast
    broadcast_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(adm_broadcast_start, pattern="^adm_broadcast$")],
        states={ADM_REAL_BROADCAST: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_broadcast_do)]},
        fallbacks=[CommandHandler("cancel", cancel_to_admin), CommandHandler("start", cancel_to_start)],
        **AE
    )

    # Manual deposit
    manual_dep_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(adm_manual_dep_start, pattern="^adm_manual_dep$")],
        states={
            ADM_MANUAL_DEP_UID:  [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_manual_dep_uid)],
            ADM_MANUAL_DEP_AMT:  [MessageHandler(NUM_FILTER, adm_manual_dep_amt)],
            ADM_MANUAL_DEP_TXID: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_manual_dep_txid)],
        },
        fallbacks=[CommandHandler("cancel", cancel_to_admin), CommandHandler("start", cancel_to_start)],
        **AE
    )

    coupon_apply_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cart_coupon_start_cb, pattern="^cart_coupon_start$")],
        states={
            CART_COUPON_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, cart_coupon_code_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(cart_view_cb, pattern="^cart$"), CommandHandler("start", cancel_to_start)],
        **AE
    )

    # Find Order by ID
    find_order_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(find_order_start_cb, pattern="^find_order_start$")],
        states={
            FIND_ORDER_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, find_order_code_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CallbackQueryHandler(history_cb, pattern="^history"), CommandHandler("start", cancel_to_start)],
        **AE
    )

    # User history search
    user_hist_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(adm_user_hist_start, pattern="^adm_user_hist$")],
        states={ADM_USER_HIST: [MessageHandler(filters.TEXT & ~filters.COMMAND, adm_user_hist_search)]},
        fallbacks=[CommandHandler("cancel", cancel_to_admin), CommandHandler("start", cancel_to_start)],
        **AE
    )

    # User refund request conversation
    refund_req_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(user_refund_start_cb, pattern=r"^refund_req_\d+$")],
        states={
            USER_REFUND_REASON: [MessageHandler(filters.TEXT & ~filters.COMMAND, user_refund_reason_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", cancel_to_start)],
        **AE
    )

    # Refund chat (works for both admin and user, role decided at runtime)
    refund_chat_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(refund_msg_start, pattern=r"^refund_msg_\d+$")],
        states={
            ADM_REFUND_REPLY:  [MessageHandler(filters.TEXT & ~filters.COMMAND, refund_msg_admin_received)],
            USER_REFUND_REPLY: [MessageHandler(filters.TEXT & ~filters.COMMAND, refund_msg_user_received)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", cancel_to_start)],
        **AE
    )

    # Reseller withdrawal request (user-side)
    reseller_wd_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(feat.reseller_wd_start_cb, pattern="^reseller_wd$")],
        states={
            feat.FEAT_WD_AMT: [MessageHandler(NUM_FILTER, feat.feat_wd_amt)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", cancel_to_start)],
        **AE
    )

    for h in [dep_conv, dep_hash_conv, ticket_conv, ticket_reply_conv, adm_reply_conv,
               stock_conv, cat_conv, prd_conv, settings_conv, addbal_conv, rembal_conv,
               addadmin_conv, search_conv, coupon_conv, gift_conv, free_conv, daily_conv,
               broadcast_conv, manual_dep_conv, user_hist_conv, refund_req_conv, coupon_apply_conv,
               refund_chat_conv, reseller_wd_conv, find_order_conv]:
        app.add_handler(h)

    # ── Command handlers ──────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start",   start))
    app.add_handler(CommandHandler("cancel",  cancel))
    app.add_handler(CommandHandler("wallet",  wallet_cmd))
    app.add_handler(CommandHandler("shop",    shop_cmd))
    app.add_handler(CommandHandler("orders",  orders_cmd))
    app.add_handler(CommandHandler("profile", profile_cmd))
    app.add_handler(CommandHandler("support", support_cmd))

    # ── Callback query handlers ───────────────────────────────────────────────
    for pat, fn in [
        ("^setlang_",              setlang_cb),
        ("^language$",             language_cb),
        ("^home$",                 home),
        ("^check_join$",           check_join_cb),
        ("^profile$",              profile_cb),
        (r"^history(_\d+)?$",       history_cb),
        (r"^order_\d+$",           order_detail_cb),
        ("^referral$",             referral_cb),
        ("^shop$",                 shop_cb),
        (r"^cat_\d+$",             cat_cb),
        (r"^product_\d+$",         product_cb),
        (r"^restock_\d+$",         restock_cb),
        (r"^qtydec_\d+_\d+$",     qty_change_cb),
        (r"^qtyinc_\d+_\d+$",     qty_change_cb),
        (r"^cartadd_\d+",          cartadd_cb),
        (r"^buy_\d+$",             buy_cb),
        (r"^buyqty_\d+_\d+$",     buyqty_cb),
        ("^cart$",                 cart_view_cb),
        ("^cart_vip$",             cart_vip_cb),
        (r"^cartqty_\d+_",         cart_qty_cb),
        (r"^cartrem_\d+$",         cart_remove_cb),
        ("^cart_clear$",           cart_clear_cb),
        ("^cart_checkout$",        cart_checkout_cb),
        ("^cart_coupon_rm$",       cart_coupon_remove_cb),
        ("^deposit$",              deposit_cb),
        ("^deposit_start$",        deposit_start_cb),
        (r"^dep_status_\d+$",      dep_status_cb),
        ("^dep_history$",          dep_history_cb),
        (r"^dep_pay_ipaid_\d+$",   dep_pay_ipaid_cb),
        (r"^dep_check_\d+",        dep_check_again_cb),
        (r"^dep_notify_adm_\d+$",  dep_notify_admin_cb),
        (r"^adm_dep_ok_\d+$",      adm_dep_ok_cb),
        (r"^adm_dep_no_\d+$",      adm_dep_no_cb),
        ("^support$",              support_cb),
        ("^ticket_list$",          ticket_list_cb),
        (r"^ticket_view_\d+$",     ticket_view_cb),
        (r"^ticket_close_\d+$",    ticket_close_user_cb),
        ("^admin$",                admin_cb),
        ("^adm_settings$",         adm_settings_cb),
        (r"^adm_tog_\w+$",         adm_toggle_cb),
        ("^adm_cats$",             adm_cats_cb),
        (r"^adm_toggle_cat_\d+$",  adm_toggle_cat_cb),
        (r"^adm_del_cat_\d+$",     adm_del_cat_cb),
        (r"^adm_delcat_force_\d+$",adm_del_cat_force_cb),
        ("^adm_prds$",             adm_prds_cb),
        (r"^adm_del_prd_\d+$",     adm_del_prd_cb),
        ("^adm_stock_menu$",       adm_stock_menu_cb),
        ("^adm_view_stock_menu$",  adm_view_stock_menu_cb),
        (r"^adm_viewstock_\d+$",   adm_viewstock_cb),
        (r"^adm_remstock_\d+$",    adm_remstock_cb),
        (r"^adm_clearstock_\d+$",  adm_clearstock_cb),
        ("^adm_users$",            adm_users_cb),
        (r"^adm_ban_\d+$",         adm_ban_cb),
        ("^adm_tickets$",          adm_tickets_cb),
        (r"^adm_ticket_\d+$",      adm_ticket_view_cb),
        (r"^adm_close_ticket_\d+$",adm_close_ticket_cb),
        ("^adm_view_admins$",      adm_view_admins_cb),
        ("^adm_rem_admin_start$",  adm_rem_admin_start_cb),
        (r"^adm_do_rem_admin_\d+$",adm_do_rem_admin_cb),
        ("^adm_rem_channel$",      adm_rem_channel_cb),
        (r"^adm_remch_\d+$",       adm_remch_cb),
        # New admin features
        ("^adm_today_orders$",         adm_today_orders_cb),
        ("^adm_dl_today_orders$",      adm_dl_today_orders_cb),
        ("^adm_today_deps$",           adm_today_deps_cb),
        ("^adm_dl_today_deps$",        adm_dl_today_deps_cb),
        ("^adm_dl_all_orders$",        adm_dl_all_orders_cb),
        ("^adm_dl_all_deps$",          adm_dl_all_deps_cb),
        (r"^adm_uhist_\d+$",           adm_uhist_cb),
        (r"^adm_dl_uhist_\d+$",        adm_dl_uhist_cb),
        ("^adm_all_tickets$",          adm_all_tickets_cb),
        # Features
        ("^adm_coupons$",              feat.adm_coupons_cb),
        (r"^adm_tog_coupon_\d+$",      feat.adm_toggle_coupon_cb),
        (r"^adm_del_coupon_\d+$",      feat.adm_delete_coupon_cb),
        (r"^adm_refund_\d+$",          feat.adm_refund_cb),
        (r"^adm_rreq_ok_\d+$",        adm_rreq_ok_cb),
        (r"^adm_rreq_no_\d+$",        adm_rreq_no_cb),
        (r"^adm_dismiss_report_\d+$",  feat.adm_dismiss_report_cb),
        ("^adm_export_menu$",          feat.adm_export_menu_cb),
        ("^adm_export_today$",         feat.adm_export_cb),
        ("^adm_export_all$",           feat.adm_export_cb),
        ("^reseller_panel$",           feat.reseller_panel_cb),
        ("^adm_reseller_menu$",        feat.adm_reseller_menu_cb),
        (r"^adm_reseller_revoke_\d+$", feat.adm_reseller_revoke_cb),
        ("^adm_wd_list$",              feat.adm_wd_list_cb),
        (r"^adm_wd_paid_\d+$",         feat.adm_wd_process_cb),
        (r"^adm_wd_reject_\d+$",       feat.adm_wd_process_cb),
        ("^free_items$",               free_items_cb),
        (r"^free_claim_\d+$",          free_item_claim_cb),
        ("^adm_free_menu$",            adm_free_menu_cb),
        (r"^adm_free_toggle_\d+$",     adm_free_toggle_cb),
        (r"^adm_free_del_\d+$",        adm_free_del_cb),
        ("^adm_daily_menu$",           adm_daily_menu_cb),
        (r"^adm_daily_\d{4}-\d{2}-\d{2}$", adm_daily_view_cb),
        ("^noop$",                     noop_cb),
    ]:
        app.add_handler(CallbackQueryHandler(fn, pattern=pat))

    app.add_error_handler(error_handler)

    # ── Background jobs ───────────────────────────────────────────────────────
    app.job_queue.run_repeating(feat.renewal_reminder_job, interval=3600, first=60)
    app.job_queue.run_repeating(feat.reseller_maturation_job, interval=600, first=30)

    async def _set_commands(a):
        await a.bot.set_my_commands([
            BotCommand("start",   "🏠 Open the home menu"),
            BotCommand("shop",    "🛍️ Browse the shop"),
            BotCommand("wallet",  "👛 View your wallet & balance"),
            BotCommand("orders",  "📦 View your orders"),
            BotCommand("profile", "👤 View your profile"),
            BotCommand("support", "🎫 Contact support"),
            BotCommand("cancel",  "❌ Cancel current action"),
        ])
    app.post_init = _set_commands

    return app

if __name__ == "__main__":
    db.init_db()
    print(f"🤖 {bot_name()} v5.0 starting…")
    print(f"  TRC20: {usdt_trc20() or 'Not configured'}")
    print(f"  BEP20: {usdt_bep20() or 'Not configured'}")
    print(f"  Force-join: {len(get_force_channels())} channel(s)")
    build_app().run_polling(drop_pending_updates=True)
