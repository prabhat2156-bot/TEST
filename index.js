/**
 * WhatsApp Group Manager Bot — Multi-User Edition
 *
 * Features:
 *  - Multi-user: har user ka apna WhatsApp session
 *  - Premium System: free / trial (24hr) / premium (paid)
 *  - Bot Mode: free_mode / paid_mode
 *  - 24hr Trial on first /start
 *  - Referral System: /refer → 1 day premium per referral
 *  - Admin Panel: user management, stats, ban/unban
 *  - 6hr Auto Logout: WhatsApp session auto disconnect
 *  - /mystatus — user ka plan details
 *  - /help — all features
 *  - Data isolation: sabka alag data
 *  - Memory optimized for Render free tier
 *  - Keep-alive /health endpoint for UptimeRobot
 */

const { Telegraf, Markup } = require("telegraf");
const { connectDB }        = require("./src/db");
const { getSession, updateSession, resetSession, defaultGroupFlow, defaultFeatureFlow } = require("./src/session");
const {
  setCallbacks, getStatus, getPhone,
  connectAccount, disconnectAccount, reconnectSavedAccount,
  createGroup, updateGroupDescription, updateGroupPhoto,
  setDisappearingMessages, promoteToAdmin, setGroupPermissions,
  getGroupInviteLink, joinGroupViaLink,
  getAllGroupsWithDetails,
  leaveGroup, removeAllMembers,
  makeAdminByNumbers,
  getGroupApprovalStatus, setGroupApproval,
  approveAllPending,
  getGroupMembers, getGroupPendingRequests,
  resetGroupInviteLink,
  demoteAdminInGroup,
  getGroupSettings, applyGroupSettings,
  renameGroup,
  addMembersToGroup,
  getGroupInfoFromLink,
  getPendingRawJids,
  resolveVcfPhones,
  startAutoAcceptForGroups, stopAutoAcceptForGroups, getAutoAcceptStats,
} = require("./src/whatsapp-manager");
const {
  getOrCreateUser, userHasAccess, isPlanActive, getPlanLabel,
  addPremium, addTempPremium, removePremium,
  banUser, unbanUser,
  processReferral,
  getBotMode, setBotMode,
  getBotStats, listUsers,
  touchUser,
  getUsersForAutoLogout,
  markWaConnected, markWaDisconnected,
} = require("./src/users");
const { User } = require("./src/models");
const express  = require("express");
const http     = require("http");

const TOKEN    = process.env.TELEGRAM_BOT_TOKEN;
if (!TOKEN) { console.error("TELEGRAM_BOT_TOKEN not set!"); process.exit(1); }
const ADMIN_ID = parseInt(process.env.OWNER_ID || "0", 10);

const bot        = new Telegraf(TOKEN);
const sleep      = (ms) => new Promise((r) => setTimeout(r, ms));
const PAGE_SIZE  = 10;
const startTimes = new Map();
const aaLiveIntervals = new Map(); // uid → timer

// ─── Per-feature delay constants ───────────────────────────────────────────
const D = {
  getLinks: 1500, leave: 3000, removeMembers: 4000, makeAdmin: 3000,
  demoteAdmin: 2500, approvalToggle: 2000, approvePending: 4500,
  memberList: 1500, pendingList: 1500, resetLink: 2000,
  changeName: 2000, createGroup: 2500, joinGroup: 2500,
  addMembers: 2500, ctcCheck: 1200, vcfAutoMatch: 2000, pendingCheck: 1000,
};

// ─── accountId helper ──────────────────────────────────────────────────────
function accountId(uid) { return `wa_${uid}`; }

// ─── Access check middleware ───────────────────────────────────────────────
async function checkAccess(ctx, next) {
  const uid = ctx.from?.id;
  if (!uid) return;
  if (uid === ADMIN_ID) return next();

  const user = await getOrCreateUser(uid, ctx.from.first_name || "", ctx.from.username || "");
  await touchUser(uid);

  if (user.banned) {
    const reason = user.bannedReason ? `\nReason: ${user.bannedReason}` : "";
    if (ctx.callbackQuery) await ctx.answerCbQuery(`⛔ You are banned.${reason}`, { show_alert: true }).catch(() => {});
    else await ctx.reply(`⛔ *You are banned from using this bot.*${reason}`, { parse_mode: "Markdown" }).catch(() => {});
    return;
  }

  const mode = await getBotMode();
  if (mode === "paid_mode" && !isPlanActive(user)) {
    if (ctx.callbackQuery) {
      await ctx.answerCbQuery("⛔ Premium required!", { show_alert: true }).catch(() => {});
      await ctx.reply(
        `🔒 *Bot is in Paid Mode*\n━━━━━━━━━━━━━━━━━━━━\nYour plan: ${getPlanLabel(user)}\n\nContact admin for premium access.\n\nReferral: /refer (earn 1 day free per referral)`,
        { parse_mode: "Markdown" }
      ).catch(() => {});
    } else {
      await ctx.reply(
        `🔒 *Bot is in Paid Mode*\n━━━━━━━━━━━━━━━━━━━━\nYour plan: ${getPlanLabel(user)}\n\nContact admin for premium access.\n\nUse /refer to earn free premium days!`,
        { parse_mode: "Markdown" }
      ).catch(() => {});
    }
    return;
  }
  return next();
}

bot.use(checkAccess);

// ─── WA Callbacks ─────────────────────────────────────────────────────────
const pendingPairingCbs = new Map(); // accountId → cb
const pendingReadyCbs   = new Map();

setCallbacks({
  onPairingCode: async (aid, code) => {
    const cb = pendingPairingCbs.get(aid);
    if (cb) { pendingPairingCbs.delete(aid); await cb(code); }
  },
  onReady: async (aid) => {
    const cb = pendingReadyCbs.get(aid);
    if (cb) { pendingReadyCbs.delete(aid); await cb(); }
    // Mark connected in DB
    const uid = parseInt(aid.replace("wa_", ""), 10);
    const phone = getPhone(aid);
    if (uid) await markWaConnected(uid, phone).catch(() => {});
  },
  onDisconnected: async (aid) => {
    const uid = parseInt(aid.replace("wa_", ""), 10);
    if (uid) await markWaDisconnected(uid).catch(() => {});
  },
});

// ─── 6hr Auto Logout ──────────────────────────────────────────────────────
setInterval(async () => {
  try {
    const stale = await getUsersForAutoLogout();
    for (const u of stale) {
      const aid = accountId(u.telegramId);
      if (getStatus(aid) === "connected") {
        await disconnectAccount(aid).catch(() => {});
        await markWaDisconnected(u.telegramId).catch(() => {});
        await bot.telegram.sendMessage(u.telegramId,
          `⏰ *Auto Logout*\n━━━━━━━━━━━━━━━━━━━━\nYour WhatsApp session was automatically logged out after 6 hours.\n\nUse 📱 WhatsApp to reconnect.`,
          { parse_mode: "Markdown" }
        ).catch(() => {});
      }
    }
  } catch {}
}, 10 * 60 * 1000); // check every 10 min

// ─── withTimeout / withRetry ───────────────────────────────────────────────
function withTimeout(promise, ms = 15000, label = "Operation") {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error(`${label} timed out after ${ms / 1000}s`)), ms);
    promise.then((v) => { clearTimeout(t); resolve(v); }, (e) => { clearTimeout(t); reject(e); });
  });
}
async function withRetry(fn, retries = 3, baseDelay = 2000) {
  for (let attempt = 0; attempt <= retries; attempt++) {
    try { return await fn(); }
    catch (err) {
      if (attempt < retries) await sleep(Math.round(baseDelay * Math.pow(1.5, attempt)));
      else throw err;
    }
  }
}

// ─── Progress helpers ──────────────────────────────────────────────────────
async function startProgress(ctx, uid, text) {
  const m = await ctx.reply(text, {
    parse_mode: "Markdown",
    ...Markup.inlineKeyboard([[Markup.button.callback("🛑 Cancel", "cancel_exec")]]),
  });
  startTimes.set(uid, Date.now());
  updateSession(uid, { cancelMsgId: m.message_id, cancelPending: false });
  return m;
}
async function editProgress(chatId, msgId, text) {
  try {
    await bot.telegram.editMessageText(chatId, msgId, undefined, text, {
      parse_mode: "Markdown",
      reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🛑 Cancel", "cancel_exec")]]).reply_markup,
    });
  } catch {}
}
bot.action("cancel_exec", async (ctx) => {
  await ctx.answerCbQuery("Cancelling...");
  updateSession(ctx.from.id, { cancelPending: true });
  try { await ctx.editMessageText("🛑 *Cancelling...*", { parse_mode: "Markdown" }); } catch {}
});
function isCancelled(uid) { return getSession(uid).cancelPending === true; }
function elapsed(uid) { const t = startTimes.get(uid); return t ? Math.round((Date.now() - t) / 1000) : 0; }

// ─── Misc helpers ──────────────────────────────────────────────────────────
async function reply(ctx, text, extra = {})       { return await ctx.reply(text, extra); }
async function editOrReply(ctx, text, extra = {}) {
  try { return await ctx.editMessageText(text, extra); }
  catch { return await ctx.reply(text, extra); }
}
function bar(done, total) {
  const p = total > 0 ? Math.round((done / total) * 10) : 0;
  return `[${"█".repeat(p)}${"░".repeat(10 - p)}] ${total > 0 ? Math.round((done / total) * 100) : 0}%`;
}
function extractParticipantPhone(p) {
  const allJids = [p.jid, p.id, p.lid, p.userJid].filter((j) => j && typeof j === "string");
  const phoneJid = allJids.find((j) => j.endsWith("@s.whatsapp.net"));
  const displayJid = phoneJid || allJids[0] || "";
  return displayJid.split("@")[0].split(":")[0];
}
function parseVcf(content) {
  const contacts = [];
  const blocks = content.split(/(?=BEGIN:VCARD)/gi);
  for (const block of blocks) {
    if (!block.toUpperCase().includes("BEGIN:VCARD")) continue;
    const nameMatch = block.match(/^FN:(.+)$/m) || block.match(/^N:([^;\r\n]+)/m);
    const name = nameMatch ? nameMatch[1].trim().replace(/\\/g, "") : "";
    const telMatches = [...block.matchAll(/^TEL[^:]*:([^\r\n]+)/gim)];
    for (const m of telMatches) {
      const digits = m[1].trim().replace(/[\s()\-+]/g, "").replace(/[^0-9]/g, "");
      if (digits.length >= 10) contacts.push({ name, phone: digits });
    }
  }
  return contacts;
}
function extractCodes(text) {
  const matches = [...text.matchAll(/chat\.whatsapp\.com\/([A-Za-z0-9_-]+)/g)];
  return [...new Set(matches.map((m) => m[1]))];
}
async function downloadFile(ctx, fileId) {
  const u = await ctx.telegram.getFileLink(fileId);
  const r = await fetch(u.href);
  return Buffer.from(await r.arrayBuffer());
}

// ─── Feature Labels ────────────────────────────────────────────────────────
const FEAT_LABEL = {
  get_links: "🔗 Get Links", leave: "🚪 Leave Groups",
  remove_members: "🧹 Remove Members", make_admin: "👑 Make Admin",
  approval: "🔀 Approval Toggle", approve_pending: "✅ Approve Pending",
  member_list: "📋 Member List", pending_list: "⏳ Pending List",
  join_groups: "🔗 Join Groups", create_groups: "➕ Create Groups",
  add_members: "➕ Add Members", edit_settings: "⚙️ Edit Settings",
  change_name: "✏️ Change Name", reset_link: "🔄 Reset Link",
  demote_admin: "⬇️ Demote Admin", auto_accept: "⏰ Auto Accept",
  ctc_checker: "🔍 CTC Checker",
};

// ─── Summary ───────────────────────────────────────────────────────────────
async function sendSummary(ctx, opts) {
  const { feature, total, success, failed, cancelled, extra = [], boxLines = [] } = opts;
  const uid  = ctx.from?.id;
  const secs = uid ? elapsed(uid) : 0;
  if (uid) startTimes.delete(uid);
  const statusLine = cancelled ? "🚫 *Cancelled*" : failed === 0 ? "✅ *All done!*" : `⚠️ *Done with ${failed} failure(s)*`;
  let text = `📊 *${FEAT_LABEL[feature] || feature}*\n━━━━━━━━━━━━━━━━━━━━\n${statusLine}\n━━━━━━━━━━━━━━━━━━━━\nTotal   : ${total}\nSuccess : ${success}\nFailed  : ${failed}\nTime    : ${secs}s\n`;
  if (extra.length) text += `━━━━━━━━━━━━━━━━━━━━\n` + extra.join("\n") + "\n";
  text += `━━━━━━━━━━━━━━━━━━━━`;
  if (text.length > 4000) text = text.slice(0, 3990) + "\n_...more_";
  const replyMarkup = Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]);
  const session = uid ? getSession(uid) : null;
  const cancelMsgId = session?.cancelMsgId;
  if (cancelMsgId && ctx.chat?.id) {
    try {
      await bot.telegram.editMessageText(ctx.chat.id, cancelMsgId, undefined, text, { parse_mode: "Markdown", reply_markup: replyMarkup.reply_markup });
      if (uid) updateSession(uid, { cancelMsgId: null });
    } catch { await ctx.reply(text, { parse_mode: "Markdown", ...replyMarkup }); }
  } else { await ctx.reply(text, { parse_mode: "Markdown", ...replyMarkup }); }
  if (boxLines.length) {
    const CHUNK = 50;
    for (let c = 0; c < boxLines.length; c += CHUNK) {
      const chunk = boxLines.slice(c, c + CHUNK).join("\n");
      try { await ctx.reply("```\n" + chunk + "\n```", { parse_mode: "Markdown" }); }
      catch { await ctx.reply(chunk); }
      if (c + CHUNK < boxLines.length) await sleep(400);
    }
  }
}

// ══════════════════════════════════════════════════════════════════════════
// ─── COMMANDS ─────────────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

