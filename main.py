import asyncio
import importlib.util
import json
import logging
import os
import sys
import time
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
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    ChatMemberHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ============================================
# CONFIGURATION — SET THESE
# ============================================
BOT_TOKEN = "7727685861:AAFR5NtU4dH-8T8gGqBOMou59vlvPGs7h9Q"
OWNER_ID = 8395315423 # Your Telegram user ID (integer)

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
# These are tracked but NOT auto-approved.
pending_join_requests: set = set()

# Membership cache: {user_id: {"verified": bool, "ts": float}}
membership_cache: dict = {}
CACHE_TTL = 300  # 5 minutes

# Admin panel conversation states
(
    ADMIN_WAITING_CHANNEL_ID,
    ADMIN_WAITING_CHANNEL_NAME,
    ADMIN_WAITING_CHANNEL_TYPE,
    ADMIN_WAITING_GROUP_ID,
    ADMIN_WAITING_GROUP_NAME,
    ADMIN_WAITING_REMOVE_INDEX,
    ADMIN_WAITING_BROADCAST,
) = range(7)

# Admin input state per owner (simple dict since owner is one person)
admin_state: dict = {}

# ============================================
# BOT.PY DYNAMIC LOADING
# ============================================
original_start_handler = None
original_handlers = []

def load_bot_py() -> bool:
    """
    Attempt to load bot.py and extract its handlers.
    Returns True if successful, False otherwise.
    This function MUST NEVER crash the main bot.
    """
    global original_start_handler, original_handlers

    bot_py_path = os.path.join(os.path.dirname(__file__), "bot.py")

    if not os.path.exists(bot_py_path):
        print("bot.py not found — running in standalone mode.")
        logger.info("bot.py not found — standalone mode.")
        return False

    try:
        spec = importlib.util.spec_from_file_location("bot_module", bot_py_path)
        if spec is None or spec.loader is None:
            logger.warning("Could not create module spec for bot.py")
            return False

        bot_module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bot_module)  # type: ignore[attr-defined]

        handlers_found = []

        # --- Layer 1: Look for build_application / create_application ---
        for fn_name in ("build_application", "create_application", "get_application"):
            fn = getattr(bot_module, fn_name, None)
            if callable(fn):
                try:
                    app = fn()
                    if hasattr(app, "handlers"):
                        for group_handlers in app.handlers.values():
                            handlers_found.extend(group_handlers)
                    logger.info("bot.py handlers extracted via %s()", fn_name)
                    break
                except Exception as e:
                    logger.warning("bot.py %s() failed: %s", fn_name, e)

        # --- Layer 2: Look for a handlers list / dict ---
        if not handlers_found:
            for attr_name in ("handlers", "HANDLERS", "handler_list"):
                attr = getattr(bot_module, attr_name, None)
                if isinstance(attr, (list, tuple)):
                    handlers_found.extend(attr)
                    logger.info("bot.py handlers extracted via module.%s", attr_name)
                    break

        # --- Layer 3: Look for register_handlers function ---
        if not handlers_found:
            register_fn = getattr(bot_module, "register_handlers", None)
            if callable(register_fn):
                try:
                    # Some bots accept an app object; we pass None and catch
                    result = register_fn(None)
                    if isinstance(result, (list, tuple)):
                        handlers_found.extend(result)
                    logger.info("bot.py handlers extracted via register_handlers()")
                except Exception as e:
                    logger.warning("bot.py register_handlers() failed: %s", e)

        # --- Extract start handler specifically ---
        for handler in handlers_found:
            if isinstance(handler, CommandHandler):
                if "start" in getattr(handler, "commands", set()):
                    # Store the callback, not the handler wrapper
                    original_start_handler = handler.callback
                    logger.info("Found original /start handler in bot.py")
                    break

        original_handlers = handlers_found
        count = len(handlers_found)
        print(f"bot.py loaded: {count} handler(s) found")
        logger.info("bot.py loaded successfully: %d handler(s)", count)
        return True

    except SyntaxError as e:
        logger.error("bot.py has a syntax error: %s", e)
        print(f"bot.py syntax error (ignored — standalone mode): {e}")
        return False
    except Exception as e:
        logger.error("bot.py failed to load: %s", e, exc_info=True)
        print(f"bot.py load failed (ignored — standalone mode): {e}")
        return False


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
            users[uid] = {"first_seen": time.time()}
        else:
            users[uid]["last_seen"] = time.time()
        save_users(users)
    except Exception as e:
        logger.error("track_user failed: %s", e)


