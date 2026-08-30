"""
NEXUS STORE BOT — Database Layer
MongoDB persistent storage.

Data is stored in MongoDB instead of a local SQLite file, so:
  • Nothing is lost when the bot/process restarts or redeploys.
  • It works with a managed, backed-up MongoDB (e.g. MongoDB Atlas free tier),
    so your data lives outside the app's disk entirely.

Configure via environment variables:
  MONGODB_URI      — full connection string (e.g. from MongoDB Atlas)
  MONGODB_DB_NAME  — database name (default: "nexus_store_bot")

Every function below keeps the EXACT same name, arguments, and return shape
(plain dicts with the same keys) as the old SQLite version, so bot.py and
webapp/app.py did not need to change to use MongoDB.
"""

import os
import re
import random
import string
from datetime import datetime, timedelta

from pymongo import MongoClient, ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError

# ── Connection ──────────────────────────────────────────────────────────────

MONGODB_URI     = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB_NAME = os.environ.get("MONGODB_DB_NAME", "nexus_store_bot")

_client = MongoClient(
    MONGODB_URI,
    maxPoolSize=50,
    minPoolSize=5,
    serverSelectionTimeoutMS=5000,   # fail fast instead of hanging if Mongo is unreachable
    connectTimeoutMS=5000,
    socketTimeoutMS=10000,
    retryWrites=True,
)
_db     = _client[MONGODB_DB_NAME]

users             = _db["users"]
categories        = _db["categories"]
products          = _db["products"]
stock_items       = _db["stock_items"]
orders            = _db["orders"]
deposit_requests  = _db["deposit_requests"]
free_items        = _db["free_items"]
free_item_stock   = _db["free_item_stock"]
support_tickets   = _db["support_tickets"]
ticket_messages   = _db["ticket_messages"]
referrals         = _db["referrals"]
settings          = _db["settings"]
extra_admins      = _db["extra_admins"]
coupons           = _db["coupons"]
coupon_uses       = _db["coupon_uses"]
cart_items        = _db["cart_items"]
resellers         = _db["resellers"]
reseller_earnings = _db["reseller_earnings"]
withdraw_requests = _db["withdraw_requests"]
refund_requests   = _db["refund_requests"]
refund_messages   = _db["refund_messages"]
web_admins        = _db["web_admins"]
restock_requests  = _db["restock_requests"]
counters          = _db["counters"]


# ── Internal helpers ──────────────────────────────────────────────────────────

def _next_id(name: str) -> int:
    """Atomic auto-increment counter — mimics SQLite's AUTOINCREMENT id."""
    doc = counters.find_one_and_update(
        {"_id": name},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    return doc["seq"]


def _strip_mongo_id(doc):
    """Drop Mongo's internal _id so callers get the same plain dict shape
    they used to get from `dict(sqlite3.Row)`."""
    if doc is None:
        return None
    doc = dict(doc)
    doc.pop("_id", None)
    return doc


def _strip_many(cursor):
    return [_strip_mongo_id(d) for d in cursor]


def _sum(collection, filt, field):
    pipeline = [{"$match": filt}, {"$group": {"_id": None, "s": {"$sum": f"${field}"}}}]
    result = list(collection.aggregate(pipeline))
    return result[0]["s"] if result else 0


def _user_username_fullname(user_id):
    u = users.find_one({"user_id": user_id}, {"username": 1, "full_name": 1})
    return (u.get("username", "") if u else ""), (u.get("full_name", "") if u else "")


def get_conn():
    """SQLite leftover. The DB layer now runs on MongoDB — raw SQL against it
    makes no sense, so any remaining caller of this needs a proper helper
    function added to this file instead."""
    raise RuntimeError(
        "get_conn() is a SQLite leftover — the DB layer now runs on MongoDB. "
        "Add/use a database.py helper function instead of raw SQL."
    )


def init_db():
    """Create indexes + one-time bootstrap. Safe to call on every startup."""
    try:
        _client.admin.command("ping")
    except PyMongoError as e:
        raise RuntimeError(f"Could not connect to MongoDB at {MONGODB_URI}: {e}")

    users.create_index("user_id", unique=True)
    users.create_index("referral_code", unique=True, sparse=True)
    categories.create_index("id", unique=True)
    products.create_index("id", unique=True)
    products.create_index("category_id")
    stock_items.create_index("id", unique=True)
    stock_items.create_index([("product_id", ASCENDING), ("is_sold", ASCENDING)])
    stock_items.create_index("order_id")
    orders.create_index("id", unique=True)
    orders.create_index("user_id")
    orders.create_index("created_at")
    deposit_requests.create_index("id", unique=True)
    deposit_requests.create_index("user_id")
    deposit_requests.create_index("status")
    free_items.create_index("id", unique=True)
    free_item_stock.create_index("id", unique=True)
    free_item_stock.create_index("free_item_id")
    support_tickets.create_index("id", unique=True)
    ticket_messages.create_index("id", unique=True)
    ticket_messages.create_index("ticket_id")
    referrals.create_index("id", unique=True)
    settings.create_index("key", unique=True)
    extra_admins.create_index("user_id", unique=True)
    coupons.create_index("id", unique=True)
    coupons.create_index("code", unique=True)
    coupon_uses.create_index("id", unique=True)
    cart_items.create_index("id", unique=True)
    cart_items.create_index([("user_id", ASCENDING), ("product_id", ASCENDING)])
    resellers.create_index("user_id", unique=True)
    reseller_earnings.create_index("id", unique=True)
    withdraw_requests.create_index("id", unique=True)
    refund_requests.create_index("id", unique=True)
    refund_messages.create_index("id", unique=True)
    web_admins.create_index("id", unique=True)
    web_admins.create_index("username", unique=True)
    restock_requests.create_index("id", unique=True)

    if not get_categories(active_only=False):
        add_category("General", "🛍️")


# ── User ──────────────────────────────────────────────────────────────────────

def _gen_ref():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))


