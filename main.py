# Python 3.9.2 compatibility — defers annotation evaluation so all type hints
# work correctly on Python 3.9 without needing to import from typing.
from __future__ import annotations

import os, asyncio, logging, time, sys, shutil, re, secrets, socket, random, gc, base64, zipfile
from datetime import datetime, timezone, timedelta
from asyncio import create_subprocess_exec
from asyncio.subprocess import PIPE

import psutil
from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand
)
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, ConversationHandler, ContextTypes,
    filters
)

# ─────────────────────────────────────────────────────────────
# Live progress animation
# ─────────────────────────────────────────────────────────────

_PROGRESS_BAR_WIDTH = 20
_PROGRESS_EDIT_INTERVAL = 8.0   # OPT: was 2.0 — 4× fewer Telegram API edits


def _progress_fmt_time(secs: float) -> str:
    s = int(secs)
    m, s = divmod(s, 60)
    return f"{m:02d}:{s:02d}"


def _progress_bar(pct: int, frame_idx: int, width: int = _PROGRESS_BAR_WIDTH) -> str:
    filled = int(width * pct / 100)
    remaining = width - filled
    bar_chars = ["█"] * filled + ["░"] * remaining
    if remaining > 0 and pct < 100:
        pulse_pos = filled + (frame_idx % remaining)
        bar_chars[pulse_pos] = "▒"
    return "[" + "".join(bar_chars) + f"] {pct}%"


class LiveProgress:
    def __init__(self, message, title: str = "Working"):
        self.message = message
        self.title = title
        self._running = False
        self._task = None
        self._start_ts = 0.0
        self._estimated = 60.0
        self._last_text = ""

    def _render(self, pct: int, frame_idx: int, elapsed: float, status: str) -> str:
        bar = _progress_bar(pct, frame_idx)
        return (
            f"⚙️ *{self.title}*\n\n"
            f"⏳ {status}\n"
            f"`{bar}`\n"
            f"⏱ {_progress_fmt_time(elapsed)}"
        )

    async def _safe_edit(self, text: str):
        if text == self._last_text:
            return
        self._last_text = text
        try:
            await self.message.edit_text(text, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            pass

    async def start(self, status: str = "Starting..."):
        self._start_ts = time.time()
        await self._safe_edit(self._render(0, 0, 0.0, status))

    async def animate(self, estimated_seconds: float = 60.0, status: str = "Working..."):
        self._running = True
        self._estimated = max(5.0, estimated_seconds)
        self._start_ts = time.time()
        frame = 0
        try:
            while self._running:
                elapsed = time.time() - self._start_ts
                pct = min(95, int(elapsed / self._estimated * 100))
                await self._safe_edit(self._render(pct, frame, elapsed, status))
                frame += 1
                await asyncio.sleep(_PROGRESS_EDIT_INTERVAL)
        except asyncio.CancelledError:
            pass

    def run_in_background(self, estimated_seconds: float = 60.0, status: str = "Working..."):
        self._task = asyncio.create_task(self.animate(estimated_seconds, status))
        return self._task

    async def stop(self, success: bool = True, final_text: str = "Done"):
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except Exception:
                pass
        elapsed = time.time() - self._start_ts
        if success:
            bar = _progress_bar(100, 0)
            text = (
                f"✅ *{self.title}*\n\n"
                f"{final_text}\n"
                f"`{bar}`\n"
                f"⏱ {_progress_fmt_time(elapsed)}"
            )
        else:
            text = (
                f"❌ *{self.title} — Failed*\n\n"
                f"{final_text}\n"
                f"⏱ {_progress_fmt_time(elapsed)}"
            )
        self._last_text = ""
        await self._safe_edit(text)

# ─────────────────────────────────────────────────────────────
# Bootstrap
# ─────────────────────────────────────────────────────────────
load_dotenv()
logging.basicConfig(
    format="%(asctime)s — %(name)s — %(levelname)s — %(message)s",
    level=logging.WARNING,   # OPT: was INFO — cuts disk I/O from log writes
)
# Keep our own logger at INFO so important bot events still show
logging.getLogger(__name__).setLevel(logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN       = os.getenv("BOT_TOKEN", "")
OWNER_ID        = int(os.getenv("OWNER_ID", "0"))
OWNER_USERNAME  = os.getenv("OWNER_USERNAME", "owner")
MONGODB_URI     = os.getenv("MONGODB_URI", "")
DATABASE_NAME   = os.getenv("DATABASE_NAME", "god_madara_hosting")
BASE_URL        = os.getenv("BASE_URL", "http://localhost:8080")
PORT            = int(os.getenv("PORT", "8080"))

# Local SQLite DB path
LOCAL_DB_PATH   = os.path.join(os.path.dirname(__file__), "local_data.db")

# FIX: marker file used to tell a crash-restart apart from a normal first
# start / clean shutdown. Written on startup, removed on clean shutdown
# (post_shutdown). If it's still present on the next startup, the previous
# run never shut down cleanly — i.e. the bot crashed — and we notify the
# owner only now, once the restart has actually succeeded (not before).
BOT_RUNNING_MARKER = os.path.join(os.path.dirname(__file__), ".bot_running")

# Startup validation — fail early with a clear message
if not BOT_TOKEN:
    logger.critical("BOT_TOKEN is not set! Bot cannot start.")
    sys.exit(1)
if not MONGODB_URI:
    logger.critical("MONGODB_URI is not set! Bot cannot start.")
    sys.exit(1)

# Primary MongoDB
mongo_client = AsyncIOMotorClient(MONGODB_URI)
db           = mongo_client[DATABASE_NAME]
users_col    = db["users"]
projects_col = db["projects"]
tokens_col   = db["file_tokens"]
backups_col  = db["backups"]
settings_col = db["settings"]   # Bot-wide settings (lock, maintenance, active_db)
domains_col  = db["admin_domains"]  # Owner-managed domain pool (loca.lt subdomains)

# ─────────────────────────────────────────────────────────────
# Multiple Extra Databases (UNLIMITED)
# ─────────────────────────────────────────────────────────────
extra_clients = []
extra_dbs     = []

def _load_extra_databases():
    seen_names = set()
    legacy_uri  = os.getenv("MONGODB_URI_2", "")
    legacy_name = os.getenv("DATABASE_NAME_2", "")
    if legacy_uri and legacy_name and legacy_name not in seen_names:
        try:
            client = AsyncIOMotorClient(legacy_uri)
            extra_clients.append(client)
            extra_dbs.append({"name": legacy_name, "db": client[legacy_name], "client": client})
            seen_names.add(legacy_name)
            logger.info(f"✅ Extra DB connected (legacy): {legacy_name}")
        except Exception as e:
            logger.error(f"❌ Failed to connect legacy DB: {e}")

    for i in range(1, 51):
        uri  = os.getenv(f"MONGODB_URI_{i}", "")
        name = os.getenv(f"DATABASE_NAME_{i}", "")
        if not uri or not name or name in seen_names:
            continue
        try:
            client = AsyncIOMotorClient(uri)
            extra_clients.append(client)
            extra_dbs.append({"name": name, "db": client[name], "client": client})
            seen_names.add(name)
            logger.info(f"✅ Extra DB #{i} connected: {name}")
        except Exception as e:
            logger.error(f"❌ Failed to connect DB #{i} ({name}): {e}")

_load_extra_databases()
logger.info(f"📊 Total extra databases connected: {len(extra_dbs)}")

db_2 = extra_dbs[0]["db"] if extra_dbs else None
mongo_client_2 = extra_clients[0] if extra_clients else None
MONGODB_URI_2 = os.getenv("MONGODB_URI_2", "")
DATABASE_NAME_2 = os.getenv("DATABASE_NAME_2", "")

def get_extra_db_by_name(name: str):
    for entry in extra_dbs:
        if entry["name"] == name:
            return entry["db"]
    return None

def list_extra_db_names() -> list:
    return [e["name"] for e in extra_dbs]

# ─────────────────────────────────────────────────────────────
# Sharding helpers
# ─────────────────────────────────────────────────────────────

def all_backup_cols() -> list:
    return [backups_col] + [e["db"]["backups"] for e in extra_dbs]

def all_db_names() -> list:
    return [DATABASE_NAME] + [e["name"] for e in extra_dbs]

def pick_backup_col(user_id: int, project_name: str):
    import hashlib
    cols  = all_backup_cols()
    names = all_db_names()
    if len(cols) == 1:
        return (names[0], cols[0])
    key = f"{user_id}:{project_name}".encode("utf-8")
    h = int(hashlib.md5(key).hexdigest(), 16)
    idx = h % len(cols)
    return (names[idx], cols[idx])

BOT_START_TIME = time.time()
notification_bot = None
domain_tunnels: dict = {}   # {full_domain: asyncio.subprocess.Process}

# ─────────────────────────────────────────────────────────────
# ⚙️ Bot Settings (lock, maintenance, active_db)
# ─────────────────────────────────────────────────────────────

_settings_cache: dict = {}
_settings_cache_ts: float = 0.0
_SETTINGS_CACHE_TTL = 10.0  # seconds

async def get_bot_settings() -> dict:
    global _settings_cache, _settings_cache_ts
    now = time.time()
    if now - _settings_cache_ts < _SETTINGS_CACHE_TTL and _settings_cache:
        return _settings_cache
    doc = await settings_col.find_one({"_id": "bot_settings"})
    if not doc:
        doc = {
            "_id": "bot_settings",
            "bot_locked": False,
            "maintenance_mode": False,
            "active_db": "mongodb",  # "mongodb" or "local"
        }
        try:
            await settings_col.insert_one(doc)
        except Exception:
            pass
    _settings_cache = doc
    _settings_cache_ts = now
    return doc

async def set_bot_setting(key: str, value) -> None:
    global _settings_cache, _settings_cache_ts
    await settings_col.update_one(
        {"_id": "bot_settings"},
        {"$set": {key: value}},
        upsert=True,
    )
    _settings_cache = {}
    _settings_cache_ts = 0.0

async def is_bot_locked() -> bool:
    s = await get_bot_settings()
    return bool(s.get("bot_locked", False))

async def is_maintenance_mode() -> bool:
    s = await get_bot_settings()
    return bool(s.get("maintenance_mode", False))

async def get_active_db() -> str:
    s = await get_bot_settings()
    return s.get("active_db", "mongodb")

# ─────────────────────────────────────────────────────────────
# 🗄️ Local SQLite Database
# ─────────────────────────────────────────────────────────────

import sqlite3 as _sqlite3

def init_local_db():
    """Create SQLite DB and tables if they don't exist."""
    conn = _sqlite3.connect(LOCAL_DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        is_premium INTEGER DEFAULT 0,
        premium_expiry TEXT,
        is_banned INTEGER DEFAULT 0,
        is_admin INTEGER DEFAULT 0,
        joined_date TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS projects (
        user_id INTEGER,
        name TEXT,
        run_command TEXT,
        created_date TEXT,
        last_run TEXT,
        exit_code INTEGER,
        status TEXT DEFAULT 'stopped',
        pid INTEGER,
        admin_stopped INTEGER DEFAULT 0,
        auto_restart INTEGER DEFAULT 1,
        restart_count INTEGER DEFAULT 0,
        last_restart_at TEXT,
        started_at TEXT,
        locked INTEGER DEFAULT 0,
        PRIMARY KEY (user_id, name)
    )""")
    conn.commit()
    conn.close()
    logger.info(f"✅ Local SQLite DB ready at {LOCAL_DB_PATH}")

async def migrate_mongo_to_local() -> tuple:
    """Copy all data from MongoDB to local SQLite. Returns (users_count, projects_count)."""
    try:
        init_local_db()
        loop = asyncio.get_running_loop()

        all_users = await users_col.find({}).to_list(length=100000)
        all_projects = await projects_col.find({}).to_list(length=100000)

        def _do_migrate(users, projects):
            conn = _sqlite3.connect(LOCAL_DB_PATH)
            c = conn.cursor()
            for u in users:
                expiry = u.get("premium_expiry")
                expiry_str = expiry.isoformat() if expiry else None
                c.execute("""INSERT OR REPLACE INTO users
                    (user_id, username, first_name, is_premium, premium_expiry,
                     is_banned, is_admin, joined_date)
                    VALUES (?,?,?,?,?,?,?,?)""",
                    (u["user_id"], u.get("username",""), u.get("first_name",""),
                     1 if u.get("is_premium") else 0,
                     expiry_str,
                     1 if u.get("is_banned") else 0,
                     1 if u.get("is_admin") else 0,
                     u.get("joined_date", datetime.now(timezone.utc)).isoformat()
                         if hasattr(u.get("joined_date"), "isoformat") else str(u.get("joined_date",""))
                    )
                )
            for p in projects:
                def _ts(field):
                    v = p.get(field)
                    return v.isoformat() if v and hasattr(v, "isoformat") else None
                c.execute("""INSERT OR REPLACE INTO projects
                    (user_id, name, run_command, created_date, last_run, exit_code,
                     status, pid, admin_stopped, auto_restart, restart_count,
                     last_restart_at, started_at, locked)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (p["user_id"], p["name"], p.get("run_command"),
                     _ts("created_date"), _ts("last_run"), p.get("exit_code"),
                     p.get("status","stopped"), p.get("pid"),
                     1 if p.get("admin_stopped") else 0,
                     1 if p.get("auto_restart", True) else 0,
                     p.get("restart_count", 0), _ts("last_restart_at"),
                     _ts("started_at"),
                     1 if p.get("locked") else 0
                    )
                )
            conn.commit()
            conn.close()
            return len(users), len(projects)

        uc, pc = await loop.run_in_executor(None, _do_migrate, all_users, all_projects)
        logger.info(f"✅ Migration Mongo→Local: {uc} users, {pc} projects")
        return uc, pc
    except Exception as e:
        logger.error(f"Migration Mongo→Local failed: {e}")
        raise

async def migrate_local_to_mongo() -> tuple:
    """Copy all data from local SQLite back to MongoDB. Returns (users_count, projects_count)."""
    try:
        loop = asyncio.get_running_loop()

        def _read_local():
            conn = _sqlite3.connect(LOCAL_DB_PATH)
            conn.row_factory = _sqlite3.Row
            c = conn.cursor()
            users = [dict(r) for r in c.execute("SELECT * FROM users").fetchall()]
            projects = [dict(r) for r in c.execute("SELECT * FROM projects").fetchall()]
            conn.close()
            return users, projects

        local_users, local_projects = await loop.run_in_executor(None, _read_local)

        def _parse_dt(s):
            if not s:
                return None
            try:
                return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)
            except Exception:
                return None

        for u in local_users:
            doc = {
                "user_id": u["user_id"],
                "username": u.get("username",""),
                "first_name": u.get("first_name",""),
                "is_premium": bool(u.get("is_premium",0)),
                "premium_expiry": _parse_dt(u.get("premium_expiry")),
                "is_banned": bool(u.get("is_banned",0)),
                "is_admin": bool(u.get("is_admin",0)),
                "joined_date": _parse_dt(u.get("joined_date")) or datetime.now(timezone.utc),
            }
            await users_col.update_one({"user_id": doc["user_id"]}, {"$set": doc}, upsert=True)

        for p in local_projects:
            doc = {
                "user_id": p["user_id"],
                "name": p["name"],
                "run_command": p.get("run_command"),
                "created_date": _parse_dt(p.get("created_date")) or datetime.now(timezone.utc),
                "last_run": _parse_dt(p.get("last_run")),
                "exit_code": p.get("exit_code"),
                "status": p.get("status","stopped"),
                "pid": p.get("pid"),
                "admin_stopped": bool(p.get("admin_stopped",0)),
                "auto_restart": bool(p.get("auto_restart",1)),
                "restart_count": p.get("restart_count",0),
                "last_restart_at": _parse_dt(p.get("last_restart_at")),
                "started_at": _parse_dt(p.get("started_at")),
                "locked": bool(p.get("locked",0)),
            }
            await projects_col.update_one(
                {"user_id": doc["user_id"], "name": doc["name"]}, {"$set": doc}, upsert=True
            )

        logger.info(f"✅ Migration Local→Mongo: {len(local_users)} users, {len(local_projects)} projects")
        return len(local_users), len(local_projects)
    except Exception as e:
        logger.error(f"Migration Local→Mongo failed: {e}")
        raise

# ─────────────────────────────────────────────────────────────
# Conversation states
# ─────────────────────────────────────────────────────────────
(
    NEW_PROJECT_NAME,
    NEW_PROJECT_FILES,
    EDIT_RUN_CMD,
    ADMIN_GIVE_PREMIUM_ID,
    ADMIN_REMOVE_PREMIUM_ID,
    ADMIN_TEMP_PREMIUM_ID,
    ADMIN_TEMP_PREMIUM_DUR,
    ADMIN_BAN_ID,
    ADMIN_UNBAN_ID,
    ADMIN_BROADCAST_MSG,
    ADMIN_SEND_USER_ID,
    ADMIN_SEND_USER_MSG,
    ENV_ADD_KEY,
    ENV_ADD_VALUE,
    ENV_EDIT_VALUE,
    ADMIN_ADD_ADMIN_ID,
    ADMIN_REMOVE_ADMIN_ID,
    GITHUB_URL,
    CLONE_NAME,
    CRON_EXPR,
    CRON_CMD,
    CUSTOM_DOMAIN,
    PORT_SET,
    DB_VIEWER_SEARCH,
    ADMIN_DOMAIN_NAME,
    ADMIN_DOMAIN_ASSIGN_UID,
    PLAN_SELECT,
    PAY_UPI_SCREENSHOT,
    PAY_UPI_UTR,
    PAY_CRYPTO_WAIT,
    PLAN_ADMIN_UPI_ID,
    PLAN_ADMIN_UPI_QR,
    DOMAIN_EXT_ADD,
    DOM_PURCHASE_NAME,
) = range(34)

FREE_LIMIT    = 1
PREMIUM_LIMIT = 9999

PLANS = {
    "free":     {"name": "Free",     "price": 0,   "projects": 1,  "autostop_hours": 12, "custom_domain": False},
    "basic":    {"name": "Basic",    "price": 49,  "projects": 3,  "autostop_hours": 0,  "custom_domain": False},
    "pro":      {"name": "Pro",      "price": 99,  "projects": 10, "autostop_hours": 0,  "custom_domain": False},
    "premium":  {"name": "Premium",  "price": 149, "projects": 25, "autostop_hours": 0,  "custom_domain": True},
    "ultimate": {"name": "Ultimate", "price": 199, "projects": 0,  "autostop_hours": 0,  "custom_domain": True},
}

PROJECTS_ROOT = os.path.join(os.path.dirname(__file__), "projects")
os.makedirs(PROJECTS_ROOT, exist_ok=True)

# ─────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────

def project_dir(user_id: int, project_name: str) -> str:
    return os.path.join(PROJECTS_ROOT, str(user_id), project_name)

def fmt_bytes(b: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if b < 1024:
            return f"{b:.1f} {unit}"
        b /= 1024
    return f"{b:.1f} TB"

def fmt_uptime(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}h {m}m {sec}s" if h else (f"{m}m {sec}s" if m else f"{sec}s")

def fmt_duration(total_seconds: float) -> str:
    return fmt_uptime(total_seconds)

def escape_md(text: str) -> str:
    """Escape Markdown v1 special characters."""
    for ch in ('_', '*', '`', '['):
        text = str(text).replace(ch, f'\\{ch}')
    return text

_SAFE_EDIT_UNSET = object()

async def safe_edit(query, text: str, reply_markup=None, parse_mode=_SAFE_EDIT_UNSET):
    # FIX: use sentinel so callers can explicitly pass parse_mode=None to send plain text.
    # Previously the default was ParseMode.MARKDOWN, which silently overrode explicit None.
    if parse_mode is _SAFE_EDIT_UNSET:
        parse_mode = ParseMode.MARKDOWN
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
    except BadRequest as e:
        logger.warning(f"safe_edit BadRequest: {e}")
        try:
            await query.edit_message_text(text, reply_markup=reply_markup)
        except Exception:
            pass
    except Exception as e:
        logger.error(f"safe_edit error: {e}")

async def ensure_user(user):
    await users_col.update_one(
        {"user_id": user.id},
        {"$setOnInsert": {
            "user_id":       user.id,
            "username":      user.username or "",
            "first_name":    user.first_name or "",
            "is_premium":    False,
            "premium_expiry": None,
            "is_banned":     False,
            "is_admin":      False,
            "joined_date":   datetime.now(timezone.utc),
        }},
        upsert=True,
    )
    await users_col.update_one(
        {"user_id": user.id},
        {"$set": {
            "username":   user.username or "",
            "first_name": user.first_name or "",
        }},
    )

async def check_premium_expiry(user_id: int):
    """Strip premium if expired. Also lock extra projects for expired premium users."""
    doc = await users_col.find_one({"user_id": user_id})
    if not doc:
        return

    was_premium = doc.get("is_premium", False)
    expiry = doc.get("premium_expiry")

    if was_premium and expiry:
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry < datetime.now(timezone.utc):
            # Premium expired — remove it
            await users_col.update_one(
                {"user_id": user_id},
                {"$set": {"is_premium": False, "premium_expiry": None}},
            )
            # Lock all projects except the first one (by created_date)
            await _lock_extra_projects_on_expiry(user_id)
            logger.info(f"Premium expired for user {user_id}. Extra projects locked.")

async def _lock_extra_projects_on_expiry(user_id: int):
    """When premium expires, keep only the 1st project (by creation date) unlocked. Stop & lock the rest."""
    all_projs = await projects_col.find({"user_id": user_id}).sort("created_date", 1).to_list(length=1000)
    if len(all_projs) <= FREE_LIMIT:
        return

    # First project stays free
    first_proj = all_projs[0]["name"]

    for i, p in enumerate(all_projs):
        if i == 0:
            # Unlock the first project
            await projects_col.update_one(
                {"user_id": user_id, "name": p["name"]},
                {"$set": {"locked": False}},
            )
            continue

        # Lock and stop all others
        was_running = p.get("status") == "running"
        await projects_col.update_one(
            {"user_id": user_id, "name": p["name"]},
            {"$set": {"locked": True}},
        )
        if was_running:
            await kill_project(user_id, p["name"])
            # Notify user
            if notification_bot:
                try:
                    await notification_bot.send_message(
                        chat_id=user_id,
                        text=(
                            f"⚠️ *Premium Expired*\n\n"
                            f"Your premium has expired. Project `{p['name']}` has been stopped and locked.\n"
                            f"Only `{first_proj}` remains active.\n\n"
                            f"Upgrade to Premium to unlock all your projects!"
                        ),
                        parse_mode=ParseMode.MARKDOWN,
                    )
                except Exception:
                    pass

async def get_user(user_id: int):
    return await users_col.find_one({"user_id": user_id})

async def is_banned(user_id: int) -> bool:
    doc = await get_user(user_id)
    return bool(doc and doc.get("is_banned"))

async def is_premium(user_id: int) -> bool:
    await check_premium_expiry(user_id)
    doc = await get_user(user_id)
    return bool(doc and doc.get("is_premium"))

async def is_admin(user_id: int) -> bool:
    doc = await get_user(user_id)
    return bool(doc and doc.get("is_admin"))

async def is_owner_or_admin(user_id: int) -> bool:
    return user_id == OWNER_ID or await is_admin(user_id)

def owner_only(func):
    import functools
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if uid != OWNER_ID:
            if update.callback_query:
                await update.callback_query.answer("⛔ Owner only", show_alert=True)
            return
        return await func(update, context)
    return wrapper

def admin_or_owner(func):
    import functools
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if not await is_owner_or_admin(uid):
            if update.callback_query:
                await update.callback_query.answer("⛔ Admin only", show_alert=True)
            return
        return await func(update, context)
    return wrapper

async def project_count(user_id: int) -> int:
    return await projects_col.count_documents({"user_id": user_id})

async def get_user_plan(user_id: int) -> str:
    doc = await get_user(user_id)
    if not doc:
        return "free"
    plan = doc.get("plan", "")
    if plan in PLANS:
        return plan
    if doc.get("is_premium"):
        return "premium"
    return "free"

async def get_plan_limit(user_id: int) -> int:
    return PLANS[await get_user_plan(user_id)]["projects"]

async def user_can_use_custom_domain(user_id: int) -> bool:
    return PLANS[await get_user_plan(user_id)]["custom_domain"] or user_id == OWNER_ID

async def get_autostop_hours(user_id: int) -> int:
    return PLANS[await get_user_plan(user_id)]["autostop_hours"]

async def get_project(user_id: int, name: str):
    return await projects_col.find_one({"user_id": user_id, "name": name})

async def running_project_count() -> int:
    return await projects_col.count_documents({"status": "running"})

# ─────────────────────────────────────────────────────────────
# Log rotation helper
# ─────────────────────────────────────────────────────────────

MAX_LOG_SIZE = 4 * 1024 * 1024  # 4 MB

def rotate_log_if_needed(log_path: str):
    """If log > 4MB, keep only the last 2MB."""
    try:
        if os.path.exists(log_path) and os.path.getsize(log_path) > MAX_LOG_SIZE:
            with open(log_path, "rb") as f:
                f.seek(-2 * 1024 * 1024, 2)
                data = f.read()
            with open(log_path, "wb") as f:
                f.write(b"...[log rotated]...\n")
                f.write(data)
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────
# /start
# ─────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_user(user)
    await check_premium_expiry(user.id)

    # Maintenance mode — only owner allowed
    if await is_maintenance_mode() and user.id != OWNER_ID:
        await update.message.reply_text(
            "🔧 *Bot is under maintenance.*\n\nOnly the owner can use it right now. Please try again later.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if await is_banned(user.id):
        await update.message.reply_text("🚫 You are banned. Contact owner.", parse_mode=ParseMode.MARKDOWN)
        return

    doc      = await get_user(user.id)
    plan_key = await get_user_plan(user.id)
    plan_inf = PLANS[plan_key]
    count    = await project_count(user.id)
    lim      = plan_inf["projects"]
    lim_lbl  = "∞" if lim == 0 else str(lim)
    lock_line = ""
    if await is_bot_locked() and plan_key == "free" and user.id != OWNER_ID:
        lock_line = "\n\n🔒 *Bot is locked.* New projects restricted to paid plans."
    parts = [
        "🌟 *Welcome to God Madara Hosting Bot!*\n\n",
        "👋 Hello " + user.first_name + "!\n\n",
        "🚀 *What I can do:*\n",
        "• Host Python / Node.js projects 24/7\n",
        "• Web File Manager — Edit files in browser\n",
        "• Auto-install requirements.txt / package.json\n",
        "• Real-time logs & monitoring\n",
        "• Custom domains, UPI & crypto payments\n\n",
        "📊 *Your Status:*\n",
        "👤 ID: `" + str(user.id) + "`\n",
        "💎 Plan: *" + plan_inf["name"] + "*\n",
        "📁 Projects: " + str(count) + "/" + lim_lbl,
        lock_line,
        "\n\nChoose an option below:",
    ]
    text = "".join(parts)
    kb = [
        [
            InlineKeyboardButton("🆕 New Project",  callback_data="new_project"),
            InlineKeyboardButton("📂 My Projects",  callback_data="my_projects"),
        ],
        [
            InlineKeyboardButton("💎 Plans",         callback_data="plans"),
            InlineKeyboardButton("📊 My Status",     callback_data="my_status"),
        ],
    ]
    if user.id == OWNER_ID or await is_admin(user.id):
        kb.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")])
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def cb_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = update.effective_user
    await ensure_user(user)
    await check_premium_expiry(user.id)

    if await is_maintenance_mode() and user.id != OWNER_ID:
        await safe_edit(query, "🔧 *Bot is under maintenance.* Only the owner can use it right now.", parse_mode=ParseMode.MARKDOWN)
        return

    if await is_banned(user.id):
        await safe_edit(query, "🚫 You are banned. Contact owner.", parse_mode=ParseMode.MARKDOWN)
        return

    doc      = await get_user(user.id)
    plan_key = await get_user_plan(user.id)
    plan_inf = PLANS[plan_key]
    count    = await project_count(user.id)
    lim      = plan_inf["projects"]
    lim_lbl  = "∞" if lim == 0 else str(lim)
    lock_line = ""
    if await is_bot_locked() and plan_key == "free" and user.id != OWNER_ID:
        lock_line = "\n\n🔒 *Bot is locked.* New projects restricted to paid plans."
    parts = [
        "🌟 *Welcome to God Madara Hosting Bot!*\n\n",
        "👋 Hello " + user.first_name + "!\n\n",
        "🚀 *What I can do:*\n",
        "• Host Python / Node.js projects 24/7\n",
        "• Web File Manager — Edit files in browser\n",
        "• Auto-install requirements / package.json\n",
        "• Real-time logs & monitoring\n",
        "• Custom domains, UPI & crypto payments\n\n",
        "📊 *Your Status:*\n",
        "👤 ID: `" + str(user.id) + "`\n",
        "💎 Plan: *" + plan_inf["name"] + "*\n",
        "📁 Projects: " + str(count) + "/" + lim_lbl,
        lock_line,
        "\n\nChoose an option below:",
    ]
    text = "".join(parts)
    kb = [
        [
            InlineKeyboardButton("🆕 New Project",  callback_data="new_project"),
            InlineKeyboardButton("📂 My Projects",  callback_data="my_projects"),
        ],
        [
            InlineKeyboardButton("💎 Plans",         callback_data="plans"),
            InlineKeyboardButton("📊 My Status",     callback_data="my_status"),
        ],
    ]
    if user.id == OWNER_ID or await is_admin(user.id):
        kb.append([InlineKeyboardButton("⚙️ Admin Panel", callback_data="admin_panel")])
    await safe_edit(query, text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────────────────────
# 📊 Bot Status
# ─────────────────────────────────────────────────────────────

@admin_or_owner
async def cb_bot_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        db_ping = 0
        try:
            t0 = time.time()
            await db.command("ping")
            db_ping = int((time.time() - t0) * 1000)
        except Exception:
            db_ping = -1

        api_ping = 0
        try:
            t1 = time.time()
            await context.bot.get_me()
            api_ping = int((time.time() - t1) * 1000)
        except Exception:
            api_ping = -1

        total_users = await users_col.count_documents({})
        premium_users = await users_col.count_documents({"is_premium": True})
        total_proj = await projects_col.count_documents({})
        running_proj = await running_project_count()

        cpu = psutil.cpu_percent(interval=None)
        ram = psutil.virtual_memory()
        disk = psutil.disk_usage("/")

        uptime = fmt_uptime(time.time() - BOT_START_TIME)
        py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

        backup_line = "💾 Last Backup: `Never`\n"
        try:
            meta = await backups_col.find_one({"type": "backup_meta"})
            if meta:
                backup_time = meta["backed_up_at"].strftime("%Y-%m-%d %H:%M UTC")
                backup_size = fmt_bytes(meta.get("total_size", 0))
                backup_files = meta.get("total_files", 0)
                backup_line = (
                    f"💾 Last Backup: `{backup_time}`\n"
                    f"📦 Backup: `{backup_files}` files, `{backup_size}`\n"
                )
        except Exception:
            pass

        extra_db_lines = ""
        extra_online = 0
        per_db_stats = []

        try:
            primary_proj_count = await backups_col.count_documents({"type": "file_backup"})
        except Exception:
            primary_proj_count = 0
        per_db_stats.append((DATABASE_NAME, db_ping >= 0, primary_proj_count))

        for entry in extra_dbs:
            online = False
            count = 0
            try:
                await entry["db"].command("ping")
                online = True
                extra_online += 1
                count = await entry["db"]["backups"].count_documents({"type": "file_backup"})
            except Exception:
                pass
            per_db_stats.append((entry["name"], online, count))

        total_dbs = 1 + len(extra_dbs)
        total_online = (1 if db_ping >= 0 else 0) + extra_online

        if extra_dbs:
            extra_db_lines = f"\n*🗄 Storage Distribution:*\n"
            for name, online, count in per_db_stats:
                icon = "🟢" if online else "🔴"
                extra_db_lines += f"   {icon} `{name}`: `{count}` projects\n"

        db_ping_str = f"{db_ping}ms" if db_ping >= 0 else "Error"
        api_ping_str = f"{api_ping}ms" if api_ping >= 0 else "Error"

        # Bot settings status
        settings = await get_bot_settings()
        lock_icon = "🔒 ON" if settings.get("bot_locked") else "🔓 OFF"
        maint_icon = "🔧 ON" if settings.get("maintenance_mode") else "✅ OFF"
        active_db_label = "🗄 Local (SQLite)" if settings.get("active_db") == "local" else "☁️ MongoDB"
        local_db_exists = "✅" if os.path.exists(LOCAL_DB_PATH) else "❌"

        text = (
            f"📊 *Bot Dashboard*\n\n"
            f"👥 Total Users: `{total_users}`\n"
            f"💎 Premium Users: `{premium_users}`\n"
            f"📁 Total Projects: `{total_proj}`\n"
            f"🟢 Running Projects: `{running_proj}`\n"
            f"🔒 Bot Lock: `{lock_icon}`\n"
            f"🔧 Maintenance: `{maint_icon}`\n"
            f"💾 Active DB: `{active_db_label}`\n"
            f"🗄 Local DB File: `{local_db_exists}`\n"
            f"🔗 Connected DBs: `{total_online}/{total_dbs}`\n"
            f"{extra_db_lines}"
            f"🐍 Python: `{py_ver}`\n\n"
            f"💻 *System:*\n"
            f"├ CPU: `{cpu}%`\n"
            f"├ RAM: `{fmt_bytes(ram.used)}/{fmt_bytes(ram.total)}` (`{ram.percent}%`)\n"
            f"└ Disk: `{fmt_bytes(disk.used)}/{fmt_bytes(disk.total)}` (`{disk.percent}%`)\n\n"
            f"🏓 Bot Ping: `{api_ping_str}`\n"
            f"💾 DB Ping: `{db_ping_str}`\n"
            f"⏰ Uptime: `{uptime}`\n\n"
            f"*Backup Status:*\n"
            f"{backup_line}"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔃 Refresh", callback_data="bot_status"),
             InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")],
        ])
        await safe_edit(query, text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"bot_status error: {e}")
        await safe_edit(
            query,
            f"📊 *Bot Dashboard*\n\n⚠️ Error: {str(e)[:200]}\n\nBot is online!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔃 Retry", callback_data="bot_status"),
                 InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")],
            ]),
            parse_mode=ParseMode.MARKDOWN,
        )

# ─────────────────────────────────────────────────────────────
# 💎 Premium page
# ─────────────────────────────────────────────────────────────

