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
    "star":     "5431466070281165010",  # ⭐ Premium/VIP callouts
    "fire":     "5368324170671202286",  # 🔥 highlights/success banners
    "diamond":  "",                     # 💎 Premium/plan labels
    "crown":    "",                     # 👑 Owner/Admin role label

    # ── Every other emoji used in bot MESSAGE TEXT (not buttons) ──
    # Fill in the numeric ID from @tgemojiidbot. Leave "" to keep the plain emoji.
    "cross":          "",  # ❌
    "check":          "",  # ✅
    "lock":           "",  # 🔒
    "arrow_right":    "",  # →
    "warning":        "",  # ⚠
    "chart":          "",  # 📊
    "banned":         "",  # 🚫
    "wrench":         "",  # 🔧
    "green_dot":      "",  # 🟢
    "database":       "",  # 🗄
    "folder":         "",  # 📁
    "package":        "",  # 📦
    "refresh_arrows": "",  # 🔄
    "floppy":         "",  # 💾
    "open_folder":    "",  # 📂
    "blue_diamond":   "",  # 🔹
    "red_dot":        "",  # 🔴
    "github":         "",  # 🐙
    "users":          "",  # 👥
    "tv":             "",  # 📺
    "gear":           "",  # ⚙
    "repeat":         "",  # 🔁
    "party":          "",  # 🎉
    "clipboard":      "",  # 📋
    "trash":          "",  # 🗑
    "shield":         "",  # 🛡
    "lock_key":       "",  # 🔐
    "sparkles":       "",  # ✨
    "rocket":         "",  # 🚀
    "person":         "",  # 👤
    "unlock":         "",  # 🔓
    "pencil":         "",  # ✏
    "star2":          "",  # 🌟
    "cloud":          "",  # ☁
    "link":           "",  # 🔗
    "bell":           "",  # 🔔
    "clock":          "",  # 🕐
    "megaphone":      "",  # 📢
    "noentry":        "",  # ⛔
    "wave":           "",  # 👋
    "laptop":         "",  # 💻
    "desktop":        "",  # 🖥
    "calendar":       "",  # 📅
    "inbox":          "",  # 📥
    "boom":           "",  # 💥
    "python":         "",  # 🐍
    "pingpong":       "",  # 🏓
    "magnifier":      "",  # 🔍
    "memo":           "",  # 📝
    "globe":          "",  # 🌐
    "page":           "",  # 📄
    "envelope":       "",  # 📩
    "minus":          "",  # ➖
    "plus":           "",  # ➕
    "broom":          "",  # 🧹
    "chart_up":       "",  # 📈
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
