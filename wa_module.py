"""
wa_module.py — WhatsApp Account Store (user-facing flow).

Design notes
────────────
• Every message carries an inline keyboard (no dead-end screens).
• All copy is in professional English.
• Button labels contain NO leading unicode emoji — icons are rendered from
  Premium Custom Emoji IDs mapped in emoji_config.BTN_EMOJI /
  BTN_EMOJI_PREFIX (auto-resolved from callback_data), exactly like the
  Telegram (TG) account section.
• Shared wallet: balance lives in USDT on `users.balance`, pricing is INR.

Delivery model: number is reserved for the buyer, the administrator forwards
the live login code from the WA Panel, and an automatic refund is issued if
no code is delivered inside the SLA window.
"""
import asyncio, time, logging
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, ContextTypes

import database as db
import styled_api
import emoji_config as EC
import otp_module as om

logger = logging.getLogger("wa_module")

AUTO_REFUND_SECONDS = 600          # 10 minutes SLA
PER_PAGE = 10
WA_SALES_SETTING = "wa_sales_enabled"


# ── schema (MongoDB: indexes only) ───────────────────────────────────────────
from mongo_client import col, next_id, now_iso, ensure_indexes


def init_schema():
    ensure_indexes()
    recover_stale_orders()


def wa_sales_enabled():
    """Return whether users are allowed to start new WhatsApp purchases.

    The value is stored in the shared MongoDB settings collection so the
    switch survives restarts and is shared by every bot process.
    """
    return db.get_setting(WA_SALES_SETTING, "1") == "1"


def set_wa_sales_enabled(enabled):
    db.set_setting(WA_SALES_SETTING, "1" if enabled else "0")


def _sales_disabled_text():
    return (
        "<b>WhatsApp purchases are currently unavailable</b>\n"
        "────────────────────────────\n\n"
        "The WhatsApp account section is temporarily turned off by the "
        "owner or an administrator.\n\n"
        "Please check again later."
    )


def recover_stale_orders():
    """Refund WhatsApp reservations left behind by a process restart."""
    cutoff = (datetime.now() - timedelta(seconds=AUTO_REFUND_SECONDS)).isoformat()
    for order in _orders().find({"status": "pending", "created_at": {"$lt": cutoff}}):
        changed = _orders().update_one(
            {"id": order["id"], "status": "pending"},
            {"$set": {"status": "refunded", "refunded_at": now_iso(),
                      "refund_reason": "process_restart"}})
        if changed.modified_count:
            db.update_balance(
                order["user_id"], inr_to_usdt(order["price"]),
                "wa_refund", "wa_order", order["id"])
            release_stock(order.get("phone"))


def _stock():
    return col("wa_stock")


def _orders():
    return col("wa_orders")


# ── shared helpers (reuse the TG section's wallet + styling layer) ───────────
inr_to_usdt = om.inr_to_usdt
usdt_to_inr = om.usdt_to_inr
user_balance_inr = om.user_balance_inr


def debit_inr(uid, inr):
    return db.debit_balance(uid, inr_to_usdt(inr), "wa_purchase", "wa", int(uid))


def credit_inr(uid, inr):
    db.update_balance(uid, inr_to_usdt(inr), "wa_refund", "wa", int(uid))



def _btn(text, cb=None, *, url=None, style=None, emoji_id=None):
    eid = emoji_id if emoji_id else EC.get_btn_emoji(cb or "")
    return styled_api.btn(text, cb, url=url, style=style, emoji_id=eid or None)


async def _se(q, text, rows, parse_mode="HTML"):
    """Styled edit with a python-telegram-bot fallback."""
    is_photo = bool(q.message and q.message.photo)
    method = styled_api.edit_caption if is_photo else styled_api.edit
    result = await method(q.message.chat_id, q.message.message_id, text, rows, parse_mode)
    if result.get("ok"):
        return
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(
        b["text"], callback_data=b.get("callback_data", "noop"), url=b.get("url"))
        for b in row] for row in rows])
    etext = EC.apply_premium_emoji(text)
    try:
        if is_photo:
            await q.edit_message_caption(caption=etext, reply_markup=kb, parse_mode=parse_mode)
        else:
            await q.edit_message_text(etext, reply_markup=kb, parse_mode=parse_mode)
    except Exception as exc:
        logger.warning(f"_se edit failed: {exc}")
        try:
            await q.message.reply_html(etext, reply_markup=kb)
        except Exception:
            pass