bot.start(async (ctx) => {
  const uid = ctx.from.id;
  const args = ctx.message?.text?.split(" ").slice(1) || [];
  const refCode = args[0] || null;

  // Create or get user (gives trial on first join)
  const user = await getOrCreateUser(uid, ctx.from.first_name || "", ctx.from.username || "");

  // Process referral if present
  if (refCode && !user.referredBy) {
    const referrer = await processReferral(uid, refCode);
    if (referrer) {
      await ctx.reply(`🎁 *Referral Applied!*\nYou were referred by a friend.\n\n${getPlanLabel(user)}`, { parse_mode: "Markdown" });
    }
  }

  resetSession(uid);
  await sendMainMenu(ctx);
});

bot.command("menu", async (ctx) => {
  const uid = ctx.from.id;
  updateSession(uid, { awaitingPhoneForIndex: null, groupFlow: null, joinFlow: null, featureFlow: null, cancelPending: false, awaitingVcf: null });
  await sendMainMenu(ctx);
});

bot.command("mystatus", async (ctx) => {
  const uid  = ctx.from.id;
  const user = await getOrCreateUser(uid, ctx.from.first_name || "", ctx.from.username || "");
  const now  = Date.now();
  const waStatus = getStatus(accountId(uid));

  let planDetails = "";
  if (user.plan === "trial" && user.trialEnd) {
    const hrs = Math.max(0, Math.ceil((user.trialEnd - now) / 3600000));
    planDetails = `⏳ Trial expires in *${hrs} hour(s)*\n📅 Trial started: ${user.trialStart ? new Date(user.trialStart).toLocaleDateString() : "-"}\n📅 Trial ends: ${new Date(user.trialEnd).toLocaleDateString()}`;
  } else if (user.plan === "premium" && user.premiumEnd) {
    const days = Math.max(0, Math.ceil((user.premiumEnd - now) / 86400000));
    planDetails = `⭐ Premium — *${days} day(s) left*\n📅 Started: ${user.premiumStart ? new Date(user.premiumStart).toLocaleDateString() : "-"}\n📅 Expires: ${new Date(user.premiumEnd).toLocaleDateString()}`;
  } else {
    planDetails = `❌ No active plan`;
  }

  const refLink = `https://t.me/${ctx.botInfo.username}?start=${user.referralCode}`;

  await ctx.reply(
    `👤 *My Status*\n━━━━━━━━━━━━━━━━━━━━\n` +
    `Name     : *${user.firstName || ctx.from.first_name || "User"}*\n` +
    `User ID  : \`${uid}\`\n` +
    `Plan     : ${getPlanLabel(user)}\n` +
    `━━━━━━━━━━━━━━━━━━━━\n${planDetails}\n` +
    `━━━━━━━━━━━━━━━━━━━━\n` +
    `📱 WhatsApp: ${waStatus === "connected" ? `✅ +${getPhone(accountId(uid))}` : "❌ Not connected"}\n` +
    `━━━━━━━━━━━━━━━━━━━━\n` +
    `🔗 Referral Code: \`${user.referralCode}\`\n` +
    `👥 Referrals Made: *${user.referralCount}*\n` +
    `🔗 Your Link: ${refLink}`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
  );
});

bot.command("refer", async (ctx) => {
  const uid  = ctx.from.id;
  const user = await getOrCreateUser(uid, ctx.from.first_name || "", ctx.from.username || "");
  const refLink = `https://t.me/${ctx.botInfo.username}?start=${user.referralCode}`;
  await ctx.reply(
    `🎁 *Referral Program*\n━━━━━━━━━━━━━━━━━━━━\n` +
    `Share your link — earn *1 day premium* per referral!\n\n` +
    `🔗 *Your Link:*\n${refLink}\n\n` +
    `Your Code: \`${user.referralCode}\`\n` +
    `Referrals Made: *${user.referralCount}*\n\n` +
    `_When someone joins using your link, you get 1 day premium automatically._`,
    { parse_mode: "Markdown" }
  );
});

bot.command("help", async (ctx) => {
  await ctx.reply(
    `📖 *Bot Features — Help*\n━━━━━━━━━━━━━━━━━━━━\n\n` +
    `*WhatsApp Group Tools:*\n` +
    `🔗 Get Links — Invite links nikalo\n` +
    `🚪 Leave Groups — Groups chhodo\n` +
    `🧹 Remove Members — Members hatao\n` +
    `👑 Make Admin — Admin banao\n` +
    `⬇️ Demote Admin — Admin hatao\n` +
    `🔀 Approval Toggle — Join approval on/off\n` +
    `✅ Approve Pending — Pending approve karo\n` +
    `📋 Member List — Members count dekho\n` +
    `⏳ Pending List — Pending count dekho\n` +
    `🔄 Reset Link — Invite link reset karo\n` +
    `⚙️ Edit Settings — Group settings\n` +
    `✏️ Change Name — Group rename karo\n` +
    `⏰ Auto Accept — Auto join requests accept\n` +
    `🔍 CTC Checker — VCF vs group check\n` +
    `➕ Add Members — VCF se members add karo\n` +
    `🔗 Join Groups — Links se groups join karo\n` +
    `➕ Create Groups — Naye groups banao\n\n` +
    `━━━━━━━━━━━━━━━━━━━━\n` +
    `*Commands:*\n` +
    `/start — Bot shuru karo\n` +
    `/menu — Main menu\n` +
    `/mystatus — Apna plan dekho\n` +
    `/refer — Referral link\n` +
    `/help — Yeh list\n\n` +
    `━━━━━━━━━━━━━━━━━━━━\n` +
    `*Plans:*\n` +
    `🆓 Trial — 24 hours free (naye user)\n` +
    `⭐ Premium — Full access\n` +
    `_Refer karo, 1 day premium pao!_`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
  );
});

// ─── Main Menu ─────────────────────────────────────────────────────────────
function buildMainMenu(uid) {
  const aid = accountId(uid);
  const c = getStatus(aid) === "connected", p = getPhone(aid);
  const b = (label, cb) => Markup.button.callback(label, c ? cb : "need_connect");
  return Markup.inlineKeyboard([
    [Markup.button.callback(c ? `📱 WhatsApp ✅ +${p}` : `📱 WhatsApp ❌ Not Connected`, "menu_account")],
    [b("➕ Create Groups",   "create_groups_start"), b("🔗 Join Groups",     "join_groups_start")],
    [b("🔗 Get Links",       "feat_getlinks"),       b("🚪 Leave Groups",    "feat_leave")],
    [b("🧹 Remove Members",  "feat_removemem"),      b("👑 Make Admin",      "feat_makeadmin")],
    [b("⬇️ Demote Admin",    "feat_demoteadmin"),    b("🔀 Approval Toggle", "feat_approval")],
    [b("✅ Approve Pending", "feat_approvepending"), b("🔄 Reset Link",      "feat_resetlink")],
    [b("📋 Member List",     "feat_memberlist"),     b("➕ Add Members",     "feat_addmembers")],
    [b("⚙️ Edit Settings",   "feat_editsettings"),   b("✏️ Change Name",     "feat_changename")],
    [b("⏰ Auto Accept",     "feat_autoaccept"),     b("🔍 CTC Checker",     "feat_ctcchecker")],
    [Markup.button.callback("📊 Status", "menu_status")],
  ]);
}

async function sendMainMenu(ctx) {
  const uid = ctx.from?.id;
  const aid = accountId(uid);
  const user = await getOrCreateUser(uid, ctx.from?.first_name || "", ctx.from?.username || "");
  updateSession(uid, { cancelPending: false, awaitingVcf: null });
  const c = getStatus(aid) === "connected", p = getPhone(aid);
  const userName = ctx.from?.first_name || "User";
  const planLabel = getPlanLabel(user);

  await ctx.reply(
    `🤖 *ᴡꜱ ᴀᴜᴛᴏᴍᴀᴛɪᴏɴ* 🤖\n` +
    `▰▰▰▰▰▰▰▰▰▰▰▰▰\n\n` +
    `👋 Hey *${userName}*!\n` +
    `📋 Plan: ${planLabel}\n\n` +
    `╭─── 📡 Status ─────────╮\n` +
    `│ ${c ? "✅" : "❌"}  WhatsApp: ${c ? `Connected (+${p})` : "Not Connected"}\n` +
    `╰───────────────────────╯\n\n` +
    `› Choose an option:`,
    { parse_mode: "Markdown", ...buildMainMenu(uid) }
  );
}

bot.action("need_connect", async (ctx) => { await ctx.answerCbQuery("⚠️ Connect WhatsApp first!", { show_alert: true }); });
bot.action("back_menu", async (ctx) => {
  await ctx.answerCbQuery();
  const uid = ctx.from.id;
  if (aaLiveIntervals.has(uid)) { clearInterval(aaLiveIntervals.get(uid)); aaLiveIntervals.delete(uid); }
  updateSession(uid, { awaitingPhoneForIndex: null, groupFlow: null, joinFlow: null, featureFlow: null, cancelPending: false, awaitingVcf: null });
  await sendMainMenu(ctx);
});

// ─── Status ────────────────────────────────────────────────────────────────
bot.action("menu_status", async (ctx) => {
  await ctx.answerCbQuery();
  const uid = ctx.from.id;
  const aid = accountId(uid);
  const s = getStatus(aid), p = getPhone(aid);
  const icon = s === "connected" ? "✅" : s === "connecting" ? "⏳" : "❌";
  await editOrReply(ctx,
    `📊 *Bot Status*\n━━━━━━━━━━━━━━━━━━━━\n${icon} WhatsApp: *${s}*${s === "connected" ? `\n📞 +${p}` : ""}\n━━━━━━━━━━━━━━━━━━━━`,
    { parse_mode: "Markdown", reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]).reply_markup }
  );
});

// ─── Account ───────────────────────────────────────────────────────────────
bot.action("menu_account", async (ctx) => {
  await ctx.answerCbQuery();
  const uid    = ctx.from.id;
  const aid    = accountId(uid);
  const status = getStatus(aid), phone = getPhone(aid);
  if (status === "connected") {
    await editOrReply(ctx,
      `📱 *WhatsApp Account*\n━━━━━━━━━━━━━━━━━━━━\n✅ Connected\n📞 +${phone}\n⏰ Auto-logout in 6h from connect\n━━━━━━━━━━━━━━━━━━━━\nLogout?`,
      { parse_mode: "Markdown", reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🔌 Logout", "logout_wa")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]).reply_markup }
    );
  } else if (status === "connecting") {
    await editOrReply(ctx,
      `📱 *WhatsApp Account*\n━━━━━━━━━━━━━━━━━━━━\n⏳ Connecting...\n━━━━━━━━━━━━━━━━━━━━`,
      { parse_mode: "Markdown", reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🔄 Reset", "reset_wa")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]).reply_markup }
    );
  } else {
    updateSession(uid, { awaitingPhoneForIndex: 0 });
    await editOrReply(ctx,
      `📱 *Connect WhatsApp*\n━━━━━━━━━━━━━━━━━━━━\n\nSend your phone number with country code:\n\n*Example:* \`919876543210\`\n\n⚠️ Pairing code expires in 60 seconds!`,
      { parse_mode: "Markdown", reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]).reply_markup }
    );
  }
});

bot.action("logout_wa", async (ctx) => {
  await ctx.answerCbQuery("Logging out...");
  const uid = ctx.from.id;
  const aid = accountId(uid);
  await editOrReply(ctx, `⏳ *Logging out...*`, { parse_mode: "Markdown" });
  await disconnectAccount(aid);
  await markWaDisconnected(uid);
  await sleep(800);
  await sendMainMenu(ctx);
});
bot.action("reset_wa", async (ctx) => {
  await ctx.answerCbQuery("Resetting...");
  const uid = ctx.from.id;
  const aid = accountId(uid);
  await disconnectAccount(aid);
  await markWaDisconnected(uid);
  updateSession(uid, { awaitingPhoneForIndex: 0 });
  await editOrReply(ctx,
    `📱 *Connect WhatsApp*\n━━━━━━━━━━━━━━━━━━━━\n\nSend your phone number:\n*Example:* \`919876543210\``,
    { parse_mode: "Markdown", reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]).reply_markup }
  );
});

// ══════════════════════════════════════════════════════════════════════════
// ─── ADMIN PANEL ──────────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

function isAdmin(uid) { return uid === ADMIN_ID; }

bot.command("admin", async (ctx) => {
  if (!isAdmin(ctx.from.id)) { await ctx.reply("⛔ Unauthorized."); return; }
  await showAdminPanel(ctx);
});

async function showAdminPanel(ctx) {
  const stats = await getBotStats();
  const mode  = await getBotMode();
  await ctx.reply(
    `🛠 *Admin Panel*\n━━━━━━━━━━━━━━━━━━━━\n` +
    `👥 Total Users   : *${stats.total}*\n` +
    `📅 Today New     : *${stats.todayNew}*\n` +
    `⭐ Premium Active: *${stats.premiumCount}*\n` +
    `🆓 Trial Active  : *${stats.trialCount}*\n` +
    `⛔ Banned        : *${stats.banned}*\n` +
    `━━━━━━━━━━━━━━━━━━━━\n` +
    `🤖 Bot Mode: *${mode === "free_mode" ? "🟢 Free Mode" : "🔴 Paid Mode"}*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("👥 User List",        "adm_userlist_0")],
      [Markup.button.callback("⭐ Add Premium",      "adm_add_prem"),   Markup.button.callback("❌ Remove Premium", "adm_rem_prem")],
      [Markup.button.callback("⏱ Temp Premium",     "adm_temp_prem")],
      [Markup.button.callback("⛔ Ban User",         "adm_ban"),        Markup.button.callback("✅ Unban User",      "adm_unban")],
      [Markup.button.callback(`🔄 Toggle Mode (now: ${mode === "free_mode" ? "Free" : "Paid"})`, "adm_toggle_mode")],
    ]) }
  );
}

bot.action("adm_toggle_mode", async (ctx) => {
  if (!isAdmin(ctx.from.id)) { await ctx.answerCbQuery("⛔"); return; }
  await ctx.answerCbQuery();
  const cur = await getBotMode();
  const next = cur === "free_mode" ? "paid_mode" : "free_mode";
  await setBotMode(next);
  await ctx.reply(`✅ Bot mode changed to: *${next === "free_mode" ? "🟢 Free Mode" : "🔴 Paid Mode"}*`, { parse_mode: "Markdown" });
  await showAdminPanel(ctx);
});

bot.action(/^adm_userlist_(\d+)$/, async (ctx) => {
  if (!isAdmin(ctx.from.id)) { await ctx.answerCbQuery("⛔"); return; }
  await ctx.answerCbQuery();
  const page  = parseInt(ctx.match[1]);
  const users = await listUsers(page, 8);
  const now   = Date.now();
  const lines = users.map((u, i) => {
    const plan = u.plan === "premium" && u.premiumEnd && u.premiumEnd > now
      ? `⭐${Math.ceil((u.premiumEnd - now) / 86400000)}d`
      : u.plan === "trial" && u.trialEnd && u.trialEnd > now
        ? `🆓${Math.ceil((u.trialEnd - now) / 3600000)}h`
        : "❌";
    return `${page * 8 + i + 1}. ${u.firstName || "?"} [\`${u.telegramId}\`] ${plan}${u.banned ? " ⛔" : ""}`;
  });
  const nav = [];
  if (page > 0) nav.push(Markup.button.callback("◀️ Prev", `adm_userlist_${page - 1}`));
  if (users.length === 8) nav.push(Markup.button.callback("▶️ Next", `adm_userlist_${page + 1}`));
  await editOrReply(ctx,
    `👥 *Users — Page ${page + 1}*\n━━━━━━━━━━━━━━━━━━━━\n${lines.join("\n") || "No users"}`,
    { parse_mode: "Markdown", reply_markup: Markup.inlineKeyboard([nav, [Markup.button.callback("🔙 Admin Panel", "adm_back")]]).reply_markup }
  );
});

