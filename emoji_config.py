"""
Premium emoji configuration.

Telegram lets a bot show *animated Telegram Premium emojis* inside message
text (HTML parse_mode) using the <tg-emoji emoji-id="..."> tag. Premium
users see the real animated emoji; non-Premium users automatically see the
fallback emoji you put between the tags — so it degrades gracefully.

NOTE: real button colours (green/red/blue) are NOT a Telegram Bot API
feature — the Bot API has no such field, buttons are always styled by the
user's own Telegram theme. What we CAN do (and have done in main.py) is
prefix button labels with 🟢 / 🔴 / 🔵 emoji so buttons are visually
grouped by meaning (success / danger / primary) even though Telegram
itself doesn't recolor them.

HOW TO USE
----------
1. Get a premium custom emoji's numeric ID: forward/copy a premium emoji
   sticker into @tgemojiidbot (or any similar bot) and it replies with the
   numeric ID — paste it below.
2. Fill in the IDs you want per category. Leave "" to skip a category
   (the plain fallback emoji will still show for everyone).
3. Use `premium_emoji(key, fallback)` wherever you build message text with
   parse_mode=ParseMode.HTML.
"""

# Paste your real premium custom emoji IDs here (numeric strings from
# @tgemojiidbot). Two examples the user already supplied are pre-filled;
# replace/add more as needed.
PREMIUM_EMOJI_IDS = {
    "star":     "5431466070281165010",  # ⭐ used for Premium/VIP callouts
    "fire":     "5368324170671202286",  # 🔥 used for highlights/success banners
    "diamond":  "",                     # 💎 e.g. Premium menu header
    "crown":    "",                     # 👑 e.g. Admin panel header
    "rocket":   "",                     # 🚀 e.g. deploy/run success
    "warning":  "",                     # ⚠️ e.g. stop/danger notices
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


# ── Button "colour" prefixes (visual grouping only — see NOTE above) ──
BTN_SUCCESS = "🟢"   # positive / go actions: Run, New, Yes-confirm
BTN_DANGER  = "🔴"   # destructive / stop actions: Stop, Delete, Cancel-danger
BTN_PRIMARY = "🔵"   # navigation / neutral actions: Back, Refresh, Settings
