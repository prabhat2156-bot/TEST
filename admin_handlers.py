import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ConversationHandler,
    ContextTypes,
    filters,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# Placeholder service imports (replace with real implementations)
# ─────────────────────────────────────────────
# from services.whatsapp import (
#     get_all_groups, get_group_members, get_group_admins,
#     make_admin, remove_admin,
#     set_approval_setting, get_pending_requests,
#     approve_request, reject_request,
#     get_invite_link, revoke_and_get_invite_link,
# )

# ─────────────────────────────────────────────
# Mock service layer (replace with actual WhatsApp API calls)
# ─────────────────────────────────────────────

async def get_all_groups() -> list[dict]:
    """Returns list of dicts: {id, name}"""
    return [
        {"id": "g1", "name": "Sales Team 1"},
        {"id": "g2", "name": "Sales Team 2"},
        {"id": "g3", "name": "Support Group"},
    ]

async def get_group_members(group_id: str) -> list[str]:
    """Returns list of phone numbers in group."""
    return ["+919876543210", "+919123456789"]

async def get_group_admins(group_id: str) -> list[str]:
    """Returns list of phone numbers who are admins."""
    return ["+919876543210"]

async def make_admin(group_id: str, phone: str) -> dict:
    """Returns {success: bool, error: str|None}"""
    return {"success": True, "error": None}

async def remove_admin(group_id: str, phone: str) -> dict:
    """Returns {success: bool, error: str|None}"""
    return {"success": True, "error": None}

async def set_approval_setting(group_id: str, enabled: bool) -> dict:
    """Returns {success: bool, error: str|None}"""
    return {"success": True, "error": None}

async def get_pending_requests(group_id: str) -> list[dict]:
    """Returns list of dicts: {id, phone, name}"""
    return [{"id": "r1", "phone": "+919000000001", "name": "User A"}]

async def approve_request(group_id: str, request_id: str) -> dict:
    return {"success": True, "error": None}

async def reject_request(group_id: str, request_id: str) -> dict:
    return {"success": True, "error": None}

async def get_invite_link(group_id: str) -> dict:
    """Returns {link: str|None, error: str|None}"""
    return {"link": f"https://chat.whatsapp.com/ExampleLink{group_id}", "error": None}

async def revoke_and_get_invite_link(group_id: str) -> dict:
    """Revokes old link and returns new one."""
    return {"link": f"https://chat.whatsapp.com/NewLink{group_id}", "error": None}

# ─────────────────────────────────────────────
# Helper utilities
# ─────────────────────────────────────────────

def parse_phone_numbers(text: str) -> list[str]:
    """Parse one or multiple phone numbers from user input (newline or comma separated)."""
    raw = text.replace(",", "\n").splitlines()
    numbers = []
    for entry in raw:
        cleaned = entry.strip().replace(" ", "")
        if cleaned:
            if not cleaned.startswith("+"):
                cleaned = "+" + cleaned
            numbers.append(cleaned)
    return numbers

def parse_group_selections(text: str, groups: list[dict]) -> list[dict]:
    """Parse comma-separated group numbers and return selected groups."""
    selected = []
    parts = text.replace(" ", "").split(",")
    for part in parts:
        if part.isdigit():
            idx = int(part) - 1
            if 0 <= idx < len(groups):
                selected.append(groups[idx])
    return selected

def groups_keyboard(groups: list[dict]) -> str:
    """Format numbered group list for display."""
    lines = []
    for i, g in enumerate(groups, 1):
        lines.append(f"{i}. {g['name']}")
    return "\n".join(lines)

def scope_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["🌐 All Groups", "📋 Select Groups"]],
        one_time_keyboard=True,
        resize_keyboard=True,
    )

def cancel_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        [["❌ Cancel"]],
        one_time_keyboard=True,
        resize_keyboard=True,
    )

CANCEL_TEXT = "❌ Cancel"

# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE 1 — MAKE / REMOVE ADMIN
# ═══════════════════════════════════════════════════════════════════════════════

# Conversation states
(
    ADMIN_SELECT_ACTION,
    ADMIN_SEND_NUMBERS,
    ADMIN_SELECT_SCOPE,
    ADMIN_SELECT_GROUPS,
    ADMIN_PROCESSING,
) = range(5)


