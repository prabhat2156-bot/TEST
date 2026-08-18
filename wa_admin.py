"""
wa_admin.py — WA Panel (WhatsApp account administration).

Features
────────
• Add Single  — add one number (guided prompts)
• Add Bulk    — paste many lines: phone|price|category|2fa|note
• Manage Stock— per-country stock view, delete a number, wipe a country
• Set Pricing — update the price of every number of one country
• Pending     — open orders + one-tap login-code delivery
• Statistics  — sales, revenue and stock overview

Every reply includes an inline keyboard, all copy is professional English and
button icons come from Premium Custom Emoji IDs in emoji_config.
"""
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CallbackQueryHandler, MessageHandler, CommandHandler,
    ConversationHandler, ContextTypes, filters,
)

import database as db
import styled_api
import emoji_config as EC
import otp_module as om
import wa_module as wm
from mongo_client import col, next_id, now_iso

logger = logging.getLogger("wa_admin")

_btn = wm._btn
_se = wm._se


# ── MongoDB collection helpers (replaces the old raw-SQL call sites) ─────────
def _stock():
    return col("wa_stock")


def _orders():
    return col("wa_orders")


def _order_totals(match=None):
    """Returns (count, revenue) for wa_orders matching `match`."""
    pipeline = []
    if match:
        pipeline.append({"$match": match})
    pipeline.append({"$group": {"_id": None, "cnt": {"$sum": 1},
                                "rev": {"$sum": "$price"}}})
    for g in _orders().aggregate(pipeline):
        return int(g.get("cnt") or 0), int(g.get("rev") or 0)
    return 0, 0

# conversation states
WA_SINGLE = 9101
WA_BULK   = 9102
WA_PRICE  = 9103
WA_DELETE = 9104
WA_CODE   = 9105


def _is_admin(uid):
    try:
        from bot import is_admin
        return is_admin(uid)
    except Exception:
        return uid in wm._admin_ids()


async def _reply(update, text, rows):
    kb = InlineKeyboardMarkup([[InlineKeyboardButton(
        b["text"], callback_data=b.get("callback_data", "noop"), url=b.get("url"))
        for b in row] for row in rows])
    await update.message.reply_html(EC.apply_premium_emoji(text), reply_markup=kb)


def _back_rows():
    return [[_btn("Back to WA Panel", "wa_panel", style="primary")],
            [_btn("Admin Panel", "admin", style="danger")]]


def _cancel_rows():
    return [[_btn("Cancel", "wa_panel", style="danger")]]


# ── panel home ───────────────────────────────────────────────────────────────
async def wa_panel_cb(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id):
        return await q.answer("Access denied.", show_alert=True)
    await q.answer()
    await _panel(q)


async def _panel(q):
    stock = _stock().count_documents({"available": 1})
    held = _stock().count_documents({"available": 0})
    countries = len(_stock().distinct("country_name", {"available": 1}))
    pending = _orders().count_documents({"status": "pending"})
    sold, revenue = _order_totals({"status": "delivered"})
    sales_on = wm.wa_sales_enabled()
    sales_status = "ON — users can buy" if sales_on else "OFF — purchases blocked"
    toggle_label = "Turn OFF WhatsApp Sales" if sales_on else "Turn ON WhatsApp Sales"

    text = ("<b>WA PANEL — WhatsApp Section</b>\n"
            "────────────────────────────\n\n"
            f"🟢 <b>Purchase status:</b> {sales_status}\n"
            f"📦 <b>Available stock:</b> {stock} across {countries} country/countries\n"
            f"🔒 <b>Reserved:</b> {held}\n"
            f"⏳ <b>Pending orders:</b> {pending}\n"
            f"✅ <b>Delivered orders:</b> {sold}\n"
            f"💰 <b>Total revenue:</b> ₹{revenue}\n\n"
            "Use the controls below to manage inventory, pricing and live "
            "order delivery.")
    rows = [
        [_btn("Add Single", "wap_single", style="success"),
         _btn("Add Bulk", "wap_bulk", style="success")],
        [_btn("Manage Stock", "wap_stock", style="primary"),
         _btn("Set Pricing", "wap_price", style="primary")],
        [_btn("Pending Orders", "wap_pending", style="primary"),
         _btn("Statistics", "wap_stats", style="primary")],
        [_btn(toggle_label, "wap_toggle", style="danger" if sales_on else "success")],
        [_btn("Admin Panel", "admin", style="danger")],
    ]
    await _se(q, text, rows)


