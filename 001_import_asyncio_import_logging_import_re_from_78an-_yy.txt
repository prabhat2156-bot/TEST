import asyncio
import logging
import re
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    KeyboardButton,
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)
from telegram.error import TelegramError

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────
# CREATE GROUP — States
# ─────────────────────────────────────────────────────────────
(
    GROUP_NAME,
    GROUP_PHOTO,
    DISAPPEARING_MSG,
    GROUP_PERMISSIONS,
    ADD_MEMBERS,
    NUMBERING_START,
    NUMBER_OF_GROUPS,
    CONFIRMATION,
    CREATING,
) = range(9)

# ─────────────────────────────────────────────────────────────
# JOIN GROUPS — States
# ─────────────────────────────────────────────────────────────
(
    SEND_LINKS,
    CONFIRM_JOIN,
    JOINING,
) = range(9, 12)

# ─────────────────────────────────────────────────────────────
# LEAVE GROUPS — States
# ─────────────────────────────────────────────────────────────
(
    SELECT_SCOPE,
    SELECT_GROUPS,
    CONFIRM_LEAVE,
    LEAVING,
) = range(12, 16)


# ═══════════════════════════════════════════════════════════════
#  HELPER UTILITIES
# ═══════════════════════════════════════════════════════════════

def _default_permissions() -> dict:
    """Return default permission states."""
    return {
        "messages_send": "Everyone",
        "group_info_edit": "Admins Only",
        "members_add": "Everyone",
        "new_members_approve": "Off",
        "media_share": "Everyone",
        "polls_create": "Everyone",
        "video_voice_calls": "Everyone",
        "admin_join_link_approve": "Off",
    }

PERMISSION_LABELS = {
    "messages_send": "💬 Messages Send",
    "group_info_edit": "✏️ Group Info Edit",
    "members_add": "➕ Members Add",
    "new_members_approve": "🔐 New Members Approve",
    "media_share": "🖼️ Media Share",
    "polls_create": "📊 Polls Create",
    "video_voice_calls": "📹 Video/Voice Calls",
    "admin_join_link_approve": "🔗 Admin Join Link Approve",
}

TOGGLE_MAP = {
    "Everyone": "Admins Only",
    "Admins Only": "Everyone",
    "On": "Off",
    "Off": "On",
}

ON_OFF_KEYS = {"new_members_approve", "admin_join_link_approve"}


def _build_permissions_keyboard(perms: dict) -> InlineKeyboardMarkup:
    """Build inline keyboard for permission toggles."""
    buttons = []
    for key, label in PERMISSION_LABELS.items():
        value = perms[key]
        buttons.append(
            [InlineKeyboardButton(f"{label}: {value}", callback_data=f"perm_toggle:{key}")]
        )
    buttons.append([InlineKeyboardButton("💾 Save Permissions", callback_data="perm_save")])
    return InlineKeyboardMarkup(buttons)


def _progress_bar(current: int, total: int, width: int = 10) -> str:
    filled = int(width * current / total) if total else 0
    bar = "█" * filled + "░" * (width - filled)
    return f"[{bar}] {current}/{total}"


def _validate_invite_link(link: str) -> bool:
    """Check if a string looks like a valid Telegram invite link."""
    pattern = r"^https://t\.me/(?:\+[\w-]+|joinchat/[\w-]+)$"
    return bool(re.match(pattern, link.strip()))


# ═══════════════════════════════════════════════════════════════
#  FEATURE 1 — CREATE GROUP
# ═══════════════════════════════════════════════════════════════

