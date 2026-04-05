import asyncio
import importlib.util
import inspect
import json
import logging
import os
import sys
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Set, Tuple

from telegram import (
    Bot,
    ChatMember,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    ApplicationHandlerStop,
    BaseHandler,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    TypeHandler,
    filters,
)

# =============================================================================
#  CONFIGURATION — EDIT THESE
# =============================================================================

BOT_TOKEN = "7727685861:AAFR5NtU4dH-8T8gGqBOMou59vlvPGs7h9Q"
OWNER_ID = 8395315423    # Your Telegram user ID (integer)

CONFIG_FILE = "config.json"
USERS_FILE  = "users.json"

CACHE_TTL = 300  # Membership cache TTL in seconds (5 minutes)

# =============================================================================
#  LOGGING
# =============================================================================

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("main")

logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)

# =============================================================================
#  CONFIG  &  USER  DATA
# =============================================================================

def _migrate_config(cfg: Dict) -> Dict:
    """Migrate old v3.0 config (separate 'channels'/'groups') to unified 'chats'."""
    if "chats" in cfg:
        return cfg

    chats: List[Dict] = []

    for ch in cfg.get("channels", []):
        entry = dict(ch)
        entry.setdefault("type", "channel")
        entry.setdefault("join_request", False)
        if "id" in entry and "chat_id" not in entry:
            entry["chat_id"] = entry.pop("id")
        chats.append(entry)

    for gr in cfg.get("groups", []):
        entry = dict(gr)
        entry.setdefault("type", "group")
        entry.setdefault("join_request", False)
        if "id" in entry and "chat_id" not in entry:
            entry["chat_id"] = entry.pop("id")
        chats.append(entry)

    return {"chats": chats}


def load_config() -> Dict:
    if not os.path.exists(CONFIG_FILE):
        return {"chats": []}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return _migrate_config(raw)
    except Exception:
        return {"chats": []}


def save_config(cfg: Dict) -> None:
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log.error("save_config error: %s", e)


def load_users() -> Dict:
    if not os.path.exists(USERS_FILE):
        return {}
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_users(users: Dict) -> None:
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log.error("save_users error: %s", e)


def record_user(user) -> None:
    """Persist basic user info to users.json."""
    try:
        users = load_users()
        uid = str(user.id)
        entry = users.get(uid, {})
        entry["id"]         = user.id
        entry["username"]   = user.username or ""
        entry["first_name"] = user.first_name or ""
        entry["last_name"]  = user.last_name or ""
        entry.setdefault("first_seen", datetime.utcnow().isoformat())
        entry["last_seen"] = datetime.utcnow().isoformat()
        users[uid] = entry
        save_users(users)
    except Exception as e:
        log.warning("record_user error: %s", e)


# =============================================================================
#  MEMBERSHIP  CACHE
# =============================================================================

_membership_cache: Dict[int, Dict] = {}


def _cache_get(user_id: int) -> Optional[bool]:
    try:
        entry = _membership_cache.get(user_id)
        if entry and (time.time() - entry["ts"]) < CACHE_TTL:
            return entry["result"]
        return None
    except Exception:
        return None


def _cache_set(user_id: int, result: bool) -> None:
    try:
        _membership_cache[user_id] = {"result": result, "ts": time.time()}
    except Exception:
        pass


def _cache_invalidate(user_id: int) -> None:
    try:
        _membership_cache.pop(user_id, None)
    except Exception:
        pass


# =============================================================================
#  JOIN  REQUEST  TRACKING
# =============================================================================

# Set of (chat_id, user_id) tuples with a pending join request
pending_join_requests: Set[Tuple[int, int]] = set()

# =============================================================================
#  VERIFICATION  LOGIC
# =============================================================================

MEMBER_STATUSES = {
    ChatMember.MEMBER,
    ChatMember.ADMINISTRATOR,
    ChatMember.OWNER,
    "restricted",  # Still in the chat
}


async def check_membership_for_chat(
    bot: Bot,
    user_id: int,
    chat_id: Any,
    is_join_request_chat: bool = False,
) -> bool:
    """
    Return True if user is an active member of chat_id.
    If is_join_request_chat=True, also treats a pending join request as verified.
    """
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        if member.status in MEMBER_STATUSES:
            return True
        if is_join_request_chat and (chat_id, user_id) in pending_join_requests:
            return True
        return False
    except TelegramError:
        if is_join_request_chat and (chat_id, user_id) in pending_join_requests:
            return True
        return False


async def is_user_currently_verified(user_id: int, bot: Bot) -> bool:
    """Check membership in ALL configured chats. Uses 5-min cache."""
    try:
        cached = _cache_get(user_id)
        if cached is not None:
            return cached

        config = load_config()
        chats  = config.get("chats", [])

        if not chats:
            _cache_set(user_id, True)
            return True

        for chat in chats:
            chat_id = chat.get("chat_id") or chat.get("id")
            if not chat_id:
                continue
            is_jr = bool(chat.get("join_request", False))
            ok = await check_membership_for_chat(bot, user_id, chat_id,
                                                  is_join_request_chat=is_jr)
            if not ok:
                _cache_set(user_id, False)
                return False

        _cache_set(user_id, True)
        return True
    except Exception as e:
        log.error("is_user_currently_verified error: %s", e)
        return False


# =============================================================================
#  JOIN  MESSAGE  /  KEYBOARD  BUILDER
# =============================================================================

