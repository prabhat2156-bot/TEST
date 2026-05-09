/**
 * WhatsApp Group Creator Bot — Extended Version
 * Features: Create | Join | Get Links | Leave | Remove Members |
 *           Make Admin | Approval Toggle | Approve Pending | Member List |
 *           Add Members | Edit Settings | Change Name | Reset Link |
 *           Demote Admin | Auto Accept | CTC Checker
 */

const { Telegraf, Markup } = require("telegraf");
const { connectDB }        = require("./src/db");
const { getSession, updateSession, resetSession, defaultGroupFlow, defaultFeatureFlow } = require("./src/session");
const {
  setCallbacks, getStatus, getPhone, getConnectedCount,
  connectAccount, disconnectAccount, reconnectSavedAccounts,
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
  getPendingForGroup,
  startAutoAcceptForGroups, stopAutoAcceptForGroups, getAutoAcceptStats,
} = require("./src/whatsapp-manager");
const express = require("express");
const http    = require("http");
const https   = require("https");

const TOKEN    = process.env.TELEGRAM_BOT_TOKEN;
if (!TOKEN) { console.error("TELEGRAM_BOT_TOKEN not set!"); process.exit(1); }
const OWNER_ID = parseInt(process.env.OWNER_ID || "0", 10);

const bot        = new Telegraf(TOKEN);
const sleep      = (ms) => new Promise((r) => setTimeout(r, ms));
const PAGE_SIZE  = 10;
const startTimes = new Map();

// ─── Owner guard ───────────────────────────────────────────────────────────
bot.use(async (ctx, next) => {
  if (OWNER_ID && ctx.from?.id !== OWNER_ID) {
    if (ctx.callbackQuery) await ctx.answerCbQuery("⛔ Unauthorized.", { show_alert: true }).catch(() => {});
    else await ctx.reply("⛔ This bot is for the owner only.").catch(() => {});
    return;
  }
  return next();
});

// ─── Pairing callbacks ─────────────────────────────────────────────────────
const pendingPairingCbs = new Map();
const pendingReadyCbs   = new Map();

setCallbacks({
  onPairingCode: async (i, code) => { const cb = pendingPairingCbs.get(i); if (cb) { pendingPairingCbs.delete(i); await cb(code); } },
  onReady:       async (i) => { const cb = pendingReadyCbs.get(i); if (cb) { pendingReadyCbs.delete(i); await cb(); } },
  onDisconnected: async () => { console.log("[Bot] WhatsApp disconnected"); },
});

// ─── Helpers ───────────────────────────────────────────────────────────────
async function sendClean(ctx, text, extra = {}) {
  const uid = ctx.from?.id;
  if (uid) {
    const { lastMsgId } = getSession(uid);
    if (lastMsgId) { try { await ctx.telegram.deleteMessage(ctx.chat.id, lastMsgId); } catch {} }
  }
  const msg = await ctx.reply(text, extra);
  if (uid) updateSession(uid, { lastMsgId: msg.message_id });
  return msg;
}

async function editOrSend(ctx, text, extra = {}) {
  try { await ctx.editMessageText(text, extra); }
  catch { await sendClean(ctx, text, extra); }
}

function bar(done, total) {
  const p = total > 0 ? Math.round((done / total) * 10) : 0;
  return `[${"█".repeat(p)}${"░".repeat(10 - p)}] ${total > 0 ? Math.round((done / total) * 100) : 0}%`;
}

function elapsed(uid) {
  const t = startTimes.get(uid);
  return t ? Math.round((Date.now() - t) / 1000) : 0;
}

async function showCancelBtn(ctx) {
  const uid = ctx.from?.id;
  try {
    const m = await ctx.reply("🛑 *Running... tap below to cancel anytime.*", {
      parse_mode: "Markdown",
      ...Markup.inlineKeyboard([[Markup.button.callback("🛑 Cancel Operation", "cancel_exec")]]),
    });
    if (uid) updateSession(uid, { cancelMsgId: m.message_id });
  } catch {}
}

async function removeCancelBtn(ctx) {
  const uid = ctx.from?.id; if (!uid) return;
  const { cancelMsgId } = getSession(uid);
  if (cancelMsgId) {
    try { await ctx.telegram.deleteMessage(ctx.chat.id, cancelMsgId); } catch {}
    updateSession(uid, { cancelMsgId: null });
  }
}

bot.action("cancel_exec", async (ctx) => {
  await ctx.answerCbQuery("🛑 Cancelling...", { show_alert: true });
  updateSession(ctx.from.id, { cancelPending: true });
  try { await ctx.editMessageText("🛑 *Cancellation requested — stopping after current item...*", { parse_mode: "Markdown" }); } catch {}
});

function isCancelled(uid) { return getSession(uid).cancelPending === true; }

// ─── VCF Parser ───────────────────────────────────────────────────────────
function parseVcf(content) {
  const contacts = [];
  const blocks = content.split(/(?=BEGIN:VCARD)/gi);
  for (const block of blocks) {
    if (!block.toUpperCase().includes("BEGIN:VCARD")) continue;
    const nameMatch = block.match(/^FN:(.+)$/m) || block.match(/^N:([^;\r\n]+)/m);
    const name = nameMatch ? nameMatch[1].trim().replace(/\\/g, "") : "";
    const telMatches = [...block.matchAll(/^TEL[^:]*:([^\r\n]+)/gim)];
    for (const m of telMatches) {
      const raw = m[1].trim().replace(/[\s()\-]/g, "").replace(/^\+/, "");
      const digits = raw.replace(/[^0-9]/g, "");
      if (digits.length >= 10) contacts.push({ name, phone: digits });
    }
  }
  return contacts;
}

function extractCodes(text) {
  return text.split(/\s+|\n/)
    .map((l) => { const m = l.match(/chat\.whatsapp\.com\/([A-Za-z0-9_-]+)/); return m ? m[1] : null; })
    .filter(Boolean);
}

// ─── Download file as buffer ──────────────────────────────────────────────
async function downloadFile(ctx, fileId) {
  const u = await ctx.telegram.getFileLink(fileId);
  const r = await fetch(u.href);
  return Buffer.from(await r.arrayBuffer());
}

// ─── Summary Report ────────────────────────────────────────────────────────
const FEAT_LABEL = {
  get_links:       "🔗 Get Links",
  leave:           "🚪 Leave Groups",
  remove_members:  "👥 Remove Members",
  make_admin:      "👑 Make Admin",
  approval:        "✅ Approval Toggle",
  approve_pending: "✋ Approve Pending Members",
  member_list:     "📊 Member List",
  pending_list:    "⏳ Pending Requests",
  join_groups:     "🔗 Join Groups",
  create_groups:   "📋 Create Groups",
  add_members:     "➕ Add Members",
  edit_settings:   "⚙️ Edit Settings",
  change_name:     "✏️ Change Name",
  reset_link:      "🔄 Reset Link",
  demote_admin:    "👤 Demote Admin",
  auto_accept:     "⏰ Auto Accept",
  ctc_checker:     "🔍 CTC Checker",
};

async function sendSummary(ctx, opts) {
  const { feature, total, success, failed, cancelled, extra = [] } = opts;
  const uid  = ctx.from?.id;
  const secs = uid ? elapsed(uid) : 0;
  if (uid) startTimes.delete(uid);

  let text =
    `📊 *Execution Summary*\n` +
    `━━━━━━━━━━━━━━━━━━\n` +
    `🔧 Feature: ${FEAT_LABEL[feature] || feature}\n` +
    `📁 Total: *${total}*\n` +
    `✅ Success: *${success}*\n` +
    `❌ Failed: *${failed}*\n` +
    `⏱ Time: *${secs}s*\n` +
    `🚫 Cancelled: *${cancelled ? "Yes (stopped early)" : "No"}*\n`;

  if (extra.length) text += `━━━━━━━━━━━━━━━━━━\n` + extra.join("\n");
  text += `\n━━━━━━━━━━━━━━━━━━`;

  await ctx.reply(text, { parse_mode: "Markdown" });
}

// ─── Main Menu ─────────────────────────────────────────────────────────────
function buildMainMenu() {
  const c = getStatus(0) === "connected", p = getPhone(0);
  const b = (label, cb) => Markup.button.callback(label, c ? cb : "need_connect");
  return Markup.inlineKeyboard([
    [Markup.button.callback(c ? `📱 WhatsApp: ✅ +${p}` : `📱 WhatsApp: ❌ Not Connected`, "menu_account")],
    [b("📋 Create Groups", "create_groups_start"), b("🔗 Join Groups",    "join_groups_start")],
    [b("🔗 Get Links",      "feat_getlinks"),       b("🚪 Leave Groups",   "feat_leave")],
    [b("👥 Remove Members", "feat_removemem"),      b("👑 Make Admin",     "feat_makeadmin")],
    [b("✅ Approval Toggle","feat_approval"),        b("✋ Approve Pending","feat_approvepending")],
    [b("📊 Member List",    "feat_memberlist"),      b("➕ Add Members",    "feat_addmembers")],
    [b("⚙️ Edit Settings",  "feat_editsettings"),   b("✏️ Change Name",    "feat_changename")],
    [b("🔄 Reset Link",     "feat_resetlink"),       b("👤 Demote Admin",   "feat_demoteadmin")],
    [b("⏰ Auto Accept",    "feat_autoaccept"),      b("🔍 CTC Checker",    "feat_ctcchecker")],
    [Markup.button.callback("📊 Status", "menu_status")],
  ]);
}

async function sendMainMenu(ctx) {
  const user = ctx.from, c = getStatus(0) === "connected", p = getPhone(0);
  const uid  = user?.id;
  if (uid) {
    updateSession(uid, { cancelPending: false });
    const { lastMsgId, cancelMsgId } = getSession(uid);
    if (cancelMsgId) { try { await ctx.telegram.deleteMessage(ctx.chat.id, cancelMsgId); } catch {} updateSession(uid, { cancelMsgId: null }); }
    if (lastMsgId)   { try { await ctx.telegram.deleteMessage(ctx.chat.id, lastMsgId);   } catch {} }
  }
  const text =
    `╔══════════════════════╗\n║  🤖 *WA Group Creator Bot*  ║\n╚══════════════════════╝\n\n` +
    `👤 *User:* ${user.first_name}${user.last_name ? " " + user.last_name : ""}\n` +
    `🆔 *ID:* \`${user.id}\`\n` +
    (c ? `📱 *WhatsApp:* ✅ Connected — \`+${p}\`` : `📱 *WhatsApp:* ❌ Not Connected`) +
    `\n\nSelect a feature:`;
  const msg = await ctx.reply(text, { parse_mode: "Markdown", ...buildMainMenu() });
  if (uid) updateSession(uid, { lastMsgId: msg.message_id });
}

bot.start(async (ctx) => { resetSession(ctx.from.id); await sendMainMenu(ctx); });
bot.command("menu", async (ctx) => {
  updateSession(ctx.from.id, { awaitingPhoneForIndex: null, groupFlow: null, joinFlow: null, featureFlow: null, cancelPending: false, awaitingVcf: null });
  await sendMainMenu(ctx);
});
bot.action("need_connect", async (ctx) => { await ctx.answerCbQuery("⚠️ Connect WhatsApp first!", { show_alert: true }); });
bot.action("back_menu", async (ctx) => {
  await ctx.answerCbQuery();
  updateSession(ctx.from.id, { awaitingPhoneForIndex: null, groupFlow: null, joinFlow: null, featureFlow: null, cancelPending: false, awaitingVcf: null });
  await sendMainMenu(ctx);
});

// ─── Status ────────────────────────────────────────────────────────────────
bot.action("menu_status", async (ctx) => {
  await ctx.answerCbQuery();
  const s = getStatus(0), p = getPhone(0);
  const icon = s === "connected" ? "✅" : s === "connecting" ? "⏳" : "❌";
  await editOrSend(ctx,
    `📊 *Bot Status*\n━━━━━━━━━━━━━━━━━━\n${icon} WhatsApp: ${s === "connected" ? `Connected\n📞 \`+${p}\`` : s}\n━━━━━━━━━━━━━━━━━━`,
    { parse_mode: "Markdown", reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]).reply_markup }
  );
});

// ─── Account ───────────────────────────────────────────────────────────────
bot.action("menu_account", async (ctx) => {
  await ctx.answerCbQuery();
  const status = getStatus(0), phone = getPhone(0);
  if (status === "connected") {
    await editOrSend(ctx,
      `📱 *WhatsApp Account*\n━━━━━━━━━━━━━━━━━━\n✅ Status: Connected\n📞 Number: \`+${phone}\`\n━━━━━━━━━━━━━━━━━━\n\nDo you want to logout?`,
      { parse_mode: "Markdown", reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🔌 Logout", "logout_wa")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]).reply_markup }
    );
  } else if (status === "connecting") {
    await editOrSend(ctx,
      `📱 *WhatsApp Account*\n━━━━━━━━━━━━━━━━━━\n⏳ Connecting...\n━━━━━━━━━━━━━━━━━━`,
      { parse_mode: "Markdown", reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🔄 Reset", "reset_wa")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]).reply_markup }
    );
  } else {
    updateSession(ctx.from.id, { awaitingPhoneForIndex: 0 });
    await editOrSend(ctx,
      `📱 *Connect WhatsApp*\n━━━━━━━━━━━━━━━━━━\n\nEnter phone number with country code:\n\n*Example:* \`919876543210\`\n\n⚠️ Pairing code expires in *60 seconds!*`,
      { parse_mode: "Markdown", reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]).reply_markup }
    );
  }
});
bot.action("logout_wa", async (ctx) => {
  await ctx.answerCbQuery("Logging out...");
  await editOrSend(ctx, `⏳ *Logging out...*`, { parse_mode: "Markdown" });
  await disconnectAccount(0); await sleep(800); await sendMainMenu(ctx);
});
bot.action("reset_wa", async (ctx) => {
  await ctx.answerCbQuery("Resetting...");
  await disconnectAccount(0);
  updateSession(ctx.from.id, { awaitingPhoneForIndex: 0 });
  await editOrSend(ctx,
    `📱 *Connect WhatsApp*\n━━━━━━━━━━━━━━━━━━\n\nEnter phone number:\n*Example:* \`919876543210\``,
    { parse_mode: "Markdown", reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]).reply_markup }
  );
});

// ══════════════════════════════════════════════════════════════════════════
// ─── GROUP SELECTION SYSTEM ───────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