async def create_group_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point: /create_group command."""
    context.user_data["create_group"] = {
        "base_name": None,
        "photo": None,
        "disappearing": "Off",
        "permissions": _default_permissions(),
        "members_file": None,
        "numbering_start": 1,
        "number_of_groups": 1,
    }
    await update.message.reply_text(
        "🎉 *Group Creation Wizard* mein aapka swagat hai!\n\n"
        "📝 *Step 1/8* — Group ka *base name* batao.\n"
        "_Example: `My Group` → My Group 1, My Group 2 ..._\n\n"
        "Type karo aur send karo 👇",
        parse_mode="Markdown",
    )
    return GROUP_NAME


async def create_group_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("❌ Name khali nahi ho sakta! Dobara try karo.")
        return GROUP_NAME
    context.user_data["create_group"]["base_name"] = name
    keyboard = ReplyKeyboardMarkup(
        [[KeyboardButton("⏭️ Skip")]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )
    await update.message.reply_text(
        f"✅ Group name set: *{name}*\n\n"
        "📸 *Step 2/8* — Group ki profile photo bhejo.\n"
        "_Ya Skip karo agar abhi nahi lagani._",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    return GROUP_PHOTO


async def create_group_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text and update.message.text.strip() == "⏭️ Skip":
        context.user_data["create_group"]["photo"] = None
        await update.message.reply_text(
            "⏭️ Photo skip kar diya.\n\n"
            "⏱️ *Step 3/8* — Disappearing messages setting chuno:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
    elif update.message.photo:
        file_id = update.message.photo[-1].file_id
        context.user_data["create_group"]["photo"] = file_id
        await update.message.reply_text(
            "✅ Photo save ho gayi!\n\n"
            "⏱️ *Step 3/8* — Disappearing messages setting chuno:",
            parse_mode="Markdown",
            reply_markup=ReplyKeyboardRemove(),
        )
    else:
        await update.message.reply_text(
            "❌ Koi photo nahi mili. Photo send karo ya *Skip* karo.",
            parse_mode="Markdown",
        )
        return GROUP_PHOTO

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🚫 Off", callback_data="disappear:Off"),
            InlineKeyboardButton("⏱️ 24h", callback_data="disappear:24h"),
        ],
        [
            InlineKeyboardButton("📅 7 Days", callback_data="disappear:7d"),
            InlineKeyboardButton("🗓️ 90 Days", callback_data="disappear:90d"),
        ],
        [InlineKeyboardButton("⏭️ Skip", callback_data="disappear:Off")],
    ])
    await update.message.reply_text(
        "⏱️ *Disappearing Messages* ka time set karo:\n"
        "_Messages automatically delete ho jayenge is time ke baad._",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    return DISAPPEARING_MSG


async def create_group_disappearing(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    choice = query.data.split(":")[1]
    context.user_data["create_group"]["disappearing"] = choice
    perms = context.user_data["create_group"]["permissions"]
    keyboard = _build_permissions_keyboard(perms)
    await query.edit_message_text(
        f"✅ Disappearing messages: *{choice}*\n\n"
        "🔒 *Step 4/8* — Group Permissions set karo.\n"
        "_Har button click karne par toggle hoga. Jab ho jaye 'Save' karo._",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    return GROUP_PERMISSIONS


async def create_group_permission_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "perm_save":
        keyboard = ReplyKeyboardMarkup(
            [[KeyboardButton("⏭️ Skip")]],
            resize_keyboard=True,
            one_time_keyboard=True,
        )
        await query.edit_message_text(
            "✅ Permissions save ho gayi!\n\n"
            "👥 *Step 5/8* — Members add karne hain?\n"
            "_VCF file ya numbers list (text file) bhejo, ya Skip karo._",
            parse_mode="Markdown",
        )
        await query.message.reply_text(
            "📎 File bhejo ya Skip karo 👇",
            reply_markup=keyboard,
        )
        return ADD_MEMBERS

    key = query.data.split(":")[1]
    perms = context.user_data["create_group"]["permissions"]
    current = perms[key]
    if key in ON_OFF_KEYS:
        perms[key] = "On" if current == "Off" else "Off"
    else:
        perms[key] = TOGGLE_MAP.get(current, "Everyone")

    keyboard = _build_permissions_keyboard(perms)
    try:
        await query.edit_message_reply_markup(reply_markup=keyboard)
    except TelegramError:
        pass
    return GROUP_PERMISSIONS


async def create_group_add_members(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.message.text and update.message.text.strip() == "⏭️ Skip":
        context.user_data["create_group"]["members_file"] = None
    elif update.message.document:
        doc = update.message.document
        context.user_data["create_group"]["members_file"] = doc.file_id
    else:
        await update.message.reply_text(
            "❌ VCF/text file bhejo ya *Skip* karo.",
            parse_mode="Markdown",
        )
        return ADD_MEMBERS

    await update.message.reply_text(
        "✅ Members info save!\n\n"
        "🔢 *Step 6/8* — Numbering kahan se start kare?\n"
        "_Example: 1 → Group 1, Group 2..._\n"
        "_Ya 51 → Group 51, Group 52..._\n\n"
        "Number type karo 👇",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    return NUMBERING_START


async def create_group_numbering_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text.isdigit() or int(text) < 1:
        await update.message.reply_text(
            "❌ Valid positive number daalo (e.g., 1, 51, 101)."
        )
        return NUMBERING_START
    context.user_data["create_group"]["numbering_start"] = int(text)
    await update.message.reply_text(
        f"✅ Numbering start: *{text}*\n\n"
        "📦 *Step 7/8* — Kitne groups banana chahte ho?\n"
        "_Example: 5, 10, 50..._\n\n"
        "Number type karo 👇",
        parse_mode="Markdown",
    )
    return NUMBER_OF_GROUPS


async def create_group_number_of_groups(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if not text.isdigit() or int(text) < 1:
        await update.message.reply_text(
            "❌ Valid positive number daalo (e.g., 5, 10)."
        )
        return NUMBER_OF_GROUPS
    context.user_data["create_group"]["number_of_groups"] = int(text)
    data = context.user_data["create_group"]
    start = data["numbering_start"]
    total = data["number_of_groups"]
    end = start + total - 1
    perms = data["permissions"]
    perm_text = "\n".join(
        f"  • {PERMISSION_LABELS[k]}: *{v}*" for k, v in perms.items()
    )
    summary = (
        f"📋 *Step 8/8 — Confirmation*\n\n"
        f"📌 *Base Name:* {data['base_name']}\n"
        f"📸 *Photo:* {'Set ✅' if data['photo'] else 'Nahi ❌'}\n"
        f"⏱️ *Disappearing Msgs:* {data['disappearing']}\n"
        f"👥 *Members File:* {'Set ✅' if data['members_file'] else 'Nahi ❌'}\n"
        f"🔢 *Numbering:* {start} → {end}\n"
        f"📦 *Total Groups:* {total}\n\n"
        f"🔒 *Permissions:*\n{perm_text}\n\n"
        f"_Groups banenge: `{data['base_name']} {start}` se `{data['base_name']} {end}` tak_\n\n"
        f"Sab theek hai? Confirm karo! 👇"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm", callback_data="cg_confirm"),
            InlineKeyboardButton("✏️ Edit", callback_data="cg_edit"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="cg_cancel")],
    ])
    await update.message.reply_text(summary, parse_mode="Markdown", reply_markup=keyboard)
    return CONFIRMATION


async def create_group_confirmation(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "cg_cancel":
        context.user_data.pop("create_group", None)
        await query.edit_message_text(
            "❌ Group creation cancel kar diya gaya.\n"
            "Dobara shuru karne ke liye /create_group use karo."
        )
        return ConversationHandler.END

    if query.data == "cg_edit":
        context.user_data["create_group"] = {
            "base_name": None,
            "photo": None,
            "disappearing": "Off",
            "permissions": _default_permissions(),
            "members_file": None,
            "numbering_start": 1,
            "number_of_groups": 1,
        }
        await query.edit_message_text(
            "♻️ Chaliye phir se shuru karte hain!\n\n"
            "📝 Group ka *base name* batao:",
            parse_mode="Markdown",
        )
        return GROUP_NAME

    # cg_confirm
    data = context.user_data["create_group"]
    total = data["number_of_groups"]
    start = data["numbering_start"]
    base = data["base_name"]

    progress_msg = await query.edit_message_text(
        f"⚙️ *Groups create ho rahe hain...*\n\n"
        f"{_progress_bar(0, total)}\n"
        f"_Please wait, band mat karo!_",
        parse_mode="Markdown",
    )

    success_list = []
    fail_list = []

    for i in range(total):
        group_number = start + i
        group_name = f"{base} {group_number}"
        try:
            # Actual group creation logic would go here via WhatsApp API / bot API
            # Simulated with asyncio.sleep for rate limiting
            await asyncio.sleep(2.5)
            # Simulate occasional failure for robustness demo
            # In production: call your group creation API here
            success_list.append(group_name)
        except Exception as e:
            logger.error(f"Failed to create group '{group_name}': {e}")
            # Retry once
            await asyncio.sleep(3)
            try:
                await asyncio.sleep(2)
                success_list.append(group_name)
            except Exception as e2:
                logger.error(f"Retry also failed for '{group_name}': {e2}")
                fail_list.append(group_name)

        # Update progress
        done = i + 1
        bar = _progress_bar(done, total)
        status_lines = [f"✅ {g}" for g in success_list[-3:]]
        if fail_list:
            status_lines += [f"❌ {g}" for g in fail_list[-2:]]
        status_text = "\n".join(status_lines)
        try:
            await progress_msg.edit_text(
                f"⚙️ *Groups create ho rahe hain...*\n\n"
                f"{bar}\n\n"
                f"{status_text}",
                parse_mode="Markdown",
            )
        except TelegramError:
            pass

    # Final summary
    success_count = len(success_list)
    fail_count = len(fail_list)
    fail_section = ""
    if fail_list:
        fail_names = "\n".join(f"  ❌ {g}" for g in fail_list)
        fail_section = f"\n\n*Failed Groups ({fail_count}):*\n{fail_names}"

    await progress_msg.edit_text(
        f"🎊 *Group Creation Complete!*\n\n"
        f"✅ *Successfully created:* {success_count}\n"
        f"❌ *Failed:* {fail_count}\n"
        f"📦 *Total attempted:* {total}"
        f"{fail_section}\n\n"
        f"_Naye groups manage karne ke liye /menu use karo._",
        parse_mode="Markdown",
    )
    context.user_data.pop("create_group", None)
    return ConversationHandler.END


async def create_group_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("create_group", None)
    await update.message.reply_text(
        "❌ Group creation cancel kar diya.\n"
        "Dobara shuru karne ke liye /create_group use karo.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════
#  FEATURE 2 — JOIN GROUPS
# ═══════════════════════════════════════════════════════════════

async def join_groups_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point: /join_groups command."""
    context.user_data["join_groups"] = {"links": []}
    await update.message.reply_text(
        "🔗 *Join Groups* wizard mein aapka swagat hai!\n\n"
        "📋 *Step 1/3* — Group invite links bhejo.\n"
        "_Ek ya zyada links bhejo, har link alag line mein._\n\n"
        "_Example:_\n"
        "`https://t.me/+abc123`\n"
        "`https://t.me/joinchat/xyz456`",
        parse_mode="Markdown",
    )
    return SEND_LINKS


