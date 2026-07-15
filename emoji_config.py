"""
Premium emoji configuration.

Telegram lets a bot show *animated Telegram Premium emojis* inside message
text (HTML parse_mode) using the <tg-emoji emoji-id="..."> tag. Premium
users see the real animated emoji; non-Premium users automatically see the
fallback emoji you put between the tags — so it degrades gracefully.

IMPORTANT LIMITATION (Telegram Bot API, not this bot):
Inline keyboard BUTTON labels can NEVER show premium/custom emoji — the
Bot API's InlineKeyboardButton only accepts plain text, no HTML/entities.
So emoji that only ever appear on buttons (🔙 Back, ➡ Next, ⬅ Prev,
🔃 Refresh-icon-only-buttons, and the 🟢🔴🔵 colour-illusion prefixes) stay
plain forever, no matter what ID you put here — that's a hard Telegram
limit, not something we can work around. Real button "colour" is faked
with 🟢/🔴/🔵 emoji prefixes (see BTN_SUCCESS/BTN_DANGER/BTN_PRIMARY below).

Every emoji below DOES appear in actual message TEXT somewhere in the bot,
so it's a real candidate for a premium ID.

HOW TO USE
----------
1. Get a premium custom emoji's numeric ID: forward/copy a premium emoji
   sticker into @tgemojiidbot (or any similar bot) and it replies with the
   numeric ID.
2. Paste the ID as the value for the matching key below. Leave "" to skip
   (the plain fallback emoji shown in the comment will keep being used).
3. Nothing else needs to change in main.py — once main.py is wired to call
   premium_emoji(key, fallback) for a given spot, filling the ID here is
   enough to make it "go premium" everywhere that key is used.
"""

PREMIUM_EMOJI_IDS = {
    # ── Already set (examples you gave earlier) ──
    "star":     "5334523697174683404",  # ⭐ Premium/VIP callouts
    "fire":     "5368324170671202286",  # 🔥 highlights/success banners
    "diamond":  "5039670412733055750",                     # 💎 Premium/plan labels
    "crown":    "5039539210072097557",                     # 👑 Owner/Admin role label

    # ── Every other emoji used in bot MESSAGE TEXT (not buttons) ──
    # Fill in the numeric ID from @tgemojiidbot. Leave "" to keep the plain emoji.
    "cross":          "5040042498634810056",  # ❌
    "check":          "5039844895779455925",  # ✅
    "lock":           "6129906126625447892",  # 🔒
    "arrow_right":    "5346105514575025401",  # →
    "warning":        "5039665997506675838",  # ⚠
    "chart":          "5039808285478224750",  # 📊
    "banned":         "5042112436648281096",  # 🚫
    "wrench":         "5350396951407895212",  # 🔧
    "green_dot":      "5188234920639632382",  # 🟢
    "database":       "6129553763213515073",  # 🗄
    "folder":         "5445221832074483553",  # 📁
    "package":        "4967518033061872209",  # 📦
    "refresh_arrows": "5310278924616356636",  # 🔄
    "floppy":         "5235989279024373566",  # 💾
    "open_folder":    "5445353829304387411",  # 📂
    "blue_diamond":   "4956719506027185156",  # 🔹
    "red_dot":        "6116324271804387654",  # 🔴
    "github":         "6129889801454754893",  # 🐙
    "users":          "6156434919842127016",  # 👥
    "tv":             "6129888444245089008",  # 📺
    "gear":           "4965290516993278759",  # ⚙
    "repeat":         "5337328443962960187",  # 🔁
    "party":          "4956596167451346576",  # 🎉
    "clipboard":      "5873153278023307367",  # 📋
    "trash":          "4956337889593000947",  # 🗑
    "shield":         "4963323872943276909",  # 🛡
    "lock_key":       "6176966310920983412",  # 🔐
    "sparkles":       "4956371914323920049",  # ✨
    "rocket":         "6093651105089065114",  # 🚀
    "person":         "6273840152980755328",  # 👤
    "unlock":         "5310278924616356636",  # 🔓
    "pencil":         "5395444784611480792",  # ✏
    "star2":          "5224205542326557875",  # 🌟
    "cloud":          "6129695952400820630",  # ☁
    "link":           "5379742233853451967",  # 🔗
    "bell":           "6129577213734952104",  # 🔔
    "clock":          "6242510612824332116",  # 🕐
    "megaphone":      "6129492160497589882",  # 📢
    "noentry":        "5039614900280754969",  # ⛔
    "wave":           "6325790754543241229",  # 👋
    "laptop":         "5870994129244131212",  # 💻
    "desktop":        "5870772616305839506",  # 🖥
    "calendar":       "5274055917766202507",  # 📅
    "inbox":          "5445221832074483553",  # 📥
    "boom":           "5280569974404966639",  # 💥
    "python":         "6093869083269271739",  # 🐍
    "pingpong":       "6034910005612778344",  # 🏓
    "magnifier":      "5397986013681295058",  # 🔍
    "memo":           "5197269100878907942",  # 📝
    "globe":          "5042186567783809934",  # 🌐
    "page":           "5033080906403808074",  # 📄
    "envelope":       "5274055917766202507",  # 📩
    "minus":          "6307665627481903641",  # ➖
    "plus":           "6093406373557571574",  # ➕
    "broom":          "6082192016379220195",  # 🧹
    "chart_up":       "5429651785352501917",  # 📈
}