async function showGroupTypeSelect(ctx, feature) {
  const label = FEAT_LABEL[feature] || feature;
  await sendClean(ctx,
    `${label}\n━━━━━━━━━━━━━━━━━━\n\n*Which groups do you want to use?*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("🔍 Similar Groups", `gs_similar_${feature}`)],
      [Markup.button.callback("📋 All Groups",      `gs_all_${feature}`)],
      [Markup.button.callback("☑️ Select Groups",   `gs_select_${feature}`)],
      [Markup.button.callback("🏠 Main Menu", "back_menu")],
    ]) }
  );
}

// ─── Feature entry points ─────────────────────────────────────────────────
const FEAT_MAP = {
  getlinks: "get_links", leave: "leave", removemem: "remove_members",
  makeadmin: "make_admin", approval: "approval", approvepending: "approve_pending",
  editsettings: "edit_settings", resetlink: "reset_link", demoteadmin: "demote_admin",
  autoaccept: "auto_accept",
};

Object.keys(FEAT_MAP).forEach((key) => {
  bot.action(`feat_${key}`, async (ctx) => {
    await ctx.answerCbQuery();
    if (getStatus(0) !== "connected") { await ctx.answerCbQuery("⚠️ Connect WhatsApp first!", { show_alert: true }); return; }
    const feature = FEAT_MAP[key];
    updateSession(ctx.from.id, { featureFlow: defaultFeatureFlow(feature), cancelPending: false });
    await showGroupTypeSelect(ctx, feature);
  });
});

// Member List — sub-menu
bot.action("feat_memberlist", async (ctx) => {
  await ctx.answerCbQuery();
  if (getStatus(0) !== "connected") { await ctx.answerCbQuery("⚠️ Connect WhatsApp first!", { show_alert: true }); return; }
  updateSession(ctx.from.id, { featureFlow: defaultFeatureFlow("member_list"), cancelPending: false });
  await sendClean(ctx,
    `📊 *Member List*\n━━━━━━━━━━━━━━━━━━\n\n*What do you want to see?*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("👥 Get Members List",  "ml_sub_members")],
      [Markup.button.callback("⏳ Pending Requests",  "ml_sub_pending")],
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

// Add Members — direct flow (no group selection, uses links)
bot.action("feat_addmembers", async (ctx) => {
  await ctx.answerCbQuery();
  if (getStatus(0) !== "connected") { await ctx.answerCbQuery("⚠️ Connect WhatsApp first!", { show_alert: true }); return; }
  updateSession(ctx.from.id, {
    featureFlow: { ...defaultFeatureFlow("add_members"), step: "am_links", links: [], vcfs: [], currentVcfIdx: 0, addMode: "bulk" },
    cancelPending: false,
  });
  await sendClean(ctx,
    `➕ *Add Members*\n━━━━━━━━━━━━━━━━━━\n\n*Send group invite links — one per line:*\n\`\`\`\nhttps://chat.whatsapp.com/ABC\nhttps://chat.whatsapp.com/DEF\n\`\`\`\n\n_Each link will be paired with a VCF file._`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
  );
});

// Change Name — direct flow
bot.action("feat_changename", async (ctx) => {
  await ctx.answerCbQuery();
  if (getStatus(0) !== "connected") { await ctx.answerCbQuery("⚠️ Connect WhatsApp first!", { show_alert: true }); return; }
  updateSession(ctx.from.id, {
    featureFlow: { ...defaultFeatureFlow("change_name"), step: "cn_mode" },
    cancelPending: false,
  });
  await sendClean(ctx,
    `✏️ *Change Name*\n━━━━━━━━━━━━━━━━━━\n\n*Choose naming method:*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("🔀 Name Randomly",  "cn_random")],
      [Markup.button.callback("📛 Name as VCF",    "cn_vcf")],
      [Markup.button.callback("🏠 Main Menu", "back_menu")],
    ]) }
  );
});

// CTC Checker — direct flow
bot.action("feat_ctcchecker", async (ctx) => {
  await ctx.answerCbQuery();
  if (getStatus(0) !== "connected") { await ctx.answerCbQuery("⚠️ Connect WhatsApp first!", { show_alert: true }); return; }
  updateSession(ctx.from.id, {
    featureFlow: { ...defaultFeatureFlow("ctc_checker"), step: "ctc_links" },
    cancelPending: false,
  });
  await sendClean(ctx,
    `🔍 *CTC Checker*\n━━━━━━━━━━━━━━━━━━\n\nSend *all group links* — one per line:\n\`\`\`\nhttps://chat.whatsapp.com/ABC\nhttps://chat.whatsapp.com/DEF\n\`\`\``,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
  );
});

// ─── Group type: Similar (auto-detect with word grouping) ─────────────────
bot.action(/^gs_similar_(.+)$/, async (ctx) => {
  await ctx.answerCbQuery("Detecting groups...");
  const feature = ctx.match[1];
  try {
    const all = await getAllGroupsWithDetails(0);
    if (!all.length) { await sendClean(ctx, "❌ No groups found.", Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]])); return; }

    // Group by first word (auto-detect)
    const wordMap = {};
    for (const g of all) {
      const firstWord = (g.name.trim().split(/\s+/)[0] || g.name).toLowerCase();
      if (!wordMap[firstWord]) wordMap[firstWord] = [];
      wordMap[firstWord].push(g.id);
    }
    const entries = Object.entries(wordMap).sort((a, b) => b[1].length - a[1].length);

    updateSession(ctx.from.id, {
      featureFlow: { ...getSession(ctx.from.id).featureFlow, feature, allGroups: all, wordGroups: wordMap, step: "similar_pick" },
    });

    // Build inline buttons: each word (count) — 2 per row, max 20 entries
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
    rows.push([Markup.button.callback("🔍 Custom Search", `gs_sim_custom`)]);
    rows.push([Markup.button.callback("🏠 Main Menu", "back_menu")]);

    await sendClean(ctx,
      `🔍 *Similar Groups*\n━━━━━━━━━━━━━━━━━━\n*Auto-detected group prefixes:*\n\nTotal groups: *${all.length}*\n\n_Tap a prefix to select all matching groups:_`,
      { parse_mode: "Markdown", ...Markup.inlineKeyboard(rows) }
    );
  } catch (err) { await sendClean(ctx, `❌ Error: ${err.message}`, Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]])); }
});

bot.action(/^gs_swp_(\d+)$/, async (ctx) => {
  await ctx.answerCbQuery();
  const idx = parseInt(ctx.match[1]);
  const flow = getSession(ctx.from.id).featureFlow;
  const wordMap = flow.wordGroups || {};
  const entries = Object.entries(wordMap).sort((a, b) => b[1].length - a[1].length);
  if (idx >= entries.length) return;
  const [word, ids] = entries[idx];
  const matching = flow.allGroups.filter((g) => ids.includes(g.id));
  updateSession(ctx.from.id, { featureFlow: { ...flow, selectedIds: ids, keyword: word, step: "confirm" } });
  await sendClean(ctx,
    `✅ *Selected: "${word}" — ${matching.length} group(s)*\n━━━━━━━━━━━━━━━━━━\n\n${matching.slice(0, 20).map((g, i) => `${i + 1}. ${g.name}`).join("\n")}${matching.length > 20 ? `\n_...and ${matching.length - 20} more_` : ""}`,
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
  await sendClean(ctx,
    `🔍 *Custom Search*\n━━━━━━━━━━━━━━━━━━\n\nType a keyword to find matching groups:`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
  );
});

// ─── Group type: All ──────────────────────────────────────────────────────
bot.action(/^gs_all_(.+)$/, async (ctx) => {
  await ctx.answerCbQuery("Loading groups...");
  const feature = ctx.match[1];
  try {
    const groups = await getAllGroupsWithDetails(0);
    if (!groups.length) { await sendClean(ctx, "❌ No groups found on this WhatsApp account.", Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]])); return; }
    updateSession(ctx.from.id, { featureFlow: { ...getSession(ctx.from.id).featureFlow, feature, allGroups: groups, selectedIds: groups.map((g) => g.id), step: "executing" } });
    await onGroupsConfirmed(ctx, feature, groups.map((g) => g.id), groups);
  } catch (err) { await sendClean(ctx, `❌ Error: ${err.message}`, Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]])); }
});

// ─── Group type: Select (paginated, 1 per row, full name) ──────────────────
bot.action(/^gs_select_(.+)$/, async (ctx) => {
  await ctx.answerCbQuery("Loading groups...");
  const feature = ctx.match[1];
  try {
    const groups = await getAllGroupsWithDetails(0);
    if (!groups.length) { await sendClean(ctx, "❌ No groups found.", Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]])); return; }
    updateSession(ctx.from.id, { featureFlow: { ...getSession(ctx.from.id).featureFlow, feature, allGroups: groups, selectedIds: [], page: 0, step: "paginate" } });
    await showPaginatedGroups(ctx);
  } catch (err) { await sendClean(ctx, `❌ Error: ${err.message}`, Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]])); }
});

// ─── Paginated group selection (1 per row, full name) ─────────────────────
async function showPaginatedGroups(ctx) {
  const flow = getSession(ctx.from.id).featureFlow;
  const { allGroups, selectedIds, page } = flow;
  const totalPages = Math.ceil(allGroups.length / PAGE_SIZE);
  const slice      = allGroups.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE);
  const selSet     = new Set(selectedIds);
  const rows       = [];

  for (let i = 0; i < slice.length; i++) {
    const idx = page * PAGE_SIZE + i, g = slice[i];
    const fullName = g.name.length > 40 ? g.name.slice(0, 39) + "…" : g.name;
    rows.push([Markup.button.callback(`${selSet.has(g.id) ? "✅" : "◻️"} ${fullName}`, `gs_tog_${idx}`)]);
  }

  const nav = [];
  if (page > 0)              nav.push(Markup.button.callback("◀️ Prev", "gs_prev"));
  nav.push(Markup.button.callback(`📄 ${page + 1}/${totalPages}`, "gs_noop"));
  if (page < totalPages - 1) nav.push(Markup.button.callback("▶️ Next", "gs_next"));
  rows.push(nav);
  rows.push([Markup.button.callback(`✅ Confirm (${selSet.size} selected)`, "gs_confirm")]);
  rows.push([Markup.button.callback("🏠 Main Menu", "back_menu")]);

  const text = `☑️ *Select Groups* — Page ${page + 1}/${totalPages}\n━━━━━━━━━━━━━━━━━━\nTotal: *${allGroups.length}* | Selected: *${selSet.size}*\n\n_Tap to select/deselect. 10 per page._`;
  try { await ctx.editMessageText(text, { parse_mode: "Markdown", reply_markup: Markup.inlineKeyboard(rows).reply_markup }); }
  catch { await sendClean(ctx, text, { parse_mode: "Markdown", ...Markup.inlineKeyboard(rows) }); }
}

bot.action("gs_noop", async (ctx) => { await ctx.answerCbQuery(); });
bot.action("gs_next", async (ctx) => {
  await ctx.answerCbQuery();
  const flow = getSession(ctx.from.id).featureFlow;
  if (flow.page < Math.ceil(flow.allGroups.length / PAGE_SIZE) - 1)
    updateSession(ctx.from.id, { featureFlow: { ...flow, page: flow.page + 1 } });
  await showPaginatedGroups(ctx);
});
bot.action("gs_prev", async (ctx) => {
  await ctx.answerCbQuery();
  const flow = getSession(ctx.from.id).featureFlow;
  if (flow.page > 0) updateSession(ctx.from.id, { featureFlow: { ...flow, page: flow.page - 1 } });
  await showPaginatedGroups(ctx);
});
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

