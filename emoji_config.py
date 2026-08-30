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

import re

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
    "🇬🇧": "5294354358408859664",                                # flag — English language button
    "🇮🇳": "5291933173674957761",                                # flag — Hindi language button
    "🇮🇩": "5294378161117614233",                                # flag — Indonesian language button
    "🇻🇳": "5292108962391414885",                                # flag — Vietnamese language button
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
    "ticket_list": E_MY_TICKETS, "noop": "", "free_items": E_REFERRAL,
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
}


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


# ──────────────────────────────────────────────────────────────────────────────
#  DESCRIPTION AUTO-UPGRADE MAP
# ──────────────────────────────────────────────────────────────────────────────
#  Fill in a Premium Emoji ID next to any plain emoji below. Then, anywhere you
#  type that plain emoji inside a PRODUCT DESCRIPTION (web panel or bot admin),
#  it is automatically upgraded to the matching premium emoji when the bot
#  sends the message — no need to type <tg-emoji> tags yourself.
#
#  Leave a value as "" to leave that emoji as plain text (no upgrade).
#  Get IDs the same way as everywhere else: forward a message containing the
#  premium emoji to @getidsbot.

DESC_EMOJI_MAP = {
    # 💰 Price / Offer
    "💰": "5039789890133296083", "🏷️": "5240228673738527951", "💵": "6086664791026307819", "💳": "5364036341610858181", "🪙": "5332455502917949981", "🔖": "5240228673738527951", "🎁": "5039823300683891773",
    "🔥": "5039644681583985437", "⭐": "5039555114335994885", "💎": "5039670412733055750",
    # ⏳ Duration / Validity
    "⏳": "6215133834149629990", "⌛": "6215133834149629990", "📅": "6156443144704497624", "🗓️": "5274055917766202507", "⏱️": "6093456762113888541", "🕐": "5370561264516865667", "🔄": "5213452215527677338", "♾️": "6082252992029920416",
    # 🎬 Entertainment
    "🎬": "5235837920081887219",
"🎥": "5235837920081887219",
"🍿": "5371081166013078244",
"📺": "5373330964372004748",
"🎞️": "5373330964372004748",
"🎭": "5226711870492126219",
"🎶": "5382360961313152917",
"🎵": "5467398680959023683",
"🎧": "5229095839334410849",
"▶️": "5375464961822695044",
"📱": "5407025283456835913",
"💻": "5362079447136610876",
"🖥️": "5431376038628171216",
"⌚": "5386494631112353009",
"🎮": "5467583879948803288",
"🕹️": "5465169893580086142",
"📲": "5406809207947142040",
"⚡": "5042334757040423886",
"🚀": "5195033767969839232",
"✨": "5040016479722931047",
"💫": "5042061201983407048",
"💯": "5042297717242463211",
"✅": "5039793437776282663",
"🛠️": "5469909248257317234",
"🎯": "5350460637182993292",
"🔒": "5350619413533958825",
"🔐": "5197288647275071607",
"🛡️": "5352888345972187597",
"🔑": "5330115548900501467",
"👤": "5445164245152966395",
"👥": "5463200135678796607",
"🪪": "5422683699130933153",
"📥": "5359741159566484212",
"⬇️": "5443127283898405358",
"💾": "5431721976769027887",
"📂": "5431736674147114227",
"☁️": "5213274008744632782",
"🚫": "5463131536461144809",
"❌": "5215572533507529497",
"⛔": "5445080884132719243",
"🔕": "5244807637157029775",
"🙅‍♂️": "5260702125708557233",
"🛒": "5312361253610475399",
"🛍️": "5377660214096974712",
"📦": "5445221832074483553",
"🚚": "5445085952194124000",
"🏠": "5278702045883292456",
"📍": "5228843986747147814",
"🎤": "5382360961313152917",
"🔊": "5231071730449004635",
"🔉": "5382013970905309819",
"🎼": "5229095839334410849",
"📚": "5445353829304387411",
"📖": "5445353829304387411",
"📕": "5033080906403808074",
"📝": "5334882760735598374",
"✏️": "5334673106202010226",
"🎓": "6093467039970629408",
"💡": "5262844652964303985",
"👾": "5371053287380361807",
"🏆": "6082571661423414884",
"🥇": "5204271353565300127",
"👑": "5219827798125846744",
"🌟": "5262844652964303985",
"📌": "5215578413317758112",
"ℹ️": "5188463524568926712",
"⚠️": "5215203655946346044",
"❗": "5215557810359639942",
"❕": "6269411431813223365",
"💬": "5224327270289644832",
"📢": "5215659875962462292",
"☑️": "6266893760639015886",
"✔️": "6224510580980454948",
"🟢": "6267243585725274398",
"🔵": "6267118138320491195",
"🟡": "6266913543258379318",
"🔴": "6267047713741739273",
"📩": "5215324073944423501",
}


def upgrade_description_emojis(text: str) -> str:
    """
    Scans a product description for plain emoji that have a Premium Emoji ID
    configured in DESC_EMOJI_MAP above, and wraps each one in a <tg-emoji>
    HTML tag so it renders as the premium version when sent (parse_mode=HTML).

    Any <tg-emoji ...>...</tg-emoji> tags the admin already typed manually
    are left completely untouched (not double-wrapped).

    Emoji with no ID configured (value "") are left as plain text, unchanged.
    """
    if not text:
        return text

    # Protect existing <tg-emoji ...>...</tg-emoji> blocks from being touched.
    protected = []
    def _stash(m):
        protected.append(m.group(0))
        return f"\x00{len(protected) - 1}\x00"
    safe_text = re.sub(r"<tg-emoji[^>]*>.*?</tg-emoji>", _stash, text, flags=re.DOTALL)

    # Longest emoji first, so multi-codepoint emoji (e.g. "🏠") aren't
    # partially matched by a shorter unrelated sequence.
    for emoji_char in sorted(DESC_EMOJI_MAP, key=len, reverse=True):
        eid = DESC_EMOJI_MAP[emoji_char]
        if not eid:
            continue
        if emoji_char in safe_text:
            safe_text = safe_text.replace(
                emoji_char, f'<tg-emoji emoji-id="{eid}">{emoji_char}</tg-emoji>'
            )

    # Restore the protected blocks.
    for i, block in enumerate(protected):
        safe_text = safe_text.replace(f"\x00{i}\x00", block)

    return safe_text
