import os
import re
import asyncio
import pandas as pd
import phonenumbers
from phonenumbers import geocoder
from telegram import Update, ReplyKeyboardMarkup, BotCommand
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# Bot token
BOT_TOKEN = "7727685861:AAE3CMfUysoE02PKtqKbWBrg0MXTnBfnnMU"

# ================= SETTINGS =================

DEFAULT_SETTINGS = {
    "file_name": "Contacts",
    "contact_name": "Contact",
    "limit": 100,
    "contact_start": 1,
    "vcf_start": 1,
    "country_code": "",
    "group_number": None,
}

users_data = {}


def get_ud(uid):
    if uid not in users_data:
        users_data[uid] = {
            "mode": None,
            "step": None,
            "files": [],
            "merge_choice": None,
            "format": None,
            "action": None,
            "edit_nums": [],
            "custom_name": "Output",
            "split_limit": 100,
            "quick_data": [],
            "contact": None,
            "base_name": None,
            "settings": DEFAULT_SETTINGS.copy(),
        }
    return users_data[uid]


def safe_remove(path):
    if os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass


def clear_ud(uid):
    if uid in users_data:
        for f in users_data[uid].get("files", []):
            safe_remove(f)
        users_data[uid].update(
            {
                "mode": None,
                "step": None,
                "files": [],
                "merge_choice": None,
                "format": None,
                "action": None,
                "edit_nums": [],
                "custom_name": "Output",
                "split_limit": 100,
                "quick_data": [],
                "contact": None,
                "base_name": None,
            }
        )


# ================= HELPERS ==================

