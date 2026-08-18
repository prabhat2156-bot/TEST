"""
NEXUS STORE BOT — MongoDB core
==============================

Single shared MongoDB connection + helpers used by every module
(database.py, otp_module.py, wa_module.py, admin modules).

Everything the bot stores lives here:
  users, categories, products, stock_items, orders, deposit_requests,
  free_items, free_item_stock, support_tickets, ticket_messages, referrals,
  settings, extra_admins, coupons, coupon_uses, cart_items, resellers,
  reseller_earnings, withdraw_requests, refund_requests,
  wa_stock, wa_orders,
  otp_settings, otp_stock, otp_auto_prices, otp_orders, otp_custom_countries

Env:
  MONGODB_URI   e.g. mongodb+srv://user:pass@cluster.mongodb.net
  MONGODB_DB    default: godmadara01
"""

import os
import threading
import logging
from datetime import datetime

from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import DuplicateKeyError, PyMongoError

MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB = os.environ.get("MONGODB_DB", "godmadara01")

_client = None
_db = None
_lock = threading.Lock()
logger = logging.getLogger("mongo_client")

__all__ = [
    "get_db", "col", "next_id", "now_iso", "ensure_indexes",
    "DuplicateKeyError", "PyMongoError", "ASCENDING", "DESCENDING",
]


def now_iso() -> str:
    """Timestamp format kept identical to the old SQLite layer."""
    return datetime.now().isoformat()


def get_db():
    """Thread-safe lazy singleton. Safe to call from PTB job queue threads."""
    global _client, _db
    if _db is not None:
        return _db
    with _lock:
        if _db is None:
            _client = MongoClient(
                MONGODB_URI,
                serverSelectionTimeoutMS=8000,
                connectTimeoutMS=8000,
                socketTimeoutMS=20000,
                retryWrites=True,
                retryReads=True,
                tz_aware=False,
                maxPoolSize=100,
                minPoolSize=5,            # keep warm sockets -> no per-query handshake
                maxIdleTimeMS=300000,
                compressors="zstd,snappy,zlib",
                appname="godmadara-bot",
            )
            _db = _client[MONGODB_DB]
    return _db


def col(name: str):
    return get_db()[name]


