"""
╔══════════════════════════════════════════════════════════════════════════════╗
║           BASS TG STORE — PREMIUM EMOJI CONFIG FILE                        ║
║  Fill in your Premium Custom Emoji IDs below.                              ║
║                                                                            ║
║  How to get Premium Emoji IDs:                                             ║
║    1. Forward a message with premium emojis to @getidsbot                  ║
║    2. Or use Telegram Desktop → right-click emoji → Copy File ID           ║
║    3. Or use @userinfobot / @get_sticker_id_bot                           ║
║                                                                            ║
║  Leave any field as "" to skip that emoji (button shows text only).        ║
║  You must have a Telegram Premium account or channel to USE these emojis.  ║
╚══════════════════════════════════════════════════════════════════════════════╝
"""

# ──────────────────────────────────────────────────────────────────────────────
#  MAIN MENU BUTTONS
# ──────────────────────────────────────────────────────────────────────────────

E_SHOP       = "5278702045883292456"   # 🛍️  Shop button (main menu)
E_DEPOSIT    = "5445353829304387411"   # 💳  Deposit button (main menu)
E_PROFILE    = "6203999513686837822"   # 👤  Profile button (main menu)
E_SUPPORT    = "6093701420630938474"   # 🎧  Support / Help button (main menu)
E_REFERRAL   = "6093780439439249308"   # 🎁  Referral button (main menu)
E_CART       = "5312361253610475399"   # 🛒  Cart button (main menu)
E_LANGUAGE   = "5042186567783809934"   # 🌐  Language selector button
E_ADMIN      = "5215327492738392838"   # ⚙️  Admin panel button (only shown to admins)

# ──────────────────────────────────────────────────────────────────────────────
#  NAVIGATION (used on many screens)
# ──────────────────────────────────────────────────────────────────────────────

E_HOME       = "5042022053356504092"   # 🏠  Home / Back to main menu
E_BACK       = "5042156073516008537"   # ◀️  Back button (general)
E_CANCEL     = "5040042498634810056"   # ❌  Cancel button

# ──────────────────────────────────────────────────────────────────────────────
#  PUBLIC SALE FEED (broadcasts "Someone just bought…" / "BACK IN STOCK")
# ──────────────────────────────────────────────────────────────────────────────
E_SALE_BAG      = ""   # 🛍️  used in "Someone just bought..." header
E_SALE_FIRE     = ""   # 🔥  used in "BACK IN STOCK" header
E_SALE_PACKAGE  = ""   # 📦  used in restock stock-count line
E_SALE_MONEY    = ""   # 💰  used in restock price line


# ──────────────────────────────────────────────────────────────────────────────
#  SHOP FLOW
# ──────────────────────────────────────────────────────────────────────────────

E_BUY        = "5039844895779455925"   # ✅  Buy Now  (green CTA button)
E_ADD_CART   = "5039891861246838069"   # ➕  Add to Cart button
E_TOP_UP     = "5197434882321567830"   # 💰  Top Up balance (shown when balance too low)
E_CHECKOUT   = "5332455502917949981"   # 🛍️  Checkout / Confirm purchase
E_QTY_PLUS   = "5039891861246838069"   # ➕  Quantity increase
E_QTY_MINUS  = "6307665627481903641"   # ➖  Quantity decrease

# ──────────────────────────────────────────────────────────────────────────────
#  DEPOSIT FLOW
# ──────────────────────────────────────────────────────────────────────────────

E_START_DEP  = "5042200814190330758"   # 🚀  "Start Deposit" button
E_DEP_HIST   = "5197269100878907942"   # 📋  Deposit History button
E_TRC20      = "5039810295522919687"   # 🟡  TRC20 (TRON) network button
E_BEP20      = "5199552030615558774"   # 🟠  BEP20 (BSC) network button
E_PAY        = "5379773896352355687"   # 💛  Binance Pay ID button

# ──────────────────────────────────────────────────────────────────────────────
#  PROFILE
# ──────────────────────────────────────────────────────────────────────────────

E_HISTORY    = "5445353829304387411"   # 📋  Order History button
E_DEPOSIT2   = "4983539296163070766"   # 💳  Deposit button shown on profile page
E_VIP        = "6082505845344571494"   # 👑  VIP badge / VIP section button

# ──────────────────────────────────────────────────────────────────────────────
#  SUPPORT / TICKETS
# ──────────────────────────────────────────────────────────────────────────────