async def toggle_sales_cb(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id):
        return await q.answer("Access denied.", show_alert=True)
    new_state = not wm.wa_sales_enabled()
    wm.set_wa_sales_enabled(new_state)
    await q.answer(
        "WhatsApp purchases enabled." if new_state
        else "WhatsApp purchases disabled.",
        show_alert=True,
    )
    await _panel(q)


# ── add single ───────────────────────────────────────────────────────────────
async def single_start(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id):
        return await q.answer("Access denied.", show_alert=True)
    await q.answer()
    await _se(q,
        "<b>Add Single Number</b>\n"
        "────────────────────────────\n\n"
        "Send the entry in the following format:\n\n"
        "<code>phone|price|category|2fa|note</code>\n\n"
        "<b>Example</b>\n"
        "<code>919812345678|120|Premium|None|Fresh number</code>\n\n"
        "Only <b>phone</b> and <b>price</b> are mandatory; the remaining "
        "fields are optional.\n\n"
        "<i>Send /cancel to abort.</i>", _cancel_rows())
    return WA_SINGLE


def _add_row(line):
    parts = [p.strip() for p in line.split("|")]
    if len(parts) < 2:
        return None, "missing price"
    phone = parts[0].lstrip("+").replace(" ", "")
    if not phone.isdigit():
        return None, "invalid phone"
    try:
        price = int(float(parts[1]))
    except Exception:
        return None, "invalid price"
    category = parts[2] if len(parts) > 2 and parts[2] else "Standard"
    twofa = parts[3] if len(parts) > 3 and parts[3] else "None"
    note = parts[4] if len(parts) > 4 else ""
    name, icon = om.country_from_phone(phone)
    _stock().update_one(
        {"phone": phone},
        {"$set": {"country_name": name, "country_icon": icon,
                  "category": category, "price": int(price), "twofa": twofa,
                  "note": note, "available": 1},
         "$setOnInsert": {"id": next_id("wa_stock"), "added_at": now_iso()}},
        upsert=True)
    return (phone, name, price, category), None


async def single_save(update, ctx):
    res, err = _add_row(update.message.text.strip())
    if err:
        await _reply(update,
            f"<b>Invalid entry</b> — {err}.\n\nExpected format:\n"
            "<code>phone|price|category|2fa|note</code>\n\nPlease try again.",
            _cancel_rows())
        return WA_SINGLE
    phone, name, price, category = res
    await _reply(update,
        "<b>Number Added</b>\n"
        "────────────────────────────\n\n"
        f"📱 <b>Number:</b> <code>+{phone}</code>\n"
        f"🌍 <b>Country:</b> {name}\n"
        f"📦 <b>Package:</b> {category}\n"
        f"💰 <b>Price:</b> ₹{price}\n\n"
        "The number is now live in the WhatsApp store.",
        [[_btn("Add Another", "wap_single", style="success")],
         [_btn("Manage Stock", "wap_stock", style="primary")],
         [_btn("Back to WA Panel", "wa_panel", style="danger")]])
    return ConversationHandler.END


# ── add bulk ─────────────────────────────────────────────────────────────────
async def bulk_start(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id):
        return await q.answer("Access denied.", show_alert=True)
    await q.answer()
    await _se(q,
        "<b>Add Bulk Numbers</b>\n"
        "────────────────────────────\n\n"
        "Paste one entry per line using the format:\n\n"
        "<code>phone|price|category|2fa|note</code>\n\n"
        "<b>Example</b>\n"
        "<code>919812345678|120|Premium|None|Fresh\n"
        "628123456789|150|Standard|123456|Aged</code>\n\n"
        "Duplicate numbers are updated instead of duplicated.\n\n"
        "<i>Send /cancel to abort.</i>", _cancel_rows())
    return WA_BULK


