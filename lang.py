"""
NEXUS STORE BOT — Multi-Language Support
Languages: English, Hindi, Indonesian, Vietnamese
"""

TRANSLATIONS = {

# ═══════════════════════════════════════════════════════════════════════════
#  ENGLISH
# ═══════════════════════════════════════════════════════════════════════════
"en": {
    "lang_name": "🇬🇧 English",

    # Language selection
    "select_language": (
        "🌐 <b>SELECT YOUR LANGUAGE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Please choose your preferred language\nto continue:"
    ),
    "language_set": "✅ Language set to English! Welcome!",

    # Force join
    "force_join_msg": (
        "🔔 <b>JOIN REQUIRED</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚠️ To use this bot, you must join\nour official channel(s) first:\n\n"
        "After joining, tap the button below to verify."
    ),
    "join_btn":      "📢 Join Channel",
    "join_verify":   "✅ I've Joined — Verify",
    "join_not_done": "❌ Please join ALL channels first!",
    "join_success":  "✅ Verified! Welcome to {bot_name}!",

    # Main menu
    "welcome": (
        "{emoji} <b>{bot_name}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "👋 Welcome, <b>{name}</b>!\n\n"
        "<i>{tagline}</i>\n\n"
        "📋 <b>What would you like to do?</b>"
    ),
    "btn_shop":      "Shop",
    "btn_deposit":   "Deposit",
    "btn_profile":   "My Profile",
    "btn_support":   "Support",
    "btn_referral":  "Refer & Earn VIP",
    "btn_language":  "Language",
    "btn_admin":     "Admin Panel",
    "btn_back":      "« Back",
    "btn_home":      "Main Menu",
    "btn_cancel":    "Cancel",
    "btn_cart":      "Cart",
    "btn_free_item": "Free Item",

    # Profile
    "profile_body": (
        "👤 <b>MY PROFILE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🆔 User ID: <code>{uid}</code>\n"
        "👤 Username: {uname}\n\n"
        "💎 <b>WALLET</b>\n"
        "💰 Balance: <b>${balance:.2f} USDT</b>\n\n"
        "🏆 <b>LOYALTY STATUS</b>\n"
        "{tier_emoji} Tier: <b>{tier}</b>\n"
        "🎫 Discount: <b>{disc}% OFF</b>"
        "{vip_badge}\n\n"
        "📊 <b>STATISTICS</b>\n"
        "📦 Total Orders: <b>{orders}</b>\n"
        "💸 Total Spent: <b>${spent:.2f} USDT</b>"
    ),
    "btn_history":  "Purchase History",
    "btn_deposit2": "Add Funds",

    # Shop
    "shop_title": "🛍️ <b>SHOP — Choose Category</b>",
    "shop_empty": "🛍️ <b>SHOP</b>\n\nNo products available yet. Check back soon!",
    "cat_choose_product": "Choose a product:",
    "product_detail": (
        "{emoji} <b>{name}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📝 {desc}\n\n"
        "⏱️ Duration: <b>{duration}</b>\n"
        "💰 Price: <b>${price:.2f} USDT</b>{disc_line}\n\n"
        "💼 Your Balance: <b>${balance:.2f} USDT</b>"
    ),
    "disc_line":        "\n🎫 Your price: <b>${final:.2f}</b> ({disc}% OFF)",
    "in_stock":         "✅ In Stock",
    "out_of_stock":     "❌ Out of Stock",
    "btn_buy":          "Buy Now",
    "btn_add_cart":     "Add to Cart",
    "btn_top_up":       "Top Up ${needed:.2f}",
    "btn_shop_more":    "Shop More",
    "qty_label":        "🔢 Quantity: <b>{qty}</b> × {unit} = <b>{total}</b>",
    "cat_no_products":  "No products in this category yet!",
    "product_not_found": "Product not found.",
    "purchased": (
        "🎉 <b>PURCHASE SUCCESSFUL!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "{emoji} <b>{name}</b>\n\n"
        "📦 <b>Your credentials:</b>\n"
        "<code>{cred}</code>\n\n"
        "💰 Paid: <b>${amount:.2f} USDT</b>\n"
        "💼 Remaining: <b>${balance:.2f} USDT</b>\n\n"
        "📋 Order ID: <code>#{oid}</code>"
    ),
    "insufficient":     "❌ Insufficient balance. You need ${needed:.2f} more USDT.",
    "out_of_stock_buy": "❌ This product is out of stock!",
    "multi_purchased_header": (
        "🎉 <b>PURCHASE SUCCESSFUL!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "{emoji} <b>{name}</b> × {qty}\n\n"
        "📦 <b>Your credentials:</b>\n"
        "💰 Total Paid: <b>${amount:.2f} USDT</b>\n"
        "💼 Remaining: <b>${balance:.2f} USDT</b>"
    ),
    "multi_purchased": (
        "🎉 <b>PURCHASE SUCCESSFUL!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "{emoji} <b>{name}</b> × {qty}\n\n"
        "📦 <b>Your credentials:</b>\n{creds}\n\n"
        "💰 Total Paid: <b>${amount:.2f} USDT</b>\n"
        "💼 Remaining: <b>${balance:.2f} USDT</b>"
    ),

    # ─── WALLET / DEPOSIT DETAIL ───────────────────────────────────────────
    "wallet_title": (
        "👛 <b>MY WALLET</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "💰 Balance: <b>{balance}</b>\n"
        "{tier_emoji} Tier: <b>{tier}</b> ({disc}% off)\n"
        "🛒 Orders: <b>{orders}</b>\n"
        "💵 Total Spent: <b>{spent}</b>"
    ),
    "btn_payment_history": "Payment History",
    "btn_dep_history_wallet": "Deposit History",
    "btn_add_funds":     "Add Funds",
    "wallet_not_started": "Please /start the bot first.",

    "deposit_wallet_text": (
        "💳 <b>WALLET</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "💰 Balance:  <b>${bal:.2f}</b>\n"
        "💎 Total Spent: <b>${spent:.2f}</b>\n"
        "{tier_icon} Membership: <b>{tier_name}</b>\n\n"
        "Choose a payment method below to add funds to your wallet."
    ),
    "btn_binance_pay":   "Binance Pay",
    "btn_usdt_trc20":    "USDT (TRC20)",
    "btn_usdt_bep20":    "USDT (BEP20)",
    "btn_tx_history":    "Transaction History",
    "btn_back_wallet":   "« Back to Wallet",

    "dep_net_pay": (
        "✦ <b>Binance Pay Deposit</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Send USDT to our <b>Binance Pay ID</b>:\n\n"
        "<code>{pay_id}</code>\n\n"
        "💵 Minimum: <b>${min_dep:.2f} USDT</b>\n\n"
        "Enter the <b>USDT amount</b> you want to deposit:"
    ),
    "dep_net_trc20": (
        "🔴 <b>USDT — TRC20 (TRON)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📬 Send to:\n<code>{addr}</code>\n\n"
        "⚡ Verified via <b>TronScan</b>\n\n"
        "💵 Minimum: <b>${min_dep:.2f} USDT</b>\n\n"
        "Enter the <b>USDT amount</b> you want to deposit:"
    ),
    "dep_net_bep20": (
        "🟡 <b>USDT — BEP20 (BSC)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📬 Send to:\n<code>{addr}</code>\n\n"
        "⚡ Verified via <b>BSCScan</b>\n\n"
        "💵 Minimum: <b>${min_dep:.2f} USDT</b>\n\n"
        "Enter the <b>USDT amount</b> you want to deposit:"
    ),
    "dep_not_configured": "❌ Not configured",

    "dep_pay_caption": (
        "✦ <b>BINANCE PAY DEPOSIT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📲 <b>Binance Pay ID:</b>\n"
        "<code>{pay_id}</code>\n\n"
        "💵 <b>Send EXACTLY:</b> <code>{expected:.3f}</code> USDT\n\n"
        "📝 <b>Unique Note (include in remarks):</b>\n"
        "<code>{note}</code>\n\n"
        "⚠️ <i>Include the note in payment remarks so owner can identify your payment.</i>\n\n"
        "After sending, tap <b>✅ I Have Paid</b> below."
    ),
    "btn_i_have_paid":   "I Have Paid",

    "dep_chain_caption": (
        "{net_icon} <b>USDT {network} DEPOSIT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📬 <b>Send to this address:</b>\n"
        "<code>{addr}</code>\n\n"
        "💵 <b>Send EXACTLY:</b> <code>{expected:.3f}</code> USDT\n\n"
        "⚠️ <i>Send the exact amount — it uniquely identifies your payment.</i>\n\n"
        "After sending, tap <b>✅ I Have Paid</b> to submit your Transaction Hash.\n"
        "Bot will verify on <b>{explorer}</b> automatically."
    ),
    "btn_submit_tx_hash": "I Have Paid — Submit TX Hash",

    "dep_submit_hash_prompt": (
        "🔍 <b>Submit Transaction Hash</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Paste the <b>Transaction Hash (TxID)</b> from your wallet or blockchain explorer:\n\n"
        "<i>(It starts with 0x for BEP20, or is 64 hex chars for TRC20)</i>\n\n"
        "/cancel to go back"
    ),
    "dep_session_expired":  "❌ Session expired. Please start deposit again.",
    "dep_not_found":        "❌ Deposit not found or already processed.",
    "dep_invalid_hash":     "⚠️ That doesn't look like a valid TX hash. Please try again.",
    "dep_duplicate_hash": (
        "❌ <b>Deposit Failed</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Reason:</b> This transaction hash has already been used. Please contact admin."
    ),
    "dep_tx_too_old": (
        "❌ <b>Deposit Failed</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Reason:</b> This transaction happened before your deposit request was created — "
        "it can't belong to this deposit. Please submit the correct TX hash, or contact admin."
    ),
    "dep_verified_credited": (
        "✅ <b>Deposit Verified & Credited!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "💵 Amount: <b>{amount}</b>\n"
        "💰 New Balance: <b>${new_bal:.2f}</b>\n\n"
        "Thank you! You can now shop. 🛍"
    ),
    "btn_open_shop":      "Open Shop",
    "dep_not_verified": (
        "⚠️ <b>COULD NOT VERIFY AUTOMATICALLY</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "That Transaction ID was not found or amount didn't match.\n\n"
        "If you paid just now, the blockchain can take a few minutes "
        "to confirm — wait a moment and tap <b>Check Again</b>.\n\n"
        "If you're sure it went through, double-check the TX hash you "
        "copied, or tap <b>Notify Admin</b>."
    ),
    "btn_check_again":    "Check Again",
    "btn_notify_admin":   "Notify Admin",
    "dep_recheck_rechecking": "Re-checking…",
    "dep_already_processed":  "✅ This deposit was already processed.",
    "dep_still_not_found": (
        "⚠️ <b>Still not found on-chain.</b>\n\n"
        "Try again in a minute, or tap Notify Admin."
    ),
    "dep_check_failed_alert": "Still not found. Try again in a minute or notify admin.",
    "dep_duplicate_recheck": (
        "❌ <b>Deposit Failed</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Reason:</b> This transaction hash has already been used. Please contact admin."
    ),
    "dep_tx_too_old_recheck": (
        "❌ <b>Deposit Failed</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Reason:</b> This transaction happened before your deposit request was created. "
        "Please contact admin."
    ),
    "dep_verified_recheck": (
        "✅ <b>Deposit Verified & Credited!</b>\n\n"
        "💵 {amount} → 💰 Balance: ${new_bal:.2f}"
    ),

    "dep_admin_notified": (
        "📢 <b>Admin Notified</b>\n\n"
        "We've sent your deposit details to the admin for manual review.\n"
        "You'll be notified once it's approved.\n\n"
        "Deposit #{dep_id:04d} — {amount}"
    ),
    "dep_pay_submitted": (
        "✅ <b>Payment Submitted!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "💵 Amount: <b>{amount}</b>\n"
        "📝 Note: <code>{note}</code>\n\n"
        "Admin has been notified and will approve shortly.\n"
        "You'll receive a message once your balance is credited."
    ),
    "dep_rejected_user": (
        "❌ <b>Deposit Rejected</b>\n\n"
        "Your deposit of {amount} was reviewed and rejected.\n"
        "If you believe this is a mistake, open a support ticket."
    ),

    # Deposit (existing keys)
    "deposit_body": (
        "💰 <b>DEPOSIT USDT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔗 <b>Supported Networks:</b>\n\n"
        "🟡 <b>TRC20 (TRON)</b>\n"
        "<code>{trc20_address}</code>\n\n"
        "🟠 <b>BEP20 (BSC)</b>\n"
        "<code>{bep20_address}</code>\n\n"
        "⚠️ Min deposit: <b>${min_dep:.2f} USDT</b>\n"
        "⚠️ Send exact amount shown after starting deposit\n\n"
        "{addr_warn}"
    ),
    "deposit_body_single": (
        "💰 <b>DEPOSIT USDT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔗 Network: <b>{network}</b>\n"
        "📬 Address:\n<code>{address}</code>\n\n"
        "⚠️ Min deposit: <b>${min_dep:.2f} USDT</b>\n\n"
        "{addr_warn}"
    ),
    "addr_warn":        "⚠️ Deposit address not configured yet. Contact support.",
    "btn_start_dep":    "Start Deposit",
    "btn_dep_history":  "Deposit History",
    "dep_enter_amount": (
        "💰 <b>ENTER DEPOSIT AMOUNT</b>\n\n"
        "Minimum: <b>${min:.2f} USDT</b>\n\n"
        "Enter the amount you want to deposit:"
    ),
    "dep_select_network": (
        "🌐 <b>SELECT NETWORK</b>\n\n"
        "Choose the network you will use to send USDT:"
    ),
    "btn_trc20": "TRC20 (TRON)",
    "btn_bep20": "BEP20 (BSC)",
    "dep_invalid":    "❌ Invalid amount. Please enter a number like 5 or 10.5",
    "dep_too_small":  "❌ Minimum deposit is <b>${min:.2f} USDT</b>. Please enter a higher amount.",
    "dep_created": (
        "✅ <b>DEPOSIT REQUEST CREATED</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🆔 Request ID: <code>#{dep_id}</code>\n"
        "🔗 Network: <b>{network}</b>\n"
        "📬 Send to:\n<code>{address}</code>\n\n"
        "💵 <b>Send EXACTLY:</b>\n"
        "<code>{amount}</code> USDT\n\n"
        "⏰ Expires in: <b>{timeout} minutes</b>\n\n"
        "⚠️ Send the EXACT amount shown above.\n"
        "Bot auto-detects via Binance API."
    ),
    "dep_confirmed": (
        "🎉 <b>DEPOSIT CONFIRMED!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "✅ <b>${amount:.2f} USDT</b> has been added to your wallet!\n"
        "{vip_line}\n"
        "💼 New Balance: <b>${balance:.2f} USDT</b>"
    ),
    "dep_expired": "⏰ Deposit request <code>#{dep_id}</code> for ${amount:.2f} USDT has expired.",
    "vip_dep_line": "🎁 VIP bonus: +${bonus:.2f} USDT",

    # ─── DEPOSIT HISTORY ──────────────────────────────────────────────────
    "dep_history_title":  "📋 <b>Deposit History</b>",
    "dep_history_empty":  "📋 <b>Deposit History</b>\n\n<i>No deposits yet.</i>",
    "dep_status_title": (
        "💰 <b>Deposit Status</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🆔 Request: <code>#{dep_id}</code>\n"
        "🔗 Network: <b>{net}</b>\n"
        "💵 Expected: <b>{amount}</b>\n"
        "📊 Status: {status_icon} <b>{status_label}</b>\n"
        "🔑 TX: <code>{tx}</code>\n"
        "⏰ Created: {created_at}"
    ),
    "btn_refresh":        "Refresh",
    "dep_status_pending":    "Pending",
    "dep_status_completed":  "Credited ✅",
    "dep_status_expired":    "Expired ❌",
    "dep_status_cancelled":  "Cancelled",

    # Referral
    "referral_title": (
        "🎁 <b>REFER & EARN VIP</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📨 Your referral link:\n"
        "<code>https://t.me/{bot}?start={code}</code>\n\n"
        "👥 Referrals: <b>{count}/{needed}</b>\n"
        "🌟 VIP Status: {vip}\n\n"
        "🎁 Earn <b>{bonus} USDT</b> per friend's deposit!\n"
        "Refer <b>{needed}</b> friends to unlock VIP status!"
    ),
    "vip_badge": "\n🌟 VIP Member",

    # Referral bonus (sent to referrer)
    "referral_bonus_msg": (
        "💰 <b>Referral Deposit Bonus!</b>\n\n"
        "Your friend deposited {amount}. "
        "🎁 You earned <b>{bonus}</b>!"
    ),

    # Support
    "support_title": (
        "🎧 <b>SUPPORT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Need help? Open a support ticket.\n"
        "Our team responds quickly.\n\n"
        "🔐 All conversations are private."
    ),
    "btn_new_ticket":  "New Support Ticket",
    "btn_my_tickets":  "My Tickets",
    "ticket_create_msg": (
        "🆕 <b>CREATE TICKET</b>\n\n"
        "Describe your issue in detail.\n"
        "<i>Include Order ID if about a purchase.</i>\n\n"
        "Type /cancel to abort."
    ),
    "ticket_describe_issue": "Please describe your issue.",
    "ticket_created": (
        "✅ <b>Ticket #{tid:04d} created!</b>\n\n"
        "📝 {subject}\n\n"
        "🎧 Our team will respond shortly."
    ),
    "ticket_reply_prompt": "📩 <b>Reply to Ticket #{tid:04d}</b>\n\nType your message:\n\n/cancel to abort.",
    "ticket_reply_sent":   "✅ Reply sent for Ticket #{tid:04d}",
    "btn_view_ticket":     "View Ticket #{tid:04d}",
    "btn_send_reply":      "Reply",
    "btn_close_ticket":    "Close Ticket",
    "no_open_tickets":     "✅ No open tickets.",
    "ticket_closed_user":  "✅ Ticket #{tid:04d} has been closed.",
    "ticket_list_title":   "📋 <b>My Tickets</b>",
    "ticket_not_found":    "Ticket not found.",
    "ticket_admin_reply": (
        "🎧 <b>Support Reply — Ticket #{tid:04d}</b>\n\n{msg}"
    ),

    # ─── ORDER DETAIL ─────────────────────────────────────────────────────
    "order_status_completed": "✅ Completed",
    "order_status_refunded":  "♻️ Refunded",
    "order_no_credential":    "<i>No credential data</i>",
    "order_not_found":        "Order not found.",
    "btn_request_refund":     "Request Refund",
    "btn_refund_pending":     "Refund Pending…",
    "history_title":          "📋 <b>Purchase History</b>",
    "order_detail_body": (
        "📦 <b>Order #{oid:04d}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🛍️ {product_name}\n"
        "💰 {amount}\n"
        "📊 {status}\n"
        "📅 {date}\n\n"
        "{cred_text}"
    ),

    # ─── REFUND ───────────────────────────────────────────────────────────
    "refund_order_not_found":     "Order not found.",
    "refund_already_refunded":    "This order is already refunded.",
    "refund_already_pending":     "You already have a pending refund request.",
    "refund_prompt": (
        "📦 <b>Refund Request — Order #{oid:04d}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🛍 <b>{product_name}</b> — {amount}\n\n"
        "✍️ Please type your reason for the refund request:\n"
        "<i>(or /cancel to go back)</i>"
    ),
    "refund_error":          "❌ Something went wrong. Please try again.",
    "refund_order_missing":  "❌ Order not found.",
    "refund_too_short":      "⚠️ Reason is too short. Please write at least 5 characters.",
    "refund_submitted": (
        "✅ <b>Refund request submitted!</b>\n\n"
        "📦 Order #{oid:04d} — {product_name}\n"
        "💬 Reason: <i>{reason}</i>\n\n"
        "⏳ Admin will review your request and respond shortly."
    ),
    "refund_approved": (
        "✅ <b>Refund Approved!</b>\n\n"
        "📦 Order #{oid:04d} — {product_name}\n"
        "💰 <b>{amount}</b> has been credited to your wallet balance.\n\n"
        "🙏 Thank you for your patience."
    ),
    "btn_my_profile":       "My Profile",
    "refund_rejected": (
        "❌ <b>Refund Request Rejected</b>\n\n"
        "📦 Order #{oid:04d}{product_part}\n\n"
        "💬 Your refund request has been reviewed and rejected by the admin.\n"
        "If you believe this is a mistake, please open a support ticket."
    ),
    "btn_support_ticket":   "Support",

    # ─── CART ─────────────────────────────────────────────────────────────
    "maintenance":         "🔧 <b>BOT UNDER MAINTENANCE</b>\n\nWe'll be back soon!",
    "cancelled":           "❌ Cancelled.",
    "cart_empty":          "🛒 Your cart is empty.",
    "cart_title":          "🛒 <b>YOUR CART</b>",
    "cart_view_title":     "🛒 <b>YOUR CART</b>",
    "cart_total_line":     "💰 <b>Total: {total}</b>",
    "cart_balance_line":   "💼 Balance: {balance}",
    "cart_item_oos":       "❌ {name} is out of stock!",
    "cart_checkout_done": (
        "🎉 <b>CHECKOUT COMPLETE!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    ),
    "cart_checkout_total": "💰 Total: {total}\n💼 Balance: {balance}",
    "btn_checkout":        "Checkout",
    "btn_clear_cart":      "Clear Cart",
    "btn_cart_added":      "Added to cart!",
    "history_empty":       "📋 No purchases yet.",
    "order_detail_title":  "📦 <b>ORDER #{oid:04d}</b>",

    # ─── FREE ITEMS ───────────────────────────────────────────────────────
    "free_item_already_claimed": (
        "🎁 <b>FREE ITEM</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "✅ You've already claimed your free item.\n"
        "Each user can claim <b>only one</b> free item — thanks for being with us!"
    ),
    "free_item_none_available": (
        "🎁 <b>FREE ITEM</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "😔 No free items available right now. Check back later!"
    ),
    "free_item_list_header": (
        "🎁 <b>FREE ITEM</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Pick ONE free item below — you can only claim once ever:"
    ),
    "free_item_stock_line":     "{emoji} <b>{name}</b>  (📦 {count} left)",
    "btn_claim_free":           "{emoji} Claim {name}",
    "free_item_not_available":  "Not available.",
    "free_item_already_alert":  "You've already claimed your free item.",
    "free_item_oos_alert":      "Out of stock — try another item.",
    "free_item_claimed_alert":  "🎉 Claimed!",
    "free_item_claimed_msg": (
        "🎉 <b>FREE ITEM CLAIMED!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "{emoji} <b>{name}</b>\n\n"
        "<code>{data}</code>\n\n"
        "Enjoy! 🙌"
    ),
    "free_item_broadcast": (
        "🎁🎉 <b>FREE ITEM AVAILABLE!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "{emoji} <b>{name}</b> is now up for grabs — FREE!\n\n"
        "⚠️ Only <b>1 free item per user, ever</b> — first come, first served.\n"
        "Tap below to claim it now 👇"
    ),
    "btn_claim_now":         "{emoji} Claim Now",

    # ─── ADMIN NOTIFICATIONS TO USERS ─────────────────────────────────────
    "admin_balance_added": (
        "💰 <b>Balance Added!</b>\n\n"
        "+{amount} credited by admin.\n"
        "New balance: {balance}"
    ),
    "admin_deposit_credited": (
        "💰 <b>Deposit Credited!</b>\n\n"
        "✅ {amount} has been added to your wallet by admin.\n"
        "💼 New balance: <b>{balance}</b>"
    ),
    "admin_gift_received": (
        "🎁 <b>Gift Received!</b>\n\n"
        "An admin has gifted you <b>{amount}</b>!\n"
        "💼 New Balance: <b>{balance}</b>"
    ),
    "admin_refund_processed": (
        "♻️ <b>Refund Processed</b>\n\n"
        "Order <code>#{oid:04d}</code> has been refunded.\n"
        "💰 {amount} returned to your wallet."
    ),
    "wd_approved_msg":   "✅ Your withdrawal of {amount} has been processed!",
    "wd_rejected_msg":   "❌ Your withdrawal of {amount} was rejected. Contact support.",

    # ─── RENEWAL REMINDER ─────────────────────────────────────────────────
    "renewal_reminder": (
        "🔔 <b>Renewal Reminder</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Your <b>{product_name}</b> subscription may be expiring soon!\n\n"
        "Head to the shop to renew."
    ),
    "btn_shop_now":    "Shop Now",

    # ─── RESELLER ─────────────────────────────────────────────────────────
    "reseller_not_approved": (
        "🏪 <b>RESELLER PROGRAM</b>\n\n"
        "Apply to become a reseller and buy products at wholesale prices!\n\n"
        "Contact an admin to get approved."
    ),
    "reseller_panel": (
        "🏪 <b>RESELLER PANEL</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🏷️ Discount: <b>{discount}% OFF</b>\n"
        "💰 Available: <b>{available}</b>\n"
        "⏳ Pending: <b>{pending}</b>\n"
        "💸 Withdrawn: <b>{withdrawn}</b>"
    ),
    "btn_request_withdrawal": "Request Withdrawal",
},

# ═══════════════════════════════════════════════════════════════════════════
#  HINDI
# ═══════════════════════════════════════════════════════════════════════════
"hi": {
    "lang_name": "🇮🇳 हिंदी",

    "select_language": (
        "🌐 <b>अपनी भाषा चुनें</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "कृपया जारी रखने के लिए अपनी\nपसंदीदा भाषा चुनें:"
    ),
    "language_set": "✅ भाषा हिंदी में सेट हो गई! स्वागत है!",

    "force_join_msg": (
        "🔔 <b>जुड़ना जरूरी है</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚠️ इस बॉट का उपयोग करने के लिए,\nपहले हमारे चैनल से जुड़ें:\n\n"
        "जुड़ने के बाद नीचे बटन दबाएं।"
    ),
    "join_btn":      "📢 चैनल जॉइन करें",
    "join_verify":   "✅ मैं जुड़ गया — वेरीफाई करें",
    "join_not_done": "❌ पहले सभी चैनल जॉइन करें!",
    "join_success":  "✅ वेरीफाई! {bot_name} में आपका स्वागत है!",

    "welcome": (
        "{emoji} <b>{bot_name}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "👋 स्वागत है, <b>{name}</b>!\n\n"
        "<i>{tagline}</i>\n\n"
        "📋 <b>क्या करना चाहते हैं?</b>"
    ),
    "btn_shop":      "शॉप",
    "btn_deposit":   "डिपॉजिट",
    "btn_profile":   "प्रोफाइल",
    "btn_support":   "सहायता",
    "btn_referral":  "रेफर करें",
    "btn_language":  "भाषा",
    "btn_admin":     "एडमिन पैनल",
    "btn_back":      "« वापस",
    "btn_home":      "मुख्य मेनू",
    "btn_cancel":    "रद्द करें",
    "btn_cart":      "कार्ट",
    "btn_free_item": "मुफ्त आइटम",

    "profile_body": (
        "👤 <b>मेरी प्रोफाइल</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🆔 यूजर ID: <code>{uid}</code>\n"
        "👤 यूजरनेम: {uname}\n\n"
        "💎 <b>वॉलेट</b>\n"
        "💰 बैलेंस: <b>${balance:.2f} USDT</b>\n\n"
        "🏆 <b>लॉयल्टी स्टेटस</b>\n"
        "{tier_emoji} टियर: <b>{tier}</b>\n"
        "🎫 छूट: <b>{disc}% OFF</b>"
        "{vip_badge}\n\n"
        "📊 <b>आंकड़े</b>\n"
        "📦 कुल ऑर्डर: <b>{orders}</b>\n"
        "💸 कुल खर्च: <b>${spent:.2f} USDT</b>"
    ),
    "btn_history":  "खरीद इतिहास",
    "btn_deposit2": "फंड जोड़ें",

    "shop_title": "🛍️ <b>शॉप — कैटेगरी चुनें</b>",
    "shop_empty": "🛍️ <b>शॉप</b>\n\nअभी कोई उत्पाद उपलब्ध नहीं है।",
    "cat_choose_product": "एक उत्पाद चुनें:",
    "product_detail": (
        "{emoji} <b>{name}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📝 {desc}\n\n"
        "⏱️ अवधि: <b>{duration}</b>\n"
        "💰 कीमत: <b>${price:.2f} USDT</b>{disc_line}\n\n"
        "💼 आपका बैलेंस: <b>${balance:.2f} USDT</b>"
    ),
    "disc_line":        "\n🎫 आपकी कीमत: <b>${final:.2f}</b> ({disc}% छूट)",
    "in_stock":         "✅ स्टॉक में",
    "out_of_stock":     "❌ स्टॉक खत्म",
    "btn_buy":          "अभी खरीदें",
    "btn_add_cart":     "कार्ट में जोड़ें",
    "btn_top_up":       "${needed:.2f} जोड़ें",
    "btn_shop_more":    "और खरीदें",
    "qty_label":        "🔢 मात्रा: <b>{qty}</b> × {unit} = <b>{total}</b>",
    "cat_no_products":  "इस कैटेगरी में अभी कोई उत्पाद नहीं है!",
    "product_not_found": "उत्पाद नहीं मिला।",
    "purchased": (
        "🎉 <b>खरीदारी सफल!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "{emoji} <b>{name}</b>\n\n"
        "📦 <b>आपके क्रेडेंशियल:</b>\n"
        "<code>{cred}</code>\n\n"
        "💰 भुगतान: <b>${amount:.2f} USDT</b>\n"
        "💼 शेष: <b>${balance:.2f} USDT</b>\n\n"
        "📋 ऑर्डर ID: <code>#{oid}</code>"
    ),
    "insufficient":     "❌ अपर्याप्त बैलेंस। आपको ${needed:.2f} USDT और चाहिए।",
    "out_of_stock_buy": "❌ यह उत्पाद स्टॉक में नहीं है!",
    "multi_purchased_header": (
        "🎉 <b>खरीद सफल!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "{emoji} <b>{name}</b> × {qty}\n\n"
        "📦 <b>आपकी क्रेडेंशियल:</b>\n"
        "💰 कुल भुगतान: <b>${amount:.2f} USDT</b>\n"
        "💼 शेष: <b>${balance:.2f} USDT</b>"
    ),
    "multi_purchased": (
        "🎉 <b>खरीदारी सफल!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "{emoji} <b>{name}</b> × {qty}\n\n"
        "📦 <b>आपके क्रेडेंशियल:</b>\n{creds}\n\n"
        "💰 कुल: <b>${amount:.2f} USDT</b>\n"
        "💼 शेष: <b>${balance:.2f} USDT</b>"
    ),

    # ─── WALLET / DEPOSIT DETAIL ───────────────────────────────────────────
    "wallet_title": (
        "👛 <b>मेरा वॉलेट</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "💰 बैलेंस: <b>{balance}</b>\n"
        "{tier_emoji} टियर: <b>{tier}</b> ({disc}% छूट)\n"
        "🛒 ऑर्डर: <b>{orders}</b>\n"
        "💵 कुल खर्च: <b>{spent}</b>"
    ),
    "btn_payment_history":    "भुगतान इतिहास",
    "btn_dep_history_wallet": "डिपॉजिट इतिहास",
    "btn_add_funds":          "फंड जोड़ें",
    "wallet_not_started":     "कृपया पहले /start करें।",

    "deposit_wallet_text": (
        "💳 <b>वॉलेट</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "💰 बैलेंस:  <b>${bal:.2f}</b>\n"
        "💎 कुल खर्च: <b>${spent:.2f}</b>\n"
        "{tier_icon} सदस्यता: <b>{tier_name}</b>\n\n"
        "नीचे पेमेंट विधि चुनें और अपने वॉलेट में पैसे जोड़ें।"
    ),
    "btn_binance_pay":   "Binance Pay",
    "btn_usdt_trc20":    "USDT (TRC20)",
    "btn_usdt_bep20":    "USDT (BEP20)",
    "btn_tx_history":    "ट्रांज़ैक्शन इतिहास",
    "btn_back_wallet":   "« वॉलेट पर वापस",

    "dep_net_pay": (
        "✦ <b>Binance Pay डिपॉजिट</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "हमारे <b>Binance Pay ID</b> पर USDT भेजें:\n\n"
        "<code>{pay_id}</code>\n\n"
        "💵 न्यूनतम: <b>${min_dep:.2f} USDT</b>\n\n"
        "जितना डिपॉजिट करना है वो <b>USDT राशि</b> दर्ज करें:"
    ),
    "dep_net_trc20": (
        "🔴 <b>USDT — TRC20 (TRON)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📬 इस पते पर भेजें:\n<code>{addr}</code>\n\n"
        "⚡ <b>TronScan</b> से वेरीफाई किया जाएगा\n\n"
        "💵 न्यूनतम: <b>${min_dep:.2f} USDT</b>\n\n"
        "जितना डिपॉजिट करना है वो <b>USDT राशि</b> दर्ज करें:"
    ),
    "dep_net_bep20": (
        "🟡 <b>USDT — BEP20 (BSC)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📬 इस पते पर भेजें:\n<code>{addr}</code>\n\n"
        "⚡ <b>BSCScan</b> से वेरीफाई किया जाएगा\n\n"
        "💵 न्यूनतम: <b>${min_dep:.2f} USDT</b>\n\n"
        "जितना डिपॉजिट करना है वो <b>USDT राशि</b> दर्ज करें:"
    ),
    "dep_not_configured": "❌ कॉन्फ़िगर नहीं हुआ",

    "dep_pay_caption": (
        "✦ <b>BINANCE PAY डिपॉजिट</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📲 <b>Binance Pay ID:</b>\n"
        "<code>{pay_id}</code>\n\n"
        "💵 <b>बिल्कुल यही भेजें:</b> <code>{expected:.3f}</code> USDT\n\n"
        "📝 <b>विशेष नोट (रिमार्क्स में डालें):</b>\n"
        "<code>{note}</code>\n\n"
        "⚠️ <i>पेमेंट में यह नोट जरूर डालें ताकि ओनर आपकी पेमेंट पहचान सके।</i>\n\n"
        "भेजने के बाद नीचे <b>✅ मैंने भुगतान कर दिया</b> दबाएं।"
    ),
    "btn_i_have_paid":   "मैंने भुगतान कर दिया",

    "dep_chain_caption": (
        "{net_icon} <b>USDT {network} डिपॉजिट</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📬 <b>इस पते पर भेजें:</b>\n"
        "<code>{addr}</code>\n\n"
        "💵 <b>बिल्कुल यही भेजें:</b> <code>{expected:.3f}</code> USDT\n\n"
        "⚠️ <i>बिल्कुल सही राशि भेजें — यही आपकी पेमेंट पहचानती है।</i>\n\n"
        "भेजने के बाद <b>✅ मैंने भुगतान कर दिया</b> दबाकर TX Hash दर्ज करें।\n"
        "बॉट <b>{explorer}</b> से स्वतः वेरीफाई करेगा।"
    ),
    "btn_submit_tx_hash": "मैंने भुगतान कर दिया — TX Hash दर्ज करें",

    "dep_submit_hash_prompt": (
        "🔍 <b>ट्रांज़ैक्शन हैश दर्ज करें</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "अपने वॉलेट या ब्लॉकचेन एक्सप्लोरर से <b>TX Hash (TxID)</b> पेस्ट करें:\n\n"
        "<i>(BEP20 के लिए 0x से शुरू होता है, TRC20 के लिए 64 हेक्स कैरेक्टर होते हैं)</i>\n\n"
        "/cancel से वापस जाएं"
    ),
    "dep_session_expired":  "❌ सेशन समाप्त हो गया। कृपया फिर से डिपॉजिट शुरू करें।",
    "dep_not_found":        "❌ डिपॉजिट नहीं मिला या पहले से प्रोसेस हो चुका है।",
    "dep_invalid_hash":     "⚠️ यह TX Hash सही नहीं लगता। कृपया फिर प्रयास करें।",
    "dep_duplicate_hash": (
        "❌ <b>डिपॉजिट असफल</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>कारण:</b> यह TX Hash पहले से इस्तेमाल हो चुका है। कृपया एडमिन से संपर्क करें।"
    ),
    "dep_tx_too_old": (
        "❌ <b>डिपॉजिट असफल</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>कारण:</b> यह ट्रांज़ैक्शन आपकी डिपॉजिट रिक्वेस्ट से पहले हुआ था — "
        "यह इस डिपॉजिट से संबंधित नहीं हो सकता। सही TX Hash दर्ज करें या एडमिन से संपर्क करें।"
    ),
    "dep_verified_credited": (
        "✅ <b>डिपॉजिट वेरीफाई और क्रेडिट हो गया!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "💵 राशि: <b>{amount}</b>\n"
        "💰 नया बैलेंस: <b>${new_bal:.2f}</b>\n\n"
        "धन्यवाद! अब आप शॉपिंग कर सकते हैं। 🛍"
    ),
    "btn_open_shop":      "शॉप खोलें",
    "dep_not_verified": (
        "⚠️ <b>स्वचालित वेरीफिकेशन असफल</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "TX Hash नहीं मिला या राशि मेल नहीं खाती।\n\n"
        "अगर अभी भुगतान किया है तो ब्लॉकचेन को कुछ मिनट लग सकते हैं — "
        "थोड़ी देर बाद <b>फिर जांचें</b> दबाएं।\n\n"
        "अगर आपको यकीन है कि भुगतान हुआ है तो TX Hash जांचें या <b>एडमिन को सूचित करें</b>।"
    ),
    "btn_check_again":    "फिर जांचें",
    "btn_notify_admin":   "एडमिन को सूचित करें",
    "dep_recheck_rechecking": "फिर से जांच हो रही है…",
    "dep_already_processed":  "✅ यह डिपॉजिट पहले से प्रोसेस हो चुका है।",
    "dep_still_not_found": (
        "⚠️ <b>अभी भी ब्लॉकचेन पर नहीं मिला।</b>\n\n"
        "एक मिनट बाद फिर कोशिश करें, या एडमिन को सूचित करें।"
    ),
    "dep_check_failed_alert": "अभी भी नहीं मिला। एक मिनट बाद फिर कोशिश करें या एडमिन को सूचित करें।",
    "dep_duplicate_recheck": (
        "❌ <b>डिपॉजिट असफल</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>कारण:</b> यह TX Hash पहले से इस्तेमाल हो चुका है। कृपया एडमिन से संपर्क करें।"
    ),
    "dep_tx_too_old_recheck": (
        "❌ <b>डिपॉजिट असफल</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>कारण:</b> यह ट्रांज़ैक्शन डिपॉजिट रिक्वेस्ट से पहले हुआ था। कृपया एडमिन से संपर्क करें।"
    ),
    "dep_verified_recheck": (
        "✅ <b>डिपॉजिट वेरीफाई और क्रेडिट हो गया!</b>\n\n"
        "💵 {amount} → 💰 बैलेंस: ${new_bal:.2f}"
    ),

    "dep_admin_notified": (
        "📢 <b>एडमिन को सूचित किया गया</b>\n\n"
        "आपके डिपॉजिट की जानकारी मैन्युअल समीक्षा के लिए एडमिन को भेजी गई है।\n"
        "अप्रूव होने पर आपको सूचित किया जाएगा।\n\n"
        "डिपॉजिट #{dep_id:04d} — {amount}"
    ),
    "dep_pay_submitted": (
        "✅ <b>भुगतान जमा हो गया!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "💵 राशि: <b>{amount}</b>\n"
        "📝 नोट: <code>{note}</code>\n\n"
        "एडमिन को सूचित कर दिया गया है और वो जल्द अप्रूव करेंगे।\n"
        "बैलेंस जुड़ने पर आपको मैसेज मिलेगा।"
    ),
    "dep_rejected_user": (
        "❌ <b>डिपॉजिट अस्वीकार</b>\n\n"
        "{amount} का आपका डिपॉजिट समीक्षा के बाद अस्वीकार कर दिया गया।\n"
        "अगर आपको लगता है यह गलती है, तो सपोर्ट टिकट खोलें।"
    ),

    "deposit_body": (
        "💰 <b>USDT डिपॉजिट करें</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔗 <b>समर्थित नेटवर्क:</b>\n\n"
        "🟡 <b>TRC20 (TRON)</b>\n"
        "<code>{trc20_address}</code>\n\n"
        "🟠 <b>BEP20 (BSC)</b>\n"
        "<code>{bep20_address}</code>\n\n"
        "⚠️ न्यूनतम डिपॉजिट: <b>${min_dep:.2f} USDT</b>\n\n"
        "{addr_warn}"
    ),
    "deposit_body_single": (
        "💰 <b>USDT डिपॉजिट</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔗 नेटवर्क: <b>{network}</b>\n"
        "📬 पता:\n<code>{address}</code>\n\n"
        "⚠️ न्यूनतम: <b>${min_dep:.2f} USDT</b>\n\n"
        "{addr_warn}"
    ),
    "addr_warn":        "⚠️ डिपॉजिट एड्रेस अभी सेट नहीं है। सपोर्ट से संपर्क करें।",
    "btn_start_dep":    "डिपॉजिट शुरू करें",
    "btn_dep_history":  "डिपॉजिट इतिहास",
    "dep_enter_amount": "💰 <b>राशि दर्ज करें</b>\n\nन्यूनतम: <b>${min:.2f} USDT</b>\n\nकितना डिपॉजिट करना चाहते हैं?",
    "dep_select_network": "🌐 <b>नेटवर्क चुनें</b>\n\nUSDT भेजने के लिए नेटवर्क चुनें:",
    "btn_trc20": "TRC20 (TRON)",
    "btn_bep20": "BEP20 (BSC)",
    "dep_invalid":   "❌ गलत राशि। 5 या 10.5 जैसा नंबर दर्ज करें।",
    "dep_too_small": "❌ न्यूनतम डिपॉजिट <b>${min:.2f} USDT</b> है।",
    "dep_created": (
        "✅ <b>डिपॉजिट रिक्वेस्ट बनाई गई</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🆔 रिक्वेस्ट ID: <code>#{dep_id}</code>\n"
        "🔗 नेटवर्क: <b>{network}</b>\n"
        "📬 इस एड्रेस पर भेजें:\n<code>{address}</code>\n\n"
        "💵 <b>बिल्कुल यही राशि भेजें:</b>\n"
        "<code>{amount}</code> USDT\n\n"
        "⏰ समाप्त होगी: <b>{timeout} मिनट में</b>"
    ),
    "dep_confirmed": (
        "🎉 <b>डिपॉजिट कन्फर्म!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "✅ <b>${amount:.2f} USDT</b> आपके वॉलेट में जोड़े गए!\n"
        "{vip_line}\n"
        "💼 नया बैलेंस: <b>${balance:.2f} USDT</b>"
    ),
    "dep_expired":   "⏰ डिपॉजिट रिक्वेस्ट <code>#{dep_id}</code> समाप्त हो गई।",
    "vip_dep_line":  "🎁 VIP बोनस: +${bonus:.2f} USDT",

    "dep_history_title":  "📋 <b>डिपॉजिट इतिहास</b>",
    "dep_history_empty":  "📋 <b>डिपॉजिट इतिहास</b>\n\n<i>अभी तक कोई डिपॉजिट नहीं।</i>",
    "dep_status_title": (
        "💰 <b>डिपॉजिट स्थिति</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🆔 रिक्वेस्ट: <code>#{dep_id}</code>\n"
        "🔗 नेटवर्क: <b>{net}</b>\n"
        "💵 राशि: <b>{amount}</b>\n"
        "📊 स्थिति: {status_icon} <b>{status_label}</b>\n"
        "🔑 TX: <code>{tx}</code>\n"
        "⏰ बनाई गई: {created_at}"
    ),
    "btn_refresh":        "रिफ्रेश",
    "dep_status_pending":    "प्रतीक्षारत",
    "dep_status_completed":  "क्रेडिट ✅",
    "dep_status_expired":    "समाप्त ❌",
    "dep_status_cancelled":  "रद्द",

    "referral_title": (
        "🎁 <b>रेफर करें और VIP पाएं</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📨 आपका रेफरल लिंक:\n"
        "<code>https://t.me/{bot}?start={code}</code>\n\n"
        "👥 रेफरल: <b>{count}/{needed}</b>\n"
        "🌟 VIP स्टेटस: {vip}\n\n"
        "🎁 हर दोस्त के डिपॉजिट पर <b>{bonus} USDT</b> कमाएं!"
    ),
    "vip_badge": "\n🌟 VIP सदस्य",

    "referral_bonus_msg": (
        "💰 <b>रेफरल डिपॉजिट बोनस!</b>\n\n"
        "आपके दोस्त ने {amount} डिपॉजिट किया। "
        "🎁 आपने <b>{bonus}</b> कमाए!"
    ),

    "support_title": (
        "🎧 <b>सहायता</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "मदद चाहिए? सपोर्ट टिकट खोलें।\n🔐 पूरी तरह निजी।"
    ),
    "btn_new_ticket":  "नया टिकट बनाएं",
    "btn_my_tickets":  "मेरे टिकट",
    "ticket_create_msg": (
        "🆕 <b>टिकट बनाएं</b>\n\n"
        "अपनी समस्या विस्तार से लिखें।\n"
        "<i>अगर खरीदारी से संबंधित है तो Order ID भी बताएं।</i>\n\n"
        "/cancel से रद्द करें।"
    ),
    "ticket_describe_issue": "कृपया अपनी समस्या बताएं।",
    "ticket_created": (
        "✅ <b>टिकट #{tid:04d} बनाया गया!</b>\n\n"
        "📝 {subject}\n\n🎧 हमारी टीम जल्द जवाब देगी।"
    ),
    "ticket_reply_prompt": "📩 <b>टिकट #{tid:04d} का जवाब</b>\n\nअपना संदेश लिखें:\n\n/cancel से रद्द करें।",
    "ticket_reply_sent":   "✅ टिकट #{tid:04d} का जवाब भेजा गया।",
    "btn_view_ticket":     "टिकट #{tid:04d} देखें",
    "btn_send_reply":      "जवाब दें",
    "btn_close_ticket":    "टिकट बंद करें",
    "no_open_tickets":     "✅ कोई खुला टिकट नहीं।",
    "ticket_closed_user":  "✅ टिकट #{tid:04d} बंद किया गया।",
    "ticket_list_title":   "📋 <b>मेरे टिकट</b>",
    "ticket_not_found":    "टिकट नहीं मिला।",
    "ticket_admin_reply": (
        "🎧 <b>सपोर्ट जवाब — टिकट #{tid:04d}</b>\n\n{msg}"
    ),

    "order_status_completed": "✅ पूर्ण",
    "order_status_refunded":  "♻️ रिफंड हो गया",
    "order_no_credential":    "<i>कोई क्रेडेंशियल डेटा नहीं</i>",
    "order_not_found":        "ऑर्डर नहीं मिला।",
    "btn_request_refund":     "रिफंड अनुरोध करें",
    "btn_refund_pending":     "रिफंड प्रक्रिया में…",
    "history_title":          "📋 <b>खरीद इतिहास</b>",
    "order_detail_body": (
        "📦 <b>ऑर्डर #{oid:04d}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🛍️ {product_name}\n"
        "💰 {amount}\n"
        "📊 {status}\n"
        "📅 {date}\n\n"
        "{cred_text}"
    ),

    "refund_order_not_found":     "ऑर्डर नहीं मिला।",
    "refund_already_refunded":    "यह ऑर्डर पहले से रिफंड हो चुका है।",
    "refund_already_pending":     "आपका रिफंड अनुरोध पहले से लंबित है।",
    "refund_prompt": (
        "📦 <b>रिफंड अनुरोध — ऑर्डर #{oid:04d}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🛍 <b>{product_name}</b> — {amount}\n\n"
        "✍️ कृपया रिफंड का कारण लिखें:\n"
        "<i>(या /cancel से वापस जाएं)</i>"
    ),
    "refund_error":          "❌ कुछ गलत हो गया। कृपया फिर प्रयास करें।",
    "refund_order_missing":  "❌ ऑर्डर नहीं मिला।",
    "refund_too_short":      "⚠️ कारण बहुत छोटा है। कम से कम 5 अक्षर लिखें।",
    "refund_submitted": (
        "✅ <b>रिफंड अनुरोध जमा हो गया!</b>\n\n"
        "📦 ऑर्डर #{oid:04d} — {product_name}\n"
        "💬 कारण: <i>{reason}</i>\n\n"
        "⏳ एडमिन जल्द आपके अनुरोध की समीक्षा करेंगे।"
    ),
    "refund_approved": (
        "✅ <b>रिफंड मंजूर!</b>\n\n"
        "📦 ऑर्डर #{oid:04d} — {product_name}\n"
        "💰 <b>{amount}</b> आपके वॉलेट बैलेंस में जोड़ दिया गया।\n\n"
        "🙏 आपकी प्रतीक्षा के लिए धन्यवाद।"
    ),
    "btn_my_profile":       "मेरी प्रोफाइल",
    "refund_rejected": (
        "❌ <b>रिफंड अनुरोध अस्वीकार</b>\n\n"
        "📦 ऑर्डर #{oid:04d}{product_part}\n\n"
        "💬 एडमिन ने आपके रिफंड अनुरोध की समीक्षा के बाद अस्वीकार कर दिया।\n"
        "अगर आपको लगता है यह गलती है, तो सपोर्ट टिकट खोलें।"
    ),
    "btn_support_ticket":   "सहायता",

    "maintenance":         "🔧 <b>बॉट रखरखाव में है</b>\n\nजल्द वापस आएंगे!",
    "cancelled":           "❌ रद्द किया गया।",
    "cart_empty":          "🛒 आपका कार्ट खाली है।",
    "cart_title":          "🛒 <b>आपका कार्ट</b>",
    "cart_view_title":     "🛒 <b>आपका कार्ट</b>",
    "cart_total_line":     "💰 <b>कुल: {total}</b>",
    "cart_balance_line":   "💼 बैलेंस: {balance}",
    "cart_item_oos":       "❌ {name} स्टॉक में नहीं है!",
    "cart_checkout_done": (
        "🎉 <b>चेकआउट पूर्ण!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    ),
    "cart_checkout_total": "💰 कुल: {total}\n💼 बैलेंस: {balance}",
    "btn_checkout":        "चेकआउट",
    "btn_clear_cart":      "कार्ट साफ करें",
    "btn_cart_added":      "कार्ट में जोड़ा गया!",
    "history_empty":       "📋 अभी तक कोई खरीद नहीं।",
    "order_detail_title":  "📦 <b>ऑर्डर #{oid:04d}</b>",

    "free_item_already_claimed": (
        "🎁 <b>मुफ्त आइटम</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "✅ आप पहले ही अपना मुफ्त आइटम ले चुके हैं।\n"
        "प्रत्येक उपयोगकर्ता केवल <b>एक</b> मुफ्त आइटम ले सकता है — धन्यवाद!"
    ),
    "free_item_none_available": (
        "🎁 <b>मुफ्त आइटम</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "😔 अभी कोई मुफ्त आइटम उपलब्ध नहीं है। बाद में देखें!"
    ),
    "free_item_list_header": (
        "🎁 <b>मुफ्त आइटम</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "नीचे से एक मुफ्त आइटम चुनें — आप केवल एक बार ही ले सकते हैं:"
    ),
    "free_item_stock_line":     "{emoji} <b>{name}</b>  (📦 {count} बचे)",
    "btn_claim_free":           "{emoji} {name} लें",
    "free_item_not_available":  "उपलब्ध नहीं है।",
    "free_item_already_alert":  "आप पहले ही अपना मुफ्त आइटम ले चुके हैं।",
    "free_item_oos_alert":      "स्टॉक खत्म — दूसरा आइटम आज़माएं।",
    "free_item_claimed_alert":  "🎉 मिल गया!",
    "free_item_claimed_msg": (
        "🎉 <b>मुफ्त आइटम मिल गया!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "{emoji} <b>{name}</b>\n\n"
        "<code>{data}</code>\n\n"
        "आनंद लें! 🙌"
    ),
    "free_item_broadcast": (
        "🎁🎉 <b>मुफ्त आइटम उपलब्ध है!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "{emoji} <b>{name}</b> अब बिल्कुल मुफ्त में उपलब्ध है!\n\n"
        "⚠️ प्रति उपयोगकर्ता केवल <b>1 मुफ्त आइटम</b> — पहले आओ, पहले पाओ।\n"
        "अभी लेने के लिए नीचे दबाएं 👇"
    ),
    "btn_claim_now":         "{emoji} अभी लें",

    "admin_balance_added": (
        "💰 <b>बैलेंस जोड़ा गया!</b>\n\n"
        "+{amount} एडमिन द्वारा जोड़ा गया।\n"
        "नया बैलेंस: {balance}"
    ),
    "admin_deposit_credited": (
        "💰 <b>डिपॉजिट क्रेडिट हो गया!</b>\n\n"
        "✅ {amount} एडमिन द्वारा आपके वॉलेट में जोड़ा गया।\n"
        "💼 नया बैलेंस: <b>{balance}</b>"
    ),
    "admin_gift_received": (
        "🎁 <b>गिफ्ट मिला!</b>\n\n"
        "एडमिन ने आपको <b>{amount}</b> गिफ्ट किया!\n"
        "💼 नया बैलेंस: <b>{balance}</b>"
    ),
    "admin_refund_processed": (
        "♻️ <b>रिफंड प्रोसेस हो गया</b>\n\n"
        "ऑर्डर <code>#{oid:04d}</code> रिफंड कर दिया गया।\n"
        "💰 {amount} आपके वॉलेट में वापस आ गया।"
    ),
    "wd_approved_msg":   "✅ आपका {amount} का विथड्रॉवल प्रोसेस हो गया!",
    "wd_rejected_msg":   "❌ आपका {amount} का विथड्रॉवल अस्वीकार कर दिया गया। सपोर्ट से संपर्क करें।",

    "renewal_reminder": (
        "🔔 <b>नवीनीकरण अनुस्मारक</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "आपकी <b>{product_name}</b> सदस्यता जल्द समाप्त हो सकती है!\n\n"
        "नवीनीकरण के लिए शॉप पर जाएं।"
    ),
    "btn_shop_now":    "अभी खरीदें",

    "reseller_not_approved": (
        "🏪 <b>रीसेलर प्रोग्राम</b>\n\n"
        "रीसेलर बनें और थोक मूल्य पर उत्पाद खरीदें!\n\n"
        "अप्रूवल के लिए एडमिन से संपर्क करें।"
    ),
    "reseller_panel": (
        "🏪 <b>रीसेलर पैनल</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🏷️ छूट: <b>{discount}% OFF</b>\n"
        "💰 उपलब्ध: <b>{available}</b>\n"
        "⏳ लंबित: <b>{pending}</b>\n"
        "💸 निकाला गया: <b>{withdrawn}</b>"
    ),
    "btn_request_withdrawal": "विथड्रॉवल अनुरोध करें",
},

# ═══════════════════════════════════════════════════════════════════════════
#  INDONESIAN
# ═══════════════════════════════════════════════════════════════════════════
"id": {
    "lang_name": "🇮🇩 Bahasa Indonesia",

    "select_language": (
        "🌐 <b>PILIH BAHASA ANDA</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Silakan pilih bahasa pilihan Anda\nuntuk melanjutkan:"
    ),
    "language_set": "✅ Bahasa diatur ke Bahasa Indonesia! Selamat datang!",

    "force_join_msg": (
        "🔔 <b>WAJIB BERGABUNG</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚠️ Untuk menggunakan bot ini,\nbergabunglah dengan channel kami dulu:\n\n"
        "Setelah bergabung, tekan tombol verifikasi."
    ),
    "join_btn":      "📢 Bergabung Channel",
    "join_verify":   "✅ Saya Sudah Bergabung — Verifikasi",
    "join_not_done": "❌ Silakan bergabung dengan semua channel terlebih dahulu!",
    "join_success":  "✅ Terverifikasi! Selamat datang di {bot_name}!",

    "welcome": (
        "{emoji} <b>{bot_name}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "👋 Selamat datang, <b>{name}</b>!\n\n"
        "<i>{tagline}</i>\n\n"
        "📋 <b>Pilih menu di bawah:</b>"
    ),
    "btn_shop":      "Belanja",
    "btn_deposit":   "Deposit",
    "btn_profile":   "Profil Saya",
    "btn_support":   "Dukungan",
    "btn_referral":  "Referral & VIP",
    "btn_language":  "Bahasa",
    "btn_admin":     "Panel Admin",
    "btn_back":      "« Kembali",
    "btn_home":      "Menu Utama",
    "btn_cancel":    "Batal",
    "btn_cart":      "Keranjang",
    "btn_free_item": "Item Gratis",

    "profile_body": (
        "👤 <b>PROFIL SAYA</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🆔 User ID: <code>{uid}</code>\n"
        "👤 Username: {uname}\n\n"
        "💎 <b>DOMPET</b>\n"
        "💰 Saldo: <b>${balance:.2f} USDT</b>\n\n"
        "🏆 <b>STATUS LOYALITAS</b>\n"
        "{tier_emoji} Tingkat: <b>{tier}</b>\n"
        "🎫 Diskon: <b>{disc}% OFF</b>"
        "{vip_badge}\n\n"
        "📊 <b>STATISTIK</b>\n"
        "📦 Total Pesanan: <b>{orders}</b>\n"
        "💸 Total Belanja: <b>${spent:.2f} USDT</b>"
    ),
    "btn_history":  "Riwayat Pembelian",
    "btn_deposit2": "Tambah Dana",

    "shop_title": "🛍️ <b>BELANJA — Pilih Kategori</b>",
    "shop_empty": "🛍️ <b>BELANJA</b>\n\nBelum ada produk tersedia.",
    "cat_choose_product": "Pilih produk:",
    "product_detail": (
        "{emoji} <b>{name}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📝 {desc}\n\n"
        "⏱️ Durasi: <b>{duration}</b>\n"
        "💰 Harga: <b>${price:.2f} USDT</b>{disc_line}\n\n"
        "💼 Saldo Anda: <b>${balance:.2f} USDT</b>"
    ),
    "disc_line":        "\n🎫 Harga Anda: <b>${final:.2f}</b> ({disc}% OFF)",
    "in_stock":         "✅ Tersedia",
    "out_of_stock":     "❌ Habis",
    "btn_buy":          "Beli Sekarang",
    "btn_add_cart":     "Tambah ke Keranjang",
    "btn_top_up":       "Isi ${needed:.2f}",
    "btn_shop_more":    "Belanja Lagi",
    "qty_label":        "🔢 Jumlah: <b>{qty}</b> × {unit} = <b>{total}</b>",
    "cat_no_products":  "Belum ada produk di kategori ini!",
    "product_not_found": "Produk tidak ditemukan.",
    "purchased": (
        "🎉 <b>PEMBELIAN BERHASIL!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "{emoji} <b>{name}</b>\n\n"
        "📦 <b>Kredensial Anda:</b>\n"
        "<code>{cred}</code>\n\n"
        "💰 Dibayar: <b>${amount:.2f} USDT</b>\n"
        "💼 Sisa: <b>${balance:.2f} USDT</b>\n\n"
        "📋 ID Pesanan: <code>#{oid}</code>"
    ),
    "insufficient":     "❌ Saldo tidak cukup. Anda butuh ${needed:.2f} USDT lagi.",
    "out_of_stock_buy": "❌ Produk ini sedang habis!",
    "multi_purchased_header": (
        "🎉 <b>PEMBELIAN BERHASIL!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "{emoji} <b>{name}</b> × {qty}\n\n"
        "📦 <b>Kredensial Anda:</b>\n"
        "💰 Total Dibayar: <b>${amount:.2f} USDT</b>\n"
        "💼 Sisa: <b>${balance:.2f} USDT</b>"
    ),
    "multi_purchased": (
        "🎉 <b>PEMBELIAN BERHASIL!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "{emoji} <b>{name}</b> × {qty}\n\n"
        "📦 <b>Kredensial Anda:</b>\n{creds}\n\n"
        "💰 Total: <b>${amount:.2f} USDT</b>\n"
        "💼 Sisa: <b>${balance:.2f} USDT</b>"
    ),

    "wallet_title": (
        "👛 <b>DOMPET SAYA</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "💰 Saldo: <b>{balance}</b>\n"
        "{tier_emoji} Tingkat: <b>{tier}</b> ({disc}% diskon)\n"
        "🛒 Pesanan: <b>{orders}</b>\n"
        "💵 Total Belanja: <b>{spent}</b>"
    ),
    "btn_payment_history":    "Riwayat Pembayaran",
    "btn_dep_history_wallet": "Riwayat Deposit",
    "btn_add_funds":          "Tambah Dana",
    "wallet_not_started":     "Silakan /start bot terlebih dahulu.",

    "deposit_wallet_text": (
        "💳 <b>DOMPET</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "💰 Saldo:  <b>${bal:.2f}</b>\n"
        "💎 Total Belanja: <b>${spent:.2f}</b>\n"
        "{tier_icon} Keanggotaan: <b>{tier_name}</b>\n\n"
        "Pilih metode pembayaran di bawah untuk menambah dana."
    ),
    "btn_binance_pay":   "Binance Pay",
    "btn_usdt_trc20":    "USDT (TRC20)",
    "btn_usdt_bep20":    "USDT (BEP20)",
    "btn_tx_history":    "Riwayat Transaksi",
    "btn_back_wallet":   "« Kembali ke Dompet",

    "dep_net_pay": (
        "✦ <b>Deposit Binance Pay</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Kirim USDT ke <b>Binance Pay ID</b> kami:\n\n"
        "<code>{pay_id}</code>\n\n"
        "💵 Minimum: <b>${min_dep:.2f} USDT</b>\n\n"
        "Masukkan <b>jumlah USDT</b> yang ingin Anda deposit:"
    ),
    "dep_net_trc20": (
        "🔴 <b>USDT — TRC20 (TRON)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📬 Kirim ke:\n<code>{addr}</code>\n\n"
        "⚡ Diverifikasi via <b>TronScan</b>\n\n"
        "💵 Minimum: <b>${min_dep:.2f} USDT</b>\n\n"
        "Masukkan <b>jumlah USDT</b> yang ingin Anda deposit:"
    ),
    "dep_net_bep20": (
        "🟡 <b>USDT — BEP20 (BSC)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📬 Kirim ke:\n<code>{addr}</code>\n\n"
        "⚡ Diverifikasi via <b>BSCScan</b>\n\n"
        "💵 Minimum: <b>${min_dep:.2f} USDT</b>\n\n"
        "Masukkan <b>jumlah USDT</b> yang ingin Anda deposit:"
    ),
    "dep_not_configured": "❌ Belum dikonfigurasi",

    "dep_pay_caption": (
        "✦ <b>DEPOSIT BINANCE PAY</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📲 <b>Binance Pay ID:</b>\n"
        "<code>{pay_id}</code>\n\n"
        "💵 <b>Kirim PERSIS:</b> <code>{expected:.3f}</code> USDT\n\n"
        "📝 <b>Catatan Unik (sertakan di keterangan):</b>\n"
        "<code>{note}</code>\n\n"
        "⚠️ <i>Sertakan catatan ini di keterangan pembayaran agar owner dapat mengidentifikasi pembayaran Anda.</i>\n\n"
        "Setelah mengirim, tekan <b>✅ Saya Sudah Bayar</b> di bawah."
    ),
    "btn_i_have_paid":   "Saya Sudah Bayar",

    "dep_chain_caption": (
        "{net_icon} <b>DEPOSIT USDT {network}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📬 <b>Kirim ke alamat ini:</b>\n"
        "<code>{addr}</code>\n\n"
        "💵 <b>Kirim PERSIS:</b> <code>{expected:.3f}</code> USDT\n\n"
        "⚠️ <i>Kirim jumlah yang persis — ini yang mengidentifikasi pembayaran Anda.</i>\n\n"
        "Setelah mengirim, tekan <b>✅ Saya Sudah Bayar</b> untuk mengirim TX Hash.\n"
        "Bot akan memverifikasi di <b>{explorer}</b> secara otomatis."
    ),
    "btn_submit_tx_hash": "Saya Sudah Bayar — Kirim TX Hash",

    "dep_submit_hash_prompt": (
        "🔍 <b>Kirim Hash Transaksi</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Tempel <b>Hash Transaksi (TxID)</b> dari dompet atau blockchain explorer Anda:\n\n"
        "<i>(Dimulai 0x untuk BEP20, atau 64 karakter hex untuk TRC20)</i>\n\n"
        "/cancel untuk kembali"
    ),
    "dep_session_expired":  "❌ Sesi habis. Silakan mulai deposit lagi.",
    "dep_not_found":        "❌ Deposit tidak ditemukan atau sudah diproses.",
    "dep_invalid_hash":     "⚠️ Itu bukan TX hash yang valid. Silakan coba lagi.",
    "dep_duplicate_hash": (
        "❌ <b>Deposit Gagal</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Alasan:</b> Hash transaksi ini sudah pernah digunakan. Hubungi admin."
    ),
    "dep_tx_too_old": (
        "❌ <b>Deposit Gagal</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Alasan:</b> Transaksi ini terjadi sebelum permintaan deposit Anda dibuat — "
        "tidak bisa dikaitkan dengan deposit ini. Kirim TX hash yang benar, atau hubungi admin."
    ),
    "dep_verified_credited": (
        "✅ <b>Deposit Terverifikasi & Dikreditkan!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "💵 Jumlah: <b>{amount}</b>\n"
        "💰 Saldo Baru: <b>${new_bal:.2f}</b>\n\n"
        "Terima kasih! Anda bisa berbelanja sekarang. 🛍"
    ),
    "btn_open_shop":      "Buka Toko",
    "dep_not_verified": (
        "⚠️ <b>TIDAK BISA DIVERIFIKASI OTOMATIS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "TX Hash tidak ditemukan atau jumlahnya tidak cocok.\n\n"
        "Jika baru saja membayar, blockchain butuh beberapa menit — "
        "tunggu sebentar dan tekan <b>Cek Lagi</b>.\n\n"
        "Jika yakin sudah terbayar, periksa ulang TX hash atau tekan <b>Beritahu Admin</b>."
    ),
    "btn_check_again":    "Cek Lagi",
    "btn_notify_admin":   "Beritahu Admin",
    "dep_recheck_rechecking": "Memeriksa ulang…",
    "dep_already_processed":  "✅ Deposit ini sudah diproses sebelumnya.",
    "dep_still_not_found": (
        "⚠️ <b>Masih belum ditemukan di blockchain.</b>\n\n"
        "Coba lagi dalam satu menit, atau beritahu admin."
    ),
    "dep_check_failed_alert": "Masih belum ditemukan. Coba lagi dalam satu menit atau beritahu admin.",
    "dep_duplicate_recheck": (
        "❌ <b>Deposit Gagal</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Alasan:</b> Hash transaksi ini sudah pernah digunakan. Hubungi admin."
    ),
    "dep_tx_too_old_recheck": (
        "❌ <b>Deposit Gagal</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Alasan:</b> Transaksi ini terjadi sebelum permintaan deposit dibuat. Hubungi admin."
    ),
    "dep_verified_recheck": (
        "✅ <b>Deposit Terverifikasi & Dikreditkan!</b>\n\n"
        "💵 {amount} → 💰 Saldo: ${new_bal:.2f}"
    ),

    "dep_admin_notified": (
        "📢 <b>Admin Diberitahu</b>\n\n"
        "Detail deposit Anda telah dikirim ke admin untuk tinjauan manual.\n"
        "Anda akan diberitahu setelah disetujui.\n\n"
        "Deposit #{dep_id:04d} — {amount}"
    ),
    "dep_pay_submitted": (
        "✅ <b>Pembayaran Dikirim!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "💵 Jumlah: <b>{amount}</b>\n"
        "📝 Catatan: <code>{note}</code>\n\n"
        "Admin telah diberitahu dan akan segera menyetujui.\n"
        "Anda akan menerima pesan setelah saldo dikreditkan."
    ),
    "dep_rejected_user": (
        "❌ <b>Deposit Ditolak</b>\n\n"
        "Deposit Anda sebesar {amount} telah ditinjau dan ditolak.\n"
        "Jika Anda rasa ini kesalahan, buka tiket support."
    ),

    "deposit_body": (
        "💰 <b>DEPOSIT USDT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔗 <b>Jaringan yang Didukung:</b>\n\n"
        "🟡 <b>TRC20 (TRON)</b>\n"
        "<code>{trc20_address}</code>\n\n"
        "🟠 <b>BEP20 (BSC)</b>\n"
        "<code>{bep20_address}</code>\n\n"
        "⚠️ Min deposit: <b>${min_dep:.2f} USDT</b>\n\n"
        "{addr_warn}"
    ),
    "deposit_body_single": (
        "💰 <b>DEPOSIT USDT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔗 Jaringan: <b>{network}</b>\n"
        "📬 Alamat:\n<code>{address}</code>\n\n"
        "⚠️ Min: <b>${min_dep:.2f} USDT</b>\n\n"
        "{addr_warn}"
    ),
    "addr_warn":        "⚠️ Alamat deposit belum dikonfigurasi. Hubungi support.",
    "btn_start_dep":    "Mulai Deposit",
    "btn_dep_history":  "Riwayat Deposit",
    "dep_enter_amount": "💰 <b>MASUKKAN JUMLAH</b>\n\nMinimum: <b>${min:.2f} USDT</b>\n\nBerapa yang ingin Anda deposit?",
    "dep_select_network": "🌐 <b>PILIH JARINGAN</b>\n\nPilih jaringan yang akan Anda gunakan:",
    "btn_trc20": "TRC20 (TRON)",
    "btn_bep20": "BEP20 (BSC)",
    "dep_invalid":   "❌ Jumlah tidak valid. Masukkan angka seperti 5 atau 10.5",
    "dep_too_small": "❌ Minimum deposit adalah <b>${min:.2f} USDT</b>.",
    "dep_created": (
        "✅ <b>PERMINTAAN DEPOSIT DIBUAT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🆔 ID: <code>#{dep_id}</code>\n"
        "🔗 Jaringan: <b>{network}</b>\n"
        "📬 Kirim ke:\n<code>{address}</code>\n\n"
        "💵 <b>Kirim PERSIS:</b>\n"
        "<code>{amount}</code> USDT\n\n"
        "⏰ Kadaluarsa: <b>{timeout} menit</b>"
    ),
    "dep_confirmed": (
        "🎉 <b>DEPOSIT DIKONFIRMASI!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "✅ <b>${amount:.2f} USDT</b> telah ditambahkan!\n"
        "{vip_line}\n"
        "💼 Saldo Baru: <b>${balance:.2f} USDT</b>"
    ),
    "dep_expired":   "⏰ Permintaan deposit <code>#{dep_id}</code> telah kadaluarsa.",
    "vip_dep_line":  "🎁 Bonus VIP: +${bonus:.2f} USDT",

    "dep_history_title":  "📋 <b>Riwayat Deposit</b>",
    "dep_history_empty":  "📋 <b>Riwayat Deposit</b>\n\n<i>Belum ada deposit.</i>",
    "dep_status_title": (
        "💰 <b>Status Deposit</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🆔 Permintaan: <code>#{dep_id}</code>\n"
        "🔗 Jaringan: <b>{net}</b>\n"
        "💵 Diharapkan: <b>{amount}</b>\n"
        "📊 Status: {status_icon} <b>{status_label}</b>\n"
        "🔑 TX: <code>{tx}</code>\n"
        "⏰ Dibuat: {created_at}"
    ),
    "btn_refresh":        "Perbarui",
    "dep_status_pending":    "Menunggu",
    "dep_status_completed":  "Dikreditkan ✅",
    "dep_status_expired":    "Kadaluarsa ❌",
    "dep_status_cancelled":  "Dibatalkan",

    "referral_title": (
        "🎁 <b>REFERRAL & EARN VIP</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📨 Link referral Anda:\n"
        "<code>https://t.me/{bot}?start={code}</code>\n\n"
        "👥 Referral: <b>{count}/{needed}</b>\n"
        "🌟 Status VIP: {vip}\n\n"
        "🎁 Dapatkan <b>{bonus} USDT</b> per deposit teman!"
    ),
    "vip_badge": "\n🌟 Member VIP",

    "referral_bonus_msg": (
        "💰 <b>Bonus Referral Deposit!</b>\n\n"
        "Teman Anda mendepositkan {amount}. "
        "🎁 Anda mendapatkan <b>{bonus}</b>!"
    ),

    "support_title": (
        "🎧 <b>DUKUNGAN</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Butuh bantuan? Buka tiket support.\n🔐 Sepenuhnya privat."
    ),
    "btn_new_ticket":  "Buat Tiket Support",
    "btn_my_tickets":  "Tiket Saya",
    "ticket_create_msg": (
        "🆕 <b>BUAT TIKET</b>\n\n"
        "Jelaskan masalah Anda secara detail.\n"
        "<i>Sertakan Order ID jika terkait pembelian.</i>\n\n"
        "/cancel untuk membatalkan."
    ),
    "ticket_describe_issue": "Silakan jelaskan masalah Anda.",
    "ticket_created": (
        "✅ <b>Tiket #{tid:04d} dibuat!</b>\n\n"
        "📝 {subject}\n\n🎧 Tim kami akan segera merespons."
    ),
    "ticket_reply_prompt": "📩 <b>Balas Tiket #{tid:04d}</b>\n\nKetik pesan Anda:\n\n/cancel untuk batal.",
    "ticket_reply_sent":   "✅ Balasan dikirim untuk Tiket #{tid:04d}",
    "btn_view_ticket":     "Lihat Tiket #{tid:04d}",
    "btn_send_reply":      "Balas",
    "btn_close_ticket":    "Tutup Tiket",
    "no_open_tickets":     "✅ Tidak ada tiket terbuka.",
    "ticket_closed_user":  "✅ Tiket #{tid:04d} telah ditutup.",
    "ticket_list_title":   "📋 <b>Tiket Saya</b>",
    "ticket_not_found":    "Tiket tidak ditemukan.",
    "ticket_admin_reply": (
        "🎧 <b>Balasan Support — Tiket #{tid:04d}</b>\n\n{msg}"
    ),

    "order_status_completed": "✅ Selesai",
    "order_status_refunded":  "♻️ Dikembalikan",
    "order_no_credential":    "<i>Tidak ada data kredensial</i>",
    "order_not_found":        "Pesanan tidak ditemukan.",
    "btn_request_refund":     "Minta Pengembalian Dana",
    "btn_refund_pending":     "Pengembalian Dana Menunggu…",
    "history_title":          "📋 <b>Riwayat Pembelian</b>",
    "order_detail_body": (
        "📦 <b>Pesanan #{oid:04d}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🛍️ {product_name}\n"
        "💰 {amount}\n"
        "📊 {status}\n"
        "📅 {date}\n\n"
        "{cred_text}"
    ),

    "refund_order_not_found":     "Pesanan tidak ditemukan.",
    "refund_already_refunded":    "Pesanan ini sudah dikembalikan dananya.",
    "refund_already_pending":     "Anda sudah memiliki permintaan pengembalian dana yang menunggu.",
    "refund_prompt": (
        "📦 <b>Permintaan Pengembalian Dana — Pesanan #{oid:04d}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🛍 <b>{product_name}</b> — {amount}\n\n"
        "✍️ Silakan tulis alasan pengembalian dana Anda:\n"
        "<i>(atau /cancel untuk kembali)</i>"
    ),
    "refund_error":          "❌ Terjadi kesalahan. Silakan coba lagi.",
    "refund_order_missing":  "❌ Pesanan tidak ditemukan.",
    "refund_too_short":      "⚠️ Alasan terlalu singkat. Tulis minimal 5 karakter.",
    "refund_submitted": (
        "✅ <b>Permintaan pengembalian dana berhasil dikirim!</b>\n\n"
        "📦 Pesanan #{oid:04d} — {product_name}\n"
        "💬 Alasan: <i>{reason}</i>\n\n"
        "⏳ Admin akan segera meninjau permintaan Anda."
    ),
    "refund_approved": (
        "✅ <b>Pengembalian Dana Disetujui!</b>\n\n"
        "📦 Pesanan #{oid:04d} — {product_name}\n"
        "💰 <b>{amount}</b> telah dikreditkan ke saldo dompet Anda.\n\n"
        "🙏 Terima kasih atas kesabaran Anda."
    ),
    "btn_my_profile":       "Profil Saya",
    "refund_rejected": (
        "❌ <b>Permintaan Pengembalian Dana Ditolak</b>\n\n"
        "📦 Pesanan #{oid:04d}{product_part}\n\n"
        "💬 Permintaan pengembalian dana Anda telah ditinjau dan ditolak oleh admin.\n"
        "Jika Anda rasa ini kesalahan, buka tiket support."
    ),
    "btn_support_ticket":   "Support",

    "maintenance":         "🔧 <b>BOT SEDANG MAINTENANCE</b>\n\nSegera kembali!",
    "cancelled":           "❌ Dibatalkan.",
    "cart_empty":          "🛒 Keranjang Anda kosong.",
    "cart_title":          "🛒 <b>KERANJANG ANDA</b>",
    "cart_view_title":     "🛒 <b>KERANJANG ANDA</b>",
    "cart_total_line":     "💰 <b>Total: {total}</b>",
    "cart_balance_line":   "💼 Saldo: {balance}",
    "cart_item_oos":       "❌ {name} sedang habis!",
    "cart_checkout_done": (
        "🎉 <b>CHECKOUT SELESAI!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    ),
    "cart_checkout_total": "💰 Total: {total}\n💼 Saldo: {balance}",
    "btn_checkout":        "Checkout",
    "btn_clear_cart":      "Kosongkan Keranjang",
    "btn_cart_added":      "Ditambahkan ke keranjang!",
    "history_empty":       "📋 Belum ada pembelian.",
    "order_detail_title":  "📦 <b>PESANAN #{oid:04d}</b>",

    "free_item_already_claimed": (
        "🎁 <b>ITEM GRATIS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "✅ Anda sudah mengklaim item gratis Anda.\n"
        "Setiap pengguna hanya bisa klaim <b>satu</b> item gratis — terima kasih!"
    ),
    "free_item_none_available": (
        "🎁 <b>ITEM GRATIS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "😔 Tidak ada item gratis tersedia saat ini. Cek lagi nanti!"
    ),
    "free_item_list_header": (
        "🎁 <b>ITEM GRATIS</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Pilih SATU item gratis di bawah — hanya bisa klaim sekali:"
    ),
    "free_item_stock_line":     "{emoji} <b>{name}</b>  (📦 {count} tersisa)",
    "btn_claim_free":           "{emoji} Klaim {name}",
    "free_item_not_available":  "Tidak tersedia.",
    "free_item_already_alert":  "Anda sudah mengklaim item gratis Anda.",
    "free_item_oos_alert":      "Stok habis — coba item lain.",
    "free_item_claimed_alert":  "🎉 Berhasil diklaim!",
    "free_item_claimed_msg": (
        "🎉 <b>ITEM GRATIS BERHASIL DIKLAIM!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "{emoji} <b>{name}</b>\n\n"
        "<code>{data}</code>\n\n"
        "Selamat menikmati! 🙌"
    ),
    "free_item_broadcast": (
        "🎁🎉 <b>ITEM GRATIS TERSEDIA!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "{emoji} <b>{name}</b> kini tersedia GRATIS!\n\n"
        "⚠️ Hanya <b>1 item gratis per pengguna</b> — siapa cepat dia dapat.\n"
        "Tekan di bawah untuk mengklaim sekarang 👇"
    ),
    "btn_claim_now":         "{emoji} Klaim Sekarang",

    "admin_balance_added": (
        "💰 <b>Saldo Ditambahkan!</b>\n\n"
        "+{amount} dikreditkan oleh admin.\n"
        "Saldo baru: {balance}"
    ),
    "admin_deposit_credited": (
        "💰 <b>Deposit Dikreditkan!</b>\n\n"
        "✅ {amount} telah ditambahkan ke dompet Anda oleh admin.\n"
        "💼 Saldo baru: <b>{balance}</b>"
    ),
    "admin_gift_received": (
        "🎁 <b>Hadiah Diterima!</b>\n\n"
        "Seorang admin telah memberikan <b>{amount}</b> kepada Anda!\n"
        "💼 Saldo Baru: <b>{balance}</b>"
    ),
    "admin_refund_processed": (
        "♻️ <b>Pengembalian Dana Diproses</b>\n\n"
        "Pesanan <code>#{oid:04d}</code> telah dikembalikan.\n"
        "💰 {amount} dikembalikan ke dompet Anda."
    ),
    "wd_approved_msg":   "✅ Penarikan dana Anda sebesar {amount} telah diproses!",
    "wd_rejected_msg":   "❌ Penarikan dana Anda sebesar {amount} ditolak. Hubungi support.",

    "renewal_reminder": (
        "🔔 <b>Pengingat Perpanjangan</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Langganan <b>{product_name}</b> Anda mungkin akan segera berakhir!\n\n"
        "Kunjungi toko untuk memperpanjang."
    ),
    "btn_shop_now":    "Belanja Sekarang",

    "reseller_not_approved": (
        "🏪 <b>PROGRAM RESELLER</b>\n\n"
        "Daftar menjadi reseller dan beli produk dengan harga grosir!\n\n"
        "Hubungi admin untuk mendapatkan persetujuan."
    ),
    "reseller_panel": (
        "🏪 <b>PANEL RESELLER</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🏷️ Diskon: <b>{discount}% OFF</b>\n"
        "💰 Tersedia: <b>{available}</b>\n"
        "⏳ Tertunda: <b>{pending}</b>\n"
        "💸 Ditarik: <b>{withdrawn}</b>"
    ),
    "btn_request_withdrawal": "Minta Penarikan Dana",
},

# ═══════════════════════════════════════════════════════════════════════════
#  VIETNAMESE
# ═══════════════════════════════════════════════════════════════════════════
"vi": {
    "lang_name": "🇻🇳 Tiếng Việt",

    "select_language": (
        "🌐 <b>CHỌN NGÔN NGỮ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Vui lòng chọn ngôn ngữ\nưa thích của bạn để tiếp tục:"
    ),
    "language_set": "✅ Đã đặt ngôn ngữ Tiếng Việt! Chào mừng!",

    "force_join_msg": (
        "🔔 <b>YÊU CẦU THAM GIA</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚠️ Để sử dụng bot này, vui lòng\ntham gia kênh của chúng tôi trước:\n\n"
        "Sau khi tham gia, nhấn nút xác minh bên dưới."
    ),
    "join_btn":      "📢 Tham Gia Kênh",
    "join_verify":   "✅ Tôi Đã Tham Gia — Xác Minh",
    "join_not_done": "❌ Vui lòng tham gia tất cả các kênh trước!",
    "join_success":  "✅ Đã xác minh! Chào mừng đến {bot_name}!",

    "welcome": (
        "{emoji} <b>{bot_name}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "👋 Chào mừng, <b>{name}</b>!\n\n"
        "<i>{tagline}</i>\n\n"
        "📋 <b>Chọn tùy chọn bên dưới:</b>"
    ),
    "btn_shop":      "Mua Sắm",
    "btn_deposit":   "Nạp Tiền",
    "btn_profile":   "Hồ Sơ",
    "btn_support":   "Hỗ Trợ",
    "btn_referral":  "Giới Thiệu & VIP",
    "btn_language":  "Ngôn Ngữ",
    "btn_admin":     "Bảng Admin",
    "btn_back":      "« Quay Lại",
    "btn_home":      "Menu Chính",
    "btn_cancel":    "Hủy",
    "btn_cart":      "Giỏ Hàng",
    "btn_free_item": "Quà Miễn Phí",

    "profile_body": (
        "👤 <b>HỒ SƠ CỦA TÔI</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🆔 User ID: <code>{uid}</code>\n"
        "👤 Username: {uname}\n\n"
        "💎 <b>VÍ TIỀN</b>\n"
        "💰 Số dư: <b>${balance:.2f} USDT</b>\n\n"
        "🏆 <b>TRẠNG THÁI LOYALTY</b>\n"
        "{tier_emoji} Cấp: <b>{tier}</b>\n"
        "🎫 Giảm giá: <b>{disc}% OFF</b>"
        "{vip_badge}\n\n"
        "📊 <b>THỐNG KÊ</b>\n"
        "📦 Tổng đơn: <b>{orders}</b>\n"
        "💸 Tổng chi: <b>${spent:.2f} USDT</b>"
    ),
    "btn_history":  "Lịch Sử Mua Hàng",
    "btn_deposit2": "Thêm Tiền",

    "shop_title": "🛍️ <b>MUA SẮM — Chọn Danh Mục</b>",
    "shop_empty": "🛍️ <b>MUA SẮM</b>\n\nChưa có sản phẩm nào. Quay lại sau nhé!",
    "cat_choose_product": "Chọn sản phẩm:",
    "product_detail": (
        "{emoji} <b>{name}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📝 {desc}\n\n"
        "⏱️ Thời hạn: <b>{duration}</b>\n"
        "💰 Giá: <b>${price:.2f} USDT</b>{disc_line}\n\n"
        "💼 Số dư của bạn: <b>${balance:.2f} USDT</b>"
    ),
    "disc_line":        "\n🎫 Giá của bạn: <b>${final:.2f}</b> (giảm {disc}%)",
    "in_stock":         "✅ Còn hàng",
    "out_of_stock":     "❌ Hết hàng",
    "btn_buy":          "Mua Ngay",
    "btn_add_cart":     "Thêm Vào Giỏ",
    "btn_top_up":       "Nạp Thêm ${needed:.2f}",
    "btn_shop_more":    "Mua Thêm",
    "qty_label":        "🔢 Số lượng: <b>{qty}</b> × {unit} = <b>{total}</b>",
    "cat_no_products":  "Chưa có sản phẩm nào trong danh mục này!",
    "product_not_found": "Không tìm thấy sản phẩm.",
    "purchased": (
        "🎉 <b>MUA HÀNG THÀNH CÔNG!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "{emoji} <b>{name}</b>\n\n"
        "📦 <b>Thông tin đăng nhập:</b>\n"
        "<code>{cred}</code>\n\n"
        "💰 Đã trả: <b>${amount:.2f} USDT</b>\n"
        "💼 Còn lại: <b>${balance:.2f} USDT</b>\n\n"
        "📋 Mã đơn: <code>#{oid}</code>"
    ),
    "insufficient":     "❌ Số dư không đủ. Bạn cần thêm ${needed:.2f} USDT.",
    "out_of_stock_buy": "❌ Sản phẩm này đã hết hàng!",
    "multi_purchased_header": (
        "🎉 <b>MUA HÀNG THÀNH CÔNG!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "{emoji} <b>{name}</b> × {qty}\n\n"
        "📦 <b>Thông tin đăng nhập của bạn:</b>\n"
        "💰 Đã thanh toán: <b>${amount:.2f} USDT</b>\n"
        "💼 Còn lại: <b>${balance:.2f} USDT</b>"
    ),
    "multi_purchased": (
        "🎉 <b>MUA HÀNG THÀNH CÔNG!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "{emoji} <b>{name}</b> × {qty}\n\n"
        "📦 <b>Thông tin đăng nhập:</b>\n{creds}\n\n"
        "💰 Tổng: <b>${amount:.2f} USDT</b>\n"
        "💼 Còn lại: <b>${balance:.2f} USDT</b>"
    ),

    "wallet_title": (
        "👛 <b>VÍ CỦA TÔI</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "💰 Số dư: <b>{balance}</b>\n"
        "{tier_emoji} Cấp: <b>{tier}</b> (giảm {disc}%)\n"
        "🛒 Đơn hàng: <b>{orders}</b>\n"
        "💵 Tổng chi tiêu: <b>{spent}</b>"
    ),
    "btn_payment_history":    "Lịch Sử Thanh Toán",
    "btn_dep_history_wallet": "Lịch Sử Nạp Tiền",
    "btn_add_funds":          "Thêm Tiền",
    "wallet_not_started":     "Vui lòng /start bot trước.",

    "deposit_wallet_text": (
        "💳 <b>VÍ TIỀN</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "💰 Số dư:  <b>${bal:.2f}</b>\n"
        "💎 Tổng Chi Tiêu: <b>${spent:.2f}</b>\n"
        "{tier_icon} Thành Viên: <b>{tier_name}</b>\n\n"
        "Chọn phương thức thanh toán bên dưới để nạp tiền vào ví."
    ),
    "btn_binance_pay":   "Binance Pay",
    "btn_usdt_trc20":    "USDT (TRC20)",
    "btn_usdt_bep20":    "USDT (BEP20)",
    "btn_tx_history":    "Lịch Sử Giao Dịch",
    "btn_back_wallet":   "« Quay Lại Ví",

    "dep_net_pay": (
        "✦ <b>Nạp Tiền Binance Pay</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Gửi USDT đến <b>Binance Pay ID</b> của chúng tôi:\n\n"
        "<code>{pay_id}</code>\n\n"
        "💵 Tối thiểu: <b>${min_dep:.2f} USDT</b>\n\n"
        "Nhập <b>số tiền USDT</b> bạn muốn nạp:"
    ),
    "dep_net_trc20": (
        "🔴 <b>USDT — TRC20 (TRON)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📬 Gửi đến:\n<code>{addr}</code>\n\n"
        "⚡ Xác minh qua <b>TronScan</b>\n\n"
        "💵 Tối thiểu: <b>${min_dep:.2f} USDT</b>\n\n"
        "Nhập <b>số tiền USDT</b> bạn muốn nạp:"
    ),
    "dep_net_bep20": (
        "🟡 <b>USDT — BEP20 (BSC)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📬 Gửi đến:\n<code>{addr}</code>\n\n"
        "⚡ Xác minh qua <b>BSCScan</b>\n\n"
        "💵 Tối thiểu: <b>${min_dep:.2f} USDT</b>\n\n"
        "Nhập <b>số tiền USDT</b> bạn muốn nạp:"
    ),
    "dep_not_configured": "❌ Chưa được cấu hình",

    "dep_pay_caption": (
        "✦ <b>NẠP TIỀN BINANCE PAY</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📲 <b>Binance Pay ID:</b>\n"
        "<code>{pay_id}</code>\n\n"
        "💵 <b>Gửi ĐÚNG SỐ TIỀN:</b> <code>{expected:.3f}</code> USDT\n\n"
        "📝 <b>Ghi chú đặc biệt (ghi vào ghi chú giao dịch):</b>\n"
        "<code>{note}</code>\n\n"
        "⚠️ <i>Hãy ghi chú này vào phần ghi chú để chủ cửa hàng nhận ra thanh toán của bạn.</i>\n\n"
        "Sau khi gửi, nhấn <b>✅ Tôi Đã Thanh Toán</b> bên dưới."
    ),
    "btn_i_have_paid":   "Tôi Đã Thanh Toán",

    "dep_chain_caption": (
        "{net_icon} <b>NẠP TIỀN USDT {network}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📬 <b>Gửi đến địa chỉ này:</b>\n"
        "<code>{addr}</code>\n\n"
        "💵 <b>Gửi ĐÚNG SỐ TIỀN:</b> <code>{expected:.3f}</code> USDT\n\n"
        "⚠️ <i>Gửi đúng số tiền — điều này giúp xác định giao dịch của bạn.</i>\n\n"
        "Sau khi gửi, nhấn <b>✅ Tôi Đã Thanh Toán</b> để gửi TX Hash.\n"
        "Bot sẽ tự động xác minh trên <b>{explorer}</b>."
    ),
    "btn_submit_tx_hash": "Tôi Đã Thanh Toán — Gửi TX Hash",

    "dep_submit_hash_prompt": (
        "🔍 <b>Gửi Hash Giao Dịch</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Dán <b>Hash Giao Dịch (TxID)</b> từ ví hoặc blockchain explorer của bạn:\n\n"
        "<i>(Bắt đầu bằng 0x cho BEP20, hoặc 64 ký tự hex cho TRC20)</i>\n\n"
        "/cancel để quay lại"
    ),
    "dep_session_expired":  "❌ Phiên đã hết hạn. Vui lòng bắt đầu lại giao dịch nạp tiền.",
    "dep_not_found":        "❌ Không tìm thấy giao dịch nạp tiền hoặc đã được xử lý.",
    "dep_invalid_hash":     "⚠️ TX Hash không hợp lệ. Vui lòng thử lại.",
    "dep_duplicate_hash": (
        "❌ <b>Nạp Tiền Thất Bại</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Lý do:</b> Hash giao dịch này đã được sử dụng. Vui lòng liên hệ admin."
    ),
    "dep_tx_too_old": (
        "❌ <b>Nạp Tiền Thất Bại</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Lý do:</b> Giao dịch này xảy ra trước khi yêu cầu nạp tiền được tạo — "
        "không thể thuộc giao dịch này. Vui lòng gửi TX hash đúng, hoặc liên hệ admin."
    ),
    "dep_verified_credited": (
        "✅ <b>Nạp Tiền Đã Xác Minh & Được Ghi Có!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "💵 Số tiền: <b>{amount}</b>\n"
        "💰 Số dư mới: <b>${new_bal:.2f}</b>\n\n"
        "Cảm ơn bạn! Bạn có thể mua sắm ngay bây giờ. 🛍"
    ),
    "btn_open_shop":      "Mở Cửa Hàng",
    "dep_not_verified": (
        "⚠️ <b>KHÔNG THỂ XÁC MINH TỰ ĐỘNG</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Không tìm thấy TX Hash hoặc số tiền không khớp.\n\n"
        "Nếu vừa thanh toán, blockchain cần vài phút để xác nhận — "
        "đợi một chút rồi nhấn <b>Kiểm Tra Lại</b>.\n\n"
        "Nếu chắc chắn đã thanh toán, kiểm tra lại TX hash hoặc nhấn <b>Thông Báo Admin</b>."
    ),
    "btn_check_again":    "Kiểm Tra Lại",
    "btn_notify_admin":   "Thông Báo Admin",
    "dep_recheck_rechecking": "Đang kiểm tra lại…",
    "dep_already_processed":  "✅ Giao dịch nạp tiền này đã được xử lý trước đó.",
    "dep_still_not_found": (
        "⚠️ <b>Vẫn chưa tìm thấy trên blockchain.</b>\n\n"
        "Thử lại sau một phút, hoặc thông báo admin."
    ),
    "dep_check_failed_alert": "Vẫn chưa tìm thấy. Thử lại sau một phút hoặc thông báo admin.",
    "dep_duplicate_recheck": (
        "❌ <b>Nạp Tiền Thất Bại</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Lý do:</b> Hash giao dịch này đã được sử dụng. Vui lòng liên hệ admin."
    ),
    "dep_tx_too_old_recheck": (
        "❌ <b>Nạp Tiền Thất Bại</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "<b>Lý do:</b> Giao dịch này xảy ra trước khi yêu cầu nạp tiền được tạo. Liên hệ admin."
    ),
    "dep_verified_recheck": (
        "✅ <b>Nạp Tiền Đã Xác Minh & Được Ghi Có!</b>\n\n"
        "💵 {amount} → 💰 Số dư: ${new_bal:.2f}"
    ),

    "dep_admin_notified": (
        "📢 <b>Đã Thông Báo Admin</b>\n\n"
        "Thông tin nạp tiền của bạn đã được gửi đến admin để xem xét thủ công.\n"
        "Bạn sẽ được thông báo khi được duyệt.\n\n"
        "Nạp tiền #{dep_id:04d} — {amount}"
    ),
    "dep_pay_submitted": (
        "✅ <b>Đã Gửi Thanh Toán!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "💵 Số tiền: <b>{amount}</b>\n"
        "📝 Ghi chú: <code>{note}</code>\n\n"
        "Admin đã được thông báo và sẽ sớm duyệt.\n"
        "Bạn sẽ nhận được tin nhắn khi số dư được ghi có."
    ),
    "dep_rejected_user": (
        "❌ <b>Nạp Tiền Bị Từ Chối</b>\n\n"
        "Giao dịch nạp tiền {amount} của bạn đã được xem xét và bị từ chối.\n"
        "Nếu bạn cho rằng đây là nhầm lẫn, hãy mở phiếu hỗ trợ."
    ),

    "deposit_body": (
        "💰 <b>NẠP TIỀN USDT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔗 <b>Mạng được hỗ trợ:</b>\n\n"
        "🟡 <b>TRC20 (TRON)</b>\n"
        "<code>{trc20_address}</code>\n\n"
        "🟠 <b>BEP20 (BSC)</b>\n"
        "<code>{bep20_address}</code>\n\n"
        "⚠️ Nạp tối thiểu: <b>${min_dep:.2f} USDT</b>\n\n"
        "{addr_warn}"
    ),
    "deposit_body_single": (
        "💰 <b>NẠP TIỀN USDT</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🔗 Mạng: <b>{network}</b>\n"
        "📬 Địa chỉ:\n<code>{address}</code>\n\n"
        "⚠️ Tối thiểu: <b>${min_dep:.2f} USDT</b>\n\n"
        "{addr_warn}"
    ),
    "addr_warn":        "⚠️ Địa chỉ nạp tiền chưa được cấu hình. Liên hệ hỗ trợ.",
    "btn_start_dep":    "Bắt Đầu Nạp Tiền",
    "btn_dep_history":  "Lịch Sử Nạp Tiền",
    "dep_enter_amount": "💰 <b>NHẬP SỐ TIỀN</b>\n\nTối thiểu: <b>${min:.2f} USDT</b>\n\nBạn muốn nạp bao nhiêu?",
    "dep_select_network": "🌐 <b>CHỌN MẠNG</b>\n\nChọn mạng bạn sẽ dùng để gửi USDT:",
    "btn_trc20": "TRC20 (TRON)",
    "btn_bep20": "BEP20 (BSC)",
    "dep_invalid":   "❌ Số tiền không hợp lệ. Nhập số như 5 hoặc 10.5",
    "dep_too_small": "❌ Nạp tối thiểu là <b>${min:.2f} USDT</b>.",
    "dep_created": (
        "✅ <b>YÊU CẦU NẠP TIỀN ĐÃ TẠO</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🆔 ID: <code>#{dep_id}</code>\n"
        "🔗 Mạng: <b>{network}</b>\n"
        "📬 Gửi đến:\n<code>{address}</code>\n\n"
        "💵 <b>Gửi ĐÚNG SỐ TIỀN:</b>\n"
        "<code>{amount}</code> USDT\n\n"
        "⏰ Hết hạn: <b>{timeout} phút</b>"
    ),
    "dep_confirmed": (
        "🎉 <b>NẠP TIỀN THÀNH CÔNG!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "✅ <b>${amount:.2f} USDT</b> đã được thêm vào ví!\n"
        "{vip_line}\n"
        "💼 Số dư mới: <b>${balance:.2f} USDT</b>"
    ),
    "dep_expired":   "⏰ Yêu cầu nạp tiền <code>#{dep_id}</code> đã hết hạn.",
    "vip_dep_line":  "🎁 Thưởng VIP: +${bonus:.2f} USDT",

    "dep_history_title":  "📋 <b>Lịch Sử Nạp Tiền</b>",
    "dep_history_empty":  "📋 <b>Lịch Sử Nạp Tiền</b>\n\n<i>Chưa có giao dịch nạp tiền nào.</i>",
    "dep_status_title": (
        "💰 <b>Trạng Thái Nạp Tiền</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🆔 Yêu cầu: <code>#{dep_id}</code>\n"
        "🔗 Mạng: <b>{net}</b>\n"
        "💵 Dự kiến: <b>{amount}</b>\n"
        "📊 Trạng thái: {status_icon} <b>{status_label}</b>\n"
        "🔑 TX: <code>{tx}</code>\n"
        "⏰ Tạo lúc: {created_at}"
    ),
    "btn_refresh":        "Làm Mới",
    "dep_status_pending":    "Đang chờ",
    "dep_status_completed":  "Đã ghi có ✅",
    "dep_status_expired":    "Hết hạn ❌",
    "dep_status_cancelled":  "Đã hủy",

    "referral_title": (
        "🎁 <b>GIỚI THIỆU & NHẬN VIP</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "📨 Link giới thiệu của bạn:\n"
        "<code>https://t.me/{bot}?start={code}</code>\n\n"
        "👥 Giới thiệu: <b>{count}/{needed}</b>\n"
        "🌟 Trạng thái VIP: {vip}\n\n"
        "🎁 Nhận <b>{bonus} USDT</b> mỗi khi bạn bè nạp tiền!"
    ),
    "vip_badge": "\n🌟 Thành Viên VIP",

    "referral_bonus_msg": (
        "💰 <b>Thưởng Giới Thiệu Nạp Tiền!</b>\n\n"
        "Bạn bè của bạn đã nạp {amount}. "
        "🎁 Bạn nhận được <b>{bonus}</b>!"
    ),

    "support_title": (
        "🎧 <b>HỖ TRỢ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Cần giúp đỡ? Mở phiếu hỗ trợ.\n🔐 Hoàn toàn riêng tư."
    ),
    "btn_new_ticket":  "Tạo Phiếu Hỗ Trợ",
    "btn_my_tickets":  "Phiếu Của Tôi",
    "ticket_create_msg": (
        "🆕 <b>TẠO PHIẾU</b>\n\n"
        "Mô tả vấn đề của bạn chi tiết.\n"
        "<i>Bao gồm Order ID nếu liên quan đến mua hàng.</i>\n\n"
        "Nhập /cancel để hủy."
    ),
    "ticket_describe_issue": "Vui lòng mô tả vấn đề của bạn.",
    "ticket_created": (
        "✅ <b>Phiếu #{tid:04d} đã tạo!</b>\n\n"
        "📝 {subject}\n\n🎧 Đội ngũ sẽ phản hồi sớm nhất."
    ),
    "ticket_reply_prompt": "📩 <b>Trả lời Phiếu #{tid:04d}</b>\n\nNhập tin nhắn:\n\n/cancel để hủy.",
    "ticket_reply_sent":   "✅ Đã gửi trả lời cho Phiếu #{tid:04d}",
    "btn_view_ticket":     "Xem Phiếu #{tid:04d}",
    "btn_send_reply":      "Trả Lời",
    "btn_close_ticket":    "Đóng Phiếu",
    "no_open_tickets":     "✅ Không có phiếu nào đang mở.",
    "ticket_closed_user":  "✅ Phiếu #{tid:04d} đã được đóng.",
    "ticket_list_title":   "📋 <b>Phiếu Của Tôi</b>",
    "ticket_not_found":    "Không tìm thấy phiếu.",
    "ticket_admin_reply": (
        "🎧 <b>Phản Hồi Hỗ Trợ — Phiếu #{tid:04d}</b>\n\n{msg}"
    ),

    "order_status_completed": "✅ Hoàn thành",
    "order_status_refunded":  "♻️ Đã hoàn tiền",
    "order_no_credential":    "<i>Không có dữ liệu thông tin đăng nhập</i>",
    "order_not_found":        "Không tìm thấy đơn hàng.",
    "btn_request_refund":     "Yêu Cầu Hoàn Tiền",
    "btn_refund_pending":     "Đang Chờ Hoàn Tiền…",
    "history_title":          "📋 <b>Lịch Sử Mua Hàng</b>",
    "order_detail_body": (
        "📦 <b>Đơn Hàng #{oid:04d}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🛍️ {product_name}\n"
        "💰 {amount}\n"
        "📊 {status}\n"
        "📅 {date}\n\n"
        "{cred_text}"
    ),

    "refund_order_not_found":     "Không tìm thấy đơn hàng.",
    "refund_already_refunded":    "Đơn hàng này đã được hoàn tiền rồi.",
    "refund_already_pending":     "Bạn đã có yêu cầu hoàn tiền đang chờ xử lý.",
    "refund_prompt": (
        "📦 <b>Yêu Cầu Hoàn Tiền — Đơn Hàng #{oid:04d}</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🛍 <b>{product_name}</b> — {amount}\n\n"
        "✍️ Vui lòng nhập lý do yêu cầu hoàn tiền:\n"
        "<i>(hoặc /cancel để quay lại)</i>"
    ),
    "refund_error":          "❌ Có lỗi xảy ra. Vui lòng thử lại.",
    "refund_order_missing":  "❌ Không tìm thấy đơn hàng.",
    "refund_too_short":      "⚠️ Lý do quá ngắn. Vui lòng nhập ít nhất 5 ký tự.",
    "refund_submitted": (
        "✅ <b>Yêu cầu hoàn tiền đã được gửi!</b>\n\n"
        "📦 Đơn hàng #{oid:04d} — {product_name}\n"
        "💬 Lý do: <i>{reason}</i>\n\n"
        "⏳ Admin sẽ xem xét yêu cầu của bạn sớm."
    ),
    "refund_approved": (
        "✅ <b>Hoàn Tiền Được Duyệt!</b>\n\n"
        "📦 Đơn hàng #{oid:04d} — {product_name}\n"
        "💰 <b>{amount}</b> đã được ghi có vào số dư ví của bạn.\n\n"
        "🙏 Cảm ơn sự kiên nhẫn của bạn."
    ),
    "btn_my_profile":       "Hồ Sơ Của Tôi",
    "refund_rejected": (
        "❌ <b>Yêu Cầu Hoàn Tiền Bị Từ Chối</b>\n\n"
        "📦 Đơn hàng #{oid:04d}{product_part}\n\n"
        "💬 Yêu cầu hoàn tiền của bạn đã được xem xét và bị từ chối bởi admin.\n"
        "Nếu bạn cho rằng đây là nhầm lẫn, hãy mở phiếu hỗ trợ."
    ),
    "btn_support_ticket":   "Hỗ Trợ",

    "maintenance":         "🔧 <b>BOT ĐANG BẢO TRÌ</b>\n\nSẽ trở lại sớm!",
    "cancelled":           "❌ Đã hủy.",
    "cart_empty":          "🛒 Giỏ hàng của bạn trống.",
    "cart_title":          "🛒 <b>GIỎ HÀNG CỦA BẠN</b>",
    "cart_view_title":     "🛒 <b>GIỎ HÀNG CỦA BẠN</b>",
    "cart_total_line":     "💰 <b>Tổng: {total}</b>",
    "cart_balance_line":   "💼 Số dư: {balance}",
    "cart_item_oos":       "❌ {name} đã hết hàng!",
    "cart_checkout_done": (
        "🎉 <b>THANH TOÁN HOÀN TẤT!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    ),
    "cart_checkout_total": "💰 Tổng: {total}\n💼 Số dư: {balance}",
    "btn_checkout":        "Thanh Toán",
    "btn_clear_cart":      "Xóa Giỏ Hàng",
    "btn_cart_added":      "Đã thêm vào giỏ hàng!",
    "history_empty":       "📋 Chưa có lịch sử mua hàng.",
    "order_detail_title":  "📦 <b>ĐƠN HÀNG #{oid:04d}</b>",

    "free_item_already_claimed": (
        "🎁 <b>QUÀ MIỄN PHÍ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "✅ Bạn đã nhận quà miễn phí của mình rồi.\n"
        "Mỗi người dùng chỉ được nhận <b>một</b> quà miễn phí — cảm ơn bạn!"
    ),
    "free_item_none_available": (
        "🎁 <b>QUÀ MIỄN PHÍ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "😔 Hiện không có quà miễn phí nào. Quay lại sau nhé!"
    ),
    "free_item_list_header": (
        "🎁 <b>QUÀ MIỄN PHÍ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Chọn MỘT quà miễn phí bên dưới — chỉ được nhận một lần:"
    ),
    "free_item_stock_line":     "{emoji} <b>{name}</b>  (📦 còn {count})",
    "btn_claim_free":           "{emoji} Nhận {name}",
    "free_item_not_available":  "Không có sẵn.",
    "free_item_already_alert":  "Bạn đã nhận quà miễn phí của mình rồi.",
    "free_item_oos_alert":      "Hết hàng — thử quà khác.",
    "free_item_claimed_alert":  "🎉 Đã nhận!",
    "free_item_claimed_msg": (
        "🎉 <b>ĐÃ NHẬN QUÀ MIỄN PHÍ!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "{emoji} <b>{name}</b>\n\n"
        "<code>{data}</code>\n\n"
        "Chúc bạn vui vẻ! 🙌"
    ),
    "free_item_broadcast": (
        "🎁🎉 <b>CÓ QUÀ MIỄN PHÍ!</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "{emoji} <b>{name}</b> hiện đang được tặng MIỄN PHÍ!\n\n"
        "⚠️ Chỉ <b>1 quà miễn phí mỗi người</b> — ai nhanh người đó được.\n"
        "Nhấn bên dưới để nhận ngay 👇"
    ),
    "btn_claim_now":         "{emoji} Nhận Ngay",

    "admin_balance_added": (
        "💰 <b>Đã Thêm Số Dư!</b>\n\n"
        "+{amount} được ghi có bởi admin.\n"
        "Số dư mới: {balance}"
    ),
    "admin_deposit_credited": (
        "💰 <b>Đã Ghi Có Nạp Tiền!</b>\n\n"
        "✅ {amount} đã được admin thêm vào ví của bạn.\n"
        "💼 Số dư mới: <b>{balance}</b>"
    ),
    "admin_gift_received": (
        "🎁 <b>Đã Nhận Quà!</b>\n\n"
        "Một admin đã tặng bạn <b>{amount}</b>!\n"
        "💼 Số Dư Mới: <b>{balance}</b>"
    ),
    "admin_refund_processed": (
        "♻️ <b>Hoàn Tiền Đã Được Xử Lý</b>\n\n"
        "Đơn hàng <code>#{oid:04d}</code> đã được hoàn tiền.\n"
        "💰 {amount} đã được hoàn lại vào ví của bạn."
    ),
    "wd_approved_msg":   "✅ Yêu cầu rút tiền {amount} của bạn đã được xử lý!",
    "wd_rejected_msg":   "❌ Yêu cầu rút tiền {amount} của bạn bị từ chối. Liên hệ hỗ trợ.",

    "renewal_reminder": (
        "🔔 <b>Nhắc Nhở Gia Hạn</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Gói <b>{product_name}</b> của bạn sắp hết hạn!\n\n"
        "Hãy vào cửa hàng để gia hạn."
    ),
    "btn_shop_now":    "Mua Sắm Ngay",

    "reseller_not_approved": (
        "🏪 <b>CHƯƠNG TRÌNH ĐẠI LÝ</b>\n\n"
        "Đăng ký trở thành đại lý và mua sản phẩm với giá buôn!\n\n"
        "Liên hệ admin để được phê duyệt."
    ),
    "reseller_panel": (
        "🏪 <b>BẢNG ĐIỀU KHIỂN ĐẠI LÝ</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "🏷️ Giảm giá: <b>{discount}% OFF</b>\n"
        "💰 Khả dụng: <b>{available}</b>\n"
        "⏳ Đang chờ: <b>{pending}</b>\n"
        "💸 Đã rút: <b>{withdrawn}</b>"
    ),
    "btn_request_withdrawal": "Yêu Cầu Rút Tiền",
},
}

# Fallback to English for missing keys
_FALLBACK = TRANSLATIONS["en"]

def T(lang: str, key: str, **kwargs) -> str:
    """Translate key to user's language, fallback to English."""
    lang = lang if lang in TRANSLATIONS else "en"
    text = TRANSLATIONS[lang].get(key) or _FALLBACK.get(key, key)
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, ValueError):
            try:
                return _FALLBACK.get(key, key).format(**kwargs)
            except Exception:
                return text
    return text

LANG_OPTIONS = [
    ("en", "🇬🇧 English"),
    ("hi", "🇮🇳 हिंदी"),
    ("id", "🇮🇩 Bahasa Indonesia"),
    ("vi", "🇻🇳 Tiếng Việt"),
]