async def cb_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Legacy — redirect to new Plans page."""
    return await cb_plans(update, context)

# ─────────────────────────────────────────────────────────────
# 💳 Plans, Payment & Domain Purchase
# ─────────────────────────────────────────────────────────────


async def get_user_plan(user_id: int) -> str:
    doc = await get_user(user_id)
    if not doc:
        return "free"
    plan = doc.get("plan", "")
    if plan in PLANS:
        return plan
    if doc.get("is_premium"):
        return "premium"
    return "free"

async def get_plan_limit(user_id: int) -> int:
    return PLANS[await get_user_plan(user_id)]["projects"]

async def user_can_use_custom_domain(user_id: int) -> bool:
    return PLANS[await get_user_plan(user_id)]["custom_domain"] or user_id == OWNER_ID

async def get_autostop_hours(user_id: int) -> int:
    return PLANS[await get_user_plan(user_id)]["autostop_hours"]

async def cb_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if await is_banned(uid):
        await safe_edit(query, "🚫 You are banned.")
        return
    plan_key = await get_user_plan(uid)
    INR_TO_USD = 83
    emojis = {"free": "🆓", "basic": "🥉", "pro": "🥈", "premium": "🥇", "ultimate": "👑"}
    lines = ["💎 *Hosting Plans*\n━━━━━━━━━━━━━━━━\n\n"]
    for key, p in PLANS.items():
        proj = "Unlimited" if p["projects"] == 0 else str(p["projects"])
        stop = "⏰ 12hr auto-stop" if p["autostop_hours"] else "♾️ Always on"
        dom  = "🌐 Custom domain ✅" if p["custom_domain"] else "🌐 Custom domain ❌"
        cur  = "  ◀️ *Your Plan*" if key == plan_key else ""
        icon = emojis.get(key, "•")
        if p["price"] == 0:
            price_txt = "🆓 *Free forever*"
        else:
            usd = round(p["price"] / INR_TO_USD, 2)
            price_txt = "*₹" + str(p["price"]) + "/mo*  _( ≈ $" + str(usd) + " USD)_"
        lines.append(icon + " *" + p["name"] + " Plan*" + cur + "\n")
        lines.append("   " + price_txt + "\n")
        lines.append("   📁 " + proj + " project(s)  •  " + stop + "\n")
        lines.append("   " + dom + "\n\n")
    lines.append("━━━━━━━━━━━━━━━━\n")
    lines.append("💳 *UPI:* Pay → screenshot + UTR → owner approves\n")
    lines.append("₿ *USDT BEP20:* Auto-verified via Binance API\n")
    lines.append("🌐 *Domain:* ₹49/30 days  _( any user can buy!)_")
    kb_rows = []
    for key, p in PLANS.items():
        if key == "free" or key == plan_key:
            continue
        usd = round(p["price"] / INR_TO_USD, 2)
        icon = emojis.get(key, "•")
        kb_rows.append([InlineKeyboardButton(
            icon + " " + p["name"] + " — ₹" + str(p["price"]) + "/mo  ($" + str(usd) + ")",
            callback_data="buy_plan:" + key,
        )])
    kb_rows.append([InlineKeyboardButton("🌐 Buy Custom Domain", callback_data="plans_domain_info")])
    kb_rows.append([InlineKeyboardButton("🔙 Back", callback_data="back_start")])
    await safe_edit(query, "".join(lines), reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode=ParseMode.MARKDOWN)

async def cb_buy_plan(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    plan_key = query.data.split(":", 1)[1]
    if plan_key not in PLANS or PLANS[plan_key]["price"] == 0:
        await safe_edit(query, "❌ Invalid plan.")
        return
    plan = PLANS[plan_key]
    context.user_data["buying_plan"] = plan_key
    settings = await get_bot_settings()
    upi_id = settings.get("upi_id", "")
    upi_qr = settings.get("upi_qr_file_id", "")
    INR_TO_USD = 83
    usd = round(plan["price"] / INR_TO_USD, 2)
    proj = "Unlimited" if plan["projects"] == 0 else str(plan["projects"])
    dom  = "✅ Custom Domain" if plan["custom_domain"] else "❌ No Custom Domain"
    text = ("💎 *" + plan["name"] + " Plan*\n\n"
            "💰 Price: *₹" + str(plan["price"]) + "/month*  _( ≈ $" + str(usd) + " USD)_\n"
            "📁 Projects: *" + proj + "*\n"
            "🌐 " + dom + "\n\n"
            "Choose your payment method:")
    kb = []
    if upi_id or upi_qr:
        kb.append([InlineKeyboardButton("💸 Pay via UPI  (₹" + str(plan["price"]) + ")", callback_data="pay_upi:" + plan_key)])
    kb.append([InlineKeyboardButton("₿ Pay via USDT BEP20  ($" + str(usd) + ")", callback_data="pay_crypto:" + plan_key)])
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="plans")])
    await safe_edit(query, text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)


# ──────────────────────────────────────────────────────────────
# 🌐 Plans → Domain info page + self-service domain purchase
# ──────────────────────────────────────────────────────────────

async def cb_plans_domain_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Info page for domain purchase — accessible from Plans menu."""
    query = update.callback_query
    await query.answer()
    text = ("🌐 *Custom Domain Hosting*\n\n"
            "Buy a loca.lt subdomain for any project!\n\n"
            "💰 Price: *₹49*  or  *$0.55 USDT BEP20* per 30 days\n"
            "⚡ *Any user can buy* — no plan upgrade required!\n\n"
            "🔗 *How it works:*\n"
            "1. Click *Buy Domain Now*\n"
            "2. Type your preferred name  e.g. `mybot`\n"
            "3. Bot checks if `mybot.loca.lt` is available\n"
            "4. Pay ₹49 UPI  or  $0.55 USDT\n"
            "5. Domain assigned instantly after payment!\n\n"
            "📩 For .com/.in domains contact the owner directly.")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Buy Domain Now",  callback_data="domain_purchase_start")],
        [InlineKeyboardButton("🔙 Back to Plans",   callback_data="plans")],
    ])
    await safe_edit(query, text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)


async def cb_domain_purchase_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask user for their desired subdomain name."""
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if await is_banned(uid):
        await safe_edit(query, "🚫 You are banned.")
        return
    await safe_edit(
        query,
        ("🌐 *Buy Custom Domain*\n\n"
         "Type your desired subdomain name.\n"
         "_The domain will end with_ `.loca.lt`\n\n"
         "Example: type `mybot` → you get `mybot.loca.lt`\n\n"
         "✅ Rules:\n"
         "• Only letters, numbers, hyphens\n"
         "• 3–30 characters, no spaces\n\n"
         "👇 Send your preferred name now:"),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="plans_domain_info")]]),
        parse_mode=ParseMode.MARKDOWN,
    )
    return DOM_PURCHASE_NAME


async def domain_purchase_name_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Validate name, check availability, show payment options."""
    uid = update.effective_user.id
    raw = update.message.text.strip().lower()

    if not re.match(r"^[a-z0-9][a-z0-9\-]{1,28}[a-z0-9]$", raw) and not re.match(r"^[a-z0-9]{3,30}$", raw):
        await update.message.reply_text(
            "❌ Invalid name. Use only letters, numbers and hyphens (3–30 chars).\n\nTry again:",
            parse_mode=ParseMode.MARKDOWN,
        )
        return DOM_PURCHASE_NAME

    full_domain = raw + ".loca.lt"
    doc = await domains_col.find_one({"subdomain": raw, "active": True})
    if not doc:
        await update.message.reply_text(
            "😔 *`" + escape_md(full_domain) + "`* is not available in our pool.\n\n"
            "Try a different name:",
            parse_mode=ParseMode.MARKDOWN,
        )
        return DOM_PURCHASE_NAME

    if doc.get("assigned_to") is not None:
        await update.message.reply_text(
            "❌ *`" + escape_md(full_domain) + "`* is already taken.\n\nTry a different name:",
            parse_mode=ParseMode.MARKDOWN,
        )
        return DOM_PURCHASE_NAME

    context.user_data["domain_purchase_name"] = raw
    context.user_data["buying_plan"] = "__domain__"

    settings = await get_bot_settings()
    upi_id = settings.get("upi_id", "")
    upi_qr = settings.get("upi_qr_file_id", "")
    bsc    = settings.get("binance_bsc_address", "")

    text = ("✅ *`" + escape_md(full_domain) + "` is available!*\n\n"
            "💰 Price: *₹49*  or  *$0.55 USDT BEP20*\n"
            "⏱ Valid for 30 days\n\n"
            "Choose payment method:")
    kb_rows = []
    if upi_id or upi_qr:
        kb_rows.append([InlineKeyboardButton("💸 Pay ₹49 via UPI",          callback_data="pay_upi:__domain__")])
    if bsc:
        kb_rows.append([InlineKeyboardButton("₿ Pay $0.55 via USDT BEP20",  callback_data="pay_crypto:__domain__")])
    if not kb_rows:
        kb_rows.append([InlineKeyboardButton("📩 Contact Owner", callback_data="back_start")])
    kb_rows.append([InlineKeyboardButton("❌ Cancel", callback_data="plans_domain_info")])
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END

async def cb_pay_upi(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    plan_key = query.data.split(":", 1)[1]

    if plan_key == "__domain__":
        domain_name = context.user_data.get("domain_purchase_name", "")
        full_domain = domain_name + ".loca.lt" if domain_name else "your domain"
        price = 49
        pname = "Custom Domain — " + full_domain
        cancel_cb = "plans_domain_info"
    elif plan_key in PLANS:
        plan = PLANS[plan_key]
        price = plan["price"]
        pname = plan["name"] + " Plan"
        cancel_cb = "plans"
    else:
        await safe_edit(query, "❌ Invalid plan.")
        return

    context.user_data["buying_plan"] = plan_key
    settings = await get_bot_settings()
    upi_id = settings.get("upi_id", "")
    upi_qr = settings.get("upi_qr_file_id", "")
    parts = ["💸 *UPI Payment — " + escape_md(pname) + "*\n\nAmount: *₹" + str(price) + "*\n"]
    if upi_id:
        parts.append("UPI ID: `" + escape_md(upi_id) + "`\n")
    parts.append("\n1️⃣ Send ₹" + str(price) + " to the UPI ID above\n")
    parts.append("2️⃣ Take a screenshot of the payment confirmation\n")
    parts.append("3️⃣ Send the screenshot here 👇\n\n")
    parts.append("_Your payment will be reviewed by the owner within a few minutes._")
    text = "".join(parts)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=cancel_cb)]])
    if upi_qr:
        await query.message.reply_photo(photo=upi_qr, caption=text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    else:
        await query.message.reply_text(text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    return PAY_UPI_SCREENSHOT

async def pay_upi_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo and not update.message.document:
        await update.message.reply_text(
            "❌ Please send a *screenshot* (photo) of your payment.\n\nSend a photo:",
            parse_mode=ParseMode.MARKDOWN,
        )
        return PAY_UPI_SCREENSHOT
    fid = update.message.photo[-1].file_id if update.message.photo else update.message.document.file_id
    context.user_data["pay_screenshot_file_id"] = fid
    await update.message.reply_text(
        "✅ Screenshot received!\n\nNow send your *UTR / Transaction ID*:",
        parse_mode=ParseMode.MARKDOWN,
    )
    return PAY_UPI_UTR

async def pay_upi_utr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    utr = update.message.text.strip()
    plan_key = context.user_data.get("buying_plan", "")
    ss = context.user_data.get("pay_screenshot_file_id", "")
    doc = await get_user(uid)
    uname = doc.get("username", "") if doc else ""
    us = "@" + uname if uname else "ID:" + str(uid)

    # ── Domain purchase ───────────────────────────────────────────
    if plan_key == "__domain__":
        domain_name = context.user_data.get("domain_purchase_name",
                      context.user_data.get("domain_buy_project", "unknown"))
        full_domain = domain_name + ".loca.lt" if not domain_name.endswith(".loca.lt") else domain_name
        await settings_col.insert_one({
            "type": "pending_payment", "user_id": uid, "plan": "__domain__",
            "amount": 49, "method": "upi", "utr": utr,
            "screenshot": ss, "domain_name": domain_name, "full_domain": full_domain,
            "created_at": datetime.now(timezone.utc), "status": "pending",
        })
        if OWNER_ID and notification_bot:
            try:
                kb = InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Approve + Assign", callback_data="pay_approve:" + str(uid) + ":__domain__"),
                    InlineKeyboardButton("❌ Reject",           callback_data="pay_reject:"  + str(uid) + ":__domain__"),
                ]])
                cap = ("🌐 *Custom Domain Payment*\n\n"
                       "👤 User: " + escape_md(us) + " (`" + str(uid) + "`)\n"
                       "🌐 Domain: `" + escape_md(full_domain) + "`\n"
                       "💰 Amount: ₹49\n"
                       "🔢 UTR: `" + escape_md(utr) + "`\n"
                       "📅 Time: " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC") + "\n\n"
                       "_Click Approve to automatically assign this domain to the user._")
                if ss:
                    await notification_bot.send_photo(OWNER_ID, photo=ss, caption=cap, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
                else:
                    await notification_bot.send_message(OWNER_ID, text=cap, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
            except Exception as e:
                logger.error("Domain payment notify error: " + str(e))
        await update.message.reply_text(
            "✅ *Domain payment submitted!*\n\n🌐 Domain: `" + escape_md(full_domain) + "`\nUTR: `" + escape_md(utr) + "`\n\nOwner will verify and assign your domain shortly.",
            parse_mode=ParseMode.MARKDOWN,
        )
        context.user_data.clear()
        return ConversationHandler.END

    # ── Plan purchase ─────────────────────────────────────────────
    if not plan_key or plan_key not in PLANS:
        await update.message.reply_text("❌ Session expired. Please start again from /start.")
        return ConversationHandler.END
    plan = PLANS[plan_key]
    await settings_col.insert_one({
        "type": "pending_payment", "user_id": uid, "plan": plan_key,
        "amount": plan["price"], "method": "upi", "utr": utr,
        "screenshot": ss, "created_at": datetime.now(timezone.utc), "status": "pending",
    })
    if OWNER_ID and notification_bot:
        try:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Approve", callback_data="pay_approve:" + str(uid) + ":" + plan_key),
                InlineKeyboardButton("❌ Reject",  callback_data="pay_reject:"  + str(uid) + ":" + plan_key),
            ]])
            cap = ("💳 *New Payment Request*\n\n"
                   "👤 User: " + escape_md(us) + " (`" + str(uid) + "`)\n"
                   "💎 Plan: *" + plan["name"] + "*\n"
                   "💰 Amount: ₹" + str(plan["price"]) + "\n"
                   "🔢 UTR: `" + escape_md(utr) + "`\n"
                   "📅 Time: " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
            if ss:
                await notification_bot.send_photo(OWNER_ID, photo=ss, caption=cap, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
            else:
                await notification_bot.send_message(OWNER_ID, text=cap, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        except Exception as e:
            logger.error("Payment notify error: " + str(e))
    await update.message.reply_text(
        "✅ *Payment request submitted!*\n\nPlan: *" + plan["name"] + "*\nUTR: `" + escape_md(utr) + "`\n\nThe owner will review and activate your plan shortly.",
        parse_mode=ParseMode.MARKDOWN,
    )
    context.user_data.clear()
    return ConversationHandler.END

async def cb_pay_approve(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != OWNER_ID:
        await query.answer("⛔ Owner only", show_alert=True)
        return
    parts = query.data.split(":")
    tuid = int(parts[1])
    plan_key = parts[2]

    # ── Domain payment approval — auto-assign domain ──────────────
    if plan_key == "__domain__":
        now = datetime.now(timezone.utc)
        pmt = await settings_col.find_one(
            {"type": "pending_payment", "user_id": tuid, "plan": "__domain__", "status": "pending"},
            sort=[("created_at", -1)],
        )
        domain_name = pmt.get("domain_name", "") if pmt else ""
        full_domain = pmt.get("full_domain", domain_name + ".loca.lt") if pmt else ""

        # Mark payment approved
        await settings_col.update_one(
            {"type": "pending_payment", "user_id": tuid, "plan": "__domain__", "status": "pending"},
            {"$set": {"status": "approved", "approved_at": now}},
            sort=[("created_at", -1)],
        )

        # Assign domain in pool
        assigned = False
        if domain_name:
            result = await domains_col.update_one(
                {"subdomain": domain_name, "active": True, "assigned_to": None},
                {"$set": {"assigned_to": tuid, "assigned_at": now}},
            )
            assigned = result.modified_count > 0

        # Notify user
        if notification_bot:
            try:
                if assigned:
                    await notification_bot.send_message(
                        tuid,
                        "🌐 *Domain Assigned!*\n\n✅ `" + escape_md(full_domain) + "` is now yours for 30 days!\n\nGo to your project → ⚙️ Settings → 🌐 My Domain to activate it.",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                else:
                    await notification_bot.send_message(
                        tuid,
                        "🌐 *Domain Payment Approved!*\n\nYour payment of ₹49 has been received.\nThe owner will assign your domain shortly.",
                        parse_mode=ParseMode.MARKDOWN,
                    )
            except Exception:
                pass

        msg = "✅ Domain payment approved for user `" + str(tuid) + "`."
        if assigned:
            msg += "\n🌐 `" + escape_md(full_domain) + "` auto-assigned!"
        else:
            msg += "\n⚠️ Domain not found in pool — assign manually."
        await safe_edit(query, msg, parse_mode=ParseMode.MARKDOWN)
        return

    # ── Plan payment approval ──────────────────────────────────────
    if plan_key not in PLANS:
        await safe_edit(query, "❌ Invalid plan key.")
        return
    plan = PLANS[plan_key]
    expiry = datetime.now(timezone.utc) + timedelta(days=30)
    await users_col.update_one(
        {"user_id": tuid},
        {"$set": {
            "plan": plan_key, "plan_expiry": expiry,
            "is_premium": plan_key in ("premium", "ultimate"),
            "premium_expiry": expiry if plan_key in ("premium", "ultimate") else None,
        }}, upsert=True,
    )
    await settings_col.update_one(
        {"type": "pending_payment", "user_id": tuid, "plan": plan_key, "status": "pending"},
        {"$set": {"status": "approved", "approved_at": datetime.now(timezone.utc)}},
        sort=[("created_at", -1)],
    )
    if notification_bot:
        try:
            pj = str(plan["projects"]) if plan["projects"] else "unlimited"
            await notification_bot.send_message(
                tuid,
                "🎉 *Payment Approved!*\n\nYour *" + plan["name"] + "* plan is now active for 30 days!\nExpiry: `" + expiry.strftime("%Y-%m-%d %H:%M UTC") + "`\n\nYou now have " + pj + " project(s). Enjoy!",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass
    await safe_edit(query, "✅ Approved *" + plan["name"] + "* plan for user `" + str(tuid) + "`.", parse_mode=ParseMode.MARKDOWN)

async def cb_pay_reject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != OWNER_ID:
        await query.answer("⛔ Owner only", show_alert=True)
        return
    parts = query.data.split(":")
    tuid = int(parts[1])
    plan_key = parts[2]
    if plan_key == "__domain__":
        pname = "Custom Domain (₹49)"
    else:
        pname = PLANS.get(plan_key, {}).get("name", plan_key)
    await settings_col.update_one(
        {"type": "pending_payment", "user_id": tuid, "plan": plan_key, "status": "pending"},
        {"$set": {"status": "rejected", "rejected_at": datetime.now(timezone.utc)}},
        sort=[("created_at", -1)],
    )
    if notification_bot:
        try:
            await notification_bot.send_message(
                tuid,
                "❌ *Payment Rejected*\n\nYour payment for *" + pname + "* plan was rejected.\nContact the owner if you think this is a mistake.",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass
    await safe_edit(query, "❌ Rejected payment from user `" + str(tuid) + "`.", parse_mode=ParseMode.MARKDOWN)

async def cb_pay_crypto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    plan_key = query.data.split(":", 1)[1]

    settings = await get_bot_settings()
    bsc = settings.get("binance_bsc_address", "")

    if plan_key == "__domain__":
        domain_name = context.user_data.get("domain_purchase_name", "")
        full_domain = domain_name + ".loca.lt" if domain_name else "your domain"
        usdt = 0.55
        context.user_data["buying_plan"] = "__domain__"
        context.user_data["crypto_usdt"] = usdt
        context.user_data["crypto_started"] = time.time()
        if not bsc:
            await safe_edit(
                query,
                "⚠️ Crypto payment not configured yet.\nContact the owner to set up a USDT BEP20 address.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="plans_domain_info")]]),
                parse_mode=ParseMode.MARKDOWN,
            )
            return
        text = ("₿ *USDT BEP20 Payment — Custom Domain*\n\n"
                "🌐 Domain: `" + escape_md(full_domain) + "`\n"
                "Amount: *0.55 USDT* (≈ ₹49)\n"
                "Network: *BEP20 (BSC)*\n\n"
                "Send to:\n`" + escape_md(bsc) + "`\n\n"
                "⚠️ *Send ONLY USDT on BEP20 / BSC network!*\n\n"
                "After sending, click verify:")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ I've sent — Verify", callback_data="crypto_verify:__domain__:0.55:" + domain_name)],
            [InlineKeyboardButton("❌ Cancel", callback_data="plans_domain_info")],
        ])
        await safe_edit(query, text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        return

    if plan_key not in PLANS:
        await safe_edit(query, "❌ Invalid plan.")
        return
    plan = PLANS[plan_key]
    if not bsc:
        await safe_edit(
            query,
            "⚠️ Crypto payment not configured yet.\nContact the owner to set up a USDT BEP20 address.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="plans")]]),
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    usdt = round(plan["price"] / 83, 2)
    context.user_data["buying_plan"] = plan_key
    context.user_data["crypto_usdt"] = usdt
    context.user_data["crypto_started"] = time.time()
    text = ("₿ *USDT BEP20 Payment — " + plan["name"] + " Plan*\n\n"
            "Amount: *" + str(usdt) + " USDT* (≈ ₹" + str(plan["price"]) + ")\n"
            "Network: *BEP20 (BSC)*\n\n"
            "Send to:\n`" + escape_md(bsc) + "`\n\n"
            "⚠️ *Send ONLY USDT on BEP20 / BSC network!*\n\n"
            "After sending, click the button below to verify:")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ I've sent — Verify", callback_data="crypto_verify:" + plan_key + ":" + str(usdt))],
        [InlineKeyboardButton("❌ Cancel", callback_data="plans")],
    ])
    await safe_edit(query, text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def cb_crypto_verify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("🔍 Checking deposit...", show_alert=False)
    uid = query.from_user.id
    parts = query.data.split(":")
    plan_key = parts[1]
    try:
        expected_usdt = float(parts[2])
    except Exception:
        expected_usdt = 0
    # For domain: crypto_verify:__domain__:0.55:domainname
    domain_name = parts[3] if plan_key == "__domain__" and len(parts) > 3 else context.user_data.get("domain_purchase_name", "")
    full_domain = domain_name + ".loca.lt" if domain_name and not domain_name.endswith(".loca.lt") else domain_name

    bkey = os.getenv("BINANCE_API_KEY", "")
    bsec = os.getenv("BINANCE_API_SECRET", "")
    plan = PLANS.get(plan_key, {})
    plan_label = "Custom Domain (" + full_domain + ")" if plan_key == "__domain__" else plan.get("name", plan_key)

    if not bkey or not bsec:
        if OWNER_ID and notification_bot:
            try:
                doc = await get_user(uid)
                uname = "@" + doc.get("username","") if doc and doc.get("username") else "ID:" + str(uid)
                verify_cb = "crypto_verify:" + plan_key + ":" + str(expected_usdt) + (":" + domain_name if plan_key == "__domain__" else "")
                await notification_bot.send_message(
                    OWNER_ID,
                    "₿ *Crypto Claim*\n\nUser " + escape_md(uname) + " (`" + str(uid) + "`) claims to have sent *" + str(expected_usdt) + " USDT* for *" + escape_md(plan_label) + "*\n\nVerify on BSC explorer.",
                    parse_mode=ParseMode.MARKDOWN,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("✅ Approve", callback_data="pay_approve:" + str(uid) + ":" + plan_key),
                        InlineKeyboardButton("❌ Reject",  callback_data="pay_reject:"  + str(uid) + ":" + plan_key),
                    ]]),
                )
            except Exception:
                pass
        await safe_edit(
            query,
            "⏳ *Verification pending*\n\nSent to owner for manual review. You will be notified once approved.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="back_start")]]),
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    import hmac as _hmac, hashlib as _hashlib, urllib.request as _ur
    try:
        ts = int(time.time() * 1000)
        params = "timestamp=" + str(ts) + "&coin=USDT&network=BSC"
        sig = _hmac.new(bsec.encode(), params.encode(), _hashlib.sha256).hexdigest()
        url = "https://api.binance.com/sapi/v1/capital/deposit/hisrec?" + params + "&signature=" + sig
        req = _ur.Request(url, headers={"X-MBX-APIKEY": bkey})
        import json as _json
        with _ur.urlopen(req, timeout=15) as resp:
            deposits = _json.loads(resp.read().decode())
        cutoff = time.time() - 3600
        found = any(
            d.get("coin") == "USDT" and d.get("network") == "BSC"
            and d.get("status") == 1
            and float(d.get("amount", 0)) >= expected_usdt * 0.95
            and int(d.get("insertTime", 0)) / 1000 > cutoff
            for d in (deposits if isinstance(deposits, list) else [])
        )
        if found:
            now = datetime.now(timezone.utc)
            if plan_key == "__domain__":
                # Auto-assign domain
                assigned = False
                if domain_name:
                    result = await domains_col.update_one(
                        {"subdomain": domain_name, "active": True, "assigned_to": None},
                        {"$set": {"assigned_to": uid, "assigned_at": now}},
                    )
                    assigned = result.modified_count > 0
                await settings_col.insert_one({
                    "type": "pending_payment", "user_id": uid, "plan": "__domain__",
                    "amount": 49, "method": "usdt", "utr": "USDT-AUTO",
                    "screenshot": "", "domain_name": domain_name, "full_domain": full_domain,
                    "created_at": now, "status": "approved", "approved_at": now,
                })
                msg = ("✅ *Payment Verified!*\n\n"
                       "🌐 `" + escape_md(full_domain) + "` is yours for 30 days!\n\n"
                       "Go to your project → ⚙️ Settings → 🌐 My Domain to activate it.") if assigned else (
                       "✅ *Payment received!*\n\n"
                       "Domain `" + escape_md(full_domain) + "` will be assigned shortly by the owner.")
                await safe_edit(
                    query, msg,
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="back_start")]]),
                    parse_mode=ParseMode.MARKDOWN,
                )
            else:
                expiry = now + timedelta(days=30)
                p2 = PLANS[plan_key]
                await users_col.update_one(
                    {"user_id": uid},
                    {"$set": {
                        "plan": plan_key, "plan_expiry": expiry,
                        "is_premium": plan_key in ("premium","ultimate"),
                        "premium_expiry": expiry if plan_key in ("premium","ultimate") else None,
                    }}, upsert=True,
                )
                await safe_edit(
                    query,
                    "✅ *Payment Verified!*\n\nYour *" + p2["name"] + "* plan is now active!\nExpiry: `" + expiry.strftime("%Y-%m-%d %H:%M UTC") + "`",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="back_start")]]),
                    parse_mode=ParseMode.MARKDOWN,
                )
        else:
            retry_cb = "crypto_verify:" + plan_key + ":" + str(expected_usdt) + (":" + domain_name if plan_key == "__domain__" else "")
            await safe_edit(
                query,
                "⚠️ *Deposit not found yet*\n\nExpected: " + str(expected_usdt) + " USDT on BSC (last 1 hour).\nWait 2-5 min for confirmation and try again.",
                reply_markup=InlineKeyboardMarkup([
                    [InlineKeyboardButton("🔄 Try Again", callback_data=retry_cb)],
                    [InlineKeyboardButton("🔙 Back", callback_data="plans" if plan_key != "__domain__" else "plans_domain_info")],
                ]),
                parse_mode=ParseMode.MARKDOWN,
            )
    except Exception as e:
        logger.error("Binance verify: " + str(e))
        await safe_edit(
            query,
            "⚠️ Binance API error. Sent to owner for manual review.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_start")]]),
            parse_mode=ParseMode.MARKDOWN,
        )

