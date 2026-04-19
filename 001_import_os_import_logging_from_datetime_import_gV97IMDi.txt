import os
import logging
from datetime import datetime
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

# Import from other modules (these will be separate files)
# from models_and_utils import Database, validate_phone_number, get_timestamp
# from whatsapp_client import WhatsAppClient
# from group_handlers import get_group_handlers
# from member_handlers import get_member_handlers
# from admin_handlers import get_admin_handlers

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment variables
# ---------------------------------------------------------------------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
BAILEYS_API_URL = os.environ.get("BAILEYS_API_URL", "http://localhost:3000")
SUPPORT_CONTACT = os.environ.get("SUPPORT_CONTACT", "@YourSupportHandle")

# ---------------------------------------------------------------------------
# ConversationHandler States
# ---------------------------------------------------------------------------
# Connect flow
SELECT_METHOD = 0
PHONE_INPUT = 1
OTP_INPUT = 2
QR_SCAN = 3
CONNECTED = 4

# Disconnect flow
DISCONNECT_CONFIRM = 10

# Feature menu state (after account selection)
FEATURE_MENU = 20
ACCOUNT_SELECTION = 21

# ---------------------------------------------------------------------------
# Hinglish Messages
# ---------------------------------------------------------------------------
MSG_WELCOME = (
    "🤖 *WhatsApp Bot mein Aapka Swagat Hai!*\n\n"
    "Namaste! Main aapka WhatsApp assistant hoon. 😊\n\n"
    "Neeche diye gaye options mein se koi bhi choose karein:\n\n"
    "📱 *Connect WhatsApp* — Apna WhatsApp account jodein\n"
    "🔌 *Disconnect WhatsApp* — Account hatayein\n"
    "👤 *Connected Account* — Jude hue accounts dekhein\n"
    "❓ *Help* — Madad aur features ki jaankari\n\n"
    "_Koi bhi option choose karein aur shuru karein!_ 🚀"
)

MSG_CONNECT_METHOD = (
    "📱 *WhatsApp Connect Karein*\n\n"
    "Kaise connect karna chahte hain?\n\n"
    "🔢 *Phone Number* — OTP se verify karein\n"
    "📷 *QR Code* — QR scan karke connect karein\n\n"
    "_Apna preferred method chunein:_"
)

MSG_PHONE_INPUT = (
    "📞 *Phone Number Darj Karein*\n\n"
    "Apna WhatsApp number country code ke saath likhein.\n\n"
    "✅ *Sahi format:* `+91XXXXXXXXXX`\n"
    "❌ *Galat format:* `91XXXXXXXXXX` ya `0XXXXXXXXXX`\n\n"
    "_Ab apna number type karein:_"
)

MSG_OTP_SENT = (
    "✅ *OTP Bheja Gaya!*\n\n"
    "📱 Aapke number `{phone}` par OTP bheja gaya hai.\n\n"
    "⏳ OTP aane mein thoda waqt lag sakta hai.\n"
    "🔢 6-digit OTP yahan enter karein:\n\n"
    "_Agar OTP na aaye toh /cancel karke dobara try karein._"
)

MSG_OTP_INVALID = (
    "❌ *Galat OTP!*\n\n"
    "Aapne jo OTP diya woh galat hai ya expire ho gaya.\n\n"
    "🔄 Dobara sahi OTP enter karein ya /cancel karke restart karein."
)

MSG_QR_GENERATING = (
    "⏳ *QR Code Ban Raha Hai...*\n\n"
    "Thoda ruko, aapka QR code generate ho raha hai. 🔄"
)

MSG_QR_SCAN = (
    "📷 *QR Code Scan Karein*\n\n"
    "Neeche diya gaya QR code apne WhatsApp se scan karein:\n\n"
    "📲 *Kaise karein:*\n"
    "1️⃣ WhatsApp kholein\n"
    "2️⃣ Menu (⋮) → Linked Devices\n"
    "3️⃣ 'Link a Device' tap karein\n"
    "4️⃣ Is QR code ko scan karein\n\n"
    "⏳ Scan karne ke baad automatically connect ho jayega...\n"
    "_QR code 60 seconds mein expire hoga._"
)

MSG_CONNECT_SUCCESS = (
    "🎉 *WhatsApp Connect Ho Gaya!*\n\n"
    "✅ Account successfully jud gaya!\n\n"
    "📱 *Number:* `{phone}`\n"
    "👤 *Name:* {name}\n"
    "🕐 *Connected At:* {timestamp}\n"
    "🆔 *Account ID:* `{account_id}`\n\n"
    "Ab aap is account se sabhi features use kar sakte hain! 🚀\n\n"
    "_Main menu par wapas jaane ke liye button dabayein._"
)