def build_join_keyboard(config: Dict) -> InlineKeyboardMarkup:
    """Build inline keyboard with join links for all chats + verify button."""
    try:
        buttons = []
        for chat in config.get("chats", []):
            label = chat.get("title", "Chat")
            link  = chat.get("invite_link", "")
            icon  = "📢" if chat.get("type") == "channel" else "👥"
            if link:
                buttons.append([InlineKeyboardButton(f"{icon} {label}", url=link)])
            else:
                buttons.append([InlineKeyboardButton(
                    f"{icon} {label} (no link)", callback_data="no_link"
                )])
        buttons.append([InlineKeyboardButton(
            "✅ I've Joined — Verify Me", callback_data="verify_membership"
        )])
        return InlineKeyboardMarkup(buttons)
    except Exception as e:
        log.error("build_join_keyboard error: %s", e)
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Verify Me", callback_data="verify_membership")
        ]])


async def send_join_message(update: Update,
                             context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the join-requirement message to the user."""
    try:
        config = load_config()
        chats  = config.get("chats", [])

        lines = ["<b>🔒 Access Restricted</b>\n"]
        lines.append("To use this bot you must join the following:")

        channels = [c for c in chats if c.get("type") == "channel"]
        groups   = [c for c in chats if c.get("type") == "group"]

        if channels:
            lines.append("\n<b>📢 Channels:</b>")
            for ch in channels:
                lines.append(f"  • {ch.get('title', 'Channel')}")
        if groups:
            lines.append("\n<b>👥 Groups:</b>")
            for gr in groups:
                lines.append(f"  • {gr.get('title', 'Group')}")

        lines.append("\nClick the buttons below to join, then press <b>Verify Me</b>.")

        text     = "\n".join(lines)
        keyboard = build_join_keyboard(config)

        if update.message:
            await update.message.reply_text(
                text, parse_mode=ParseMode.HTML, reply_markup=keyboard
            )
        elif update.callback_query:
            await update.callback_query.message.reply_text(
                text, parse_mode=ParseMode.HTML, reply_markup=keyboard
            )
    except Exception as e:
        log.error("send_join_message error: %s", e)


# =============================================================================
#  GLOBAL  PRE-CHECK  (group = -1)
# =============================================================================

async def global_verification_check(update: Update,
                                      context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Runs BEFORE every handler (group -1).
    Blocks unverified users with ApplicationHandlerStop.
    Owner, /start, /cancel, and system callbacks always pass through.
    """
    try:
        user = update.effective_user
        if not user:
            return  # Channel posts / service messages — pass

        # Owner always passes
        if user.id == OWNER_ID:
            return

        # No chats configured → no gate
        config = load_config()
        if not config.get("chats"):
            return

        # Whitelist /start and /cancel so unverified users can reach them
        if update.message and update.message.text:
            cmd_text = update.message.text.strip()
            if cmd_text.startswith("/"):
                cmd = cmd_text.split()[0].lower().lstrip("/").split("@")[0]
                if cmd in ("start", "cancel"):
                    return

        # Whitelist our own callback data
        if update.callback_query:
            data = update.callback_query.data or ""
            if data in ("verify_membership", "no_link") or data.startswith("admin_"):
                return

        # Membership check (cached)
        verified = await is_user_currently_verified(user.id, context.bot)
        if verified:
            record_user(user)
            return

        # NOT VERIFIED — block
        if update.callback_query:
            try:
                await update.callback_query.answer(
                    "⚠️ You must verify first! Send /start to get access.",
                    show_alert=True,
                )
            except TelegramError:
                pass
        elif update.message and update.message.chat.type == "private":
            await send_join_message(update, context)

        raise ApplicationHandlerStop

    except ApplicationHandlerStop:
        raise
    except Exception as e:
        log.error("global_verification_check error: %s", e)


# =============================================================================
#  ADMIN  FLOW  FILTER
# =============================================================================

_admin_state: Dict[int, Dict] = {}


class AdminFlowFilter(filters.UpdateFilter):
    """
    Passes only when:
      • The user is the owner, AND
      • There is an active admin conversation state.
    """
    def filter(self, update: Update) -> bool:
        try:
            if not update.effective_user:
                return False
            if update.effective_user.id != OWNER_ID:
                return False
            return bool(_admin_state.get(OWNER_ID))
        except Exception:
            return False


admin_flow_filter = AdminFlowFilter()

# =============================================================================
#  ORIGINAL  BOT.PY  START  CALLBACK
# =============================================================================

_original_bot_start: Optional[Any] = None

# =============================================================================
#  ADMIN  PANEL  HELPERS
# =============================================================================

def build_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Add Channel",  callback_data="admin_add_channel"),
            InlineKeyboardButton("➕ Add Group",    callback_data="admin_add_group"),
        ],
        [
            InlineKeyboardButton("📋 View All",     callback_data="admin_view"),
            InlineKeyboardButton("🗑 Remove",       callback_data="admin_remove"),
        ],
        [
            InlineKeyboardButton("📊 Statistics",   callback_data="admin_stats"),
            InlineKeyboardButton("📣 Broadcast",    callback_data="admin_broadcast"),
        ],
        [
            InlineKeyboardButton("🔄 Clear Cache",  callback_data="admin_clear_cache"),
            InlineKeyboardButton("❌ Close",        callback_data="admin_close"),
        ],
    ])


async def show_admin_panel(update: Update,
                            context: ContextTypes.DEFAULT_TYPE,
                            edit: bool = False) -> None:
    """Display the admin panel. Called on owner /start and via /admin."""
    try:
        config  = load_config()
        chats   = config.get("chats", [])
        users   = load_users()
        n_ch    = sum(1 for c in chats if c.get("type") == "channel")
        n_gr    = sum(1 for c in chats if c.get("type") == "group")
        n_users = len(users)
        n_cache = sum(1 for v in _membership_cache.values() if v.get("result"))

        name = update.effective_user.first_name if update.effective_user else "Owner"

        text = (
            f"<b>Welcome back, {name}! 👑</b>\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n"
            "🤖 <b>Bot Status:</b> Online\n"
            f"📢 <b>Channels:</b> {n_ch}\n"
            f"👥 <b>Groups:</b>   {n_gr}\n"
            f"👤 <b>Users:</b> {n_users} | ✅ <b>Verified cache:</b> {n_cache}\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "🔧 <b>ADMIN PANEL</b>\n\n"
            "Select an action below:"
        )

        kb = build_admin_keyboard()

        if edit and update.callback_query:
            await update.callback_query.edit_message_text(
                text, parse_mode=ParseMode.HTML, reply_markup=kb
            )
        else:
            msg = update.message or (
                update.callback_query.message if update.callback_query else None
            )
            if msg:
                await msg.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception as e:
        log.error("show_admin_panel error: %s", e)