async def cb_domain_buy_localt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    name = query.data.split(":", 1)[1]
    p = await get_project(uid, name)
    if not p:
        await safe_edit(query, "❌ Project not found.")
        return
    settings  = await get_bot_settings()
    upi_id    = settings.get("upi_id", "")
    upi_qr    = settings.get("upi_qr_file_id", "")
    available = await domains_col.find({"assigned_to": None, "active": True}).to_list(length=10)
    if not available:
        await safe_edit(
            query,
            "😔 *No subdomains available right now.*\n\nContact the owner to add more loca.lt subdomains.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="domain:" + name)]]),
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    context.user_data["domain_buy_project"] = name
    parts_t = ["🌐 *Buy loca.lt Subdomain*\n\nPrice: *₹49 / 30 days*\nAvailable: `" + str(len(available)) + "` subdomains\n\n"]
    if upi_id:
        parts_t.append("Pay ₹49 to UPI ID: `" + escape_md(upi_id) + "`\n\n")
    parts_t.append("After paying, send your payment screenshot and UTR.\nThe owner will assign a subdomain within minutes.")
    text = "".join(parts_t)
    kb = []
    if upi_id:
        kb.append([InlineKeyboardButton("💸 Pay ₹49 via UPI", callback_data="domain_buy_pay:" + name)])
    else:
        kb.append([InlineKeyboardButton("📩 Contact Owner", url="https://t.me/" + OWNER_USERNAME)])
    kb.append([InlineKeyboardButton("🔙 Back", callback_data="domain:" + name)])
    if upi_qr:
        await query.message.reply_photo(photo=upi_qr, caption=text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)
    else:
        await safe_edit(query, text, reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

async def cb_domain_buy_pay(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    name = query.data.split(":", 1)[1]
    context.user_data["buying_plan"]        = "__domain__"
    context.user_data["domain_buy_project"] = name
    await query.message.reply_text(
        "📸 Send your UPI payment *screenshot* for ₹49:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="domain:" + name)]]),
    )
    return PAY_UPI_SCREENSHOT

async def cb_admin_payment_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != OWNER_ID:
        await query.answer("⛔ Owner only", show_alert=True)
        return
    settings = await get_bot_settings()
    uid_str  = settings.get("upi_id", "_Not set_")
    upi_qr   = "✅ Set" if settings.get("upi_qr_file_id") else "❌ Not set"
    bsc_addr = settings.get("binance_bsc_address", "_Not set_")
    pending  = await settings_col.count_documents({"type": "pending_payment", "status": "pending"})
    total    = await settings_col.count_documents({"type": "pending_payment"})
    text = ("💳 *Payment Settings*\n\n"
            "💸 UPI ID: `" + escape_md(uid_str) + "`\n"
            "📷 UPI QR: " + upi_qr + "\n"
            "₿ BSC Address: `" + escape_md(bsc_addr) + "`\n\n"
            "📊 Pending: *" + str(pending) + "*  •  Total: *" + str(total) + "*")
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Set UPI ID",         callback_data="admin:set_upi_id")],
        [InlineKeyboardButton("📷 Upload UPI QR",      callback_data="admin:set_upi_qr")],
        [InlineKeyboardButton("₿ Set BSC Address",     callback_data="admin:set_bsc_address")],
        [InlineKeyboardButton("📊 Purchase History",   callback_data="admin:purchase_history:0")],
        [InlineKeyboardButton("🔙 Admin Panel",         callback_data="admin_panel")],
    ])
    await safe_edit(query, text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)


@owner_only
async def cb_admin_purchase_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show full purchase history with all details."""
    query = update.callback_query
    await query.answer()
    parts   = query.data.split(":")
    page    = int(parts[2]) if len(parts) > 2 else 0
    per_pg  = 5
    skip    = page * per_pg

    total  = await settings_col.count_documents({"type": "pending_payment"})
    recs   = await settings_col.find(
        {"type": "pending_payment"},
    ).sort("created_at", -1).skip(skip).limit(per_pg).to_list(length=per_pg)

    if not recs:
        await safe_edit(query, "📊 *Purchase History*\n\nNo records found.",
                        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin:payment_settings")]]),
                        parse_mode=ParseMode.MARKDOWN)
        return

    lines = ["📊 *Purchase History* (page " + str(page+1) + "/" + str(max(1,(total+per_pg-1)//per_pg)) + ")\n━━━━━━━━━━━━━━━━\n\n"]
    for r in recs:
        tuid  = r.get("user_id", 0)
        udoc  = await users_col.find_one({"user_id": tuid})
        uname = ""
        fname = ""
        if udoc:
            uname = "@" + udoc.get("username","") if udoc.get("username") else ""
            fname = udoc.get("first_name","")
        display = (fname + " " + uname).strip() or "ID:" + str(tuid)

        plan_key = r.get("plan","?")
        if plan_key == "__domain__":
            plan_label = "🌐 Domain: " + r.get("full_domain", r.get("domain_name","?") + ".loca.lt")
        else:
            plan_label = "💎 " + PLANS.get(plan_key, {}).get("name", plan_key) + " Plan"

        amt    = r.get("amount", 0)
        method = r.get("method","?").upper()
        utr    = r.get("utr","—")
        status = r.get("status","?")
        status_icon = {"pending":"⏳","approved":"✅","rejected":"❌"}.get(status, "❓")

        created   = r.get("created_at")
        approved  = r.get("approved_at")
        created_s = created.strftime("%d %b %Y  %H:%M UTC") if created else "—"
        approved_s = approved.strftime("%d %b %Y  %H:%M UTC") if approved else "—"

        # Expiry from users_col
        expiry_s = "—"
        if udoc and plan_key != "__domain__":
            exp = udoc.get("plan_expiry")
            if exp:
                expiry_s = exp.strftime("%d %b %Y  %H:%M UTC")

        lines.append("👤 *" + escape_md(display) + "* (`" + str(tuid) + "`)\n")
        lines.append(plan_label + "\n")
        lines.append("💰 ₹" + str(amt) + "  •  " + method + "  •  " + status_icon + " " + status.upper() + "\n")
        lines.append("🔢 UTR: `" + escape_md(utr) + "`\n")
        lines.append("📅 Submitted: " + created_s + "\n")
        if approved_s != "—":
            lines.append("✅ Approved: " + approved_s + "\n")
        if expiry_s != "—":
            lines.append("🔚 Expiry: " + expiry_s + "\n")
        lines.append("\n")

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data="admin:purchase_history:" + str(page-1)))
    if skip + per_pg < total:
        nav.append(InlineKeyboardButton("Next ▶️", callback_data="admin:purchase_history:" + str(page+1)))
    kb_rows = []
    if nav:
        kb_rows.append(nav)
    kb_rows.append([InlineKeyboardButton("🔙 Payment Settings", callback_data="admin:payment_settings")])
    await safe_edit(query, "".join(lines), reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode=ParseMode.MARKDOWN)

async def cb_admin_set_upi_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != OWNER_ID:
        await query.answer("⛔ Owner only", show_alert=True)
        return
    context.user_data["awaiting_bsc"] = False
    await safe_edit(
        query,
        "💸 *Set UPI ID*\n\nSend your UPI ID (e.g. `name@upi`):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_panel")]]),
        parse_mode=ParseMode.MARKDOWN,
    )
    return PLAN_ADMIN_UPI_ID

async def admin_upi_id_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    val = update.message.text.strip()
    if context.user_data.get("awaiting_bsc"):
        if not val.startswith("0x") or len(val) != 42:
            await update.message.reply_text("❌ Invalid BSC address (0x + 42 chars). Try again:")
            return PLAN_ADMIN_UPI_ID
        await set_bot_setting("binance_bsc_address", val)
        await update.message.reply_text("✅ BSC address set to `" + escape_md(val) + "`", parse_mode=ParseMode.MARKDOWN)
    else:
        if not val or "@" not in val:
            await update.message.reply_text("❌ Invalid UPI ID (format: name@bank). Try again:")
            return PLAN_ADMIN_UPI_ID
        await set_bot_setting("upi_id", val)
        await update.message.reply_text("✅ UPI ID set to `" + escape_md(val) + "`", parse_mode=ParseMode.MARKDOWN)
    context.user_data.pop("awaiting_bsc", None)
    return ConversationHandler.END

async def cb_admin_set_upi_qr_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != OWNER_ID:
        await query.answer("⛔ Owner only", show_alert=True)
        return
    await safe_edit(
        query,
        "📷 *Upload UPI QR*\n\nSend a photo of your UPI QR code:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_panel")]]),
        parse_mode=ParseMode.MARKDOWN,
    )
    return PLAN_ADMIN_UPI_QR

async def admin_upi_qr_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.photo:
        await update.message.reply_text("❌ Send a photo (QR image):")
        return PLAN_ADMIN_UPI_QR
    await set_bot_setting("upi_qr_file_id", update.message.photo[-1].file_id)
    await update.message.reply_text("✅ UPI QR image saved!")
    return ConversationHandler.END

async def cb_admin_set_bsc_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.from_user.id != OWNER_ID:
        await query.answer("⛔ Owner only", show_alert=True)
        return
    settings = await get_bot_settings()
    current = settings.get("binance_bsc_address", "_Not set_")
    context.user_data["awaiting_bsc"] = True
    await safe_edit(
        query,
        "₿ *BSC USDT Address*\n\nCurrent: `" + escape_md(current) + "`\n\nSend new BEP20 address (0x...):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="admin_panel")]]),
        parse_mode=ParseMode.MARKDOWN,
    )
    return PLAN_ADMIN_UPI_ID



# ─────────────────────────────────────────────────────────────
# 📂 My Projects
# ─────────────────────────────────────────────────────────────

async def cb_my_projects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if await is_banned(uid):
        await safe_edit(query, "🚫 You are banned. Contact owner.")
        return

    if await is_maintenance_mode() and uid != OWNER_ID:
        await safe_edit(query, "🔧 Bot is under maintenance. Please try later.")
        return

    # OPT: projection — my_projects only needs name+status+pid+locked for buttons
    # FIX: added "locked":1 — without it p.get("locked") always returns None so
    #      locked projects always show 🔴 instead of the correct 🔒 icon.
    projects = await projects_col.find(
        {"user_id": uid}, {"name":1,"status":1,"pid":1,"run_command":1,"locked":1,"_id":0}
    ).to_list(length=100)
    if not projects:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_start")]])
        await safe_edit(query, "📂 *My Projects*\n\nYou have no projects yet.", reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        return

    kb_rows = []
    for p in projects:
        icon = "🟢" if p.get("status") == "running" else ("🔒" if p.get("locked") else "🔴")
        kb_rows.append([InlineKeyboardButton(f"{icon} {p['name']}", callback_data=f"proj:{p['name']}")])
    kb_rows.append([InlineKeyboardButton("🔙 Back", callback_data="back_start")])

    await safe_edit(query, "📂 *My Projects*\n\nSelect a project:", reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────────────────────
# 📊 My Status
# ─────────────────────────────────────────────────────────────

async def cb_my_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    if await is_banned(uid):
        await safe_edit(query, "🚫 You are banned. Contact owner.")
        return

    projects = await projects_col.find({"user_id": uid}).to_list(length=100)
    doc      = await get_user(uid)
    plan_key = await get_user_plan(uid)
    plan_inf = PLANS[plan_key]
    count    = len(projects)
    lim      = plan_inf["projects"]
    lim_lbl  = "∞" if lim == 0 else str(lim)
    plan_lbl = "💎 " + plan_inf["name"]

    if not projects:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🆕 New Project", callback_data="new_project"),
                                    InlineKeyboardButton("🔙 Back", callback_data="back_start")]])
        await safe_edit(
            query,
            "📊 *My Status*\n\n" + plan_lbl + " | 📁 0/" + lim_lbl + " projects\n\nNo projects yet.",
            reply_markup=kb,
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    lines = ["📊 *My Projects Status*\n"]
    lines.append(plan_lbl + "  •  📁 " + str(count) + "/" + lim_lbl + " projects\n")

    for i, p in enumerate(projects, 1):
        name   = p.get("name", "?")
        status = p.get("status", "stopped")
        cmd    = p.get("run_command") or "Not set"
        ar     = p.get("auto_restart", True)
        locked = p.get("locked", False)

        uptime_str = "—"
        if status == "running" and p.get("started_at"):
            try:
                started = p["started_at"]
                if started.tzinfo is None:
                    started = started.replace(tzinfo=timezone.utc)
                secs = (datetime.now(timezone.utc) - started).total_seconds()
                uptime_str = fmt_uptime(max(0, secs))
            except Exception:
                uptime_str = "—"

        exit_code = p.get("exit_code")

        if locked:
            status_line = f"🔒 Locked"
            extra_line  = f"   ├ ⚠️ Premium expired"
        elif status == "running":
            status_line = f"🟢 Running"
            extra_line  = f"   ├ ⏱ Uptime: `{uptime_str}`"
        elif exit_code is not None and exit_code != 0:
            status_line = f"🔴 Crashed"
            extra_line  = f"   ├ ⚠️ Exit Code: `{exit_code}`"
        else:
            status_line = f"🔴 Stopped"
            extra_line  = f"   ├ ⏱ Uptime: `—`"

        ar_line = "ON ✅" if ar else "OFF ❌"

        lines.append(
            f"{i}️⃣  *{escape_md(name)}*\n"
            f"   ├ {status_line}\n"
            f"{extra_line}\n"
            f"   ├ 🔁 Auto-Restart: {ar_line}\n"
            f"   └ 🖥 `{escape_md(cmd)}`\n"
        )

    text = "\n".join(lines)
    if len(text) > 3800:
        text = text[:3800] + "\n\n_...more projects, use /start_"

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔃 Refresh",    callback_data="my_status"),
         InlineKeyboardButton("📂 Projects",   callback_data="my_projects")],
        [InlineKeyboardButton("🔙 Back",       callback_data="back_start")],
    ])
    await safe_edit(query, text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────────────────────
# Project Dashboard
# ─────────────────────────────────────────────────────────────

def project_dashboard_text(p: dict) -> str:
    status  = p.get("status", "stopped")
    locked  = p.get("locked", False)
    if locked:
        icon = "🔒 Locked"
    elif status == "running":
        icon = "🟢 Running"
    else:
        icon = "🔴 Stopped"
    pid     = str(p.get("pid")) if p.get("pid") else "N/A"
    uptime  = "N/A"
    if status == "running" and p.get("started_at"):
        try:
            started = p["started_at"]
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - started).total_seconds()
            uptime  = fmt_uptime(max(0, elapsed))
        except Exception:
            uptime = "N/A"
    last_run = "Never"
    if p.get("last_run"):
        try:
            last_run = p["last_run"].strftime("%Y-%m-%d %H:%M UTC")
        except Exception:
            last_run = str(p["last_run"])
    exit_code = str(p.get("exit_code")) if p.get("exit_code") is not None else "None"
    run_cmd   = p.get("run_command") or "Not set"
    created   = "N/A"
    if p.get("created_date"):
        try:
            created = p["created_date"].strftime("%Y-%m-%d")
        except Exception:
            created = str(p["created_date"])

    ar_status = "✅ ON" if p.get("auto_restart", True) else "❌ OFF"
    locked_line = "\n🔒 Status: *LOCKED* (Premium expired)" if locked else ""
    github_line = f"\n🐙 GitHub: `{p['github_url'].replace('.git','')}`" if p.get("github_url") else ""
    crash_count = p.get("crash_count", 0)
    domain_line = f"\n🌐 Domain: `{p['custom_domain']}`" if p.get("custom_domain") else ""
    port_line   = f"\n🔌 Port: `{p['port']}`" if p.get("port") else ""
    html_url    = f"\n🖥 HTML URL: {BASE_URL}/site/{p['user_id']}/{p['name']}/" if p.get("project_type") == "html" else ""
    wh_line     = f"\n🔗 Webhook: ✅ Active" if p.get("webhook_secret") else ""

    return (
        f"📊 Project: *{p['name']}*\n\n"
        f"🔹 Status: {icon}{locked_line}\n"
        f"🔹 PID: `{pid}`\n"
        f"🔹 Uptime: `{uptime}`\n"
        f"🔹 Last Run: `{last_run}`\n"
        f"🔹 Exit Code: `{exit_code}`\n"
        f"🔹 Run Command: `{run_cmd}`\n"
        f"🔹 Auto-Restart: {ar_status}\n"
        f"🔹 Crashes: `{crash_count}`\n"
        f"📅 Created: `{created}`"
        f"{github_line}"
        f"{domain_line}"
        f"{port_line}"
        f"{html_url}"
        f"{wh_line}"
    )

def project_dashboard_kb(user_id: int, project_name: str, auto_restart: bool = True,
                          is_running: bool = False, is_locked: bool = False,
                          user_premium: bool = False) -> InlineKeyboardMarkup:
    pn = project_name

    def _lock(label: str, cb: str) -> InlineKeyboardButton:
        if user_premium:
            return InlineKeyboardButton(label, callback_data=cb)
        return InlineKeyboardButton(f"🔒 {label}", callback_data="premium_lock")

    if is_locked:
        row1 = [InlineKeyboardButton("🔒 Project Locked", callback_data=f"locked_info:{pn}")]
    elif is_running:
        row1 = [
            InlineKeyboardButton("⏹ Stop",    callback_data=f"stop:{pn}"),
            InlineKeyboardButton("🔄 Restart", callback_data=f"restart:{pn}"),
            InlineKeyboardButton("📋 Logs",    callback_data=f"logs:{pn}"),
        ]
    else:
        row1 = [
            InlineKeyboardButton("▶️ Run",     callback_data=f"run:{pn}"),
            InlineKeyboardButton("🔄 Restart", callback_data=f"restart:{pn}"),
            InlineKeyboardButton("📋 Logs",    callback_data=f"logs:{pn}"),
        ]

    ar_label = ("⏰ Auto-Restart: ✅" if auto_restart else "⏰ Auto-Restart: ❌")

    rows = [
        row1,
        [
            _lock("📺 Live Logs", f"live_logs:{pn}"),
            _lock("📊 Uptime",    f"uptime:{pn}"),
            InlineKeyboardButton("🔃 Refresh", callback_data=f"proj:{pn}"),
        ],
        [
            InlineKeyboardButton("✏️ Edit CMD", callback_data=f"editcmd:{pn}"),
            InlineKeyboardButton("📁 Files",    callback_data=f"filemgr:{pn}"),
            _lock("🔁 Clone",                   f"clone:{pn}"),
        ],
        [
            _lock(ar_label,        f"toggle_ar:{pn}"),
            InlineKeyboardButton("🔐 Env Vars", callback_data=f"envvars:{pn}"),
        ],
        [
            InlineKeyboardButton("🐙 GitHub",    callback_data=f"github:{pn}"),
            _lock("⏰ Cron Jobs",  f"cron:{pn}"),
            _lock("🔔 Notifs",     f"notif:{pn}"),
        ],
        [
            _lock("🌐 Domain",   f"domain:{pn}"),
            _lock("🔌 Port",     f"portmgmt:{pn}"),
            _lock("🔗 Webhook",  f"wh_setup:{pn}"),
        ],
        [
            InlineKeyboardButton("📦 Reinstall Requirements", callback_data=f"reinstall_reqs:{pn}"),
        ],
        [
            InlineKeyboardButton("🗑 Delete", callback_data=f"delete:{pn}"),
            InlineKeyboardButton("🔙 Back",   callback_data="my_projects"),
        ],
    ]
    return InlineKeyboardMarkup(rows)

async def cb_project_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    name = query.data.split(":", 1)[1]

    if await is_banned(uid):
        await safe_edit(query, "🚫 You are banned. Contact owner.")
        return

    p = await get_project(uid, name)
    if not p:
        await safe_edit(query, "❌ Project not found.", parse_mode=ParseMode.MARKDOWN)
        return

    # ── Real-time PID sync: correct DB if it disagrees with actual process state ──
    db_status = p.get("status", "stopped")
    pid        = p.get("pid")
    cs_key     = f"{uid}:{name}"
    proc_obj   = context_store.get(cs_key)

    # Check 1: context_store has actual asyncio Process — most reliable
    # Check 2: psutil.pid_exists — fast but can return True for recycled PIDs
    # Check 3: find_project_process — scans all procs by CWD, safe on VPS/RDP
    if proc_obj is not None:
        actual_alive = proc_obj.returncode is None
        real_pid = pid
    elif pid and psutil.pid_exists(pid):
        actual_alive = True
        real_pid = pid
    else:
        # PID stale or missing — scan ALL processes by project directory
        pdir = project_dir(uid, name)
        found, real_pid = find_project_process(pdir)
        actual_alive = found
        if found and real_pid and real_pid != pid:
            # Process alive with different PID (VPS/RDP scenario) — update DB
            await projects_col.update_one(
                {"user_id": uid, "name": name},
                {"$set": {"pid": real_pid}},
            )
            p["pid"] = real_pid

    if db_status == "running" and not actual_alive:
        # FIX: grace period — after bot restart, auto_restart_on_startup runs at ~5s
        # and restarts all "running" projects. If user opens dashboard BEFORE that
        # completes, context_store is empty and old PID is dead → we must NOT mark
        # the project as stopped or auto_restart_on_startup will never find it.
        # Only mark as stopped if bot has been running for >30 seconds (grace period).
        if time.time() - BOT_START_TIME > 30:
            await projects_col.update_one(
                {"user_id": uid, "name": name},
                {"$set": {"status": "stopped", "pid": None}},
            )
            p["status"] = "stopped"
            p["pid"]    = None
        # else: bot just started — keep showing "running" optimistically
        # auto_restart_on_startup will bring the project back within 5-30 seconds
    elif db_status != "running" and actual_alive:
        # Process is alive but DB says stopped (orphan from bot restart)
        await projects_col.update_one(
            {"user_id": uid, "name": name},
            {"$set": {"status": "running"}},
        )
        p["status"] = "running"

    user_premium = await is_premium(uid) or uid == OWNER_ID
    await safe_edit(
        query,
        project_dashboard_text(p),
        reply_markup=project_dashboard_kb(uid, name, p.get("auto_restart", True),
                                          p.get("status") == "running", p.get("locked", False),
                                          user_premium=user_premium),
        parse_mode=ParseMode.MARKDOWN,
    )

async def cb_locked_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer(
        "🔒 This project is locked because your Premium expired. Upgrade to Premium to unlock!",
        show_alert=True,
    )

# ─────────────────────────────────────────────────────────────
# 📦 Reinstall Requirements
# ─────────────────────────────────────────────────────────────

async def cb_reinstall_reqs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    name = query.data.split(":", 1)[1]

    if await is_banned(uid):
        await safe_edit(query, "🚫 You are banned. Contact owner.")
        return

    p = await get_project(uid, name)
    if not p:
        await safe_edit(query, "❌ Project not found.", parse_mode=ParseMode.MARKDOWN)
        return

    pdir     = project_dir(uid, name)
    req_path = os.path.join(pdir, "requirements.txt")
    pkg_json = os.path.join(pdir, "package.json")
    venv_dir = os.path.join(pdir, "venv")
    pip_path = os.path.join(venv_dir, "bin", "pip")

    back_kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"proj:{name}")]])

    if os.path.exists(pkg_json) and not os.path.exists(req_path):
        progress = LiveProgress(query.message, title=f"Installing npm packages — {name}")
        await progress.start("npm install starting...")
        progress.run_in_background(estimated_seconds=90, status="npm install (downloading + linking)")
        try:
            proc_n = await asyncio.wait_for(
                create_subprocess_exec("npm", "install", "--no-audit", "--no-fund", "--silent",
                                       stdout=PIPE, stderr=PIPE, cwd=pdir),
                timeout=600,
            )
            stdout_n, stderr_n = await asyncio.wait_for(proc_n.communicate(), timeout=600)
            if proc_n.returncode == 0:
                await progress.stop(success=True, final_text=f"npm packages reinstalled for {name}")
            else:
                err = (stderr_n or b"").decode()[:400]
                await progress.stop(success=False, final_text=f"```\n{err}\n```")
        except asyncio.TimeoutError:
            await progress.stop(success=False, final_text="npm install timed out")
        except FileNotFoundError:
            await progress.stop(success=False, final_text="npm not installed on host.")
        except Exception as e:
            await progress.stop(success=False, final_text=f"npm error: {escape_md(str(e))}")

        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Restart Project", callback_data=f"restart:{name}")],
            [InlineKeyboardButton("🔙 Back", callback_data=f"proj:{name}")],
        ])
        await query.message.reply_text("Choose next:", reply_markup=kb)
        return

    if not os.path.exists(req_path):
        await safe_edit(
            query,
            f"⚠️ *No requirements.txt or package.json found* in `{name}`.\n\nUpload one via 📁 Files first.",
            reply_markup=back_kb,
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    results = []

    if not os.path.exists(pip_path):
        progress = LiveProgress(query.message, title=f"Creating venv — {name}")
        await progress.start("python -m venv ...")
        progress.run_in_background(estimated_seconds=20, status="Building virtual environment")
        try:
            proc = await asyncio.wait_for(
                create_subprocess_exec(sys.executable, "-m", "venv", venv_dir,
                                       stdout=PIPE, stderr=PIPE),
                timeout=120,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            if proc.returncode == 0:
                await progress.stop(success=True, final_text="Virtual environment created")
                results.append("✅ Virtual environment created")
            else:
                err = stderr.decode()[:200]
                await progress.stop(success=False, final_text=err)
                results.append(f"❌ venv failed: {err}")
                await query.message.reply_text(
                    f"📦 *Reinstall failed*\n\n" + "\n".join(results),
                    reply_markup=back_kb, parse_mode=ParseMode.MARKDOWN,
                )
                return
        except Exception as e:
            await progress.stop(success=False, final_text=str(e))
            results.append(f"❌ venv error: {e}")
            await query.message.reply_text(
                f"📦 *Reinstall failed*\n\n" + "\n".join(results),
                reply_markup=back_kb, parse_mode=ParseMode.MARKDOWN,
            )
            return

    # OPT: skip "pip --upgrade pip" — downloads pip on every reinstall unnecessarily
    # OPT: removed --upgrade flag — forces re-download of all packages even if installed
    req_progress = LiveProgress(query.message, title=f"Installing requirements — {name}")
    await req_progress.start("pip install -r requirements.txt")
    req_progress.run_in_background(estimated_seconds=120, status="Resolving packages...")
    try:
        proc = await asyncio.wait_for(
            create_subprocess_exec(pip_path, "install", "-q", "-r", req_path,
                                   stdout=PIPE, stderr=PIPE, cwd=pdir),
            timeout=600,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=600)
        if proc.returncode == 0:
            await req_progress.stop(success=True, final_text="Requirements installed")
            results.append("✅ Requirements installed successfully")
        else:
            err = stderr.decode()[:400] if stderr else "unknown error"
            await req_progress.stop(success=False, final_text=f"```\n{err}\n```")
            results.append(f"❌ pip install failed:\n```\n{err}\n```")
            await query.message.reply_text(
                f"📦 *Reinstall failed for {name}*\n\n" + "\n".join(results),
                reply_markup=back_kb, parse_mode=ParseMode.MARKDOWN,
            )
            return
    except asyncio.TimeoutError:
        await req_progress.stop(success=False, final_text="pip install timed out")
        results.append("❌ pip install timed out")
        await query.message.reply_text(
            f"📦 *Reinstall failed for {name}*\n\n" + "\n".join(results),
            reply_markup=back_kb, parse_mode=ParseMode.MARKDOWN,
        )
        return
    except Exception as e:
        await req_progress.stop(success=False, final_text=str(e))
        results.append(f"❌ pip error: {e}")
        await query.message.reply_text(
            f"📦 *Reinstall failed for {name}*\n\n" + "\n".join(results),
            reply_markup=back_kb, parse_mode=ParseMode.MARKDOWN,
        )
        return

    # FIX: max(0, ...) to prevent negative package count
    try:
        proc2 = await asyncio.wait_for(
            create_subprocess_exec(pip_path, "list", stdout=PIPE, stderr=PIPE),
            timeout=30,
        )
        out2, _ = await asyncio.wait_for(proc2.communicate(), timeout=30)
        pkg_count = max(0, len(out2.decode().strip().splitlines()) - 2)
        results.append(f"✅ {pkg_count} packages available")
    except Exception:
        results.append("⚠️ Could not verify packages")

    is_running = p.get("status") == "running"
    note = ""
    if is_running:
        note = "\n\nℹ️ Project is running. Click 🔄 *Restart* to apply new packages."

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Restart Project", callback_data=f"restart:{name}")],
        [InlineKeyboardButton("🔙 Back", callback_data=f"proj:{name}")],
    ])

    await safe_edit(
        query,
        f"🎉 *Requirements reinstalled for {name}!*\n\n" + "\n".join(results) + note,
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN,
    )

# ─────────────────────────────────────────────────────────────
# ▶️ Run project
# ─────────────────────────────────────────────────────────────

context_store: dict = {}

# ─────────────────────────────────────────────────────────────
# 🔍 Process Discovery Helper (VPS/RDP PID-safe detection)
# ─────────────────────────────────────────────────────────────

def find_project_process(project_path: str) -> "tuple[bool, int | None]":
    """
    Scan ALL running OS processes to find one whose working directory
    matches project_path.  Handles stale PIDs on VPS/RDP where the PID
    can change after venv activation, shell wrapping, or bot restart.

    Returns: (is_running: bool, pid: int | None)
    """
    try:
        real_path = os.path.realpath(project_path)
        for proc in psutil.process_iter(["pid", "cwd", "cmdline", "status"]):
            try:
                info = proc.info
                if info.get("status") in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD):
                    continue
                # CWD check — most reliable
                proc_cwd = info.get("cwd") or ""
                if proc_cwd:
                    try:
                        if os.path.realpath(proc_cwd) == real_path:
                            return True, info["pid"]
                    except Exception:
                        pass
                # cmdline fallback — handles shell-wrapped invocations
                for arg in (info.get("cmdline") or []):
                    if real_path in str(arg):
                        return True, info["pid"]
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
    except Exception:
        pass
    return False, None


async def start_project_process(uid: int, name: str) -> dict:
    """Start project subprocess. Returns updated project dict."""
    p   = await get_project(uid, name)
    pdir = project_dir(uid, name)
    cmd  = p.get("run_command") or "python main.py"

    log_path = os.path.join(pdir, "output.log")
    rotate_log_if_needed(log_path)  # RAM optimization: rotate large logs

    venv_python = os.path.join(pdir, "venv", "bin", "python")
    if not os.path.exists(venv_python):
        venv_python = sys.executable

    import shlex
    parts = shlex.split(cmd)
    if parts and parts[0] in ("python", "python3"):
        parts[0] = venv_python

    logger.info(f"Starting process: {' '.join(parts)} in {pdir}")

    import copy
    proc_env = copy.copy(os.environ)
    env_path = os.path.join(pdir, ".env")
    if os.path.exists(env_path):
        with open(env_path, "r") as ef:
            for eline in ef:
                eline = eline.strip()
                if eline and not eline.startswith("#") and "=" in eline:
                    ekey, _, evalue = eline.partition("=")
                    proc_env[ekey.strip()] = evalue.strip()
        logger.info(f"Loaded .env for project {name}")

    log_fd = open(log_path, "a")
    proc = await create_subprocess_exec(
        *parts,
        stdout=log_fd,
        stderr=log_fd,
        cwd=pdir,
        env=proc_env,
        start_new_session=True,
    )
    log_fd.close()

    logger.info(f"Process started with PID {proc.pid}")

    now = datetime.now(timezone.utc)
    await projects_col.update_one(
        {"user_id": uid, "name": name},
        {"$set": {
            "status":        "running",
            "pid":           proc.pid,
            "ppid":          proc.pid,       # parent PID snapshot (for VPS tracking)
            "project_dir":   pdir,           # saved so we can find by CWD on VPS
            "run_command":   cmd,            # keep in sync
            "started_at":    now,
            "last_run":      now,
            "exit_code":     None,
            "admin_stopped": False,
        }},
    )
    # Clean up old entries from context_store to prevent memory leak
    key = f"{uid}:{name}"
    context_store[key] = proc

    updated = await get_project(uid, name)
    logger.info(f"DB updated - status: {updated.get('status')}, pid: {updated.get('pid')}")
    return updated

async def cb_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    name = query.data.split(":", 1)[1]

    if await is_banned(uid):
        await safe_edit(query, "🚫 You are banned. Contact owner.")
        return

    if await is_maintenance_mode() and uid != OWNER_ID:
        await safe_edit(query, "🔧 Bot is under maintenance. Only owner can use the bot.")
        return

    user_premium = await is_premium(uid)

    # Bot lock check: free users can't run projects
    if await is_bot_locked() and not user_premium and uid != OWNER_ID:
        await safe_edit(
            query,
            "🔒 *Bot is locked.*\n\nOnly Premium users can run projects while the bot is locked.\nContact owner to upgrade!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"proj:{name}")]]),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    p = await get_project(uid, name)
    if not p:
        await safe_edit(query, "❌ Project not found.", parse_mode=ParseMode.MARKDOWN)
        return

    # Locked project check
    if p.get("locked"):
        await safe_edit(
            query,
            "🔒 *Project is locked.*\n\nYour premium expired. Upgrade to Premium to unlock all projects!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"proj:{name}")]]),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if p.get("admin_stopped"):
        await safe_edit(
            query,
            "⚠️ Your project was stopped by admin. Contact owner.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"proj:{name}")]]),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if p.get("status") == "running" and p.get("pid"):
        _run_pid = p["pid"]
        if psutil.pid_exists(_run_pid):
            await safe_edit(query, "▶️ Project is already running.", parse_mode=ParseMode.MARKDOWN)
            return
        # PID stale — check by project directory (VPS/RDP safety)
        _pdir_check = project_dir(uid, name)
        _found, _real = find_project_process(_pdir_check)
        if _found:
            if _real and _real != _run_pid:
                await projects_col.update_one({"user_id": uid, "name": name}, {"$set": {"pid": _real}})
            await safe_edit(query, "▶️ Project is already running.", parse_mode=ParseMode.MARKDOWN)
            return

    if not p.get("run_command"):
        await safe_edit(
            query,
            "❌ No run command set. Use ✏️ Edit CMD first.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"proj:{name}")]]),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await safe_edit(query, f"▶️ Starting {name}...")

    try:
        # FIX: install/refresh requirements before launching — mirrors the admin-run
        # fix. Without this, a project whose requirements.txt was edited (or whose
        # venv was never built) crashes immediately after start with a missing-module
        # error, then sits "offline" until process_monitor's next tick auto-restarts
        # it (still broken) — exactly the "offline, then online after a while" symptom.
        req_ok, req_msg = await _install_requirements_for_project(uid, name)
        if not req_ok:
            await safe_edit(query, f"❌ Failed to install requirements:\n{req_msg[:300]}")
            return

        updated = await start_project_process(uid, name)
        logger.info(f"Started project {name} for user {uid}, PID: {updated.get('pid')}")
        await safe_edit(
            query,
            project_dashboard_text(updated),
            reply_markup=project_dashboard_kb(uid, name, updated.get("auto_restart", True),
                                              updated.get("status") == "running",
                                              updated.get("locked", False),
                                              user_premium=user_premium),
        )
    except Exception as e:
        logger.error(f"Failed to start project {name}: {e}")
        await safe_edit(query, f"❌ Failed to start: {str(e)[:300]}")

# ─────────────────────────────────────────────────────────────
# ⏹ Stop project
# ─────────────────────────────────────────────────────────────

async def kill_project(uid: int, name: str):
    p = await get_project(uid, name)
    # Track uptime_total before killing
    uptime_update = {}
    if p and p.get("status") == "running" and p.get("started_at"):
        try:
            started = p["started_at"]
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            session_secs = (datetime.now(timezone.utc) - started).total_seconds()
            new_total = p.get("uptime_total", 0.0) + max(0.0, session_secs)
            uptime_update["uptime_total"] = new_total
        except Exception:
            pass

    if p and p.get("pid"):
        try:
            proc = psutil.Process(p["pid"])
            for child in proc.children(recursive=True):
                try:
                    child.kill()
                except Exception:
                    pass
            proc.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass

    update_fields = {"status": "stopped", "pid": None, **uptime_update}
    await projects_col.update_one(
        {"user_id": uid, "name": name},
        {"$set": update_fields},
    )
    # Clean up context_store entry
    context_store.pop(f"{uid}:{name}", None)

async def cb_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    name = query.data.split(":", 1)[1]

    if await is_banned(uid):
        await safe_edit(query, "🚫 You are banned. Contact owner.")
        return

    p = await get_project(uid, name)
    if not p:
        await safe_edit(query, "❌ Project not found.")
        return

    if p.get("status") != "running":
        await safe_edit(query, "⏹ Project is not running.", parse_mode=ParseMode.MARKDOWN)
        return

    await safe_edit(query, f"⏹ Stopping {name}...")
    await kill_project(uid, name)

    p = await get_project(uid, name)
    _up = await is_premium(uid) or uid == OWNER_ID
    await safe_edit(
        query,
        project_dashboard_text(p),
        reply_markup=project_dashboard_kb(uid, name, p.get("auto_restart", True),
                                          p.get("status") == "running", p.get("locked", False),
                                          user_premium=_up),
    )

# ─────────────────────────────────────────────────────────────
# 🔄 Restart
# ─────────────────────────────────────────────────────────────

async def cb_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    name = query.data.split(":", 1)[1]

    if await is_banned(uid):
        await safe_edit(query, "🚫 You are banned. Contact owner.")
        return

    if await is_maintenance_mode() and uid != OWNER_ID:
        await safe_edit(query, "🔧 Bot is under maintenance. Only owner can use the bot.")
        return

    user_premium = await is_premium(uid)
    if await is_bot_locked() and not user_premium and uid != OWNER_ID:
        await safe_edit(
            query,
            "🔒 *Bot is locked.* Only Premium users can restart projects.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"proj:{name}")]]),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    p = await get_project(uid, name)
    if not p:
        await safe_edit(query, "❌ Project not found.", parse_mode=ParseMode.MARKDOWN)
        return

    if p.get("locked"):
        await safe_edit(query, "🔒 Project is locked. Upgrade to Premium to unlock.", parse_mode=ParseMode.MARKDOWN)
        return

    if p.get("admin_stopped"):
        await safe_edit(query, "⚠️ Your project was stopped by admin. Contact owner.", parse_mode=ParseMode.MARKDOWN)
        return

    await safe_edit(query, f"🔄 Restarting *{escape_md(name)}*...", parse_mode=ParseMode.MARKDOWN)
    await kill_project(uid, name)
    await asyncio.sleep(1)

    try:
        updated = await start_project_process(uid, name)
        await safe_edit(
            query,
            project_dashboard_text(updated),
            reply_markup=project_dashboard_kb(uid, name, updated.get("auto_restart", True),
                                              updated.get("status") == "running",
                                              updated.get("locked", False),
                                              user_premium=await is_premium(uid) or uid == OWNER_ID),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        await safe_edit(query, f"❌ Restart failed: {escape_md(str(e))}", parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────────────────────
# 📋 Logs
# ─────────────────────────────────────────────────────────────

async def cb_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    name = query.data.split(":", 1)[1]

    if await is_banned(uid):
        await safe_edit(query, "🚫 You are banned. Contact owner.")
        return

    log_path = os.path.join(project_dir(uid, name), "output.log")
    if not os.path.exists(log_path):
        lines = "No logs yet."
    else:
        with open(log_path, "r", errors="replace") as f:
            all_lines = f.readlines()
        lines = "".join(all_lines[-50:]) or "Log file is empty."

    if len(lines) > 3500:
        lines = "...(truncated)...\n" + lines[-3500:]

    text = f"📋 *Logs — {escape_md(name)}*\n\n```\n{escape_md(lines)}\n```"
    kb   = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"proj:{name}")]])
    await safe_edit(query, text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────────────────────
# ✏️ Edit Run CMD
# ─────────────────────────────────────────────────────────────

async def cb_editcmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    name = query.data.split(":", 1)[1]
    context.user_data["editcmd_project"] = name
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"proj:{name}")]])
    await safe_edit(
        query,
        f"✏️ *Edit Run Command for {escape_md(name)}*\n\nSend the new run command.\nExample: `python main.py`",
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN,
    )
    return EDIT_RUN_CMD

async def editcmd_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    cmd  = update.message.text.strip()
    name = context.user_data.get("editcmd_project")

    await projects_col.update_one(
        {"user_id": uid, "name": name},
        {"$set": {"run_command": cmd}},
    )
    p   = await get_project(uid, name)
    _up = await is_premium(uid) or uid == OWNER_ID
    kb  = project_dashboard_kb(uid, name, p.get("auto_restart", True),
                               p.get("status") == "running", p.get("locked", False),
                               user_premium=_up)
    await update.message.reply_text(
        f"✅ Run command updated!\n\n" + project_dashboard_text(p),
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END

# ─────────────────────────────────────────────────────────────
# 📁 File Manager
# ─────────────────────────────────────────────────────────────

async def cb_filemgr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    name = query.data.split(":", 1)[1]

    if await is_banned(uid):
        await safe_edit(query, "🚫 You are banned. Contact owner.")
        return

    token    = secrets.token_urlsafe(6)
    now      = datetime.now(timezone.utc)
    expires  = now.timestamp() + 600

    from file_manager import token_store
    token_store[token] = {
        "user_id":      uid,
        "project_name": name,
        "project_dir":  project_dir(uid, name),
        "expires_at":   expires,
        "session_total": 600,   # FIX: store actual session duration so file manager timer ring is correct
    }
    await tokens_col.insert_one({
        "token":        token,
        "user_id":      uid,
        "project_name": name,
        "created_at":   now,
        "expires_at":   datetime.fromtimestamp(expires, tz=timezone.utc),
    })

    url = f"{BASE_URL}/fm/{token}/"
    kb  = InlineKeyboardMarkup([
        [InlineKeyboardButton("🌐 Open File Manager", url=url)],
        [InlineKeyboardButton("🔙 Back",              callback_data=f"proj:{name}")],
    ])
    await safe_edit(
        query,
        f"📁 *File Manager*\n\nYour session link (valid 10 min):\n`{escape_md(url)}`",
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN,
    )

# ─────────────────────────────────────────────────────────────
# 🗑 Delete project
# ─────────────────────────────────────────────────────────────

async def cb_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    name = query.data.split(":", 1)[1]
    kb   = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes, Delete", callback_data=f"delete_yes:{name}"),
            InlineKeyboardButton("❌ Cancel",       callback_data=f"proj:{name}"),
        ],
    ])
    await safe_edit(
        query,
        f"🗑 *Delete {escape_md(name)}?*\n\nThis cannot be undone.",
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN,
    )

async def cb_delete_yes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    name = query.data.split(":", 1)[1]

    await kill_project(uid, name)
    pdir = project_dir(uid, name)
    if os.path.exists(pdir):
        # OPT: run heavy rmtree in thread pool — avoids blocking event loop on large venvs
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: shutil.rmtree(pdir, ignore_errors=True))
    await projects_col.delete_one({"user_id": uid, "name": name})
    for col in all_backup_cols():
        try:
            await col.delete_many({"type": "file_backup", "user_id": uid, "project_name": name})
        except Exception as e:
            logger.warning(f"Backup cleanup failed on one DB: {e}")

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 My Projects", callback_data="my_projects")]])
    await safe_edit(query, f"✅ Project *{escape_md(name)}* deleted.", reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────────────────────
# 🆕 New Project — ConversationHandler
# ─────────────────────────────────────────────────────────────

async def cb_new_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    if await is_banned(uid):
        await safe_edit(query, "🚫 You are banned. Contact owner.")
        return ConversationHandler.END

    if await is_maintenance_mode() and uid != OWNER_ID:
        await safe_edit(query, "🔧 Bot is under maintenance. Please try later.")
        return ConversationHandler.END

    plan_key     = await get_user_plan(uid)
    user_premium = plan_key in ("premium", "ultimate")

    # Bot lock: free users can't create new projects
    if await is_bot_locked() and plan_key == "free" and uid != OWNER_ID:
        await safe_edit(
            query,
            "🔒 *Bot is locked.*\n\nOnly paid plan users can create new projects while the bot is locked.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💎 Upgrade Plan", callback_data="plans")],
                [InlineKeyboardButton("🔙 Back",         callback_data="back_start")],
            ]),
            parse_mode=ParseMode.MARKDOWN,
        )
        return ConversationHandler.END

    count = await project_count(uid)
    limit = PLANS[plan_key]["projects"]

    if limit > 0 and count >= limit:
        pname = PLANS[plan_key]["name"]
        await safe_edit(
            query,
            "❌ *Project limit reached* (" + str(count) + "/" + str(limit) + ")\n\n*" + pname + "* plan allows " + str(limit) + " project(s).\nUpgrade for more!",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("💎 Upgrade Plan", callback_data="plans")],
                [InlineKeyboardButton("🔙 Back",         callback_data="back_start")],
            ]),
            parse_mode=ParseMode.MARKDOWN,
        )
        return ConversationHandler.END

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="back_start")]])
    await safe_edit(
        query,
        "📝 *New Project*\n\nEnter a project name:\n(alphanumeric + underscore, max 20 chars)",
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN,
    )
    return NEW_PROJECT_NAME

async def new_project_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    name = update.message.text.strip()

    if not re.match(r"^[a-zA-Z0-9_]{1,20}$", name):
        await update.message.reply_text(
            "❌ Invalid name. Use only letters, numbers, underscore (max 20). Try again:",
            parse_mode=ParseMode.MARKDOWN,
        )
        return NEW_PROJECT_NAME

    existing = await get_project(uid, name)
    if existing:
        await update.message.reply_text(
            f"❌ You already have a project named *{escape_md(name)}*. Choose another:",
            parse_mode=ParseMode.MARKDOWN,
        )
        return NEW_PROJECT_NAME

    context.user_data["new_project_name"]  = name
    context.user_data["new_project_files"] = []
    context.user_data["new_project_github"] = None

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Done Uploading", callback_data="upload_done")],
        [InlineKeyboardButton("❌ Cancel",          callback_data="back_start")],
    ])
    await update.message.reply_text(
        f"📁 *Project: {escape_md(name)}*\n\n"
        f"Choose how to upload your project:\n\n"
        f"*Option 1 — File Upload*\n"
        f"Send your `.py` files one by one, or a single `.zip` archive.\n"
        f"Click *Done Uploading* when finished.\n\n"
        f"*Option 2 — GitHub URL*\n"
        f"Send a GitHub repo link and the bot will clone it directly:\n"
        f"`https://github.com/username/repo`",
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN,
    )
    return NEW_PROJECT_FILES

async def new_project_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid       = update.effective_user.id
    name      = context.user_data.get("new_project_name")
    pdir      = project_dir(uid, name)
    os.makedirs(pdir, exist_ok=True)

    doc       = update.message.document
    file_name = doc.file_name or "file"
    tg_file   = await context.bot.get_file(doc.file_id)
    dest      = os.path.join(pdir, file_name)
    await tg_file.download_to_drive(dest)

    if file_name.lower().endswith(".zip"):
        try:
            with zipfile.ZipFile(dest, "r") as z:
                names = z.namelist()
                # Detect single top-level directory (strip it)
                # FIX: also check for root-level files to avoid false positive
                top_dirs = set()
                has_root_files = False
                for n in names:
                    if n.endswith("/"):
                        continue
                    parts = n.split("/")
                    if len(parts) > 1:
                        top_dirs.add(parts[0])
                    else:
                        has_root_files = True
                has_single_root = len(top_dirs) == 1 and not has_root_files

                z.extractall(pdir)

                if has_single_root:
                    root_dir = list(top_dirs)[0]
                    root_path = os.path.join(pdir, root_dir)
                    if os.path.isdir(root_path):
                        for item in os.listdir(root_path):
                            src = os.path.join(root_path, item)
                            dst = os.path.join(pdir, item)
                            if os.path.exists(dst):
                                if os.path.isdir(dst):
                                    shutil.rmtree(dst)
                                else:
                                    os.remove(dst)
                            shutil.move(src, dst)
                        try:
                            shutil.rmtree(root_path)
                        except Exception:
                            pass

            extracted_count = len([n for n in names if not n.endswith("/")])
            await update.message.reply_text(
                f"📦 `{escape_md(file_name)}` extracted ({extracted_count} files).\n"
                f"Send more files or click *Done Uploading*.",
                parse_mode=ParseMode.MARKDOWN,
            )
        except zipfile.BadZipFile:
            try: os.remove(dest)
            except Exception: pass
            await update.message.reply_text(
                f"❌ `{escape_md(file_name)}` corrupt zip. Try again.",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            logger.error(f"Zip extract error for {file_name}: {e}")
            await update.message.reply_text(
                f"❌ Extract failed: `{escape_md(str(e))[:200]}`",
                parse_mode=ParseMode.MARKDOWN,
            )
    else:
        await update.message.reply_text(
            f"✅ `{escape_md(file_name)}` uploaded. Send more or click Done.",
            parse_mode=ParseMode.MARKDOWN,
        )

    return NEW_PROJECT_FILES

async def new_project_github_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle a GitHub URL sent during new project creation — clone and finalize."""
    uid  = update.effective_user.id
    name = context.user_data.get("new_project_name")
    text = update.message.text.strip()

    # Validate it looks like a GitHub/GitLab URL
    if not re.match(r"https?://(github\.com|gitlab\.com|bitbucket\.org)/\S+", text):
        await update.message.reply_text(
            "❌ That doesn't look like a valid GitHub URL.\n\n"
            "Send a link like:\n`https://github.com/username/repo`\n\n"
            "Or send your project files and click *Done Uploading*.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return NEW_PROJECT_FILES

    github_url = text.rstrip("/")
    if not github_url.endswith(".git"):
        github_url += ".git"

    pdir = project_dir(uid, name)
    os.makedirs(pdir, exist_ok=True)

    # FIX: removed duplicate LiveProgress assignment (the first one was immediately
    #      overwritten by the second, wasting a message object).
    status_msg = await update.message.reply_text(
        f"🐙 Cloning from GitHub...\n`{text}`",
        parse_mode=ParseMode.MARKDOWN,
    )
    progress = LiveProgress(status_msg, title=f"GitHub Clone — {name}")
    await progress.start("Connecting to GitHub...")
    progress.run_in_background(estimated_seconds=90, status="Cloning repository...")

    import tempfile, shutil as _shutil
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            proc = await asyncio.wait_for(
                create_subprocess_exec(
                    "git", "clone", "--depth=1", "--single-branch", "--no-tags",
                    github_url, tmpdir,
                    stdout=PIPE, stderr=PIPE,
                ),
                timeout=180,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
            if proc.returncode != 0:
                await progress.stop(
                    success=False,
                    final_text=f"Clone failed:\n`{stderr.decode()[:300]}`",
                )
                return NEW_PROJECT_FILES

            # Copy files from tmpdir → pdir (skip .git)
            for item in os.listdir(tmpdir):
                if item == ".git":
                    continue
                src = os.path.join(tmpdir, item)
                dst = os.path.join(pdir, item)
                if os.path.exists(dst):
                    _shutil.rmtree(dst) if os.path.isdir(dst) else os.remove(dst)
                _shutil.move(src, dst)

            # Copy .git for future pulls
            git_src = os.path.join(tmpdir, ".git")
            if os.path.exists(git_src):
                _shutil.copytree(git_src, os.path.join(pdir, ".git"))

        await progress.stop(success=True, final_text=f"Repository cloned successfully!")
    except asyncio.TimeoutError:
        await progress.stop(success=False, final_text="Clone timed out (180s). Try again.")
        return NEW_PROJECT_FILES
    except Exception as e:
        await progress.stop(success=False, final_text=f"Error: {str(e)[:200]}")
        return NEW_PROJECT_FILES

    # Save github_url so _finalize_new_project stores it in DB
    context.user_data["new_project_github"] = github_url
    return await _finalize_new_project(update, context, via_message=True)


async def new_project_done_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _finalize_new_project(update, context, via_message=True)

async def new_project_done_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    return await _finalize_new_project(update, context, via_message=False)

async def _finalize_new_project(update: Update, context: ContextTypes.DEFAULT_TYPE, via_message: bool):
    uid  = update.effective_user.id
    name = context.user_data.get("new_project_name")
    pdir = project_dir(uid, name)

    status_msg = await (update.message or update.callback_query.message).reply_text(
        f"⚙️ *Setting up {escape_md(name)}*\n\n⏳ Initializing project...",
        parse_mode=ParseMode.MARKDOWN,
    )

    results = []

    # Step 1: Create venv
    venv_progress = LiveProgress(status_msg, title=f"Setup — {name} (venv)")
    await venv_progress.start("python -m venv venv")
    venv_progress.run_in_background(estimated_seconds=20, status="Creating virtual environment")
    try:
        proc = await asyncio.wait_for(
            create_subprocess_exec(sys.executable, "-m", "venv", os.path.join(pdir, "venv"),
                                   stdout=PIPE, stderr=PIPE),
            timeout=60,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)
        if proc.returncode == 0:
            await venv_progress.stop(success=True, final_text="Virtual environment created")
            results.append("✅ Virtual environment created")
        else:
            err = stderr.decode()[:200]
            await venv_progress.stop(success=False, final_text=err)
            results.append(f"❌ venv failed: {err}")
    except asyncio.TimeoutError:
        await venv_progress.stop(success=False, final_text="venv timed out")
        results.append("❌ venv timed out")
    except Exception as e:
        await venv_progress.stop(success=False, final_text=str(e))
        results.append(f"❌ venv error: {e}")

    # Step 2a: Node.js dependencies
    pkg_json_path = os.path.join(pdir, "package.json")
    if os.path.exists(pkg_json_path):
        npm_progress = LiveProgress(status_msg, title=f"Setup — {name} (npm)")
        await npm_progress.start("npm install starting...")
        npm_progress.run_in_background(estimated_seconds=90, status="Installing npm packages")
        try:
            proc_n = await asyncio.wait_for(
                create_subprocess_exec("npm", "install", "--no-audit", "--no-fund", "--silent",
                                       stdout=PIPE, stderr=PIPE, cwd=pdir),
                timeout=600,
            )
            _, stderr_n = await asyncio.wait_for(proc_n.communicate(), timeout=600)
            if proc_n.returncode == 0:
                await npm_progress.stop(success=True, final_text="npm packages installed")
                results.append("✅ npm packages installed")
            else:
                err = stderr_n.decode()[:300]
                await npm_progress.stop(success=False, final_text=err)
                results.append(f"❌ npm install failed: {err}")
        except asyncio.TimeoutError:
            await npm_progress.stop(success=False, final_text="npm install timed out")
            results.append("❌ npm install timed out")
        except FileNotFoundError:
            await npm_progress.stop(success=False, final_text="npm not found on host")
            results.append("❌ npm not found on host")
        except Exception as e:
            await npm_progress.stop(success=False, final_text=str(e))
            results.append(f"❌ npm error: {e}")

    # Step 2b: Python requirements
    req_path = os.path.join(pdir, "requirements.txt")
    pip_path = os.path.join(pdir, "venv", "bin", "pip")
    if os.path.exists(req_path) and os.path.exists(pip_path):
        req_progress = LiveProgress(status_msg, title=f"Setup — {name} (requirements)")
        await req_progress.start("pip install -r requirements.txt")
        req_progress.run_in_background(estimated_seconds=120, status="Resolving + downloading wheels")
        try:
            proc = await asyncio.wait_for(
                # OPT: -q suppresses verbose wheel download logs → less stdout memory
                create_subprocess_exec(pip_path, "install", "-q", "-r", req_path,
                                       stdout=PIPE, stderr=PIPE, cwd=pdir),
                timeout=300,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            if proc.returncode == 0:
                await req_progress.stop(success=True, final_text="Requirements installed")
                results.append("✅ Requirements installed")
            else:
                err = stderr.decode()[:300]
                await req_progress.stop(success=False, final_text=err)
                results.append(f"❌ pip install failed: {err}")
        except asyncio.TimeoutError:
            await req_progress.stop(success=False, final_text="pip install timed out")
            results.append("❌ pip install timed out")
        except Exception as e:
            await req_progress.stop(success=False, final_text=str(e))
            results.append(f"❌ pip error: {e}")

        if os.path.exists(pip_path):
            try:
                proc2 = await asyncio.wait_for(
                    create_subprocess_exec(pip_path, "list", stdout=PIPE, stderr=PIPE),
                    timeout=30,
                )
                out2, _ = await asyncio.wait_for(proc2.communicate(), timeout=30)
                # FIX: max(0, ...) prevents negative count
                pkg_count = max(0, len(out2.decode().strip().splitlines()) - 2)
                results.append(f"✅ {pkg_count} packages verified")
            except Exception:
                results.append("⚠️ Could not verify packages")
    else:
        results.append("ℹ️ No requirements.txt found")

    # Determine default run command
    py_candidates   = ["main.py", "bot.py", "app.py", "index.py", "run.py"]
    node_candidates = ["index.js", "bot.js", "app.js", "main.js", "server.js"]
    default_cmd = None

    if os.path.exists(pkg_json_path):
        try:
            import json as _json
            with open(pkg_json_path, "r", encoding="utf-8") as _pf:
                _pkg = _json.load(_pf)
            if isinstance(_pkg, dict) and isinstance(_pkg.get("scripts"), dict) and _pkg["scripts"].get("start"):
                default_cmd = "npm start"
            elif isinstance(_pkg, dict) and _pkg.get("main") and os.path.exists(os.path.join(pdir, _pkg["main"])):
                default_cmd = f"node {_pkg['main']}"
        except Exception as _e:
            logger.warning(f"package.json parse failed for {name}: {_e}")

    if not default_cmd:
        for c in py_candidates:
            if os.path.exists(os.path.join(pdir, c)):
                default_cmd = f"python {c}"
                break
    if not default_cmd:
        for c in node_candidates:
            if os.path.exists(os.path.join(pdir, c)):
                default_cmd = f"node {c}"
                break

    # HTML project detection
    html_candidates = ["index.html", "index.htm"]
    is_html_project = False
    for hc in html_candidates:
        if os.path.exists(os.path.join(pdir, hc)) and not default_cmd:
            is_html_project = True
            default_cmd = None  # HTML served directly via Flask — no subprocess needed
            break

    await projects_col.insert_one({
        "user_id":      uid,
        "name":         name,
        "run_command":  default_cmd,
        "created_date": datetime.now(timezone.utc),
        "last_run":     None,
        "exit_code":    None,
        "status":       "stopped",
        "pid":          None,
        "admin_stopped": False,
        "auto_restart":  True,
        "restart_count": 0,
        "last_restart_at": None,
        "locked":        False,
        "github_url":    context.user_data.get("new_project_github"),
        "github_last_pull": None,
        "cron_jobs":     [],
        "crash_count":   0,
        "uptime_total":  0.0,
        "last_crash_at": None,
        "notif_crash":    True,
        "notif_restart":  True,
        "project_type":   "html" if is_html_project else "default",
        "custom_domain":  None,
        "port":           None,
        "webhook_secret": None,
    })

    result_text = "\n".join(results)
    if is_html_project:
        html_url = f"{BASE_URL}/site/{uid}/{name}/"
        result_text += f"\n\n🌐 *HTML Project Detected!*\nYour site URL:\n`{html_url}`"
    elif default_cmd:
        result_text += f"\n\n🚀 Default run cmd: `{escape_md(default_cmd)}`"
    else:
        result_text += "\n\n⚠️ No main file detected. Set run command manually."

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📊 Open Dashboard", callback_data=f"proj:{name}")],
        [InlineKeyboardButton("🔙 My Projects",    callback_data="my_projects")],
    ])
    await status_msg.edit_text(
        f"🎉 *Project {escape_md(name)} ready!*\n\n{result_text}\n\n[████████████] ✅ Complete!",
        reply_markup=kb,
        parse_mode=ParseMode.MARKDOWN,
    )
    context.user_data.clear()
    return ConversationHandler.END

async def new_project_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="back_start")]])
    msg = update.effective_message
    await msg.reply_text("❌ Cancelled.", reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END

# ─────────────────────────────────────────────────────────────
# ⚙️ Admin Panel
# ─────────────────────────────────────────────────────────────

@admin_or_owner
async def cb_admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id

    total_users   = await users_col.count_documents({})
    premium_count = await users_col.count_documents({"is_premium": True})
    banned_count  = await users_col.count_documents({"is_banned": True})
    admin_count   = await users_col.count_documents({"is_admin": True})
    total_proj    = await projects_col.count_documents({})
    running_proj  = await running_project_count()

    meta = await backups_col.find_one({"type": "backup_meta"})
    if meta:
        backup_time = escape_md(meta["backed_up_at"].strftime("%Y-%m-%d %H:%M UTC"))
        backup_info = f"\n💾 Last Backup: `{backup_time}`"
    else:
        backup_info = "\n💾 Last Backup: `Never`"

    db_count_line = f"\n🗄 Databases: `{1 + len(extra_dbs)}` (1 primary + {len(extra_dbs)} extra)"

    settings = await get_bot_settings()
    lock_icon = "🔒 ON" if settings.get("bot_locked") else "🔓 OFF"
    maint_icon = "🔧 ON" if settings.get("maintenance_mode") else "✅ OFF"
    active_db_label = "SQLite" if settings.get("active_db") == "local" else "MongoDB"

    role_label = "👑 Owner" if uid == OWNER_ID else "🛡 Admin"

    text = (
        f"⚙️ *Admin Panel* ({role_label})\n\n"
        f"👥 Total Users: `{total_users}`\n"
        f"💎 Premium: `{premium_count}`\n"
        f"🛡 Admins: `{admin_count}`\n"
        f"🚫 Banned: `{banned_count}`\n"
        f"📁 Projects: `{total_proj}`\n"
        f"🟢 Running: `{running_proj}`"
        f"{db_count_line}"
        f"{backup_info}\n"
        f"🔒 Bot Lock: `{lock_icon}`\n"
        f"🔧 Maintenance: `{maint_icon}`\n"
        f"🗄 Active DB: `{active_db_label}`"
    )

    kb_rows = [
        [InlineKeyboardButton("👥 User List",        callback_data="admin:user_list:0"),
         InlineKeyboardButton("🟢 Running Scripts",  callback_data="admin:running")],
        [InlineKeyboardButton("💎 Give Premium",     callback_data="admin:give_premium"),
         InlineKeyboardButton("❌ Remove Premium",   callback_data="admin:remove_premium")],
        [InlineKeyboardButton("⏰ Temp Premium",     callback_data="admin:temp_premium"),
         InlineKeyboardButton("🚫 Ban User",         callback_data="admin:ban")],
        [InlineKeyboardButton("✅ Unban User",       callback_data="admin:unban"),
         InlineKeyboardButton("📢 Broadcast",        callback_data="admin:broadcast_menu")],
        [InlineKeyboardButton("💾 Backup Now",       callback_data="admin:backup_now"),
         InlineKeyboardButton("🗑 Delete All Backup", callback_data="admin:del_backups")],
        [InlineKeyboardButton("📊 Bot Status",       callback_data="bot_status"),
         InlineKeyboardButton("🗄️ DB Viewer",       callback_data="db_viewer")],
    ]

    # Owner-only buttons
    if uid == OWNER_ID:
        kb_rows.append([
            InlineKeyboardButton("💳 Payment Settings", callback_data="admin:payment_settings"),
        ])
        kb_rows.append([
            InlineKeyboardButton("➕ Add Admin",    callback_data="admin:add_admin"),
            InlineKeyboardButton("➖ Remove Admin", callback_data="admin:remove_admin"),
        ])
        # Bot lock & maintenance (owner only)
        lock_btn_label = "🔓 Unlock Bot" if settings.get("bot_locked") else "🔒 Lock Bot"
        maint_btn_label = "✅ Disable Maintenance" if settings.get("maintenance_mode") else "🔧 Maintenance Mode"
        kb_rows.append([
            InlineKeyboardButton(lock_btn_label,  callback_data="admin:toggle_lock"),
            InlineKeyboardButton(maint_btn_label, callback_data="admin:toggle_maintenance"),
        ])
        kb_rows.append([
            InlineKeyboardButton("🗄 Database Settings", callback_data="admin:db_settings"),
        ])
        kb_rows.append([
            InlineKeyboardButton("🌐 Domain Manager", callback_data="admin:domain_manager"),
        ])

    kb_rows.append([InlineKeyboardButton("🔙 Back", callback_data="back_start")])

    await safe_edit(query, text, reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────────────────────
# 🔒 Bot Lock Toggle (Owner only)
# ─────────────────────────────────────────────────────────────

@owner_only
async def cb_admin_toggle_lock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    settings = await get_bot_settings()
    current = settings.get("bot_locked", False)
    new_val = not current

    await set_bot_setting("bot_locked", new_val)

    if new_val:
        # Lock is now ON — stop all free users' running projects
        await safe_edit(
            query,
            "🔒 *Locking bot...*\n\nStopping all free users' running projects...",
            parse_mode=ParseMode.MARKDOWN,
        )
        stopped_count = 0
        # OPT: projection — only user_id+name needed to kill/update
        running = await projects_col.find(
            {"status": "running"}, {"user_id":1,"name":1,"_id":0}
        ).to_list(length=1000)
        for p in running:
            p_uid = p["user_id"]
            if p_uid == OWNER_ID:
                continue
            prem = await is_premium(p_uid)
            if not prem:
                await kill_project(p_uid, p["name"])
                # FIX: do NOT set admin_stopped here. cb_run already blocks free users
                # when is_bot_locked() is True. Setting admin_stopped:True causes a
                # permanent block — after bot unlock, users get "stopped by admin" and
                # can never restart their projects without owner manually re-running each one.
                stopped_count += 1
                if notification_bot:
                    try:
                        await notification_bot.send_message(
                            chat_id=p_uid,
                            text=(
                                f"🔒 *Bot Locked*\n\n"
                                f"Project `{p['name']}` has been stopped because the bot is now locked.\n"
                                f"Only Premium users can run projects.\n"
                                f"Contact owner to upgrade!"
                            ),
                            parse_mode=ParseMode.MARKDOWN,
                        )
                    except Exception:
                        pass
        status_msg = f"🔒 *Bot is now LOCKED*\n\n✅ {stopped_count} free user project(s) stopped.\nPremium users are unaffected."
    else:
        status_msg = "🔓 *Bot is now UNLOCKED*\n\nAll users can create and run projects."

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]])
    await safe_edit(query, status_msg, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────────────────────
# 🔧 Maintenance Mode Toggle (Owner only)
# ─────────────────────────────────────────────────────────────

@owner_only
async def cb_admin_toggle_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    settings = await get_bot_settings()
    current = settings.get("maintenance_mode", False)
    new_val = not current

    await set_bot_setting("maintenance_mode", new_val)

    if new_val:
        # Maintenance ON — stop ALL projects (except owner's)
        await safe_edit(
            query,
            "🔧 *Enabling maintenance mode...*\n\nStopping all running projects...",
            parse_mode=ParseMode.MARKDOWN,
        )
        stopped_count = 0
        running = await projects_col.find({"status": "running"}).to_list(length=1000)
        for p in running:
            p_uid = p["user_id"]
            if p_uid == OWNER_ID:
                continue
            await kill_project(p_uid, p["name"])
            stopped_count += 1
            if notification_bot:
                try:
                    await notification_bot.send_message(
                        chat_id=p_uid,
                        text=(
                            f"🔧 *Maintenance Mode*\n\n"
                            f"Project `{p['name']}` has been stopped for maintenance.\n"
                            f"The bot will be back soon!"
                        ),
                        parse_mode=ParseMode.MARKDOWN,
                    )
                except Exception:
                    pass
        status_msg = (
            f"🔧 *Maintenance Mode is now ON*\n\n"
            f"✅ {stopped_count} project(s) stopped.\n"
            f"Only owner can use the bot now."
        )
    else:
        status_msg = "✅ *Maintenance Mode is now OFF*\n\nAll users can use the bot again."

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]])
    await safe_edit(query, status_msg, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────────────────────
# 🗄 Database Settings (Owner only)
# ─────────────────────────────────────────────────────────────

@owner_only
async def cb_admin_db_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    settings = await get_bot_settings()
    active_db = settings.get("active_db", "mongodb")
    local_exists = os.path.exists(LOCAL_DB_PATH)
    local_size = fmt_bytes(os.path.getsize(LOCAL_DB_PATH)) if local_exists else "N/A"

    mongo_proj = await projects_col.count_documents({})
    mongo_users = await users_col.count_documents({})

    text = (
        f"🗄 *Database Settings*\n\n"
        f"*Currently Active:* `{'SQLite (Local)' if active_db == 'local' else 'MongoDB'}`\n\n"
        f"*MongoDB:*\n"
        f"├ Users: `{mongo_users}`\n"
        f"├ Projects: `{mongo_proj}`\n"
        f"└ Status: `{'🟢 Connected'}`\n\n"
        f"*Local SQLite:*\n"
        f"├ File: `{LOCAL_DB_PATH}`\n"
        f"├ Exists: `{'✅ Yes' if local_exists else '❌ No'}`\n"
        f"└ Size: `{local_size}`\n\n"
        f"_When switching, all data is copied automatically._\n"
        f"_MongoDB always continues to receive backups regardless of active DB._"
    )

    kb_rows = []
    if active_db == "mongodb":
        kb_rows.append([InlineKeyboardButton("🗄 Switch to Local (SQLite)", callback_data="admin:db_switch_to_local")])
    else:
        kb_rows.append([InlineKeyboardButton("☁️ Switch to MongoDB", callback_data="admin:db_switch_to_mongo")])

    kb_rows.append([InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")])
    await safe_edit(query, text, reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode=ParseMode.MARKDOWN)

@owner_only
async def cb_admin_db_switch_to_local(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    kb_confirm = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Yes, Switch to Local", callback_data="admin:db_confirm_local")],
        [InlineKeyboardButton("❌ Cancel", callback_data="admin:db_settings")],
    ])
    await safe_edit(
        query,
        "🗄 *Switch to Local SQLite?*\n\n"
        "• All MongoDB data will be copied to local SQLite\n"
        "• Bot will use local SQLite going forward\n"
        "• MongoDB will continue receiving backups\n\n"
        "⚠️ Make sure you have enough disk space!",
        reply_markup=kb_confirm,
        parse_mode=ParseMode.MARKDOWN,
    )

@owner_only
async def cb_admin_db_confirm_local(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await safe_edit(
        query,
        "🗄 *Migrating MongoDB → Local SQLite...*\n\n⏳ Please wait...",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        uc, pc = await migrate_mongo_to_local()
        await set_bot_setting("active_db", "local")
        result_text = (
            f"✅ *Switched to Local SQLite!*\n\n"
            f"📊 Migrated:\n"
            f"├ Users: `{uc}`\n"
            f"└ Projects: `{pc}`\n\n"
            f"Bot is now using local SQLite database.\n"
            f"MongoDB backup continues in background."
        )
    except Exception as e:
        result_text = f"❌ *Migration failed!*\n\n`{escape_md(str(e)[:300])}`\n\nStill on MongoDB."

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 DB Settings", callback_data="admin:db_settings")]])
    await safe_edit(query, result_text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

@owner_only
async def cb_admin_db_switch_to_mongo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    kb_confirm = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Yes, Switch to MongoDB", callback_data="admin:db_confirm_mongo")],
        [InlineKeyboardButton("❌ Cancel", callback_data="admin:db_settings")],
    ])
    await safe_edit(
        query,
        "☁️ *Switch to MongoDB?*\n\n"
        "• All local SQLite data will be copied to MongoDB\n"
        "• Bot will use MongoDB going forward\n"
        "• Local SQLite file is kept as backup\n\n"
        "Proceed?",
        reply_markup=kb_confirm,
        parse_mode=ParseMode.MARKDOWN,
    )

@owner_only
async def cb_admin_db_confirm_mongo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    await safe_edit(
        query,
        "☁️ *Migrating Local SQLite → MongoDB...*\n\n⏳ Please wait...",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        uc, pc = await migrate_local_to_mongo()
        await set_bot_setting("active_db", "mongodb")
        result_text = (
            f"✅ *Switched to MongoDB!*\n\n"
            f"📊 Migrated:\n"
            f"├ Users: `{uc}`\n"
            f"└ Projects: `{pc}`\n\n"
            f"Bot is now using MongoDB.\n"
            f"Local SQLite file kept as backup at:\n`{escape_md(LOCAL_DB_PATH)}`"
        )
    except Exception as e:
        result_text = f"❌ *Migration failed!*\n\n`{escape_md(str(e)[:300])}`\n\nStill on local SQLite."

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 DB Settings", callback_data="admin:db_settings")]])
    await safe_edit(query, result_text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────────────────────
# 💾 Backup (admin)
# ─────────────────────────────────────────────────────────────

@admin_or_owner
async def cb_admin_backup_now(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("⏳ Running backup...", show_alert=False)

    await safe_edit(
        query,
        "💾 *Backup in progress...*\n\nThis may take a moment.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]]),
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        # OPT: projection — backup_now only needs uid+name to walk filesystem
        all_projects = await projects_col.find(
            {}, {"user_id":1,"name":1,"_id":0}
        ).to_list(length=10000)
        total_files = 0
        total_size = 0
        db_distribution = {}

        for proj in all_projects:
            uid  = proj["user_id"]
            name = proj["name"]
            pdir = project_dir(uid, name)

            if not os.path.exists(pdir):
                continue

            files_data = []
            for root, dirs, files in os.walk(pdir):
                dirs[:] = [d for d in dirs if d not in ("venv", "__pycache__", ".git", "node_modules")]
                for fname in files:
                    if fname in ("output.log",) or fname.endswith(".pyc"):
                        continue
                    fpath   = os.path.join(root, fname)
                    rel_path = os.path.relpath(fpath, pdir)
                    try:
                        file_size = os.path.getsize(fpath)
                        if file_size > 15 * 1024 * 1024:
                            continue
                        try:
                            with open(fpath, "r", encoding="utf-8") as f:
                                content = f.read()
                            content_b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
                            is_binary = False
                        except (UnicodeDecodeError, ValueError):
                            with open(fpath, "rb") as f:
                                content_bytes = f.read()
                            content_b64 = base64.b64encode(content_bytes).decode("ascii")
                            is_binary = True
                        files_data.append({
                            "path": rel_path, "content_b64": content_b64,
                            "size": file_size, "is_binary": is_binary,
                        })
                        total_files += 1
                        total_size  += file_size
                    except Exception:
                        continue

            if files_data:
                target_db_name, target_col = pick_backup_col(uid, name)
                doc = {
                    "type": "file_backup", "user_id": uid,
                    "project_name": name, "files": files_data,
                    "backed_up_at": datetime.now(timezone.utc),
                    "stored_in": target_db_name,
                }
                try:
                    for col in all_backup_cols():
                        try:
                            await col.delete_many({"type": "file_backup", "user_id": uid, "project_name": name})
                        except Exception:
                            pass
                    await target_col.insert_one(doc)
                    db_distribution[target_db_name] = db_distribution.get(target_db_name, 0) + 1
                except Exception as e:
                    logger.warning(f"Backup write failed for {name} on {target_db_name}: {e}")

        now = datetime.now(timezone.utc)
        try:
            await backups_col.delete_many({"type": "backup_meta"})
            await backups_col.insert_one({
                "type": "backup_meta", "total_projects": len(all_projects),
                "total_files": total_files, "total_size": total_size,
                "backed_up_at": now, "distribution": db_distribution,
            })
        except Exception as e:
            logger.warning(f"Backup meta write failed: {e}")

        total_db_count = 1 + len(extra_dbs)
        backup_time = escape_md(now.strftime("%Y-%m-%d %H:%M UTC"))
        dist_lines = ""
        if db_distribution:
            dist_lines = "\n*📊 Storage Distribution:*\n"
            for db_name, count in sorted(db_distribution.items()):
                dist_lines += f"   • `{escape_md(db_name)}`: `{count}` projects\n"

        result_text = (
            f"✅ *Backup Complete!*\n\n"
            f"📁 Projects: `{len(all_projects)}`\n"
            f"📄 Files: `{total_files}`\n"
            f"📦 Size: `{escape_md(fmt_bytes(total_size))}`\n"
            f"🗄 Distributed across: `{total_db_count}` database(s)\n"
            f"🕐 Time: `{backup_time}`"
            f"{dist_lines}"
        )
    except Exception as e:
        logger.error(f"Manual backup failed: {e}")
        result_text = f"❌ *Backup Failed!*\n\n`{escape_md(str(e))}`"

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]])
    await safe_edit(query, result_text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────────────────────
# 🗑 Delete All Backups (owner)
# ─────────────────────────────────────────────────────────────

@owner_only
async def cb_admin_delete_backups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    primary_count = await backups_col.count_documents({})
    extra_counts = []
    total_extra = 0
    for entry in extra_dbs:
        try:
            c = await entry["db"]["backups"].count_documents({})
        except Exception:
            c = 0
        extra_counts.append((entry["name"], c))
        total_extra += c

    lines = [
        "⚠️ *Delete ALL Backups?*\n",
        f"📂 Primary DB (`{DATABASE_NAME}`): `{primary_count}` docs",
    ]
    if extra_counts:
        lines.append(f"\n📂 *Extra DBs ({len(extra_counts)}):*")
        for name, c in extra_counts:
            lines.append(f"   • `{name}`: `{c}` docs")
        lines.append(f"\n📊 *Total to delete:* `{primary_count + total_extra}` documents")
    else:
        lines.append("\nℹ️ No extra DBs configured.")
    lines.append("\nThis action is *permanent* and cannot be undone.")
    lines.append("Project files will NOT be deleted — only MongoDB backups.")

    text = "\n".join(lines)
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑 Yes, Delete All", callback_data="admin:del_backups_confirm")],
        [InlineKeyboardButton("🔙 Cancel",          callback_data="admin_panel")],
    ])
    await safe_edit(query, text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

@owner_only
async def cb_admin_delete_backups_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("⏳ Deleting backups...", show_alert=False)

    await safe_edit(
        query, "🗑 *Deleting all backups...*\n\nPlease wait.",
        reply_markup=None, parse_mode=ParseMode.MARKDOWN,
    )

    primary_deleted = 0
    extra_results = []
    errors = []

    try:
        res = await backups_col.delete_many({})
        primary_deleted = res.deleted_count
    except Exception as e:
        errors.append(f"Primary DB error: {e}")

    for entry in extra_dbs:
        name = entry["name"]
        try:
            res_x = await entry["db"]["backups"].delete_many({})
            extra_results.append((name, res_x.deleted_count, None))
        except Exception as e:
            extra_results.append((name, 0, str(e)))
            errors.append(f"DB '{name}' error: {e}")

    total_deleted = primary_deleted + sum(c for _, c, _ in extra_results)

    lines = [
        "✅ *All Backups Deleted!*\n",
        f"📂 Primary (`{DATABASE_NAME}`): `{primary_deleted}` removed",
    ]
    if extra_results:
        lines.append(f"\n📂 *Extra DBs ({len(extra_results)}):*")
        for name, count, err in extra_results:
            if err:
                lines.append(f"   • `{name}`: ❌ failed")
            else:
                lines.append(f"   • `{name}`: `{count}` removed")
        lines.append(f"\n📊 *Total deleted:* `{total_deleted}` documents")
    if errors:
        lines.append("\n⚠️ *Some errors occurred:*")
        for err in errors[:5]:
            lines.append(f"`{escape_md(err)}`")

    kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")]])
    await safe_edit(query, "\n".join(lines), reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────────────────────
# 👥 Admin User List
# ─────────────────────────────────────────────────────────────

@admin_or_owner
async def cb_admin_user_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split(":")[-1])
    per_page = 10

    total = await users_col.count_documents({})
    users = await users_col.find({}).skip(page * per_page).limit(per_page).to_list(length=per_page)

    lines = [f"👥 *User List* (page {page+1})\n"]
    for u in users:
        badges = ""
        if u.get("is_admin"):
            badges += " 🛡"
        if u.get("is_premium"):
            badges += " 💎"
        if u.get("is_banned"):
            badges += " 🚫"
        uname = f"@{u['username']}" if u.get("username") else "no-username"
        lines.append(f"`{u['user_id']}` {escape_md(uname)}{badges}")

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"admin:user_list:{page-1}"))
    if (page + 1) * per_page < total:
        nav.append(InlineKeyboardButton("➡️ Next", callback_data=f"admin:user_list:{page+1}"))

    kb_rows = []
    if nav:
        kb_rows.append(nav)
    kb_rows.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])

    await safe_edit(query, "\n".join(lines), reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────────────────────
# 🟢 Running Scripts
# ─────────────────────────────────────────────────────────────

@admin_or_owner
async def cb_admin_running(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        # OPT: projection — cb_admin_running only needs uid/name/cmd/pid/started_at
        all_db_running = await projects_col.find(
            {"status": "running"},
            {"user_id":1,"name":1,"run_command":1,"pid":1,"started_at":1,"_id":0}
        ).to_list(length=100)

        # Real-time PID sync: filter out dead processes and update DB
        # Priority: context_store → psutil.pid_exists → find_project_process (VPS-safe)
        running = []
        for p in all_db_running:
            pid      = p.get("pid")
            cs_key   = f"{p['user_id']}:{p['name']}"
            proc_obj = context_store.get(cs_key)

            if proc_obj is not None:
                alive = proc_obj.returncode is None
            elif pid and psutil.pid_exists(pid):
                alive = True
            else:
                # PID stale/missing — search by project directory (VPS/RDP safe)
                _pdir = project_dir(p["user_id"], p["name"])
                found, real_pid = find_project_process(_pdir)
                alive = found
                if found and real_pid and real_pid != pid:
                    await projects_col.update_one(
                        {"user_id": p["user_id"], "name": p["name"]},
                        {"$set": {"pid": real_pid}},
                    )
                    p["pid"] = real_pid

            if not alive:
                # FIX: grace period — same race condition as cb_project_dashboard.
                # Within 30s of bot start, auto_restart_on_startup hasn't finished yet.
                # Don't mark as stopped or auto-restart will never find these projects.
                if time.time() - BOT_START_TIME > 30:
                    await projects_col.update_one(
                        {"user_id": p["user_id"], "name": p["name"]},
                        {"$set": {"status": "stopped", "pid": None}},
                    )
                else:
                    # Grace period: show as running, auto_restart will fix it shortly
                    running.append(p)
            else:
                running.append(p)

        if not running:
            await safe_edit(
                query,
                "🟢 *Running Scripts*\n\nNo projects running.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]]),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        lines = ["🟢 *Running Scripts*\n"]
        kb_rows = []

        for p in running:
            user_doc = await get_user(p["user_id"])
            fname = user_doc.get("first_name", "Unknown") if user_doc else "Unknown"
            uname = f"@{user_doc['username']}" if user_doc and user_doc.get("username") else "no-username"
            pid = p.get("pid", "N/A")
            uptime = "N/A"
            if p.get("started_at"):
                try:
                    started = p["started_at"]
                    if started.tzinfo is None:
                        started = started.replace(tzinfo=timezone.utc)
                    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
                    uptime = fmt_uptime(max(0, elapsed))
                except Exception:
                    uptime = "N/A"

            lines.append(
                f"- - - - - - - - - - -\n"
                f"👤 {fname} ({uname})\n"
                f"📁 Project: {p['name']}\n"
                f"🔹 PID: {pid} | Uptime: {uptime}"
            )
            row_btns = [InlineKeyboardButton(f"⏹ Stop {p['name']}", callback_data=f"admin_stop:{p['user_id']}:{p['name']}")]
            if query.from_user.id == OWNER_ID:
                row_btns.append(InlineKeyboardButton(f"📥 Download", callback_data=f"admin_dl:{p['user_id']}:{p['name']}"))
            kb_rows.append(row_btns)

        kb_rows.append([InlineKeyboardButton("👥 All Users & Projects", callback_data="admin:all_projects:0")])
        kb_rows.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])

        full_text = "\n".join(lines)
        if len(full_text) > 4000:
            full_text = full_text[:3900] + "\n...(truncated)"

        await safe_edit(query, full_text, reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        logger.error(f"cb_admin_running error: {e}")
        await safe_edit(
            query,
            f"❌ Error loading running scripts: {str(e)[:200]}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin_panel")]]),
        )

