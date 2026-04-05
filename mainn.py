import asyncio
import importlib.util
import json
import logging
import os
import sys
import time
from datetime import datetime
from typing import Optional

from telegram import (
    Bot,
    ChatMemberUpdated,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    BaseHandler,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ============================================
# CONFIGURATION — SET THESE
# ============================================
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
OWNER_ID = 123456789  # Your Telegram user ID (integer)

# ============================================
# LOGGING
# ============================================
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ============================================
# FILE PATHS
# ============================================
CONFIG_FILE = "config.json"
USERS_FILE = "users.json"

# ============================================
# IN-MEMORY STATE
# ============================================

# Pending join requests: set of (chat_id, user_id) tuples
# Tracked but NOT auto-approved.
pending_join_requests: set = set()

# Membership cache: {user_id: {"verified": bool, "ts": float}}
membership_cache: dict = {}
CACHE_TTL = 300  # 5 minutes

# Admin input state per owner
admin_state: dict = {}

# ============================================
# BOT.PY DYNAMIC LOADING — MONKEY-PATCH APPROACH
# ============================================
original_start_handler = None
original_handlers = []


def load_bot_py() -> bool:
    """
    Load bot.py by monkey-patching Application.builder() to intercept all
    handler registrations and prevent the bot from actually starting.

    Works with ANY typical bot.py structure — even the most common pattern:
        app = Application.builder().token(TOKEN).build()
        app.add_handler(CommandHandler("start", start))
        app.run_polling()

    Always restores original methods in finally block.
    """
    global original_start_handler, original_handlers

    captured_handlers = []

    # ---- Save original methods before patching ----
    orig_builder = Application.builder

    class FakeBuilder:
        """Intercepts Application.builder() chain — absorbs all chained calls."""
        def __init__(self):
            self._token = None

        def token(self, t):
            self._token = t
            return self

        def build(self):
            return FakeApp()

        def __getattr__(self, name):
            # Absorb any other builder methods:
            # .defaults(), .read_timeout(), .connection_pool_size(), etc.
            return lambda *a, **kw: self

    class FakeApp:
        """Fake Application that silently captures handlers without running."""

        def add_handler(self, handler, group=0):
            captured_handlers.append(handler)

        def add_error_handler(self, *args, **kwargs):
            pass

        def run_polling(self, *args, **kwargs):
            pass  # DO NOT actually start polling

        def run_webhook(self, *args, **kwargs):
            pass

        def __getattr__(self, name):
            # Absorb everything else: post_init, post_shutdown, etc.
            return lambda *a, **kw: None

    try:
        # ---- Apply monkey-patch ----
        Application.builder = staticmethod(lambda: FakeBuilder())

        bot_py_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "bot.py"
        )
        if not os.path.exists(bot_py_path):
            print("[main.py] bot.py not found — running standalone.")
            return False

        # Clear any previous import of bot/bot_module
        for mod_name in list(sys.modules.keys()):
            if mod_name in ("bot", "bot_module"):
                del sys.modules[mod_name]

        spec = importlib.util.spec_from_file_location("bot_module", bot_py_path)
        if spec is None or spec.loader is None:
            print("[main.py] Could not create module spec for bot.py")
            return False

        bot_module = importlib.util.module_from_spec(spec)
        sys.modules["bot_module"] = bot_module
        sys.modules["bot"] = bot_module

        # Execute the module — top-level code runs here.
        # If bot.py uses `if __name__ == "__main__": main()`, main() won't fire.
        # Our patched Application.builder() catches any top-level handler registration.
        spec.loader.exec_module(bot_module)  # type: ignore[attr-defined]

        # ---- Try calling main()/setup()/etc. if no handlers captured yet ----
        # This handles the common pattern where all setup is inside main()
        if not captured_handlers:
            for fn_name in ["main", "setup", "start_bot", "run", "init", "register"]:
                fn = getattr(bot_module, fn_name, None)
                if callable(fn):
                    try:
                        result = fn()
                        if asyncio.iscoroutine(result):
                            result.close()  # Don't run coroutines here
                    except SystemExit:
                        pass
                    except Exception as e:
                        logger.warning("bot.py %s() raised: %s", fn_name, e)
                    if captured_handlers:
                        break

        # ---- Also scan for module-level handler objects (rare but valid) ----
        for attr_name in dir(bot_module):
            try:
                obj = getattr(bot_module, attr_name, None)
                if isinstance(obj, BaseHandler) and obj not in captured_handlers:
                    captured_handlers.append(obj)
            except Exception:
                pass

        # ---- Extract /start handler callback ----
        for handler in captured_handlers:
            if isinstance(handler, CommandHandler):
                cmds = getattr(handler, "commands", set())
                if "start" in cmds:
                    original_start_handler = handler.callback
                    print(f"[main.py] ✓ Captured /start handler from bot.py")
                    break

        # Fallback: find start function by common name conventions
        if not original_start_handler:
            for fn_name in [
                "start", "start_command", "cmd_start",
                "handle_start", "start_handler",
            ]:
                fn = getattr(bot_module, fn_name, None)
                if callable(fn) and asyncio.iscoroutinefunction(fn):
                    original_start_handler = fn
                    print(
                        f"[main.py] ✓ Captured /start handler via name: {fn_name}"
                    )
                    break

        original_handlers = captured_handlers
        count = len(captured_handlers)

        print(f"\n[main.py] ══ bot.py loaded: {count} handler(s) captured ══")
        for h in captured_handlers:
            if isinstance(h, CommandHandler):
                cmds = ", /".join(sorted(h.commands))
                print(f"  ├ CommandHandler: /{cmds}")
            elif isinstance(h, MessageHandler):
                print(f"  ├ MessageHandler: {h.filters}")
            elif isinstance(h, CallbackQueryHandler):
                print(f"  ├ CallbackQueryHandler")
            else:
                print(f"  ├ {type(h).__name__}")
        print()

        return True

    except SyntaxError as e:
        print(f"[main.py] ✗ bot.py has syntax error: {e}")
        return False
    except Exception as e:
        print(f"[main.py] ✗ bot.py load failed: {e}")
        logger.error("bot.py load error: %s", e, exc_info=True)
        return False
    finally:
        # ALWAYS restore the original builder, no matter what
        try:
            Application.builder = orig_builder
        except Exception:
            pass


