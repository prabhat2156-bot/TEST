"""
NEXUS STORE BOT — Database Layer  (MongoDB)
===========================================

Migrated from SQLite → MongoDB. Every public function keeps the SAME name,
the SAME arguments and the SAME return shape (plain dicts with the old column
names), so bot.py / otp_module.py / wa_module.py / admin modules keep working.

What is stored here (all of it, permanently, in MongoDB):
  • users .................. balance, VIP, ban, language, referral code
  • categories / products .. OTT subscription catalogue
  • stock_items ............ deliverable credentials per product
  • orders ................. FULL purchase history (incl. refunded flag)
  • deposit_requests ....... FULL deposit history (pending/completed/failed/expired)
  • free_items / free_item_stock
  • support_tickets / ticket_messages
  • referrals, coupons, coupon_uses, cart_items, settings, extra_admins
  • resellers / reseller_earnings / withdraw_requests  (sell-side history)
  • refund_requests
WhatsApp accounts live in wa_stock / wa_orders, Telegram accounts in
otp_stock / otp_orders — same database, see wa_module.py / otp_module.py.
"""

import random
import string
import logging
from datetime import datetime, timedelta

from mongo_client import (
    col, next_id, now_iso, ensure_indexes, get_db,
    strip_id, strip_ids, DuplicateKeyError,
)

# Kept for backwards compatibility with old code/log lines that referenced it.
DB_PATH = "mongodb"
logger = logging.getLogger("database")


def get_conn():
    """DEPRECATED. The SQLite connection no longer exists.

    Raises loudly instead of silently doing nothing, so any leftover raw-SQL
    call site is caught immediately instead of corrupting state at runtime.
    """
    raise RuntimeError(
        "database.get_conn() is gone — this bot now runs on MongoDB. "
        "Use the database.py helper functions or mongo_client.col('<name>')."
    )


def init_db():
    """Create indexes + default docs. Safe to call on every boot."""
    ensure_indexes()
    return True


# ── User ──────────────────────────────────────────────────────────────────────

USER_DEFAULTS = {
    "username": "",
    "full_name": "",
    "balance": 0.0,
    "referral_code": "",
    "referred_by": None,
    "total_orders": 0,
    "total_spent_usdt": 0.0,
    "is_vip": 0,
    "is_banned": 0,
    "language": "",
    "claimed_free_item": 0,
    "joined_at": "",
}


def _user_out(doc):
    if not doc:
        return None
    d = dict(USER_DEFAULTS)
    d.update(strip_id(doc))
    return d


def _gen_ref():
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))


def get_or_create_user(user_id: int, username: str = "", full_name: str = "") -> dict:
    user_id = int(user_id)
    doc = col("users").find_one({"user_id": user_id})
    if doc:
        updates = {}
        if username and username != doc.get("username"):
            updates["username"] = username
        if full_name and full_name != doc.get("full_name"):
            updates["full_name"] = full_name
        if updates:
            col("users").update_one({"user_id": user_id}, {"$set": updates})
            doc.update(updates)
        return _user_out(doc)

    for _ in range(25):
        code = _gen_ref()
        new_doc = dict(USER_DEFAULTS)
        new_doc.update({
            "user_id": user_id,
            "username": username,
            "full_name": full_name,
            "referral_code": code,
            "joined_at": now_iso(),
        })
        try:
            col("users").insert_one(new_doc)
            return _user_out(new_doc)
        except DuplicateKeyError:
            # Either the referral code collided, or another thread created the
            # same user first. Re-check the user before retrying the code.
            existing = col("users").find_one({"user_id": user_id})
            if existing:
                return _user_out(existing)
    raise RuntimeError("could not allocate a unique referral code")


def get_user(user_id: int):
    return _user_out(col("users").find_one({"user_id": int(user_id)}))


def user_exists(user_id: int) -> bool:
    return col("users").count_documents({"user_id": int(user_id)}, limit=1) > 0


def get_user_language(user_id: int) -> str:
    doc = col("users").find_one({"user_id": int(user_id)}, {"language": 1})
    return (doc or {}).get("language") or "en"


def set_user_language(user_id: int, lang: str):
    col("users").update_one({"user_id": int(user_id)}, {"$set": {"language": lang}})


def _record_wallet_transaction(user_id: int, delta: float, reason="adjustment",
                               reference_type="", reference_id=None):
    """Write an audit row after a successful wallet mutation.

    The user balance remains the source used by the bot for fast checks. This
    ledger makes deposits, purchases, refunds, gifts and withdrawals traceable
    even when the balance is later changed again.
    """
    try:
        col("wallet_transactions").insert_one({
            "id": next_id("wallet_transactions"),
            "user_id": int(user_id),
            "delta": float(delta),
            "direction": "credit" if float(delta) >= 0 else "debit",
            "reason": reason or "adjustment",
            "reference_type": reference_type or "",
            "reference_id": int(reference_id) if reference_id is not None else None,
            "created_at": now_iso(),
        })
    except Exception:
        # Never turn a successful balance mutation into a second mutation or
        # an unhandled bot error just because an audit insert was interrupted.
        logger.exception("wallet ledger insert failed for user %s", user_id)


def update_balance(user_id: int, delta: float, reason="adjustment",
                   reference_type="", reference_id=None):
    res = col("users").update_one(
        {"user_id": int(user_id)}, {"$inc": {"balance": float(delta)}})
    if res.modified_count:
        _record_wallet_transaction(user_id, delta, reason, reference_type, reference_id)
    return res.modified_count > 0


def debit_balance(user_id: int, amount: float, reason="purchase",
                  reference_type="", reference_id=None):
    """Atomically debit only when the user still has enough balance."""
    amount = round(float(amount), 6)
    if amount < 0:
        raise ValueError("debit amount cannot be negative")
    res = col("users").update_one(
        {"user_id": int(user_id), "balance": {"$gte": amount}},
        {"$inc": {"balance": -amount}},
    )
    if res.modified_count:
        _record_wallet_transaction(
            user_id, -amount, reason, reference_type, reference_id)
        return True
    return False


