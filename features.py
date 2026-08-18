"""
NEXUS STORE BOT — Features Module
Coupons, low-stock alerts, renewal reminders, balance gifting,
credential reports/refunds, reseller program, and CSV export.
"""

import csv
import io
from datetime import datetime, timedelta

import config
import database as db
from mongo_client import col as _mcol
from lang import T

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ConversationHandler, ContextTypes, MessageHandler, CommandHandler, filters
)

# ── Conversation States ────────────────────────────────────────────────────────
(
    FEAT_COUPON_CODE, FEAT_COUPON_DISC, FEAT_COUPON_USES,
    FEAT_GIFT_UID, FEAT_GIFT_AMT,
    FEAT_WD_AMT,
) = range(100, 106)


def is_admin(uid): return uid in config.ADMIN_IDS or uid in db.get_extra_admins()
def fmt(a): return f"${a:.2f} USDT"
def sep(): return "━━━━━━━━━━━━━━━━━━━━━━"

def reseller_settings():
    disc   = db.get_setting_int("reseller_discount",  config.RESELLER_DISCOUNT_PERCENT)
    comm   = db.get_setting_int("reseller_commission", config.RESELLER_OWNER_COMMISSION)
    delay  = db.get_setting_int("reseller_credit_delay_hours", config.RESELLER_CREDIT_DELAY_HOURS)
    return {"discount": disc, "commission": comm, "delay": delay}

def reseller_price(base: float) -> float:
    s = reseller_settings()
    return round(base * (1 - s["discount"] / 100), 6)


# ═══════════════════════════════════════════════════════════════════════════
#  1. COUPONS
# ═══════════════════════════════════════════════════════════════════════════

async def adm_coupons_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    coupons = db.get_coupons()
    lines = [f"🎟️ <b>COUPONS</b>\n{sep()}\n"]
    for c in coupons:
        status = "🟢" if c["is_active"] else "🔴"
        lines.append(f"{status} <code>{c['code']}</code> — {c['discount']}% OFF — {c['used_count']}/{c['max_uses']} used")
    text = "\n".join(lines) if coupons else f"🎟️ <b>COUPONS</b>\n\nNo coupons yet."
    btns = [[InlineKeyboardButton(f"{'🟢' if c['is_active'] else '🔴'} {c['code']}", callback_data=f"adm_tog_coupon_{c['id']}"),
             InlineKeyboardButton("🗑️", callback_data=f"adm_del_coupon_{c['id']}")] for c in coupons]
    btns.append([InlineKeyboardButton("➕ Add Coupon", callback_data="adm_add_coupon")])
    btns.append([InlineKeyboardButton("« Admin", callback_data="admin")])
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(btns), parse_mode="HTML")

async def adm_add_coupon_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    await q.edit_message_text("🎟️ <b>Add Coupon</b>\n\nSend coupon code (e.g. SAVE20):\n\n/cancel to abort",
                               parse_mode="HTML")
    return FEAT_COUPON_CODE

async def feat_coupon_code(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    ctx.user_data["coup_code"] = update.message.text.strip().upper()
    await update.message.reply_text("Discount %? (e.g. 10 for 10% off):"); return FEAT_COUPON_DISC

async def feat_coupon_disc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        d = int(update.message.text.strip())
        if not 1 <= d <= 99: raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Enter a number 1-99:"); return FEAT_COUPON_DISC
    ctx.user_data["coup_disc"] = d
    await update.message.reply_text("Max uses? (e.g. 100):"); return FEAT_COUPON_USES

async def feat_coupon_uses(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        u = int(update.message.text.strip())
        if u < 1: raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Enter a positive number:"); return FEAT_COUPON_USES
    d = ctx.user_data
    db.add_coupon(d["coup_code"], d["coup_disc"], u)
    await update.message.reply_html(f"✅ Coupon <code>{d['coup_code']}</code> created ({d['coup_disc']}% OFF, {u} uses)!",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎟️ Coupons", callback_data="adm_coupons")]]))
    return ConversationHandler.END

async def adm_toggle_coupon_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    cid = int(q.data.split("_")[3])
    db.toggle_coupon(cid)
    await adm_coupons_cb(update, ctx)

async def adm_delete_coupon_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    cid = int(q.data.split("_")[3])
    db.delete_coupon(cid)
    await adm_coupons_cb(update, ctx)


# ═══════════════════════════════════════════════════════════════════════════
#  2. LOW STOCK ALERT
# ═══════════════════════════════════════════════════════════════════════════

async def check_low_stock(bot, product_id: int, product_name: str, emoji: str):
    """Called after stock changes; sends alert if below threshold."""
    threshold = db.get_setting_int("low_stock_threshold", config.LOW_STOCK_THRESHOLD)
    count = db.get_stock_count(product_id)
    if 0 < count <= threshold:
        log_cid = getattr(config, "LOG_CHANNEL_ID", None)
        if log_cid:
            try:
                await bot.send_message(log_cid,
                    f"⚠️ <b>Low Stock Alert</b>\n{sep()}\n"
                    f"{emoji} <b>{product_name}</b>\n"
                    f"📦 Only <b>{count}</b> item(s) remaining!",
                    parse_mode="HTML")
            except Exception:
                pass


# ═══════════════════════════════════════════════════════════════════════════
#  3. RENEWAL REMINDER JOB
# ═══════════════════════════════════════════════════════════════════════════

async def renewal_reminder_job(context: ContextTypes.DEFAULT_TYPE):
    """Hourly job: remind users whose subscription may be expiring."""
    days = db.get_setting_int("renewal_reminder_days", config.RENEWAL_REMINDER_DAYS)
    if days <= 0:
        return
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    rows = list(_mcol("orders").find({
        "created_at": {"$lte": cutoff},
        "status": "completed",
        "reminder_sent": {"$in": [0, None, False]},
        "refunded": {"$in": [0, None, False]},
    }))
    for row in rows:
        row = dict(row)
        try:
            lang = db.get_user_language(row["user_id"]) or "en"
            await context.bot.send_message(
                row["user_id"],
                T(lang,"renewal_reminder", product_name=row['product_name']),
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(T(lang,"btn_shop_now"), callback_data="shop")]]),
                parse_mode="HTML"
            )
            _mcol("orders").update_one({"id": row["id"]},
                                       {"$set": {"reminder_sent": 1}})
        except Exception:
            pass