def get_file_format(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".txt":
        return "txt"
    if ext == ".csv":
        return "csv"
    if ext in [".xlsx", ".xls"]:
        return "xlsx"
    return "vcf"


def extract_all_numbers(path):
    ext = os.path.splitext(path)[1].lower()
    nums = []
    try:
        if ext == ".vcf":
            with open(path, "r", errors="ignore") as f:
                for line in f:
                    if line.startswith("TEL"):
                        n = re.sub(r"[^\d+]", "", line)
                        if len(n) >= 7:
                            nums.append(n)
        elif ext in [".xlsx", ".xls"]:
            df = pd.read_excel(path, dtype=str)
            text_data = " ".join(df.values.flatten().astype(str))
            nums = re.findall(r"\+?\d{7,}", text_data)
        elif ext == ".csv":
            df = pd.read_csv(path, dtype=str)
            text_data = " ".join(df.values.flatten().astype(str))
            nums = re.findall(r"\+?\d{7,}", text_data)
        else:
            with open(path, "r", errors="ignore") as f:
                nums = re.findall(r"\+?\d{7,}", f.read())
    except Exception as e:
        print(f"Error extracting: {e}")
        return []
    return list(dict.fromkeys(nums))


def detect_primary_country(numbers):
    countries = {}
    for n in numbers[:50]:
        try:
            parse_num = "+" + n if not n.startswith("+") else n
            pn = phonenumbers.parse(parse_num, None)
            region = geocoder.description_for_number(pn, "en")
            if region:
                countries[region] = countries.get(region, 0) + 1
        except Exception:
            continue
    if countries:
        return max(countries, key=countries.get)
    return "Unknown"


def generate_analysis_report(file_name, numbers):
    total = len(numbers)
    unique_set = set(numbers)
    unique_count = len(unique_set)
    duplicates = total - unique_count

    country_stats = {}
    invalid_count = 0
    for n in unique_set:
        try:
            parse_num = "+" + n if not n.startswith("+") else n
            pn = phonenumbers.parse(parse_num, None)
            if phonenumbers.is_valid_number(pn):
                region = geocoder.description_for_number(pn, "en") or "Unknown"
                country_stats[region] = country_stats.get(region, 0) + 1
            else:
                invalid_count += 1
        except Exception:
            invalid_count += 1

    country_text = "\n".join([f"  └ {c}: {count}" for c, count in country_stats.items()])
    if not country_text:
        country_text = "  └ None detected"

    return (
        f"📊 **FILE ANALYSIS REPORT**\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📁 **File:** `{file_name}`\n\n"
        f"📌 **Statistics:**\n"
        f"  ├ 🔢 Total Numbers: `{total}`\n"
        f"  ├ ✅ Unique: `{unique_count}`\n"
        f"  └ ♻️ Duplicates: `{duplicates}`\n\n"
        f"🌍 **Country Breakdown:**\n"
        f"{country_text}\n\n"
        f"⚠️ **Issues:**\n"
        f"  └ ❌ Invalid/Junk: `{invalid_count}`\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )


def chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def make_vcf(numbers, cfg, index=0, custom_limit=None, custom_fname=None):
    limit = custom_limit if custom_limit else cfg["limit"]
    start = cfg["contact_start"] + index * limit
    out = ""
    for i, n in enumerate(numbers, start=start):
        name = f"{cfg['contact_name']}{str(i).zfill(3)}"
        if cfg.get("group_number"):
            name += f" ({cfg['group_number']})"
        clean_n = n.replace("+", "")
        prefix = cfg["country_code"] if cfg["country_code"] else "+"
        final_num = f"{prefix}{clean_n}"
        out += f"BEGIN:VCARD\nVERSION:3.0\nFN:{name}\nTEL;TYPE=CELL:{final_num}\nEND:VCARD\n"

    fname = custom_fname if custom_fname else f"{cfg['file_name']}{cfg['vcf_start'] + index}.vcf"
    with open(fname, "w", encoding="utf-8") as f:
        f.write(out)
    return fname


def save_format(numbers, target_fmt, out_file, cfg):
    if target_fmt == "vcf":
        return make_vcf(numbers, cfg, custom_limit=len(numbers), custom_fname=out_file)
    if target_fmt == "txt":
        with open(out_file, "w") as f:
            f.write("\n".join(["+" + n.replace("+", "") for n in numbers]))
    elif target_fmt == "csv":
        pd.DataFrame(["+" + n.replace("+", "") for n in numbers], columns=["Mobile Number"]).to_csv(out_file, index=False)
    elif target_fmt == "xlsx":
        pd.DataFrame(["+" + n.replace("+", "") for n in numbers], columns=["Mobile Number"]).to_excel(out_file, index=False)
    return out_file


# ================= UI & MENUS (REPLY KEYBOARD) =================

BTN_ANALYSIS = "📂 File Analysis"
BTN_CONVERTER = "🔄 File Converter"
BTN_QUICK = "⚡ Quick VCF"
BTN_GENERATOR = "📇 VCF Generator"
BTN_SPLIT = "✂️ Split VCF"
BTN_MERGE = "🧩 Merge Files"
BTN_EDITOR = "🛠 File Editor"
BTN_NAME_MAKER = "📝 Name Maker"
BTN_RENAME_FILE = "✏️ Rename File"
BTN_RENAME_CONTACT = "👤 Rename Contact"
BTN_SETTINGS = "⚙️ Settings"
BTN_RESET = "🗑 Reset"

BTN_CANCEL = "❌ Cancel"
BTN_MENU = "🏠 Main Menu"
BTN_DONE_UPLOADING = "✅ Done Uploading"
BTN_MERGE_ALL = "🧩 Merge All"
BTN_KEEP_SINGLE = "📄 Keep Single"

BTN_TO_TXT = "📝 To TXT"
BTN_TO_VCF = "📇 To VCF"
BTN_TO_CSV = "📊 To CSV"
BTN_TO_XLSX = "📑 To XLSX"

BTN_EDIT_ADD = "➕ Add"
BTN_EDIT_REMOVE = "➖ Remove"

BTN_SKIP_CC = "⏩ Auto Detect Country"
BTN_SKIP_GROUP = "⏭ Skip Group"

BTN_GEN_START = "✅ Start Process"
BTN_GEN_EDIT = "✏️ Edit Settings"

BTN_ADD_MORE = "➕ Add More"
BTN_FINISH_QUICK = "🏁 Finish"

MAIN_ACTIONS = {
    BTN_ANALYSIS: "analysis",
    BTN_CONVERTER: "converter",
    BTN_QUICK: "quick",
    BTN_GENERATOR: "gen",
    BTN_SPLIT: "split_vcf",
    BTN_MERGE: "merge",
    BTN_EDITOR: "vcf_editor",
    BTN_NAME_MAKER: "name_gen",
    BTN_RENAME_FILE: "rename_files",
    BTN_RENAME_CONTACT: "rename_contacts",
    BTN_SETTINGS: "mysettings",
    BTN_RESET: "reset",
}

FORMAT_CHOICES = {
    BTN_TO_TXT: "txt",
    BTN_TO_VCF: "vcf",
    BTN_TO_CSV: "csv",
    BTN_TO_XLSX: "xlsx",
}

EDITOR_CHOICES = {
    BTN_EDIT_ADD: "add",
    BTN_EDIT_REMOVE: "remove",
}


def kb(rows):
    return ReplyKeyboardMarkup(rows, resize_keyboard=True, one_time_keyboard=False)


def main_menu():
    return kb(
        [
            [BTN_ANALYSIS, BTN_CONVERTER],
            [BTN_QUICK, BTN_GENERATOR],
            [BTN_SPLIT, BTN_MERGE],
            [BTN_EDITOR, BTN_NAME_MAKER],
            [BTN_RENAME_FILE, BTN_RENAME_CONTACT],
            [BTN_SETTINGS, BTN_RESET],
        ]
    )


def nav_kb():
    return kb([[BTN_CANCEL, BTN_MENU]])


def upload_kb():
    return kb([[BTN_DONE_UPLOADING], [BTN_CANCEL, BTN_MENU]])


def merge_single_kb():
    return kb([[BTN_MERGE_ALL], [BTN_KEEP_SINGLE], [BTN_CANCEL, BTN_MENU]])


def convert_kb():
    return kb([[BTN_TO_TXT, BTN_TO_VCF], [BTN_TO_CSV, BTN_TO_XLSX], [BTN_CANCEL, BTN_MENU]])


def editor_action_kb():
    return kb([[BTN_EDIT_ADD, BTN_EDIT_REMOVE], [BTN_CANCEL, BTN_MENU]])


def gen_country_kb():
    return kb([[BTN_SKIP_CC], [BTN_CANCEL, BTN_MENU]])


def gen_group_kb():
    return kb([[BTN_SKIP_GROUP], [BTN_CANCEL, BTN_MENU]])


def gen_summary_kb():
    return kb([[BTN_GEN_START, BTN_GEN_EDIT], [BTN_CANCEL, BTN_MENU]])


def quick_choice_kb():
    return kb([[BTN_ADD_MORE, BTN_FINISH_QUICK], [BTN_CANCEL, BTN_MENU]])


async def send_main_menu(message, heading=None):
    text = heading if heading else "🤖 **MAIN MENU**\nChoose any tool to continue:"
    await message.reply_text(text, reply_markup=main_menu(), parse_mode=ParseMode.MARKDOWN)


def summary_text(cfg):
    c_disp = cfg["country_code"] if cfg["country_code"] else "Auto Detect"
    g_disp = cfg["group_number"] if cfg["group_number"] else "None"
    return (
        "⚙️ **CURRENT SETTINGS (VCF GENERATOR)**\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📂 **File Name:** `{cfg['file_name']}`\n"
        f"👤 **Contact Name:** `{cfg['contact_name']}`\n"
        f"📏 **Limit Per File:** `{cfg['limit']}`\n"
        f"🔢 **Start Index:** `{cfg['contact_start']}`\n"
        f"📄 **VCF Start Index:** `{cfg['vcf_start']}`\n"
        f"🌍 **Country Code:** `{c_disp}`\n"
        f"🏷 **Group Tag:** `{g_disp}`\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "Choose **Start Process** or **Edit Settings**."
    )


async def show_summary(message, cfg):
    await message.reply_text(summary_text(cfg), parse_mode=ParseMode.MARKDOWN, reply_markup=gen_summary_kb())


# ================= HANDLERS =================

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    clear_ud(uid)
    user = update.effective_user
    text = (
        f"👋 **Welcome {user.first_name}!**\n\n"
        "🤖 **Ultimate VCF Manager** is ready.\n"
        "All controls are now permanent menu buttons for faster use.\n\n"
        "Use the menu below or commands: /menu, /help, /cancel"
    )
    await update.message.reply_text(text, reply_markup=main_menu(), parse_mode=ParseMode.MARKDOWN)


async def menu_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    clear_ud(uid)
    await send_main_menu(update.message, "🏠 **Main menu opened.**")


async def cancel_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    clear_ud(uid)
    await send_main_menu(update.message, "❌ **Current flow cancelled.**")


async def help_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "🆘 **HELP**\n"
        "• Use permanent menu buttons below for all tools\n"
        "• Upload files when a tool asks for it\n"
        "• Use **Done Uploading** after bulk upload\n"
        "• Use /cancel anytime to stop current flow\n"
        "• Use /menu to return to dashboard"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu())


async def open_mode(update: Update, uid: int, mode: str):
    ud = get_ud(uid)

    if mode == "mysettings":
        await show_summary(update.message, ud["settings"])
        return

    if mode == "reset":
        ud["settings"] = DEFAULT_SETTINGS.copy()
        clear_ud(uid)
        await send_main_menu(update.message, "♻️ **Settings reset to default.**")
        return

    clear_ud(uid)
    ud = get_ud(uid)

    if mode in ["analysis", "split_vcf"]:
        ud["mode"] = mode
        ud["step"] = "upload"
        prompts = {
            "analysis": "🧐 **FILE ANALYSIS**\n\nUpload one file (TXT, VCF, CSV, XLSX) to get a detailed report.",
            "split_vcf": "✂️ **SPLIT FILE**\n\nUpload one file (VCF/TXT/CSV/XLSX) you want to split.",
        }
        await update.message.reply_text(prompts[mode], parse_mode=ParseMode.MARKDOWN, reply_markup=nav_kb())
        return

    if mode in ["converter", "vcf_editor", "merge", "rename_files", "rename_contacts"]:
        ud["mode"] = mode
        ud["step"] = "upload"
        msg = (
            "📤 **BULK UPLOAD MODE**\n\n"
            "Upload one or more files now.\n"
            "When done, tap **Done Uploading**."
        )
        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN, reply_markup=upload_kb())
        return

    if mode == "gen":
        ud["mode"] = "gen"
        ud["step"] = "file_name"
        await update.message.reply_text(
            "📇 **VCF GENERATOR**\n\nEnter output **File Name**:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=nav_kb(),
        )
        return

    if mode == "quick":
        ud["mode"] = "quick"
        ud["step"] = "file"
        ud["quick_data"] = []
        await update.message.reply_text(
            "⚡ **QUICK VCF MODE**\n\nEnter a file name for your VCF:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=nav_kb(),
        )


