import asyncio
import logging
import random
import string
import httpx
import aiosqlite
import nest_asyncio
from datetime import datetime, timezone
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    JobQueue,
)
from telegram.error import TelegramError

# ──────────────────────────────────────────────────────────────
#  ⚙️  CONFIG — Yahan apna data bharo
# ──────────────────────────────────────────────────────────────
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"          # @BotFather se lena
OWNER_ID = 123456789                        # Apna Telegram ID (int)
CRICKET_API_KEY = ""                        # https://cricketdata.org se free key lo (optional)
DB_PATH = "ipl_bot.db"                     # SQLite database file
AUTO_RESULT_INTERVAL = 300                  # Auto result check: 300 seconds = 5 min
CHALLENGE_TIMEOUT = 300                     # Challenge auto-cancel: 5 min
MIN_BET = 1                                 # Minimum bet amount in ₹

# ──────────────────────────────────────────────────────────────
#  📝  LOGGING SETUP
# ──────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────
#  🗄️  DATABASE INIT — Tables banao
# ──────────────────────────────────────────────────────────────
async def init_db():
    """SQLite tables create karo agar exist nahi karte."""
    async with aiosqlite.connect(DB_PATH) as db:
        # Challenges table — ONLY these columns exist, koi extra column mat add karna
        await db.execute("""
            CREATE TABLE IF NOT EXISTS challenges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ch_id TEXT UNIQUE NOT NULL,
                group_id INTEGER,
                creator_id INTEGER NOT NULL,
                creator_username TEXT,
                opponent_id INTEGER NOT NULL,
                opponent_username TEXT,
                amount REAL NOT NULL,
                team_creator TEXT,
                team_opponent TEXT,
                status TEXT DEFAULT 'pending',
                winner_team TEXT,
                winner_username TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                message_id INTEGER,
                auto_result_enabled INTEGER DEFAULT 0,
                match_id TEXT
            )
        """)
        # Users table — stats track karo
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                wins INTEGER DEFAULT 0,
                losses INTEGER DEFAULT 0,
                total_won REAL DEFAULT 0.0,
                total_lost REAL DEFAULT 0.0
            )
        """)
        # Banned users table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS banned_users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                banned_at TEXT DEFAULT CURRENT_TIMESTAMP,
                banned_by INTEGER
            )
        """)
        # Registered groups table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS registered_groups (
                group_id INTEGER PRIMARY KEY,
                group_title TEXT,
                registered_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Settings table — generic config store (acceptance flags yahan store hote hain)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await db.commit()

# ──────────────────────────────────────────────────────────────
#  🔧  DATABASE HELPER FUNCTIONS
# ──────────────────────────────────────────────────────────────
async def get_challenge(ch_id: str) -> dict | None:
    """Challenge ID se challenge fetch karo."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM challenges WHERE ch_id = ?", (ch_id.upper(),)
            ) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None
    except Exception as e:
        logger.error(f"get_challenge error [{ch_id}]: {e}")
        return None


async def update_challenge(ch_id: str, **kwargs):
    """Challenge ke fields update karo.
    IMPORTANT: Sirf valid columns pass karo jo challenges table mein hain.
    Valid columns: group_id, creator_id, creator_username, opponent_id,
    opponent_username, amount, team_creator, team_opponent, status,
    winner_team, winner_username, message_id, auto_result_enabled, match_id
    """
    if not kwargs:
        return
    # Valid columns whitelist — schema ke bahar koi column update nahi hoga
    VALID_COLUMNS = {
        "group_id", "creator_id", "creator_username", "opponent_id",
        "opponent_username", "amount", "team_creator", "team_opponent",
        "status", "winner_team", "winner_username", "message_id",
        "auto_result_enabled", "match_id"
    }
    # Invalid columns filter karo aur warn karo
    invalid = set(kwargs.keys()) - VALID_COLUMNS
    if invalid:
        logger.warning(f"update_challenge called with INVALID columns (skipping): {invalid}")
        kwargs = {k: v for k, v in kwargs.items() if k in VALID_COLUMNS}
    if not kwargs:
        return

    cols = ", ".join(f"{k} = ?" for k in kwargs)
    vals = list(kwargs.values()) + [ch_id.upper()]
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(f"UPDATE challenges SET {cols} WHERE ch_id = ?", vals)
            await db.commit()
    except Exception as e:
        logger.error(f"update_challenge error [{ch_id}]: {e}")
        raise


async def is_banned(user_id: int) -> bool:
    """User banned hai ya nahi check karo."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT 1 FROM banned_users WHERE user_id = ?", (user_id,)
            ) as cursor:
                return await cursor.fetchone() is not None
    except Exception as e:
        logger.error(f"is_banned error [{user_id}]: {e}")
        return False  # DB error pe ban assume mat karo


async def is_registered_group(chat_id: int) -> bool:
    """Group registered hai ya nahi."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT 1 FROM registered_groups WHERE group_id = ?", (chat_id,)
            ) as cursor:
                return await cursor.fetchone() is not None
    except Exception as e:
        logger.error(f"is_registered_group error [{chat_id}]: {e}")
        return False


async def upsert_user(user_id: int, username: str):
    """User record insert karo ya update karo."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO users (user_id, username) VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET username = excluded.username
                """,
                (user_id, username or "unknown"),
            )
            await db.commit()
    except Exception as e:
        logger.error(f"upsert_user error [{user_id}]: {e}")


async def update_user_stats(user_id: int, won: bool, amount: float):
    """Win ya loss ke baad user stats update karo."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            if won:
                await db.execute(
                    "UPDATE users SET wins = wins + 1, total_won = total_won + ? WHERE user_id = ?",
                    (amount, user_id),
                )
            else:
                await db.execute(
                    "UPDATE users SET losses = losses + 1, total_lost = total_lost + ? WHERE user_id = ?",
                    (amount, user_id),
                )
            await db.commit()
    except Exception as e:
        logger.error(f"update_user_stats error [{user_id}]: {e}")


def gen_ch_id() -> str:
    """Random 4-digit challenge ID generate karo: CH + 4 digits."""
    digits = "".join(random.choices(string.digits, k=4))
    return f"CH{digits}"