async def admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point: /makeadmin or /removeadmin command."""
    keyboard = ReplyKeyboardMarkup(
        [["⬆️ Admin Banana Hai", "⬇️ Admin Hatana Hai"], ["❌ Cancel"]],
        one_time_keyboard=True,
        resize_keyboard=True,
    )
    await update.message.reply_text(
        "👑 *Admin Management*\n\n"
        "Aap kya karna chahte hain?\n\n"
        "⬆️ *Admin Banana Hai* — Kisi ko admin banao\n"
        "⬇️ *Admin Hatana Hai* — Kisi ka admin remove karo",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    return ADMIN_SELECT_ACTION


async def admin_select_action(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == CANCEL_TEXT:
        return await admin_cancel(update, context)

    if text == "⬆️ Admin Banana Hai":
        context.user_data["admin_action"] = "make"
    elif text == "⬇️ Admin Hatana Hai":
        context.user_data["admin_action"] = "remove"
    else:
        await update.message.reply_text("❗ Please diye gaye options mein se choose karein.")
        return ADMIN_SELECT_ACTION

    action_label = "banane" if context.user_data["admin_action"] == "make" else "hatane"
    await update.message.reply_text(
        f"📱 *Phone Number(s) Enter Karein*\n\n"
        f"Jinhe admin {action_label} hain unke number darj karein.\n"
        f"Ek ek line pe ya comma se alag karein:\n\n"
        f"_Example:_\n`+919876543210`\n`+919123456789`",
        parse_mode="Markdown",
        reply_markup=cancel_keyboard(),
    )
    return ADMIN_SEND_NUMBERS


async def admin_send_numbers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == CANCEL_TEXT:
        return await admin_cancel(update, context)

    numbers = parse_phone_numbers(text)
    if not numbers:
        await update.message.reply_text(
            "❗ Koi valid number nahi mila. Dobara try karein.\n"
            "_Format: +919876543210 (ek per line ya comma se alag)_",
            parse_mode="Markdown",
        )
        return ADMIN_SEND_NUMBERS

    context.user_data["admin_numbers"] = numbers
    await update.message.reply_text(
        f"✅ *{len(numbers)} number(s) receive ho gaye.*\n\n"
        "Ab scope select karein:",
        parse_mode="Markdown",
        reply_markup=scope_keyboard(),
    )
    return ADMIN_SELECT_SCOPE


async def admin_select_scope(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == CANCEL_TEXT:
        return await admin_cancel(update, context)

    groups = await get_all_groups()
    context.user_data["all_groups"] = groups

    if text == "🌐 All Groups":
        context.user_data["selected_groups"] = groups
        return await admin_process(update, context)
    elif text == "📋 Select Groups":
        group_list = groups_keyboard(groups)
        await update.message.reply_text(
            f"📋 *Groups ki list:*\n\n{group_list}\n\n"
            "Jinhe select karna hai unke number darj karein (comma se alag):\n"
            "_Example: 1, 3, 5_",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard(),
        )
        return ADMIN_SELECT_GROUPS
    else:
        await update.message.reply_text("❗ Please diye gaye options mein se choose karein.")
        return ADMIN_SELECT_SCOPE


async def admin_select_groups(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == CANCEL_TEXT:
        return await admin_cancel(update, context)

    all_groups = context.user_data.get("all_groups", [])
    selected = parse_group_selections(text, all_groups)

    if not selected:
        await update.message.reply_text(
            "❗ Koi valid group select nahi hua. Dobara try karein.\n"
            "_Example: 1, 2, 3_",
            parse_mode="Markdown",
        )
        return ADMIN_SELECT_GROUPS

    context.user_data["selected_groups"] = selected
    return await admin_process(update, context)


async def admin_process(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Core processing for make/remove admin."""
    numbers = context.user_data["admin_numbers"]
    groups = context.user_data["selected_groups"]
    action = context.user_data["admin_action"]

    action_label = "Admin Banaya Ja Raha Hai" if action == "make" else "Admin Hataya Ja Raha Hai"
    emoji = "⬆️" if action == "make" else "⬇️"

    progress_msg = await update.message.reply_text(
        f"{emoji} *{action_label}...*\n\n⏳ Processing shuru ho raha hai...",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )

    results = []
    total = len(numbers) * len(groups)
    done = 0

    for number in numbers:
        for group in groups:
            group_id = group["id"]
            group_name = group["name"]

            members = await get_group_members(group_id)
            admins = await get_group_admins(group_id)

            if action == "make":
                if number not in members:
                    status = f"⚠️ `{number}` — *{group_name}*: Group mein nahi hai, skip"
                elif number in admins:
                    status = f"⏭️ `{number}` — *{group_name}*: Pehle se Admin hai"
                else:
                    res = await make_admin(group_id, number)
                    if res["success"]:
                        status = f"✅ `{number}` — *{group_name}*: Admin ban gaya!"
                    else:
                        status = f"❌ `{number}` — *{group_name}*: Error — {res['error']}"
            else:  # remove
                if number not in admins:
                    status = f"⏭️ `{number}` — *{group_name}*: Admin nahi hai"
                else:
                    res = await remove_admin(group_id, number)
                    if res["success"]:
                        status = f"✅ `{number}` — *{group_name}*: Admin hat gaya!"
                    else:
                        status = f"❌ `{number}` — *{group_name}*: Error — {res['error']}"

            results.append(status)
            done += 1

            # Update progress every step
            progress_text = (
                f"{emoji} *{action_label}...*\n\n"
                f"⏳ Progress: {done}/{total}\n\n"
                + "\n".join(results[-5:])  # Show last 5 results
            )
            try:
                await progress_msg.edit_text(progress_text, parse_mode="Markdown")
            except Exception:
                pass

    # Final summary
    summary = "\n".join(results)
    final_text = (
        f"{emoji} *{action_label} — Completed!*\n\n"
        f"📊 Total processed: {done}\n\n"
        f"{summary}\n\n"
        f"✅ *Kaam ho gaya!*"
    )
    await progress_msg.edit_text(final_text, parse_mode="Markdown")

    context.user_data.clear()
    return ConversationHandler.END