E_NEW_TICKET = "5444856076954520455"   # 🎫  New Ticket button
E_MY_TICKETS = "5033080906403808074"   # 📂  My Tickets button
E_REPLY      = "5197269100878907942"   # 📩  Reply to ticket button

# ──────────────────────────────────────────────────────────────────────────────
#  REFERRAL
# ──────────────────────────────────────────────────────────────────────────────

E_REF_LINK   = "5379742233853451967"   # 🔗  Copy Referral Link button
E_REF_STATS  = "6093382540784046658"   # 📊  Referral Stats button

# ──────────────────────────────────────────────────────────────────────────────
#  ADMIN PANEL  (reuses IDs already defined above/below — no unverified IDs)
# ──────────────────────────────────────────────────────────────────────────────

E_POLICE     = "6080394890393423700"   # 👮  Admins management
E_CATEGORY   = "5240228673738527951"   # 🏷️  Categories
E_PACKAGE    = "5039834781131474002"   # 📦  Products / stock
E_MEGAPHONE  = "6095888417978061469"   # 📢  Broadcast / add channel
E_LOUDSPKR   = "6129492160497589882"   # 📣  Set log channel
E_CALENDAR   = "6244425785986257276"   # 📅  Daily history
E_EXPORT     = "5445355530111437729"   # 📤  Export / download TXT
E_MAGNIFY    = "5397986013681295058"   # 🔍  Search user
E_PEOPLE     = "5453957997418004470"   # 👥  Users list
E_TRASH      = "4956337889593000947"   # 🗑️  Remove channel
E_STORE      = "6143438580732664355"   # 🏪  Resellers
E_ORANGE     = "5239975081689498076"   # 🟠  BEP20
E_YELLOW     = "5273931763146565225"   # 🟡  TRC20
E_SPARKLES   = "6267209144382526972"   # ✨  Bot emoji setting
E_PENCIL     = "5371053145646441722"   # ✏️  Bot name setting
E_WARNING    = "6215486554043846997"   # ⚠️  Low stock threshold
E_MONEYBAG   = "5278467510604160626"   # 💰  Min deposit setting
E_WRENCH     = "4967667085606912536"   # 🔧  Settings / maintenance
E_INBOX      = "5443127283898405358"   # 📥  Add stock
E_CHART      = "6089079919856325971"   # 📊  Today orders/deposits stats
E_TICKET_STUB= "6267209204512069720"   # 🎟️  Coupons
E_EYE        = "5463200135678796607"   # 👁  View stock

# ──────────────────────────────────────────────────────────────────────────────
#  STATUS ICONS  (used in messages, not buttons)
# ──────────────────────────────────────────────────────────────────────────────

E_SUCCESS    = "5039844895779455925"   # ✅  Deposit/purchase confirmed
E_ERROR      = "5040042498634810056"   # ❌  Error / failed
E_PENDING    = "5041784790773138608"   # ⏳  Pending / waiting
E_STAR       = "5334523697174683404"   # ⭐  VIP / Premium highlight
E_FIRE       = "5039644681583985437"   # 🔥  Hot deal / featured

# ──────────────────────────────────────────────────────────────────────────────
#  MESSAGE-TEXT PREMIUM EMOJI  (icons INSIDE message bodies, not buttons)
# ──────────────────────────────────────────────────────────────────────────────
#  Fill in a Premium Custom Emoji ID next to any glyph below and every message
#  that contains that exact unicode emoji will automatically show the premium
#  icon instead, via Telegram's <tg-emoji emoji-id="..."> HTML tag.
#  Leave "" to leave that emoji as plain unicode (default, no change).
#
#  NOTE: you need a Telegram Premium account (or a bot linked to one) for
#  <tg-emoji> to actually render as a custom icon for other users — otherwise
#  Telegram clients silently show the plain fallback emoji instead.