def get_or_create_user(user_id: int, username: str = "", full_name: str = "") -> dict:
    existing = users.find_one({"user_id": user_id})
    if existing:
        new_username  = username or existing.get("username", "")
        new_full_name = full_name or existing.get("full_name", "")
        users.update_one({"user_id": user_id}, {"$set": {"username": new_username, "full_name": new_full_name}})
        return get_user(user_id)

    code = _gen_ref()
    while users.find_one({"referral_code": code}):
        code = _gen_ref()
    try:
        users.insert_one({
            "user_id": user_id,
            "username": username,
            "full_name": full_name,
            "balance": 0.0,
            "referral_code": code,
            "referred_by": None,
            "total_orders": 0,
            "total_spent_usdt": 0.0,
            "is_vip": 0,
            "is_banned": 0,
            "language": "",
            "claimed_free_item": 0,
            "joined_at": datetime.now().isoformat(),
        })
    except DuplicateKeyError:
        pass  # created concurrently by another request — fine, just re-read it
    return get_user(user_id)


def get_user(user_id: int):
    return _strip_mongo_id(users.find_one({"user_id": user_id}))


def user_exists(user_id: int) -> bool:
    return users.find_one({"user_id": user_id}, {"_id": 1}) is not None


def get_user_language(user_id: int) -> str:
    u = users.find_one({"user_id": user_id}, {"language": 1})
    if u and u.get("language"):
        return u["language"]
    return "en"


def set_user_language(user_id: int, lang: str):
    users.update_one({"user_id": user_id}, {"$set": {"language": lang}})


def update_balance(user_id: int, delta: float):
    users.update_one({"user_id": user_id}, {"$inc": {"balance": delta}})


def get_all_users():
    return _strip_many(users.find().sort("joined_at", -1))


def ban_user(user_id: int, ban: bool):
    users.update_one({"user_id": user_id}, {"$set": {"is_banned": 1 if ban else 0}})


def search_users(query: str):
    q = re.escape(query.strip())
    or_clauses = [
        {"username": {"$regex": q, "$options": "i"}},
        {"full_name": {"$regex": q, "$options": "i"}},
        {"$expr": {"$regexMatch": {"input": {"$toString": "$user_id"}, "regex": q, "options": "i"}}},
    ]
    return _strip_many(users.find({"$or": or_clauses}).limit(20))


# ── Categories ────────────────────────────────────────────────────────────────

def get_categories(active_only=True):
    filt = {"is_active": 1} if active_only else {}
    return _strip_many(categories.find(filt).sort("id", 1))


def add_category(name, emoji):
    cid = _next_id("categories")
    categories.insert_one({"id": cid, "name": name, "emoji": emoji, "is_active": 1})
    return cid


def toggle_category(cid):
    cat = categories.find_one({"id": cid})
    if not cat:
        return
    categories.update_one({"id": cid}, {"$set": {"is_active": 1 - cat.get("is_active", 1)}})


def delete_category(cid, force=False):
    try:
        prod_count = products.count_documents({"category_id": cid})
        if prod_count:
            if not force:
                return False
            general = categories.find_one({"name": "General", "id": {"$ne": cid}})
            general_id = general["id"] if general else add_category("General", "🛍️")
            products.update_many({"category_id": cid}, {"$set": {"category_id": general_id}})
        categories.delete_one({"id": cid})
        return True
    except PyMongoError:
        return False


def get_category(cid):
    return _strip_mongo_id(categories.find_one({"id": cid}))


# ── Products ──────────────────────────────────────────────────────────────────

def get_products(category_id=None, active_only=True):
    filt = {}
    if category_id:
        filt["category_id"] = category_id
    if active_only:
        filt["is_active"] = 1
    return _strip_many(products.find(filt).sort("id", 1))


def get_product(pid):
    return _strip_mongo_id(products.find_one({"id": pid}))


def add_product(category_id, name, emoji, description, price_usdt, duration, emoji_id=""):
    pid = _next_id("products")
    products.insert_one({
        "id": pid, "category_id": category_id, "name": name, "emoji": emoji,
        "description": description, "price_usdt": price_usdt, "duration": duration,
        "stock_count": 0, "is_active": 1, "created_at": datetime.now().isoformat(),
        "emoji_id": emoji_id,
    })
    return pid


def update_product_emoji_id(pid, emoji_id):
    products.update_one({"id": pid}, {"$set": {"emoji_id": emoji_id}})


def delete_product(pid):
    products.delete_one({"id": pid})


# ── Stock ─────────────────────────────────────────────────────────────────────

def get_stock_count(product_id):
    return stock_items.count_documents({"product_id": product_id, "is_sold": 0})


def refresh_stock_count(product_id):
    n = get_stock_count(product_id)
    products.update_one({"id": product_id}, {"$set": {"stock_count": n}})


def add_stock(product_id, items: list):
    now = datetime.now().isoformat()
    existing = {d["data_norm"] for d in stock_items.find({"product_id": product_id}, {"data_norm": 1})}
    to_add = []
    duplicate_lines = []
    for item in items:
        norm = item.strip().lower()
        if norm in existing:
            duplicate_lines.append(item)
        else:
            to_add.append(item)
            existing.add(norm)
    docs = [{
        "id": _next_id("stock_items"), "product_id": product_id,
        "data": item.strip(), "data_norm": item.strip().lower(),
        "is_sold": 0, "sold_to": None, "order_id": None,
        "added_at": now, "sold_at": None,
    } for item in to_add]
    if docs:
        stock_items.insert_many(docs)
        refresh_stock_count(product_id)
    return {"added": len(to_add), "duplicates": len(duplicate_lines), "duplicate_lines": duplicate_lines}


def get_stock_item_by_order(order_id):
    """Replaces a raw-SQL lookup that used to live directly in bot.py."""
    return _strip_mongo_id(stock_items.find_one({"order_id": order_id}))


# ── Free Items ────────────────────────────────────────────────────────────────

def create_free_item(name, emoji, description=""):
    fid = _next_id("free_items")
    free_items.insert_one({"id": fid, "name": name, "emoji": emoji, "description": description,
                            "is_active": 1, "created_at": datetime.now().isoformat()})
    return fid