def premium_emoji(key: str, fallback: str) -> str:
    """
    Return an HTML <tg-emoji> tag for the given premium emoji key, with
    `fallback` as the plain emoji shown to non-Premium users (and to
    everyone if no ID is configured for `key`).

    Must be used inside text sent with parse_mode=ParseMode.HTML.
    """
    emoji_id = PREMIUM_EMOJI_IDS.get(key, "")
    if not emoji_id:
        return fallback
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'


# ─────────────────────────────────────────────────────────────
# Bot-wide auto-conversion: every message the bot ever sends gets routed
# through here (see the Bot.send_message / Bot.edit_message_text patch in
# main.py) so ALL 58 message-text-eligible emoji automatically become
# premium emoji the moment you fill in an ID above — no per-message code
# changes needed anywhere else in main.py.
# ─────────────────────────────────────────────────────────────
import html as _html
import re as _re

# Every emoji that's eligible to become a premium emoji (appears in message
# text somewhere in the bot). Longest-first so multi-codepoint emoji aren't
# partially matched.
_EMOJI_TO_KEY = {
    "❌": "cross", "✅": "check", "🔒": "lock", "→": "arrow_right", "⚠": "warning",
    "📊": "chart", "🚫": "banned", "🔧": "wrench", "🟢": "green_dot", "🗄": "database",
    "📁": "folder", "💎": "diamond", "📦": "package", "🔄": "refresh_arrows", "💾": "floppy",
    "📂": "open_folder", "🔹": "blue_diamond", "🔴": "red_dot", "🐙": "github", "👥": "users",
    "📺": "tv", "⚙": "gear", "🔁": "repeat", "🎉": "party", "📋": "clipboard", "🗑": "trash",
    "🛡": "shield", "🔐": "lock_key", "✨": "sparkles", "🚀": "rocket", "👤": "person",
    "🔓": "unlock", "✏": "pencil", "🌟": "star2", "☁": "cloud", "🔗": "link", "🔔": "bell",
    "🕐": "clock", "📢": "megaphone", "⛔": "noentry", "👋": "wave", "💻": "laptop",
    "🖥": "desktop", "📅": "calendar", "📥": "inbox", "💥": "boom", "🐍": "python",
    "🏓": "pingpong", "🔍": "magnifier", "📝": "memo", "🌐": "globe", "👑": "crown",
    "📄": "page", "📩": "envelope", "➖": "minus", "➕": "plus", "🧹": "broom", "📈": "chart_up",
    "⭐": "star", "🔥": "fire",
}

_EMOJI_CHARS_RE = _re.compile(
    "|".join(_re.escape(e) for e in sorted(_EMOJI_TO_KEY, key=len, reverse=True))
)

# Simple *bold* and `code` -> HTML. Deliberately NOT converting _italic_
# (bot's technical text is full of legit underscored identifiers like
# user_id / project_id, so an _italic_ regex would mangle those).
_MD_CODE_RE = _re.compile(r"`([^`\n]+)`")
_MD_BOLD_RE = _re.compile(r"\*([^*\n]+)\*")


def _swap_emoji(text: str) -> str:
    """Replace plain emoji characters with their premium <tg-emoji> tag
    (or leave as-is if no ID configured for that emoji)."""
    return _EMOJI_CHARS_RE.sub(lambda m: premium_emoji(_EMOJI_TO_KEY[m.group(0)], m.group(0)), text)


def markdown_to_html_auto(text: str) -> str:
    """
    Best-effort convert a legacy Markdown-flavoured message (the bot's old
    default) into safe HTML, then swap in premium emoji. HTML-escapes the
    raw text first (so stray & / < / > from project names etc. can't break
    the HTML), then converts *bold* / `code`, then swaps emoji last (so the
    tags we insert are never re-escaped).
    """
    escaped = _html.escape(text, quote=False)
    escaped = _MD_CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", escaped)
    escaped = _MD_BOLD_RE.sub(lambda m: f"<b>{m.group(1)}</b>", escaped)
    return _swap_emoji(escaped)


def emoji_only_html(text: str) -> str:
    """For text that's already valid, hand-written HTML (has real <b>/<code>/
    <tg-emoji> tags already) — only swap plain emoji characters, don't
    re-escape or re-convert anything else."""
    return _swap_emoji(text)


# ── Button "colour" prefixes (visual grouping only — Bot API buttons can't
# render premium emoji at all, see limitation note above) ──
BTN_SUCCESS = "🟢"   # positive / go actions: Run, New, Yes-confirm
BTN_DANGER  = "🔴"   # destructive / stop actions: Stop, Delete, Cancel-danger
BTN_PRIMARY = "🔵"   # navigation / neutral actions: Back, Refresh, Settings
