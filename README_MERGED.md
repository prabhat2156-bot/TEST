# NEXUS STORE + OTP — Merged Bot (Phase 1)

Single bot. Single MongoDB database. Single wallet (USDT).
OTP.py ka **Buy Account** feature aur ek naya **📱 TG Panel** admin button
Store bot ke andar hi live hai.

## Kya kya merge hua

- ✅ **Main menu → 📱 Buy Account** (Phase 1)
  - Country → Year/Price → Buy 1 (live OTP fetch) / Buy Bulk (sessions .zip)
  - 10-min auto-cancel + auto-refund
  - INR pricing, USDT-backed wallet
- ✅ **Admin panel → 📱 TG Panel** (Phase 1: stats stub, Phase 2 me full features)
- ✅ **Unified history**: OTP sale bhi Store bot ke `orders` table me log hoti hai,
  isliye "My Stats" me ek hi jagah dikhega.
- ✅ Sab kuch same MongoDB database pe.

## Setup

```bash
pip install -r requirements.txt
```

`.env` (extends Store bot's existing config):

```
BOT_TOKEN=<store bot token — single token>
ADMIN_ID=<admin uid>
API_ID=<my.telegram.org API ID>       # OTP fetch ke liye
API_HASH=<my.telegram.org API hash>   # OTP fetch ke liye
OTP_SESSIONS_DIR=sessions             # optional, default: sessions/
OTP_LOG_CHANNEL_ID=                   # optional
```

Run:
```bash
python bot.py
```

## Render deployment

This folder includes `render.yaml` for a Render Web Service. Create a new
Render Blueprint from this repository/folder, then add the required values
from `.env.example` as Render environment variables. The service starts with
`python bot.py` and exposes `/healthz` on Render's `PORT` so the web service
can be monitored. It also performs a best-effort self-ping to
`RENDER_EXTERNAL_URL/healthz` every 2 minutes. Set `SELF_PING_URL` only if you
want to override that target.

Important: Render's free plan may spin a web service down after inactivity and
does not provide a contractual 24/7 uptime guarantee. The self-ping is a
keep-alive attempt, not a guaranteed way to bypass Render's free-tier sleep
policy. Guaranteed always-on operation requires an always-on plan or another
suitable host.

## WhatsApp purchase switch

Open the existing admin panel and select **WA PANEL — WhatsApp Section**.
Only configured owners/admins can see and use the **Turn ON/OFF WhatsApp
Sales** control. The state is saved in MongoDB under `wa_sales_enabled`, so
it survives restarts.

- **ON**: users can browse and buy WhatsApp numbers normally.
- **OFF**: new WhatsApp purchases are blocked at the menu, country, package,
  and final confirmation steps, with an unavailable message.
- Existing pending orders are not deleted or changed when sales are switched
  off; administrators can still deliver or refund them.

## Files
- `bot.py` — Store bot main (patched with 3 hooks to otp_module)
- `otp_module.py` — NEW. Buy Account + OTP fetch (Telethon) + TG panel stub
- `database.py`, `features.py`, `config.py`, `lang.py`, `styled_api.py`,
  `emoji_config.py` — unchanged Store bot files

## Session files
Rakh do `sessions/` folder me — `otp_stock.session_file` path yahan point karega.
Phase 2 me admin panel se zip upload karke bulk stock add kar sakoge.

## Phase roadmap
- **Phase 1 (this build)** — Buy Account user flow + minimal TG Panel stats.
- **Phase 2** — Full admin TG Panel: Add Single / Add ZIP stock, Manage Stock,
  Auto-Price, Country management, Test/Delete dead sessions, OTP-specific stats.
- **Phase 3** — Referral bonus mirror, log-channel unified formatting,
  crash-recovery of in-flight orders.


## OTP login flow note

`otp_module.py` / `otp_admin.py` now load `.env` exactly like the original `OTP.py` (`load_dotenv()`), and Add Single Account uses the same Telethon sequence: `TelegramClient(...)` → `connect()` → `send_code_request(phone)` → `sign_in(... phone_code_hash ...)` → optional 2FA.

Keep your real `API_ID` and `API_HASH` in the same `.env` from which you run `python bot.py`. Do not copy placeholder values from `.env.example`.

## Login fix note

`otp_module.py` now uses the exact working Telegram API fallback from your original `OTP.py`. If `.env` contains placeholder/wrong `API_ID` or `API_HASH`, it will ignore that broken pair and use the OTP.py default pair, preventing `api_id/api_hash combination is invalid` during `send_code_request`.

## WhatsApp Section (WP ACCOUNT BUY)

- Main menu buttons: **TG ACCOUNT BUY** (Telegram) and **WP ACCOUNT BUY** (WhatsApp),
  both with premium custom emoji icons (`emoji_config.BTN_EMOJI`).
- `wa_module.py` — user flow: Countries → Package/Price → Order Summary →
  Confirm → live login-code delivery, with `Refresh Status`, `Cancel & Refund`,
  `My Orders`, `Support` and `Main Menu` inline buttons on every screen.
- `wa_admin.py` — **WA Panel** in the Admin Panel: Add Single, Add Bulk
  (`phone|price|category|2fa|note`), Manage Stock, Set Pricing, Pending Orders
  (one-tap login-code delivery / refund) and Statistics.
- Shared wallet (USDT balance, INR pricing), automatic full refund after a
  10-minute delivery SLA, and sales logged to the store `orders` table.
- All WhatsApp copy is in professional English; every message ships with an
  inline keyboard.