async def admin_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "❌ *Operation cancel ho gaya.*\n\nWapas jaane ke liye /start karein.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    context.user_data.clear()
    return ConversationHandler.END


admin_handler = ConversationHandler(
    entry_points=[CommandHandler("makeadmin", admin_start), CommandHandler("removeadmin", admin_start)],
    states={
        ADMIN_SELECT_ACTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_select_action)],
        ADMIN_SEND_NUMBERS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_send_numbers)],
        ADMIN_SELECT_SCOPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_select_scope)],
        ADMIN_SELECT_GROUPS: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_select_groups)],
    },
    fallbacks=[CommandHandler("cancel", admin_cancel), MessageHandler(filters.Regex(f"^{CANCEL_TEXT}$"), admin_cancel)],
    allow_reentry=True,
)

# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE 2 — APPROVAL SETTING
# ═══════════════════════════════════════════════════════════════════════════════

(
    APPROVAL_CONFIRM,
    APPROVAL_PROCESSING,
    APPROVAL_COMPLETION,
) = range(3)


async def approval_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point: /approvalsetting command."""
    keyboard = ReplyKeyboardMarkup(
        [["✅ Haan, Karo", "❌ Nahi, Cancel"]],
        one_time_keyboard=True,
        resize_keyboard=True,
    )
    await update.message.reply_text(
        "⚙️ *Approval Setting Reset*\n\n"
        "Yeh feature *sabhi groups* mein 'Approve New Members' setting ko reset karega:\n\n"
        "🔴 *Phase 1:* Pehle setting *OFF* karein sabhi groups mein\n"
        "🟢 *Phase 2:* Phir setting *ON* karein sabhi groups mein\n\n"
        "⚠️ _Note: Is process mein thoda waqt lag sakta hai._\n\n"
        "Kya aap sure hain? ✅",
        parse_mode="Markdown",
        reply_markup=keyboard,
    )
    return APPROVAL_CONFIRM


async def approval_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()

    if text in ("❌ Nahi, Cancel", CANCEL_TEXT):
        return await approval_cancel(update, context)

    if text != "✅ Haan, Karo":
        await update.message.reply_text("❗ Please diye gaye options mein se choose karein.")
        return APPROVAL_CONFIRM

    return await approval_process(update, context)


async def approval_process(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Two-phase: turn OFF all groups, then turn ON all groups."""
    groups = await get_all_groups()
    total = len(groups)

    progress_msg = await update.message.reply_text(
        "⚙️ *Approval Setting Reset Ho Raha Hai...*\n\n"
        "🔴 *Phase 1: OFF kar rahe hain...*\n"
        f"⏳ 0/{total} groups done",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )

    phase1_results = []
    # ── Phase 1: Turn OFF ──
    for i, group in enumerate(groups, 1):
        res = await set_approval_setting(group["id"], False)
        if res["success"]:
            phase1_results.append(f"✅ {group['name']}: OFF ho gaya")
        else:
            error = res.get("error", "Unknown error")
            if "not admin" in str(error).lower():
                phase1_results.append(f"⚠️ {group['name']}: Bot admin nahi hai")
            elif "not available" in str(error).lower():
                phase1_results.append(f"⚠️ {group['name']}: Feature available nahi")
            else:
                phase1_results.append(f"❌ {group['name']}: {error}")

        try:
            await progress_msg.edit_text(
                "⚙️ *Approval Setting Reset Ho Raha Hai...*\n\n"
                f"🔴 *Phase 1: OFF kar rahe hain...*\n"
                f"⏳ {i}/{total} groups done\n\n"
                + "\n".join(phase1_results[-5:]),
                parse_mode="Markdown",
            )
        except Exception:
            pass

    # ── Phase 2: Turn ON ──
    phase2_results = []
    for i, group in enumerate(groups, 1):
        res = await set_approval_setting(group["id"], True)
        if res["success"]:
            phase2_results.append(f"✅ {group['name']}: ON ho gaya")
        else:
            error = res.get("error", "Unknown error")
            if "not admin" in str(error).lower():
                phase2_results.append(f"⚠️ {group['name']}: Bot admin nahi hai")
            elif "not available" in str(error).lower():
                phase2_results.append(f"⚠️ {group['name']}: Feature available nahi")
            else:
                phase2_results.append(f"❌ {group['name']}: {error}")

        try:
            await progress_msg.edit_text(
                "⚙️ *Approval Setting Reset Ho Raha Hai...*\n\n"
                f"🔴 Phase 1 complete ({total}/{total})\n\n"
                f"🟢 *Phase 2: ON kar rahe hain...*\n"
                f"⏳ {i}/{total} groups done\n\n"
                + "\n".join(phase2_results[-5:]),
                parse_mode="Markdown",
            )
        except Exception:
            pass

    # ── Final summary ──
    phase1_text = "\n".join(phase1_results)
    phase2_text = "\n".join(phase2_results)

    off_ok = sum(1 for r in phase1_results if r.startswith("✅"))
    on_ok = sum(1 for r in phase2_results if r.startswith("✅"))

    final_text = (
        "⚙️ *Approval Setting Reset — Complete!*\n\n"
        f"🔴 *Phase 1 (OFF):* {off_ok}/{total} success\n"
        f"{phase1_text}\n\n"
        f"🟢 *Phase 2 (ON):* {on_ok}/{total} success\n"
        f"{phase2_text}\n\n"
        "✅ *Approval setting reset ho gayi!*"
    )
    await progress_msg.edit_text(final_text, parse_mode="Markdown")

    context.user_data.clear()
    return ConversationHandler.END