def get_free_items(active_only=True):
    filt = {"is_active": 1} if active_only else {}
    return _strip_many(free_items.find(filt).sort("id", -1))


def get_free_item(fid):
    return _strip_mongo_id(free_items.find_one({"id": fid}))


def toggle_free_item(fid):
    item = free_items.find_one({"id": fid})
    if not item:
        return None
    new_val = 0 if item.get("is_active") else 1
    free_items.update_one({"id": fid}, {"$set": {"is_active": new_val}})
    return new_val


def delete_free_item(fid):
    free_items.delete_one({"id": fid})
    free_item_stock.delete_many({"free_item_id": fid})


def get_free_stock_count(fid):
    return free_item_stock.count_documents({"free_item_id": fid, "is_claimed": 0})


def add_free_stock(fid, items: list):
    now = datetime.now().isoformat()
    docs = []
    for item in items:
        item = item.strip()
        if not item:
            continue
        docs.append({"id": _next_id("free_item_stock"), "free_item_id": fid, "data": item,
                     "is_claimed": 0, "claimed_by": None, "claimed_at": None, "added_at": now})
    if docs:
        free_item_stock.insert_many(docs)
    return len(items)


def has_user_claimed_free_item(user_id):
    u = users.find_one({"user_id": user_id}, {"claimed_free_item": 1})
    return bool(u and u.get("claimed_free_item"))


def claim_free_item(user_id, fid):
    """Atomically claim one free-item stock line for this user.
    Returns (ok, data_or_reason). A user may claim only ONE free item, EVER."""
    # Step 1: atomically reserve the "one claim per user, ever" slot.
    claimed_user = users.find_one_and_update(
        {"user_id": user_id, "claimed_free_item": {"$ne": 1}},
        {"$set": {"claimed_free_item": 1}},
    )
    if claimed_user is None:
        return False, "already_claimed"

    # Step 2: atomically grab one unclaimed stock line.
    now = datetime.now().isoformat()
    stock_doc = free_item_stock.find_one_and_update(
        {"free_item_id": fid, "is_claimed": 0},
        {"$set": {"is_claimed": 1, "claimed_by": user_id, "claimed_at": now}},
        return_document=ReturnDocument.AFTER,
    )
    if stock_doc is None:
        # Out of stock — release the claim slot so the user can try again later.
        users.update_one({"user_id": user_id}, {"$set": {"claimed_free_item": 0}})
        return False, "out_of_stock"
    return True, stock_doc["data"]


def pop_stock(product_id, qty=1):
    return _strip_many(stock_items.find({"product_id": product_id, "is_sold": 0}).limit(qty))


def mark_stock_sold(item_id, user_id, order_id):
    stock_items.update_one({"id": item_id}, {"$set": {
        "is_sold": 1, "sold_to": user_id, "order_id": order_id, "sold_at": datetime.now().isoformat()
    }})


def get_stock_items(product_id, include_sold=False, limit=50):
    filt = {"product_id": product_id}
    if not include_sold:
        filt["is_sold"] = 0
    return _strip_many(stock_items.find(filt).limit(limit))


def edit_stock_item(item_id, new_data):
    item = stock_items.find_one({"id": item_id})
    if not item:
        return False
    stock_items.update_one({"id": item_id}, {"$set": {"data": new_data, "data_norm": new_data.strip().lower()}})
    return True


def remove_stock_item(item_id):
    item = stock_items.find_one({"id": item_id})
    stock_items.delete_one({"id": item_id})
    if item:
        refresh_stock_count(item["product_id"])


def clear_stock(product_id):
    stock_items.delete_many({"product_id": product_id, "is_sold": 0})
    refresh_stock_count(product_id)


# ── Orders ────────────────────────────────────────────────────────────────────

def create_order(user_id, product_id, product_name, amount_usdt, stock_item_id=None,
                 coupon_code=None, is_reseller_sale=False):
    oid = _next_id("orders")
    orders.insert_one({
        "id": oid, "user_id": user_id, "product_id": product_id, "product_name": product_name,
        "amount_usdt": amount_usdt, "status": "completed", "stock_item_id": stock_item_id,
        "coupon_code": coupon_code, "is_reseller_sale": 1 if is_reseller_sale else 0,
        "reminder_sent": 0, "refunded": 0, "created_at": datetime.now().isoformat(),
    })
    users.update_one({"user_id": user_id}, {"$inc": {"total_orders": 1, "total_spent_usdt": amount_usdt}})
    refresh_stock_count(product_id)
    return oid


def get_user_orders(user_id, limit=20, offset=0):
    return _strip_many(orders.find({"user_id": user_id}).sort("id", -1).skip(offset).limit(limit))


def get_user_order_count(user_id):
    return orders.count_documents({"user_id": user_id})


def get_order(oid):
    return _strip_mongo_id(orders.find_one({"id": oid}))


def _batch_usernames(user_ids):
    """One round-trip lookup of username+full_name for a list of user_ids,
    instead of one query per row (this was the main slowdown vs SQLite)."""
    ids = list({uid for uid in user_ids if uid is not None})
    if not ids:
        return {}
    docs = users.find({"user_id": {"$in": ids}}, {"user_id": 1, "username": 1, "full_name": 1})
    return {d["user_id"]: (d.get("username", ""), d.get("full_name", "")) for d in docs}


def _attach_username_and_cred(order_docs):
    """Batched equivalent of the old LEFT JOIN users / stock_items."""
    order_docs = list(order_docs)
    if not order_docs:
        return []
    uname_map = _batch_usernames([o["user_id"] for o in order_docs])
    oid_list = [o["id"] for o in order_docs]
    stock_map = {
        s["order_id"]: s.get("data")
        for s in stock_items.find({"order_id": {"$in": oid_list}}, {"order_id": 1, "data": 1})
    }
    out = []
    for o in order_docs:
        o = dict(o)
        o["username"] = uname_map.get(o["user_id"], ("", ""))[0]
        o["cred_data"] = stock_map.get(o["id"])
        out.append(o)
    return out