# =============================================================================
#  /start  COMMAND
#  Owner   → bot.py /start FIRST, then admin panel.
#  Verified → bot.py /start (if any), else welcome.
#  Unverified → join message.
# =============================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user = update.effective_user
        if not user:
            return

        record_user(user)

        # ── OWNER ──────────────────────────────────────────────────────────
        if user.id == OWNER_ID:
            # Step 1: call bot.py's /start to show bot features
            if _original_bot_start:
                try:
                    await _original_bot_start(update, context)
                except Exception as e:
                    log.warning("original /start (owner) failed: %s", e)
            # Step 2: show admin panel as a separate message
            await show_admin_panel(update, context)
            return

        # ── REGULAR USERS ───────────────────────────────────────────────────
        config = load_config()
        chats  = config.get("chats", [])

        if not chats:
            # No gate configured
            if _original_bot_start:
                try:
                    await _original_bot_start(update, context)
                    return
                except Exception as e:
                    log.warning("original /start (no gate) failed: %s", e)
            await update.message.reply_text(
                "<b>👋 Welcome!</b>\n\nNo access restrictions configured.\n"
                "The bot is fully available.",
                parse_mode=ParseMode.HTML,
            )
            return

        verified = await is_user_currently_verified(user.id, context.bot)
        if verified:
            if _original_bot_start:
                try:
                    await _original_bot_start(update, context)
                    return
                except Exception as e:
                    log.warning("original /start (verified) failed: %s", e)
            await update.message.reply_text(
                "<b>✅ You're verified!</b>\n\nEnjoy full access to the bot.",
                parse_mode=ParseMode.HTML,
            )
            return

        # Not verified
        await send_join_message(update, context)

    except Exception as e:
        log.error("start_command error: %s", e)


# =============================================================================
#  VERIFY  CALLBACK
#  After success → delete join message + tell user to press /start.
#  We do NOT call _original_bot_start here because update.message is None
#  in a callback context, which would crash most bot.py /start handlers.
# =============================================================================

async def handle_verify_callback(update: Update,
                                   context: ContextTypes.DEFAULT_TYPE) -> None:
    """User pressed 'I've Joined — Verify Me'."""
    try:
        query = update.callback_query
        await query.answer()

        user = update.effective_user
        _cache_invalidate(user.id)  # Force fresh check

        verified = await is_user_currently_verified(user.id, context.bot)

        if verified:
            record_user(user)

            # Delete the join message
            try:
                await query.message.delete()
            except Exception:
                pass

            # Tell the user to press /start — safe regardless of bot.py structure
            await context.bot.send_message(
                chat_id=user.id,
                text=(
                    "✅ <b>Verification successful!</b>\n\n"
                    "You now have full access to the bot.\n"
                    "Press /start to begin!"
                ),
                parse_mode=ParseMode.HTML,
            )
        else:
            config   = load_config()
            keyboard = build_join_keyboard(config)
            try:
                await query.edit_message_text(
                    "<b>❌ Verification failed.</b>\n\n"
                    "It looks like you haven't joined all required channels/groups yet.\n"
                    "Please join them all, then press <b>Verify Me</b> again.",
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                )
            except Exception:
                await context.bot.send_message(
                    chat_id=user.id,
                    text=(
                        "<b>❌ Verification failed.</b>\n\n"
                        "Please join all required chats and try again."
                    ),
                    parse_mode=ParseMode.HTML,
                    reply_markup=keyboard,
                )

    except Exception as e:
        log.error("handle_verify_callback error: %s", e)


async def handle_no_link_callback(update: Update,
                                   context: ContextTypes.DEFAULT_TYPE) -> None:
    """User pressed a chat button that has no invite link."""
    try:
        await update.callback_query.answer(
            "⚠️ No invite link available for this chat. Contact the admin.",
            show_alert=True,
        )
    except Exception as e:
        log.error("handle_no_link_callback error: %s", e)


# =============================================================================
#  /cancel  COMMAND
# =============================================================================

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        user_id = update.effective_user.id if update.effective_user else None
        if user_id and user_id in _admin_state:
            _admin_state.pop(user_id)
            await update.message.reply_text("❌ Admin action cancelled.")
        else:
            await update.message.reply_text("Nothing to cancel.")
    except Exception as e:
        log.error("cancel_command error: %s", e)


# =============================================================================
#  /admin  COMMAND
# =============================================================================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        if not update.effective_user or update.effective_user.id != OWNER_ID:
            await update.message.reply_text("⛔ Owner only.")
            return
        await show_admin_panel(update, context)
    except Exception as e:
        log.error("admin_command error: %s", e)


# =============================================================================
#  ADMIN  CALLBACK  ROUTER
# =============================================================================