async def approval_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "❌ *Operation cancel ho gaya.*\n\nWapas jaane ke liye /start karein.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    context.user_data.clear()
    return ConversationHandler.END


approval_handler = ConversationHandler(
    entry_points=[CommandHandler("approvalsetting", approval_start)],
    states={
        APPROVAL_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, approval_confirm)],
    },
    fallbacks=[CommandHandler("cancel", approval_cancel), MessageHandler(filters.Regex(f"^{CANCEL_TEXT}$"), approval_cancel)],
    allow_reentry=True,
)

# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE 3 — GET PENDING LIST
# ═══════════════════════════════════════════════════════════════════════════════

(
    PENDING_SELECT_SCOPE,
    PENDING_SELECT_GROUPS,
    PENDING_FETCHING,
    PENDING_SHOW_RESULTS,
    PENDING_ACTION,
) = range(5)

PENDING_APPROVE_ALL = "pending_approve_all"
PENDING_REJECT_ALL = "pending_reject_all"
PENDING_EXPORT_TXT = "pending_export_txt"
PENDING_BACK = "pending_back"


async def pending_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point: /pendinglist command."""
    await update.message.reply_text(
        "📋 *Pending Requests List*\n\n"
        "Konse groups ka pending list dekhna hai?",
        parse_mode="Markdown",
        reply_markup=scope_keyboard(),
    )
    return PENDING_SELECT_SCOPE


async def pending_select_scope(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == CANCEL_TEXT:
        return await pending_cancel(update, context)

    groups = await get_all_groups()
    context.user_data["all_groups"] = groups

    if text == "🌐 All Groups":
        context.user_data["selected_groups"] = groups
        return await pending_fetch(update, context)
    elif text == "📋 Select Groups":
        group_list = groups_keyboard(groups)
        await update.message.reply_text(
            f"📋 *Groups ki list:*\n\n{group_list}\n\n"
            "Jinhe select karna hai unke number darj karein (comma se alag):\n"
            "_Example: 1, 3, 5_",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard(),
        )
        return PENDING_SELECT_GROUPS
    else:
        await update.message.reply_text("❗ Please diye gaye options mein se choose karein.")
        return PENDING_SELECT_SCOPE


async def pending_select_groups(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == CANCEL_TEXT:
        return await pending_cancel(update, context)

    all_groups = context.user_data.get("all_groups", [])
    selected = parse_group_selections(text, all_groups)

    if not selected:
        await update.message.reply_text(
            "❗ Koi valid group select nahi hua. Dobara try karein.\n"
            "_Example: 1, 2, 3_",
            parse_mode="Markdown",
        )
        return PENDING_SELECT_GROUPS

    context.user_data["selected_groups"] = selected
    return await pending_fetch(update, context)


async def pending_fetch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    groups = context.user_data["selected_groups"]
    total = len(groups)

    progress_msg = await update.message.reply_text(
        "🔍 *Pending Requests Fetch Ho Rahe Hain...*\n\n"
        f"⏳ 0/{total} groups done",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )

    group_pending_data = []
    grand_total = 0

    for i, group in enumerate(groups, 1):
        pending = await get_pending_requests(group["id"])
        count = len(pending)
        grand_total += count
        group_pending_data.append({
            "group": group,
            "pending": pending,
            "count": count,
        })
        try:
            await progress_msg.edit_text(
                "🔍 *Pending Requests Fetch Ho Rahe Hain...*\n\n"
                f"⏳ {i}/{total} groups done\n"
                f"📊 Abhi tak: {grand_total} requests",
                parse_mode="Markdown",
            )
        except Exception:
            pass

    context.user_data["group_pending_data"] = group_pending_data

    # Build results table
    header = "```\nNo. | Group Name           | Pending\n" + "-" * 40 + "\n"
    rows = ""
    for idx, entry in enumerate(group_pending_data, 1):
        name = entry["group"]["name"][:20].ljust(20)
        count = str(entry["count"]).rjust(7)
        rows += f"{str(idx).ljust(3)} | {name} | {count}\n"
    footer = f"```\n📊 *TOTAL PENDING: {grand_total} requests*"

    table_text = header + rows + footer

    result_text = f"📋 *Pending Requests List*\n\n{table_text}"

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Sabhi Approve Karein", callback_data=PENDING_APPROVE_ALL),
            InlineKeyboardButton("❌ Sabhi Reject Karein", callback_data=PENDING_REJECT_ALL),
        ],
        [
            InlineKeyboardButton("📁 Export as TXT", callback_data=PENDING_EXPORT_TXT),
            InlineKeyboardButton("🔙 Back", callback_data=PENDING_BACK),
        ],
    ])

    await progress_msg.edit_text(result_text, parse_mode="Markdown", reply_markup=keyboard)
    return PENDING_SHOW_RESULTS


async def pending_action_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    action = query.data

    if action == PENDING_BACK:
        await query.edit_message_text(
            "🔙 *Wapas aa gaye.*\n\nNaya command use karein.",
            parse_mode="Markdown",
        )
        context.user_data.clear()
        return ConversationHandler.END

    if action == PENDING_EXPORT_TXT:
        group_pending_data = context.user_data.get("group_pending_data", [])
        lines = ["PENDING REQUESTS EXPORT\n", "=" * 40 + "\n"]
        grand_total = 0
        for entry in group_pending_data:
            g_name = entry["group"]["name"]
            count = entry["count"]
            grand_total += count
            lines.append(f"\nGroup: {g_name}\nPending: {count}\n")
            for r in entry["pending"]:
                lines.append(f"  - {r.get('name', 'N/A')} ({r.get('phone', 'N/A')})\n")
        lines.append(f"\n{'='*40}\nTOTAL PENDING: {grand_total}\n")
        export_text = "".join(lines)

        await query.message.reply_document(
            document=export_text.encode("utf-8"),
            filename="pending_requests.txt",
            caption="📁 *Pending requests export ready hai!*",
            parse_mode="Markdown",
        )
        return PENDING_SHOW_RESULTS

    if action in (PENDING_APPROVE_ALL, PENDING_REJECT_ALL):
        group_pending_data = context.user_data.get("group_pending_data", [])
        action_label = "Approve" if action == PENDING_APPROVE_ALL else "Reject"
        action_emoji = "✅" if action == PENDING_APPROVE_ALL else "❌"

        total_requests = sum(e["count"] for e in group_pending_data)
        done = 0
        results = []

        await query.edit_message_text(
            f"{action_emoji} *Sabhi Requests {action_label} Ho Rahi Hain...*\n\n"
            f"⏳ 0/{total_requests} done",
            parse_mode="Markdown",
        )

        for entry in group_pending_data:
            group_id = entry["group"]["id"]
            group_name = entry["group"]["name"]
            for req in entry["pending"]:
                req_id = req["id"]
                if action == PENDING_APPROVE_ALL:
                    res = await approve_request(group_id, req_id)
                else:
                    res = await reject_request(group_id, req_id)

                done += 1
                status = "✅" if res["success"] else "❌"
                results.append(f"{status} {group_name} — {req.get('name', req_id)}")

                try:
                    await query.edit_message_text(
                        f"{action_emoji} *Sabhi Requests {action_label} Ho Rahi Hain...*\n\n"
                        f"⏳ {done}/{total_requests} done\n\n"
                        + "\n".join(results[-5:]),
                        parse_mode="Markdown",
                    )
                except Exception:
                    pass

        success_count = sum(1 for r in results if r.startswith("✅"))
        final = (
            f"{action_emoji} *{action_label} Complete!*\n\n"
            f"📊 {success_count}/{total_requests} successfully {action_label.lower()}d\n\n"
            + "\n".join(results)
            + "\n\n✅ *Kaam ho gaya!*"
        )
        try:
            await query.edit_message_text(final, parse_mode="Markdown")
        except Exception:
            await query.message.reply_text(final, parse_mode="Markdown")

        context.user_data.clear()
        return ConversationHandler.END

    return PENDING_SHOW_RESULTS


async def pending_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "❌ *Operation cancel ho gaya.*\n\nWapas jaane ke liye /start karein.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    context.user_data.clear()
    return ConversationHandler.END


pending_list_handler = ConversationHandler(
    entry_points=[CommandHandler("pendinglist", pending_start)],
    states={
        PENDING_SELECT_SCOPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, pending_select_scope)],
        PENDING_SELECT_GROUPS: [MessageHandler(filters.TEXT & ~filters.COMMAND, pending_select_groups)],
        PENDING_SHOW_RESULTS: [CallbackQueryHandler(pending_action_callback)],
    },
    fallbacks=[CommandHandler("cancel", pending_cancel), MessageHandler(filters.Regex(f"^{CANCEL_TEXT}$"), pending_cancel)],
    allow_reentry=True,
)

# ═══════════════════════════════════════════════════════════════════════════════
# FEATURE 4 — GET LINK
# ═══════════════════════════════════════════════════════════════════════════════

(
    LINK_SELECT_SCOPE,
    LINK_SELECT_GROUPS,
    LINK_FETCHING,
    LINK_SHOW_RESULTS,
) = range(4)


async def link_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point: /getlink command."""
    await update.message.reply_text(
        "🔗 *Group Invite Links*\n\n"
        "Konse groups ka link lena hai?",
        parse_mode="Markdown",
        reply_markup=scope_keyboard(),
    )
    return LINK_SELECT_SCOPE


