"""
╔══════════════════════════════════════════════════════════════════╗
║       🤖 IPL Challenge Bot - Complete Code (Updated) 🤖         ║
║        Telegram Group Betting/Challenge Management Bot           ║
╚══════════════════════════════════════════════════════════════════╝

INSTALLATION INSTRUCTIONS:
───────────────────────────
1. Python 3.10+ install karo:
   https://www.python.org/downloads/

2. Dependencies install karo:
   pip install "python-telegram-bot[job-queue]==20.7" aiosqlite

3. Bot token lao @BotFather se:
   - Telegram pe @BotFather ko message karo
   - /newbot command se naya bot banao
   - Token copy karo

4. Apna Telegram User ID lao:
   - @userinfobot ko message karo apna ID milega

5. Yahan configure karo (neche dekho):
   BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"
   OWNER_ID  = 123456789  # apna numeric ID

6. Bot run karo:
   python ipl_challenge_bot.py

7. Bot ko group mein add karo aur admin banao.

COMMANDS LIST:
──────────────
/challenge @username amount  - Naya challenge create karo
/team TEAMNAME               - Apni team choose karo (active challenge mein)
/confirm CHID                - Owner: challenge confirm karo
/winner CHID TEAMNAME        - Owner: winner declare karo
/cancel CHID                 - Owner: challenge cancel karo
/active                      - Sabhi active challenges dekho
/history                     - Past challenges ki history
/help                        - Help message

IPL TEAMS (valid names):
CSK, MI, RCB, KKR, SRH, DC, PBKS, RR, LSG, GT
"""

# ─────────────────────────────────────────────
#  IMPORTS — Saari zaruri libraries
# ─────────────────────────────────────────────
import asyncio
import logging
import random
import string
from datetime import datetime, timezone

import aiosqlite
from telegram import (
    Bot,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    JobQueue,
)
from telegram.error import BadRequest

# ─────────────────────────────────────────────
#  CONFIGURATION — Yahan apna token aur ID daalo
# ─────────────────────────────────────────────
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"   # @BotFather se mila token
OWNER_ID  = 123456789               # Apna Telegram numeric user ID

# ─────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────
DB_PATH      = "ipl_challenges.db"
MIN_BET      = 1          # Minimum bet ₹1 (updated)
# MAX_BET removed — koi bhi maximum limit nahi hai!
AUTO_CANCEL_SECONDS = 300  # 5 minutes

VALID_TEAMS = {
    "CSK", "MI", "RCB", "KKR", "SRH",
    "DC", "PBKS", "RR", "LSG", "GT"
}

# ─────────────────────────────────────────────
#  LOGGING SETUP
# ─────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════
#  DATABASE LAYER — SQLite async operations
# ══════════════════════════════════════════════

async def init_db() -> None:
    """Database tables create karo agar exist nahi karte."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS challenges (
                challenge_id    TEXT PRIMARY KEY,
                creator_id      INTEGER NOT NULL,
                creator_username TEXT NOT NULL,
                opponent_id     INTEGER,
                opponent_username TEXT NOT NULL,
                creator_team    TEXT,
                opponent_team   TEXT,
                amount          INTEGER NOT NULL,
                status          TEXT NOT NULL DEFAULT 'pending',
                chat_id         INTEGER NOT NULL,
                message_id      INTEGER,
                created_at      TEXT NOT NULL,
                updated_at      TEXT NOT NULL
            )
        """)
        await db.commit()
    logger.info("Database initialized ✅")


async def db_insert_challenge(ch: dict) -> None:
    """Naya challenge database mein daalo."""
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            INSERT INTO challenges
              (challenge_id, creator_id, creator_username, opponent_id,
               opponent_username, creator_team, opponent_team, amount,
               status, chat_id, message_id, created_at, updated_at)
            VALUES
              (:challenge_id, :creator_id, :creator_username, :opponent_id,
               :opponent_username, :creator_team, :opponent_team, :amount,
               :status, :chat_id, :message_id, :created_at, :updated_at)
        """, ch)
        await db.commit()


async def db_get_challenge(challenge_id: str) -> dict | None:
    """Challenge ID se challenge fetch karo."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM challenges WHERE challenge_id = ?", (challenge_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def db_update_challenge(challenge_id: str, **kwargs) -> None:
    """Challenge ke fields update karo."""
    kwargs["updated_at"] = _now()
    kwargs["challenge_id"] = challenge_id
    set_clause = ", ".join(f"{k} = :{k}" for k in kwargs if k != "challenge_id")
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            f"UPDATE challenges SET {set_clause} WHERE challenge_id = :challenge_id",
            kwargs,
        )
        await db.commit()