MSG_NO_ACCOUNTS = (
    "😕 *Koi Account Connected Nahi Hai*\n\n"
    "Abhi tak koi WhatsApp account connect nahi kiya gaya.\n\n"
    "📱 Pehle koi account connect karein!\n"
    "_'Connect WhatsApp' button dabayein._"
)

MSG_ACCOUNTS_LIST = (
    "👤 *Connected Accounts*\n\n"
    "Aapke jude hue WhatsApp accounts:\n\n"
    "{accounts_text}\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "Naya account jodne ke liye neeche ka button dabayein. ➕"
)

MSG_ACCOUNT_DETAIL = (
    "📋 *Account Details*\n\n"
    "📱 *Number:* `{phone}`\n"
    "👤 *Name:* {name}\n"
    "🟢 *Status:* {status}\n"
    "📅 *Connected Since:* {connected_since}\n"
    "👥 *Groups:* {group_count}\n"
    "🆔 *Account ID:* `{account_id}`"
)

MSG_DISCONNECT_LIST = (
    "🔌 *Disconnect Karein*\n\n"
    "Kaun sa account disconnect karna chahte hain?\n\n"
    "{accounts_text}\n"
    "⚠️ _Disconnect karne ke baad us account ki saari settings hata di jayengi._"
)

MSG_DISCONNECT_CONFIRM = (
    "⚠️ *Confirm Karein*\n\n"
    "Kya aap sach mein `{phone}` ko disconnect karna chahte hain?\n\n"
    "🗑️ Is action ko undo nahi kiya ja sakta.\n"
    "_Haan ya Nahi chunein:_"
)

MSG_DISCONNECT_ALL_CONFIRM = (
    "⚠️ *Sabhi Accounts Disconnect Karein?*\n\n"
    "Kya aap sach mein *sabhi* connected accounts disconnect karna chahte hain?\n\n"
    "❌ Ye action sabhi `{count}` accounts ko hata dega.\n"
    "_Ye action undo nahi hoga!_"
)

MSG_DISCONNECT_SUCCESS = (
    "✅ *Disconnect Ho Gaya!*\n\n"
    "📱 `{phone}` successfully disconnect ho gaya.\n\n"
    "Kisi aur account ke liye main menu par wapas jayein. 🔙"
)

MSG_DISCONNECT_ALL_SUCCESS = (
    "✅ *Sabhi Accounts Disconnect Ho Gaye!*\n\n"
    "🗑️ Sabhi `{count}` accounts successfully disconnect ho gaye.\n\n"
    "_Ab koi bhi WhatsApp account connected nahi hai._"
)

MSG_HELP = (
    "❓ *Help & Features Guide*\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "🤖 *Bot ke Features:*\n\n"
    "➕ *Create Group* — Naya WhatsApp group banayein\n"
    "🔗 *Join Groups* — Links se groups join karein\n"
    "🔍 *CTC Checker* — Contact ka WhatsApp check karein\n"
    "📎 *Get Link* — Group invite link hasil karein\n"
    "🚪 *Leave Groups* — Ek ya sab groups chhoden\n"
    "🗑️ *Remove Members* — Members ko group se hatayein\n"
    "👑 *Make/Remove Admin* — Admin rights manage karein\n"
    "✅ *Approval Setting* — Group join approval set karein\n"
    "📋 *Get Pending List* — Pending join requests dekhein\n"
    "👥 *Add Members* — Members ko group mein jodein\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    "📱 *Multiple Accounts:*\n"
    "Aap unlimited WhatsApp accounts connect kar sakte hain!\n\n"
    "🔄 *Account Switch:*\n"
    "Har feature use karne se pehle account select kar sakte hain.\n\n"
    "━━━━━━━━━━━━━━━━━━━━\n"
    f"📞 *Support:* {SUPPORT_CONTACT}\n"
    "⏰ *Timing:* 24/7 Available\n\n"
    "_Kisi bhi problem ke liye support se contact karein!_ 🙏"
)

MSG_SELECT_ACCOUNT = (
    "👤 *Account Chunein*\n\n"
    "Kaun se account se ye feature use karna chahte hain?\n\n"
    "{accounts_text}\n"
    "💡 _'All Accounts' se sabhi accounts par ek saath kaam karo._"
)

MSG_FEATURE_MENU = (
    "⚡ *Feature Menu*\n\n"
    "📱 *Active Account:* `{phone}`\n\n"
    "Kya karna chahte hain? Neeche se chunein:"
)