# ============================================
# MEMBERSHIP / VERIFICATION
# ============================================

async def check_single_channel(user_id: int, channel: dict, bot: Bot) -> bool:
    """
    Check if user is a member of the given channel/group.
    Also checks pending_join_requests for join-request-gated chats.
    Returns True if verified for this channel, False otherwise.
    """
    chat_id = channel.get("chat_id")
    if not chat_id:
        logger.warning("Channel entry missing chat_id: %s", channel)
        return False

    try:
        chat_id_int = int(chat_id)
    except (ValueError, TypeError):
        logger.error("Invalid chat_id value: %s", chat_id)
        return False

    try:
        member = await bot.get_chat_member(chat_id=chat_id_int, user_id=user_id)
        status = member.status

        # Fully accepted statuses
        if status in ("member", "administrator", "creator"):
            return True

        # "restricted" still means they're in a group
        if channel.get("type") == "group" and status == "restricted":
            return True

        # Join-request channels: pending request counts as verified
        if channel.get("join_request", False):
            if (chat_id_int, user_id) in pending_join_requests:
                return True

        return False

    except TelegramError as e:
        logger.error(
            "Membership check API error: chat=%s user=%s error=%s",
            chat_id,
            user_id,
            e,
        )
        # Fallback: if API fails, still check pending join requests
        try:
            chat_id_int = int(chat_id)
            if channel.get("join_request", False):
                if (chat_id_int, user_id) in pending_join_requests:
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
        # Cache check
        cached = membership_cache.get(user_id)
        if cached and (time.time() - cached["ts"]) < CACHE_TTL:
            return cached["verified"]

        config = load_config()
        channels = config.get("channels", [])

        if not channels:
            # No verification required
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
    """Remove a user from the membership cache so next check is fresh."""
    membership_cache.pop(user_id, None)


# ============================================
# JOIN MESSAGE & KEYBOARD
# ============================================

def build_join_keyboard(channels: list) -> InlineKeyboardMarkup:
    """Build inline keyboard with Join buttons for each channel/group + a Verify button."""
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
            # If no link stored, show a placeholder — admin should add invite links
            buttons.append([InlineKeyboardButton(f"{label} (link not set)", callback_data="no_link")])

    buttons.append([InlineKeyboardButton("✅ I've Joined — Verify Now", callback_data="verify_membership")])
    return InlineKeyboardMarkup(buttons)