async def _send(bot, chat_id, text, rows, parse_mode="HTML"):
    result = await styled_api.send(chat_id, text, rows, parse_mode)
    if result.get("ok"):
        return result
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(
        b["text"], callback_data=b.get("callback_data", "noop"), url=b.get("url"))
        for b in row] for row in rows])
    try:
        await bot.send_message(chat_id, EC.apply_premium_emoji(text),
                               reply_markup=kb, parse_mode=parse_mode)
    except Exception as exc:
        logger.warning(f"_send failed: {exc}")
    return {"ok": False}


def flag_html(country):
    return om.flag_html(country)


def _prefix(country):
    flag = flag_html(country)
    return (flag + " ") if flag else ""


def home_row():
    return [_btn("Buy Another", "wa_buy", style="success"),
            _btn("Main Menu", "home", style="danger")]


# ── order helpers ────────────────────────────────────────────────────────────
def get_order(oid):
    """Returns the same tuple shape the rest of the bot already expects."""
    d = _orders().find_one({"id": int(oid)})
    if not d:
        return None
    return (d.get("id"), d.get("user_id"), d.get("country"), d.get("price"),
            d.get("phone"), d.get("otp"), d.get("status"), d.get("created_at"))


def set_order_status(oid, status):
    _orders().update_one({"id": int(oid)}, {"$set": {"status": status}})


def release_stock(phone):
    _stock().update_one({"phone": phone},
                        {"$set": {"available": 1}, "$unset": {"reserved_at": ""}})



# ── screen 1: countries ──────────────────────────────────────────────────────
async def wa_buy_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if not wa_sales_enabled():
        return await _se(q, _sales_disabled_text(), [
            [_btn("Support", "support", style="primary"),
             _btn("Main Menu", "home", style="danger")]
        ])
    await _show_countries(q, page=1)


async def wa_page_cb(update, ctx):
    q = update.callback_query
    await q.answer()
    await _show_countries(q, page=int(q.data.split("|")[1]))


async def _show_countries(q, page=1):
    if not wa_sales_enabled():
        return await _se(q, _sales_disabled_text(), [
            [_btn("Support", "support", style="primary"),
             _btn("Main Menu", "home", style="danger")]
        ])

    rows = [(g["_id"], g["cnt"]) for g in _stock().aggregate([
        {"$match": {"available": 1}},
        {"$group": {"_id": "$country_name", "cnt": {"$sum": 1}}},
        {"$sort": {"_id": 1}},
    ])]

    total = len(rows)
    page_rows = rows[(page - 1) * PER_PAGE: page * PER_PAGE]

    if not page_rows:
        return await _se(q,
            "<b>WhatsApp Account Store</b>\n"
            "────────────────────────────\n\n"
            "There is no stock available at the moment.\n"
            "New numbers are added regularly — please check back shortly or "
            "contact support for a restock estimate.",
            [[_btn("Refresh", "wa_buy", style="primary")],
             [_btn("Support", "support", style="primary"),
              _btn("Main Menu", "home", style="danger")]])

    btns = []
    for name, cnt in page_rows:
        btns.append([_btn(f"{name}  •  {cnt} in stock", f"wa_c|{name}",
                          style="primary", emoji_id=EC.get_country_emoji(name) or None)])
    nav = []
    if page > 1:
        nav.append(_btn("Previous", f"wa_cp|{page-1}", style="primary"))
    if page * PER_PAGE < total:
        nav.append(_btn("Next", f"wa_cp|{page+1}", style="primary"))
    if nav:
        btns.append(nav)
    btns.append([_btn("My Orders", "wa_orders", style="primary"),
                 _btn("Add Funds", "deposit", style="success")])
    btns.append([_btn("Main Menu", "home", style="danger")])

    uid = q.from_user.id
    bal_inr = user_balance_inr(uid)
    user = db.get_user(uid)
    bal_usdt = user["balance"] if user else 0.0
    txt = ("<b>WhatsApp Account Store</b>\n"
           "────────────────────────────\n\n"
           "Select a country to view the available numbers and pricing.\n"
           "All prices are quoted in Indian Rupees (₹) and charged from your "
           "store wallet.\n\n"
           f"💰 <b>Wallet balance:</b> ₹{bal_inr}  (${bal_usdt:.2f})\n"
           f"🌍 <b>Countries available:</b> {total}")
    await _se(q, txt, btns)