MSG_CANCEL = (
    "❌ *Cancel Ho Gaya!*\n\n"
    "Aapka current action cancel kar diya gaya hai.\n\n"
    "_Main menu par wapas jaane ke liye /start likhein._"
)

MSG_ERROR = (
    "😞 *Kuch Gadbad Ho Gayi!*\n\n"
    "Ek unexpected error aaya:\n`{error}`\n\n"
    "🔄 Dobara try karein ya /start se restart karein.\n"
    f"_Agar problem bani rahe toh {SUPPORT_CONTACT} se contact karein._"
)

MSG_PHONE_INVALID = (
    "❌ *Galat Phone Number!*\n\n"
    "Aapne jo number diya woh valid nahi hai.\n\n"
    "✅ *Sahi format:* `+91XXXXXXXXXX`\n"
    "🔢 Country code zaroori hai (e.g., +91 India ke liye)\n\n"
    "_Dobara sahi number enter karein:_"
)

MSG_CONNECTING = (
    "⏳ *Connect Ho Raha Hai...*\n\n"
    "Aapka WhatsApp account connect ho raha hai.\n"
    "Thoda ruko... 🔄"
)

# ---------------------------------------------------------------------------
# Helper utilities (inline — real implementation via models_and_utils)
# ---------------------------------------------------------------------------

def validate_phone_number(phone: str) -> bool:
    """Basic phone number validation."""
    import re
    pattern = r"^\+[1-9]\d{6,14}$"
    return bool(re.match(pattern, phone.strip()))


def get_timestamp() -> str:
    """Return current datetime formatted for display."""
    return datetime.now().strftime("%d %b %Y, %I:%M %p")


def format_accounts_text(accounts: list[dict]) -> str:
    """Format account list into readable Hinglish text."""
    if not accounts:
        return "_Koi account nahi mila._\n"
    lines = []
    for i, acc in enumerate(accounts, 1):
        status_emoji = "🟢" if acc.get("status") == "connected" else "🔴"
        lines.append(
            f"{i}. {status_emoji} `{acc['phone']}` — {acc.get('name', 'Unknown')}\n"
            f"   👥 Groups: {acc.get('group_count', 0)} | "
            f"📅 {acc.get('connected_since', 'N/A')}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# In-memory "database" for demo (replace with real DB later)
# ---------------------------------------------------------------------------
# Structure: { user_id: [ {account_id, phone, name, status, connected_since, group_count}, ... ] }
_user_accounts: dict[int, list[dict]] = {}


def get_user_accounts(user_id: int) -> list[dict]:
    return _user_accounts.get(user_id, [])


def add_user_account(user_id: int, account: dict) -> None:
    if user_id not in _user_accounts:
        _user_accounts[user_id] = []
    _user_accounts[user_id].append(account)


def remove_user_account(user_id: int, account_id: str) -> bool:
    accounts = _user_accounts.get(user_id, [])
    new_accounts = [a for a in accounts if a["account_id"] != account_id]
    if len(new_accounts) == len(accounts):
        return False
    _user_accounts[user_id] = new_accounts
    return True


def remove_all_user_accounts(user_id: int) -> int:
    count = len(_user_accounts.get(user_id, []))
    _user_accounts[user_id] = []
    return count


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📱 Connect WhatsApp", callback_data="menu_connect"),
            InlineKeyboardButton("🔌 Disconnect WhatsApp", callback_data="menu_disconnect"),
        ],
        [
            InlineKeyboardButton("👤 Connected Account", callback_data="menu_accounts"),
            InlineKeyboardButton("❓ Help", callback_data="menu_help"),
        ],
    ])


def connect_method_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔢 Phone Number", callback_data="connect_phone"),
            InlineKeyboardButton("📷 QR Code", callback_data="connect_qr"),
        ],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="menu_back")],
    ])


def back_to_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔙 Main Menu", callback_data="menu_back")],
    ])


def add_account_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Naya Account Jodein", callback_data="menu_connect")],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="menu_back")],
    ])


def disconnect_accounts_keyboard(accounts: list[dict]) -> InlineKeyboardMarkup:
    buttons = []
    for acc in accounts:
        label = f"🔌 {acc['phone']} ({acc.get('name', 'Unknown')})"
        buttons.append(
            [InlineKeyboardButton(label, callback_data=f"disconnect_{acc['account_id']}")]
        )
    buttons.append([InlineKeyboardButton("💥 Sab Disconnect", callback_data="disconnect_all")])
    buttons.append([InlineKeyboardButton("🔙 Main Menu", callback_data="menu_back")])
    return InlineKeyboardMarkup(buttons)


