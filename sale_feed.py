"""
Public sale + restock broadcaster.

Sends "someone just bought X" and "back in stock" messages to a configured
public group/channel. Uses premium emoji IDs via emoji_config.

Set the destination in .env:
    PUBLIC_CHANNEL_ID=-1001234567890     # group or channel id (bot must be admin)

Optional custom text/emoji IDs are read from emoji_config.SALE_FEED (see below).
Silently no-ops if PUBLIC_CHANNEL_ID is missing.
"""
import os, logging, html
from dotenv import load_dotenv

_here = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_here, ".env"))

logger = logging.getLogger("sale_feed")

def _chan():
    v = (os.getenv("PUBLIC_CHANNEL_ID") or os.getenv("SALES_CHANNEL_ID") or "").strip()
    if not v: return None
    try: return int(v)
    except: return v  # allow @channelusername

def _premium_wrap(unicode_emoji: str, eid: str) -> str:
    if not eid: return unicode_emoji
    return f'<tg-emoji emoji-id="{eid}">{unicode_emoji}</tg-emoji>'

def _ids():
    try:
        import emoji_config as EC
        return {
            "bag":     getattr(EC, "E_SALE_BAG",     "") or EC.MSG_EMOJI.get("🛍️", ""),
            "fire":    getattr(EC, "E_SALE_FIRE",    "") or EC.MSG_EMOJI.get("🔥", ""),
            "package": getattr(EC, "E_SALE_PACKAGE", "") or EC.MSG_EMOJI.get("📦", ""),
            "money":   getattr(EC, "E_SALE_MONEY",   "") or EC.MSG_EMOJI.get("💰", ""),
        }
    except Exception:
        return {"bag":"","fire":"","package":"","money":""}

async def broadcast_sale(bot, *, qty: int, product_name: str,
                         product_emoji: str = "", product_emoji_id: str = "",
                         source: str = "bot"):
    """Fires 'Someone just bought Nx <product>' to PUBLIC_CHANNEL_ID."""
    ch = _chan()
    if not ch: return
    ids = _ids()
    bag = _premium_wrap("🛍️", ids["bag"])
    prod_prefix = ""
    if product_emoji_id:
        prod_prefix = _premium_wrap(product_emoji or "✨", product_emoji_id) + " "
    elif product_emoji:
        prod_prefix = f"{product_emoji} "
    safe_name = html.escape(product_name)
    text = (f"{bag} <b>Someone just bought {qty}x "
            f"{prod_prefix}{safe_name}!</b>\n"
            f"<i>From {html.escape(source)}</i>")
    try:
        await bot.send_message(ch, text, parse_mode="HTML",
                               disable_web_page_preview=True)
    except Exception as e:
        logger.warning(f"sale broadcast failed: {e}")

async def broadcast_restock(bot, *, product_name: str, stock: int = 0,
                            price: str = "", product_emoji: str = "",
                            product_emoji_id: str = "", source: str = "bot"):
    """Fires 'BACK IN STOCK' banner to PUBLIC_CHANNEL_ID."""
    ch = _chan()
    if not ch: return
    ids = _ids()
    fire = _premium_wrap("🔥", ids["fire"])
    pack = _premium_wrap("📦", ids["package"])
    money = _premium_wrap("💰", ids["money"])
    prod_prefix = ""
    if product_emoji_id:
        prod_prefix = _premium_wrap(product_emoji or "✨", product_emoji_id) + " "
    elif product_emoji:
        prod_prefix = f"{product_emoji} "
    safe_name = html.escape(product_name)
    lines = [f"{fire} <b>BACK IN STOCK!</b>",
             f"{prod_prefix}<b>{safe_name}</b>"]
    if stock:  lines.append(f"{pack} Available: <b>{stock}</b>")
    if price:  lines.append(f"{money} Price: <b>{html.escape(str(price))}</b>")
    lines.append(f"<i>From {html.escape(source)}</i>")
    try:
        await bot.send_message(ch, "\n".join(lines), parse_mode="HTML",
                               disable_web_page_preview=True)
    except Exception as e:
        logger.warning(f"restock broadcast failed: {e}")