async def handle_done_uploading(update: Update, uid: int, ud):
    if not ud["files"]:
        await update.message.reply_text("❌ Please upload at least one file first.", reply_markup=upload_kb())
        return

    file_count = len(ud["files"])

    if file_count == 1:
        ud["merge_choice"] = "single"
        if ud["mode"] == "converter":
            ud["step"] = "ask_format"
            await update.message.reply_text("🔄 **Choose target format:**", parse_mode=ParseMode.MARKDOWN, reply_markup=convert_kb())
        elif ud["mode"] == "vcf_editor":
            ud["step"] = "ask_action"
            await update.message.reply_text("🛠 **Choose action:**", parse_mode=ParseMode.MARKDOWN, reply_markup=editor_action_kb())
        elif ud["mode"] == "rename_files":
            ud["step"] = "ask_name"
            await update.message.reply_text("✏️ Enter **new file name**:", parse_mode=ParseMode.MARKDOWN, reply_markup=nav_kb())
        elif ud["mode"] == "rename_contacts":
            ud["step"] = "ask_name"
            await update.message.reply_text(
                "👤 Enter **new contact base name**\n(original file name will remain same):",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=nav_kb(),
            )
        elif ud["mode"] == "merge":
            ud["merge_choice"] = "merge"
            ud["step"] = "ask_name"
            await update.message.reply_text("✏️ Enter **output base name**:", parse_mode=ParseMode.MARKDOWN, reply_markup=nav_kb())
        return

    if ud["mode"] == "merge":
        ud["merge_choice"] = "merge"
        ud["step"] = "ask_name"
        await update.message.reply_text("✏️ Enter **output base name** for merged file:", parse_mode=ParseMode.MARKDOWN, reply_markup=nav_kb())
    else:
        ud["step"] = "ask_merge"
        await update.message.reply_text(
            "❓ **Merge or Single?**\n\nChoose one processing mode:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=merge_single_kb(),
        )


