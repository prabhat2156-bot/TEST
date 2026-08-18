"""
styled_api.py — BASS TG STORE v5.1
────────────────────────────────────────────────────────────────────────────
Direct Telegram HTTP helper for COLOURED INLINE BUTTONS + PREMIUM EMOJI IDs.

Why direct HTTP instead of python-telegram-bot?
  python-telegram-bot serialises InlineKeyboardButton through its own models
  and strips any extra fields (like 'style' or 'icon_custom_emoji_id') that
  are not yet in the official Bot-API schema it was compiled against.
  By calling the REST endpoint ourselves we can pass ANY JSON fields we want,
  so Telegram receives them and renders coloured buttons / premium emoji icons.

Supported button styles (rendered in Telegram clients that support them):
  "success"  → green  ✅
  "primary"  → blue   🔵  (default Telegram blue — same as no style)
  "danger"   → red    🔴

If a style or emoji_id field is left empty (""), it is simply not sent —
the button renders in the normal grey/default style.

All functions are async and use aiohttp (already in requirements.txt).
"""

import logging
import aiohttp

import config
import emoji_config as EC

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
#  LOW-LEVEL HTTP HELPER
# ─────────────────────────────────────────────────────────────────────────────

async def _post(method: str, payload: dict) -> dict:
    """
    POST to https://api.telegram.org/bot{TOKEN}/{method}.
    Returns the parsed JSON dict (always a dict, never raises on API errors).
    """
    token = getattr(config, "BOT_TOKEN", "")
    if not token:
        logger.warning("styled_api: BOT_TOKEN not set — falling back")
        return {"ok": False, "description": "no token"}

    url = f"https://api.telegram.org/bot{token}/{method}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=15)
            ) as resp:
                data = await resp.json()
                if not data.get("ok"):
                    logger.debug(f"styled_api {method}: {data.get('description')}")
                return data
    except Exception as exc:
        logger.warning(f"styled_api {method} failed: {exc}")
        return {"ok": False, "description": str(exc)}


# ─────────────────────────────────────────────────────────────────────────────
#  BUTTON BUILDER
# ─────────────────────────────────────────────────────────────────────────────

def btn(
    text:          str,
    callback_data: str  = None,
    url:           str  = None,
    style:         str  = None,   # "success" | "primary" | "danger"
    emoji_id:      str  = None,   # premium custom emoji ID
) -> dict:
    """
    Build a single inline-keyboard button dict.

    Usage:
        btn("✅ Buy Now",  "buy_123",  style="success",  emoji_id=EC.E_BUY)
        btn("◀️ Back",     "home",     style="danger",   emoji_id=EC.E_HOME)
        btn("🌐 Website",  url="https://example.com")
    """
    b: dict = {"text": text}
    if callback_data:
        b["callback_data"] = callback_data
    if url:
        b["url"] = url
    if style:                           # only add if not empty
        b["style"] = style
    if emoji_id:                        # only add if user filled it in
        b["icon_custom_emoji_id"] = emoji_id
    return b


# ─────────────────────────────────────────────────────────────────────────────
#  HIGH-LEVEL SEND / EDIT
# ─────────────────────────────────────────────────────────────────────────────

async def send(
    chat_id:    int,
    text:       str,
    rows:       list,           # list of list of btn() dicts
    parse_mode: str = "HTML",
) -> dict:
    """
    Send a NEW message with styled inline keyboard.

    Example:
        await styled_api.send(chat_id, "<b>Hello!</b>", [
            [btn("Shop 🛍️", "shop", style="success")],
            [btn("Back ◀️",  "home", style="danger")],
        ])
    """
    text = EC.apply_premium_emoji(text)
    return await _post("sendMessage", {
        "chat_id":      chat_id,
        "text":         text,
        "parse_mode":   parse_mode,
        "reply_markup": {"inline_keyboard": rows},
    })


async def edit(
    chat_id:    int,
    message_id: int,
    text:       str,
    rows:       list,
    parse_mode: str = "HTML",
) -> dict:
    """
    Edit an EXISTING message with styled inline keyboard.

    Example:
        await styled_api.edit(chat_id, msg_id, text, [
            [btn("✅ Confirm", "confirm", style="success")],
            [btn("❌ Cancel",  "cancel",  style="danger")],
        ])
    """
    text = EC.apply_premium_emoji(text)
    return await _post("editMessageText", {
        "chat_id":      chat_id,
        "message_id":   message_id,
        "text":         text,
        "parse_mode":   parse_mode,
        "reply_markup": {"inline_keyboard": rows},
    })


async def edit_caption(
    chat_id:    int,
    message_id: int,
    caption:    str,
    rows:       list,
    parse_mode: str = "HTML",
) -> dict:
    """
    Edit the CAPTION + keyboard of an existing PHOTO message (e.g. a QR-code
    deposit screen). Telegram's editMessageText fails with "there is no text
    in the message to edit" on photo messages — this uses editMessageCaption
    instead, which is the correct method for that case.
    """
    caption = EC.apply_premium_emoji(caption)
    return await _post("editMessageCaption", {
        "chat_id":      chat_id,
        "message_id":   message_id,
        "caption":      caption,
        "parse_mode":   parse_mode,
        "reply_markup": {"inline_keyboard": rows},
    })


async def edit_reply_markup(
    chat_id:    int,
    message_id: int,
    rows:       list,
) -> dict:
    """Edit ONLY the inline keyboard of an existing message (keep text unchanged)."""
    return await _post("editMessageReplyMarkup", {
        "chat_id":      chat_id,
        "message_id":   message_id,
        "reply_markup": {"inline_keyboard": rows},
    })