// ─── Route after group selection ──────────────────────────────────────────
async function onGroupsConfirmed(ctx, feature, selectedIds, allGroups) {
  const s = getSession(ctx.from.id);

  if (feature === "make_admin") {
    updateSession(ctx.from.id, { featureFlow: { ...s.featureFlow, selectedIds, step: "admin_numbers" } });
    await sendClean(ctx,
      `👑 *Make Admin*\n━━━━━━━━━━━━━━━━━━\n\n*${selectedIds.length} group(s) selected.*\n\nEnter phone number(s) to promote — one per line:\n\`\`\`\n919876543210\n918765432109\n\`\`\``,
      { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
    );
    return;
  }

  if (feature === "demote_admin") {
    updateSession(ctx.from.id, { featureFlow: { ...s.featureFlow, selectedIds, step: "demote_numbers" } });
    await sendClean(ctx,
      `👤 *Demote Admin*\n━━━━━━━━━━━━━━━━━━\n\n*${selectedIds.length} group(s) selected.*\n\nEnter phone number(s) to demote — one per line:\n\`\`\`\n919876543210\n\`\`\``,
      { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
    );
    return;
  }

  if (feature === "edit_settings") {
    updateSession(ctx.from.id, { featureFlow: { ...s.featureFlow, selectedIds, step: "es_configure",
      desiredSettings: { announce: undefined, restrict: undefined, joinApproval: undefined, memberAddMode: undefined } } });
    await showEditSettingsConfig(ctx);
    return;
  }

  if (feature === "auto_accept") {
    updateSession(ctx.from.id, { featureFlow: { ...s.featureFlow, selectedIds, step: "aa_duration" } });
    await showAutoAcceptDuration(ctx);
    return;
  }

  await runFeature(ctx, feature, selectedIds, allGroups, []);
}

// ══════════════════════════════════════════════════════════════════════════
// ─── EDIT SETTINGS FLOW ───────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

function settingsKb(d) {
  const fmt = (val) => val === true ? "✅ On" : val === false ? "❌ Off" : "— (unchanged)";
  return Markup.inlineKeyboard([
    [Markup.button.callback(`📢 Send Messages: ${fmt(d.announce)}`,    "es_tog_announce")],
    [Markup.button.callback(`✏️ Edit Group Info: ${fmt(d.restrict)}`,  "es_tog_restrict")],
    [Markup.button.callback(`✅ Join Approval: ${fmt(d.joinApproval)}`, "es_tog_joinApproval")],
    [Markup.button.callback(`➕ Add Members: ${fmt(d.memberAddMode)}`,  "es_tog_memberAddMode")],
    [Markup.button.callback("💾 Confirm & Apply", "es_apply")],
    [Markup.button.callback("🏠 Main Menu", "back_menu")],
  ]);
}

async function showEditSettingsConfig(ctx) {
  const flow = getSession(ctx.from.id).featureFlow;
  const d = flow.desiredSettings;
  await sendClean(ctx,
    `⚙️ *Edit Settings*\n━━━━━━━━━━━━━━━━━━\n*${flow.selectedIds.length} group(s) selected.*\n\nTap to set each option:\n_"On/Off" = force that setting_\n_"unchanged" = skip that setting_`,
    { parse_mode: "Markdown", ...settingsKb(d) }
  );
}

["announce", "restrict", "joinApproval", "memberAddMode"].forEach((key) => {
  bot.action(`es_tog_${key}`, async (ctx) => {
    await ctx.answerCbQuery();
    const flow = getSession(ctx.from.id).featureFlow;
    const cur = flow.desiredSettings[key];
    const next = cur === undefined ? true : cur === true ? false : undefined;
    const newSettings = { ...flow.desiredSettings, [key]: next };
    updateSession(ctx.from.id, { featureFlow: { ...flow, desiredSettings: newSettings } });
    try { await ctx.editMessageReplyMarkup(settingsKb(newSettings).reply_markup); } catch { await showEditSettingsConfig(ctx); }
  });
});

bot.action("es_apply", async (ctx) => {
  await ctx.answerCbQuery("Applying...");
  const uid = ctx.from.id;
  const flow = getSession(uid).featureFlow;
  const sel = flow.allGroups.filter((g) => flow.selectedIds.includes(g.id));
  const total = sel.length;

  // Check if any settings are defined
  const d = flow.desiredSettings;
  if (d.announce === undefined && d.restrict === undefined && d.joinApproval === undefined && d.memberAddMode === undefined) {
    await ctx.answerCbQuery("⚠️ No settings selected!", { show_alert: true }); return;
  }

  const pm = await ctx.reply(`⚙️ *Applying settings to ${total} group(s)...*\n━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`, { parse_mode: "Markdown" });
  updateSession(uid, { lastMsgId: pm.message_id });
  await showCancelBtn(ctx);

  let done = 0, failed = 0, skipped = 0, cancelled = false;
  const details = [];
  startTimes.set(uid, Date.now());

  for (let i = 0; i < total; i++) {
    if (isCancelled(uid)) { cancelled = true; break; }
    const g = sel[i];
    try {
      await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
        `⚙️ *Applying Settings...*\n━━━━━━━━━━━━━━━━━━\n✅ Done: ${done}/${total}\n⚙️ ${g.name}\n${bar(i, total)}`,
        { parse_mode: "Markdown" });
    } catch {}
    try {
      const result = await applyGroupSettings(0, g.id, d);
      if (result.changes.length) { done++; details.push(`✅ *${g.name}*\n  ${result.changes.join("\n  ")}`); }
      else { skipped++; details.push(`⏭️ *${g.name}* — already set`); }
    } catch (err) { failed++; details.push(`❌ *${g.name}* — Error: ${err.message}`); }
    await sleep(800);
  }

  await removeCancelBtn(ctx);
  try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
    `✅ *Settings Applied!* Changed: ${done} | Skipped: ${skipped} | Failed: ${failed}`, { parse_mode: "Markdown" }); } catch {}

  for (let c = 0; c < details.length; c += 15) {
    await ctx.reply(details.slice(c, c + 15).join("\n"), { parse_mode: "Markdown" });
    await sleep(300);
  }
  await sendSummary(ctx, { feature: "edit_settings", total, success: done, failed, cancelled,
    extra: [`⏭️ *Already set (skipped): ${skipped}*`] });
  updateSession(uid, { featureFlow: null }); await sendMainMenu(ctx);
});

// ══════════════════════════════════════════════════════════════════════════
// ─── CHANGE NAME FLOW ─────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

bot.action("cn_random", async (ctx) => {
  await ctx.answerCbQuery();
  const flow = getSession(ctx.from.id).featureFlow;
  updateSession(ctx.from.id, { featureFlow: { ...flow, step: "cn_random_name", cnMethod: "random" } });
  await sendClean(ctx,
    `✏️ *Change Name — Randomly*\n━━━━━━━━━━━━━━━━━━\n\n*Enter base name:*\n\n_e.g._ \`Madara\` → groups get names like _Madara 1, Madara 2..._`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
  );
});

bot.action("cn_vcf", async (ctx) => {
  await ctx.answerCbQuery();
  const flow = getSession(ctx.from.id).featureFlow;
  updateSession(ctx.from.id, { featureFlow: { ...flow, step: "cn_vcf_links", cnMethod: "vcf", links: [] } });
  await sendClean(ctx,
    `📛 *Change Name — as VCF*\n━━━━━━━━━━━━━━━━━━\n\nSend *all group links* (one per line):\n\`\`\`\nhttps://chat.whatsapp.com/ABC\nhttps://chat.whatsapp.com/DEF\n\`\`\`\n\n_Bot will detect which VCF belongs to which group._`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
  );
});

bot.action("cn_numbering_yes", async (ctx) => {
  await ctx.answerCbQuery();
  const flow = getSession(ctx.from.id).featureFlow;
  updateSession(ctx.from.id, { featureFlow: { ...flow, numbering: true, step: "cn_random_links" } });
  await sendClean(ctx,
    `✏️ *Change Name — Randomly*\n━━━━━━━━━━━━━━━━━━\n\n✅ Numbering: ON\n\nNow send *all group links* (one per line):`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
  );
});

bot.action("cn_numbering_no", async (ctx) => {
  await ctx.answerCbQuery();
  const flow = getSession(ctx.from.id).featureFlow;
  updateSession(ctx.from.id, { featureFlow: { ...flow, numbering: false, step: "cn_random_links" } });
  await sendClean(ctx,
    `✏️ *Change Name — Randomly*\n━━━━━━━━━━━━━━━━━━\n\n❌ Numbering: OFF (all groups get same name)\n\nNow send *all group links* (one per line):`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
  );
});

// ══════════════════════════════════════════════════════════════════════════
// ─── AUTO ACCEPT FLOW ─────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

async function showAutoAcceptDuration(ctx) {
  const flow = getSession(ctx.from.id).featureFlow;
  await sendClean(ctx,
    `⏰ *Auto Accept*\n━━━━━━━━━━━━━━━━━━\n*${flow.selectedIds.length} group(s) selected.*\n\n*Select duration:*\n\n_Bot will auto-approve anyone who joins via invite link._`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("5 min",  "aa_dur_300"),   Markup.button.callback("10 min", "aa_dur_600")],
      [Markup.button.callback("30 min", "aa_dur_1800"),  Markup.button.callback("1 hour", "aa_dur_3600")],
      [Markup.button.callback("2 hours","aa_dur_7200"),  Markup.button.callback("6 hours","aa_dur_21600")],
      [Markup.button.callback("✏️ Custom Duration", "aa_dur_custom")],
      [Markup.button.callback("🏠 Main Menu", "back_menu")],
    ]) }
  );
}

bot.action(/^aa_dur_(\d+)$/, async (ctx) => {
  await ctx.answerCbQuery();
  const secs = parseInt(ctx.match[1]);
  const flow = getSession(ctx.from.id).featureFlow;
  updateSession(ctx.from.id, { featureFlow: { ...flow, aaDuration: secs, step: "aa_confirm" } });
  const mins = Math.round(secs / 60);
  const label = mins >= 60 ? `${mins / 60}h` : `${mins}min`;
  await sendClean(ctx,
    `⏰ *Auto Accept — Confirm*\n━━━━━━━━━━━━━━━━━━\n📁 Groups: *${flow.selectedIds.length}*\n⏱ Duration: *${label}*\n\n_Only link-join requests will be auto-approved._\n_Pre-existing pending and member-added requests will NOT be approved._`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("✅ Start Auto Accept", "aa_start")],
      [Markup.button.callback("🔙 Change Duration", "aa_back_duration")],
      [Markup.button.callback("🏠 Main Menu", "back_menu")],
    ]) }
  );
});

bot.action("aa_dur_custom", async (ctx) => {
  await ctx.answerCbQuery();
  const flow = getSession(ctx.from.id).featureFlow;
  updateSession(ctx.from.id, { featureFlow: { ...flow, step: "aa_custom_duration" } });
  await sendClean(ctx,
    `⏰ *Auto Accept — Custom Duration*\n━━━━━━━━━━━━━━━━━━\n\nEnter duration in *minutes:*\n\n_Example:_ \`120\` _= 2 hours_`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
  );
});

bot.action("aa_back_duration", async (ctx) => {
  await ctx.answerCbQuery();
  await showAutoAcceptDuration(ctx);
});

bot.action("aa_start", async (ctx) => {
  await ctx.answerCbQuery("Starting auto accept...");
  const uid = ctx.from.id;
  const flow = getSession(uid).featureFlow;
  const secs = flow.aaDuration;
  const sel = flow.allGroups.filter((g) => flow.selectedIds.includes(g.id));
  const mins = Math.round(secs / 60);
  const label = mins >= 60 ? `${mins / 60}h` : `${mins}min`;

  startAutoAcceptForGroups(flow.selectedIds);

  const pm = await ctx.reply(
    `⏰ *Auto Accept ACTIVE!*\n━━━━━━━━━━━━━━━━━━\n📁 Groups: *${sel.length}*\n⏱ Duration: *${label}*\n\n_Anyone who joins via link will be auto-approved._\n\n⏳ Stopping at: ${new Date(Date.now() + secs * 1000).toLocaleTimeString()}`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🛑 Stop Auto Accept", "aa_stop")]]) }
  );
  updateSession(uid, { lastMsgId: pm.message_id, featureFlow: { ...flow, step: "aa_running" } });

  setTimeout(async () => {
    stopAutoAcceptForGroups(flow.selectedIds);
    const stats = getAutoAcceptStats(flow.selectedIds);
    const totalAccepted = Object.values(stats).reduce((s, v) => s + v.accepted, 0);
    const details = sel.map((g) => `• ${g.name}: ${stats[g.id]?.accepted || 0} accepted`);
    try { await bot.telegram.deleteMessage(pm.chat?.id || ctx.chat.id, pm.message_id); } catch {}
    await ctx.reply(
      `⏰ *Auto Accept Completed!*\n━━━━━━━━━━━━━━━━━━\n⏱ Duration: *${label}*\n✅ Total Accepted: *${totalAccepted}*\n━━━━━━━━━━━━━━━━━━\n${details.join("\n")}`,
      { parse_mode: "Markdown" }
    );
    updateSession(uid, { featureFlow: null });
    await sendMainMenu(ctx);
  }, secs * 1000);
});

bot.action("aa_stop", async (ctx) => {
  await ctx.answerCbQuery("Stopping...");
  const uid = ctx.from.id;
  const flow = getSession(uid).featureFlow;
  if (!flow?.selectedIds) { await sendMainMenu(ctx); return; }
  stopAutoAcceptForGroups(flow.selectedIds);
  const stats = getAutoAcceptStats(flow.selectedIds);
  const total = Object.values(stats).reduce((s, v) => s + v.accepted, 0);
  const sel = flow.allGroups.filter((g) => flow.selectedIds.includes(g.id));
  const details = sel.map((g) => `• ${g.name}: ${stats[g.id]?.accepted || 0} accepted`);
  try { await ctx.editMessageText(
    `⏰ *Auto Accept Stopped*\n━━━━━━━━━━━━━━━━━━\n✅ Total Accepted: *${total}*\n━━━━━━━━━━━━━━━━━━\n${details.join("\n")}`,
    { parse_mode: "Markdown" }); } catch {}
  updateSession(uid, { featureFlow: null }); await sendMainMenu(ctx);
});

// ══════════════════════════════════════════════════════════════════════════
// ─── CHANGE NAME: Random naming handlers ──────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

async function runChangeNameRandom(ctx, links, baseName, numbering) {
  const uid = ctx.from.id;
  startTimes.set(uid, Date.now());
  const total = links.length;
  const pm = await ctx.reply(`✏️ *Renaming ${total} group(s)...*\n━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`, { parse_mode: "Markdown" });
  updateSession(uid, { lastMsgId: pm.message_id });
  await showCancelBtn(ctx);

  let done = 0, failed = 0, cancelled = false;
  const details = [];

  for (let i = 0; i < total; i++) {
    if (isCancelled(uid)) { cancelled = true; break; }
    const code = links[i];
    const newName = numbering ? `${baseName} ${i + 1}` : baseName;
    try {
      await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
        `✏️ *Renaming...*\n━━━━━━━━━━━━━━━━━━\n✅ Done: ${done}/${total}\n⚙️ → ${newName}\n${bar(i, total)}`,
        { parse_mode: "Markdown" });
    } catch {}
    try {
      const info = await getGroupInfoFromLink(0, code);
      if (!info) throw new Error("Invalid link");
      await renameGroup(0, info.id, newName);
      done++; details.push(`✅ ${info.name} → ${newName}`);
    } catch (err) { failed++; details.push(`❌ Group ${i + 1}: ${err.message}`); }
    await sleep(1200);
  }

  await removeCancelBtn(ctx);
  try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
    `✅ *Rename Done!* ${done}/${total}`, { parse_mode: "Markdown" }); } catch {}
  for (let c = 0; c < details.length; c += 30)
    await ctx.reply(details.slice(c, c + 30).join("\n"), { parse_mode: "Markdown" });
  await sendSummary(ctx, { feature: "change_name", total, success: done, failed, cancelled });
  updateSession(uid, { featureFlow: null, awaitingVcf: null }); await sendMainMenu(ctx);
}

// ══════════════════════════════════════════════════════════════════════════
// ─── FEATURE EXECUTION ────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