def confirm_keyboard(yes_data: str, no_data: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Haan", callback_data=yes_data),
            InlineKeyboardButton("❌ Nahi", callback_data=no_data),
        ],
    ])


def account_selection_keyboard(accounts: list[dict], prefix: str = "select") -> InlineKeyboardMarkup:
    buttons = []
    for acc in accounts:
        label = f"📱 {acc['phone']} — {acc.get('name', 'Unknown')}"
        buttons.append(
            [InlineKeyboardButton(label, callback_data=f"{prefix}_{acc['account_id']}")]
        )
    buttons.append([InlineKeyboardButton("🌐 All Accounts", callback_data=f"{prefix}_all")])
    buttons.append([InlineKeyboardButton("🔙 Main Menu", callback_data="menu_back")])
    return InlineKeyboardMarkup(buttons)


def feature_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Create Group", callback_data="feature_create_group"),
            InlineKeyboardButton("🔗 Join Groups", callback_data="feature_join_groups"),
        ],
        [
            InlineKeyboardButton("🔍 CTC Checker", callback_data="feature_ctc_checker"),
            InlineKeyboardButton("📎 Get Link", callback_data="feature_get_link"),
        ],
        [
            InlineKeyboardButton("🚪 Leave Groups", callback_data="feature_leave_groups"),
            InlineKeyboardButton("🗑️ Remove Members", callback_data="feature_remove_members"),
        ],
        [
            InlineKeyboardButton("👑 Make/Remove Admin", callback_data="feature_admin"),
            InlineKeyboardButton("✅ Approval Setting", callback_data="feature_approval"),
        ],
        [
            InlineKeyboardButton("📋 Get Pending List", callback_data="feature_pending_list"),
            InlineKeyboardButton("👥 Add Members", callback_data="feature_add_members"),
        ],
        [InlineKeyboardButton("🔙 Main Menu", callback_data="menu_back")],
    ])


# ---------------------------------------------------------------------------
# /start command handler
# ---------------------------------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start — show welcome message with main menu."""
    context.user_data.clear()
    await update.message.reply_text(
        MSG_WELCOME,
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )


# ---------------------------------------------------------------------------
# /cancel command handler
# ---------------------------------------------------------------------------

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Cancel any ongoing conversation and return to idle."""
    context.user_data.clear()
    if update.message:
        await update.message.reply_text(
            MSG_CANCEL,
            parse_mode="Markdown",
            reply_markup=back_to_menu_keyboard(),
        )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Main menu callback (non-conversation)
# ---------------------------------------------------------------------------

async def menu_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Return to main menu from any state."""
    query = update.callback_query
    await query.answer()
    context.user_data.clear()
    await query.edit_message_text(
        MSG_WELCOME,
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Help callback (non-conversation)
# ---------------------------------------------------------------------------

async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle Help button — show features guide."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        MSG_HELP,
        parse_mode="Markdown",
        reply_markup=back_to_menu_keyboard(),
    )


# ---------------------------------------------------------------------------
# Connected Accounts callback (non-conversation)
# ---------------------------------------------------------------------------

async def accounts_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show all connected accounts with details."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    accounts = get_user_accounts(user_id)

    if not accounts:
        await query.edit_message_text(
            MSG_NO_ACCOUNTS,
            parse_mode="Markdown",
            reply_markup=add_account_keyboard(),
        )
        return

    accounts_text = format_accounts_text(accounts)
    msg = MSG_ACCOUNTS_LIST.format(accounts_text=accounts_text)

    # Build per-account detail buttons
    buttons = []
    for acc in accounts:
        label = f"📋 {acc['phone']} — {acc.get('name', 'Unknown')}"
        buttons.append(
            [InlineKeyboardButton(label, callback_data=f"account_detail_{acc['account_id']}")]
        )
    buttons.append([InlineKeyboardButton("➕ Naya Account Jodein", callback_data="menu_connect")])
    buttons.append([InlineKeyboardButton("🔙 Main Menu", callback_data="menu_back")])

    await query.edit_message_text(
        msg,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


async def account_detail_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show detailed info for a specific account."""
    query = update.callback_query
    await query.answer()
    account_id = query.data.replace("account_detail_", "")
    user_id = update.effective_user.id
    accounts = get_user_accounts(user_id)
    acc = next((a for a in accounts if a["account_id"] == account_id), None)

    if not acc:
        await query.edit_message_text(
            "❌ Account nahi mila. Shayad already disconnect ho gaya.",
            parse_mode="Markdown",
            reply_markup=back_to_menu_keyboard(),
        )
        return

    detail_msg = MSG_ACCOUNT_DETAIL.format(
        phone=acc["phone"],
        name=acc.get("name", "Unknown"),
        status="🟢 Connected" if acc.get("status") == "connected" else "🔴 Disconnected",
        connected_since=acc.get("connected_since", "N/A"),
        group_count=acc.get("group_count", 0),
        account_id=acc["account_id"],
    )

    buttons = [
        [InlineKeyboardButton("⚡ Use This Account", callback_data=f"use_account_{account_id}")],
        [InlineKeyboardButton("🔌 Disconnect", callback_data=f"disconnect_{account_id}")],
        [InlineKeyboardButton("🔙 Accounts List", callback_data="menu_accounts")],
    ]
    await query.edit_message_text(
        detail_msg,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(buttons),
    )


# ---------------------------------------------------------------------------
# Connect WhatsApp — ConversationHandler
# ---------------------------------------------------------------------------

async def connect_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for Connect WhatsApp flow."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        MSG_CONNECT_METHOD,
        parse_mode="Markdown",
        reply_markup=connect_method_keyboard(),
    )
    return SELECT_METHOD


async def connect_phone_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User selected Phone Number method."""
    query = update.callback_query
    await query.answer()
    context.user_data["connect_method"] = "phone"
    await query.edit_message_text(
        MSG_PHONE_INPUT,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data="menu_back")]
        ]),
    )
    return PHONE_INPUT