bot.action("adm_back", async (ctx) => {
  if (!isAdmin(ctx.from.id)) { await ctx.answerCbQuery("⛔"); return; }
  await ctx.answerCbQuery();
  await showAdminPanel(ctx);
});

// ─── Add Premium ──────────────────────────────────────────────────────────
bot.action("adm_add_prem", async (ctx) => {
  if (!isAdmin(ctx.from.id)) { await ctx.answerCbQuery("⛔"); return; }
  await ctx.answerCbQuery();
  updateSession(ctx.from.id, { adminFlow: { step: "add_prem_id" } });
  await ctx.reply(`⭐ *Add Premium*\nSend: \`userId days\`\nExample: \`123456789 30\``, { parse_mode: "Markdown" });
});

// ─── Remove Premium ───────────────────────────────────────────────────────
bot.action("adm_rem_prem", async (ctx) => {
  if (!isAdmin(ctx.from.id)) { await ctx.answerCbQuery("⛔"); return; }
  await ctx.answerCbQuery();
  updateSession(ctx.from.id, { adminFlow: { step: "rem_prem_id" } });
  await ctx.reply(`❌ *Remove Premium*\nSend user ID:`, { parse_mode: "Markdown" });
});

// ─── Temp Premium ─────────────────────────────────────────────────────────
bot.action("adm_temp_prem", async (ctx) => {
  if (!isAdmin(ctx.from.id)) { await ctx.answerCbQuery("⛔"); return; }
  await ctx.answerCbQuery();
  updateSession(ctx.from.id, { adminFlow: { step: "temp_prem_id" } });
  await ctx.reply(`⏱ *Temp Premium*\nSend: \`userId hours\`\nExample: \`123456789 12\``, { parse_mode: "Markdown" });
});

// ─── Ban ──────────────────────────────────────────────────────────────────
bot.action("adm_ban", async (ctx) => {
  if (!isAdmin(ctx.from.id)) { await ctx.answerCbQuery("⛔"); return; }
  await ctx.answerCbQuery();
  updateSession(ctx.from.id, { adminFlow: { step: "ban_id" } });
  await ctx.reply(`⛔ *Ban User*\nSend: \`userId reason\`\nExample: \`123456789 spam\``, { parse_mode: "Markdown" });
});

// ─── Unban ────────────────────────────────────────────────────────────────
bot.action("adm_unban", async (ctx) => {
  if (!isAdmin(ctx.from.id)) { await ctx.answerCbQuery("⛔"); return; }
  await ctx.answerCbQuery();
  updateSession(ctx.from.id, { adminFlow: { step: "unban_id" } });
  await ctx.reply(`✅ *Unban User*\nSend user ID:`, { parse_mode: "Markdown" });
});

// ══════════════════════════════════════════════════════════════════════════
// ─── GROUP SELECTION SYSTEM ───────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

async function showGroupTypeSelect(ctx, feature) {
  const label = FEAT_LABEL[feature] || feature;
  await reply(ctx, `${label}\n━━━━━━━━━━━━━━━━━━━━\n*Select groups:*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("🔍 Similar Groups", `gs_similar_${feature}`)],
      [Markup.button.callback("📋 All Groups",      `gs_all_${feature}`)],
      [Markup.button.callback("☑️ Select Groups",   `gs_select_${feature}`)],
      [Markup.button.callback("🏠 Main Menu", "back_menu")],
    ]) }
  );
}

const FEAT_MAP = {
  getlinks: "get_links", leave: "leave", removemem: "remove_members",
  makeadmin: "make_admin", approval: "approval", approvepending: "approve_pending",
  editsettings: "edit_settings", resetlink: "reset_link", demoteadmin: "demote_admin",
  autoaccept: "auto_accept",
};

Object.keys(FEAT_MAP).forEach((key) => {
  bot.action(`feat_${key}`, async (ctx) => {
    await ctx.answerCbQuery();
    const uid = ctx.from.id;
    const aid = accountId(uid);
    if (getStatus(aid) !== "connected") { await ctx.answerCbQuery("⚠️ WhatsApp not connected!", { show_alert: true }); return; }
    const feature = FEAT_MAP[key];
    updateSession(uid, { featureFlow: defaultFeatureFlow(feature), cancelPending: false });
    await showGroupTypeSelect(ctx, feature);
  });
});

bot.action("feat_memberlist", async (ctx) => {
  await ctx.answerCbQuery();
  const uid = ctx.from.id;
  if (getStatus(accountId(uid)) !== "connected") { await ctx.answerCbQuery("⚠️ WhatsApp not connected!", { show_alert: true }); return; }
  updateSession(uid, { featureFlow: defaultFeatureFlow("member_list"), cancelPending: false });
  await reply(ctx, `📋 *Member List*\n━━━━━━━━━━━━━━━━━━━━\n*What to view?*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("👥 Member Count",     "ml_sub_members")],
      [Markup.button.callback("⏳ Pending Requests", "ml_sub_pending")],
      [Markup.button.callback("🏠 Main Menu", "back_menu")],
    ]) }
  );
});
bot.action("ml_sub_members", async (ctx) => {
  await ctx.answerCbQuery();
  updateSession(ctx.from.id, { featureFlow: { ...getSession(ctx.from.id).featureFlow, feature: "member_list" } });
  await showGroupTypeSelect(ctx, "member_list");
});
bot.action("ml_sub_pending", async (ctx) => {
  await ctx.answerCbQuery();
  updateSession(ctx.from.id, { featureFlow: { ...getSession(ctx.from.id).featureFlow, feature: "pending_list" } });
  await showGroupTypeSelect(ctx, "pending_list");
});

bot.action("feat_addmembers", async (ctx) => {
  await ctx.answerCbQuery();
  const uid = ctx.from.id;
  if (getStatus(accountId(uid)) !== "connected") { await ctx.answerCbQuery("⚠️ WhatsApp not connected!", { show_alert: true }); return; }
  updateSession(uid, {
    featureFlow: { ...defaultFeatureFlow("add_members"), step: "am_links", links: [], vcfs: [], currentVcfIdx: 0, addMode: "bulk" },
    cancelPending: false,
  });
  await reply(ctx,
    `➕ *Add Members*\n━━━━━━━━━━━━━━━━━━━━\n\nSend group invite links — one per line:\n\`\`\`\nhttps://chat.whatsapp.com/ABC\n\`\`\``,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
  );
});

bot.action("feat_changename", async (ctx) => {
  await ctx.answerCbQuery();
  const uid = ctx.from.id;
  if (getStatus(accountId(uid)) !== "connected") { await ctx.answerCbQuery("⚠️ WhatsApp not connected!", { show_alert: true }); return; }
  updateSession(uid, { featureFlow: { ...defaultFeatureFlow("change_name"), step: "cn_mode" }, cancelPending: false });
  await reply(ctx, `✏️ *Change Name*\n━━━━━━━━━━━━━━━━━━━━\n*Select naming method:*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("🔀 Custom Name",        "cn_random")],
      [Markup.button.callback("📛 Match VCF Filename", "cn_vcf")],
      [Markup.button.callback("🏠 Main Menu",           "back_menu")],
    ]) }
  );
});

bot.action("ctc_start_check", async (ctx) => {
  await ctx.answerCbQuery("Starting...");
  const uid  = ctx.from.id;
  const flow = getSession(uid).featureFlow;
  if (!flow || flow.step !== "ctc_vcf_collecting") { await ctx.answerCbQuery("⚠️ No active CTC session.", { show_alert: true }); return; }
  const vcfList = flow.vcfList || [];
  if (!vcfList.length) { await ctx.answerCbQuery("⚠️ Upload at least 1 VCF first!", { show_alert: true }); return; }
  updateSession(uid, { featureFlow: { ...flow, step: "ctc_running" }, awaitingVcf: null });
  await ctx.reply(`⏳ *Starting CTC check — ${vcfList.length} VCF(s) vs ${(flow.links||[]).length} group(s)...*`, { parse_mode: "Markdown" });
  await runCtcChecker(ctx);
});

bot.action("feat_ctcchecker", async (ctx) => {
  await ctx.answerCbQuery();
  const uid = ctx.from.id;
  if (getStatus(accountId(uid)) !== "connected") { await ctx.answerCbQuery("⚠️ WhatsApp not connected!", { show_alert: true }); return; }
  updateSession(uid, { featureFlow: { ...defaultFeatureFlow("ctc_checker"), step: "ctc_links", links: [], vcfList: [], ctcVcfIdx: 0 }, cancelPending: false });
  await reply(ctx,
    `🔍 *CTC Checker*\n━━━━━━━━━━━━━━━━━━━━\n\n*Step 1:* Send all group invite links — one per line:\n\`\`\`\nhttps://chat.whatsapp.com/ABC\n\`\`\`\n_Then in Step 2 send ALL VCFs at once (1st VCF = 1st group, etc.)_`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
  );
});

// ─── Similar Groups ────────────────────────────────────────────────────────
bot.action(/^gs_similar_(.+)$/, async (ctx) => {
  await ctx.answerCbQuery("Detecting groups...");
  const uid = ctx.from.id;
  const feature = ctx.match[1];
  try {
    const all = await getAllGroupsWithDetails(accountId(uid));
    if (!all.length) { await reply(ctx, "❌ No groups found.", Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]])); return; }
    const wordMap = {};
    for (const g of all) {
      const firstWord = (g.name.trim().split(/\s+/)[0] || g.name).toLowerCase();
      if (!wordMap[firstWord]) wordMap[firstWord] = [];
      wordMap[firstWord].push(g.id);
    }
    const entries = Object.entries(wordMap).sort((a, b) => b[1].length - a[1].length);
    updateSession(uid, { featureFlow: { ...getSession(uid).featureFlow, feature, allGroups: all, wordGroups: wordMap, step: "similar_pick" } });
    const visEntries = entries.slice(0, 20);
    const rows = [];
    for (let i = 0; i < visEntries.length; i += 2) {
      const row = [];
      for (let j = i; j < Math.min(i + 2, visEntries.length); j++) {
        const [word, ids] = visEntries[j];
        const idx = entries.findIndex(([w]) => w === word);
        row.push(Markup.button.callback(`${word} (${ids.length})`, `gs_swp_${idx}`));
      }
      rows.push(row);
    }
    rows.push([Markup.button.callback("🔍 Custom Keyword", "gs_sim_custom")]);
    rows.push([Markup.button.callback("🏠 Main Menu", "back_menu")]);
    await reply(ctx, `🔍 *Similar Groups*\n━━━━━━━━━━━━━━━━━━━━\nTotal: *${all.length}* groups\n\n*Auto-detected prefixes:*`, { parse_mode: "Markdown", ...Markup.inlineKeyboard(rows) });
  } catch (err) { await reply(ctx, `❌ Error: ${err.message}`, Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]])); }
});

bot.action(/^gs_swp_(\d+)$/, async (ctx) => {
  await ctx.answerCbQuery();
  const uid = ctx.from.id;
  const idx  = parseInt(ctx.match[1]);
  const flow = getSession(uid).featureFlow;
  const entries = Object.entries(flow.wordGroups || {}).sort((a, b) => b[1].length - a[1].length);
  if (idx >= entries.length) return;
  const [word, ids] = entries[idx];
  const matching = flow.allGroups.filter((g) => ids.includes(g.id));
  updateSession(uid, { featureFlow: { ...flow, selectedIds: ids, keyword: word, step: "confirm" } });
  await reply(ctx,
    `✅ *"${word}" — ${matching.length} group(s):*\n━━━━━━━━━━━━━━━━━━━━\n${matching.slice(0, 20).map((g, i) => `${i + 1}. ${g.name}`).join("\n")}${matching.length > 20 ? `\n_...and ${matching.length - 20} more_` : ""}`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("🚀 Proceed", "gs_sim_proceed")],
      [Markup.button.callback("🔙 Back", `gs_similar_${flow.feature}`)],
      [Markup.button.callback("🏠 Main Menu", "back_menu")],
    ]) }
  );
});

bot.action("gs_sim_custom", async (ctx) => {
  await ctx.answerCbQuery();
  const flow = getSession(ctx.from.id).featureFlow;
  updateSession(ctx.from.id, { featureFlow: { ...flow, step: "similar_query" } });
  await reply(ctx, `🔍 *Custom Keyword Search*\n━━━━━━━━━━━━━━━━━━━━\nType a keyword:`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) });
});