async def handle_admin_callback_router(update: Update,
                                        context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        query = update.callback_query

        if not update.effective_user or update.effective_user.id != OWNER_ID:
            await query.answer("⛔ Owner only.", show_alert=True)
            return

        data = query.data or ""

        if data.startswith("admin_remove_confirm_"):
            await _admin_remove_confirm(update, context)
            return

        dispatch = {
            "admin_add_channel": _admin_add_channel,
            "admin_add_group":   _admin_add_group,
            "admin_remove":      _admin_remove,
            "admin_view":        _admin_view,
            "admin_stats":       _admin_stats,
            "admin_broadcast":   _admin_broadcast,
            "admin_clear_cache": _admin_clear_cache,
            "admin_close":       _admin_close,
            "admin_back":        lambda u, c: show_admin_panel(u, c, edit=True),
        }

        fn = dispatch.get(data)
        if fn:
            await fn(update, context)
        else:
            await query.answer("Unknown action.", show_alert=True)

    except Exception as e:
        log.error("handle_admin_callback_router error: %s", e)


# =============================================================================
#  ADMIN  PANEL  ACTIONS
# =============================================================================

async def _admin_add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        query = update.callback_query
        await query.answer()
        _admin_state[OWNER_ID] = {"action": "add_chat", "type": "channel",
                                   "step": "waiting_id"}
        await query.edit_message_text(
            "<b>➕ Add Channel</b>\n\n"
            "Send the channel <b>username</b> (e.g. <code>@mychannel</code>) "
            "or numeric <b>chat ID</b> (e.g. <code>-1001234567890</code>).\n\n"
            "Send /cancel to abort.",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        log.error("_admin_add_channel error: %s", e)


async def _admin_add_group(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        query = update.callback_query
        await query.answer()
        _admin_state[OWNER_ID] = {"action": "add_chat", "type": "group",
                                   "step": "waiting_id"}
        await query.edit_message_text(
            "<b>➕ Add Group</b>\n\n"
            "Send the group <b>username</b> (e.g. <code>@mygroup</code>) "
            "or numeric <b>chat ID</b> (e.g. <code>-1001234567890</code>).\n\n"
            "Send /cancel to abort.",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        log.error("_admin_add_group error: %s", e)


async def _admin_remove(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        query = update.callback_query
        await query.answer()

        config = load_config()
        chats  = config.get("chats", [])

        if not chats:
            await query.edit_message_text(
                "No chats configured yet.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 Back", callback_data="admin_back")
                ]]),
            )
            return

        buttons = []
        for i, chat in enumerate(chats):
            icon  = "📢" if chat.get("type") == "channel" else "👥"
            label = chat.get("title", str(chat.get("chat_id", "?")))
            buttons.append([InlineKeyboardButton(
                f"{icon} {label}", callback_data=f"admin_remove_confirm_{i}"
            )])
        buttons.append([InlineKeyboardButton("🔙 Back", callback_data="admin_back")])

        _admin_state[OWNER_ID] = {"action": "removing", "snapshot": chats}

        await query.edit_message_text(
            "<b>🗑 Remove Chat</b>\n\nSelect which chat to remove:",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(buttons),
        )
    except Exception as e:
        log.error("_admin_remove error: %s", e)


async def _admin_remove_confirm(update: Update,
                                 context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        query = update.callback_query
        await query.answer()

        data = query.data  # admin_remove_confirm_<index>
        try:
            index = int(data.rsplit("_", 1)[-1])
        except ValueError:
            await query.answer("Invalid selection.", show_alert=True)
            return

        state    = _admin_state.get(OWNER_ID, {})
        snapshot = state.get("snapshot", [])

        if index < 0 or index >= len(snapshot):
            await query.answer("Invalid index.", show_alert=True)
            return

        chat  = snapshot[index]
        title = chat.get("title", str(chat.get("chat_id", "?")))
        cid   = chat.get("chat_id")

        config = load_config()
        config["chats"] = [c for c in config.get("chats", [])
                           if c.get("chat_id") != cid]
        save_config(config)

        _admin_state.pop(OWNER_ID, None)
        _membership_cache.clear()

        await query.edit_message_text(
            f"✅ Removed <b>{title}</b> from the verification list.\n"
            "Membership cache cleared.",
            parse_mode=ParseMode.HTML,
            reply_markup=build_admin_keyboard(),
        )
    except Exception as e:
        log.error("_admin_remove_confirm error: %s", e)


async def _admin_view(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        query = update.callback_query
        await query.answer()

        config = load_config()
        chats  = config.get("chats", [])

        lines = ["<b>📋 Configured Chats</b>\n"]

        if chats:
            for ch in chats:
                icon  = "📢" if ch.get("type") == "channel" else "👥"
                cid   = ch.get("chat_id", "?")
                title = ch.get("title", "?")
                link  = ch.get("invite_link") or "none (private)"
                jr    = "✅ Yes" if ch.get("join_request") else "❌ No"
                lines.append(
                    f"{icon} <b>{title}</b>\n"
                    f"    ID: <code>{cid}</code>\n"
                    f"    Invite: {link}\n"
                    f"    Join Requests: {jr}"
                )
        else:
            lines.append("No chats configured yet.")

        await query.edit_message_text(
            "\n\n".join(lines),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="admin_back")
            ]]),
        )
    except Exception as e:
        log.error("_admin_view error: %s", e)


async def _admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        query = update.callback_query
        await query.answer()

        config  = load_config()
        chats   = config.get("chats", [])
        users   = load_users()
        n_ch    = sum(1 for c in chats if c.get("type") == "channel")
        n_gr    = sum(1 for c in chats if c.get("type") == "group")
        n_users = len(users)

        cached_ok = sum(1 for v in _membership_cache.values() if v.get("result"))
        cached_no = sum(1 for v in _membership_cache.values() if not v.get("result"))

        text = (
            "<b>📊 Bot Statistics</b>\n\n"
            f"<b>📢 Channels required:</b> {n_ch}\n"
            f"<b>👥 Groups required:</b>   {n_gr}\n\n"
            f"<b>👤 Total users seen:</b>  {n_users}\n\n"
            f"<b>🔐 Cache (TTL {CACHE_TTL}s):</b>\n"
            f"   ✅ Verified:     {cached_ok}\n"
            f"   ❌ Not verified: {cached_no}\n"
            f"   📦 Total:        {len(_membership_cache)}\n\n"
            f"<b>📨 Pending join requests:</b> {len(pending_join_requests)}"
        )

        await query.edit_message_text(
            text,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Back", callback_data="admin_back")
            ]]),
        )
    except Exception as e:
        log.error("_admin_stats error: %s", e)