# ═══════════════════════════════════════════════════════════════════════════
#  4. BALANCE GIFTING
# ═══════════════════════════════════════════════════════════════════════════

async def gift_start_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    await q.edit_message_text("🎁 <b>Gift Balance</b>\n\nSend user ID to gift balance to:\n\n/cancel to abort",
                               parse_mode="HTML")
    return FEAT_GIFT_UID

async def feat_gift_uid(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    try:
        uid = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid user ID."); return FEAT_GIFT_UID
    u = db.get_user(uid)
    if not u:
        await update.message.reply_text("❌ User not found."); return FEAT_GIFT_UID
    ctx.user_data["gift_uid"] = uid
    name = u.get("full_name") or u.get("username") or str(uid)
    await update.message.reply_text(f"👤 Gifting to: <b>{name}</b>\n\nHow much USDT to gift?", parse_mode="HTML")
    return FEAT_GIFT_AMT

async def feat_gift_amt(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id): return ConversationHandler.END
    try:
        amt = float(update.message.text.strip())
        if amt <= 0: raise ValueError
    except ValueError:
        await update.message.reply_text("❌ Enter a positive number:"); return FEAT_GIFT_AMT
    uid = ctx.user_data["gift_uid"]
    db.update_balance(uid, amt)
    u = db.get_user(uid)
    try:
        uid_lang = db.get_user_language(uid) or "en"
        await update.effective_message.bot.send_message(uid,
            T(uid_lang,"admin_gift_received", amount=fmt(amt), balance=fmt(u['balance'])),
            parse_mode="HTML")
    except Exception:
        pass
    await update.message.reply_html(
        f"✅ Gifted <b>{fmt(amt)}</b> to user <code>{uid}</code>!",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Admin", callback_data="admin")]]))
    return ConversationHandler.END


# ═══════════════════════════════════════════════════════════════════════════
#  5. REFUND & CREDENTIAL REPORT
# ═══════════════════════════════════════════════════════════════════════════

async def adm_refund_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    oid = int(q.data.split("_")[2])
    result = db.refund_order(oid)
    if not result:
        await q.answer("Already refunded or not found.", show_alert=True); return
    await q.edit_message_text(
        f"✅ <b>Order #{oid:04d} Refunded</b>\n\n"
        f"💰 {fmt(result['amount_usdt'])} returned to user <code>{result['user_id']}</code>.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⚙️ Admin", callback_data="admin")]])
    )
    try:
        lang_u = db.get_user_language(result["user_id"]) or "en"
        await ctx.bot.send_message(result["user_id"],
            T(lang_u,"admin_refund_processed", oid=oid, amount=fmt(result['amount_usdt'])),
            parse_mode="HTML")
    except Exception:
        pass

async def adm_dismiss_report_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer("Report dismissed.")
    await q.edit_message_text("🗂️ Report dismissed.", parse_mode="HTML")


# ═══════════════════════════════════════════════════════════════════════════
#  6. RESELLER PROGRAM
# ═══════════════════════════════════════════════════════════════════════════

async def reseller_panel_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid  = q.from_user.id
    lang = db.get_user_language(uid) or "en"
    if not db.is_reseller(uid):
        await q.edit_message_text(
            T(lang,"reseller_not_approved"),
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(T(lang,"btn_home"), callback_data="home")]]))
        return
    s = reseller_settings()
    summary = db.get_reseller_balance_summary(uid)
    await q.edit_message_text(
        T(lang,"reseller_panel",
          discount=s['discount'],
          available=fmt(summary['available']),
          pending=fmt(summary['pending']),
          withdrawn=fmt(summary['withdrawn'])),
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton(T(lang,"btn_request_withdrawal"), callback_data="reseller_wd")],
            [InlineKeyboardButton(T(lang,"btn_home"), callback_data="home")]
        ])
    )