async def db_get_active_challenges(chat_id: int | None = None) -> list[dict]:
    """Saare active/confirmed challenges lao."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if chat_id:
            query = "SELECT * FROM challenges WHERE status IN ('pending','accepted','active','confirmed') AND chat_id = ? ORDER BY created_at DESC"
            params = (chat_id,)
        else:
            query = "SELECT * FROM challenges WHERE status IN ('pending','accepted','active','confirmed') ORDER BY created_at DESC"
            params = ()
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def db_get_history(chat_id: int | None = None, limit: int = 10) -> list[dict]:
    """Past completed/cancelled challenges lao."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        if chat_id:
            query = "SELECT * FROM challenges WHERE status IN ('completed','cancelled') AND chat_id = ? ORDER BY updated_at DESC LIMIT ?"
            params = (chat_id, limit)
        else:
            query = "SELECT * FROM challenges WHERE status IN ('completed','cancelled') ORDER BY updated_at DESC LIMIT ?"
            params = (limit,)
        async with db.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def db_get_user_active_challenge(user_id: int) -> dict | None:
    """Kisi user ka active (pending/accepted/active) challenge dhundo."""
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT * FROM challenges
            WHERE (creator_id = ? OR opponent_id = ?)
              AND status IN ('accepted', 'active')
            ORDER BY created_at DESC LIMIT 1
        """, (user_id, user_id)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


# ══════════════════════════════════════════════
#  HELPER UTILITIES
# ══════════════════════════════════════════════

def _now() -> str:
    """Current UTC time ISO string."""
    return datetime.now(timezone.utc).isoformat()


def _generate_challenge_id() -> str:
    """Random CH + 4 digits wala ID banao, e.g. CH9399."""
    digits = "".join(random.choices(string.digits, k=4))
    return f"CH{digits}"


def _challenge_card(ch: dict) -> str:
    """
    Challenge card text banao dictionary se.
    Saare statuses ke liye ek hi function.
    """
    team1 = f"{ch['creator_team']} (Creator)" if ch.get("creator_team") else "TBD"
    team2 = f"{ch['opponent_team']} (Opponent)" if ch.get("opponent_team") else "TBD"

    return (
        f"🏏 *IPL CHALLENGE CARD*\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"🆔 `{ch['challenge_id']}`\n"
        f"👤 Creator: @{ch['creator_username']}\n"
        f"👤 Opponent: @{ch['opponent_username']}\n"
        f"🏏 Team 1: {team1}\n"
        f"🏏 Team 2: {team2}\n"
        f"🎯 Amount: ₹{ch['amount']}\n"
        f"📌 Status: *{ch['status'].upper()}*\n"
        f"━━━━━━━━━━━━━━━━━━━━━"
    )


def _accept_keyboard(ch: dict) -> InlineKeyboardMarkup:
    """Creator aur Opponent ke liye accept buttons banao."""
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"✅ @{ch['creator_username']} Accept",
                callback_data=f"accept_creator:{ch['challenge_id']}",
            ),
            InlineKeyboardButton(
                f"✅ @{ch['opponent_username']} Accept",
                callback_data=f"accept_opponent:{ch['challenge_id']}",
            ),
        ]
    ])


def _accepted_keyboard(ch: dict) -> InlineKeyboardMarkup:
    """Dono accept ke baad buttons update karo (kya kya ho gaya show karo)."""
    creator_label  = "✅ Creator Accepted" if ch.get("creator_accepted") else f"⏳ @{ch['creator_username']}"
    opponent_label = "✅ Opponent Accepted" if ch.get("opponent_accepted") else f"⏳ @{ch['opponent_username']}"
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(creator_label,  callback_data="noop"),
            InlineKeyboardButton(opponent_label, callback_data="noop"),
        ]
    ])


# ══════════════════════════════════════════════
#  COMMAND HANDLERS
# ══════════════════════════════════════════════

# ── /start ──────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Bot ka welcome message."""
    await update.message.reply_text(
        "🏏 *IPL Challenge Bot mein aapka swagat hai!*\n\n"
        "Group mein challenges create karo, teams choose karo, aur winner declare karo!\n\n"
        "👉 `/help` type karo saari commands dekhne ke liye.",
        parse_mode=ParseMode.MARKDOWN,
    )


