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

# How long (seconds) a membership check is cached
CACHE_TTL = 300  # 5 minutes

# =============================================================================
#  LOGGING
# =============================================================================

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("main")

# Silence noisy telegram library loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)
logging.getLogger("telegram.ext").setLevel(logging.WARNING)

# =============================================================================
#  CONFIG  &  USER  DATA
#  FIX 5: Single "chats" list with "type" + "join_request" fields.
# =============================================================================

def _migrate_config(cfg: Dict) -> Dict:
    """
    Migrate old v3.0 config (separate 'channels' / 'groups' keys)
    to the new unified 'chats' list format.
    """
    if "chats" in cfg:
        return cfg  # Already new format

    chats: List[Dict] = []

    for ch in cfg.get("channels", []):
        entry = dict(ch)
        entry.setdefault("type", "channel")
        entry.setdefault("join_request", False)
        # Rename 'id' → 'chat_id' if needed
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

# {user_id: {"result": bool, "ts": float}}
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
#  FIX 3: pending_join_requests set for in-memory join request tracking.
# =============================================================================

# Set of (chat_id, user_id) tuples for pending join requests
pending_join_requests: Set[Tuple[int, int]] = set()

# =============================================================================
#  VERIFICATION  LOGIC
#  FIX 3: check_membership_for_chat now accepts is_join_request_chat flag.
# =============================================================================

MEMBER_STATUSES = {
    ChatMember.MEMBER,
    ChatMember.ADMINISTRATOR,
    ChatMember.OWNER,
}


async def check_membership_for_chat(
    bot: Bot,
    user_id: int,
    chat_id: Any,
    is_join_request_chat: bool = False,
) -> bool:
    """
    Return True if user is an active member of chat_id.
    If is_join_request_chat=True, also accepts pending join requests.
    """
    try:
        member = await bot.get_chat_member(chat_id=chat_id, user_id=user_id)
        if member.status in MEMBER_STATUSES:
            return True
        # "restricted" users are still in the chat
        if member.status == "restricted":
            return True
        # Check pending join requests as fallback
        if is_join_request_chat and (chat_id, user_id) in pending_join_requests:
            return True
        return False
    except TelegramError:
        # API failed — fall back to join request tracking
        if is_join_request_chat and (chat_id, user_id) in pending_join_requests:
            return True
        return False


async def is_user_currently_verified(user_id: int, bot: Bot) -> bool:
    """
    Check if user is a member of ALL configured chats.
    Uses a 5-minute cache.
    """
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
            is_jr_chat = bool(chat.get("join_request", False))
            ok = await check_membership_for_chat(bot, user_id, chat_id,
                                                  is_join_request_chat=is_jr_chat)
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
                buttons.append([InlineKeyboardButton(f"{icon} {label} (no link)",
                                                      callback_data="no_link")])
        buttons.append([InlineKeyboardButton("✅ I've Joined — Verify Me",
                                              callback_data="verify_membership")])
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
            await update.message.reply_text(text, parse_mode=ParseMode.HTML,
                                             reply_markup=keyboard)
        elif update.callback_query:
            await update.callback_query.message.reply_text(text, parse_mode=ParseMode.HTML,
                                                            reply_markup=keyboard)
    except Exception as e:
        log.error("send_join_message error: %s", e)


# =============================================================================
#  GLOBAL  PRE-CHECK  (group = -1)
# =============================================================================