async def link_select_scope(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == CANCEL_TEXT:
        return await link_cancel(update, context)

    groups = await get_all_groups()
    context.user_data["all_groups"] = groups

    if text == "🌐 All Groups":
        context.user_data["selected_groups"] = groups
        return await link_fetch(update, context)
    elif text == "📋 Select Groups":
        group_list = groups_keyboard(groups)
        await update.message.reply_text(
            f"📋 *Groups ki list:*\n\n{group_list}\n\n"
            "Jinhe select karna hai unke number darj karein (comma se alag):\n"
            "_Example: 1, 3, 5_",
            parse_mode="Markdown",
            reply_markup=cancel_keyboard(),
        )
        return LINK_SELECT_GROUPS
    else:
        await update.message.reply_text("❗ Please diye gaye options mein se choose karein.")
        return LINK_SELECT_SCOPE


async def link_select_groups(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    if text == CANCEL_TEXT:
        return await link_cancel(update, context)

    all_groups = context.user_data.get("all_groups", [])
    selected = parse_group_selections(text, all_groups)

    if not selected:
        await update.message.reply_text(
            "❗ Koi valid group select nahi hua. Dobara try karein.\n"
            "_Example: 1, 2, 3_",
            parse_mode="Markdown",
        )
        return LINK_SELECT_GROUPS

    context.user_data["selected_groups"] = selected
    return await link_fetch(update, context)


async def link_fetch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    groups = context.user_data["selected_groups"]
    total = len(groups)

    progress_msg = await update.message.reply_text(
        "🔗 *Invite Links Fetch Ho Rahe Hain...*\n\n"
        f"⏳ 0/{total} groups done",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )

    link_results = []

    for i, group in enumerate(groups, 1):
        res = await get_invite_link(group["id"])

        if res["link"]:
            link_results.append({
                "name": group["name"],
                "link": res["link"],
                "status": "ok",
            })
        elif res.get("error") and "revoked" in str(res["error"]).lower():
            # Try generating a new link
            new_res = await revoke_and_get_invite_link(group["id"])
            if new_res["link"]:
                link_results.append({
                    "name": group["name"],
                    "link": new_res["link"],
                    "status": "regenerated",
                })
            else:
                link_results.append({
                    "name": group["name"],
                    "link": None,
                    "status": "error",
                    "error": new_res.get("error", "Unknown error"),
                })
        elif res.get("error") and "not admin" in str(res["error"]).lower():
            link_results.append({
                "name": group["name"],
                "link": None,
                "status": "not_admin",
            })
        else:
            link_results.append({
                "name": group["name"],
                "link": None,
                "status": "error",
                "error": res.get("error", "Unknown error"),
            })

        try:
            await progress_msg.edit_text(
                "🔗 *Invite Links Fetch Ho Rahe Hain...*\n\n"
                f"⏳ {i}/{total} groups done",
                parse_mode="Markdown",
            )
        except Exception:
            pass

    # Build result text
    lines = ["📎 *Group Invite Links:*\n"]
    success_count = 0
    for idx, entry in enumerate(link_results, 1):
        name = entry["name"]
        if entry["status"] == "ok":
            lines.append(f"{idx}. {name} — {entry['link']}")
            success_count += 1
        elif entry["status"] == "regenerated":
            lines.append(f"{idx}. {name} — {entry['link']} _(nayi link generate ki)_")
            success_count += 1
        elif entry["status"] == "not_admin":
            lines.append(f"{idx}. {name} — ⚠️ Bot admin nahi hai, link nahi mil sakta")
        else:
            lines.append(f"{idx}. {name} — ❌ Error: {entry.get('error', 'Unknown')}")

    lines.append(f"\n📊 *Total: {success_count}/{total} links mili*")

    result_text = "\n".join(lines)

    # Telegram message limit guard — split if too long
    if len(result_text) > 4000:
        chunks = [result_text[i:i+4000] for i in range(0, len(result_text), 4000)]
        await progress_msg.edit_text(chunks[0], parse_mode="Markdown")
        for chunk in chunks[1:]:
            await update.message.reply_text(chunk, parse_mode="Markdown")
    else:
        await progress_msg.edit_text(result_text, parse_mode="Markdown")

    context.user_data.clear()
    return ConversationHandler.END


async def link_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text(
        "❌ *Operation cancel ho gaya.*\n\nWapas jaane ke liye /start karein.",
        parse_mode="Markdown",
        reply_markup=ReplyKeyboardRemove(),
    )
    context.user_data.clear()
    return ConversationHandler.END


get_link_handler = ConversationHandler(
    entry_points=[CommandHandler("getlink", link_start)],
    states={
        LINK_SELECT_SCOPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, link_select_scope)],
        LINK_SELECT_GROUPS: [MessageHandler(filters.TEXT & ~filters.COMMAND, link_select_groups)],
    },
    fallbacks=[CommandHandler("cancel", link_cancel), MessageHandler(filters.Regex(f"^{CANCEL_TEXT}$"), link_cancel)],
    allow_reentry=True,
)

# ═══════════════════════════════════════════════════════════════════════════════
# EXPORT
# ═══════════════════════════════════════════════════════════════════════════════

def get_admin_handlers() -> list:
    """Returns list of all ConversationHandlers for admin features."""
    return [admin_handler, approval_handler, pending_list_handler, get_link_handler]
