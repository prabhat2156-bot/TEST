"""
NEXUS STORE BOT — Configuration
Sensitive values come from Replit Secrets / env vars.
"""

import os

# ── MongoDB (persistent storage — data survives restarts) ─────────────────────
# Get a free MongoDB Atlas connection string at https://www.mongodb.com/atlas
# Format: mongodb+srv://<user>:<password>@<cluster>.mongodb.net/
MONGODB_URI     = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB_NAME = os.environ.get("MONGODB_DB_NAME", "nexus_store_bot")

# ── Telegram Bot ──────────────────────────────────────────────────────────────
BOT_TOKEN    = os.environ.get("BOT_TOKEN", "")
BOT_USERNAME = os.environ.get("BOT_USERNAME", "")

_admin_ids_env = os.environ.get("ADMIN_IDS", "")
ADMIN_IDS = [int(x) for x in _admin_ids_env.split(",") if x.strip().isdigit()]

# ── Log Channel ───────────────────────────────────────────────────────────────
LOG_CHANNEL_ID = os.environ.get("LOG_CHANNEL_ID") or None
if LOG_CHANNEL_ID is not None:
    try:
        LOG_CHANNEL_ID = int(LOG_CHANNEL_ID)
    except ValueError:
        LOG_CHANNEL_ID = None

# ── Force-Join Channels (also editable from Admin -> Settings) ────────────────
# These are defaults; live values come from the DB settings table.
FORCE_JOIN_CHANNELS = []
FORCE_JOIN_URLS     = []

# ── Binance API (for auto-deposit detection) ──────────────────────────────────
BINANCE_API_KEY    = os.environ.get("BINANCE_API_KEY", "")
BINANCE_SECRET_KEY = os.environ.get("BINANCE_SECRET_KEY", "")

# ── Binance Pay (users send to your Binance ID directly) ──────────────────────
# Set to your Binance Pay ID (numeric UID shown in Binance → Profile → Pay ID)
# Requires Binance API with "Enable Reading" permission for Pay history
BINANCE_PAY_ID = os.environ.get("BINANCE_PAY_ID", "")

# ── Blockchain explorer APIs (for verifying USDT deposits on-chain) ───────────
# TronScan API key (optional but recommended — avoids rate-limit failures)
TRONSCAN_API_KEY = os.environ.get("TRONSCAN_API_KEY", "")
# BscScan API key (required for BEP20/BSC deposit verification)
BSCSCAN_API_KEY  = os.environ.get("BSCSCAN_API_KEY", "")

# ── USDT Deposit Addresses ────────────────────────────────────────────────────
# TRC20 (TRON network) address
USDT_TRC20_ADDRESS = os.environ.get("USDT_TRC20_ADDRESS", "")
# BEP20 (BSC network) address
USDT_BEP20_ADDRESS = os.environ.get("USDT_BEP20_ADDRESS", "")

# Legacy single-address support
USDT_DEPOSIT_ADDRESS = os.environ.get("USDT_DEPOSIT_ADDRESS", "")
USDT_NETWORK         = "TRC20"   # kept for compat; actual logic uses both

# ── Deposit Settings ──────────────────────────────────────────────────────────
MIN_DEPOSIT_USDT     = 0.1    # minimum 0.1 USDT
DEPOSIT_TIMEOUT_MINS = 60
DEPOSIT_CHECK_SECS   = 30

# ── VIP System ────────────────────────────────────────────────────────────────
VIP_REFERRALS_NEEDED  = 5
VIP_DISCOUNT_PERCENT  = 5
VIP_DEPOSIT_BONUS_PCT = 5
VIP_MIN_BONUS_DEPOSIT = 10.0
MAX_VIP_MEMBERS       = 5000

# ── Loyalty Tiers ─────────────────────────────────────────────────────────────
TIER_BRONZE_MIN  = 0.0
TIER_SILVER_MIN  = 10.0
TIER_GOLD_MIN    = 50.0
TIER_SILVER_DISC = 3
TIER_GOLD_DISC   = 7

# ── Referral ──────────────────────────────────────────────────────────────────
REFERRAL_BONUS_USDT = 0.5

# ── Reseller Program ──────────────────────────────────────────────────────────
RESELLER_DISCOUNT_PERCENT   = 15
RESELLER_OWNER_COMMISSION   = 20
RESELLER_CREDIT_DELAY_HOURS = 48

# ── Low stock auto-alert ──────────────────────────────────────────────────────
LOW_STOCK_THRESHOLD = 3

# ── Renewal reminder ──────────────────────────────────────────────────────────
RENEWAL_REMINDER_DAYS = 25

# ── Bot Texts ─────────────────────────────────────────────────────────────────
BOT_NAME    = "BASS TG STORE"
BOT_TAGLINE = "Premium digital services with instant automated\ndelivery and 100% secure transactions."