async def global_verification_check(update: Update,
                                      context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Runs BEFORE every single handler (group -1).
    Blocks unverified users by raising ApplicationHandlerStop.
    Owner, /start, /cancel, and our own callbacks always pass.
    """
    try:
        user = update.effective_user
        if not user:
            return  # Channel posts, service messages — pass through

        user_id = user.id

        # ── Owner always passes ──────────────────────────────────────────
        if user_id == OWNER_ID:
            return

        # ── No chats configured → no gate ───────────────────────────────
        config = load_config()
        if not config.get("chats"):
            return

        # ── Whitelist system commands so users can always reach /start ───
        if update.message and update.message.text:
            cmd_text = update.message.text.strip()
            if cmd_text.startswith("/"):
                cmd = cmd_text.split()[0].lower().lstrip("/").split("@")[0]
                if cmd in ("start", "cancel"):
                    return

        # ── Whitelist our own callback data ─────────────────────────────
        if update.callback_query:
            data = update.callback_query.data or ""
            if data in ("verify_membership", "no_link") or data.startswith("admin_"):
                return

        # ── Membership check (cached) ────────────────────────────────────
        verified = await is_user_currently_verified(user_id, context.bot)
        if verified:
            record_user(user)
            return

        # ── NOT VERIFIED — block ─────────────────────────────────────────
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

# {owner_id: {"action": str, "step": str, ...}}
_admin_state: Dict[int, Dict] = {}


class AdminFlowFilter(filters.UpdateFilter):
    """
    Passes only when:
      - The user is the owner, AND
      - There is an active admin conversation state.
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
#  ORIGINAL  BOT.PY  START  CALLBACK  (populated by load_bot_py)
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
    """
    FIX 8: Beautiful HTML admin panel.
    Called on owner /start (after bot.py start) and via /admin command.
    """
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
            f"👥 <b>Groups:</b> {n_gr}\n"
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
            msg = update.message or (update.callback_query.message
                                     if update.callback_query else None)
            if msg:
                await msg.reply_text(text, parse_mode=ParseMode.HTML, reply_markup=kb)
    except Exception as e:
        log.error("show_admin_panel error: %s", e)


# =============================================================================
#  /start  COMMAND
#  FIX 1: Owner sees bot.py /start FIRST, then admin panel.
#  FIX 6: (drop_pending_updates handled in run_polling call)
# =============================================================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Entry point for all users.
      Owner      → bot.py /start (if exists) THEN admin panel.
      Verified   → bot.py /start (if exists) THEN verified message.
      Unverified → join message.
    """
    try:
        user = update.effective_user
        if not user:
            return

        record_user(user)

        # ── OWNER ────────────────────────────────────────────────────────
        if user.id == OWNER_ID:
            # Step 1: Call bot.py's original /start (show bot features)
            if _original_bot_start:
                try:
                    await _original_bot_start(update, context)
                except Exception as e:
                    log.warning("original /start (owner) failed: %s", e)
            # Step 2: Show admin panel as separate message
            await show_admin_panel(update, context)
            return

        # ── REGULAR USERS ─────────────────────────────────────────────────
        config = load_config()
        chats  = config.get("chats", [])

        if not chats:
            # No gate — just call bot.py start or send welcome
            if _original_bot_start:
                try:
                    await _original_bot_start(update, context)
                    return
                except Exception as e:
                    log.warning("original /start (no gate) failed: %s", e)
            await update.message.reply_text(
                "<b>👋 Welcome!</b>\n\nNo access restrictions are configured.\n"
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
#  FIX 2: After successful verify → directly call original bot start.
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

            # Delete the join/verify message
            try:
                await query.message.delete()
            except Exception:
                pass

            # FIX 2: Directly call bot.py's original /start
            if _original_bot_start:
                try:
                    await _original_bot_start(update, context)
                except Exception as e:
                    log.warning("original /start after verify failed: %s", e)
                    await context.bot.send_message(
                        chat_id=user.id,
                        text=(
                            "✅ <b>Verified!</b> You now have full access.\n"
                            "Send /start to begin."
                        ),
                        parse_mode=ParseMode.HTML,
                    )
            else:
                await context.bot.send_message(
                    chat_id=user.id,
                    text="✅ <b>Verified!</b> You now have full access.",
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
    """User pressed a chat button that has no invite link configured."""
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
            "admin_add_channel":  _admin_add_channel,
            "admin_add_group":    _admin_add_group,
            "admin_remove":       _admin_remove,
            "admin_view":         _admin_view,
            "admin_stats":        _admin_stats,
            "admin_broadcast":    _admin_broadcast,
            "admin_clear_cache":  _admin_clear_cache,
            "admin_close":        _admin_close,
            "admin_back":         lambda u, c: show_admin_panel(u, c, edit=True),
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

# -- Add Channel / Group (FIX 4: multi-step with join_request question) -------

async def _admin_add_channel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        query = update.callback_query
        await query.answer()
        _admin_state[OWNER_ID] = {"action": "add_chat", "type": "channel",
                                   "step": "waiting_id"}
        await query.edit_message_text(
            "<b>➕ Add Channel</b>\n\n"
            "Send the channel's <b>username</b> (e.g. <code>@mychannel</code>) "
            "or its numeric <b>chat ID</b> (e.g. <code>-1001234567890</code>).\n\n"
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
            "Send the group's <b>username</b> (e.g. <code>@mygroup</code>) "
            "or its numeric <b>chat ID</b> (e.g. <code>-1001234567890</code>).\n\n"
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
                f"{icon} {label}",
                callback_data=f"admin_remove_confirm_{i}",
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

        data  = query.data  # admin_remove_confirm_<index>
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
            "Send the message you want broadcast to all users in the database.\n"
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
    FIX 4: Multi-step add-chat flow.
      Step 1: 'waiting_id'          → fetch info, ask join_request question.
      Step 2: 'waiting_join_request' → save entry to config.
    """
    try:
        step      = state.get("step", "waiting_id")
        chat_type = state.get("type", "channel")
        icon      = "📢" if chat_type == "channel" else "👥"
        type_label = "Channel" if chat_type == "channel" else "Group"

        # ── STEP 1: Receive chat ID / username ───────────────────────────
        if step == "waiting_id":
            raw: Any = text
            if text.lstrip("-").isdigit():
                raw = int(text)

            # Fetch chat info from Telegram
            try:
                chat_info = await context.bot.get_chat(raw)
            except TelegramError as e:
                await update.message.reply_text(
                    f"❌ Could not fetch info for <code>{text}</code>:\n<i>{e}</i>\n\n"
                    "Make sure the bot is an admin in that chat, then try again.\n"
                    "Send /cancel to abort.",
                    parse_mode=ParseMode.HTML,
                )
                return  # Keep state — let owner try again

            # Fetch invite link
            invite_link = ""
            try:
                if chat_info.invite_link:
                    invite_link = chat_info.invite_link
                else:
                    link_obj = await context.bot.export_chat_invite_link(chat_info.id)
                    invite_link = link_obj if isinstance(link_obj, str) else ""
            except TelegramError:
                pass  # Private channels — OK, we'll store empty

            # Check for duplicate
            config = load_config()
            existing_ids = [c.get("chat_id") for c in config.get("chats", [])]
            if chat_info.id in existing_ids:
                await update.message.reply_text(
                    f"⚠️ <b>{chat_info.title}</b> is already in the verification list.",
                    parse_mode=ParseMode.HTML,
                )
                _admin_state.pop(OWNER_ID, None)
                return

            # Advance to step 2 — store partial entry
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

        # ── STEP 2: Receive join_request answer ──────────────────────────
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

            jr_label = "✅ Yes (join requests enabled)" if join_request \
                       else "❌ No (direct membership)"

            icon = "📢" if entry.get("type") == "channel" else "👥"
            await update.message.reply_text(
                f"✅ Added {icon} <b>{entry['title']}</b>\n"
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
#  FIX 3: Track join requests in memory (do NOT auto-approve).
#  FIX 7: Leave/kick also removes from pending_join_requests.
# =============================================================================

async def handle_join_request(update: Update,
                               context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Track join requests — do NOT auto-approve.
    Admin must approve via the channel/group native interface.
    """
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
    Detect when a user leaves or is kicked from a required chat.
    Invalidate their cache AND discard from pending_join_requests.
    FIX 7: Also cleans pending_join_requests on leave/kick/ban.
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
                "User %s left/kicked from chat %s — invalidating cache and join requests",
                user_id, chat_id,
            )
            _cache_invalidate(user_id)
            # FIX 7: Remove from pending join requests too
            pending_join_requests.discard((chat_id, user_id))
    except Exception as e:
        log.error("handle_chat_member_update error: %s", e)


# =============================================================================
#  BOT.PY  LOADER  (FakeBuilder / FakeApp monkey-patch)
# =============================================================================

def _describe_handler(handler) -> str:
    """Human-readable description of a handler for the startup banner."""
    try:
        if isinstance(handler, CommandHandler):
            cmds = ", ".join(f"/{c}" for c in sorted(handler.commands))
            return f"CommandHandler: {cmds}"
        if isinstance(handler, CallbackQueryHandler):
            pat = getattr(handler, "pattern", None)
            pat_str = pat.pattern if hasattr(pat, "pattern") else str(pat) if pat else "*"
            return f"CallbackQueryHandler pattern={pat_str}"
        if isinstance(handler, MessageHandler):
            return f"MessageHandler: {handler.filters}"
        return type(handler).__name__
    except Exception:
        return type(handler).__name__


def load_bot_py(app: Application) -> None:
    """
    Load bot.py and capture its handlers onto our Application in group=1.

    Strategy:
      1. Monkey-patch Application.builder() → FakeBuilder → FakeApp
      2. FakeApp.add_handler() stores (handler, group) in `captured`
      3. FakeApp.run_polling() / run_webhook() are no-ops
      4. After bot.py executes, add captured handlers UNMODIFIED to real app in group=1
      5. Extract bot.py's /start callback → saved as _original_bot_start
      6. The global pre-check in group=-1 handles all verification — no wrapping needed

    Populates the global _original_bot_start.
    """
    global _original_bot_start

    if not os.path.exists("bot.py"):
        print("[main.py] ⚠  bot.py not found — running in standalone verification mode.")
        return

    print("[main.py] Loading bot.py…")
    captured: List[Tuple[Any, int]] = []

    # ── Fake classes ────────────────────────────────────────────────────────

    class FakeApp:
        def add_handler(self, handler, group: int = 0) -> None:
            captured.append((handler, group))

        def add_error_handler(self, *a, **kw) -> None:
            pass

        def run_polling(self, *a, **kw) -> None:
            pass

        def run_webhook(self, *a, **kw) -> None:
            pass

        def __getattr__(self, name):
            return lambda *a, **kw: None

    class FakeBuilder:
        def __init__(self):
            self._app = FakeApp()

        def token(self, t):
            return self

        def build(self) -> "FakeApp":
            return self._app

        def __getattr__(self, name):
            return lambda *a, **kw: self

    # ── Patch ────────────────────────────────────────────────────────────────

    orig_builder = Application.builder

    try:
        Application.builder = staticmethod(lambda: FakeBuilder())

        spec   = importlib.util.spec_from_file_location("bot", "bot.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["bot"] = module

        try:
            spec.loader.exec_module(module)
        except SystemExit:
            pass
        except Exception as load_err:
            print(f"[main.py] ⚠  Error executing bot.py module body: {load_err}")

        # Some bots define a main() / setup() / register_handlers() function
        for fn_name in ("main", "setup", "register_handlers"):
            fn = getattr(module, fn_name, None)
            if callable(fn):
                try:
                    sig    = inspect.signature(fn)
                    params = [p for p in sig.parameters.values()
                              if p.default is inspect.Parameter.empty]
                    if len(params) == 0:
                        fn()
                    elif len(params) == 1:
                        fn(FakeApp())
                    break
                except SystemExit:
                    pass
                except Exception as fn_err:
                    print(f"[main.py] ⚠  Error calling bot.py {fn_name}(): {fn_err}")

    finally:
        Application.builder = orig_builder

    # ── Process captured handlers ─────────────────────────────────────────────

    if not captured:
        print("[main.py] ⚠  WARNING: 0 handlers captured from bot.py.")
        print("[main.py]   Possible fixes:")
        print("[main.py]   • Your bot.py should call Application.builder() at module")
        print("[main.py]     level or in a main()/setup() function (0–1 args).")
        print("[main.py]   • Alternatively, define HANDLERS list in bot.py:")
        print("[main.py]       HANDLERS = [CommandHandler('help', help_fn), ...]")
        print("[main.py]   Verification system will still work standalone.")
        return

    print(f"[main.py] ══ bot.py: {len(captured)} handler(s) captured ══")

    for handler, orig_group in captured:
        desc = _describe_handler(handler)

        # Intercept bot.py's /start — save callback, do NOT re-add to group 1
        if isinstance(handler, CommandHandler) and "start" in handler.commands:
            _original_bot_start = handler.callback
            print(f"  ├ {desc}  ← captured as _original_bot_start")
            continue

        # Add completely UNMODIFIED to group 1
        app.add_handler(handler, group=1)
        print(f"  ├ {desc}")

    print()


# =============================================================================
#  BUILD  APPLICATION
# =============================================================================

def build_application() -> Application:
    """
    Construct the Application with three handler groups:
      -1 : global_verification_check   (TypeHandler — runs first)
       0 : system handlers
       1 : bot.py handlers (unmodified)
    """
    app = Application.builder().token(BOT_TOKEN).build()

    # ── GROUP 1: Load bot.py first (so its handlers are in group 1) ───────────
    load_bot_py(app)

    # ── GROUP -1: Global verification gate ────────────────────────────────────
    app.add_handler(TypeHandler(Update, global_verification_check), group=-1)

    # ── GROUP 0: System handlers ───────────────────────────────────────────────
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

    app.add_handler(ChatJoinRequestHandler(handle_join_request),          group=0)
    app.add_handler(
        ChatMemberHandler(handle_chat_member_update, ChatMemberHandler.CHAT_MEMBER),
        group=0,
    )

    # Admin free-text input (only fires when owner has active admin state)
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
        config  = load_config()
        chats   = config.get("chats", [])
        n_ch    = sum(1 for c in chats if c.get("type") == "channel")
        n_gr    = sum(1 for c in chats if c.get("type") == "group")

        group_counts: Dict[int, int] = {}
        for grp, handler_list in app.handlers.items():
            group_counts[grp] = len(handler_list)

        g_minus1 = group_counts.get(-1, 0)
        g0       = group_counts.get(0, 0)
        g1       = group_counts.get(1, 0)

        sep = "═" * 56
        print(f"\n{sep}")
        print("  Telegram Verification Wrapper v4.0 — Starting")
        print(sep)
        print()
        print("[main.py] Handler groups:")
        print(f"  Group -1 : Global verification pre-check  ({g_minus1} handler)")
        print(f"  Group  0 : System (start/admin/cancel/verify/callbacks)  ({g0} handlers)")
        print(f"  Group  1 : bot.py handlers (unmodified)  ({g1} handlers)")
        print()
        print(f"[main.py] Channels required : {n_ch}")
        print(f"[main.py] Groups required   : {n_gr}")
        print(f"[main.py] Bot.py /start      : {'captured ✅' if _original_bot_start else 'not found (standalone)'}")
        print(f"[main.py] drop_pending_updates: True")
        print()
        print(sep)
        print("  Bot is LIVE!  Press Ctrl+C to stop.")
        print(f"{sep}\n")
    except Exception as e:
        print(f"[main.py] Banner error: {e}")


# =============================================================================
#  MAIN
# =============================================================================

def main() -> None:
    # Validate configuration
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("ERROR: Please set BOT_TOKEN in main.py before running.")
        sys.exit(1)
    if OWNER_ID == 123456789:
        print("WARNING: OWNER_ID is still the default value (123456789).")
        print("         Make sure this matches your actual Telegram user ID.")

    app = build_application()
    print_banner(app)

    # FIX 6: drop_pending_updates=True
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