MSG_EMOJI = {
    "⏰": "6217487596486922033",                                 # alarm clock
    "⏱️": "6217721388736712699",                                # stopwatch
    "⏳": "6215133834149629990",                                 # hourglass — pending/waiting
    "♻️": "6217296801154731905",                                # recycle
    "⚙️": "5215327492738392838",             # gear — admin/settings
    "⚠️": "6215486554043846997",                                # warning
    "⚡": "6267253279466460112",                                 # lightning — fast/instant
    "⚫": "5370782098850323832",                                 # black circle
    "⛓️": "6269213828957868371",                                # chain
    "⛔": "6217490044618281742",                                 # no entry — blocked
    "✅": "5039844895779455925",              # check mark — success
    "✍️": "6113971389935391397",                                # writing hand
    "✏️": "5371053145646441722",                                # pencil — edit/note
    "✦": "6217237882793365420",                                 # star bullet
    "✨": "6267209144382526972",                                 # sparkles
    "❌": "5040042498634810056",              # cross mark — error/cancel
    "➕": "5228889792573360456",                                 # plus — add/increase
    "➖": "5229229911033530793",                                 # minus — decrease
    "🆔": "6266996805494379857",                                 # ID badge
    "🆕": "6082537967404977299",                                 # NEW badge
    # Country flags intentionally NOT mapped here — they'd override country
    # flags inside message text (e.g. "🇮🇳 India — 2020") with the wrong
    # premium visual. Configure per-country premium flags in COUNTRY_EMOJI
    # below; flag_html() renders them as <tg-emoji> entities.
    # "🇬🇧": "…",  "🇮🇳": "…",  "🇮🇩": "…",  "🇻🇳": "…",
    "🌐": "5042186567783809934",              # globe — language
    "🌟": "5422367241645611298",                                 # glowing star
    "🎁": "5330312778093704176",                                 # gift — referral/free item
    "🎉": "5042274086332400375",              # party popper — purchase success
    "🎟️": "6267209204512069720",                                # ticket stub
    "🎧": "6093701420630938474",              # headphones — support
    "🎫": "5197269100878907942",              # ticket — support ticket
    "🎬": "5229121484584139947",                                 # clapper board
    "🏆": "4958725487682650920",                                 # trophy
    "🏠": "4970038633403777664",                                 # house — home/main menu
    "🏪": "6143438580732664355",                                 # convenience store — shop
    "🏷": "5240228673738527951",                                 # label tag
    "🏷️": "5240228673738527951",                                # label tag (variant)
    "👁": "5463200135678796607",                                 # eye — view/watch
    "👇": "5463241466149086508",                                 # point down
    "👋": "5040033797031070992",              # wave — welcome greeting
    "👛": "5445353829304387411",              # purse — profile/wallet
    "👤": "5231065262228250587",              # bust — profile
    "👥": "5453957997418004470",                                 # people — users
    "👮": "6080394890393423700",                                 # police officer — admin/mod
    "💎": "5039670412733055750",              # diamond — store title/spent
    "💚": "6082168406943993221",                                 # green heart
    "💛": "6082292578743487881",                                 # yellow heart
    "💬": "6095865895169560113",                                 # speech bubble
    "💰": "5278467510604160626",              # money bag — balance/wallet
    "💳": "5332455502917949981",              # credit card — wallet/payment
    "💵": "6086664791026307819",                                 # banknote
    "💸": "5197434882321567830",              # money with wings — spent/withdrawal
    "💼": "6093612746736145083",                                 # briefcase
    "📂": "5303214794336125778",                                 # open folder — my tickets
    "📅": "6244425785986257276",                                 # calendar
    "📈": "6156443144704497624",                                 # chart increasing
    "📊": "6089079919856325971",                                 # bar chart — stats
    "📋": "5033080906403808074",              # clipboard — history/menu
    "📝": "5197269100878907942",                                 # memo/note
    "📢": "6095888417978061469",                                 # megaphone — broadcast
    "📣": "6129492160497589882",                                 # loudspeaker
    "📤": "5445355530111437729",                                 # outbox tray
    "📥": "5443127283898405358",                                 # inbox tray
    "📦": "5039834781131474002",              # package — orders
    "📧": "5445353829304387411",                                 # email
    "📨": "5444856076954520455",                                 # incoming envelope
    "📩": "5274055917766202507",                                 # envelope with arrow — reply
    "📬": "5033080906403808074",                                 # mailbox
    "📲": "6093587384954262033",                                 # phone with arrow
    "📷": "5870994129244131212",                                 # camera
    "📺": "5870772616305839506",                                 # television
    "🔄": "5337328443962960187",                                 # refresh/reload
    "🔍": "5397986013681295058",                                 # magnifying glass — search
    "🔎": "5397986013681295058",                                 # magnifying glass (right)
    "🔐": "5197288647275071607",                                 # locked with key
    "🔑": "6176966310920983412",                                 # key
    "🔒": "5310278924616356636",                                 # locked
    "🔔": "5039599902254957590",              # bell — reminder
    "🔗": "4958689671950369798",                                 # link — referral link
    "🔢": "5361741454685256344",                                 # numbers
    "🔧": "4967667085606912536",                                 # wrench — settings
    "🔴": "5318840353510408444",                                 # red circle
    "🔵": "5321518192605019723",                                 # blue circle
    "🕐": "6242510612824332116",                                 # clock
    "🖼": "5235989279024373566",                                 # picture frame
    "🗂️": "5445353829304387411",                                # card index dividers
    "🗑️": "4956337889593000947",                                # trash — remove/delete
    "😔": "6086933162057798581",                                 # sad face
    "🙌": "6089118557382121313",                                 # raised hands
    "🙏": "6093661404420641058",                                 # folded hands — thanks
    "🚫": "6264989883241076562",                                 # prohibited
    "🛍": "6093612746736145083",                                 # shopping bags (no VS)
    "🛍️": "5445221832074483553",             # shopping bags — shop
    "🛒": "5312361253610475399",              # shopping cart — cart
    "🟠": "5239975081689498076",                                 # orange circle — BEP20
    "🟡": "5273931763146565225",                                 # yellow circle — TRC20
    "🟢": "5188234920639632382",                                 # green circle
    "🤖": "6129889801454754893",                                 # robot — bot
    "🥇": "6265004494719816749",                                 # gold medal
    "🥈": "5447203607294265305",                                 # silver medal
    "🥉": "5453902265922376865",                                 # bronze medal
    "🏅": "5042061201983407048",              # medal — membership tier
}