async def _admin_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        query = update.callback_query
        await query.answer()
        _admin_state[OWNER_ID] = {"action": "broadcast", "step": "waiting_message"}
        await query.edit_message_text(
            "<b>📣 Broadcast</b>\n\n"
            "Send the message you want to broadcast to all users in the database.\n"
            "HTML formatting is supported.\n\n"
            "Send /cancel to abort.",
            parse_mode=ParseMode.HTML,
        )
    except Exception as e:
        log.error("_admin_broadcast error: %s", e)


async def _admin_clear_cache(update: Update,
                              context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        query = update.callback_query
        await query.answer()
        count = len(_membership_cache)
        _membership_cache.clear()
        await query.edit_message_text(
            f"✅ Cleared <b>{count}</b> cache entries.\n"
            "All users will be re-checked on their next interaction.",
            parse_mode=ParseMode.HTML,
            reply_markup=build_admin_keyboard(),
        )
    except Exception as e:
        log.error("_admin_clear_cache error: %s", e)


async def _admin_close(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        query = update.callback_query
        await query.answer()
        _admin_state.pop(OWNER_ID, None)
        await query.edit_message_text("✅ Admin panel closed.")
    except Exception as e:
        log.error("_admin_close error: %s", e)


# =============================================================================
#  ADMIN  MESSAGE  HANDLER  (free-text input for add / broadcast flows)
# =============================================================================

async def admin_message_handler(update: Update,
                                  context: ContextTypes.DEFAULT_TYPE) -> None:
    """Routes free-text owner input to the correct admin flow handler."""
    try:
        state  = _admin_state.get(OWNER_ID, {})
        action = state.get("action", "")
        text   = (update.message.text or "").strip()

        if action == "add_chat":
            await _handle_add_chat_step(update, context, state, text)
        elif action == "broadcast":
            await _handle_broadcast(update, context, text)
        else:
            _admin_state.pop(OWNER_ID, None)

    except Exception as e:
        log.error("admin_message_handler error: %s", e)


async def _handle_add_chat_step(update: Update,
                                  context: ContextTypes.DEFAULT_TYPE,
                                  state: Dict,
                                  text: str) -> None:
    """
    Multi-step add-chat flow.
      Step 1 (waiting_id)           → fetch chat info, ask join_request flag.
      Step 2 (waiting_join_request) → save entry to config.
    """
    try:
        step       = state.get("step", "waiting_id")
        chat_type  = state.get("type", "channel")
        icon       = "📢" if chat_type == "channel" else "👥"
        type_label = "Channel" if chat_type == "channel" else "Group"

        # ── STEP 1: Receive chat ID / username ─────────────────────────
        if step == "waiting_id":
            raw: Any = text
            if text.lstrip("-").isdigit():
                raw = int(text)

            try:
                chat_info = await context.bot.get_chat(raw)
            except TelegramError as e:
                await update.message.reply_text(
                    f"❌ Could not fetch info for <code>{text}</code>:\n<i>{e}</i>\n\n"
                    "Make sure the bot is an admin in that chat, then try again.\n"
                    "Send /cancel to abort.",
                    parse_mode=ParseMode.HTML,
                )
                return  # Keep state — let owner retry

            invite_link = ""
            try:
                if chat_info.invite_link:
                    invite_link = chat_info.invite_link
                else:
                    link_obj = await context.bot.export_chat_invite_link(chat_info.id)
                    invite_link = link_obj if isinstance(link_obj, str) else ""
            except TelegramError:
                pass

            config = load_config()
            existing_ids = [c.get("chat_id") for c in config.get("chats", [])]
            if chat_info.id in existing_ids:
                await update.message.reply_text(
                    f"⚠️ <b>{chat_info.title}</b> is already in the verification list.",
                    parse_mode=ParseMode.HTML,
                )
                _admin_state.pop(OWNER_ID, None)
                return

            _admin_state[OWNER_ID] = {
                "action": "add_chat",
                "type":   chat_type,
                "step":   "waiting_join_request",
                "pending_entry": {
                    "chat_id":     chat_info.id,
                    "title":       chat_info.title or str(chat_info.id),
                    "type":        chat_type,
                    "username":    chat_info.username or "",
                    "invite_link": invite_link,
                },
            }

            await update.message.reply_text(
                f"{icon} <b>{chat_info.title}</b> found!\n"
                f"ID: <code>{chat_info.id}</code>\n"
                f"Invite link: {invite_link or 'none (private)'}\n\n"
                f"<b>Does this {type_label} use Join Requests?</b>\n"
                "Reply <b>yes</b> or <b>no</b>.\n\n"
                "Send /cancel to abort.",
                parse_mode=ParseMode.HTML,
            )

        # ── STEP 2: Receive join_request answer ─────────────────────────
        elif step == "waiting_join_request":
            answer = text.lower().strip()
            if answer not in ("yes", "no", "y", "n"):
                await update.message.reply_text(
                    "Please reply <b>yes</b> or <b>no</b>.\n"
                    "Does this chat use Join Requests?",
                    parse_mode=ParseMode.HTML,
                )
                return  # Keep state

            join_request = answer in ("yes", "y")
            entry        = state.get("pending_entry", {})
            entry["join_request"] = join_request

            config = load_config()
            config.setdefault("chats", []).append(entry)
            save_config(config)
            _membership_cache.clear()
            _admin_state.pop(OWNER_ID, None)

            jr_label = ("✅ Yes (join requests enabled)"
                        if join_request else "❌ No (direct membership)")
            icon_out = "📢" if entry.get("type") == "channel" else "👥"

            await update.message.reply_text(
                f"✅ Added {icon_out} <b>{entry['title']}</b>\n"
                f"ID: <code>{entry['chat_id']}</code>\n"
                f"Invite link: {entry['invite_link'] or 'none (private)'}\n"
                f"Join Requests: {jr_label}",
                parse_mode=ParseMode.HTML,
                reply_markup=build_admin_keyboard(),
            )

        else:
            _admin_state.pop(OWNER_ID, None)

    except Exception as e:
        log.error("_handle_add_chat_step error: %s", e)
        _admin_state.pop(OWNER_ID, None)
        await update.message.reply_text(
            f"⚠️ An error occurred: <i>{e}</i>\nPlease try again.",
            parse_mode=ParseMode.HTML,
        )


async def _handle_broadcast(update: Update,
                              context: ContextTypes.DEFAULT_TYPE,
                              message_text: str) -> None:
    try:
        users = load_users()
        total = len(users)

        if total == 0:
            await update.message.reply_text("No users in the database yet.")
            _admin_state.pop(OWNER_ID, None)
            return

        progress_msg = await update.message.reply_text(
            f"📣 Broadcasting to {total} user(s)…"
        )

        sent = failed = 0
        for uid_str in users:
            try:
                await context.bot.send_message(
                    chat_id=int(uid_str),
                    text=message_text,
                    parse_mode=ParseMode.HTML,
                )
                sent += 1
            except TelegramError:
                failed += 1
            await asyncio.sleep(0.05)  # Respect rate limits

        _admin_state.pop(OWNER_ID, None)

        try:
            await progress_msg.edit_text(
                f"📣 <b>Broadcast complete!</b>\n"
                f"✅ Sent: {sent}\n"
                f"❌ Failed: {failed}",
                parse_mode=ParseMode.HTML,
            )
        except TelegramError:
            await update.message.reply_text(
                f"📣 Broadcast done — ✅ {sent} sent, ❌ {failed} failed."
            )
    except Exception as e:
        log.error("_handle_broadcast error: %s", e)


# =============================================================================
#  JOIN  REQUEST  HANDLER
# =============================================================================

async def handle_join_request(update: Update,
                               context: ContextTypes.DEFAULT_TYPE) -> None:
    """Track pending join requests. Do NOT auto-approve."""
    try:
        req = update.chat_join_request
        if not req:
            return
        key = (req.chat.id, req.from_user.id)
        pending_join_requests.add(key)
        log.info("Join request tracked: user=%s chat=%s", req.from_user.id, req.chat.id)
        record_user(req.from_user)
    except Exception as e:
        log.error("handle_join_request error: %s", e)


async def handle_chat_member_update(update: Update,
                                     context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Detect when a user leaves / is kicked from a required chat.
    Invalidate cache and remove from pending_join_requests.
    """
    try:
        cmu = update.chat_member
        if not cmu:
            return

        new_status = cmu.new_chat_member.status
        user_id    = cmu.new_chat_member.user.id
        chat_id    = cmu.chat.id

        left_statuses = {ChatMember.LEFT, ChatMember.BANNED, "kicked"}

        if new_status in left_statuses:
            log.info(
                "User %s left/kicked from chat %s — invalidating cache",
                user_id, chat_id,
            )
            _cache_invalidate(user_id)
            pending_join_requests.discard((chat_id, user_id))
    except Exception as e:
        log.error("handle_chat_member_update error: %s", e)


# =============================================================================
#  BOT.PY  LOADER  —  ProxyApp  (THE KEY FIX)
#
#  ProxyApp forwards EVERY add_handler() call DIRECTLY to the real Application
#  in group 1.  Handlers are registered ONCE on the CORRECT app object with
#  the CORRECT Application context.  Nothing is captured, re-added, or wrapped.
#
#  Handling async main() in bot.py:
#    1. Try a fresh asyncio event loop (best case, no loop running yet).
#    2. Fall back to nest_asyncio if available (loop already running).
#    3. Fall back to a daemon thread with its own loop.
# =============================================================================

def load_bot_py(real_app: Application) -> None:
    """
    Load bot.py and register its handlers DIRECTLY on real_app via ProxyApp.
    Populates the global _original_bot_start.
    """
    global _original_bot_start

    bot_py_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "bot.py"
    )
    if not os.path.exists(bot_py_path):
        print("[main.py] ⚠  bot.py not found — standalone verification mode.")
        return

    print("[main.py] Loading bot.py via ProxyApp…")

    handler_count = 0

    # ──────────────────────────────────────────────────────────────────────────
    class ProxyApp:
        """
        Proxy that sits in front of real_app.
        bot.py calls proxy.add_handler() → handler goes DIRECTLY to real_app.
        This means handlers have the correct Application context from day one.
        """

        def add_handler(self, handler, group: int = 0) -> None:
            nonlocal handler_count
            global _original_bot_start  # THIS IS THE FIX — it's a module-level variable

            # Intercept /start — save callback, do NOT add to group 1
            if isinstance(handler, CommandHandler):
                if "start" in getattr(handler, "commands", set()):
                    _original_bot_start = handler.callback
                    print(f"  ├ CommandHandler: /start  ← captured as _original_bot_start")
                    return

            # ALL other handlers → DIRECTLY to real_app in group 1
            real_app.add_handler(handler, group=1)
            handler_count += 1

            # Pretty log
            if isinstance(handler, CommandHandler):
                cmds = ", /".join(sorted(handler.commands))
                print(f"  ├ CommandHandler: /{cmds}  → group 1")
            elif isinstance(handler, CallbackQueryHandler):
                pat = getattr(handler, "pattern", None)
                pat_str = (pat.pattern if hasattr(pat, "pattern")
                           else str(pat) if pat else "*")
                print(f"  ├ CallbackQueryHandler: {pat_str}  → group 1")
            elif isinstance(handler, MessageHandler):
                print(f"  ├ MessageHandler: {handler.filters}  → group 1")
            else:
                print(f"  ├ {type(handler).__name__}  → group 1")

        def add_error_handler(self, callback, *args, **kwargs) -> None:
            real_app.add_error_handler(callback, *args, **kwargs)
            print("  ├ ErrorHandler  → forwarded to real app")

        def run_polling(self, *args, **kwargs) -> None:
            pass  # BLOCKED — we control startup

        def run_webhook(self, *args, **kwargs) -> None:
            pass  # BLOCKED

        @property
        def bot(self):
            return real_app.bot

        @property
        def job_queue(self):
            return real_app.job_queue

        @property
        def bot_data(self):
            return real_app.bot_data

        @property
        def user_data(self):
            return real_app.user_data

        @property
        def chat_data(self):
            return real_app.chat_data

        @property
        def update_queue(self):
            return real_app.update_queue

        def __getattr__(self, name: str):
            # Forward any attribute not explicitly defined above
            try:
                return getattr(real_app, name)
            except AttributeError:
                return lambda *a, **kw: None

    # ──────────────────────────────────────────────────────────────────────────
    class FakeBuilder:
        """
        Fake builder that returns our ProxyApp instead of creating a new
        Application.  bot.py calls Application.builder().token(X).build()
        and gets the ProxyApp — its token call is ignored (we use ours).
        """

        def __init__(self):
            self._proxy = ProxyApp()

        def token(self, t):
            return self  # Discard bot.py's token — we use BOT_TOKEN

        def build(self) -> ProxyApp:
            return self._proxy

        def __getattr__(self, name: str):
            # Absorb all other builder methods (persistence, defaults, etc.)
            return lambda *a, **kw: self

    # ──────────────────────────────────────────────────────────────────────────

    proxy   = FakeBuilder()._proxy
    orig_builder = Application.builder

    try:
        Application.builder = staticmethod(lambda: FakeBuilder())

        # Clear stale module cache
        for mod_name in list(sys.modules.keys()):
            if mod_name in ("bot",):
                del sys.modules[mod_name]

        spec   = importlib.util.spec_from_file_location("bot", bot_py_path)
        module = importlib.util.module_from_spec(spec)
        sys.modules["bot"] = module

        # ── Execute module body ──────────────────────────────────────────
        try:
            spec.loader.exec_module(module)
        except SystemExit:
            pass
        except Exception as load_err:
            print(f"[main.py] ⚠  bot.py module body error: {load_err}")

        # ── If handlers still zero, try calling setup functions ──────────
        if handler_count == 0 and _original_bot_start is None:
            for fn_name in ("main", "setup", "start_bot", "run", "init",
                            "register_handlers", "configure"):
                fn = getattr(module, fn_name, None)
                if not callable(fn):
                    continue

                print(f"[main.py]   Trying {fn_name}()…")

                try:
                    result = fn()
                except SystemExit:
                    result = None
                except TypeError:
                    # Function needs arguments — try with proxy
                    try:
                        result = fn(proxy)
                    except Exception:
                        result = None
                except Exception as fn_err:
                    print(f"[main.py] ⚠  {fn_name}() error: {fn_err}")
                    result = None

                # ── Handle async functions ───────────────────────────────
                if asyncio.iscoroutine(result):
                    print(f"[main.py]   {fn_name}() is async — running coroutine…")
                    ran = False

                    # Approach 1: fresh event loop (no loop running)
                    try:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        loop.run_until_complete(result)
                        loop.close()
                        ran = True
                    except RuntimeError as re:
                        if "running event loop" not in str(re).lower():
                            print(f"[main.py] ⚠  new_event_loop approach failed: {re}")
                        # result coroutine may be partially consumed — get a fresh one
                        try:
                            result = fn()
                        except Exception:
                            result = None

                    # Approach 2: nest_asyncio (loop already running)
                    if not ran and asyncio.iscoroutine(result):
                        try:
                            import nest_asyncio
                            nest_asyncio.apply()
                            asyncio.get_event_loop().run_until_complete(result)
                            ran = True
                        except ImportError:
                            pass
                        except Exception as na_err:
                            print(f"[main.py] ⚠  nest_asyncio approach failed: {na_err}")
                            try:
                                result = fn()
                            except Exception:
                                result = None

                    # Approach 3: daemon thread with its own loop
                    if not ran and asyncio.iscoroutine(result):
                        import threading
                        coro_ref = [result]
                        errors   = []

                        def _thread_runner():
                            new_loop = asyncio.new_event_loop()
                            asyncio.set_event_loop(new_loop)
                            try:
                                new_loop.run_until_complete(coro_ref[0])
                            except Exception as te:
                                errors.append(te)
                            finally:
                                new_loop.close()

                        t = threading.Thread(target=_thread_runner, daemon=True)
                        t.start()
                        t.join(timeout=15)
                        if errors:
                            print(f"[main.py] ⚠  thread approach error: {errors[0]}")
                        ran = True

                if handler_count > 0 or _original_bot_start is not None:
                    print(f"[main.py] ✓ Handlers loaded via {fn_name}()")
                    break

        # ── Scan module-level attributes for stray handler objects ───────
        for attr_name in dir(module):
            try:
                obj = getattr(module, attr_name, None)
                if not isinstance(obj, BaseHandler):
                    continue
                # Skip if already registered (proxy.add_handler already ran for it)
                # We detect duplicates by checking real_app's group 1
                if isinstance(obj, CommandHandler):
                    if "start" in getattr(obj, "commands", set()):
                        if _original_bot_start is None:
                            _original_bot_start = obj.callback
                            print(f"  ├ /start found as module attr '{attr_name}'  ← captured")
                        continue
                # Only add if not already in group 1
                g1_handlers = real_app.handlers.get(1, [])
                if obj not in g1_handlers:
                    real_app.add_handler(obj, group=1)
                    handler_count += 1
                    print(f"  ├ {type(obj).__name__} from module attr '{attr_name}'  → group 1")
            except Exception:
                pass

        # ── Final fallback: scan for a start function by name ────────────
        if _original_bot_start is None:
            for fn_name in ("start", "start_command", "cmd_start", "handle_start",
                            "start_handler"):
                fn = getattr(module, fn_name, None)
                if callable(fn) and asyncio.iscoroutinefunction(fn):
                    _original_bot_start = fn
                    print(f"  ├ /start found via function name '{fn_name}'  ← captured")
                    break

    except Exception as e:
        print(f"[main.py] ✗ bot.py load failed: {e}")

    finally:
        Application.builder = orig_builder

    total = handler_count + (1 if _original_bot_start else 0)
    print(f"\n[main.py] ══ bot.py: {total} handler(s) loaded "
          f"({handler_count} in group 1"
          + (", /start captured" if _original_bot_start else "")
          + ") ══\n")

    if total == 0:
        print("[main.py] ⚠  WARNING: 0 handlers loaded from bot.py.")
        print("[main.py]   Possible causes:")
        print("[main.py]   • bot.py uses Application.builder().token().build() at module")
        print("[main.py]     level or inside main()/setup() with no required arguments.")
        print("[main.py]   • If bot.py has an async main(), make sure it runs setup logic")
        print("[main.py]     before calling run_polling() (which is blocked by ProxyApp).")
        print("[main.py]   The verification system will still work in standalone mode.")


# =============================================================================
#  BUILD  APPLICATION
# =============================================================================

def build_application() -> Application:
    """
    Build the Application with three handler groups:
      -1 : global_verification_check  (TypeHandler — runs first, every update)
       0 : system handlers
       1 : bot.py handlers — added DIRECTLY via ProxyApp.add_handler()
    """
    app = Application.builder().token(BOT_TOKEN).build()

    # ── GROUP 1: Load bot.py first — handlers go straight to group 1 ────────
    load_bot_py(app)

    # ── GROUP -1: Global verification gate (runs before everything) ──────────
    app.add_handler(TypeHandler(Update, global_verification_check), group=-1)

    # ── GROUP 0: System handlers ─────────────────────────────────────────────
    app.add_handler(CommandHandler("start",  start_command),  group=0)
    app.add_handler(CommandHandler("admin",  admin_command),  group=0)
    app.add_handler(CommandHandler("cancel", cancel_command), group=0)

    app.add_handler(
        CallbackQueryHandler(handle_verify_callback,  pattern="^verify_membership$"),
        group=0,
    )
    app.add_handler(
        CallbackQueryHandler(handle_no_link_callback, pattern="^no_link$"),
        group=0,
    )
    app.add_handler(
        CallbackQueryHandler(handle_admin_callback_router, pattern="^admin_"),
        group=0,
    )

    app.add_handler(ChatJoinRequestHandler(handle_join_request),           group=0)
    app.add_handler(
        ChatMemberHandler(handle_chat_member_update, ChatMemberHandler.CHAT_MEMBER),
        group=0,
    )

    # Admin free-text input (fires only when owner has active admin state)
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.ChatType.PRIVATE & admin_flow_filter,
            admin_message_handler,
        ),
        group=0,
    )

    return app


# =============================================================================
#  STARTUP  BANNER
# =============================================================================

def print_banner(app: Application) -> None:
    try:
        config = load_config()
        chats  = config.get("chats", [])
        n_ch   = sum(1 for c in chats if c.get("type") == "channel")
        n_gr   = sum(1 for c in chats if c.get("type") == "group")

        gc: Dict[int, int] = {}
        for grp, hlist in app.handlers.items():
            gc[grp] = len(hlist)

        sep = "═" * 58
        print(f"\n{sep}")
        print("  Telegram Verification Wrapper v5.0 (ProxyApp)  —  LIVE")
        print(sep)
        print()
        print("[main.py] Handler groups:")
        print(f"  Group -1 : Global verification pre-check   ({gc.get(-1, 0)} handler)")
        print(f"  Group  0 : System (start/admin/verify/…)   ({gc.get(0, 0)} handlers)")
        print(f"  Group  1 : bot.py handlers (ProxyApp)      ({gc.get(1, 0)} handlers)")
        print()
        print(f"[main.py] Channels required   : {n_ch}")
        print(f"[main.py] Groups required     : {n_gr}")
        print(f"[main.py] Bot.py /start       : "
              f"{'captured ✅' if _original_bot_start else 'not found (standalone)'}")
        print(f"[main.py] drop_pending_updates: True")
        print()
        print(sep)
        print("  Bot is online!  Press Ctrl+C to stop.")
        print(f"{sep}\n")
    except Exception as e:
        print(f"[main.py] Banner error: {e}")


# =============================================================================
#  MAIN
# =============================================================================

def main() -> None:
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("ERROR: Set BOT_TOKEN in main.py before running.")
        sys.exit(1)
    if OWNER_ID == 123456789:
        print("WARNING: OWNER_ID is still the default (123456789).")
        print("         Update it to your actual Telegram user ID.")

    app = build_application()
    print_banner(app)

    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