async def connect_qr_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User selected QR Code method."""
    query = update.callback_query
    await query.answer()
    context.user_data["connect_method"] = "qr"

    # Show "generating QR" message
    await query.edit_message_text(
        MSG_QR_GENERATING,
        parse_mode="Markdown",
    )

    # --- Real implementation: call WhatsApp API to generate QR ---
    # qr_image_bytes = await WhatsAppClient(BAILEYS_API_URL).generate_qr()
    # For now, send a placeholder and instruct user
    # In production, send qr_image_bytes as a photo via context.bot.send_photo

    # Simulate QR session ID
    import uuid
    session_id = str(uuid.uuid4())[:8].upper()
    context.user_data["qr_session_id"] = session_id

    await query.edit_message_text(
        MSG_QR_SCAN + f"\n\n🆔 *Session:* `{session_id}`",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Naya QR Generate Karein", callback_data="connect_qr")],
            [InlineKeyboardButton("✅ Maine Scan Kar Liya", callback_data="qr_scanned")],
            [InlineKeyboardButton("❌ Cancel", callback_data="menu_back")],
        ]),
    )
    return QR_SCAN


async def phone_number_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive and validate phone number, then trigger OTP."""
    phone = update.message.text.strip()

    if not validate_phone_number(phone):
        await update.message.reply_text(
            MSG_PHONE_INVALID,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("❌ Cancel", callback_data="menu_back")]
            ]),
        )
        return PHONE_INPUT

    context.user_data["phone"] = phone

    # Send "connecting" message
    connecting_msg = await update.message.reply_text(
        MSG_CONNECTING,
        parse_mode="Markdown",
    )

    # --- Real implementation: call WhatsApp API to send OTP ---
    # try:
    #     client = WhatsAppClient(BAILEYS_API_URL)
    #     await client.send_otp(phone)
    # except Exception as e:
    #     await connecting_msg.edit_text(MSG_ERROR.format(error=str(e)), parse_mode="Markdown")
    #     return ConversationHandler.END

    await connecting_msg.edit_text(
        MSG_OTP_SENT.format(phone=phone),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data="menu_back")]
        ]),
    )
    return OTP_INPUT