bot.action(/^gs_all_(.+)$/, async (ctx) => {
  await ctx.answerCbQuery("Loading groups...");
  const uid = ctx.from.id;
  const feature = ctx.match[1];
  try {
    const groups = await getAllGroupsWithDetails(accountId(uid));
    if (!groups.length) { await reply(ctx, "❌ No groups found.", Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]])); return; }
    updateSession(uid, { featureFlow: { ...getSession(uid).featureFlow, feature, allGroups: groups, selectedIds: groups.map(g=>g.id), step: "confirm" } });
    await reply(ctx,
      `✅ *All Groups Selected — ${groups.length} groups*\n━━━━━━━━━━━━━━━━━━━━\n${groups.slice(0, 10).map((g, i) => `${i + 1}. ${g.name}`).join("\n")}${groups.length > 10 ? `\n_...and ${groups.length - 10} more_` : ""}`,
      { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🚀 Proceed", "gs_sim_proceed")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
    );
  } catch (err) { await reply(ctx, `❌ Error: ${err.message}`, Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]])); }
});

bot.action(/^gs_select_(.+)$/, async (ctx) => {
  await ctx.answerCbQuery("Loading...");
  const uid = ctx.from.id;
  const feature = ctx.match[1];
  try {
    const groups = await getAllGroupsWithDetails(accountId(uid));
    if (!groups.length) { await reply(ctx, "❌ No groups found.", Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]])); return; }
    updateSession(uid, { featureFlow: { ...getSession(uid).featureFlow, feature, allGroups: groups, selectedIds: [], page: 0, step: "select" } });
    await showPaginatedGroups(ctx);
  } catch (err) { await reply(ctx, `❌ Error: ${err.message}`, Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]])); }
});

async function showPaginatedGroups(ctx) {
  const flow = getSession(ctx.from.id).featureFlow;
  const { allGroups, selectedIds, page } = flow;
  const selSet = new Set(selectedIds);
  const totalPages = Math.ceil(allGroups.length / PAGE_SIZE);
  const slice = allGroups.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE);
  const rows = [];
  for (let i = 0; i < slice.length; i++) {
    const idx = page * PAGE_SIZE + i, g = slice[i];
    const name = g.name.length > 40 ? g.name.slice(0, 39) + "…" : g.name;
    rows.push([Markup.button.callback(`${selSet.has(g.id) ? "✅" : "◻️"} ${name}`, `gs_tog_${idx}`)]);
  }
  const nav = [];
  if (page > 0) nav.push(Markup.button.callback("◀️", "gs_prev"));
  nav.push(Markup.button.callback(`${page + 1}/${totalPages}`, "gs_noop"));
  if (page < totalPages - 1) nav.push(Markup.button.callback("▶️", "gs_next"));
  rows.push(nav);
  rows.push([Markup.button.callback(`✅ Confirm (${selSet.size} selected)`, "gs_confirm")]);
  rows.push([Markup.button.callback("🏠 Main Menu", "back_menu")]);
  const text = `☑️ *Select Groups* — Page ${page + 1}/${totalPages}\n━━━━━━━━━━━━━━━━━━━━\nTotal: *${allGroups.length}*  •  Selected: *${selSet.size}*\n_Tap to select/deselect_`;
  try { await ctx.editMessageText(text, { parse_mode: "Markdown", reply_markup: Markup.inlineKeyboard(rows).reply_markup }); }
  catch { await ctx.reply(text, { parse_mode: "Markdown", ...Markup.inlineKeyboard(rows) }); }
}

bot.action("gs_noop",    async (ctx) => { await ctx.answerCbQuery(); });
bot.action("gs_next",    async (ctx) => { await ctx.answerCbQuery(); const flow = getSession(ctx.from.id).featureFlow; if (flow.page < Math.ceil(flow.allGroups.length / PAGE_SIZE) - 1) updateSession(ctx.from.id, { featureFlow: { ...flow, page: flow.page + 1 } }); await showPaginatedGroups(ctx); });
bot.action("gs_prev",    async (ctx) => { await ctx.answerCbQuery(); const flow = getSession(ctx.from.id).featureFlow; if (flow.page > 0) updateSession(ctx.from.id, { featureFlow: { ...flow, page: flow.page - 1 } }); await showPaginatedGroups(ctx); });
bot.action(/^gs_tog_(\d+)$/, async (ctx) => {
  await ctx.answerCbQuery();
  const idx = parseInt(ctx.match[1]);
  const flow = getSession(ctx.from.id).featureFlow;
  const gid  = flow.allGroups[idx]?.id; if (!gid) return;
  const sel  = new Set(flow.selectedIds);
  sel.has(gid) ? sel.delete(gid) : sel.add(gid);
  updateSession(ctx.from.id, { featureFlow: { ...flow, selectedIds: [...sel] } });
  await showPaginatedGroups(ctx);
});
bot.action("gs_confirm", async (ctx) => {
  await ctx.answerCbQuery();
  const flow = getSession(ctx.from.id).featureFlow;
  if (!flow.selectedIds.length) { await ctx.answerCbQuery("⚠️ Select at least 1 group!", { show_alert: true }); return; }
  await onGroupsConfirmed(ctx, flow.feature, flow.selectedIds, flow.allGroups);
});
bot.action("gs_sim_proceed", async (ctx) => {
  await ctx.answerCbQuery();
  const flow = getSession(ctx.from.id).featureFlow;
  await onGroupsConfirmed(ctx, flow.feature, flow.selectedIds, flow.allGroups);
});

async function onGroupsConfirmed(ctx, feature, selectedIds, allGroups) {
  const s = getSession(ctx.from.id);
  if (feature === "make_admin") {
    updateSession(ctx.from.id, { featureFlow: { ...s.featureFlow, selectedIds, allGroups, step: "admin_numbers" } });
    await reply(ctx, `👑 *Make Admin*\n━━━━━━━━━━━━━━━━━━━━\n*${selectedIds.length} group(s) selected*\n\nSend phone numbers — one per line:\n\`\`\`\n919876543210\n\`\`\`\n_Country code required_`,
      { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }); return;
  }
  if (feature === "demote_admin") {
    updateSession(ctx.from.id, { featureFlow: { ...s.featureFlow, selectedIds, allGroups, step: "demote_numbers" } });
    await reply(ctx, `⬇️ *Demote Admin*\n━━━━━━━━━━━━━━━━━━━━\n*${selectedIds.length} group(s) selected*\n\nSend admin phone numbers to demote:`,
      { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }); return;
  }
  if (feature === "edit_settings") {
    updateSession(ctx.from.id, { featureFlow: { ...s.featureFlow, selectedIds, allGroups, step: "es_configure",
      desiredSettings: { announce: null, restrict: null, joinApproval: null, memberAddMode: null } } });
    await showEditSettingsConfig(ctx); return;
  }
  if (feature === "auto_accept") {
    updateSession(ctx.from.id, { featureFlow: { ...s.featureFlow, selectedIds, allGroups, step: "aa_duration" } });
    await showAutoAcceptDuration(ctx); return;
  }
  updateSession(ctx.from.id, { featureFlow: { ...s.featureFlow, selectedIds, allGroups } });
  await runFeature(ctx, feature, selectedIds, allGroups, []);
}

// ══════════════════════════════════════════════════════════════════════════
// ─── EDIT SETTINGS ────────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

function esFmt(val)    { if (val === null || val === undefined) return "Skip"; return val ? "✅ ON" : "❌ OFF"; }
function esFmtSend(val){ if (val === null || val === undefined) return "Skip"; return val === false ? "✅ ON" : "❌ OFF"; }
function settingsKb(d) {
  return Markup.inlineKeyboard([
    [Markup.button.callback(`💬 All Can Send      : ${esFmtSend(d.announce)}`,  "es_tog_announce")],
    [Markup.button.callback(`✏️ Edit Info (lock)  : ${esFmt(d.restrict)}`,      "es_tog_restrict")],
    [Markup.button.callback(`🔐 Join Approval     : ${esFmt(d.joinApproval)}`,  "es_tog_joinApproval")],
    [Markup.button.callback(`➕ All Can Add       : ${esFmt(d.memberAddMode)}`, "es_tog_memberAddMode")],
    [Markup.button.callback("💾 Apply Settings", "es_apply")],
    [Markup.button.callback("🏠 Main Menu", "back_menu")],
  ]);
}
async function showEditSettingsConfig(ctx) {
  const flow = getSession(ctx.from.id).featureFlow;
  const d    = flow.desiredSettings;
  await reply(ctx, `⚙️ *Edit Settings*\n━━━━━━━━━━━━━━━━━━━━\n*${flow.selectedIds.length} group(s) selected*\n\nTap to toggle — cycles: Skip → ON → OFF`,
    { parse_mode: "Markdown", ...settingsKb(d) });
}
["announce", "restrict", "joinApproval", "memberAddMode"].forEach((key) => {
  bot.action(`es_tog_${key}`, async (ctx) => {
    await ctx.answerCbQuery();
    const flow = getSession(ctx.from.id).featureFlow;
    const cur  = flow.desiredSettings[key];
    let next;
    if (key === "announce") { next = cur === null ? false : cur === false ? true : null; }
    else { next = cur === null ? true : cur === true ? false : null; }
    const newSettings = { ...flow.desiredSettings, [key]: next };
    updateSession(ctx.from.id, { featureFlow: { ...flow, desiredSettings: newSettings } });
    try { await ctx.editMessageReplyMarkup(settingsKb(newSettings).reply_markup); }
    catch { await showEditSettingsConfig(ctx); }
  });
});
bot.action("es_apply", async (ctx) => {
  await ctx.answerCbQuery("Applying...");
  const uid  = ctx.from.id;
  const flow = getSession(uid).featureFlow;
  const d    = flow.desiredSettings;
  if (d.announce === null && d.restrict === null && d.joinApproval === null && d.memberAddMode === null) { await ctx.answerCbQuery("⚠️ No settings selected!", { show_alert: true }); return; }
  const sel = flow.allGroups.filter((g) => flow.selectedIds.includes(g.id));
  const total = sel.length;
  updateSession(uid, { cancelPending: false });
  const pm = await startProgress(ctx, uid, `⚙️ Applying settings — ${total} group(s)...\n${bar(0, total)}`);
  let changed = 0, alreadyOk = 0, failed = 0, cancelled = false;
  for (let i = 0; i < total; i++) {
    if (isCancelled(uid)) { cancelled = true; break; }
    const g = sel[i];
    await editProgress(ctx.chat.id, pm.message_id, `⚙️ Applying settings...\nDone: ${i}/${total}  ❌ ${failed}\n→ ${g.name}\n${bar(i, total)}`);
    try {
      const result = await withRetry(() => applyGroupSettings(accountId(uid), g.id, d), 2, 2000);
      if (result.changes.length) changed++; else alreadyOk++;
    } catch { failed++; }
    await sleep(D.approvalToggle);
  }
  await sendSummary(ctx, { feature: "edit_settings", total, success: changed, failed, cancelled,
    extra: [`Total Selected: ${total}`, `Changed       : ${changed}`, `Already OK    : ${alreadyOk}`, `Failed        : ${failed}`] });
  updateSession(uid, { featureFlow: null });
});

// ══════════════════════════════════════════════════════════════════════════
// ─── CHANGE NAME ──────────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

bot.action("cn_random", async (ctx) => {
  await ctx.answerCbQuery();
  const flow = getSession(ctx.from.id).featureFlow;
  updateSession(ctx.from.id, { featureFlow: { ...flow, step: "cn_random_name", cnMethod: "random" } });
  await reply(ctx, `✏️ *Change Name — Custom*\n━━━━━━━━━━━━━━━━━━━━\n\nType the base name:`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) });
});
bot.action("cn_numbering_yes", async (ctx) => {
  await ctx.answerCbQuery();
  const flow = getSession(ctx.from.id).featureFlow;
  updateSession(ctx.from.id, { featureFlow: { ...flow, numbering: true, step: "cn_random_links" } });
  await reply(ctx, `✏️ *Numbering: ON*\n\nNow send group invite links (one per line):`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) });
});
bot.action("cn_numbering_no", async (ctx) => {
  await ctx.answerCbQuery();
  const flow = getSession(ctx.from.id).featureFlow;
  updateSession(ctx.from.id, { featureFlow: { ...flow, numbering: false, step: "cn_random_links" } });
  await reply(ctx, `✏️ *Numbering: OFF*\n\nNow send group invite links (one per line):`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) });
});
async function runChangeNameRandom(ctx, links, baseName, numbering) {
  const uid = ctx.from.id;
  const total = links.length;
  updateSession(uid, { cancelPending: false });
  const pm = await startProgress(ctx, uid, `✏️ Renaming ${total} group(s)...\n${bar(0, total)}`);
  let done = 0, failed = 0, cancelled = false;
  const boxLines = [];
  for (let i = 0; i < total; i++) {
    if (isCancelled(uid)) { cancelled = true; break; }
    const code    = links[i];
    const newName = numbering ? `${baseName} ${i + 1}` : baseName;
    await editProgress(ctx.chat.id, pm.message_id, `✏️ Renaming...\nDone: ${done}/${total}  ❌ ${failed}\n→ "${newName}"\n${bar(i, total)}`);
    try {
      const info = await withTimeout(withRetry(() => getGroupInfoFromLink(accountId(uid), code), 2, 1500), 12000, "GetGroupInfo");
      if (!info) throw new Error("Invalid/expired link");
      await withTimeout(withRetry(() => renameGroup(accountId(uid), info.id, newName), 2, 1500), 12000, "RenameGroup");
      done++; boxLines.push(`${info.name} ➡️ ${newName}`);
    } catch (err) { failed++; boxLines.push(`❌ Group ${i + 1}: ${err.message}`); }
    await sleep(D.changeName);
  }
  await sendSummary(ctx, { feature: "change_name", total, success: done, failed, cancelled, boxLines });
  updateSession(uid, { featureFlow: null });
}