async function runFeature(ctx, feature, selectedIds, allGroups, adminNumbers) {
  const uid   = ctx.from.id;
  const sel   = allGroups.filter((g) => selectedIds.includes(g.id));
  const total = sel.length;
  startTimes.set(uid, Date.now());
  updateSession(uid, { cancelPending: false });

  // ── GET LINKS ────────────────────────────────────────────────────────
  if (feature === "get_links") {
    const pm = await ctx.reply(`🔗 *Getting links for ${total} group(s)...*\n━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`, { parse_mode: "Markdown" });
    updateSession(uid, { lastMsgId: pm.message_id });
    await showCancelBtn(ctx);
    const results = [], failedNames = [];
    let done = 0, cancelled = false;

    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined, `🔗 *Getting Links...*\n━━━━━━━━━━━━━━━━━━\n✅ Done: ${done}/${total}\n⚙️ ${g.name}\n${bar(i, total)}`, { parse_mode: "Markdown" }); } catch {}
      try { const link = await getGroupInviteLink(0, g.id); results.push({ name: g.name, link }); done++; }
      catch { failedNames.push(g.name); }
      await sleep(600);
    }

    await removeCancelBtn(ctx);
    try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined, `✅ *Links Ready!* ${done}/${total}`, { parse_mode: "Markdown" }); } catch {}
    for (let c = 0; c < results.length; c += 20) {
      await ctx.reply(`🔗 *Group Links (${c + 1}–${Math.min(c + 20, results.length)} of ${results.length}):*\n\n` +
        results.slice(c, c + 20).map((r, i) => `*${c + i + 1}.* ${r.name}\n${r.link}`).join("\n\n"), { parse_mode: "Markdown" });
      await sleep(300);
    }
    await sendSummary(ctx, { feature, total, success: done, failed: failedNames.length, cancelled,
      extra: failedNames.length ? [`❌ Failed:\n${failedNames.map((n) => `• ${n}`).join("\n")}`] : [] });
    updateSession(uid, { featureFlow: null }); await sendMainMenu(ctx); return;
  }

  // ── LEAVE GROUPS ─────────────────────────────────────────────────────
  if (feature === "leave") {
    const pm = await ctx.reply(`🚪 *Leaving ${total} group(s)...*\n━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`, { parse_mode: "Markdown" });
    updateSession(uid, { lastMsgId: pm.message_id });
    await showCancelBtn(ctx);
    let done = 0, failed = 0, cancelled = false;
    const failedNames = [];

    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined, `🚪 *Leaving Groups...*\n━━━━━━━━━━━━━━━━━━\n✅ Left: ${done} | ❌ Failed: ${failed}\n⚙️ ${g.name}\n${bar(i, total)}`, { parse_mode: "Markdown" }); } catch {}
      try { await leaveGroup(0, g.id); done++; }
      catch { failed++; failedNames.push(g.name); }
      await sleep(1500);
    }

    await removeCancelBtn(ctx);
    try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined, `✅ *Done!* Left: ${done} | Failed: ${failed}`, { parse_mode: "Markdown" }); } catch {}
    await sendSummary(ctx, { feature, total, success: done, failed, cancelled,
      extra: failedNames.length ? [`❌ Failed:\n${failedNames.map((n) => `• ${n}`).join("\n")}`] : [] });
    updateSession(uid, { featureFlow: null }); await sendMainMenu(ctx); return;
  }

  // ── REMOVE MEMBERS ────────────────────────────────────────────────────
  if (feature === "remove_members") {
    const pm = await ctx.reply(`👥 *Removing members from ${total} group(s)...*\n━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`, { parse_mode: "Markdown" });
    updateSession(uid, { lastMsgId: pm.message_id });
    await showCancelBtn(ctx);
    let done = 0, failed = 0, totalRemoved = 0, cancelled = false;
    const details = [];

    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined, `👥 *Removing Members...*\n━━━━━━━━━━━━━━━━━━\n✅ Groups: ${done}/${total}\n⚙️ ${g.name}\n${bar(i, total)}`, { parse_mode: "Markdown" }); } catch {}
      try { const n = await removeAllMembers(0, g.id); totalRemoved += n; done++; details.push(`${g.name}: ${n} removed`); }
      catch { failed++; details.push(`${g.name}: ❌ error`); }
      await sleep(2000);
    }

    await removeCancelBtn(ctx);
    try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined, `✅ *Done!* ${done}/${total} groups | 👥 ${totalRemoved} removed`, { parse_mode: "Markdown" }); } catch {}
    await sendSummary(ctx, { feature, total, success: done, failed, cancelled,
      extra: [`👥 *Total Members Removed: ${totalRemoved}*`, ...details.slice(0, 20).map((d) => `• ${d}`)] });
    updateSession(uid, { featureFlow: null }); await sendMainMenu(ctx); return;
  }

  // ── MAKE ADMIN ────────────────────────────────────────────────────────
  if (feature === "make_admin") {
    const pm = await ctx.reply(`👑 *Making admin in ${total} group(s)...*\n━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`, { parse_mode: "Markdown" });
    updateSession(uid, { lastMsgId: pm.message_id });
    await showCancelBtn(ctx);
    let done = 0, failed = 0, totalPromoted = 0, cancelled = false;
    const details = [];

    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined, `👑 *Making Admin...*\n━━━━━━━━━━━━━━━━━━\n✅ Done: ${done}/${total}\n⚙️ ${g.name}\n${bar(i, total)}`, { parse_mode: "Markdown" }); } catch {}
      try { const n = await makeAdminByNumbers(0, g.id, adminNumbers); totalPromoted += n; done++; details.push(`${g.name}: ${n} promoted`); }
      catch { failed++; details.push(`${g.name}: ❌ error`); }
      await sleep(1500);
    }

    await removeCancelBtn(ctx);
    try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined, `✅ *Done!* ${done}/${total} groups | 👑 ${totalPromoted} promoted`, { parse_mode: "Markdown" }); } catch {}
    await sendSummary(ctx, { feature, total, success: done, failed, cancelled,
      extra: [`👑 *Total Promoted: ${totalPromoted}*`, ...details.slice(0, 20).map((d) => `• ${d}`)] });
    updateSession(uid, { featureFlow: null }); await sendMainMenu(ctx); return;
  }

  // ── DEMOTE ADMIN ──────────────────────────────────────────────────────
  if (feature === "demote_admin") {
    const pm = await ctx.reply(`👤 *Demoting admin in ${total} group(s)...*\n━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`, { parse_mode: "Markdown" });
    updateSession(uid, { lastMsgId: pm.message_id });
    await showCancelBtn(ctx);
    let done = 0, failed = 0, totalDemoted = 0, cancelled = false;
    const details = [];

    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
        `👤 *Demoting Admin...*\n━━━━━━━━━━━━━━━━━━\n✅ Done: ${done}/${total}\n⚙️ ${g.name}\n${bar(i, total)}`,
        { parse_mode: "Markdown" }); } catch {}
      try { const n = await demoteAdminInGroup(0, g.id, adminNumbers); totalDemoted += n; done++; details.push(`${g.name}: ${n} demoted`); }
      catch { failed++; details.push(`${g.name}: ❌ error`); }
      await sleep(1200);
    }

    await removeCancelBtn(ctx);
    try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
      `✅ *Done!* ${done}/${total} groups | 👤 ${totalDemoted} demoted`, { parse_mode: "Markdown" }); } catch {}
    await sendSummary(ctx, { feature, total, success: done, failed, cancelled,
      extra: [`👤 *Total Demoted: ${totalDemoted}*`, ...details.slice(0, 20).map((d) => `• ${d}`)] });
    updateSession(uid, { featureFlow: null }); await sendMainMenu(ctx); return;
  }

  // ── RESET LINK ────────────────────────────────────────────────────────
  if (feature === "reset_link") {
    const pm = await ctx.reply(`🔄 *Resetting links for ${total} group(s)...*\n━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`, { parse_mode: "Markdown" });
    updateSession(uid, { lastMsgId: pm.message_id });
    await showCancelBtn(ctx);
    const results = [], failedNames = [];
    let done = 0, cancelled = false;

    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
        `🔄 *Resetting Links...*\n━━━━━━━━━━━━━━━━━━\n✅ Done: ${done}/${total}\n⚙️ ${g.name}\n${bar(i, total)}`,
        { parse_mode: "Markdown" }); } catch {}
      try { const link = await resetGroupInviteLink(0, g.id); results.push({ name: g.name, link }); done++; }
      catch { failedNames.push(g.name); }
      await sleep(1000);
    }

    await removeCancelBtn(ctx);
    try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
      `✅ *Links Reset!* ${done}/${total}`, { parse_mode: "Markdown" }); } catch {}
    for (let c = 0; c < results.length; c += 20) {
      await ctx.reply(`🔄 *New Links (${c + 1}–${Math.min(c + 20, results.length)} of ${results.length}):*\n\n` +
        results.slice(c, c + 20).map((r, i) => `*${c + i + 1}.* ${r.name}\n${r.link}`).join("\n\n"), { parse_mode: "Markdown" });
      await sleep(300);
    }
    if (failedNames.length) await ctx.reply(`❌ *Failed:*\n${failedNames.map((n) => `• ${n}`).join("\n")}`, { parse_mode: "Markdown" });
    await sendSummary(ctx, { feature, total, success: done, failed: failedNames.length, cancelled });
    updateSession(uid, { featureFlow: null }); await sendMainMenu(ctx); return;
  }

  // ── APPROVAL TOGGLE ───────────────────────────────────────────────────
  if (feature === "approval") {
    const pm = await ctx.reply(`✅ *Toggling approval in ${total} group(s)...*\n━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`, { parse_mode: "Markdown" });
    updateSession(uid, { lastMsgId: pm.message_id });
    await showCancelBtn(ctx);
    let done = 0, failed = 0, cancelled = false;
    const details = [];

    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined, `✅ *Toggling Approval...*\n━━━━━━━━━━━━━━━━━━\n✅ Done: ${done}/${total}\n⚙️ ${g.name}\n${bar(i, total)}`, { parse_mode: "Markdown" }); } catch {}
      try {
        const cur = await getGroupApprovalStatus(0, g.id), next = !cur;
        await setGroupApproval(0, g.id, next);
        details.push(`${g.name}: ${cur ? "✅ On" : "❌ Off"} → ${next ? "✅ On" : "❌ Off"}`);
        done++;
      } catch { failed++; details.push(`${g.name}: ❌ error`); }
      await sleep(1000);
    }

    await removeCancelBtn(ctx);
    try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined, `✅ *Approval Toggled!* ${done}/${total}`, { parse_mode: "Markdown" }); } catch {}
    for (let c = 0; c < details.length; c += 30)
      await ctx.reply(details.slice(c, c + 30).map((d) => `• ${d}`).join("\n"), { parse_mode: "Markdown" });
    await sendSummary(ctx, { feature, total, success: done, failed, cancelled });
    updateSession(uid, { featureFlow: null }); await sendMainMenu(ctx); return;
  }

  // ── APPROVE PENDING MEMBERS ───────────────────────────────────────────
  if (feature === "approve_pending") {
    const pm = await ctx.reply(
      `✋ *Approving pending members in ${total} group(s)...*\n━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`,
      { parse_mode: "Markdown" }
    );
    updateSession(uid, { lastMsgId: pm.message_id });
    await showCancelBtn(ctx);
    let done = 0, failed = 0, totalPending = 0, totalApproved = 0, totalActuallyJoined = 0, cancelled = false;
    const details = [];

    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      try {
        await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
          `✋ *Approving Pending Members...*\n━━━━━━━━━━━━━━━━━━\n✅ Groups done: ${done}/${total}\n⚙️ ${g.name}\n${bar(i, total)}`,
          { parse_mode: "Markdown" });
      } catch {}
      try {
        const result = await approveAllPending(0, g.id);
        totalPending += result.pendingCount; totalApproved += result.approved;
        totalActuallyJoined += result.actuallyJoined ?? 0; done++;
        const notJoined = result.approved - (result.actuallyJoined ?? result.approved);
        details.push(`*${g.name}*\n  ⏳ Pending: ${result.pendingCount}\n  ✅ Approved: ${result.approved} | ❌ Failed: ${result.failed}\n  👥 Members: ${result.beforeCount} → ${result.afterCount}` +
          (notJoined > 0 ? ` ⚠️ (${notJoined} not joined yet)` : ""));
      } catch (err) { failed++; details.push(`*${g.name}* — ❌ Error: ${err.message}`); }
      await sleep(2500);
    }

    await removeCancelBtn(ctx);
    try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
      `✅ *Approve Pending Done!*\n━━━━━━━━━━━━━━━━━━\n✋ Total Pending: ${totalPending}\n✅ Approved: ${totalApproved}\n👥 Joined: ${totalActuallyJoined}\n📁 Groups: ${done}/${total}`,
      { parse_mode: "Markdown" }); } catch {}
    for (let c = 0; c < details.length; c += 15) {
      await ctx.reply(`📋 *Group Details:*\n━━━━━━━━━━━━━━━━━━\n\n` + details.slice(c, c + 15).join("\n\n"), { parse_mode: "Markdown" });
      await sleep(300);
    }
    await sendSummary(ctx, { feature, total, success: done, failed, cancelled,
      extra: [`✋ *Total Pending: ${totalPending}*`, `✅ *Approved: ${totalApproved}*`, `👥 *Joined: ${totalActuallyJoined}*`,
        totalPending - totalApproved > 0 ? `⚠️ *Not approved: ${totalPending - totalApproved}* (deleted/banned)` : ""
      ].filter(Boolean) });
    updateSession(uid, { featureFlow: null }); await sendMainMenu(ctx); return;
  }

  // ── MEMBER LIST ───────────────────────────────────────────────────────
  if (feature === "member_list") {
    const pm = await ctx.reply(`📊 *Getting member list for ${total} group(s)...*\n━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`, { parse_mode: "Markdown" });
    updateSession(uid, { lastMsgId: pm.message_id });
    await showCancelBtn(ctx);
    let done = 0, failed = 0, totalMembers = 0, cancelled = false;
    const listData = [];

    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      try {
        await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined, `📊 *Member List...*\n━━━━━━━━━━━━━━━━━━\n✅ Done: ${done}/${total}\n⚙️ ${g.name}\n${bar(i, total)}`, { parse_mode: "Markdown" });
        const members = await getGroupMembers(0, g.id);
        totalMembers += members.length; listData.push({ name: g.name, count: members.length, members }); done++;
      } catch { failed++; listData.push({ name: g.name, count: 0, members: [], error: true }); }
      await sleep(600);
    }

    await removeCancelBtn(ctx);
    const sorted = [...listData].sort((a, b) => b.count - a.count);
    for (let c = 0; c < sorted.length; c += 20) {
      const chunk = sorted.slice(c, c + 20);
      await ctx.reply(`📊 *Member Count (${c + 1}–${Math.min(c + 20, sorted.length)} of ${sorted.length}):*\n━━━━━━━━━━━━━━━━━━\n` +
        chunk.map((g, i) => `${c + i + 1}. *${g.name}* — ${g.error ? "❌ Error" : `${g.count} members`}`).join("\n") +
        (c + 20 >= sorted.length ? `\n━━━━━━━━━━━━━━━━━━\n📊 *Total: ${totalMembers} members*` : ""),
        { parse_mode: "Markdown" });
      await sleep(300);
    }
    for (const g of sorted) {
      if (g.error || !g.members.length) continue;
      const lines = g.members.map((m) => `+${m.phone}${m.admin === "superadmin" ? " 👑" : m.admin === "admin" ? " ⭐" : ""}`);
      for (let c = 0; c < lines.length; c += 50) {
        await ctx.reply(`👥 *${g.name}* (${g.count}):\n━━━━━━━━━━━━━━━━━━\n` + lines.slice(c, c + 50).join("\n"), { parse_mode: "Markdown" });
        await sleep(300);
      }
    }
    await sendSummary(ctx, { feature, total, success: done, failed, cancelled, extra: [`👥 *Total Members: ${totalMembers}*`] });
    updateSession(uid, { featureFlow: null }); await sendMainMenu(ctx); return;
  }

  // ── PENDING LIST ──────────────────────────────────────────────────────
  if (feature === "pending_list") {
    const pm = await ctx.reply(`⏳ *Getting pending requests for ${total} group(s)...*\n━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`, { parse_mode: "Markdown" });
    updateSession(uid, { lastMsgId: pm.message_id });
    await showCancelBtn(ctx);
    let done = 0, failed = 0, totalPending = 0, cancelled = false;
    const listData = [];

    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      try {
        await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined, `⏳ *Pending Requests...*\n━━━━━━━━━━━━━━━━━━\n✅ Done: ${done}/${total}\n⚙️ ${g.name}\n${bar(i, total)}`, { parse_mode: "Markdown" });
        const pending = await getGroupPendingRequests(0, g.id);
        totalPending += pending.length; listData.push({ name: g.name, count: pending.length, pending }); done++;
      } catch { failed++; listData.push({ name: g.name, count: 0, pending: [], error: true }); }
      await sleep(600);
    }

    await removeCancelBtn(ctx);
    await ctx.reply(`⏳ *Pending Requests Summary:*\n━━━━━━━━━━━━━━━━━━\n` +
      listData.map((g, i) => `${i + 1}. *${g.name}* — ${g.error ? "❌ Error" : `${g.count} pending`}`).join("\n") +
      `\n━━━━━━━━━━━━━━━━━━\n⏳ *Total: ${totalPending}*`, { parse_mode: "Markdown" });
    for (const g of listData) {
      if (g.error || !g.count) continue;
      const lines = g.pending.map((p) => `+${p.phone}`);
      for (let c = 0; c < lines.length; c += 50) {
        await ctx.reply(`⏳ *${g.name}* (${g.count} pending):\n━━━━━━━━━━━━━━━━━━\n` + lines.slice(c, c + 50).join("\n"), { parse_mode: "Markdown" });
        await sleep(300);
      }
    }
    if (!totalPending && !cancelled) await ctx.reply("✅ *No pending join requests found.*", { parse_mode: "Markdown" });
    await sendSummary(ctx, { feature, total, success: done, failed, cancelled, extra: [`⏳ *Total Pending: ${totalPending}*`] });
    updateSession(uid, { featureFlow: null }); await sendMainMenu(ctx); return;
  }
}