def get_all_users():
    return [_user_out(d) for d in col("users").find().sort("joined_at", -1)]


def ban_user(user_id: int, ban: bool):
    col("users").update_one({"user_id": int(user_id)}, {"$set": {"is_banned": 1 if ban else 0}})


def search_users(query: str):
    q = str(query).strip()
    ors = [
        {"username": {"$regex": q, "$options": "i"}},
        {"full_name": {"$regex": q, "$options": "i"}},
    ]
    if q.isdigit():
        ors.append({"user_id": int(q)})
    else:
        ors.append({"$expr": {"$regexMatch": {
            "input": {"$toString": "$user_id"}, "regex": q, "options": "i"}}})
    return [_user_out(d) for d in col("users").find({"$or": ors}).limit(20)]


# ── Categories ────────────────────────────────────────────────────────────────

def get_categories(active_only=True):
    q = {"is_active": 1} if active_only else {}
    return strip_ids(col("categories").find(q).sort("id", 1))


def add_category(name, emoji):
    cid = next_id("categories")
    col("categories").insert_one(
        {"id": cid, "name": name, "emoji": emoji or "📺", "is_active": 1})
    return cid


def toggle_category(cid):
    doc = col("categories").find_one({"id": int(cid)}, {"is_active": 1})
    if not doc:
        return None
    new_val = 0 if doc.get("is_active") else 1
    col("categories").update_one({"id": int(cid)}, {"$set": {"is_active": new_val}})
    return new_val


def delete_category(cid, force=False):
    cid = int(cid)
    if not force and col("products").count_documents({"category_id": cid}, limit=1):
        return False
    col("categories").delete_one({"id": cid})
    if force:
        # keep the catalogue consistent: drop the orphaned products + their stock
        pids = [p["id"] for p in col("products").find({"category_id": cid}, {"id": 1})]
        if pids:
            col("products").delete_many({"id": {"$in": pids}})
            col("stock_items").delete_many({"product_id": {"$in": pids}, "is_sold": 0})
            col("cart_items").delete_many({"product_id": {"$in": pids}})
    return True


def get_category(cid):
    return strip_id(col("categories").find_one({"id": int(cid)}))


# ── Products ──────────────────────────────────────────────────────────────────

def get_products(category_id=None, active_only=True):
    q = {}
    if category_id:
        q["category_id"] = int(category_id)
    if active_only:
        q["is_active"] = 1
    return strip_ids(col("products").find(q).sort("id", 1))


def get_product(pid):
    return strip_id(col("products").find_one({"id": int(pid)}))


def add_product(category_id, name, emoji, description, price_usdt, duration):
    pid = next_id("products")
    col("products").insert_one({
        "id": pid,
        "category_id": int(category_id) if category_id else None,
        "name": name,
        "emoji": emoji or "🎬",
        "description": description or "",
        "price_usdt": float(price_usdt),
        "duration": duration or "1 Month",
        "stock_count": 0,
        "is_active": 1,
        "created_at": now_iso(),
    })
    return pid


def delete_product(pid):
    pid = int(pid)
    col("products").delete_one({"id": pid})
    # unsold stock and carts pointing at a dead product would break the UI
    col("stock_items").delete_many({"product_id": pid, "is_sold": 0})
    col("cart_items").delete_many({"product_id": pid})


def toggle_product(pid):
    doc = col("products").find_one({"id": int(pid)}, {"is_active": 1})
    if not doc:
        return None
    new_val = 0 if doc.get("is_active") else 1
    col("products").update_one({"id": int(pid)}, {"$set": {"is_active": new_val}})
    return new_val


# ── Stock ─────────────────────────────────────────────────────────────────────

def get_stock_count(product_id):
    return col("stock_items").count_documents({"product_id": int(product_id), "is_sold": 0})


def refresh_stock_count(product_id):
    n = get_stock_count(product_id)
    col("products").update_one({"id": int(product_id)}, {"$set": {"stock_count": n}})
    return n


# alias kept: the old code passed a connection as the first argument
def refresh_stock_count_conn(_conn, product_id):
    return refresh_stock_count(product_id)


def add_stock(product_id, items: list):
    product_id = int(product_id)
    now = now_iso()
    existing = {d.get("data_norm", "") for d in
                col("stock_items").find({"product_id": product_id}, {"data_norm": 1})}
    to_add, duplicate_lines, docs = [], [], []
    for item in items:
        clean = (item or "").strip()
        if not clean:
            continue
        norm = clean.lower()
        if norm in existing:
            duplicate_lines.append(item)
            continue
        existing.add(norm)
        to_add.append(clean)
        docs.append({
            "id": next_id("stock_items"),
            "product_id": product_id,
            "data": clean,
            "data_norm": norm,
            "is_sold": 0,
            "sold_to": None,
            "order_id": None,
            "added_at": now,
            "sold_at": None,
        })
    if docs:
        col("stock_items").insert_many(docs)
        refresh_stock_count(product_id)
    return {"added": len(to_add), "duplicates": len(duplicate_lines),
            "duplicate_lines": duplicate_lines}


def pop_stock(product_id, qty=1):
    """Peek at available stock (non-destructive, same as before)."""
    return strip_ids(col("stock_items")
                     .find({"product_id": int(product_id), "is_sold": 0})
                     .sort("id", 1).limit(int(qty)))


def reserve_stock(product_id, user_id, qty=1):
    """ATOMIC replacement for pop_stock()+mark_stock_sold().

    The old two-step flow let two buyers grab the same credentials during a
    concurrent purchase. This claims each line with a single conditional
    update, so a line can be sold exactly once.
    """
    claimed = []
    for _ in range(int(qty)):
        doc = col("stock_items").find_one_and_update(
            {"product_id": int(product_id), "is_sold": 0},
            {"$set": {"is_sold": 1, "sold_to": int(user_id), "sold_at": now_iso()}},
            sort=[("id", 1)],
            return_document=True,
        )
        if not doc:
            break
        claimed.append(strip_id(doc))
    if claimed:
        refresh_stock_count(product_id)
    return claimed