async def bulk_save(update, ctx):
    added, failed = 0, []
    for line in update.message.text.splitlines():
        line = line.strip()
        if not line:
            continue
        res, err = _add_row(line)
        if err:
            failed.append(f"{line} — {err}")
        else:
            added += 1
    text = ("<b>Bulk Import Complete</b>\n"
            "────────────────────────────\n\n"
            f"✅ <b>Imported:</b> {added}\n"
            f"❌ <b>Rejected:</b> {len(failed)}")
    if failed:
        text += "\n\n<b>Rejected lines</b>\n" + "\n".join(
            f"• <code>{f}</code>" for f in failed[:10])
    await _reply(update, text,
        [[_btn("Add More", "wap_bulk", style="success")],
         [_btn("Manage Stock", "wap_stock", style="primary")],
         [_btn("Back to WA Panel", "wa_panel", style="danger")]])
    return ConversationHandler.END


# ── manage stock ─────────────────────────────────────────────────────────────
async def stock_cb(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id):
        return await q.answer("Access denied.", show_alert=True)
    await q.answer()
    rows = [(g["_id"], g.get("cnt") or 0, g.get("lo") or 0, g.get("hi") or 0)
            for g in _stock().aggregate([
                {"$match": {"available": 1}},
                {"$group": {"_id": "$country_name", "cnt": {"$sum": 1},
                            "lo": {"$min": "$price"}, "hi": {"$max": "$price"}}},
                {"$sort": {"_id": 1}},
            ])]
    if not rows:
        return await _se(q,
            "<b>Manage Stock</b>\n\nThere are no available numbers in the "
            "WhatsApp inventory.",
            [[_btn("Add Single", "wap_single", style="success"),
              _btn("Add Bulk", "wap_bulk", style="success")]] + _back_rows())
    btns = [[_btn(f"{name}  •  {cnt}  •  ₹{lo}-₹{hi}", f"wap_c|{name}",
                  style="primary", emoji_id=EC.get_country_emoji(name) or None)]
            for name, cnt, lo, hi in rows]
    btns.append([_btn("Delete Number", "wap_del", style="danger")])
    btns += _back_rows()
    await _se(q,
        "<b>Manage Stock</b>\n"
        "────────────────────────────\n\n"
        "Select a country to review its numbers, pricing and availability.", btns)


async def stock_country_cb(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id):
        return await q.answer("Access denied.", show_alert=True)
    await q.answer()
    country = q.data.split("|", 1)[1]
    rows = [(d.get("phone"), d.get("price"), d.get("category") or "Standard",
             d.get("twofa") or "None")
            for d in _stock().find({"country_name": country, "available": 1},
                                   {"phone": 1, "price": 1, "category": 1, "twofa": 1})
                             .sort("price", 1).limit(30)]
    lines = [f"<b>{country} — Inventory</b>", "────────────────────────────", ""]
    for phone, price, category, twofa in rows:
        lines.append(f"📱 <code>+{phone}</code> • ₹{price} • {category} • "
                     f"2FA: <code>{twofa}</code>")
    if not rows:
        lines.append("<i>No available numbers for this country.</i>")
    lines.append("\n<i>Showing up to 30 entries.</i>")
    btns = [
        [_btn("Update Country Price", f"wap_pc|{country}", style="primary")],
        [_btn("Delete Number", "wap_del", style="danger"),
         _btn("Wipe Country", f"wap_wipe|{country}", style="danger")],
        [_btn("Back to Stock", "wap_stock", style="primary")],
        [_btn("Back to WA Panel", "wa_panel", style="danger")],
    ]
    await _se(q, "\n".join(lines), btns)


async def wipe_cb(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id):
        return await q.answer("Access denied.", show_alert=True)
    await q.answer()
    country = q.data.split("|", 1)[1]
    removed = _stock().delete_many(
        {"country_name": country, "available": 1}).deleted_count
    await _se(q,
        "<b>Country Wiped</b>\n"
        "────────────────────────────\n\n"
        f"🌍 <b>Country:</b> {country}\n"
        f"🗑️ <b>Removed:</b> {removed} number(s)\n\n"
        "Reserved numbers belonging to open orders were not affected.",
        [[_btn("Back to Stock", "wap_stock", style="primary")]] + _back_rows())