# ─────────────────────────────────────────────────────────────
# All Users & Projects
# ─────────────────────────────────────────────────────────────

@admin_or_owner
async def cb_admin_all_projects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split(":")[-1])
    per_page = 5

    # OPT: projection — admin_all_projects only needs uid/name/status/cmd/locked for display
    # FIX: added "locked":1 — without it p.get("locked") always returns None so the
    #      🔒 icon never shows in the admin panel project list.
    all_projects = await projects_col.find(
        {}, {"user_id":1,"name":1,"status":1,"run_command":1,"locked":1,"_id":0}
    ).to_list(length=10000)
    user_projects = {}
    for p in all_projects:
        uid = p["user_id"]
        if uid not in user_projects:
            user_projects[uid] = []
        user_projects[uid].append(p)

    user_ids = list(user_projects.keys())
    total = len(user_ids)
    start = page * per_page
    end = min(start + per_page, total)
    page_user_ids = user_ids[start:end]

    if not page_user_ids:
        await safe_edit(
            query,
            "👥 *All Users & Projects*\n\nNo projects found.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin:running")]]),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    lines = [f"👥 *All Users & Projects* (page {page+1})\n"]
    kb_rows = []

    for uid in page_user_ids:
        user_doc = await get_user(uid)
        fname = user_doc.get("first_name", "Unknown") if user_doc else "Unknown"
        uname = f"@{user_doc['username']}" if user_doc and user_doc.get("username") else ""

        projects = user_projects[uid]
        proj_lines = []
        for p in projects:
            status_icon = "🟢" if p.get("status") == "running" else ("🔒" if p.get("locked") else "🔴")
            proj_lines.append(f"  {status_icon} {p['name']}")

            is_caller_owner = query.from_user.id == OWNER_ID
            if p.get("status") == "running":
                row = [InlineKeyboardButton(f"⏹ Stop {p['name']}", callback_data=f"admin_stop:{uid}:{p['name']}")]
                if is_caller_owner:
                    row.append(InlineKeyboardButton(f"📥 DL {p['name']}", callback_data=f"admin_dl:{uid}:{p['name']}"))
                kb_rows.append(row)
            else:
                row = [InlineKeyboardButton(f"▶️ Run {p['name']}", callback_data=f"admin_run:{uid}:{p['name']}")]
                if is_caller_owner:
                    row.append(InlineKeyboardButton(f"📥 DL {p['name']}", callback_data=f"admin_dl:{uid}:{p['name']}"))
                kb_rows.append(row)

        lines.append(
            f"- - - - - - - - - - -\n"
            f"👤 {fname} {uname} (`{uid}`)\n"
            f"📁 {len(projects)} project(s):\n" + "\n".join(proj_lines)
        )

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"admin:all_projects:{page-1}"))
    if end < total:
        nav.append(InlineKeyboardButton("➡️ Next", callback_data=f"admin:all_projects:{page+1}"))
    if nav:
        kb_rows.append(nav)

    kb_rows.append([InlineKeyboardButton("🔙 Back", callback_data="admin:running")])

    full_text = "\n".join(lines)
    if len(full_text) > 4000:
        full_text = full_text[:3900] + "\n...(truncated)"

    await safe_edit(query, full_text, reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode=ParseMode.MARKDOWN)