async def send_join_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Send the 'please join these channels' message to an unverified user.
    Completely bulletproof — NEVER crashes silently.
    """
    try:
        config = load_config()
        channels = config.get("channels", [])

        # Determine target message object
        if update.callback_query:
            target = update.callback_query.message
        else:
            target = update.message

        if not target:
            logger.error("send_join_message: no message target found")
            return

        if not channels:
            # No channels configured — let them through
            try:
                await target.reply_text("Welcome! No verification required. Use /help for commands.")
            except Exception as e:
                logger.error("send_join_message reply (no channels) failed: %s", e)
            return

        # Build the message text (plain text — no Markdown to avoid parse errors)
        lines = [
            "Access Restricted",
            "",
            "To use this bot, join the following channels/groups:",
            "",
        ]
        for i, ch in enumerate(channels, 1):
            name = ch.get("name") or ch.get("username") or f"Chat {ch.get('chat_id', '?')}"
            ch_type = ch.get("type", "channel")
            emoji = "📢" if ch_type == "channel" else "👥"
            type_label = "Channel" if ch_type == "channel" else "Group"
            lines.append(f"{i}. {emoji} {type_label}: {name}")

        lines += ["", "After joining all above, click the Verify button below."]
        text = "\n".join(lines)

        keyboard = build_join_keyboard(channels)

        try:
            if update.callback_query:
                await target.edit_text(text, reply_markup=keyboard)
            else:
                await target.reply_text(text, reply_markup=keyboard)
        except Exception as e:
            logger.warning("send_join_message edit/reply failed, trying plain reply: %s", e)
            try:
                await target.reply_text(text, reply_markup=keyboard)
            except Exception as e2:
                logger.error("send_join_message plain reply also failed: %s", e2)

    except Exception as e:
        logger.error("send_join_message crashed: %s", e, exc_info=True)
        try:
            msg = None
            if update.callback_query:
                msg = update.callback_query.message
            elif update.message:
                msg = update.message
            if msg:
                await msg.reply_text("Error loading verification. Please try /start again.")
        except Exception:
            pass


# ============================================
# ADMIN PANEL
# ============================================

async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the owner's admin panel with inline buttons."""
    try:
        config = load_config()
        channels = config.get("channels", [])
        users = load_users()

        channel_count = sum(1 for ch in channels if ch.get("type") == "channel")
        group_count = sum(1 for ch in channels if ch.get("type") == "group")
        user_count = len(users)

        text = (
            "Admin Panel\n"
            "\n"
            f"Channels: {channel_count}\n"
            f"Groups: {group_count}\n"
            f"Total verified users: {user_count}\n"
            "\n"
            "Choose an action:"
        )

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Add Channel", callback_data="admin_add_channel"),
                InlineKeyboardButton("Add Group", callback_data="admin_add_group"),
            ],
            [
                InlineKeyboardButton("View All", callback_data="admin_view_all"),
                InlineKeyboardButton("Remove Entry", callback_data="admin_remove"),
            ],
            [
                InlineKeyboardButton("Stats", callback_data="admin_stats"),
                InlineKeyboardButton("Broadcast", callback_data="admin_broadcast"),
            ],
        ])

        msg = update.message or (update.callback_query.message if update.callback_query else None)
        if msg:
            try:
                if update.callback_query:
                    await msg.edit_text(text, reply_markup=keyboard)
                else:
                    await msg.reply_text(text, reply_markup=keyboard)
            except Exception as e:
                logger.warning("show_admin_panel edit failed, trying reply: %s", e)
                try:
                    await msg.reply_text(text, reply_markup=keyboard)
                except Exception as e2:
                    logger.error("show_admin_panel reply also failed: %s", e2)

    except Exception as e:
        logger.error("show_admin_panel crashed: %s", e, exc_info=True)


async def admin_view_all(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show all configured channels/groups."""
    try:
        config = load_config()
        channels = config.get("channels", [])

        if not channels:
            text = "No channels or groups configured yet."
        else:
            lines = ["Configured channels/groups:\n"]
            for i, ch in enumerate(channels, 1):
                name = ch.get("name") or ch.get("username") or "Unnamed"
                ch_type = ch.get("type", "channel")
                chat_id = ch.get("chat_id", "?")
                jr = " [join request]" if ch.get("join_request") else ""
                emoji = "📢" if ch_type == "channel" else "👥"
                lines.append(f"{i}. {emoji} {name} (ID: {chat_id}){jr}")
            text = "\n".join(lines)

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Back to Panel", callback_data="admin_panel")]
        ])

        msg = update.callback_query.message
        try:
            await msg.edit_text(text, reply_markup=keyboard)
        except Exception as e:
            logger.error("admin_view_all failed: %s", e)

    except Exception as e:
        logger.error("admin_view_all crashed: %s", e, exc_info=True)


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show usage statistics."""
    try:
        users = load_users()
        config = load_config()
        channels = config.get("channels", [])

        total_users = len(users)
        pending_requests = len(pending_join_requests)
        cached_verified = sum(
            1 for v in membership_cache.values() if v.get("verified")
        )

        text = (
            "Stats\n"
            "\n"
            f"Total tracked users: {total_users}\n"
            f"Configured channels/groups: {len(channels)}\n"
            f"Pending join requests (in memory): {pending_requests}\n"
            f"Currently cached as verified: {cached_verified}\n"
        )

        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("Back to Panel", callback_data="admin_panel")]
        ])

        msg = update.callback_query.message
        try:
            await msg.edit_text(text, reply_markup=keyboard)
        except Exception as e:
            logger.error("admin_stats display failed: %s", e)

    except Exception as e:
        logger.error("admin_stats crashed: %s", e, exc_info=True)