async def otp_received(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Receive OTP, verify with WhatsApp API, and finalise connection."""
    otp = update.message.text.strip()
    phone = context.user_data.get("phone", "")

    # Basic OTP format check
    if not otp.isdigit() or len(otp) < 4:
        await update.message.reply_text(
            MSG_OTP_INVALID,
            parse_mode="Markdown",
        )
        return OTP_INPUT

    verifying_msg = await update.message.reply_text(
        "⏳ *OTP Verify Ho Raha Hai...*",
        parse_mode="Markdown",
    )

    # --- Real implementation: verify OTP with WhatsApp API ---
    # try:
    #     client = WhatsAppClient(BAILEYS_API_URL)
    #     result = await client.verify_otp(phone, otp)
    #     account_id = result["account_id"]
    #     name = result["name"]
    # except InvalidOTPError:
    #     await verifying_msg.edit_text(MSG_OTP_INVALID, parse_mode="Markdown")
    #     return OTP_INPUT
    # except Exception as e:
    #     await verifying_msg.edit_text(MSG_ERROR.format(error=str(e)), parse_mode="Markdown")
    #     return ConversationHandler.END

    # Simulated success
    import uuid
    account_id = str(uuid.uuid4())[:8]
    name = "User"
    timestamp = get_timestamp()
    user_id = update.effective_user.id

    add_user_account(user_id, {
        "account_id": account_id,
        "phone": phone,
        "name": name,
        "status": "connected",
        "connected_since": timestamp,
        "group_count": 0,
    })

    context.user_data["active_account_id"] = account_id
    context.user_data["active_phone"] = phone

    await verifying_msg.edit_text(
        MSG_CONNECT_SUCCESS.format(
            phone=phone,
            name=name,
            timestamp=timestamp,
            account_id=account_id,
        ),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⚡ Features Use Karein", callback_data=f"use_account_{account_id}")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="menu_back")],
        ]),
    )
    return CONNECTED


async def qr_scanned_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle user confirming QR scan."""
    query = update.callback_query
    await query.answer()

    session_id = context.user_data.get("qr_session_id", "")
    checking_msg_text = f"⏳ *Session Check Ho Raha Hai...*\n\n🆔 Session: `{session_id}`"
    await query.edit_message_text(checking_msg_text, parse_mode="Markdown")

    # --- Real implementation: poll WhatsApp API for session status ---
    # try:
    #     client = WhatsAppClient(BAILEYS_API_URL)
    #     result = await client.check_qr_session(session_id)
    #     if not result["connected"]:
    #         raise Exception("QR scan nahi hua ya session expire ho gaya.")
    #     phone = result["phone"]
    #     name = result["name"]
    #     account_id = result["account_id"]
    # except Exception as e:
    #     await query.edit_message_text(MSG_ERROR.format(error=str(e)), parse_mode="Markdown")
    #     return ConversationHandler.END

    # Simulated success
    import uuid
    account_id = str(uuid.uuid4())[:8]
    phone = "+91XXXXXXXXXX"
    name = "QR User"
    timestamp = get_timestamp()
    user_id = update.effective_user.id

    add_user_account(user_id, {
        "account_id": account_id,
        "phone": phone,
        "name": name,
        "status": "connected",
        "connected_since": timestamp,
        "group_count": 0,
    })

    context.user_data["active_account_id"] = account_id
    context.user_data["active_phone"] = phone

    await query.edit_message_text(
        MSG_CONNECT_SUCCESS.format(
            phone=phone,
            name=name,
            timestamp=timestamp,
            account_id=account_id,
        ),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("⚡ Features Use Karein", callback_data=f"use_account_{account_id}")],
            [InlineKeyboardButton("🔙 Main Menu", callback_data="menu_back")],
        ]),
    )
    return CONNECTED


async def connected_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Terminal state — connected. Show main menu."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        MSG_WELCOME,
        parse_mode="Markdown",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Disconnect WhatsApp — ConversationHandler
# ---------------------------------------------------------------------------

async def disconnect_entry(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point for Disconnect WhatsApp flow."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    accounts = get_user_accounts(user_id)

    if not accounts:
        await query.edit_message_text(
            MSG_NO_ACCOUNTS,
            parse_mode="Markdown",
            reply_markup=add_account_keyboard(),
        )
        return ConversationHandler.END

    accounts_text = format_accounts_text(accounts)
    await query.edit_message_text(
        MSG_DISCONNECT_LIST.format(accounts_text=accounts_text),
        parse_mode="Markdown",
        reply_markup=disconnect_accounts_keyboard(accounts),
    )
    return DISCONNECT_CONFIRM


async def disconnect_account_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User selected a specific account to disconnect — ask for confirmation."""
    query = update.callback_query
    await query.answer()
    account_id = query.data.replace("disconnect_", "")
    user_id = update.effective_user.id
    accounts = get_user_accounts(user_id)
    acc = next((a for a in accounts if a["account_id"] == account_id), None)

    if not acc:
        await query.edit_message_text(
            "❌ Account nahi mila.",
            parse_mode="Markdown",
            reply_markup=back_to_menu_keyboard(),
        )
        return ConversationHandler.END

    context.user_data["disconnect_account_id"] = account_id
    context.user_data["disconnect_phone"] = acc["phone"]

    await query.edit_message_text(
        MSG_DISCONNECT_CONFIRM.format(phone=acc["phone"]),
        parse_mode="Markdown",
        reply_markup=confirm_keyboard(
            yes_data=f"confirm_disconnect_{account_id}",
            no_data="menu_back",
        ),
    )
    return DISCONNECT_CONFIRM


async def disconnect_all_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """User chose to disconnect all accounts — ask for confirmation."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    accounts = get_user_accounts(user_id)
    count = len(accounts)

    context.user_data["disconnect_all"] = True

    await query.edit_message_text(
        MSG_DISCONNECT_ALL_CONFIRM.format(count=count),
        parse_mode="Markdown",
        reply_markup=confirm_keyboard(
            yes_data="confirm_disconnect_all",
            no_data="menu_back",
        ),
    )
    return DISCONNECT_CONFIRM


async def confirm_disconnect_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Execute disconnect after user confirmation."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    data = query.data

    if data == "confirm_disconnect_all":
        count = remove_all_user_accounts(user_id)
        await query.edit_message_text(
            MSG_DISCONNECT_ALL_SUCCESS.format(count=count),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Naya Account Jodein", callback_data="menu_connect")],
                [InlineKeyboardButton("🔙 Main Menu", callback_data="menu_back")],
            ]),
        )
    else:
        account_id = data.replace("confirm_disconnect_", "")
        phone = context.user_data.get("disconnect_phone", account_id)
        remove_user_account(user_id, account_id)
        await query.edit_message_text(
            MSG_DISCONNECT_SUCCESS.format(phone=phone),
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔌 Aur Disconnect Karein", callback_data="menu_disconnect")],
                [InlineKeyboardButton("🔙 Main Menu", callback_data="menu_back")],
            ]),
        )

    context.user_data.clear()
    return ConversationHandler.END