@admin_or_owner
async def cb_admin_run_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, uid_str, name = query.data.split(":", 2)
    uid = int(uid_str)

    p = await get_project(uid, name)
    if not p:
        await safe_edit(
            query, "❌ Project not found.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin:all_projects:0")]]),
        )
        return

    if not p.get("run_command"):
        await safe_edit(
            query,
            f"❌ No run command set for *{escape_md(name)}*.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin:all_projects:0")]]),
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if p.get("status") == "running":
        _ap = p.get("pid")
        _aproc = context_store.get(f"{uid}:{name}")
        _alive = (_aproc is not None and _aproc.returncode is None) or \
                 (_ap and psutil.pid_exists(_ap))
        if not _alive:
            _found2, _real2 = find_project_process(project_dir(uid, name))
            if _found2 and _real2 and _real2 != _ap:
                await projects_col.update_one({"user_id": uid, "name": name}, {"$set": {"pid": _real2}})
            _alive = _found2
        if _alive:
            await safe_edit(
                query,
                f"▶️ Project *{escape_md(name)}* is already running.\nPID: `{p.get('pid')}`",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin:all_projects:0")]]),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

    # FIX: admin run used to start the process directly without installing
    # requirements first — unlike auto_restart_on_startup, which always
    # installs before starting. Now we: 1) install requirements/deps,
    # 2) run the script, 3) notify the user — in that order.
    await safe_edit(query, f"📦 Installing requirements for *{escape_md(name)}*...", parse_mode=ParseMode.MARKDOWN)
    try:
        req_ok, req_msg = await _install_requirements_for_project(uid, name)
        if not req_ok:
            await safe_edit(
                query,
                f"❌ Requirements install failed for *{escape_md(name)}*:\n`{escape_md(req_msg)[:300]}`",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin:all_projects:0")]]),
                parse_mode=ParseMode.MARKDOWN,
            )
            return

        updated = await start_project_process(uid, name)
        try:
            if notification_bot:
                await notification_bot.send_message(
                    uid,
                    f"▶️ Your project *{escape_md(name)}* was started by admin.",
                    parse_mode=ParseMode.MARKDOWN,
                )
        except Exception:
            pass

        await safe_edit(
            query,
            f"✅ Project *{escape_md(name)}* started by admin.\nPID: `{updated.get('pid', 'N/A')}`",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("👥 All Projects", callback_data="admin:all_projects:0")],
                [InlineKeyboardButton("🟢 Running Scripts", callback_data="admin:running")],
            ]),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.error(f"Admin run project failed for {uid}/{name}: {e}")
        await safe_edit(
            query,
            f"❌ Failed to start *{escape_md(name)}*: `{escape_md(str(e))[:250]}`",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin:all_projects:0")]]),
            parse_mode=ParseMode.MARKDOWN,
        )

@owner_only
async def cb_admin_download_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("📥 Creating zip...", show_alert=False)
    _, uid_str, name = query.data.split(":", 2)
    uid = int(uid_str)

    pdir = project_dir(uid, name)
    if not os.path.exists(pdir):
        await query.answer("❌ Project directory not found!", show_alert=True)
        return

    zip_path = os.path.join(PROJECTS_ROOT, f"{uid}_{name}.zip")
    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for root, dirs, files in os.walk(pdir):
                dirs[:] = [d for d in dirs if d not in ("venv", "__pycache__", ".git", "node_modules")]
                for fname_file in files:
                    if fname_file in ("output.log",) or fname_file.endswith(".pyc"):
                        continue
                    fpath = os.path.join(root, fname_file)
                    arcname = os.path.relpath(fpath, pdir)
                    zf.write(fpath, arcname)

        with open(zip_path, "rb") as f:
            await query.message.reply_document(
                document=f,
                filename=f"{name}.zip",
                caption=f"📥 Project: {name}\nUser ID: {uid}",
            )
    except Exception as e:
        logger.error(f"Admin download failed: {e}")
        await query.answer(f"❌ Download failed: {str(e)[:100]}", show_alert=True)
    finally:
        if os.path.exists(zip_path):
            os.remove(zip_path)

@admin_or_owner
async def cb_admin_stop_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    _, uid_str, name = query.data.split(":", 2)
    uid = int(uid_str)

    p = await get_project(uid, name)
    if not p:
        await safe_edit(
            query, "❌ Project not found.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin:running")]]),
        )
        return

    try:
        await kill_project(uid, name)
        await projects_col.update_one(
            {"user_id": uid, "name": name},
            {"$set": {"admin_stopped": True}},
        )
        try:
            if notification_bot:
                await notification_bot.send_message(
                    uid,
                    f"⏹ Your project *{escape_md(name)}* was stopped by admin.\nContact owner to resume.",
                    parse_mode=ParseMode.MARKDOWN,
                )
        except Exception:
            pass

        await safe_edit(
            query,
            f"✅ Project *{escape_md(name)}* stopped (admin).",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🟢 Running Scripts", callback_data="admin:running")],
                [InlineKeyboardButton("👥 All Projects", callback_data="admin:all_projects:0")],
            ]),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.error(f"Admin stop project failed for {uid}/{name}: {e}")
        await safe_edit(
            query,
            f"❌ Failed to stop *{escape_md(name)}*: `{escape_md(str(e))[:250]}`",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data="admin:running")]]),
            parse_mode=ParseMode.MARKDOWN,
        )

# ─────────────────────────────────────────────────────────────
# Admin premium / ban / broadcast conversations
# ─────────────────────────────────────────────────────────────

@admin_or_owner
async def cb_admin_give_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit(query, "💎 *Give Premium*\n\nSend the user ID:", parse_mode=ParseMode.MARKDOWN)
    return ADMIN_GIVE_PREMIUM_ID