def release_stock(item_id):
    """Put a reserved line back on the shelf (used when an order fails)."""
    doc = col("stock_items").find_one_and_update(
        {"id": int(item_id)},
        {"$set": {"is_sold": 0, "sold_to": None, "order_id": None, "sold_at": None}},
        return_document=True,
    )
    if doc:
        refresh_stock_count(doc["product_id"])
    return strip_id(doc)


def mark_stock_sold(item_id, user_id, order_id):
    doc = col("stock_items").find_one_and_update(
        {"id": int(item_id)},
        {"$set": {"is_sold": 1, "sold_to": int(user_id),
                  "order_id": int(order_id), "sold_at": now_iso()}},
        return_document=True,
    )
    if doc:
        refresh_stock_count(doc["product_id"])
    return strip_id(doc)


def get_stock_items(product_id, include_sold=False, limit=50):
    q = {"product_id": int(product_id)}
    if not include_sold:
        q["is_sold"] = 0
    return strip_ids(col("stock_items").find(q).sort("id", 1).limit(int(limit)))


def remove_stock_item(item_id):
    doc = col("stock_items").find_one_and_delete({"id": int(item_id)})
    if doc:
        refresh_stock_count(doc["product_id"])


def clear_stock(product_id):
    col("stock_items").delete_many({"product_id": int(product_id), "is_sold": 0})
    refresh_stock_count(product_id)


# ── Free Items ────────────────────────────────────────────────────────────────

def create_free_item(name, emoji, description=""):
    fid = next_id("free_items")
    col("free_items").insert_one({
        "id": fid, "name": name, "emoji": emoji or "🎁",
        "description": description or "", "is_active": 1, "created_at": now_iso(),
    })
    return fid


def get_free_items(active_only=True):
    q = {"is_active": 1} if active_only else {}
    return strip_ids(col("free_items").find(q).sort("id", -1))


def get_free_item(fid):
    return strip_id(col("free_items").find_one({"id": int(fid)}))


def toggle_free_item(fid):
    doc = col("free_items").find_one({"id": int(fid)}, {"is_active": 1})
    if not doc:
        return None
    new_val = 0 if doc.get("is_active") else 1
    col("free_items").update_one({"id": int(fid)}, {"$set": {"is_active": new_val}})
    return new_val


def delete_free_item(fid):
    fid = int(fid)
    col("free_items").delete_one({"id": fid})
    col("free_item_stock").delete_many({"free_item_id": fid})


def get_free_stock_count(fid):
    return col("free_item_stock").count_documents({"free_item_id": int(fid), "is_claimed": 0})


def add_free_stock(fid, items: list):
    fid = int(fid)
    now = now_iso()
    docs = []
    for item in items:
        clean = (item or "").strip()
        if not clean:
            continue
        docs.append({
            "id": next_id("free_item_stock"),
            "free_item_id": fid, "data": clean, "is_claimed": 0,
            "claimed_by": None, "claimed_at": None, "added_at": now,
        })
    if docs:
        col("free_item_stock").insert_many(docs)
    return len(docs)   # was len(items) — now reports what was really added


def has_user_claimed_free_item(user_id):
    doc = col("users").find_one({"user_id": int(user_id)}, {"claimed_free_item": 1})
    return bool(doc and doc.get("claimed_free_item"))


def claim_free_item(user_id, fid):
    """Atomically claim one free-item line. A user may claim only ONE, EVER.

    Both steps are conditional updates, so two taps on the button (or two
    devices) can no longer hand out two free items.
    """
    user_id, fid = int(user_id), int(fid)

    flagged = col("users").find_one_and_update(
        {"user_id": user_id, "$or": [{"claimed_free_item": 0},
                                     {"claimed_free_item": {"$exists": False}},
                                     {"claimed_free_item": None}]},
        {"$set": {"claimed_free_item": 1}},
    )
    if not flagged:
        return False, "already_claimed"

    stock = col("free_item_stock").find_one_and_update(
        {"free_item_id": fid, "is_claimed": 0},
        {"$set": {"is_claimed": 1, "claimed_by": user_id, "claimed_at": now_iso()}},
        sort=[("id", 1)],
        return_document=True,
    )
    if not stock:
        # nothing handed out → give the claim right back
        col("users").update_one({"user_id": user_id}, {"$set": {"claimed_free_item": 0}})
        return False, "out_of_stock"

    return True, stock["data"]


# ── Orders ────────────────────────────────────────────────────────────────────

def create_order(user_id, product_id, product_name, amount_usdt, stock_item_id=None,
                 coupon_code=None, is_reseller_sale=False):
    oid = next_id("orders")
    col("orders").insert_one({
        "id": oid,
        "user_id": int(user_id),
        "product_id": int(product_id),
        "product_name": product_name,
        "amount_usdt": float(amount_usdt),
        "status": "completed",
        "stock_item_id": int(stock_item_id) if stock_item_id else None,
        "coupon_code": coupon_code,
        "is_reseller_sale": 1 if is_reseller_sale else 0,
        "reminder_sent": 0,
        "refunded": 0,
        "created_at": now_iso(),
    })
    col("users").update_one(
        {"user_id": int(user_id)},
        {"$inc": {"total_orders": 1, "total_spent_usdt": float(amount_usdt)}},
    )
    if stock_item_id:
        col("stock_items").update_one({"id": int(stock_item_id)}, {"$set": {"order_id": oid}})
    refresh_stock_count(product_id)
    return oid


def get_user_orders(user_id, limit=20, offset=0):
    cur = (col("orders").find({"user_id": int(user_id)})
           .sort("id", -1).skip(int(offset)).limit(int(limit)))
    return strip_ids(cur)


def get_user_order_count(user_id):
    return col("orders").count_documents({"user_id": int(user_id)})