// ══════════════════════════════════════════════════════════════════════════
// ─── JOIN GROUPS ──────────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

bot.action("join_groups_start", async (ctx) => {
  await ctx.answerCbQuery();
  if (getStatus(0) !== "connected") { await ctx.answerCbQuery("⚠️ Connect WhatsApp first!", { show_alert: true }); return; }
  updateSession(ctx.from.id, { joinFlow: { step: "links" }, cancelPending: false });
  await editOrSend(ctx,
    `🔗 *Join Groups*\n━━━━━━━━━━━━━━━━━━\n\nSend all invite links — one per line:\n\n\`\`\`\nhttps://chat.whatsapp.com/ABC123\nhttps://chat.whatsapp.com/DEF456\n\`\`\``,
    { parse_mode: "Markdown", reply_markup: Markup.inlineKeyboard([[Markup.button.callback("❌ Cancel", "back_menu")]]).reply_markup }
  );
});

// ══════════════════════════════════════════════════════════════════════════
// ─── CREATE GROUPS FLOW ───────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

bot.action("create_groups_start", async (ctx) => {
  await ctx.answerCbQuery();
  if (getStatus(0) !== "connected") { await ctx.answerCbQuery("⚠️ Connect WhatsApp first!", { show_alert: true }); return; }
  updateSession(ctx.from.id, { groupFlow: defaultGroupFlow() });
  await editOrSend(ctx,
    `📋 *Create Groups — Step 1 of 9*\n━━━━━━━━━━━━━━━━━━\n\n*What should the group name be?*\n\n_Type a name below._`,
    { parse_mode: "Markdown", reply_markup: Markup.inlineKeyboard([[Markup.button.callback("❌ Cancel", "back_menu")]]).reply_markup }
  );
});

async function askNumbering(ctx) {
  const flow = getSession(ctx.from.id).groupFlow;
  await sendClean(ctx, `📋 *Create Groups — Step 3 of 9*\n━━━━━━━━━━━━━━━━━━\n\n*Add numbering?*\n\n✅ Yes → _${flow.name} 1, ${flow.name} 2..._\n❌ No  → All named _${flow.name}_`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("✅ Yes", "gf_num_yes"), Markup.button.callback("❌ No", "gf_num_no")], [Markup.button.callback("❌ Cancel", "back_menu")]]) });
}
bot.action("gf_num_yes", async (ctx) => { await ctx.answerCbQuery(); const s=getSession(ctx.from.id); updateSession(ctx.from.id,{groupFlow:{...s.groupFlow,numbering:true,step:"description"}}); await askDescription(ctx); });
bot.action("gf_num_no",  async (ctx) => { await ctx.answerCbQuery(); const s=getSession(ctx.from.id); updateSession(ctx.from.id,{groupFlow:{...s.groupFlow,numbering:false,step:"description"}}); await askDescription(ctx); });

async function askDescription(ctx) {
  await sendClean(ctx, `📋 *Create Groups — Step 4 of 9*\n━━━━━━━━━━━━━━━━━━\n\n*Enter Group Description:*\n\n_Same for all groups. Skip if none._`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("⏭️ Skip", "gf_desc_skip")], [Markup.button.callback("❌ Cancel", "back_menu")]]) });
}
bot.action("gf_desc_skip", async (ctx) => { await ctx.answerCbQuery(); const s=getSession(ctx.from.id); updateSession(ctx.from.id,{groupFlow:{...s.groupFlow,description:"",step:"photo"}}); await askPhoto(ctx); });

async function askPhoto(ctx) {
  await sendClean(ctx, `📋 *Create Groups — Step 5 of 9*\n━━━━━━━━━━━━━━━━━━\n\n*Send a Group Photo:*\n\n_Same for all groups. Skip if none._`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("⏭️ Skip", "gf_photo_skip")], [Markup.button.callback("❌ Cancel", "back_menu")]]) });
}
bot.action("gf_photo_skip", async (ctx) => { await ctx.answerCbQuery(); const s=getSession(ctx.from.id); updateSession(ctx.from.id,{groupFlow:{...s.groupFlow,photo:null,step:"disappearing"}}); await askDisappearing(ctx); });

async function askDisappearing(ctx) {
  await sendClean(ctx, `📋 *Create Groups — Step 6 of 9*\n━━━━━━━━━━━━━━━━━━\n\n*Set Disappearing Messages:*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("⏱ 24h","gf_dis_86400"), Markup.button.callback("📅 7 Days","gf_dis_604800"), Markup.button.callback("🗓 90 Days","gf_dis_7776000")],
      [Markup.button.callback("⏭️ Skip / Off","gf_dis_0")], [Markup.button.callback("❌ Cancel","back_menu")],
    ]) });
}
[0,86400,604800,7776000].forEach((s) => {
  bot.action(`gf_dis_${s}`, async (ctx) => { await ctx.answerCbQuery(); const ss=getSession(ctx.from.id); updateSession(ctx.from.id,{groupFlow:{...ss.groupFlow,disappearing:s,step:"members"}}); await askMembers(ctx); });
});

async function askMembers(ctx) {
  await sendClean(ctx, `📋 *Create Groups — Step 7 of 9*\n━━━━━━━━━━━━━━━━━━\n\n*Add members? (one per line):*\n\`\`\`\n919876543210\n\`\`\`\n\n_Skip if none._`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("⏭️ Skip","gf_mem_skip")],[Markup.button.callback("❌ Cancel","back_menu")]]) });
}
bot.action("gf_mem_skip", async (ctx) => { await ctx.answerCbQuery(); const s=getSession(ctx.from.id); updateSession(ctx.from.id,{groupFlow:{...s.groupFlow,members:[],makeAdmin:false,step:"permissions"}}); await askPermissions(ctx); });