async def admin_give_premium_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid ID. Send a numeric user ID:", parse_mode=ParseMode.MARKDOWN)
        return ADMIN_GIVE_PREMIUM_ID

    await users_col.update_one(
        {"user_id": uid},
        {"$set": {"is_premium": True, "premium_expiry": None}},
    )
    # Unlock all projects for this user
    await projects_col.update_many({"user_id": uid}, {"$set": {"locked": False}})

    try:
        await update.get_bot().send_message(uid, "🎉 You have been granted *Premium*! Enjoy unlimited projects!", parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass
    await update.message.reply_text(f"✅ Premium granted to `{uid}`. All their projects unlocked.", parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END

@admin_or_owner
async def cb_admin_remove_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit(query, "❌ *Remove Premium*\n\nSend the user ID:", parse_mode=ParseMode.MARKDOWN)
    return ADMIN_REMOVE_PREMIUM_ID

async def admin_remove_premium_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid ID:", parse_mode=ParseMode.MARKDOWN)
        return ADMIN_REMOVE_PREMIUM_ID

    await users_col.update_one({"user_id": uid}, {"$set": {"is_premium": False, "premium_expiry": None}})
    # Lock extra projects
    await _lock_extra_projects_on_expiry(uid)
    await update.message.reply_text(f"✅ Premium removed from `{uid}`. Extra projects locked.", parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END

@admin_or_owner
async def cb_admin_temp_premium(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit(query, "⏰ *Temp Premium*\n\nSend the user ID:", parse_mode=ParseMode.MARKDOWN)
    return ADMIN_TEMP_PREMIUM_ID

async def admin_temp_premium_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid ID:", parse_mode=ParseMode.MARKDOWN)
        return ADMIN_TEMP_PREMIUM_ID
    context.user_data["temp_premium_uid"] = uid
    await update.message.reply_text(
        "⏰ Send duration (e.g. `24h` or `7d`):",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ADMIN_TEMP_PREMIUM_DUR

async def admin_temp_premium_dur(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    uid  = context.user_data.get("temp_premium_uid")
    m = re.match(r"^(\d+)([hd])$", text)
    if not m:
        await update.message.reply_text("❌ Invalid format. Use `24h` or `7d`:", parse_mode=ParseMode.MARKDOWN)
        return ADMIN_TEMP_PREMIUM_DUR

    amount, unit = int(m.group(1)), m.group(2)
    seconds = amount * 3600 if unit == "h" else amount * 86400
    expiry  = datetime.fromtimestamp(time.time() + seconds, tz=timezone.utc)

    await users_col.update_one(
        {"user_id": uid},
        {"$set": {"is_premium": True, "premium_expiry": expiry}},
    )
    # Unlock all projects
    await projects_col.update_many({"user_id": uid}, {"$set": {"locked": False}})

    try:
        await update.get_bot().send_message(uid, f"🎉 You received *Temp Premium* for {escape_md(text)}! All projects unlocked!", parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass
    await update.message.reply_text(
        f"✅ Temp premium set for `{uid}` — expires {escape_md(expiry.strftime('%Y-%m-%d %H:%M UTC'))}.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END

@admin_or_owner
async def cb_admin_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit(query, "🚫 *Ban User*\n\nSend the user ID:", parse_mode=ParseMode.MARKDOWN)
    return ADMIN_BAN_ID

async def admin_ban_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid ID:", parse_mode=ParseMode.MARKDOWN)
        return ADMIN_BAN_ID

    await users_col.update_one({"user_id": uid}, {"$set": {"is_banned": True}})
    user_projects = await projects_col.find({"user_id": uid, "status": "running"}).to_list(length=100)
    for p in user_projects:
        await kill_project(uid, p["name"])
    await update.message.reply_text(f"✅ User `{uid}` banned and all projects stopped.", parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END

@admin_or_owner
async def cb_admin_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit(query, "✅ *Unban User*\n\nSend the user ID:", parse_mode=ParseMode.MARKDOWN)
    return ADMIN_UNBAN_ID

async def admin_unban_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid ID:", parse_mode=ParseMode.MARKDOWN)
        return ADMIN_UNBAN_ID

    await users_col.update_one({"user_id": uid}, {"$set": {"is_banned": False}})
    try:
        await update.get_bot().send_message(uid, "✅ You have been unbanned! You can use the bot again.", parse_mode=ParseMode.MARKDOWN)
    except Exception:
        pass
    await update.message.reply_text(f"✅ User `{uid}` unbanned.", parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END

@admin_or_owner
async def cb_admin_broadcast_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Broadcast All",  callback_data="admin:broadcast_all")],
        [InlineKeyboardButton("📩 Send to User",   callback_data="admin:send_to_user")],
        [InlineKeyboardButton("🔙 Back",           callback_data="admin_panel")],
    ])
    await safe_edit(query, "📢 *Broadcast Menu*", reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

@admin_or_owner
async def cb_admin_broadcast_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["broadcast_type"] = "all"
    await safe_edit(query, "📢 *Broadcast All*\n\nSend the message:", parse_mode=ParseMode.MARKDOWN)
    return ADMIN_BROADCAST_MSG

@admin_or_owner
async def cb_admin_send_to_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit(query, "📩 *Send to User*\n\nSend the target user ID:", parse_mode=ParseMode.MARKDOWN)
    return ADMIN_SEND_USER_ID

async def admin_send_user_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid ID:", parse_mode=ParseMode.MARKDOWN)
        return ADMIN_SEND_USER_ID
    context.user_data["broadcast_target"] = uid
    await update.message.reply_text("Send the message:", parse_mode=ParseMode.MARKDOWN)
    return ADMIN_SEND_USER_MSG

async def admin_send_user_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = context.user_data.get("broadcast_target")
    msg = update.message.text
    try:
        await update.get_bot().send_message(uid, msg)
        await update.message.reply_text(f"✅ Sent to `{uid}`.", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"❌ Failed: {escape_md(str(e))}", parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END

async def admin_broadcast_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg  = update.message.text
    bot  = update.get_bot()
    # OPT: projection — only user_id needed for broadcast
    all_users = await users_col.find({}, {"user_id":1,"_id":0}).to_list(length=10000)
    sent = failed = 0
    for u in all_users:
        try:
            await bot.send_message(u["user_id"], msg)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)  # Rate limit friendly
    await update.message.reply_text(
        f"📢 Broadcast complete!\n✅ Sent: `{sent}`\n❌ Failed: `{failed}`",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END

async def admin_conv_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer()
    await (update.effective_message).reply_text("❌ Cancelled.", parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END

# ─────────────────────────────────────────────────────────────
# 🛡 Add / Remove Admin (Owner only)
# ─────────────────────────────────────────────────────────────

@owner_only
async def cb_admin_add_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit(
        query,
        "🛡 *Add Admin*\n\nSend the user ID:",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ADMIN_ADD_ADMIN_ID

async def admin_add_admin_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid ID:", parse_mode=ParseMode.MARKDOWN)
        return ADMIN_ADD_ADMIN_ID

    if uid == OWNER_ID:
        await update.message.reply_text("⚠️ Owner is already the highest role!", parse_mode=ParseMode.MARKDOWN)
        return ConversationHandler.END

    # FIX: upsert=True so admin is set even if user hasn't started the bot yet
    await users_col.update_one(
        {"user_id": uid},
        {"$set": {"is_admin": True}},
        upsert=True,
    )
    try:
        await update.get_bot().send_message(
            uid,
            "🎉 You have been made *Admin*! You can now access the Admin Panel.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass
    await update.message.reply_text(f"✅ User `{uid}` is now Admin.", parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END

@owner_only
async def cb_admin_remove_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await safe_edit(
        query,
        "➖ *Remove Admin*\n\nSend the user ID:",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ADMIN_REMOVE_ADMIN_ID

async def admin_remove_admin_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        uid = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid ID:", parse_mode=ParseMode.MARKDOWN)
        return ADMIN_REMOVE_ADMIN_ID

    await users_col.update_one({"user_id": uid}, {"$set": {"is_admin": False}})
    try:
        await update.get_bot().send_message(
            uid,
            "⚠️ Your *Admin* access has been removed.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception:
        pass
    await update.message.reply_text(f"✅ Admin access removed from `{uid}`.", parse_mode=ParseMode.MARKDOWN)
    return ConversationHandler.END

# ─────────────────────────────────────────────────────────────
# ⏰ Auto-Restart Toggle
# ─────────────────────────────────────────────────────────────

async def cb_toggle_auto_restart(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    uid = query.from_user.id
    name = query.data.split(":", 1)[1]

    p = await get_project(uid, name)
    if not p:
        await query.answer("❌ Project not found.", show_alert=True)
        return

    current = p.get("auto_restart", True)
    new_val = not current

    await projects_col.update_one(
        {"user_id": uid, "name": name},
        {"$set": {"auto_restart": new_val}},
    )

    status = "✅ ON" if new_val else "❌ OFF"
    await query.answer(f"Auto-Restart: {status}", show_alert=True)

    p = await get_project(uid, name)
    _up2 = await is_premium(uid) or uid == OWNER_ID
    await safe_edit(
        query,
        project_dashboard_text(p),
        reply_markup=project_dashboard_kb(uid, name, p.get("auto_restart", True),
                                          p.get("status") == "running", p.get("locked", False),
                                          user_premium=_up2),
    )

# ─────────────────────────────────────────────────────────────
# 🔐 Environment Variables Manager
# ─────────────────────────────────────────────────────────────

async def cb_envvars(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    name = query.data.split(":", 1)[1]

    if await is_banned(uid):
        await safe_edit(query, "🚫 You are banned. Contact owner.")
        return

    pdir = project_dir(uid, name)
    env_path = os.path.join(pdir, ".env")

    env_vars = {}
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, _, value = line.partition("=")
                    env_vars[key.strip()] = value.strip()

    if not env_vars:
        text = f"🔐 *Environment Variables — {escape_md(name)}*\n\nNo variables set yet.\n\n_Tip: Click Add Variable and send like:_\n`BOT_TOKEN=your_value`"
    else:
        lines = [f"🔐 *Environment Variables — {escape_md(name)}*\n"]
        for key, value in env_vars.items():
            masked = value[:3] + "***" if len(value) > 3 else "***"
            lines.append(f"• `{key}` = `{masked}`")
        text = "\n".join(lines)

    kb_rows = []
    for key in env_vars:
        kb_rows.append([
            InlineKeyboardButton(f"✏️ {key}", callback_data=f"env_edit:{name}:{key}"),
            InlineKeyboardButton(f"🗑 {key}", callback_data=f"env_del:{name}:{key}"),
        ])
    kb_rows.append([InlineKeyboardButton("➕ Add Variable", callback_data=f"env_add:{name}")])
    kb_rows.append([InlineKeyboardButton("🔙 Back", callback_data=f"proj:{name}")])

    await safe_edit(query, text, reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode=ParseMode.MARKDOWN)

async def cb_env_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    name = query.data.split(":", 1)[1]
    context.user_data["env_project"] = name

    await safe_edit(
        query,
        "➕ *Add Environment Variables*\n\n"
        "Send in any format:\n\n"
        "1️⃣ *Single:*\n`API_KEY=your_value`\n\n"
        "2️⃣ *Multiple (one per line):*\n`TOKEN=abc123`\n`DB_URI=mongodb://...`\n\n"
        "3️⃣ *Just key name:*\n`API_KEY`\n_(bot will ask for value next)_",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"envvars:{name}")]]),
        parse_mode=ParseMode.MARKDOWN,
    )
    return ENV_ADD_KEY

async def env_add_key(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    name = context.user_data.get("env_project")
    uid = update.effective_user.id
    pdir = project_dir(uid, name)
    env_path = os.path.join(pdir, ".env")

    lines = text.strip().split("\n")
    pairs_to_save = []

    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip()
            if key:
                pairs_to_save.append((key, value))

    if pairs_to_save:
        # Read existing env file
        existing = {}
        existing_order = []
        if os.path.exists(env_path):
            with open(env_path, "r") as f:
                for eline in f:
                    eline_stripped = eline.strip()
                    if eline_stripped and not eline_stripped.startswith("#") and "=" in eline_stripped:
                        ekey, _, evalue = eline_stripped.partition("=")
                        ekey = ekey.strip()
                        existing[ekey] = evalue.strip()
                        if ekey not in existing_order:
                            existing_order.append(ekey)

        # Update/add new pairs
        for key, value in pairs_to_save:
            existing[key] = value
            if key not in existing_order:
                existing_order.append(key)

        # FIX: Write all vars back (removed duplicate write bug)
        with open(env_path, "w") as f:
            for key in existing_order:
                f.write(f"{key}={existing[key]}\n")

        saved_keys = [k for k, v in pairs_to_save]
        saved_list = "\n".join([f"• `{k}` ✅" for k in saved_keys])

        await update.message.reply_text(
            f"✅ *{len(pairs_to_save)} variable(s) saved!*\n\n"
            f"{saved_list}\n\n"
            f"_Restart your project for changes to take effect._",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Add More", callback_data=f"env_add:{name}")],
                [InlineKeyboardButton("🔙 Back to Env Vars", callback_data=f"envvars:{name}")],
            ]),
        )
        context.user_data.pop("env_key", None)
        context.user_data.pop("env_project", None)
        return ConversationHandler.END

    key = text.strip().split()[0] if text.strip() else ""
    if not key or len(key) > 100:
        await update.message.reply_text(
            "❌ Could not parse variables.\n\n"
            "Send like: `API_KEY=your_value`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ENV_ADD_KEY

    context.user_data["env_key"] = key
    await update.message.reply_text(
        f"Now send the value for `{key}`:",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ENV_ADD_VALUE

async def env_add_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    value = update.message.text.strip()
    name = context.user_data.get("env_project")
    key = context.user_data.get("env_key")
    uid = update.effective_user.id

    pdir = project_dir(uid, name)
    env_path = os.path.join(pdir, ".env")

    env_lines = []
    key_found = False
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                if line.strip().startswith(f"{key}="):
                    env_lines.append(f"{key}={value}\n")
                    key_found = True
                else:
                    env_lines.append(line)

    if not key_found:
        env_lines.append(f"{key}={value}\n")

    with open(env_path, "w") as f:
        f.writelines(env_lines)

    await update.message.reply_text(
        f"✅ Variable `{key}` saved!\n\n_Restart your project for changes to take effect._",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Env Vars", callback_data=f"envvars:{name}")]]),
    )
    context.user_data.pop("env_key", None)
    context.user_data.pop("env_project", None)
    return ConversationHandler.END

async def cb_env_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":", 2)
    name = parts[1]
    key = parts[2]
    context.user_data["env_project"] = name
    context.user_data["env_key"] = key

    await safe_edit(
        query,
        f"✏️ *Edit `{key}`*\n\nSend the new value:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"envvars:{name}")]]),
        parse_mode=ParseMode.MARKDOWN,
    )
    return ENV_EDIT_VALUE

async def env_edit_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await env_add_value(update, context)

async def cb_env_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    # NOTE: Do NOT call query.answer() here — we call it below with show_alert=True
    # Calling it twice causes the popup to silently fail (Telegram only allows one answer per query)
    parts = query.data.split(":", 2)
    name = parts[1]
    key = parts[2]
    uid = query.from_user.id

    pdir = project_dir(uid, name)
    env_path = os.path.join(pdir, ".env")

    deleted = False
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            lines = f.readlines()
        with open(env_path, "w") as f:
            for line in lines:
                if line.strip().startswith(f"{key}="):
                    deleted = True
                    continue
                f.write(line)

    if deleted:
        await query.answer(f"🗑 {key} deleted!", show_alert=True)
    else:
        await query.answer(f"⚠️ {key} not found.", show_alert=True)

    # Refresh the env vars screen
    env_vars = {}
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env_vars[k.strip()] = v.strip()

    if not env_vars:
        text = (
            f"🔐 *Environment Variables — {escape_md(name)}*\n\n"
            f"No variables set yet.\n\n"
            f"_Tip: Click Add Variable and send like:_\n`BOT_TOKEN=your_value`"
        )
    else:
        lines_out = [f"🔐 *Environment Variables — {escape_md(name)}*\n"]
        for k, v in env_vars.items():
            masked = v[:3] + "***" if len(v) > 3 else "***"
            lines_out.append(f"• `{k}` = `{masked}`")
        text = "\n".join(lines_out)

    kb_rows = []
    for k in env_vars:
        kb_rows.append([
            InlineKeyboardButton(f"✏️ {k}", callback_data=f"env_edit:{name}:{k}"),
            InlineKeyboardButton(f"🗑 {k}", callback_data=f"env_del:{name}:{k}"),
        ])
    kb_rows.append([InlineKeyboardButton("➕ Add Variable", callback_data=f"env_add:{name}")])
    kb_rows.append([InlineKeyboardButton("🔙 Back", callback_data=f"proj:{name}")])

    await safe_edit(query, text, reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────────────────────
# 🔄 Process Monitor (auto-restart, crash notifications)
# ─────────────────────────────────────────────────────────────

async def process_monitor():
    first_tick = True
    while True:
        # FIX: first tick waits 30s — gives auto_restart_on_startup (runs at 5s) time to
        # finish restarting all projects before we check PIDs for the first time.
        # FIX: subsequent ticks run every 10s (was 60s) — a crashed project used to sit
        # "offline" in My Projects / Admin Panel for up to a full minute before this loop
        # noticed and auto-restarted it. 10s keeps status accurate without excess CPU use.
        await asyncio.sleep(30 if first_tick else 10)
        first_tick = False
        try:
            # ── Webhook deploy queue processing ──
            from file_manager import pending_webhooks
            while pending_webhooks:
                deploy = pending_webhooks.pop(0)
                try:
                    uid  = deploy["user_id"]
                    name = deploy["project_name"]
                    p    = await get_project(uid, name)
                    if p:
                        await kill_project(uid, name)
                        await asyncio.sleep(1)
                        if p.get("run_command"):
                            updated = await start_project_process(uid, name)
                            if notification_bot:
                                try:
                                    await notification_bot.send_message(
                                        chat_id=uid,
                                        text=(
                                            f"🔄 *Auto-Deploy — {name}*\n\n"
                                            f"✅ GitHub push detected!\n"
                                            f"📦 Pulled latest code\n"
                                            f"🚀 Project restarted\n\n"
                                            f"```\n{deploy.get('pull_output','')[:300]}\n```"
                                        ),
                                        parse_mode="Markdown",
                                    )
                                except Exception:
                                    pass
                except Exception as e:
                    logger.error(f"Webhook deploy failed for {deploy}: {e}")

            # OPT: projection — only fetch fields process_monitor actually reads
            _mon_proj = {"user_id":1,"name":1,"pid":1,"auto_restart":1,"run_command":1,
                         "restart_count":1,"last_restart_at":1,"uptime_total":1,
                         "started_at":1,"notif_crash":1,"notif_restart":1,
                         "admin_stopped":1,"locked":1,"crash_count":1,"_id":0}
            running = await projects_col.find({"status": "running"}, _mon_proj).to_list(length=1000)
            for p in running:
                pid = p.get("pid")
                key = f"{p['user_id']}:{p['name']}"
                proc = context_store.get(key)

                # ── Free plan 12hr auto-stop (check running projects) ─────────
                if not p.get("admin_stopped") and not p.get("locked") and p.get("started_at"):
                    try:
                        _as_plan = await get_user_plan(p["user_id"])
                        _as_hrs  = PLANS[_as_plan]["autostop_hours"]
                        if _as_hrs > 0:
                            _as_st = p["started_at"]
                            if _as_st.tzinfo is None:
                                _as_st = _as_st.replace(tzinfo=timezone.utc)
                            _as_el = (datetime.now(timezone.utc) - _as_st).total_seconds()
                            if _as_el >= _as_hrs * 3600:
                                logger.info("Auto-stop (free plan): " + str(p["user_id"]) + ":" + p["name"])
                                await kill_project(p["user_id"], p["name"])
                                await projects_col.update_one(
                                    {"user_id": p["user_id"], "name": p["name"]},
                                    {"$set": {"status": "stopped", "pid": None, "admin_stopped": True}},
                                )
                                context_store.pop(key, None)
                                if notification_bot:
                                    try:
                                        await notification_bot.send_message(
                                            p["user_id"],
                                            "⏱ *Auto-Stop: " + escape_md(p["name"]) + "*\n\n"
                                            "Your project ran for " + str(_as_hrs) + " hours (Free plan limit).\n\n"
                                            "💎 Upgrade to *Basic* or higher to remove auto-stop!",
                                            parse_mode=ParseMode.MARKDOWN,
                                            reply_markup=InlineKeyboardMarkup([
                                                [InlineKeyboardButton("💎 Upgrade Plan", callback_data="plans")],
                                            ]),
                                        )
                                    except Exception:
                                        pass
                                continue
                    except Exception as _e_as:
                        logger.error("Auto-stop check: " + str(_e_as))

                # ── Determine if process is actually dead ──────────────────────
                # Priority 1: context_store has the real asyncio Process object.
                #   returncode is None  → OS confirms still running  → skip.
                #   returncode is not None → OS confirmed exit.
                # Priority 2: psutil fallback (used after bot restart when
                #   context_store is empty but pid was restored from DB).
                if proc is not None:
                    # We have the actual subprocess object — trust it completely.
                    if proc.returncode is None:
                        # Definitely still running — do nothing.
                        continue
                    code = proc.returncode
                else:
                    # No subprocess object (bot restart scenario).
                    # Priority: psutil.pid_exists → find_project_process (VPS-safe)
                    if pid and psutil.pid_exists(pid):
                        continue                          # psutil says alive, skip
                    # PID stale/missing — search by project directory
                    _mdir = project_dir(p["user_id"], p["name"])
                    _mfound, _mreal = find_project_process(_mdir)
                    if _mfound:
                        if _mreal and _mreal != pid:
                            await projects_col.update_one(
                                {"user_id": p["user_id"], "name": p["name"]},
                                {"$set": {"pid": _mreal}},
                            )
                        continue                          # process alive, skip
                    if not pid:
                        continue                          # no pid and not found, skip
                    code = None                          # unknown exit code

                # ── Process confirmed dead — update DB ────────────────────────
                # Track uptime + crash stats
                now = datetime.now(timezone.utc)
                uptime_fields: dict = {"status": "stopped", "pid": None, "exit_code": code}
                if p.get("started_at"):
                    try:
                        started = p["started_at"]
                        if started.tzinfo is None:
                            started = started.replace(tzinfo=timezone.utc)
                        sess = (now - started).total_seconds()
                        uptime_fields["uptime_total"] = p.get("uptime_total", 0.0) + max(0.0, sess)
                    except Exception:
                        pass
                is_crash = code is not None and code != 0
                if is_crash:
                    uptime_fields["crash_count"] = p.get("crash_count", 0) + 1
                    uptime_fields["last_crash_at"] = now

                await projects_col.update_one(
                    {"user_id": p["user_id"], "name": p["name"]},
                    {"$set": uptime_fields},
                )
                context_store.pop(key, None)  # RAM optimization: remove stale entry

                logger.info(f"Process {key} exited with code {code}")

                def _read_log_tail(uid, name, n=25):
                    lp = os.path.join(project_dir(uid, name), "output.log")
                    if not os.path.exists(lp):
                        return ""
                    with open(lp, "r", errors="replace") as _f:
                        _lines = _f.readlines()
                    tail = "".join(_lines[-n:]).strip()
                    if len(tail) > 800:
                        tail = "..." + tail[-800:]
                    return tail

                # Auto-restart logic
                if p.get("auto_restart", True) and is_crash and not p.get("admin_stopped") and not p.get("locked"):
                    last_restart = p.get("last_restart_at")
                    restart_count = p.get("restart_count", 0)

                    if last_restart:
                        if last_restart.tzinfo is None:
                            last_restart = last_restart.replace(tzinfo=timezone.utc)
                        if (now - last_restart).total_seconds() > 300:
                            restart_count = 0

                    if restart_count < 3:
                        try:
                            logger.info(f"Auto-restarting {key} (attempt {restart_count + 1}/3)")
                            await asyncio.sleep(3)
                            await start_project_process(p["user_id"], p["name"])
                            await projects_col.update_one(
                                {"user_id": p["user_id"], "name": p["name"]},
                                {"$set": {"restart_count": restart_count + 1, "last_restart_at": now}},
                            )
                            if notification_bot and p.get("notif_restart", True):
                                try:
                                    log_tail = _read_log_tail(p["user_id"], p["name"])
                                    log_section = f"\n\n📋 *Last Logs:*\n```\n{log_tail}\n```" if log_tail else ""
                                    await notification_bot.send_message(
                                        chat_id=p["user_id"],
                                        text=(
                                            f"🔄 *Auto-Restart*\n\n"
                                            f"Project `{p['name']}` crashed (exit `{code}`).\n"
                                            f"Auto-restarted! ({restart_count + 1}/3)"
                                            f"{log_section}"
                                        ),
                                        parse_mode=ParseMode.MARKDOWN,
                                    )
                                except Exception:
                                    pass
                        except Exception as e:
                            logger.error(f"Auto-restart failed for {key}: {e}")
                    else:
                        logger.warning(f"Auto-restart limit reached for {key}")
                        if notification_bot and p.get("notif_crash", True):
                            try:
                                log_tail = _read_log_tail(p["user_id"], p["name"])
                                log_section = f"\n\n📋 *Last Logs:*\n```\n{log_tail}\n```" if log_tail else ""
                                await notification_bot.send_message(
                                    chat_id=p["user_id"],
                                    text=(
                                        f"⚠️ *Auto-Restart Limit Reached*\n\n"
                                        f"Project `{p['name']}` crashed {restart_count} times in 5 min.\n"
                                        f"Auto-restart paused. Restart manually."
                                        f"{log_section}"
                                    ),
                                    parse_mode=ParseMode.MARKDOWN,
                                )
                            except Exception:
                                pass

                elif is_crash and not p.get("admin_stopped") and not p.get("locked"):
                    if notification_bot and p.get("notif_crash", True):
                        try:
                            log_tail = _read_log_tail(p["user_id"], p["name"])
                            log_section = f"\n\n📋 *Last Logs:*\n```\n{log_tail}\n```" if log_tail else ""
                            total_crashes = uptime_fields.get("crash_count", p.get("crash_count", 0) + 1)
                            msg_text = (
                                f"❌ *Project Crashed*\n\n"
                                f"Project: `{p['name']}`\n"
                                f"Exit Code: `{code}`\n"
                                f"Total Crashes: `{total_crashes}`\n"
                                f"Auto-Restart: OFF"
                                f"{log_section}"
                            )
                            if len(msg_text) > 4000:
                                msg_text = msg_text[:4000] + "..."
                            await notification_bot.send_message(
                                chat_id=p["user_id"],
                                text=msg_text,
                                parse_mode=ParseMode.MARKDOWN,
                            )
                        except Exception:
                            pass

        except Exception as e:
            logger.warning(f"Monitor error: {e}")

# ─────────────────────────────────────────────────────────────
# 💾 Auto Backup Task
# ─────────────────────────────────────────────────────────────

async def backup_task():
    while True:
        await asyncio.sleep(300)
        try:
            # OPT: projection — backup_task only needs uid+name to walk filesystem
            all_projects = await projects_col.find(
                {}, {"user_id": 1, "name": 1, "_id": 0}
            ).to_list(length=10000)
            db_distribution = {}
            total_files = 0
            total_size  = 0

            for proj in all_projects:
                uid  = proj["user_id"]
                name = proj["name"]
                pdir = project_dir(uid, name)

                if not os.path.exists(pdir):
                    continue

                files_data = []
                for root, dirs, files in os.walk(pdir):
                    dirs[:] = [d for d in dirs if d not in ("venv", "__pycache__", ".git", "node_modules")]
                    for fname in files:
                        if fname in ("output.log",) or fname.endswith(".pyc"):
                            continue
                        fpath    = os.path.join(root, fname)
                        rel_path = os.path.relpath(fpath, pdir)
                        try:
                            # FIX: check file size BEFORE reading into memory to prevent OOM on large files
                            file_size = os.path.getsize(fpath)
                            if file_size > 15 * 1024 * 1024:
                                continue
                            try:
                                with open(fpath, "r", encoding="utf-8") as f:
                                    content = f.read()
                                content_b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
                                is_binary = False
                            except (UnicodeDecodeError, ValueError):
                                with open(fpath, "rb") as f:
                                    content_bytes = f.read()
                                content_b64 = base64.b64encode(content_bytes).decode("ascii")
                                is_binary = True

                            files_data.append({
                                "path": rel_path, "content_b64": content_b64,
                                "size": file_size, "is_binary": is_binary,
                            })
                            total_files += 1
                            total_size  += file_size
                        except Exception:
                            continue

                if files_data:
                    target_db_name, target_col = pick_backup_col(uid, name)
                    for col in all_backup_cols():
                        try:
                            await col.delete_many({"type": "file_backup", "user_id": uid, "project_name": name})
                        except Exception:
                            pass
                    await target_col.insert_one({
                        "type": "file_backup", "user_id": uid,
                        "project_name": name, "files": files_data,
                        "backed_up_at": datetime.now(timezone.utc),
                        "stored_in": target_db_name,
                    })
                    db_distribution[target_db_name] = db_distribution.get(target_db_name, 0) + 1

            await backups_col.delete_many({"type": "backup_meta"})
            await backups_col.insert_one({
                "type": "backup_meta", "total_projects": len(all_projects),
                "total_files": total_files, "total_size": total_size,
                "backed_up_at": datetime.now(timezone.utc),
                "distribution": db_distribution,
            })
            logger.info(f"Auto backup: {len(all_projects)} projects, {total_files} files — distribution: {db_distribution}")

        except Exception as e:
            logger.error(f"Backup failed: {e}")

# ─────────────────────────────────────────────────────────────
# 🧹 RAM Optimization Task
# ─────────────────────────────────────────────────────────────

async def ram_cleanup_task():
    """Periodic RAM cleanup: rotate logs, clean context_store, force GC."""
    while True:
        await asyncio.sleep(600)  # OPT: was 120s — GC every 10 min is plenty
        try:
            # Clean up stale context_store entries (processes that no longer exist)
            stale_keys = []
            for key, proc in list(context_store.items()):
                try:
                    if proc.returncode is not None:
                        stale_keys.append(key)
                    elif proc.pid and not psutil.pid_exists(proc.pid):
                        # Double-check via find_project_process before removing
                        parts = key.split(":", 1)
                        if len(parts) == 2:
                            try:
                                _pdir = project_dir(int(parts[0]), parts[1])
                                _alive, _ = find_project_process(_pdir)
                                if not _alive:
                                    stale_keys.append(key)
                            except Exception:
                                stale_keys.append(key)
                        else:
                            stale_keys.append(key)
                except Exception:
                    stale_keys.append(key)
            for key in stale_keys:
                context_store.pop(key, None)
            if stale_keys:
                logger.info(f"RAM cleanup: removed {len(stale_keys)} stale context_store entries")

            # Rotate large log files for all projects
            try:
                for user_dir in os.listdir(PROJECTS_ROOT):
                    user_path = os.path.join(PROJECTS_ROOT, user_dir)
                    if not os.path.isdir(user_path):
                        continue
                    for proj_dir in os.listdir(user_path):
                        log_path = os.path.join(user_path, proj_dir, "output.log")
                        rotate_log_if_needed(log_path)
            except Exception:
                pass

            # Force garbage collection
            gc.collect()

        except Exception as e:
            logger.warning(f"RAM cleanup error: {e}")

# ─────────────────────────────────────────────────────────────
# 🔄 Keep-Alive Task
# ─────────────────────────────────────────────────────────────

async def keep_alive_task():
    """Ping health endpoint every 5 minutes, clean expired tokens."""
    health_url = f"{BASE_URL}/health"
    logger.info(f"Keep-alive task started. Pinging {health_url} every 5 minutes.")

    while True:
        # OPT: 5 min interval (was 2 min) — 60% fewer pings, still keeps host alive
        await asyncio.sleep(300)

        try:
            result = await tokens_col.delete_many(
                {"expires_at": {"$lt": datetime.now(timezone.utc)}}
            )
            if result.deleted_count:
                logger.info(f"Cleaned {result.deleted_count} expired tokens")
        except Exception as e:
            logger.warning(f"Token cleanup failed: {e}")

        try:
            # OPT: async ping — urllib.request was blocking the event loop
            proc = await asyncio.wait_for(
                create_subprocess_exec(
                    "curl", "-sf", "--max-time", "10", health_url,
                    stdout=PIPE, stderr=PIPE,
                ),
                timeout=15,
            )
            await proc.communicate()
            logger.info(f"Keep-alive ping OK (rc={proc.returncode})")
        except Exception as e:
            logger.warning(f"Keep-alive ping failed: {e}")

# ─────────────────────────────────────────────────────────────
# 🔄 Auto Restore (startup)
# ─────────────────────────────────────────────────────────────

async def restore_from_backup():
    try:
        logger.info("Checking for backups to restore...")

        meta = await backups_col.find_one({"type": "backup_meta"})
        if not meta:
            logger.info("No backup found. Fresh start.")
            return

        logger.info(
            f"Found backup from {meta['backed_up_at']} — "
            f"{meta['total_projects']} projects, {meta['total_files']} files"
        )

        seen = {}
        for col in all_backup_cols():
            try:
                async for backup in col.find({"type": "file_backup"}):
                    key = (backup["user_id"], backup["project_name"])
                    existing = seen.get(key)
                    if (existing is None
                            or backup.get("backed_up_at", datetime.min.replace(tzinfo=timezone.utc))
                                > existing.get("backed_up_at", datetime.min.replace(tzinfo=timezone.utc))):
                        seen[key] = backup
            except Exception as e:
                logger.warning(f"Restore read failed on one DB: {e}")

        restored_projects = 0
        restored_files    = 0

        for backup in seen.values():
            uid  = backup["user_id"]
            name = backup["project_name"]
            pdir = project_dir(uid, name)
            os.makedirs(pdir, exist_ok=True)

            for file_data in backup.get("files", []):
                rel_path    = file_data["path"]
                content_b64 = file_data["content_b64"]
                is_binary   = file_data.get("is_binary", False)
                file_path = os.path.join(pdir, rel_path)
                parent_dir = os.path.dirname(file_path)
                if parent_dir:
                    os.makedirs(parent_dir, exist_ok=True)
                try:
                    decoded = base64.b64decode(content_b64)
                    if is_binary:
                        with open(file_path, "wb") as f:
                            f.write(decoded)
                    else:
                        with open(file_path, "w", encoding="utf-8") as f:
                            f.write(decoded.decode("utf-8"))
                    restored_files += 1
                except Exception as e:
                    logger.warning(f"Failed to restore {rel_path}: {e}")

            restored_projects += 1

        logger.info(f"Files restored: {restored_projects} projects, {restored_files} files")
        asyncio.create_task(setup_venvs_background())
        # NOTE: auto_restart_on_startup is now called from post_init directly — not here

    except Exception as e:
        logger.error(f"Restore failed (non-fatal): {e}")

async def _install_requirements_for_project(uid: int, name: str) -> tuple:
    pdir     = project_dir(uid, name)
    req_path = os.path.join(pdir, "requirements.txt")
    pkg_json = os.path.join(pdir, "package.json")
    venv_dir = os.path.join(pdir, "venv")
    pip_path = os.path.join(venv_dir, "bin", "pip")

    if os.path.exists(pkg_json) and not os.path.exists(req_path):
        try:
            proc = await asyncio.wait_for(
                create_subprocess_exec("npm", "install", "--no-audit", "--no-fund", "--silent",
                                       stdout=PIPE, stderr=PIPE, cwd=pdir),
                timeout=300,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
            if proc.returncode == 0:
                return (True, "npm install success")
            else:
                return (False, f"npm install failed: {stderr.decode()[:200]}")
        except asyncio.TimeoutError:
            return (False, "npm install timed out")
        except Exception as e:
            return (False, f"npm error: {e}")

    if not os.path.exists(req_path):
        return (True, "no requirements file, skip")

    # DATA SAVE: skip pip install if requirements.txt hasn't changed since last install
    import hashlib as _hashlib
    hash_cache_path = os.path.join(pdir, ".req_hash")
    try:
        cur_hash = _hashlib.md5(open(req_path, "rb").read()).hexdigest()
        if os.path.exists(hash_cache_path):
            saved_hash = open(hash_cache_path).read().strip()
            if saved_hash == cur_hash and os.path.exists(pip_path):
                return (True, "requirements unchanged — skipped install")
    except Exception:
        cur_hash = None

    if not os.path.exists(pip_path):
        try:
            proc = await asyncio.wait_for(
                create_subprocess_exec(sys.executable, "-m", "venv", venv_dir, stdout=PIPE, stderr=PIPE),
                timeout=120,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            if proc.returncode != 0:
                return (False, f"venv create failed: {stderr.decode()[:200]}")
        except Exception as e:
            return (False, f"venv error: {e}")

    try:
        proc = await asyncio.wait_for(
            # OPT: -q suppresses verbose output — less stdout memory usage
        create_subprocess_exec(pip_path, "install", "-q", "-r", req_path, stdout=PIPE, stderr=PIPE, cwd=pdir),
            timeout=300,
        )
        _, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        if proc.returncode == 0:
            # Save hash so next startup skips download if unchanged
            if cur_hash:
                try:
                    open(hash_cache_path, "w").write(cur_hash)
                except Exception:
                    pass
            return (True, "pip install success")
        else:
            return (False, f"pip install failed: {stderr.decode()[:300]}")
    except asyncio.TimeoutError:
        return (False, "pip install timed out")
    except Exception as e:
        return (False, f"pip error: {e}")

async def auto_restart_on_startup():
    """Restart projects that were running before bot restart. Respects bot_lock & maintenance."""
    # FIX: sleep(5) not sleep(30) — must run BEFORE process_monitor's first tick (which is at 60s)
    # so that process_monitor finds correct PIDs, not stale ones from previous session.
    await asyncio.sleep(5)
    try:
        # Don't auto-restart if maintenance mode is on
        if await is_maintenance_mode():
            logger.info("Maintenance mode ON — skipping auto-restart on startup")
            return

        # OPT: projection — also fetch pid so we can kill old orphan before restarting
        _ar_proj = {"user_id":1,"name":1,"github_url":1,"github_last_pull":1,
                    "run_command":1,"auto_restart":1,"pid":1,"_id":0}
        running_projects = await projects_col.find({
            "status": "running",
            "admin_stopped": {"$ne": True},
            "locked": {"$ne": True},
        }, _ar_proj).to_list(length=10000)

        if not running_projects:
            logger.info("Auto-restart on startup: no running projects found.")
            return

        logger.info(f"Auto-restart on startup: {len(running_projects)} projects...")
        bot_locked = await is_bot_locked()

        for proj in running_projects:
            uid  = proj["user_id"]
            name = proj["name"]
            try:
                # Bot lock: skip free users' projects
                if bot_locked and uid != OWNER_ID:
                    user_prem = await is_premium(uid)
                    if not user_prem:
                        await projects_col.update_one(
                            {"user_id": uid, "name": name},
                            {"$set": {"status": "stopped", "pid": None}},
                        )
                        logger.info(f"Skipped {uid}:{name} — bot locked and user is free")
                        continue

                # Kill old orphan process (from previous bot session) before restarting
                # This prevents duplicate instances running simultaneously.
                # Use find_project_process for VPS/RDP where stored PID may be stale.
                old_pid = proj.get("pid")
                _ar_pdir = project_dir(uid, name)
                _ar_found, _ar_real = find_project_process(_ar_pdir)
                if _ar_found:
                    # Process actually still running — don't restart it, just update PID
                    if _ar_real and _ar_real != old_pid:
                        await projects_col.update_one(
                            {"user_id": uid, "name": name},
                            {"$set": {"pid": _ar_real}},
                        )
                        logger.info(f"Project {uid}:{name} already running (PID {_ar_real}), skip restart")
                    continue  # Skip restart — project is already up
                elif old_pid and psutil.pid_exists(old_pid):
                    try:
                        old_proc = psutil.Process(old_pid)
                        for child in old_proc.children(recursive=True):
                            try: child.kill()
                            except Exception: pass
                        old_proc.kill()
                        logger.info(f"Killed old orphan process PID {old_pid} for {uid}:{name}")
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass

                if notification_bot:
                    try:
                        await notification_bot.send_message(
                            chat_id=uid,
                            text=(
                                f"🔄 *Bot Restarted*\n\n"
                                f"Project `{name}` requirements are being installed...\n"
                                f"⏳ Your project will start automatically in a few moments."
                            ),
                            parse_mode=ParseMode.MARKDOWN,
                        )
                    except Exception:
                        pass

                # GitHub: git pull before starting — only if last pull was >6 hours ago
                if proj.get("github_url"):
                    try:
                        pdir = project_dir(uid, name)
                        git_dir = os.path.join(pdir, ".git")
                        last_pull = proj.get("github_last_pull")
                        pull_age  = (datetime.now(timezone.utc) - last_pull).total_seconds() if last_pull else 999999
                        if os.path.exists(git_dir) and pull_age > 21600:   # 6 hours
                            gp = await asyncio.wait_for(
                                create_subprocess_exec(
                                    "git", "-C", pdir, "pull", "--rebase",
                                    "--depth=1", "--no-tags",              # DATA SAVE: minimal fetch
                                    stdout=PIPE, stderr=PIPE,
                                ),
                                timeout=60,
                            )
                            await asyncio.wait_for(gp.communicate(), timeout=60)
                            logger.info(f"GitHub pull on startup {uid}:{name}: code={gp.returncode}")
                            await projects_col.update_one(
                                {"user_id": uid, "name": name},
                                {"$set": {"github_last_pull": datetime.now(timezone.utc)}},
                            )
                        else:
                            logger.info(f"Skip startup git pull {uid}:{name} — pulled {pull_age/3600:.1f}h ago")
                    except Exception as ge:
                        logger.warning(f"GitHub pull on startup failed {uid}:{name}: {ge}")

                logger.info(f"Installing requirements for {uid}:{name} before startup...")
                success, msg = await _install_requirements_for_project(uid, name)
                logger.info(f"Requirements for {uid}:{name}: {msg}")

                await asyncio.sleep(1)
                updated = await start_project_process(uid, name)
                logger.info(f"Auto-restarted on startup: {uid}:{name} PID={updated.get('pid')}")

                if notification_bot:
                    try:
                        req_status = "✅ Requirements installed" if success else f"⚠️ Issue: {msg[:100]}"
                        await notification_bot.send_message(
                            chat_id=uid,
                            text=(
                                f"✅ *Project Started*\n\n"
                                f"Project: `{name}`\n"
                                f"{req_status}\n"
                                f"🟢 Your project is running!"
                            ),
                            parse_mode=ParseMode.MARKDOWN,
                        )
                    except Exception:
                        pass

            except Exception as e:
                logger.error(f"Auto-restart on startup failed for {uid}:{name}: {e}")
                await projects_col.update_one(
                    {"user_id": uid, "name": name},
                    {"$set": {"status": "stopped", "pid": None}},
                )
                if notification_bot:
                    try:
                        await notification_bot.send_message(
                            chat_id=uid,
                            text=(
                                f"❌ *Project Start Failed*\n\n"
                                f"Project `{name}` could not start after bot restart.\n"
                                f"Error: `{str(e)[:200]}`\n\n"
                                f"Please start it manually."
                            ),
                            parse_mode=ParseMode.MARKDOWN,
                        )
                    except Exception:
                        pass

        logger.info("Auto-restart on startup complete.")
    except Exception as e:
        logger.error(f"auto_restart_on_startup failed: {e}")

async def setup_venvs_background():
    try:
        # OPT: only need uid+name to locate project dirs
        all_projects = await projects_col.find({}, {"user_id":1,"name":1,"_id":0}).to_list(length=10000)
        for proj in all_projects:
            uid  = proj["user_id"]
            name = proj["name"]
            pdir = project_dir(uid, name)
            venv_dir = os.path.join(pdir, "venv")

            if os.path.exists(pdir) and not os.path.exists(venv_dir):
                try:
                    proc = await create_subprocess_exec(
                        sys.executable, "-m", "venv", venv_dir, stdout=PIPE, stderr=PIPE
                    )
                    await asyncio.wait_for(proc.communicate(), timeout=120)

                    req_file = os.path.join(pdir, "requirements.txt")
                    pip_path = os.path.join(pdir, "venv", "bin", "pip")
                    if os.path.exists(req_file) and os.path.exists(pip_path):
                        proc2 = await create_subprocess_exec(
                            pip_path, "install", "-r", req_file, "--quiet",
                            stdout=PIPE, stderr=PIPE, cwd=pdir
                        )
                        await asyncio.wait_for(proc2.communicate(), timeout=300)
                    logger.info(f"Venv setup complete for {name}")
                except Exception as e:
                    logger.warning(f"Failed to setup venv for {name}: {e}")
    except Exception as e:
        logger.error(f"Background venv setup failed: {e}")

# ═══════════════════════════════════════════════════════════════
# 🐙 GITHUB DEPLOY
# ═══════════════════════════════════════════════════════════════

live_log_streams: dict = {}   # "uid:name" → asyncio.Task

async def cb_github(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    name = query.data.split(":", 1)[1]
    p = await get_project(uid, name)
    if not p:
        await safe_edit(query, "❌ Project not found.")
        return
    github_url = p.get("github_url")
    last_pull  = p.get("github_last_pull")
    pull_info  = f"\n🕐 Last Pull: `{last_pull.strftime('%Y-%m-%d %H:%M UTC')}`" if last_pull else ""
    if github_url:
        text = (f"🐙 *GitHub — {escape_md(name)}*\n\n"
                f"🔗 `{github_url.replace('.git','')}`{pull_info}")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔄 Pull Latest",      callback_data=f"github_pull:{name}")],
            [InlineKeyboardButton("✏️ Change URL",       callback_data=f"github_set:{name}")],
            [InlineKeyboardButton("🗑 Remove GitHub",    callback_data=f"github_remove:{name}")],
            [InlineKeyboardButton("🔙 Back",             callback_data=f"proj:{name}")],
        ])
    else:
        text = (f"🐙 *GitHub Deploy — {escape_md(name)}*\n\n"
                f"No GitHub repo linked.\nLink a repo to deploy directly from GitHub.")
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔗 Set GitHub URL",   callback_data=f"github_set:{name}")],
            [InlineKeyboardButton("🔙 Back",             callback_data=f"proj:{name}")],
        ])
    await safe_edit(query, text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def cb_github_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    name = query.data.split(":", 1)[1]
    context.user_data["github_project"] = name
    await safe_edit(
        query,
        f"🐙 *Set GitHub URL — {escape_md(name)}*\n\nSend the GitHub repo URL:\n`https://github.com/username/repo`",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"github:{name}")]]),
        parse_mode=ParseMode.MARKDOWN,
    )
    return GITHUB_URL

async def github_url_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    url  = update.message.text.strip()
    name = context.user_data.get("github_project", "")
    if not (url.startswith("https://github.com/") or url.startswith("http://github.com/")):
        await update.message.reply_text(
            "❌ Must be a GitHub URL:\n`https://github.com/username/repo`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return GITHUB_URL
    url = url.rstrip("/")
    if not url.endswith(".git"):
        url += ".git"
    await projects_col.update_one({"user_id": uid, "name": name}, {"$set": {"github_url": url}})
    await update.message.reply_text(
        f"✅ GitHub URL saved!\n🔗 `{url}`\n\nUse *Pull Latest* button to deploy.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END

async def cb_github_pull(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    name = query.data.split(":", 1)[1]
    p    = await get_project(uid, name)
    if not p or not p.get("github_url"):
        await safe_edit(query, "❌ No GitHub URL set.")
        return
    github_url   = p["github_url"]
    pdir         = project_dir(uid, name)
    was_running  = p.get("status") == "running"
    progress     = LiveProgress(query.message, title=f"GitHub Pull — {name}")
    await progress.start("Connecting to GitHub...")
    progress.run_in_background(estimated_seconds=120, status="Downloading repository...")
    try:
        if was_running:
            await kill_project(uid, name)
        git_dir = os.path.join(pdir, ".git")
        if os.path.exists(git_dir):
            proc = await asyncio.wait_for(
                create_subprocess_exec(
                    "git", "-C", pdir, "pull", "--rebase",
                    "--depth=1", "--no-tags",   # OPT: minimal fetch, no tag objects
                    stdout=PIPE, stderr=PIPE,
                ),
                timeout=120,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            if proc.returncode != 0:
                await progress.stop(success=False, final_text=f"git pull failed: {stderr.decode()[:200]}")
                return
        else:
            import tempfile, shutil
            with tempfile.TemporaryDirectory() as tmpdir:
                proc = await asyncio.wait_for(
                    create_subprocess_exec(
                        "git", "clone", "--depth=1", "--single-branch", "--no-tags",
                        github_url, tmpdir, stdout=PIPE, stderr=PIPE,
                    ),
                    timeout=180,
                )
                _, stderr = await asyncio.wait_for(proc.communicate(), timeout=180)
                if proc.returncode != 0:
                    await progress.stop(success=False, final_text=f"git clone failed: {stderr.decode()[:300]}")
                    return
                # Preserve .env and output.log, replace everything else
                env_bak = None
                env_path = os.path.join(pdir, ".env")
                if os.path.exists(env_path):
                    with open(env_path, "r") as _f:
                        env_bak = _f.read()
                for item in os.listdir(pdir):
                    if item in (".env", "output.log", "venv"):
                        continue
                    ip = os.path.join(pdir, item)
                    if os.path.isdir(ip):
                        shutil.rmtree(ip)
                    else:
                        os.remove(ip)
                for item in os.listdir(tmpdir):
                    if item == ".git":
                        continue
                    shutil.copytree(os.path.join(tmpdir, item), os.path.join(pdir, item)) \
                        if os.path.isdir(os.path.join(tmpdir, item)) \
                        else shutil.copy2(os.path.join(tmpdir, item), os.path.join(pdir, item))
                # Move .git into pdir so future pulls work
                shutil.copytree(os.path.join(tmpdir, ".git"), git_dir)
                if env_bak is not None:
                    with open(env_path, "w") as _f:
                        _f.write(env_bak)
        success, msg = await _install_requirements_for_project(uid, name)
        await projects_col.update_one(
            {"user_id": uid, "name": name},
            {"$set": {"github_last_pull": datetime.now(timezone.utc)}},
        )
        req_status = "✅ Requirements installed" if success else f"⚠️ {msg[:100]}"
        await progress.stop(success=True, final_text=f"✅ Pull done! {req_status}")
        await safe_edit(
            query,
            f"🐙 *GitHub Deploy — {escape_md(name)}*\n\n✅ Pull complete!\n{req_status}",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("▶️ Run Project", callback_data=f"run:{name}")],
                [InlineKeyboardButton("🔙 Back",        callback_data=f"proj:{name}")],
            ]),
            parse_mode=ParseMode.MARKDOWN,
        )
    except asyncio.TimeoutError:
        await progress.stop(success=False, final_text="❌ Git timed out (180s)")
    except Exception as e:
        logger.error(f"GitHub pull failed {uid}:{name}: {e}")
        await progress.stop(success=False, final_text=f"❌ Error: {str(e)[:200]}")

async def cb_github_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    name = query.data.split(":", 1)[1]
    await projects_col.update_one(
        {"user_id": uid, "name": name},
        {"$set": {"github_url": None, "github_last_pull": None}},
    )
    await safe_edit(
        query,
        f"✅ GitHub URL removed from *{escape_md(name)}*",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"proj:{name}")]]),
        parse_mode=ParseMode.MARKDOWN,
    )

# ═══════════════════════════════════════════════════════════════
# ⏰ CRON JOBS
# ═══════════════════════════════════════════════════════════════

def _parse_cron(expr: str, now: datetime) -> bool:
    """Parse simple cron expressions: HH:MM (daily), */N (every N min), * (every min)."""
    expr = expr.strip()
    if expr.startswith("*/"):
        try:
            n = int(expr[2:])
            return now.minute % n == 0
        except ValueError:
            return False
    if ":" in expr:
        try:
            h, m = map(int, expr.split(":", 1))
            return now.hour == h and now.minute == m
        except ValueError:
            return False
    return expr in ("*", "* *")

async def cron_task():
    """Background: run user cron jobs every minute."""
    logger.info("Cron task started.")
    while True:
        now  = datetime.now(timezone.utc)
        wait = 60 - now.second
        await asyncio.sleep(wait)
        now  = datetime.now(timezone.utc)
        try:
            # OPT: projection — cron_task only needs uid/name/status/cron_jobs
            all_proj = await projects_col.find(
                {"cron_jobs": {"$exists": True, "$ne": []}},
                {"user_id":1,"name":1,"status":1,"cron_jobs":1,"_id":0}
            ).to_list(length=10000)
            for p in all_proj:
                uid   = p["user_id"]
                name  = p["name"]
                pdir  = project_dir(uid, name)
                jobs  = p.get("cron_jobs", [])
                changed = False
                for i, job in enumerate(jobs):
                    if not job.get("enabled", True):
                        continue
                    expr = job.get("expr", "")
                    cmd  = job.get("cmd", "")
                    if not expr or not cmd or not _parse_cron(expr, now):
                        continue
                    try:
                        import shlex as _shlex
                        venv_py = os.path.join(pdir, "venv", "bin", "python")
                        parts   = _shlex.split(cmd)
                        if parts and parts[0] in ("python", "python3") and os.path.exists(venv_py):
                            parts[0] = venv_py
                        log_path = os.path.join(pdir, f"cron_{i}.log")
                        # FIX: open file handle once, pass to subprocess, then close → no fd leak
                        with open(log_path, "a") as lf:
                            lf.write(f"\n--- {now.strftime('%Y-%m-%d %H:%M UTC')} ---\n")
                        log_fd = open(log_path, "a")
                        try:
                            proc = await asyncio.wait_for(
                                create_subprocess_exec(
                                    *parts,
                                    stdout=log_fd,
                                    stderr=log_fd,
                                    cwd=pdir,
                                ),
                                timeout=300,
                            )
                        finally:
                            log_fd.close()
                        jobs[i]["last_run"] = now.strftime("%Y-%m-%d %H:%M UTC")
                        changed = True
                        logger.info(f"Cron ran '{cmd}' for {uid}:{name}")
                        if notification_bot:
                            try:
                                await notification_bot.send_message(
                                    chat_id=uid,
                                    text=f"⏰ *Cron Ran*\nProject: `{name}`\nCmd: `{cmd}`\nAt: `{now.strftime('%H:%M UTC')}`",
                                    parse_mode=ParseMode.MARKDOWN,
                                )
                            except Exception:
                                pass
                    except Exception as ce:
                        logger.error(f"Cron error {uid}:{name} '{cmd}': {ce}")
                if changed:
                    await projects_col.update_one(
                        {"user_id": uid, "name": name},
                        {"$set": {"cron_jobs": jobs}},
                    )
        except Exception as e:
            logger.warning(f"cron_task error: {e}")

async def cb_cron(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    name = query.data.split(":", 1)[1]
    p    = await get_project(uid, name)
    if not p:
        await safe_edit(query, "❌ Project not found.")
        return
    jobs = p.get("cron_jobs", [])
    lines, kb_rows = [], []
    for i, job in enumerate(jobs):
        st   = "✅" if job.get("enabled", True) else "❌"
        last = job.get("last_run") or "Never"
        lines.append(f"{st} `{job['expr']}` → `{job['cmd']}`\n   Last: {last}")
        kb_rows.append([
            InlineKeyboardButton(
                f"{'🟢' if job.get('enabled', True) else '🔴'} #{i+1} Toggle",
                callback_data=f"cron_toggle:{name}:{i}",
            ),
            InlineKeyboardButton(f"🗑 #{i+1}", callback_data=f"cron_del:{name}:{i}"),
        ])
    hint = "\n\n*Formats:*\n• `HH:MM` — daily (e.g. `03:00`)\n• `*/N` — every N min (e.g. `*/30`)"
    text = f"⏰ *Cron Jobs — {escape_md(name)}*\n\n" + ("\n\n".join(lines) if lines else "No cron jobs yet." + hint)
    kb_rows.append([InlineKeyboardButton("➕ Add Cron Job", callback_data=f"cron_add:{name}")])
    kb_rows.append([InlineKeyboardButton("🔙 Back",         callback_data=f"proj:{name}")])
    await safe_edit(query, text, reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode=ParseMode.MARKDOWN)

async def cb_cron_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    name = query.data.split(":", 1)[1]
    context.user_data["cron_project"] = name
    await safe_edit(
        query,
        f"⏰ *Add Cron Job — {escape_md(name)}*\n\nSend schedule:\n• `HH:MM` — daily (e.g. `03:00`)\n• `*/N` — every N min (e.g. `*/30`)\n• `*/1` — every minute",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"cron:{name}")]]),
        parse_mode=ParseMode.MARKDOWN,
    )
    return CRON_EXPR

async def cron_expr_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    expr = update.message.text.strip()
    name = context.user_data.get("cron_project", "")
    valid = False
    if ":" in expr:
        try:
            h, m = map(int, expr.split(":", 1))
            valid = 0 <= h <= 23 and 0 <= m <= 59
        except Exception:
            pass
    elif expr.startswith("*/"):
        try:
            n = int(expr[2:])
            valid = 1 <= n <= 1440
        except Exception:
            pass
    elif expr in ("*", "* *"):
        valid = True
    if not valid:
        await update.message.reply_text(
            "❌ Invalid. Use `HH:MM` (e.g. `03:00`) or `*/N` (e.g. `*/30`)",
            parse_mode=ParseMode.MARKDOWN,
        )
        return CRON_EXPR
    context.user_data["cron_expr"] = expr
    await update.message.reply_text(
        f"✅ Schedule: `{expr}`\n\nNow send the *command* to run:\n• `python script.py`\n• `node job.js`\n• `bash backup.sh`",
        parse_mode=ParseMode.MARKDOWN,
    )
    return CRON_CMD

async def cron_cmd_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    cmd  = update.message.text.strip()
    name = context.user_data.get("cron_project", "")
    expr = context.user_data.get("cron_expr", "")
    p    = await get_project(uid, name)
    if not p:
        await update.message.reply_text("❌ Project not found.")
        return ConversationHandler.END
    jobs = p.get("cron_jobs", [])
    jobs.append({
        "expr": expr, "cmd": cmd, "enabled": True,
        "last_run": None,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
    })
    await projects_col.update_one({"user_id": uid, "name": name}, {"$set": {"cron_jobs": jobs}})
    await update.message.reply_text(
        f"✅ *Cron job added!*\n\n⏰ `{expr}`\n💻 `{cmd}`",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END

async def cb_cron_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid   = query.from_user.id
    parts = query.data.split(":", 2)
    name, idx = parts[1], int(parts[2])
    p = await get_project(uid, name)
    if p:
        jobs = p.get("cron_jobs", [])
        if 0 <= idx < len(jobs):
            jobs[idx]["enabled"] = not jobs[idx].get("enabled", True)
            await projects_col.update_one({"user_id": uid, "name": name}, {"$set": {"cron_jobs": jobs}})
    query.data = f"cron:{name}"
    await cb_cron(update, context)

async def cb_cron_delete(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid   = query.from_user.id
    parts = query.data.split(":", 2)
    name, idx = parts[1], int(parts[2])
    p = await get_project(uid, name)
    if p:
        jobs = p.get("cron_jobs", [])
        if 0 <= idx < len(jobs):
            jobs.pop(idx)
            await projects_col.update_one({"user_id": uid, "name": name}, {"$set": {"cron_jobs": jobs}})
    query.data = f"cron:{name}"
    await cb_cron(update, context)

# ═══════════════════════════════════════════════════════════════
# 📺 LIVE LOG STREAMING
# ═══════════════════════════════════════════════════════════════

async def _live_log_task(uid: int, name: str, chat_id: int, message_id: int, bot):
    """Send live log tail updates every 3 s for max 5 min."""
    pdir      = project_dir(uid, name)
    log_path  = os.path.join(pdir, "output.log")
    key       = f"{uid}:{name}"
    start_ts  = datetime.now(timezone.utc)
    last_size = -1
    stop_kb   = InlineKeyboardMarkup([[InlineKeyboardButton("⏹ Stop", callback_data=f"live_stop:{name}")]])
    try:
        while True:
            if (datetime.now(timezone.utc) - start_ts).total_seconds() > 300:
                try:
                    await bot.edit_message_text(
                        chat_id=chat_id, message_id=message_id,
                        text=f"📺 *Live Logs — {name}*\n\n_Auto-stopped after 5 minutes._",
                        parse_mode=ParseMode.MARKDOWN,
                    )
                except Exception:
                    pass
                break
            if os.path.exists(log_path):
                cur = os.path.getsize(log_path)
                if cur != last_size:
                    last_size = cur
                    with open(log_path, "r", errors="replace") as f:
                        lines = f.readlines()
                    tail = "".join(lines[-30:]).strip()
                    if len(tail) > 3500:
                        tail = "..." + tail[-3500:]
                    try:
                        await bot.edit_message_text(
                            chat_id=chat_id, message_id=message_id,
                            text=f"📺 *Live Logs — {escape_md(name)}*\n```\n{tail}\n```",
                            reply_markup=stop_kb,
                            parse_mode=ParseMode.MARKDOWN,
                        )
                    except Exception:
                        pass
            await asyncio.sleep(5)  # OPT: was 3s — 40% fewer Telegram edits
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.warning(f"_live_log_task {key}: {e}")
    finally:
        live_log_streams.pop(key, None)

async def cb_live_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    name = query.data.split(":", 1)[1]
    key  = f"{uid}:{name}"
    if key in live_log_streams:
        live_log_streams[key].cancel()
    pdir     = project_dir(uid, name)
    log_path = os.path.join(pdir, "output.log")
    stop_kb  = InlineKeyboardMarkup([[InlineKeyboardButton("⏹ Stop Streaming", callback_data=f"live_stop:{name}")]])
    if not os.path.exists(log_path):
        await safe_edit(query, f"📺 No logs yet for *{escape_md(name)}*.", parse_mode=ParseMode.MARKDOWN)
        return
    with open(log_path, "r", errors="replace") as f:
        lines = f.readlines()
    tail = "".join(lines[-20:]).strip()
    if len(tail) > 3500:
        tail = "..." + tail[-3500:]
    await safe_edit(
        query,
        f"📺 *Live Logs — {escape_md(name)}*\n```\n{tail}\n```\n_Updates every 3s • Auto-stops in 5 min_",
        reply_markup=stop_kb,
        parse_mode=ParseMode.MARKDOWN,
    )
    msg  = query.message
    task = asyncio.create_task(_live_log_task(uid, name, msg.chat_id, msg.message_id, context.bot))
    live_log_streams[key] = task

async def cb_live_logs_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Streaming stopped.")
    uid  = query.from_user.id
    name = query.data.split(":", 1)[1]
    key  = f"{uid}:{name}"
    if key in live_log_streams:
        live_log_streams[key].cancel()
        live_log_streams.pop(key, None)
    await safe_edit(
        query,
        "⏹ *Live streaming stopped.*",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"proj:{name}")]]),
        parse_mode=ParseMode.MARKDOWN,
    )

# ═══════════════════════════════════════════════════════════════
# 🔁 PROJECT CLONE
# ═══════════════════════════════════════════════════════════════

async def cb_clone_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    name = query.data.split(":", 1)[1]
    context.user_data["clone_source"] = name
    await safe_edit(
        query,
        f"🔁 *Clone — {escape_md(name)}*\n\nSend a new project name:\n• Letters, numbers, underscore only\n• Max 20 chars",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"proj:{name}")]]),
        parse_mode=ParseMode.MARKDOWN,
    )
    return CLONE_NAME