def get_order(oid):
    return strip_id(col("orders").find_one({"id": int(oid)}))


def _decorate_orders(orders):
    """Replaces the old LEFT JOIN on users + stock_items."""
    orders = [strip_id(o) for o in orders]
    if not orders:
        return []
    uids = {o["user_id"] for o in orders}
    oids = {o["id"] for o in orders}
    unames = {u["user_id"]: u.get("username", "")
              for u in col("users").find({"user_id": {"$in": list(uids)}},
                                         {"user_id": 1, "username": 1})}
    creds = {}
    for s in col("stock_items").find({"order_id": {"$in": list(oids)}},
                                     {"order_id": 1, "data": 1}):
        creds.setdefault(s["order_id"], s.get("data"))
    for o in orders:
        o["username"] = unames.get(o["user_id"], "")
        o["cred_data"] = creds.get(o["id"])
    return orders


def get_today_orders(limit=10000):
    today = datetime.now().strftime("%Y-%m-%d")
    cur = (col("orders").find({"created_at": {"$regex": f"^{today}"}})
           .sort("id", -1).limit(int(limit)))
    return _decorate_orders(cur)


def get_all_orders(limit=1000000):
    return _decorate_orders(col("orders").find().sort("id", -1).limit(int(limit)))


def refund_order(oid):
    order = col("orders").find_one_and_update(
        {"id": int(oid), "refunded": 0},
        {"$set": {"refunded": 1, "status": "refunded"}},
        return_document=False,          # return the pre-update doc
    )
    if not order:
        return None                     # missing OR already refunded (no double credit)
    col("users").update_one(
        {"user_id": order["user_id"]},
        {"$inc": {"balance": order["amount_usdt"],
                  "total_spent_usdt": -order["amount_usdt"]}},
    )
    _record_wallet_transaction(
        order["user_id"], order["amount_usdt"], "order_refund",
        "order", order["id"])
    if order.get("stock_item_id"):
        col("stock_items").update_one(
            {"id": order["stock_item_id"]},
            {"$set": {"is_sold": 0, "sold_to": None, "order_id": None, "sold_at": None}},
        )
        refresh_stock_count(order["product_id"])
    return strip_id(order)


# ── Deposits ──────────────────────────────────────────────────────────────────

def get_unique_expected_amount(base_amount: float, network: str = "TRC20") -> float:
    """Collision-free expected_usdt for a new deposit request.

    Only PENDING requests on the SAME network are considered — previously every
    pending request was compared, so two networks needlessly exhausted slots.
    """
    used = {round(float(r.get("expected_usdt", 0)), 3)
            for r in col("deposit_requests").find(
                {"status": "pending", "network": network}, {"expected_usdt": 1})}

    for i in range(1, 100):                      # 0.001 → 0.099
        candidate = round(base_amount + i / 1000, 3)
        if candidate not in used:
            return candidate
    for i in range(1, 10):                       # 0.10 → 0.90
        candidate = round(base_amount + i / 10, 1)
        if candidate not in used:
            return candidate
    return round(base_amount + random.randint(1, 9) / 100, 2)


def create_deposit_request(user_id, requested_usdt, expected_usdt, expires_at,
                           network="TRC20", deposit_type="address", pay_uid="", dep_note=""):
    did = next_id("deposit_requests")
    col("deposit_requests").insert_one({
        "id": did,
        "user_id": int(user_id),
        "requested_usdt": float(requested_usdt),
        "expected_usdt": float(expected_usdt),
        "network": network,
        "deposit_type": deposit_type,
        "pay_uid": pay_uid or "",
        "dep_note": dep_note or "",
        "tx_hash": "",
        "status": "pending",
        "binance_txid": None,
        "fail_reason": "",
        "credited_at": None,
        "created_at": now_iso(),
        "expires_at": expires_at,
    })
    return did


def set_deposit_tx_hash(dep_id: int, tx_hash: str):
    col("deposit_requests").update_one(
        {"id": int(dep_id)}, {"$set": {"tx_hash": (tx_hash or "").strip()}})


def get_deposit(dep_id: int):
    return strip_id(col("deposit_requests").find_one({"id": int(dep_id)}))


def get_pending_deposits():
    return strip_ids(col("deposit_requests").find({"status": "pending"}).sort("id", 1))


def get_pending_deposits_by_type(deposit_type: str):
    return strip_ids(col("deposit_requests")
                     .find({"status": "pending", "deposit_type": deposit_type})
                     .sort("id", 1))


def complete_deposit(dep_id, txid):
    """Credit-once guard: only a still-pending request can be completed."""
    set_fields = {"status": "completed", "binance_txid": txid,
                  "credited_at": now_iso()}
    # On-chain requests already have tx_hash saved before verification. For
    # admin-approved payment requests, retain a real transaction id there too,
    # but never use generic values such as ADMIN/MANUAL in the unique field.
    if txid and str(txid).upper() not in {"ADMIN", "MANUAL"}:
        set_fields["tx_hash"] = str(txid).strip()
    try:
        doc = col("deposit_requests").find_one_and_update(
            {"id": int(dep_id), "status": "pending"},
            {"$set": set_fields},
            return_document=True,
        )
    except DuplicateKeyError:
        # Another deposit won the same transaction hash race. Treat this as
        # "not completed" so the caller cannot credit the wallet.
        return None
    return strip_id(doc)


def get_completed_deposit_by_txhash(tx_hash: str):
    """A COMPLETED deposit (any user) already using this tx hash, or None."""
    if not tx_hash:
        return None
    escaped = f"^{_escape_regex(tx_hash.strip())}$"
    doc = col("deposit_requests").find_one({
        "status": "completed",
        "$or": [
            {"tx_hash": {"$regex": escaped, "$options": "i"}},
            {"binance_txid": {"$regex": escaped, "$options": "i"}},
        ],
    })
    return strip_id(doc)


def _escape_regex(text: str) -> str:
    import re
    return re.escape(text)