# ---------------------------------------------------------------------------
# Account Selection + Feature Menu
# ---------------------------------------------------------------------------

async def use_account_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Select a specific account and show feature menu."""
    query = update.callback_query
    await query.answer()
    account_id = query.data.replace("use_account_", "")
    user_id = update.effective_user.id
    accounts = get_user_accounts(user_id)
    acc = next((a for a in accounts if a["account_id"] == account_id), None)

    if not acc:
        await query.edit_message_text(
            "❌ Account nahi mila.",
            parse_mode="Markdown",
            reply_markup=back_to_menu_keyboard(),
        )
        return ConversationHandler.END

    context.user_data["active_account_id"] = account_id
    context.user_data["active_phone"] = acc["phone"]

    await query.edit_message_text(
        MSG_FEATURE_MENU.format(phone=acc["phone"]),
        parse_mode="Markdown",
        reply_markup=feature_menu_keyboard(),
    )
    return FEATURE_MENU


async def feature_account_selection(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """When multiple accounts exist, ask which one to use for a feature."""
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    accounts = get_user_accounts(user_id)

    if not accounts:
        await query.edit_message_text(
            MSG_NO_ACCOUNTS,
            parse_mode="Markdown",
            reply_markup=add_account_keyboard(),
        )
        return ConversationHandler.END

    if len(accounts) == 1:
        # Auto-select single account
        acc = accounts[0]
        context.user_data["active_account_id"] = acc["account_id"]
        context.user_data["active_phone"] = acc["phone"]
        await query.edit_message_text(
            MSG_FEATURE_MENU.format(phone=acc["phone"]),
            parse_mode="Markdown",
            reply_markup=feature_menu_keyboard(),
        )
        return FEATURE_MENU

    accounts_text = format_accounts_text(accounts)
    await query.edit_message_text(
        MSG_SELECT_ACCOUNT.format(accounts_text=accounts_text),
        parse_mode="Markdown",
        reply_markup=account_selection_keyboard(accounts, prefix="use_account"),
    )
    return ACCOUNT_SELECTION


async def feature_placeholder_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Placeholder handler for feature buttons — redirects to respective modules."""
    query = update.callback_query
    await query.answer()
    feature = query.data.replace("feature_", "").replace("_", " ").title()
    phone = context.user_data.get("active_phone", "Unknown")

    await query.edit_message_text(
        f"⚡ *{feature}*\n\n"
        f"📱 Active Account: `{phone}`\n\n"
        f"🔄 Ye feature load ho raha hai...\n"
        f"_(Ye feature alag module se handle hoga)_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Feature Menu", callback_data=f"use_account_{context.user_data.get('active_account_id', '')}")]
        ]),
    )