def get_today_orders(limit=10000):
    today = datetime.now().strftime("%Y-%m-%d")
    cursor = orders.find({"created_at": {"$regex": f"^{re.escape(today)}"}}).sort("id", -1).limit(limit)
    return _attach_username_and_cred(_strip_many(cursor))


def get_all_orders(limit=1000000):
    cursor = orders.find().sort("id", -1).limit(limit)
    return _attach_username_and_cred(_strip_many(cursor))


def refund_order(oid):
    order = orders.find_one({"id": oid})
    if not order or order.get("refunded"):
        return None
    orders.update_one({"id": oid}, {"$set": {"refunded": 1}})
    users.update_one({"user_id": order["user_id"]},
                      {"$inc": {"balance": order["amount_usdt"], "total_spent_usdt": -order["amount_usdt"]}})
    if order.get("stock_item_id"):
        stock_items.update_one({"id": order["stock_item_id"]},
                                {"$set": {"is_sold": 0, "sold_to": None, "order_id": None, "sold_at": None}})
        refresh_stock_count(order["product_id"])
    return _strip_mongo_id(order)


# ── Deposits ──────────────────────────────────────────────────────────────────

def get_unique_expected_amount(base_amount: float, network: str = "TRC20") -> float:
    """
    Generate a collision-free expected_usdt amount for a new deposit request.
    Supports 100+ concurrent users depositing the same base amount.
    """
    used = {round(d["expected_usdt"], 3) for d in deposit_requests.find({"status": "pending"}, {"expected_usdt": 1})}

    for i in range(1, 100):
        candidate = round(base_amount + i / 1000, 3)
        if candidate not in used:
            return candidate

    for i in range(1, 10):
        candidate = round(base_amount + i / 10, 1)
        if candidate not in used:
            return candidate

    return round(base_amount + random.randint(1, 9) / 100, 2)


def create_deposit_request(user_id, requested_usdt, expected_usdt, expires_at,
                           network="TRC20", deposit_type="address", pay_uid="", dep_note=""):
    did = _next_id("deposit_requests")
    deposit_requests.insert_one({
        "id": did, "user_id": user_id, "requested_usdt": requested_usdt, "expected_usdt": expected_usdt,
        "network": network, "deposit_type": deposit_type, "pay_uid": pay_uid, "dep_note": dep_note,
        "tx_hash": "", "status": "pending", "binance_txid": None, "credited_at": None,
        "created_at": datetime.now().isoformat(), "expires_at": expires_at, "fail_reason": "",
    })
    return did


def set_deposit_tx_hash(dep_id: int, tx_hash: str):
    deposit_requests.update_one({"id": dep_id}, {"$set": {"tx_hash": tx_hash}})


def get_deposit(dep_id: int):
    return _strip_mongo_id(deposit_requests.find_one({"id": dep_id}))


def get_pending_deposits():
    return _strip_many(deposit_requests.find({"status": "pending"}).sort("id", 1))


def get_pending_deposits_by_type(deposit_type: str):
    return _strip_many(deposit_requests.find({"status": "pending", "deposit_type": deposit_type}).sort("id", 1))


def complete_deposit(dep_id, txid):
    deposit_requests.update_one({"id": dep_id}, {"$set": {
        "status": "completed", "binance_txid": txid, "credited_at": datetime.now().isoformat()
    }})


def get_completed_deposit_by_txhash(tx_hash: str):
    """Return a COMPLETED deposit (any user) that already used this exact tx hash, or None.
    Used to block a transaction hash from being claimed/credited more than once."""
    if not tx_hash:
        return None
    row = deposit_requests.find_one({
        "status": "completed",
        "tx_hash": {"$regex": f"^{re.escape(tx_hash.strip())}$", "$options": "i"},
    })
    return _strip_mongo_id(row)


def mark_deposit_failed(dep_id: int, reason: str):
    deposit_requests.update_one({"id": dep_id}, {"$set": {"status": "failed", "fail_reason": reason}})


def set_deposit_status(dep_id: int, status: str):
    """New helper — replaces raw-SQL status updates that used to live directly
    in bot.py / webapp/app.py (e.g. cancelling a rejected deposit)."""
    deposit_requests.update_one({"id": dep_id}, {"$set": {"status": status}})


def expire_old_deposits():
    now = datetime.now().isoformat()
    rows = list(deposit_requests.find({"status": "pending", "expires_at": {"$lt": now}}))
    if rows:
        ids = [r["id"] for r in rows]
        deposit_requests.update_many({"id": {"$in": ids}}, {"$set": {"status": "expired"}})
    return _strip_many(rows)


def get_user_deposits(user_id, limit=10):
    return _strip_many(deposit_requests.find({"user_id": user_id}).sort("id", -1).limit(limit))


def get_today_deposits():
    today = datetime.now().strftime("%Y-%m-%d")
    cursor = deposit_requests.find({
        "status": "completed", "credited_at": {"$regex": f"^{re.escape(today)}"},
    }).sort("id", -1)
    docs = list(cursor)
    if not docs:
        return []
    uname_map = _batch_usernames([d["user_id"] for d in docs])
    out = []
    for d in docs:
        d = dict(d)
        d["username"] = uname_map.get(d["user_id"], ("", ""))[0]
        out.append(_strip_mongo_id(d))
    return out


# ── Settings ──────────────────────────────────────────────────────────────────

def get_setting(key, default=""):
    row = settings.find_one({"key": key})
    return row["value"] if row else default


def get_setting_float(key, default=0.0):
    v = get_setting(key, "")
    try:
        return float(v) if v else default
    except Exception:
        return default


def get_setting_int(key, default=0):
    v = get_setting(key, "")
    try:
        return int(v) if v else default
    except Exception:
        return default


def set_setting(key, value):
    settings.update_one({"key": key}, {"$set": {"value": str(value)}}, upsert=True)


# ── Stats ─────────────────────────────────────────────────────────────────────

def get_stats():
    n_users   = users.count_documents({})
    n_orders  = orders.count_documents({"refunded": 0})
    revenue   = _sum(orders, {"refunded": 0}, "amount_usdt")
    total_dep = _sum(deposit_requests, {"status": "completed"}, "requested_usdt")
    return {"users": n_users, "orders": n_orders, "revenue": revenue, "total_dep": total_dep}