def mark_deposit_failed(dep_id: int, reason: str):
    col("deposit_requests").update_one(
        {"id": int(dep_id), "status": "pending"},
        {"$set": {"status": "failed", "fail_reason": reason}})


def expire_old_deposits():
    now = now_iso()
    q = {"status": "pending", "expires_at": {"$lt": now}}
    rows = strip_ids(col("deposit_requests").find(q))
    if rows:
        col("deposit_requests").update_many(q, {"$set": {"status": "expired"}})
    return rows


def get_user_deposits(user_id, limit=10):
    return strip_ids(col("deposit_requests").find({"user_id": int(user_id)})
                     .sort("id", -1).limit(int(limit)))


def _decorate_users(rows, fields=("username", "full_name")):
    rows = [strip_id(r) for r in rows]
    if not rows:
        return []
    uids = list({r["user_id"] for r in rows})
    proj = {"user_id": 1}
    for f in fields:
        proj[f] = 1
    umap = {u["user_id"]: u for u in col("users").find({"user_id": {"$in": uids}}, proj)}
    for r in rows:
        u = umap.get(r["user_id"], {})
        for f in fields:
            r[f] = u.get(f, "")
    return rows


def get_today_deposits():
    today = datetime.now().strftime("%Y-%m-%d")
    cur = col("deposit_requests").find({
        "status": "completed", "credited_at": {"$regex": f"^{today}"}}).sort("id", -1)
    return _decorate_users(cur, ("username",))


# ── Settings ──────────────────────────────────────────────────────────────────

def get_setting(key, default=""):
    doc = col("settings").find_one({"key": key})
    return doc["value"] if doc and doc.get("value") is not None else default


def get_setting_float(key, default=0.0):
    v = get_setting(key, "")
    try:
        return float(v) if v not in ("", None) else default
    except (TypeError, ValueError):
        return default


def get_setting_int(key, default=0):
    v = get_setting(key, "")
    try:
        return int(float(v)) if v not in ("", None) else default
    except (TypeError, ValueError):
        return default


def set_setting(key, value):
    col("settings").update_one({"key": key}, {"$set": {"value": str(value)}}, upsert=True)


# ── Stats ─────────────────────────────────────────────────────────────────────

def _sum(collection, match, field):
    cur = col(collection).aggregate([
        {"$match": match},
        {"$group": {"_id": None, "s": {"$sum": f"${field}"}}},
    ])
    for row in cur:
        return float(row.get("s") or 0)
    return 0.0


def get_stats():
    users = col("users").count_documents({})
    orders = col("orders").count_documents({"refunded": 0})
    revenue = _sum("orders", {"refunded": 0}, "amount_usdt")
    total_dep = _sum("deposit_requests", {"status": "completed"}, "requested_usdt")
    return {"users": users, "orders": orders, "revenue": revenue, "total_dep": total_dep}


def get_today_stats():
    today = datetime.now().strftime("%Y-%m-%d")
    day = {"$regex": f"^{today}"}
    ord_match = {"created_at": day, "refunded": 0}
    # only completed deposits count as money in — the old query summed every
    # row that merely had a credited_at value
    dep_match = {"credited_at": day, "status": "completed"}
    return {
        "ord_count": col("orders").count_documents(ord_match),
        "ord_amount": _sum("orders", ord_match, "amount_usdt"),
        "dep_count": col("deposit_requests").count_documents(dep_match),
        "dep_amount": _sum("deposit_requests", dep_match, "requested_usdt"),
    }


def get_daily_report(date_str: str):
    day = {"$regex": f"^{date_str}"}
    ord_match = {"created_at": day, "refunded": 0}
    dep_match = {"status": "completed", "credited_at": day}

    orders = _decorate_users(col("orders").find(ord_match).sort("id", -1), ("username",))
    deposits = _decorate_users(col("deposit_requests").find(dep_match).sort("id", -1),
                               ("username",))
    return {
        "date": date_str,
        "ord_count": len(orders),
        "ord_amount": _sum("orders", ord_match, "amount_usdt"),
        "dep_count": len(deposits),
        "dep_amount": _sum("deposit_requests", dep_match, "requested_usdt"),
        "dep_failed": col("deposit_requests").count_documents(
            {"created_at": day, "status": {"$in": ["expired", "cancelled", "failed"]}}),
        "new_users": col("users").count_documents({"joined_at": day}),
        "total_balance_now": _sum("users", {}, "balance"),
        "total_users_now": col("users").count_documents({}),
        "orders": orders,
        "deposits": deposits,
    }


# ── Admins ────────────────────────────────────────────────────────────────────

def get_extra_admins():
    return [d["user_id"] for d in col("extra_admins").find({}, {"user_id": 1})]


def add_extra_admin(user_id):
    col("extra_admins").update_one({"user_id": int(user_id)},
                                   {"$set": {"user_id": int(user_id)}}, upsert=True)


def remove_extra_admin(user_id):
    col("extra_admins").delete_one({"user_id": int(user_id)})


# ── Support Tickets ───────────────────────────────────────────────────────────

def create_ticket(user_id, subject):
    tid = next_id("support_tickets")
    now = now_iso()
    col("support_tickets").insert_one({
        "id": tid, "user_id": int(user_id), "subject": subject,
        "status": "open", "created_at": now, "updated_at": now,
    })
    return tid


def get_ticket(tid):
    return strip_id(col("support_tickets").find_one({"id": int(tid)}))


def get_user_tickets(user_id):
    return strip_ids(col("support_tickets").find({"user_id": int(user_id)}).sort("id", -1))


def get_open_tickets():
    cur = col("support_tickets").find({"status": "open"}).sort("id", 1)
    return _decorate_users(cur)


def add_ticket_message(ticket_id, sender_id, message, is_admin=False):
    now = now_iso()
    col("ticket_messages").insert_one({
        "id": next_id("ticket_messages"),
        "ticket_id": int(ticket_id), "sender_id": int(sender_id),
        "is_admin": 1 if is_admin else 0, "message": message, "sent_at": now,
    })
    col("support_tickets").update_one({"id": int(ticket_id)},
                                      {"$set": {"updated_at": now}})