# ============================================
# CONFIG / USER PERSISTENCE
# ============================================

def load_config() -> dict:
    """Load config.json. Returns empty config on any error."""
    try:
        if os.path.exists(CONFIG_FILE):
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    return {"channels": []}
                return data
    except Exception as e:
        logger.error("load_config failed: %s", e)
    return {"channels": []}


def save_config(config: dict) -> bool:
    """Save config.json. Returns True on success."""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error("save_config failed: %s", e)
        return False


def load_users() -> dict:
    """Load users.json. Returns empty dict on any error."""
    try:
        if os.path.exists(USERS_FILE):
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if not isinstance(data, dict):
                    return {}
                return data
    except Exception as e:
        logger.error("load_users failed: %s", e)
    return {}


def save_users(users: dict) -> bool:
    """Save users.json. Returns True on success."""
    try:
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, indent=2, ensure_ascii=False)
        return True
    except Exception as e:
        logger.error("save_users failed: %s", e)
        return False


def track_user(user_id: int) -> None:
    """Record a user ID in users.json."""
    try:
        users = load_users()
        uid = str(user_id)
        if uid not in users:
            users[uid] = {"first_seen": time.time(), "verified": False}
        users[uid]["last_seen"] = time.time()
        save_users(users)
    except Exception as e:
        logger.error("track_user failed: %s", e)


def mark_user_verified(user_id: int) -> None:
    """Mark a user as verified in users.json."""
    try:
        users = load_users()
        uid = str(user_id)
        if uid not in users:
            users[uid] = {"first_seen": time.time()}
        users[uid]["verified"] = True
        users[uid]["verified_at"] = time.time()
        users[uid]["last_seen"] = time.time()
        save_users(users)
    except Exception as e:
        logger.error("mark_user_verified failed: %s", e)


# ============================================
# MEMBERSHIP / VERIFICATION
# ============================================

async def check_single_channel(user_id: int, channel: dict, bot: Bot) -> bool:
    """
    Check if user is a member of the given channel/group.
    Also checks pending_join_requests for join-request-gated chats.
    """
    chat_id = channel.get("chat_id")
    if not chat_id:
        return False

    try:
        chat_id_int = int(chat_id)
    except (ValueError, TypeError):
        logger.error("Invalid chat_id value: %s", chat_id)
        return False

    try:
        member = await bot.get_chat_member(chat_id=chat_id_int, user_id=user_id)
        status = member.status

        if status in ("member", "administrator", "creator"):
            return True
        if channel.get("type") == "group" and status == "restricted":
            return True
        if channel.get("join_request", False):
            if (chat_id_int, user_id) in pending_join_requests:
                return True

        return False

    except TelegramError as e:
        logger.error(
            "Membership check API error: chat=%s user=%s error=%s",
            chat_id, user_id, e,
        )
        try:
            if channel.get("join_request", False):
                if (int(chat_id), user_id) in pending_join_requests:
                    return True
        except Exception:
            pass
        return False
    except Exception as e:
        logger.error("check_single_channel unexpected error: %s", e, exc_info=True)
        return False


async def is_user_currently_verified(user_id: int, bot: Bot) -> bool:
    """
    Returns True if the user has joined ALL required channels/groups.
    Uses a 5-minute cache to avoid hammering the Telegram API.
    """
    try:
        cached = membership_cache.get(user_id)
        if cached and (time.time() - cached["ts"]) < CACHE_TTL:
            return cached["verified"]

        config = load_config()
        channels = config.get("channels", [])

        if not channels:
            membership_cache[user_id] = {"verified": True, "ts": time.time()}
            return True

        for channel in channels:
            if not await check_single_channel(user_id, channel, bot):
                membership_cache[user_id] = {"verified": False, "ts": time.time()}
                return False

        membership_cache[user_id] = {"verified": True, "ts": time.time()}
        return True

    except Exception as e:
        logger.error("is_user_currently_verified failed: %s", e, exc_info=True)
        return False


def invalidate_cache(user_id: int) -> None:
    """Remove a user from the membership cache."""
    membership_cache.pop(user_id, None)