# ── auto-increment ids (replaces SQLite AUTOINCREMENT) ───────────────────────
def next_id(name: str) -> int:
    doc = col("counters").find_one_and_update(
        {"_id": name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True,
    )
    # pymongo >=4 returns the updated doc with return_document=True
    return int(doc["seq"]) if doc else 1


def seed_counter(name: str, value: int):
    """Used by the migration script so new ids never collide with imported ones."""
    cur = col("counters").find_one({"_id": name})
    if not cur or int(cur.get("seq", 0)) < int(value):
        col("counters").update_one(
            {"_id": name}, {"$set": {"seq": int(value)}}, upsert=True
        )


# ── indexes ──────────────────────────────────────────────────────────────────
def ensure_indexes():
    """Idempotent. Mirrors the old SQLite PKs/unique indexes and adds the
    lookups the bot actually performs, so queries stay fast at scale."""
    d = get_db()

    d.users.create_index([("user_id", ASCENDING)], unique=True)
    d.users.create_index([("referral_code", ASCENDING)], unique=True, sparse=True)
    d.users.create_index([("joined_at", DESCENDING)])
    d.users.create_index([("username", ASCENDING)])

    d.categories.create_index([("id", ASCENDING)], unique=True)
    d.products.create_index([("id", ASCENDING)], unique=True)
    d.products.create_index([("category_id", ASCENDING), ("is_active", ASCENDING)])

    d.stock_items.create_index([("id", ASCENDING)], unique=True)
    d.stock_items.create_index([("product_id", ASCENDING), ("is_sold", ASCENDING)])
    d.stock_items.create_index([("product_id", ASCENDING), ("data_norm", ASCENDING)])

    d.orders.create_index([("id", ASCENDING)], unique=True)
    d.orders.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
    d.orders.create_index([("created_at", DESCENDING)])

    d.deposit_requests.create_index([("id", ASCENDING)], unique=True)
    d.deposit_requests.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
    d.deposit_requests.create_index([("status", ASCENDING), ("deposit_type", ASCENDING)])
    # replaces: UNIQUE INDEX ... ON deposit_requests(tx_hash) WHERE status='completed'
    d.deposit_requests.create_index(
        [("tx_hash", ASCENDING)],
        unique=True,
        name="uniq_completed_txhash",
        partialFilterExpression={"status": "completed", "tx_hash": {"$gt": ""}},
    )

    d.free_items.create_index([("id", ASCENDING)], unique=True)
    d.free_item_stock.create_index([("id", ASCENDING)], unique=True)
    d.free_item_stock.create_index([("free_item_id", ASCENDING), ("is_claimed", ASCENDING)])

    d.support_tickets.create_index([("id", ASCENDING)], unique=True)
    d.support_tickets.create_index([("user_id", ASCENDING)])
    d.support_tickets.create_index([("status", ASCENDING)])
    d.ticket_messages.create_index([("id", ASCENDING)], unique=True)
    d.ticket_messages.create_index([("ticket_id", ASCENDING), ("sent_at", ASCENDING)])

    d.referrals.create_index([("id", ASCENDING)], unique=True)
    d.referrals.create_index([("referrer_id", ASCENDING)])
    d.referrals.create_index([("referred_id", ASCENDING)], unique=True)

    d.settings.create_index([("key", ASCENDING)], unique=True)
    d.extra_admins.create_index([("user_id", ASCENDING)], unique=True)

    d.coupons.create_index([("id", ASCENDING)], unique=True)
    d.coupons.create_index([("code", ASCENDING)], unique=True)
    d.coupon_uses.create_index(
        [("coupon_id", ASCENDING), ("user_id", ASCENDING)], unique=True)

    d.cart_items.create_index([("id", ASCENDING)], unique=True)
    d.cart_items.create_index([("user_id", ASCENDING), ("product_id", ASCENDING)], unique=True)

    d.resellers.create_index([("user_id", ASCENDING)], unique=True)
    d.reseller_earnings.create_index([("id", ASCENDING)], unique=True)
    d.reseller_earnings.create_index([("reseller_id", ASCENDING), ("status", ASCENDING)])
    d.withdraw_requests.create_index([("id", ASCENDING)], unique=True)
    d.withdraw_requests.create_index([("status", ASCENDING)])
    d.refund_requests.create_index([("id", ASCENDING)], unique=True)
    d.refund_requests.create_index([("user_id", ASCENDING), ("order_id", ASCENDING)])
    d.refund_requests.create_index([("status", ASCENDING)])

    # WhatsApp section
    d.wa_stock.create_index([("phone", ASCENDING)], unique=True)
    d.wa_stock.create_index([("country_name", ASCENDING), ("available", ASCENDING)])
    d.wa_orders.create_index([("id", ASCENDING)], unique=True)
    d.wa_orders.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
    d.wa_orders.create_index([("status", ASCENDING)])

    # Telegram (OTP) section
    d.otp_settings.create_index([("key", ASCENDING)], unique=True)
    d.otp_stock.create_index([("id", ASCENDING)], unique=True)
    d.otp_stock.create_index([("phone", ASCENDING)], unique=True)
    d.otp_stock.create_index([("country_name", ASCENDING), ("available", ASCENDING)])
    d.otp_stock.create_index([("country_name", ASCENDING), ("account_year", ASCENDING),
                              ("price", ASCENDING), ("available", ASCENDING)])
    d.otp_auto_prices.create_index([("country", ASCENDING), ("year", ASCENDING)], unique=True)
    d.otp_orders.create_index([("id", ASCENDING)], unique=True)
    d.otp_orders.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])
    d.otp_orders.create_index([("status", ASCENDING)])
    d.otp_custom_countries.create_index([("name", ASCENDING)], unique=True)

    # Auditable wallet ledger. Balance mutations still live on users.balance
    # for fast reads, while every credit/debit/refund is retained here.
    d.wallet_transactions.create_index([("id", ASCENDING)], unique=True)
    d.wallet_transactions.create_index([("user_id", ASCENDING), ("created_at", DESCENDING)])


def strip_id(doc):
    """Return a plain dict without Mongo's _id, so callers keep working with
    the same keys the old sqlite3.Row exposed."""
    if not doc:
        return None
    d = dict(doc)
    d.pop("_id", None)
    return d


def strip_ids(docs):
    return [strip_id(x) for x in docs]