def get_ticket_messages(ticket_id):
    return strip_ids(col("ticket_messages").find({"ticket_id": int(ticket_id)}).sort("id", 1))


def close_ticket(tid):
    col("support_tickets").update_one(
        {"id": int(tid)}, {"$set": {"status": "closed", "updated_at": now_iso()}})


def get_all_tickets():
    return _decorate_users(col("support_tickets").find().sort("id", -1))


# ── Referrals ─────────────────────────────────────────────────────────────────

def get_user_by_referral_code(code):
    return _user_out(col("users").find_one({"referral_code": code}))


def set_referred_by(user_id, referrer_id):
    user_id, referrer_id = int(user_id), int(referrer_id)
    if user_id == referrer_id:
        return False                      # self-referral guard (was missing)
    res = col("users").update_one(
        {"user_id": user_id, "$or": [{"referred_by": None},
                                     {"referred_by": {"$exists": False}}]},
        {"$set": {"referred_by": referrer_id}},
    )
    if res.modified_count == 0:
        return False                      # already referred by someone else
    try:
        col("referrals").insert_one({
            "id": next_id("referrals"), "referrer_id": referrer_id,
            "referred_id": user_id, "bonus_paid": 0.0, "created_at": now_iso(),
        })
    except DuplicateKeyError:
        pass
    return True


def get_referral_count(user_id):
    return col("referrals").count_documents({"referrer_id": int(user_id)})


def credit_referral_deposit_bonus(user_id, amount):
    """Flat REFERRAL_BONUS_USDT to the referrer on a qualifying deposit.
    Returns (referrer_id, bonus) or (None, 0)."""
    import config
    doc = col("users").find_one({"user_id": int(user_id)}, {"referred_by": 1})
    if not doc or not doc.get("referred_by"):
        return None, 0
    ref_id = int(doc["referred_by"])
    bonus = round(float(config.REFERRAL_BONUS_USDT), 6) if amount >= 1 else 0
    if bonus > 0:
        update_balance(
            ref_id, bonus, "referral_bonus", "deposit", int(user_id))
        col("referrals").update_one(
            {"referrer_id": ref_id, "referred_id": int(user_id)},
            {"$inc": {"bonus_paid": bonus}})
    return ref_id, bonus


def promote_vip(user_id):
    import config
    if get_referral_count(user_id) < config.VIP_REFERRALS_NEEDED:
        return False
    if col("users").count_documents({"is_vip": 1}) >= config.MAX_VIP_MEMBERS:
        return False
    res = col("users").update_one({"user_id": int(user_id), "is_vip": 0},
                                  {"$set": {"is_vip": 1}})
    return res.modified_count > 0


# ── Coupons ───────────────────────────────────────────────────────────────────

def get_coupons():
    return strip_ids(col("coupons").find().sort("id", -1))


def add_coupon(code, discount, max_uses):
    code = str(code).upper().strip()
    if col("coupons").count_documents({"code": code}, limit=1):
        return None
    cid = next_id("coupons")
    col("coupons").insert_one({
        "id": cid, "code": code, "discount": int(discount),
        "max_uses": int(max_uses), "used_count": 0, "is_active": 1,
        "created_at": now_iso(),
    })
    return cid


def toggle_coupon(cid):
    doc = col("coupons").find_one({"id": int(cid)}, {"is_active": 1})
    if not doc:
        return None
    new_val = 0 if doc.get("is_active") else 1
    col("coupons").update_one({"id": int(cid)}, {"$set": {"is_active": new_val}})
    return new_val


def delete_coupon(cid):
    col("coupons").delete_one({"id": int(cid)})
    col("coupon_uses").delete_many({"coupon_id": int(cid)})


def validate_coupon(code, user_id):
    c = col("coupons").find_one({"code": str(code).upper().strip(), "is_active": 1})
    if not c:
        return None, "not_found"
    c = strip_id(c)
    if c["used_count"] >= c["max_uses"]:
        return None, "exhausted"
    if col("coupon_uses").count_documents(
            {"coupon_id": c["id"], "user_id": int(user_id)}, limit=1):
        return None, "already_used"
    return c, "ok"


def apply_coupon(coupon_id, user_id):
    """Atomic: a coupon cannot go past max_uses, and one user cannot use it twice."""
    coupon_id, user_id = int(coupon_id), int(user_id)
    if col("coupon_uses").count_documents(
            {"coupon_id": coupon_id, "user_id": user_id}, limit=1):
        return False
    doc = col("coupons").find_one_and_update(
        {"id": coupon_id, "is_active": 1, "$expr": {"$lt": ["$used_count", "$max_uses"]}},
        {"$inc": {"used_count": 1}},
        return_document=True,
    )
    if not doc:
        return False
    try:
        col("coupon_uses").insert_one({
            "id": next_id("coupon_uses"), "coupon_id": coupon_id,
            "user_id": user_id, "used_at": now_iso(),
        })
    except DuplicateKeyError:
        # Another request claimed this coupon for the same user between the
        # initial check and the insert. Put the usage counter back.
        col("coupons").update_one(
            {"id": coupon_id, "used_count": {"$gt": 0}},
            {"$inc": {"used_count": -1}})
        return False
    return True


def rollback_coupon(coupon_id, user_id):
    """Undo a coupon claim when checkout cannot be completed."""
    deleted = col("coupon_uses").delete_one(
        {"coupon_id": int(coupon_id), "user_id": int(user_id)})
    if deleted.deleted_count:
        col("coupons").update_one(
            {"id": int(coupon_id), "used_count": {"$gt": 0}},
            {"$inc": {"used_count": -1}})
        return True
    return False


# ── Cart ──────────────────────────────────────────────────────────────────────