# ============================================
# /START COMMAND — BULLETPROOF
# ============================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle /start. MUST always reply to the user no matter what happens internally.
    """
    try:
        user = update.effective_user
        if not user:
            return

        user_id = user.id
        first_name = user.first_name or "User"

        # Track this user
        track_user(user_id)

        # ---- OWNER ----
        if user_id == OWNER_ID:
            if original_start_handler:
                try:
                    await original_start_handler(update, context)
                except Exception as e:
                    logger.error("original_start_handler (owner) failed: %s", e)
                    await update.message.reply_text(f"Welcome back, {first_name}! (owner)")
            else:
                await update.message.reply_text(
                    f"Welcome back, {first_name}! Bot is running."
                )
            await show_admin_panel(update, context)
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
                f"Welcome, {first_name}! Use /help for available commands."
            )
            return

        # ---- CHECK VERIFICATION ----
        verified = await is_user_currently_verified(user_id, context.bot)

        if verified:
            if original_start_handler:
                try:
                    await original_start_handler(update, context)
                    return
                except Exception as e:
                    logger.error("original_start_handler (verified user) failed: %s", e)
            await update.message.reply_text(
                f"Welcome back, {first_name}! You're verified. Bot is ready."
            )
        else:
            # NOT VERIFIED — show join channels message
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
                    "No invite link set for this entry. Ask the admin.", show_alert=True
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


async def handle_verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Check membership when user clicks Verify button."""
    try:
        query = update.callback_query
        user_id = query.from_user.id
        first_name = query.from_user.first_name or "User"

        # Invalidate cache so we do a fresh check
        invalidate_cache(user_id)

        verified = await is_user_currently_verified(user_id, context.bot)

        if verified:
            # Mark verified + call original start if available
            track_user(user_id)
            success_text = (
                f"Verified! Welcome, {first_name}!\n"
                "You now have full access. Send /start to begin."
            )
            try:
                await query.message.edit_text(success_text)
            except Exception:
                try:
                    await query.message.reply_text(success_text)
                except Exception:
                    pass

            # Try calling original start handler
            if original_start_handler:
                try:
                    await original_start_handler(update, context)
                except Exception as e:
                    logger.error("original_start_handler post-verify failed: %s", e)
        else:
            # Still not verified — refresh the join message
            await send_join_message(update, context)
            try:
                await query.answer(
                    "You haven't joined all required channels/groups yet.", show_alert=True
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

        if data == "admin_panel":
            await show_admin_panel(update, context)

        elif data == "admin_view_all":
            await admin_view_all(update, context)

        elif data == "admin_stats":
            await admin_stats(update, context)

        elif data == "admin_add_channel":
            admin_state[OWNER_ID] = {"action": "add_channel", "step": "waiting_id"}
            try:
                await query.message.edit_text(
                    "Add Channel\n\n"
                    "Send the Channel ID (e.g. -1001234567890).\n"
                    "Tip: Use @RawDataBot or forward a message to @userinfobot to find the ID."
                )
            except Exception as e:
                logger.error("admin_add_channel prompt failed: %s", e)

        elif data == "admin_add_group":
            admin_state[OWNER_ID] = {"action": "add_group", "step": "waiting_id"}
            try:
                await query.message.edit_text(
                    "Add Group\n\n"
                    "Send the Group ID (e.g. -1001234567890).\n"
                    "Tip: Use @RawDataBot or add bot to group and check logs."
                )
            except Exception as e:
                logger.error("admin_add_group prompt failed: %s", e)

        elif data == "admin_remove":
            config = load_config()
            channels = config.get("channels", [])
            if not channels:
                try:
                    await query.message.edit_text(
                        "No entries to remove.",
                        reply_markup=InlineKeyboardMarkup([
                            [InlineKeyboardButton("Back", callback_data="admin_panel")]
                        ]),
                    )
                except Exception:
                    pass
                return
            lines = ["Remove Entry\n\nSend the number to remove:\n"]
            for i, ch in enumerate(channels, 1):
                name = ch.get("name") or ch.get("username") or "Unnamed"
                ch_type = ch.get("type", "channel")
                emoji = "📢" if ch_type == "channel" else "👥"
                lines.append(f"{i}. {emoji} {name}")
            admin_state[OWNER_ID] = {"action": "remove", "step": "waiting_index"}
            try:
                await query.message.edit_text("\n".join(lines))
            except Exception as e:
                logger.error("admin_remove prompt failed: %s", e)

        elif data == "admin_broadcast":
            admin_state[OWNER_ID] = {"action": "broadcast", "step": "waiting_message"}
            try:
                await query.message.edit_text(
                    "Broadcast\n\nSend the message text to broadcast to all tracked users.\n"
                    "Send /cancel to abort."
                )
            except Exception as e:
                logger.error("admin_broadcast prompt failed: %s", e)

    except Exception as e:
        logger.error("handle_admin_callback crashed: %s", e, exc_info=True)


# ============================================
# ADMIN TEXT INPUT HANDLER
# ============================================

async def admin_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle text messages from the owner when in an admin input state.
    This handler ONLY fires for the owner (filtered in registration).
    """
    try:
        user_id = update.effective_user.id
        if user_id != OWNER_ID:
            return  # Safety guard — should not reach here due to filter

        state = admin_state.get(OWNER_ID)
        if not state:
            return  # Owner not in any admin flow — ignore

        text = (update.message.text or "").strip()
        action = state.get("action")
        step = state.get("step")

        # ---- ADD CHANNEL / GROUP ----
        if action in ("add_channel", "add_group") and step == "waiting_id":
            try:
                chat_id = int(text)
            except ValueError:
                await update.message.reply_text(
                    "Invalid ID. Please send a numeric chat ID (e.g. -1001234567890)."
                )
                return
            state["chat_id"] = chat_id
            state["step"] = "waiting_name"
            admin_state[OWNER_ID] = state
            await update.message.reply_text(
                "Got it! Now send a display name for this entry (e.g. My Channel)."
            )
            return

        if action in ("add_channel", "add_group") and step == "waiting_name":
            state["name"] = text
            state["step"] = "waiting_invite"
            admin_state[OWNER_ID] = state
            label = "channel" if action == "add_channel" else "group"
            await update.message.reply_text(
                f"Name set to: {text}\n\n"
                f"Now send the invite link for this {label} "
                f"(e.g. https://t.me/+xxxx or https://t.me/username).\n"
                "Send 'skip' if you don't have one (users won't get a Join button)."
            )
            return

        if action in ("add_channel", "add_group") and step == "waiting_invite":
            invite_link = None if text.lower() == "skip" else text
            state["invite_link"] = invite_link
            state["step"] = "waiting_join_request"
            admin_state[OWNER_ID] = state
            await update.message.reply_text(
                "Does this channel/group use Join Requests (users must request to join)?\n"
                "Reply 'yes' or 'no'."
            )
            return

        if action in ("add_channel", "add_group") and step == "waiting_join_request":
            jr = text.lower() in ("yes", "y", "1", "true")
            chat_id = state["chat_id"]
            name = state["name"]
            invite_link = state.get("invite_link")
            ch_type = "channel" if action == "add_channel" else "group"

            new_entry = {
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
            jr_label = " (join requests)" if jr else ""
            await update.message.reply_text(
                f"Added {emoji} {name} (ID: {chat_id}){jr_label}.\n\n"
                "Send /admin to return to the panel."
            )
            return

        # ---- REMOVE ----
        if action == "remove" and step == "waiting_index":
            try:
                index = int(text) - 1
            except ValueError:
                await update.message.reply_text("Please send a number.")
                return
            config = load_config()
            channels = config.get("channels", [])
            if index < 0 or index >= len(channels):
                await update.message.reply_text("Invalid number. Please try again.")
                return
            removed = channels.pop(index)
            config["channels"] = channels
            save_config(config)
            del admin_state[OWNER_ID]
            name = removed.get("name") or removed.get("username") or "Entry"
            await update.message.reply_text(
                f"Removed: {name}.\n\nSend /admin to return to the panel."
            )
            return

        # ---- BROADCAST ----
        if action == "broadcast" and step == "waiting_message":
            if text.lower() == "/cancel":
                del admin_state[OWNER_ID]
                await update.message.reply_text("Broadcast cancelled.")
                return

            users = load_users()
            sent = 0
            failed = 0
            await update.message.reply_text(
                f"Broadcasting to {len(users)} users... please wait."
            )
            for uid_str in list(users.keys()):
                try:
                    uid = int(uid_str)
                    await context.bot.send_message(chat_id=uid, text=text)
                    sent += 1
                    await asyncio.sleep(0.05)  # Rate limit
                except Exception as e:
                    logger.warning("Broadcast failed for user %s: %s", uid_str, e)
                    failed += 1

            del admin_state[OWNER_ID]
            await update.message.reply_text(
                f"Broadcast complete.\nSent: {sent}\nFailed: {failed}"
            )
            return

    except Exception as e:
        logger.error("admin_message_handler crashed: %s", e, exc_info=True)
        try:
            await update.message.reply_text(
                "An error occurred in admin input. Use /admin to restart."
            )
        except Exception:
            pass


# ============================================
# /ADMIN COMMAND
# ============================================

async def admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Owner-only /admin command to open the admin panel."""
    try:
        user_id = update.effective_user.id
        if user_id != OWNER_ID:
            return  # Silently ignore non-owners
        await show_admin_panel(update, context)
    except Exception as e:
        logger.error("admin_command crashed: %s", e, exc_info=True)


# ============================================
# JOIN REQUEST HANDLER — TRACK ONLY, DO NOT AUTO-APPROVE
# ============================================

async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Track join requests in memory.
    DO NOT auto-approve or reject — let channel admins handle that manually.
    A pending join request counts as 'verified' for join-request-gated channels.
    """
    try:
        request = update.chat_join_request
        if not request:
            return

        user_id = request.from_user.id
        chat_id = request.chat.id

        # Store in memory
        pending_join_requests.add((chat_id, user_id))
        logger.info("Join request tracked: user=%s chat=%s", user_id, chat_id)

        # DO NOT call approve_chat_join_request or decline_chat_join_request
        # The channel/group admin will handle approval manually.

    except Exception as e:
        logger.error("handle_join_request crashed: %s", e, exc_info=True)


# ============================================
# CHAT MEMBER HANDLER — DETECT WHEN USER LEAVES
# ============================================

async def handle_chat_member_update(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Detect when a user leaves or is kicked from a channel/group.
    Invalidate their verification cache so next /start re-checks.
    """
    try:
        chat_member: Optional[ChatMemberUpdated] = update.chat_member
        if not chat_member:
            return

        new_status = chat_member.new_chat_member.status
        user_id = chat_member.new_chat_member.user.id
        chat_id = chat_member.chat.id

        # User left or was kicked
        if new_status in ("left", "kicked", "banned"):
            invalidate_cache(user_id)
            # Also remove any tracked join request for this chat
            pending_join_requests.discard((chat_id, user_id))
            logger.info(
                "User %s left/kicked from chat %s — cache invalidated", user_id, chat_id
            )

    except Exception as e:
        logger.error("handle_chat_member_update crashed: %s", e, exc_info=True)


# ============================================
# VERIFICATION MIDDLEWARE
# ============================================

def make_verified_handler(original_callback):
    """
    Wrap a handler callback with verification middleware.
    If the user is not verified, show the join message instead.
    Owner always passes through.
    """
    async def verified_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        try:
            user = update.effective_user
            if not user:
                await original_callback(update, context)
                return

            user_id = user.id

            # Owner always passes through
            if user_id == OWNER_ID:
                await original_callback(update, context)
                return

            config = load_config()
            channels = config.get("channels", [])

            if not channels:
                await original_callback(update, context)
                return

            verified = await is_user_currently_verified(user_id, context.bot)
            if verified:
                await original_callback(update, context)
            else:
                await send_join_message(update, context)

        except Exception as e:
            logger.error("verified_callback crashed (wrapping %s): %s", original_callback.__name__, e, exc_info=True)
            try:
                await original_callback(update, context)
            except Exception:
                pass

    verified_callback.__name__ = f"verified_{original_callback.__name__}"
    return verified_callback


# ============================================
# BUILD APPLICATION
# ============================================

def build_application() -> Application:
    """Build and configure the Telegram bot application."""
    app = Application.builder().token(BOT_TOKEN).build()

    # --- Core handlers (group 0 — highest priority) ---

    # /start — our bulletproof wrapper always handles this
    app.add_handler(CommandHandler("start", start_command), group=0)

    # /admin — owner only
    app.add_handler(CommandHandler("admin", admin_command), group=0)

    # Inline keyboard callbacks
    app.add_handler(CallbackQueryHandler(callback_handler), group=0)

    # Join request tracking — NEVER auto-approve
    app.add_handler(ChatJoinRequestHandler(handle_join_request), group=0)

    # Chat member updates (detect leaves/kicks)
    app.add_handler(
        ChatMemberHandler(handle_chat_member_update, ChatMemberHandler.CHAT_MEMBER),
        group=0,
    )

    # Admin text input — MUST come BEFORE bot.py handlers, AFTER /start
    # Filter: only owner, only private chats, only text
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.ChatType.PRIVATE & filters.User(user_id=OWNER_ID),
            admin_message_handler,
        ),
        group=1,
    )

    # --- Bot.py handlers (group 2 — wrapped with verification middleware) ---
    if original_handlers:
        for handler in original_handlers:
            try:
                # Skip /start from bot.py — we handle it above
                if isinstance(handler, CommandHandler) and "start" in getattr(
                    handler, "commands", set()
                ):
                    continue

                # Wrap the callback with verification middleware
                if hasattr(handler, "callback") and callable(handler.callback):
                    handler.callback = make_verified_handler(handler.callback)

                app.add_handler(handler, group=2)
            except Exception as e:
                logger.warning("Failed to register bot.py handler %s: %s", handler, e)

    return app


# ============================================
# MAIN ENTRY POINT
# ============================================

def main() -> None:
    print("Bot starting...")
    logger.info("Bot starting...")

    # Validate config
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print("ERROR: Please set your BOT_TOKEN in main.py before running!")
        sys.exit(1)

    if OWNER_ID == 123456789:
        print("WARNING: OWNER_ID is still the default placeholder. Set it to your real Telegram user ID.")

    # Load bot.py (optional)
    bot_py_loaded = load_bot_py()
    if not bot_py_loaded:
        print("Running in standalone mode (no bot.py).")

    # Build the application
    try:
        app = build_application()
    except Exception as e:
        print(f"FATAL: Failed to build application: {e}")
        logger.critical("Failed to build application: %s", e, exc_info=True)
        sys.exit(1)

    print("Bot is live! Press Ctrl+C to stop.")
    logger.info("Bot is live!")

    # Run the bot (polling)
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