# ── /help ────────────────────────────────────
async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Saari commands ka help message."""
    is_owner = update.effective_user.id == OWNER_ID
    text = (
        "🏏 *IPL CHALLENGE BOT — HELP*\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "*👥 USER COMMANDS:*\n"
        "`/challenge @username amount`\n"
        "  → Naya challenge create karo\n"
        "  → Minimum bet: ₹1, No maximum limit\n\n"
        "`/team TEAMNAME`\n"
        "  → Apni team choose karo\n"
        "  → Valid teams: CSK, MI, RCB, KKR, SRH, DC, PBKS, RR, LSG, GT\n\n"
        "`/active`\n"
        "  → Is group ke active challenges dekho\n\n"
        "`/history`\n"
        "  → Past 10 challenges ki history\n\n"
    )
    if is_owner:
        text += (
            "*🔑 OWNER COMMANDS:*\n"
            "`/confirm CHID`\n"
            "  → Challenge confirm karo (e.g. /confirm CH1234)\n\n"
            "`/winner CHID TEAMNAME`\n"
            "  → Winner declare karo (e.g. /winner CH1234 RCB)\n\n"
            "`/cancel CHID`\n"
            "  → Challenge cancel karo\n\n"
        )
    text += (
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 *Tip:* Challenge create karne ke baad dono players ko Accept button press karna hoga."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ── /challenge @opponent amount ──────────────
async def cmd_challenge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Naya challenge create karo.
    Usage: /challenge @opponent 500
    """
    msg    = update.message
    user   = update.effective_user
    chat   = update.effective_chat

    # Group mein hi kaam karta hai
    if chat.type not in ("group", "supergroup"):
        await msg.reply_text("❌ Yeh command sirf group mein use karo!")
        return

    # Arguments validate karo
    if len(context.args) < 2:
        await msg.reply_text(
            "❌ *Galat format!*\n\n"
            "✅ Sahi format: `/challenge @username amount`\n"
            "Example: `/challenge @SILENT_HEREE 500`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    raw_opponent = context.args[0].lstrip("@").strip()
    amount_str   = context.args[1].strip()

    # Amount validate karo
    try:
        amount = int(amount_str)
    except ValueError:
        await msg.reply_text("❌ Amount sirf number hona chahiye! Example: `/challenge @user 500`", parse_mode=ParseMode.MARKDOWN)
        return

    # Sirf minimum bet check karo — koi maximum limit nahi!
    if amount < MIN_BET:
        await msg.reply_text(f"❌ Minimum bet ₹{MIN_BET} hai!")
        return

    # Self-challenge rokna
    creator_username = user.username or str(user.id)
    if raw_opponent.lower() == creator_username.lower():
        await msg.reply_text("❌ Apne aap ko challenge nahi kar sakte! 😄")
        return

    # Unique Challenge ID banao
    challenge_id = _generate_challenge_id()
    # Very unlikely collision, but ensure uniqueness
    while await db_get_challenge(challenge_id):
        challenge_id = _generate_challenge_id()

    # Opponent ID resolve karne ki koshish (mention se)
    opponent_id = None
    if msg.entities:
        for ent in msg.entities:
            if ent.type == "mention":
                # Mentioned user ka username extract karo
                mentioned = msg.text[ent.offset + 1: ent.offset + ent.length]
                if mentioned.lower() == raw_opponent.lower():
                    # ID direct nahi milti mention se, None rakhenge
                    break
    # text_mention entities mein user object hota hai
    if msg.entities:
        for ent in msg.entities:
            if ent.type == "text_mention" and ent.user:
                if (ent.user.username or "").lower() == raw_opponent.lower() or str(ent.user.id) == raw_opponent:
                    opponent_id = ent.user.id
                    raw_opponent = ent.user.username or raw_opponent
                    break

    now = _now()
    ch = {
        "challenge_id":       challenge_id,
        "creator_id":         user.id,
        "creator_username":   creator_username,
        "opponent_id":        opponent_id,       # Will be set on accept
        "opponent_username":  raw_opponent,
        "creator_team":       None,
        "opponent_team":      None,
        "amount":             amount,
        "status":             "pending",
        "chat_id":            chat.id,
        "message_id":         None,
        "created_at":         now,
        "updated_at":         now,
    }

    # In-memory accept tracking karo (DB mein column nahi, context mein rakhenge)
    # context.bot_data mein challenge accept state store karenge
    context.bot_data.setdefault("accept_state", {})[challenge_id] = {
        "creator_accepted":  False,
        "opponent_accepted": False,
    }

    # Challenge card bhejo
    keyboard = _accept_keyboard(ch)
    sent = await msg.reply_text(
        _challenge_card(ch),
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=keyboard,
    )

    # Message ID save karo
    ch["message_id"] = sent.message_id
    await db_insert_challenge(ch)

    # Auto-cancel job schedule karo (5 min baad)
    context.job_queue.run_once(
        callback=_auto_cancel_challenge,
        when=AUTO_CANCEL_SECONDS,
        data={"challenge_id": challenge_id, "chat_id": chat.id, "message_id": sent.message_id},
        name=f"auto_cancel_{challenge_id}",
    )

    logger.info(f"Challenge created: {challenge_id} by @{creator_username} vs @{raw_opponent} for ₹{amount}")


# ── Accept Callback Handler ──────────────────
async def handle_accept(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Accept button press hone par handle karo.
    Creator aur Opponent dono accept karein tab aage badhega.
    """
    query = update.callback_query
    user  = query.from_user
    await query.answer()

    data = query.data  # e.g. "accept_creator:CH1234" or "accept_opponent:CH1234"
    if data == "noop":
        return

    parts = data.split(":")
    if len(parts) != 2:
        return

    role, challenge_id = parts[0], parts[1]  # role = accept_creator / accept_opponent

    ch = await db_get_challenge(challenge_id)
    if not ch:
        await query.answer("❌ Challenge nahi mila!", show_alert=True)
        return

    if ch["status"] not in ("pending", "partially_accepted"):
        await query.answer("ℹ️ Challenge already processed ho gaya.", show_alert=True)
        return

    accept_state = context.bot_data.get("accept_state", {}).get(challenge_id, {
        "creator_accepted": False, "opponent_accepted": False
    })

    # Sirf sahi person button press kar sake
    if role == "accept_creator":
        # Creator ki ID check karo
        if user.id != ch["creator_id"]:
            # Username se bhi check karo (agar ID nahi thi)
            user_uname = (user.username or "").lower()
            if user_uname != ch["creator_username"].lower():
                await query.answer("❌ Yeh button sirf creator ke liye hai!", show_alert=True)
                return
        if accept_state["creator_accepted"]:
            await query.answer("✅ Aapne pehle se accept kar liya hai!", show_alert=True)
            return
        accept_state["creator_accepted"] = True
        await query.answer("✅ Aapne accept kar liya!")

    elif role == "accept_opponent":
        # Opponent ki ID check karo
        user_uname = (user.username or "").lower()
        opponent_uname = ch["opponent_username"].lower()
        if ch["opponent_id"]:
            if user.id != ch["opponent_id"]:
                await query.answer("❌ Yeh button sirf opponent ke liye hai!", show_alert=True)
                return
        else:
            # ID nahi hai, username se check karo
            if user_uname != opponent_uname:
                await query.answer("❌ Yeh button sirf opponent ke liye hai!", show_alert=True)
                return
            # Opponent ID save karo
            await db_update_challenge(challenge_id, opponent_id=user.id)
            ch["opponent_id"] = user.id

        if accept_state["opponent_accepted"]:
            await query.answer("✅ Aapne pehle se accept kar liya hai!", show_alert=True)
            return
        accept_state["opponent_accepted"] = True
        await query.answer("✅ Aapne accept kar liya!")

    # State update karo
    context.bot_data.setdefault("accept_state", {})[challenge_id] = accept_state

    both_accepted = accept_state["creator_accepted"] and accept_state["opponent_accepted"]

    if both_accepted:
        # Status update karo
        await db_update_challenge(challenge_id, status="accepted")
        ch["status"] = "accepted"

        # Auto-cancel job cancel karo
        jobs = context.job_queue.get_jobs_by_name(f"auto_cancel_{challenge_id}")
        for job in jobs:
            job.schedule_removal()

        # Card update karo
        try:
            await query.edit_message_text(
                _challenge_card(ch),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("✅ Creator Accepted", callback_data="noop"),
                    InlineKeyboardButton("✅ Opponent Accepted", callback_data="noop"),
                ]]),
            )
        except BadRequest:
            pass

        # Dono ko team choose karne ke liye bol
        await context.bot.send_message(
            chat_id=ch["chat_id"],
            text=(
                f"🎉 Challenge *{challenge_id}* accepted by both players!\n\n"
                f"@{ch['creator_username']} aur @{ch['opponent_username']} —\n"
                f"Apni team choose karo:\n"
                f"`/team TEAMNAME`\n\n"
                f"Valid teams: `CSK, MI, RCB, KKR, SRH, DC, PBKS, RR, LSG, GT`"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    else:
        # Partial accept — card update karo
        ch_updated = await db_get_challenge(challenge_id)
        ch_updated["creator_accepted"]  = accept_state["creator_accepted"]
        ch_updated["opponent_accepted"] = accept_state["opponent_accepted"]

        # Status partially_accepted mein daalo
        await db_update_challenge(challenge_id, status="partially_accepted")

        try:
            await query.edit_message_text(
                _challenge_card(ch_updated) + "\n\n⏳ _Waiting for other player to accept..._",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=_accepted_keyboard(ch_updated),
            )
        except BadRequest:
            pass


# ── /team TEAMNAME ────────────────────────────
async def cmd_team(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Team assign karo active challenge mein.
    Usage: /team RCB
    """
    msg  = update.message
    user = update.effective_user
    chat = update.effective_chat

    if chat.type not in ("group", "supergroup"):
        await msg.reply_text("❌ Yeh command sirf group mein use karo!")
        return

    if not context.args:
        await msg.reply_text(
            "❌ Team naam do!\nExample: `/team RCB`\n\nValid teams: `CSK, MI, RCB, KKR, SRH, DC, PBKS, RR, LSG, GT`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    team_name = context.args[0].upper().strip()
    if team_name not in VALID_TEAMS:
        await msg.reply_text(
            f"❌ Invalid team `{team_name}`!\n\n"
            f"Valid teams: `CSK, MI, RCB, KKR, SRH, DC, PBKS, RR, LSG, GT`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # User ka active challenge dhundo
    ch = await db_get_user_active_challenge(user.id)

    # Username se bhi try karo agar user_id nahi mili
    if not ch:
        # username-based lookup (opponent_id = NULL case)
        async with aiosqlite.connect(DB_PATH) as db:
            db.row_factory = aiosqlite.Row
            uname = (user.username or "").lower()
            async with db.execute("""
                SELECT * FROM challenges
                WHERE (LOWER(creator_username) = ? OR LOWER(opponent_username) = ?)
                  AND status IN ('accepted', 'active', 'partially_accepted')
                ORDER BY created_at DESC LIMIT 1
            """, (uname, uname)) as cursor:
                row = await cursor.fetchone()
                ch = dict(row) if row else None

    if not ch:
        await msg.reply_text("❌ Koi active challenge nahi mila. Pehle challenge accept karo!")
        return

    user_uname = (user.username or "").lower()
    is_creator  = (user.id == ch["creator_id"]) or (user_uname == ch["creator_username"].lower())
    is_opponent = (user.id == ch.get("opponent_id")) or (user_uname == ch["opponent_username"].lower())

    if not is_creator and not is_opponent:
        await msg.reply_text("❌ Tum is challenge ke participant nahi ho!")
        return

    # Check karo team pehle se choose nahi ki
    if is_creator and ch.get("creator_team"):
        await msg.reply_text(f"ℹ️ Aapne pehle hi team *{ch['creator_team']}* choose kar li hai!", parse_mode=ParseMode.MARKDOWN)
        return
    if is_opponent and ch.get("opponent_team"):
        await msg.reply_text(f"ℹ️ Aapne pehle hi team *{ch['opponent_team']}* choose kar li hai!", parse_mode=ParseMode.MARKDOWN)
        return

    # Same team opponent se nahi le sakta
    if is_creator and ch.get("opponent_team") == team_name:
        await msg.reply_text(f"❌ Opponent ne pehle se *{team_name}* choose kar li! Koi aur team lo.", parse_mode=ParseMode.MARKDOWN)
        return
    if is_opponent and ch.get("creator_team") == team_name:
        await msg.reply_text(f"❌ Creator ne pehle se *{team_name}* choose kar li! Koi aur team lo.", parse_mode=ParseMode.MARKDOWN)
        return

    # Team save karo
    if is_creator:
        await db_update_challenge(ch["challenge_id"], creator_team=team_name)
        ch["creator_team"] = team_name
    else:
        await db_update_challenge(ch["challenge_id"], opponent_team=team_name)
        ch["opponent_team"] = team_name

    await msg.reply_text(f"✅ @{user.username or user.id} ki team: *{team_name}* set ho gayi!", parse_mode=ParseMode.MARKDOWN)

    # Dono teams choose ho gayi?
    ch_refreshed = await db_get_challenge(ch["challenge_id"])
    if ch_refreshed["creator_team"] and ch_refreshed["opponent_team"]:
        # Status active karo
        await db_update_challenge(ch["challenge_id"], status="active")
        ch_refreshed["status"] = "active"

        # Card update karo ya naya send karo
        card_text = _challenge_card(ch_refreshed)
        try:
            if ch_refreshed.get("message_id"):
                await context.bot.edit_message_text(
                    chat_id=ch_refreshed["chat_id"],
                    message_id=ch_refreshed["message_id"],
                    text=card_text,
                    parse_mode=ParseMode.MARKDOWN,
                )
        except (BadRequest, Exception):
            await context.bot.send_message(
                chat_id=ch_refreshed["chat_id"],
                text=card_text,
                parse_mode=ParseMode.MARKDOWN,
            )

        # Owner ko notify karo
        await context.bot.send_message(
            chat_id=OWNER_ID,
            text=(
                f"🔔 *NEW ACTIVE CHALLENGE*\n\n"
                f"{card_text}\n\n"
                f"Challenge confirm karne ke liye:\n"
                f"`/confirm {ch_refreshed['challenge_id']}`"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )

        await context.bot.send_message(
            chat_id=ch_refreshed["chat_id"],
            text=(
                f"🏏 Challenge *{ch_refreshed['challenge_id']}* ab ACTIVE hai!\n\n"
                f"@{ch_refreshed['creator_username']} ({ch_refreshed['creator_team']}) "
                f"vs @{ch_refreshed['opponent_username']} ({ch_refreshed['opponent_team']})\n\n"
                f"💰 Amount: ₹{ch_refreshed['amount']}\n\n"
                f"⏳ Owner confirmation ka wait karo..."
            ),
            parse_mode=ParseMode.MARKDOWN,
        )


# ── /confirm CHID ─────────────────────────────
async def cmd_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Owner sirf: challenge confirm karo.
    Usage: /confirm CH1234
    """
    msg  = update.message
    user = update.effective_user

    if user.id != OWNER_ID:
        await msg.reply_text("❌ Yeh command sirf owner ke liye hai!")
        return

    if not context.args:
        await msg.reply_text("❌ Challenge ID do!\nExample: `/confirm CH1234`", parse_mode=ParseMode.MARKDOWN)
        return

    challenge_id = context.args[0].upper().strip()
    ch = await db_get_challenge(challenge_id)

    if not ch:
        await msg.reply_text(f"❌ Challenge `{challenge_id}` nahi mila!", parse_mode=ParseMode.MARKDOWN)
        return

    if ch["status"] != "active":
        await msg.reply_text(f"❌ Challenge `{challenge_id}` active nahi hai. Current status: *{ch['status']}*", parse_mode=ParseMode.MARKDOWN)
        return

    await db_update_challenge(challenge_id, status="confirmed")
    ch["status"] = "confirmed"

    # Group mein update bhejo
    await context.bot.send_message(
        chat_id=ch["chat_id"],
        text=(
            f"✅ *Challenge {challenge_id} CONFIRMED!*\n\n"
            f"@{ch['creator_username']} ({ch['creator_team']}) vs "
            f"@{ch['opponent_username']} ({ch['opponent_team']})\n"
            f"💰 Amount: ₹{ch['amount']}\n\n"
            f"Match ke baad owner winner declare karega! 🏏"
        ),
        parse_mode=ParseMode.MARKDOWN,
    )

    await msg.reply_text(f"✅ Challenge `{challenge_id}` confirmed!", parse_mode=ParseMode.MARKDOWN)
    logger.info(f"Challenge {challenge_id} confirmed by owner.")


# ── /winner CHID TEAMNAME ─────────────────────
async def cmd_winner(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Owner sirf: winner declare karo.
    Usage: /winner CH1234 RCB
    """
    msg  = update.message
    user = update.effective_user

    if user.id != OWNER_ID:
        await msg.reply_text("❌ Yeh command sirf owner ke liye hai!")
        return

    if len(context.args) < 2:
        await msg.reply_text("❌ Format: `/winner CHID TEAMNAME`\nExample: `/winner CH1234 RCB`", parse_mode=ParseMode.MARKDOWN)
        return

    challenge_id = context.args[0].upper().strip()
    winning_team = context.args[1].upper().strip()

    ch = await db_get_challenge(challenge_id)
    if not ch:
        await msg.reply_text(f"❌ Challenge `{challenge_id}` nahi mila!", parse_mode=ParseMode.MARKDOWN)
        return

    if ch["status"] not in ("active", "confirmed"):
        await msg.reply_text(f"❌ Challenge complete/cancel ho chuka hai. Status: *{ch['status']}*", parse_mode=ParseMode.MARKDOWN)
        return

    if winning_team not in VALID_TEAMS:
        await msg.reply_text(f"❌ Invalid team `{winning_team}`!", parse_mode=ParseMode.MARKDOWN)
        return

    # Winner determine karo
    if winning_team == ch.get("creator_team"):
        winner_username = ch["creator_username"]
        loser_username  = ch["opponent_username"]
    elif winning_team == ch.get("opponent_team"):
        winner_username = ch["opponent_username"]
        loser_username  = ch["creator_username"]
    else:
        await msg.reply_text(
            f"❌ Team `{winning_team}` is challenge mein nahi hai!\n"
            f"Creator team: {ch.get('creator_team', 'N/A')}\n"
            f"Opponent team: {ch.get('opponent_team', 'N/A')}",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await db_update_challenge(challenge_id, status="completed", updated_at=_now())

    # Winner announcement group mein
    await context.bot.send_message(
        chat_id=ch["chat_id"],
        text=(
            f"🏆 *CHALLENGE {challenge_id} — RESULT DECLARED!*\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n\n"
            f"🎉 *Congratulations @{winner_username}!*\n"
            f"🏏 Winning Team: *{winning_team}*\n"
            f"💰 Amount Won: ₹{ch['amount']}\n\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"@{ch['creator_username']} & @{ch['opponent_username']}\n"
            f"Challenge Done ✅\n\n"
            f"🙏 Thanks for playing! Agle IPL match ka wait karo!"
        ),
        parse_mode=ParseMode.MARKDOWN,
    )

    # Owner ko bhi notify karo
    await msg.reply_text(
        f"✅ Winner declared!\n"
        f"Challenge: `{challenge_id}`\n"
        f"Winner: @{winner_username} ({winning_team})\n"
        f"Amount: ₹{ch['amount']}",
        parse_mode=ParseMode.MARKDOWN,
    )
    logger.info(f"Challenge {challenge_id} winner: @{winner_username} ({winning_team})")


# ── /cancel CHID ──────────────────────────────
async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Owner sirf: challenge manually cancel karo.
    Usage: /cancel CH1234
    """
    msg  = update.message
    user = update.effective_user

    if user.id != OWNER_ID:
        await msg.reply_text("❌ Yeh command sirf owner ke liye hai!")
        return

    if not context.args:
        await msg.reply_text("❌ Challenge ID do!\nExample: `/cancel CH1234`", parse_mode=ParseMode.MARKDOWN)
        return

    challenge_id = context.args[0].upper().strip()
    ch = await db_get_challenge(challenge_id)

    if not ch:
        await msg.reply_text(f"❌ Challenge `{challenge_id}` nahi mila!", parse_mode=ParseMode.MARKDOWN)
        return

    if ch["status"] in ("completed", "cancelled"):
        await msg.reply_text(f"ℹ️ Challenge pehle se hi `{ch['status']}` hai.", parse_mode=ParseMode.MARKDOWN)
        return

    await db_update_challenge(challenge_id, status="cancelled")

    # Auto-cancel job bhi hata do
    jobs = context.job_queue.get_jobs_by_name(f"auto_cancel_{challenge_id}")
    for job in jobs:
        job.schedule_removal()

    await context.bot.send_message(
        chat_id=ch["chat_id"],
        text=(
            f"❌ *Challenge {challenge_id} CANCELLED*\n\n"
            f"@{ch['creator_username']} vs @{ch['opponent_username']}\n"
            f"💰 Amount: ₹{ch['amount']}\n\n"
            f"_Owner ne challenge cancel kar diya._"
        ),
        parse_mode=ParseMode.MARKDOWN,
    )
    await msg.reply_text(f"✅ Challenge `{challenge_id}` cancel ho gaya.", parse_mode=ParseMode.MARKDOWN)
    logger.info(f"Challenge {challenge_id} manually cancelled by owner.")


# ── /active ───────────────────────────────────
async def cmd_active(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Is group ke saare active challenges dikhao."""
    chat = update.effective_chat

    if chat.type not in ("group", "supergroup"):
        # Private mein sabhi dikhao
        challenges = await db_get_active_challenges()
    else:
        challenges = await db_get_active_challenges(chat_id=chat.id)

    if not challenges:
        await update.message.reply_text("📭 Koi active challenge nahi hai abhi.")
        return

    text = "🏏 *ACTIVE CHALLENGES*\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    for ch in challenges:
        team1 = ch.get("creator_team") or "TBD"
        team2 = ch.get("opponent_team") or "TBD"
        text += (
            f"🆔 `{ch['challenge_id']}` | 📌 {ch['status'].upper()}\n"
            f"👤 @{ch['creator_username']} ({team1}) vs @{ch['opponent_username']} ({team2})\n"
            f"💰 ₹{ch['amount']}\n\n"
        )

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ── /history ──────────────────────────────────
async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Past 10 completed/cancelled challenges ki history dikhao."""
    chat = update.effective_chat

    if chat.type not in ("group", "supergroup"):
        challenges = await db_get_history()
    else:
        challenges = await db_get_history(chat_id=chat.id)

    if not challenges:
        await update.message.reply_text("📭 Koi history nahi mili abhi tak.")
        return

    text = "📜 *CHALLENGE HISTORY (Last 10)*\n━━━━━━━━━━━━━━━━━━━━━\n\n"
    for ch in challenges:
        team1 = ch.get("creator_team") or "N/A"
        team2 = ch.get("opponent_team") or "N/A"
        # Date format karo
        try:
            dt = datetime.fromisoformat(ch["updated_at"]).strftime("%d %b %Y")
        except Exception:
            dt = "N/A"
        emoji = "✅" if ch["status"] == "completed" else "❌"
        text += (
            f"{emoji} `{ch['challenge_id']}` | {ch['status'].upper()} | {dt}\n"
            f"👤 @{ch['creator_username']} ({team1}) vs @{ch['opponent_username']} ({team2})\n"
            f"💰 ₹{ch['amount']}\n\n"
        )

    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ══════════════════════════════════════════════
#  JOB QUEUE CALLBACKS
# ══════════════════════════════════════════════

async def _auto_cancel_challenge(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    5 minute baad pending challenge auto-cancel karo.
    Yeh job queue se automatically call hota hai.
    """
    job_data     = context.job.data
    challenge_id = job_data["challenge_id"]
    chat_id      = job_data["chat_id"]

    ch = await db_get_challenge(challenge_id)
    if not ch:
        return

    # Sirf pending ya partially_accepted challenges cancel karo
    if ch["status"] not in ("pending", "partially_accepted"):
        return

    await db_update_challenge(challenge_id, status="cancelled")
    logger.info(f"Challenge {challenge_id} auto-cancelled after {AUTO_CANCEL_SECONDS}s.")

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                f"⏰ *Challenge {challenge_id} AUTO-CANCELLED*\n\n"
                f"5 minute mein dono ne accept nahi kiya.\n"
                f"@{ch['creator_username']} vs @{ch['opponent_username']}\n"
                f"💰 ₹{ch['amount']}"
            ),
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as e:
        logger.error(f"Auto-cancel message send error: {e}")


# ══════════════════════════════════════════════
#  ERROR HANDLER
# ══════════════════════════════════════════════

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Saari unexpected errors handle karo aur log karo."""
    logger.error("Exception occurred:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Kuch error aaya. Please thodi der baad try karo ya `/help` dekho.",
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass


# ══════════════════════════════════════════════
#  APPLICATION SETUP & MAIN
# ══════════════════════════════════════════════

def build_application() -> Application:
    """
    Telegram Application build karo saare handlers ke saath.
    """
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .build()
    )

    # ── Command Handlers ──────────────────────
    app.add_handler(CommandHandler("start",     cmd_start))
    app.add_handler(CommandHandler("help",      cmd_help))
    app.add_handler(CommandHandler("challenge", cmd_challenge))
    app.add_handler(CommandHandler("team",      cmd_team))
    app.add_handler(CommandHandler("confirm",   cmd_confirm))
    app.add_handler(CommandHandler("winner",    cmd_winner))
    app.add_handler(CommandHandler("cancel",    cmd_cancel))
    app.add_handler(CommandHandler("active",    cmd_active))
    app.add_handler(CommandHandler("history",   cmd_history))

    # ── Callback Query Handler (Accept buttons) ─
    app.add_handler(CallbackQueryHandler(handle_accept, pattern=r"^(accept_creator|accept_opponent|noop):"))
    app.add_handler(CallbackQueryHandler(handle_accept, pattern=r"^noop$"))

    # ── Error Handler ─────────────────────────
    app.add_error_handler(error_handler)

    return app


async def main() -> None:
    """Main entry point — DB init karo aur bot start karo."""
    logger.info("🏏 IPL Challenge Bot starting...")

    # Database initialize karo
    await init_db()

    # Application build karo
    app = build_application()

    logger.info("✅ Bot is running! Press Ctrl+C to stop.")
    # Polling start karo
    await app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    asyncio.run(main())