async def finish_quick_flow(update: Update, uid: int, ud):
    f_name = ud.get("custom_name", "QuickVCF")
    proc_msg = await update.message.reply_text("⏳ **Generating VCF...**", parse_mode=ParseMode.MARKDOWN)
    out, total_nums = "", 0

    for entry in ud["quick_data"]:
        c_name = entry["contact"]
        for i, n in enumerate(entry["nums"], start=1):
            clean_n = "+" + n.replace("+", "")
            out += f"BEGIN:VCARD\nVERSION:3.0\nFN:{c_name}{str(i).zfill(3)}\nTEL;TYPE=CELL:{clean_n}\nEND:VCARD\n"
            total_nums += 1

    path = f"{f_name}.vcf"
    with open(path, "w", encoding="utf-8") as x:
        x.write(out)

    await proc_msg.delete()
    await update.message.reply_document(open(path, "rb"), caption=f"✅ **Done!**\nTotal Contacts: {total_nums}", parse_mode=ParseMode.MARKDOWN)
    safe_remove(path)
    clear_ud(uid)
    await send_main_menu(update.message, "🏠 **Quick VCF completed.**")


async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ud = get_ud(uid)
    cfg = ud["settings"]
    txt = update.message.text.strip()

    # Global navigation buttons
    if txt == BTN_MENU:
        clear_ud(uid)
        await send_main_menu(update.message, "🏠 **Back to main menu.**")
        return

    if txt == BTN_CANCEL:
        clear_ud(uid)
        await send_main_menu(update.message, "❌ **Current task cancelled.**")
        return

    # Main dashboard actions
    if txt in MAIN_ACTIONS:
        await open_mode(update, uid, MAIN_ACTIONS[txt])
        return

    # Upload mode actions
    if ud["step"] == "upload" and txt == BTN_DONE_UPLOADING:
        await handle_done_uploading(update, uid, ud)
        return

    # Merge decision
    if ud["step"] == "ask_merge" and txt in [BTN_MERGE_ALL, BTN_KEEP_SINGLE]:
        ud["merge_choice"] = "merge" if txt == BTN_MERGE_ALL else "single"
        if ud["mode"] == "converter":
            ud["step"] = "ask_format"
            await update.message.reply_text("🔄 **Choose target format:**", parse_mode=ParseMode.MARKDOWN, reply_markup=convert_kb())
        elif ud["mode"] == "vcf_editor":
            ud["step"] = "ask_action"
            await update.message.reply_text("🛠 **Choose action:**", parse_mode=ParseMode.MARKDOWN, reply_markup=editor_action_kb())
        elif ud["mode"] == "rename_files":
            ud["step"] = "ask_name"
            await update.message.reply_text("✏️ Enter **new file name**:", parse_mode=ParseMode.MARKDOWN, reply_markup=nav_kb())
        elif ud["mode"] == "rename_contacts":
            ud["step"] = "ask_name"
            await update.message.reply_text(
                "👤 Enter **new contact base name**\n(original file name will remain same):",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=nav_kb(),
            )
        return

    # Format choice
    if ud["step"] == "ask_format" and txt in FORMAT_CHOICES:
        ud["format"] = FORMAT_CHOICES[txt]
        ud["step"] = "ask_name"
        await update.message.reply_text("⌨️ Enter **custom output file name**:", parse_mode=ParseMode.MARKDOWN, reply_markup=nav_kb())
        return

    # Editor action choice
    if ud["step"] == "ask_action" and txt in EDITOR_CHOICES:
        ud["action"] = EDITOR_CHOICES[txt]
        ud["step"] = "ask_numbers"
        message = "✍️ **Send numbers to ADD:**" if ud["action"] == "add" else "🗑️ **Send number(s) to REMOVE:**"
        await update.message.reply_text(message, parse_mode=ParseMode.MARKDOWN, reply_markup=nav_kb())
        return

    # Generator summary actions
    if ud["mode"] == "gen" and txt == BTN_GEN_EDIT:
        ud["step"] = "file_name"
        await update.message.reply_text("✏️ Enter **File Name**:", parse_mode=ParseMode.MARKDOWN, reply_markup=nav_kb())
        return

    if ud["mode"] == "gen" and txt == BTN_GEN_START:
        ud["step"] = "waiting_input"
        await update.message.reply_text(
            "🔒 **Settings locked.**\n\nNow upload your source file (TXT, VCF, CSV, XLSX).",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=nav_kb(),
        )
        return

    if ud["mode"] == "gen" and ud["step"] == "country_code" and txt == BTN_SKIP_CC:
        cfg["country_code"] = ""
        ud["step"] = "group_number"
        await update.message.reply_text("📑 Enter **group name** or choose skip:", parse_mode=ParseMode.MARKDOWN, reply_markup=gen_group_kb())
        return

    if ud["mode"] == "gen" and ud["step"] == "group_number" and txt == BTN_SKIP_GROUP:
        cfg["group_number"] = None
        await show_summary(update.message, cfg)
        return

    # Quick flow actions
    if ud["mode"] == "quick" and ud["step"] == "quick_choice":
        if txt == BTN_ADD_MORE:
            ud["step"] = "contact"
            await update.message.reply_text("👤 Enter **next contact name**:", parse_mode=ParseMode.MARKDOWN, reply_markup=nav_kb())
            return
        if txt == BTN_FINISH_QUICK:
            await finish_quick_flow(update, uid, ud)
            return

    # Step-by-step data inputs
    if ud["mode"] == "gen":
        if ud["step"] == "file_name":
            cfg["file_name"] = txt
            ud["step"] = "contact_name"
            await update.message.reply_text("👤 Enter **contact base name**:", parse_mode=ParseMode.MARKDOWN, reply_markup=nav_kb())
            return
        if ud["step"] == "contact_name":
            cfg["contact_name"] = txt
            ud["step"] = "limit"
            await update.message.reply_text("📊 Enter **limit per file** (example: 100):", parse_mode=ParseMode.MARKDOWN, reply_markup=nav_kb())
            return
        if ud["step"] == "limit":
            cfg["limit"] = int(txt) if txt.isdigit() and int(txt) > 0 else 100
            ud["step"] = "contact_start"
            await update.message.reply_text("🔢 Enter **contact start index** (example: 1):", parse_mode=ParseMode.MARKDOWN, reply_markup=nav_kb())
            return
        if ud["step"] == "contact_start":
            cfg["contact_start"] = int(txt) if txt.isdigit() and int(txt) > 0 else 1
            ud["step"] = "vcf_start"
            await update.message.reply_text("📄 Enter **VCF file start index**:", parse_mode=ParseMode.MARKDOWN, reply_markup=nav_kb())
            return
        if ud["step"] == "vcf_start":
            cfg["vcf_start"] = int(txt) if txt.isdigit() and int(txt) > 0 else 1
            ud["step"] = "country_code"
            await update.message.reply_text(
                "🌍 Enter **country code** (example: +91) or choose auto detect:",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=gen_country_kb(),
            )
            return
        if ud["step"] == "country_code":
            cfg["country_code"] = txt if txt.startswith("+") else f"+{txt}"
            ud["step"] = "group_number"
            await update.message.reply_text("📑 Enter **group name** or choose skip:", parse_mode=ParseMode.MARKDOWN, reply_markup=gen_group_kb())
            return
        if ud["step"] == "group_number":
            cfg["group_number"] = txt
            await show_summary(update.message, cfg)
            return

    if ud["step"] == "ask_split_limit":
        if txt.isdigit() and int(txt) > 0:
            ud["split_limit"] = int(txt)
            ud["step"] = "ask_name"
            await update.message.reply_text("⌨️ Enter **custom file name** for split output:", parse_mode=ParseMode.MARKDOWN, reply_markup=nav_kb())
        else:
            await update.message.reply_text("❌ Please enter a valid number (example: 100).", reply_markup=nav_kb())
        return

    if ud["step"] == "ask_numbers":
        ud["edit_nums"] = list(dict.fromkeys(re.findall(r"\d{7,}", txt)))
        ud["step"] = "ask_name"
        await update.message.reply_text("⌨️ Enter **custom output file name**:", parse_mode=ParseMode.MARKDOWN, reply_markup=nav_kb())
        return

    if ud["step"] == "ask_name":
        cleaned = re.sub(r'[\\/*?:"<>|]', "", txt).strip()
        ud["custom_name"] = cleaned if cleaned else "Output"
        await process_engine(update, ctx, uid, ud)
        return

    if ud["mode"] == "quick" and ud["step"] == "file":
        cleaned = re.sub(r'[\\/*?:"<>|]', "", txt).strip()
        ud["custom_name"] = cleaned if cleaned else "QuickVCF"
        ud["step"] = "contact"
        await update.message.reply_text("👤 Enter **contact name**:", parse_mode=ParseMode.MARKDOWN, reply_markup=nav_kb())
        return

    if ud["mode"] == "quick" and ud["step"] == "contact":
        ud["contact"] = txt
        ud["step"] = "numbers"
        await update.message.reply_text(
            f"📤 Paste numbers for **{txt}**:\n(You can paste multiple lines)",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=nav_kb(),
        )
        return

    if ud["mode"] == "quick" and ud["step"] == "numbers":
        raw_nums = list(dict.fromkeys(re.findall(r"\d{7,}", txt)))
        if not raw_nums:
            await update.message.reply_text("❌ No valid numbers found. Please send again.", reply_markup=nav_kb())
            return
        ud["quick_data"].append({"contact": ud["contact"], "nums": raw_nums})
        ud["step"] = "quick_choice"
        await update.message.reply_text(
            f"✅ Added **{len(raw_nums)}** numbers for **{ud['contact']}**.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=quick_choice_kb(),
        )
        return

    if ud["mode"] == "name_gen":
        if ud["step"] == "name":
            ud["base_name"] = txt
            ud["step"] = "count"
            await update.message.reply_text("🔢 How many names should I generate?", parse_mode=ParseMode.MARKDOWN, reply_markup=nav_kb())
            return
        if ud["step"] == "count":
            if not txt.isdigit() or int(txt) <= 0:
                await update.message.reply_text("❌ Enter a valid positive number.", reply_markup=nav_kb())
                return
            proc_msg = await update.message.reply_text("⏳ **Generating list...**", parse_mode=ParseMode.MARKDOWN)
            count = int(txt)
            content = "\n".join([f"{ud['base_name']} {i + 1}" for i in range(count)])
            await proc_msg.delete()
            if len(content) > 4000:
                with open("names.txt", "w") as f:
                    f.write(content)
                await update.message.reply_document(open("names.txt", "rb"))
                safe_remove("names.txt")
            else:
                await update.message.reply_text(f"📝 **GENERATED LIST:**\n\n```\n{content}\n```", parse_mode=ParseMode.MARKDOWN)
            clear_ud(uid)
            await send_main_menu(update.message, "✅ **Task completed successfully.**")
            return

    # No active flow
    await update.message.reply_text(
        "ℹ️ Please choose an option from the permanent menu below or use /menu.",
        reply_markup=main_menu(),
    )