def challenge_card(c: dict) -> str:
    """Challenge ka formatted card text banao."""
    team_c = c.get("team_creator") or "TBD"
    team_o = c.get("team_opponent") or "TBD"
    status_emoji = {
        "pending": "⏳",
        "accepted": "🤝",
        "active": "🔥",
        "confirmed": "✅",
        "done": "🏆",
        "cancelled": "❌",
    }.get(c["status"], "❓")
    return (
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 {c['ch_id']}\n"
        f"👤 Creator: @{c['creator_username']}\n"
        f"👤 Opponent: @{c['opponent_username']}\n"
        f"🏏 Team 1 ({c['creator_username']}): {team_c}\n"
        f"🏏 Team 2 ({c['opponent_username']}): {team_o}\n"
        f"💰 Amount: ₹{c['amount']}\n"
        f"📌 Status: {status_emoji} {c['status'].upper()}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )


def accept_keyboard(ch_id: str, creator_username: str, opponent_username: str) -> InlineKeyboardMarkup:
    """Accept buttons — sirf respective user click kar sakta hai."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"✅ @{creator_username} Accept",
                callback_data=f"accept_creator:{ch_id}",
            ),
            InlineKeyboardButton(
                f"✅ @{opponent_username} Accept",
                callback_data=f"accept_opponent:{ch_id}",
            ),
        ]
    ])

# ──────────────────────────────────────────────────────────────
#  🎮  CHALLENGE COMMANDS
# ──────────────────────────────────────────────────────────────
async def cmd_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/challenge @opponent amount — Naya challenge create karo."""
    chat = update.effective_chat
    user = update.effective_user
    msg = update.message

    # Sirf registered groups mein kaam karo
    if chat.type not in ("group", "supergroup"):
        await msg.reply_text("❌ /challenge sirf group mein use karo!")
        return

    if not await is_registered_group(chat.id):
        await msg.reply_text(
            "❌ Yeh group registered nahi hai.\nOwner se /setgroup karwao pehle."
        )
        return

    # Ban check
    if await is_banned(user.id):
        await msg.reply_text("❌ You are banned from using this bot.")
        return

    # Args parse karo
    args = context.args
    if len(args) < 2:
        await msg.reply_text(
            "❌ Usage: /challenge @opponent amount\nExample: /challenge @player2 500"
        )
        return

    opponent_mention = args[0].lstrip("@")
    try:
        amount = float(args[1])
    except ValueError:
        await msg.reply_text("❌ Amount valid number hona chahiye. Example: 500")
        return

    if amount < MIN_BET:
        await msg.reply_text(f"❌ Minimum bet ₹{MIN_BET} hai.")
        return

    # Opponent dhundo
    if not msg.entities:
        await msg.reply_text("❌ Opponent ko @mention karo properly.")
        return

    opponent_user = None
    for entity in msg.entities:
        if entity.type == "mention":
            mention_text = msg.text[entity.offset : entity.offset + entity.length].lstrip("@")
            if mention_text.lower() == opponent_mention.lower():
                # Plain @mention — user ID available nahi, sirf username hai
                pass
        elif entity.type == "text_mention":
            opponent_user = entity.user
            break

    # Self-challenge block karo
    if opponent_user and opponent_user.id == user.id:
        await msg.reply_text("❌ Apne aap ko challenge nahi kar sakte! 😂")
        return

    if opponent_user and await is_banned(opponent_user.id):
        await msg.reply_text(f"❌ @{opponent_mention} ko bot se ban kiya gaya hai.")
        return

    # Unique CH ID generate karo
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            for _ in range(10):
                ch_id = gen_ch_id()
                async with db.execute(
                    "SELECT 1 FROM challenges WHERE ch_id = ?", (ch_id,)
                ) as cur:
                    if not await cur.fetchone():
                        break
    except Exception as e:
        logger.error(f"cmd_challenge CH ID generation error: {e}")
        await msg.reply_text("⚠️ Database error. Thodi der baad try karo.")
        return

    # Opponent ID — text mention se milega, warna 0 (manual confirm baad mein)
    opponent_id = opponent_user.id if opponent_user else 0
    opponent_username = opponent_user.username if opponent_user else opponent_mention

    # DB mein save karo
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT INTO challenges
                (ch_id, group_id, creator_id, creator_username, opponent_id, opponent_username, amount, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (
                    ch_id,
                    chat.id,
                    user.id,
                    user.username or user.first_name,
                    opponent_id,
                    opponent_username,
                    amount,
                ),
            )
            await db.commit()
    except Exception as e:
        logger.error(f"cmd_challenge DB insert error: {e}")
        await msg.reply_text("⚠️ Challenge save nahi hua. Thodi der baad try karo.")
        return

    # Users record save karo
    await upsert_user(user.id, user.username or user.first_name)

    # Challenge card bhejo
    c = await get_challenge(ch_id)
    if not c:
        await msg.reply_text("⚠️ Challenge create ho gaya lekin fetch nahi hua. /active check karo.")
        return

    card_text = challenge_card(c)
    keyboard = accept_keyboard(ch_id, c["creator_username"], c["opponent_username"])

    sent = await msg.reply_text(card_text, reply_markup=keyboard)

    # Message ID save karo (card update ke liye)
    await update_challenge(ch_id, message_id=sent.message_id)

    # 5 min baad auto-cancel job queue mein daalo
    context.job_queue.run_once(
        auto_cancel_challenge,
        CHALLENGE_TIMEOUT,
        data={"ch_id": ch_id, "chat_id": chat.id, "message_id": sent.message_id},
        name=f"timeout_{ch_id}",
    )

    # Owner ko DM notify karo
    try:
        await context.bot.send_message(
            OWNER_ID,
            f"🆕 New Challenge Created!\n{card_text}\n📍 Group: {chat.title}",
        )
    except TelegramError:
        pass  # Owner ne bot start nahi kiya DM mein, ignore karo


async def auto_cancel_challenge(context: ContextTypes.DEFAULT_TYPE):
    """5 min baad dono accept na karein toh auto-cancel karo."""
    data = context.job.data
    ch_id = data["ch_id"]
    chat_id = data["chat_id"]
    message_id = data["message_id"]

    c = await get_challenge(ch_id)
    if not c or c["status"] not in ("pending",):
        return  # Already accepted/cancelled, kuch mat karo

    try:
        await update_challenge(ch_id, status="cancelled")
    except Exception as e:
        logger.error(f"auto_cancel_challenge update error [{ch_id}]: {e}")
        return

    try:
        await context.bot.edit_message_text(
            f"❌ Challenge {ch_id} auto-cancelled (timeout 5 min)\n\n"
            + challenge_card(await get_challenge(ch_id)),
            chat_id=chat_id,
            message_id=message_id,
        )
    except TelegramError:
        pass


async def callback_accept(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Accept button callback — creator ya opponent apna button click kare."""
    query = update.callback_query
    user = query.from_user
    await query.answer()

    data = query.data  # "accept_creator:CH1234" ya "accept_opponent:CH1234"
    parts = data.split(":")
    role = parts[0].split("_")[1]  # "creator" ya "opponent"
    ch_id = parts[1]

    if await is_banned(user.id):
        await query.answer("❌ You are banned from using this bot.", show_alert=True)
        return

    c = await get_challenge(ch_id)
    if not c:
        await query.answer("❌ Challenge nahi mila!", show_alert=True)
        return

    if c["status"] not in ("pending",):
        await query.answer(
            f"⚠️ Challenge already {c['status']} hai.", show_alert=True
        )
        return

    # ── [FIX 1 & 2] ─────────────────────────────────────────────────────────
    # Check karo ki sahi user click kar raha hai
    # NOTE: creator_accepted column EXIST NAHI KARTA challenges table mein.
    # Acceptance sirf settings table mein track hoti hai (accept_creator_{ch_id}).
    # ─────────────────────────────────────────────────────────────────────────
    if role == "creator":
        if user.id != c["creator_id"]:
            await query.answer(
                f"❌ Yeh button sirf @{c['creator_username']} ke liye hai!",
                show_alert=True,
            )
            return
        # [FIX 1] `update_challenge(ch_id, creator_accepted=1)` REMOVED
        # — creator_accepted column nahi hai schema mein. Settings table use ho rahi hai.

    else:  # role == "opponent"
        # [FIX 3] opponent_id=0 ke case mein username fallback bhi check karo
        if c["opponent_id"] != 0:
            # Normal case: ID se match karo
            if user.id != c["opponent_id"]:
                await query.answer(
                    f"❌ Yeh button sirf @{c['opponent_username']} ke liye hai!",
                    show_alert=True,
                )
                return
        else:
            # opponent_id = 0 means plain @mention tha, ID available nahi tha
            # Username se match karo (case-insensitive)
            user_uname = (user.username or "").lower()
            expected_uname = (c["opponent_username"] or "").lower()
            if user_uname and expected_uname and user_uname != expected_uname:
                await query.answer(
                    f"❌ Yeh button sirf @{c['opponent_username']} ke liye hai!",
                    show_alert=True,
                )
                return
            # Pehli baar accept kar raha hai — ID aur username save karo
            try:
                await update_challenge(
                    ch_id,
                    opponent_id=user.id,
                    opponent_username=user.username or user.first_name,
                )
                await upsert_user(user.id, user.username or user.first_name)
                # Refreshed challenge lao with updated opponent_id
                c = await get_challenge(ch_id)
            except Exception as e:
                logger.error(f"callback_accept opponent update error [{ch_id}]: {e}")
                await query.answer("⚠️ Database error. Thodi der baad try karo.", show_alert=True)
                return

    # ── Settings table mein acceptance flag save karo ────────────────────────
    key_c = f"accept_creator_{ch_id}"
    key_o = f"accept_opponent_{ch_id}"
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            if role == "creator":
                await db.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, '1')", (key_c,)
                )
            else:
                await db.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, '1')", (key_o,)
                )
            await db.commit()

            async with db.execute(
                "SELECT value FROM settings WHERE key = ?", (key_c,)
            ) as cur:
                creator_ok = (await cur.fetchone()) is not None

            async with db.execute(
                "SELECT value FROM settings WHERE key = ?", (key_o,)
            ) as cur:
                opponent_ok = (await cur.fetchone()) is not None
    except Exception as e:
        logger.error(f"callback_accept settings error [{ch_id}]: {e}")
        await query.answer("⚠️ Database error. Thodi der baad try karo.", show_alert=True)
        return

    c = await get_challenge(ch_id)

    if creator_ok and opponent_ok:
        # Dono ne accept kiya — status: accepted
        try:
            await update_challenge(ch_id, status="accepted")
        except Exception as e:
            logger.error(f"callback_accept status update error [{ch_id}]: {e}")
            await query.answer("⚠️ Status update fail hua. Admin ko batao.", show_alert=True)
            return

        # Timeout job cancel karo
        jobs = context.job_queue.get_jobs_by_name(f"timeout_{ch_id}")
        for job in jobs:
            job.schedule_removal()

        # Card update karo
        c = await get_challenge(ch_id)
        try:
            await query.edit_message_text(
                f"✅ Dono players ne accept kiya!\n\n{challenge_card(c)}\n\n"
                f"📌 Ab dono apni team choose karo:\n"
                f"@{c['creator_username']}: `/team {ch_id} TEAMNAME`\n"
                f"@{c['opponent_username']}: `/team {ch_id} TEAMNAME`\n\n"
                f"IPL Teams: CSK, MI, RCB, KKR, SRH, DC, PBKS, RR, GT, LSG"
            )
        except TelegramError:
            pass

        # Owner notify
        try:
            await context.bot.send_message(
                OWNER_ID,
                f"🤝 Challenge {ch_id} — Dono ne accept kiya!\nNow waiting for team selection.",
            )
        except TelegramError:
            pass
    else:
        # Sirf ek ne accept kiya
        who = f"@{c['creator_username']}" if role == "creator" else f"@{c['opponent_username']}"
        await query.answer(f"✅ {who} ne accept kiya! Dusre ka wait kar rahe hain...", show_alert=True)


async def cmd_team(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/team CHID TEAMNAME — Apni team choose karo."""
    user = update.effective_user
    msg = update.message

    if await is_banned(user.id):
        await msg.reply_text("❌ You are banned from using this bot.")
        return

    if len(context.args) < 2:
        await msg.reply_text(
            "❌ Usage: /team CHID TEAMNAME\nExample: /team CH1234 MI"
        )
        return

    ch_id = context.args[0].upper()
    team = context.args[1].upper()

    # Valid IPL teams
    valid_teams = {
        "CSK", "MI", "RCB", "KKR", "SRH", "DC", "PBKS", "RR", "GT", "LSG"
    }
    if team not in valid_teams:
        await msg.reply_text(
            f"❌ Invalid team: {team}\n"
            f"Valid teams: {', '.join(sorted(valid_teams))}"
        )
        return

    c = await get_challenge(ch_id)
    if not c:
        await msg.reply_text(f"❌ Challenge {ch_id} nahi mila.")
        return

    if c["status"] != "accepted":
        await msg.reply_text(
            f"❌ Team sirf 'accepted' challenges mein choose kar sakte ho. "
            f"Current status: {c['status']}"
        )
        return

    # Determine role
    is_creator = user.id == c["creator_id"]
    is_opponent = user.id == c["opponent_id"]

    if not is_creator and not is_opponent:
        await msg.reply_text("❌ Tum is challenge mein nahi ho.")
        return

    try:
        if is_creator:
            if c["team_creator"]:
                await msg.reply_text(f"❌ Tumne already team choose ki hai: {c['team_creator']}")
                return
            # Check opponent ne same team na li ho
            if c["team_opponent"] and c["team_opponent"] == team:
                await msg.reply_text(
                    f"❌ @{c['opponent_username']} ne already {team} choose kiya hai. Alag team lo!"
                )
                return
            await update_challenge(ch_id, team_creator=team)
        else:
            if c["team_opponent"]:
                await msg.reply_text(f"❌ Tumne already team choose ki hai: {c['team_opponent']}")
                return
            if c["team_creator"] and c["team_creator"] == team:
                await msg.reply_text(
                    f"❌ @{c['creator_username']} ne already {team} choose kiya hai. Alag team lo!"
                )
                return
            await update_challenge(ch_id, team_opponent=team)
    except Exception as e:
        logger.error(f"cmd_team update error [{ch_id}]: {e}")
        await msg.reply_text("⚠️ Team save nahi hui. Thodi der baad try karo.")
        return

    c = await get_challenge(ch_id)

    # Dono ne team choose ki?
    if c["team_creator"] and c["team_opponent"]:
        try:
            await update_challenge(ch_id, status="active")
        except Exception as e:
            logger.error(f"cmd_team status active error [{ch_id}]: {e}")
            await msg.reply_text("⚠️ Status update fail hua. Admin ko batao.")
            return
        c = await get_challenge(ch_id)
        await msg.reply_text(
            f"🔥 Challenge ACTIVE!\n\n{challenge_card(c)}\n\n"
            f"Owner confirmation ka wait karo. /confirm {ch_id}"
        )
        try:
            await context.bot.send_message(
                OWNER_ID,
                f"🔥 Challenge {ch_id} ACTIVE!\n\n{challenge_card(c)}\n\n"
                f"Use /confirm {ch_id} to confirm or /winner {ch_id} TEAMNAME to declare winner.",
            )
        except TelegramError:
            pass
    else:
        await msg.reply_text(
            f"✅ Team {team} choose ki gayi!\n\n"
            f"Dusre player ka wait karo apni team choose karne ka."
        )

# ──────────────────────────────────────────────────────────────
#  👑  OWNER COMMANDS
# ──────────────────────────────────────────────────────────────
def owner_only(func):
    """Owner-only decorator — non-owner ko block karo."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if user.id != OWNER_ID:
            await update.message.reply_text("❌ Yeh command sirf owner ke liye hai.")
            return
        return await func(update, context)
    wrapper.__name__ = func.__name__
    return wrapper


@owner_only
async def cmd_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/confirm CHID — Challenge confirm karo (owner)."""
    msg = update.message
    if not context.args:
        await msg.reply_text("❌ Usage: /confirm CHID")
        return

    ch_id = context.args[0].upper()
    c = await get_challenge(ch_id)

    if not c:
        await msg.reply_text(f"❌ Challenge {ch_id} nahi mila.")
        return

    if c["status"] != "active":
        await msg.reply_text(f"❌ Confirm sirf 'active' challenges ho sakte. Status: {c['status']}")
        return

    try:
        await update_challenge(ch_id, status="confirmed")
    except Exception as e:
        logger.error(f"cmd_confirm error [{ch_id}]: {e}")
        await msg.reply_text("⚠️ Confirm fail hua. Thodi der baad try karo.")
        return

    c = await get_challenge(ch_id)
    await msg.reply_text(f"✅ Challenge {ch_id} CONFIRMED!\n\n{challenge_card(c)}")

    # Group mein bhi notify karo
    if c["group_id"] and c["message_id"]:
        try:
            await context.bot.send_message(
                c["group_id"],
                f"✅ Challenge {ch_id} owner ne confirm kiya!\n\n{challenge_card(c)}",
            )
        except TelegramError:
            pass


@owner_only
async def cmd_winner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/winner CHID TEAMNAME — Winner declare karo (owner)."""
    msg = update.message
    if len(context.args) < 2:
        await msg.reply_text("❌ Usage: /winner CHID TEAMNAME")
        return

    ch_id = context.args[0].upper()
    winner_team = context.args[1].upper()

    c = await get_challenge(ch_id)
    if not c:
        await msg.reply_text(f"❌ Challenge {ch_id} nahi mila.")
        return

    if c["status"] not in ("active", "confirmed"):
        await msg.reply_text(
            f"❌ Winner sirf active/confirmed challenges mein declare hoga. Status: {c['status']}"
        )
        return

    # Winner team validate karo
    if winner_team not in (c["team_creator"], c["team_opponent"]):
        await msg.reply_text(
            f"❌ Winner team is challenge mein nahi hai.\n"
            f"Teams: {c['team_creator']} vs {c['team_opponent']}"
        )
        return

    # Winner user determine karo
    if winner_team == c["team_creator"]:
        winner_id = c["creator_id"]
        winner_username = c["creator_username"]
        loser_id = c["opponent_id"]
    else:
        winner_id = c["opponent_id"]
        winner_username = c["opponent_username"]
        loser_id = c["creator_id"]

    try:
        await update_challenge(
            ch_id,
            status="done",
            winner_team=winner_team,
            winner_username=winner_username,
        )
    except Exception as e:
        logger.error(f"cmd_winner update error [{ch_id}]: {e}")
        await msg.reply_text("⚠️ Winner declare fail hua. Thodi der baad try karo.")
        return

    c = await get_challenge(ch_id)

    # User stats update karo
    await update_user_stats(winner_id, won=True, amount=c["amount"])
    await update_user_stats(loser_id, won=False, amount=c["amount"])

    result_text = (
        f"🏆 CHALLENGE DONE ✅\n\n"
        f"{challenge_card(c)}\n\n"
        f"🎉 Winner: @{winner_username} ({winner_team})\n"
        f"💰 Amount: ₹{c['amount']}\n\n"
        f"@{c['creator_username']} @{c['opponent_username']}\n"
        f"GG! Better luck next time! 🏏"
    )

    await msg.reply_text(result_text)

    if c["group_id"]:
        try:
            await context.bot.send_message(c["group_id"], result_text)
        except TelegramError:
            pass


# ── [FIX 4] @owner_only decorator REMOVED from cmd_cancel ────────────────────
# Ab permission check andar hoti hai:
#   - Owner: koi bhi challenge cancel kar sakta hai
#   - Creator: sirf apna pending challenge cancel kar sakta hai
# ─────────────────────────────────────────────────────────────────────────────
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/cancel CHID — Challenge cancel karo (owner: any | creator: pending only)."""
    msg = update.message
    user = update.effective_user

    if not context.args:
        await msg.reply_text("❌ Usage: /cancel CHID")
        return

    ch_id = context.args[0].upper()
    c = await get_challenge(ch_id)

    if not c:
        await msg.reply_text(f"❌ Challenge {ch_id} nahi mila.")
        return

    if c["status"] in ("done", "cancelled"):
        await msg.reply_text(f"❌ Challenge already {c['status']} hai.")
        return

    # Permission check — owner ya creator?
    is_owner = user.id == OWNER_ID
    is_creator = user.id == c["creator_id"]

    if not is_owner and not is_creator:
        await msg.reply_text("❌ Sirf owner ya creator cancel kar sakte hain.")
        return

    # Creator sirf pending mein cancel kar sakta hai (active/confirmed mein nahi)
    if not is_owner and is_creator and c["status"] != "pending":
        await msg.reply_text(
            f"❌ Tum sirf pending challenge cancel kar sakte ho.\n"
            f"Current status: {c['status']}. Owner se cancel karwao."
        )
        return

    try:
        await update_challenge(ch_id, status="cancelled")
    except Exception as e:
        logger.error(f"cmd_cancel update error [{ch_id}]: {e}")
        await msg.reply_text("⚠️ Cancel fail hua. Thodi der baad try karo.")
        return

    c = await get_challenge(ch_id)
    cancel_text = f"❌ Challenge {ch_id} CANCELLED!\n\n{challenge_card(c)}"
    await msg.reply_text(cancel_text)

    if c["group_id"]:
        try:
            await context.bot.send_message(c["group_id"], cancel_text)
        except TelegramError:
            pass

    # Timeout job hata do
    jobs = context.job_queue.get_jobs_by_name(f"timeout_{ch_id}")
    for job in jobs:
        job.schedule_removal()


@owner_only
async def cmd_setgroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/setgroup — Is group ko register karo (owner, group mein use karo)."""
    chat = update.effective_chat
    msg = update.message

    if chat.type not in ("group", "supergroup"):
        await msg.reply_text("❌ /setgroup sirf group mein use karo.")
        return

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO registered_groups (group_id, group_title)
                VALUES (?, ?)
                """,
                (chat.id, chat.title),
            )
            await db.commit()
    except Exception as e:
        logger.error(f"cmd_setgroup error [{chat.id}]: {e}")
        await msg.reply_text("⚠️ Group register nahi hua. Thodi der baad try karo.")
        return

    await msg.reply_text(
        f"✅ Group registered!\n📌 {chat.title}\n🆔 {chat.id}\n\n"
        f"Ab is group mein challenges shuru ho sakte hain! 🏏"
    )


@owner_only
async def cmd_removegroup(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/removegroup — Is group ko unregister karo."""
    chat = update.effective_chat
    msg = update.message

    if chat.type not in ("group", "supergroup"):
        await msg.reply_text("❌ /removegroup sirf group mein use karo.")
        return

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "DELETE FROM registered_groups WHERE group_id = ?", (chat.id,)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"cmd_removegroup error [{chat.id}]: {e}")
        await msg.reply_text("⚠️ Remove fail hua. Thodi der baad try karo.")
        return

    await msg.reply_text(f"✅ Group '{chat.title}' unregistered. Ab challenges nahi honge.")


@owner_only
async def cmd_groups(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/groups — Registered groups ki list dikhao (DM ya group)."""
    msg = update.message

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT group_id, group_title, registered_at FROM registered_groups"
            ) as cursor:
                rows = await cursor.fetchall()
    except Exception as e:
        logger.error(f"cmd_groups error: {e}")
        await msg.reply_text("⚠️ Groups fetch nahi hue. Thodi der baad try karo.")
        return

    if not rows:
        await msg.reply_text("📭 Koi group registered nahi hai abhi.")
        return

    text = "📋 Registered Groups:\n\n"
    for i, (gid, title, reg_at) in enumerate(rows, 1):
        text += f"{i}. {title}\n   🆔 `{gid}`\n   📅 {reg_at[:10]}\n\n"

    await msg.reply_text(text, parse_mode="Markdown")


@owner_only
async def cmd_active(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/active — Sabhi active challenges dikhao (owner)."""
    msg = update.message

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT * FROM challenges WHERE status IN ('pending','accepted','active','confirmed') ORDER BY created_at DESC"
            ) as cursor:
                rows = await cursor.fetchall()
                cols = [d[0] for d in cursor.description]
    except Exception as e:
        logger.error(f"cmd_active error: {e}")
        await msg.reply_text("⚠️ Active challenges fetch nahi hue. Thodi der baad try karo.")
        return

    if not rows:
        await msg.reply_text("📭 Koi active challenge nahi hai.")
        return

    challenges = [dict(zip(cols, row)) for row in rows]
    text = f"⚡ Active Challenges ({len(challenges)}):\n\n"
    for c in challenges[:10]:  # Max 10 dikhao
        text += f"{challenge_card(c)}\n\n"

    if len(challenges) > 10:
        text += f"... aur {len(challenges) - 10} challenges hain."

    await msg.reply_text(text)


@owner_only
async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/history — Past challenges dikhao (owner)."""
    msg = update.message

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT * FROM challenges WHERE status IN ('done','cancelled') ORDER BY created_at DESC LIMIT 20"
            ) as cursor:
                rows = await cursor.fetchall()
                cols = [d[0] for d in cursor.description]
    except Exception as e:
        logger.error(f"cmd_history error: {e}")
        await msg.reply_text("⚠️ History fetch nahi hua. Thodi der baad try karo.")
        return

    if not rows:
        await msg.reply_text("📭 Koi past challenge nahi hai.")
        return

    challenges = [dict(zip(cols, row)) for row in rows]
    text = f"📜 Challenge History (last {len(challenges)}):\n\n"
    for c in challenges:
        winner_info = f"🏆 Winner: @{c['winner_username']}" if c.get("winner_username") else ""
        text += (
            f"🆔 {c['ch_id']} | {c['status'].upper()} | ₹{c['amount']}\n"
            f"   @{c['creator_username']} vs @{c['opponent_username']}\n"
            f"   {winner_info}\n"
            f"   📅 {c['created_at'][:10]}\n\n"
        )

    await msg.reply_text(text)


@owner_only
async def cmd_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/broadcast MESSAGE — Sabhi registered groups mein message bhejo (owner)."""
    msg = update.message

    if not context.args:
        await msg.reply_text("❌ Usage: /broadcast Aapka message yahan")
        return

    broadcast_text = " ".join(context.args)

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute("SELECT group_id FROM registered_groups") as cursor:
                groups = await cursor.fetchall()
    except Exception as e:
        logger.error(f"cmd_broadcast fetch error: {e}")
        await msg.reply_text("⚠️ Groups fetch nahi hue. Thodi der baad try karo.")
        return

    if not groups:
        await msg.reply_text("❌ Koi registered group nahi hai.")
        return

    sent_count = 0
    failed_count = 0
    for (gid,) in groups:
        try:
            await context.bot.send_message(gid, f"📢 Owner Message:\n\n{broadcast_text}")
            sent_count += 1
        except TelegramError:
            failed_count += 1

    await msg.reply_text(
        f"📢 Broadcast complete!\n✅ Sent: {sent_count}\n❌ Failed: {failed_count}"
    )

# ──────────────────────────────────────────────────────────────
#  🚫  BAN SYSTEM
# ──────────────────────────────────────────────────────────────
@owner_only
async def cmd_ban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/ban @username — User ko ban karo."""
    msg = update.message

    if not context.args:
        await msg.reply_text("❌ Usage: /ban @username")
        return

    target_username = context.args[0].lstrip("@")

    # Check if mentioned with text_mention entity
    target_id = 0
    if msg.entities:
        for entity in msg.entities:
            if entity.type == "text_mention":
                target_id = entity.user.id
                target_username = entity.user.username or entity.user.first_name
                break

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO banned_users (user_id, username, banned_by)
                VALUES (?, ?, ?)
                """,
                (target_id or 0, target_username, OWNER_ID),
            )
            await db.commit()
    except Exception as e:
        logger.error(f"cmd_ban error [{target_username}]: {e}")
        await msg.reply_text("⚠️ Ban fail hua. Thodi der baad try karo.")
        return

    await msg.reply_text(
        f"🚫 @{target_username} ko ban kiya gaya.\nWoh ab bot use nahi kar sakte."
    )


@owner_only
async def cmd_unban(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/unban @username — User ko unban karo."""
    msg = update.message

    if not context.args:
        await msg.reply_text("❌ Usage: /unban @username")
        return

    target_username = context.args[0].lstrip("@")

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            # Username se unban karo
            await db.execute(
                "DELETE FROM banned_users WHERE username = ?", (target_username,)
            )
            await db.commit()
    except Exception as e:
        logger.error(f"cmd_unban error [{target_username}]: {e}")
        await msg.reply_text("⚠️ Unban fail hua. Thodi der baad try karo.")
        return

    await msg.reply_text(f"✅ @{target_username} ko unban kiya gaya. Bot use kar sakte hain.")


@owner_only
async def cmd_banlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/banlist — Sabhi banned users dikhao."""
    msg = update.message

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT username, banned_at FROM banned_users ORDER BY banned_at DESC"
            ) as cursor:
                rows = await cursor.fetchall()
    except Exception as e:
        logger.error(f"cmd_banlist error: {e}")
        await msg.reply_text("⚠️ Banlist fetch nahi hua. Thodi der baad try karo.")
        return

    if not rows:
        await msg.reply_text("✅ Koi user banned nahi hai.")
        return

    text = f"🚫 Banned Users ({len(rows)}):\n\n"
    for i, (username, banned_at) in enumerate(rows, 1):
        text += f"{i}. @{username} — Banned on {banned_at[:10]}\n"

    await msg.reply_text(text)


@owner_only
async def cmd_addbalance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/addbalance @username amount — Future wallet prep (owner only)."""
    msg = update.message
    if len(context.args) < 2:
        await msg.reply_text("❌ Usage: /addbalance @username amount")
        return

    username = context.args[0].lstrip("@")
    try:
        amount = float(context.args[1])
    except ValueError:
        await msg.reply_text("❌ Amount valid number hona chahiye.")
        return

    # Future wallet: total_won update karke balance simulate karo
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            await db.execute(
                "UPDATE users SET total_won = total_won + ? WHERE username = ?",
                (amount, username),
            )
            await db.commit()
    except Exception as e:
        logger.error(f"cmd_addbalance error [{username}]: {e}")
        await msg.reply_text("⚠️ Balance update fail hua. Thodi der baad try karo.")
        return

    await msg.reply_text(f"✅ @{username} ka balance ₹{amount} se increase kiya gaya! (Wallet feature)")

# ──────────────────────────────────────────────────────────────
#  📊  STATS & LEADERBOARD
# ──────────────────────────────────────────────────────────────
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/stats ya /stats @username — User stats dikhao."""
    msg = update.message
    user = update.effective_user

    if await is_banned(user.id):
        await msg.reply_text("❌ You are banned from using this bot.")
        return

    # Target user determine karo
    target_username = None
    target_user_row = None

    if context.args:
        target_username = context.args[0].lstrip("@")
    elif msg.entities:
        for entity in msg.entities:
            if entity.type == "text_mention":
                target_username = entity.user.username or entity.user.first_name
                break

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            if target_username:
                async with db.execute(
                    "SELECT * FROM users WHERE username = ?", (target_username,)
                ) as cursor:
                    target_user_row = await cursor.fetchone()
            else:
                async with db.execute(
                    "SELECT * FROM users WHERE user_id = ?", (user.id,)
                ) as cursor:
                    target_user_row = await cursor.fetchone()

            if not target_user_row:
                name = target_username or user.username or user.first_name
                await msg.reply_text(f"❌ @{name} ke liye koi stats nahi mila. Pehle ek challenge khelo!")
                return

            # Rank calculate karo
            async with db.execute(
                "SELECT COUNT(*) FROM users WHERE wins > ?", (target_user_row[2],)
            ) as cursor:
                rank_row = await cursor.fetchone()
                rank = rank_row[0] + 1
    except Exception as e:
        logger.error(f"cmd_stats error [{user.id}]: {e}")
        await msg.reply_text("⚠️ Stats fetch nahi hui. Thodi der baad try karo.")
        return

    uid, uname, wins, losses, total_won, total_lost = (
        target_user_row[0],
        target_user_row[1],
        target_user_row[2],
        target_user_row[3],
        target_user_row[4],
        target_user_row[5],
    )
    total_matches = wins + losses
    win_rate = round((wins / total_matches * 100) if total_matches > 0 else 0, 1)

    # Pending challenges count karo
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                """
                SELECT COUNT(*) FROM challenges
                WHERE (creator_id = ? OR opponent_id = ?) AND status IN ('pending','accepted','active')
                """,
                (uid, uid),
            ) as cursor:
                pending_row = await cursor.fetchone()
                pending = pending_row[0] if pending_row else 0
    except Exception as e:
        logger.error(f"cmd_stats pending count error [{uid}]: {e}")
        pending = 0

    stats_text = (
        f"📊 Stats for @{uname}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎮 Total Challenges: {total_matches + pending}\n"
        f"✅ Wins: {wins}\n"
        f"❌ Losses: {losses}\n"
        f"⏳ Pending/Active: {pending}\n"
        f"💰 Total Won: ₹{total_won}\n"
        f"💸 Total Lost: ₹{total_lost}\n"
        f"📈 Win Rate: {win_rate}%\n"
        f"🏆 Rank: #{rank}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━"
    )
    await msg.reply_text(stats_text)


async def cmd_leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/top ya /leaderboard — Top 10 users by wins."""
    msg = update.message
    user = update.effective_user

    if await is_banned(user.id):
        await msg.reply_text("❌ You are banned from using this bot.")
        return

    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                "SELECT username, wins, total_won FROM users ORDER BY wins DESC, total_won DESC LIMIT 10"
            ) as cursor:
                rows = await cursor.fetchall()
    except Exception as e:
        logger.error(f"cmd_leaderboard error: {e}")
        await msg.reply_text("⚠️ Leaderboard fetch nahi hua. Thodi der baad try karo.")
        return

    if not rows:
        await msg.reply_text("🏆 Abhi tak koi challenge complete nahi hua. Pehle khelna shuru karo!")
        return

    text = "🏆 IPL Bet Leaderboard\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7

    for i, (uname, wins, total_won) in enumerate(rows):
        medal = medals[i] if i < len(medals) else f"{i+1}."
        text += f"{medal} @{uname} — {wins} Wins | ₹{total_won} Won\n"

    text += "\n━━━━━━━━━━━━━━━━━━━━━━\nBano India ka #1 IPL Bettor! 🏏"
    await msg.reply_text(text)