def add_to_cart(user_id, product_id, qty=1):
    col("cart_items").update_one(
        {"user_id": int(user_id), "product_id": int(product_id)},
        {"$inc": {"quantity": int(qty)},
         "$setOnInsert": {"id": next_id("cart_items"), "added_at": now_iso()}},
        upsert=True,
    )


def get_cart(user_id):
    items = list(col("cart_items").find({"user_id": int(user_id)}).sort("id", 1))
    if not items:
        return []
    pmap = {p["id"]: p for p in col("products").find(
        {"id": {"$in": [i["product_id"] for i in items]}})}
    out = []
    for i in items:
        p = pmap.get(i["product_id"])
        if not p:
            # product deleted → drop the dead cart line instead of crashing
            col("cart_items").delete_one({"id": i["id"]})
            continue
        out.append({
            "cart_id": i["id"], "product_id": i["product_id"],
            "quantity": i.get("quantity", 1), "name": p.get("name"),
            "emoji": p.get("emoji"), "price_usdt": p.get("price_usdt"),
            "category_id": p.get("category_id"), "is_active": p.get("is_active"),
        })
    return out


def get_cart_item(user_id, product_id):
    return strip_id(col("cart_items").find_one(
        {"user_id": int(user_id), "product_id": int(product_id)}))


def set_cart_qty(user_id, product_id, qty):
    q = {"user_id": int(user_id), "product_id": int(product_id)}
    if int(qty) <= 0:
        col("cart_items").delete_one(q)
    else:
        col("cart_items").update_one(q, {"$set": {"quantity": int(qty)}})


def remove_cart_item(user_id, product_id):
    col("cart_items").delete_one({"user_id": int(user_id), "product_id": int(product_id)})


def clear_cart(user_id):
    col("cart_items").delete_many({"user_id": int(user_id)})


# ── Resellers ─────────────────────────────────────────────────────────────────

def is_reseller(user_id):
    doc = col("resellers").find_one({"user_id": int(user_id)}, {"approved": 1})
    return bool(doc and doc.get("approved") == 1)     # always a real bool now


def add_reseller(user_id):
    col("resellers").update_one(
        {"user_id": int(user_id)},
        {"$setOnInsert": {"approved": 0, "created_at": now_iso()}},
        upsert=True,
    )


def approve_reseller(user_id):
    col("resellers").update_one({"user_id": int(user_id)}, {"$set": {"approved": 1}})


def revoke_reseller(user_id):
    col("resellers").delete_one({"user_id": int(user_id)})


def get_resellers():
    return _decorate_users(col("resellers").find({"approved": 1}))


def add_reseller_earning(reseller_id, order_id, gross_margin, owner_cut, reseller_cut):
    import config
    delay = get_setting_int("reseller_credit_delay_hours", config.RESELLER_CREDIT_DELAY_HOURS)
    now = datetime.now()
    col("reseller_earnings").insert_one({
        "id": next_id("reseller_earnings"),
        "reseller_id": int(reseller_id), "order_id": int(order_id),
        "gross_margin": float(gross_margin), "owner_cut": float(owner_cut),
        "reseller_cut": float(reseller_cut), "status": "pending",
        "created_at": now.isoformat(),
        "available_at": (now + timedelta(hours=delay)).isoformat(),
    })
    return reseller_cut, owner_cut


def mature_reseller_earnings():
    now = now_iso()
    q = {"status": "pending", "available_at": {"$lte": now}}
    rows = strip_ids(col("reseller_earnings").find(q))
    if rows:
        col("reseller_earnings").update_many(q, {"$set": {"status": "available"}})
    return rows


def get_reseller_balance_summary(reseller_id):
    rid = int(reseller_id)
    return {
        "pending": _sum("reseller_earnings", {"reseller_id": rid, "status": "pending"}, "reseller_cut"),
        "available": _sum("reseller_earnings", {"reseller_id": rid, "status": "available"}, "reseller_cut"),
        "withdrawn": _sum("reseller_earnings", {"reseller_id": rid, "status": "withdrawn"}, "reseller_cut"),
    }


def create_withdraw_request(user_id, amount):
    wid = next_id("withdraw_requests")
    col("withdraw_requests").insert_one({
        "id": wid, "user_id": int(user_id), "amount": float(amount),
        "status": "pending", "requested_at": now_iso(), "processed_at": None,
    })
    return wid


def get_pending_withdraw_requests():
    return strip_ids(col("withdraw_requests").find({"status": "pending"}).sort("id", 1))


def get_withdraw_request(wid):
    return strip_id(col("withdraw_requests").find_one({"id": int(wid)}))


def mark_withdraw_earnings_consumed(reseller_id, amount):
    remaining = float(amount)
    for r in col("reseller_earnings").find(
            {"reseller_id": int(reseller_id), "status": "available"}).sort("id", 1):
        if remaining <= 0:
            break
        col("reseller_earnings").update_one({"id": r["id"]},
                                            {"$set": {"status": "withdrawn"}})
        remaining -= float(r.get("reseller_cut") or 0)


def process_withdraw_request(wid, approve):
    """Only a pending request can be processed — protects against double payout."""
    req = col("withdraw_requests").find_one_and_update(
        {"id": int(wid), "status": "pending"},
        {"$set": {"status": "paid" if approve else "rejected",
                  "processed_at": now_iso()}},
        return_document=False,
    )
    if not req:
        return None
    if approve:
        mark_withdraw_earnings_consumed(req["user_id"], req["amount"])
    return strip_id(req)


# ── Advanced Admin Queries ─────────────────────────────────────────────────────

def get_today_deposits_all():
    today = datetime.now().strftime("%Y-%m-%d")
    cur = col("deposit_requests").find(
        {"created_at": {"$regex": f"^{today}"}}).sort("id", -1)
    return _decorate_users(cur)