import re as _re

_EMOJI_RE = None  # compiled lazily, cached


def _get_emoji_re():
    """
    Build (once) a regex that matches any unicode emoji key configured in
    MSG_EMOJI that has a non-empty premium ID. Longest keys first so a
    multi-codepoint emoji (e.g. "🏷️" = label + variation selector) is
    matched whole instead of accidentally matching the shorter "🏷" prefix.
    """
    global _EMOJI_RE
    if _EMOJI_RE is None:
        keys = sorted((k for k, v in MSG_EMOJI.items() if v), key=len, reverse=True)
        if keys:
            _EMOJI_RE = _re.compile("|".join(_re.escape(k) for k in keys))
        else:
            _EMOJI_RE = _re.compile(r"(?!)")  # matches nothing
    return _EMOJI_RE


def apply_premium_emoji(text: str) -> str:
    """
    Replace every plain unicode emoji in `text` that has a configured
    Premium Custom Emoji ID in MSG_EMOJI with Telegram's
    <tg-emoji emoji-id="..."> HTML tag, keeping the original emoji as the
    fallback glyph inside the tag (shown to clients/users who can't render
    the custom icon).

    REQUIREMENT (Telegram Bot API 9.4, Feb 9 2026): a bot may only send
    custom-emoji entities — in message text/captions AND on button icons —
    if the Telegram account that owns the bot (the one that created it via
    @BotFather) has an active Telegram Premium subscription. Without that,
    Telegram rejects or silently strips these tags no matter how correct
    the code is. That is a Telegram-side account requirement, not something
    fixable in code alone.

    Only called when parse_mode is HTML (see bot.py's global wrapper), so
    it's safe to emit HTML tags here unconditionally.
    """
    if not text:
        return text
    pattern = _get_emoji_re()

    def _sub(m):
        emoji = m.group(0)
        eid = MSG_EMOJI.get(emoji)
        return f'<tg-emoji emoji-id="{eid}">{emoji}</tg-emoji>' if eid else emoji

    return pattern.sub(_sub, text)


# ──────────────────────────────────────────────────────────────────────────────
#  EVERY BUTTON IN THE BOT — keyed by callback_data
# ──────────────────────────────────────────────────────────────────────────────
#  This covers ALL inline buttons across every screen (Wallet, Deposit,
#  Binance Pay, TRC20/BEP20, Admin Panel, Support, etc.) — not just the main
#  menu. Fill in a Premium Custom Emoji ID next to any button below and it
#  will automatically get a premium icon, everywhere it's shown, with no
#  other code changes needed. Leave "" to leave that button plain.