bot.action("cn_vcf", async (ctx) => {
  await ctx.answerCbQuery();
  const flow = getSession(ctx.from.id).featureFlow;
  updateSession(ctx.from.id, { featureFlow: { ...flow, step: "cn_vcf_collecting", cnMethod: "vcf", vcfList: [] }, awaitingVcf: { feature: "change_name", step: "cn_vcf" } });
  await reply(ctx,
    `📛 *Change Name — Match VCF Filename*\n━━━━━━━━━━━━━━━━━━━━\n\n*How it works:*\n• Send multiple VCF files at once\n• Bot scans ALL your groups\n• Group renamed to matching VCF filename\n• No group links needed!\n\n📎 *Send all VCF files now, then tap Start Renaming:*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) });
});
async function showVcfCollectStatus(ctx, vcfList) {
  const lines = vcfList.map((v, i) => `${i + 1}. *${v.name}* — ${v.contacts.length} contacts`).join("\n");
  await reply(ctx,
    `📛 *VCFs collected: ${vcfList.length}*\n━━━━━━━━━━━━━━━━━━━━\n${lines}\n━━━━━━━━━━━━━━━━━━━━\nSend more VCF files or tap *Start Renaming*:`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback(`▶️ Start Renaming (${vcfList.length} VCF${vcfList.length > 1 ? "s" : ""})`, "cn_vcf_start")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]) });
}
bot.action("cn_vcf_start", async (ctx) => {
  await ctx.answerCbQuery("Starting...");
  const uid  = ctx.from.id;
  const flow = getSession(uid).featureFlow;
  const vcfList = flow.vcfList || [];
  if (!vcfList.length) { await ctx.answerCbQuery("⚠️ Send at least one VCF first!", { show_alert: true }); return; }
  updateSession(uid, { awaitingVcf: null });
  await runChangeNameAsVcfAuto(ctx, vcfList);
});

async function runChangeNameAsVcfAuto(ctx, vcfList) {
  const uid = ctx.from.id;
  const aid = accountId(uid);
  updateSession(uid, { cancelPending: false });
  const loadMsg = await ctx.reply(`📛 *Loading all groups...*`, { parse_mode: "Markdown" });
  let allGroups;
  try { allGroups = await withRetry(() => getAllGroupsWithDetails(aid)); }
  catch (err) { try { await bot.telegram.deleteMessage(ctx.chat.id, loadMsg.message_id); } catch {} await ctx.reply(`❌ Failed to load groups: ${err.message}`); return; }
  try { await bot.telegram.deleteMessage(ctx.chat.id, loadMsg.message_id); } catch {}
  const totalGroups = allGroups.length;
  const resolveMsg  = await ctx.reply(`📛 *Resolving ${vcfList.length} VCF(s) via WhatsApp...*`, { parse_mode: "Markdown" });
  const resolvedVcfs = [];
  for (const v of vcfList) {
    const phones = (v.contacts || []).map((c) => c.phone);
    const resolved = phones.length ? await resolveVcfPhones(aid, phones) : [];
    resolvedVcfs.push({ name: v.name, resolved });
    await sleep(300);
  }
  try { await bot.telegram.deleteMessage(ctx.chat.id, resolveMsg.message_id); } catch {}
  const pm = await startProgress(ctx, uid, `📛 Scanning ${totalGroups} group(s)...\nVCFs: ${vcfList.length}\n${bar(0, totalGroups)}`);
  let renamed = 0, skipped = 0, failed = 0, cancelled = false;
  const boxLines = [];
  for (let i = 0; i < totalGroups; i++) {
    if (isCancelled(uid)) { cancelled = true; break; }
    const g = allGroups[i];
    await editProgress(ctx.chat.id, pm.message_id, `📛 Scanning groups...\nRenamed: ${renamed}  Skipped: ${skipped}  ❌ ${failed}\n→ ${g.name}\n${bar(i, totalGroups)}`);
    try {
      const groupJids = new Set(), groupPhones = new Set();
      for (const p of (g.participants || [])) {
        const fields = [p.jid, p.id, p.lid, p.participant, p.userJid].filter((j) => j && typeof j === "string");
        for (const j of fields) {
          const norm = j.replace(/:\d+@/, "@").toLowerCase().trim();
          groupJids.add(norm);
          if (norm.endsWith("@s.whatsapp.net")) { const ph = norm.split("@")[0]; if (ph && ph.length >= 7) groupPhones.add(ph); }
        }
      }
      try {
        const { jids: pendingJids, phones: pendingPhones } = await withTimeout(withRetry(() => getPendingRawJids(aid, g.id), 2, 1500), 10000, "PendingJids");
        pendingJids.forEach((j) => groupJids.add(j));
        pendingPhones.forEach((ph) => groupPhones.add(ph));
      } catch {}
      await sleep(D.pendingCheck);
      let bestVcf = null, bestCount = 0;
      for (const vcf of resolvedVcfs) {
        let count = 0;
        for (const r of vcf.resolved) {
          if ((r.phoneJid && groupJids.has(r.phoneJid)) || (r.lid && groupJids.has(r.lid))) { count++; continue; }
          if (r.phone && groupPhones.has(r.phone)) { count++; continue; }
          if (r.phone && groupPhones.size > 0) {
            for (const gph of groupPhones) { if (numberMatchesLocal(gph, r.phone)) { count++; break; } }
          }
        }
        if (count > bestCount) { bestCount = count; bestVcf = vcf; }
      }
      if (bestVcf && bestCount > 0) { await withTimeout(withRetry(() => renameGroup(aid, g.id, bestVcf.name), 2, 1500), 12000, "RenameGroup"); renamed++; boxLines.push(`${g.name} ➡️ ${bestVcf.name}`); }
      else skipped++;
    } catch (err) { failed++; boxLines.push(`❌ ${g.name}: ${err.message}`); }
    await sleep(D.vcfAutoMatch);
  }
  await sendSummary(ctx, { feature: "change_name", total: totalGroups, success: renamed, failed, cancelled,
    extra: [`Groups scanned : ${totalGroups}`, `Renamed        : ${renamed}`, `No match (skip): ${skipped}`], boxLines });
  updateSession(uid, { featureFlow: null, awaitingVcf: null });
}

function numberMatchesLocal(stored, input) {
  if (!stored || !input) return false;
  const s = stored.replace(/\D/g, ""), i = input.replace(/\D/g, "");
  if (s === i) return true;
  if (i.length >= 8 && s.endsWith(i)) return true;
  if (s.length >= 8 && i.endsWith(s)) return true;
  return false;
}

// ══════════════════════════════════════════════════════════════════════════
// ─── AUTO ACCEPT ──────────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

async function showAutoAcceptDuration(ctx) {
  const flow = getSession(ctx.from.id).featureFlow;
  await reply(ctx, `⏰ *Auto Accept*\n━━━━━━━━━━━━━━━━━━━━\n*${flow.selectedIds.length} group(s) selected*\n\nSelect duration:`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("5 min",  "aa_dur_300"),   Markup.button.callback("10 min", "aa_dur_600")],
      [Markup.button.callback("30 min", "aa_dur_1800"),  Markup.button.callback("1 hour", "aa_dur_3600")],
      [Markup.button.callback("2 hrs",  "aa_dur_7200"),  Markup.button.callback("6 hrs",  "aa_dur_21600")],
      [Markup.button.callback("✏️ Custom (minutes)", "aa_dur_custom")],
      [Markup.button.callback("🏠 Main Menu", "back_menu")],
    ]) }
  );
}
bot.action(/^aa_dur_(\d+)$/, async (ctx) => {
  await ctx.answerCbQuery();
  const secs = parseInt(ctx.match[1]);
  const flow = getSession(ctx.from.id).featureFlow;
  updateSession(ctx.from.id, { featureFlow: { ...flow, aaDuration: secs, step: "aa_confirm" } });
  const mins = secs / 60, label = mins >= 60 ? `${mins / 60}h` : `${mins}min`;
  await reply(ctx,
    `⏰ *Auto Accept — Confirm*\n━━━━━━━━━━━━━━━━━━━━\nGroups   : *${flow.selectedIds.length}*\nDuration : *${label}*\n\n_Checks every 8 sec. Approval must be ON._`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("▶️ Start", "aa_start")], [Markup.button.callback("🔙 Change Duration", "aa_back_duration")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]) });
});
bot.action("aa_dur_custom", async (ctx) => {
  await ctx.answerCbQuery();
  const flow = getSession(ctx.from.id).featureFlow;
  updateSession(ctx.from.id, { featureFlow: { ...flow, step: "aa_custom_duration" } });
  await reply(ctx, `⏰ *Custom Duration*\nType minutes:\n_Example:_ \`120\` = 2 hours`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) });
});
bot.action("aa_back_duration", async (ctx) => { await ctx.answerCbQuery(); await showAutoAcceptDuration(ctx); });

function buildLiveAutoAcceptText(sel, label, endTime, stats) {
  const totalAccepted = Object.values(stats).reduce((s, v) => s + (v?.accepted || 0), 0);
  const groupLines    = sel.map((g) => `• *${g.name}*: ${stats[g.id]?.accepted || 0}`).join("\n");
  return `⏰ *Auto Accept — ACTIVE* 🟢\n━━━━━━━━━━━━━━━━━━━━\nGroups   : *${sel.length}*\nDuration : *${label}*\nEnds at  : ${endTime}\n━━━━━━━━━━━━━━━━━━━━\n✅ *Total Accepted: ${totalAccepted}*\n━━━━━━━━━━━━━━━━━━━━\n${groupLines}\n━━━━━━━━━━━━━━━━━━━━\n_Checks every 8 sec._`;
}