# ──────────────────────────────────────────────────────────────
#  🔁  REMATCH
# ──────────────────────────────────────────────────────────────
async def cmd_rematch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/rematch CHID — Same opponent aur amount se naya challenge."""
    msg = update.message
    user = update.effective_user
    chat = update.effective_chat

    if await is_banned(user.id):
        await msg.reply_text("❌ You are banned from using this bot.")
        return

    if not context.args:
        await msg.reply_text("❌ Usage: /rematch CHID")
        return

    ch_id = context.args[0].upper()
    orig = await get_challenge(ch_id)

    if not orig:
        await msg.reply_text(f"❌ Challenge {ch_id} nahi mila.")
        return

    if orig["status"] != "done":
        await msg.reply_text("❌ Sirf completed (done) challenges ka rematch ho sakta hai.")
        return

    if user.id not in (orig["creator_id"], orig["opponent_id"]):
        await msg.reply_text("❌ Sirf original creator ya opponent rematch kar sakte hain.")
        return

    # Naya CH ID generate karo
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            for _ in range(10):
                new_ch_id = gen_ch_id()
                async with db.execute(
                    "SELECT 1 FROM challenges WHERE ch_id = ?", (new_ch_id,)
                ) as cur:
                    if not await cur.fetchone():
                        break

            await db.execute(
                """
                INSERT INTO challenges
                (ch_id, group_id, creator_id, creator_username, opponent_id, opponent_username, amount, status)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
                """,
                (
                    new_ch_id,
                    orig["group_id"] or chat.id,
                    user.id,
                    user.username or user.first_name,
                    (orig["opponent_id"] if user.id == orig["creator_id"] else orig["creator_id"]),
                    (orig["opponent_username"] if user.id == orig["creator_id"] else orig["creator_username"]),
                    orig["amount"],
                ),
            )
            await db.commit()
    except Exception as e:
        logger.error(f"cmd_rematch error: {e}")
        await msg.reply_text("⚠️ Rematch create nahi hua. Thodi der baad try karo.")
        return

    new_c = await get_challenge(new_ch_id)
    if not new_c:
        await msg.reply_text("⚠️ Rematch create ho gaya lekin fetch nahi hua.")
        return

    card_text = challenge_card(new_c)
    keyboard = accept_keyboard(new_ch_id, new_c["creator_username"], new_c["opponent_username"])

    sent = await msg.reply_text(
        f"🔁 REMATCH from {ch_id}!\n\n{card_text}", reply_markup=keyboard
    )
    await update_challenge(new_ch_id, message_id=sent.message_id)

    # Timeout job
    context.job_queue.run_once(
        auto_cancel_challenge,
        CHALLENGE_TIMEOUT,
        data={"ch_id": new_ch_id, "chat_id": chat.id, "message_id": sent.message_id},
        name=f"timeout_{new_ch_id}",
    )

    # Owner notify
    try:
        await context.bot.send_message(
            OWNER_ID,
            f"🔁 Rematch created!\n{card_text}",
        )
    except TelegramError:
        pass

# ──────────────────────────────────────────────────────────────
#  🏏  CRICKET API & MATCH SCHEDULE
# ──────────────────────────────────────────────────────────────
async def fetch_ipl_matches() -> list[dict]:
    """CricketData API se aaj ke IPL matches fetch karo."""
    if not CRICKET_API_KEY:
        return []

    url = "https://api.cricapi.com/v1/currentMatches"
    params = {"apikey": CRICKET_API_KEY, "offset": 0}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, params=params)
            data = response.json()

        if data.get("status") != "success":
            return []

        ipl_matches = []
        for match in data.get("data", []):
            # IPL matches filter karo
            name = match.get("name", "")
            if "ipl" in name.lower() or "indian premier" in name.lower():
                ipl_matches.append(match)

        return ipl_matches
    except Exception as e:
        logger.error(f"Cricket API error: {e}")
        return []


async def cmd_matches(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/matches — Aaj ke IPL matches dikhao."""
    msg = update.message
    user = update.effective_user

    if await is_banned(user.id):
        await msg.reply_text("❌ You are banned from using this bot.")
        return

    if not CRICKET_API_KEY:
        await msg.reply_text(
            "🏏 Match Schedule\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "⚠️ Cricket API configured nahi hai.\n\n"
            "📌 Free API key lene ke liye:\n"
            "1. https://cricketdata.org par jaao\n"
            "2. Register karo (free)\n"
            "3. API key copy karo\n"
            "4. Code mein CRICKET_API_KEY = 'your_key' dalo\n\n"
            "Tab tak manually matches dekho: https://www.iplt20.com/matches/schedule"
        )
        return

    await msg.reply_text("🔄 Matches fetch kar raha hoon...")
    matches = await fetch_ipl_matches()

    if not matches:
        await msg.reply_text(
            "🏏 Today's IPL Matches\n━━━━━━━━━━━━━━━━━━━━━━\n\n"
            "📭 Aaj koi IPL match nahi hai ya API se data nahi mila.\n"
            "Check karo: https://www.iplt20.com/matches/schedule"
        )
        return

    text = f"🏏 Today's IPL Matches ({datetime.now().strftime('%d %b %Y')})\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n\n"

    for i, match in enumerate(matches, 1):
        teams = match.get("teams", [])
        team_str = " vs ".join(teams) if teams else match.get("name", "TBD")
        status = match.get("status", "Scheduled")
        date_str = match.get("dateTimeGMT", "")

        # Time convert karo (simple IST = GMT+5:30)
        time_str = "TBD"
        if date_str:
            try:
                from datetime import timedelta
                dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                ist = dt.astimezone(timezone(timedelta(hours=5, minutes=30)))
                time_str = ist.strftime("%I:%M %p IST")
            except Exception:
                time_str = date_str[:16]

        text += f"{i}. {team_str}\n   ⏰ {time_str}\n   📌 {status}\n\n"

    text += "━━━━━━━━━━━━━━━━━━━━━━\n💡 /challenge @opponent amount se bet lagao!"
    await msg.reply_text(text)


