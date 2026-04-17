import asyncio
import csv
import io
import logging
import os
import re
import sqlite3
import tempfile
import zipfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import openpyxl
import phonenumbers
import pytesseract
import vobject
from PIL import Image
from telegram import (
    Bot,
    Document,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    Message,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ──────────────────────────────────────────────────────────────────────────────

BOT_TOKEN = "7727685861:AAEJms2Jsjgusw0KDJ3yeytqHDstpuZ-8Bc"   # ← replace with your BotFather token
BOT_NAME  = "VCF MAKER "
DB_PATH   = "vcf_bot.db"

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# CONVERSATION STATES  (unique integers for every ConversationHandler)
# ──────────────────────────────────────────────────────────────────────────────

# File Analysis
(
    FA_WAIT_FILE,
) = range(1000, 1001)

# File Converter
(
    FC_WAIT_FILE,
    FC_WAIT_FORMAT,
) = range(1100, 1102)

# Quick VCF
(
    QV_WAIT_NAME,
    QV_WAIT_CONTACT_NAME,
    QV_WAIT_CONTACT_NUMBER,
    QV_WAIT_MORE,
) = range(1200, 1204)

# VCF Maker (Advanced)
(
    VM_WAIT_VCF_NAME,
    VM_WAIT_BASE_NAME,
    VM_WAIT_PER_FILE,
    VM_WAIT_CONTACT_START,
    VM_WAIT_FILE_START,
    VM_WAIT_CC,
    VM_WAIT_GROUP_NAME,
    VM_WAIT_CONFIRM,
    VM_WAIT_NUMBERS_FILE,
    VM_WAIT_MERGE_CHOICE,
) = range(1300, 1310)

# Split File
(
    SP_WAIT_FILE,
    SP_WAIT_COUNT,
) = range(1400, 1402)

# Merge Files
(
    MG_WAIT_FILES,
) = range(1500, 1501)

# File Editor
(
    ED_WAIT_FILE,
    ED_SHOW_LIST,
    ED_WAIT_EDIT_CHOICE,
    ED_WAIT_CONTACT_IDX,
    ED_WAIT_EDIT_FIELD,
    ED_WAIT_NEW_VALUE,
    ED_WAIT_ADD_NAME,
    ED_WAIT_ADD_NUMBER,
) = range(1600, 1608)

# List Maker
(
    LM_WAIT_NAME,
    LM_WAIT_SCREENSHOTS,
) = range(1700, 1702)

# Rename File
(
    RF_WAIT_FILE,
    RF_WAIT_NAME,
) = range(1800, 1802)

# Rename Contact
(
    RC_WAIT_FILE,
    RC_WAIT_BASE_NAME,
) = range(1900, 1902)

# Settings
(
    ST_SHOW,
    ST_WAIT_KEY,
    ST_WAIT_VALUE,
) = range(2000, 2003)

# Reset
(
    RS_WAIT_CONFIRM,
) = range(2100, 2101)

# ──────────────────────────────────────────────────────────────────────────────
# DATABASE
# ──────────────────────────────────────────────────────────────────────────────

DEFAULT_SETTINGS = {
    "default_contact_name": "Contact",
    "default_country_code": "+91",
    "default_per_file":     "500",
    "default_file_start":   "1",
    "default_contact_start":"1",
    "default_group_name":   "Group",
}

def db_init() -> None:
    """Create tables if they do not exist."""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_settings (
                user_id   INTEGER NOT NULL,
                key       TEXT    NOT NULL,
                value     TEXT    NOT NULL,
                PRIMARY KEY (user_id, key)
            )
        """)
        conn.commit()

def get_setting(user_id: int, key: str) -> str:
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            "SELECT value FROM user_settings WHERE user_id=? AND key=?",
            (user_id, key),
        ).fetchone()
    return row[0] if row else DEFAULT_SETTINGS.get(key, "")

def set_setting(user_id: int, key: str, value: str) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT OR REPLACE INTO user_settings (user_id, key, value) VALUES (?,?,?)",
            (user_id, key, value),
        )
        conn.commit()

def reset_settings(user_id: int) -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM user_settings WHERE user_id=?", (user_id,))
        conn.commit()

def get_all_settings(user_id: int) -> Dict[str, str]:
    result = dict(DEFAULT_SETTINGS)
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT key, value FROM user_settings WHERE user_id=?", (user_id,)
        ).fetchall()
    for k, v in rows:
        result[k] = v
    return result

# ──────────────────────────────────────────────────────────────────────────────
# HELPER UTILITIES
# ──────────────────────────────────────────────────────────────────────────────

def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]])

def back_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔙 Back", callback_data="back"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
        ]
    ])

def main_menu_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton("📊 File Analysis",      callback_data="menu_analysis"),
         InlineKeyboardButton("🔄 File Converter",     callback_data="menu_converter")],
        [InlineKeyboardButton("⚡ Quick VCF",           callback_data="menu_quick_vcf"),
         InlineKeyboardButton("🛠 VCF Maker",           callback_data="menu_vcf_maker")],
        [InlineKeyboardButton("✂️ Split File",          callback_data="menu_split"),
         InlineKeyboardButton("🔗 Merge Files",         callback_data="menu_merge")],
        [InlineKeyboardButton("✏️ File Editor",         callback_data="menu_editor"),
         InlineKeyboardButton("📋 List Maker",          callback_data="menu_list_maker")],
        [InlineKeyboardButton("📝 Rename File",         callback_data="menu_rename_file"),
         InlineKeyboardButton("👤 Rename Contact",      callback_data="menu_rename_contact")],
        [InlineKeyboardButton("⚙️ Settings",            callback_data="menu_settings"),
         InlineKeyboardButton("🔄 Reset",               callback_data="menu_reset")],
        [InlineKeyboardButton("❓ Help",                callback_data="menu_help")],
    ]
    return InlineKeyboardMarkup(buttons)

def escape_md(text: str) -> str:
    """Escape MarkdownV2 special characters."""
    special = r"_*[]()~`>#+-=|{}.!\\"
    return re.sub(r"([" + re.escape(special) + r"])", r"\\\1", str(text))

# ──────────────────────────────────────────────────────────────────────────────
# VCF / NUMBER PARSING HELPERS
# ──────────────────────────────────────────────────────────────────────────────

def parse_numbers_from_text(raw: str) -> List[str]:
    """Extract all plausible phone numbers from free-form text."""
    lines = [l.strip() for l in raw.splitlines()]
    numbers: List[str] = []
    for line in lines:
        # Remove common separators
        cleaned = re.sub(r"[\s\-\.\(\)]+", "", line)
        if cleaned:
            numbers.append(cleaned)
    return [n for n in numbers if 5 <= len(re.sub(r"[^\d+]", "", n)) <= 16]

def parse_vcf_contacts(vcf_text: str) -> List[Dict[str, str]]:
    """Return list of {name, number} dicts from VCF content."""
    contacts: List[Dict[str, str]] = []
    try:
        for vcard in vobject.readComponents(vcf_text):
            name   = ""
            number = ""
            if hasattr(vcard, "fn"):
                name = vcard.fn.value
            elif hasattr(vcard, "n"):
                n = vcard.n.value
                name = f"{n.given} {n.family}".strip()
            if hasattr(vcard, "tel"):
                number = vcard.tel.value
            contacts.append({"name": name, "number": number})
    except Exception as e:
        logger.warning(f"VCF parse warning: {e}")
    return contacts

def make_vcard(name: str, number: str) -> str:
    """Generate a minimal vCard 3.0 string."""
    return (
        "BEGIN:VCARD\r\n"
        "VERSION:3.0\r\n"
        f"FN:{name}\r\n"
        f"TEL;TYPE=CELL:{number}\r\n"
        "END:VCARD\r\n"
    )

def make_vcf_bytes(contacts: List[Dict[str, str]]) -> bytes:
    return "".join(make_vcard(c["name"], c["number"]) for c in contacts).encode("utf-8")

def detect_country(number: str) -> str:
    """Return country name for a phone number string, or 'Unknown'."""
    try:
        parsed = phonenumbers.parse(number, None)
        region = phonenumbers.region_code_for_number(parsed)
        return phonenumbers.SUPPORTED_REGIONS.get(region, region) if region else "Unknown"
    except Exception:
        pass
    # Try common prefixes
    digits = re.sub(r"[^\d]", "", number)
    cc_map = {
        "1": "USA/Canada", "91": "India", "44": "UK", "61": "Australia",
        "92": "Pakistan", "880": "Bangladesh", "977": "Nepal",
        "94": "Sri Lanka", "971": "UAE", "966": "Saudi Arabia",
        "20": "Egypt", "234": "Nigeria", "27": "South Africa",
        "86": "China", "81": "Japan", "82": "South Korea",
        "49": "Germany", "33": "France", "39": "Italy",
        "7": "Russia", "55": "Brazil", "52": "Mexico",
    }
    for prefix in sorted(cc_map.keys(), key=len, reverse=True):
        if digits.startswith(prefix):
            return cc_map[prefix]
    return "Unknown"

def is_valid_number(number: str) -> bool:
    digits = re.sub(r"[^\d]", "", number)
    return 7 <= len(digits) <= 15

def read_contacts_from_file(content: bytes, filename: str) -> List[Dict[str, str]]:
    """
    Parse contacts from VCF / TXT / CSV file bytes.
    Returns list of {name, number}.
    """
    ext = Path(filename).suffix.lower()
    contacts: List[Dict[str, str]] = []

    if ext == ".vcf":
        text = content.decode("utf-8", errors="replace")
        contacts = parse_vcf_contacts(text)

    elif ext == ".csv":
        text = content.decode("utf-8", errors="replace")
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            # Try to find name / number columns
            name   = row.get("name") or row.get("Name") or row.get("NAME") or ""
            number = (
                row.get("number") or row.get("Number") or row.get("phone")
                or row.get("Phone") or row.get("tel") or ""
            )
            if not number:
                # Use first column that looks like a number
                for v in row.values():
                    if v and re.search(r"\d{5,}", v):
                        number = v
                        break
            contacts.append({"name": name.strip(), "number": number.strip()})

    elif ext == ".txt":
        text = content.decode("utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            # Check if line has a comma/tab separator (name, number)
            if "\t" in line:
                parts = line.split("\t", 1)
                contacts.append({"name": parts[0].strip(), "number": parts[1].strip()})
            elif "," in line:
                parts = line.split(",", 1)
                contacts.append({"name": parts[0].strip(), "number": parts[1].strip()})
            else:
                contacts.append({"name": "", "number": line})

    elif ext in (".xlsx", ".xls"):
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True)
        ws = wb.active
        headers: List[str] = []
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            if i == 0:
                headers = [str(c).lower() if c else "" for c in row]
                continue
            row_dict = dict(zip(headers, row))
            name   = str(row_dict.get("name", "") or "")
            number = str(
                row_dict.get("number", "") or row_dict.get("phone", "")
                or row_dict.get("tel", "") or ""
            )
            if not number:
                for v in row:
                    sv = str(v or "")
                    if re.search(r"\d{5,}", sv):
                        number = sv
                        break
            contacts.append({"name": name.strip(), "number": number.strip()})

    return contacts

def contacts_to_format(contacts: List[Dict[str, str]], fmt: str, filename_stem: str) -> Tuple[bytes, str]:
    """
    Convert contacts list to the requested format.
    Returns (file_bytes, suggested_filename).
    """
    fmt = fmt.lower()
    if fmt == "vcf":
        data = make_vcf_bytes(contacts)
        return data, f"{filename_stem}.vcf"

    elif fmt == "txt":
        lines = []
        for c in contacts:
            if c["name"]:
                lines.append(f"{c['name']},{c['number']}")
            else:
                lines.append(c["number"])
        return "\n".join(lines).encode("utf-8"), f"{filename_stem}.txt"

    elif fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["name", "number"])
        for c in contacts:
            writer.writerow([c["name"], c["number"]])
        return buf.getvalue().encode("utf-8"), f"{filename_stem}.csv"

    elif fmt == "excel":
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Contacts"
        ws.append(["Name", "Number"])
        for c in contacts:
            ws.append([c["name"], c["number"]])
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue(), f"{filename_stem}.xlsx"

    else:
        raise ValueError(f"Unknown format: {fmt}")

async def download_file(bot: Bot, file_id: str) -> bytes:
    tg_file = await bot.get_file(file_id)
    buf = io.BytesIO()
    await tg_file.download_to_memory(buf)
    return buf.getvalue()

async def send_file_bytes(
    message_or_update,
    data: bytes,
    filename: str,
    caption: str = "",
    reply_markup=None,
) -> None:
    """Send bytes as a document. Works with Message or Update."""
    if isinstance(message_or_update, Update):
        send = message_or_update.effective_message.reply_document
    else:
        send = message_or_update.reply_document
    await send(
        document=InputFile(io.BytesIO(data), filename=filename),
        caption=caption,
        reply_markup=reply_markup,
    )

# ──────────────────────────────────────────────────────────────────────────────
# /start COMMAND
# ──────────────────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    text = (
        f"👋 *Welcome, {escape_md(user.first_name)}\\!*\n\n"
        f"🆔 *Your ID:* `{user.id}`\n"
        f"🤖 *Bot:* {escape_md(BOT_NAME)}\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "📦 *Available Features:*\n\n"
        "📊 File Analysis — analyze VCF/TXT/CSV files\n"
        "🔄 File Converter — convert between formats\n"
        "⚡ Quick VCF — create a VCF quickly\n"
        "🛠 VCF Maker — advanced bulk VCF generator\n"
        "✂️ Split File — split into smaller files\n"
        "🔗 Merge Files — merge multiple files into one\n"
        "✏️ File Editor — view & edit contacts\n"
        "📋 List Maker — extract data from screenshots\n"
        "📝 Rename File — rename any file\n"
        "👤 Rename Contact — rename contacts in VCF\n"
        "⚙️ Settings — manage default values\n"
        "🔄 Reset — reset all settings\n"
        "❓ Help — detailed usage guide\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "_Tap a button below to get started\\!_"
    )
    await update.message.reply_text(
        text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=main_menu_keyboard()
    )

# ──────────────────────────────────────────────────────────────────────────────
# MAIN MENU CALLBACK  (routes to each feature)
# ──────────────────────────────────────────────────────────────────────────────

async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data

    # Each feature returns its own entry state; we route here.
    routing = {
        "menu_analysis":       (start_analysis,       FA_WAIT_FILE),
        "menu_converter":      (start_converter,      FC_WAIT_FILE),
        "menu_quick_vcf":      (start_quick_vcf,      QV_WAIT_NAME),
        "menu_vcf_maker":      (start_vcf_maker,      VM_WAIT_VCF_NAME),
        "menu_split":          (start_split,          SP_WAIT_FILE),
        "menu_merge":          (start_merge,          MG_WAIT_FILES),
        "menu_editor":         (start_editor,         ED_WAIT_FILE),
        "menu_list_maker":     (start_list_maker,     LM_WAIT_NAME),
        "menu_rename_file":    (start_rename_file,    RF_WAIT_FILE),
        "menu_rename_contact": (start_rename_contact, RC_WAIT_FILE),
        "menu_settings":       (start_settings,       ST_SHOW),
        "menu_reset":          (start_reset,          RS_WAIT_CONFIRM),
        "menu_help":           (show_help,            ConversationHandler.END),
    }

    if data in routing:
        handler_fn, _state = routing[data]
        return await handler_fn(update, context)
    return ConversationHandler.END

# ──────────────────────────────────────────────────────────────────────────────
# CANCEL / BACK  (universal handlers)
# ──────────────────────────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query:
        await query.answer()
        await query.edit_message_text(
            "❌ *Operation cancelled\\.*\n\nUse /start to return to the main menu\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    else:
        await update.message.reply_text(
            "❌ Operation cancelled. Use /start to return to the main menu."
        )
    context.user_data.clear()
    return ConversationHandler.END

async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query:
        await query.answer()
    await cmd_start(update, context)
    context.user_data.clear()
    return ConversationHandler.END

# ──────────────────────────────────────────────────────────────────────────────
# FEATURE 2: FILE ANALYSIS
# ──────────────────────────────────────────────────────────────────────────────

async def start_analysis(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = (
        "📊 *File Analysis*\n\n"
        "Please upload a *VCF, TXT,* or *CSV* file and I will analyze it for you\\."
    )
    kb = cancel_keyboard()
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
    else:
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
    return FA_WAIT_FILE

async def handle_analysis_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    doc: Document = update.message.document
    if not doc:
        await update.message.reply_text("⚠️ Please upload a valid file.", reply_markup=cancel_keyboard())
        return FA_WAIT_FILE

    await update.message.reply_text("⏳ Analyzing your file, please wait…")

    content = await download_file(context.bot, doc.file_id)
    filename = doc.file_name or "file.vcf"

    try:
        contacts = read_contacts_from_file(content, filename)
    except Exception as e:
        await update.message.reply_text(f"❌ Error reading file: {e}")
        return ConversationHandler.END

    # Analysis
    total       = len(contacts)
    numbers     = [c["number"] for c in contacts]
    seen        = set()
    duplicates  = []
    junk        = []
    clean       = []

    for n in numbers:
        norm = re.sub(r"[^\d+]", "", n)
        if not is_valid_number(norm):
            junk.append(n)
        elif norm in seen:
            duplicates.append(n)
        else:
            seen.add(norm)
            clean.append(n)

    # Country breakdown
    country_map: Dict[str, int] = {}
    for n in clean:
        c = detect_country(n)
        country_map[c] = country_map.get(c, 0) + 1

    country_lines = "\n".join(
        f"  • {escape_md(country)}: {count}"
        for country, count in sorted(country_map.items(), key=lambda x: -x[1])
    ) or "  _None detected_"

    dup_sample = escape_md(", ".join(duplicates[:10]))
    if len(duplicates) > 10:
        dup_sample += f" \\(\\+{len(duplicates) - 10} more\\)"

    msg = (
        f"📊 *Analysis Result: {escape_md(filename)}*\n\n"
        f"📱 *Total Numbers:* {total}\n"
        f"✅ *Clean Numbers:* {len(clean)}\n"
        f"♻️ *Duplicates:* {len(duplicates)}\n"
        f"🚫 *Junk/Invalid:* {len(junk)}\n\n"
        f"🌍 *Country Breakdown:*\n{country_lines}\n"
    )
    if duplicates:
        msg += f"\n♻️ *Duplicate Numbers \\(sample\\):*\n`{dup_sample}`\n"

    await update.message.reply_text(
        msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=main_menu_keyboard()
    )
    return ConversationHandler.END

# ──────────────────────────────────────────────────────────────────────────────
# FEATURE 3: FILE CONVERTER
# ──────────────────────────────────────────────────────────────────────────────

async def start_converter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = "🔄 *File Converter*\n\nPlease upload the file you want to convert\\."
    kb  = cancel_keyboard()
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
    else:
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
    return FC_WAIT_FILE

async def converter_got_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    doc = update.message.document
    if not doc:
        await update.message.reply_text("⚠️ Please upload a file.", reply_markup=cancel_keyboard())
        return FC_WAIT_FILE

    context.user_data["fc_file_id"]   = doc.file_id
    context.user_data["fc_filename"]  = doc.file_name or "file"

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📄 TXT",   callback_data="fc_txt"),
            InlineKeyboardButton("📇 VCF",   callback_data="fc_vcf"),
        ],
        [
            InlineKeyboardButton("📊 Excel", callback_data="fc_excel"),
            InlineKeyboardButton("📋 CSV",   callback_data="fc_csv"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
    ])
    await update.message.reply_text(
        "🔄 *Choose output format:*", parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb
    )
    return FC_WAIT_FORMAT

async def converter_got_format(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    fmt_map = {"fc_txt": "txt", "fc_vcf": "vcf", "fc_excel": "excel", "fc_csv": "csv"}
    fmt = fmt_map.get(query.data)
    if not fmt:
        return FC_WAIT_FORMAT

    await query.edit_message_text("⏳ Converting, please wait…")

    file_id  = context.user_data["fc_file_id"]
    filename = context.user_data["fc_filename"]

    content  = await download_file(context.bot, file_id)
    contacts = read_contacts_from_file(content, filename)
    stem     = Path(filename).stem
    out_data, out_name = contacts_to_format(contacts, fmt, stem)

    await send_file_bytes(
        query.message,
        out_data,
        out_name,
        caption=f"✅ Converted {len(contacts)} contacts → {fmt.upper()}",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END

# ──────────────────────────────────────────────────────────────────────────────
# FEATURE 4: QUICK VCF
# ──────────────────────────────────────────────────────────────────────────────

async def start_quick_vcf(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["qv_contacts"] = []
    msg = "⚡ *Quick VCF*\n\nWhat would you like to name this VCF file? \\(e\\.g\\. `MyContacts`\\)"
    kb  = cancel_keyboard()
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
    else:
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
    return QV_WAIT_NAME

async def qv_got_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["qv_vcf_name"] = update.message.text.strip()
    await update.message.reply_text(
        "👤 Enter the *contact name*:", parse_mode=ParseMode.MARKDOWN_V2, reply_markup=cancel_keyboard()
    )
    return QV_WAIT_CONTACT_NAME

async def qv_got_contact_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["qv_cur_name"] = update.message.text.strip()
    await update.message.reply_text(
        "📱 Enter the *contact number* \\(with country code, e\\.g\\. `+919876543210`\\):",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=cancel_keyboard(),
    )
    return QV_WAIT_CONTACT_NUMBER

async def qv_got_contact_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    number = update.message.text.strip()
    name   = context.user_data.get("qv_cur_name", "Contact")
    context.user_data.setdefault("qv_contacts", []).append({"name": name, "number": number})

    count = len(context.user_data["qv_contacts"])
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes, add more", callback_data="qv_more"),
            InlineKeyboardButton("📤 Finish & Send", callback_data="qv_finish"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
    ])
    await update.message.reply_text(
        f"✅ Added *{escape_md(name)}* \\— {escape_md(number)}\n"
        f"Total contacts: *{count}*\n\nAdd another contact?",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=kb,
    )
    return QV_WAIT_MORE

async def qv_more_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "qv_more":
        await query.edit_message_text(
            "👤 Enter the next *contact name*:", parse_mode=ParseMode.MARKDOWN_V2, reply_markup=cancel_keyboard()
        )
        return QV_WAIT_CONTACT_NAME
    else:  # qv_finish
        return await qv_finish(update, context)

async def qv_finish(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    contacts = context.user_data.get("qv_contacts", [])
    vcf_name = context.user_data.get("qv_vcf_name", "Contacts")
    if not contacts:
        msg = update.callback_query.message if update.callback_query else update.message
        await msg.reply_text("⚠️ No contacts to save.")
        return ConversationHandler.END

    data     = make_vcf_bytes(contacts)
    filename = f"{vcf_name}.vcf"

    target = update.callback_query.message if update.callback_query else update.message
    await send_file_bytes(
        target, data, filename,
        caption=f"✅ *{escape_md(vcf_name)}.vcf* created with {len(contacts)} contact(s)\\!",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END

# ──────────────────────────────────────────────────────────────────────────────
# FEATURE 5: VCF MAKER (ADVANCED)
# ──────────────────────────────────────────────────────────────────────────────

async def start_vcf_maker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["vm"] = {}
    msg = (
        "🛠 *VCF Maker \\(Advanced\\)*\n\n"
        "Step 1/7 — What is the *VCF file name*? \\(e\\.g\\. `Bulk`\\)"
    )
    kb = cancel_keyboard()
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
    else:
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
    return VM_WAIT_VCF_NAME

async def vm_got_vcf_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["vm"]["vcf_name"] = update.message.text.strip()
    await update.message.reply_text(
        "Step 2/7 — Enter the *base contact name* \\(e\\.g\\. `Customer`\\):",
        parse_mode=ParseMode.MARKDOWN_V2, reply_markup=cancel_keyboard()
    )
    return VM_WAIT_BASE_NAME

async def vm_got_base_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["vm"]["base_name"] = update.message.text.strip()
    await update.message.reply_text(
        "Step 3/7 — How many *contacts per VCF file*? \\(e\\.g\\. `500`\\)",
        parse_mode=ParseMode.MARKDOWN_V2, reply_markup=cancel_keyboard()
    )
    return VM_WAIT_PER_FILE

async def vm_got_per_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        per_file = int(update.message.text.strip())
        assert per_file > 0
    except Exception:
        await update.message.reply_text("⚠️ Please enter a valid positive number.")
        return VM_WAIT_PER_FILE
    context.user_data["vm"]["per_file"] = per_file
    await update.message.reply_text(
        "Step 4/7 — *Contact numbering start* \\(e\\.g\\. `1` or `501`\\):",
        parse_mode=ParseMode.MARKDOWN_V2, reply_markup=cancel_keyboard()
    )
    return VM_WAIT_CONTACT_START

async def vm_got_contact_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        start = int(update.message.text.strip())
    except Exception:
        await update.message.reply_text("⚠️ Please enter a valid number.")
        return VM_WAIT_CONTACT_START
    context.user_data["vm"]["contact_start"] = start
    await update.message.reply_text(
        "Step 5/7 — *File numbering start* \\(e\\.g\\. `1` or `5`\\):",
        parse_mode=ParseMode.MARKDOWN_V2, reply_markup=cancel_keyboard()
    )
    return VM_WAIT_FILE_START

async def vm_got_file_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        start = int(update.message.text.strip())
    except Exception:
        await update.message.reply_text("⚠️ Please enter a valid number.")
        return VM_WAIT_FILE_START
    context.user_data["vm"]["file_start"] = start
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🌐 Auto-detect", callback_data="vm_cc_auto"),
            InlineKeyboardButton("✏️ Enter manually", callback_data="vm_cc_manual"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
    ])
    await update.message.reply_text(
        "Step 6/7 — *Country code* — auto-detect or enter manually?",
        parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb
    )
    return VM_WAIT_CC

async def vm_cc_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "vm_cc_auto":
        context.user_data["vm"]["country_code"] = "auto"
        await query.edit_message_text(
            "Step 7/7 — Enter the *group name* \\(appended after contacts\\):",
            parse_mode=ParseMode.MARKDOWN_V2, reply_markup=cancel_keyboard()
        )
    else:
        context.user_data["vm"]["country_code"] = None
        await query.edit_message_text(
            "Please enter the *country code* \\(e\\.g\\. `+91`\\):",
            parse_mode=ParseMode.MARKDOWN_V2, reply_markup=cancel_keyboard()
        )
    return VM_WAIT_GROUP_NAME if query.data == "vm_cc_auto" else VM_WAIT_CC

async def vm_got_cc_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    cc = update.message.text.strip()
    if not cc.startswith("+"):
        cc = "+" + cc
    context.user_data["vm"]["country_code"] = cc
    await update.message.reply_text(
        "Step 7/7 — Enter the *group name*:",
        parse_mode=ParseMode.MARKDOWN_V2, reply_markup=cancel_keyboard()
    )
    return VM_WAIT_GROUP_NAME

async def vm_got_group_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["vm"]["group_name"] = update.message.text.strip()
    vm = context.user_data["vm"]
    summary = (
        "📋 *Confirm VCF Maker Settings:*\n\n"
        f"📁 VCF File Name: `{escape_md(vm['vcf_name'])}`\n"
        f"👤 Base Contact Name: `{escape_md(vm['base_name'])}`\n"
        f"📦 Contacts per File: `{vm['per_file']}`\n"
        f"🔢 Contact Numbering Starts: `{vm['contact_start']}`\n"
        f"📄 File Numbering Starts: `{vm['file_start']}`\n"
        f"🌍 Country Code: `{escape_md(str(vm['country_code']))}`\n"
        f"🏷 Group Name: `{escape_md(vm['group_name'])}`\n\n"
        "Is everything correct?"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm", callback_data="vm_confirm"),
            InlineKeyboardButton("✏️ Edit", callback_data="vm_edit"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
    ])
    await update.message.reply_text(summary, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
    return VM_WAIT_CONFIRM

async def vm_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "vm_edit":
        # Restart from step 1
        return await start_vcf_maker(update, context)
    # Confirmed — ask for numbers file
    context.user_data["vm"]["number_files"]    = []
    context.user_data["vm"]["global_contact"]  = context.user_data["vm"]["contact_start"]
    context.user_data["vm"]["global_file"]     = context.user_data["vm"]["file_start"]
    await query.edit_message_text(
        "✅ *Confirmed\\!*\n\n"
        "📤 Now upload your *numbers file* \\(TXT with one number per line\\)\\.\n"
        "You can upload *multiple files* one by one\\. "
        "Click *Done* when finished uploading\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Done uploading", callback_data="vm_done_files"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
        ]]),
    )
    return VM_WAIT_NUMBERS_FILE

async def vm_got_numbers_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    doc = update.message.document
    if not doc:
        await update.message.reply_text("⚠️ Please upload a TXT file.")
        return VM_WAIT_NUMBERS_FILE

    content = await download_file(context.bot, doc.file_id)
    numbers = parse_numbers_from_text(content.decode("utf-8", errors="replace"))
    context.user_data["vm"].setdefault("all_numbers", []).extend(numbers)

    total = len(context.user_data["vm"]["all_numbers"])
    await update.message.reply_text(
        f"✅ Loaded *{len(numbers)}* numbers from this file\\. Total so far: *{total}*\\.\n"
        "Upload another file or tap *Done*\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Done uploading", callback_data="vm_done_files"),
            InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
        ]]),
    )
    return VM_WAIT_NUMBERS_FILE

async def vm_done_files_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    numbers = context.user_data["vm"].get("all_numbers", [])
    if not numbers:
        await query.edit_message_text("⚠️ No numbers loaded. Please upload at least one file.")
        return VM_WAIT_NUMBERS_FILE

    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📦 Single merged file", callback_data="vm_single"),
            InlineKeyboardButton("📂 Separate files",     callback_data="vm_separate"),
        ],
        [InlineKeyboardButton("❌ Cancel", callback_data="cancel")],
    ])
    await query.edit_message_text(
        f"📊 Total numbers loaded: *{len(numbers)}*\n\n"
        "How do you want the output?",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=kb,
    )
    return VM_WAIT_MERGE_CHOICE

async def vm_merge_choice_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    merge_all = query.data == "vm_single"
    await query.edit_message_text("⏳ Generating VCF files, please wait…")
    await _vm_generate(query.message, context, merge_all)
    return ConversationHandler.END

async def _vm_generate(message, context: ContextTypes.DEFAULT_TYPE, merge_all: bool) -> None:
    vm              = context.user_data["vm"]
    numbers         = vm["all_numbers"]
    base_name       = vm["base_name"]
    group_name      = vm["group_name"]
    vcf_name        = vm["vcf_name"]
    per_file        = vm["per_file"]
    country_code    = vm["country_code"]
    contact_counter = vm["contact_start"]
    file_counter    = vm["file_start"]

    def normalise(num: str) -> str:
        if country_code and country_code != "auto":
            digits = re.sub(r"[^\d]", "", num)
            if not num.startswith("+"):
                num = country_code + digits
        return num

    chunks = [numbers[i:i+per_file] for i in range(0, len(numbers), per_file)]
    files: List[Tuple[str, bytes]] = []

    for chunk in chunks:
        contacts = []
        for raw_num in chunk:
            num  = normalise(raw_num)
            name = f"{base_name} {group_name} {contact_counter}"
            contacts.append({"name": name, "number": num})
            contact_counter += 1
        fname = f"{vcf_name} {file_counter}.vcf"
        files.append((fname, make_vcf_bytes(contacts)))
        file_counter += 1

    if merge_all:
        merged = b"".join(data for _, data in files)
        fname  = f"{vcf_name}_merged.vcf"
        await send_file_bytes(
            message, merged, fname,
            caption=f"✅ Merged VCF with {len(numbers)} contacts\\.",
            reply_markup=main_menu_keyboard(),
        )
    elif len(files) == 1:
        await send_file_bytes(
            message, files[0][1], files[0][0],
            caption=f"✅ Generated 1 VCF file with {len(numbers)} contacts\\.",
            reply_markup=main_menu_keyboard(),
        )
    else:
        # Zip them up
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for fname, data in files:
                zf.writestr(fname, data)
        zip_buf.seek(0)
        await message.reply_document(
            document=InputFile(zip_buf, filename=f"{vcf_name}_files.zip"),
            caption=f"✅ Generated *{len(files)}* VCF files \\({len(numbers)} contacts total\\)\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=main_menu_keyboard(),
        )

# ──────────────────────────────────────────────────────────────────────────────
# FEATURE 6: SPLIT FILE
# ──────────────────────────────────────────────────────────────────────────────

async def start_split(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = "✂️ *Split File*\n\nPlease upload the file you want to split \\(VCF, TXT, or CSV\\)\\."
    kb  = cancel_keyboard()
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
    else:
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
    return SP_WAIT_FILE

async def split_got_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    doc = update.message.document
    if not doc:
        await update.message.reply_text("⚠️ Please upload a file.", reply_markup=cancel_keyboard())
        return SP_WAIT_FILE
    context.user_data["sp_file_id"]   = doc.file_id
    context.user_data["sp_filename"]  = doc.file_name or "file.vcf"
    await update.message.reply_text(
        "✂️ How many *contacts per split file*?",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=cancel_keyboard(),
    )
    return SP_WAIT_COUNT

async def split_got_count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        count = int(update.message.text.strip())
        assert count > 0
    except Exception:
        await update.message.reply_text("⚠️ Please enter a valid positive number.")
        return SP_WAIT_COUNT

    await update.message.reply_text("⏳ Splitting file…")

    content  = await download_file(context.bot, context.user_data["sp_file_id"])
    filename = context.user_data["sp_filename"]
    ext      = Path(filename).suffix.lower()
    stem     = Path(filename).stem

    contacts = read_contacts_from_file(content, filename)
    chunks   = [contacts[i:i+count] for i in range(0, len(contacts), count)]

    if len(chunks) == 1:
        await update.message.reply_text("ℹ️ The file already has fewer contacts than the split size. No split needed.")
        return ConversationHandler.END

    fmt_map = {".vcf": "vcf", ".txt": "txt", ".csv": "csv", ".xlsx": "excel", ".xls": "excel"}
    fmt     = fmt_map.get(ext, "txt")

    if len(chunks) <= 5:
        for i, chunk in enumerate(chunks, 1):
            data, fname = contacts_to_format(chunk, fmt, f"{stem}_part{i}")
            await send_file_bytes(update.message, data, fname, caption=f"Part {i}/{len(chunks)}")
        await update.message.reply_text("✅ All parts sent\\!", parse_mode=ParseMode.MARKDOWN_V2, reply_markup=main_menu_keyboard())
    else:
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for i, chunk in enumerate(chunks, 1):
                data, fname = contacts_to_format(chunk, fmt, f"{stem}_part{i}")
                zf.writestr(fname, data)
        zip_buf.seek(0)
        await update.message.reply_document(
            document=InputFile(zip_buf, filename=f"{stem}_split.zip"),
            caption=f"✅ {len(chunks)} parts zipped and sent\\!",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=main_menu_keyboard(),
        )
    return ConversationHandler.END

# ──────────────────────────────────────────────────────────────────────────────
# FEATURE 7: MERGE FILES
# ──────────────────────────────────────────────────────────────────────────────

async def start_merge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["mg_contacts"]  = []
    context.user_data["mg_ext"]       = None
    context.user_data["mg_count"]     = 0
    msg = (
        "🔗 *Merge Files*\n\n"
        "Upload files one by one \\(VCF, TXT, or CSV\\)\\. "
        "Tap *Done* when you have uploaded all files\\."
    )
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Done", callback_data="mg_done"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
    ]])
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
    else:
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
    return MG_WAIT_FILES

async def merge_got_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    doc = update.message.document
    if not doc:
        return MG_WAIT_FILES

    content  = await download_file(context.bot, doc.file_id)
    filename = doc.file_name or "file.vcf"
    ext      = Path(filename).suffix.lower()

    if context.user_data["mg_ext"] is None:
        context.user_data["mg_ext"] = ext

    contacts = read_contacts_from_file(content, filename)
    context.user_data["mg_contacts"].extend(contacts)
    context.user_data["mg_count"] += 1

    total = len(context.user_data["mg_contacts"])
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Done", callback_data="mg_done"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
    ]])
    await update.message.reply_text(
        f"✅ File added\\. Total contacts so far: *{total}*\\.\nUpload another or tap *Done*\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=kb,
    )
    return MG_WAIT_FILES

async def merge_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    contacts = context.user_data.get("mg_contacts", [])
    if not contacts:
        await query.edit_message_text("⚠️ No files were uploaded. Operation cancelled.")
        return ConversationHandler.END

    ext = context.user_data.get("mg_ext", ".vcf")
    fmt_map = {".vcf": "vcf", ".txt": "txt", ".csv": "csv", ".xlsx": "excel"}
    fmt     = fmt_map.get(ext, "vcf")

    data, fname = contacts_to_format(contacts, fmt, "merged")
    await send_file_bytes(
        query.message, data, fname,
        caption=f"✅ Merged *{context.user_data['mg_count']}* files into one \\({len(contacts)} contacts\\)\\.",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END

# ──────────────────────────────────────────────────────────────────────────────
# FEATURE 8: FILE EDITOR
# ──────────────────────────────────────────────────────────────────────────────

PAGE_SIZE = 10  # contacts per page

def editor_page_keyboard(page: int, total_pages: int, contacts: List[Dict], offset: int) -> InlineKeyboardMarkup:
    rows = []
    for i, c in enumerate(contacts):
        label = f"{offset + i + 1}. {c['name'] or c['number']}"
        rows.append([InlineKeyboardButton(label, callback_data=f"ed_sel_{offset + i}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"ed_page_{page - 1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("▶️ Next", callback_data=f"ed_page_{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([
        InlineKeyboardButton("➕ Add Contact", callback_data="ed_add"),
        InlineKeyboardButton("❌ Cancel",      callback_data="cancel"),
    ])
    rows.append([InlineKeyboardButton("📤 Save & Send", callback_data="ed_save")])
    return InlineKeyboardMarkup(rows)

async def start_editor(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = "✏️ *File Editor*\n\nPlease upload the file you want to edit \\(VCF, TXT, or CSV\\)\\."
    kb  = cancel_keyboard()
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
    else:
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
    return ED_WAIT_FILE

async def editor_got_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    doc = update.message.document
    if not doc:
        await update.message.reply_text("⚠️ Please upload a file.", reply_markup=cancel_keyboard())
        return ED_WAIT_FILE

    content  = await download_file(context.bot, doc.file_id)
    filename = doc.file_name or "file.vcf"
    contacts = read_contacts_from_file(content, filename)

    context.user_data["ed_contacts"] = contacts
    context.user_data["ed_filename"] = filename
    context.user_data["ed_page"]     = 0

    return await editor_show_list(update, context)

async def editor_show_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    contacts    = context.user_data["ed_contacts"]
    page        = context.user_data.get("ed_page", 0)
    total_pages = max(1, (len(contacts) + PAGE_SIZE - 1) // PAGE_SIZE)
    offset      = page * PAGE_SIZE
    page_contacts = contacts[offset: offset + PAGE_SIZE]

    text = f"✏️ *Contacts* \\(Page {page + 1}/{total_pages}\\) — tap to edit or remove:"
    kb   = editor_page_keyboard(page, total_pages, page_contacts, offset)

    msg = update.callback_query.message if update.callback_query else update.message
    try:
        await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
    except Exception:
        await msg.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
    return ED_SHOW_LIST

async def editor_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data  = query.data

    if data.startswith("ed_page_"):
        context.user_data["ed_page"] = int(data.split("_")[-1])
        return await editor_show_list(update, context)

    elif data.startswith("ed_sel_"):
        idx = int(data.split("_")[-1])
        context.user_data["ed_cur_idx"] = idx
        c   = context.user_data["ed_contacts"][idx]
        kb  = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("✏️ Edit Name",   callback_data="ed_edit_name"),
                InlineKeyboardButton("📱 Edit Number", callback_data="ed_edit_number"),
            ],
            [InlineKeyboardButton("🗑 Remove",         callback_data="ed_remove")],
            [InlineKeyboardButton("🔙 Back",           callback_data="ed_back_list")],
        ])
        await query.edit_message_text(
            f"👤 *{escape_md(c['name'] or '(no name)')}*\n📱 `{escape_md(c['number'])}`\n\nWhat do you want to do?",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=kb,
        )
        return ED_WAIT_EDIT_CHOICE

    elif data == "ed_remove":
        idx = context.user_data["ed_cur_idx"]
        context.user_data["ed_contacts"].pop(idx)
        await query.edit_message_text("🗑 Contact removed\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return await editor_show_list(update, context)

    elif data == "ed_edit_name":
        context.user_data["ed_edit_field"] = "name"
        await query.edit_message_text("✏️ Enter the *new name*:", parse_mode=ParseMode.MARKDOWN_V2, reply_markup=cancel_keyboard())
        return ED_WAIT_NEW_VALUE

    elif data == "ed_edit_number":
        context.user_data["ed_edit_field"] = "number"
        await query.edit_message_text("📱 Enter the *new number*:", parse_mode=ParseMode.MARKDOWN_V2, reply_markup=cancel_keyboard())
        return ED_WAIT_NEW_VALUE

    elif data == "ed_back_list":
        return await editor_show_list(update, context)

    elif data == "ed_add":
        await query.edit_message_text("➕ Enter the *new contact name*:", parse_mode=ParseMode.MARKDOWN_V2, reply_markup=cancel_keyboard())
        return ED_WAIT_ADD_NAME

    elif data == "ed_save":
        return await editor_save(update, context)

    return ED_SHOW_LIST

async def editor_got_new_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = update.message.text.strip()
    idx   = context.user_data["ed_cur_idx"]
    field = context.user_data["ed_edit_field"]
    context.user_data["ed_contacts"][idx][field] = value
    await update.message.reply_text(f"✅ Updated\\!", parse_mode=ParseMode.MARKDOWN_V2)
    return await editor_show_list(update, context)

async def editor_got_add_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["ed_new_name"] = update.message.text.strip()
    await update.message.reply_text("📱 Enter the *contact number*:", parse_mode=ParseMode.MARKDOWN_V2, reply_markup=cancel_keyboard())
    return ED_WAIT_ADD_NUMBER

async def editor_got_add_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    number = update.message.text.strip()
    name   = context.user_data.get("ed_new_name", "")
    context.user_data["ed_contacts"].append({"name": name, "number": number})
    await update.message.reply_text("✅ Contact added\\!", parse_mode=ParseMode.MARKDOWN_V2)
    return await editor_show_list(update, context)

async def editor_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    contacts = context.user_data["ed_contacts"]
    filename = context.user_data["ed_filename"]
    ext      = Path(filename).suffix.lower()
    fmt_map  = {".vcf": "vcf", ".txt": "txt", ".csv": "csv", ".xlsx": "excel"}
    fmt      = fmt_map.get(ext, "vcf")
    stem     = Path(filename).stem + "_edited"
    data, fname = contacts_to_format(contacts, fmt, stem)

    target = update.callback_query.message if update.callback_query else update.message
    await send_file_bytes(
        target, data, fname,
        caption=f"✅ Edited file with *{len(contacts)}* contacts\\.",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END

# ──────────────────────────────────────────────────────────────────────────────
# FEATURE 9: LIST MAKER
# ──────────────────────────────────────────────────────────────────────────────

async def start_list_maker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["lm_items"]    = []
    context.user_data["lm_photos"]   = []
    msg = "📋 *List Maker*\n\nEnter the *list name*:"
    kb  = cancel_keyboard()
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
    else:
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
    return LM_WAIT_NAME

async def lm_got_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data["lm_name"] = update.message.text.strip()
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Done uploading", callback_data="lm_done"),
        InlineKeyboardButton("❌ Cancel",         callback_data="cancel"),
    ]])
    await update.message.reply_text(
        "📸 Upload *WhatsApp pending request screenshots* one by one\\.\n"
        "Tap *Done* when finished\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=kb,
    )
    return LM_WAIT_SCREENSHOTS

async def lm_got_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    photo = update.message.photo or []
    doc   = update.message.document

    file_id = None
    if photo:
        file_id = photo[-1].file_id  # largest size
    elif doc and doc.mime_type and doc.mime_type.startswith("image"):
        file_id = doc.file_id

    if not file_id:
        return LM_WAIT_SCREENSHOTS

    content = await download_file(context.bot, file_id)
    try:
        img  = Image.open(io.BytesIO(content))
        text = pytesseract.image_to_string(img)
        context.user_data.setdefault("lm_texts", []).append(text)
    except Exception as e:
        logger.warning(f"OCR error: {e}")
        await update.message.reply_text("⚠️ Could not read this image. Please try again.")
        return LM_WAIT_SCREENSHOTS

    count = len(context.user_data.get("lm_texts", []))
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Done uploading", callback_data="lm_done"),
        InlineKeyboardButton("❌ Cancel",         callback_data="cancel"),
    ]])
    await update.message.reply_text(
        f"✅ Screenshot {count} processed\\. Upload more or tap *Done*\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=kb,
    )
    return LM_WAIT_SCREENSHOTS

async def lm_done_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    texts = context.user_data.get("lm_texts", [])
    if not texts:
        await query.edit_message_text("⚠️ No screenshots uploaded. Operation cancelled.")
        return ConversationHandler.END

    # Parse groups and pending counts from OCR text
    items: List[Tuple[str, str]] = []
    for text in texts:
        lines = text.splitlines()
        for line in lines:
            # Match: group name followed by a number (pending count)
            m = re.search(r"(.+?)\s+(\d+)\s*(pending|request)?", line, re.IGNORECASE)
            if m:
                group_name = m.group(1).strip()
                pending    = m.group(2).strip()
                if group_name and pending:
                    items.append((group_name, pending))

    list_name = context.user_data.get("lm_name", "List")

    if not items:
        # Fallback: extract lines with numbers
        for text in texts:
            for line in text.splitlines():
                line = line.strip()
                if line and re.search(r"\d", line):
                    items.append((line, "?"))

    # Build table
    header  = f"📋 *{escape_md(list_name)}*\n\n"
    divider = "━━━━━━━━━━━━━━━━━━━━\n"
    table   = f"{'Sr':<4} {'Group Name':<30} {'Pending':>7}\n" + "─" * 44 + "\n"
    for i, (grp, cnt) in enumerate(items, 1):
        table += f"{i:<4} {grp[:30]:<30} {cnt:>7}\n"

    full_text = header + divider + f"```\n{table}```"

    # Send as message
    await query.edit_message_text(full_text, parse_mode=ParseMode.MARKDOWN_V2)

    # Also send as TXT file
    txt_content = f"{list_name}\n" + "=" * 44 + "\n"
    txt_content += f"{'Sr':<4} {'Group Name':<30} {'Pending':>7}\n" + "-" * 44 + "\n"
    for i, (grp, cnt) in enumerate(items, 1):
        txt_content += f"{i:<4} {grp[:30]:<30} {cnt:>7}\n"

    await send_file_bytes(
        query.message,
        txt_content.encode("utf-8"),
        f"{list_name}.txt",
        caption=f"📋 {list_name} — {len(items)} entries",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END

# ──────────────────────────────────────────────────────────────────────────────
# FEATURE 10: RENAME FILE
# ──────────────────────────────────────────────────────────────────────────────

async def start_rename_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = "📝 *Rename File*\n\nPlease upload the file you want to rename\\."
    kb  = cancel_keyboard()
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
    else:
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
    return RF_WAIT_FILE

async def rename_file_got_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    doc = update.message.document
    if not doc:
        await update.message.reply_text("⚠️ Please upload a file.", reply_markup=cancel_keyboard())
        return RF_WAIT_FILE
    context.user_data["rf_file_id"]  = doc.file_id
    context.user_data["rf_ext"]      = Path(doc.file_name or "file").suffix
    await update.message.reply_text(
        "📝 Enter the *new file name* \\(without extension\\):",
        parse_mode=ParseMode.MARKDOWN_V2, reply_markup=cancel_keyboard()
    )
    return RF_WAIT_NAME

async def rename_file_got_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    new_name = update.message.text.strip()
    ext      = context.user_data.get("rf_ext", "")
    file_id  = context.user_data["rf_file_id"]

    content  = await download_file(context.bot, file_id)
    filename = f"{new_name}{ext}"

    await send_file_bytes(
        update.message, content, filename,
        caption=f"✅ File renamed to *{escape_md(filename)}*\\.",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END

# ──────────────────────────────────────────────────────────────────────────────
# FEATURE 11: RENAME CONTACT
# ──────────────────────────────────────────────────────────────────────────────

async def start_rename_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = "👤 *Rename Contact*\n\nPlease upload the *VCF* file whose contacts you want to rename\\."
    kb  = cancel_keyboard()
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
    else:
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
    return RC_WAIT_FILE

async def rename_contact_got_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    doc = update.message.document
    if not doc:
        await update.message.reply_text("⚠️ Please upload a VCF file.", reply_markup=cancel_keyboard())
        return RC_WAIT_FILE

    content  = await download_file(context.bot, doc.file_id)
    filename = doc.file_name or "contacts.vcf"
    contacts = read_contacts_from_file(content, filename)

    context.user_data["rc_contacts"] = contacts
    context.user_data["rc_filename"] = filename
    await update.message.reply_text(
        f"✅ Loaded *{len(contacts)}* contacts\\.\n\n"
        "👤 Enter the *new base contact name* \\(e\\.g\\. `Customer`\\)\\.\n"
        "The original numbering will be preserved\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=cancel_keyboard(),
    )
    return RC_WAIT_BASE_NAME

async def rename_contact_got_base(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    base_name = update.message.text.strip()
    contacts  = context.user_data["rc_contacts"]
    filename  = context.user_data["rc_filename"]

    # Try to preserve trailing numbers from original names
    renamed = []
    for c in contacts:
        old_name = c["name"]
        # Extract trailing digits from old name
        m        = re.search(r"(\d+)\s*$", old_name)
        suffix   = m.group(1) if m else ""
        new_name = f"{base_name} {suffix}".strip() if suffix else base_name
        renamed.append({"name": new_name, "number": c["number"]})

    stem = Path(filename).stem + "_renamed"
    data, fname = contacts_to_format(renamed, "vcf", stem)

    await send_file_bytes(
        update.message, data, fname,
        caption=f"✅ Renamed *{len(renamed)}* contacts to `{escape_md(base_name)} [N]`\\.",
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END

# ──────────────────────────────────────────────────────────────────────────────
# FEATURE 12: SETTINGS
# ──────────────────────────────────────────────────────────────────────────────

SETTINGS_LABELS = {
    "default_contact_name":  "Default Contact Name",
    "default_country_code":  "Default Country Code",
    "default_per_file":      "Default Contacts per File",
    "default_file_start":    "Default File Numbering Start",
    "default_contact_start": "Default Contact Numbering Start",
    "default_group_name":    "Default Group Name",
}

async def start_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id  = update.effective_user.id
    settings = get_all_settings(user_id)

    lines = "\n".join(
        f"  *{escape_md(SETTINGS_LABELS.get(k, k))}:* `{escape_md(v)}`"
        for k, v in settings.items()
    )
    kb = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(f"✏️ Edit {SETTINGS_LABELS.get(k, k)}", callback_data=f"st_{k}")]
            for k in settings.keys()
        ]
        + [[InlineKeyboardButton("❌ Close", callback_data="cancel")]]
    )
    msg = f"⚙️ *Current Settings:*\n\n{lines}"
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
    else:
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
    return ST_SHOW

async def settings_select_key(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    key = query.data[3:]  # strip "st_"
    context.user_data["st_key"] = key
    label = SETTINGS_LABELS.get(key, key)
    await query.edit_message_text(
        f"✏️ Enter new value for *{escape_md(label)}*:",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=back_cancel_keyboard(),
    )
    return ST_WAIT_VALUE

async def settings_got_value(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    key     = context.user_data.get("st_key")
    value   = update.message.text.strip()
    user_id = update.effective_user.id
    set_setting(user_id, key, value)
    await update.message.reply_text(
        f"✅ Setting *{escape_md(SETTINGS_LABELS.get(key, key))}* updated to `{escape_md(value)}`\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END

# ──────────────────────────────────────────────────────────────────────────────
# FEATURE 13: RESET
# ──────────────────────────────────────────────────────────────────────────────

async def start_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes, Reset", callback_data="rs_confirm"),
            InlineKeyboardButton("❌ No, Cancel", callback_data="cancel"),
        ]
    ])
    msg = "⚠️ *Reset Settings?*\n\nThis will restore all settings to their default values\\. Are you sure?"
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
    else:
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=kb)
    return RS_WAIT_CONFIRM

async def reset_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    reset_settings(update.effective_user.id)
    await query.edit_message_text(
        "✅ *All settings have been reset to defaults\\.*",
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=main_menu_keyboard(),
    )
    return ConversationHandler.END

# ──────────────────────────────────────────────────────────────────────────────
# FEATURE 14: HELP
# ──────────────────────────────────────────────────────────────────────────────

HELP_TEXT = """
❓ *VCF Manager Bot — Help Guide*

━━━━━━━━━━━━━━━━━━━━

📊 *File Analysis*
Upload any VCF, TXT, or CSV file\\.
The bot will show: total contacts, clean/duplicate/junk counts, and a country\\-wise breakdown\\.

🔄 *File Converter*
Upload a file and choose the output format \\(TXT / VCF / Excel / CSV\\)\\.
The bot converts and sends the file back\\.

⚡ *Quick VCF*
Enter a file name → Add contacts one by one \\(name \\+ number\\) → Tap Finish\\.
The bot generates and sends a ready VCF file\\.

🛠 *VCF Maker \\(Advanced\\)*
Fill in: file name, base contact name, contacts per file, numbering starts, country code, group name\\.
Review the summary → Confirm → Upload numbers file\\(s\\)\\.
Choose single merged file or separate files\\.
Numbering continues across multiple uploaded files automatically\\.

✂️ *Split File*
Upload a file → Enter how many contacts per part\\.
Bot splits and sends the parts \\(same format as original\\)\\.

🔗 *Merge Files*
Upload files one by one → Tap Done\\.
Bot merges all into one file and sends it\\.

✏️ *File Editor*
Upload a file → Browse contacts \\(paginated\\)\\.
Tap a contact to edit name / number, or remove it\\.
Use *Add Contact* to add a new one\\.
Tap *Save & Send* when done\\.

📋 *List Maker*
Enter a list name → Upload WhatsApp pending request screenshots\\.
Bot uses OCR to extract group names and pending counts\\.
Sends a formatted serial\\-wise list as text and file\\.

📝 *Rename File*
Upload a file → Enter the new name → Bot sends the renamed file\\.

👤 *Rename Contact*
Upload a VCF → Enter a new base name\\.
Bot renames all contacts keeping original numbering\\.

⚙️ *Settings*
View and edit default values used by VCF Maker and other features\\.

🔄 *Reset*
Confirm to restore all settings to factory defaults\\.

━━━━━━━━━━━━━━━━━━━━
_Use /start to return to the main menu at any time\\._
"""

async def show_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    if update.callback_query:
        await update.callback_query.edit_message_text(
            HELP_TEXT, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=main_menu_keyboard()
        )
    else:
        await update.message.reply_text(
            HELP_TEXT, parse_mode=ParseMode.MARKDOWN_V2, reply_markup=main_menu_keyboard()
        )
    return ConversationHandler.END

# ──────────────────────────────────────────────────────────────────────────────
# CONVERSATION HANDLER BUILDER
# ──────────────────────────────────────────────────────────────────────────────

def build_conv_handler() -> ConversationHandler:
    """
    Single master ConversationHandler routing ALL features.
    Entry points: /start, menu callback buttons.
    """
    cancel_handler = [
        CallbackQueryHandler(cancel,        pattern="^cancel$"),
        CallbackQueryHandler(back_to_menu,  pattern="^back$"),
        CommandHandler("cancel", cancel),
        CommandHandler("start",  cmd_start),
    ]

    return ConversationHandler(
        entry_points=[
            CommandHandler("start", cmd_start),
            CallbackQueryHandler(main_menu_callback, pattern="^menu_"),
        ],
        states={
            # ── File Analysis ──────────────────────────────────────────────
            FA_WAIT_FILE: [
                MessageHandler(filters.Document.ALL, handle_analysis_file),
                *cancel_handler,
            ],

            # ── File Converter ─────────────────────────────────────────────
            FC_WAIT_FILE: [
                MessageHandler(filters.Document.ALL, converter_got_file),
                *cancel_handler,
            ],
            FC_WAIT_FORMAT: [
                CallbackQueryHandler(converter_got_format, pattern="^fc_"),
                *cancel_handler,
            ],

            # ── Quick VCF ──────────────────────────────────────────────────
            QV_WAIT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, qv_got_name),
                *cancel_handler,
            ],
            QV_WAIT_CONTACT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, qv_got_contact_name),
                *cancel_handler,
            ],
            QV_WAIT_CONTACT_NUMBER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, qv_got_contact_number),
                *cancel_handler,
            ],
            QV_WAIT_MORE: [
                CallbackQueryHandler(qv_more_callback, pattern="^qv_"),
                *cancel_handler,
            ],

            # ── VCF Maker ──────────────────────────────────────────────────
            VM_WAIT_VCF_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, vm_got_vcf_name),
                *cancel_handler,
            ],
            VM_WAIT_BASE_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, vm_got_base_name),
                *cancel_handler,
            ],
            VM_WAIT_PER_FILE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, vm_got_per_file),
                *cancel_handler,
            ],
            VM_WAIT_CONTACT_START: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, vm_got_contact_start),
                *cancel_handler,
            ],
            VM_WAIT_FILE_START: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, vm_got_file_start),
                *cancel_handler,
            ],
            VM_WAIT_CC: [
                CallbackQueryHandler(vm_cc_callback, pattern="^vm_cc_(auto|manual)$"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, vm_got_cc_text),
                *cancel_handler,
            ],
            VM_WAIT_GROUP_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, vm_got_group_name),
                *cancel_handler,
            ],
            VM_WAIT_CONFIRM: [
                CallbackQueryHandler(vm_confirm_callback, pattern="^vm_(confirm|edit)$"),
                *cancel_handler,
            ],
            VM_WAIT_NUMBERS_FILE: [
                MessageHandler(filters.Document.ALL, vm_got_numbers_file),
                CallbackQueryHandler(vm_done_files_callback, pattern="^vm_done_files$"),
                *cancel_handler,
            ],
            VM_WAIT_MERGE_CHOICE: [
                CallbackQueryHandler(vm_merge_choice_callback, pattern="^vm_(single|separate)$"),
                *cancel_handler,
            ],

            # ── Split File ─────────────────────────────────────────────────
            SP_WAIT_FILE: [
                MessageHandler(filters.Document.ALL, split_got_file),
                *cancel_handler,
            ],
            SP_WAIT_COUNT: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, split_got_count),
                *cancel_handler,
            ],

            # ── Merge Files ────────────────────────────────────────────────
            MG_WAIT_FILES: [
                MessageHandler(filters.Document.ALL, merge_got_file),
                CallbackQueryHandler(merge_done_callback, pattern="^mg_done$"),
                *cancel_handler,
            ],

            # ── File Editor ────────────────────────────────────────────────
            ED_WAIT_FILE: [
                MessageHandler(filters.Document.ALL, editor_got_file),
                *cancel_handler,
            ],
            ED_SHOW_LIST: [
                CallbackQueryHandler(editor_callback, pattern="^ed_"),
                *cancel_handler,
            ],
            ED_WAIT_EDIT_CHOICE: [
                CallbackQueryHandler(editor_callback, pattern="^ed_"),
                *cancel_handler,
            ],
            ED_WAIT_NEW_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, editor_got_new_value),
                *cancel_handler,
            ],
            ED_WAIT_ADD_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, editor_got_add_name),
                *cancel_handler,
            ],
            ED_WAIT_ADD_NUMBER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, editor_got_add_number),
                *cancel_handler,
            ],

            # ── List Maker ─────────────────────────────────────────────────
            LM_WAIT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, lm_got_name),
                *cancel_handler,
            ],
            LM_WAIT_SCREENSHOTS: [
                MessageHandler(filters.PHOTO | filters.Document.IMAGE, lm_got_screenshot),
                CallbackQueryHandler(lm_done_callback, pattern="^lm_done$"),
                *cancel_handler,
            ],

            # ── Rename File ────────────────────────────────────────────────
            RF_WAIT_FILE: [
                MessageHandler(filters.Document.ALL, rename_file_got_file),
                *cancel_handler,
            ],
            RF_WAIT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, rename_file_got_name),
                *cancel_handler,
            ],

            # ── Rename Contact ─────────────────────────────────────────────
            RC_WAIT_FILE: [
                MessageHandler(filters.Document.ALL, rename_contact_got_file),
                *cancel_handler,
            ],
            RC_WAIT_BASE_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, rename_contact_got_base),
                *cancel_handler,
            ],

            # ── Settings ───────────────────────────────────────────────────
            ST_SHOW: [
                CallbackQueryHandler(settings_select_key, pattern="^st_"),
                *cancel_handler,
            ],
            ST_WAIT_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, settings_got_value),
                *cancel_handler,
            ],

            # ── Reset ──────────────────────────────────────────────────────
            RS_WAIT_CONFIRM: [
                CallbackQueryHandler(reset_confirm_callback, pattern="^rs_confirm$"),
                *cancel_handler,
            ],
        },
        fallbacks=[
            CommandHandler("start",  cmd_start),
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(cancel, pattern="^cancel$"),
        ],
        allow_reentry=True,
        per_message=False,
    )

# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────

def main() -> None:
    db_init()

    application = Application.builder().token(BOT_TOKEN).build()

    # Master conversation handler
    application.add_handler(build_conv_handler())

    # Inline menu callbacks that fire OUTSIDE an active conversation
    # (e.g. user clicks Help from the main menu message)
    application.add_handler(CallbackQueryHandler(show_help,         pattern="^menu_help$"))
    application.add_handler(CallbackQueryHandler(main_menu_callback, pattern="^menu_"))

    logger.info("🤖 %s is running. Press Ctrl+C to stop.", BOT_NAME)
    application.run_polling(allowed_updates=["message", "callback_query"])

if __name__ == "__main__":
    main()