async function askAdmin(ctx) {
  const flow = getSession(ctx.from.id).groupFlow;
  await sendClean(ctx, `📋 *Create Groups — Step 8 of 9*\n━━━━━━━━━━━━━━━━━━\n\n👥 *${flow.members.length} member(s)* will be added.\n\n*Make them Admin?*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("✅ Yes","gf_admin_yes"),Markup.button.callback("❌ No","gf_admin_no")],[Markup.button.callback("❌ Cancel","back_menu")]]) });
}
bot.action("gf_admin_yes", async (ctx) => { await ctx.answerCbQuery(); const s=getSession(ctx.from.id); updateSession(ctx.from.id,{groupFlow:{...s.groupFlow,makeAdmin:true,step:"permissions"}}); await askPermissions(ctx); });
bot.action("gf_admin_no",  async (ctx) => { await ctx.answerCbQuery(); const s=getSession(ctx.from.id); updateSession(ctx.from.id,{groupFlow:{...s.groupFlow,makeAdmin:false,step:"permissions"}}); await askPermissions(ctx); });

function permKb(p) {
  return Markup.inlineKeyboard([
    [Markup.button.callback(`💬 Send: ${p.sendMessages?"✅ All":"👑 Admins"}`,    "gf_pt_sendMessages")],
    [Markup.button.callback(`✏️ Edit Info: ${p.editInfo?"✅ All":"👑 Admins"}`,   "gf_pt_editInfo")],
    [Markup.button.callback(`➕ Add Members: ${p.addMembers?"✅ All":"👑 Admins"}`, "gf_pt_addMembers")],
    [Markup.button.callback(`🔐 Approve Join: ${p.approveMembers?"✅ On":"❌ Off"}`, "gf_pt_approveMembers")],
    [Markup.button.callback("💾 Save & Continue","gf_perm_save")],
    [Markup.button.callback("❌ Cancel","back_menu")],
  ]);
}
async function askPermissions(ctx) {
  const p = getSession(ctx.from.id).groupFlow.permissions;
  await sendClean(ctx, `📋 *Create Groups — Step 9 of 9*\n━━━━━━━━━━━━━━━━━━\n\n*Set Permissions:*\n\n_Tap to toggle. Save when done._`,
    { parse_mode: "Markdown", ...permKb(p) });
}
["sendMessages","editInfo","addMembers","approveMembers"].forEach((key) => {
  bot.action(`gf_pt_${key}`, async (ctx) => {
    await ctx.answerCbQuery();
    const s=getSession(ctx.from.id), p={...s.groupFlow.permissions,[key]:!s.groupFlow.permissions[key]};
    updateSession(ctx.from.id,{groupFlow:{...s.groupFlow,permissions:p}});
    try { await ctx.editMessageReplyMarkup(permKb(p).reply_markup); } catch { await askPermissions(ctx); }
  });
});
bot.action("gf_perm_save", async (ctx) => { await ctx.answerCbQuery("✅ Saved!"); const s=getSession(ctx.from.id); updateSession(ctx.from.id,{groupFlow:{...s.groupFlow,step:"confirm"}}); await showConfirm(ctx); });

function fmtDis(s) { return !s?"Off":s===86400?"24h":s===604800?"7 Days":s===7776000?"90 Days":`${s}s`; }

async function showConfirm(ctx) {
  const flow=getSession(ctx.from.id).groupFlow, p=flow.permissions;
  const prev=flow.numbering?Array.from({length:Math.min(flow.count,3)},(_,i)=>`${flow.name} ${i+1}`).join(", ")+(flow.count>3?` ...(${flow.count})`:""):`${flow.name} ×${flow.count}`;
  await sendClean(ctx,
    `✅ *Review Settings*\n━━━━━━━━━━━━━━━━━━\n` +
    `📝 *${flow.name}* | 🔢 ${flow.count} groups | Numbering: ${flow.numbering?"On":"Off"}\n` +
    `📋 _${prev}_\n` +
    `📄 Desc: ${flow.description?`_${flow.description.slice(0,40)}_`:"None"}\n` +
    `🖼️ Photo: ${flow.photo?"✅ Set":"None"} | ⏳ Disappearing: ${fmtDis(flow.disappearing)}\n` +
    `👥 Members: ${flow.members.length||"None"} | 👑 Admin: ${flow.members.length?(flow.makeAdmin?"Yes":"No"):"N/A"}\n` +
    `━━━━━━━━━━━━━━━━━━\n*Permissions:*\n` +
    `💬 ${p.sendMessages?"All":"Admins"} | ✏️ ${p.editInfo?"All":"Admins"} | ➕ ${p.addMembers?"All":"Admins"} | 🔐 ${p.approveMembers?"On":"Off"}\n` +
    `━━━━━━━━━━━━━━━━━━\n_All correct? Press Create Now._`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("✏️ Edit","gf_edit_menu")],
      [Markup.button.callback("🚀 Create Now","gf_create_now")],
      [Markup.button.callback("❌ Cancel","back_menu")],
    ]) }
  );
}

bot.action("gf_edit_menu", async (ctx) => {
  await ctx.answerCbQuery();
  await sendClean(ctx, `✏️ *What to edit?*`, { parse_mode: "Markdown", ...Markup.inlineKeyboard([
    [Markup.button.callback("📝 Name","ge_name"),        Markup.button.callback("🔢 Count","ge_count")],
    [Markup.button.callback("🔢 Numbering","ge_numbering"), Markup.button.callback("📄 Description","ge_desc")],
    [Markup.button.callback("🖼️ Photo","ge_photo"),      Markup.button.callback("⏳ Disappearing","ge_disappearing")],
    [Markup.button.callback("👥 Members","ge_members"),  Markup.button.callback("🔐 Permissions","ge_perms")],
    [Markup.button.callback("🔙 Back to Summary","gf_back_confirm")],
  ]) });
});
bot.action("gf_back_confirm", async (ctx) => { await ctx.answerCbQuery(); await showConfirm(ctx); });
bot.action("ge_name",   async (ctx) => { await ctx.answerCbQuery(); updateSession(ctx.from.id,{groupFlow:{...getSession(ctx.from.id).groupFlow,step:"name_edit"}}); await sendClean(ctx,`📝 *New group name:*`,{parse_mode:"Markdown",...Markup.inlineKeyboard([[Markup.button.callback("🔙 Cancel","gf_back_confirm")]])}); });
bot.action("ge_count",  async (ctx) => { await ctx.answerCbQuery(); updateSession(ctx.from.id,{groupFlow:{...getSession(ctx.from.id).groupFlow,step:"count_edit"}}); await sendClean(ctx,`🔢 *How many groups? (1–50):*`,{parse_mode:"Markdown",...Markup.inlineKeyboard([[Markup.button.callback("🔙 Cancel","gf_back_confirm")]])}); });
bot.action("ge_numbering", async (ctx) => { await ctx.answerCbQuery(); await sendClean(ctx,`🔢 *Numbering?*`,{parse_mode:"Markdown",...Markup.inlineKeyboard([[Markup.button.callback("✅ Yes","ge_num_yes"),Markup.button.callback("❌ No","ge_num_no")],[Markup.button.callback("🔙 Cancel","gf_back_confirm")]])}); });
bot.action("ge_num_yes", async (ctx) => { await ctx.answerCbQuery(); updateSession(ctx.from.id,{groupFlow:{...getSession(ctx.from.id).groupFlow,numbering:true,step:"confirm"}}); await showConfirm(ctx); });
bot.action("ge_num_no",  async (ctx) => { await ctx.answerCbQuery(); updateSession(ctx.from.id,{groupFlow:{...getSession(ctx.from.id).groupFlow,numbering:false,step:"confirm"}}); await showConfirm(ctx); });
bot.action("ge_desc",   async (ctx) => { await ctx.answerCbQuery(); updateSession(ctx.from.id,{groupFlow:{...getSession(ctx.from.id).groupFlow,step:"description_edit"}}); await sendClean(ctx,`📄 *New description:*`,{parse_mode:"Markdown",...Markup.inlineKeyboard([[Markup.button.callback("⏭️ Remove","ge_desc_rm")],[Markup.button.callback("🔙 Cancel","gf_back_confirm")]])}); });
bot.action("ge_desc_rm", async (ctx) => { await ctx.answerCbQuery(); updateSession(ctx.from.id,{groupFlow:{...getSession(ctx.from.id).groupFlow,description:"",step:"confirm"}}); await showConfirm(ctx); });
bot.action("ge_photo",  async (ctx) => { await ctx.answerCbQuery(); updateSession(ctx.from.id,{groupFlow:{...getSession(ctx.from.id).groupFlow,step:"photo_edit"}}); await sendClean(ctx,`🖼️ *Send new photo:*`,{parse_mode:"Markdown",...Markup.inlineKeyboard([[Markup.button.callback("⏭️ Remove","ge_photo_rm")],[Markup.button.callback("🔙 Cancel","gf_back_confirm")]])}); });
bot.action("ge_photo_rm", async (ctx) => { await ctx.answerCbQuery(); updateSession(ctx.from.id,{groupFlow:{...getSession(ctx.from.id).groupFlow,photo:null,step:"confirm"}}); await showConfirm(ctx); });
bot.action("ge_disappearing", async (ctx) => { await ctx.answerCbQuery(); updateSession(ctx.from.id,{groupFlow:{...getSession(ctx.from.id).groupFlow,step:"disappearing_edit"}}); await sendClean(ctx,`⏳ *Set Disappearing:*`,{parse_mode:"Markdown",...Markup.inlineKeyboard([[Markup.button.callback("⏱ 24h","ge_dis_86400"),Markup.button.callback("📅 7d","ge_dis_604800"),Markup.button.callback("🗓 90d","ge_dis_7776000")],[Markup.button.callback("⏭️ Off","ge_dis_0")],[Markup.button.callback("🔙 Cancel","gf_back_confirm")]])}); });
[0,86400,604800,7776000].forEach((s) => { bot.action(`ge_dis_${s}`, async (ctx) => { await ctx.answerCbQuery(); updateSession(ctx.from.id,{groupFlow:{...getSession(ctx.from.id).groupFlow,disappearing:s,step:"confirm"}}); await showConfirm(ctx); }); });
bot.action("ge_members", async (ctx) => { await ctx.answerCbQuery(); updateSession(ctx.from.id,{groupFlow:{...getSession(ctx.from.id).groupFlow,step:"members_edit"}}); await sendClean(ctx,`👥 *New member numbers (one per line):*`,{parse_mode:"Markdown",...Markup.inlineKeyboard([[Markup.button.callback("⏭️ Remove All","ge_mem_rm")],[Markup.button.callback("🔙 Cancel","gf_back_confirm")]])}); });
bot.action("ge_mem_rm",  async (ctx) => { await ctx.answerCbQuery(); updateSession(ctx.from.id,{groupFlow:{...getSession(ctx.from.id).groupFlow,members:[],makeAdmin:false,step:"confirm"}}); await showConfirm(ctx); });
bot.action("ge_perms",   async (ctx) => { await ctx.answerCbQuery(); updateSession(ctx.from.id,{groupFlow:{...getSession(ctx.from.id).groupFlow,step:"permissions_edit"}}); await askPermissions(ctx); });

bot.action("gf_create_now", async (ctx) => {
  await ctx.answerCbQuery("🚀 Starting...");
  const uid=ctx.from.id, flow=getSession(uid).groupFlow;
  if (!flow?.name||!flow?.count) { await sendClean(ctx,"⚠️ Settings incomplete.",Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu","back_menu")]])); return; }
  if (getStatus(0)!=="connected") { await sendClean(ctx,"❌ WhatsApp not connected!",Markup.inlineKeyboard([[Markup.button.callback("📱 Connect","menu_account")]])); return; }

  const jids=flow.members.map((n)=>`${n.replace(/[^0-9]/g,"")}@s.whatsapp.net`);
  startTimes.set(uid,Date.now()); updateSession(uid,{cancelPending:false});
  const pm=await ctx.reply(`🚀 *Creating ${flow.count} group(s)...*\n━━━━━━━━━━━━━━━━━━\n⏳ Starting...`,{parse_mode:"Markdown"});
  updateSession(uid,{lastMsgId:pm.message_id});
  await showCancelBtn(ctx);
  const created=[], failed=[];
  let cancelled=false;

  for (let i=0;i<flow.count;i++) {
    if (isCancelled(uid)) { cancelled=true; break; }
    const gname=flow.numbering?`${flow.name} ${i+1}`:flow.name;
    try {
      await bot.telegram.editMessageText(ctx.chat.id,pm.message_id,undefined,`🚀 *Creating Groups...*\n━━━━━━━━━━━━━━━━━━\n✅ Done: ${i}/${flow.count}\n⚙️ ${gname}\n${bar(i,flow.count)}`,{parse_mode:"Markdown"});
    } catch {}
    try {
      const r=await createGroup(0,gname,jids); const gid=r.id; await sleep(1500);
      if (flow.description)  { await updateGroupDescription(0,gid,flow.description).catch(()=>{});  await sleep(400); }
      if (flow.photo)        { await updateGroupPhoto(0,gid,flow.photo).catch(()=>{});               await sleep(400); }
      if (flow.disappearing) { await setDisappearingMessages(0,gid,flow.disappearing).catch(()=>{}); await sleep(400); }
      if (flow.makeAdmin&&jids.length) { await promoteToAdmin(0,gid,jids).catch(()=>{});             await sleep(400); }
      await setGroupPermissions(0,gid,flow.permissions).catch(()=>{});
      let link=""; try { link=await getGroupInviteLink(0,gid); } catch { link="(unavailable)"; }
      created.push({name:gname,link});
    } catch (err) { console.error("[CreateGroup]",err.message); failed.push(gname); }
    await sleep(2000);
  }

  await removeCancelBtn(ctx);
  try { await bot.telegram.editMessageText(ctx.chat.id,pm.message_id,undefined,`✅ *Done!* Created: ${created.length} | Failed: ${failed.length}`,{parse_mode:"Markdown"}); } catch {}
  for (let c=0;c<created.length;c+=20) {
    await ctx.reply(`📋 *Created (${c+1}–${Math.min(c+20,created.length)} of ${created.length}):*\n\n`+created.slice(c,c+20).map((g,i)=>`*${c+i+1}.* ${g.name}\n${g.link}`).join("\n\n"),{parse_mode:"Markdown"});
    await sleep(300);
  }
  if (failed.length) await ctx.reply(`❌ *Failed:*\n${failed.map((n)=>`• ${n}`).join("\n")}`,{parse_mode:"Markdown"});
  await sendSummary(ctx,{feature:"create_groups",total:flow.count,success:created.length,failed:failed.length,cancelled});
  updateSession(uid,{groupFlow:null}); await sendMainMenu(ctx);
});

[1,5,10,20,50].forEach((n) => {
  bot.action(`gf_count_${n}`, async (ctx) => { await ctx.answerCbQuery(); const s=getSession(ctx.from.id); updateSession(ctx.from.id,{groupFlow:{...s.groupFlow,count:n,step:"numbering"}}); await askNumbering(ctx); });
});

// ══════════════════════════════════════════════════════════════════════════
// ─── ADD MEMBERS — VCF MODE ACTIONS ───────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

bot.action("am_mode_onebyone", async (ctx) => {
  await ctx.answerCbQuery();
  const flow = getSession(ctx.from.id).featureFlow;
  updateSession(ctx.from.id, { featureFlow: { ...flow, addMode: "onebyone", step: "am_awaiting_vcf" } });
  await askNextVcf(ctx);
});

bot.action("am_mode_bulk", async (ctx) => {
  await ctx.answerCbQuery();
  const flow = getSession(ctx.from.id).featureFlow;
  updateSession(ctx.from.id, { featureFlow: { ...flow, addMode: "bulk", step: "am_awaiting_vcf" } });
  await askNextVcf(ctx);
});

async function askNextVcf(ctx) {
  const flow = getSession(ctx.from.id).featureFlow;
  const idx = flow.currentVcfIdx || 0;
  const total = (flow.links || []).length;
  if (idx >= total) {
    await runAddMembersFromVcfs(ctx);
    return;
  }
  const code = flow.links[idx];
  updateSession(ctx.from.id, { awaitingVcf: { feature: "add_members", step: "am_vcf", linkIdx: idx } });
  await sendClean(ctx,
    `➕ *Add Members — VCF ${idx + 1}/${total}*\n━━━━━━━━━━━━━━━━━━\n\n📎 Send VCF file for group link ${idx + 1}:\n\`https://chat.whatsapp.com/${code}\`\n\n_Numbers in this VCF will be added to that group._`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("⏭️ Skip This Group", "am_skip_vcf")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
  );
}

bot.action("am_skip_vcf", async (ctx) => {
  await ctx.answerCbQuery("Skipped");
  const uid = ctx.from.id;
  const flow = getSession(uid).featureFlow;
  updateSession(uid, { featureFlow: { ...flow, currentVcfIdx: (flow.currentVcfIdx || 0) + 1, vcfs: [...(flow.vcfs || []), null] }, awaitingVcf: null });
  await askNextVcf(ctx);
});

async function runAddMembersFromVcfs(ctx) {
  const uid = ctx.from.id;
  const flow = getSession(uid).featureFlow;
  const links = flow.links || [];
  const vcfs  = flow.vcfs  || [];
  const total = links.length;
  startTimes.set(uid, Date.now());
  updateSession(uid, { cancelPending: false, awaitingVcf: null });

  const pm = await ctx.reply(`➕ *Adding Members to ${total} group(s)...*\n━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`, { parse_mode: "Markdown" });
  updateSession(uid, { lastMsgId: pm.message_id });
  await showCancelBtn(ctx);

  let doneGroups = 0, failedGroups = 0, totalAdded = 0, totalFailed = 0, totalSkipped = 0, cancelled = false;
  const summaryLines = [];

  for (let i = 0; i < total; i++) {
    if (isCancelled(uid)) { cancelled = true; break; }
    const code = links[i];
    const contacts = vcfs[i];
    if (!contacts || !contacts.length) { summaryLines.push(`⏭️ Group ${i + 1}: skipped (no VCF)`); continue; }

    try {
      await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
        `➕ *Adding Members...*\n━━━━━━━━━━━━━━━━━━\n✅ Groups: ${doneGroups}/${total}\n⚙️ Group ${i + 1} — ${contacts.length} numbers\n${bar(i, total)}`,
        { parse_mode: "Markdown" });
    } catch {}

    try {
      const info = await getGroupInfoFromLink(0, code);
      if (!info) throw new Error("Invalid/expired link");
      const phones = contacts.map((c) => c.phone);
      const result = await addMembersToGroup(0, info.id, phones, flow.addMode === "onebyone");
      totalAdded += result.added; totalFailed += result.failed; totalSkipped += result.skipped;
      doneGroups++;
      summaryLines.push(`✅ *${info.name}*\n  ➕ Added: ${result.added} | ❌ Failed: ${result.failed} | ⏭️ Skipped: ${result.skipped}`);
      if (result.failedNums.length) {
        summaryLines.push(`  ❌ Could not add: ${result.failedNums.slice(0, 10).join(", ")}${result.failedNums.length > 10 ? ` +${result.failedNums.length - 10} more` : ""}`);
      }
    } catch (err) { failedGroups++; summaryLines.push(`❌ Group ${i + 1}: ${err.message}`); }
    await sleep(2000);
  }

  await removeCancelBtn(ctx);
  try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
    `✅ *Add Members Done!* ${doneGroups}/${total} groups\n➕ Added: ${totalAdded} | ❌ Failed: ${totalFailed} | ⏭️ Skipped: ${totalSkipped}`,
    { parse_mode: "Markdown" }); } catch {}

  for (let c = 0; c < summaryLines.length; c += 15) {
    await ctx.reply(summaryLines.slice(c, c + 15).join("\n"), { parse_mode: "Markdown" });
    await sleep(300);
  }
  await sendSummary(ctx, { feature: "add_members", total, success: doneGroups, failed: failedGroups, cancelled,
    extra: [`➕ *Total Added: ${totalAdded}*`, `❌ *Total Failed: ${totalFailed}*`, `⏭️ *Skipped (privacy): ${totalSkipped}*`] });
  updateSession(uid, { featureFlow: null, awaitingVcf: null }); await sendMainMenu(ctx);
}