async def delete_start(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id):
        return await q.answer("Access denied.", show_alert=True)
    await q.answer()
    await _se(q,
        "<b>Delete Number</b>\n"
        "────────────────────────────\n\n"
        "Send the number you want to remove, for example:\n"
        "<code>919812345678</code>\n\n"
        "Multiple numbers can be sent, one per line.\n\n"
        "<i>Send /cancel to abort.</i>", _cancel_rows())
    return WA_DELETE


async def delete_save(update, ctx):
    removed = 0
    for line in update.message.text.splitlines():
        phone = line.strip().lstrip("+").replace(" ", "")
        if not phone:
            continue
        removed += _stock().delete_many({"phone": phone}).deleted_count
    await _reply(update,
        "<b>Deletion Complete</b>\n"
        "────────────────────────────\n\n"
        f"🗑️ <b>Removed:</b> {removed} number(s)",
        [[_btn("Delete More", "wap_del", style="danger")],
         [_btn("Manage Stock", "wap_stock", style="primary")],
         [_btn("Back to WA Panel", "wa_panel", style="danger")]])
    return ConversationHandler.END


# ── pricing ──────────────────────────────────────────────────────────────────
async def price_start(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id):
        return await q.answer("Access denied.", show_alert=True)
    await q.answer()
    country = q.data.split("|", 1)[1] if "|" in q.data else ""
    ctx.user_data["wa_price_country"] = country
    if country:
        prompt = (f"<b>Update Pricing — {country}</b>\n"
                  "────────────────────────────\n\n"
                  "Send the new price in ₹ for every available number of this "
                  "country, for example <code>150</code>.")
    else:
        prompt = ("<b>Update Pricing</b>\n"
                  "────────────────────────────\n\n"
                  "Send the country and the new price in the format:\n\n"
                  "<code>country|price</code>\n\n"
                  "<b>Example</b>\n<code>India|150</code>")
    await _se(q, prompt + "\n\n<i>Send /cancel to abort.</i>", _cancel_rows())
    return WA_PRICE


async def price_save(update, ctx):
    text = update.message.text.strip()
    country = ctx.user_data.pop("wa_price_country", "")
    if country:
        raw_price = text
    else:
        if "|" not in text:
            await _reply(update,
                "<b>Invalid input</b>\n\nExpected <code>country|price</code>.",
                _cancel_rows())
            return WA_PRICE
        country, raw_price = [p.strip() for p in text.split("|", 1)]
    try:
        price = int(float(raw_price))
    except Exception:
        await _reply(update, "<b>Invalid price</b>\n\nSend a number, e.g. "
                             "<code>150</code>.", _cancel_rows())
        return WA_PRICE
    updated = _stock().update_many(
        {"country_name": country, "available": 1},
        {"$set": {"price": int(price)}}).modified_count
    await _reply(update,
        "<b>Pricing Updated</b>\n"
        "────────────────────────────\n\n"
        f"🌍 <b>Country:</b> {country}\n"
        f"💰 <b>New price:</b> ₹{price}\n"
        f"📦 <b>Numbers updated:</b> {updated}",
        [[_btn("Manage Stock", "wap_stock", style="primary")],
         [_btn("Back to WA Panel", "wa_panel", style="danger")]])
    return ConversationHandler.END


# ── pending orders ───────────────────────────────────────────────────────────
async def pending_cb(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id):
        return await q.answer("Access denied.", show_alert=True)
    await q.answer()
    rows = [(d.get("id"), d.get("user_id"), d.get("country"), d.get("phone"),
             d.get("price"))
            for d in _orders().find({"status": "pending"},
                                    {"id": 1, "user_id": 1, "country": 1,
                                     "phone": 1, "price": 1})
                              .sort("id", -1).limit(15)]
    if not rows:
        return await _se(q,
            "<b>Pending Orders</b>\n\nThere are no orders awaiting a login code.",
            _back_rows())
    lines = ["<b>Pending Orders</b>", "────────────────────────────", ""]
    btns = []
    for oid, uid, country, phone, price in rows:
        lines.append(f"🧾 <b>#{oid}</b> — {country} • <code>+{phone}</code> • "
                     f"₹{price} • buyer <code>{uid}</code>")
        btns.append([_btn(f"Send Code — Order #{oid}", f"wap_send|{oid}", style="success"),
                     _btn("Refund", f"wap_refund|{oid}", style="danger")])
    btns += _back_rows()
    await _se(q, "\n".join(lines), btns)


