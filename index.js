/**
 * WhatsApp Group Creator Bot — Final Version
 * Features: Create | Join | Get Links | Leave | Remove Members |
 *           Make Admin | Approval Toggle | Approve Pending |
 *           Member List | Pending List
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
} = require("./src/whatsapp-manager");
const express = require("express");
const http    = require("http");
const https   = require("https");

const TOKEN    = process.env.TELEGRAM_BOT_TOKEN;
if (!TOKEN) { console.error("TELEGRAM_BOT_TOKEN not set!"); process.exit(1); }
const OWNER_ID = parseInt(process.env.OWNER_ID || "0", 10);

const bot       = new Telegraf(TOKEN);
const sleep     = (ms) => new Promise((r) => setTimeout(r, ms));
const PAGE_SIZE = 10;
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
    `📁 Total Groups: *${total}*\n` +
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
    [b("📊 Member List",    "feat_memberlist")],
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
  updateSession(ctx.from.id, { awaitingPhoneForIndex: null, groupFlow: null, joinFlow: null, featureFlow: null, cancelPending: false });
  await sendMainMenu(ctx);
});
bot.action("need_connect", async (ctx) => { await ctx.answerCbQuery("⚠️ Connect WhatsApp first!", { show_alert: true }); });
bot.action("back_menu", async (ctx) => {
  await ctx.answerCbQuery();
  updateSession(ctx.from.id, { awaitingPhoneForIndex: null, groupFlow: null, joinFlow: null, featureFlow: null, cancelPending: false });
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

// ─── Group type: Similar ──────────────────────────────────────────────────
bot.action(/^gs_similar_(.+)$/, async (ctx) => {
  await ctx.answerCbQuery();
  const feature = ctx.match[1];
  const s = getSession(ctx.from.id);
  updateSession(ctx.from.id, { featureFlow: { ...s.featureFlow, feature, step: "similar_query" } });
  await sendClean(ctx,
    `🔍 *Similar Groups*\n━━━━━━━━━━━━━━━━━━\n\nType a keyword to find matching groups:\n\n_e.g._ \`Madara\` _→ finds all groups with "Madara" in name_`,
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

// ─── Group type: Select (paginated) ──────────────────────────────────────
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

// ─── Paginated group selection ────────────────────────────────────────────
async function showPaginatedGroups(ctx) {
  const flow = getSession(ctx.from.id).featureFlow;
  const { allGroups, selectedIds, page } = flow;
  const totalPages = Math.ceil(allGroups.length / PAGE_SIZE);
  const slice      = allGroups.slice(page * PAGE_SIZE, page * PAGE_SIZE + PAGE_SIZE);
  const selSet     = new Set(selectedIds);
  const rows       = [];

  for (let i = 0; i < slice.length; i += 2) {
    const row = [];
    for (let j = i; j < Math.min(i + 2, slice.length); j++) {
      const idx = page * PAGE_SIZE + j, g = slice[j];
      row.push(Markup.button.callback(`${selSet.has(g.id) ? "✅" : "◻️"} ${g.name.slice(0, 16)}`, `gs_tog_${idx}`));
    }
    rows.push(row);
  }

  const nav = [];
  if (page > 0)              nav.push(Markup.button.callback("◀️ Prev", "gs_prev"));
  nav.push(Markup.button.callback(`📄 ${page + 1}/${totalPages}`, "gs_noop"));
  if (page < totalPages - 1) nav.push(Markup.button.callback("▶️ Next", "gs_next"));
  rows.push(nav);
  rows.push([Markup.button.callback(`✅ Confirm (${selSet.size} selected)`, "gs_confirm")]);
  rows.push([Markup.button.callback("🏠 Main Menu", "back_menu")]);

  const text = `☑️ *Select Groups* — Page ${page + 1}/${totalPages}\n━━━━━━━━━━━━━━━━━━\nTotal: *${allGroups.length}* | Selected: *${selSet.size}*\n\n_Tap to select/deselect._`;
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
  if (feature === "make_admin") {
    const s = getSession(ctx.from.id);
    updateSession(ctx.from.id, { featureFlow: { ...s.featureFlow, selectedIds, step: "admin_numbers" } });
    await sendClean(ctx,
      `👑 *Make Admin*\n━━━━━━━━━━━━━━━━━━\n\n*${selectedIds.length} group(s) selected.*\n\nEnter phone number(s) to promote — one per line:\n\`\`\`\n919876543210\n918765432109\n\`\`\``,
      { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
    );
    return;
  }
  await runFeature(ctx, feature, selectedIds, allGroups, []);
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

    let done = 0, failed = 0, totalPending = 0, totalApproved = 0, totalActuallyJoined = 0;
    let cancelled = false;
    const details = [];

    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      try {
        await bot.telegram.editMessageText(
          ctx.chat.id, pm.message_id, undefined,
          `✋ *Approving Pending Members...*\n━━━━━━━━━━━━━━━━━━\n✅ Groups done: ${done}/${total}\n⚙️ ${g.name}\n${bar(i, total)}`,
          { parse_mode: "Markdown" }
        );
      } catch {}

      try {
        const result = await approveAllPending(0, g.id);
        totalPending       += result.pendingCount;
        totalApproved      += result.approved;
        totalActuallyJoined += result.actuallyJoined ?? 0;
        done++;

        const notJoined = result.approved - (result.actuallyJoined ?? result.approved);
        details.push(
          `*${g.name}*\n` +
          `  ⏳ Pending: ${result.pendingCount}\n` +
          `  ✅ Approved: ${result.approved} | ❌ Failed: ${result.failed}\n` +
          `  👥 Members: ${result.beforeCount} → ${result.afterCount}` +
          (notJoined > 0 ? ` ⚠️ (${notJoined} not joined yet)` : "")
        );
      } catch (err) {
        failed++;
        details.push(`*${g.name}* — ❌ Error: ${err.message}`);
      }
      await sleep(2500);
    }

    await removeCancelBtn(ctx);
    try {
      await bot.telegram.editMessageText(
        ctx.chat.id, pm.message_id, undefined,
        `✅ *Approve Pending Done!*\n━━━━━━━━━━━━━━━━━━\n` +
        `✋ Total Pending Found: ${totalPending}\n` +
        `✅ Total Approved: ${totalApproved}\n` +
        `👥 Total Actually Joined: ${totalActuallyJoined}\n` +
        `📁 Groups: ${done}/${total}`,
        { parse_mode: "Markdown" }
      );
    } catch {}

    // Send per-group details
    for (let c = 0; c < details.length; c += 15) {
      await ctx.reply(
        `📋 *Group Details (${c + 1}–${Math.min(c + 15, details.length)} of ${details.length}):*\n━━━━━━━━━━━━━━━━━━\n\n` +
        details.slice(c, c + 15).join("\n\n"),
        { parse_mode: "Markdown" }
      );
      await sleep(300);
    }

    await sendSummary(ctx, {
      feature, total, success: done, failed, cancelled,
      extra: [
        `✋ *Total Pending Found: ${totalPending}*`,
        `✅ *Total Approved: ${totalApproved}*`,
        `👥 *Actually Joined: ${totalActuallyJoined}*`,
        totalPending - totalApproved > 0
          ? `⚠️ *Could not approve: ${totalPending - totalApproved}* (accounts deleted/banned)`
          : ``,
      ].filter(Boolean),
    });
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
        totalMembers += members.length;
        listData.push({ name: g.name, count: members.length, members });
        done++;
      } catch { failed++; listData.push({ name: g.name, count: 0, members: [], error: true }); }
      await sleep(600);
    }

    await removeCancelBtn(ctx);
    const sorted = [...listData].sort((a, b) => b.count - a.count);

    for (let c = 0; c < sorted.length; c += 20) {
      const chunk = sorted.slice(c, c + 20);
      await ctx.reply(
        `📊 *Member Count (${c + 1}–${Math.min(c + 20, sorted.length)} of ${sorted.length}):*\n━━━━━━━━━━━━━━━━━━\n` +
        chunk.map((g, i) => `${c + i + 1}. *${g.name}* — ${g.error ? "❌ Error" : `${g.count} members`}`).join("\n") +
        (c + 20 >= sorted.length ? `\n━━━━━━━━━━━━━━━━━━\n📊 *Total: ${totalMembers} members*` : ""),
        { parse_mode: "Markdown" }
      );
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
        totalPending += pending.length;
        listData.push({ name: g.name, count: pending.length, pending });
        done++;
      } catch { failed++; listData.push({ name: g.name, count: 0, pending: [], error: true }); }
      await sleep(600);
    }

    await removeCancelBtn(ctx);
    await ctx.reply(
      `⏳ *Pending Requests Summary:*\n━━━━━━━━━━━━━━━━━━\n` +
      listData.map((g, i) => `${i + 1}. *${g.name}* — ${g.error ? "❌ Error" : `${g.count} pending`}`).join("\n") +
      `\n━━━━━━━━━━━━━━━━━━\n⏳ *Total Pending: ${totalPending}*`,
      { parse_mode: "Markdown" }
    );
    for (const g of listData) {
      if (g.error || !g.count) continue;
      const lines = g.pending.map((p) => `+${p.phone}`);
      for (let c = 0; c < lines.length; c += 50) {
        await ctx.reply(`⏳ *${g.name}* (${g.count} pending):\n━━━━━━━━━━━━━━━━━━\n` + lines.slice(c, c + 50).join("\n"), { parse_mode: "Markdown" });
        await sleep(300);
      }
    }
    if (!totalPending && !cancelled) await ctx.reply("✅ *No pending join requests found in selected groups.*", { parse_mode: "Markdown" });
    await sendSummary(ctx, { feature, total, success: done, failed, cancelled, extra: [`⏳ *Total Pending Requests: ${totalPending}*`] });
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
    [Markup.button.callback(`💬 Send: ${p.sendMessages?"✅ All":"👑 Admins"}`,   "gf_pt_sendMessages")],
    [Markup.button.callback(`✏️ Edit Info: ${p.editInfo?"✅ All":"👑 Admins"}`,  "gf_pt_editInfo")],
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
    `━━━━━━━━━━━━━━━━━━\n` +
    `*Permissions:*\n` +
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
bot.action("ge_desc",   async (ctx) => { await ctx.answerCbQuery(); updateSession(ctx.from.id,{groupFlow:{...getSession(ctx.from.id).groupFlow,step:"description_edit"}}); await sendClean(ctx,`📄 *New description or remove:*`,{parse_mode:"Markdown",...Markup.inlineKeyboard([[Markup.button.callback("⏭️ Remove","ge_desc_rm")],[Markup.button.callback("🔙 Cancel","gf_back_confirm")]])}); });
bot.action("ge_desc_rm", async (ctx) => { await ctx.answerCbQuery(); updateSession(ctx.from.id,{groupFlow:{...getSession(ctx.from.id).groupFlow,description:"",step:"confirm"}}); await showConfirm(ctx); });
bot.action("ge_photo",  async (ctx) => { await ctx.answerCbQuery(); updateSession(ctx.from.id,{groupFlow:{...getSession(ctx.from.id).groupFlow,step:"photo_edit"}}); await sendClean(ctx,`🖼️ *Send new photo or remove:*`,{parse_mode:"Markdown",...Markup.inlineKeyboard([[Markup.button.callback("⏭️ Remove","ge_photo_rm")],[Markup.button.callback("🔙 Cancel","gf_back_confirm")]])}); });
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
// ─── TEXT HANDLER ─────────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

bot.on("text", async (ctx) => {
  const uid=ctx.from.id, s=getSession(uid), text=ctx.message.text.trim();
  if (text.startsWith("/")) return;
  try { await ctx.deleteMessage(); } catch {}

  // WA phone input
  if (s.awaitingPhoneForIndex !== null && s.awaitingPhoneForIndex !== undefined) {
    const phone=text.replace(/[^0-9]/g,"");
    if (phone.length<10) { await sendClean(ctx,`❌ Invalid. Example: \`919876543210\``,{parse_mode:"Markdown",...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu","back_menu")]])}); return; }
    updateSession(uid,{awaitingPhoneForIndex:null});
    const wm=await ctx.reply(`⏳ *Generating pairing code...*`,{parse_mode:"Markdown"});
    updateSession(uid,{lastMsgId:wm.message_id});
    pendingPairingCbs.set(0, async (code) => {
      try { await ctx.telegram.deleteMessage(ctx.chat.id,wm.message_id); } catch {}
      if (!code) { await sendClean(ctx,`❌ *Code failed. Try again.*`,{parse_mode:"Markdown",...Markup.inlineKeyboard([[Markup.button.callback("🔄 Try Again","menu_account")],[Markup.button.callback("🏠 Main Menu","back_menu")]])}); return; }
      await sendClean(ctx,
        `🔑 *Pairing Code*\n━━━━━━━━━━━━━━━━━━\n\n\`${code}\`\n\n━━━━━━━━━━━━━━━━━━\n*How to link:*\n1️⃣ Open WhatsApp\n2️⃣ Settings → Linked Devices → Link a Device\n3️⃣ "Link with phone number"\n4️⃣ Enter code above\n\n⚠️ Valid *60 seconds* only!\n⏳ Waiting for connection...`,
        {parse_mode:"Markdown",...Markup.inlineKeyboard([[Markup.button.callback("🔄 New Code","reset_wa")],[Markup.button.callback("🏠 Main Menu","back_menu")]])}
      );
    });
    pendingReadyCbs.set(0, async () => { await sendMainMenu(ctx); });
    connectAccount(0,phone).catch(async (err) => {
      pendingPairingCbs.delete(0); pendingReadyCbs.delete(0);
      await sendClean(ctx,`❌ Error: \`${err.message}\``,{parse_mode:"Markdown",...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu","back_menu")]])});
    });
    return;
  }

  // Join Groups links
  if (s.joinFlow?.step==="links") {
    const codes=text.split(/\n/).map((l)=>{const m=l.match(/chat\.whatsapp\.com\/([A-Za-z0-9]+)/);return m?m[1]:null;}).filter(Boolean);
    if (!codes.length) { await sendClean(ctx,`❌ *No valid links found.*\nFormat: \`https://chat.whatsapp.com/XXXXX\``,{parse_mode:"Markdown",...Markup.inlineKeyboard([[Markup.button.callback("🔙 Try Again","join_groups_start")],[Markup.button.callback("🏠 Main Menu","back_menu")]])}); return; }
    updateSession(uid,{joinFlow:null});
    startTimes.set(uid,Date.now());
    const pm=await ctx.reply(`🔗 *Joining ${codes.length} group(s)...*\n━━━━━━━━━━━━━━━━━━\n${bar(0,codes.length)}`,{parse_mode:"Markdown"});
    updateSession(uid,{lastMsgId:pm.message_id});
    await showCancelBtn(ctx);
    let joined=0, failed=0, failedLinks=[], cancelled=false;
    for (let i=0;i<codes.length;i++) {
      if (isCancelled(uid)) { cancelled=true; break; }
      try { await bot.telegram.editMessageText(ctx.chat.id,pm.message_id,undefined,`🔗 *Joining...*\n━━━━━━━━━━━━━━━━━━\n✅ ${joined} | ❌ ${failed}\n⚙️ Group ${i+1}/${codes.length}\n${bar(i,codes.length)}`,{parse_mode:"Markdown"}); } catch {}
      try { await joinGroupViaLink(0,codes[i]); joined++; }
      catch { failed++; failedLinks.push(`https://chat.whatsapp.com/${codes[i]}`); }
      await sleep(2000);
    }
    await removeCancelBtn(ctx);
    try { await bot.telegram.editMessageText(ctx.chat.id,pm.message_id,undefined,`✅ *Done!* Joined: ${joined} | Failed: ${failed}`,{parse_mode:"Markdown"}); } catch {}
    if (failedLinks.length) await ctx.reply(`❌ *Could not join:*\n${failedLinks.join("\n")}`,{parse_mode:"Markdown"});
    await sendSummary(ctx,{feature:"join_groups",total:codes.length,success:joined,failed,cancelled});
    await sendMainMenu(ctx); return;
  }

  // Feature: similar keyword
  if (s.featureFlow?.step==="similar_query") {
    const kw=text.toLowerCase();
    try {
      const all=await getAllGroupsWithDetails(0);
      const filtered=all.filter((g)=>g.name.toLowerCase().includes(kw));
      if (!filtered.length) { await sendClean(ctx,`❌ No groups match "*${text}*"`,{parse_mode:"Markdown",...Markup.inlineKeyboard([[Markup.button.callback("🔙 Try Again",`gs_similar_${s.featureFlow.feature}`)],[Markup.button.callback("🏠 Main Menu","back_menu")]])}); return; }
      updateSession(uid,{featureFlow:{...s.featureFlow,allGroups:all,selectedIds:filtered.map((g)=>g.id),keyword:kw,step:"confirm"}});
      await sendClean(ctx,
        `✅ *Found ${filtered.length} matching group(s):*\n\n${filtered.slice(0,15).map((g,i)=>`${i+1}. ${g.name}`).join("\n")}${filtered.length>15?`\n_...and ${filtered.length-15} more_`:""}`,
        {parse_mode:"Markdown",...Markup.inlineKeyboard([[Markup.button.callback("🚀 Proceed","gs_sim_proceed")],[Markup.button.callback("🏠 Main Menu","back_menu")]]) }
      );
    } catch (err) { await sendClean(ctx,`❌ Error: ${err.message}`,Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu","back_menu")]])); }
    return;
  }

  // Feature: make admin numbers
  if (s.featureFlow?.step==="admin_numbers") {
    const nums=text.split(/[\n,\s]+/).map((n)=>n.replace(/[^0-9]/g,"")).filter((n)=>n.length>=10);
    if (!nums.length) { await ctx.reply("⚠️ No valid numbers. Include country code."); return; }
    const flow=s.featureFlow;
    updateSession(uid,{featureFlow:{...flow,adminNumbers:nums,step:"executing"}});
    await runFeature(ctx,flow.feature,flow.selectedIds,flow.allGroups,nums);
    return;
  }

  // Create Groups text steps
  const flow=s.groupFlow;
  if (!flow) { await sendMainMenu(ctx); return; }

  if (flow.step==="name") {
    const name=text.slice(0,100); updateSession(uid,{groupFlow:{...flow,name,step:"count"}});
    await sendClean(ctx,`📋 *Create Groups — Step 2 of 9*\n━━━━━━━━━━━━━━━━━━\n\n✅ Name: *${name}*\n\n*How many groups? (1–50)*`,
      {parse_mode:"Markdown",...Markup.inlineKeyboard([[1,5,10,20,50].map((n)=>Markup.button.callback(`${n}`,`gf_count_${n}`)),[Markup.button.callback("❌ Cancel","back_menu")]]) });
    return;
  }
  if (flow.step==="name_edit")  { updateSession(uid,{groupFlow:{...flow,name:text.slice(0,100),step:"confirm"}}); await showConfirm(ctx); return; }
  if (flow.step==="count"||flow.step==="count_edit") {
    const n=parseInt(text,10); if (isNaN(n)||n<1||n>50) { await ctx.reply("⚠️ Enter a number 1–50."); return; }
    if (flow.step==="count_edit") { updateSession(uid,{groupFlow:{...flow,count:n,step:"confirm"}}); await showConfirm(ctx); }
    else { updateSession(uid,{groupFlow:{...flow,count:n,step:"numbering"}}); await askNumbering(ctx); }
    return;
  }
  if (flow.step==="description")      { updateSession(uid,{groupFlow:{...flow,description:text.slice(0,512),step:"photo"}}); await askPhoto(ctx); return; }
  if (flow.step==="description_edit") { updateSession(uid,{groupFlow:{...flow,description:text.slice(0,512),step:"confirm"}}); await showConfirm(ctx); return; }
  if (flow.step==="members"||flow.step==="members_edit") {
    const nums=text.split(/[\n,\s]+/).map((n)=>n.replace(/[^0-9]/g,"")).filter((n)=>n.length>=10);
    if (!nums.length) { await ctx.reply("⚠️ No valid numbers found."); return; }
    if (flow.step==="members_edit") { updateSession(uid,{groupFlow:{...flow,members:nums,step:"confirm"}}); await showConfirm(ctx); }
    else { updateSession(uid,{groupFlow:{...flow,members:nums,step:"admin"}}); await askAdmin(ctx); }
    return;
  }
  await sendMainMenu(ctx);
});

// Photo Handler
bot.on("photo", async (ctx) => {
  const uid=ctx.from.id, flow=getSession(uid).groupFlow;
  if (!flow||(flow.step!=="photo"&&flow.step!=="photo_edit")) return;
  try { await ctx.deleteMessage(); } catch {}
  try {
    const p=ctx.message.photo[ctx.message.photo.length-1];
    const u=await ctx.telegram.getFileLink(p.file_id);
    const r=await fetch(u.href);
    const buf=Buffer.from(await r.arrayBuffer());
    const ns=flow.step==="photo_edit"?"confirm":"disappearing";
    updateSession(uid,{groupFlow:{...flow,photo:buf,step:ns}});
    if (ns==="confirm") await showConfirm(ctx); else await askDisappearing(ctx);
  } catch (err) { console.error("[Photo]",err.message); await ctx.reply("❌ Could not save photo. Try again."); }
});

bot.catch((err) => console.error("[Bot Error]", err.message));

// ─── Health server ─────────────────────────────────────────────────────────
const app=express(), PORT=process.env.PORT||3000;
app.get("/",(_, res) => res.send(`<html><body style="font-family:sans-serif;text-align:center;padding:50px;background:#111;color:#fff"><h2>✅ WA Group Creator Bot</h2><p style="color:#4ade80">Running 🟢</p><p>WA: ${getConnectedCount()>0?"Connected ✅":"Disconnected ❌"}</p></body></html>`));
app.get("/health",(_,res)=>res.json({status:"ok",whatsapp:getStatus(0),phone:getPhone(0)||null,ts:new Date().toISOString()}));
app.listen(PORT,()=>console.log(`HTTP server on port ${PORT}`));

function selfPing() {
  const url=process.env.RENDER_EXTERNAL_URL||process.env.SELF_URL; if (!url) return;
  const full=url.startsWith("http")?url:`https://${url}`;
  (full.startsWith("https")?https:http).get(`${full}/health`,(r)=>console.log(`[Ping] ${r.statusCode}`)).on("error",(e)=>console.error("[Ping]",e.message));
}
setTimeout(()=>{selfPing();setInterval(selfPing,120000);},60000);

async function main() {
  await connectDB();
  await reconnectSavedAccounts();
  await bot.launch({ dropPendingUpdates: true });
  console.log(`WA Group Creator Bot running! Owner: ${OWNER_ID||"NOT SET"}`);
}
main().catch((err)=>{console.error("Fatal:",err.message);process.exit(1);});
process.once("SIGINT",()=>bot.stop("SIGINT"));
process.once("SIGTERM",()=>bot.stop("SIGTERM"));