# ============================================
# JOIN MESSAGE — BEAUTIFUL HTML VERSION
# ============================================

def build_join_keyboard(channels: list) -> InlineKeyboardMarkup:
    """Build inline keyboard with Join buttons + Verify button."""
    buttons = []
    for ch in channels:
        name = ch.get("name") or ch.get("username") or f"Chat {ch.get('chat_id', '?')}"
        invite_link = ch.get("invite_link") or ch.get("link")
        ch_type = ch.get("type", "channel")
        emoji = "📢" if ch_type == "channel" else "👥"
        label = f"{emoji} Join: {name}"
        if invite_link:
            buttons.append([InlineKeyboardButton(label, url=invite_link)])
        else:
            buttons.append(
                [InlineKeyboardButton(f"{label} (no link set)", callback_data="no_link")]
            )

    buttons.append(
        [InlineKeyboardButton("✅ Verify My Membership", callback_data="verify_membership")]
    )
    return InlineKeyboardMarkup(buttons)


def build_join_text(channels: list) -> str:
    """Build the HTML-formatted join requirement message."""
    lines = [
        "🔐 <b>VERIFICATION REQUIRED</b>",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "To use this bot, you must join:",
        "",
    ]
    for ch in channels:
        name = ch.get("name") or ch.get("username") or f"Chat {ch.get('chat_id', '?')}"
        ch_type = ch.get("type", "channel")
        emoji = "📢" if ch_type == "channel" else "👥"
        type_label = "Channel" if ch_type == "channel" else "Group"
        lines.append(f"  {emoji} <b>{type_label}:</b> {name}")

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        "After joining all above, tap <b>Verify</b> below:",
    ]
    return "\n".join(lines)


async def send_join_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the verification required message. Bulletproof — never crashes."""
    try:
        config = load_config()
        channels = config.get("channels", [])

        if update.callback_query:
            target = update.callback_query.message
        else:
            target = update.message

        if not target:
            logger.error("send_join_message: no message target found")
            return

        if not channels:
            try:
                await target.reply_text("Welcome! No verification required. Use /help for commands.")
            except Exception as e:
                logger.error("send_join_message reply (no channels) failed: %s", e)
            return

        text = build_join_text(channels)
        keyboard = build_join_keyboard(channels)

        try:
            if update.callback_query:
                await target.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            else:
                await target.reply_text(text, reply_markup=keyboard, parse_mode="HTML")
        except Exception as e:
            logger.warning("send_join_message edit/reply failed, retrying plain: %s", e)
            try:
                await target.reply_text(text, reply_markup=keyboard, parse_mode="HTML")
            except Exception as e2:
                logger.error("send_join_message plain reply also failed: %s", e2)

    except Exception as e:
        logger.error("send_join_message crashed: %s", e, exc_info=True)
        try:
            msg = (
                update.callback_query.message
                if update.callback_query
                else update.message
            )
            if msg:
                await msg.reply_text("Error loading verification. Please try /start again.")
        except Exception:
            pass


# ============================================
# ADMIN PANEL — BEAUTIFUL HTML VERSION
# ============================================

def build_admin_panel_text() -> str:
    """Build the HTML-formatted admin panel overview text."""
    config = load_config()
    channels = config.get("channels", [])
    users = load_users()

    channel_count = sum(1 for ch in channels if ch.get("type") == "channel")
    group_count = sum(1 for ch in channels if ch.get("type") == "group")
    user_count = len(users)
    verified_count = sum(1 for u in users.values() if u.get("verified"))

    return (
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "🤖 <b>Bot Status:</b> Online\n"
        f"📢 <b>Channels:</b> {channel_count}\n"
        f"👥 <b>Groups:</b> {group_count}\n"
        f"👤 <b>Users:</b> {user_count}  ✅ <b>Verified:</b> {verified_count}\n"
        "━━━━━━━━━━━━━━━━━━━━━━━\n"
        "\n"
        "🔧 <b>ADMIN PANEL</b>"
    )


def build_admin_keyboard() -> InlineKeyboardMarkup:
    """Build the admin panel inline keyboard."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Add Channel", callback_data="admin_add_channel"),
            InlineKeyboardButton("➕ Add Group", callback_data="admin_add_group"),
        ],
        [
            InlineKeyboardButton("📋 View All", callback_data="admin_view_all"),
            InlineKeyboardButton("🗑 Remove", callback_data="admin_remove"),
        ],
        [
            InlineKeyboardButton("📊 Statistics", callback_data="admin_stats"),
            InlineKeyboardButton("📣 Broadcast", callback_data="admin_broadcast"),
        ],
        [
            InlineKeyboardButton("🔄 Refresh Panel", callback_data="admin_panel"),
        ],
    ])


