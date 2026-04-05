import asyncio
import importlib.util
import inspect
import json
import logging
import os
import sys
import time
from datetime import datetime

from telegram import (
    Bot,
    ChatMemberAdministrator,
    ChatMemberMember,
    ChatMemberOwner,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ChatJoinRequestHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ============================================
# CONFIGURATION — EDIT THESE
# ============================================
BOT_TOKEN = "7727685861:AAFR5NtU4dH-8T8gGqBOMou59vlvPGs7h9Q"
OWNER_ID = 8395315423 # Your Telegram user ID (integer)

# ============================================
# FILE PATHS
# ============================================
CONFIG_FILE = "config.json"
USERS_FILE = "users.json"
BOT_MODULE = "bot"  # bot.py → import as "bot"

# ============================================
# LOGGING
# ============================================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ============================================
# MEMBERSHIP CACHE — 5-minute TTL
# ============================================
membership_cache: dict = {}  # {user_id: {"verified": bool, "timestamp": float}}
CACHE_TTL = 300  # 5 minutes in seconds

# ============================================
# CONVERSATION STATES
# ============================================
(
    ADD_CHAT_ID,
    ADD_USERNAME,
    ADD_LINK,
    ADD_TYPE_CONFIRM,
    REMOVE_CONFIRM,
    BROADCAST_MSG,
) = range(6)

# ============================================
# GLOBAL: ORIGINAL BOT.PY START HANDLER
# ============================================
original_start_handler = None  # Captured from bot.py
_original_app = None           # Reference to the monkey-patched application

# ============================================
# PERSISTENCE HELPERS
# ============================================

def load_config() -> dict:
    """Load channel/group config from config.json."""
    if not os.path.exists(CONFIG_FILE):
        default = {"channels": []}
        with open(CONFIG_FILE, "w") as f:
            json.dump(default, f, indent=2)
        return default
    with open(CONFIG_FILE, "r") as f:
        return json.load(f)


def save_config(config: dict) -> None:
    """Save config to config.json."""
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def load_users() -> dict:
    """Load verified users from users.json."""
    if not os.path.exists(USERS_FILE):
        default = {"verified": [], "all_users": []}
        with open(USERS_FILE, "w") as f:
            json.dump(default, f, indent=2)
        return default
    with open(USERS_FILE, "r") as f:
        return json.load(f)


def save_users(users: dict) -> None:
    """Save users to users.json."""
    with open(USERS_FILE, "w") as f:
        json.dump(users, f, indent=2)


def add_verified_user(user_id: int) -> None:
    """Mark a user as verified."""
    users = load_users()
    if user_id not in users["verified"]:
        users["verified"].append(user_id)
    if user_id not in users.get("all_users", []):
        users.setdefault("all_users", []).append(user_id)
    save_users(users)


def remove_verified_user(user_id: int) -> None:
    """Remove a user from verified list."""
    users = load_users()
    if user_id in users["verified"]:
        users["verified"].remove(user_id)
        save_users(users)


def is_verified_user(user_id: int) -> bool:
    """Check persistent verified status (no live API call)."""
    users = load_users()
    return user_id in users["verified"]


def track_user(user_id: int) -> None:
    """Record any user who has interacted with the bot."""
    users = load_users()
    if user_id not in users.get("all_users", []):
        users.setdefault("all_users", []).append(user_id)
        save_users(users)


# ============================================
# CHANNEL MEMBERSHIP CHECK (CHAT_ID BASED)
# ============================================

async def check_single_channel(user_id: int, channel: dict, bot: Bot) -> bool:
    """
    Check if user is a member of a single channel/group.
    ALWAYS uses chat_id (integer) — never username strings — so private
    channels/groups are supported correctly.
    """
    chat_id = channel.get("chat_id")
    if not chat_id:
        logger.warning("Channel entry missing chat_id: %s", channel)
        return False

    try:
        member = await bot.get_chat_member(chat_id=int(chat_id), user_id=user_id)
        allowed_statuses = (
            ChatMemberMember,
            ChatMemberAdministrator,
            ChatMemberOwner,
        )
        return isinstance(member, allowed_statuses)
    except TelegramError as e:
        logger.error("Membership check failed for chat_id=%s user=%s: %s", chat_id, user_id, e)
        # If bot is not admin or chat not found, fail gracefully
        return False


async def check_all_channels(user_id: int, bot: Bot) -> bool:
    """Check membership in ALL configured channels/groups."""
    config = load_config()
    channels = config.get("channels", [])
    if not channels:
        return True  # No channels configured → everyone passes

    results = await asyncio.gather(
        *[check_single_channel(user_id, ch, bot) for ch in channels],
        return_exceptions=True,
    )
    return all(r is True for r in results)


async def check_which_channels_failed(user_id: int, bot: Bot) -> list:
    """Return list of channel dicts where user is NOT a member."""
    config = load_config()
    channels = config.get("channels", [])
    failed = []
    for ch in channels:
        ok = await check_single_channel(user_id, ch, bot)
        if not ok:
            failed.append(ch)
    return failed


async def is_user_currently_verified(user_id: int, bot: Bot) -> bool:
    """
    Fresh membership check with 5-minute cache.
    Removes from verified users if they've left any channel.
    Owner always passes.
    """
    if user_id == OWNER_ID:
        return True

    now = time.time()
    cached = membership_cache.get(user_id)
    if cached and (now - cached["timestamp"]) < CACHE_TTL:
        return cached["verified"]

    # Fresh API check
    verified = await check_all_channels(user_id, bot)
    membership_cache[user_id] = {"verified": verified, "timestamp": now}

    if not verified:
        remove_verified_user(user_id)
    else:
        add_verified_user(user_id)

    return verified


def invalidate_cache(user_id: int) -> None:
    """Force next check to bypass cache for this user."""
    membership_cache.pop(user_id, None)


# ============================================
# JOIN MESSAGE UI
# ============================================

def build_join_keyboard(channels: list, failed_ids: set = None) -> InlineKeyboardMarkup:
    """Build inline keyboard with join buttons for each channel/group."""
    buttons = []
    for ch in channels:
        name = ch.get("username") or ch.get("name") or f"Chat {ch['chat_id']}"
        link = ch.get("link", "")
        status = ""
        if failed_ids is not None:
            status = " ❌" if ch["chat_id"] in failed_ids else " ✅"
        label = f"{'📢' if ch.get('type') == 'channel' else '👥'} {name}{status}"
        row = []
        if link:
            row.append(InlineKeyboardButton(label, url=link))
        else:
            row.append(InlineKeyboardButton(label, callback_data="no_link"))
        buttons.append(row)

    buttons.append([InlineKeyboardButton("✅ Verify Membership", callback_data="verify")])
    return InlineKeyboardMarkup(buttons)


async def send_join_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the 'please join these channels' message."""
    config = load_config()
    channels = config.get("channels", [])

    if not channels:
        # No channels set — just pass through
        if original_start_handler:
            await original_start_handler(update, context)
        else:
            await update.message.reply_text("Welcome! Use /help to see available commands.")
        return

    text = (
        "🔐 *Access Restricted*\n\n"
        "To use this bot, you must join the following channels/groups:\n\n"
    )
    for i, ch in enumerate(channels, 1):
        name = ch.get("username") or ch.get("name") or f"Chat {ch['chat_id']}"
        ch_type = "Channel" if ch.get("type") == "channel" else "Group"
        text += f"{i}. {ch_type}: *{name}*\n"

    text += "\nAfter joining, click **✅ Verify Membership** below."
    keyboard = build_join_keyboard(channels)

    if update.callback_query:
        await update.callback_query.message.edit_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")


# ============================================
# ADMIN PANEL
# ============================================

def admin_panel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Add Channel", callback_data="admin_add_channel"),
            InlineKeyboardButton("➕ Add Group", callback_data="admin_add_group"),
        ],
        [InlineKeyboardButton("➖ Remove Channel/Group", callback_data="admin_remove")],
        [InlineKeyboardButton("📋 View All Channels/Groups", callback_data="admin_view")],
        [InlineKeyboardButton("📊 Bot Statistics", callback_data="admin_stats")],
        [InlineKeyboardButton("📣 Broadcast Message", callback_data="admin_broadcast")],
    ])


async def show_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send the admin panel (only to owner)."""
    text = "🔧 *Admin Panel*\n\nSelect an action:"
    keyboard = admin_panel_keyboard()
    if update.callback_query:
        await update.callback_query.message.reply_text(
            text, reply_markup=keyboard, parse_mode="Markdown"
        )
    else:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")


# ============================================
# /START HANDLER
# ============================================

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Main /start handler.
    - Owner → original bot start + admin panel
    - Verified user → original bot start (pass-through)
    - Unverified user → join channels message
    """
    user_id = update.effective_user.id
    track_user(user_id)

    if user_id == OWNER_ID:
        # Always grant owner access — call original bot start first
        if original_start_handler:
            await original_start_handler(update, context)
        else:
            await update.message.reply_text(
                f"Welcome back, Owner! 👑\n\nBot is running.",
                parse_mode="Markdown",
            )
        # Then send admin panel as a separate message
        await show_admin_panel(update, context)
        return

    # Regular user — live membership check
    if await is_user_currently_verified(user_id, context.bot):
        if original_start_handler:
            await original_start_handler(update, context)
        else:
            await update.message.reply_text("✅ You're verified! Use the bot features.")
    else:
        await send_join_message(update, context)


# ============================================
# VERIFY CALLBACK (user clicks ✅ Verify)
# ============================================

async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle the ✅ Verify Membership button click."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    track_user(user_id)

    # Invalidate cache to force fresh check
    invalidate_cache(user_id)

    config = load_config()
    channels = config.get("channels", [])
    failed = await check_which_channels_failed(user_id, context.bot)

    if not failed:
        # All passed — verify and show bot features
        add_verified_user(user_id)
        membership_cache[user_id] = {"verified": True, "timestamp": time.time()}

        # Delete the join message (clean UX)
        try:
            await query.message.delete()
        except TelegramError:
            pass

        # Pass control to original bot.py start handler
        if original_start_handler:
            # Simulate a /start update so the handler works correctly
            await original_start_handler(update, context)
        else:
            await context.bot.send_message(
                chat_id=user_id,
                text="✅ Verification complete! Use /help to see commands.",
            )
    else:
        # Some channels still not joined — show which ones failed
        failed_ids = {ch["chat_id"] for ch in failed}
        keyboard = build_join_keyboard(channels, failed_ids=failed_ids)
        failed_names = [
            ch.get("username") or ch.get("name") or f"Chat {ch['chat_id']}"
            for ch in failed
        ]
        text = (
            "❌ *Verification Failed*\n\n"
            "You have not joined the following:\n"
            + "\n".join(f"• {n}" for n in failed_names)
            + "\n\nPlease join them and click **✅ Verify Membership** again."
        )
        try:
            await query.message.edit_text(text, reply_markup=keyboard, parse_mode="Markdown")
        except TelegramError:
            await context.bot.send_message(
                chat_id=user_id, text=text, reply_markup=keyboard, parse_mode="Markdown"
            )


# ============================================
# VERIFICATION MIDDLEWARE WRAPPER
# ============================================

def verified_middleware(handler_func):
    """
    Wrap any handler so that EVERY interaction re-checks membership.
    Uses 5-minute cache to avoid spamming the Telegram API.
    """
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user:
            return await handler_func(update, context)

        user_id = user.id
        track_user(user_id)

        # Owner always passes
        if user_id == OWNER_ID:
            return await handler_func(update, context)

        # Live membership check (cached 5 min)
        if await is_user_currently_verified(user_id, context.bot):
            return await handler_func(update, context)
        else:
            # User has left a channel — notify them
            if update.message:
                await send_join_message(update, context)
            elif update.callback_query:
                await update.callback_query.answer(
                    "⚠️ You must join all required channels to use this bot.",
                    show_alert=True,
                )
            return

    wrapper.__name__ = getattr(handler_func, "__name__", "wrapped_handler")
    return wrapper


# ============================================
# JOIN REQUEST HANDLER
# ============================================

async def handle_join_request(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Auto-approve join requests for channels/groups configured with
    join_request=True. The user still needs to verify via /start.
    """
    request = update.chat_join_request
    config = load_config()
    for ch in config.get("channels", []):
        if ch.get("chat_id") == request.chat.id and ch.get("join_request", False):
            try:
                await context.bot.approve_chat_join_request(
                    chat_id=request.chat.id, user_id=request.from_user.id
                )
            except TelegramError as e:
                logger.error("Could not approve join request: %s", e)
            break


# ============================================
# ADMIN CALLBACK ROUTER
# ============================================

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route all admin_* callback queries. Owner-only."""
    query = update.callback_query
    user_id = query.from_user.id

    # Security: only owner can use admin panel
    if user_id != OWNER_ID:
        await query.answer("⛔ You are not authorized to use this.", show_alert=True)
        return

    data = query.data
    await query.answer()

    if data == "admin_add_channel":
        context.user_data["adding_type"] = "channel"
        await query.message.reply_text(
            "➕ *Add Channel*\n\n"
            "Please send the *Chat ID* of the channel.\n\n"
            "💡 The Chat ID is a negative number like `-1001234567890`.\n"
            "To find it: Add your bot to the channel as admin, then forward "
            "a message from the channel to @userinfobot.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back")]]
            ),
        )
        context.user_data["admin_state"] = ADD_CHAT_ID

    elif data == "admin_add_group":
        context.user_data["adding_type"] = "group"
        await query.message.reply_text(
            "➕ *Add Group*\n\n"
            "Please send the *Chat ID* of the group.\n\n"
            "💡 The Chat ID is a negative number like `-1009876543210`.\n"
            "To find it: Add your bot to the group, then forward a message "
            "from the group to @userinfobot.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back")]]
            ),
        )
        context.user_data["admin_state"] = ADD_CHAT_ID

    elif data == "admin_remove":
        await show_remove_list(update, context)

    elif data == "admin_view":
        await show_all_channels(update, context)

    elif data == "admin_stats":
        await show_statistics(update, context)

    elif data == "admin_broadcast":
        await query.message.reply_text(
            "📣 *Broadcast Message*\n\n"
            "Send the message you want to broadcast to all verified users.\n"
            "Supports text, photos, videos, and documents.\n\n"
            "Type /cancel to abort.",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back")]]
            ),
        )
        context.user_data["admin_state"] = BROADCAST_MSG

    elif data == "admin_back":
        await show_admin_panel(update, context)

    elif data.startswith("admin_remove_"):
        # admin_remove_<index>
        try:
            index = int(data.split("_")[-1])
            config = load_config()
            channels = config.get("channels", [])
            if 0 <= index < len(channels):
                removed = channels.pop(index)
                save_config(config)
                name = removed.get("username") or removed.get("name") or f"Chat {removed['chat_id']}"
                await query.message.edit_text(
                    f"✅ Removed *{name}* from required channels/groups.",
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back")]]
                    ),
                )
            else:
                await query.message.reply_text("❌ Invalid selection.")
        except (ValueError, IndexError) as e:
            logger.error("Remove callback error: %s", e)
            await query.message.reply_text("❌ Error removing entry.")

    elif data == "no_link":
        await query.answer("No join link configured for this chat.", show_alert=True)


# ============================================
# ADMIN: SHOW REMOVE LIST
# ============================================

async def show_remove_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    config = load_config()
    channels = config.get("channels", [])

    if not channels:
        await query.message.reply_text(
            "📭 No channels/groups configured yet.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back")]]
            ),
        )
        return

    buttons = []
    for i, ch in enumerate(channels):
        name = ch.get("username") or ch.get("name") or f"Chat {ch['chat_id']}"
        ch_type = "📢" if ch.get("type") == "channel" else "👥"
        buttons.append([
            InlineKeyboardButton(
                f"❌ Remove {ch_type} {name}",
                callback_data=f"admin_remove_{i}",
            )
        ])
    buttons.append([InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back")])

    await query.message.reply_text(
        "➖ *Remove Channel/Group*\n\nSelect the one to remove:",
        reply_markup=InlineKeyboardMarkup(buttons),
        parse_mode="Markdown",
    )


# ============================================
# ADMIN: VIEW ALL CHANNELS
# ============================================

async def show_all_channels(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    config = load_config()
    channels = config.get("channels", [])

    if not channels:
        text = "📭 No channels/groups configured yet."
    else:
        lines = ["📋 *Configured Channels/Groups*\n"]
        for i, ch in enumerate(channels, 1):
            name = ch.get("username") or ch.get("name") or "—"
            ch_type = "Channel" if ch.get("type") == "channel" else "Group"
            chat_id = ch.get("chat_id", "—")
            link = ch.get("link", "—")
            jr = "Yes" if ch.get("join_request") else "No"
            lines.append(
                f"{i}. *{name}*\n"
                f"   Type: {ch_type}\n"
                f"   Chat ID: `{chat_id}`\n"
                f"   Link: {link}\n"
                f"   Join Request: {jr}\n"
            )
        text = "\n".join(lines)

    await query.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back")]]
        ),
    )


# ============================================
# ADMIN: STATISTICS
# ============================================

async def show_statistics(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    users = load_users()
    config = load_config()

    total_users = len(users.get("all_users", []))
    verified_users = len(users.get("verified", []))
    channel_count = len(config.get("channels", []))

    text = (
        "📊 *Bot Statistics*\n\n"
        f"👤 Total Users: `{total_users}`\n"
        f"✅ Verified Users: `{verified_users}`\n"
        f"📢 Required Channels/Groups: `{channel_count}`\n"
        f"🕐 Generated: `{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}`"
    )
    await query.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back")]]
        ),
    )


# ============================================
# ADMIN: MESSAGE HANDLER (text input for admin flows)
# ============================================

async def admin_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Handle free-text input from owner during admin flows.
    Uses context.user_data["admin_state"] as state machine.
    """
    user_id = update.effective_user.id
    if user_id != OWNER_ID:
        return  # Only owner reaches here

    state = context.user_data.get("admin_state")
    if state is None:
        return  # Not in an admin flow — let other handlers process it

    text = update.message.text.strip()

    if text == "/cancel":
        context.user_data.pop("admin_state", None)
        context.user_data.pop("adding_type", None)
        context.user_data.pop("new_channel", None)
        await update.message.reply_text(
            "❌ Cancelled.",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back")]]
            ),
        )
        return

    # ── ADD CHANNEL/GROUP FLOW ──────────────────────────────────────────
    if state == ADD_CHAT_ID:
        # Expect a numeric chat_id
        try:
            chat_id = int(text)
        except ValueError:
            await update.message.reply_text(
                "❌ That doesn't look like a valid Chat ID.\n"
                "Chat IDs are integers, e.g. `-1001234567890`.\n"
                "Please try again or send /cancel."
            )
            return

        # Verify bot can access this chat
        try:
            chat = await context.bot.get_chat(chat_id)
        except TelegramError as e:
            await update.message.reply_text(
                f"❌ Could not access that chat: `{e}`\n\n"
                "Make sure the bot is an *admin* in that channel/group, "
                "then try again.",
                parse_mode="Markdown",
            )
            return

        # Store interim data
        context.user_data["new_channel"] = {
            "chat_id": chat_id,
            "name": chat.title or str(chat_id),
            "type": context.user_data.get("adding_type", "channel"),
        }
        context.user_data["admin_state"] = ADD_USERNAME
        await update.message.reply_text(
            f"✅ Found: *{chat.title}*\n\n"
            "Now send the *@username* of the channel/group (optional, for display).\n"
            "Or send `-` to skip.",
            parse_mode="Markdown",
        )

    elif state == ADD_USERNAME:
        nc = context.user_data.get("new_channel", {})
        if text != "-" and text:
            nc["username"] = text.lstrip("@")
        context.user_data["new_channel"] = nc
        context.user_data["admin_state"] = ADD_LINK
        await update.message.reply_text(
            "Now send the *join link* (t.me/... or t.me/+...) for the channel/group (optional).\n"
            "Or send `-` to skip.",
            parse_mode="Markdown",
        )

    elif state == ADD_LINK:
        nc = context.user_data.get("new_channel", {})
        if text != "-" and text:
            nc["link"] = text
        context.user_data["new_channel"] = nc
        context.user_data["admin_state"] = ADD_TYPE_CONFIRM

        # Confirm join_request support
        await update.message.reply_text(
            "Does this channel/group use *Join Requests* (users must request to join)?\n\n"
            "Reply *yes* or *no*.",
            parse_mode="Markdown",
        )

    elif state == ADD_TYPE_CONFIRM:
        nc = context.user_data.get("new_channel", {})
        if text.lower() in ("yes", "y"):
            nc["join_request"] = True
        else:
            nc["join_request"] = False

        # Save to config
        config = load_config()
        config["channels"].append(nc)
        save_config(config)

        name = nc.get("username") or nc.get("name") or f"Chat {nc['chat_id']}"
        ch_type = "Channel" if nc.get("type") == "channel" else "Group"
        await update.message.reply_text(
            f"✅ *{ch_type} Added Successfully!*\n\n"
            f"Name: *{name}*\n"
            f"Chat ID: `{nc['chat_id']}`\n"
            f"Join Request: {'Yes' if nc.get('join_request') else 'No'}",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back")]]
            ),
        )
        # Clear state
        context.user_data.pop("admin_state", None)
        context.user_data.pop("adding_type", None)
        context.user_data.pop("new_channel", None)

    # ── BROADCAST FLOW ──────────────────────────────────────────────────
    elif state == BROADCAST_MSG:
        users = load_users()
        verified = users.get("verified", [])
        if not verified:
            await update.message.reply_text(
                "📭 No verified users to broadcast to.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back")]]
                ),
            )
        else:
            sent = 0
            failed = 0
            status_msg = await update.message.reply_text(
                f"📣 Broadcasting to {len(verified)} users..."
            )
            for uid in verified:
                try:
                    await context.bot.copy_message(
                        chat_id=uid,
                        from_chat_id=update.message.chat_id,
                        message_id=update.message.message_id,
                    )
                    sent += 1
                    await asyncio.sleep(0.05)  # Avoid flood limits
                except TelegramError:
                    failed += 1

            await status_msg.edit_text(
                f"📣 *Broadcast Complete*\n\n"
                f"✅ Sent: {sent}\n"
                f"❌ Failed: {failed}",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("🔙 Back to Admin Panel", callback_data="admin_back")]]
                ),
            )
        context.user_data.pop("admin_state", None)