def get_today_stats():
    today = datetime.now().strftime("%Y-%m-%d")
    rgx = {"$regex": f"^{re.escape(today)}"}
    n_orders = orders.count_documents({"created_at": rgx, "refunded": 0})
    revenue  = _sum(orders, {"created_at": rgx, "refunded": 0}, "amount_usdt")
    dep_cnt  = deposit_requests.count_documents({"credited_at": rgx})
    dep_amt  = _sum(deposit_requests, {"credited_at": rgx}, "requested_usdt")
    return {"ord_count": n_orders, "ord_amount": revenue, "dep_count": dep_cnt, "dep_amount": dep_amt}


def _attach_username_only(cursor):
    docs = list(cursor)
    if not docs:
        return []
    uname_map = _batch_usernames([d["user_id"] for d in docs])
    out = []
    for d in docs:
        d = dict(d)
        d["username"] = uname_map.get(d["user_id"], ("", ""))[0]
        out.append(_strip_mongo_id(d))
    return out


def get_daily_report(date_str: str):
    """Full admin daily report for an arbitrary date (YYYY-MM-DD): orders
    placed that day, deposits completed that day, and a wallet-balance
    snapshot (current, not historical)."""
    rgx = {"$regex": f"^{re.escape(date_str)}"}
    ord_count  = orders.count_documents({"created_at": rgx, "refunded": 0})
    ord_amount = _sum(orders, {"created_at": rgx, "refunded": 0}, "amount_usdt")
    dep_count  = deposit_requests.count_documents({"status": "completed", "credited_at": rgx})
    dep_amount = _sum(deposit_requests, {"status": "completed", "credited_at": rgx}, "requested_usdt")
    dep_failed = deposit_requests.count_documents({"created_at": rgx, "status": {"$in": ["expired", "cancelled"]}})
    new_users  = users.count_documents({"joined_at": rgx})
    total_bal   = _sum(users, {}, "balance")
    total_users = users.count_documents({})
    day_orders   = _attach_username_only(orders.find({"created_at": rgx, "refunded": 0}).sort("id", -1))
    day_deposits = _attach_username_only(deposit_requests.find({"status": "completed", "credited_at": rgx}).sort("id", -1))
    return {
        "date": date_str,
        "ord_count": ord_count, "ord_amount": ord_amount,
        "dep_count": dep_count, "dep_amount": dep_amount,
        "dep_failed": dep_failed, "new_users": new_users,
        "total_balance_now": total_bal, "total_users_now": total_users,
        "orders": day_orders, "deposits": day_deposits,
    }


# ── Admins ────────────────────────────────────────────────────────────────────

def get_extra_admins():
    return [d["user_id"] for d in extra_admins.find({}, {"user_id": 1})]


def add_extra_admin(user_id):
    extra_admins.update_one({"user_id": user_id}, {"$setOnInsert": {"user_id": user_id}}, upsert=True)


def remove_extra_admin(user_id):
    extra_admins.delete_one({"user_id": user_id})


# ── Support Tickets ───────────────────────────────────────────────────────────

def create_ticket(user_id, subject):
    tid = _next_id("support_tickets")
    now = datetime.now().isoformat()
    support_tickets.insert_one({"id": tid, "user_id": user_id, "subject": subject,
                                 "status": "open", "created_at": now, "updated_at": now})
    return tid


def get_ticket(tid):
    return _strip_mongo_id(support_tickets.find_one({"id": tid}))


def get_user_tickets(user_id):
    return _strip_many(support_tickets.find({"user_id": user_id}).sort("id", -1))


def _attach_username_fullname(cursor):
    docs = list(cursor)
    if not docs:
        return []
    uname_map = _batch_usernames([d["user_id"] for d in docs])
    out = []
    for d in docs:
        d = dict(d)
        d["username"], d["full_name"] = uname_map.get(d["user_id"], ("", ""))
        out.append(_strip_mongo_id(d))
    return out


def get_open_tickets():
    return _attach_username_fullname(support_tickets.find({"status": "open"}).sort("id", 1))


def add_ticket_message(ticket_id, sender_id, message, is_admin=False):
    mid = _next_id("ticket_messages")
    ticket_messages.insert_one({"id": mid, "ticket_id": ticket_id, "sender_id": sender_id,
                                 "is_admin": 1 if is_admin else 0, "message": message,
                                 "sent_at": datetime.now().isoformat()})
    support_tickets.update_one({"id": ticket_id}, {"$set": {"updated_at": datetime.now().isoformat()}})


def get_ticket_messages(ticket_id):
    return _strip_many(ticket_messages.find({"ticket_id": ticket_id}).sort("id", 1))


def close_ticket(tid):
    support_tickets.update_one({"id": tid}, {"$set": {"status": "closed", "updated_at": datetime.now().isoformat()}})


def get_all_tickets():
    return _attach_username_fullname(support_tickets.find().sort("id", -1))


# ── Referrals ─────────────────────────────────────────────────────────────────

def get_user_by_referral_code(code):
    return _strip_mongo_id(users.find_one({"referral_code": code}))


def set_referred_by(user_id, referrer_id):
    users.update_one({"user_id": user_id, "referred_by": None}, {"$set": {"referred_by": referrer_id}})
    if not referrals.find_one({"referrer_id": referrer_id, "referred_id": user_id}):
        rid = _next_id("referrals")
        referrals.insert_one({"id": rid, "referrer_id": referrer_id, "referred_id": user_id,
                               "bonus_paid": 0.0, "created_at": datetime.now().isoformat()})


def get_referral_count(user_id):
    return referrals.count_documents({"referrer_id": user_id})