async def show_admin_panel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    name: str = "Admin",
) -> None:
    """Show the owner's beautiful admin panel."""
    try:
        text = f"Welcome back, <b>{name}</b>! 👑\n\n" + build_admin_panel_text()
        keyboard = build_admin_keyboard()

        msg = update.message or (
            update.callback_query.message if update.callback_query else None
        )
        if not msg:
            return

        try:
            if update.callback_query:
                await msg.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
            else:
                await msg.reply_text(text, reply_markup=keyboard, parse_mode="HTML")
        except Exception as e:
            logger.warning("show_admin_panel edit failed, trying reply: %s", e)
            try:
                await msg.reply_text(text, reply_markup=keyboard, parse_mode="HTML")
            except Exception as e2:
                logger.error("show_admin_panel reply also failed: %s", e2)

    except Exception as e:
        logger.error("show_admin_panel crashed: %s", e, exc_info=True)


async def admin_view_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show all configured channels/groups in a beautiful formatted panel."""
    try:
        config = load_config()
        channels = config.get("channels", [])
        msg = update.callback_query.message

        if not channels:
            text = (
                "📋 <b>CONFIGURED CHANNELS &amp; GROUPS</b>\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "No entries configured yet.\n"
                "Use the panel to add channels or groups.\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━"
            )
        else:
            lines = [
                "📋 <b>CONFIGURED CHANNELS &amp; GROUPS</b>",
                "",
                "━━━━━━━━━━━━━━━━━━━━━━━",
                "",
            ]
            for i, ch in enumerate(channels, 1):
                name = ch.get("name") or ch.get("username") or "Unnamed"
                ch_type = ch.get("type", "channel")
                chat_id = ch.get("chat_id", "?")
                invite_link = ch.get("invite_link", "Not set")
                jr = "Yes" if ch.get("join_request") else "No"
                emoji = "📢" if ch_type == "channel" else "👥"
                type_label = "CHANNEL" if ch_type == "channel" else "GROUP"

                lines.append(f"<b>{i}. {emoji} {type_label}:</b> {name}")
                lines.append(f"   ├ <b>ID:</b> <code>{chat_id}</code>")
                if invite_link and invite_link != "Not set":
                    lines.append(f"   ├ <b>Link:</b> {invite_link}")
                else:
                    lines.append(f"   ├ <b>Link:</b> Not set")
                lines.append(f"   └ <b>Join Request:</b> {jr}")
                lines.append("")

            lines += [
                "━━━━━━━━━━━━━━━━━━━━━━━",
                "",
                f"<b>Total:</b> {len(channels)} entr{'y' if len(channels) == 1 else 'ies'}",
            ]
            text = "\n".join(lines)

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Panel", callback_data="admin_panel")]
        ])

        try:
            await msg.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        except Exception as e:
            logger.error("admin_view_all display failed: %s", e)

    except Exception as e:
        logger.error("admin_view_all crashed: %s", e, exc_info=True)


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show a beautiful statistics panel."""
    try:
        users = load_users()
        config = load_config()
        channels = config.get("channels", [])

        total_users = len(users)
        verified_users = sum(1 for u in users.values() if u.get("verified"))
        pending_requests = len(pending_join_requests)
        channel_count = sum(1 for ch in channels if ch.get("type") == "channel")
        group_count = sum(1 for ch in channels if ch.get("type") == "group")
        cache_entries = len(membership_cache)
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        text = (
            "📊 <b>BOT STATISTICS</b>\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"👤 <b>Total Users</b>       : {total_users}\n"
            f"✅ <b>Verified Users</b>    : {verified_users}\n"
            f"⏳ <b>Pending Requests</b>  : {pending_requests}\n"
            f"📢 <b>Channels</b>          : {channel_count}\n"
            f"👥 <b>Groups</b>            : {group_count}\n"
            f"💾 <b>Cache Entries</b>     : {cache_entries}\n\n"
            "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🕐 <b>Updated:</b> {now_str}"
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back to Panel", callback_data="admin_panel")]
        ])

        msg = update.callback_query.message
        try:
            await msg.edit_text(text, reply_markup=keyboard, parse_mode="HTML")
        except Exception as e:
            logger.error("admin_stats display failed: %s", e)

    except Exception as e:
        logger.error("admin_stats crashed: %s", e, exc_info=True)


# ============================================
# /START COMMAND — BULLETPROOF
# ============================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /start. MUST always reply to the user no matter what.
    Owner gets admin panel. Verified users pass to bot.py handler.
    Unverified users see the join requirement message.
    """
    try:
        user = update.effective_user
        if not user:
            return

        user_id = user.id
        first_name = user.first_name or "User"

        track_user(user_id)

        # ---- OWNER ----
        if user_id == OWNER_ID:
            if original_start_handler:
                try:
                    await original_start_handler(update, context)
                except Exception as e:
                    logger.error("original_start_handler (owner) failed: %s", e)
            await show_admin_panel(update, context, name=first_name)
            return

        # ---- LOAD CONFIG ----
        config = load_config()
        channels = config.get("channels", [])

        # ---- NO CHANNELS CONFIGURED — let everyone through ----
        if not channels:
            if original_start_handler:
                try:
                    await original_start_handler(update, context)
                    return
                except Exception as e:
                    logger.error("original_start_handler (no channels) failed: %s", e)
            await update.message.reply_text(
                f"Welcome, <b>{first_name}</b>! Use /help for available commands.",
                parse_mode="HTML",
            )
            return

        # ---- CHECK VERIFICATION ----
        verified = await is_user_currently_verified(user_id, context.bot)

        if verified:
            mark_user_verified(user_id)
            if original_start_handler:
                try:
                    await original_start_handler(update, context)
                    return
                except Exception as e:
                    logger.error("original_start_handler (verified) failed: %s", e)
            await update.message.reply_text(
                f"Welcome back, <b>{first_name}</b>! ✅\n\nYou're verified. Use /help for commands.",
                parse_mode="HTML",
            )
        else:
            await send_join_message(update, context)

    except Exception as e:
        logger.error("start_command crashed: %s", e, exc_info=True)
        try:
            await update.message.reply_text(
                "Something went wrong. Please try /start again."
            )
        except Exception:
            pass


# ============================================
# /ADMIN COMMAND
# ============================================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only /admin command — opens the admin panel at any time."""
    try:
        user = update.effective_user
        if not user or user.id != OWNER_ID:
            return
        first_name = user.first_name or "Admin"
        await show_admin_panel(update, context, name=first_name)
    except Exception as e:
        logger.error("admin_command crashed: %s", e, exc_info=True)