# ---------------------------------------------------------------------------
# Error handler
# ---------------------------------------------------------------------------

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Log errors and notify user if possible."""
    logger.error("Exception while handling an update:", exc_info=context.error)

    error_msg = str(context.error) if context.error else "Unknown error"

    if isinstance(update, Update):
        if update.effective_message:
            try:
                await update.effective_message.reply_text(
                    MSG_ERROR.format(error=error_msg),
                    parse_mode="Markdown",
                    reply_markup=back_to_menu_keyboard(),
                )
            except Exception:
                pass
        elif update.callback_query:
            try:
                await update.callback_query.message.reply_text(
                    MSG_ERROR.format(error=error_msg),
                    parse_mode="Markdown",
                    reply_markup=back_to_menu_keyboard(),
                )
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Build Application
# ---------------------------------------------------------------------------

def main() -> None:
    """Build and run the Telegram bot."""
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN environment variable not set!")

    app = Application.builder().token(BOT_TOKEN).build()

    # -----------------------------------------------------------------------
    # Connect WhatsApp ConversationHandler
    # -----------------------------------------------------------------------
    connect_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(connect_entry, pattern="^menu_connect$")],
        states={
            SELECT_METHOD: [
                CallbackQueryHandler(connect_phone_selected, pattern="^connect_phone$"),
                CallbackQueryHandler(connect_qr_selected, pattern="^connect_qr$"),
                CallbackQueryHandler(menu_back_callback, pattern="^menu_back$"),
            ],
            PHONE_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, phone_number_received),
                CallbackQueryHandler(menu_back_callback, pattern="^menu_back$"),
            ],
            OTP_INPUT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, otp_received),
                CallbackQueryHandler(menu_back_callback, pattern="^menu_back$"),
            ],
            QR_SCAN: [
                CallbackQueryHandler(qr_scanned_callback, pattern="^qr_scanned$"),
                CallbackQueryHandler(connect_qr_selected, pattern="^connect_qr$"),
                CallbackQueryHandler(menu_back_callback, pattern="^menu_back$"),
            ],
            CONNECTED: [
                CallbackQueryHandler(connected_callback, pattern="^menu_back$"),
                CallbackQueryHandler(use_account_callback, pattern=r"^use_account_\w+$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_command),
            CommandHandler("start", start_command),
            CallbackQueryHandler(menu_back_callback, pattern="^menu_back$"),
        ],
        allow_reentry=True,
    )

    # -----------------------------------------------------------------------
    # Disconnect WhatsApp ConversationHandler
    # -----------------------------------------------------------------------
    disconnect_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(disconnect_entry, pattern="^menu_disconnect$")],
        states={
            DISCONNECT_CONFIRM: [
                CallbackQueryHandler(
                    disconnect_all_selected,
                    pattern="^disconnect_all$",
                ),
                CallbackQueryHandler(
                    disconnect_account_selected,
                    pattern=r"^disconnect_[a-zA-Z0-9]+$",
                ),
                CallbackQueryHandler(
                    confirm_disconnect_callback,
                    pattern=r"^confirm_disconnect_",
                ),
                CallbackQueryHandler(menu_back_callback, pattern="^menu_back$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_command),
            CommandHandler("start", start_command),
            CallbackQueryHandler(menu_back_callback, pattern="^menu_back$"),
        ],
        allow_reentry=True,
    )

    # -----------------------------------------------------------------------
    # Feature Menu ConversationHandler
    # -----------------------------------------------------------------------
    feature_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(use_account_callback, pattern=r"^use_account_\w+$"),
        ],
        states={
            FEATURE_MENU: [
                CallbackQueryHandler(
                    feature_placeholder_callback,
                    pattern=r"^feature_",
                ),
                CallbackQueryHandler(menu_back_callback, pattern="^menu_back$"),
            ],
            ACCOUNT_SELECTION: [
                CallbackQueryHandler(use_account_callback, pattern=r"^use_account_\w+$"),
                CallbackQueryHandler(menu_back_callback, pattern="^menu_back$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", cancel_command),
            CommandHandler("start", start_command),
            CallbackQueryHandler(menu_back_callback, pattern="^menu_back$"),
        ],
        allow_reentry=True,
    )

    # -----------------------------------------------------------------------
    # Register all handlers
    # -----------------------------------------------------------------------

    # Core commands
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("cancel", cancel_command))

    # Conversation handlers (order matters — most specific first)
    app.add_handler(connect_conv)
    app.add_handler(disconnect_conv)
    app.add_handler(feature_conv)

    # Standalone callback handlers (outside conversations)
    app.add_handler(CallbackQueryHandler(help_callback, pattern="^menu_help$"))
    app.add_handler(CallbackQueryHandler(accounts_callback, pattern="^menu_accounts$"))
    app.add_handler(CallbackQueryHandler(account_detail_callback, pattern=r"^account_detail_\w+$"))
    app.add_handler(CallbackQueryHandler(menu_back_callback, pattern="^menu_back$"))

    # Error handler
    app.add_error_handler(error_handler)

    # -----------------------------------------------------------------------
    # Start polling
    # -----------------------------------------------------------------------
    logger.info("🤖 Bot shuru ho raha hai...")
    logger.info(f"📡 Baileys API URL: {BAILEYS_API_URL}")
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