# ── screen 2: price / category list ──────────────────────────────────────────
async def wa_country_cb(update, ctx):
    q = update.callback_query
    await q.answer()
    if not wa_sales_enabled():
        return await _se(q, _sales_disabled_text(), [
            [_btn("Support", "support", style="primary"),
             _btn("Main Menu", "home", style="danger")]
        ])
    country = q.data.split("|", 1)[1]
    rows = [(g["_id"]["p"], g["_id"]["c"], g["cnt"]) for g in _stock().aggregate([
        {"$match": {"country_name": country, "available": 1}},
        {"$group": {"_id": {"p": "$price", "c": "$category"}, "cnt": {"$sum": 1}}},
        {"$sort": {"_id.p": 1}},
    ])]

    if not rows:
        return await _se(q,
            f"{_prefix(country)}<b>{country} — Sold Out</b>\n\n"
            "Every number for this country has just been sold.\n"
            "Please select a different country or try again later.",
            [[_btn("Back to Countries", "wa_buy", style="primary")],
             [_btn("Main Menu", "home", style="danger")]])

    btns = []
    for price, category, cnt in rows:
        btns.append([_btn(f"{category}  •  ₹{price}  •  {cnt} available",
                          f"wa_p|{country}|{price}|{category}", style="primary")])
    btns.append([_btn("Back to Countries", "wa_buy", style="primary"),
                 _btn("Main Menu", "home", style="danger")])

    await _se(q,
        f"{_prefix(country)}<b>{country} — WhatsApp Numbers</b>\n"
        "────────────────────────────\n\n"
        "Choose a package below. Each package reserves one verified number "
        "for you and delivers the login code required to activate it.\n\n"
        f"💰 <b>Wallet balance:</b> ₹{user_balance_inr(q.from_user.id)}", btns)


# ── screen 3: confirmation ───────────────────────────────────────────────────
async def wa_price_cb(update, ctx):
    q = update.callback_query
    await q.answer()
    if not wa_sales_enabled():
        return await _se(q, _sales_disabled_text(), [
            [_btn("Support", "support", style="primary"),
             _btn("Main Menu", "home", style="danger")]
        ])
    _, country, price, category = q.data.split("|", 3)
    price = int(price)
    stock = _stock().count_documents({"country_name": country, "price": price,
                                      "category": category, "available": 1})

    if not stock:
        return await _se(q,
            f"{_prefix(country)}<b>Package unavailable</b>\n\n"
            "This package was sold while you were browsing.",
            [[_btn("Back", f"wa_c|{country}", style="primary")],
             [_btn("Main Menu", "home", style="danger")]])

    bal = user_balance_inr(q.from_user.id)
    rows = [
        [_btn(f"Confirm Purchase  (₹{price})",
              f"wa_ok|{country}|{price}|{category}", style="success")],
        [_btn("Add Funds", "deposit", style="primary"),
         _btn("Back", f"wa_c|{country}", style="primary")],
        [_btn("Main Menu", "home", style="danger")],
    ]
    await _se(q,
        f"{_prefix(country)}<b>Order Summary</b>\n"
        "────────────────────────────\n\n"
        f"🌍 <b>Country:</b> {country}\n"
        f"📦 <b>Package:</b> {category}\n"
        f"💰 <b>Price:</b> ₹{price}\n"
        f"📊 <b>In stock:</b> {stock}\n"
        f"👛 <b>Your balance:</b> ₹{bal}\n\n"
        "<b>How delivery works</b>\n"
        "1. The amount is debited and one number is reserved for you.\n"
        "2. You enter the number in WhatsApp and request the login code.\n"
        "3. Our operator forwards the code to you inside this chat.\n"
        "4. If no code is delivered within 10 minutes, the order is cancelled "
        "and the full amount is refunded automatically.", rows)