# ============================================
# /CANCEL COMMAND
# ============================================

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only /cancel — cancels any active admin flow."""
    try:
        user = update.effective_user
        if not user or user.id != OWNER_ID:
            return

        if OWNER_ID in admin_state:
            action = admin_state.pop(OWNER_ID, {}).get("action", "flow")
            await update.message.reply_text(
                f"❌ Cancelled <b>{action}</b>. Use /admin to reopen the panel.",
                parse_mode="HTML",
            )
        else:
            await update.message.reply_text(
                "Nothing to cancel. Use /admin to open the panel.",
                parse_mode="HTML",
            )
    except Exception as e:
        logger.error("cancel_command crashed: %s", e, exc_info=True)


# ============================================
# CALLBACK QUERY HANDLER
# ============================================

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle all inline keyboard button presses."""
    try:
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id
        data = query.data or ""

        # --- Admin panel actions (owner only) ---
        if data.startswith("admin_") and user_id == OWNER_ID:
            await handle_admin_callback(update, context, data)
            return

        # --- Verify membership button ---
        if data == "verify_membership":
            await handle_verify_callback(update, context)
            return

        # --- No-link placeholder ---
        if data == "no_link":
            try:
                await query.answer(
                    "No invite link set for this entry. Contact the admin.",
                    show_alert=True,
                )
            except Exception:
                pass
            return

    except Exception as e:
        logger.error("callback_handler crashed: %s", e, exc_info=True)
        try:
            await update.callback_query.answer(
                "An error occurred. Please try again.", show_alert=True
            )
        except Exception:
            pass


