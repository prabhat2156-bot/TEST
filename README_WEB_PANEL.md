# 🌐 Web Admin Panel (Render Web Service)

Ek hi Render **Web Service** me bot + browser admin panel dono chalte hain.
`bot.py` start hote hi `render_server.py` panel ko background thread me serve karta hai.

## Deploy (Render)
1. Repo push karo → Render → New → Web Service → Python.
2. Build: `pip install -r requirements.txt` · Start: `python bot.py`
3. Health check path: `/healthz`
4. Environment variables (render.yaml me already listed):
   - `BOT_TOKEN`, `BOT_USERNAME`, `ADMIN_IDS`
   - `MONGODB_URI`, `MONGODB_DB`
   - `OTP_BOT_TOKEN`, `OTP_API_ID`, `OTP_API_HASH`, `OTP_ADMIN_ID`
   - **`ADMIN_USER`**, **`ADMIN_PASS`** → panel login
   - `PANEL_SECRET` (auto-generate)
5. Deploy ke baad `https://<your-service>.onrender.com/login` kholo.

## Panel me kya-kya hai (bot ke admin panel ke saare features)
| Page | Features |
|---|---|
| Dashboard | Today/all-time deposit + buy, users, TG/WA stock, open tickets, refund requests, TXT exports |
| Deposits | Today / All-time / Pending, manual **Credit** ya **Fail**, export |
| Sales/Orders | Today / All-time list, total, **Refund**, export |
| Users | Search, ban/unban, balance +/-, manual deposit credit, **gift + notify**, direct Telegram message, full history |
| Catalog & Stock | Category add/toggle/delete, product add/price/toggle/delete, stock add (bulk lines), remove, clear |
| Free Items | Create, toggle, delete, bulk stock add |
| Coupons | Create (code/discount/max uses), toggle, delete |
| Tickets | Open/all list, chat view, **reply → user ko Telegram par jaata hai**, close |
| Refunds & Resellers | Refund requests approve/reject, reseller add/approve/revoke, withdraw approve/reject |
| Broadcast | All / unbanned / buyers ko message, live progress (sent/failed) |
| Daily Report | Kisi bhi date ka deposits, orders, new users, wallet snapshot |
| TG Accounts | OTP stock, phone se login (OTP + 2FA), group pricing, auto price, wipe country, USDT rate |
| WhatsApp | Bulk add `phone|price|country|2fa|note`, delete, country pricing, sales ON/OFF |
| Settings | Maintenance / referral / WA / OTP / free-items switches, bot name, wallets, min deposit, low stock, log channel, extra admins, raw key-value settings |

Broadcast aur ticket-reply Telegram Bot API se jaate hain, isliye `BOT_TOKEN` zaroori hai.