async def clone_name_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid       = update.effective_user.id
    new_name  = update.message.text.strip()
    src_name  = context.user_data.get("clone_source", "")
    if not re.match(r"^[a-zA-Z0-9_]{1,20}$", new_name):
        await update.message.reply_text("❌ Only letters/numbers/underscore, max 20 chars.")
        return CLONE_NAME
    if await get_project(uid, new_name):
        await update.message.reply_text(f"❌ `{new_name}` already exists.", parse_mode=ParseMode.MARKDOWN)
        return CLONE_NAME
    src_p = await get_project(uid, src_name)
    if not src_p:
        await update.message.reply_text("❌ Source project not found.")
        return ConversationHandler.END
    src_dir = project_dir(uid, src_name)
    dst_dir = project_dir(uid, new_name)
    try:
        msg = await update.message.reply_text(
            f"🔁 Cloning *{escape_md(src_name)}* → *{escape_md(new_name)}*...",
            parse_mode=ParseMode.MARKDOWN,
        )
        # FIX: dst_dir must not exist before copytree; project_dir guarantees a new path but guard anyway
        if os.path.exists(dst_dir):
            raise FileExistsError(f"Destination {dst_dir} already exists")
        shutil.copytree(
            src_dir, dst_dir,
            ignore=shutil.ignore_patterns("venv", "__pycache__", "*.pyc", "output.log"),
        )
        await projects_col.insert_one({
            "user_id": uid, "name": new_name,
            "run_command":    src_p.get("run_command"),
            "created_date":   datetime.now(timezone.utc),
            "last_run": None, "exit_code": None,
            "status": "stopped", "pid": None,
            "admin_stopped": False,
            "auto_restart":  src_p.get("auto_restart", True),
            "restart_count": 0, "last_restart_at": None,
            "locked": False,
            "github_url":    src_p.get("github_url"),
            "github_last_pull": None,
            "cron_jobs":     [],
            "crash_count":   0,
            "uptime_total":  0.0,
            "last_crash_at": None,
            "notif_crash":   src_p.get("notif_crash", True),
            "notif_restart": src_p.get("notif_restart", True),
        })
        await msg.edit_text(
            f"✅ *Cloned!*\n\n`{src_name}` → `{new_name}`\n\nVenv not copied — run *Reinstall Requirements* before starting.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Clone failed: {str(e)[:200]}")
    return ConversationHandler.END

# ═══════════════════════════════════════════════════════════════
# 📊 PROJECT UPTIME STATS
# ═══════════════════════════════════════════════════════════════

async def cb_uptime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    name = query.data.split(":", 1)[1]
    p    = await get_project(uid, name)
    if not p:
        await safe_edit(query, "❌ Project not found.")
        return
    status       = p.get("status", "stopped")
    uptime_total = p.get("uptime_total", 0.0)
    crash_count  = p.get("crash_count", 0)
    cur_sess     = 0.0
    if status == "running" and p.get("started_at"):
        try:
            st = p["started_at"]
            if st.tzinfo is None:
                st = st.replace(tzinfo=timezone.utc)
            cur_sess = (datetime.now(timezone.utc) - st).total_seconds()
        except Exception:
            pass
    total_secs = uptime_total + cur_sess

    def _fmt(secs):
        secs = int(secs)
        d, r  = divmod(secs, 86400)
        h, r  = divmod(r, 3600)
        m, _  = divmod(r, 60)
        parts = []
        if d: parts.append(f"{d}d")
        if h: parts.append(f"{h}h")
        if m: parts.append(f"{m}m")
        return " ".join(parts) or "< 1m"

    lc_line = ""
    if p.get("last_crash_at"):
        try:
            lc = p["last_crash_at"]
            if lc.tzinfo is None:
                lc = lc.replace(tzinfo=timezone.utc)
            lc_line = f"\n🕐 Last Crash: `{lc.strftime('%Y-%m-%d %H:%M UTC')}`"
        except Exception:
            pass

    age_line = "N/A"
    if p.get("created_date"):
        try:
            cd = p["created_date"]
            if cd.tzinfo is None:
                cd = cd.replace(tzinfo=timezone.utc)
            age_line = f"{(datetime.now(timezone.utc) - cd).days}d ago"
        except Exception:
            pass

    text = (
        f"📊 *Uptime Stats — {escape_md(name)}*\n\n"
        f"🟢 Status: *{'Running' if status == 'running' else 'Stopped'}*\n"
        f"⏱ Current Session: `{_fmt(cur_sess)}`\n"
        f"📈 Total Uptime: `{_fmt(total_secs)}`\n"
        f"💥 Crash Count: `{crash_count}`\n"
        f"📅 Project Age: `{age_line}`"
        f"{lc_line}"
    )
    await safe_edit(
        query, text,
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"proj:{name}")]]),
        parse_mode=ParseMode.MARKDOWN,
    )

# ═══════════════════════════════════════════════════════════════
# 🔔 CUSTOM NOTIFICATIONS
# ═══════════════════════════════════════════════════════════════

async def cb_notif(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    name = query.data.split(":", 1)[1]
    p    = await get_project(uid, name)
    if not p:
        await safe_edit(query, "❌ Project not found.")
        return
    nc = p.get("notif_crash", True)
    nr = p.get("notif_restart", True)
    tf = lambda v: "✅ ON" if v else "❌ OFF"
    text = (
        f"🔔 *Notifications — {escape_md(name)}*\n\n"
        f"Choose which events send you a Telegram message:\n\n"
        f"💥 Crash Notify: *{tf(nc)}*\n"
        f"🔄 Restart Notify: *{tf(nr)}*"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"💥 Crash: {tf(nc)}",   callback_data=f"notif_toggle:{name}:crash")],
        [InlineKeyboardButton(f"🔄 Restart: {tf(nr)}", callback_data=f"notif_toggle:{name}:restart")],
        [InlineKeyboardButton("🔙 Back",                callback_data=f"proj:{name}")],
    ])
    await safe_edit(query, text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def cb_notif_toggle(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid   = query.from_user.id
    parts = query.data.split(":", 2)
    name, setting = parts[1], parts[2]
    field_map = {"crash": "notif_crash", "restart": "notif_restart"}
    field = field_map.get(setting)
    if field:
        p = await get_project(uid, name)
        if p:
            await projects_col.update_one(
                {"user_id": uid, "name": name},
                {"$set": {field: not p.get(field, True)}},
            )
    query.data = f"notif:{name}"
    await cb_notif(update, context)

# ─────────────────────────────────────────────────────────────
# 🔒 Premium Lock notice
# ─────────────────────────────────────────────────────────────

async def cb_premium_lock(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer(
        "🔒 Premium Feature!\n\nContact owner to upgrade to Premium and unlock all features.",
        show_alert=True,
    )

# ─────────────────────────────────────────────────────────────
# 🌐 Admin Domain Pool Manager (loca.lt subdomain creation)
# ─────────────────────────────────────────────────────────────

import re as _re_domain
import shutil as _shutil_domain

def _get_tunnel_base() -> str:
    """Return the localtunnel service's real base domain.

    FIX: this used to derive the base from BASE_URL (the bot's own webhook
    URL, e.g. your Repl's domain) — completely unrelated to the tunnel
    service's actual domain. The real `lt`/`localtunnel` client always
    responds with a `*.loca.lt` URL, so if BASE_URL wasn't itself a
    `*.loca.lt` address, the "expected" URL built from the wrong base never
    matched the tunnel's real URL and _start_domain_tunnel() silently failed
    (treated as "subdomain taken"), which is why custom domains never
    actually served the running project. Set TUNNEL_BASE_DOMAIN env var to
    override only if you're running your own private tunnel server.
    """
    return os.getenv("TUNNEL_BASE_DOMAIN", "loca.lt").strip().lower()


async def _start_domain_tunnel(subdomain: str) -> "tuple[str | None, object | None]":
    """
    Start a localtunnel process for *subdomain* pointing at PORT.
    Returns (actual_url, process) on success, (None, None) if the
    subdomain is taken or lt is not installed.
    """
    from asyncio import subprocess as _asp
    lt_bin = _shutil_domain.which("lt") or _shutil_domain.which("lt.cmd")
    if lt_bin:
        cmd = [lt_bin, "--port", str(PORT), "--subdomain", subdomain]
    else:
        npx = _shutil_domain.which("npx")
        if not npx:
            logger.error("localtunnel not found. Run: npm install -g localtunnel")
            return None, None
        cmd = [npx, "localtunnel", "--port", str(PORT), "--subdomain", subdomain]

    try:
        proc = await create_subprocess_exec(
            *cmd,
            stdout=_asp.PIPE,
            stderr=_asp.PIPE,
        )
        try:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=25)
            url_line = line.decode("utf-8", errors="replace").strip()
        except asyncio.TimeoutError:
            try: proc.kill()
            except Exception: pass
            return None, None

        m = _re_domain.search(r"https?://\S+", url_line)
        if not m:
            try: proc.kill()
            except Exception: pass
            return None, None

        actual_url = m.group(0).rstrip("/")
        base = _get_tunnel_base()
        expected = f"https://{subdomain}.{base}"
        if actual_url.lower() != expected.lower():
            try: proc.kill()
            except Exception: pass
            return None, None   # subdomain taken — got a random one

        return actual_url, proc
    except Exception as e:
        logger.error(f"_start_domain_tunnel({subdomain}): {e}")
        return None, None


async def _restore_domain_tunnels():
    """Restart all active domain tunnels after bot reboot."""
    try:
        docs = await domains_col.find({"active": True}).to_list(length=500)
        for doc in docs:
            sub  = doc["subdomain"]
            full = doc["full_domain"]
            url, proc = await _start_domain_tunnel(sub)
            if url and proc:
                domain_tunnels[full] = proc
                logger.info(f"Domain tunnel restored: {full}")
            else:
                await domains_col.update_one({"subdomain": sub}, {"$set": {"active": False}})
                logger.warning(f"Domain tunnel restore failed: {full}")
    except Exception as e:
        logger.error(f"_restore_domain_tunnels: {e}")


async def domain_tunnel_monitor():
    """Background task: restart dead domain tunnels.
    FIX: was every 60s — a dropped tunnel left the custom domain dead for up to
    a minute. Checking every 15s recovers much faster."""
    while True:
        await asyncio.sleep(15)
        try:
            for full_domain, proc in list(domain_tunnels.items()):
                if proc.returncode is not None:
                    domain_tunnels.pop(full_domain, None)
                    doc = await domains_col.find_one({"full_domain": full_domain, "active": True})
                    if doc:
                        url, new_proc = await _start_domain_tunnel(doc["subdomain"])
                        if url and new_proc:
                            domain_tunnels[full_domain] = new_proc
                            logger.info(f"Domain tunnel auto-restarted: {full_domain}")
                        else:
                            await domains_col.update_one(
                                {"full_domain": full_domain},
                                {"$set": {"active": False}},
                            )
                            logger.warning(f"Domain tunnel auto-restart failed: {full_domain}")
        except Exception as e:
            logger.warning(f"domain_tunnel_monitor error: {e}")


@owner_only
async def cb_admin_domain_manager(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all domains in the pool."""
    query = update.callback_query
    await query.answer()
    docs = await domains_col.find({}).to_list(length=200)
    base = _get_tunnel_base()

    if not docs:
        text = (
            f"🌐 *Domain Manager*\n\n"
            f"No domains in pool yet\\.\n\n"
            f"Add a `{escape_md(base)}` subdomain and assign it to a user\\."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("➕ Add Domain", callback_data="admin:domain_add")],
            [InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")],
        ])
    else:
        lines = [f"🌐 *Domain Manager* — `{escape_md(base)}`\n"]
        for d in docs:
            assigned  = d.get("assigned_to")
            act_icon  = "🟢" if d.get("active") else "🔴"
            usr_label = f"👤 `{assigned}`" if assigned else "_unassigned_"
            lines.append(f"{act_icon} `{escape_md(d['full_domain'])}` → {usr_label}")
        text = "\n".join(lines)

        kb_rows = []
        for d in docs:
            sub      = d["subdomain"]
            assigned = d.get("assigned_to")
            btn_assign = InlineKeyboardButton(
                f"👤 Assign: {sub}" if not assigned else f"🔄 Reassign: {sub}",
                callback_data=f"admin:domain_assign:{sub}",
            )
            btn_revoke = InlineKeyboardButton(f"🚫 Revoke: {sub}", callback_data=f"admin:domain_revoke:{sub}")
            btn_del    = InlineKeyboardButton(f"🗑 Del: {sub}",    callback_data=f"admin:domain_del_pool:{sub}")
            if assigned:
                kb_rows.append([btn_revoke, btn_del])
            else:
                kb_rows.append([btn_assign, btn_del])
        kb_rows.append([InlineKeyboardButton("➕ Add Domain", callback_data="admin:domain_add")])
        kb_rows.append([InlineKeyboardButton("🔙 Admin Panel", callback_data="admin_panel")])
        kb = InlineKeyboardMarkup(kb_rows)

    await safe_edit(query, text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)


@owner_only
async def cb_admin_domain_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start conversation: add a new domain to the pool."""
    query = update.callback_query
    await query.answer()
    base = _get_tunnel_base()
    await safe_edit(
        query,
        f"🌐 *Add Domain*\n\n"
        f"Send a *subdomain name* \\(letters/numbers/hyphens, min 3 chars\\):\n\n"
        f"Example: `madaraop` → `{escape_md(base)}`\n\n"
        f"Bot will try to claim `madaraop\\.{escape_md(base)}` automatically\\.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data="admin:domain_manager")]
        ]),
        parse_mode=ParseMode.MARKDOWN,
    )
    return ADMIN_DOMAIN_NAME


async def admin_domain_name_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive subdomain name, check availability, start tunnel."""
    subdomain = update.message.text.strip().lower()
    if not _re_domain.match(r"^[a-z0-9][a-z0-9\-]{2,62}$", subdomain):
        await update.message.reply_text(
            "❌ Invalid name. Use letters/numbers/hyphens, at least 3 chars. Try again:",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ADMIN_DOMAIN_NAME

    base        = _get_tunnel_base()
    full_domain = f"{subdomain}.{base}"

    existing = await domains_col.find_one({"subdomain": subdomain})
    if existing:
        await update.message.reply_text(
            f"❌ `{escape_md(full_domain)}` is already in your domain pool.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ConversationHandler.END

    msg = await update.message.reply_text(
        f"🔄 Checking `{escape_md(full_domain)}`…\n_Starting tunnel — this may take up to 25 s…_",
        parse_mode=ParseMode.MARKDOWN,
    )

    actual_url, proc = await _start_domain_tunnel(subdomain)

    if not actual_url or not proc:
        await msg.edit_text(
            f"❌ *Subdomain not available*\n\n"
            f"`{escape_md(full_domain)}` is already taken by another tunnel\\.\n"
            f"Try a different name\\.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return ConversationHandler.END

    now = datetime.now(timezone.utc)
    await domains_col.insert_one({
        "subdomain":   subdomain,
        "full_domain": full_domain,
        "url":         actual_url,
        "assigned_to": None,
        "created_at":  now,
        "active":      True,
    })
    domain_tunnels[full_domain] = proc

    await msg.edit_text(
        f"✅ *Domain Created\\!*\n\n"
        f"🌐 `{escape_md(full_domain)}` is live\\!\n\n"
        f"Now assign it to a user via *Domain Manager → Assign*\\.",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🌐 Domain Manager", callback_data="admin:domain_manager")]
        ]),
    )
    return ConversationHandler.END


@owner_only
async def cb_admin_domain_assign_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start conversation: assign a domain to a user."""
    query = update.callback_query
    await query.answer()
    subdomain = query.data.split(":", 2)[2]
    context.user_data["domain_assign_subdomain"] = subdomain
    base = _get_tunnel_base()
    await safe_edit(
        query,
        f"👤 *Assign Domain*\n\n"
        f"Domain: `{escape_md(subdomain)}.{escape_md(base)}`\n\n"
        f"Send the Telegram *User ID* to assign this domain to\\:",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("❌ Cancel", callback_data="admin:domain_manager")]
        ]),
        parse_mode=ParseMode.MARKDOWN,
    )
    return ADMIN_DOMAIN_ASSIGN_UID


async def admin_domain_assign_uid_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive user ID and assign the domain."""
    try:
        target_uid = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID. Send a numeric ID:")
        return ADMIN_DOMAIN_ASSIGN_UID

    subdomain = context.user_data.get("domain_assign_subdomain", "")
    if not subdomain:
        await update.message.reply_text("❌ Session expired. Start again.")
        return ConversationHandler.END

    doc = await domains_col.find_one({"subdomain": subdomain})
    if not doc:
        await update.message.reply_text("❌ Domain not found in pool.")
        return ConversationHandler.END

    full_domain = doc["full_domain"]

    # Notify old assignee if changing
    old_uid = doc.get("assigned_to")
    if old_uid and old_uid != target_uid:
        try:
            if notification_bot:
                await notification_bot.send_message(
                    old_uid,
                    f"🚫 *Domain Revoked*\n\n`{escape_md(full_domain)}` has been reassigned away from you.",
                    parse_mode=ParseMode.MARKDOWN,
                )
        except Exception:
            pass

    await domains_col.update_one(
        {"subdomain": subdomain},
        {"$set": {"assigned_to": target_uid, "assigned_at": datetime.now(timezone.utc)}},
    )

    # Notify new assignee
    try:
        if notification_bot:
            await notification_bot.send_message(
                target_uid,
                f"🎉 *Custom Domain Assigned\\!*\n\n"
                f"🌐 `{escape_md(full_domain)}`\n\n"
                f"Go to your project → 🌐 Custom Domain to activate it\\!",
                parse_mode=ParseMode.MARKDOWN,
            )
    except Exception:
        pass

    await update.message.reply_text(
        f"✅ `{escape_md(full_domain)}` assigned to user `{target_uid}`\\!",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END


@owner_only
async def cb_admin_domain_revoke(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Revoke a domain from its current user."""
    query = update.callback_query
    await query.answer()
    subdomain = query.data.split(":", 2)[2]
    doc = await domains_col.find_one({"subdomain": subdomain})
    if not doc:
        await query.answer("❌ Domain not found!", show_alert=True)
        return

    full_domain = doc["full_domain"]
    old_uid     = doc.get("assigned_to")

    if old_uid:
        # Unlink from any project using it
        await projects_col.update_many(
            {"user_id": old_uid, "custom_domain": full_domain},
            {"$set": {"custom_domain": None}},
        )
        from file_manager import domain_map
        domain_map.pop(full_domain, None)
        try:
            if notification_bot:
                await notification_bot.send_message(
                    old_uid,
                    f"🚫 *Domain Revoked*\n\n`{escape_md(full_domain)}` has been removed from your account.",
                    parse_mode=ParseMode.MARKDOWN,
                )
        except Exception:
            pass

    await domains_col.update_one(
        {"subdomain": subdomain},
        {"$set": {"assigned_to": None, "assigned_at": None}},
    )
    await safe_edit(
        query,
        f"✅ `{escape_md(full_domain)}` revoked successfully\\.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Domain Manager", callback_data="admin:domain_manager")]
        ]),
        parse_mode=ParseMode.MARKDOWN,
    )


@owner_only
async def cb_admin_domain_delete_pool(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove a domain from the pool and kill its tunnel."""
    query = update.callback_query
    await query.answer()
    subdomain = query.data.split(":", 2)[2]
    doc = await domains_col.find_one({"subdomain": subdomain})
    if not doc:
        await query.answer("❌ Domain not found!", show_alert=True)
        return

    full_domain = doc["full_domain"]

    # Kill tunnel
    proc = domain_tunnels.pop(full_domain, None)
    if proc:
        try: proc.kill()
        except Exception: pass

    # Remove from domain_map and project records
    from file_manager import domain_map
    domain_map.pop(full_domain, None)
    await projects_col.update_many(
        {"custom_domain": full_domain},
        {"$set": {"custom_domain": None}},
    )
    await domains_col.delete_one({"subdomain": subdomain})

    await safe_edit(
        query,
        f"✅ `{escape_md(full_domain)}` deleted and tunnel stopped\\.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Domain Manager", callback_data="admin:domain_manager")]
        ]),
        parse_mode=ParseMode.MARKDOWN,
    )


# ─────────────────────────────────────────────────────────────
# 🌐 Custom Domain  (user-facing — pool-based)
# ─────────────────────────────────────────────────────────────

async def cb_domain(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show user their owner-assigned domains from the pool."""
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    name = query.data.split(":", 1)[1]
    p    = await get_project(uid, name)
    if not p:
        await safe_edit(query, "❌ Project not found.")
        return

    current = p.get("custom_domain")
    if current:
        text = (
            f"🌐 *Custom Domain — {escape_md(name)}*\n\n"
            f"✅ Active: `{escape_md(current)}`\n\n"
            f"Your project is accessible at:\n`https://{escape_md(current)}/`\n\n"
            f"File Manager: `https://{escape_md(current)}/fm/`"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑 Remove Domain", callback_data=f"domain_del:{name}")],
            [InlineKeyboardButton("🔙 Back",          callback_data=f"proj:{name}")],
        ])
        await safe_edit(query, text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)
        return

    # Show domains assigned to this user from the pool
    assigned_domains = await domains_col.find(
        {"assigned_to": uid, "active": True}
    ).to_list(length=50)

    if not assigned_domains:
        _can = await user_can_use_custom_domain(uid)
        heading = "🌐 *Custom Domain — " + escape_md(name) + "*\n\n"
        if _can:
            body = "No domains assigned to you yet\.\n\nYou can:\n• Buy a *loca\.lt* subdomain \(₹49/30 days\)\n• Contact owner for \.com/\.in domain\n\nYour User ID: `" + str(uid) + "`"
        else:
            body = "Custom domains require *Premium* or *Ultimate* plan\.\n\nUpgrade to unlock this feature\.\n\nYour User ID: `" + str(uid) + "`"
        kb2 = []
        if _can:
            kb2.append([InlineKeyboardButton("🌐 Buy loca.lt Subdomain (₹49)", callback_data="domain_buy_localt:" + name)])
        else:
            kb2.append([InlineKeyboardButton("💎 Upgrade Plan", callback_data="plans")])
        kb2.append([InlineKeyboardButton("🔙 Back", callback_data="proj:" + name)])
        await safe_edit(query, heading + body, reply_markup=InlineKeyboardMarkup(kb2), parse_mode=ParseMode.MARKDOWN)
        return

    text = f"🌐 *Custom Domain — {escape_md(name)}*\n\nSelect a domain to activate:"
    kb_rows = []
    for d in assigned_domains:
        # Check if domain is already used by another project of this user
        other = await projects_col.find_one({
            "user_id": uid,
            "custom_domain": d["full_domain"],
            "name": {"$ne": name},
        })
        label = f"{'🔗' if other else '🌐'} {d['full_domain']}"
        if other:
            label += f" (used by {other['name']})"
        kb_rows.append([
            InlineKeyboardButton(label, callback_data=f"domain_pick:{name}:{d['subdomain']}")
        ])
    kb_rows.append([InlineKeyboardButton("🔙 Back", callback_data=f"proj:{name}")])
    await safe_edit(
        query, text,
        reply_markup=InlineKeyboardMarkup(kb_rows),
        parse_mode=ParseMode.MARKDOWN,
    )