# ── purchase ─────────────────────────────────────────────────────────────────
async def wa_confirm_cb(update, ctx):
    q = update.callback_query
    uid = q.from_user.id
    _, country, price, category = q.data.split("|", 3)
    price = int(price)
    await q.answer()

    # Re-check at confirmation time so an order cannot slip through after an
    # owner/admin has switched WhatsApp sales off while the buyer was browsing.
    if not wa_sales_enabled():
        return await _se(q, _sales_disabled_text(), [
            [_btn("Support", "support", style="primary"),
             _btn("Main Menu", "home", style="danger")]
        ])

    if user_balance_inr(uid) < price:
        return await _se(q,
            "<b>Insufficient Balance</b>\n"
            "────────────────────────────\n\n"
            f"Required: <b>₹{price}</b>\n"
            f"Available: <b>₹{user_balance_inr(uid)}</b>\n\n"
            "Please top up your wallet to complete this purchase.",
            [[_btn("Add Funds", "deposit", style="success")],
             [_btn("Back", f"wa_c|{country}", style="primary"),
              _btn("Main Menu", "home", style="danger")]])

    # Debit first, then atomically reserve the number (prevents double-selling).
    if not debit_inr(uid, price):
        return await _se(q,
            "<b>Payment Failed</b>\n\nYour wallet balance could not be debited. "
            "Please try again.",
            [[_btn("Retry", f"wa_c|{country}", style="primary")],
             [_btn("Main Menu", "home", style="danger")]])

    row = _stock().find_one_and_update(
        {"country_name": country, "price": price, "category": category, "available": 1},
        {"$set": {"available": 0, "reserved_at": now_iso()}},
    )
    if not row:
        credit_inr(uid, price)   # refund — nothing reserved
        return await _se(q,
            "<b>Out of Stock</b>\n\nThis number was sold a moment ago. "
            "Please choose another package.",
            [[_btn("Back", f"wa_c|{country}", style="primary")],
             [_btn("Main Menu", "home", style="danger")]])
    phone = row.get("phone")
    twofa = row.get("twofa")
    note = row.get("note") or ""
    icon = row.get("country_icon") or "🌍"

    oid = next_id("wa_orders")
    _orders().insert_one({
        "id": oid, "user_id": uid, "country": country, "price": price,
        "phone": phone, "otp": None, "status": "pending",
        "amount_usdt": inr_to_usdt(price),
        "created_at": now_iso(), "delivered_at": None, "refunded_at": None,
        "store_order_id": None,
    })


    await _se(q, _order_text(oid, country, phone, price, twofa),
              _order_rows(oid))

    # notify operators
    await _notify_admins(ctx.application, oid, uid, country, phone, price, note)

    ctx.application.create_task(_auto_refund_task(ctx.application, oid,
                                                  q.message.chat_id,
                                                  q.message.message_id))


def _order_text(oid, country, phone, price, twofa):
    twofa_line = (f"🔐 <b>Two-step PIN:</b> <code>{twofa}</code>\n"
                  if twofa and twofa != "None" else "")
    return (f"{_prefix(country)}<b>Order #{oid} — Awaiting Login Code</b>\n"
            "────────────────────────────\n\n"
            f"📱 <b>Number:</b> <code>+{phone}</code>\n"
            f"🌍 <b>Country:</b> {country}\n"
            f"💰 <b>Paid:</b> ₹{price}\n"
            f"{twofa_line}"
            "\n<b>Next steps</b>\n"
            "1. Open WhatsApp and register the number above.\n"
            "2. Request the SMS verification code.\n"
            "3. The code will be delivered here automatically.\n\n"
            "<i>Service window: 10 minutes. If no code is delivered, the order "
            "is cancelled and refunded in full.</i>")


def _order_rows(oid):
    return [
        [_btn("Refresh Status", f"wa_st|{oid}", style="primary")],
        [_btn("Cancel & Refund", f"wa_cx|{oid}", style="danger")],
        [_btn("Support", "support", style="primary"),
         _btn("Main Menu", "home", style="danger")],
    ]


async def _notify_admins(app: Application, oid, uid, country, phone, price, note):
    text = ("<b>New WhatsApp Order</b>\n"
            "────────────────────────────\n\n"
            f"🧾 <b>Order:</b> #{oid}\n"
            f"👤 <b>Buyer:</b> <code>{uid}</code>\n"
            f"🌍 <b>Country:</b> {country}\n"
            f"📱 <b>Number:</b> <code>+{phone}</code>\n"
            f"💰 <b>Amount:</b> ₹{price}\n"
            + (f"📝 <b>Note:</b> {note}\n" if note else "") +
            "\nForward the login code to the buyer within 10 minutes, "
            "otherwise the order is refunded automatically.")
    rows = [
        [_btn("Send Login Code", f"wap_send|{oid}", style="success")],
        [_btn("Cancel & Refund", f"wap_refund|{oid}", style="danger")],
        [_btn("WA Panel", "wa_panel", style="primary")],
    ]
    for admin_id in _admin_ids():
        try:
            await _send(app.bot, admin_id, text, rows)
        except Exception as exc:
            logger.debug(f"admin notify failed {admin_id}: {exc}")