async def send_code_start(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id):
        return await q.answer("Access denied.", show_alert=True)
    await q.answer()
    oid = int(q.data.split("|")[1])
    o = wm.get_order(oid)
    if not o:
        await _se(q, "<b>Order not found.</b>", _back_rows())
        return ConversationHandler.END
    _, uid, country, price, phone, otp, status, created = o
    if status != "pending":
        await _se(q, f"<b>Order #{oid}</b> is already marked as "
                     f"<b>{status}</b>.", _back_rows())
        return ConversationHandler.END
    ctx.user_data["wa_send_oid"] = oid
    await _se(q,
        f"<b>Deliver Login Code — Order #{oid}</b>\n"
        "────────────────────────────\n\n"
        f"👤 <b>Buyer:</b> <code>{uid}</code>\n"
        f"🌍 <b>Country:</b> {country}\n"
        f"📱 <b>Number:</b> <code>+{phone}</code>\n"
        f"💰 <b>Amount:</b> ₹{price}\n\n"
        "Send the login code now (digits only). It will be delivered to the "
        "buyer immediately and the order will be closed.\n\n"
        "<i>Send /cancel to abort.</i>", _cancel_rows())
    return WA_CODE


async def send_code_save(update, ctx):
    oid = ctx.user_data.pop("wa_send_oid", None)
    code = update.message.text.strip()
    if not oid:
        await _reply(update, "<b>Session expired.</b> Please reopen the order.",
                     _back_rows())
        return ConversationHandler.END
    ok, msg = await wm.deliver_code(ctx.application, oid, code)
    if not ok:
        await _reply(update, f"<b>Delivery failed</b> — {msg}", _back_rows())
        return ConversationHandler.END
    await _reply(update,
        "<b>Login Code Delivered</b>\n"
        "────────────────────────────\n\n"
        f"🧾 <b>Order:</b> #{oid}\n"
        f"🔢 <b>Code sent:</b> <code>{code}</code>\n\n"
        "The buyer has been notified and the order is now closed.",
        [[_btn("Pending Orders", "wap_pending", style="primary")],
         [_btn("Back to WA Panel", "wa_panel", style="danger")]])
    return ConversationHandler.END


async def refund_cb(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id):
        return await q.answer("Access denied.", show_alert=True)
    await q.answer()
    oid = int(q.data.split("|")[1])
    o = wm.get_order(oid)
    if not o:
        return await _se(q, "<b>Order not found.</b>", _back_rows())
    _, uid, country, price, phone, otp, status, created = o
    if status != "pending":
        return await _se(q, f"<b>Order #{oid}</b> is already <b>{status}</b>.",
                         _back_rows())
    if not wm.refund_order(oid, uid, price, phone):
        return await q.answer("This order was already processed.", show_alert=True)
    try:
        await wm._send(ctx.application.bot, uid,
            f"<b>Order #{oid} — Cancelled</b>\n"
            "────────────────────────────\n\n"
            f"📱 <b>Number:</b> <code>+{phone}</code>\n"
            f"💰 <b>Refunded:</b> ₹{price}\n\n"
            "We were unable to complete this delivery, so the order was "
            "cancelled and your wallet has been credited in full. "
            "We apologise for the inconvenience.",
            [[_btn("Buy Another", "wa_buy", style="success")],
             [_btn("Support", "support", style="primary"),
              _btn("Main Menu", "home", style="danger")]])
    except Exception as exc:
        logger.debug(f"refund notice failed: {exc}")
    await _se(q,
        "<b>Order Refunded</b>\n"
        "────────────────────────────\n\n"
        f"🧾 <b>Order:</b> #{oid}\n"
        f"💰 <b>Refunded:</b> ₹{price}\n"
        f"📦 <b>Number returned to stock:</b> <code>+{phone}</code>",
        [[_btn("Pending Orders", "wap_pending", style="primary")]] + _back_rows())