// ══════════════════════════════════════════════════════════════════════════
// ─── TEXT HANDLER ─────────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

bot.on("text", async (ctx) => {
  const uid = ctx.from.id, s = getSession(uid), text = ctx.message.text.trim();
  if (text.startsWith("/")) return;
  try { await ctx.deleteMessage(); } catch {}

  // WA phone input
  if (s.awaitingPhoneForIndex !== null && s.awaitingPhoneForIndex !== undefined) {
    const phone = text.replace(/[^0-9]/g, "");
    if (phone.length < 10) { await sendClean(ctx, `❌ Invalid. Example: \`919876543210\``, {parse_mode:"Markdown",...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu","back_menu")]])}); return; }
    updateSession(uid, { awaitingPhoneForIndex: null });
    const wm = await ctx.reply(`⏳ *Generating pairing code...*`, { parse_mode: "Markdown" });
    updateSession(uid, { lastMsgId: wm.message_id });
    pendingPairingCbs.set(0, async (code) => {
      try { await ctx.telegram.deleteMessage(ctx.chat.id, wm.message_id); } catch {}
      if (!code) { await sendClean(ctx, `❌ *Code failed. Try again.*`, {parse_mode:"Markdown",...Markup.inlineKeyboard([[Markup.button.callback("🔄 Try Again","menu_account")],[Markup.button.callback("🏠 Main Menu","back_menu")]])}); return; }
      await sendClean(ctx,
        `🔑 *Pairing Code*\n━━━━━━━━━━━━━━━━━━\n\n\`${code}\`\n\n━━━━━━━━━━━━━━━━━━\n*How to link:*\n1️⃣ Open WhatsApp\n2️⃣ Settings → Linked Devices → Link a Device\n3️⃣ "Link with phone number"\n4️⃣ Enter code above\n\n⚠️ Valid *60 seconds* only!\n⏳ Waiting for connection...`,
        {parse_mode:"Markdown",...Markup.inlineKeyboard([[Markup.button.callback("🔄 New Code","reset_wa")],[Markup.button.callback("🏠 Main Menu","back_menu")]])}
      );
    });
    pendingReadyCbs.set(0, async () => { await sendMainMenu(ctx); });
    connectAccount(0, phone).catch(async (err) => {
      pendingPairingCbs.delete(0); pendingReadyCbs.delete(0);
      await sendClean(ctx, `❌ Error: \`${err.message}\``, {parse_mode:"Markdown",...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu","back_menu")]])});
    });
    return;
  }

  // Join Groups links
  if (s.joinFlow?.step === "links") {
    const codes = extractCodes(text);
    if (!codes.length) { await sendClean(ctx, `❌ *No valid links found.*\nFormat: \`https://chat.whatsapp.com/XXXXX\``, {parse_mode:"Markdown",...Markup.inlineKeyboard([[Markup.button.callback("🔙 Try Again","join_groups_start")],[Markup.button.callback("🏠 Main Menu","back_menu")]])}); return; }
    updateSession(uid, { joinFlow: null });
    startTimes.set(uid, Date.now());
    const pm = await ctx.reply(`🔗 *Joining ${codes.length} group(s)...*\n━━━━━━━━━━━━━━━━━━\n${bar(0, codes.length)}`, { parse_mode: "Markdown" });
    updateSession(uid, { lastMsgId: pm.message_id });
    await showCancelBtn(ctx);
    let joined = 0, failed = 0, failedLinks = [], cancelled = false;
    for (let i = 0; i < codes.length; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined, `🔗 *Joining...*\n━━━━━━━━━━━━━━━━━━\n✅ ${joined} | ❌ ${failed}\n⚙️ Group ${i+1}/${codes.length}\n${bar(i,codes.length)}`, {parse_mode:"Markdown"}); } catch {}
      try { await joinGroupViaLink(0, codes[i]); joined++; }
      catch { failed++; failedLinks.push(`https://chat.whatsapp.com/${codes[i]}`); }
      await sleep(2000);
    }
    await removeCancelBtn(ctx);
    try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined, `✅ *Done!* Joined: ${joined} | Failed: ${failed}`, {parse_mode:"Markdown"}); } catch {}
    if (failedLinks.length) await ctx.reply(`❌ *Could not join:*\n${failedLinks.join("\n")}`, {parse_mode:"Markdown"});
    await sendSummary(ctx, {feature:"join_groups",total:codes.length,success:joined,failed,cancelled});
    await sendMainMenu(ctx); return;
  }

  // Similar Groups — custom keyword search
  if (s.featureFlow?.step === "similar_query") {
    const kw = text.toLowerCase();
    try {
      const all = s.featureFlow.allGroups.length ? s.featureFlow.allGroups : await getAllGroupsWithDetails(0);
      const filtered = all.filter((g) => g.name.toLowerCase().includes(kw));
      if (!filtered.length) { await sendClean(ctx, `❌ No groups match "*${text}*"`, {parse_mode:"Markdown",...Markup.inlineKeyboard([[Markup.button.callback("🔙 Try Again","gs_sim_custom")],[Markup.button.callback("🏠 Main Menu","back_menu")]])}); return; }
      updateSession(uid, { featureFlow: { ...s.featureFlow, allGroups: all, selectedIds: filtered.map((g) => g.id), keyword: kw, step: "confirm" } });
      await sendClean(ctx,
        `✅ *Found ${filtered.length} matching group(s):*\n\n${filtered.slice(0, 15).map((g, i) => `${i + 1}. ${g.name}`).join("\n")}${filtered.length > 15 ? `\n_...and ${filtered.length - 15} more_` : ""}`,
        {parse_mode:"Markdown",...Markup.inlineKeyboard([[Markup.button.callback("🚀 Proceed","gs_sim_proceed")],[Markup.button.callback("🏠 Main Menu","back_menu")]]) }
      );
    } catch (err) { await sendClean(ctx, `❌ Error: ${err.message}`, Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu","back_menu")]])); }
    return;
  }

  // Make Admin numbers
  if (s.featureFlow?.step === "admin_numbers") {
    const nums = text.split(/[\n,\s]+/).map((n) => n.replace(/[^0-9]/g, "")).filter((n) => n.length >= 10);
    if (!nums.length) { await ctx.reply("⚠️ No valid numbers. Include country code."); return; }
    const flow = s.featureFlow;
    updateSession(uid, { featureFlow: { ...flow, adminNumbers: nums, step: "executing" } });
    await runFeature(ctx, flow.feature, flow.selectedIds, flow.allGroups, nums);
    return;
  }

  // Demote Admin numbers
  if (s.featureFlow?.step === "demote_numbers") {
    const nums = text.split(/[\n,\s]+/).map((n) => n.replace(/[^0-9]/g, "")).filter((n) => n.length >= 10);
    if (!nums.length) { await ctx.reply("⚠️ No valid numbers. Include country code."); return; }
    const flow = s.featureFlow;
    updateSession(uid, { featureFlow: { ...flow, adminNumbers: nums, step: "executing" } });
    await runFeature(ctx, "demote_admin", flow.selectedIds, flow.allGroups, nums);
    return;
  }

  // Auto Accept — custom duration (minutes)
  if (s.featureFlow?.step === "aa_custom_duration") {
    const mins = parseInt(text, 10);
    if (isNaN(mins) || mins < 1) { await ctx.reply("⚠️ Enter a valid number of minutes (e.g. 120)."); return; }
    const secs = mins * 60;
    const flow = s.featureFlow;
    updateSession(uid, { featureFlow: { ...flow, aaDuration: secs, step: "aa_confirm" } });
    const label = mins >= 60 ? `${Math.round(mins / 60 * 10) / 10}h` : `${mins}min`;
    await sendClean(ctx,
      `⏰ *Auto Accept — Confirm*\n━━━━━━━━━━━━━━━━━━\n📁 Groups: *${flow.selectedIds.length}*\n⏱ Duration: *${label}*`,
      { parse_mode: "Markdown", ...Markup.inlineKeyboard([
        [Markup.button.callback("✅ Start Auto Accept", "aa_start")],
        [Markup.button.callback("🔙 Change Duration", "aa_back_duration")],
        [Markup.button.callback("🏠 Main Menu", "back_menu")],
      ]) }
    );
    return;
  }

  // Add Members — links input
  if (s.featureFlow?.step === "am_links") {
    const codes = extractCodes(text);
    if (!codes.length) { await ctx.reply("❌ No valid WhatsApp links found. Send links like:\n`https://chat.whatsapp.com/ABC`", {parse_mode:"Markdown"}); return; }
    const flow = s.featureFlow;
    updateSession(uid, { featureFlow: { ...flow, links: codes, currentVcfIdx: 0, vcfs: [], step: "am_mode" } });
    await sendClean(ctx,
      `➕ *Add Members — ${codes.length} group(s) detected*\n━━━━━━━━━━━━━━━━━━\n\n*How to add members?*`,
      { parse_mode: "Markdown", ...Markup.inlineKeyboard([
        [Markup.button.callback("🔢 1-by-1 (Safer, Slower)",  "am_mode_onebyone")],
        [Markup.button.callback("⚡ Bulk (Faster)",            "am_mode_bulk")],
        [Markup.button.callback("🏠 Main Menu", "back_menu")],
      ]) }
    );
    return;
  }

  // Change Name — random: base name input
  if (s.featureFlow?.step === "cn_random_name") {
    const name = text.slice(0, 100);
    const flow = s.featureFlow;
    updateSession(uid, { featureFlow: { ...flow, cnBaseName: name, step: "cn_random_numbering" } });
    await sendClean(ctx,
      `✏️ *Change Name — Randomly*\n━━━━━━━━━━━━━━━━━━\n\n✅ Name: *${name}*\n\n*Add numbering?*\n_Yes → ${name} 1, ${name} 2..._\n_No → All groups get same name_`,
      { parse_mode: "Markdown", ...Markup.inlineKeyboard([
        [Markup.button.callback("✅ Yes — add numbers", "cn_numbering_yes"), Markup.button.callback("❌ No", "cn_numbering_no")],
        [Markup.button.callback("🏠 Main Menu", "back_menu")],
      ]) }
    );
    return;
  }

  // Change Name — random: links input
  if (s.featureFlow?.step === "cn_random_links") {
    const codes = extractCodes(text);
    if (!codes.length) { await ctx.reply("❌ No valid WhatsApp links found."); return; }
    const flow = s.featureFlow;
    await runChangeNameRandom(ctx, codes, flow.cnBaseName, flow.numbering !== false);
    return;
  }

  // Change Name — VCF: links input
  if (s.featureFlow?.step === "cn_vcf_links") {
    const codes = extractCodes(text);
    if (!codes.length) { await ctx.reply("❌ No valid WhatsApp links found."); return; }
    const flow = s.featureFlow;
    updateSession(uid, { featureFlow: { ...flow, links: codes, currentVcfIdx: 0, vcfs: [], step: "cn_vcf_awaiting" },
      awaitingVcf: { feature: "change_name", step: "cn_vcf" } });
    await sendClean(ctx,
      `📛 *Change Name as VCF — ${codes.length} links received*\n━━━━━━━━━━━━━━━━━━\n\nNow send *all VCF files* one by one.\n\n📎 Send VCF file 1/${codes.length}:`,
      { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
    );
    return;
  }

  // CTC Checker — links input
  if (s.featureFlow?.step === "ctc_links") {
    const codes = extractCodes(text);
    if (!codes.length) { await ctx.reply("❌ No valid WhatsApp links found."); return; }
    const flow = s.featureFlow;
    updateSession(uid, { featureFlow: { ...flow, links: codes, step: "ctc_vcf" },
      awaitingVcf: { feature: "ctc_checker", step: "ctc_vcf" } });
    await sendClean(ctx,
      `🔍 *CTC Checker — ${codes.length} links received*\n━━━━━━━━━━━━━━━━━━\n\nNow send a *VCF file* with your known/trusted contacts:`,
      { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
    );
    return;
  }

  // Create Groups text steps
  const flow = s.groupFlow;
  if (!flow) { await sendMainMenu(ctx); return; }

  if (flow.step === "name") {
    const name = text.slice(0, 100); updateSession(uid, { groupFlow: { ...flow, name, step: "count" } });
    await sendClean(ctx, `📋 *Create Groups — Step 2 of 9*\n━━━━━━━━━━━━━━━━━━\n\n✅ Name: *${name}*\n\n*How many groups? (1–50)*`,
      {parse_mode:"Markdown",...Markup.inlineKeyboard([[1,5,10,20,50].map((n)=>Markup.button.callback(`${n}`,`gf_count_${n}`)),[Markup.button.callback("❌ Cancel","back_menu")]]) });
    return;
  }
  if (flow.step === "name_edit")  { updateSession(uid,{groupFlow:{...flow,name:text.slice(0,100),step:"confirm"}}); await showConfirm(ctx); return; }
  if (flow.step==="count"||flow.step==="count_edit") {
    const n=parseInt(text,10); if (isNaN(n)||n<1||n>50) { await ctx.reply("⚠️ Enter a number 1–50."); return; }
    if (flow.step==="count_edit") { updateSession(uid,{groupFlow:{...flow,count:n,step:"confirm"}}); await showConfirm(ctx); }
    else { updateSession(uid,{groupFlow:{...flow,count:n,step:"numbering"}}); await askNumbering(ctx); }
    return;
  }
  if (flow.step === "description")      { updateSession(uid,{groupFlow:{...flow,description:text.slice(0,512),step:"photo"}}); await askPhoto(ctx); return; }
  if (flow.step === "description_edit") { updateSession(uid,{groupFlow:{...flow,description:text.slice(0,512),step:"confirm"}}); await showConfirm(ctx); return; }
  if (flow.step==="members"||flow.step==="members_edit") {
    const nums=text.split(/[\n,\s]+/).map((n)=>n.replace(/[^0-9]/g,"")).filter((n)=>n.length>=10);
    if (!nums.length) { await ctx.reply("⚠️ No valid numbers found."); return; }
    if (flow.step==="members_edit") { updateSession(uid,{groupFlow:{...flow,members:nums,step:"confirm"}}); await showConfirm(ctx); }
    else { updateSession(uid,{groupFlow:{...flow,members:nums,step:"admin"}}); await askAdmin(ctx); }
    return;
  }
  await sendMainMenu(ctx);
});

// ══════════════════════════════════════════════════════════════════════════
// ─── DOCUMENT HANDLER (VCF Files) ─────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

bot.on("document", async (ctx) => {
  const uid = ctx.from.id, s = getSession(uid);
  const doc = ctx.message.document;
  try { await ctx.deleteMessage(); } catch {}

  const isVcf = doc.mime_type === "text/vcard" || doc.mime_type === "text/x-vcard" ||
    doc.file_name?.toLowerCase().endsWith(".vcf");

  const awaitingVcf = s.awaitingVcf;

  // ── Add Members VCF ────────────────────────────────────────────────
  if (awaitingVcf?.feature === "add_members" && s.featureFlow?.step === "am_awaiting_vcf") {
    if (!isVcf) { await ctx.reply("⚠️ Please send a .vcf file."); return; }
    try {
      const buf = await downloadFile(ctx, doc.file_id);
      const contacts = parseVcf(buf.toString("utf8"));
      const flow = s.featureFlow;
      const idx = flow.currentVcfIdx || 0;
      const newVcfs = [...(flow.vcfs || [])];
      newVcfs[idx] = contacts;
      const nextIdx = idx + 1;
      updateSession(uid, {
        featureFlow: { ...flow, vcfs: newVcfs, currentVcfIdx: nextIdx },
        awaitingVcf: null,
      });
      await ctx.reply(`✅ *VCF ${idx + 1} received!* ${contacts.length} numbers found.`, { parse_mode: "Markdown" });
      await askNextVcf(ctx);
    } catch (err) { await ctx.reply(`❌ Error reading VCF: ${err.message}`); }
    return;
  }

  // ── Change Name as VCF ─────────────────────────────────────────────
  if (awaitingVcf?.feature === "change_name" && s.featureFlow?.step === "cn_vcf_awaiting") {
    if (!isVcf) { await ctx.reply("⚠️ Please send a .vcf file."); return; }
    try {
      const buf = await downloadFile(ctx, doc.file_id);
      const contacts = parseVcf(buf.toString("utf8"));
      const vcfName = (doc.file_name || "group").replace(/\.vcf$/i, "").trim();
      const flow = s.featureFlow;
      const idx = flow.currentVcfIdx || 0;
      const newVcfs = [...(flow.vcfs || [])];
      newVcfs[idx] = { name: vcfName, contacts };
      const nextIdx = idx + 1;
      const totalLinks = (flow.links || []).length;

      updateSession(uid, { featureFlow: { ...flow, vcfs: newVcfs, currentVcfIdx: nextIdx } });
      await ctx.reply(`✅ *VCF "${vcfName}" received!* ${contacts.length} numbers.`, { parse_mode: "Markdown" });

      if (nextIdx >= totalLinks) {
        // All VCFs received — run rename
        updateSession(uid, { awaitingVcf: null });
        await runChangeNameAsVcf(ctx);
      } else {
        await ctx.reply(`📎 *Send VCF file ${nextIdx + 1}/${totalLinks}:*`, { parse_mode: "Markdown",
          ...Markup.inlineKeyboard([[Markup.button.callback("⏭️ Skip", "cn_vcf_skip_next")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]) });
      }
    } catch (err) { await ctx.reply(`❌ Error reading VCF: ${err.message}`); }
    return;
  }

  // ── CTC Checker VCF ───────────────────────────────────────────────
  if (awaitingVcf?.feature === "ctc_checker" && s.featureFlow?.step === "ctc_vcf") {
    if (!isVcf) { await ctx.reply("⚠️ Please send a .vcf file."); return; }
    try {
      const buf = await downloadFile(ctx, doc.file_id);
      const contacts = parseVcf(buf.toString("utf8"));
      updateSession(uid, { featureFlow: { ...s.featureFlow, vcfContacts: contacts, step: "ctc_running" }, awaitingVcf: null });
      await ctx.reply(`✅ *VCF received!* ${contacts.length} trusted numbers.\n\n⏳ Checking pending requests...`, { parse_mode: "Markdown" });
      await runCtcChecker(ctx);
    } catch (err) { await ctx.reply(`❌ Error reading VCF: ${err.message}`); }
    return;
  }
});

// ─── Change Name as VCF — skip next VCF ──────────────────────────────────
bot.action("cn_vcf_skip_next", async (ctx) => {
  await ctx.answerCbQuery("Skipped");
  const uid = ctx.from.id;
  const flow = getSession(uid).featureFlow;
  const idx = flow.currentVcfIdx || 0;
  const newVcfs = [...(flow.vcfs || [])];
  newVcfs[idx] = null;
  const nextIdx = idx + 1;
  const totalLinks = (flow.links || []).length;
  updateSession(uid, { featureFlow: { ...flow, vcfs: newVcfs, currentVcfIdx: nextIdx } });
  if (nextIdx >= totalLinks) {
    updateSession(uid, { awaitingVcf: null });
    await runChangeNameAsVcf(ctx);
  } else {
    await ctx.reply(`📎 *Send VCF file ${nextIdx + 1}/${totalLinks}:*`, { parse_mode: "Markdown",
      ...Markup.inlineKeyboard([[Markup.button.callback("⏭️ Skip", "cn_vcf_skip_next")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]) });
  }
});

// ─── Change Name as VCF — execution ──────────────────────────────────────
async function runChangeNameAsVcf(ctx) {
  const uid = ctx.from.id;
  const flow = getSession(uid).featureFlow;
  const links = flow.links || [];
  const vcfs  = flow.vcfs  || [];
  const total = links.length;
  startTimes.set(uid, Date.now());

  const pm = await ctx.reply(`📛 *Renaming ${total} group(s) by VCF...*\n━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`, { parse_mode: "Markdown" });
  updateSession(uid, { lastMsgId: pm.message_id });
  await showCancelBtn(ctx);

  let done = 0, failed = 0, skipped = 0, cancelled = false;
  const details = [];

  for (let i = 0; i < total; i++) {
    if (isCancelled(uid)) { cancelled = true; break; }
    const code = links[i];
    const vcfEntry = vcfs[i];
    if (!vcfEntry) { skipped++; details.push(`⏭️ Group ${i + 1}: no VCF`); continue; }

    try {
      await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
        `📛 *Renaming...*\n━━━━━━━━━━━━━━━━━━\n✅ Done: ${done}/${total}\n⚙️ → "${vcfEntry.name}"\n${bar(i, total)}`,
        { parse_mode: "Markdown" });
    } catch {}

    try {
      const info = await getGroupInfoFromLink(0, code);
      if (!info) throw new Error("Invalid link");

      // Check if VCF has numbers matching group members or pending
      const vcfPhones = new Set(vcfEntry.contacts.map((c) => c.phone));
      const memberPhones = new Set(info.participants.map((p) => p.id.replace("@s.whatsapp.net", "")));
      const pending = await getPendingForGroup(0, info.id);
      const pendingPhones = new Set(pending.map((p) => p.phone));

      const matches = [...vcfPhones].some((ph) => memberPhones.has(ph) || pendingPhones.has(ph));

      if (matches) {
        await renameGroup(0, info.id, vcfEntry.name);
        done++;
        details.push(`✅ ${info.name} → *${vcfEntry.name}*`);
      } else {
        skipped++;
        details.push(`⏭️ ${info.name} — no match for VCF "${vcfEntry.name}"`);
      }
    } catch (err) { failed++; details.push(`❌ Group ${i + 1}: ${err.message}`); }
    await sleep(1200);
  }

  await removeCancelBtn(ctx);
  try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
    `✅ *Rename Done!* ${done}/${total}`, { parse_mode: "Markdown" }); } catch {}
  for (let c = 0; c < details.length; c += 30)
    await ctx.reply(details.slice(c, c + 30).join("\n"), { parse_mode: "Markdown" });
  await sendSummary(ctx, { feature: "change_name", total, success: done, failed, cancelled,
    extra: [`⏭️ *Skipped (no match): ${skipped}*`] });
  updateSession(uid, { featureFlow: null, awaitingVcf: null }); await sendMainMenu(ctx);
}

// ─── CTC Checker — execution ──────────────────────────────────────────────
async function runCtcChecker(ctx) {
  const uid = ctx.from.id;
  const flow = getSession(uid).featureFlow;
  const links = flow.links || [];
  const trustedPhones = new Set((flow.vcfContacts || []).map((c) => c.phone));
  const total = links.length;
  startTimes.set(uid, Date.now());

  const pm = await ctx.reply(`🔍 *CTC Checker — ${total} group(s)...*\n━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`, { parse_mode: "Markdown" });
  updateSession(uid, { lastMsgId: pm.message_id });
  await showCancelBtn(ctx);

  let done = 0, failed = 0, cancelled = false;
  const reportLines = [], unknownTotal = [];

  for (let i = 0; i < total; i++) {
    if (isCancelled(uid)) { cancelled = true; break; }
    const code = links[i];

    try {
      await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
        `🔍 *Checking Pending Requests...*\n━━━━━━━━━━━━━━━━━━\n✅ Done: ${done}/${total}\n⚙️ Group ${i + 1}/${total}\n${bar(i, total)}`,
        { parse_mode: "Markdown" });
    } catch {}

    try {
      const info = await getGroupInfoFromLink(0, code);
      if (!info) throw new Error("Invalid link");
      const pending = await getPendingForGroup(0, info.id);
      const unknown = pending.filter((p) => !trustedPhones.has(p.phone));
      done++;

      if (unknown.length) {
        unknownTotal.push(...unknown.map((u) => u.phone));
        reportLines.push(`⚠️ *${info.name}* — ${unknown.length} unknown:\n  ${unknown.map((u) => `+${u.phone}`).join(", ")}`);
      } else {
        reportLines.push(`✅ *${info.name}* — ${pending.length} pending, all trusted`);
      }
    } catch (err) { failed++; reportLines.push(`❌ Group ${i + 1}: ${err.message}`); }
    await sleep(800);
  }

  await removeCancelBtn(ctx);
  try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
    `🔍 *CTC Check Complete!* ${done}/${total} groups\n⚠️ Unknown Numbers: ${unknownTotal.length}`,
    { parse_mode: "Markdown" }); } catch {}

  for (let c = 0; c < reportLines.length; c += 15) {
    await ctx.reply(reportLines.slice(c, c + 15).join("\n\n"), { parse_mode: "Markdown" });
    await sleep(300);
  }

  // Final report
  await ctx.reply(
    `📊 *CTC Checker — Final Report*\n━━━━━━━━━━━━━━━━━━\n` +
    `📁 Groups Checked: *${done}*\n` +
    `⚠️ Unknown Numbers Found: *${unknownTotal.length}*\n` +
    (unknownTotal.length ? `\n*Unknown Numbers:*\n${[...new Set(unknownTotal)].map((p) => `+${p}`).join("\n")}` : `\n✅ All pending requests are from trusted numbers!`),
    { parse_mode: "Markdown" }
  );

  await sendSummary(ctx, { feature: "ctc_checker", total, success: done, failed, cancelled,
    extra: [`⚠️ *Total Unknown: ${unknownTotal.length}*`, `✅ *Trusted contacts in VCF: ${trustedPhones.size}*`] });
  updateSession(uid, { featureFlow: null, awaitingVcf: null }); await sendMainMenu(ctx);
}

// ── Photo Handler ─────────────────────────────────────────────────────────
bot.on("photo", async (ctx) => {
  const uid = ctx.from.id, flow = getSession(uid).groupFlow;
  if (!flow || (flow.step !== "photo" && flow.step !== "photo_edit")) return;
  try { await ctx.deleteMessage(); } catch {}
  try {
    const p = ctx.message.photo[ctx.message.photo.length - 1];
    const u = await ctx.telegram.getFileLink(p.file_id);
    const r = await fetch(u.href);
    const buf = Buffer.from(await r.arrayBuffer());
    const ns = flow.step === "photo_edit" ? "confirm" : "disappearing";
    updateSession(uid, { groupFlow: { ...flow, photo: buf, step: ns } });
    if (ns === "confirm") await showConfirm(ctx); else await askDisappearing(ctx);
  } catch (err) { console.error("[Photo]", err.message); await ctx.reply("❌ Could not save photo. Try again."); }
});

bot.catch((err) => console.error("[Bot Error]", err.message));

// ─── Health server ─────────────────────────────────────────────────────────
const app = express(), PORT = process.env.PORT || 3000;
app.get("/", (_, res) => res.send(`<html><body style="font-family:sans-serif;text-align:center;padding:50px;background:#111;color:#fff"><h2>✅ WA Group Creator Bot</h2><p style="color:#4ade80">Running 🟢</p><p>WA: ${getConnectedCount() > 0 ? "Connected ✅" : "Disconnected ❌"}</p></body></html>`));
app.get("/health", (_, res) => res.json({ status: "ok", whatsapp: getStatus(0), phone: getPhone(0) || null, ts: new Date().toISOString() }));
app.listen(PORT, () => console.log(`HTTP server on port ${PORT}`));

function selfPing() {
  const url = process.env.RENDER_EXTERNAL_URL || process.env.SELF_URL; if (!url) return;
  const full = url.startsWith("http") ? url : `https://${url}`;
  (full.startsWith("https") ? https : http).get(`${full}/health`, (r) => console.log(`[Ping] ${r.statusCode}`)).on("error", (e) => console.error("[Ping]", e.message));
}
setTimeout(() => { selfPing(); setInterval(selfPing, 120000); }, 60000);

async function main() {
  await connectDB();
  await reconnectSavedAccounts();
  await bot.launch({ dropPendingUpdates: true });
  console.log(`WA Group Creator Bot running! Owner: ${OWNER_ID || "NOT SET"}`);
}
main().catch((err) => { console.error("Fatal:", err.message); process.exit(1); });
process.once("SIGINT", () => bot.stop("SIGINT"));
process.once("SIGTERM", () => bot.stop("SIGTERM"));