def credit_referral_deposit_bonus(user_id, amount):
    """Give the referrer a flat REFERRAL_BONUS_USDT bonus when their referred
    friend makes a qualifying deposit. Returns (referrer_id, bonus_amount) or (None, 0)."""
    import config
    user = users.find_one({"user_id": user_id}, {"referred_by": 1})
    if not user or not user.get("referred_by"):
        return None, 0
    ref_id = user["referred_by"]
    bonus = round(config.REFERRAL_BONUS_USDT, 6) if amount >= 1 else 0
    if bonus > 0:
        users.update_one({"user_id": ref_id}, {"$inc": {"balance": bonus}})
        referrals.update_one({"referrer_id": ref_id, "referred_id": user_id}, {"$inc": {"bonus_paid": bonus}})
    return ref_id, bonus


def promote_vip(user_id):
    import config
    ref_count = get_referral_count(user_id)
    if ref_count >= config.VIP_REFERRALS_NEEDED:
        vip_count = users.count_documents({"is_vip": 1})
        if vip_count < config.MAX_VIP_MEMBERS:
            users.update_one({"user_id": user_id}, {"$set": {"is_vip": 1}})


# ── Coupons ───────────────────────────────────────────────────────────────────

def get_coupons():
    return _strip_many(coupons.find().sort("id", -1))


def add_coupon(code, discount, max_uses):
    if coupons.find_one({"code": code.upper()}):
        return
    cid = _next_id("coupons")
    try:
        coupons.insert_one({"id": cid, "code": code.upper(), "discount": discount, "max_uses": max_uses,
                             "used_count": 0, "is_active": 1, "created_at": datetime.now().isoformat()})
    except DuplicateKeyError:
        pass


def toggle_coupon(cid):
    c = coupons.find_one({"id": cid})
    if not c:
        return
    coupons.update_one({"id": cid}, {"$set": {"is_active": 1 - c.get("is_active", 1)}})


def delete_coupon(cid):
    coupons.delete_one({"id": cid})


def validate_coupon(code, user_id):
    c = coupons.find_one({"code": code.upper(), "is_active": 1})
    if not c:
        return None, "not_found"
    c = _strip_mongo_id(c)
    if c["used_count"] >= c["max_uses"]:
        return None, "exhausted"
    if coupon_uses.find_one({"coupon_id": c["id"], "user_id": user_id}):
        return None, "already_used"
    return c, "ok"


def apply_coupon(coupon_id, user_id):
    coupons.update_one({"id": coupon_id}, {"$inc": {"used_count": 1}})
    if not coupon_uses.find_one({"coupon_id": coupon_id, "user_id": user_id}):
        uid = _next_id("coupon_uses")
        coupon_uses.insert_one({"id": uid, "coupon_id": coupon_id, "user_id": user_id,
                                 "used_at": datetime.now().isoformat()})


# ── Cart ──────────────────────────────────────────────────────────────────────

def add_to_cart(user_id, product_id, qty=1):
    existing = cart_items.find_one({"user_id": user_id, "product_id": product_id})
    if existing:
        cart_items.update_one({"id": existing["id"]}, {"$inc": {"quantity": qty}})
    else:
        cid = _next_id("cart_items")
        cart_items.insert_one({"id": cid, "user_id": user_id, "product_id": product_id,
                                "quantity": qty, "added_at": datetime.now().isoformat()})


def get_cart(user_id):
    out = []
    for ci in cart_items.find({"user_id": user_id}).sort("id", 1):
        p = products.find_one({"id": ci["product_id"]})
        if not p:
            continue  # matches the old INNER JOIN behaviour
        out.append({
            "cart_id": ci["id"], "product_id": ci["product_id"], "quantity": ci["quantity"],
            "name": p.get("name"), "emoji": p.get("emoji"), "price_usdt": p.get("price_usdt"),
            "category_id": p.get("category_id"), "is_active": p.get("is_active"),
        })
    return out


def get_cart_item(user_id, product_id):
    return _strip_mongo_id(cart_items.find_one({"user_id": user_id, "product_id": product_id}))


def set_cart_qty(user_id, product_id, qty):
    if qty <= 0:
        cart_items.delete_one({"user_id": user_id, "product_id": product_id})
    else:
        cart_items.update_one({"user_id": user_id, "product_id": product_id}, {"$set": {"quantity": qty}})


def remove_cart_item(user_id, product_id):
    cart_items.delete_one({"user_id": user_id, "product_id": product_id})


def clear_cart(user_id):
    cart_items.delete_many({"user_id": user_id})


# ── Resellers ─────────────────────────────────────────────────────────────────

def is_reseller(user_id):
    r = resellers.find_one({"user_id": user_id})
    return bool(r and r.get("approved") == 1)


def add_reseller(user_id):
    if not resellers.find_one({"user_id": user_id}):
        try:
            resellers.insert_one({"user_id": user_id, "approved": 0, "created_at": datetime.now().isoformat()})
        except DuplicateKeyError:
            pass


def approve_reseller(user_id):
    resellers.update_one({"user_id": user_id}, {"$set": {"approved": 1}})


def revoke_reseller(user_id):
    resellers.delete_one({"user_id": user_id})


def get_resellers():
    docs = list(resellers.find({"approved": 1}))
    if not docs:
        return []
    uname_map = _batch_usernames([d["user_id"] for d in docs])
    out = []
    for r in docs:
        r = dict(r)
        r["username"], r["full_name"] = uname_map.get(r["user_id"], ("", ""))
        out.append(_strip_mongo_id(r))
    return out


def add_reseller_earning(reseller_id, order_id, gross_margin, owner_cut, reseller_cut):
    import config
    delay = get_setting_int("reseller_credit_delay_hours", config.RESELLER_CREDIT_DELAY_HOURS)
    now = datetime.now()
    available_at = (now + timedelta(hours=delay)).isoformat()
    eid = _next_id("reseller_earnings")
    reseller_earnings.insert_one({
        "id": eid, "reseller_id": reseller_id, "order_id": order_id,
        "gross_margin": gross_margin, "owner_cut": owner_cut, "reseller_cut": reseller_cut,
        "status": "pending", "created_at": now.isoformat(), "available_at": available_at,
    })
    return reseller_cut, owner_cut