async def handle_file(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    ud = get_ud(uid)
    doc = update.message.document

    if ud["step"] not in ["upload", "waiting_input"]:
        await update.message.reply_text("❌ Please choose a tool first from the menu.", reply_markup=main_menu())
        return

    path = f"{uid}_{len(ud['files'])}_{doc.file_name}"
    file_obj = await ctx.bot.get_file(doc.file_id)
    await file_obj.download_to_drive(path)
    ud["files"].append(path)

    if ud["mode"] == "analysis":
        proc_msg = await update.message.reply_text("⏳ **Analyzing file...**", parse_mode=ParseMode.MARKDOWN)
        nums = extract_all_numbers(path)
        report = generate_analysis_report(doc.file_name, nums)
        await proc_msg.delete()
        await update.message.reply_text(report, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu())
        clear_ud(uid)
        return

    if ud["mode"] == "split_vcf":
        ud["step"] = "ask_split_limit"
        nums = extract_all_numbers(path)
        ud["split_nums"] = nums
        await update.message.reply_text(
            f"📊 Found **{len(nums)}** numbers.\nEnter limit per output file (example: 100):",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=nav_kb(),
        )
        return

    if ud["mode"] == "gen" and ud["step"] == "waiting_input":
        proc_msg = await update.message.reply_text("⚙️ **Processing VCF Generator...**", parse_mode=ParseMode.MARKDOWN)
        cfg = ud["settings"]
        nums = extract_all_numbers(path)
        detected_country = "Manual"
        if not cfg["country_code"]:
            detected_country = detect_primary_country(nums)

        generated_files = []
        for i, c in enumerate(chunk(nums, cfg["limit"])):
            f = make_vcf(c, cfg, i)
            await update.message.reply_document(open(f, "rb"))
            generated_files.append(f)
            await asyncio.sleep(0.3)

        await proc_msg.delete()
        summary = (
            "✅ **GENERATION COMPLETE**\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"📂 File Name: `{cfg['file_name']}`\n"
            f"🔢 Total Numbers: `{len(nums)}`\n"
            f"📁 Generated Files: `{len(generated_files)}`\n"
            f"🌍 Detection Mode: `{detected_country}`"
        )
        await update.message.reply_text(summary, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu())
        for f in generated_files:
            safe_remove(f)
        clear_ud(uid)
        return

    await update.message.reply_text(
        f"📥 **Files uploaded:** `{len(ud['files'])}`\n"
        f"📄 **Latest file:** `{doc.file_name}`\n\n"
        "Upload more files or tap **Done Uploading**.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=upload_kb(),
    )


async def process_engine(update, ctx, uid, ud):
    proc_msg = await update.message.reply_text("⏳ **Processing files... please wait!**", parse_mode=ParseMode.MARKDOWN)

    try:
        mode = ud["mode"]
        merge_choice = ud["merge_choice"]
        c_name = ud["custom_name"]
        file_count = len(ud["files"])

        # ======== SPLIT ========
        if mode == "split_vcf":
            nums = ud["split_nums"]
            limit = ud["split_limit"]
            orig_fmt = get_file_format(ud["files"][0]) if ud["files"] else "vcf"

            for i, p in enumerate(chunk(nums, limit), start=1):
                out_name = f"{c_name}{i}.{orig_fmt}"
                f = save_format(p, orig_fmt, out_name, ud["settings"])
                await update.message.reply_document(open(f, "rb"))
                safe_remove(f)
                await asyncio.sleep(0.3)

            await proc_msg.delete()
            await update.message.reply_text("✅ **Split completed.**", parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu())
            clear_ud(uid)
            return

        # ======== MERGE LOGIC ========
        if merge_choice == "merge" or mode == "merge":
            all_nums = []

            if mode == "rename_contacts":
                for f in ud["files"]:
                    all_nums.extend(extract_all_numbers(f))
                ud["settings"]["contact_name"] = c_name
                out_file = make_vcf(
                    list(dict.fromkeys(all_nums)),
                    ud["settings"],
                    custom_limit=len(all_nums),
                    custom_fname=f"{c_name}.vcf",
                )
                await update.message.reply_document(open(out_file, "rb"))
                safe_remove(out_file)

            elif mode == "rename_files":
                for f in ud["files"]:
                    all_nums.extend(extract_all_numbers(f))
                out_file = make_vcf(
                    list(dict.fromkeys(all_nums)),
                    ud["settings"],
                    custom_limit=len(all_nums),
                    custom_fname=f"{c_name}.vcf",
                )
                await update.message.reply_document(open(out_file, "rb"))
                safe_remove(out_file)

            else:
                for f in ud["files"]:
                    all_nums.extend(extract_all_numbers(f))
                all_nums = list(dict.fromkeys(all_nums))

                if mode in ["converter", "merge"]:
                    if mode == "converter":
                        fmt = ud["format"] if ud["format"] else "vcf"
                    else:
                        fmt = get_file_format(ud["files"][0]) if ud["files"] else "vcf"

                    out_file = save_format(all_nums, fmt, f"{c_name}.{fmt}", ud["settings"])
                    await update.message.reply_document(open(out_file, "rb"))
                    safe_remove(out_file)

                elif mode == "vcf_editor":
                    if ud["action"] == "add":
                        all_nums.extend(ud["edit_nums"])
                        all_nums = list(dict.fromkeys(all_nums))
                    elif ud["action"] == "remove":
                        remove_set = set([n.replace("+", "") for n in ud["edit_nums"]])
                        all_nums = [n for n in all_nums if n.replace("+", "") not in remove_set]
                    out_file = make_vcf(all_nums, ud["settings"], custom_limit=len(all_nums), custom_fname=f"{c_name}.vcf")
                    await update.message.reply_document(open(out_file, "rb"))
                    safe_remove(out_file)

        # ======== SINGLE LOGIC ========
        elif merge_choice == "single":
            for i, fpath in enumerate(ud["files"], start=1):
                single_name = c_name if file_count == 1 else f"{c_name}{i}"
                orig_name = fpath.split("_", 2)[-1]
                orig_ext = os.path.splitext(orig_name)[1]

                if mode == "rename_files":
                    new_name = f"{single_name}{orig_ext}"
                    os.rename(fpath, new_name)
                    await update.message.reply_document(open(new_name, "rb"))
                    safe_remove(new_name)

                elif mode == "rename_contacts":
                    out_text, idx = "", 1
                    with open(fpath, "r", errors="ignore") as r:
                        for line in r:
                            if line.startswith("FN:"):
                                out_text += f"FN:{c_name}{str(idx).zfill(3)}\n"
                                idx += 1
                            else:
                                out_text += line
                    with open(orig_name, "w", encoding="utf-8") as w:
                        w.write(out_text)
                    await update.message.reply_document(open(orig_name, "rb"))
                    safe_remove(orig_name)

                elif mode == "converter":
                    nums = extract_all_numbers(fpath)
                    out_file = save_format(nums, ud["format"], f"{single_name}.{ud['format']}", ud["settings"])
                    await update.message.reply_document(open(out_file, "rb"))
                    safe_remove(out_file)

                elif mode == "vcf_editor":
                    nums = extract_all_numbers(fpath)
                    if ud["action"] == "add":
                        nums.extend(ud["edit_nums"])
                    elif ud["action"] == "remove":
                        remove_set = set([n.replace("+", "") for n in ud["edit_nums"]])
                        nums = [n for n in nums if n.replace("+", "") not in remove_set]
                    out_file = make_vcf(
                        list(dict.fromkeys(nums)),
                        ud["settings"],
                        custom_limit=len(nums),
                        custom_fname=f"{single_name}.vcf",
                    )
                    await update.message.reply_document(open(out_file, "rb"))
                    safe_remove(out_file)

                await asyncio.sleep(0.3)

        await proc_msg.delete()
        await update.message.reply_text(
            "✅ **All files processed successfully!**",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu(),
        )
        clear_ud(uid)

    except Exception as e:
        await proc_msg.delete()
        await update.message.reply_text(f"❌ Error occurred: {e}", reply_markup=main_menu())
        clear_ud(uid)


async def post_init(application):
    await application.bot.set_my_commands(
        [
            BotCommand("start", "Start bot"),
            BotCommand("menu", "Open main menu"),
            BotCommand("help", "How to use"),
            BotCommand("cancel", "Cancel current task"),
        ]
    )


if __name__ == "__main__":
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("cancel", cancel_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_file))
    print("🚀 ULTIMATE VCF MANAGER STARTED (PERMANENT BUTTON UI)")
    app.run_polling()