def _admin_ids():
    ids = set()
    try:
        import config
        ids.update(config.ADMIN_IDS)
    except Exception:
        pass
    try:
        ids.update(db.get_extra_admins())
    except Exception:
        pass
    if om.OTP_ADMIN_ID:
        ids.add(om.OTP_ADMIN_ID)
    return [i for i in ids if i]


# ── status / cancel ──────────────────────────────────────────────────────────
async def wa_status_cb(update, ctx):
    q = update.callback_query
    oid = int(q.data.split("|")[1])
    o = get_order(oid)
    if not o:
        await q.answer()
        return await _se(q, "<b>Order not found.</b>",
                         [home_row()])
    _, uid, country, price, phone, otp, status, created = o
    if uid != q.from_user.id:
        return await q.answer("This order belongs to another account.", show_alert=True)

    if status == "delivered":
        await q.answer("Login code delivered.")
        return await _se(q, _delivered_text(oid, country, phone, otp),
                         [[_btn("Buy Another", "wa_buy", style="success")],
                          [_btn("Support", "support", style="primary"),
                           _btn("Main Menu", "home", style="danger")]])
    if status in ("refunded", "cancelled"):
        await q.answer("Order cancelled.")
        return await _se(q,
            f"<b>Order #{oid} — Cancelled</b>\n\n"
            f"₹{price} has been refunded to your wallet.",
            [home_row()])
    await q.answer("Still pending — the operator has been notified.")
    await _se(q, _order_text(oid, country, phone, price, None), _order_rows(oid))


async def wa_cancel_cb(update, ctx):
    q = update.callback_query
    oid = int(q.data.split("|")[1])
    o = get_order(oid)
    if not o:
        await q.answer()
        return await _se(q, "<b>Order not found.</b>", [home_row()])
    _, uid, country, price, phone, otp, status, created = o
    if uid != q.from_user.id:
        return await q.answer("This order belongs to another account.", show_alert=True)
    if status != "pending":
        return await q.answer("This order can no longer be cancelled.", show_alert=True)
    await q.answer("Cancelling…")
    if not refund_order(oid, uid, price, phone):
        return await q.answer("This order was already processed.", show_alert=True)
    await _se(q,
        f"<b>Order #{oid} — Cancelled</b>\n"
        "────────────────────────────\n\n"
        f"📱 <b>Number:</b> <code>+{phone}</code>\n"
        f"💰 <b>Refunded:</b> ₹{price}\n\n"
        "The amount is back in your wallet and the number has been returned "
        "to stock.",
        [home_row()])


def refund_order(oid, uid, price, phone, status="refunded"):
    # Cancel, operator refund and timeout can race. Only the first transition
    # from pending may return money or release the reserved number.
    changed = _orders().update_one(
        {"id": int(oid), "user_id": int(uid), "status": "pending"},
        {"$set": {"status": status, "refunded_at": now_iso()}})
    if not changed.modified_count:
        return False
    credit_inr(uid, price)
    release_stock(phone)
    return True


def _delivered_text(oid, country, phone, otp):
    return (f"{_prefix(country)}<b>Order #{oid} — Delivered</b>\n"
            "────────────────────────────\n\n"
            f"📱 <b>Number:</b> <code>+{phone}</code>\n"
            f"🔢 <b>Login code:</b> <code>{otp}</code>\n\n"
            "Enter the code in WhatsApp to complete verification. "
            "Enable two-step verification immediately after login and keep "
            "the number active for at least 24 hours.\n\n"
            "<i>Thank you for your purchase.</i>")


# ── my orders ────────────────────────────────────────────────────────────────
async def wa_orders_cb(update, ctx):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    rows = [(d.get("id"), d.get("country"), d.get("phone"), d.get("price"), d.get("status"))
            for d in _orders().find({"user_id": uid}).sort("id", -1).limit(10)]

    if not rows:
        return await _se(q,
            "<b>My WhatsApp Orders</b>\n\nYou have not placed any orders yet.",
            [[_btn("Browse Stock", "wa_buy", style="success")],
             [_btn("Main Menu", "home", style="danger")]])
    lines = ["<b>My WhatsApp Orders</b>", "────────────────────────────", ""]
    btns = []
    for oid, country, phone, price, status in rows:
        lines.append(f"🧾 <b>#{oid}</b> — {country} • <code>+{phone}</code> • "
                     f"₹{price} • {status.title()}")
        btns.append([_btn(f"Order #{oid}  •  {status.title()}",
                          f"wa_st|{oid}", style="primary")])
    btns.append([_btn("Browse Stock", "wa_buy", style="success"),
                 _btn("Main Menu", "home", style="danger")])
    await _se(q, "\n".join(lines), btns)