BTN_EMOJI = {
    # main menu
    "shop": E_SHOP, "deposit": E_DEPOSIT, "profile": E_PROFILE, "support": E_SUPPORT,
    "history": E_HISTORY, "home": E_HOME, "admin": E_ADMIN, "cart_checkout": E_CHECKOUT,
    "cart_clear": "", "check_join": "", "gift_start": E_REFERRAL,
    "ticket_list": E_MY_TICKETS, "noop": "",
    # wallet / deposit screen
    "dep_net_PAY": E_PAY, "dep_net_TRC20": E_TRC20, "dep_net_BEP20": E_BEP20,
    "dep_history": E_DEP_HIST, "deposit_start": E_START_DEP,
    # admin panel
    "adm_add_admin": E_POLICE, "adm_add_cat": E_CATEGORY, "adm_add_channel": E_MEGAPHONE, "adm_add_prd": E_PACKAGE,
    "adm_addbal": E_TOP_UP, "adm_all_tickets": E_SUPPORT, "adm_broadcast": E_MEGAPHONE, "adm_cats": E_CATEGORY,
    "adm_coupons": E_TICKET_STUB, "adm_daily_custom": E_CALENDAR, "adm_daily_menu": E_CALENDAR,
    "adm_dl_all_deps": E_EXPORT, "adm_dl_all_orders": E_EXPORT, "adm_dl_today_deps": E_EXPORT,
    "adm_dl_today_orders": E_EXPORT, "adm_free_add": E_REFERRAL, "adm_free_menu": E_REFERRAL,
    "adm_manual_dep": E_CHECKOUT, "adm_prds": E_PACKAGE, "adm_rem_admin_start": E_POLICE,
    "adm_rem_channel": E_TRASH, "adm_rembal": E_QTY_MINUS, "adm_reseller_menu": E_STORE,
    "adm_search_user": E_MAGNIFY, "adm_set_bep20": E_ORANGE, "adm_set_bep20_qr": E_ORANGE,
    "adm_set_botemoji": E_SPARKLES, "adm_set_botname": E_PENCIL, "adm_set_deplogch": E_LOUDSPKR,
    "adm_set_logch": E_LOUDSPKR, "adm_set_low_stock": E_WARNING, "adm_set_min_dep": E_MONEYBAG,
    "adm_set_pay_qr": E_PAY, "adm_set_payid": E_PAY, "adm_set_trc20": E_YELLOW,
    "adm_set_trc20_qr": E_YELLOW, "adm_settings": E_WRENCH, "adm_stock_menu": E_INBOX,
    "adm_tickets": E_SUPPORT, "adm_today_deps": E_CHART, "adm_today_orders": E_CHART,
    "adm_tog_maintenance": E_WRENCH, "adm_tog_referral_on": E_REF_LINK, "adm_user_hist": E_MY_TICKETS,
    "adm_users": E_PEOPLE, "adm_view_admins": E_POLICE, "adm_view_stock_menu": E_EYE,
    "adm_wd_list": E_TOP_UP,
}

# ──────────────────────────────────────────────────────────────────────────────
#  DYNAMIC / PER-ITEM BUTTONS — matched by callback_data PREFIX
# ──────────────────────────────────────────────────────────────────────────────
#  These buttons carry a variable ID suffix (order_42, adm_rreq_ok_5, ...), so
#  they can't be matched exactly. Any callback_data that STARTS WITH one of
#  these prefixes gets the icon below. Covers admin approve/reject, tickets,
#  deposits, cart, referrals, free-item claims, product categories, and the
#  language-picker buttons.

BTN_EMOJI_PREFIX = {
    "adm_addstock_": "", "adm_ban_": "", "adm_clearstock_": "",
    "adm_close_ticket_": "", "adm_daily_": "", "adm_del_cat_": "",
    "adm_del_prd_": "", "adm_delcat_force_": "", "adm_dep_no_": E_ERROR,
    "adm_dep_ok_": E_SUCCESS, "adm_dl_uhist_": "", "adm_do_rem_admin_": "",
    "adm_free_addstock_": "", "adm_free_del_": "", "adm_free_toggle_": "",
    "adm_remch_": "", "adm_reply_ticket_": E_REPLY, "adm_rreq_no_": E_ERROR,
    "adm_rreq_ok_": E_SUCCESS, "adm_ticket_": "", "adm_toggle_cat_": "",
    "adm_uhist_": "", "adm_viewstock_": "", "cartqty_": "", "cartrem_": "",
    "dep_check_": "", "dep_notify_adm_": "", "dep_pay_ipaid_": E_SUCCESS,
    "dep_status_": "", "dep_submit_hash_": "", "free_claim_": "",
    "order_": "", "prd_cat_": "", "refund_req_": "", "setlang_": "",
    "ticket_close_": "", "ticket_reply_": E_REPLY, "ticket_view_": "",

    # ─── OTP-bot merged: Buy Account + TG Panel ─────────────────────────────
    # Fill in your Premium Custom Emoji IDs. Leave "" to show plain text.
    # (Button labels have NO leading unicode emoji so premium ones can shine.)

    # User Buy-Account flow
     "otp_c|":       "5294236848103643477",   # country pick  (label already has flag)
    "otp_cp|":      "6120863614149596295",   # countries pagination
    "otp_y|":       "6242034872181855803",   # year+price row
    "otp_buy1|":    "6244425785986257276",   # Buy 1 button
    "otp_bulk|":    "6237871949360403914",   # Buy Bulk button
    "otp_again|":   "5370896319210595146",   # Get OTP Again
    "otp_logout|":  "5415607860769857239",   # Finish & Logout

    # TG Panel dynamic buttons
    "tgp_country|": "5208573502046610594",   # country row in Manage Stock
    "tgp_clear|":   "6228597878017758215",   # clear stock
    "tgp_reavail|": "6208272666843945422",   # mark available
    "tgp_unavail|": "6174665578544896521",   # mark used

    # WhatsApp store dynamic buttons
    "wa_c|":        "5294236848103643477",   # country row
    "wa_cp|":       "6120863614149596295",   # pagination
    "wa_p|":        "6242034872181855803",   # package/price row
    "wa_ok|":       "6244425785986257276",   # confirm purchase
    "wa_st|":       "5370896319210595146",   # refresh status / order row
    "wa_cx|":       "5415607860769857239",   # cancel & refund
    "wap_c|":       "5208573502046610594",   # admin country row
    "wap_wipe|":    "6228597878017758215",   # wipe country
    "wap_pc|":      "6266994305823413453",   # update country price
    "wap_send|":    "6244425785986257276",   # send login code
    "wap_refund|":  "6174665578544896521",   # refund order
}

