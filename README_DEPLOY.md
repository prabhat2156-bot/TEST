# BASS TG STORE — Bot + Web Admin Panel (Render 24/7 deploy)

This package runs **two things in one process** so it fits Render's free
web-service tier (which only allows one exposed port):

1. **The Telegram bot** — unchanged, still `bot.py`, running in a background thread.
2. **A web admin panel** (Flask) at your Render URL — a full mirror of the bot's
   `/admin` panel, protected by a username + password login.

A built-in **self-ping** thread hits your own `/ping` route every 2 minutes so
the free instance doesn't spin down from inactivity.

---

## 1. Deploy to Render

1. Push this folder to a GitHub repo.
2. On [render.com](https://render.com) → **New → Web Service** → connect the repo.
   (Render will auto-detect `render.yaml` — or set these manually:)
   - **Build Command:** `pip install -r requirements.txt`
   - **Start Command:** `python run.py`
   - **Plan:** Free
3. Add these **Environment Variables** (Render → your service → Environment):

   | Key | Value |
   |---|---|
   | `BOT_TOKEN` | your bot token from @BotFather |
   | `ADMIN_IDS` | your Telegram user ID (comma-separated if more than one) |
   | `WEB_ADMIN_USERNAME` | username you'll use to log into the web panel |
   | `WEB_ADMIN_PASSWORD` | a strong password for the web panel |
   | `FLASK_SECRET_KEY` | any long random string (Render can auto-generate this) |
   | `USDT_TRC20_ADDRESS` | your TRC20 wallet address *(optional — can set later in Settings)* |
   | `USDT_BEP20_ADDRESS` | your BEP20 wallet address *(optional)* |
   | `BINANCE_PAY_ID` | your Binance Pay ID *(optional)* |
   | `LOG_CHANNEL_ID` | a Telegram channel/group ID for order logs *(optional)* |

   Do **not** put `BOT_TOKEN` or passwords directly in code or in `.env` committed
   to git — always use Render's Environment tab.

4. Deploy. Render gives you a URL like `https://bass-tg-store.onrender.com`.
   - Open it → you'll see the **login page**.
   - Log in with `WEB_ADMIN_USERNAME` / `WEB_ADMIN_PASSWORD`.
   - The Telegram bot starts automatically in the background — no extra step.

That's it — the bot and the web panel are now both live 24/7 on the same
free Render service, and `/ping` gets hit every 2 minutes automatically so
it won't sleep.

---

## 2. What the web panel can do

Every feature from the Telegram `/admin` panel is mirrored here, reading and
writing the **same database**, so changes made on the web show up instantly
in the bot and vice versa:

- Dashboard (today + all-time stats)
- Orders (today / all, with full date & time)
- Deposits (today / pending / all) — approve or reject pending ones
- Manual Deposit, Gift Balance
- **Refund Requests** — open a request to see the full order, the exact
  item/credential (ID + password) that was delivered, the user's reason,
  and a live chat thread with the user — then Approve or Reject
- Coupons, Categories, Products, Stock (add/view/clear), Free Items
- Users (search, ban/unban, adjust balance), full per-user history
- Tickets (reply, close)
- Admins (add/remove extra admins)
- Broadcast to all users
- Daily History (last 14 days)
- Settings — bot name/emoji, TRC20/BEP20/Binance Pay addresses, log channel,
  min deposit, low-stock threshold, maintenance mode, referral toggle,
  force-join channels (up to 5)

---

## 3. Local testing (optional)

```bash
pip install -r requirements.txt
export BOT_TOKEN=xxx
export ADMIN_IDS=your_telegram_id
export WEB_ADMIN_USERNAME=admin
export WEB_ADMIN_PASSWORD=test1234
export FLASK_SECRET_KEY=any-string
python run.py
```

Then open `http://localhost:10000`.

---

## 4. Notes & security

- The web admin login is completely separate from Telegram admin IDs — it's
  its own username/password stored (hashed) in the database.
- Change `WEB_ADMIN_PASSWORD` to something strong before going live.
- If you ever suspect your `BOT_TOKEN` was exposed, revoke and regenerate it
  via **@BotFather → /revoke** immediately, then update the Render env var.
- `run.py` restarts the bot's polling loop automatically if it ever crashes,
  so a temporary network blip won't take the bot down.