@owner_only
async def cmd_autoresult(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/autoresult CHID — Challenge ke liye auto-result enable karo (owner)."""
    msg = update.message

    if not context.args:
        await msg.reply_text("❌ Usage: /autoresult CHID")
        return

    ch_id = context.args[0].upper()
    c = await get_challenge(ch_id)

    if not c:
        await msg.reply_text(f"❌ Challenge {ch_id} nahi mila.")
        return

    if c["status"] not in ("active", "confirmed"):
        await msg.reply_text(
            f"❌ Auto-result sirf active challenges ke liye. Status: {c['status']}"
        )
        return

    if not CRICKET_API_KEY:
        await msg.reply_text(
            "❌ CRICKET_API_KEY set nahi hai. Auto-result kaam nahi karega.\n"
            "Manual /winner use karo."
        )
        return

    try:
        await update_challenge(ch_id, auto_result_enabled=1)
    except Exception as e:
        logger.error(f"cmd_autoresult error [{ch_id}]: {e}")
        await msg.reply_text("⚠️ Auto-result enable nahi hua. Thodi der baad try karo.")
        return

    await msg.reply_text(
        f"✅ Auto-result enabled for {ch_id}!\n"
        f"Bot har 5 min mein match result check karega.\n"
        f"Teams: {c['team_creator']} vs {c['team_opponent']}"
    )


async def check_auto_results(context: ContextTypes.DEFAULT_TYPE):
    """Har 5 min mein auto-result enabled challenges check karo."""
    try:
        async with aiosqlite.connect(DB_PATH) as db:
            async with db.execute(
                """
                SELECT * FROM challenges
                WHERE auto_result_enabled = 1 AND status IN ('active', 'confirmed')
                """
            ) as cursor:
                rows = await cursor.fetchall()
                cols = [d[0] for d in cursor.description]
    except Exception as e:
        logger.error(f"check_auto_results fetch error: {e}")
        return

    if not rows:
        return

    challenges = [dict(zip(cols, row)) for row in rows]
    matches = await fetch_ipl_matches()

    if not matches:
        logger.info("Auto-result: No matches data from API")
        return

    for c in challenges:
        # Match dhundo jismein dono teams hain
        matched_result = None
        for match in matches:
            teams = match.get("teams", [])
            if c["team_creator"] in teams and c["team_opponent"] in teams:
                status = match.get("status", "").lower()
                # Match complete check karo
                if "won" in status or "result" in status.lower():
                    matched_result = match
                    break

        if not matched_result:
            continue

        # Winner extract karo
        status = matched_result.get("status", "")
        winner_team = None

        if c["team_creator"] in status:
            winner_team = c["team_creator"]
            winner_id = c["creator_id"]
            winner_username = c["creator_username"]
            loser_id = c["opponent_id"]
        elif c["team_opponent"] in status:
            winner_team = c["team_opponent"]
            winner_id = c["opponent_id"]
            winner_username = c["opponent_username"]
            loser_id = c["creator_id"]

        if not winner_team:
            continue

        # Challenge update karo
        try:
            await update_challenge(
                c["ch_id"],
                status="done",
                winner_team=winner_team,
                winner_username=winner_username,
                auto_result_enabled=0,
            )
        except Exception as e:
            logger.error(f"check_auto_results update error [{c['ch_id']}]: {e}")
            continue

        # Stats update karo
        await update_user_stats(winner_id, won=True, amount=c["amount"])
        await update_user_stats(loser_id, won=False, amount=c["amount"])

        result_text = (
            f"🤖 AUTO RESULT!\n\n"
            f"🏆 Challenge {c['ch_id']} — DONE!\n"
            f"Winner: @{winner_username} ({winner_team})\n"
            f"💰 Amount: ₹{c['amount']}\n\n"
            f"@{c['creator_username']} @{c['opponent_username']}\n"
            f"GG! 🏏"
        )

        if c["group_id"]:
            try:
                await context.bot.send_message(c["group_id"], result_text)
            except TelegramError:
                pass

        try:
            await context.bot.send_message(OWNER_ID, f"🤖 Auto-result declared!\n{result_text}")
        except TelegramError:
            pass

# ──────────────────────────────────────────────────────────────
#  💬  UTILITY COMMANDS
# ──────────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/start — Welcome message."""
    user = update.effective_user
    await update.message.reply_text(
        f"🏏 Namaste @{user.username or user.first_name}!\n\n"
        f"IPL Challenge Bot mein aapka swagat hai! 🎉\n\n"
        f"Main aapko IPL matches pe challenges lagane mein help karta hoon.\n\n"
        f"📌 Shuru kaise karo:\n"
        f"1️⃣ Group mein add karo\n"
        f"2️⃣ Owner /setgroup kare\n"
        f"3️⃣ /challenge @opponent amount se challenge karo\n\n"
        f"📋 Sabhi commands ke liye: /help\n"
        f"🆔 Apna ID dekhne ke liye: /id\n\n"
        f"Best of luck! 🏆"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/help — Sabhi commands list karo."""
    user = update.effective_user
    is_owner = user.id == OWNER_ID

    help_text = (
        "📋 IPL Challenge Bot — Commands\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🎮 CHALLENGE COMMANDS:\n"
        "/challenge @opponent amount — Naya challenge create karo\n"
        "/team CHID TEAMNAME — Apni team choose karo\n"
        "/rematch CHID — Same opponent se dobara khelo\n"
        "/cancel CHID — Challenge cancel karo (creator: pending only)\n\n"
        "📊 STATS & INFO:\n"
        "/stats — Apni stats dekho\n"
        "/stats @username — Kisi ki bhi stats dekho\n"
        "/top or /leaderboard — Top 10 winners\n"
        "/matches — Aaj ke IPL matches\n"
        "/id — Apna Telegram ID dekho\n"
        "/start — Welcome message\n"
        "/help — Yeh help message\n"
    )

    if is_owner:
        help_text += (
            "\n👑 OWNER COMMANDS:\n"
            "/setgroup — Group register karo (group mein use karo)\n"
            "/removegroup — Group unregister karo\n"
            "/groups — Registered groups list\n"
            "/confirm CHID — Challenge confirm karo\n"
            "/winner CHID TEAMNAME — Winner declare karo\n"
            "/cancel CHID — Koi bhi challenge cancel karo\n"
            "/active — Sabhi active challenges\n"
            "/history — Past challenges\n"
            "/autoresult CHID — Auto-result enable karo\n"
            "/broadcast message — Sabhi groups mein message bhejo\n"
            "/ban @username — User ban karo\n"
            "/unban @username — User unban karo\n"
            "/banlist — Banned users list\n"
            "/addbalance @username amount — Balance add karo\n"
        )

    help_text += (
        "\n━━━━━━━━━━━━━━━━━━━━━━\n"
        "IPL Teams: CSK | MI | RCB | KKR | SRH | DC | PBKS | RR | GT | LSG\n"
        "Min Bet: ₹1 | No max limit 🏏"
    )

    await update.message.reply_text(help_text)


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/id — User ka Telegram ID dikhao."""
    user = update.effective_user
    chat = update.effective_chat
    await update.message.reply_text(
        f"🆔 Your Info:\n"
        f"👤 Name: {user.full_name}\n"
        f"📌 Username: @{user.username or 'N/A'}\n"
        f"🔢 User ID: `{user.id}`\n"
        f"💬 Chat ID: `{chat.id}`\n\n"
        f"_Yeh ID OWNER_ID mein paste karo._",
        parse_mode="Markdown",
    )

# ──────────────────────────────────────────────────────────────
#  ⚠️  ERROR HANDLER
# ──────────────────────────────────────────────────────────────
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """Global error handler — crashes se bachao."""
    logger.error("Exception while handling update:", exc_info=context.error)

    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Kuch galat ho gaya. Thodi der baad try karo.\n"
                f"Error: {type(context.error).__name__}"
            )
        except TelegramError:
            pass

# ──────────────────────────────────────────────────────────────
#  🚀  MAIN FUNCTION (Render Compatible)
# ──────────────────────────────────────────────────────────────
def main():
    """
    Main entry point — Render/Railway compatible.
    nest_asyncio event loop fix apply karo.
    Synchronous main() use karo — NO asyncio.run() with run_polling().
    """
    # Render pe event loop already running hoti hai, nest_asyncio se fix karo
    nest_asyncio.apply()

    # Database initialize karo
    asyncio.get_event_loop().run_until_complete(init_db())
    logger.info("✅ Database initialized successfully!")

    # Application build karo
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # ── Challenge Commands ──
    app.add_handler(CommandHandler("challenge", cmd_challenge))
    app.add_handler(CommandHandler("team", cmd_team))
    app.add_handler(CommandHandler("rematch", cmd_rematch))
    app.add_handler(CallbackQueryHandler(callback_accept, pattern=r"^accept_(creator|opponent):.+"))

    # ── Owner + Mixed Commands ──
    app.add_handler(CommandHandler("confirm", cmd_confirm))
    app.add_handler(CommandHandler("winner", cmd_winner))
    app.add_handler(CommandHandler("cancel", cmd_cancel))    # No @owner_only — handles internally
    app.add_handler(CommandHandler("setgroup", cmd_setgroup))
    app.add_handler(CommandHandler("removegroup", cmd_removegroup))
    app.add_handler(CommandHandler("groups", cmd_groups))
    app.add_handler(CommandHandler("active", cmd_active))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("broadcast", cmd_broadcast))
    app.add_handler(CommandHandler("autoresult", cmd_autoresult))
    app.add_handler(CommandHandler("addbalance", cmd_addbalance))

    # ── Ban Commands ──
    app.add_handler(CommandHandler("ban", cmd_ban))
    app.add_handler(CommandHandler("unban", cmd_unban))
    app.add_handler(CommandHandler("banlist", cmd_banlist))

    # ── Stats & Leaderboard ──
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("top", cmd_leaderboard))
    app.add_handler(CommandHandler("leaderboard", cmd_leaderboard))

    # ── Match Schedule ──
    app.add_handler(CommandHandler("matches", cmd_matches))

    # ── Utility Commands ──
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("id", cmd_id))

    # ── Error Handler ──
    app.add_error_handler(error_handler)

    # ── JobQueue — Auto result check har 5 min ──
    job_queue = app.job_queue
    job_queue.run_repeating(
        check_auto_results,
        interval=AUTO_RESULT_INTERVAL,
        first=60,  # 1 min baad pehli baar run karo
        name="auto_result_checker",
    )

    logger.info("🤖 IPL Challenge Bot V2 (Bug Fixed) starting...")
    logger.info(f"👑 Owner ID: {OWNER_ID}")
    logger.info(f"🏏 Cricket API: {'Configured' if CRICKET_API_KEY else 'Not configured (optional)'}")

    # Render compatible polling — synchronous
    app.run_polling(
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