# ─── OTP-bot merged: exact-match buttons ─────────────────────────────────────
# ─── OTP-bot merged: exact-match buttons ─────────────────────────────────────
BTN_EMOJI.update({
    # user
    "otp_buy":         "5330237710655306682",   # Buy Account (main menu)
    # TG Panel main
    "tg_panel":        "5330237710655306682",   # TG Panel button in Admin Panel

    "tgp_addstock":    "6269459131720012969",   # Add Stock (menu)

    "tgp_add_single":  "6266794727283102434",   # Add Single Acc

    "tgp_add_zip":     "6266945197167351134",   # Add ZIP (bulk)

    "tgp_manage":      "6269092886973781624",   # Manage Stock

    "tgp_prices":      "6266996805494379857",   # Auto-Price


    "tgp_price_add":   "6266994305823413453",   # Add price rule

    "tgp_price_clear": "6269173138437706805",   # Clear

 "tgp_test":        "6253268947555063546",   # Test Sessions

    "tgp_del_dead":    "6255527846949688170",   # Delete Dead

    "tgp_stats":       "6253440265210566668",   # OTP Stats

    "tgp_2fa":         "5319120041780726017",   # 2FA Manager

    "tgp_2fa_edit":    "5235611059909323996",   # Edit 2FA

    "tgp_folder":      "5260553279321944543",   # Sessions


    "tgp_rate":        "5346105514575025401",   # USDT⇄INR Rate

    "tgp_rate_set":    "5188234920639632382",   # Change Rate

 "tgp_add_bulk":    "6093869083269271739",

    # ─── WhatsApp store (WP ACCOUNT BUY + WA Panel) ─────────────────────────
    "wa_buy":          "5334998226636390258",   # WP ACCOUNT BUY (main menu)
    "wa_orders":       "6253440265210566668",   # My Orders
    "wa_panel":        "5334998226636390258",   # WA Panel (admin panel)
    "wap_single":      "6266794727283102434",   # Add Single
    "wap_bulk":        "6093869083269271739",   # Add Bulk
    "wap_stock":       "6269092886973781624",   # Manage Stock
    "wap_price":       "6266996805494379857",   # Set Pricing
    "wap_pending":     "5370896319210595146",   # Pending Orders
    "wap_stats":       "6253440265210566668",   # Statistics
    "wap_del":         "6255527846949688170",   # Delete Number
})

