"""
webapp/app.py — Web Admin Panel for BASS TG STORE
──────────────────────────────────────────────────────────────────────────
Full-featured web mirror of the Telegram bot's admin panel. Reads/writes
the SAME SQLite database (database.py) the bot uses, so changes made here
show up instantly in the bot and vice versa.

Routes are grouped as:
  - /login, /logout              → session auth
  - /                             → single-page admin UI (templates/admin.html)
  - /ping                         → health check / self-ping target
  - /api/...                      → JSON endpoints used by static/admin.js
"""
import os
import functools
from datetime import datetime, timedelta

from flask import Flask, request, jsonify, session, redirect, url_for, render_template
from werkzeug.security import generate_password_hash, check_password_hash

import database as db
import config
from . import tg

TEMPLATE_DIR = os.path.join(os.path.dirname(__file__), "templates")
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")


def create_app():
    app = Flask(__name__, template_folder=TEMPLATE_DIR, static_folder=STATIC_DIR)
    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "change-me-please-" + str(os.urandom(8)))
    app.permanent_session_lifetime = timedelta(days=7)

    db.init_db()
    _bootstrap_admin()

    # ── auth helpers ────────────────────────────────────────────────────
    def login_required(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            if not session.get("web_admin"):
                if request.path.startswith("/api/"):
                    return jsonify({"ok": False, "error": "auth_required"}), 401
                return redirect(url_for("login"))
            return view(*args, **kwargs)
        return wrapped

    # ── pages ───────────────────────────────────────────────────────────
    @app.route("/ping")
    def ping():
        return "OK", 200

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "GET":
            if session.get("web_admin"):
                return redirect(url_for("index"))
            return render_template("login.html", error=None)
        username = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        admin = db.get_web_admin(username)
        if admin and check_password_hash(admin["password_hash"], password):
            session.permanent = True
            session["web_admin"] = username
            return redirect(url_for("index"))
        return render_template("login.html", error="Invalid username or password."), 401

    @app.route("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    @app.route("/")
    @login_required
    def index():
        return render_template("admin.html", bot_name=_bot_name(), admin_user=session.get("web_admin"))

    # ── dashboard ───────────────────────────────────────────────────────
    @app.route("/api/dashboard")
    @login_required
    def api_dashboard():
        stats = db.get_stats()
        today = db.get_today_stats()
        dep_today = db.get_today_deposit_stats() if hasattr(db, "get_today_deposit_stats") else {}
        recent_orders = db.get_today_orders(limit=8) or db.get_all_orders(limit=8)
        return jsonify({
            "ok": True,
            "stats": stats,
            "today": today,
            "deposits_today": dep_today,
            "recent_orders": [_order_out(o) for o in recent_orders[:8]],
        })

    # ── orders ──────────────────────────────────────────────────────────
    @app.route("/api/orders")
    @login_required
    def api_orders():
        scope = request.args.get("scope", "today")
        rows = db.get_today_orders() if scope == "today" else db.get_all_orders(limit=2000)
        return jsonify({"ok": True, "orders": [_order_out(o) for o in rows]})

    @app.route("/api/orders/find")
    @login_required
    def api_orders_find():
        """Look up a single order by its Order ID (order_code shown to users,
        or the internal numeric id for orders created before order_code existed)."""
        code = (request.args.get("code") or "").strip().lstrip("#")
        if not code:
            return jsonify({"ok": False, "error": "Missing code"}), 400
        order = db.get_order_by_code(code)
        if not order and code.isdigit():
            order = db.get_order(int(code))
        if not order:
            return jsonify({"ok": False, "error": "Order not found"}), 404
        # attach username + credential, matching what the list view shows
        user = db.get_user(order["user_id"])
        stock = db.get_stock_item_by_order(order["id"])
        order = dict(order)
        order["username"] = user.get("username", "") if user else ""
        order["cred_data"] = stock.get("data") if stock else ""
        return jsonify({"ok": True, "order": _order_out(order)})

    # ── deposits ────────────────────────────────────────────────────────
    @app.route("/api/deposits")
    @login_required
    def api_deposits():
        scope = request.args.get("scope", "today")
        if scope == "today":
            rows = db.get_today_deposits()
        elif scope == "pending":
            rows = db.get_pending_deposits()
        else:
            rows = db.get_all_deposits_list()
        return jsonify({"ok": True, "deposits": [_deposit_out(d) for d in rows]})

    @app.route("/api/deposits/<int:dep_id>/approve", methods=["POST"])
    @login_required
    def api_deposit_approve(dep_id):
        dep = db.get_deposit(dep_id)
        if not dep or dep["status"] != "pending":
            return jsonify({"ok": False, "error": "not_found_or_resolved"}), 400
        db.complete_deposit(dep_id, dep.get("tx_hash") or "ADMIN-WEB")
        db.update_balance(dep["user_id"], dep["requested_usdt"])
        user = db.get_user(dep["user_id"])
        tg.send_message(dep["user_id"],
            f"✅ <b>Deposit Approved</b>\n\nAmount: <b>{dep['requested_usdt']:.2f} USDT</b>\n"
            f"New balance: <b>{(user or {}).get('balance', 0):.2f} USDT</b>")
        return jsonify({"ok": True})

    @app.route("/api/deposits/<int:dep_id>/reject", methods=["POST"])
    @login_required
    def api_deposit_reject(dep_id):
        dep = db.get_deposit(dep_id)
        if not dep or dep["status"] != "pending":
            return jsonify({"ok": False, "error": "not_found_or_resolved"}), 400
        reason = (request.json or {}).get("reason", "") if request.is_json else request.form.get("reason", "")
        db.mark_deposit_failed(dep_id, reason or "Rejected by admin")
        db.set_deposit_status(dep_id, "cancelled")
        tg.send_message(dep["user_id"],
            f"❌ <b>Deposit Rejected</b>\n\nAmount: {dep['requested_usdt']:.2f} USDT\n"
            f"Reason: {reason or 'Not specified'}\n\nContact support if you believe this is a mistake.")
        return jsonify({"ok": True})

    @app.route("/api/manual-deposit", methods=["POST"])
    @login_required
    def api_manual_deposit():
        data = request.get_json(force=True)
        uid = int(data["user_id"])
        amount = float(data["amount"])
        txid = data.get("txid") or "MANUAL"
        if not db.get_user(uid):
            return jsonify({"ok": False, "error": "user_not_found"}), 404
        db.manual_credit_deposit(uid, amount, txid=txid, network="MANUAL")
        user = db.get_user(uid)
        tg.send_message(uid,
            f"💰 <b>Balance Credited</b>\n\nAmount: <b>{amount:.2f} USDT</b>\n"
            f"New balance: <b>{user['balance']:.2f} USDT</b>")
        return jsonify({"ok": True, "balance": user["balance"]})

    @app.route("/api/gift", methods=["POST"])
    @login_required
    def api_gift():
        data = request.get_json(force=True)
        uid = int(data["user_id"])
        amount = float(data["amount"])
        u = db.get_user(uid)
        if not u:
            return jsonify({"ok": False, "error": "user_not_found"}), 404
        db.update_balance(uid, amount)
        u = db.get_user(uid)
        tg.send_message(uid, f"🎁 <b>You received a gift!</b>\n\nAmount: <b>{amount:.2f} USDT</b>\n"
                              f"New balance: <b>{u['balance']:.2f} USDT</b>")
        return jsonify({"ok": True, "balance": u["balance"]})

    # ── refunds ─────────────────────────────────────────────────────────
    @app.route("/api/refunds")
    @login_required
    def api_refunds():
        status = request.args.get("status", "pending")
        rows = db.get_pending_refund_requests() if status == "pending" else db.get_all_refund_requests()
        return jsonify({"ok": True, "refunds": [_refund_list_out(r) for r in rows]})

    @app.route("/api/refunds/<int:rid>")
    @login_required
    def api_refund_detail(rid):
        req = db.get_refund_request_full(rid)
        if not req:
            return jsonify({"ok": False, "error": "not_found"}), 404
        messages = db.get_refund_messages(rid)
        return jsonify({"ok": True, "refund": _refund_full_out(req), "messages": messages})

    @app.route("/api/refunds/<int:rid>/approve", methods=["POST"])
    @login_required
    def api_refund_approve(rid):
        req, order = db.approve_refund_request(rid)
        if not req:
            return jsonify({"ok": False, "error": "not_found_or_resolved"}), 400
        tg.send_message(req["user_id"],
            f"✅ <b>Refund Approved</b>\n\nOrder #{order.get('order_code') or order['id']} — {order['product_name']}\n"
            f"Amount <b>{order['amount_usdt']:.2f} USDT</b> has been credited back to your balance.")
        return jsonify({"ok": True})

    @app.route("/api/refunds/<int:rid>/reject", methods=["POST"])
    @login_required
    def api_refund_reject(rid):
        note = (request.get_json(force=True) or {}).get("note", "Rejected by admin")
        req = db.reject_refund_request(rid, admin_note=note)
        if not req:
            return jsonify({"ok": False, "error": "not_found_or_resolved"}), 400
        order = db.get_order(req["order_id"])
        product_part = f" — {order['product_name']}" if order else ""
        display_oid = (order.get("order_code") if order else None) or req["order_id"]
        tg.send_message(req["user_id"],
            f"❌ <b>Refund Rejected</b>\n\nOrder #{display_oid}{product_part}\nNote: {note}")
        return jsonify({"ok": True})

    @app.route("/api/refunds/<int:rid>/message", methods=["POST"])
    @login_required
    def api_refund_message(rid):
        msg = (request.get_json(force=True) or {}).get("message", "").strip()
        if not msg:
            return jsonify({"ok": False, "error": "empty_message"}), 400
        req = db.get_refund_request(rid)
        if not req:
            return jsonify({"ok": False, "error": "not_found"}), 404
        admin_id = 0  # web admin has no telegram id; sender_id=0 marks "web admin"
        db.add_refund_message(rid, admin_id, msg, is_admin=True)
        tg.send_message(req["user_id"], f"🔧 <b>Admin (refund #{rid}):</b>\n{msg}")
        return jsonify({"ok": True})

    # ── categories ──────────────────────────────────────────────────────
    @app.route("/api/categories")
    @login_required
    def api_categories():
        cats = db.get_categories(active_only=False)
        for c in cats:
            c["product_count"] = len(db.get_products(category_id=c["id"], active_only=False))
        return jsonify({"ok": True, "categories": cats})

    @app.route("/api/categories", methods=["POST"])
    @login_required
    def api_add_category():
        data = request.get_json(force=True)
        cid = db.add_category(data["name"].strip(), data.get("emoji", "🛍️").strip())
        return jsonify({"ok": True, "id": cid})

    @app.route("/api/categories/<int:cid>/toggle", methods=["POST"])
    @login_required
    def api_toggle_category(cid):
        db.toggle_category(cid)
        return jsonify({"ok": True})

    @app.route("/api/categories/<int:cid>", methods=["DELETE"])
    @login_required
    def api_delete_category(cid):
        ok = db.delete_category(cid, force=request.args.get("force") == "1")
        if not ok:
            return jsonify({"ok": False, "error": "has_products"}), 400
        return jsonify({"ok": True})

    # ── products ────────────────────────────────────────────────────────
    @app.route("/api/products")
    @login_required
    def api_products():
        cat_id = request.args.get("category_id", type=int)
        prods = db.get_products(category_id=cat_id, active_only=False)
        for p in prods:
            p["stock_count"] = db.get_stock_count(p["id"])
        return jsonify({"ok": True, "products": prods})

    @app.route("/api/products", methods=["POST"])
    @login_required
    def api_add_product():
        data = request.get_json(force=True)
        pid = db.add_product(
            int(data["category_id"]), data["name"].strip(), data.get("emoji", "📦").strip(),
            data.get("description", ""), float(data["price"]), data.get("duration", ""),
            emoji_id=data.get("emoji_id", "").strip()
        )
        return jsonify({"ok": True, "id": pid})

    @app.route("/api/products/<int:pid>/emoji", methods=["POST"])
    @login_required
    def api_product_emoji(pid):
        emoji_id = (request.get_json(force=True) or {}).get("emoji_id", "").strip()
        db.update_product_emoji_id(pid, emoji_id)
        return jsonify({"ok": True})

    @app.route("/api/products/<int:pid>", methods=["DELETE"])
    @login_required
    def api_delete_product(pid):
        db.delete_product(pid)
        return jsonify({"ok": True})

    # ── stock ───────────────────────────────────────────────────────────
    @app.route("/api/stock")
    @login_required
    def api_stock_overview():
        prods = db.get_products(active_only=False)
        low = db.get_setting_int("low_stock_threshold", config.LOW_STOCK_THRESHOLD)
        out = []
        for p in prods:
            count = db.get_stock_count(p["id"])
            out.append({"id": p["id"], "name": p["name"], "emoji": p.get("emoji", ""),
                        "stock_count": count, "low": count <= low})
        return jsonify({"ok": True, "products": out, "low_stock_threshold": low})

    @app.route("/api/stock/add", methods=["POST"])
    @login_required
    def api_stock_add():
        data = request.get_json(force=True)
        pid = int(data["product_id"])
        was_empty = db.get_stock_count(pid) == 0
        items = [l for l in (data.get("data") or "").splitlines() if l.strip()]
        result = db.add_stock(pid, items)
        notified = 0
        if was_empty and result.get("added"):
            notified = _notify_restock(pid)
        return jsonify({"ok": True, "notified_users": notified, **result})

    @app.route("/api/stock/<int:pid>/items")
    @login_required
    def api_stock_items(pid):
        items = db.get_stock_items(pid, include_sold=False, limit=500)
        return jsonify({"ok": True, "items": items})

    @app.route("/api/stock/item/<int:item_id>", methods=["POST"])
    @login_required
    def api_stock_item_edit(item_id):
        new_data = (request.get_json(force=True) or {}).get("data", "").strip()
        if not new_data:
            return jsonify({"ok": False, "error": "empty_data"}), 400
        ok = db.edit_stock_item(item_id, new_data)
        if not ok:
            return jsonify({"ok": False, "error": "not_found"}), 404
        return jsonify({"ok": True})

    @app.route("/api/stock/item/<int:item_id>", methods=["DELETE"])
    @login_required
    def api_stock_item_delete(item_id):
        db.remove_stock_item(item_id)
        return jsonify({"ok": True})

    @app.route("/api/stock/<int:pid>/clear", methods=["POST"])
    @login_required
    def api_stock_clear(pid):
        db.clear_stock(pid)
        return jsonify({"ok": True})

    # ── free items ──────────────────────────────────────────────────────
    @app.route("/api/free-items")
    @login_required
    def api_free_items():
        items = db.get_free_items(active_only=False)
        for i in items:
            i["remaining"] = db.get_free_stock_count(i["id"])
        return jsonify({"ok": True, "items": items})

    @app.route("/api/free-items", methods=["POST"])
    @login_required
    def api_free_item_add():
        data = request.get_json(force=True)
        fid = db.create_free_item(data["name"].strip(), data.get("emoji", "🎁"), data.get("description", ""))
        return jsonify({"ok": True, "id": fid})

    @app.route("/api/free-items/<int:fid>/toggle", methods=["POST"])
    @login_required
    def api_free_item_toggle(fid):
        new_val = db.toggle_free_item(fid)
        return jsonify({"ok": True, "active": bool(new_val)})

    @app.route("/api/free-items/<int:fid>", methods=["DELETE"])
    @login_required
    def api_free_item_delete(fid):
        db.delete_free_item(fid)
        return jsonify({"ok": True})

    @app.route("/api/free-items/<int:fid>/stock", methods=["POST"])
    @login_required
    def api_free_item_stock(fid):
        data = request.get_json(force=True)
        items = [l for l in (data.get("data") or "").splitlines() if l.strip()]
        db.add_free_stock(fid, items)
        return jsonify({"ok": True, "added": len(items)})

    # ── users ───────────────────────────────────────────────────────────
    @app.route("/api/users")
    @login_required
    def api_users():
        q = request.args.get("q", "").strip()
        users = db.search_users(q) if q else db.get_all_users()[:200]
        return jsonify({"ok": True, "users": users})

    @app.route("/api/users/<int:uid>/ban", methods=["POST"])
    @login_required
    def api_user_ban(uid):
        ban = bool((request.get_json(force=True) or {}).get("ban", True))
        db.ban_user(uid, ban)
        return jsonify({"ok": True})

    @app.route("/api/users/<int:uid>/balance", methods=["POST"])
    @login_required
    def api_user_balance(uid):
        delta = float((request.get_json(force=True) or {}).get("delta", 0))
        if not db.get_user(uid):
            return jsonify({"ok": False, "error": "user_not_found"}), 404
        db.update_balance(uid, delta)
        u = db.get_user(uid)
        return jsonify({"ok": True, "balance": u["balance"]})

    @app.route("/api/users/<int:uid>/history")
    @login_required
    def api_user_history(uid):
        user = db.get_user(uid)
        if not user:
            return jsonify({"ok": False, "error": "user_not_found"}), 404
        history = db.get_user_full_history(uid)
        return jsonify({"ok": True, "user": user, "history": history})

    # ── tickets ─────────────────────────────────────────────────────────
    @app.route("/api/tickets")
    @login_required
    def api_tickets():
        status = request.args.get("status", "open")
        rows = db.get_open_tickets() if status == "open" else db.get_all_tickets()
        return jsonify({"ok": True, "tickets": rows})

    @app.route("/api/tickets/<int:tid>")
    @login_required
    def api_ticket_detail(tid):
        t = db.get_ticket(tid)
        if not t:
            return jsonify({"ok": False, "error": "not_found"}), 404
        msgs = db.get_ticket_messages(tid)
        return jsonify({"ok": True, "ticket": t, "messages": msgs})

    @app.route("/api/tickets/<int:tid>/reply", methods=["POST"])
    @login_required
    def api_ticket_reply(tid):
        msg = (request.get_json(force=True) or {}).get("message", "").strip()
        if not msg:
            return jsonify({"ok": False, "error": "empty_message"}), 400
        t = db.get_ticket(tid)
        if not t:
            return jsonify({"ok": False, "error": "not_found"}), 404
        db.add_ticket_message(tid, 0, msg, is_admin=True)
        tg.send_message(t["user_id"], f"🎫 <b>Support reply (ticket #{tid}):</b>\n{msg}")
        return jsonify({"ok": True})

    @app.route("/api/tickets/<int:tid>/close", methods=["POST"])
    @login_required
    def api_ticket_close(tid):
        db.close_ticket(tid)
        return jsonify({"ok": True})

    # ── coupons ─────────────────────────────────────────────────────────
    @app.route("/api/coupons")
    @login_required
    def api_coupons():
        return jsonify({"ok": True, "coupons": db.get_coupons()})

    @app.route("/api/coupons", methods=["POST"])
    @login_required
    def api_coupon_add():
        data = request.get_json(force=True)
        db.add_coupon(data["code"].strip(), int(data["discount"]), int(data["max_uses"]))
        return jsonify({"ok": True})

    @app.route("/api/coupons/<int:cid>/toggle", methods=["POST"])
    @login_required
    def api_coupon_toggle(cid):
        db.toggle_coupon(cid)
        return jsonify({"ok": True})

    @app.route("/api/coupons/<int:cid>", methods=["DELETE"])
    @login_required
    def api_coupon_delete(cid):
        db.delete_coupon(cid)
        return jsonify({"ok": True})

    # ── admins ──────────────────────────────────────────────────────────
    @app.route("/api/admins")
    @login_required
    def api_admins():
        owners = list(config.ADMIN_IDS)
        extra = db.get_extra_admins()
        return jsonify({"ok": True, "owners": owners, "extra_admins": extra})

    @app.route("/api/admins", methods=["POST"])
    @login_required
    def api_admin_add():
        uid = int((request.get_json(force=True) or {}).get("user_id"))
        db.add_extra_admin(uid)
        return jsonify({"ok": True})

    @app.route("/api/admins/<int:uid>", methods=["DELETE"])
    @login_required
    def api_admin_remove(uid):
        db.remove_extra_admin(uid)
        return jsonify({"ok": True})

    # ── broadcast ───────────────────────────────────────────────────────
    @app.route("/api/broadcast", methods=["POST"])
    @login_required
    def api_broadcast():
        msg = (request.get_json(force=True) or {}).get("message", "").strip()
        if not msg:
            return jsonify({"ok": False, "error": "empty_message"}), 400
        users = db.get_all_users()
        ids = [u["user_id"] for u in users if not u.get("is_banned")]
        sent, failed = tg.broadcast(ids, msg)
        return jsonify({"ok": True, "sent": sent, "failed": failed, "total": len(ids)})

    # ── daily history ───────────────────────────────────────────────────
    @app.route("/api/daily-history")
    @login_required
    def api_daily_history():
        date_str = request.args.get("date")
        if date_str:
            return jsonify({"ok": True, "report": db.get_daily_report(date_str)})
        reports = []
        for i in range(14):
            d = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            r = db.get_daily_report(d)
            reports.append({"date": d, "ord_count": r["ord_count"], "ord_amount": r["ord_amount"],
                            "new_users": r["new_users"]})
        return jsonify({"ok": True, "reports": reports})

    # ── settings ────────────────────────────────────────────────────────
    @app.route("/api/settings")
    @login_required
    def api_settings_get():
        channels = []
        for n in range(1, 6):
            h = db.get_setting(f"force_join_ch{n}", "").strip()
            u = db.get_setting(f"force_join_url{n}", "").strip()
            if h:
                channels.append({"n": n, "handle": h, "url": u})
        return jsonify({"ok": True, "settings": {
            "bot_name": db.get_setting("bot_name", config.BOT_NAME),
            "bot_emoji": db.get_setting("bot_emoji", "💎"),
            "trc20_address": db.get_setting("usdt_trc20_address", config.USDT_TRC20_ADDRESS),
            "bep20_address": db.get_setting("usdt_bep20_address", config.USDT_BEP20_ADDRESS),
            "binance_pay_id": db.get_setting("binance_pay_id", config.BINANCE_PAY_ID),
            "log_channel_id": db.get_setting("log_channel_id", str(config.LOG_CHANNEL_ID or "")),
            "deposit_log_channel_id": db.get_setting("deposit_log_channel_id", ""),
            "min_deposit": db.get_setting_float("min_deposit_usdt", config.MIN_DEPOSIT_USDT),
            "low_stock_threshold": db.get_setting_int("low_stock_threshold", config.LOW_STOCK_THRESHOLD),
            "maintenance": db.get_setting("maintenance") == "1",
            "referral_on": db.get_setting("referral_on") == "1",
        }, "channels": channels})

    @app.route("/api/settings", methods=["POST"])
    @login_required
    def api_settings_save():
        data = request.get_json(force=True)
        mapping = {
            "bot_name": "bot_name", "bot_emoji": "bot_emoji",
            "trc20_address": "usdt_trc20_address", "bep20_address": "usdt_bep20_address",
            "binance_pay_id": "binance_pay_id", "log_channel_id": "log_channel_id",
            "deposit_log_channel_id": "deposit_log_channel_id",
        }
        for key, setting_key in mapping.items():
            if key in data:
                db.set_setting(setting_key, str(data[key]).strip())
        if "min_deposit" in data:
            db.set_setting("min_deposit_usdt", str(float(data["min_deposit"])))
        if "low_stock_threshold" in data:
            db.set_setting("low_stock_threshold", str(int(data["low_stock_threshold"])))
        return jsonify({"ok": True})

    @app.route("/api/settings/toggle/<key>", methods=["POST"])
    @login_required
    def api_settings_toggle(key):
        if key not in ("maintenance", "referral_on"):
            return jsonify({"ok": False, "error": "invalid_key"}), 400
        db.set_setting(key, "0" if db.get_setting(key) == "1" else "1")
        return jsonify({"ok": True, "value": db.get_setting(key) == "1"})

    @app.route("/api/settings/channel", methods=["POST"])
    @login_required
    def api_settings_channel_add():
        data = request.get_json(force=True)
        handle = data["handle"].strip()
        url = data.get("url", "").strip()
        for n in range(1, 6):
            if not db.get_setting(f"force_join_ch{n}", "").strip():
                db.set_setting(f"force_join_ch{n}", handle)
                db.set_setting(f"force_join_url{n}", url)
                return jsonify({"ok": True, "slot": n})
        return jsonify({"ok": False, "error": "max_5_channels"}), 400

    @app.route("/api/settings/channel/<int:n>", methods=["DELETE"])
    @login_required
    def api_settings_channel_remove(n):
        db.set_setting(f"force_join_ch{n}", "")
        db.set_setting(f"force_join_url{n}", "")
        return jsonify({"ok": True})

    return app


# ── helpers ────────────────────────────────────────────────────────────────

def _notify_restock(product_id):
    """Sends a 'Back in Stock' style message to everyone who tapped
    'Request Restock' on this product, then clears the request queue."""
    product = db.get_product(product_id)
    if not product:
        return 0
    user_ids = db.get_pending_restock_requesters(product_id)
    if not user_ids:
        return 0
    stock = db.get_stock_count(product_id)
    text = (
        f"🔥 <b>BACK IN STOCK</b>\n"
        f"{'─' * 24}\n"
        f"{product.get('emoji','📦')} <b>{product['name']}</b>\n\n"
        f"✅ Freshly restocked — <b>{stock}</b> available now.\n"
        f"💵 Price: <b>{product['price_usdt']:.2f} USDT</b>\n\n"
        f"<i>Back by popular demand — order now before it runs out again.</i>"
    )
    sent = 0
    buttons = [[{"text": "🛒 Buy Now", "callback_data": f"product_{product_id}"}]]
    for uid in user_ids:
        if tg.send_message(uid, text, buttons=buttons):
            sent += 1
    db.mark_restock_notified(product_id)
    return sent


def _bot_name():
    return db.get_setting("bot_name", config.BOT_NAME)


def _bootstrap_admin():
    """Create the first web-panel login from env vars, if none exists yet."""
    username = os.environ.get("WEB_ADMIN_USERNAME")
    password = os.environ.get("WEB_ADMIN_PASSWORD")
    if not username or not password:
        return
    if db.get_web_admin(username):
        return
    db.create_web_admin(username, generate_password_hash(password))


def _order_out(o):
    return {
        "id": o["id"], "order_code": o.get("order_code") or f"{o['id']:04d}", "user_id": o["user_id"],
        "username": o.get("username") or "",
        "product_name": o.get("product_name"), "amount": o.get("amount_usdt"),
        "status": "refunded" if o.get("refunded") else o.get("status", "completed"),
        "created_at": o.get("created_at"),
        "credential": o.get("cred_data") or "",
    }


def _deposit_out(d):
    return {
        "id": d["id"], "user_id": d["user_id"], "username": d.get("username") or "",
        "amount": d.get("requested_usdt"), "network": d.get("network"),
        "status": d.get("status"), "tx_hash": d.get("tx_hash") or d.get("binance_txid") or "",
        "created_at": d.get("created_at"), "credited_at": d.get("credited_at"),
    }


def _refund_list_out(r):
    return {
        "id": r["id"], "user_id": r["user_id"],
        "username": r.get("username") or r.get("full_name") or str(r["user_id"]),
        "order_id": r["order_id"], "product_name": r.get("product_name"),
        "amount": r.get("amount_usdt"), "reason": r.get("reason"),
        "status": r.get("status"), "created_at": r.get("created_at"),
    }


def _refund_full_out(r):
    order = r.get("order") or {}
    user = r.get("user") or {}
    return {
        "id": r["id"], "status": r.get("status"), "reason": r.get("reason"),
        "created_at": r.get("created_at"), "resolved_at": r.get("resolved_at"),
        "admin_note": r.get("admin_note"),
        "user": {"id": user.get("user_id"), "username": user.get("username"),
                  "full_name": user.get("full_name"), "balance": user.get("balance")},
        "order": {"id": order.get("id"), "product_name": order.get("product_name"),
                   "amount": order.get("amount_usdt"), "created_at": order.get("created_at")},
        "credential": r.get("credential") or "—",
    }