async def cb_domain_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    name = query.data.split(":", 1)[1]
    context.user_data["domain_project"] = name
    await safe_edit(
        query,
        f"🌐 *Set Domain — {escape_md(name)}*\n\nSend your domain name:\nExample: `mysite.com` or `bot.mysite.com`",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"domain:{name}")]]),
        parse_mode=ParseMode.MARKDOWN,
    )
    return CUSTOM_DOMAIN

async def domain_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    name = context.user_data.get("domain_project", "")
    raw  = update.message.text.strip().lower()
    raw  = raw.replace("https://", "").replace("http://", "").rstrip("/")
    if not raw or " " in raw or len(raw) > 253:
        await update.message.reply_text("❌ Invalid domain. Send like: `mysite.com`", parse_mode=ParseMode.MARKDOWN)
        return CUSTOM_DOMAIN
    await projects_col.update_one({"user_id": uid, "name": name}, {"$set": {"custom_domain": raw}})
    from file_manager import domain_map
    domain_map[raw] = {"user_id": uid, "project_name": name,
                       "project_dir": project_dir(uid, name)}
    await update.message.reply_text(
        f"✅ Domain saved: `{escape_md(raw)}`\n\nYour site:\n`http://{escape_md(raw)}/`\n\nDNS changes can take up to 48 hours.",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END

async def cb_domain_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    name = query.data.split(":", 1)[1]
    p    = await get_project(uid, name)
    old  = p.get("custom_domain") if p else None
    await projects_col.update_one({"user_id": uid, "name": name}, {"$set": {"custom_domain": None}})
    if old:
        from file_manager import domain_map
        domain_map.pop(old, None)
    await safe_edit(query, f"✅ Domain removed.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"domain:{name}")]]))


async def cb_domain_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User picks one of their assigned pool domains for a project."""
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    # pattern: domain_pick:<project_name>:<subdomain>
    parts = query.data.split(":", 2)
    if len(parts) < 3:
        await query.answer("❌ Invalid data.", show_alert=True)
        return
    name, subdomain = parts[1], parts[2]

    # Verify ownership
    doc = await domains_col.find_one({"subdomain": subdomain, "assigned_to": uid})
    if not doc:
        await query.answer("❌ Domain not assigned to you!", show_alert=True)
        return

    full_domain = doc["full_domain"]

    # Unlink domain from any existing project of this user
    await projects_col.update_many(
        {"user_id": uid, "custom_domain": full_domain},
        {"$set": {"custom_domain": None}},
    )
    from file_manager import domain_map
    domain_map.pop(full_domain, None)

    # Link to selected project
    await projects_col.update_one(
        {"user_id": uid, "name": name},
        {"$set": {"custom_domain": full_domain}},
    )
    domain_map[full_domain] = {
        "user_id":      uid,
        "project_name": name,
        "project_dir":  project_dir(uid, name),
    }

    await safe_edit(
        query,
        f"✅ *Domain Activated\\!*\n\n"
        f"🌐 `{escape_md(full_domain)}` → `{escape_md(name)}`\n\n"
        f"Your project is now accessible at:\n`https://{escape_md(full_domain)}/`",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔙 Back", callback_data=f"proj:{name}")]
        ]),
        parse_mode=ParseMode.MARKDOWN,
    )


# ─────────────────────────────────────────────────────────────
# 🔌 Port Management
# ─────────────────────────────────────────────────────────────

def _find_free_port_sync(start: int = 10000, end: int = 60000) -> int:
    """Blocking helper — always call via run_in_executor to avoid blocking the event loop."""
    for _ in range(200):
        p = random.randint(start, end)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    return random.randint(start, end)

async def _find_free_port(start: int = 10000, end: int = 60000) -> int:
    """Async wrapper — runs blocking socket probe in executor so event loop never blocks."""
    # FIX: use get_running_loop() — get_event_loop() is deprecated inside async functions
    # in Python 3.10+ and emits a DeprecationWarning in Python 3.9.
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _find_free_port_sync, start, end)

async def cb_portmgmt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    name = query.data.split(":", 1)[1]
    p    = await get_project(uid, name)
    if not p:
        await safe_edit(query, "❌ Project not found.")
        return
    cur_port = p.get("port")
    if cur_port:
        text = (
            f"🔌 *Port Management — {escape_md(name)}*\n\n"
            f"Current Port: `{cur_port}`\n"
            f"Access URL: `{BASE_URL}:{cur_port}`\n\n"
            f"Your project must listen on this port."
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🔀 Change Port",  callback_data=f"port_set:{name}")],
            [InlineKeyboardButton("🎲 Auto-Assign",  callback_data=f"port_auto:{name}")],
            [InlineKeyboardButton("🔙 Back",         callback_data=f"proj:{name}")],
        ])
    else:
        text = (
            f"🔌 *Port Management — {escape_md(name)}*\n\n"
            f"No port assigned yet.\n\n"
            f"Assign a port so your project is accessible from outside.\n"
            f"Range: 10000 — 60000"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🎲 Auto-Assign", callback_data=f"port_auto:{name}")],
            [InlineKeyboardButton("✏️ Set Custom",  callback_data=f"port_set:{name}")],
            [InlineKeyboardButton("🔙 Back",        callback_data=f"proj:{name}")],
        ])
    await safe_edit(query, text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def cb_port_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    name = query.data.split(":", 1)[1]
    port = await _find_free_port()
    await projects_col.update_one({"user_id": uid, "name": name}, {"$set": {"port": port}})
    await safe_edit(
        query,
        f"✅ *Port Assigned!*\n\n🔌 Port: `{port}`\nAccess: `{BASE_URL}:{port}`\n\nMake your project listen on port `{port}`.",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🔌 Port Settings", callback_data=f"portmgmt:{name}")],
            [InlineKeyboardButton("🔙 Back",          callback_data=f"proj:{name}")],
        ]),
        parse_mode=ParseMode.MARKDOWN,
    )

async def cb_port_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    name = query.data.split(":", 1)[1]
    context.user_data["port_project"] = name
    await safe_edit(
        query,
        f"🔌 *Set Port — {escape_md(name)}*\n\nSend a port number (10000 — 60000):",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"portmgmt:{name}")]]),
        parse_mode=ParseMode.MARKDOWN,
    )
    return PORT_SET

async def port_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid  = update.effective_user.id
    name = context.user_data.get("port_project", "")
    txt  = update.message.text.strip()
    if not txt.isdigit() or not (10000 <= int(txt) <= 60000):
        await update.message.reply_text("❌ Send a number between 10000 and 60000.")
        return PORT_SET
    port = int(txt)
    await projects_col.update_one({"user_id": uid, "name": name}, {"$set": {"port": port}})
    await update.message.reply_text(
        f"✅ Port set to `{port}`\nAccess: `{BASE_URL}:{port}`",
        parse_mode=ParseMode.MARKDOWN,
    )
    return ConversationHandler.END

# ─────────────────────────────────────────────────────────────
# 🔗 GitHub Webhook Setup
# ─────────────────────────────────────────────────────────────

async def cb_wh_setup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    name = query.data.split(":", 1)[1]
    p    = await get_project(uid, name)
    if not p:
        await safe_edit(query, "❌ Project not found.")
        return
    wh_secret = p.get("webhook_secret")
    if not wh_secret:
        wh_secret = secrets.token_urlsafe(16)
        await projects_col.update_one({"user_id": uid, "name": name}, {"$set": {"webhook_secret": wh_secret}})
        from file_manager import webhook_secrets
        webhook_secrets[wh_secret] = {
            "user_id": uid, "project_name": name,
            "project_dir": project_dir(uid, name),
        }
    wh_url = f"{BASE_URL}/wh/{wh_secret}"
    text = (
        f"🔗 *GitHub Webhook — {escape_md(name)}*\n\n"
        f"Add this webhook to your GitHub repo:\n\n"
        f"📌 *Settings → Webhooks → Add webhook*\n\n"
        f"Payload URL:\n`{escape_md(wh_url)}`\n\n"
        f"Content type: `application/json`\n"
        f"Event: ✅ Just the push event\n\n"
        f"When you push to GitHub, bot will:\n"
        f"1️⃣ Pull latest code\n"
        f"2️⃣ Auto-restart your project"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔄 Regenerate Secret", callback_data=f"wh_regen:{name}")],
        [InlineKeyboardButton("🗑 Remove Webhook",    callback_data=f"wh_del:{name}")],
        [InlineKeyboardButton("🔙 Back",              callback_data=f"proj:{name}")],
    ])
    await safe_edit(query, text, reply_markup=kb, parse_mode=ParseMode.MARKDOWN)

async def cb_wh_regen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    name = query.data.split(":", 1)[1]
    p    = await get_project(uid, name)
    old  = p.get("webhook_secret") if p else None
    new_secret = secrets.token_urlsafe(16)
    await projects_col.update_one({"user_id": uid, "name": name}, {"$set": {"webhook_secret": new_secret}})
    from file_manager import webhook_secrets
    if old:
        webhook_secrets.pop(old, None)
    webhook_secrets[new_secret] = {
        "user_id": uid, "project_name": name,
        "project_dir": project_dir(uid, name),
    }
    query.data = f"wh_setup:{name}"
    await cb_wh_setup(update, context)

async def cb_wh_del(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid  = query.from_user.id
    name = query.data.split(":", 1)[1]
    p    = await get_project(uid, name)
    old  = p.get("webhook_secret") if p else None
    await projects_col.update_one({"user_id": uid, "name": name}, {"$set": {"webhook_secret": None}})
    if old:
        from file_manager import webhook_secrets
        webhook_secrets.pop(old, None)
    await safe_edit(query, "✅ Webhook removed.",
                    reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data=f"proj:{name}")]]))

# ─────────────────────────────────────────────────────────────
# 🗄️ MongoDB DB Viewer (Admin only)
# ─────────────────────────────────────────────────────────────

async def cb_db_viewer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if uid != OWNER_ID and not await is_admin(uid):
        await query.answer("🔒 Admin only.", show_alert=True)
        return
    cols = await db.list_collection_names()
    lines = [f"🗄️ *MongoDB Viewer*\n\nDB: `{escape_md(DATABASE_NAME)}`\n"]
    kb_rows = []
    for col_name in sorted(cols):
        count = await db[col_name].count_documents({})
        lines.append(f"📂 `{escape_md(col_name)}` — {count} docs")
        kb_rows.append([InlineKeyboardButton(f"📂 {col_name} ({count})", callback_data=f"dbcol:{col_name}:0")])
    kb_rows.append([InlineKeyboardButton("🔙 Back", callback_data="admin_panel")])
    await safe_edit(query, "\n".join(lines), reply_markup=InlineKeyboardMarkup(kb_rows), parse_mode=ParseMode.MARKDOWN)

async def cb_db_collection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    uid = query.from_user.id
    if uid != OWNER_ID and not await is_admin(uid):
        await query.answer("🔒 Admin only.", show_alert=True)
        return
    parts = query.data.split(":")
    col_name = parts[1]
    page = int(parts[2]) if len(parts) > 2 else 0
    PAGE_SIZE = 5
    col = db[col_name]
    total = await col.count_documents({})
    docs  = await col.find({}, {"_id": 0}).skip(page * PAGE_SIZE).limit(PAGE_SIZE).to_list(PAGE_SIZE)
    import json as _json
    lines = [f"📂 *{escape_md(col_name)}* — Page {page+1}/{max(1,(total+PAGE_SIZE-1)//PAGE_SIZE)}\n"]
    for i, doc in enumerate(docs, 1):
        try:
            doc_str = _json.dumps(doc, default=str, ensure_ascii=False)[:300]
        except Exception:
            doc_str = str(doc)[:300]
        lines.append(f"*Doc {page*PAGE_SIZE+i}:*\n`{escape_md(doc_str)}`\n")
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("◀️ Prev", callback_data=f"dbcol:{col_name}:{page-1}"))
    if (page + 1) * PAGE_SIZE < total:
        nav.append(InlineKeyboardButton("Next ▶️", callback_data=f"dbcol:{col_name}:{page+1}"))
    kb = []
    if nav: kb.append(nav)
    kb.append([InlineKeyboardButton("🔙 Collections", callback_data="db_viewer")])
    await safe_edit(query, "\n".join(lines), reply_markup=InlineKeyboardMarkup(kb), parse_mode=ParseMode.MARKDOWN)

# ─────────────────────────────────────────────────────────────
# App setup
# ─────────────────────────────────────────────────────────────

def build_application() -> Application:
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .concurrent_updates(False)  # FIX: race condition
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    # New project conversation
    new_proj_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_new_project, pattern="^new_project$")],
        states={
            NEW_PROJECT_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, new_project_name),
                CallbackQueryHandler(new_project_cancel, pattern="^back_start$"),
            ],
            NEW_PROJECT_FILES: [
                MessageHandler(filters.Document.ALL, new_project_file),
                MessageHandler(filters.TEXT & ~filters.COMMAND, new_project_github_url),
                CommandHandler("done", new_project_done_cmd),
                CallbackQueryHandler(new_project_done_cb, pattern="^upload_done$"),
                CallbackQueryHandler(new_project_cancel, pattern="^back_start$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", new_project_cancel),
            CommandHandler("start", new_project_cancel),
        ],
        per_chat=True,
    )

    editcmd_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_editcmd_start, pattern=r"^editcmd:")],
        states={
            EDIT_RUN_CMD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, editcmd_receive),
                CallbackQueryHandler(admin_conv_cancel, pattern=r"^proj:"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", admin_conv_cancel),
            CommandHandler("start", admin_conv_cancel),
        ],
        per_chat=True,
    )

    env_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_env_add_start,  pattern=r"^env_add:"),
            CallbackQueryHandler(cb_env_edit_start, pattern=r"^env_edit:"),
        ],
        states={
            ENV_ADD_KEY: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, env_add_key),
            ],
            ENV_ADD_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, env_add_value),
            ],
            ENV_EDIT_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, env_edit_value),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", admin_conv_cancel),
            CommandHandler("start", admin_conv_cancel),
        ],
        per_chat=True,
    )

    admin_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_admin_give_premium,   pattern="^admin:give_premium$"),
            CallbackQueryHandler(cb_admin_remove_premium, pattern="^admin:remove_premium$"),
            CallbackQueryHandler(cb_admin_temp_premium,   pattern="^admin:temp_premium$"),
            CallbackQueryHandler(cb_admin_ban,            pattern="^admin:ban$"),
            CallbackQueryHandler(cb_admin_unban,          pattern="^admin:unban$"),
            CallbackQueryHandler(cb_admin_broadcast_all,  pattern="^admin:broadcast_all$"),
            CallbackQueryHandler(cb_admin_send_to_user,   pattern="^admin:send_to_user$"),
            CallbackQueryHandler(cb_admin_add_admin,      pattern="^admin:add_admin$"),
            CallbackQueryHandler(cb_admin_remove_admin,   pattern="^admin:remove_admin$"),
        ],
        states={
            ADMIN_GIVE_PREMIUM_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_give_premium_id),
                CallbackQueryHandler(admin_conv_cancel, pattern="^admin_panel$"),
            ],
            ADMIN_REMOVE_PREMIUM_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_remove_premium_id),
                CallbackQueryHandler(admin_conv_cancel, pattern="^admin_panel$"),
            ],
            ADMIN_TEMP_PREMIUM_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_temp_premium_id),
                CallbackQueryHandler(admin_conv_cancel, pattern="^admin_panel$"),
            ],
            ADMIN_TEMP_PREMIUM_DUR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_temp_premium_dur),
                CallbackQueryHandler(admin_conv_cancel, pattern="^admin_panel$"),
            ],
            ADMIN_BAN_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_ban_id),
                CallbackQueryHandler(admin_conv_cancel, pattern="^admin_panel$"),
            ],
            ADMIN_UNBAN_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_unban_id),
                CallbackQueryHandler(admin_conv_cancel, pattern="^admin_panel$"),
            ],
            ADMIN_BROADCAST_MSG: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_broadcast_msg),
                CallbackQueryHandler(admin_conv_cancel, pattern="^admin_panel$"),
            ],
            ADMIN_SEND_USER_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_send_user_id),
                CallbackQueryHandler(admin_conv_cancel, pattern="^admin_panel$"),
            ],
            ADMIN_SEND_USER_MSG: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_send_user_msg),
                CallbackQueryHandler(admin_conv_cancel, pattern="^admin_panel$"),
            ],
            ADMIN_ADD_ADMIN_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_admin_id),
                CallbackQueryHandler(admin_conv_cancel, pattern="^admin_panel$"),
            ],
            ADMIN_REMOVE_ADMIN_ID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_remove_admin_id),
                CallbackQueryHandler(admin_conv_cancel, pattern="^admin_panel$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", admin_conv_cancel),
            CommandHandler("start", admin_conv_cancel),
        ],
        per_chat=True,
    )

    # GitHub deploy conversation
    github_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_github_set, pattern=r"^github_set:")],
        states={
            GITHUB_URL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, github_url_receive),
                CallbackQueryHandler(admin_conv_cancel, pattern=r"^github:"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", admin_conv_cancel),
            CommandHandler("start",  admin_conv_cancel),
        ],
        per_chat=True,
    )

    # Clone conversation
    clone_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_clone_start, pattern=r"^clone:")],
        states={
            CLONE_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, clone_name_receive),
                CallbackQueryHandler(admin_conv_cancel, pattern=r"^proj:"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", admin_conv_cancel),
            CommandHandler("start",  admin_conv_cancel),
        ],
        per_chat=True,
    )

    # Cron job conversation
    cron_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_cron_add_start, pattern=r"^cron_add:")],
        states={
            CRON_EXPR: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cron_expr_receive),
                CallbackQueryHandler(admin_conv_cancel, pattern=r"^cron:"),
            ],
            CRON_CMD: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, cron_cmd_receive),
                CallbackQueryHandler(admin_conv_cancel, pattern=r"^cron:"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", admin_conv_cancel),
            CommandHandler("start",  admin_conv_cancel),
        ],
        per_chat=True,
    )

    # Register conversations first
    app.add_handler(github_conv)
    app.add_handler(clone_conv)
    app.add_handler(cron_conv)
    app.add_handler(new_proj_conv)
    app.add_handler(editcmd_conv)
    app.add_handler(env_conv)
    app.add_handler(admin_conv)

    app.add_handler(CommandHandler("start", start))

    # Callback handlers
    app.add_handler(CallbackQueryHandler(cb_start,             pattern="^back_start$"))
    app.add_handler(CallbackQueryHandler(cb_my_projects,       pattern="^my_projects$"))
    app.add_handler(CallbackQueryHandler(cb_my_status,         pattern="^my_status$"))
    app.add_handler(CallbackQueryHandler(cb_bot_status,        pattern="^bot_status$"))
    app.add_handler(CallbackQueryHandler(cb_premium,           pattern="^premium$"))
    app.add_handler(CallbackQueryHandler(cb_admin_panel,       pattern="^admin_panel$"))
    app.add_handler(CallbackQueryHandler(cb_admin_user_list,   pattern=r"^admin:user_list:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_admin_running,     pattern="^admin:running$"))
    app.add_handler(CallbackQueryHandler(cb_admin_stop_project, pattern=r"^admin_stop:"))
    app.add_handler(CallbackQueryHandler(cb_admin_broadcast_menu, pattern="^admin:broadcast_menu$"))
    app.add_handler(CallbackQueryHandler(cb_admin_backup_now,  pattern="^admin:backup_now$"))
    app.add_handler(CallbackQueryHandler(cb_admin_delete_backups,         pattern="^admin:del_backups$"))
    app.add_handler(CallbackQueryHandler(cb_admin_delete_backups_confirm, pattern="^admin:del_backups_confirm$"))
    app.add_handler(CallbackQueryHandler(cb_admin_all_projects,     pattern=r"^admin:all_projects:\d+$"))
    app.add_handler(CallbackQueryHandler(cb_admin_run_project,      pattern=r"^admin_run:"))
    app.add_handler(CallbackQueryHandler(cb_admin_download_project, pattern=r"^admin_dl:"))

    # New feature handlers
    app.add_handler(CallbackQueryHandler(cb_admin_toggle_lock,        pattern="^admin:toggle_lock$"))
    app.add_handler(CallbackQueryHandler(cb_admin_toggle_maintenance, pattern="^admin:toggle_maintenance$"))
    app.add_handler(CallbackQueryHandler(cb_admin_db_settings,        pattern="^admin:db_settings$"))
    app.add_handler(CallbackQueryHandler(cb_admin_db_switch_to_local, pattern="^admin:db_switch_to_local$"))
    app.add_handler(CallbackQueryHandler(cb_admin_db_confirm_local,   pattern="^admin:db_confirm_local$"))
    app.add_handler(CallbackQueryHandler(cb_admin_db_switch_to_mongo, pattern="^admin:db_switch_to_mongo$"))
    app.add_handler(CallbackQueryHandler(cb_admin_db_confirm_mongo,   pattern="^admin:db_confirm_mongo$"))
    app.add_handler(CallbackQueryHandler(cb_locked_info,              pattern=r"^locked_info:"))

    app.add_handler(CallbackQueryHandler(cb_project_dashboard, pattern=r"^proj:"))
    app.add_handler(CallbackQueryHandler(cb_run,               pattern=r"^run:"))
    app.add_handler(CallbackQueryHandler(cb_stop,              pattern=r"^stop:"))
    app.add_handler(CallbackQueryHandler(cb_restart,           pattern=r"^restart:"))
    app.add_handler(CallbackQueryHandler(cb_logs,              pattern=r"^logs:"))
    app.add_handler(CallbackQueryHandler(cb_filemgr,           pattern=r"^filemgr:"))
    app.add_handler(CallbackQueryHandler(cb_delete_confirm,    pattern=r"^delete:[a-zA-Z0-9_]+$"))
    app.add_handler(CallbackQueryHandler(cb_delete_yes,        pattern=r"^delete_yes:"))
    app.add_handler(CallbackQueryHandler(cb_toggle_auto_restart, pattern=r"^toggle_ar:"))
    app.add_handler(CallbackQueryHandler(cb_envvars,             pattern=r"^envvars:"))
    app.add_handler(CallbackQueryHandler(cb_env_delete,          pattern=r"^env_del:"))
    app.add_handler(CallbackQueryHandler(cb_reinstall_reqs,      pattern=r"^reinstall_reqs:"))

    # ── GitHub Deploy
    app.add_handler(CallbackQueryHandler(cb_github,        pattern=r"^github:[^_]"))
    app.add_handler(CallbackQueryHandler(cb_github_pull,   pattern=r"^github_pull:"))
    app.add_handler(CallbackQueryHandler(cb_github_remove, pattern=r"^github_remove:"))

    # ── Cron Jobs
    app.add_handler(CallbackQueryHandler(cb_cron,          pattern=r"^cron:[^_ad]"))
    app.add_handler(CallbackQueryHandler(cb_cron_toggle,   pattern=r"^cron_toggle:"))
    app.add_handler(CallbackQueryHandler(cb_cron_delete,   pattern=r"^cron_del:"))

    # ── Live Logs
    app.add_handler(CallbackQueryHandler(cb_live_logs,      pattern=r"^live_logs:"))
    app.add_handler(CallbackQueryHandler(cb_live_logs_stop, pattern=r"^live_stop:"))

    # ── Uptime Stats
    app.add_handler(CallbackQueryHandler(cb_uptime,         pattern=r"^uptime:"))

    # ── Custom Notifications
    app.add_handler(CallbackQueryHandler(cb_notif,          pattern=r"^notif:[^_]"))
    app.add_handler(CallbackQueryHandler(cb_notif_toggle,   pattern=r"^notif_toggle:"))

    # ── Premium Lock
    app.add_handler(CallbackQueryHandler(cb_premium_lock,   pattern=r"^premium_lock$"))

    # ── Custom Domain
    domain_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_domain_set, pattern=r"^domain_set:")],
        states={
            CUSTOM_DOMAIN: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, domain_receive),
                CallbackQueryHandler(admin_conv_cancel, pattern=r"^domain:"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", admin_conv_cancel),
            CommandHandler("start",  admin_conv_cancel),
        ],
        per_chat=True,
    )
    app.add_handler(domain_conv)
    app.add_handler(CallbackQueryHandler(cb_domain,      pattern=r"^domain:"))
    app.add_handler(CallbackQueryHandler(cb_domain_del,  pattern=r"^domain_del:"))
    app.add_handler(CallbackQueryHandler(cb_domain_pick, pattern=r"^domain_pick:"))

    # ── Admin Domain Pool Manager
    domain_admin_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_admin_domain_add_start,    pattern="^admin:domain_add$"),
            CallbackQueryHandler(cb_admin_domain_assign_start, pattern=r"^admin:domain_assign:"),
        ],
        states={
            ADMIN_DOMAIN_NAME: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_domain_name_receive),
                CallbackQueryHandler(admin_conv_cancel, pattern="^admin:domain_manager$"),
            ],
            ADMIN_DOMAIN_ASSIGN_UID: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, admin_domain_assign_uid_receive),
                CallbackQueryHandler(admin_conv_cancel, pattern="^admin:domain_manager$"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", admin_conv_cancel),
            CommandHandler("start",  admin_conv_cancel),
        ],
        per_chat=True,
    )
    app.add_handler(domain_admin_conv)
    app.add_handler(CallbackQueryHandler(cb_admin_domain_manager,     pattern="^admin:domain_manager$"))
    app.add_handler(CallbackQueryHandler(cb_admin_domain_revoke,      pattern=r"^admin:domain_revoke:"))
    app.add_handler(CallbackQueryHandler(cb_admin_domain_delete_pool, pattern=r"^admin:domain_del_pool:"))

    # ── Port Management
    port_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_port_set, pattern=r"^port_set:")],
        states={
            PORT_SET: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, port_receive),
                CallbackQueryHandler(admin_conv_cancel, pattern=r"^portmgmt:"),
            ],
        },
        fallbacks=[
            CommandHandler("cancel", admin_conv_cancel),
            CommandHandler("start",  admin_conv_cancel),
        ],
        per_chat=True,
    )
    app.add_handler(port_conv)
    app.add_handler(CallbackQueryHandler(cb_portmgmt,   pattern=r"^portmgmt:"))
    app.add_handler(CallbackQueryHandler(cb_port_auto,  pattern=r"^port_auto:"))

    # ── GitHub Webhook
    app.add_handler(CallbackQueryHandler(cb_wh_setup, pattern=r"^wh_setup:"))
    app.add_handler(CallbackQueryHandler(cb_wh_regen, pattern=r"^wh_regen:"))
    app.add_handler(CallbackQueryHandler(cb_wh_del,   pattern=r"^wh_del:"))

    # ── DB Viewer
    app.add_handler(CallbackQueryHandler(cb_db_viewer,     pattern=r"^db_viewer$"))
    # ── Plans & Payment handlers ──
    app.add_handler(CallbackQueryHandler(cb_plans,             pattern="^plans$"))
    app.add_handler(CallbackQueryHandler(cb_plans_domain_info, pattern="^plans_domain_info$"))
    # NOTE: cb_domain_purchase_start is entry_point in domain_purchase_conv below — NOT standalone.
    # Standalone registration would intercept the callback before the ConversationHandler,
    # discarding the DOM_PURCHASE_NAME return state so user text messages are silently ignored.
    app.add_handler(CallbackQueryHandler(cb_admin_purchase_history, pattern=r"^admin:purchase_history:"))
    app.add_handler(CallbackQueryHandler(cb_buy_plan,    pattern=r"^buy_plan:"))
    # NOTE: cb_pay_upi is entry_point in upi_conv below — NOT standalone
    app.add_handler(CallbackQueryHandler(cb_pay_crypto,  pattern=r"^pay_crypto:"))
    app.add_handler(CallbackQueryHandler(cb_crypto_verify, pattern=r"^crypto_verify:"))
    app.add_handler(CallbackQueryHandler(cb_pay_approve, pattern=r"^pay_approve:"))
    app.add_handler(CallbackQueryHandler(cb_pay_reject,  pattern=r"^pay_reject:"))
    app.add_handler(CallbackQueryHandler(cb_admin_payment_settings, pattern="^admin:payment_settings$"))
    # NOTE: cb_admin_set_bsc_address is entry_point in admin_payment_conv — NOT standalone
    app.add_handler(CallbackQueryHandler(cb_domain_buy_localt, pattern=r"^domain_buy_localt:"))
    # NOTE: cb_domain_buy_pay is entry_point in upi_conv below — NOT standalone

    # ── Self-service domain purchase conversation ──
    domain_purchase_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_domain_purchase_start, pattern="^domain_purchase_start$")],
        states={
            DOM_PURCHASE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, domain_purchase_name_receive)],
        },
        fallbacks=[CommandHandler("start", start), CommandHandler("cancel", start)],
        per_chat=True, per_user=True, per_message=False,
        conversation_timeout=300,
    )
    app.add_handler(domain_purchase_conv)

    # ── UPI payment conversation ──
    # IMPORTANT: entry_points here — NOT registered as standalone handlers above.
    # Standalone registration intercepts before the ConversationHandler, so the
    # return-state is discarded and screenshot/UTR messages are silently ignored.
    upi_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_pay_upi,        pattern=r"^pay_upi:"),
            CallbackQueryHandler(cb_domain_buy_pay, pattern=r"^domain_buy_pay:"),
        ],
        states={
            PAY_UPI_SCREENSHOT: [MessageHandler(filters.PHOTO | filters.Document.ALL, pay_upi_screenshot)],
            PAY_UPI_UTR:        [MessageHandler(filters.TEXT & ~filters.COMMAND,      pay_upi_utr)],
        },
        fallbacks=[CommandHandler("start", start)],
        per_chat=True, per_user=True, per_message=False,
        conversation_timeout=600,
    )
    app.add_handler(upi_conv)

    # ── Admin UPI/payment settings conversation ──
    admin_payment_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(cb_admin_set_upi_start,   pattern="^admin:set_upi_id$"),
            CallbackQueryHandler(cb_admin_set_bsc_address, pattern="^admin:set_bsc_address$"),
            CallbackQueryHandler(cb_admin_set_upi_qr_start, pattern="^admin:set_upi_qr$"),
        ],
        states={
            PLAN_ADMIN_UPI_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_upi_id_receive)],
            PLAN_ADMIN_UPI_QR: [MessageHandler(filters.PHOTO,                   admin_upi_qr_receive)],
        },
        fallbacks=[CommandHandler("start", start)],
        per_chat=True, per_user=True, per_message=False,
        conversation_timeout=300,
    )
    app.add_handler(admin_payment_conv)

    app.add_handler(CallbackQueryHandler(cb_db_collection, pattern=r"^dbcol:"))

    return app


async def _load_webhooks_and_domains():
    """Restore webhook_secrets and domain_map from MongoDB on startup."""
    try:
        from file_manager import webhook_secrets, domain_map
        all_projects = await projects_col.find(
            {"$or": [{"webhook_secret": {"$ne": None}}, {"custom_domain": {"$ne": None}}]},
            {"user_id": 1, "name": 1, "webhook_secret": 1, "custom_domain": 1, "_id": 0},
        ).to_list(length=10000)
        for p in all_projects:
            uid  = p["user_id"]
            name = p["name"]
            pdir = project_dir(uid, name)
            wh   = p.get("webhook_secret")
            dom  = p.get("custom_domain")
            if wh:
                webhook_secrets[wh] = {"user_id": uid, "project_name": name, "project_dir": pdir}
            if dom:
                domain_map[dom] = {"user_id": uid, "project_name": name, "project_dir": pdir}
        logger.info(f"✅ Loaded {len(webhook_secrets)} webhooks, {len(domain_map)} domains from DB")
    except Exception as e:
        logger.error(f"Failed to load webhooks/domains: {e}")


async def post_init(app: Application):
    global notification_bot
    notification_bot = app.bot

    # Initialize local SQLite DB (always, so it's ready when needed)
    init_local_db()

    await app.bot.set_my_commands([
        BotCommand("start",  "Start the bot"),
        BotCommand("done",   "Finish file upload"),
        BotCommand("cancel", "Cancel current action"),
    ])
    await restore_from_backup()
    await _load_webhooks_and_domains()

    # FIX: notify the owner about a crash ONLY after the bot has actually
    # restarted and come back online — never before/at the moment of the
    # crash itself (we can't send anything then anyway — the process is dead).
    # A leftover BOT_RUNNING_MARKER from a previous run means that run never
    # shut down cleanly (i.e. it crashed); a fresh first start has no marker.
    try:
        crashed_last_run = os.path.exists(BOT_RUNNING_MARKER)
        with open(BOT_RUNNING_MARKER, "w") as _mf:
            _mf.write(str(os.getpid()))
        if crashed_last_run and OWNER_ID:
            try:
                await app.bot.send_message(
                    OWNER_ID,
                    "🔄 *Bot restarted*\n\nThe bot process crashed and has now come back online.",
                    parse_mode=ParseMode.MARKDOWN,
                )
            except Exception as e:
                logger.warning(f"Failed to send crash-restart notification: {e}")
    except Exception as e:
        logger.warning(f"Bot-running marker check failed: {e}")

    asyncio.create_task(process_monitor())
    asyncio.create_task(auto_restart_on_startup())  # Always run — not only when backup found
    asyncio.create_task(backup_task())
    asyncio.create_task(keep_alive_task())
    asyncio.create_task(ram_cleanup_task())       # RAM optimization
    asyncio.create_task(cron_task())              # Cron job scheduler
    asyncio.create_task(domain_tunnel_monitor())  # Domain tunnel watchdog
    asyncio.create_task(_restore_domain_tunnels())  # Restart saved domain tunnels


async def post_shutdown(app: Application):
    # Clean shutdown — remove the marker so the NEXT startup knows this run
    # ended intentionally and does not fire a false "restarted after crash"
    # notification.
    try:
        if os.path.exists(BOT_RUNNING_MARKER):
            os.remove(BOT_RUNNING_MARKER)
    except Exception as e:
        logger.warning(f"Failed to remove bot-running marker: {e}")


def main():
    from file_manager import start_flask
    import threading
    t = threading.Thread(target=start_flask, args=(PORT,), daemon=True)
    t.start()
    logger.info(f"Flask file manager started on port {PORT}")

    application = build_application()
    # DATA SAVE: Only subscribe to update types the bot actually uses.
    # ALL_TYPES downloads channel posts, inline queries, polls, etc. — all wasted data.
    application.run_polling(
        allowed_updates=["message", "callback_query"],
        drop_pending_updates=True,   # OPT: skip backlog built up while bot was offline
    )


if __name__ == "__main__":
    main()