# ── auto refund ──────────────────────────────────────────────────────────────
async def _auto_refund_task(app: Application, oid, chat_id, msg_id):
    await asyncio.sleep(AUTO_REFUND_SECONDS)
    o = get_order(oid)
    if not o:
        return
    _, uid, country, price, phone, otp, status, created = o
    if status != "pending":
        return
    if not refund_order(oid, uid, price, phone):
        return
    text = (f"<b>Order #{oid} — Expired</b>\n"
            "────────────────────────────\n\n"
            f"📱 <b>Number:</b> <code>+{phone}</code>\n"
            f"💰 <b>Refunded:</b> ₹{price}\n\n"
            "No login code was delivered inside the service window, so the "
            "order was cancelled automatically and your wallet has been "
            "credited in full.")
    rows = [[_btn("Buy Another", "wa_buy", style="success")],
            [_btn("Support", "support", style="primary"),
             _btn("Main Menu", "home", style="danger")]]
    r = await styled_api.edit(chat_id, msg_id, text, rows, "HTML")
    if not r.get("ok"):
        await _send(app.bot, chat_id, text, rows)


# ── delivery hook used by wa_admin ───────────────────────────────────────────
async def deliver_code(app: Application, oid, code):
    o = get_order(oid)
    if not o:
        return False, "Order not found."
    _, uid, country, price, phone, otp, status, created = o
    if status != "pending":
        return False, f"Order is already marked as {status}."
    upd = _orders().update_one(
        {"id": int(oid), "status": "pending"},
        {"$set": {"otp": code, "status": "delivered", "delivered_at": now_iso()}},
    )
    if upd.modified_count == 0:
        return False, "Order was already processed."
    _stock().delete_one({"phone": phone})


    try:
        db.create_order(uid, 0, f"WhatsApp {country} +{phone}", inr_to_usdt(price))
    except Exception as exc:
        logger.debug(f"store order log skipped: {exc}")

    rows = [[_btn("Buy Another", "wa_buy", style="success")],
            [_btn("My Orders", "wa_orders", style="primary"),
             _btn("Support", "support", style="primary")],
            [_btn("Main Menu", "home", style="danger")]]
    await _send(app.bot, uid, _delivered_text(oid, country, phone, code), rows)

    if om.OTP_LOG_CHANNEL:
        try:
            await _send(app.bot, om.OTP_LOG_CHANNEL,
                        "<b>WhatsApp Sale</b>\n"
                        f"👤 <code>{uid}</code>\n"
                        f"🌍 {country}\n"
                        f"📱 <code>+{phone}</code>\n"
                        f"💰 ₹{price}",
                        [[_btn("WA Panel", "wa_panel", style="primary")]])
        except Exception:
            pass
    try:
        import sale_feed
        await sale_feed.broadcast_sale(
            app.bot, qty=1, product_name=f"{country} WhatsApp Account",
            product_emoji="🟢", product_emoji_id=EC.get_country_emoji(country),
            source="bot")
    except Exception as exc:
        logger.debug(f"sale_feed wa skip: {exc}")
    return True, "Delivered."


# ── registration ─────────────────────────────────────────────────────────────
def register_handlers(app: Application):
    app.add_handler(CallbackQueryHandler(wa_buy_cb,     pattern=r"^wa_buy$"))
    app.add_handler(CallbackQueryHandler(wa_page_cb,    pattern=r"^wa_cp\|\d+$"))
    app.add_handler(CallbackQueryHandler(wa_orders_cb,  pattern=r"^wa_orders$"))
    app.add_handler(CallbackQueryHandler(wa_country_cb, pattern=r"^wa_c\|"))
    app.add_handler(CallbackQueryHandler(wa_price_cb,   pattern=r"^wa_p\|"))
    app.add_handler(CallbackQueryHandler(wa_confirm_cb, pattern=r"^wa_ok\|"))
    app.add_handler(CallbackQueryHandler(wa_status_cb,  pattern=r"^wa_st\|"))
    app.add_handler(CallbackQueryHandler(wa_cancel_cb,  pattern=r"^wa_cx\|"))
    try:
        import wa_admin
        wa_admin.register(app)
    except Exception as exc:
        logger.warning(f"wa_admin not loaded: {exc}")
    logger.info("wa_module (WhatsApp store) handlers registered")