def mature_reseller_earnings():
    now = datetime.now().isoformat()
    rows = list(reseller_earnings.find({"status": "pending", "available_at": {"$lte": now}}))
    if rows:
        ids = [r["id"] for r in rows]
        reseller_earnings.update_many({"id": {"$in": ids}}, {"$set": {"status": "available"}})
    return _strip_many(rows)


def get_reseller_balance_summary(reseller_id):
    pending   = _sum(reseller_earnings, {"reseller_id": reseller_id, "status": "pending"},   "reseller_cut")
    available = _sum(reseller_earnings, {"reseller_id": reseller_id, "status": "available"}, "reseller_cut")
    withdrawn = _sum(reseller_earnings, {"reseller_id": reseller_id, "status": "withdrawn"}, "reseller_cut")
    return {"pending": pending, "available": available, "withdrawn": withdrawn}


def create_withdraw_request(user_id, amount):
    wid = _next_id("withdraw_requests")
    withdraw_requests.insert_one({"id": wid, "user_id": user_id, "amount": amount,
                                   "status": "pending", "requested_at": datetime.now().isoformat(),
                                   "processed_at": None})
    return wid


def get_pending_withdraw_requests():
    return _strip_many(withdraw_requests.find({"status": "pending"}).sort("id", 1))


def get_withdraw_request(wid):
    return _strip_mongo_id(withdraw_requests.find_one({"id": wid}))


def mark_withdraw_earnings_consumed(reseller_id, amount):
    rows = list(reseller_earnings.find({"reseller_id": reseller_id, "status": "available"}).sort("id", 1))
    remaining = amount
    for r in rows:
        if remaining <= 0:
            break
        reseller_earnings.update_one({"id": r["id"]}, {"$set": {"status": "withdrawn"}})
        remaining -= r["reseller_cut"]


def process_withdraw_request(wid, approve):
    req = withdraw_requests.find_one({"id": wid})
    if not req:
        return None
    status = "paid" if approve else "rejected"
    withdraw_requests.update_one({"id": wid}, {"$set": {"status": status, "processed_at": datetime.now().isoformat()}})
    if approve:
        mark_withdraw_earnings_consumed(req["user_id"], req["amount"])
    return _strip_mongo_id(req)


# ── Advanced Admin Queries ─────────────────────────────────────────────────────

def get_today_deposits_all():
    """All deposits created today regardless of status (success + failed + pending)."""
    today = datetime.now().strftime("%Y-%m-%d")
    cursor = deposit_requests.find({"created_at": {"$regex": f"^{re.escape(today)}"}}).sort("id", -1)
    return _attach_username_fullname(cursor)


def get_today_deposit_stats():
    """Today counts: success (count+sum), failed (expired+cancelled), pending."""
    today = datetime.now().strftime("%Y-%m-%d")
    rgx = {"$regex": f"^{re.escape(today)}"}
    ok_count   = deposit_requests.count_documents({"created_at": rgx, "status": "completed"})
    ok_amt     = _sum(deposit_requests, {"created_at": rgx, "status": "completed"}, "requested_usdt")
    fail_count = deposit_requests.count_documents({"created_at": rgx, "status": {"$in": ["expired", "cancelled"]}})
    pend_count = deposit_requests.count_documents({"created_at": rgx, "status": "pending"})
    return {"success_count": ok_count, "success_amt": ok_amt,
            "failed_count": fail_count, "pending_count": pend_count}


def get_all_deposits_list(limit=50000):
    """All deposits ever, all statuses."""
    cursor = deposit_requests.find().sort("id", -1).limit(limit)
    return _attach_username_fullname(cursor)


def get_all_time_deposit_stats():
    ok_count   = deposit_requests.count_documents({"status": "completed"})
    ok_amt     = _sum(deposit_requests, {"status": "completed"}, "requested_usdt")
    fail_count = deposit_requests.count_documents({"status": {"$in": ["expired", "cancelled"]}})
    pend_count = deposit_requests.count_documents({"status": "pending"})
    return {"success_count": ok_count, "success_amt": ok_amt,
            "failed_count": fail_count, "pending_count": pend_count}


def get_user_full_history(user_id):
    """All deposits + orders for a user, newest first."""
    deps = [{
        "type": "deposit", "id": d["id"], "created_at": d.get("created_at"),
        "amount": d.get("requested_usdt"), "extra": d.get("network", ""),
        "status": d.get("status"), "product_name": "",
    } for d in deposit_requests.find({"user_id": user_id}).sort("id", -1)]
    ords = [{
        "type": "order", "id": o["id"], "created_at": o.get("created_at"),
        "amount": o.get("amount_usdt"), "extra": "",
        "status": o.get("status"), "product_name": o.get("product_name"),
    } for o in orders.find({"user_id": user_id}).sort("id", -1)]
    merged = deps + ords
    merged.sort(key=lambda x: x.get("created_at", "") or "", reverse=True)
    return merged


def manual_credit_deposit(user_id, amount, txid="MANUAL", network="MANUAL"):
    """Admin manually credits a deposit to a user."""
    now = datetime.now().isoformat()
    did = _next_id("deposit_requests")
    deposit_requests.insert_one({
        "id": did, "user_id": user_id, "requested_usdt": amount, "expected_usdt": amount,
        "network": network, "deposit_type": "address", "pay_uid": "", "dep_note": "",
        "tx_hash": "", "status": "completed", "binance_txid": txid,
        "credited_at": now, "created_at": now, "expires_at": now, "fail_reason": "",
    })
    users.update_one({"user_id": user_id}, {"$inc": {"balance": amount}})
    return did


# ── Refund Requests (user-initiated) ─────────────────────────────────────────

def create_refund_request(user_id: int, order_id: int, reason: str) -> int:
    rid = _next_id("refund_requests")
    refund_requests.insert_one({
        "id": rid, "user_id": user_id, "order_id": order_id, "reason": reason,
        "status": "pending", "admin_note": "", "created_at": datetime.now().isoformat(),
        "resolved_at": None,
    })
    return rid


def get_refund_request(rid: int):
    return _strip_mongo_id(refund_requests.find_one({"id": rid}))