bot.action("aa_start", async (ctx) => {
  await ctx.answerCbQuery("Starting...");
  const uid  = ctx.from.id;
  const flow = getSession(uid).featureFlow;
  const secs = flow.aaDuration;
  const sel  = (flow.allGroups || []).filter((g) => flow.selectedIds.includes(g.id));
  const mins = secs / 60, label = mins >= 60 ? `${mins / 60}h` : `${mins}min`;
  const endTime = new Date(Date.now() + secs * 1000).toLocaleTimeString();
  if (aaLiveIntervals.has(uid)) { clearInterval(aaLiveIntervals.get(uid)); aaLiveIntervals.delete(uid); }
  startAutoAcceptForGroups(accountId(uid), flow.selectedIds);
  updateSession(uid, { featureFlow: { ...flow, step: "aa_running" } });
  const initialStats = getAutoAcceptStats(accountId(uid), flow.selectedIds);
  const statusMsg = await reply(ctx, buildLiveAutoAcceptText(sel, label, endTime, initialStats),
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🛑 Stop", "aa_stop")]]) }
  );
  const liveInterval = setInterval(async () => {
    try {
      const stats = getAutoAcceptStats(accountId(uid), flow.selectedIds);
      await bot.telegram.editMessageText(ctx.chat.id, statusMsg.message_id, undefined,
        buildLiveAutoAcceptText(sel, label, endTime, stats),
        { parse_mode: "Markdown", reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🛑 Stop", "aa_stop")]]).reply_markup }
      );
    } catch {}
  }, 5000);
  aaLiveIntervals.set(uid, liveInterval);
  setTimeout(async () => {
    if (!aaLiveIntervals.has(uid)) return;
    clearInterval(aaLiveIntervals.get(uid)); aaLiveIntervals.delete(uid);
    const stats = getAutoAcceptStats(accountId(uid), flow.selectedIds);
    stopAutoAcceptForGroups(accountId(uid), flow.selectedIds);
    const totalAccepted = Object.values(stats).reduce((s, v) => s + (v?.accepted || 0), 0);
    const boxLines = sel.map((g) => `${g.name}: ${stats[g.id]?.accepted || 0} accepted`);
    try { await bot.telegram.editMessageText(ctx.chat.id, statusMsg.message_id, undefined, `⏰ *Auto Accept — Finished*  ✅ Accepted: *${totalAccepted}*`, { parse_mode: "Markdown" }); } catch {}
    await sendSummary(ctx, { feature: "auto_accept", total: sel.length, success: sel.length, failed: 0, cancelled: false,
      extra: [`Total Groups : ${sel.length}`, `Total Accepted: ${totalAccepted}`, `Duration      : ${label}`], boxLines });
    updateSession(uid, { featureFlow: null });
  }, secs * 1000);
});

bot.action("aa_stop", async (ctx) => {
  await ctx.answerCbQuery("Stopping...");
  const uid  = ctx.from.id;
  const flow = getSession(uid).featureFlow;
  if (aaLiveIntervals.has(uid)) { clearInterval(aaLiveIntervals.get(uid)); aaLiveIntervals.delete(uid); }
  if (!flow?.selectedIds) { await sendMainMenu(ctx); return; }
  const stats = getAutoAcceptStats(accountId(uid), flow.selectedIds);
  stopAutoAcceptForGroups(accountId(uid), flow.selectedIds);
  const totalAccepted = Object.values(stats).reduce((s, v) => s + (v?.accepted || 0), 0);
  const sel     = (flow.allGroups || []).filter((g) => flow.selectedIds.includes(g.id));
  const boxLines = sel.map((g) => `${g.name}: ${stats[g.id]?.accepted || 0} accepted`);
  try { await ctx.editMessageText(`🛑 *Auto Accept Stopped*  Total: *${totalAccepted}*`, { parse_mode: "Markdown" }); } catch {}
  await sendSummary(ctx, { feature: "auto_accept", total: sel.length, success: sel.length, failed: 0, cancelled: true,
    extra: [`Total Groups : ${sel.length}`, `Total Accepted: ${totalAccepted}`], boxLines });
  updateSession(uid, { featureFlow: null });
});

// ══════════════════════════════════════════════════════════════════════════
// ─── MAIN FEATURE RUNNER ──────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

async function runFeature(ctx, feature, selectedIds, allGroups, extraNums) {
  const uid   = ctx.from.id;
  const aid   = accountId(uid);
  const sel   = allGroups.filter((g) => selectedIds.includes(g.id));
  const total = sel.length;
  if (!total) { await reply(ctx, "❌ No groups selected."); return; }
  updateSession(uid, { cancelPending: false });

  if (feature === "get_links") {
    const pm = await startProgress(ctx, uid, `🔗 Getting links — ${total} group(s)...\n${bar(0, total)}`);
    let done = 0, failed = 0, cancelled = false;
    const results = [], fails = [];
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      await editProgress(ctx.chat.id, pm.message_id, `🔗 Getting links...\nDone: ${done}/${total}  ❌ ${failed}\n→ ${g.name}\n${bar(i, total)}`);
      try { results.push({ name: g.name, link: await withRetry(() => getGroupInviteLink(aid, g.id)) }); done++; }
      catch { fails.push(g.name); failed++; }
      await sleep(D.getLinks);
    }
    const boxLines = results.map((r) => `${r.name}\n${r.link}`);
    if (fails.length) fails.forEach((n) => boxLines.push(`❌ ${n}: failed`));
    await sendSummary(ctx, { feature: "get_links", total, success: done, failed, cancelled, extra: [`Total Groups : ${total}`, `Successful   : ${done}`, `Failed       : ${failed}`], boxLines });
    updateSession(uid, { featureFlow: null }); return;
  }

  if (feature === "leave") {
    const pm = await startProgress(ctx, uid, `🚪 Leaving ${total} group(s)...\n${bar(0, total)}`);
    let done = 0, failed = 0, cancelled = false;
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      await editProgress(ctx.chat.id, pm.message_id, `🚪 Leaving groups...\nDone: ${done}/${total}  ❌ ${failed}\n→ ${g.name}\n${bar(i, total)}`);
      try { await withRetry(() => leaveGroup(aid, g.id)); done++; }
      catch { failed++; }
      await sleep(D.leave);
    }
    await sendSummary(ctx, { feature, total, success: done, failed, cancelled, extra: [`Total Selected: ${total}`, `Leave Success : ${done}`, `Leave Failed  : ${failed}`] });
    updateSession(uid, { featureFlow: null }); return;
  }

  if (feature === "remove_members") {
    const pm = await startProgress(ctx, uid, `🧹 Removing members — ${total} group(s)...\n${bar(0, total)}`);
    let done = 0, failed = 0, totalRem = 0, cancelled = false;
    const boxLines = [];
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      await editProgress(ctx.chat.id, pm.message_id, `🧹 Removing members (1 by 1)...\nDone: ${done}/${total}  ❌ ${failed}\n→ ${g.name}\n${bar(i, total)}`);
      try {
        const n = await withRetry(() => removeAllMembers(aid, g.id, 1, true));
        totalRem += n; done++; boxLines.push(`${g.name}: ${n} members removed`);
      } catch { failed++; boxLines.push(`❌ ${g.name}: failed`); }
      await sleep(D.removeMembers);
    }
    await sendSummary(ctx, { feature, total, success: done, failed, cancelled,
      extra: [`Total Selected: ${total}`, `Total Removed : ${totalRem}`, `Bot left group: ❌ No (stays in group)`], boxLines });
    updateSession(uid, { featureFlow: null }); return;
  }

  if (feature === "make_admin") {
    const pm = await startProgress(ctx, uid, `👑 Making admin — ${total} group(s)...\n${bar(0, total)}`);
    let done = 0, failed = 0, totalProm = 0, cancelled = false;
    const boxLines = [];
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      await editProgress(ctx.chat.id, pm.message_id, `👑 Making admin...\nDone: ${done}/${total}  ❌ ${failed}\n→ ${g.name}\n${bar(i, total)}`);
      try {
        const n = await makeAdminByNumbers(aid, g.id, extraNums);
        totalProm += n; done++;
        boxLines.push(n > 0 ? `${g.name}: ${n} admin set` : `${g.name}: not found`);
      } catch { failed++; boxLines.push(`❌ ${g.name}: failed`); }
      await sleep(D.makeAdmin);
    }
    await sendSummary(ctx, { feature, total, success: done, failed, cancelled,
      extra: [`Number(s)     : ${extraNums.map((n) => `+${n}`).join(", ")}`, `Total Selected: ${total}`, `Admin Set     : ${totalProm}`], boxLines });
    updateSession(uid, { featureFlow: null }); return;
  }

  if (feature === "demote_admin") {
    const pm = await startProgress(ctx, uid, `⬇️ Demoting admins — ${total} group(s)...\n${bar(0, total)}`);
    let done = 0, failed = 0, totalDem = 0, cancelled = false;
    const boxLines = [];
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      await editProgress(ctx.chat.id, pm.message_id, `⬇️ Demoting admins...\nDone: ${done}/${total}  ❌ ${failed}\n→ ${g.name}\n${bar(i, total)}`);
      try {
        const n = await demoteAdminInGroup(aid, g.id, extraNums);
        totalDem += n; done++;
        boxLines.push(n > 0 ? `${g.name}: ${n} demoted` : `${g.name}: not an admin`);
      } catch { failed++; boxLines.push(`❌ ${g.name}: failed`); }
      await sleep(D.demoteAdmin);
    }
    await sendSummary(ctx, { feature, total, success: done, failed, cancelled,
      extra: [`Number(s)     : ${extraNums.map((n) => `+${n}`).join(", ")}`, `Total Selected: ${total}`, `Total Demoted : ${totalDem}`], boxLines });
    updateSession(uid, { featureFlow: null }); return;
  }

  if (feature === "reset_link") {
    const pm = await startProgress(ctx, uid, `🔄 Resetting links — ${total} group(s)...\n${bar(0, total)}`);
    let done = 0, failed = 0, cancelled = false;
    const results = [], fails = [];
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      await editProgress(ctx.chat.id, pm.message_id, `🔄 Resetting links...\nDone: ${done}/${total}  ❌ ${failed}\n→ ${g.name}\n${bar(i, total)}`);
      try { results.push({ name: g.name, link: await withRetry(() => resetGroupInviteLink(aid, g.id)) }); done++; }
      catch { fails.push(g.name); failed++; }
      await sleep(D.resetLink);
    }
    const boxLines = results.map((r) => `${r.name}\n${r.link}`);
    if (fails.length) fails.forEach((n) => boxLines.push(`❌ ${n}: failed`));
    await sendSummary(ctx, { feature: "reset_link", total, success: done, failed, cancelled,
      extra: [`Total Selected: ${total}`, `Success       : ${done}`, `Failed        : ${failed}`], boxLines });
    updateSession(uid, { featureFlow: null }); return;
  }

  if (feature === "approval") {
    const pm = await startProgress(ctx, uid, `🔀 Toggling approval — ${total} group(s)...\n${bar(0, total)}`);
    let done = 0, failed = 0, cancelled = false;
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      await editProgress(ctx.chat.id, pm.message_id, `🔀 Toggling approval...\nDone: ${done}/${total}  ❌ ${failed}\n→ ${g.name}\n${bar(i, total)}`);
      try {
        const cur = await withRetry(() => getGroupApprovalStatus(aid, g.id)), next = !cur;
        await withRetry(() => setGroupApproval(aid, g.id, next));
        done++;
      } catch { failed++; }
      await sleep(D.approvalToggle);
    }
    await sendSummary(ctx, { feature, total, success: done, failed, cancelled,
      extra: [`Total Selected: ${total}`, `Toggle Success : ${done}`, `Toggle Failed  : ${failed}`] });
    updateSession(uid, { featureFlow: null }); return;
  }

  if (feature === "approve_pending") {
    const pm = await startProgress(ctx, uid, `✅ Approving pending — ${total} group(s)...\n${bar(0, total)}`);
    let done = 0, failed = 0, totPend = 0, totApproved = 0, cancelled = false;
    const boxLines = [];
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      await editProgress(ctx.chat.id, pm.message_id, `✅ Approving pending...\nDone: ${done}/${total}  ❌ ${failed}\n→ ${g.name}\n${bar(i, total)}`);
      try {
        const r = await withRetry(() => approveAllPending(aid, g.id), 2, 5000);
        totPend += r.pendingCount; totApproved += r.approved; done++;
        boxLines.push(`${i + 1}. ${g.name} ${r.approved} member add`);
      } catch { failed++; boxLines.push(`${i + 1}. ${g.name}: failed`); }
      await sleep(D.approvePending);
    }
    await sendSummary(ctx, { feature, total, success: done, failed, cancelled,
      extra: [`Total Groups  : ${total}`, `Total Pending : ${totPend}`, `Total Approved: ${totApproved}`], boxLines });
    updateSession(uid, { featureFlow: null }); return;
  }

  if (feature === "member_list") {
    const pm = await startProgress(ctx, uid, `📋 Counting members — ${total} group(s)...\n${bar(0, total)}`);
    let done = 0, failed = 0, grandTotal = 0, cancelled = false;
    const boxLines = [];
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      await editProgress(ctx.chat.id, pm.message_id, `📋 Member list...\nDone: ${done}/${total}  ❌ ${failed}\n→ ${g.name}\n${bar(i, total)}`);
      try {
        const members = await withRetry(() => getGroupMembers(aid, g.id));
        grandTotal += members.length;
        boxLines.push(`${i + 1} = ${members.length} members`);
        done++;
      } catch { failed++; boxLines.push(`${g.name}: failed`); }
      await sleep(D.memberList);
    }
    await sendSummary(ctx, { feature: "member_list", total, success: done, failed, cancelled,
      extra: [`Total Groups  : ${total}`, `Total Members : ${grandTotal}`], boxLines });
    updateSession(uid, { featureFlow: null }); return;
  }

  if (feature === "pending_list") {
    const pm = await startProgress(ctx, uid, `⏳ Fetching pending — ${total} group(s)...\n${bar(0, total)}`);
    let done = 0, failed = 0, grandPending = 0, cancelled = false;
    const boxLines = [];
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      await editProgress(ctx.chat.id, pm.message_id, `⏳ Pending list...\nDone: ${done}/${total}  ❌ ${failed}\n→ ${g.name}\n${bar(i, total)}`);
      try {
        const { list: pending } = await withRetry(() => getGroupPendingRequests(aid, g.id));
        grandPending += pending.length;
        boxLines.push(`${i + 1} = ${pending.length} pending`);
        done++;
      } catch { failed++; boxLines.push(`${g.name}: failed`); }
      await sleep(D.pendingList);
    }
    await sendSummary(ctx, { feature: "pending_list", total, success: done, failed, cancelled,
      extra: [`Total Groups  : ${total}`, `Total Pending : ${grandPending}`], boxLines });
    updateSession(uid, { featureFlow: null }); return;
  }
}

// ══════════════════════════════════════════════════════════════════════════
// ─── JOIN GROUPS ──────────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

bot.action("join_groups_start", async (ctx) => {
  await ctx.answerCbQuery();
  const uid = ctx.from.id;
  if (getStatus(accountId(uid)) !== "connected") { await ctx.answerCbQuery("⚠️ WhatsApp not connected!", { show_alert: true }); return; }
  updateSession(uid, { joinFlow: { step: "links" }, cancelPending: false });
  await reply(ctx, `🔗 *Join Groups*\n━━━━━━━━━━━━━━━━━━━━\n\nSend invite links — one per line:\n\`\`\`\nhttps://chat.whatsapp.com/ABC123\n\`\`\``,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("❌ Cancel", "back_menu")]]) });
});

// ══════════════════════════════════════════════════════════════════════════
// ─── CREATE GROUPS ────────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

