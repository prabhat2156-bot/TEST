import asyncio
import io
import logging
import re
import time
from typing import Any

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def parse_vcf(data: bytes) -> set[str]:
    """Extract all phone numbers from a VCF file and return as a set of
    normalised digit-only strings (last 10 digits for Indian numbers)."""
    text = data.decode("utf-8", errors="ignore")
    numbers: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if line.upper().startswith("TEL"):
            # TEL;TYPE=...:+919876543210  or  TEL:9876543210
            parts = line.split(":", 1)
            if len(parts) == 2:
                raw = re.sub(r"\D", "", parts[1])
                if len(raw) >= 10:
                    numbers.add(raw[-10:])
    return numbers


def parse_txt(data: bytes) -> list[str]:
    """Extract phone numbers from a plain-text file (one per line)."""
    text = data.decode("utf-8", errors="ignore")
    numbers: list[str] = []
    for line in text.splitlines():
        raw = re.sub(r"\D", "", line.strip())
        if 7 <= len(raw) <= 15:
            numbers.append(raw)
    return numbers


def normalise(number: str) -> str:
    """Return last 10 digits of a number string."""
    digits = re.sub(r"\D", "", number)
    return digits[-10:] if len(digits) >= 10 else digits


async def fetch_group_info(client: Any, link: str) -> dict:
    """
    Placeholder — replace with your actual WhatsApp/Telethon/whatsapp-web.js
    bridge call.  Returns:
        {
            "name": str,
            "id": str,
            "members": [{"id": str, "phone": str, "name": str, "is_admin": bool}],
            "pending_requests": int,
        }
    """
    raise NotImplementedError("Implement fetch_group_info() with your WA bridge")


async def remove_member(client: Any, group_id: str, member_id: str) -> bool:
    """Remove a single member from a group. Returns True on success."""
    raise NotImplementedError("Implement remove_member() with your WA bridge")


async def add_member(client: Any, group_id: str, phone: str) -> str:
    """
    Add a member to a group.
    Returns one of: 'success', 'already_in', 'invalid', 'not_on_wa',
                    'group_full', 'rate_limit', 'error'
    """
    raise NotImplementedError("Implement add_member() with your WA bridge")


def get_wa_client(context: ContextTypes.DEFAULT_TYPE) -> Any:
    """Retrieve the shared WA client stored in bot_data."""
    return context.bot_data.get("wa_client")


# ---------------------------------------------------------------------------
# ════════════════════════════════════════════════════════════════════════════
# FEATURE 1 — CTC CHECKER
# ════════════════════════════════════════════════════════════════════════════
# ---------------------------------------------------------------------------

# --- States ---
(
    CTC_SELECT_MODE,
    CTC_A_SEND_LINKS,
    CTC_A_FETCHING,
    CTC_B_SEND_LINKS,
    CTC_B_SEND_VCF,
    CTC_B_CHECKING,
    CTC_B_ACTION_SELECT,
    CTC_B_SELECT_GROUPS,
    CTC_B_REMOVING,
) = range(9)

# Callback data
CB_PENDING = "ctc_mode_pending"
CB_MEMBERS = "ctc_mode_members"
CB_REMOVE_ALL = "ctc_remove_all"
CB_REMOVE_SELECT = "ctc_remove_select"
CB_EXPORT_LIST = "ctc_export_list"
CB_DO_NOTHING = "ctc_do_nothing"