def get_today_deposit_stats():
    today = datetime.now().strftime("%Y-%m-%d")
    day = {"$regex": f"^{today}"}
    ok_match = {"created_at": day, "status": "completed"}
    return {
        "success_count": col("deposit_requests").count_documents(ok_match),
        "success_amt": _sum("deposit_requests", ok_match, "requested_usdt"),
        "failed_count": col("deposit_requests").count_documents(
            {"created_at": day, "status": {"$in": ["expired", "cancelled", "failed"]}}),
        "pending_count": col("deposit_requests").count_documents(
            {"created_at": day, "status": "pending"}),
    }


def get_all_deposits_list(limit=50000):
    cur = col("deposit_requests").find().sort("id", -1).limit(int(limit))
    return _decorate_users(cur)


def get_all_time_deposit_stats():
    return {
        "success_count": col("deposit_requests").count_documents({"status": "completed"}),
        "success_amt": _sum("deposit_requests", {"status": "completed"}, "requested_usdt"),
        "failed_count": col("deposit_requests").count_documents(
            {"status": {"$in": ["expired", "cancelled", "failed"]}}),
        "pending_count": col("deposit_requests").count_documents({"status": "pending"}),
    }


def get_user_full_history(user_id):
    """Every deposit + completed store order + unresolved account order."""
    uid = int(user_id)
    merged = []
    for d in col("deposit_requests").find({"user_id": uid}).sort("id", -1):
        merged.append({"type": "deposit", "id": d["id"], "created_at": d.get("created_at"),
                       "amount": d.get("requested_usdt"), "extra": d.get("network"),
                       "status": d.get("status"), "product_name": ""})
    for o in col("orders").find({"user_id": uid}).sort("id", -1):
        merged.append({"type": "order", "id": o["id"], "created_at": o.get("created_at"),
                       "amount": o.get("amount_usdt"), "extra": "",
                       "status": o.get("status"), "product_name": o.get("product_name")})
    # OTP/WhatsApp requests are kept in their dedicated collections. Include
    # only pending/refunded/cancelled rows here because delivered/completed
    # rows also have a canonical entry in orders and must not be double-counted.
    external_statuses = {"pending", "refunded", "cancelled"}
    for o in col("otp_orders").find({
            "user_id": uid, "status": {"$in": list(external_statuses)}}):
        merged.append({
            "type": "order", "id": o["id"], "created_at": o.get("created_at"),
            "amount": o.get("amount_usdt", 0), "extra": "Telegram",
            "status": o.get("status"),
            "product_name": f"OTP {o.get('country', '')} {o.get('year', '')}".strip(),
        })
    for o in col("wa_orders").find({
            "user_id": uid, "status": {"$in": list(external_statuses)}}):
        merged.append({
            "type": "order", "id": o["id"], "created_at": o.get("created_at"),
            "amount": o.get("amount_usdt", 0), "extra": "WhatsApp",
            "status": o.get("status"),
            "product_name": f"WhatsApp {o.get('country', '')}".strip(),
        })
    merged.sort(key=lambda x: x.get("created_at") or "", reverse=True)
    return merged


def manual_credit_deposit(user_id, amount, txid="MANUAL", network="MANUAL"):
    now = now_iso()
    did = next_id("deposit_requests")
    col("deposit_requests").insert_one({
        "id": did, "user_id": int(user_id),
        "requested_usdt": float(amount), "expected_usdt": float(amount),
        "network": network, "deposit_type": "manual", "pay_uid": "",
        "dep_note": "manual credit", "tx_hash": "", "status": "completed",
        "binance_txid": txid, "fail_reason": "", "credited_at": now,
        "created_at": now, "expires_at": now,
    })
    update_balance(
        user_id, amount, "manual_deposit", "deposit", did)
    return did


# ── Refund Requests (user-initiated) ─────────────────────────────────────────

def create_refund_request(user_id: int, order_id: int, reason: str) -> int:
    rid = next_id("refund_requests")
    col("refund_requests").insert_one({
        "id": rid, "user_id": int(user_id), "order_id": int(order_id),
        "reason": reason or "", "status": "pending", "admin_note": "",
        "created_at": now_iso(), "resolved_at": None,
    })
    return rid


def get_refund_request(rid: int):
    return strip_id(col("refund_requests").find_one({"id": int(rid)}))


def get_user_refund_request_for_order(user_id: int, order_id: int):
    doc = col("refund_requests").find_one(
        {"user_id": int(user_id), "order_id": int(order_id),
         "status": {"$in": ["pending", "approved"]}},
        sort=[("id", -1)])
    return strip_id(doc)


def get_pending_refund_requests():
    rows = _decorate_users(col("refund_requests").find({"status": "pending"}).sort("id", 1))
    if not rows:
        return []
    omap = {o["id"]: o for o in col("orders").find(
        {"id": {"$in": [r["order_id"] for r in rows]}},
        {"id": 1, "product_name": 1, "amount_usdt": 1})}
    for r in rows:
        o = omap.get(r["order_id"], {})
        r["product_name"] = o.get("product_name")
        r["amount_usdt"] = o.get("amount_usdt")
    return rows


def approve_refund_request(rid: int):
    """Approve + refund, exactly once. Returns (request, order) or (None, None)."""
    req = col("refund_requests").find_one_and_update(
        {"id": int(rid), "status": "pending"},
        {"$set": {"status": "approved", "resolved_at": now_iso()}},
        return_document=False,
    )
    if not req:
        return None, None
    order = col("orders").find_one({"id": req["order_id"]})
    if not order:
        # roll the request back so an admin can retry instead of losing it
        col("refund_requests").update_one(
            {"id": int(rid)}, {"$set": {"status": "pending", "resolved_at": None}})
        return None, None

    refunded = refund_order(order["id"])
    return strip_id(req), (refunded or strip_id(order))


def reject_refund_request(rid: int, admin_note: str = ""):
    req = col("refund_requests").find_one_and_update(
        {"id": int(rid), "status": "pending"},
        {"$set": {"status": "rejected", "admin_note": admin_note or "",
                  "resolved_at": now_iso()}},
        return_document=False,
    )
    return strip_id(req)