# ── statistics ───────────────────────────────────────────────────────────────
async def stats_cb(update, ctx):
    q = update.callback_query
    if not _is_admin(q.from_user.id):
        return await q.answer("Access denied.", show_alert=True)
    await q.answer()
    from datetime import timedelta
    day = datetime.now().strftime("%Y-%m-%d")
    week_from = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    today = _order_totals({"status": "delivered",
                           "created_at": {"$regex": f"^{day}"}})
    week = _order_totals({"status": "delivered",
                          "created_at": {"$gte": week_from}})
    total = _order_totals({"status": "delivered"})
    refunded = _orders().count_documents(
        {"status": {"$in": ["refunded", "cancelled"]}})
    stock = _stock().count_documents({"available": 1})
    await _se(q,
        "<b>WhatsApp Statistics</b>\n"
        "────────────────────────────\n\n"
        f"📅 <b>Today:</b> {today[0]} sale(s) • ₹{today[1]}\n"
        f"🗓️ <b>Last 7 days:</b> {week[0]} sale(s) • ₹{week[1]}\n"
        f"📈 <b>All time:</b> {total[0]} sale(s) • ₹{total[1]}\n"
        f"↩️ <b>Refunded orders:</b> {refunded}\n"
        f"📦 <b>Numbers in stock:</b> {stock}",
        [[_btn("Pending Orders", "wap_pending", style="primary"),
          _btn("Manage Stock", "wap_stock", style="primary")]] + _back_rows())


# ── cancel ───────────────────────────────────────────────────────────────────
async def cancel(update, ctx):
    ctx.user_data.pop("wa_send_oid", None)
    ctx.user_data.pop("wa_price_country", None)
    if update.message:
        await _reply(update, "<b>Action cancelled.</b>",
                     [[_btn("Back to WA Panel", "wa_panel", style="primary")]])
    return ConversationHandler.END


async def cancel_cb(update, ctx):
    q = update.callback_query
    await q.answer("Cancelled.")
    ctx.user_data.pop("wa_send_oid", None)
    ctx.user_data.pop("wa_price_country", None)
    await _panel(q)
    return ConversationHandler.END


# ── registration ─────────────────────────────────────────────────────────────
def register(app: Application):
    text_filter = filters.TEXT & ~filters.COMMAND
    fallbacks = [CommandHandler("cancel", cancel),
                 CallbackQueryHandler(cancel_cb, pattern=r"^wa_panel$")]

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(single_start, pattern=r"^wap_single$")],
        states={WA_SINGLE: [MessageHandler(text_filter, single_save)]},
        fallbacks=fallbacks, per_message=False))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(bulk_start, pattern=r"^wap_bulk$")],
        states={WA_BULK: [MessageHandler(text_filter, bulk_save)]},
        fallbacks=fallbacks, per_message=False))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(price_start, pattern=r"^wap_price$"),
                      CallbackQueryHandler(price_start, pattern=r"^wap_pc\|")],
        states={WA_PRICE: [MessageHandler(text_filter, price_save)]},
        fallbacks=fallbacks, per_message=False))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(delete_start, pattern=r"^wap_del$")],
        states={WA_DELETE: [MessageHandler(text_filter, delete_save)]},
        fallbacks=fallbacks, per_message=False))

    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(send_code_start, pattern=r"^wap_send\|")],
        states={WA_CODE: [MessageHandler(text_filter, send_code_save)]},
        fallbacks=fallbacks, per_message=False))

    for pattern, fn in [
        (r"^wa_panel$",     wa_panel_cb),
        (r"^wap_toggle$",   toggle_sales_cb),
        (r"^wap_stock$",    stock_cb),
        (r"^wap_pending$",  pending_cb),
        (r"^wap_stats$",    stats_cb),
        (r"^wap_c\|",       stock_country_cb),
        (r"^wap_wipe\|",    wipe_cb),
        (r"^wap_refund\|",  refund_cb),
    ]:
        app.add_handler(CallbackQueryHandler(fn, pattern=pattern))
    logger.info("wa_admin (WA Panel) handlers registered")
