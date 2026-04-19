import asyncio
import json
import logging
import os
import re
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import aiosqlite

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
DB_PATH: str = os.getenv("BOT_DB_PATH", "whatsapp_bot.db")

IST = timezone(timedelta(hours=5, minutes=30))

# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------


class Database:
    """
    Async SQLite database wrapper using aiosqlite.

    Usage
    -----
    db = Database()
    await db.init_db()
    await db.add_user(123456, "alice")
    """

    def __init__(self, db_path: str = DB_PATH) -> None:
        self.db_path = db_path
        self._conn: Optional[aiosqlite.Connection] = None

    # ------------------------------------------------------------------
    # Connection helpers
    # ------------------------------------------------------------------

    async def _get_conn(self) -> aiosqlite.Connection:
        """Return (or lazily open) the persistent connection."""
        if self._conn is None:
            self._conn = await aiosqlite.connect(self.db_path)
            self._conn.row_factory = aiosqlite.Row
            await self._conn.execute("PRAGMA journal_mode=WAL;")
            await self._conn.execute("PRAGMA foreign_keys=ON;")
        return self._conn

    async def close(self) -> None:
        """Close the underlying database connection gracefully."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None
            logger.info("Database connection closed.")

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    async def init_db(self) -> None:
        """
        Create all required tables if they do not already exist.

        Tables
        ------
        users               – Telegram users.
        whatsapp_accounts   – Linked WA phone sessions.
        groups              – WA groups discovered per account.
        operation_logs      – Audit trail for every bot operation.
        """
        conn = await self._get_conn()
        async with conn.cursor() as cur:
            # ── users ──────────────────────────────────────────────────
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id    INTEGER PRIMARY KEY,
                    username   TEXT,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                );
                """
            )

            # ── whatsapp_accounts ──────────────────────────────────────
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS whatsapp_accounts (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id       INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    phone_number  TEXT    NOT NULL,
                    session_data  TEXT,                          -- JSON blob (Baileys auth state)
                    status        TEXT    NOT NULL DEFAULT 'disconnected'
                                  CHECK(status IN ('connected', 'disconnected', 'banned', 'pending')),
                    connected_at  TEXT,
                    UNIQUE(user_id, phone_number)
                );
                """
            )

            # ── groups ─────────────────────────────────────────────────
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS groups (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id   INTEGER NOT NULL REFERENCES whatsapp_accounts(id) ON DELETE CASCADE,
                    group_jid    TEXT    NOT NULL,
                    group_name   TEXT,
                    invite_link  TEXT,
                    member_count INTEGER DEFAULT 0,
                    UNIQUE(account_id, group_jid)
                );
                """
            )

            # ── operation_logs ─────────────────────────────────────────
            await cur.execute(
                """
                CREATE TABLE IF NOT EXISTS operation_logs (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id        INTEGER NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
                    operation_type TEXT    NOT NULL,
                    status         TEXT    NOT NULL DEFAULT 'pending'
                                   CHECK(status IN ('pending', 'running', 'success', 'failed', 'cancelled')),
                    details        TEXT,
                    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                );
                """
            )

            # Helpful indices
            await cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_accounts_user ON whatsapp_accounts(user_id);"
            )
            await cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_groups_account ON groups(account_id);"
            )
            await cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_logs_user ON operation_logs(user_id);"
            )

        await conn.commit()
        logger.info("Database initialised at '%s'.", self.db_path)

    # ------------------------------------------------------------------
    # User operations
    # ------------------------------------------------------------------

    async def add_user(self, user_id: int, username: Optional[str]) -> None:
        """
        Insert a Telegram user, or update their username if they already exist.

        Parameters
        ----------
        user_id:  Telegram numeric user ID.
        username: Telegram @username (may be None for private accounts).
        """
        conn = await self._get_conn()
        await conn.execute(
            """
            INSERT INTO users (user_id, username)
            VALUES (?, ?)
            ON CONFLICT(user_id) DO UPDATE SET username = excluded.username;
            """,
            (user_id, username),
        )
        await conn.commit()
        logger.debug("Upserted user %s (%s).", user_id, username)

    async def get_user(self, user_id: int) -> Optional[Dict[str, Any]]:
        """
        Fetch a single user row by Telegram user_id.

        Returns ``None`` if the user is not registered.
        """
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT * FROM users WHERE user_id = ?;", (user_id,)
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # WhatsApp account operations
    # ------------------------------------------------------------------

    async def add_whatsapp_account(
        self,
        user_id: int,
        phone_number: str,
        session_data: Optional[Dict[str, Any]] = None,
    ) -> int:
        """
        Register a new WhatsApp account for a Telegram user.

        Parameters
        ----------
        user_id:      Owning Telegram user ID.
        phone_number: E.164-like phone number string.
        session_data: Optional Baileys auth-state dictionary (stored as JSON).

        Returns
        -------
        The ``id`` (rowid) of the inserted / existing account row.
        """
        session_json: Optional[str] = (
            json.dumps(session_data) if session_data else None
        )
        conn = await self._get_conn()
        async with conn.execute(
            """
            INSERT INTO whatsapp_accounts (user_id, phone_number, session_data, status)
            VALUES (?, ?, ?, 'pending')
            ON CONFLICT(user_id, phone_number)
            DO UPDATE SET
                session_data = COALESCE(excluded.session_data, whatsapp_accounts.session_data),
                status       = 'pending'
            RETURNING id;
            """,
            (user_id, phone_number, session_json),
        ) as cur:
            row = await cur.fetchone()
        await conn.commit()
        account_id: int = row[0]
        logger.debug("Account id=%s registered for user %s.", account_id, user_id)
        return account_id

    async def get_accounts(self, user_id: int) -> List[Dict[str, Any]]:
        """
        Return all WhatsApp accounts linked to *user_id*.

        The ``session_data`` field is automatically deserialised from JSON.
        """
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT * FROM whatsapp_accounts WHERE user_id = ? ORDER BY id;",
            (user_id,),
        ) as cur:
            rows = await cur.fetchall()
        result: List[Dict[str, Any]] = []
        for row in rows:
            rec = dict(row)
            if rec.get("session_data"):
                try:
                    rec["session_data"] = json.loads(rec["session_data"])
                except json.JSONDecodeError:
                    pass  # leave as raw string
            result.append(rec)
        return result

    async def update_account_status(
        self,
        account_id: int,
        status: str,
        session_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Update the connection status (and optionally session data) of an account.

        Parameters
        ----------
        account_id:   PK of the whatsapp_accounts row.
        status:       One of ``connected``, ``disconnected``, ``banned``, ``pending``.
        session_data: If provided, overwrites the stored Baileys auth state.
        """
        conn = await self._get_conn()
        if session_data is not None:
            await conn.execute(
                """
                UPDATE whatsapp_accounts
                SET status       = ?,
                    session_data = ?,
                    connected_at = CASE WHEN ? = 'connected'
                                        THEN strftime('%Y-%m-%dT%H:%M:%fZ','now')
                                        ELSE connected_at END
                WHERE id = ?;
                """,
                (status, json.dumps(session_data), status, account_id),
            )
        else:
            await conn.execute(
                """
                UPDATE whatsapp_accounts
                SET status       = ?,
                    connected_at = CASE WHEN ? = 'connected'
                                        THEN strftime('%Y-%m-%dT%H:%M:%fZ','now')
                                        ELSE connected_at END
                WHERE id = ?;
                """,
                (status, status, account_id),
            )
        await conn.commit()
        logger.debug("Account %s status → %s.", account_id, status)

    async def remove_account(self, account_id: int) -> bool:
        """
        Delete a WhatsApp account and all associated groups (cascade).

        Returns ``True`` if a row was deleted, ``False`` otherwise.
        """
        conn = await self._get_conn()
        async with conn.execute(
            "DELETE FROM whatsapp_accounts WHERE id = ?;", (account_id,)
        ) as cur:
            deleted = cur.rowcount > 0
        await conn.commit()
        if deleted:
            logger.info("Account %s removed.", account_id)
        return deleted

    # ------------------------------------------------------------------
    # Group operations
    # ------------------------------------------------------------------

    async def save_groups(
        self, account_id: int, groups_list: List[Dict[str, Any]]
    ) -> int:
        """
        Upsert a list of WhatsApp groups for a given account.

        Each item in *groups_list* should be a dict with keys:
        ``group_jid``, ``group_name``, ``invite_link``, ``member_count``.

        Returns the number of rows inserted/updated.
        """
        if not groups_list:
            return 0
        conn = await self._get_conn()
        params: List[Tuple] = [
            (
                account_id,
                g.get("group_jid", ""),
                g.get("group_name"),
                g.get("invite_link"),
                g.get("member_count", 0),
            )
            for g in groups_list
            if g.get("group_jid")
        ]
        await conn.executemany(
            """
            INSERT INTO groups (account_id, group_jid, group_name, invite_link, member_count)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(account_id, group_jid) DO UPDATE SET
                group_name   = COALESCE(excluded.group_name,   groups.group_name),
                invite_link  = COALESCE(excluded.invite_link,  groups.invite_link),
                member_count = excluded.member_count;
            """,
            params,
        )
        await conn.commit()
        logger.debug("Saved %d groups for account %s.", len(params), account_id)
        return len(params)

    async def get_groups(self, account_id: int) -> List[Dict[str, Any]]:
        """Return all groups stored for *account_id*."""
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT * FROM groups WHERE account_id = ? ORDER BY group_name;",
            (account_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def delete_group(self, group_id: int) -> bool:
        """Remove a single group by its PK. Returns ``True`` on success."""
        conn = await self._get_conn()
        async with conn.execute(
            "DELETE FROM groups WHERE id = ?;", (group_id,)
        ) as cur:
            deleted = cur.rowcount > 0
        await conn.commit()
        return deleted

    # ------------------------------------------------------------------
    # Operation logging
    # ------------------------------------------------------------------

    async def log_operation(
        self,
        user_id: int,
        op_type: str,
        status: str,
        details: Optional[str] = None,
    ) -> int:
        """
        Insert a new entry into operation_logs.

        Parameters
        ----------
        user_id:  Telegram user ID of the actor.
        op_type:  Short label, e.g. ``"add_members"``, ``"scrape_groups"``.
        status:   ``pending`` | ``running`` | ``success`` | ``failed`` | ``cancelled``.
        details:  Optional human-readable description or error message.

        Returns
        -------
        The ``id`` of the inserted log row.
        """
        conn = await self._get_conn()
        async with conn.execute(
            """
            INSERT INTO operation_logs (user_id, operation_type, status, details)
            VALUES (?, ?, ?, ?)
            RETURNING id;
            """,
            (user_id, op_type, status, details),
        ) as cur:
            row = await cur.fetchone()
        await conn.commit()
        log_id: int = row[0]
        logger.debug(
            "Logged op #%s: user=%s type=%s status=%s.", log_id, user_id, op_type, status
        )
        return log_id

    async def update_log_status(
        self, log_id: int, status: str, details: Optional[str] = None
    ) -> None:
        """Update the status (and optionally details) of an existing log entry."""
        conn = await self._get_conn()
        if details is not None:
            await conn.execute(
                "UPDATE operation_logs SET status = ?, details = ? WHERE id = ?;",
                (status, details, log_id),
            )
        else:
            await conn.execute(
                "UPDATE operation_logs SET status = ? WHERE id = ?;",
                (status, log_id),
            )
        await conn.commit()

    async def get_logs(
        self, user_id: int, limit: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Fetch the most recent *limit* log entries for a user.

        Results are ordered newest-first.
        """
        conn = await self._get_conn()
        async with conn.execute(
            """
            SELECT * FROM operation_logs
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?;
            """,
            (user_id, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Singleton convenience accessor
# ---------------------------------------------------------------------------

_db_instance: Optional[Database] = None


def get_db(db_path: str = DB_PATH) -> Database:
    """Return the process-wide :class:`Database` singleton."""
    global _db_instance
    if _db_instance is None:
        _db_instance = Database(db_path)
    return _db_instance


# ===========================================================================
# Utilities
# ===========================================================================


# ---------------------------------------------------------------------------
# Phone-number helpers
# ---------------------------------------------------------------------------

# Regex used when no phonenumbers library is available
_DIGIT_STRIP_RE = re.compile(r"[\s\-().+]")
_PHONE_BASIC_RE = re.compile(r"^\+?[1-9]\d{6,14}$")

# VCF TEL line pattern  (TEL;TYPE=...:+1234567890)
_VCF_TEL_RE = re.compile(r"^TEL[^:]*:\s*(.+)$", re.IGNORECASE)


def validate_phone_number(number: str) -> Optional[str]:
    """
    Validate and normalise a phone-number string.

    Strips common formatting characters and returns the number in E.164-like
    format (digits only, with a leading ``+``).  Returns ``None`` when the
    input cannot be resolved to a plausible international number.

    The function attempts to use the ``phonenumbers`` library for strict
    validation; if it is not installed it falls back to a simple regex check.

    Parameters
    ----------
    number: Raw phone number string (any format).

    Returns
    -------
    Cleaned number string (e.g. ``"+919876543210"``) or ``None``.

    Examples
    --------
    >>> validate_phone_number("+91 98765-43210")
    '+919876543210'
    >>> validate_phone_number("invalid") is None
    True
    """
    if not number or not isinstance(number, str):
        return None

    cleaned = _DIGIT_STRIP_RE.sub("", number.strip())

    # Ensure leading +
    if not cleaned.startswith("+"):
        cleaned = "+" + cleaned

    # Remove any residual non-digit chars (except the leading +)
    cleaned = "+" + re.sub(r"\D", "", cleaned[1:])

    try:
        import phonenumbers  # type: ignore

        parsed = phonenumbers.parse(cleaned, None)
        if phonenumbers.is_valid_number(parsed):
            return phonenumbers.format_number(
                parsed, phonenumbers.PhoneNumberFormat.E164
            )
        return None
    except ImportError:
        # Fallback: basic length/format check
        return cleaned if _PHONE_BASIC_RE.match(cleaned) else None
    except Exception:
        return None


def parse_vcf(file_path: str | Path) -> List[str]:
    """
    Parse a VCF / vCard file and extract all valid phone numbers.

    Handles both vCard 2.1 and 3.0 / 4.0 TEL properties.
    Duplicate numbers are deduplicated while preserving order.

    Parameters
    ----------
    file_path: Path to the ``.vcf`` file.

    Returns
    -------
    List of cleaned E.164 phone number strings.

    Raises
    ------
    FileNotFoundError: If *file_path* does not exist.
    ValueError:        If the file cannot be decoded as UTF-8 or Latin-1.

    Examples
    --------
    >>> numbers = parse_vcf("contacts.vcf")
    >>> print(numbers[:3])
    ['+919876543210', '+12025551234', '+441234567890']
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"VCF file not found: {path}")

    # Try UTF-8 first, then fall back to latin-1
    text: str
    for encoding in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            text = path.read_text(encoding=encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError(f"Cannot decode VCF file: {path}")

    seen: set[str] = set()
    numbers: List[str] = []

    for line in text.splitlines():
        match = _VCF_TEL_RE.match(line.strip())
        if not match:
            continue
        raw = match.group(1).strip()
        cleaned = validate_phone_number(raw)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            numbers.append(cleaned)

    logger.debug("parse_vcf: extracted %d numbers from '%s'.", len(numbers), path)
    return numbers


def parse_txt_numbers(file_path: str | Path) -> List[str]:
    """
    Parse a plain-text file containing one phone number per line.

    Empty lines, comment lines (``#``), and invalid numbers are silently
    skipped.  Duplicates are deduplicated while preserving order.

    Parameters
    ----------
    file_path: Path to the text file.

    Returns
    -------
    List of cleaned E.164 phone number strings.

    Raises
    ------
    FileNotFoundError: If *file_path* does not exist.

    Examples
    --------
    >>> numbers = parse_txt_numbers("numbers.txt")
    >>> print(numbers[:2])
    ['+919876543210', '+12025551234']
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Text file not found: {path}")

    seen: set[str] = set()
    numbers: List[str] = []

    for raw_line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        cleaned = validate_phone_number(line)
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            numbers.append(cleaned)

    logger.debug(
        "parse_txt_numbers: extracted %d numbers from '%s'.", len(numbers), path
    )
    return numbers


# ---------------------------------------------------------------------------
# WhatsApp link helpers
# ---------------------------------------------------------------------------

# Official invite-link patterns
_WA_LINK_RE = re.compile(
    r"^https?://(?:chat\.whatsapp\.com|wa\.me/invite)/([A-Za-z0-9_-]{10,})$"
)


def validate_whatsapp_link(link: str) -> bool:
    """
    Return ``True`` if *link* is a well-formed WhatsApp group invite URL.

    Accepts both ``chat.whatsapp.com/<code>`` and ``wa.me/invite/<code>``
    formats.

    Parameters
    ----------
    link: URL string to validate.

    Returns
    -------
    ``True`` for valid invite links, ``False`` otherwise.

    Examples
    --------
    >>> validate_whatsapp_link("https://chat.whatsapp.com/AbCdEfGhIj1")
    True
    >>> validate_whatsapp_link("https://example.com")
    False
    """
    if not link or not isinstance(link, str):
        return False
    return bool(_WA_LINK_RE.match(link.strip()))


def extract_group_code_from_link(link: str) -> Optional[str]:
    """
    Extract the invite code from a WhatsApp group invite URL.

    Parameters
    ----------
    link: A valid WhatsApp invite URL.

    Returns
    -------
    The invite code string, or ``None`` if the link is invalid.

    Examples
    --------
    >>> extract_group_code_from_link("https://chat.whatsapp.com/AbCdEfGhIj1")
    'AbCdEfGhIj1'
    """
    if not link:
        return None
    match = _WA_LINK_RE.match(link.strip())
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# Progress display
# ---------------------------------------------------------------------------

_FILLED_BLOCK = "█"
_EMPTY_BLOCK = "░"


def format_progress(current: int, total: int, label: str = "", width: int = 10) -> str:
    """
    Render a compact Unicode progress bar string.

    Parameters
    ----------
    current: Number of completed items.
    total:   Total number of items.
    label:   Optional label prepended to the bar.
    width:   Total number of block characters in the bar (default 10).

    Returns
    -------
    A string like ``"Adding members ██████░░░░ 60% (6/10)"``.

    Examples
    --------
    >>> format_progress(6, 10, "Sending")
    'Sending ██████░░░░ 60% (6/10)'
    >>> format_progress(10, 10, "Done")
    'Done ██████████ 100% (10/10)'
    """
    if total <= 0:
        pct = 0
        filled = 0
    else:
        pct = min(100, int(current * 100 / total))
        filled = min(width, int(current * width / total))

    bar = _FILLED_BLOCK * filled + _EMPTY_BLOCK * (width - filled)
    parts = [bar, f"{pct}%", f"({current}/{total})"]
    if label:
        parts.insert(0, label)
    return " ".join(parts)


# ---------------------------------------------------------------------------
# List utilities
# ---------------------------------------------------------------------------


def chunk_list(lst: List[Any], size: int) -> List[List[Any]]:
    """
    Split *lst* into consecutive sub-lists of at most *size* items.

    This is used for rate-limiting bulk operations (e.g. batching add-member
    requests to avoid WhatsApp throttling).

    Parameters
    ----------
    lst:  The source list.
    size: Maximum chunk size (must be ≥ 1).

    Returns
    -------
    A list of sub-lists.

    Raises
    ------
    ValueError: If *size* < 1.

    Examples
    --------
    >>> chunk_list([1, 2, 3, 4, 5], 2)
    [[1, 2], [3, 4], [5]]
    """
    if size < 1:
        raise ValueError(f"chunk_list: size must be >= 1, got {size!r}")
    return [lst[i : i + size] for i in range(0, len(lst), size)]


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------


def get_timestamp(tz: timezone = IST) -> str:
    """
    Return the current date/time as a human-readable IST string.

    Format: ``DD-Mon-YYYY HH:MM:SS IST``

    Parameters
    ----------
    tz: Target timezone (defaults to IST, UTC+5:30).

    Returns
    -------
    Formatted timestamp string.

    Examples
    --------
    >>> ts = get_timestamp()
    >>> print(ts)   # e.g. '19-Apr-2026 14:35:07 IST'
    """
    now = datetime.now(tz=tz)
    tz_name = "IST" if tz == IST else now.strftime("%Z")
    return now.strftime(f"%d-%b-%Y %H:%M:%S {tz_name}")


def utc_now_iso() -> str:
    """Return the current UTC time as an ISO-8601 string (millisecond precision)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


# ---------------------------------------------------------------------------
# Self-test (run directly: python models_and_utils.py)
# ---------------------------------------------------------------------------

async def _selftest() -> None:
    """Quick smoke test – creates an in-memory DB and exercises every helper."""
    print("=== Self-test ===")
    db = Database(":memory:")
    await db.init_db()

    await db.add_user(111, "alice")
    await db.add_user(222, "bob")

    acc_id = await db.add_whatsapp_account(111, "+919876543210", {"key": "val"})
    print(f"Account created: id={acc_id}")

    await db.update_account_status(acc_id, "connected")
    accounts = await db.get_accounts(111)
    print(f"Accounts for user 111: {accounts}")

    await db.save_groups(
        acc_id,
        [
            {"group_jid": "123@g.us", "group_name": "Test Group", "member_count": 42},
        ],
    )
    groups = await db.get_groups(acc_id)
    print(f"Groups: {groups}")

    log_id = await db.log_operation(111, "scrape_groups", "success", "Scraped 1 group")
    logs = await db.get_logs(111)
    print(f"Logs: {logs}")

    await db.remove_account(acc_id)
    print("Account removed.")
    await db.close()

    # Utils
    print("\n--- validate_phone_number ---")
    for raw in ["+91 98765-43210", "0044 7911 123456", "123"]:
        print(f"  {raw!r} → {validate_phone_number(raw)!r}")

    print("\n--- validate_whatsapp_link ---")
    for lnk in [
        "https://chat.whatsapp.com/AbCdEfGhIj1K2L",
        "https://wa.me/invite/AbCdEfGhIj1",
        "https://example.com",
    ]:
        code = extract_group_code_from_link(lnk)
        print(f"  valid={validate_whatsapp_link(lnk)}  code={code!r}  url={lnk}")

    print("\n--- format_progress ---")
    for n in [0, 3, 6, 10]:
        print(f"  {format_progress(n, 10, 'Upload')}")

    print("\n--- chunk_list ---")
    print(f"  {chunk_list(list(range(7)), 3)}")

    print(f"\n--- get_timestamp ---\n  {get_timestamp()}")
    print("=== All tests passed ===")


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)
    asyncio.run(_selftest())