def get_user_refund_request_for_order(user_id: int, order_id: int):
    rows = list(refund_requests.find(
        {"user_id": user_id, "order_id": order_id, "status": {"$in": ["pending", "approved"]}}
    ).sort("id", -1).limit(1))
    return _strip_mongo_id(rows[0]) if rows else None


def _attach_user_order_info_batch(cursor):
    """Batched version of _attach_user_order_info — 2 queries total instead
    of 2 queries per refund request (this was the main slowdown vs SQLite)."""
    docs = list(cursor)
    if not docs:
        return []
    uname_map = _batch_usernames([d["user_id"] for d in docs])
    oid_list = [d["order_id"] for d in docs]
    order_map = {
        o["id"]: o for o in orders.find({"id": {"$in": oid_list}}, {"id": 1, "product_name": 1, "amount_usdt": 1})
    }
    out = []
    for rr in docs:
        rr = dict(rr)
        rr["username"], rr["full_name"] = uname_map.get(rr["user_id"], ("", ""))
        o = order_map.get(rr["order_id"])
        rr["product_name"] = o.get("product_name") if o else None
        rr["amount_usdt"]  = o.get("amount_usdt") if o else None
        out.append(_strip_mongo_id(rr))
    return out


def get_pending_refund_requests():
    return _attach_user_order_info_batch(refund_requests.find({"status": "pending"}).sort("id", 1))


def approve_refund_request(rid: int):
    """Mark request as approved and perform balance refund. Returns (request_dict, order_dict) or (None, None)."""
    req = refund_requests.find_one({"id": rid})
    if not req or req["status"] != "pending":
        return None, None
    order = orders.find_one({"id": req["order_id"]})
    if not order:
        return None, None
    refund_requests.update_one({"id": rid}, {"$set": {"status": "approved", "resolved_at": datetime.now().isoformat()}})
    if not order.get("refunded"):
        orders.update_one({"id": order["id"]}, {"$set": {"refunded": 1}})
        users.update_one({"user_id": order["user_id"]},
                          {"$inc": {"balance": order["amount_usdt"], "total_spent_usdt": -order["amount_usdt"]}})
        if order.get("stock_item_id"):
            stock_items.update_one({"id": order["stock_item_id"]},
                                    {"$set": {"is_sold": 0, "sold_to": None, "order_id": None, "sold_at": None}})
            refresh_stock_count(order["product_id"])
    return _strip_mongo_id(req), _strip_mongo_id(order)


def reject_refund_request(rid: int, admin_note: str = ""):
    """Mark request as rejected. Returns request_dict or None."""
    req = refund_requests.find_one({"id": rid})
    if not req or req["status"] != "pending":
        return None
    refund_requests.update_one({"id": rid}, {"$set": {
        "status": "rejected", "admin_note": admin_note, "resolved_at": datetime.now().isoformat()
    }})
    return _strip_mongo_id(req)


# ── Refund detail + chat (used by web admin panel) ────────────────────────────

def get_refund_request_full(rid: int):
    """Full refund request detail: user, order, product, credential, chat."""
    req = refund_requests.find_one({"id": rid})
    if not req:
        return None
    req = _strip_mongo_id(req)
    order = orders.find_one({"id": req["order_id"]})
    user  = users.find_one({"user_id": req["user_id"]})
    stock = stock_items.find_one({"order_id": req["order_id"]}, {"data": 1})
    req["order"]      = _strip_mongo_id(order)
    req["user"]       = _strip_mongo_id(user)
    req["credential"] = stock.get("data") if stock else ""
    return req


def get_all_refund_requests(limit=1000):
    """All refund requests (any status), most recent first, with user/order joined."""
    return _attach_user_order_info_batch(refund_requests.find().sort("id", -1).limit(limit))


def add_refund_message(refund_id: int, sender_id: int, message: str, is_admin: bool = False):
    mid = _next_id("refund_messages")
    refund_messages.insert_one({"id": mid, "refund_id": refund_id, "sender_id": sender_id,
                                 "is_admin": 1 if is_admin else 0, "message": message,
                                 "sent_at": datetime.now().isoformat()})


def get_refund_messages(refund_id: int):
    return _strip_many(refund_messages.find({"refund_id": refund_id}).sort("id", 1))


# ── Web admin panel login ──────────────────────────────────────────────────────

def get_web_admin(username: str):
    return _strip_mongo_id(web_admins.find_one({"username": username}))


def create_web_admin(username: str, password_hash: str):
    existing = web_admins.find_one({"username": username})
    if existing:
        web_admins.update_one({"username": username}, {"$set": {
            "password_hash": password_hash, "created_at": datetime.now().isoformat()
        }})
    else:
        wid = _next_id("web_admins")
        web_admins.insert_one({"id": wid, "username": username, "password_hash": password_hash,
                                "created_at": datetime.now().isoformat()})


# ── Restock requests (users who tapped "Request Restock") ──────────────────

def add_restock_request(product_id: int, user_id: int):
    if restock_requests.find_one({"product_id": product_id, "user_id": user_id, "notified": 0}):
        return
    rid = _next_id("restock_requests")
    restock_requests.insert_one({"id": rid, "product_id": product_id, "user_id": user_id,
                                  "notified": 0, "created_at": datetime.now().isoformat()})


def get_pending_restock_requesters(product_id: int):
    """User IDs who asked to be notified when this product is back in stock."""
    return restock_requests.distinct("user_id", {"product_id": product_id, "notified": 0})


def mark_restock_notified(product_id: int):
    restock_requests.update_many({"product_id": product_id, "notified": 0}, {"$set": {"notified": 1}})


# ── Renewal reminders (used by features.py) ───────────────────────────────────

def get_orders_needing_reminder(cutoff_iso: str):
    """Replaces a raw-SQL query that used to live directly in features.py."""
    return _strip_many(orders.find({
        "created_at": {"$lte": cutoff_iso}, "status": "completed",
        "reminder_sent": 0, "refunded": 0,
    }))


def mark_reminder_sent(order_id: int):
    orders.update_one({"id": order_id}, {"$set": {"reminder_sent": 1}})