async def handle_verify_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Check membership when user clicks Verify button."""
    try:
        query = update.callback_query
        user_id = query.from_user.id
        first_name = query.from_user.first_name or "User"

        # Fresh check — invalidate cache first
        invalidate_cache(user_id)
        verified = await is_user_currently_verified(user_id, context.bot)

        if verified:
            mark_user_verified(user_id)

            success_text = (
                "✅ <b>Verified Successfully!</b>\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"Welcome, <b>{first_name}</b>! 🎉\n"
                "You now have full access to the bot.\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━"
            )

            try:
                # Delete the join message, then show the bot's actual start
                await query.message.delete()
            except Exception:
                try:
                    await query.message.edit_text(
                        success_text, parse_mode="HTML"
                    )
                except Exception:
                    pass

            # Call original start handler from bot.py
            if original_start_handler:
                try:
                    await original_start_handler(update, context)
                except Exception as e:
                    logger.error("original_start_handler post-verify failed: %s", e)
                    try:
                        await context.bot.send_message(
                            chat_id=user_id,
                            text=success_text,
                            parse_mode="HTML",
                        )
                    except Exception:
                        pass
            else:
                try:
                    await context.bot.send_message(
                        chat_id=user_id,
                        text=(
                            "✅ Verified! You now have full access.\n"
                            "Use /help for commands."
                        ),
                    )
                except Exception:
                    pass
        else:
            # Still not verified — refresh join message
            await send_join_message(update, context)
            try:
                await query.answer(
                    "You haven't joined all required channels/groups yet.",
                    show_alert=True,
                )
            except Exception:
                pass

    except Exception as e:
        logger.error("handle_verify_callback crashed: %s", e, exc_info=True)


async def handle_admin_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE, data: str
) -> None:
    """Handle admin panel button presses (owner only)."""
    try:
        query = update.callback_query
        user = query.from_user
        first_name = user.first_name if user else "Admin"

        if data == "admin_panel":
            await show_admin_panel(update, context, name=first_name)

        elif data == "admin_view_all":
            await admin_view_all(update, context)

        elif data == "admin_stats":
            await admin_stats(update, context)

        elif data == "admin_add_channel":
            admin_state[OWNER_ID] = {"action": "add_channel", "step": "waiting_id"}
            await query.message.edit_text(
                "➕ <b>ADD CHANNEL</b>\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Send the <b>Channel ID</b> (e.g. <code>-1001234567890</code>)\n\n"
                "💡 <i>Tip: Add @RawDataBot to the channel, or forward a "
                "message from it to @userinfobot to get the ID.</i>\n\n"
                "Send /cancel to abort.",
                parse_mode="HTML",
            )

        elif data == "admin_add_group":
            admin_state[OWNER_ID] = {"action": "add_group", "step": "waiting_id"}
            await query.message.edit_text(
                "➕ <b>ADD GROUP</b>\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Send the <b>Group ID</b> (e.g. <code>-1001234567890</code>)\n\n"
                "💡 <i>Tip: Add @RawDataBot to the group to find its ID.</i>\n\n"
                "Send /cancel to abort.",
                parse_mode="HTML",
            )

        elif data == "admin_remove":
            config = load_config()
            channels = config.get("channels", [])
            if not channels:
                await query.message.edit_text(
                    "🗑 <b>REMOVE ENTRY</b>\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "No entries configured yet.\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━",
                    reply_markup=InlineKeyboardMarkup([
                        [InlineKeyboardButton("🔙 Back to Panel", callback_data="admin_panel")]
                    ]),
                    parse_mode="HTML",
                )
                return

            lines = [
                "🗑 <b>REMOVE ENTRY</b>",
                "",
                "━━━━━━━━━━━━━━━━━━━━━━━",
                "",
                "Send the <b>number</b> of the entry to remove:",
                "",
            ]
            for i, ch in enumerate(channels, 1):
                name = ch.get("name") or ch.get("username") or "Unnamed"
                ch_type = ch.get("type", "channel")
                emoji = "📢" if ch_type == "channel" else "👥"
                lines.append(f"  {i}. {emoji} {name}")

            lines += ["", "Send /cancel to abort."]
            admin_state[OWNER_ID] = {"action": "remove", "step": "waiting_index"}
            await query.message.edit_text(
                "\n".join(lines), parse_mode="HTML"
            )

        elif data == "admin_broadcast":
            admin_state[OWNER_ID] = {"action": "broadcast", "step": "waiting_message"}
            await query.message.edit_text(
                "📣 <b>BROADCAST</b>\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Send the message text to broadcast to all tracked users.\n\n"
                "⚠️ <i>This will send to ALL users in users.json.</i>\n\n"
                "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                "Send /cancel to abort.",
                parse_mode="HTML",
            )

    except Exception as e:
        logger.error("handle_admin_callback crashed: %s", e, exc_info=True)


# ============================================
# ADMIN TEXT INPUT HANDLER
# ============================================

async def admin_message_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Handle text messages from the owner during admin input flows.
    Only fires for the owner in private chat (filtered at registration time).
    """
    try:
        user_id = update.effective_user.id
        if user_id != OWNER_ID:
            return

        state = admin_state.get(OWNER_ID)
        if not state:
            return  # Not in any admin flow

        text = (update.message.text or "").strip()

        # /cancel handled separately via cancel_command, but also check here
        if text.lower() in ("/cancel", "cancel"):
            del admin_state[OWNER_ID]
            await update.message.reply_text(
                "❌ Cancelled. Use /admin to open the panel.", parse_mode="HTML"
            )
            return

        action = state.get("action")
        step = state.get("step")

        # ──────────────────────────────────────────
        # ADD CHANNEL / GROUP — Step 1: Chat ID
        # ──────────────────────────────────────────
        if action in ("add_channel", "add_group") and step == "waiting_id":
            try:
                chat_id = int(text)
            except ValueError:
                await update.message.reply_text(
                    "❌ Invalid ID. Please send a numeric chat ID.\n"
                    "Example: <code>-1001234567890</code>",
                    parse_mode="HTML",
                )
                return

            # Verify bot can access the chat
            await update.message.reply_text("🔍 Verifying bot access to chat...")
            try:
                chat_info = await context.bot.get_chat(chat_id=chat_id)
                chat_title = chat_info.title or str(chat_id)
                state["name"] = chat_title  # Pre-fill name from chat title
                state["chat_id"] = chat_id
                state["step"] = "waiting_name"
                admin_state[OWNER_ID] = state

                label = "channel" if action == "add_channel" else "group"
                await update.message.reply_text(
                    f"✅ <b>Found:</b> {chat_title}\n\n"
                    f"Send a <b>display name</b> for this {label}, "
                    f"or type <code>ok</code> to use <b>{chat_title}</b>.",
                    parse_mode="HTML",
                )
            except TelegramError as e:
                await update.message.reply_text(
                    f"❌ <b>Cannot access chat</b> <code>{chat_id}</code>:\n"
                    f"<i>{e}</i>\n\n"
                    "Make sure the bot is an <b>admin</b> in that chat, "
                    "then try again.",
                    parse_mode="HTML",
                )
            return

        # ──────────────────────────────────────────
        # ADD CHANNEL / GROUP — Step 2: Display Name
        # ──────────────────────────────────────────
        if action in ("add_channel", "add_group") and step == "waiting_name":
            if text.lower() == "ok":
                name = state.get("name", f"Chat {state.get('chat_id', '?')}")
            else:
                name = text
            state["name"] = name
            state["step"] = "waiting_invite"
            admin_state[OWNER_ID] = state
            label = "channel" if action == "add_channel" else "group"
            await update.message.reply_text(
                f"✅ Name set: <b>{name}</b>\n\n"
                f"Now send the <b>invite link</b> for this {label}:\n"
                f"e.g. <code>https://t.me/+xxxx</code> or <code>https://t.me/username</code>\n\n"
                "Send <code>skip</code> if you don't have one "
                "(users won't get a clickable Join button).",
                parse_mode="HTML",
            )
            return

        # ──────────────────────────────────────────
        # ADD CHANNEL / GROUP — Step 3: Invite Link
        # ──────────────────────────────────────────
        if action in ("add_channel", "add_group") and step == "waiting_invite":
            invite_link = None if text.lower() == "skip" else text
            state["invite_link"] = invite_link
            state["step"] = "waiting_join_request"
            admin_state[OWNER_ID] = state
            await update.message.reply_text(
                "Does this channel/group use <b>Join Requests</b>?\n"
                "(Users must request to join and wait for approval)\n\n"
                "Reply <code>yes</code> or <code>no</code>.",
                parse_mode="HTML",
            )
            return

        # ──────────────────────────────────────────
        # ADD CHANNEL / GROUP — Step 4: Join Request
        # ──────────────────────────────────────────
        if action in ("add_channel", "add_group") and step == "waiting_join_request":
            jr = text.lower() in ("yes", "y", "1", "true")
            chat_id = state["chat_id"]
            name = state["name"]
            invite_link = state.get("invite_link")
            ch_type = "channel" if action == "add_channel" else "group"

            new_entry: dict = {
                "chat_id": chat_id,
                "name": name,
                "type": ch_type,
                "join_request": jr,
            }
            if invite_link:
                new_entry["invite_link"] = invite_link

            config = load_config()
            config.setdefault("channels", []).append(new_entry)
            save_config(config)
            del admin_state[OWNER_ID]

            emoji = "📢" if ch_type == "channel" else "👥"
            jr_label = " (join requests enabled)" if jr else ""
            await update.message.reply_text(
                f"✅ <b>Added successfully!</b>\n\n"
                f"{emoji} <b>{name}</b>\n"
                f"   ID: <code>{chat_id}</code>{jr_label}\n\n"
                "Use /admin to return to the panel.",
                parse_mode="HTML",
            )
            return

        # ──────────────────────────────────────────
        # REMOVE — Step 1: Index
        # ──────────────────────────────────────────
        if action == "remove" and step == "waiting_index":
            try:
                index = int(text) - 1
            except ValueError:
                await update.message.reply_text(
                    "❌ Please send a number.", parse_mode="HTML"
                )
                return

            config = load_config()
            channels = config.get("channels", [])
            if index < 0 or index >= len(channels):
                await update.message.reply_text(
                    f"❌ Invalid number. Choose between 1 and {len(channels)}.",
                    parse_mode="HTML",
                )
                return

            removed = channels.pop(index)
            config["channels"] = channels
            save_config(config)
            del admin_state[OWNER_ID]

            name = removed.get("name") or removed.get("username") or "Entry"
            ch_type = removed.get("type", "channel")
            emoji = "📢" if ch_type == "channel" else "👥"
            await update.message.reply_text(
                f"🗑 <b>Removed:</b> {emoji} {name}\n\n"
                "Use /admin to return to the panel.",
                parse_mode="HTML",
            )
            return

        # ──────────────────────────────────────────
        # BROADCAST — Step 1: Message Text
        # ──────────────────────────────────────────
        if action == "broadcast" and step == "waiting_message":
            users = load_users()
            total = len(users)
            sent = 0
            failed = 0

            progress_msg = await update.message.reply_text(
                f"📣 Broadcasting to <b>{total}</b> users...",
                parse_mode="HTML",
            )

            for uid_str in list(users.keys()):
                try:
                    uid = int(uid_str)
                    await context.bot.send_message(chat_id=uid, text=text)
                    sent += 1
                    await asyncio.sleep(0.05)  # Respect rate limits
                except Exception as e:
                    logger.warning("Broadcast failed for %s: %s", uid_str, e)
                    failed += 1

            del admin_state[OWNER_ID]

            try:
                await progress_msg.edit_text(
                    "📣 <b>Broadcast Complete</b>\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    f"✅ <b>Sent:</b>   {sent}\n"
                    f"❌ <b>Failed:</b> {failed}\n\n"
                    "━━━━━━━━━━━━━━━━━━━━━━━\n\n"
                    "Use /admin to return to the panel.",
                    parse_mode="HTML",
                )
            except Exception:
                await update.message.reply_text(
                    f"Broadcast done. Sent: {sent}, Failed: {failed}"
                )
            return

    except Exception as e:
        logger.error("admin_message_handler crashed: %s", e, exc_info=True)
        try:
            await update.message.reply_text(
                "⚠️ An error occurred. Use /admin to restart the panel."
            )
        except Exception:
            pass