async def ctc_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry point — /ctc_checker command."""
    context.user_data.clear()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⏳ Pending Requests Check", callback_data=CB_PENDING)],
        [InlineKeyboardButton("👥 Members Check (VCF)", callback_data=CB_MEMBERS)],
    ])
    await update.message.reply_text(
        "🔍 *CTC Checker* — Kya check karna hai?\n\n"
        "Ek option select karein:",
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN,
    )
    return CTC_SELECT_MODE


async def ctc_mode_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    mode = query.data

    if mode == CB_PENDING:
        context.user_data["ctc_mode"] = "pending"
        await query.edit_message_text(
            "⏳ *Pending Requests Check*\n\n"
            "📎 Group links bhejein (ek line mein ek link):\n\n"
            "_Example:_\n`https://chat.whatsapp.com/ABC123`\n`https://chat.whatsapp.com/XYZ456`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return CTC_A_SEND_LINKS

    else:  # CB_MEMBERS
        context.user_data["ctc_mode"] = "members"
        await query.edit_message_text(
            "👥 *Members Check (VCF ke against)*\n\n"
            "📎 Group links bhejein (ek line mein ek link):\n\n"
            "_Example:_\n`https://chat.whatsapp.com/ABC123`\n`https://chat.whatsapp.com/XYZ456`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return CTC_B_SEND_LINKS


# ── Mode A: Pending Requests ──────────────────────────────────────────────

async def ctc_a_receive_links(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text or ""
    links = [l.strip() for l in text.splitlines() if l.strip().startswith("https://chat.whatsapp.com/")]
    if not links:
        await update.message.reply_text(
            "❌ Koi valid WhatsApp group link nahi mila!\n"
            "Dobara bhejein (https://chat.whatsapp.com/... format mein)."
        )
        return CTC_A_SEND_LINKS

    context.user_data["ctc_links"] = links
    msg = await update.message.reply_text(
        f"⏳ {len(links)} group(s) ka data fetch ho raha hai... kripya rukein 🙏"
    )

    client = get_wa_client(context)
    results = []
    for i, link in enumerate(links, 1):
        try:
            info = await fetch_group_info(client, link)
            results.append({
                "name": info["name"],
                "pending": info["pending_requests"],
            })
            await msg.edit_text(
                f"⏳ Fetch ho raha hai... {i}/{len(links)} complete ✅"
            )
        except Exception as e:
            results.append({"name": link, "pending": f"Error: {e}"})

    # Build table
    lines = ["📊 *Pending Requests Report*\n"]
    lines.append(f"{'Group Name':<30} | {'Pending':>7}")
    lines.append("-" * 42)
    total = 0
    for r in results:
        p = r["pending"]
        pstr = str(p) if isinstance(p, str) else str(p)
        lines.append(f"{r['name'][:30]:<30} | {pstr:>7}")
        if isinstance(p, int):
            total += p
    lines.append("-" * 42)
    lines.append(f"{'TOTAL':<30} | {total:>7}")

    table = "```\n" + "\n".join(lines) + "\n```"
    await msg.edit_text(table, parse_mode=ParseMode.MARKDOWN)
    await update.message.reply_text(
        "✅ Pending requests check complete!\n\n"
        "Kuch aur karna hai? /menu par wapas jayein."
    )
    return ConversationHandler.END


# ── Mode B: Members vs VCF ───────────────────────────────────────────────

async def ctc_b_receive_links(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text or ""
    links = [l.strip() for l in text.splitlines() if l.strip().startswith("https://chat.whatsapp.com/")]
    if not links:
        await update.message.reply_text(
            "❌ Koi valid WhatsApp group link nahi mila!\n"
            "Dobara bhejein (https://chat.whatsapp.com/... format mein)."
        )
        return CTC_B_SEND_LINKS

    context.user_data["ctc_links"] = links
    await update.message.reply_text(
        f"✅ {len(links)} link(s) save ho gaye!\n\n"
        "📁 Ab apna *.vcf* contact file bhejein.\n"
        "_(Ek ya zyada files bhej sakte hain)_",
        parse_mode=ParseMode.MARKDOWN,
    )
    context.user_data["ctc_vcf_numbers"] = set()
    context.user_data["ctc_vcf_files_received"] = 0
    return CTC_B_SEND_VCF


async def ctc_b_receive_vcf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    doc = update.message.document
    if not doc or not doc.file_name.lower().endswith(".vcf"):
        await update.message.reply_text(
            "❌ Sirf *.vcf* file accept hogi!\n"
            "Sahi file bhejein ya /cancel karein.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return CTC_B_SEND_VCF

    file = await doc.get_file()
    data = bytes(await file.download_as_bytearray())
    new_numbers = parse_vcf(data)
    context.user_data["ctc_vcf_numbers"].update(new_numbers)
    context.user_data["ctc_vcf_files_received"] += 1
    count = len(context.user_data["ctc_vcf_numbers"])

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Bas, Check Shuru Karein", callback_data="ctc_vcf_done")],
        [InlineKeyboardButton("➕ Aur File Add Karein", callback_data="ctc_vcf_more")],
    ])
    await update.message.reply_text(
        f"📁 File #{context.user_data['ctc_vcf_files_received']} load ho gaya!\n"
        f"📞 Total contacts abhi tak: *{count}*\n\n"
        "Kya aur VCF files add karni hain?",
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN,
    )
    return CTC_B_SEND_VCF


async def ctc_vcf_more(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "📁 Agli VCF file bhejein:",
        parse_mode=ParseMode.MARKDOWN,
    )
    return CTC_B_SEND_VCF


async def ctc_vcf_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    vcf_numbers = context.user_data.get("ctc_vcf_numbers", set())
    links = context.user_data.get("ctc_links", [])

    await query.edit_message_text(
        f"🔄 {len(links)} group(s) ke members check ho rahe hain VCF ke against...\n"
        f"📞 VCF mein {len(vcf_numbers)} contacts hain.\n\n"
        "⏳ Kripya wait karein..."
    )

    client = get_wa_client(context)
    group_results = []

    for i, link in enumerate(links, 1):
        try:
            info = await fetch_group_info(client, link)
            members = info["members"]
            unknown = []
            for idx, member in enumerate(members, 1):
                phone_norm = normalise(member["phone"])
                if phone_norm not in vcf_numbers:
                    unknown.append(member)
                if idx % 10 == 0:
                    await query.edit_message_text(
                        f"🔄 Group {i}/{len(links)}: *{info['name']}*\n"
                        f"👤 Check ho rahe hain: {idx}/{len(members)}...",
                        parse_mode=ParseMode.MARKDOWN,
                    )
            group_results.append({
                "name": info["name"],
                "id": info["id"],
                "total": len(members),
                "unknown": unknown,
                "link": link,
            })
        except Exception as e:
            group_results.append({
                "name": link,
                "id": None,
                "total": 0,
                "unknown": [],
                "error": str(e),
                "link": link,
            })

    context.user_data["ctc_group_results"] = group_results

    # Show unknown members per group
    lines = ["👥 *Unknown Members Report*\n"]
    total_unknown = 0
    for gr in group_results:
        if "error" in gr:
            lines.append(f"❌ *{gr['name']}* — Error: {gr['error']}")
            continue
        unk_count = len(gr["unknown"])
        total_unknown += unk_count
        lines.append(f"📌 *{gr['name']}*: {unk_count} unknown / {gr['total']} total")
        for m in gr["unknown"][:10]:
            lines.append(f"   • {m.get('name', 'Unknown')} ({m['phone']})")
        if unk_count > 10:
            lines.append(f"   _...aur {unk_count - 10} members_")
        lines.append("")

    lines.append(f"📊 *Total Unknown: {total_unknown}*")
    report = "\n".join(lines)

    # Split if too long
    if len(report) > 3800:
        report = report[:3800] + "\n\n_...list truncated_"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑️ Sabko Remove Karein (All Groups)", callback_data=CB_REMOVE_ALL)],
        [InlineKeyboardButton("🎯 Select Groups se Remove Karein", callback_data=CB_REMOVE_SELECT)],
        [InlineKeyboardButton("📋 List Export Karein", callback_data=CB_EXPORT_LIST)],
        [InlineKeyboardButton("❌ Kuch Nahi Karna", callback_data=CB_DO_NOTHING)],
    ])
    await query.edit_message_text(
        report,
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN,
    )
    return CTC_B_ACTION_SELECT


async def ctc_action_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    action = query.data
    group_results = context.user_data.get("ctc_group_results", [])

    if action == CB_DO_NOTHING:
        await query.edit_message_text("✅ Theek hai! Koi action nahi liya gaya. /menu par wapas jayein.")
        return ConversationHandler.END

    if action == CB_EXPORT_LIST:
        lines = ["Unknown Members Export\n"]
        for gr in group_results:
            lines.append(f"\nGroup: {gr['name']}")
            lines.append("-" * 30)
            for m in gr.get("unknown", []):
                lines.append(f"{m.get('name','Unknown')}\t{m['phone']}")
        export_text = "\n".join(lines)
        buf = io.BytesIO(export_text.encode("utf-8"))
        buf.name = "unknown_members.txt"
        await query.message.reply_document(
            document=buf,
            filename="unknown_members.txt",
            caption="📋 Unknown members list export ho gaya!",
        )
        await query.edit_message_text("✅ List export ho gaya! /menu par wapas jayein.")
        return ConversationHandler.END

    if action == CB_REMOVE_SELECT:
        lines = ["🎯 *Kaunsa group select karna hai?*\n"]
        valid = [gr for gr in group_results if gr.get("unknown")]
        for i, gr in enumerate(valid, 1):
            lines.append(f"{i}. {gr['name']} ({len(gr['unknown'])} unknown)")
        context.user_data["ctc_valid_groups"] = valid
        await query.edit_message_text(
            "\n".join(lines) + "\n\n"
            "📝 Number(s) type karein (comma-separated):\n_Example: 1,3_",
            parse_mode=ParseMode.MARKDOWN,
        )
        return CTC_B_SELECT_GROUPS

    # CB_REMOVE_ALL
    context.user_data["ctc_selected_groups"] = [gr for gr in group_results if gr.get("unknown")]
    return await ctc_do_remove(query, context)


async def ctc_select_groups_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text or ""
    valid = context.user_data.get("ctc_valid_groups", [])
    try:
        indices = [int(x.strip()) - 1 for x in text.split(",")]
        selected = [valid[i] for i in indices if 0 <= i < len(valid)]
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Invalid input! Sirf numbers aur commas use karein. Dobara try karein.")
        return CTC_B_SELECT_GROUPS

    if not selected:
        await update.message.reply_text("❌ Koi valid group select nahi hua. Dobara try karein.")
        return CTC_B_SELECT_GROUPS

    context.user_data["ctc_selected_groups"] = selected
    msg = await update.message.reply_text("⏳ Remove shuru ho raha hai...")
    return await _ctc_remove_unknown(msg, context)


async def ctc_do_remove(query, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = await query.message.reply_text("⏳ Remove shuru ho raha hai...")
    return await _ctc_remove_unknown(msg, context)


async def _ctc_remove_unknown(msg, context: ContextTypes.DEFAULT_TYPE) -> int:
    client = get_wa_client(context)
    selected = context.user_data.get("ctc_selected_groups", [])
    summary = []

    for gr in selected:
        removed = 0
        failed = 0
        unknown = gr.get("unknown", [])
        for idx, member in enumerate(unknown, 1):
            await msg.edit_text(
                f"🗑️ *{gr['name']}* se remove ho raha hai...\n"
                f"👤 {member.get('name','Unknown')} ({member['phone']})\n"
                f"Progress: {idx}/{len(unknown)}",
                parse_mode=ParseMode.MARKDOWN,
            )
            try:
                success = await remove_member(client, gr["id"], member["id"])
                if success:
                    removed += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
            await asyncio.sleep(2.5)
        summary.append(f"✅ *{gr['name']}*: {removed} removed, {failed} failed")

    await msg.edit_text(
        "🎉 *Remove Complete!*\n\n" + "\n".join(summary),
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END


async def ctc_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ CTC Checker cancel ho gaya. /menu par wapas jayein.")
    context.user_data.clear()
    return ConversationHandler.END


ctc_checker_handler = ConversationHandler(
    entry_points=[CommandHandler("ctc_checker", ctc_start)],
    states={
        CTC_SELECT_MODE: [
            CallbackQueryHandler(ctc_mode_selected, pattern=f"^({CB_PENDING}|{CB_MEMBERS})$"),
        ],
        CTC_A_SEND_LINKS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, ctc_a_receive_links),
        ],
        CTC_B_SEND_LINKS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, ctc_b_receive_links),
        ],
        CTC_B_SEND_VCF: [
            MessageHandler(filters.Document.ALL, ctc_b_receive_vcf),
            CallbackQueryHandler(ctc_vcf_more, pattern="^ctc_vcf_more$"),
            CallbackQueryHandler(ctc_vcf_done, pattern="^ctc_vcf_done$"),
        ],
        CTC_B_ACTION_SELECT: [
            CallbackQueryHandler(
                ctc_action_selected,
                pattern=f"^({CB_REMOVE_ALL}|{CB_REMOVE_SELECT}|{CB_EXPORT_LIST}|{CB_DO_NOTHING})$",
            ),
        ],
        CTC_B_SELECT_GROUPS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, ctc_select_groups_input),
        ],
    },
    fallbacks=[CommandHandler("cancel", ctc_cancel)],
    name="ctc_checker",
    persistent=False,
)


# ---------------------------------------------------------------------------
# ════════════════════════════════════════════════════════════════════════════
# FEATURE 2 — REMOVE MEMBERS
# ════════════════════════════════════════════════════════════════════════════
# ---------------------------------------------------------------------------

(
    RM_SELECT_SCOPE,
    RM_SELECT_GROUPS,
    RM_CONFIRM,
    RM_REMOVING,
) = range(10, 14)

CB_RM_ALL = "rm_scope_all"
CB_RM_SELECT = "rm_scope_select"
CB_RM_YES = "rm_confirm_yes"
CB_RM_NO = "rm_confirm_no"


async def rm_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 All Groups", callback_data=CB_RM_ALL)],
        [InlineKeyboardButton("🎯 Select Groups", callback_data=CB_RM_SELECT)],
    ])
    await update.message.reply_text(
        "🗑️ *Remove Members*\n\n"
        "⚠️ Yeh feature selected group(s) ke SABHI members ko remove kar dega!\n\n"
        "Kahan se remove karna hai?",
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN,
    )
    return RM_SELECT_SCOPE


async def rm_scope_selected(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    scope = query.data

    if scope == CB_RM_ALL:
        context.user_data["rm_scope"] = "all"
        await query.edit_message_text(
            "📎 Un *sabhi* groups ke links bhejein jahan se members remove karne hain\n"
            "(Ek line mein ek link):",
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        context.user_data["rm_scope"] = "select"
        await query.edit_message_text(
            "📎 Sabhi groups ke links bhejein (hum baad mein select karenge)\n"
            "(Ek line mein ek link):",
            parse_mode=ParseMode.MARKDOWN,
        )
    return RM_SELECT_GROUPS


async def rm_receive_links(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text or ""
    links = [l.strip() for l in text.splitlines() if l.strip().startswith("https://chat.whatsapp.com/")]
    if not links:
        await update.message.reply_text("❌ Koi valid link nahi mila! Dobara bhejein.")
        return RM_SELECT_GROUPS

    msg = await update.message.reply_text("⏳ Group info fetch ho raha hai...")
    client = get_wa_client(context)
    groups = []
    for link in links:
        try:
            info = await fetch_group_info(client, link)
            groups.append(info)
        except Exception as e:
            await update.message.reply_text(f"⚠️ Link fetch failed: {link}\nError: {e}")

    if not groups:
        await msg.edit_text("❌ Koi group fetch nahi hua. Dobara try karein.")
        return RM_SELECT_GROUPS

    context.user_data["rm_all_groups"] = groups

    if context.user_data["rm_scope"] == "select":
        lines = ["🎯 *Kaunsa group select karna hai?*\n"]
        for i, gr in enumerate(groups, 1):
            lines.append(f"{i}. {gr['name']} ({len(gr['members'])} members)")
        await msg.edit_text(
            "\n".join(lines) + "\n\n"
            "📝 Number(s) type karein (comma-separated):\n_Example: 1,3_",
            parse_mode=ParseMode.MARKDOWN,
        )
        return RM_SELECT_GROUPS  # reuse state for group selection input

    else:
        # all groups — go to confirm
        context.user_data["rm_selected_groups"] = groups
        return await rm_show_confirm(msg, context)


async def rm_select_groups_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    # This handler is called only when rm_scope == 'select' and links already received
    all_groups = context.user_data.get("rm_all_groups")
    if not all_groups:
        # Still receiving links
        return await rm_receive_links(update, context)

    text = update.message.text or ""
    try:
        indices = [int(x.strip()) - 1 for x in text.split(",")]
        selected = [all_groups[i] for i in indices if 0 <= i < len(all_groups)]
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Invalid input! Sirf numbers aur commas use karein.")
        return RM_SELECT_GROUPS

    if not selected:
        await update.message.reply_text("❌ Koi valid group select nahi hua. Dobara try karein.")
        return RM_SELECT_GROUPS

    context.user_data["rm_selected_groups"] = selected
    msg = await update.message.reply_text("...")
    return await rm_show_confirm(msg, context)


async def rm_show_confirm(msg, context: ContextTypes.DEFAULT_TYPE) -> int:
    selected = context.user_data.get("rm_selected_groups", [])
    total_members = sum(len(gr["members"]) for gr in selected)

    lines = [
        "⚠️ *KHABARDAR — BADI KARVAI!* ⚠️\n",
        f"Neeche diye gaye *{len(selected)} group(s)* ke *~{total_members} members* remove honge:\n",
    ]
    for gr in selected:
        lines.append(f"• {gr['name']} ({len(gr['members'])} members)")
    lines.append("\n🔴 *Kya aap PAKKA sure hain?*")
    lines.append("_Yeh action UNDO nahi ho sakta!_")

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Haan, Remove Karo!", callback_data=CB_RM_YES)],
        [InlineKeyboardButton("❌ Nahi, Ruk Jao", callback_data=CB_RM_NO)],
    ])
    await msg.edit_text("\n".join(lines), reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    return RM_CONFIRM


async def rm_confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == CB_RM_NO:
        await query.edit_message_text("✅ Sahi kiya! Koi member remove nahi hua. /menu par wapas jayein.")
        return ConversationHandler.END

    # Start removing
    client = get_wa_client(context)
    selected = context.user_data.get("rm_selected_groups", [])
    bot_phone = context.bot_data.get("bot_phone", "")
    summary = []

    for gr in selected:
        removed = 0
        skipped_self = 0
        skipped_admin = 0
        failed = 0
        members = gr["members"]
        non_admin_members = [m for m in members if not m.get("is_admin", False)]

        for idx, member in enumerate(non_admin_members, 1):
            # Skip self
            if normalise(member["phone"]) == normalise(bot_phone):
                skipped_self += 1
                continue

            await query.message.reply_text(
                f"🗑️ *{gr['name']}* — Member remove ho raha hai...\n"
                f"👤 {member.get('name','Unknown')} ({member['phone']})\n"
                f"📊 Progress: {idx}/{len(non_admin_members)}",
                parse_mode=ParseMode.MARKDOWN,
            )

            try:
                ok = await remove_member(client, gr["id"], member["id"])
                if ok:
                    removed += 1
                else:
                    failed += 1
            except Exception as ex:
                err = str(ex).lower()
                if "not admin" in err:
                    await query.message.reply_text(f"⚠️ {gr['name']}: Bot admin nahi hai! Group skip ho raha hai.")
                    break
                elif "rate" in err:
                    await query.message.reply_text(f"⏳ Rate limit! 10 seconds wait kar rahe hain...")
                    await asyncio.sleep(10)
                    failed += 1
                elif "already left" in err:
                    skipped_admin += 1
                else:
                    failed += 1
            await asyncio.sleep(2.5)

        admins_in_group = len(members) - len(non_admin_members)
        summary.append(
            f"✅ *{gr['name']}*:\n"
            f"   Removed: {removed} | Failed: {failed} | "
            f"Admins (skipped): {admins_in_group} | Self: {skipped_self}"
        )

    await query.message.reply_text(
        "🎉 *Remove Members — Complete!*\n\n" + "\n\n".join(summary),
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END


async def rm_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Remove Members cancel ho gaya. /menu par wapas jayein.")
    context.user_data.clear()
    return ConversationHandler.END


remove_members_handler = ConversationHandler(
    entry_points=[CommandHandler("remove_members", rm_start)],
    states={
        RM_SELECT_SCOPE: [
            CallbackQueryHandler(rm_scope_selected, pattern=f"^({CB_RM_ALL}|{CB_RM_SELECT})$"),
        ],
        RM_SELECT_GROUPS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, rm_select_groups_input),
        ],
        RM_CONFIRM: [
            CallbackQueryHandler(rm_confirm_handler, pattern=f"^({CB_RM_YES}|{CB_RM_NO})$"),
        ],
    },
    fallbacks=[CommandHandler("cancel", rm_cancel)],
    name="remove_members",
    persistent=False,
)


# ---------------------------------------------------------------------------
# ════════════════════════════════════════════════════════════════════════════
# FEATURE 3 — ADD MEMBERS
# ════════════════════════════════════════════════════════════════════════════
# ---------------------------------------------------------------------------

(
    AM_SEND_LINKS,
    AM_SEND_FILES,
    AM_HANDLE_MISMATCH,
    AM_CONFIRM,
    AM_ADDING,
) = range(20, 25)

CB_AM_DONE_FILES = "am_files_done"
CB_AM_ONE_TO_ALL = "am_mismatch_one_to_all"
CB_AM_TRIM = "am_mismatch_trim"
CB_AM_REENTER = "am_mismatch_reenter"
CB_AM_CONFIRM_YES = "am_confirm_yes"
CB_AM_CONFIRM_NO = "am_confirm_no"


async def am_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.clear()
    await update.message.reply_text(
        "➕ *Add Members*\n\n"
        "📎 Un group links ko bhejein jahan members add karne hain.\n"
        "(Ek line mein ek link)\n\n"
        "_Example:_\n`https://chat.whatsapp.com/ABC123`\n`https://chat.whatsapp.com/XYZ456`",
        parse_mode=ParseMode.MARKDOWN,
    )
    return AM_SEND_LINKS


async def am_receive_links(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text or ""
    links = [l.strip() for l in text.splitlines() if l.strip().startswith("https://chat.whatsapp.com/")]
    if not links:
        await update.message.reply_text("❌ Koi valid WhatsApp group link nahi mila! Dobara bhejein.")
        return AM_SEND_LINKS

    context.user_data["am_links"] = links
    context.user_data["am_files"] = []  # list of {"name": str, "numbers": list[str]}
    await update.message.reply_text(
        f"✅ *{len(links)} link(s)* save ho gaye!\n\n"
        "📁 Ab number files bhejein (*.vcf* ya *.txt* — dono supported).\n"
        "Multiple files bhej sakte hain. Done hone par button dabayein.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return AM_SEND_FILES


async def am_receive_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    doc = update.message.document
    if not doc:
        await update.message.reply_text("❌ File nahi mili! .vcf ya .txt file bhejein.")
        return AM_SEND_FILES

    fname = doc.file_name.lower()
    if not (fname.endswith(".vcf") or fname.endswith(".txt")):
        await update.message.reply_text(
            "❌ Sirf *.vcf* ya *.txt* files accept hongi!\n"
            "Sahi file bhejein.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return AM_SEND_FILES

    file = await doc.get_file()
    data = bytes(await file.download_as_bytearray())

    if fname.endswith(".vcf"):
        numbers_set = parse_vcf(data)
        numbers = list(numbers_set)
    else:
        numbers = parse_txt(data)

    context.user_data["am_files"].append({"name": doc.file_name, "numbers": numbers})
    total_files = len(context.user_data["am_files"])

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Done, Aage Badho", callback_data=CB_AM_DONE_FILES)],
        [InlineKeyboardButton("➕ Aur File Add Karein", callback_data="am_more_files")],
    ])
    await update.message.reply_text(
        f"📁 *File #{total_files}* load ho gaya: `{doc.file_name}`\n"
        f"📞 Numbers: *{len(numbers)}*\n\n"
        "Kya aur files add karni hain?",
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN,
    )
    return AM_SEND_FILES


async def am_more_files(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📁 Agli file bhejein (.vcf ya .txt):")
    return AM_SEND_FILES


async def am_files_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    links = context.user_data.get("am_links", [])
    files = context.user_data.get("am_files", [])

    if not files:
        await query.edit_message_text("❌ Koi file nahi mili! Pehle file bhejein.")
        return AM_SEND_FILES

    if len(links) == len(files):
        # Perfect match — show pairing
        return await am_show_pairing(query, context)

    # Mismatch
    context.user_data["am_mismatch"] = True
    lines = [
        "⚠️ *Mismatch!*\n",
        f"🔗 Links: {len(links)}",
        f"📁 Files: {len(files)}\n",
        "Kya karna chahte hain?",
    ]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"📋 File 1 sabhi {len(links)} groups mein use karein",
            callback_data=CB_AM_ONE_TO_ALL,
        )],
        [InlineKeyboardButton(
            f"✂️ Sirf pehle {min(len(links), len(files))} pairs use karein",
            callback_data=CB_AM_TRIM,
        )],
        [InlineKeyboardButton("🔄 Files dobara bhejein", callback_data=CB_AM_REENTER)],
    ])
    await query.edit_message_text("\n".join(lines), reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    return AM_HANDLE_MISMATCH


async def am_mismatch_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    choice = query.data
    links = context.user_data.get("am_links", [])
    files = context.user_data.get("am_files", [])

    if choice == CB_AM_ONE_TO_ALL:
        # Use first file for all links
        context.user_data["am_pairs"] = [{"link": l, "file": files[0]} for l in links]
    elif choice == CB_AM_TRIM:
        count = min(len(links), len(files))
        context.user_data["am_pairs"] = [
            {"link": links[i], "file": files[i]} for i in range(count)
        ]
    elif choice == CB_AM_REENTER:
        context.user_data["am_files"] = []
        await query.edit_message_text(
            "📁 Files dubara bhejein (.vcf ya .txt).\n"
            "Done hone par button dabayein."
        )
        return AM_SEND_FILES

    return await am_show_pairing(query, context)


async def am_show_pairing(query_or_update, context: ContextTypes.DEFAULT_TYPE) -> int:
    pairs = context.user_data.get("am_pairs")
    if not pairs:
        links = context.user_data.get("am_links", [])
        files = context.user_data.get("am_files", [])
        pairs = [{"link": links[i], "file": files[i]} for i in range(min(len(links), len(files)))]
        context.user_data["am_pairs"] = pairs

    lines = ["📋 *Pairing Summary*\n"]
    total_numbers = 0
    for i, pair in enumerate(pairs, 1):
        count = len(pair["file"]["numbers"])
        total_numbers += count
        short_link = pair["link"].split("/")[-1]
        lines.append(f"*{i}.* `...{short_link}` ← `{pair['file']['name']}` ({count} numbers)")

    lines.append(f"\n📊 Total numbers to add: *{total_numbers}*")
    lines.append("\n✅ Kya yeh pairing sahi hai?")

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Haan, Add Karo!", callback_data=CB_AM_CONFIRM_YES)],
        [InlineKeyboardButton("❌ Cancel", callback_data=CB_AM_CONFIRM_NO)],
    ])

    if hasattr(query_or_update, "edit_message_text"):
        await query_or_update.edit_message_text(
            "\n".join(lines), reply_markup=kb, parse_mode=ParseMode.MARKDOWN
        )
    else:
        await query_or_update.message.reply_text(
            "\n".join(lines), reply_markup=kb, parse_mode=ParseMode.MARKDOWN
        )
    return AM_CONFIRM


async def am_confirm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == CB_AM_CONFIRM_NO:
        await query.edit_message_text("❌ Add Members cancel ho gaya. /menu par wapas jayein.")
        return ConversationHandler.END

    client = get_wa_client(context)
    pairs = context.user_data.get("am_pairs", [])
    summary = []

    for pair in pairs:
        link = pair["link"]
        file_info = pair["file"]
        numbers = file_info["numbers"]
        short_link = link.split("/")[-1]

        added = 0
        already_in = 0
        invalid = 0
        not_on_wa = 0
        group_full = 0
        rate_limited = 0
        errors = 0

        try:
            info = await fetch_group_info(client, link)
            group_id = info["id"]
            group_name = info["name"]
        except Exception as e:
            summary.append(f"❌ `...{short_link}`: Group fetch failed — {e}")
            continue

        for idx, number in enumerate(numbers, 1):
            await query.message.reply_text(
                f"➕ *{group_name}* — Member add ho raha hai...\n"
                f"📞 {number}\n"
                f"📊 Progress: {idx}/{len(numbers)}",
                parse_mode=ParseMode.MARKDOWN,
            )

            delay = 3 + (2 * (idx % 2))  # Alternate 3 and 5 seconds
            try:
                result = await add_member(client, group_id, number)
                if result == "success":
                    added += 1
                elif result == "already_in":
                    already_in += 1
                elif result == "invalid":
                    invalid += 1
                elif result == "not_on_wa":
                    not_on_wa += 1
                elif result == "group_full":
                    group_full += 1
                    await query.message.reply_text(f"⚠️ *{group_name}* full ho gaya!", parse_mode=ParseMode.MARKDOWN)
                    break
                elif result == "rate_limit":
                    rate_limited += 1
                    await query.message.reply_text("⏳ Rate limit! 15 seconds ruk rahe hain...")
                    await asyncio.sleep(15)
                else:
                    errors += 1
            except Exception as ex:
                err = str(ex).lower()
                if "rate" in err:
                    rate_limited += 1
                    await asyncio.sleep(15)
                else:
                    errors += 1
            await asyncio.sleep(delay)

        summary.append(
            f"✅ *{group_name}*:\n"
            f"   ✔️ Added: {added} | ⏭️ Already in: {already_in}\n"
            f"   ❌ Invalid: {invalid} | 🚫 Not on WA: {not_on_wa}\n"
            f"   📦 Group Full: {group_full} | ⏳ Rate limited: {rate_limited} | ⚠️ Errors: {errors}"
        )

    await query.message.reply_text(
        "🎉 *Add Members — Complete!*\n\n" + "\n\n".join(summary),
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END


async def am_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Add Members cancel ho gaya. /menu par wapas jayein.")
    context.user_data.clear()
    return ConversationHandler.END


add_members_handler = ConversationHandler(
    entry_points=[CommandHandler("add_members", am_start)],
    states={
        AM_SEND_LINKS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, am_receive_links),
        ],
        AM_SEND_FILES: [
            MessageHandler(filters.Document.ALL, am_receive_file),
            CallbackQueryHandler(am_more_files, pattern="^am_more_files$"),
            CallbackQueryHandler(am_files_done, pattern=f"^{CB_AM_DONE_FILES}$"),
        ],
        AM_HANDLE_MISMATCH: [
            CallbackQueryHandler(
                am_mismatch_handler,
                pattern=f"^({CB_AM_ONE_TO_ALL}|{CB_AM_TRIM}|{CB_AM_REENTER})$",
            ),
        ],
        AM_CONFIRM: [
            CallbackQueryHandler(
                am_confirm_handler,
                pattern=f"^({CB_AM_CONFIRM_YES}|{CB_AM_CONFIRM_NO})$",
            ),
        ],
    },
    fallbacks=[CommandHandler("cancel", am_cancel)],
    name="add_members",
    persistent=False,
)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def get_member_handlers() -> list:
    """Returns list of all ConversationHandlers for member features."""
    return [ctc_checker_handler, remove_members_handler, add_members_handler]