# ─── OTP-bot merged: per-country premium flag emoji IDs ──────────────────────
# Aap yahan har country ke apne Premium Custom Emoji ID daal do.
# Ye map otp_module.py aur otp_admin.py flag rendering me use ho sakta hai.
# Keys are the country names used in COUNTRY_CODES (otp_module.py).
COUNTRY_EMOJI: dict[str, str] = {
    "Flag": "5294236848103643477",
"Afghanistan": "5291937511591925566",
"Aland Islands": "5294077418917616055",
"Albania": "5294202819077756005",
"Algeria": "5294048127240655242",
"American Samoa": "5291994273879709721",
"Andorra": "5294215205763434181",
"Angola": "5294516785482062829",
"Anguilla": "5292186323342350940",
"Antigua and Barbuda": "5294005972136647964",
"Argentina": "5292208210495689627",
"Armenia": "5291978717508164018",
"Aruba": "5294007002928798927",
"Australia": "5294444247779399477",
"Austria": "5291975174160145850",
"Azerbaijan": "5294323533428579078",
"Bahamas": "5294031587321600012",
"Bahrain": "5294108398516720753",
"Bangladesh": "5291824687096027834",
"Barbados": "5294526187165471742",
"Belarus": "5294134426018536120",
"Belgium": "5291774466043435275",
"Belize": "5294171848068584842",
"Benin": "5293984969746566866",
"Bhutan": "5294121983498277263",
"Bolivia": "5294201479047957700",
"Botswana": "5294026179957772585",
"Brazil": "5291892229751723900",
"Brunei": "5292098293692650297",
"Bulgaria": "5294308947719640437",
"Burkina Faso": "5294153164960848949",
"Burundi": "5294051631933967760",
"Cambodia": "5294225191562400452",
"Cameroon": "5291997306126626950",
"Canada": "5292290347450259214",
"Cape Verde": "5292203503211535593",
"Central African Republic": "5294210571493724819",
"Chad": "5291780728105753403",
"Chile": "5294231037012888049",
"China": "5294068833277990704",
"Colombia": "5294010206974397371",
"Comoros": "5294351381996521508",
"Congo": "5294035229453865597",
"Cook Islands": "5292098684534675100",
"Costa Rica": "5292063805105263554",
"Ivory Coast": "5293991322003200135",
"Croatia": "5291999676948569127",
"Cuba": "5291963947115631526",
"Cyprus": "5294062721539526918",
"Czech Republic": "5294242852467923382",
"Denmark": "5294531860817268837",
"Djibouti": "5294127214768468283",
"Dominica": "5294485513825178032",
"Dominican Republic": "5294522197140857947",
"Ecuador": "5292083733753517221",
"Egypt": "5293992082212409502",
"El Salvador": "5294337307388695687",
"England": "5294410107084365278",
"Equatorial Guinea": "5292170045416297012",
"Eritrea": "5291922054004625949",
"Estonia": "5291951143818123103",
"Ethiopia": "5292245976143124155",
"European Union": "5291992809295861098",
"Gibraltar": "5292055799286224027",
"Gambia": "5294399820637688352",
"Greenland": "5292014752283774878",
"Finland": "5294049961191690629",
"France": "5291817660529533837",
"Gabon": "5294321325815389139",
"Georgia": "5294349389131697267",
"Germany": "5292013274815028523",
"Ghana": "5294347396266873249",
"Greece": "5291948395039054764",
"Guinea-Bissau": "5294409819321550432",
"Guatemala": "5294336633078831209",
"Guinea": "5291892096607739008",
"Guyana": "5292062692708736193",
"Haiti": "5292045130587462814",
"Honduras": "5291901034434682297",
"Hong Kong": "5292166459118606932",
"Hungary": "5294229581018975260",
"Iceland": "5294354358408859664",
"India": "5291933173674957761",
"Iran": "5294220170745630736",
"Iraq": "5294325010897327367",
"Ireland": "5294471971793293647",
"Isle of Man": "5294318478252070646",
"Israel": "5294069056616289553",
"Italy": "5291826830284709120",
"Jamaica": "5294505107465982830",
"Japan": "5291799063321139445",
"Jersey": "5291950280529697493",
"Jordan": "5291988613112814801",
"Kazakhstan": "5294227175837290463",
"Kenya": "5292111852904416801",
"Kiribati": "5294538934628405146",
"North Korea": "5294193812531333564",
"South Korea": "5294408281723262763",
"Kuwait": "5292066437920218075",
"Kyrgyzstan": "5292091954320922577",
"Laos": "5291981530711746037",
"Latvia": "5292236016113966127",
"Lebanon": "5294193108156699621",
"Lesotho": "5292040693886247604",
"Liberia": "5291793810576137439",
"Libya": "5291858711826946840",
"Liechtenstein": "5292048742654957785",
"Lithuania": "5294343084119708700",
"Luxembourg": "5294423709245787718",
"North Macedonia": "5294023611567332075",
"Madagascar": "5291991568050312348",
"Malawi": "5294241881805312589",
"Malaysia": "5291858351049696702",
"Maldives": "5292004203844097218",
"Mali": "5292086972158858331",
"Malta": "5294532213004588353",
"Marshall Islands": "5294180730060954484",
"Mauritania": "5294429743674840973",
"Mauritius": "5294127824653797277",
"Mexico": "5294535073452809778",
"Micronesia": "5291838156113470124",
"Moldova": "5294158486425325375",
"Monaco": "5294378161117614233",
"Mongolia": "5294316532631883496",
"Morocco": "5292108962391414885",
"Mozambique": "5294086708931874940",
"Myanmar": "5294254478944393569",
"Namibia": "5292021761670404922",
"Nauru": "5294463274484521342",
"Nepal": "5294458756178924088",
"Netherlands": "5291917797692042265",
"New Zealand": "5294189019347833274",
"Nicaragua": "5294240825243358100",
"Niger": "5291809418487290691",
"Nigeria": "5294456308047563965",
"Niue": "5294471336138134209",
"Norway": "5291761718580502030",
"Oman": "5291813666209946812",
"Pakistan": "5291825606219029010",
"Palestine": "5294289826525238172",
"Panama": "5291959935616178405",
"Papua New Guinea": "5291917995260533077",
"Paraguay": "5294525611639852679",
"Philippines": "5291798075478661634",
"Peru": "5292099427564018941",
"Poland": "5292190970496963836",
"Portugal": "5294436555492973610",
"Puerto Rico": "5292121516580820347",
"Qatar": "5292166360334357676",
"Romania": "5294107724206856227",
"Russia": "5294335323113807278",
"Rwanda": "5294191265615729158",
"San Marino": "5292147350809106831",
"Sao Tome and Principe": "5292183188016222701",
"Saudi Arabia": "5294163983983463099",
"Scotland": "5294434665707368018",
"Senegal": "5292087023698466689",
"Serbia": "5294458584380230360",
"Seychelles": "5291891186074672309",
"Sierra Leone": "5294494314213167952",
"Singapore": "5294451304410663668",
"Slovakia": "5294538440707166931",
"Slovenia": "5294279359689938006",
"Solomon Islands": "5294283890880433237",
"Somalia": "5294058817414255960",
"South Africa": "5294325281480266304",
"Spain": "5294513087515216901",
"Sri Lanka": "5292102670264328257",
"Sudan": "5294177148058228060",
"Suriname": "5294396668131692138",
"Eswatini": "5294312482477724867",
"Sweden": "5291737091238026321",
"Switzerland": "5291791748991835084",
"Syria": "5294013428199869487",
"Taiwan": "5294095745543069603",
"Tajikistan": "5294120269806328883",
"Tanzania": "5292146096678658977",
"Thailand": "5293994384314882755",
"Togo": "5294097669688415562",
"Tonga": "5294283689016973348",
"Trinidad and Tobago": "5294362935458548705",
"Tunisia": "5294484680601521871",
"Turkey": "5293993400767367408",
"Turkmenistan": "5294098958178603764",
"Turks and Caicos Islands": "5294320866253884749",
"United States": "5294244076533600593",
"Uganda": "5294192317882716626",
"United Arab Emirates": "5294314831824835370",
"United Kingdom": "5293993521026453119",
"Ukraine": "5294263837678131580",
"Vanuatu": "5294448585696368047",
"Uzbekistan": "5294217645304864345",
"Uruguay": "5291928449210932974",
"Venezuela": "5294476442854247878",
"Vietnam": "5294235963340379688",
"U.S. Virgin Islands": "5294228039125718124",
"Wales": "5294139949346476093",
"Yemen": "5294058972033076492",
"Zambia": "5294100109229838880",
"Zimbabwe": "5294422158762592930",

}

def get_country_emoji(country_name: str) -> str:
    """Return premium custom emoji ID for a country, or '' if not configured."""
    return COUNTRY_EMOJI.get(country_name or "", "")



def get_btn_emoji(callback_data: str) -> str:
    """
    Look up the premium emoji ID for a button's callback_data.
    Checks an exact match in BTN_EMOJI first, then falls back to a
    startswith() match against BTN_EMOJI_PREFIX for dynamic per-item buttons.
    Returns "" if nothing is configured (no icon shown, current behaviour).
    """
    if not callback_data:
        return ""
    eid = BTN_EMOJI.get(callback_data)
    if eid:
        return eid
    for prefix, pid in BTN_EMOJI_PREFIX.items():
        if pid and callback_data.startswith(prefix):
            return pid
    return ""
