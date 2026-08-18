"""
webpanel/app.py — FULL Web Admin Panel
======================================

Browser control panel for the Telegram store bot. Everything the in-bot
admin panel can do is available here:

  • Dashboard    — today/all-time deposits, buys, users, stock, quick links
  • Users        — search, ban/unban, balance +/-, gift, manual deposit, history
  • Deposits     — today / all-time list, pending approve or mark failed
  • Orders       — today / all-time sales, refund, TXT export
  • Catalog      — categories, products, stock add/remove/clear
  • Free items   — free/OTT giveaways + stock
  • Coupons      — create, toggle, delete
  • Tickets      — read, reply (sent to the user on Telegram), close
  • Finance      — refund requests, resellers, withdraw requests
  • Broadcast    — message every bot user from the browser
  • Daily report — any date: deposits, orders, new users, wallet snapshot
  • TG accounts  — OTP stock, add account with OTP + 2FA login, pricing
  • WhatsApp     — WA stock add/delete/pricing, sales switch
  • Settings     — quick toggles + every raw key/value setting

Login: ADMIN_USER / ADMIN_PASS environment variables.
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta
from functools import wraps
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from flask import (Flask, Response, flash, jsonify, redirect, render_template,
                   request, session, url_for)

import database as db
import otp_module as om
from mongo_client import col, next_id, now_iso, strip_ids
from webpanel import tg_login

logger = logging.getLogger("webpanel")

PANEL_USER = os.environ.get("ADMIN_USER", "admin")
PANEL_PASS = os.environ.get("ADMIN_PASS", "admin123")

# ── broadcast job state (in-memory, single worker) ───────────────────────────
BROADCAST = {"running": False, "sent": 0, "failed": 0, "total": 0,
             "started": None, "finished": None, "last_text": ""}


def _tg_api(method, payload):
    """Call the Telegram Bot API with the store-bot token (no extra deps)."""
    token = os.environ.get("BOT_TOKEN", "")
    if not token:
        raise RuntimeError("BOT_TOKEN is not set")
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = urlencode(payload).encode()
    req = Request(url, data=data, headers={"User-Agent": "webpanel/1.0"})
    with urlopen(req, timeout=25) as resp:
        return json.loads(resp.read().decode())


def send_tg_message(user_id, text):
    return _tg_api("sendMessage", {"chat_id": user_id, "text": text,
                                   "parse_mode": "HTML",
                                   "disable_web_page_preview": "true"})


def _broadcast_worker(text, user_ids):
    BROADCAST.update(running=True, sent=0, failed=0, total=len(user_ids),
                     started=datetime.now().strftime("%d %b %Y %H:%M:%S"),
                     finished=None, last_text=text)
    for uid in user_ids:
        try:
            send_tg_message(uid, text)
            BROADCAST["sent"] += 1
        except Exception:  # noqa: BLE001
            BROADCAST["failed"] += 1
        time.sleep(0.05)
    BROADCAST["running"] = False
    BROADCAST["finished"] = datetime.now().strftime("%d %b %Y %H:%M:%S")


def create_app():
    app = Flask(__name__, template_folder="templates", static_folder=None)
    app.secret_key = os.environ.get(
        "PANEL_SECRET", os.environ.get("BOT_TOKEN", "change-me-please"))
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    # ── health (Render) ──────────────────────────────────────────────────
    @app.get("/healthz")
    @app.get("/health")
    def healthz():
        return jsonify(ok=True, service="telegram-bot")

    # ── auth ─────────────────────────────────────────────────────────────
    def login_required(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            if not session.get("admin"):
                return redirect(url_for("login", next=request.path))
            return fn(*a, **kw)
        return wrapper

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if request.method == "POST":
            user = request.form.get("username", "").strip()
            pwd = request.form.get("password", "")
            if user == PANEL_USER and pwd == PANEL_PASS:
                session["admin"] = user
                session.permanent = True
                return redirect(request.args.get("next") or url_for("dashboard"))
            flash("Invalid ID or password.", "error")
        return render_template("login.html")

    @app.get("/logout")
    def logout():
        session.clear()
        return redirect(url_for("login"))

    def back(default="dashboard"):
        return redirect(request.referrer or url_for(default))

    def f_int(name, default=0):
        try:
            return int(float(request.form.get(name) or default))
        except (TypeError, ValueError):
            return default

    def f_float(name, default=0.0):
        try:
            return float(request.form.get(name) or default)
        except (TypeError, ValueError):
            return default

    # ── dashboard ────────────────────────────────────────────────────────
    @app.get("/")
    @login_required
    def dashboard():
        stats = db.get_stats()
        today = db.get_today_stats()
        dep_today = db.get_today_deposit_stats()
        dep_all = db.get_all_time_deposit_stats()
        otp_stock = col("otp_stock").count_documents({"available": 1})
        wa_stock = col("wa_stock").count_documents({"available": 1})
        otp_sold = col("otp_orders").count_documents({"status": "delivered"})
        wa_sold = col("wa_orders").count_documents({"status": "delivered"})
        recent_orders = db.get_today_orders(limit=10)
        recent_deps = db.get_today_deposits_all()[:10]
        try:
            open_tickets = len(db.get_open_tickets())
        except Exception:  # noqa: BLE001
            open_tickets = 0
        try:
            pending_refunds = len(db.get_pending_refund_requests())
        except Exception:  # noqa: BLE001
            pending_refunds = 0
        return render_template("dashboard.html", stats=stats, today=today,
                               dep_today=dep_today, dep_all=dep_all,
                               otp_stock=otp_stock, wa_stock=wa_stock,
                               otp_sold=otp_sold, wa_sold=wa_sold,
                               recent_orders=recent_orders,
                               recent_deps=recent_deps,
                               open_tickets=open_tickets,
                               pending_refunds=pending_refunds,
                               date=datetime.now().strftime("%d %b %Y"))

    # ── users ────────────────────────────────────────────────────────────
    @app.get("/users")
    @login_required
    def users():
        q = request.args.get("q", "").strip()
        rows = db.search_users(q) if q else db.get_all_users()[:300]
        return render_template("users.html", users=rows, q=q)

    @app.post("/users/action")
    @login_required
    def users_action():
        uid = f_int("user_id")
        action = request.form.get("action")
        note = (request.form.get("note") or "").strip()
        try:
            if action == "ban":
                db.ban_user(uid, True)
            elif action == "unban":
                db.ban_user(uid, False)
            elif action == "balance":
                db.update_balance(uid, f_float("amount"), reason="admin_adjustment")
            elif action == "credit":
                db.manual_credit_deposit(uid, f_float("amount"))
            elif action == "gift":
                amount = f_float("amount")
                db.update_balance(uid, amount, reason="admin_gift")
                try:
                    send_tg_message(uid, f"🎁 <b>Gift received!</b>\n\n"
                                         f"+{amount:.2f} USDT has been added to your wallet."
                                         + (f"\n\n📝 {note}" if note else ""))
                except Exception:  # noqa: BLE001
                    pass
            elif action == "message":
                send_tg_message(uid, note)
            flash("Done.", "ok")
        except Exception as exc:  # noqa: BLE001
            flash(f"Failed: {exc}", "error")
        return back("users")

    @app.get("/users/<int:uid>")
    @login_required
    def user_detail(uid):
        user = db.get_user(uid)
        if not user:
            flash("User not found.", "error")
            return redirect(url_for("users"))
        try:
            tickets = db.get_user_tickets(uid)
        except Exception:  # noqa: BLE001
            tickets = []
        return render_template("user_detail.html", user=user, tickets=tickets,
                               history=db.get_user_full_history(uid))

    # ── deposits ─────────────────────────────────────────────────────────
    @app.get("/deposits")
    @login_required
    def deposits():
        scope = request.args.get("scope", "today")
        if scope == "all":
            rows, stats = db.get_all_deposits_list(2000), db.get_all_time_deposit_stats()
        elif scope == "pending":
            rows, stats = db.get_pending_deposits(), db.get_today_deposit_stats()
        else:
            rows, stats = db.get_today_deposits_all(), db.get_today_deposit_stats()
        return render_template("deposits.html", rows=rows, stats=stats, scope=scope)

    @app.post("/deposits/action")
    @login_required
    def deposits_action():
        action = request.form.get("action")
        dep_id = f_int("dep_id")
        try:
            if action == "approve":
                db.complete_deposit(dep_id, request.form.get("txid") or "MANUAL")
                flash("Deposit credited.", "ok")
            elif action == "fail":
                db.mark_deposit_failed(dep_id, request.form.get("reason") or "admin")
                flash("Deposit marked failed.", "ok")
        except Exception as exc:  # noqa: BLE001
            flash(f"Failed: {exc}", "error")
        return back("deposits")

    @app.get("/export/<kind>")
    @login_required
    def export_txt(kind):
        lines = []
        if kind == "deposits":
            for d in db.get_all_deposits_list(100000):
                lines.append(f"#{d.get('id')} | {d.get('created_at','')} | "
                             f"{d.get('username') or d.get('user_id')} | "
                             f"{float(d.get('requested_usdt') or 0):.2f} USDT | "
                             f"{d.get('network','')} | {d.get('status','')} | "
                             f"{d.get('tx_hash','')}")
        elif kind == "today_deposits":
            for d in db.get_today_deposits_all():
                lines.append(f"#{d.get('id')} | {d.get('created_at','')} | "
                             f"{d.get('username') or d.get('user_id')} | "
                             f"{float(d.get('requested_usdt') or 0):.2f} USDT | "
                             f"{d.get('status','')}")
        elif kind == "orders":
            for o in db.get_all_orders(100000):
                lines.append(f"#{o.get('id')} | {o.get('created_at','')} | "
                             f"{o.get('username') or o.get('user_id')} | "
                             f"{o.get('product_name','')} | "
                             f"{float(o.get('amount_usdt') or 0):.2f} USDT")
        elif kind == "today_orders":
            for o in db.get_today_orders(100000):
                lines.append(f"#{o.get('id')} | {o.get('created_at','')} | "
                             f"{o.get('username') or o.get('user_id')} | "
                             f"{o.get('product_name','')} | "
                             f"{float(o.get('amount_usdt') or 0):.2f} USDT")
        elif kind == "users":
            for u in db.get_all_users():
                lines.append(f"{u.get('user_id')} | @{u.get('username') or '-'} | "
                             f"{u.get('full_name','')} | "
                             f"{float(u.get('balance') or 0):.2f} USDT")
        else:
            flash("Unknown export.", "error")
            return back("dashboard")
        body = "\n".join(lines) or "empty"
        return Response(body, mimetype="text/plain", headers={
            "Content-Disposition": f'attachment; filename="{kind}.txt"'})

    # ── orders / sales ───────────────────────────────────────────────────
    @app.get("/orders")
    @login_required
    def orders():
        scope = request.args.get("scope", "today")
        rows = db.get_today_orders(2000) if scope == "today" else db.get_all_orders(2000)
        total = sum(float(r.get("amount_usdt") or 0) for r in rows if not r.get("refunded"))
        return render_template("orders.html", rows=rows, scope=scope, total=total)

    @app.post("/orders/refund")
    @login_required
    def order_refund():
        try:
            db.refund_order(f_int("order_id"))
            flash("Order refunded.", "ok")
        except Exception as exc:  # noqa: BLE001
            flash(f"Failed: {exc}", "error")
        return back("orders")

    # ── catalog: categories + products + stock ───────────────────────────
    @app.get("/catalog")
    @login_required
    def catalog():
        cats = db.get_categories(active_only=False)
        prods = db.get_products(active_only=False)
        for p in prods:
            p["stock"] = db.get_stock_count(p["id"])
        return render_template("catalog.html", cats=cats, prods=prods)

    @app.post("/catalog/category")
    @login_required
    def catalog_category():
        action = request.form.get("action")
        try:
            if action == "add":
                db.add_category(request.form.get("name", "").strip(),
                                request.form.get("emoji", "📦").strip() or "📦")
            elif action == "toggle":
                db.toggle_category(f_int("cid"))
            elif action == "delete":
                db.delete_category(f_int("cid"), force=True)
            flash("Done.", "ok")
        except Exception as exc:  # noqa: BLE001
            flash(f"Failed: {exc}", "error")
        return back("catalog")

    @app.post("/catalog/product")
    @login_required
    def catalog_product():
        action = request.form.get("action")
        try:
            if action == "add":
                db.add_product(f_int("category_id"),
                               request.form.get("name", "").strip(),
                               request.form.get("emoji", "🛒").strip() or "🛒",
                               request.form.get("description", "").strip(),
                               f_float("price"),
                               request.form.get("duration", "").strip())
            elif action == "toggle":
                db.toggle_product(f_int("pid"))
            elif action == "delete":
                db.delete_product(f_int("pid"))
            elif action == "price":
                col("products").update_one({"id": f_int("pid")},
                                           {"$set": {"price_usdt": f_float("price")}})
            flash("Done.", "ok")
        except Exception as exc:  # noqa: BLE001
            flash(f"Failed: {exc}", "error")
        return back("catalog")

    @app.get("/stock/<int:pid>")
    @login_required
    def stock(pid):
        product = db.get_product(pid)
        if not product:
            flash("Product not found.", "error")
            return redirect(url_for("catalog"))
        items = db.get_stock_items(pid, include_sold=True, limit=500)
        return render_template("stock.html", product=product, items=items,
                               count=db.get_stock_count(pid))

    @app.post("/stock/<int:pid>")
    @login_required
    def stock_action(pid):
        action = request.form.get("action")
        try:
            if action == "add":
                lines = [l.strip() for l in
                         (request.form.get("items") or "").splitlines() if l.strip()]
                res = db.add_stock(pid, lines) or {}
                flash(f"Added {res.get('added', 0)} item(s), "
                      f"{res.get('duplicates', 0)} duplicate(s) skipped.", "ok")
            elif action == "remove":
                db.remove_stock_item(f_int("item_id"))
                flash("Item removed.", "ok")
            elif action == "clear":
                db.clear_stock(pid)
                flash("Stock cleared.", "ok")
        except Exception as exc:  # noqa: BLE001
            flash(f"Failed: {exc}", "error")
        return redirect(url_for("stock", pid=pid))

    # ── free / OTT giveaway items ────────────────────────────────────────
    @app.get("/free")
    @login_required
    def free_items():
        items = db.get_free_items(active_only=False)
        for it in items:
            it["stock"] = db.get_free_stock_count(it["id"])
        return render_template("free.html", items=items)

    @app.post("/free")
    @login_required
    def free_action():
        action = request.form.get("action")
        try:
            if action == "add":
                db.create_free_item(request.form.get("name", "").strip(),
                                    request.form.get("emoji", "🎁").strip() or "🎁",
                                    request.form.get("description", "").strip())
            elif action == "toggle":
                db.toggle_free_item(f_int("fid"))
            elif action == "delete":
                db.delete_free_item(f_int("fid"))
            elif action == "stock":
                lines = [l.strip() for l in
                         (request.form.get("items") or "").splitlines() if l.strip()]
                db.add_free_stock(f_int("fid"), lines)
            flash("Done.", "ok")
        except Exception as exc:  # noqa: BLE001
            flash(f"Failed: {exc}", "error")
        return back("free_items")

    # ── coupons ──────────────────────────────────────────────────────────
    @app.get("/coupons")
    @login_required
    def coupons():
        return render_template("coupons.html", rows=db.get_coupons())

    @app.post("/coupons")
    @login_required
    def coupons_action():
        action = request.form.get("action")
        try:
            if action == "add":
                db.add_coupon(request.form.get("code", "").strip().upper(),
                              f_float("discount"), f_int("max_uses", 1))
            elif action == "toggle":
                db.toggle_coupon(f_int("cid"))
            elif action == "delete":
                db.delete_coupon(f_int("cid"))
            flash("Done.", "ok")
        except Exception as exc:  # noqa: BLE001
            flash(f"Failed: {exc}", "error")
        return redirect(url_for("coupons"))

    # ── support tickets ──────────────────────────────────────────────────
    @app.get("/tickets")
    @login_required
    def tickets():
        scope = request.args.get("scope", "open")
        rows = db.get_open_tickets() if scope == "open" else db.get_all_tickets()
        return render_template("tickets.html", rows=rows, scope=scope)

    @app.get("/tickets/<int:tid>")
    @login_required
    def ticket_detail(tid):
        ticket = db.get_ticket(tid)
        if not ticket:
            flash("Ticket not found.", "error")
            return redirect(url_for("tickets"))
        return render_template("ticket_detail.html", ticket=ticket,
                               messages=db.get_ticket_messages(tid),
                               user=db.get_user(ticket["user_id"]))

    @app.post("/tickets/<int:tid>")
    @login_required
    def ticket_action(tid):
        action = request.form.get("action")
        try:
            ticket = db.get_ticket(tid)
            if action == "reply":
                msg = (request.form.get("message") or "").strip()
                if not msg:
                    raise ValueError("Empty message")
                db.add_ticket_message(tid, 0, msg, is_admin=True)
                send_tg_message(ticket["user_id"],
                                f"🎧 <b>Support reply — Ticket #{tid}</b>\n\n{msg}")
                flash("Reply sent.", "ok")
            elif action == "close":
                db.close_ticket(tid)
                try:
                    send_tg_message(ticket["user_id"],
                                    f"✅ Ticket #{tid} has been closed by support.")
                except Exception:  # noqa: BLE001
                    pass
                flash("Ticket closed.", "ok")
        except Exception as exc:  # noqa: BLE001
            flash(f"Failed: {exc}", "error")
        return redirect(url_for("ticket_detail", tid=tid))

    # ── finance: refund requests, resellers, withdrawals ─────────────────
    @app.get("/finance")
    @login_required
    def finance():
        try:
            refunds = db.get_pending_refund_requests()
        except Exception:  # noqa: BLE001
            refunds = []
        try:
            resellers = db.get_resellers()
        except Exception:  # noqa: BLE001
            resellers = []
        try:
            withdraws = db.get_pending_withdraw_requests()
        except Exception:  # noqa: BLE001
            withdraws = []
        return render_template("finance.html", refunds=refunds,
                               resellers=resellers, withdraws=withdraws)

    @app.post("/finance")
    @login_required
    def finance_action():
        action = request.form.get("action")
        try:
            if action == "refund_approve":
                db.approve_refund_request(f_int("rid"))
            elif action == "refund_reject":
                db.reject_refund_request(f_int("rid"),
                                         request.form.get("note", ""))
            elif action == "reseller_add":
                db.add_reseller(f_int("user_id"))
            elif action == "reseller_approve":
                db.approve_reseller(f_int("user_id"))
            elif action == "reseller_revoke":
                db.revoke_reseller(f_int("user_id"))
            elif action == "withdraw_approve":
                db.process_withdraw_request(f_int("wid"), True)
            elif action == "withdraw_reject":
                db.process_withdraw_request(f_int("wid"), False)
            flash("Done.", "ok")
        except Exception as exc:  # noqa: BLE001
            flash(f"Failed: {exc}", "error")
        return redirect(url_for("finance"))

    # ── broadcast ────────────────────────────────────────────────────────
    @app.get("/broadcast")
    @login_required
    def broadcast():
        return render_template("broadcast.html", state=BROADCAST,
                               total_users=len(db.get_all_users()))

    @app.post("/broadcast")
    @login_required
    def broadcast_start():
        text = (request.form.get("text") or "").strip()
        target = request.form.get("target", "all")
        if not text:
            flash("Message is empty.", "error")
            return redirect(url_for("broadcast"))
        if BROADCAST["running"]:
            flash("A broadcast is already running.", "error")
            return redirect(url_for("broadcast"))
        users = db.get_all_users()
        if target == "unbanned":
            users = [u for u in users if not u.get("is_banned")]
        elif target == "buyers":
            buyers = {o.get("user_id") for o in db.get_all_orders(100000)}
            users = [u for u in users if u.get("user_id") in buyers]
        ids = [u["user_id"] for u in users]
        threading.Thread(target=_broadcast_worker, args=(text, ids),
                         name="broadcast", daemon=True).start()
        flash(f"Broadcast started for {len(ids)} user(s).", "ok")
        return redirect(url_for("broadcast"))

    @app.get("/broadcast/status")
    @login_required
    def broadcast_status():
        return jsonify(BROADCAST)

    # ── daily report ─────────────────────────────────────────────────────
    @app.get("/daily")
    @login_required
    def daily():
        date_str = request.args.get("date") or datetime.now().strftime("%Y-%m-%d")
        try:
            datetime.strptime(date_str, "%Y-%m-%d")
        except ValueError:
            flash("Use YYYY-MM-DD.", "error")
            date_str = datetime.now().strftime("%Y-%m-%d")
        report = db.get_daily_report(date_str)
        recent = [(datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
                  for i in range(7)]
        return render_template("daily.html", r=report, date=date_str, recent=recent)

    # ── Telegram (OTP) accounts ──────────────────────────────────────────
    @app.get("/tg")
    @login_required
    def tg_panel():
        rows = strip_ids(col("otp_stock").find().sort("id", -1).limit(1000))
        groups = {}
        for r in rows:
            key = (r.get("country_name", "?"), r.get("account_year", "?"))
            g = groups.setdefault(key, {"country": key[0], "year": key[1],
                                        "icon": r.get("country_icon", "🌍"),
                                        "price": r.get("price", 0),
                                        "available": 0, "total": 0})
            g["total"] += 1
            g["available"] += 1 if r.get("available") else 0
        prices = strip_ids(col("otp_auto_prices").find())
        orders_today = col("otp_orders").count_documents(
            {"created_at": {"$regex": "^" + datetime.now().strftime('%Y-%m-%d')},
             "status": "delivered"})
        return render_template("tg.html", rows=rows, groups=list(groups.values()),
                               prices=prices, orders_today=orders_today,
                               login_state=session.get("tg_login"),
                               usdt_rate=om.get_usdt_rate())

    @app.post("/tg/add/start")
    @login_required
    def tg_add_start():
        token, err = tg_login.start_login(request.form.get("phone", ""))
        if err:
            flash(err, "error")
        else:
            session["tg_login"] = {"token": token, "step": "otp",
                                   "phone": request.form.get("phone", "").strip()}
            flash("OTP sent — check the Telegram app for that number.", "ok")
        return redirect(url_for("tg_panel"))

    @app.post("/tg/add/code")
    @login_required
    def tg_add_code():
        state = session.get("tg_login") or {}
        status, msg = tg_login.submit_code(state.get("token", ""),
                                           request.form.get("code", ""))
        if status == "need_2fa":
            state["step"] = "2fa"
            session["tg_login"] = state
        elif status == "ok":
            state["step"] = "details"
            session["tg_login"] = state
        flash(msg, "error" if status == "error" else "ok")
        return redirect(url_for("tg_panel"))

    @app.post("/tg/add/password")
    @login_required
    def tg_add_password():
        state = session.get("tg_login") or {}
        pwd = request.form.get("password", "")
        status, msg = tg_login.submit_password(state.get("token", ""), pwd)
        if status == "ok":
            state["step"] = "details"
            state["twofa"] = pwd
            session["tg_login"] = state
        flash(msg, "error" if status == "error" else "ok")
        return redirect(url_for("tg_panel"))

    @app.post("/tg/add/finish")
    @login_required
    def tg_add_finish():
        state = session.get("tg_login") or {}
        err = tg_login.finish(state.get("token", ""),
                              request.form.get("country", "").strip(),
                              request.form.get("icon", "🌍").strip() or "🌍",
                              request.form.get("year", ""),
                              request.form.get("price", ""),
                              request.form.get("twofa", "").strip()
                              or state.get("twofa", ""))
        if err:
            flash(err, "error")
        else:
            session.pop("tg_login", None)
            flash("Account added to stock.", "ok")
        return redirect(url_for("tg_panel"))

    @app.post("/tg/add/cancel")
    @login_required
    def tg_add_cancel():
        state = session.pop("tg_login", None) or {}
        tg_login.cancel(state.get("token", ""))
        flash("Login cancelled.", "ok")
        return redirect(url_for("tg_panel"))

    @app.post("/tg/stock")
    @login_required
    def tg_stock_action():
        action = request.form.get("action")
        try:
            if action == "delete":
                phone = request.form.get("phone", "").lstrip("+")
                row = col("otp_stock").find_one({"phone": phone}, {"session_file": 1})
                if row and row.get("session_file"):
                    om._delete_session_files(row["session_file"])
                col("otp_stock").delete_one({"phone": phone})
            elif action == "price_group":
                col("otp_stock").update_many(
                    {"country_name": request.form.get("country"),
                     "account_year": f_int("year")},
                    {"$set": {"price": f_int("price")}})
            elif action == "auto_price":
                col("otp_auto_prices").update_one(
                    {"country": request.form.get("country"),
                     "year": str(request.form.get("year"))},
                    {"$set": {"price": f_int("price")}}, upsert=True)
            elif action == "wipe_country":
                country = request.form.get("country")
                for d in col("otp_stock").find({"country_name": country},
                                               {"session_file": 1}):
                    if d.get("session_file"):
                        om._delete_session_files(d["session_file"])
                col("otp_stock").delete_many({"country_name": country})
            elif action == "rate":
                om.set_setting("usdt_rate", request.form.get("rate"))
            flash("Done.", "ok")
        except Exception as exc:  # noqa: BLE001
            flash(f"Failed: {exc}", "error")
        return redirect(url_for("tg_panel"))

    # ── WhatsApp accounts ────────────────────────────────────────────────
    @app.get("/wa")
    @login_required
    def wa_panel():
        rows = strip_ids(col("wa_stock").find().sort("id", -1).limit(1000))
        sold = col("wa_orders").count_documents({"status": "delivered"})
        return render_template("wa.html", rows=rows, sold=sold,
                               enabled=db.get_setting("wa_sales_enabled", "1") == "1")

    @app.post("/wa")
    @login_required
    def wa_action():
        action = request.form.get("action")
        try:
            if action == "add":
                added = 0
                for line in (request.form.get("items") or "").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    parts = [p.strip() for p in line.split("|")]
                    phone = parts[0].lstrip("+")
                    price = int(float(parts[1])) if len(parts) > 1 and parts[1] else 0
                    country = parts[2] if len(parts) > 2 and parts[2] else "Unknown"
                    twofa = parts[3] if len(parts) > 3 and parts[3] else "None"
                    note = parts[4] if len(parts) > 4 else ""
                    col("wa_stock").update_one(
                        {"phone": phone},
                        {"$set": {"price": price, "country": country,
                                  "twofa": twofa, "note": note, "available": 1},
                         "$setOnInsert": {"id": next_id("wa_stock"),
                                          "added_at": now_iso()}},
                        upsert=True)
                    added += 1
                flash(f"Added {added} number(s).", "ok")
            elif action == "delete":
                col("wa_stock").delete_one(
                    {"phone": request.form.get("phone", "").lstrip("+")})
                flash("Deleted.", "ok")
            elif action == "price_country":
                col("wa_stock").update_many(
                    {"country": request.form.get("country")},
                    {"$set": {"price": f_int("price")}})
                flash("Pricing updated.", "ok")
            elif action == "toggle_sales":
                db.set_setting("wa_sales_enabled",
                               "0" if db.get_setting("wa_sales_enabled", "1") == "1" else "1")
                flash("Sales switch updated.", "ok")
        except Exception as exc:  # noqa: BLE001
            flash(f"Failed: {exc}", "error")
        return redirect(url_for("wa_panel"))

    # ── settings ─────────────────────────────────────────────────────────
    QUICK_KEYS = [
        ("bot_name", "Bot name", "text"),
        ("bot_emoji", "Bot emoji", "text"),
        ("usdt_trc20_address", "USDT TRC20 address", "text"),
        ("usdt_bep20_address", "USDT BEP20 address", "text"),
        ("binance_pay_id", "Binance Pay ID", "text"),
        ("min_deposit_usdt", "Min deposit (USDT)", "number"),
        ("low_stock_threshold", "Low stock threshold", "number"),
        ("log_channel_id", "Log channel ID", "text"),
        ("support_username", "Support username", "text"),
        ("referral_bonus_usdt", "Referral bonus (USDT)", "number"),
    ]
    TOGGLE_KEYS = [
        ("maintenance", "Maintenance mode"),
        ("referral_on", "Referral system"),
        ("wa_sales_enabled", "WhatsApp sales"),
        ("otp_sales_enabled", "Telegram (OTP) sales"),
        ("free_items_enabled", "Free items"),
    ]

    @app.get("/settings")
    @login_required
    def settings_page():
        rows = sorted(strip_ids(col("settings").find()),
                      key=lambda r: r.get("key", ""))
        otp_rows = sorted(strip_ids(col("otp_settings").find()),
                          key=lambda r: r.get("key", ""))
        admins = db.get_extra_admins()
        quick = [(k, label, kind, db.get_setting(k, ""))
                 for k, label, kind in QUICK_KEYS]
        toggles = [(k, label, db.get_setting(k, "0") == "1")
                   for k, label in TOGGLE_KEYS]
        return render_template("settings.html", rows=rows, otp_rows=otp_rows,
                               admins=admins, quick=quick, toggles=toggles)

    @app.post("/settings")
    @login_required
    def settings_action():
        action = request.form.get("action")
        try:
            if action == "set":
                db.set_setting(request.form.get("key", "").strip(),
                               request.form.get("value", ""))
            elif action == "set_otp":
                om.set_setting(request.form.get("key", "").strip(),
                               request.form.get("value", ""))
            elif action == "quick":
                for key, _label, _kind in QUICK_KEYS:
                    if key in request.form:
                        db.set_setting(key, request.form.get(key, "").strip())
            elif action == "toggle":
                key = request.form.get("key", "")
                db.set_setting(key, "0" if db.get_setting(key, "0") == "1" else "1")
            elif action == "add_admin":
                db.add_extra_admin(f_int("user_id"))
            elif action == "remove_admin":
                db.remove_extra_admin(f_int("user_id"))
            flash("Saved.", "ok")
        except Exception as exc:  # noqa: BLE001
            flash(f"Failed: {exc}", "error")
        return redirect(url_for("settings_page"))

    return app