# ============================================
# BOT.PY AUTO-DETECTION & HANDLER IMPORT
# ============================================

def load_bot_module():
    """
    Dynamically import bot.py from the same directory.
    Returns the module, or None if not found.
    """
    bot_path = os.path.join(os.path.dirname(__file__), f"{BOT_MODULE}.py")
    if not os.path.exists(bot_path):
        logger.warning("bot.py not found at %s — running in standalone mode.", bot_path)
        return None

    spec = importlib.util.spec_from_file_location(BOT_MODULE, bot_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[BOT_MODULE] = module
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        logger.error("Failed to load bot.py: %s", e)
        return None

    return module


def extract_handlers_from_module(module) -> list:
    """
    Extract all handler objects from bot.py module using 3 strategies:
    1. Module-level attributes that are handler instances
    2. Lists of handlers
    3. Catch-all fallback via Application mock
    """
    from telegram.ext import BaseHandler

    handlers = []

    # Strategy 1: Direct handler objects in module
    for name in dir(module):
        obj = getattr(module, name, None)
        if isinstance(obj, BaseHandler):
            handlers.append(obj)
        elif isinstance(obj, (list, tuple)):
            for item in obj:
                if isinstance(item, BaseHandler) and item not in handlers:
                    handlers.append(item)

    return handlers


def capture_start_handler(module) -> None:
    """
    Find and capture the /start callback from bot.py.
    Sets the global `original_start_handler`.
    """
    global original_start_handler
    from telegram.ext import BaseHandler, CommandHandler as CH

    # First, check extracted handlers
    for attr_name in dir(module):
        obj = getattr(module, attr_name, None)
        if isinstance(obj, CH):
            cmds = getattr(obj, "commands", set())
            if "start" in cmds:
                original_start_handler = obj.callback
                logger.info("Captured /start handler from bot.py attribute: %s", attr_name)
                return

    # Second, look for a function named start / start_command / cmd_start
    for fname in ("start", "start_command", "cmd_start", "handle_start"):
        fn = getattr(module, fname, None)
        if callable(fn):
            original_start_handler = fn
            logger.info("Captured /start handler via function name: %s", fname)
            return

    logger.warning("Could not find /start handler in bot.py — using fallback.")


class MockApplication:
    """
    Fake Application used to intercept add_handler calls from bot.py
    setup functions (like `main()` or `setup()`).
    """
    def __init__(self):
        self._handlers: list = []

    def add_handler(self, handler, group=0):
        self._handlers.append(handler)

    def add_error_handler(self, *args, **kwargs):
        pass

    def run_polling(self, *args, **kwargs):
        pass  # Don't actually run — we just want handlers

    def __getattr__(self, item):
        # Absorb anything else silently
        return lambda *a, **kw: None


def extract_handlers_via_mock(module) -> list:
    """
    Call bot.py's setup/main function with a MockApplication to harvest handlers.
    """
    mock_app = MockApplication()

    for fname in ("setup", "register_handlers", "add_handlers", "init_handlers"):
        fn = getattr(module, fname, None)
        if callable(fn):
            try:
                result = fn(mock_app)
                if asyncio.iscoroutine(result):
                    pass  # Skip async setup functions
                logger.info("Called bot.py's %s() to harvest handlers.", fname)
                return mock_app._handlers
            except Exception as e:
                logger.warning("Could not call %s(): %s", fname, e)

    # Try calling main() (common pattern) — but catch SystemExit
    main_fn = getattr(module, "main", None)
    if callable(main_fn):
        try:
            mock_app2 = MockApplication()
            # Temporarily patch Application.builder in the module to return mock
            main_fn()
        except (SystemExit, Exception):
            pass

    return mock_app._handlers


# ============================================
# MAIN APPLICATION BUILDER
# ============================================

async def post_init(application: Application) -> None:
    """Called after bot initializes — log bot info."""
    me = await application.bot.get_me()
    logger.info("Bot started: @%s (ID: %s)", me.username, me.id)


def build_application() -> Application:
    """
    Build the main Application with all handlers.
    1. Load bot.py
    2. Capture /start handler
    3. Extract all other handlers
    4. Wrap them with verified_middleware
    5. Register our own handlers (start, verify, admin)
    6. Register wrapped bot.py handlers
    """
    global original_start_handler

    # ── Load bot.py ─────────────────────────────────────────────────────
    bot_module = load_bot_module()
    extracted_handlers = []

    if bot_module:
        capture_start_handler(bot_module)
        extracted_handlers = extract_handlers_from_module(bot_module)
        if not extracted_handlers:
            extracted_handlers = extract_handlers_via_mock(bot_module)
        logger.info("Extracted %d handler(s) from bot.py.", len(extracted_handlers))

    # ── Build application ────────────────────────────────────────────────
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    # ── Our own handlers (highest priority) ─────────────────────────────
    # /start — must be first, not wrapped (manages its own verification)
    app.add_handler(CommandHandler("start", start_command), group=0)

    # Verify button callback
    app.add_handler(
        CallbackQueryHandler(verify_callback, pattern="^verify$"), group=0
    )

    # No-link button (informational)
    app.add_handler(
        CallbackQueryHandler(
            lambda u, c: u.callback_query.answer("No link configured.", show_alert=True),
            pattern="^no_link$",
        ),
        group=0,
    )

    # All admin callbacks (owner-only enforced inside)
    app.add_handler(
        CallbackQueryHandler(admin_callback, pattern="^admin_"), group=0
    )

    # Owner free-text input for admin flows (owner only, checked inside)
    app.add_handler(
        MessageHandler(
            filters.TEXT & filters.User(OWNER_ID) & ~filters.COMMAND,
            admin_message_handler,
        ),
        group=0,
    )

    # Join request handler
    app.add_handler(ChatJoinRequestHandler(handle_join_request), group=0)

    # ── Wrap and register bot.py handlers ───────────────────────────────
    from telegram.ext import CommandHandler as CH

    for handler in extracted_handlers:
        # Skip /start from bot.py — we handle it ourselves
        if isinstance(handler, CH) and "start" in getattr(handler, "commands", set()):
            logger.info("Skipping bot.py /start handler (using our own).")
            continue

        # Wrap callback with membership middleware
        try:
            original_callback = handler.callback
            handler.callback = verified_middleware(original_callback)
            app.add_handler(handler, group=1)
        except Exception as e:
            logger.warning("Could not wrap handler %s: %s", handler, e)

    logger.info("Application built with all handlers.")
    return app


# ============================================
# ENTRY POINT
# ============================================

def main() -> None:
    """Run the bot."""
    if BOT_TOKEN == "YOUR_BOT_TOKEN_HERE":
        print(
            "ERROR: Please set your BOT_TOKEN in main.py before running.\n"
            "       Open main.py and replace 'YOUR_BOT_TOKEN_HERE' with your token."
        )
        sys.exit(1)

    if OWNER_ID == 123456789:
        print(
            "WARNING: OWNER_ID is still set to the default (123456789).\n"
            "         Update it to your actual Telegram user ID."
        )

    app = build_application()
    logger.info("Starting bot... Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