bot.action("create_groups_start", async (ctx) => {
  await ctx.answerCbQuery();
  const uid = ctx.from.id;
  if (getStatus(accountId(uid)) !== "connected") { await ctx.answerCbQuery("⚠️ WhatsApp not connected!", { show_alert: true }); return; }
  updateSession(uid, { groupFlow: defaultGroupFlow() });
  await reply(ctx, `➕ *Create Groups — Step 1/9*\n━━━━━━━━━━━━━━━━━━━━\n\n*Group name?*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("❌ Cancel", "back_menu")]]) });
});

async function askNumbering(ctx) {
  const flow = getSession(ctx.from.id).groupFlow;
  await reply(ctx, `➕ *Create Groups — Step 3/9*\n━━━━━━━━━━━━━━━━━━━━\n\n*Add numbering?*\n\nYes → _${flow.name} 1, ${flow.name} 2..._\nNo  → All named _${flow.name}_`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("✅ Yes","gf_num_yes"),Markup.button.callback("❌ No","gf_num_no")],[Markup.button.callback("❌ Cancel","back_menu")]]) });
}
bot.action("gf_num_yes",async(ctx)=>{await ctx.answerCbQuery();const s=getSession(ctx.from.id);updateSession(ctx.from.id,{groupFlow:{...s.groupFlow,numbering:true,step:"description"}});await askDescription(ctx);});
bot.action("gf_num_no", async(ctx)=>{await ctx.answerCbQuery();const s=getSession(ctx.from.id);updateSession(ctx.from.id,{groupFlow:{...s.groupFlow,numbering:false,step:"description"}});await askDescription(ctx);});

async function askDescription(ctx) {
  await reply(ctx,`➕ *Create Groups — Step 4/9*\n━━━━━━━━━━━━━━━━━━━━\n\n*Group description:*\n_Skip to leave empty._`,
    {parse_mode:"Markdown",...Markup.inlineKeyboard([[Markup.button.callback("⏭ Skip","gf_desc_skip")],[Markup.button.callback("❌ Cancel","back_menu")]])});
}
bot.action("gf_desc_skip",async(ctx)=>{await ctx.answerCbQuery();const s=getSession(ctx.from.id);updateSession(ctx.from.id,{groupFlow:{...s.groupFlow,description:"",step:"photo"}});await askPhoto(ctx);});

async function askPhoto(ctx) {
  await reply(ctx,`➕ *Create Groups — Step 5/9*\n━━━━━━━━━━━━━━━━━━━━\n\n*Group photo:*\n_Skip for default._`,
    {parse_mode:"Markdown",...Markup.inlineKeyboard([[Markup.button.callback("⏭ Skip","gf_photo_skip")],[Markup.button.callback("❌ Cancel","back_menu")]])});
}
bot.action("gf_photo_skip",async(ctx)=>{await ctx.answerCbQuery();const s=getSession(ctx.from.id);updateSession(ctx.from.id,{groupFlow:{...s.groupFlow,photo:null,step:"disappearing"}});await askDisappearing(ctx);});

async function askDisappearing(ctx) {
  await reply(ctx,`➕ *Create Groups — Step 6/9*\n━━━━━━━━━━━━━━━━━━━━\n\n*Disappearing messages:*`,
    {parse_mode:"Markdown",...Markup.inlineKeyboard([[Markup.button.callback("24h","gf_dis_86400"),Markup.button.callback("7 Days","gf_dis_604800"),Markup.button.callback("90 Days","gf_dis_7776000")],[Markup.button.callback("⏭ Off","gf_dis_0")],[Markup.button.callback("❌ Cancel","back_menu")]])});
}
[0,86400,604800,7776000].forEach((s)=>{bot.action(`gf_dis_${s}`,async(ctx)=>{await ctx.answerCbQuery();const ss=getSession(ctx.from.id);updateSession(ctx.from.id,{groupFlow:{...ss.groupFlow,disappearing:s,step:"members"}});await askMembers(ctx);});});

async function askMembers(ctx) {
  await reply(ctx,`➕ *Create Groups — Step 7/9*\n━━━━━━━━━━━━━━━━━━━━\n\n*Add members? (one number per line)*`,
    {parse_mode:"Markdown",...Markup.inlineKeyboard([[Markup.button.callback("⏭ Skip","gf_mem_skip")],[Markup.button.callback("❌ Cancel","back_menu")]])});
}
bot.action("gf_mem_skip",async(ctx)=>{await ctx.answerCbQuery();const s=getSession(ctx.from.id);updateSession(ctx.from.id,{groupFlow:{...s.groupFlow,members:[],makeAdmin:false,step:"permissions"}});await askPermissions(ctx);});

async function askAdmin(ctx) {
  const flow=getSession(ctx.from.id).groupFlow;
  await reply(ctx,`➕ *Create Groups — Step 8/9*\n━━━━━━━━━━━━━━━━━━━━\n\n👥 *${flow.members.length} member(s)* added.\n\n*Make them admin?*`,
    {parse_mode:"Markdown",...Markup.inlineKeyboard([[Markup.button.callback("✅ Yes","gf_admin_yes"),Markup.button.callback("❌ No","gf_admin_no")],[Markup.button.callback("❌ Cancel","back_menu")]])}); 
}
bot.action("gf_admin_yes",async(ctx)=>{await ctx.answerCbQuery();const s=getSession(ctx.from.id);updateSession(ctx.from.id,{groupFlow:{...s.groupFlow,makeAdmin:true,step:"permissions"}});await askPermissions(ctx);});
bot.action("gf_admin_no", async(ctx)=>{await ctx.answerCbQuery();const s=getSession(ctx.from.id);updateSession(ctx.from.id,{groupFlow:{...s.groupFlow,makeAdmin:false,step:"permissions"}});await askPermissions(ctx);});

function permKb(p) {
  return Markup.inlineKeyboard([
    [Markup.button.callback(`💬 All Can Send   : ${p.sendMessages?"✅ ON":"❌ OFF"}`,   "gf_pt_sendMessages")],
    [Markup.button.callback(`✏️ All Can Edit   : ${p.editInfo?"✅ ON":"❌ OFF"}`,       "gf_pt_editInfo")],
    [Markup.button.callback(`➕ All Can Add    : ${p.addMembers?"✅ ON":"❌ OFF"}`,     "gf_pt_addMembers")],
    [Markup.button.callback(`🔐 Join Approval : ${p.approveMembers?"✅ ON":"❌ OFF"}`, "gf_pt_approveMembers")],
    [Markup.button.callback("💾 Save & Continue","gf_perm_save")],
    [Markup.button.callback("❌ Cancel","back_menu")],
  ]);
}
async function askPermissions(ctx) {
  const p=getSession(ctx.from.id).groupFlow.permissions;
  await reply(ctx,`➕ *Create Groups — Step 9/9*\n━━━━━━━━━━━━━━━━━━━━\n\n*Set permissions:*\n_Tap to toggle, then Save._`,
    {parse_mode:"Markdown",...permKb(p)});
}
["sendMessages","editInfo","addMembers","approveMembers"].forEach((key)=>{
  bot.action(`gf_pt_${key}`,async(ctx)=>{
    await ctx.answerCbQuery();
    const s=getSession(ctx.from.id),p={...s.groupFlow.permissions,[key]:!s.groupFlow.permissions[key]};
    updateSession(ctx.from.id,{groupFlow:{...s.groupFlow,permissions:p}});
    try{await ctx.editMessageReplyMarkup(permKb(p).reply_markup);}catch{await askPermissions(ctx);}
  });
});
bot.action("gf_perm_save",async(ctx)=>{await ctx.answerCbQuery();const s=getSession(ctx.from.id);updateSession(ctx.from.id,{groupFlow:{...s.groupFlow,step:"confirm"}});await showConfirm(ctx);});
function fmtDis(s){return !s?"Off":s===86400?"24h":s===604800?"7 Days":s===7776000?"90 Days":`${s}s`;}
async function showConfirm(ctx) {
  const flow=getSession(ctx.from.id).groupFlow,p=flow.permissions;
  const prev=flow.numbering
    ?Array.from({length:Math.min(flow.count,3)},(_,i)=>`${flow.name} ${i+1}`).join(", ")+(flow.count>3?` ...(${flow.count})`:"")
    :`${flow.name} ×${flow.count}`;
  await reply(ctx,
    `✅ *Review — Create Groups*\n━━━━━━━━━━━━━━━━━━━━\n`+
    `Name       : *${flow.name}*\nCount      : ${flow.count} groups\nNumbering  : ${flow.numbering?"Yes":"No"}\nPreview    : _${prev}_\n`+
    `Desc       : ${flow.description||"(none)"}\nPhoto      : ${flow.photo?"✅ Yes":"(none)"}\nDisappear  : ${fmtDis(flow.disappearing)}\n`+
    `Members    : ${flow.members.length} added\nMake Admin : ${flow.makeAdmin?"Yes":"No"}\n`+
    `━━━━━━━━━━━━━━━━━━━━\nPermissions:\n`+
    `💬 All Can Send  : ${p.sendMessages?"✅ ON":"❌ OFF"}\n`+
    `✏️ All Can Edit  : ${p.editInfo?"✅ ON":"❌ OFF"}\n`+
    `➕ All Can Add   : ${p.addMembers?"✅ ON":"❌ OFF"}\n`+
    `🔐 Join Approval : ${p.approveMembers?"✅ ON":"❌ OFF"}`,
    {parse_mode:"Markdown",...Markup.inlineKeyboard([[Markup.button.callback("🚀 Create Now","gf_create")],[Markup.button.callback("❌ Cancel","back_menu")]])});
}

bot.action("gf_create", async (ctx) => {
  await ctx.answerCbQuery("Creating...");
  const uid  = ctx.from.id;
  const aid  = accountId(uid);
  const flow = getSession(uid).groupFlow;
  const total = flow.count;
  updateSession(uid, { cancelPending: false });
  const pm = await startProgress(ctx, uid, `➕ Creating ${total} group(s)...\n${bar(0, total)}`);
  let done = 0, failed = 0, cancelled = false;
  const boxLines = [];
  for (let i = 0; i < total; i++) {
    if (isCancelled(uid)) { cancelled = true; break; }
    const name = flow.numbering ? `${flow.name} ${i + 1}` : flow.name;
    await editProgress(ctx.chat.id, pm.message_id, `➕ Creating groups...\nDone: ${done}/${total}  ❌ ${failed}\n→ ${name}\n${bar(i, total)}`);
    try {
      const memberJids = flow.members.map((n) => `${n.replace(/\D/g, "")}@s.whatsapp.net`);
      const g = await withRetry(() => createGroup(aid, name, memberJids));
      const gid = g.id || g.gid;
      if (flow.description) await updateGroupDescription(aid, gid, flow.description).catch(() => {});
      if (flow.photo)       await updateGroupPhoto(aid, gid, Buffer.from(flow.photo, "base64")).catch(() => {});
      if (flow.disappearing) await setDisappearingMessages(aid, gid, flow.disappearing).catch(() => {});
      await setGroupPermissions(aid, gid, flow.permissions).catch(() => {});
      if (flow.makeAdmin && memberJids.length) await promoteToAdmin(aid, gid, memberJids).catch(() => {});
      done++; boxLines.push(`✅ ${name}`);
    } catch (err) { failed++; boxLines.push(`❌ ${name}: ${err.message}`); }
    await sleep(D.createGroup);
  }
  await sendSummary(ctx, { feature: "create_groups", total, success: done, failed, cancelled, boxLines });
  updateSession(uid, { groupFlow: null });
});

// ══════════════════════════════════════════════════════════════════════════
// ─── MESSAGE HANDLER ──────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

bot.on("message", async (ctx) => {
  const uid  = ctx.from.id;
  const aid  = accountId(uid);
  const s    = getSession(uid);
  const msg  = ctx.message;
  const text = msg.text?.trim() || "";

  // ─── Admin flows ────────────────────────────────────────────────────────
  if (isAdmin(uid) && s.adminFlow) {
    const step = s.adminFlow.step;

    if (step === "add_prem_id") {
      const parts = text.split(/\s+/);
      const tid = parseInt(parts[0]), days = parseInt(parts[1]);
      if (isNaN(tid) || isNaN(days) || days < 1) { await ctx.reply("❌ Format: `userId days`", { parse_mode: "Markdown" }); return; }
      const exp = await addPremium(tid, days);
      if (!exp) { await ctx.reply(`❌ User ${tid} not found.`); updateSession(uid, { adminFlow: null }); return; }
      await ctx.reply(`✅ Premium added!\nUser: \`${tid}\`\nDays: ${days}\nExpires: ${exp.toLocaleDateString()}`, { parse_mode: "Markdown" });
      try { await bot.telegram.sendMessage(tid, `⭐ *Premium Activated!*\nYou have been given *${days} day(s)* of premium.\nExpires: ${exp.toLocaleDateString()}`, { parse_mode: "Markdown" }); } catch {}
      updateSession(uid, { adminFlow: null }); return;
    }

    if (step === "rem_prem_id") {
      const tid = parseInt(text);
      if (isNaN(tid)) { await ctx.reply("❌ Send a valid user ID."); return; }
      await removePremium(tid);
      await ctx.reply(`✅ Premium removed for \`${tid}\``, { parse_mode: "Markdown" });
      try { await bot.telegram.sendMessage(tid, `❌ *Your premium has been removed.*`, { parse_mode: "Markdown" }); } catch {}
      updateSession(uid, { adminFlow: null }); return;
    }

    if (step === "temp_prem_id") {
      const parts = text.split(/\s+/);
      const tid = parseInt(parts[0]), hrs = parseInt(parts[1]);
      if (isNaN(tid) || isNaN(hrs) || hrs < 1) { await ctx.reply("❌ Format: `userId hours`", { parse_mode: "Markdown" }); return; }
      const exp = await addTempPremium(tid, hrs);
      if (!exp) { await ctx.reply(`❌ User ${tid} not found.`); updateSession(uid, { adminFlow: null }); return; }
      await ctx.reply(`✅ Temp premium added!\nUser: \`${tid}\`\nHours: ${hrs}\nExpires: ${exp.toLocaleString()}`, { parse_mode: "Markdown" });
      try { await bot.telegram.sendMessage(tid, `⏱ *Temporary Premium!*\nYou have *${hrs} hour(s)* of premium.\nExpires: ${exp.toLocaleString()}`, { parse_mode: "Markdown" }); } catch {}
      updateSession(uid, { adminFlow: null }); return;
    }

    if (step === "ban_id") {
      const parts = text.split(/\s+/);
      const tid = parseInt(parts[0]);
      const reason = parts.slice(1).join(" ") || "";
      if (isNaN(tid)) { await ctx.reply("❌ Send a valid user ID."); return; }
      await banUser(tid, reason);
      await ctx.reply(`✅ User \`${tid}\` banned.${reason ? `\nReason: ${reason}` : ""}`, { parse_mode: "Markdown" });
      try { await bot.telegram.sendMessage(tid, `⛔ *You have been banned.*${reason ? `\nReason: ${reason}` : ""}`, { parse_mode: "Markdown" }); } catch {}
      updateSession(uid, { adminFlow: null }); return;
    }

    if (step === "unban_id") {
      const tid = parseInt(text);
      if (isNaN(tid)) { await ctx.reply("❌ Send a valid user ID."); return; }
      await unbanUser(tid);
      await ctx.reply(`✅ User \`${tid}\` unbanned.`, { parse_mode: "Markdown" });
      try { await bot.telegram.sendMessage(tid, `✅ *You have been unbanned. Welcome back!*`, { parse_mode: "Markdown" }); } catch {}
      updateSession(uid, { adminFlow: null }); return;
    }
  }

  // ─── Phone number for WhatsApp pairing ────────────────────────────────
  if (s.awaitingPhoneForIndex === 0 && /^\d{7,15}$/.test(text)) {
    updateSession(uid, { awaitingPhoneForIndex: null });
    const pairingMsg = await ctx.reply(`⏳ *Connecting WhatsApp...*\nGenerating pairing code for +${text}`, { parse_mode: "Markdown" });
    const codePromise = new Promise((resolve) => pendingPairingCbs.set(aid, resolve));
    const readyPromise = new Promise((resolve) => pendingReadyCbs.set(aid, resolve));
    try {
      await connectAccount(aid, text, true);
      const code = await Promise.race([codePromise, new Promise((_, r) => setTimeout(() => r(new Error("timeout")), 60000))]);
      if (!code) throw new Error("Failed to generate pairing code");
      await bot.telegram.editMessageText(ctx.chat.id, pairingMsg.message_id, undefined,
        `📱 *Pairing Code:*\n\n\`${code}\`\n\n_Enter this code in WhatsApp → Linked Devices → Link a Device_\n\n⚠️ Expires in 60 seconds!`,
        { parse_mode: "Markdown" }
      );
      await readyPromise;
      await markWaConnected(uid, getPhone(aid));
      await ctx.reply(`✅ *WhatsApp Connected!*\n📞 +${getPhone(aid)}\n⏰ Auto-logout in 6 hours.`, { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) });
    } catch (err) {
      await ctx.reply(`❌ Connection failed: ${err.message}\nPlease try again.`, Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]));
    }
    return;
  }

  // ─── Feature flows ─────────────────────────────────────────────────────
  const flow = s.featureFlow;

  if (flow?.step === "similar_query") {
    const kw = text.toLowerCase();
    const all = flow.allGroups || [];
    const matching = all.filter((g) => g.name.toLowerCase().includes(kw));
    if (!matching.length) { await ctx.reply(`❌ No groups matching "${text}"`); return; }
    updateSession(uid, { featureFlow: { ...flow, selectedIds: matching.map((g) => g.id), keyword: kw, step: "confirm" } });
    await ctx.reply(
      `✅ *"${text}" — ${matching.length} group(s):*\n━━━━━━━━━━━━━━━━━━━━\n${matching.slice(0, 20).map((g, i) => `${i + 1}. ${g.name}`).join("\n")}${matching.length > 20 ? `\n_...and ${matching.length - 20} more_` : ""}`,
      { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🚀 Proceed", "gs_sim_proceed")], [Markup.button.callback("🔙 Back", `gs_similar_${flow.feature}`)], [Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
    );
    return;
  }

  if (flow?.step === "admin_numbers" || flow?.step === "demote_numbers") {
    const nums = text.split(/[\n,\s]+/).map((n) => n.replace(/\D/g, "")).filter((n) => n.length >= 7);
    if (!nums.length) { await ctx.reply("❌ No valid numbers found. Send one per line with country code."); return; }
    await runFeature(ctx, flow.feature, flow.selectedIds, flow.allGroups, nums);
    return;
  }

  if (flow?.step === "aa_custom_duration") {
    const mins = parseInt(text);
    if (isNaN(mins) || mins < 1) { await ctx.reply("❌ Send a valid number of minutes."); return; }
    const secs = mins * 60;
    updateSession(uid, { featureFlow: { ...flow, aaDuration: secs, step: "aa_confirm" } });
    const label = mins >= 60 ? `${mins / 60}h` : `${mins}min`;
    await ctx.reply(
      `⏰ *Auto Accept — Confirm*\n━━━━━━━━━━━━━━━━━━━━\nGroups   : *${flow.selectedIds.length}*\nDuration : *${label}*`,
      { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("▶️ Start", "aa_start")], [Markup.button.callback("🔙 Change Duration", "aa_back_duration")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
    );
    return;
  }

  if (flow?.step === "cn_random_name") {
    const baseName = text;
    updateSession(uid, { featureFlow: { ...flow, cnBaseName: baseName, step: "cn_ask_numbering" } });
    await ctx.reply(`✏️ *Name set: "${baseName}"*\n\nAdd numbering?`,
      { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("✅ Yes","cn_numbering_yes"), Markup.button.callback("❌ No","cn_numbering_no")], [Markup.button.callback("🏠 Main Menu","back_menu")]]) });
    return;
  }

  if (flow?.step === "cn_random_links") {
    const codes = extractCodes(text);
    if (!codes.length) { await ctx.reply("❌ No valid WhatsApp group links found."); return; }
    await runChangeNameRandom(ctx, codes, flow.cnBaseName, flow.numbering);
    return;
  }

  // ─── CTC Checker links step ────────────────────────────────────────────
  if (flow?.step === "ctc_links") {
    const codes = extractCodes(text);
    if (!codes.length) { await ctx.reply("❌ No valid links found."); return; }
    updateSession(uid, { featureFlow: { ...flow, links: codes, step: "ctc_vcf_collecting" }, awaitingVcf: { feature: "ctc_checker" } });
    await ctx.reply(
      `✅ *${codes.length} group link(s) saved!*\n\n*Step 2:* Send VCF files now (one per group, same order).\n\nWhen done, tap Start Check:`,
      { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("▶️ Start Check", "ctc_start_check")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
    );
    return;
  }

  // ─── Add Members links step ────────────────────────────────────────────
  if (flow?.step === "am_links") {
    const codes = extractCodes(text);
    if (!codes.length) { await ctx.reply("❌ No valid links found."); return; }
    updateSession(uid, { featureFlow: { ...flow, links: codes, step: "am_vcf" }, awaitingVcf: { feature: "add_members" } });
    await ctx.reply(`✅ *${codes.length} group link(s) saved!*\n\nNow send the VCF file with members to add:`,
      { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) });
    return;
  }

  // ─── Group flow: name + count ──────────────────────────────────────────
  const gf = s.groupFlow;
  if (gf) {
    if (gf.step === "name") {
      updateSession(uid, { groupFlow: { ...gf, name: text, step: "count" } });
      await ctx.reply(`➕ *Create Groups — Step 2/9*\n━━━━━━━━━━━━━━━━━━━━\n\n*How many groups?*\n_Enter a number (1-100):_`,
        { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("❌ Cancel","back_menu")]]) }); return;
    }
    if (gf.step === "count") {
      const n = parseInt(text);
      if (isNaN(n) || n < 1 || n > 100) { await ctx.reply("❌ Enter 1-100."); return; }
      updateSession(uid, { groupFlow: { ...gf, count: n, step: "numbering" } });
      await askNumbering(ctx); return;
    }
    if (gf.step === "description") {
      updateSession(uid, { groupFlow: { ...gf, description: text, step: "photo" } });
      await askPhoto(ctx); return;
    }
    if (gf.step === "members" && msg.text) {
      const nums = text.split(/[\n,\s]+/).map((n) => n.replace(/\D/g, "")).filter((n) => n.length >= 7);
      updateSession(uid, { groupFlow: { ...gf, members: nums, step: "make_admin" } });
      await askAdmin(ctx); return;
    }
    if (gf.step === "photo" && msg.photo) {
      const fileId = msg.photo[msg.photo.length - 1].file_id;
      try {
        const buf = await downloadFile(ctx, fileId);
        updateSession(uid, { groupFlow: { ...gf, photo: buf.toString("base64"), step: "disappearing" } });
        await ctx.reply("✅ Photo saved!"); await askDisappearing(ctx);
      } catch { await ctx.reply("❌ Failed to download photo."); }
      return;
    }
  }

  // ─── Join Groups ────────────────────────────────────────────────────────
  const jf = s.joinFlow;
  if (jf?.step === "links") {
    const codes = extractCodes(text);
    if (!codes.length) { await ctx.reply("❌ No valid links found."); return; }
    updateSession(uid, { joinFlow: null, cancelPending: false });
    const total = codes.length;
    const pm = await startProgress(ctx, uid, `🔗 Joining ${total} group(s)...\n${bar(0, total)}`);
    let done = 0, failed = 0, cancelled = false;
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      await editProgress(ctx.chat.id, pm.message_id, `🔗 Joining...\nDone: ${done}/${total}  ❌ ${failed}\n→ ${codes[i]}\n${bar(i, total)}`);
      try { await withRetry(() => joinGroupViaLink(aid, codes[i])); done++; }
      catch { failed++; }
      await sleep(D.joinGroup);
    }
    await sendSummary(ctx, { feature: "join_groups", total, success: done, failed, cancelled,
      extra: [`Total Links  : ${total}`, `Joined       : ${done}`, `Failed       : ${failed}`] });
    return;
  }

  // ─── VCF file handler ──────────────────────────────────────────────────
  if (msg.document && (msg.document.mime_type === "text/vcard" || msg.document.file_name?.endsWith(".vcf"))) {
    const vcfAwaiting = s.awaitingVcf;
    if (!vcfAwaiting) return;
    try {
      const buf  = await downloadFile(ctx, msg.document.file_id);
      const name = msg.document.file_name?.replace(/\.vcf$/i, "") || "VCF";
      const contacts = parseVcf(buf.toString("utf8"));

      if (vcfAwaiting.feature === "change_name") {
        const currentFlow = getSession(uid).featureFlow;
        const vcfList = [...(currentFlow.vcfList || []), { name, contacts }];
        updateSession(uid, { featureFlow: { ...currentFlow, vcfList } });
        await showVcfCollectStatus(ctx, vcfList);
        return;
      }

      if (vcfAwaiting.feature === "ctc_checker") {
        const currentFlow = getSession(uid).featureFlow;
        const vcfList = [...(currentFlow.vcfList || []), { name, contacts }];
        updateSession(uid, { featureFlow: { ...currentFlow, vcfList } });
        await ctx.reply(`📎 VCF ${vcfList.length} saved: *${name}* (${contacts.length} contacts)\n\nSend more or tap Start Check:`,
          { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("▶️ Start Check", "ctc_start_check")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]) });
        return;
      }

      if (vcfAwaiting.feature === "add_members") {
        const currentFlow = getSession(uid).featureFlow;
        if (!currentFlow?.links?.length) { await ctx.reply("❌ No group links saved. Start over."); return; }
        updateSession(uid, { awaitingVcf: null });
        const phones = contacts.map((c) => c.phone).filter(Boolean);
        if (!phones.length) { await ctx.reply("❌ No valid phone numbers in VCF."); return; }
        const total = currentFlow.links.length;
        const pm = await startProgress(ctx, uid, `➕ Adding members — ${total} group(s)...\n${bar(0, total)}`);
        let done = 0, failed = 0, totalAdded = 0, cancelled = false;
        const boxLines = [];
        for (let i = 0; i < total; i++) {
          if (isCancelled(uid)) { cancelled = true; break; }
          const code = currentFlow.links[i];
          await editProgress(ctx.chat.id, pm.message_id, `➕ Adding members...\nDone: ${done}/${total}  ❌ ${failed}\n→ Group ${i + 1}\n${bar(i, total)}`);
          try {
            const info = await withRetry(() => getGroupInfoFromLink(aid, code));
            if (!info) throw new Error("Invalid link");
            const result = await withRetry(() => addMembersToGroup(aid, info.id, phones, currentFlow.addMode === "one_by_one"));
            totalAdded += result.added; done++;
            boxLines.push(`${info.name}: ${result.added} added, ${result.skipped} skipped, ${result.failed} failed`);
          } catch (err) { failed++; boxLines.push(`❌ Group ${i + 1}: ${err.message}`); }
          await sleep(D.addMembers);
        }
        await sendSummary(ctx, { feature: "add_members", total, success: done, failed, cancelled,
          extra: [`Total Groups  : ${total}`, `Total Contacts: ${phones.length}`, `Total Added   : ${totalAdded}`], boxLines });
        updateSession(uid, { featureFlow: null });
        return;
      }
    } catch (err) { await ctx.reply(`❌ Failed to process VCF: ${err.message}`); }
    return;
  }

  // ─── Photo during group creation ──────────────────────────────────────
  if (msg.photo && gf?.step === "photo") {
    const fileId = msg.photo[msg.photo.length - 1].file_id;
    try {
      const buf = await downloadFile(ctx, fileId);
      updateSession(uid, { groupFlow: { ...gf, photo: buf.toString("base64"), step: "disappearing" } });
      await ctx.reply("✅ Photo saved!"); await askDisappearing(ctx);
    } catch { await ctx.reply("❌ Failed to download photo."); }
    return;
  }
});