async def adm_reseller_menu_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    resellers = db.get_resellers()
    s = reseller_settings()
    text = (f"🏪 <b>RESELLERS</b>\n{sep()}\n"
            f"Discount: {s['discount']}% | Commission: {s['commission']}% | Delay: {s['delay']}h\n\n")
    if resellers:
        for r in resellers:
            name = r.get("full_name") or r.get("username") or str(r["user_id"])
            text += f"👤 {name} (<code>{r['user_id']}</code>)\n"
    else:
        text += "No resellers yet."
    btns = []
    for r in resellers:
        btns.append([InlineKeyboardButton(f"❌ Revoke {r.get('username') or r['user_id']}",
                                          callback_data=f"adm_reseller_revoke_{r['user_id']}")])
    btns.append([InlineKeyboardButton("« Admin", callback_data="admin")])
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(btns), parse_mode="HTML")

async def adm_reseller_revoke_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    uid = int(q.data.split("_")[3])
    db.revoke_reseller(uid)
    await adm_reseller_menu_cb(update, ctx)


# ═══════════════════════════════════════════════════════════════════════════
#  7. RESELLER MATURATION JOB
# ═══════════════════════════════════════════════════════════════════════════

async def reseller_maturation_job(context: ContextTypes.DEFAULT_TYPE):
    db.mature_reseller_earnings()


# ═══════════════════════════════════════════════════════════════════════════
#  8. WITHDRAW
# ═══════════════════════════════════════════════════════════════════════════

async def adm_wd_list_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    reqs = db.get_pending_withdraw_requests()
    if not reqs:
        await q.edit_message_text("💸 No pending withdrawal requests.",
                                   reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Admin", callback_data="admin")]]),
                                   parse_mode="HTML"); return
    btns = []
    for r in reqs:
        btns.append([
            InlineKeyboardButton(f"✅ Pay #{r['id']} {fmt(r['amount'])}", callback_data=f"adm_wd_paid_{r['id']}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"adm_wd_reject_{r['id']}")
        ])
    btns.append([InlineKeyboardButton("« Admin", callback_data="admin")])
    await q.edit_message_text("💸 <b>Pending Withdrawals</b>", reply_markup=InlineKeyboardMarkup(btns), parse_mode="HTML")

async def adm_wd_process_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    parts = q.data.split("_")
    wid   = int(parts[3])
    approve = parts[2] == "paid"
    result = db.process_withdraw_request(wid, approve)
    if not result:
        await q.answer("Not found.", show_alert=True); return
    status = "✅ Approved & Paid" if approve else "❌ Rejected"
    await q.edit_message_text(f"💸 Withdrawal #{wid}: <b>{status}</b>", parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("💸 Withdrawals", callback_data="adm_wd_list")]]))
    try:
        _ulang = db.get_user_language(result["user_id"]) or "en"
        msg = (T(_ulang,"wd_approved_msg", amount=fmt(result['amount']))
               if approve else T(_ulang,"wd_rejected_msg", amount=fmt(result['amount'])))
        await ctx.bot.send_message(result["user_id"], msg, parse_mode="HTML")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════════
#  9. CSV EXPORT
# ═══════════════════════════════════════════════════════════════════════════

EXPORT_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("📅 Today", callback_data="adm_export_today"),
     InlineKeyboardButton("📊 All Time", callback_data="adm_export_all")],
])

async def adm_export_menu_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer()
    await q.edit_message_text("📤 <b>Export Orders</b>\n\nChoose a range:", parse_mode="HTML", reply_markup=EXPORT_KB)

async def adm_export_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    if not is_admin(q.from_user.id): return
    await q.answer("Generating CSV…")
    today_only = q.data == "adm_export_today"
    orders = db.get_today_orders(limit=100000) if today_only else db.get_all_orders(limit=1000000)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["order_id","user_id","username","product_name","amount_usdt","status",
                      "coupon_code","is_reseller_sale","refunded","created_at"])
    for o in orders:
        writer.writerow([o["id"],o["user_id"],o.get("username",""),o["product_name"],o["amount_usdt"],
                         o.get("status","completed"),o.get("coupon_code") or "",o.get("is_reseller_sale",0),
                         o.get("refunded",0),o["created_at"]])
    data  = buf.getvalue().encode("utf-8")
    label = "today" if today_only else "all_time"
    fname = f"orders_{label}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    await ctx.bot.send_document(
        chat_id=q.from_user.id,
        document=io.BytesIO(data),
        filename=fname,
        caption=f"📤 {len(orders)} order(s) exported ({'today' if today_only else 'all time'})."
    )