# ============================================
# JOIN REQUEST HANDLER — TRACK ONLY, NO AUTO-APPROVE
# ============================================

async def handle_join_request(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Track join requests in memory. DO NOT auto-approve or reject.
    A pending request counts as 'verified' for join-request-gated chats.
    """
    try:
        request = update.chat_join_request
        if not request:
            return
        user_id = request.from_user.id
        chat_id = request.chat.id
        pending_join_requests.add((chat_id, user_id))
        logger.info("Join request tracked: user=%s chat=%s", user_id, chat_id)
    except Exception as e:
        logger.error("handle_join_request crashed: %s", e, exc_info=True)


# ============================================
# CHAT MEMBER HANDLER — DETECT LEAVES / KICKS
# ============================================

async def handle_chat_member_update(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Detect when a user leaves or is kicked.
    Invalidate their verification cache so the next /start re-checks.
    """
    try:
        chat_member: Optional[ChatMemberUpdated] = update.chat_member
        if not chat_member:
            return

        new_status = chat_member.new_chat_member.status
        user_id = chat_member.new_chat_member.user.id
        chat_id = chat_member.chat.id

        if new_status in ("left", "kicked", "banned"):
            invalidate_cache(user_id)
            pending_join_requests.discard((chat_id, user_id))
            logger.info(
                "User %s left/kicked from %s — cache invalidated", user_id, chat_id
            )
    except Exception as e:
        logger.error("handle_chat_member_update crashed: %s", e, exc_info=True)


# ============================================
# VERIFICATION MIDDLEWARE
# ============================================

def make_verified_handler(original_callback):
    """
    Wrap a bot.py handler callback with membership verification.
    Owner always passes through. Unverified users see the join message.
    """
    async def verified_callback(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        try:
            user = update.effective_user
            if not user:
                await original_callback(update, context)
                return

            user_id = user.id

            if user_id == OWNER_ID:
                await original_callback(update, context)
                return

            config = load_config()
            if not config.get("channels"):
                await original_callback(update, context)
                return

            verified = await is_user_currently_verified(user_id, context.bot)
            if verified:
                await original_callback(update, context)
            else:
                await send_join_message(update, context)

        except Exception as e:
            logger.error(
                "verified_callback crashed (wrapping %s): %s",
                getattr(original_callback, "__name__", "?"), e, exc_info=True,
            )
            try:
                await original_callback(update, context)
            except Exception:
                pass

    verified_callback.__name__ = f"verified_{getattr(original_callback, '__name__', 'handler')}"
    return verified_callback


# ============================================
# BUILD APPLICATION
# ============================================

def build_application() -> Application:
    """Build and configure the Telegram bot application."""
    app = Application.builder().token(BOT_TOKEN).build()

    # --- Group 0: Core system handlers (highest priority) ---

    # /start — always our bulletproof wrapper
    app.add_handler(CommandHandler("start", start_command), group=0)

    # /admin — owner opens panel anytime
    app.add_handler(CommandHandler("admin", admin_command), group=0)

    # /cancel — owner cancels any active flow
    app.add_handler(CommandHandler("cancel", cancel_command), group=0)

    # Inline keyboard callbacks
    app.add_handler(CallbackQueryHandler(callback_handler), group=0)

    # Join request tracking
    app.add_handler(ChatJoinRequestHandler(handle_join_request), group=0)

    # Chat member updates (detect leaves/kicks)
    app.add_handler(
        ChatMemberHandler(handle_chat_member_update, ChatMemberHandler.CHAT_MEMBER),
        group=0,
    )

    # --- Group 1: Admin text input (must beat bot.py handlers) ---
    # Only fires for owner in private chats
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.ChatType.PRIVATE & filters.User(user_id=OWNER_ID),
            admin_message_handler,
        ),
        group=1,
    )

    # --- Group 2: Bot.py handlers wrapped with verification middleware ---
    if original_handlers:
        for handler in original_handlers:
            try:
                # Skip /start from bot.py — we handle it above
                if isinstance(handler, CommandHandler) and "start" in getattr(
                    handler, "commands", set()
                ):
                    continue

                # Wrap callback with verification middleware
                if hasattr(handler, "callback") and callable(handler.callback):
                    handler.callback = make_verified_handler(handler.callback)

                app.add_handler(handler, group=2)
            except Exception as e:
                logger.warning(
                    "Failed to register bot.py handler %s: %s", handler, e
                )

    return app


# ============================================
# MAIN ENTRY POINT
# ============================================

def main() -> None:
    print("\n" + "═" * 50)
    print("  Telegram Verification Wrapper — Starting")
    print("═" * 50 + "\n")

    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("ERROR: Please set BOT_TOKEN in main.py before running!")
        sys.exit(1)

    if OWNER_ID == 123456789:
        print(
            "WARNING: OWNER_ID is still the default placeholder.\n"
            "         Set it to your real Telegram user ID!\n"
        )

    # Load bot.py via monkey-patching (safe — never crashes main bot)
    bot_py_loaded = load_bot_py()
    if not bot_py_loaded and not os.path.exists("bot.py"):
        print("[main.py] Running in standalone mode (no bot.py detected).\n")

    # Build the application
    try:
        app = build_application()
    except Exception as e:
        print(f"FATAL: Failed to build application: {e}")
        logger.critical("Failed to build application: %s", e, exc_info=True)
        sys.exit(1)

    print("═" * 50)
    print("  Bot is live! Press Ctrl+C to stop.")
    print("═" * 50 + "\n")
    logger.info("Bot is live!")

    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