async def join_groups_receive_links(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    raw = update.message.text.strip()
    lines = [l.strip() for l in raw.splitlines() if l.strip()]
    valid = []
    invalid = []
    for line in lines:
        if _validate_invite_link(line):
            valid.append(line)
        else:
            invalid.append(line)

    if not valid:
        inv_text = "\n".join(f"  ❌ `{l}`" for l in invalid[:5])
        await update.message.reply_text(
            f"❌ Koi valid link nahi mila!\n\n"
            f"*Invalid links:*\n{inv_text}\n\n"
            f"_Sahi format: `https://t.me/+abc123` ya `https://t.me/joinchat/xyz`_\n"
            f"Dobara try karo 👇",
            parse_mode="Markdown",
        )
        return SEND_LINKS

    context.user_data["join_groups"]["links"] = valid

    valid_text = "\n".join(f"  ✅ `{l}`" for l in valid)
    invalid_section = ""
    if invalid:
        inv_text = "\n".join(f"  ❌ `{l}`" for l in invalid)
        invalid_section = f"\n\n*Invalid (skip honge):*\n{inv_text}"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Join Karo", callback_data="join_confirm"),
            InlineKeyboardButton("❌ Cancel", callback_data="join_cancel"),
        ]
    ])
    await update.message.reply_text(
        f"📋 *Step 2/3 — Confirm Links*\n\n"
        f"*Valid links ({len(valid)}):*\n{valid_text}"
        f"{invalid_section}\n\n"
        f"In groups mein join karna chahte ho?",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    return CONFIRM_JOIN


async def join_groups_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "join_cancel":
        context.user_data.pop("join_groups", None)
        await query.edit_message_text(
            "❌ Join groups cancel kar diya.\n"
            "Dobara try karne ke liye /join_groups use karo."
        )
        return ConversationHandler.END

    links = context.user_data["join_groups"]["links"]
    total = len(links)
    progress_msg = await query.edit_message_text(
        f"🔗 *Groups join ho rahe hain...*\n\n"
        f"{_progress_bar(0, total)}\n"
        f"_Please wait..._",
        parse_mode="Markdown",
    )

    joined = []
    failed = []
    skipped = []

    for i, link in enumerate(links):
        try:
            # Production: call your join API here
            await asyncio.sleep(2)
            joined.append(link)
        except Exception as e:
            err_str = str(e).lower()
            if "already" in err_str or "member" in err_str:
                skipped.append((link, "Pehle se member ho"))
            elif "invalid" in err_str or "expired" in err_str:
                failed.append((link, "Link invalid/expired hai"))
            elif "full" in err_str:
                failed.append((link, "Group full hai"))
            else:
                failed.append((link, "Unknown error"))
            logger.warning(f"Join failed for {link}: {e}")

        done = i + 1
        try:
            await progress_msg.edit_text(
                f"🔗 *Groups join ho rahe hain...*\n\n"
                f"{_progress_bar(done, total)}\n"
                f"_Processing: {done}/{total}_",
                parse_mode="Markdown",
            )
        except TelegramError:
            pass

    fail_section = ""
    if failed:
        fail_lines = "\n".join(f"  ❌ `{l}` — {reason}" for l, reason in failed)
        fail_section = f"\n\n*Failed ({len(failed)}):*\n{fail_lines}"

    skip_section = ""
    if skipped:
        skip_lines = "\n".join(f"  ⚠️ `{l}` — {reason}" for l, reason in skipped)
        skip_section = f"\n\n*Skipped ({len(skipped)}):*\n{skip_lines}"

    await progress_msg.edit_text(
        f"🎊 *Join Complete!*\n\n"
        f"✅ *Joined:* {len(joined)}\n"
        f"❌ *Failed:* {len(failed)}\n"
        f"⚠️ *Skipped:* {len(skipped)}\n"
        f"📦 *Total:* {total}"
        f"{fail_section}"
        f"{skip_section}\n\n"
        f"_Groups manage karne ke liye /menu use karo._",
        parse_mode="Markdown",
    )
    context.user_data.pop("join_groups", None)
    return ConversationHandler.END


async def join_groups_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("join_groups", None)
    await update.message.reply_text(
        "❌ Join groups cancel kar diya.\n"
        "Dobara try karne ke liye /join_groups use karo.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════
#  FEATURE 3 — LEAVE GROUPS
# ═══════════════════════════════════════════════════════════════

async def leave_groups_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point: /leave_groups command."""
    # Fetch user's current groups from storage/API (mocked here)
    context.user_data["leave_groups"] = {
        "scope": None,
        "all_groups": [],     # populated from API
        "selected_indices": [],
    }
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 All Groups", callback_data="leave_scope:all")],
        [InlineKeyboardButton("✅ Select Groups", callback_data="leave_scope:select")],
        [InlineKeyboardButton("❌ Cancel", callback_data="leave_scope:cancel")],
    ])
    await update.message.reply_text(
        "🚪 *Leave Groups* wizard mein aapka swagat hai!\n\n"
        "📋 *Step 1/4* — Kaunse groups leave karne hain?\n\n"
        "⚠️ _Yeh action _*irreversible*_ hai — ek baar leave karne ke baad rejoin karna padega!_",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    return SELECT_SCOPE


async def leave_groups_scope(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    choice = query.data.split(":")[1]

    if choice == "cancel":
        context.user_data.pop("leave_groups", None)
        await query.edit_message_text(
            "❌ Leave groups cancel kar diya.\n"
            "Dobara try karne ke liye /leave_groups use karo."
        )
        return ConversationHandler.END

    context.user_data["leave_groups"]["scope"] = choice

    # In production: fetch actual groups from your API/DB
    mock_groups = [
        "My Group 1", "My Group 2", "Family Group", "Work Team",
        "Cricket Gang", "Study Buddies", "News Updates", "Friends",
    ]
    context.user_data["leave_groups"]["all_groups"] = mock_groups

    if choice == "all":
        group_count = len(mock_groups)
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("⚠️ Haan, Sab Leave Karo", callback_data="leave_confirm:all"),
                InlineKeyboardButton("❌ Cancel", callback_data="leave_confirm:cancel"),
            ]
        ])
        await query.edit_message_text(
            f"🚨 *STRONG WARNING* 🚨\n\n"
            f"Aap *{group_count} groups* leave karne wale ho!\n\n"
            f"❌ Yeh *undo nahi ho sakta*.\n"
            f"❌ In groups ke messages aapko nahi milenge.\n"
            f"❌ Rejoin karne ke liye dobara invite chahiye.\n\n"
            f"*Kya aap 100% sure hain?*",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )
        return CONFIRM_LEAVE

    # Select mode: show numbered list
    groups = mock_groups
    numbered = "\n".join(f"  {i+1}. {g}" for i, g in enumerate(groups))
    await query.edit_message_text(
        f"📋 *Step 2/4 — Groups Select Karo*\n\n"
        f"Aapke groups:\n{numbered}\n\n"
        f"_Jinhe leave karna ho unke numbers comma se likhो._\n"
        f"_Example: `1, 3, 5` ya `2-4` ya `1, 3-5, 8`_\n\n"
        f"Numbers type karo 👇",
        parse_mode="Markdown",
    )
    return SELECT_GROUPS


async def leave_groups_select(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    groups = context.user_data["leave_groups"]["all_groups"]
    total = len(groups)
    selected_indices = set()

    # Parse ranges and individual numbers: "1,3,5-7,9"
    parts = re.split(r"[,\s]+", text)
    parse_error = False
    for part in parts:
        part = part.strip()
        if not part:
            continue
        range_match = re.match(r"^(\d+)-(\d+)$", part)
        if range_match:
            lo, hi = int(range_match.group(1)), int(range_match.group(2))
            if lo < 1 or hi > total or lo > hi:
                parse_error = True
                break
            selected_indices.update(range(lo - 1, hi))
        elif part.isdigit():
            idx = int(part) - 1
            if idx < 0 or idx >= total:
                parse_error = True
                break
            selected_indices.add(idx)
        else:
            parse_error = True
            break

    if parse_error or not selected_indices:
        await update.message.reply_text(
            f"❌ Invalid selection! 1 to {total} ke beech numbers daalo.\n"
            f"_Example: `1, 3, 5` ya `2-4`_",
            parse_mode="Markdown",
        )
        return SELECT_GROUPS

    context.user_data["leave_groups"]["selected_indices"] = list(selected_indices)
    selected_names = [groups[i] for i in sorted(selected_indices)]
    names_text = "\n".join(f"  ❌ {g}" for g in selected_names)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm Leave", callback_data="leave_confirm:selected"),
            InlineKeyboardButton("❌ Cancel", callback_data="leave_confirm:cancel"),
        ]
    ])
    await update.message.reply_text(
        f"⚠️ *Step 3/4 — Confirm Leave*\n\n"
        f"Yeh *{len(selected_names)} groups* leave honge:\n"
        f"{names_text}\n\n"
        f"🚨 _Yeh action *irreversible* hai!_\n"
        f"Kya aap pakka sure hain?",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    return CONFIRM_LEAVE


async def leave_groups_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    choice = query.data.split(":")[1]

    if choice == "cancel":
        context.user_data.pop("leave_groups", None)
        await query.edit_message_text(
            "❌ Leave groups cancel kar diya.\n"
            "Dobara try karne ke liye /leave_groups use karo."
        )
        return ConversationHandler.END

    groups = context.user_data["leave_groups"]["all_groups"]
    scope = context.user_data["leave_groups"]["scope"]

    if scope == "all":
        target_groups = groups
    else:
        indices = context.user_data["leave_groups"]["selected_indices"]
        target_groups = [groups[i] for i in sorted(indices)]

    total = len(target_groups)
    progress_msg = await query.edit_message_text(
        f"🚪 *Groups leave ho rahe hain...*\n\n"
        f"{_progress_bar(0, total)}\n"
        f"_Please wait..._",
        parse_mode="Markdown",
    )

    left = []
    failed = []
    skipped = []

    for i, group_name in enumerate(target_groups):
        try:
            # Production: call your leave group API here
            await asyncio.sleep(1.5)
            left.append(group_name)
        except Exception as e:
            err_str = str(e).lower()
            if "last admin" in err_str or "only admin" in err_str:
                skipped.append((group_name, "Aap last admin hain — pehle kisi ko admin banao"))
            elif "not member" in err_str or "already left" in err_str:
                skipped.append((group_name, "Pehle se leave kar chuke hain"))
            else:
                failed.append((group_name, str(e)))
            logger.warning(f"Leave failed for '{group_name}': {e}")

        done = i + 1
        try:
            await progress_msg.edit_text(
                f"🚪 *Groups leave ho rahe hain...*\n\n"
                f"{_progress_bar(done, total)}\n"
                f"_Processing: {done}/{total}_",
                parse_mode="Markdown",
            )
        except TelegramError:
            pass

    fail_section = ""
    if failed:
        fail_lines = "\n".join(f"  ❌ {g} — {r}" for g, r in failed)
        fail_section = f"\n\n*Failed ({len(failed)}):*\n{fail_lines}"

    skip_section = ""
    if skipped:
        skip_lines = "\n".join(f"  ⚠️ {g} — {r}" for g, r in skipped)
        skip_section = f"\n\n*Skipped ({len(skipped)}):*\n{skip_lines}"

    await progress_msg.edit_text(
        f"✅ *Leave Complete!*\n\n"
        f"🚪 *Left:* {len(left)}\n"
        f"❌ *Failed:* {len(failed)}\n"
        f"⚠️ *Skipped:* {len(skipped)}\n"
        f"📦 *Total:* {total}"
        f"{fail_section}"
        f"{skip_section}\n\n"
        f"_Groups manage karne ke liye /menu use karo._",
        parse_mode="Markdown",
    )
    context.user_data.pop("leave_groups", None)
    return ConversationHandler.END


async def leave_groups_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop("leave_groups", None)
    await update.message.reply_text(
        "❌ Leave groups cancel kar diya.\n"
        "Dobara try karne ke liye /leave_groups use karo.",
        reply_markup=ReplyKeyboardRemove(),
    )
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════
#  CONVERSATION HANDLER DEFINITIONS
# ═══════════════════════════════════════════════════════════════

create_group_handler = ConversationHandler(
    entry_points=[CommandHandler("create_group", create_group_start)],
    states={
        GROUP_NAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, create_group_name),
        ],
        GROUP_PHOTO: [
            MessageHandler(filters.PHOTO, create_group_photo),
            MessageHandler(filters.Regex(r"^⏭️ Skip$"), create_group_photo),
        ],
        DISAPPEARING_MSG: [
            CallbackQueryHandler(create_group_disappearing, pattern=r"^disappear:"),
        ],
        GROUP_PERMISSIONS: [
            CallbackQueryHandler(create_group_permission_toggle, pattern=r"^(perm_toggle:|perm_save)"),
        ],
        ADD_MEMBERS: [
            MessageHandler(filters.Document.ALL, create_group_add_members),
            MessageHandler(filters.Regex(r"^⏭️ Skip$"), create_group_add_members),
        ],
        NUMBERING_START: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, create_group_numbering_start),
        ],
        NUMBER_OF_GROUPS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, create_group_number_of_groups),
        ],
        CONFIRMATION: [
            CallbackQueryHandler(create_group_confirmation, pattern=r"^cg_(confirm|edit|cancel)$"),
        ],
    },
    fallbacks=[CommandHandler("cancel", create_group_cancel)],
    allow_reentry=True,
    name="create_group_conversation",
    persistent=False,
)

join_groups_handler = ConversationHandler(
    entry_points=[CommandHandler("join_groups", join_groups_start)],
    states={
        SEND_LINKS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, join_groups_receive_links),
        ],
        CONFIRM_JOIN: [
            CallbackQueryHandler(join_groups_confirm, pattern=r"^join_(confirm|cancel)$"),
        ],
    },
    fallbacks=[CommandHandler("cancel", join_groups_cancel)],
    allow_reentry=True,
    name="join_groups_conversation",
    persistent=False,
)

leave_groups_handler = ConversationHandler(
    entry_points=[CommandHandler("leave_groups", leave_groups_start)],
    states={
        SELECT_SCOPE: [
            CallbackQueryHandler(leave_groups_scope, pattern=r"^leave_scope:"),
        ],
        SELECT_GROUPS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, leave_groups_select),
        ],
        CONFIRM_LEAVE: [
            CallbackQueryHandler(leave_groups_confirm, pattern=r"^leave_confirm:"),
        ],
    },
    fallbacks=[CommandHandler("cancel", leave_groups_cancel)],
    allow_reentry=True,
    name="leave_groups_conversation",
    persistent=False,
)


# ═══════════════════════════════════════════════════════════════
#  EXPORT
# ═══════════════════════════════════════════════════════════════

def get_group_handlers() -> list:
    """Returns list of all ConversationHandlers for group features."""
    return [create_group_handler, join_groups_handler, leave_groups_handler]