// ─── CTC Runner ────────────────────────────────────────────────────────────
async function runCtcChecker(ctx) {
  const uid  = ctx.from.id;
  const aid  = accountId(uid);
  const flow = getSession(uid).featureFlow;
  const links   = flow.links || [];
  const vcfList = flow.vcfList || [];
  const total   = Math.min(links.length, vcfList.length);
  if (!total) { await ctx.reply("❌ No groups/VCFs to check."); return; }
  updateSession(uid, { cancelPending: false });
  const pm = await startProgress(ctx, uid, `🔍 CTC Check — ${total} group(s)...\n${bar(0, total)}`);
  let done = 0, failed = 0, cancelled = false;
  const boxLines = [];
  for (let i = 0; i < total; i++) {
    if (isCancelled(uid)) { cancelled = true; break; }
    const code = links[i], vcf = vcfList[i];
    await editProgress(ctx.chat.id, pm.message_id, `🔍 Checking...\nDone: ${done}/${total}  ❌ ${failed}\n→ ${vcf.name}\n${bar(i, total)}`);
    try {
      const info = await withRetry(() => getGroupInfoFromLink(aid, code));
      if (!info) throw new Error("Invalid link");
      const members = await withRetry(() => getGroupMembers(aid, info.id));
      const { list: pending } = await withRetry(() => getGroupPendingRequests(aid, info.id));
      const allNumbers = new Set([...members, ...pending].map((m) => m.number || m.phone).filter(Boolean));
      const vcfPhones  = new Set(vcf.contacts.map((c) => c.phone.replace(/\D/g, "")).filter(Boolean));
      let inGroup = 0, notInGroup = 0;
      const notInList = [];
      for (const phone of vcfPhones) {
        const found = [...allNumbers].some((n) => numberMatchesLocal(n, phone));
        if (found) inGroup++; else { notInGroup++; notInList.push(phone); }
      }
      done++;
      boxLines.push(`${vcf.name} vs ${info.name}\n  In: ${inGroup} | Out: ${notInGroup}`);
      if (notInList.length && notInList.length <= 10) boxLines.push(`  Missing: ${notInList.join(", ")}`);
    } catch (err) { failed++; boxLines.push(`❌ ${vcf.name}: ${err.message}`); }
    await sleep(D.ctcCheck);
  }
  await sendSummary(ctx, { feature: "ctc_checker", total, success: done, failed, cancelled,
    extra: [`Total Checked: ${total}`], boxLines });
  updateSession(uid, { featureFlow: null, awaitingVcf: null });
}

// ══════════════════════════════════════════════════════════════════════════
// ─── EXPRESS SERVER (Health + Keep-Alive) ─────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

const app = express();
app.get("/health", (_, res) => res.json({ status: "ok", uptime: process.uptime() }));
app.get("/",       (_, res) => res.send("WS Automation Bot is running."));

// ══════════════════════════════════════════════════════════════════════════
// ─── STARTUP ──────────────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

async function main() {
  await connectDB();

  // Restore active WA sessions from DB
  const { User: UserModel } = require("./src/models");
  const activeUsers = await UserModel.find({ waConnected: true }).lean();
  for (const u of activeUsers) {
    if (u.waPhone && u.waAccountId) {
      await reconnectSavedAccount(u.waAccountId, u.waPhone).catch(() => {});
      await sleep(500);
    }
  }

  const PORT = parseInt(process.env.PORT || "3000", 10);
  app.listen(PORT, () => console.log(`✅ Server running on port ${PORT}`));

  await bot.launch();
  console.log("✅ Bot started!");

  process.once("SIGINT",  () => bot.stop("SIGINT"));
  process.once("SIGTERM", () => bot.stop("SIGTERM"));
}

main().catch((err) => { console.error("Startup error:", err); process.exit(1); });
