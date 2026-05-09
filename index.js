/**
 * WA Group Manager Bot — Fixed & Advanced
 * All features working | Full English UI | Latest Baileys compatible
 */

"use strict";

const { Telegraf, Markup } = require("telegraf");
const { connectDB }        = require("./src/db");
const {
  getSession, updateSession, resetSession,
  defaultGroupFlow, defaultFeatureFlow,
} = require("./src/session");
const {
  setCallbacks, getStatus, getPhone, getConnectedCount,
  connectAccount, disconnectAccount, reconnectSavedAccounts,
  createGroup, updateGroupDescription, updateGroupPhoto,
  setDisappearingMessages, setGroupPermissions,
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

// ─── Config ───────────────────────────────────────────────────────────────
const TOKEN    = process.env.TELEGRAM_BOT_TOKEN;
if (!TOKEN) { console.error("TELEGRAM_BOT_TOKEN not set!"); process.exit(1); }
const OWNER_ID = parseInt(process.env.OWNER_ID || "0", 10);

const bot       = new Telegraf(TOKEN);
const sleep     = (ms) => new Promise((r) => setTimeout(r, ms));
const PAGE_SIZE = 10;
const startTimes     = new Map();
const aaLiveIntervals = new Map();

// ─── Owner Guard ───────────────────────────────────────────────────────────
bot.use(async (ctx, next) => {
  if (OWNER_ID && ctx.from?.id !== OWNER_ID) {
    if (ctx.callbackQuery)
      await ctx.answerCbQuery("⛔ Unauthorized.", { show_alert: true }).catch(() => {});
    else
      await ctx.reply("⛔ This bot is for the owner only.").catch(() => {});
    return;
  }
  return next();
});

// ─── Pairing Callbacks ─────────────────────────────────────────────────────
const pendingPairingCbs = new Map();
const pendingReadyCbs   = new Map();

setCallbacks({
  onPairingCode: async (i, code) => {
    const cb = pendingPairingCbs.get(i);
    if (cb) { pendingPairingCbs.delete(i); await cb(code); }
  },
  onReady: async (i) => {
    const cb = pendingReadyCbs.get(i);
    if (cb) { pendingReadyCbs.delete(i); await cb(); }
  },
  onDisconnected: async () => console.log("[Bot] WhatsApp disconnected"),
});

// ─── Utilities ─────────────────────────────────────────────────────────────
async function reply(ctx, text, extra = {}) {
  return await ctx.reply(text, extra);
}

async function editOrReply(ctx, text, extra = {}) {
  try { return await ctx.editMessageText(text, extra); }
  catch { return await ctx.reply(text, extra); }
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
    const m = await ctx.reply(
      `⏳ *Operation running... Tap below to cancel.*`,
      { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🛑 Cancel", "cancel_exec")]]) }
    );
    if (uid) updateSession(uid, { cancelMsgId: m.message_id });
  } catch (_) {}
}

async function removeCancelBtn(ctx) {
  const uid = ctx.from?.id;
  if (!uid) return;
  const { cancelMsgId } = getSession(uid);
  if (cancelMsgId) {
    try { await ctx.telegram.deleteMessage(ctx.chat.id, cancelMsgId); } catch (_) {}
    updateSession(uid, { cancelMsgId: null });
  }
}

bot.action("cancel_exec", async (ctx) => {
  await ctx.answerCbQuery("🛑 Cancelling...", { show_alert: true });
  updateSession(ctx.from.id, { cancelPending: true });
  try { await ctx.editMessageText("🛑 *Cancelling... please wait.*", { parse_mode: "Markdown" }); } catch (_) {}
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
  get_links:       "🔗 Get Links",
  leave:           "🚪 Leave Groups",
  remove_members:  "🧹 Remove Members",
  make_admin:      "👑 Make Admin",
  approval:        "🔀 Approval Toggle",
  approve_pending: "✅ Approve Pending",
  member_list:     "📋 Member List",
  pending_list:    "⏳ Pending List",
  join_groups:     "🔗 Join Groups",
  create_groups:   "➕ Create Groups",
  add_members:     "➕ Add Members",
  edit_settings:   "⚙️ Edit Settings",
  change_name:     "✏️ Change Name",
  reset_link:      "🔄 Reset Link",
  demote_admin:    "⬇️ Demote Admin",
  auto_accept:     "⏰ Auto Accept",
  ctc_checker:     "🔍 CTC Checker",
};

// ─── Summary ───────────────────────────────────────────────────────────────
async function sendSummary(ctx, opts) {
  const { feature, total, success, failed, cancelled, extra = [] } = opts;
  const uid  = ctx.from?.id;
  const secs = uid ? elapsed(uid) : 0;
  if (uid) startTimes.delete(uid);

  const statusLine = cancelled
    ? "🚫 *Cancelled*"
    : failed === 0
      ? "✅ *Completed successfully!*"
      : `⚠️ *Completed with ${failed} failure(s)*`;

  let text =
    `📊 *Summary — ${FEAT_LABEL[feature] || feature}*\n` +
    `━━━━━━━━━━━━━━━━━━━━\n` +
    `${statusLine}\n` +
    `━━━━━━━━━━━━━━━━━━━━\n` +
    `📁 Total    :  ${total}\n` +
    `✅ Success  :  ${success}\n` +
    `❌ Failed   :  ${failed}\n` +
    `⏱ Time     :  ${secs}s\n`;

  if (extra.length) {
    text += `━━━━━━━━━━━━━━━━━━━━\n` + extra.join("\n") + "\n";
  }
  text += `━━━━━━━━━━━━━━━━━━━━`;

  if (text.length > 4000) text = text.slice(0, 3990) + "\n_...truncated_";

  await ctx.reply(text, {
    parse_mode: "Markdown",
    ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]),
  });
}

// ─── Main Menu ─────────────────────────────────────────────────────────────
function buildMainMenu() {
  const c = getStatus(0) === "connected";
  const p = getPhone(0);
  const b = (label, cb) => Markup.button.callback(label, c ? cb : "need_connect");
  return Markup.inlineKeyboard([
    [Markup.button.callback(
      c ? `📱 WhatsApp: ✅ +${p}` : `📱 WhatsApp: ❌ Not Connected`,
      "menu_account"
    )],
    [b("➕ Create Groups",    "create_groups_start"), b("🔗 Join Groups",      "join_groups_start")],
    [b("🔗 Get Links",        "feat_getlinks"),       b("🚪 Leave Groups",     "feat_leave")],
    [b("🧹 Remove Members",   "feat_removemem"),      b("👑 Make Admin",       "feat_makeadmin")],
    [b("⬇️ Demote Admin",     "feat_demoteadmin"),    b("🔀 Approval Toggle",  "feat_approval")],
    [b("✅ Approve Pending",  "feat_approvepending"), b("🔄 Reset Link",       "feat_resetlink")],
    [b("📋 Member List",      "feat_memberlist"),     b("➕ Add Members",      "feat_addmembers")],
    [b("⚙️ Edit Settings",    "feat_editsettings"),   b("✏️ Change Name",      "feat_changename")],
    [b("⏰ Auto Accept",      "feat_autoaccept"),     b("🔍 CTC Checker",      "feat_ctcchecker")],
    [Markup.button.callback("📊 Status", "menu_status")],
  ]);
}

async function sendMainMenu(ctx) {
  const user = ctx.from;
  const c = getStatus(0) === "connected";
  const p = getPhone(0);
  updateSession(user?.id, { cancelPending: false, awaitingVcf: null });

  const text =
    `🤖 *WA Group Manager Bot*\n` +
    `━━━━━━━━━━━━━━━━━━━━━━━━\n` +
    `👤 *${user.first_name}${user.last_name ? " " + user.last_name : ""}*\n` +
    `🆔 ID: \`${user.id}\`\n` +
    `📱 WhatsApp: ${c ? `✅ Connected  |  +${p}` : "❌ *Not Connected*  ← Connect first"}\n` +
    `━━━━━━━━━━━━━━━━━━━━━━━━\n` +
    `*Available Features:*\n` +
    `➕ Create Groups  •  🔗 Join  •  🔗 Get Links\n` +
    `👑 Make Admin  •  ⬇️ Demote  •  🧹 Remove Members\n` +
    `✅ Approve Pending  •  ⏰ Auto Accept  •  🔍 CTC Check\n` +
    `⚙️ Edit Settings  •  ✏️ Rename  •  🔄 Reset Link\n` +
    `━━━━━━━━━━━━━━━━━━━━━━━━\n` +
    `_Choose a feature from the menu below:_`;

  await ctx.reply(text, { parse_mode: "Markdown", ...buildMainMenu() });
}

bot.start(async (ctx) => { resetSession(ctx.from.id); await sendMainMenu(ctx); });
bot.command("menu", async (ctx) => {
  updateSession(ctx.from.id, {
    awaitingPhoneForIndex: null, groupFlow: null, joinFlow: null,
    featureFlow: null, cancelPending: false, awaitingVcf: null,
  });
  await sendMainMenu(ctx);
});
bot.action("need_connect", async (ctx) => {
  await ctx.answerCbQuery("⚠️ Please connect WhatsApp first!", { show_alert: true });
});
bot.action("back_menu", async (ctx) => {
  await ctx.answerCbQuery();
  const uid = ctx.from.id;
  if (aaLiveIntervals.has(uid)) {
    clearInterval(aaLiveIntervals.get(uid));
    aaLiveIntervals.delete(uid);
  }
  updateSession(uid, {
    awaitingPhoneForIndex: null, groupFlow: null, joinFlow: null,
    featureFlow: null, cancelPending: false, awaitingVcf: null,
  });
  await sendMainMenu(ctx);
});

// ─── Status ────────────────────────────────────────────────────────────────
bot.action("menu_status", async (ctx) => {
  await ctx.answerCbQuery();
  const s = getStatus(0), p = getPhone(0);
  const icon = s === "connected" ? "✅" : s === "connecting" ? "⏳" : "❌";
  await editOrReply(ctx,
    `📊 *Bot Status*\n━━━━━━━━━━━━━━━━━━━━\n${icon} WhatsApp: *${s}*` +
    `${s === "connected" ? `\n📞 Number: +${p}` : ""}\n━━━━━━━━━━━━━━━━━━━━`,
    { parse_mode: "Markdown", reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]).reply_markup }
  );
});

// ─── Account ───────────────────────────────────────────────────────────────
bot.action("menu_account", async (ctx) => {
  await ctx.answerCbQuery();
  const status = getStatus(0), phone = getPhone(0);
  if (status === "connected") {
    await editOrReply(ctx,
      `📱 *WhatsApp Account*\n━━━━━━━━━━━━━━━━━━━━\n✅ Connected\n📞 +${phone}\n━━━━━━━━━━━━━━━━━━━━\nDo you want to log out?`,
      { parse_mode: "Markdown", reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🔌 Logout", "logout_wa")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]).reply_markup }
    );
  } else if (status === "connecting") {
    await editOrReply(ctx,
      `📱 *WhatsApp Account*\n━━━━━━━━━━━━━━━━━━━━\n⏳ Connecting...\n━━━━━━━━━━━━━━━━━━━━`,
      { parse_mode: "Markdown", reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🔄 Reset", "reset_wa")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]).reply_markup }
    );
  } else {
    updateSession(ctx.from.id, { awaitingPhoneForIndex: 0 });
    await editOrReply(ctx,
      `📱 *Connect WhatsApp*\n━━━━━━━━━━━━━━━━━━━━\n\nSend your phone number (with country code):\n\n*Example:* \`919876543210\`\n\n⚠️ The pairing code expires in *60 seconds*!`,
      { parse_mode: "Markdown", reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]).reply_markup }
    );
  }
});

bot.action("logout_wa", async (ctx) => {
  await ctx.answerCbQuery("Logging out...");
  await editOrReply(ctx, `⏳ *Logging out...*`, { parse_mode: "Markdown" });
  await disconnectAccount(0);
  await sleep(800);
  await sendMainMenu(ctx);
});

bot.action("reset_wa", async (ctx) => {
  await ctx.answerCbQuery("Resetting...");
  await disconnectAccount(0);
  updateSession(ctx.from.id, { awaitingPhoneForIndex: 0 });
  await editOrReply(ctx,
    `📱 *Connect WhatsApp*\n━━━━━━━━━━━━━━━━━━━━\n\nSend your phone number:\n*Example:* \`919876543210\``,
    { parse_mode: "Markdown", reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]).reply_markup }
  );
});

// ══════════════════════════════════════════════════════════════════════════
// ─── GROUP SELECTION SYSTEM ───────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

async function showGroupTypeSelect(ctx, feature) {
  const label = FEAT_LABEL[feature] || feature;
  await reply(ctx,
    `${label}\n━━━━━━━━━━━━━━━━━━━━\n\n*Which groups do you want to use?*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("🔍 Similar Groups", `gs_similar_${feature}`)],
      [Markup.button.callback("📋 All Groups",      `gs_all_${feature}`)],
      [Markup.button.callback("☑️ Select Groups",   `gs_select_${feature}`)],
      [Markup.button.callback("🏠 Main Menu", "back_menu")],
    ]) }
  );
}

// ─── Feature Entry Points ──────────────────────────────────────────────────
const FEAT_MAP = {
  getlinks:      "get_links",
  leave:         "leave",
  removemem:     "remove_members",
  makeadmin:     "make_admin",
  approval:      "approval",
  approvepending:"approve_pending",
  editsettings:  "edit_settings",
  resetlink:     "reset_link",
  demoteadmin:   "demote_admin",
  autoaccept:    "auto_accept",
};

Object.keys(FEAT_MAP).forEach((key) => {
  bot.action(`feat_${key}`, async (ctx) => {
    await ctx.answerCbQuery();
    if (getStatus(0) !== "connected") {
      await ctx.answerCbQuery("⚠️ WhatsApp is not connected!", { show_alert: true });
      return;
    }
    const feature = FEAT_MAP[key];
    updateSession(ctx.from.id, { featureFlow: defaultFeatureFlow(feature), cancelPending: false });
    await showGroupTypeSelect(ctx, feature);
  });
});

// Member List
bot.action("feat_memberlist", async (ctx) => {
  await ctx.answerCbQuery();
  if (getStatus(0) !== "connected") {
    await ctx.answerCbQuery("⚠️ WhatsApp is not connected!", { show_alert: true }); return;
  }
  updateSession(ctx.from.id, { featureFlow: defaultFeatureFlow("member_list"), cancelPending: false });
  await reply(ctx,
    `📋 *Member List*\n━━━━━━━━━━━━━━━━━━━━\n\n*What do you want to view?*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("👥 Member Count",      "ml_sub_members")],
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

// Add Members
bot.action("feat_addmembers", async (ctx) => {
  await ctx.answerCbQuery();
  if (getStatus(0) !== "connected") {
    await ctx.answerCbQuery("⚠️ WhatsApp is not connected!", { show_alert: true }); return;
  }
  updateSession(ctx.from.id, {
    featureFlow: { ...defaultFeatureFlow("add_members"), step: "am_links", links: [], vcfs: [], currentVcfIdx: 0, addMode: "bulk" },
    cancelPending: false,
  });
  await reply(ctx,
    `➕ *Add Members*\n━━━━━━━━━━━━━━━━━━━━\n\nSend group invite links — one per line:\n\`\`\`\nhttps://chat.whatsapp.com/ABC\nhttps://chat.whatsapp.com/DEF\n\`\`\`\n\n_A VCF file will be requested for each link._`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
  );
});

// Change Name
bot.action("feat_changename", async (ctx) => {
  await ctx.answerCbQuery();
  if (getStatus(0) !== "connected") {
    await ctx.answerCbQuery("⚠️ WhatsApp is not connected!", { show_alert: true }); return;
  }
  updateSession(ctx.from.id, {
    featureFlow: { ...defaultFeatureFlow("change_name"), step: "cn_mode" },
    cancelPending: false,
  });
  await reply(ctx,
    `✏️ *Change Name*\n━━━━━━━━━━━━━━━━━━━━\n\n*Choose naming method:*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("🔀 Random Name (custom)",      "cn_random")],
      [Markup.button.callback("📛 From VCF (filename = name)", "cn_vcf")],
      [Markup.button.callback("🏠 Main Menu", "back_menu")],
    ]) }
  );
});

// CTC Checker
bot.action("feat_ctcchecker", async (ctx) => {
  await ctx.answerCbQuery();
  if (getStatus(0) !== "connected") {
    await ctx.answerCbQuery("⚠️ WhatsApp is not connected!", { show_alert: true }); return;
  }
  updateSession(ctx.from.id, {
    featureFlow: { ...defaultFeatureFlow("ctc_checker"), step: "ctc_links" },
    cancelPending: false,
  });
  await reply(ctx,
    `🔍 *CTC Checker*\n━━━━━━━━━━━━━━━━━━━━\n\nSend all group links — one per line:\n\`\`\`\nhttps://chat.whatsapp.com/ABC\nhttps://chat.whatsapp.com/DEF\n\`\`\``,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
  );
});

// ─── Similar Groups ────────────────────────────────────────────────────────
bot.action(/^gs_similar_(.+)$/, async (ctx) => {
  await ctx.answerCbQuery("Detecting groups...");
  const feature = ctx.match[1];
  try {
    const all = await getAllGroupsWithDetails(0);
    if (!all.length) {
      await reply(ctx, "❌ No groups found.", Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]));
      return;
    }
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
    rows.push([Markup.button.callback("🔍 Custom Keyword Search", "gs_sim_custom")]);
    rows.push([Markup.button.callback("🏠 Main Menu", "back_menu")]);
    await reply(ctx,
      `🔍 *Similar Groups*\n━━━━━━━━━━━━━━━━━━━━\nTotal groups: *${all.length}*\n\n*Auto-detected prefixes:*\n_Tap a prefix to select all groups with that name_`,
      { parse_mode: "Markdown", ...Markup.inlineKeyboard(rows) }
    );
  } catch (err) {
    await reply(ctx, `❌ Error: ${err.message}`, Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]));
  }
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
  await reply(ctx,
    `✅ *"${word}" — ${matching.length} group(s) selected:*\n━━━━━━━━━━━━━━━━━━━━\n${matching.slice(0, 20).map((g, i) => `${i + 1}. ${g.name}`).join("\n")}${matching.length > 20 ? `\n_...and ${matching.length - 20} more_` : ""}`,
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
  await reply(ctx,
    `🔍 *Custom Keyword Search*\n━━━━━━━━━━━━━━━━━━━━\n\nType a keyword — all groups containing it will be selected:`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
  );
});

// ─── All Groups ────────────────────────────────────────────────────────────
bot.action(/^gs_all_(.+)$/, async (ctx) => {
  await ctx.answerCbQuery("Loading all groups...");
  const feature = ctx.match[1];
  try {
    const groups = await getAllGroupsWithDetails(0);
    if (!groups.length) {
      await reply(ctx, "❌ No groups found.", Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]));
      return;
    }
    const selectedIds = groups.map((g) => g.id);
    updateSession(ctx.from.id, {
      featureFlow: { ...getSession(ctx.from.id).featureFlow, feature, allGroups: groups, selectedIds, step: "confirm" },
    });
    await onGroupsConfirmed(ctx, feature, selectedIds, groups);
  } catch (err) {
    await reply(ctx, `❌ Error: ${err.message}`, Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]));
  }
});

// ─── Select Groups (Paginated) ─────────────────────────────────────────────
bot.action(/^gs_select_(.+)$/, async (ctx) => {
  await ctx.answerCbQuery("Loading groups...");
  const feature = ctx.match[1];
  try {
    const groups = await getAllGroupsWithDetails(0);
    if (!groups.length) {
      await reply(ctx, "❌ No groups found.", Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]));
      return;
    }
    updateSession(ctx.from.id, {
      featureFlow: { ...getSession(ctx.from.id).featureFlow, feature, allGroups: groups, selectedIds: [], page: 0, step: "select" },
    });
    await showPaginatedGroups(ctx);
  } catch (err) {
    await reply(ctx, `❌ Error: ${err.message}`, Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]));
  }
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
    const name = g.name.length > 42 ? g.name.slice(0, 41) + "…" : g.name;
    rows.push([Markup.button.callback(`${selSet.has(g.id) ? "✅" : "◻️"} ${name}`, `gs_tog_${idx}`)]);
  }
  const nav = [];
  if (page > 0)              nav.push(Markup.button.callback("◀️", "gs_prev"));
  nav.push(Markup.button.callback(`${page + 1} / ${totalPages}`, "gs_noop"));
  if (page < totalPages - 1) nav.push(Markup.button.callback("▶️", "gs_next"));
  rows.push(nav);
  rows.push([Markup.button.callback(`✅ Confirm (${selSet.size} selected)`, "gs_confirm")]);
  rows.push([Markup.button.callback("🏠 Main Menu", "back_menu")]);
  const text =
    `☑️ *Select Groups*  —  Page ${page + 1}/${totalPages}\n` +
    `━━━━━━━━━━━━━━━━━━━━\n` +
    `Total: *${allGroups.length}*  •  Selected: *${selSet.size}*\n` +
    `_Tap to select / deselect_`;
  try { await ctx.editMessageText(text, { parse_mode: "Markdown", reply_markup: Markup.inlineKeyboard(rows).reply_markup }); }
  catch { await ctx.reply(text, { parse_mode: "Markdown", ...Markup.inlineKeyboard(rows) }); }
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
  if (!flow.selectedIds.length) {
    await ctx.answerCbQuery("⚠️ Select at least 1 group!", { show_alert: true }); return;
  }
  await onGroupsConfirmed(ctx, flow.feature, flow.selectedIds, flow.allGroups);
});
bot.action("gs_sim_proceed", async (ctx) => {
  await ctx.answerCbQuery();
  const flow = getSession(ctx.from.id).featureFlow;
  await onGroupsConfirmed(ctx, flow.feature, flow.selectedIds, flow.allGroups);
});

// ─── Route After Group Selection ──────────────────────────────────────────
async function onGroupsConfirmed(ctx, feature, selectedIds, allGroups) {
  const s = getSession(ctx.from.id);

  if (feature === "make_admin") {
    updateSession(ctx.from.id, { featureFlow: { ...s.featureFlow, selectedIds, allGroups, step: "admin_numbers" } });
    await reply(ctx,
      `👑 *Make Admin*\n━━━━━━━━━━━━━━━━━━━━\n*${selectedIds.length} group(s) selected*\n\nSend phone numbers to promote — one per line:\n\`\`\`\n919876543210\n918765432109\n\`\`\`\n_Country code required_`,
      { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
    );
    return;
  }

  if (feature === "demote_admin") {
    updateSession(ctx.from.id, { featureFlow: { ...s.featureFlow, selectedIds, allGroups, step: "demote_numbers" } });
    await reply(ctx,
      `⬇️ *Demote Admin*\n━━━━━━━━━━━━━━━━━━━━\n*${selectedIds.length} group(s) selected*\n\nSend admin numbers to demote — one per line:\n\`\`\`\n919876543210\n\`\`\``,
      { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
    );
    return;
  }

  if (feature === "edit_settings") {
    updateSession(ctx.from.id, { featureFlow: { ...s.featureFlow, selectedIds, allGroups, step: "es_configure",
      desiredSettings: { announce: null, restrict: null, joinApproval: null, memberAddMode: null } } });
    await showEditSettingsConfig(ctx);
    return;
  }

  if (feature === "auto_accept") {
    updateSession(ctx.from.id, { featureFlow: { ...s.featureFlow, selectedIds, allGroups, step: "aa_duration" } });
    await showAutoAcceptDuration(ctx);
    return;
  }

  updateSession(ctx.from.id, { featureFlow: { ...s.featureFlow, selectedIds, allGroups } });
  await runFeature(ctx, feature, selectedIds, allGroups, []);
}

// ══════════════════════════════════════════════════════════════════════════
// ─── EDIT SETTINGS FLOW ───────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

function esFmt(key, val) {
  if (val === null || val === undefined) return "⏭ Skip";
  if (key === "announce")      return val ? "👑 Admins Only" : "👥 All Members";
  if (key === "restrict")      return val ? "👑 Admins Only" : "👥 All Members";
  if (key === "joinApproval")  return val ? "✅ ON"          : "❌ OFF";
  if (key === "memberAddMode") return val ? "👥 All Members" : "👑 Admins Only";
  return String(val);
}

function settingsKb(d) {
  return Markup.inlineKeyboard([
    [Markup.button.callback(`💬 Send Messages  :  ${esFmt("announce",      d.announce)}`,      "es_tog_announce")],
    [Markup.button.callback(`✏️ Edit Group Info :  ${esFmt("restrict",      d.restrict)}`,      "es_tog_restrict")],
    [Markup.button.callback(`🔐 Join Approval  :  ${esFmt("joinApproval",  d.joinApproval)}`,   "es_tog_joinApproval")],
    [Markup.button.callback(`➕ Add Members    :  ${esFmt("memberAddMode", d.memberAddMode)}`,  "es_tog_memberAddMode")],
    [Markup.button.callback("💾 Apply Settings", "es_apply")],
    [Markup.button.callback("🏠 Main Menu", "back_menu")],
  ]);
}

async function showEditSettingsConfig(ctx) {
  const flow = getSession(ctx.from.id).featureFlow;
  const d = flow.desiredSettings;
  await reply(ctx,
    `⚙️ *Edit Settings*\n━━━━━━━━━━━━━━━━━━━━\n*${flow.selectedIds.length} group(s) selected*\n\nTap each option to cycle:  Skip → On → Off → Skip\n_"Skip" = that setting won't be changed_`,
    { parse_mode: "Markdown", ...settingsKb(d) }
  );
}

["announce", "restrict", "joinApproval", "memberAddMode"].forEach((key) => {
  bot.action(`es_tog_${key}`, async (ctx) => {
    await ctx.answerCbQuery();
    const flow = getSession(ctx.from.id).featureFlow;
    const cur  = flow.desiredSettings[key];
    const next = cur === null ? true : cur === true ? false : null;
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
  if (d.announce === null && d.restrict === null && d.joinApproval === null && d.memberAddMode === null) {
    await ctx.answerCbQuery("⚠️ No setting selected!", { show_alert: true }); return;
  }
  const sel   = flow.allGroups.filter((g) => flow.selectedIds.includes(g.id));
  const total = sel.length;
  startTimes.set(uid, Date.now());
  updateSession(uid, { cancelPending: false });
  const pm = await ctx.reply(`⚙️ *Applying settings — ${total} group(s)...*\n━━━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`, { parse_mode: "Markdown" });
  await showCancelBtn(ctx);
  let changed = 0, alreadyOk = 0, failed = 0, cancelled = false;
  const details = [];
  for (let i = 0; i < total; i++) {
    if (isCancelled(uid)) { cancelled = true; break; }
    const g = sel[i];
    try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
      `⚙️ *Applying...*\n━━━━━━━━━━━━━━━━━━━━\n✅ Done: ${i}/${total}\n⚙️ ${g.name}\n${bar(i, total)}`,
      { parse_mode: "Markdown" }); } catch (_) {}
    try {
      const result = await applyGroupSettings(0, g.id, d);
      if (result.changes.length) {
        changed++;
        details.push(`✅ *${g.name}*: ${result.changes.map((c) => c.replace(/^[^:]+:/, "")).join(", ")}`);
      } else {
        alreadyOk++;
        details.push(`⏭ *${g.name}*: already matches`);
      }
    } catch (err) { failed++; details.push(`❌ *${g.name}*: ${err.message}`); }
    await sleep(800);
  }
  await removeCancelBtn(ctx);
  try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
    `✅ *Settings Applied!*\nChanged: ${changed}  •  Already OK: ${alreadyOk}  •  Failed: ${failed}`,
    { parse_mode: "Markdown" }); } catch (_) {}
  const extraLines = [`⚙️ Changed: ${changed}  •  Already OK: ${alreadyOk}`, ...details.slice(0, 30)];
  if (details.length > 30) extraLines.push(`_...and ${details.length - 30} more_`);
  await sendSummary(ctx, { feature: "edit_settings", total, success: changed, failed, cancelled, extra: extraLines });
  updateSession(uid, { featureFlow: null });
});

// ══════════════════════════════════════════════════════════════════════════
// ─── CHANGE NAME FLOW ─────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

bot.action("cn_random", async (ctx) => {
  await ctx.answerCbQuery();
  const flow = getSession(ctx.from.id).featureFlow;
  updateSession(ctx.from.id, { featureFlow: { ...flow, step: "cn_random_name", cnMethod: "random" } });
  await reply(ctx,
    `✏️ *Change Name — Random*\n━━━━━━━━━━━━━━━━━━━━\n\nEnter the base name:\n\n_Example:_ \`Alpha\` → groups become _Alpha 1, Alpha 2..._`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
  );
});

bot.action("cn_vcf", async (ctx) => {
  await ctx.answerCbQuery();
  const flow = getSession(ctx.from.id).featureFlow;
  updateSession(ctx.from.id, { featureFlow: { ...flow, step: "cn_vcf_links", cnMethod: "vcf", links: [] } });
  await reply(ctx,
    `📛 *Change Name — From VCF*\n━━━━━━━━━━━━━━━━━━━━\n\nSend all group links (one per line):\n\`\`\`\nhttps://chat.whatsapp.com/ABC\nhttps://chat.whatsapp.com/DEF\n\`\`\`\n_Bot will rename each group to match the VCF filename if contacts match._`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
  );
});

bot.action("cn_numbering_yes", async (ctx) => {
  await ctx.answerCbQuery();
  const flow = getSession(ctx.from.id).featureFlow;
  updateSession(ctx.from.id, { featureFlow: { ...flow, numbering: true, step: "cn_random_links" } });
  await reply(ctx,
    `✏️ *Numbering: ON*\n✅ Names: _${flow.cnBaseName} 1, ${flow.cnBaseName} 2..._\n\nNow send group links (one per line):`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
  );
});

bot.action("cn_numbering_no", async (ctx) => {
  await ctx.answerCbQuery();
  const flow = getSession(ctx.from.id).featureFlow;
  updateSession(ctx.from.id, { featureFlow: { ...flow, numbering: false, step: "cn_random_links" } });
  await reply(ctx,
    `✏️ *Numbering: OFF*\n✅ All groups will be named: _${flow.cnBaseName}_\n\nNow send group links (one per line):`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
  );
});

// ══════════════════════════════════════════════════════════════════════════
// ─── AUTO ACCEPT FLOW ─────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

async function showAutoAcceptDuration(ctx) {
  const flow = getSession(ctx.from.id).featureFlow;
  await reply(ctx,
    `⏰ *Auto Accept*\n━━━━━━━━━━━━━━━━━━━━\n*${flow.selectedIds.length} group(s) selected*\n\nChoose duration:\n_Approval mode must be ON in the groups!_`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("5 min",   "aa_dur_300"),    Markup.button.callback("10 min",  "aa_dur_600")],
      [Markup.button.callback("30 min",  "aa_dur_1800"),   Markup.button.callback("1 hour",  "aa_dur_3600")],
      [Markup.button.callback("2 hours", "aa_dur_7200"),   Markup.button.callback("6 hours", "aa_dur_21600")],
      [Markup.button.callback("✏️ Custom (enter minutes)",  "aa_dur_custom")],
      [Markup.button.callback("🏠 Main Menu", "back_menu")],
    ]) }
  );
}

bot.action(/^aa_dur_(\d+)$/, async (ctx) => {
  await ctx.answerCbQuery();
  const secs = parseInt(ctx.match[1]);
  const flow = getSession(ctx.from.id).featureFlow;
  updateSession(ctx.from.id, { featureFlow: { ...flow, aaDuration: secs, step: "aa_confirm" } });
  const mins  = secs / 60;
  const label = mins >= 60 ? `${mins / 60}h` : `${mins}min`;
  await reply(ctx,
    `⏰ *Auto Accept — Confirm*\n━━━━━━━━━━━━━━━━━━━━\n📁 Groups  : *${flow.selectedIds.length}*\n⏱ Duration : *${label}*\n\n_Checks every 8 seconds for pending requests._`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("▶️ Start Auto Accept", "aa_start")],
      [Markup.button.callback("🔙 Change Duration",   "aa_back_duration")],
      [Markup.button.callback("🏠 Main Menu",         "back_menu")],
    ]) }
  );
});

bot.action("aa_dur_custom", async (ctx) => {
  await ctx.answerCbQuery();
  const flow = getSession(ctx.from.id).featureFlow;
  updateSession(ctx.from.id, { featureFlow: { ...flow, step: "aa_custom_duration" } });
  await reply(ctx,
    `⏰ *Custom Duration*\n━━━━━━━━━━━━━━━━━━━━\n\nEnter duration in minutes:\n_Example:_ \`120\` = 2 hours`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
  );
});

bot.action("aa_back_duration", async (ctx) => { await ctx.answerCbQuery(); await showAutoAcceptDuration(ctx); });

function buildLiveAutoAcceptText(sel, label, endTime, stats) {
  const totalAccepted = Object.values(stats).reduce((s, v) => s + (v?.accepted || 0), 0);
  const groupLines = sel.map((g) => `• *${g.name}*: ${stats[g.id]?.accepted || 0} accepted`).join("\n");
  return (
    `⏰ *Auto Accept — ACTIVE* 🟢\n` +
    `━━━━━━━━━━━━━━━━━━━━\n` +
    `📁 Groups   : *${sel.length}*\n` +
    `⏱ Duration  : *${label}*\n` +
    `🕐 Ends at  : ${endTime}\n` +
    `━━━━━━━━━━━━━━━━━━━━\n` +
    `✅ *Total Accepted: ${totalAccepted}*\n` +
    `━━━━━━━━━━━━━━━━━━━━\n` +
    `${groupLines}\n` +
    `━━━━━━━━━━━━━━━━━━━━\n` +
    `_Checks every 8s. Tap Stop to end early._`
  );
}

bot.action("aa_start", async (ctx) => {
  await ctx.answerCbQuery("Starting...");
  const uid  = ctx.from.id;
  const flow = getSession(uid).featureFlow;
  const secs = flow.aaDuration;
  const sel  = (flow.allGroups || []).filter((g) => flow.selectedIds.includes(g.id));
  const mins  = secs / 60;
  const label = mins >= 60 ? `${mins / 60}h` : `${mins}min`;
  const endTime = new Date(Date.now() + secs * 1000).toLocaleTimeString();

  if (aaLiveIntervals.has(uid)) { clearInterval(aaLiveIntervals.get(uid)); aaLiveIntervals.delete(uid); }

  startAutoAcceptForGroups(flow.selectedIds);
  updateSession(uid, { featureFlow: { ...flow, step: "aa_running" } });

  const initialStats = getAutoAcceptStats(flow.selectedIds);
  const statusMsg = await reply(ctx,
    buildLiveAutoAcceptText(sel, label, endTime, initialStats),
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🛑 Stop Auto Accept", "aa_stop")]]) }
  );

  const liveInterval = setInterval(async () => {
    try {
      const stats = getAutoAcceptStats(flow.selectedIds);
      await bot.telegram.editMessageText(
        ctx.chat.id, statusMsg.message_id, undefined,
        buildLiveAutoAcceptText(sel, label, endTime, stats),
        { parse_mode: "Markdown", reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🛑 Stop Auto Accept", "aa_stop")]]).reply_markup }
      );
    } catch (_) {}
  }, 30000);
  aaLiveIntervals.set(uid, liveInterval);

  setTimeout(async () => {
    if (!aaLiveIntervals.has(uid)) return;
    clearInterval(aaLiveIntervals.get(uid));
    aaLiveIntervals.delete(uid);
    const stats = getAutoAcceptStats(flow.selectedIds);
    stopAutoAcceptForGroups(flow.selectedIds);
    const totalAccepted = Object.values(stats).reduce((s, v) => s + (v?.accepted || 0), 0);
    const details = sel.map((g) => `• *${g.name}*: ${stats[g.id]?.accepted || 0} accepted`);
    try { await bot.telegram.editMessageText(ctx.chat.id, statusMsg.message_id, undefined,
      `⏰ *Auto Accept — Finished!* ⏹️\n━━━━━━━━━━━━━━━━━━━━\n⏱ Duration: *${label}* — Complete\n✅ *Total Accepted: ${totalAccepted}*`,
      { parse_mode: "Markdown" }); } catch (_) {}
    await sendSummary(ctx, {
      feature: "auto_accept", total: sel.length, success: sel.length,
      failed: 0, cancelled: false,
      extra: [`✅ *Total Accepted: ${totalAccepted}*`, `⏱ Duration: ${label}`, ...details],
    });
    updateSession(uid, { featureFlow: null });
  }, secs * 1000);
});

bot.action("aa_stop", async (ctx) => {
  await ctx.answerCbQuery("Stopping...");
  const uid  = ctx.from.id;
  const flow = getSession(uid).featureFlow;
  if (aaLiveIntervals.has(uid)) { clearInterval(aaLiveIntervals.get(uid)); aaLiveIntervals.delete(uid); }
  if (!flow?.selectedIds) { await sendMainMenu(ctx); return; }
  const stats = getAutoAcceptStats(flow.selectedIds);
  stopAutoAcceptForGroups(flow.selectedIds);
  const total   = Object.values(stats).reduce((s, v) => s + (v?.accepted || 0), 0);
  const sel     = (flow.allGroups || []).filter((g) => flow.selectedIds.includes(g.id));
  const details = sel.map((g) => `• *${g.name}*: ${stats[g.id]?.accepted || 0} accepted`);
  try { await ctx.editMessageText(
    `🛑 *Auto Accept — Stopped*\n━━━━━━━━━━━━━━━━━━━━\n✅ *Total Accepted: ${total}*`,
    { parse_mode: "Markdown" }); } catch (_) {}
  await sendSummary(ctx, {
    feature: "auto_accept", total: sel.length, success: sel.length,
    failed: 0, cancelled: true,
    extra: [`✅ *Total Accepted: ${total}*`, ...details],
  });
  updateSession(uid, { featureFlow: null });
});

// ══════════════════════════════════════════════════════════════════════════
// ─── CHANGE NAME EXECUTION ────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

async function runChangeNameRandom(ctx, links, baseName, numbering) {
  const uid   = ctx.from.id;
  const total = links.length;
  startTimes.set(uid, Date.now());
  updateSession(uid, { cancelPending: false });
  const pm = await ctx.reply(`✏️ *Renaming ${total} group(s)...*\n━━━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`, { parse_mode: "Markdown" });
  await showCancelBtn(ctx);
  let done = 0, failed = 0, cancelled = false;
  const details = [];
  for (let i = 0; i < total; i++) {
    if (isCancelled(uid)) { cancelled = true; break; }
    const newName = numbering ? `${baseName} ${i + 1}` : baseName;
    try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
      `✏️ *Renaming...*\n━━━━━━━━━━━━━━━━━━━━\n✅ Done: ${done}/${total}\n→ "${newName}"\n${bar(i, total)}`,
      { parse_mode: "Markdown" }); } catch (_) {}
    try {
      const info = await getGroupInfoFromLink(0, links[i]);
      if (!info) throw new Error("Invalid/expired link");
      await renameGroup(0, info.id, newName);
      done++; details.push(`✅ ${info.name} → *${newName}*`);
    } catch (err) { failed++; details.push(`❌ Group ${i + 1}: ${err.message}`); }
    await sleep(1200);
  }
  await removeCancelBtn(ctx);
  try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
    `✅ *Done! ${done}/${total} renamed*`, { parse_mode: "Markdown" }); } catch (_) {}
  const extraLines = [...details.slice(0, 30)];
  if (details.length > 30) extraLines.push(`_...and ${details.length - 30} more_`);
  await sendSummary(ctx, { feature: "change_name", total, success: done, failed, cancelled, extra: extraLines });
  updateSession(uid, { featureFlow: null, awaitingVcf: null });
}

// ══════════════════════════════════════════════════════════════════════════
// ─── FEATURE EXECUTION ────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

async function runFeature(ctx, feature, selectedIds, allGroups, extraNums) {
  const uid   = ctx.from.id;
  const sel   = allGroups.filter((g) => selectedIds.includes(g.id));
  const total = sel.length;
  startTimes.set(uid, Date.now());
  updateSession(uid, { cancelPending: false });

  // ── GET LINKS ────────────────────────────────────────────────────────
  if (feature === "get_links") {
    const pm = await ctx.reply(`🔗 *Getting links — ${total} groups...*\n━━━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`, { parse_mode: "Markdown" });
    await showCancelBtn(ctx);
    const results = [], fails = [];
    let done = 0, cancelled = false;
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
        `🔗 *Getting Links...*\n━━━━━━━━━━━━━━━━━━━━\n✅ ${done}/${total}\n⚙️ ${g.name}\n${bar(i, total)}`,
        { parse_mode: "Markdown" }); } catch (_) {}
      try { results.push({ name: g.name, link: await getGroupInviteLink(0, g.id) }); done++; }
      catch (_) { fails.push(g.name); }
      await sleep(600);
    }
    await removeCancelBtn(ctx);
    try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
      `✅ *Links ready! ${done}/${total}*`, { parse_mode: "Markdown" }); } catch (_) {}
    const linkLines = results.map((r, i) => `*${i + 1}.* ${r.name}\n   ${r.link}`);
    const extraLines = [`🔗 *Group Links:*`, ...linkLines.slice(0, 25)];
    if (linkLines.length > 25) extraLines.push(`_...and ${linkLines.length - 25} more links_`);
    if (fails.length) extraLines.push(`❌ Failed: ${fails.slice(0, 10).join(", ")}`);
    await sendSummary(ctx, { feature, total, success: done, failed: fails.length, cancelled, extra: extraLines });
    updateSession(uid, { featureFlow: null }); return;
  }

  // ── LEAVE GROUPS ─────────────────────────────────────────────────────
  if (feature === "leave") {
    const pm = await ctx.reply(`🚪 *Leaving ${total} group(s)...*\n━━━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`, { parse_mode: "Markdown" });
    await showCancelBtn(ctx);
    let done = 0, failed = 0, cancelled = false;
    const details = [];
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
        `🚪 *Leaving...*\n━━━━━━━━━━━━━━━━━━━━\n✅ ${done}/${total}\n⚙️ ${g.name}\n${bar(i, total)}`,
        { parse_mode: "Markdown" }); } catch (_) {}
      try { await leaveGroup(0, g.id); done++; details.push(`✅ ${g.name}`); }
      catch (err) { failed++; details.push(`❌ ${g.name}: ${err.message}`); }
      await sleep(1500);
    }
    await removeCancelBtn(ctx);
    try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
      `✅ *Done! Left ${done}/${total} groups*`, { parse_mode: "Markdown" }); } catch (_) {}
    const extraLines = [...details.slice(0, 30)];
    if (details.length > 30) extraLines.push(`_...and ${details.length - 30} more_`);
    await sendSummary(ctx, { feature, total, success: done, failed, cancelled, extra: extraLines });
    updateSession(uid, { featureFlow: null }); return;
  }

  // ── REMOVE MEMBERS ───────────────────────────────────────────────────
  if (feature === "remove_members") {
    const pm = await ctx.reply(`🧹 *Removing members — ${total} group(s)...*\n━━━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`, { parse_mode: "Markdown" });
    await showCancelBtn(ctx);
    let done = 0, failed = 0, totalRem = 0, cancelled = false;
    const details = [];
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
        `🧹 *Removing...*\n━━━━━━━━━━━━━━━━━━━━\n✅ ${done}/${total}\n⚙️ ${g.name}\n${bar(i, total)}`,
        { parse_mode: "Markdown" }); } catch (_) {}
      try { const n = await removeAllMembers(0, g.id); totalRem += n; done++; details.push(`✅ ${g.name}: ${n} removed`); }
      catch (err) { failed++; details.push(`❌ ${g.name}: ${err.message}`); }
      await sleep(2000);
    }
    await removeCancelBtn(ctx);
    try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
      `✅ *Done!*  Total removed: ${totalRem}`, { parse_mode: "Markdown" }); } catch (_) {}
    const extraLines = [`🧹 *Total removed: ${totalRem}*`, ...details.slice(0, 25)];
    if (details.length > 25) extraLines.push(`_...and ${details.length - 25} more_`);
    await sendSummary(ctx, { feature, total, success: done, failed, cancelled, extra: extraLines });
    updateSession(uid, { featureFlow: null }); return;
  }

  // ── MAKE ADMIN ───────────────────────────────────────────────────────
  if (feature === "make_admin") {
    const pm = await ctx.reply(`👑 *Promoting to admin — ${total} group(s)...*\n━━━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`, { parse_mode: "Markdown" });
    await showCancelBtn(ctx);
    let done = 0, failed = 0, totalProm = 0, cancelled = false;
    const details = [];
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
        `👑 *Making Admin...*\n━━━━━━━━━━━━━━━━━━━━\n✅ ${done}/${total}\n⚙️ ${g.name}\n${bar(i, total)}`,
        { parse_mode: "Markdown" }); } catch (_) {}
      try {
        const n = await makeAdminByNumbers(0, g.id, extraNums);
        totalProm += n; done++;
        details.push(n > 0 ? `✅ ${g.name}: ${n} promoted` : `⚠️ ${g.name}: 0 found (not member/pending)`);
      } catch (err) { failed++; details.push(`❌ ${g.name}: ${err.message}`); }
      await sleep(1500);
    }
    await removeCancelBtn(ctx);
    try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
      `✅ *Done!*  Promoted: ${totalProm}`, { parse_mode: "Markdown" }); } catch (_) {}
    const extraLines = [`👑 *Total promoted: ${totalProm}*`, ...details.slice(0, 25)];
    if (details.length > 25) extraLines.push(`_...and ${details.length - 25} more_`);
    await sendSummary(ctx, { feature, total, success: done, failed, cancelled, extra: extraLines });
    updateSession(uid, { featureFlow: null }); return;
  }

  // ── DEMOTE ADMIN ──────────────────────────────────────────────────────
  if (feature === "demote_admin") {
    const pm = await ctx.reply(`⬇️ *Demoting admins — ${total} group(s)...*\n━━━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`, { parse_mode: "Markdown" });
    await showCancelBtn(ctx);
    let done = 0, failed = 0, totalDem = 0, cancelled = false;
    const details = [];
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
        `⬇️ *Demoting...*\n━━━━━━━━━━━━━━━━━━━━\n✅ ${done}/${total}\n⚙️ ${g.name}\n${bar(i, total)}`,
        { parse_mode: "Markdown" }); } catch (_) {}
      try {
        const n = await demoteAdminInGroup(0, g.id, extraNums);
        totalDem += n; done++;
        details.push(n > 0 ? `✅ ${g.name}: ${n} demoted` : `⚠️ ${g.name}: 0 found (not an admin)`);
      } catch (err) { failed++; details.push(`❌ ${g.name}: ${err.message}`); }
      await sleep(1200);
    }
    await removeCancelBtn(ctx);
    try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
      `✅ *Done!*  Demoted: ${totalDem}`, { parse_mode: "Markdown" }); } catch (_) {}
    const extraLines = [`⬇️ *Total demoted: ${totalDem}*`, ...details.slice(0, 25)];
    if (details.length > 25) extraLines.push(`_...and ${details.length - 25} more_`);
    await sendSummary(ctx, { feature, total, success: done, failed, cancelled, extra: extraLines });
    updateSession(uid, { featureFlow: null }); return;
  }

  // ── RESET LINK ────────────────────────────────────────────────────────
  if (feature === "reset_link") {
    const pm = await ctx.reply(`🔄 *Resetting invite links — ${total} group(s)...*\n━━━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`, { parse_mode: "Markdown" });
    await showCancelBtn(ctx);
    const results = [], fails = [];
    let done = 0, cancelled = false;
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
        `🔄 *Resetting...*\n━━━━━━━━━━━━━━━━━━━━\n✅ ${done}/${total}\n⚙️ ${g.name}\n${bar(i, total)}`,
        { parse_mode: "Markdown" }); } catch (_) {}
      try { results.push({ name: g.name, link: await resetGroupInviteLink(0, g.id) }); done++; }
      catch (_) { fails.push(g.name); }
      await sleep(1000);
    }
    await removeCancelBtn(ctx);
    try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
      `✅ *Links Reset! ${done}/${total}*`, { parse_mode: "Markdown" }); } catch (_) {}
    const linkLines = results.map((r, i) => `*${i + 1}.* ${r.name}\n   ${r.link}`);
    const extraLines = [`🔄 *New Links:*`, ...linkLines.slice(0, 25)];
    if (linkLines.length > 25) extraLines.push(`_...and ${linkLines.length - 25} more_`);
    if (fails.length) extraLines.push(`❌ Failed: ${fails.slice(0, 10).join(", ")}`);
    await sendSummary(ctx, { feature, total, success: done, failed: fails.length, cancelled, extra: extraLines });
    updateSession(uid, { featureFlow: null }); return;
  }

  // ── APPROVAL TOGGLE ───────────────────────────────────────────────────
  if (feature === "approval") {
    const pm = await ctx.reply(`🔀 *Toggling approval — ${total} group(s)...*\n━━━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`, { parse_mode: "Markdown" });
    await showCancelBtn(ctx);
    let done = 0, failed = 0, cancelled = false;
    const details = [];
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
        `🔀 *Toggling...*\n━━━━━━━━━━━━━━━━━━━━\n✅ ${done}/${total}\n⚙️ ${g.name}\n${bar(i, total)}`,
        { parse_mode: "Markdown" }); } catch (_) {}
      try {
        const cur = await getGroupApprovalStatus(0, g.id), next = !cur;
        await setGroupApproval(0, g.id, next);
        details.push(`${next ? "🔒" : "🔓"} ${g.name}: ${cur ? "ON" : "OFF"} → *${next ? "ON" : "OFF"}*`);
        done++;
      } catch (_) { failed++; details.push(`❌ ${g.name}: error`); }
      await sleep(1000);
    }
    await removeCancelBtn(ctx);
    try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
      `✅ *Toggled! ${done}/${total}*`, { parse_mode: "Markdown" }); } catch (_) {}
    const extraLines = [...details.slice(0, 30)];
    if (details.length > 30) extraLines.push(`_...and ${details.length - 30} more_`);
    await sendSummary(ctx, { feature, total, success: done, failed, cancelled, extra: extraLines });
    updateSession(uid, { featureFlow: null }); return;
  }

  // ── APPROVE PENDING ───────────────────────────────────────────────────
  if (feature === "approve_pending") {
    const pm = await ctx.reply(`✅ *Approving pending — ${total} group(s)...*\n━━━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`, { parse_mode: "Markdown" });
    await showCancelBtn(ctx);
    let done = 0, failed = 0, totPend = 0, totApproved = 0, totJoined = 0, cancelled = false;
    const details = [];
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
        `✅ *Approving...*\n━━━━━━━━━━━━━━━━━━━━\n✅ ${done}/${total}\n⚙️ ${g.name}\n${bar(i, total)}`,
        { parse_mode: "Markdown" }); } catch (_) {}
      try {
        const r = await approveAllPending(0, g.id);
        totPend += r.pendingCount; totApproved += r.approved; totJoined += (r.actuallyJoined || 0); done++;
        details.push(`• *${g.name}*: ${r.pendingCount} pending → ${r.approved} approved (${r.beforeCount}→${r.afterCount})`);
      } catch (err) { failed++; details.push(`• *${g.name}*: ❌ ${err.message}`); }
      await sleep(2500);
    }
    await removeCancelBtn(ctx);
    try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
      `✅ *Done!*  Approved: ${totApproved}  |  Joined: ${totJoined}`, { parse_mode: "Markdown" }); } catch (_) {}
    const extraLines = [
      `⏳ Total pending: ${totPend}`,
      `✅ Approved: ${totApproved}`,
      `👥 Actually joined: ${totJoined}`,
      ...details.slice(0, 20),
    ];
    if (details.length > 20) extraLines.push(`_...and ${details.length - 20} more_`);
    await sendSummary(ctx, { feature, total, success: done, failed, cancelled, extra: extraLines });
    updateSession(uid, { featureFlow: null }); return;
  }

  // ── MEMBER LIST ───────────────────────────────────────────────────────
  if (feature === "member_list") {
    const pm = await ctx.reply(`📋 *Member count — ${total} group(s)...*\n━━━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`, { parse_mode: "Markdown" });
    await showCancelBtn(ctx);
    let done = 0, failed = 0, grandTotal = 0, cancelled = false;
    const rows = [];
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
        `📋 *Member List...*\n━━━━━━━━━━━━━━━━━━━━\n✅ ${done}/${total}\n⚙️ ${g.name}\n${bar(i, total)}`,
        { parse_mode: "Markdown" }); } catch (_) {}
      try {
        const members = await getGroupMembers(0, g.id);
        grandTotal += members.length;
        rows.push({ name: g.name, count: members.length, ok: true });
        done++;
      } catch (_) { failed++; rows.push({ name: g.name, count: 0, ok: false }); }
      await sleep(600);
    }
    await removeCancelBtn(ctx);
    try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
      `✅ *Done! ${done}/${total} groups*`, { parse_mode: "Markdown" }); } catch (_) {}
    const sorted     = [...rows].sort((a, b) => b.count - a.count);
    const tableLines = sorted.map((r, i) => `${String(i + 1).padStart(2)}. *${r.name}*  →  ${r.ok ? r.count : "❌"}`);
    const extraLines = [
      `👥 *Grand Total: ${grandTotal} members*`,
      `━━━━━━━━━━━━━━━━━━━━`,
      ...tableLines.slice(0, 30),
    ];
    if (tableLines.length > 30) extraLines.push(`_...and ${tableLines.length - 30} more_`);
    await sendSummary(ctx, { feature, total, success: done, failed, cancelled, extra: extraLines });
    updateSession(uid, { featureFlow: null }); return;
  }

  // ── PENDING LIST ──────────────────────────────────────────────────────
  if (feature === "pending_list") {
    const pm = await ctx.reply(`⏳ *Pending requests — ${total} group(s)...*\n━━━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`, { parse_mode: "Markdown" });
    await showCancelBtn(ctx);
    let done = 0, failed = 0, grandPending = 0, cancelled = false;
    const rows = [];
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
        `⏳ *Pending Requests...*\n━━━━━━━━━━━━━━━━━━━━\n✅ ${done}/${total}\n⚙️ ${g.name}\n${bar(i, total)}`,
        { parse_mode: "Markdown" }); } catch (_) {}
      try {
        const pending = await getGroupPendingRequests(0, g.id);
        grandPending += pending.length;
        rows.push({ name: g.name, count: pending.length, ok: true });
        done++;
      } catch (_) { failed++; rows.push({ name: g.name, count: 0, ok: false }); }
      await sleep(600);
    }
    await removeCancelBtn(ctx);
    try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
      `✅ *Done! ${done}/${total} groups*`, { parse_mode: "Markdown" }); } catch (_) {}
    const tableLines = rows.map((r, i) => `${i + 1}. *${r.name}*: ${r.ok ? r.count : "❌ Error"}`);
    const extraLines = [
      `⏳ *Total Pending: ${grandPending}*`,
      `━━━━━━━━━━━━━━━━━━━━`,
      ...tableLines.slice(0, 25),
    ];
    if (tableLines.length > 25) extraLines.push(`_...and ${tableLines.length - 25} more_`);
    await sendSummary(ctx, { feature, total, success: done, failed, cancelled, extra: extraLines });
    updateSession(uid, { featureFlow: null }); return;
  }
}

// ══════════════════════════════════════════════════════════════════════════
// ─── JOIN GROUPS ──────────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

bot.action("join_groups_start", async (ctx) => {
  await ctx.answerCbQuery();
  if (getStatus(0) !== "connected") {
    await ctx.answerCbQuery("⚠️ WhatsApp is not connected!", { show_alert: true }); return;
  }
  updateSession(ctx.from.id, { joinFlow: { step: "links" }, cancelPending: false });
  await reply(ctx,
    `🔗 *Join Groups*\n━━━━━━━━━━━━━━━━━━━━\n\nSend invite links — one per line:\n\`\`\`\nhttps://chat.whatsapp.com/ABC123\nhttps://chat.whatsapp.com/DEF456\n\`\`\``,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("❌ Cancel", "back_menu")]]) }
  );
});

// ══════════════════════════════════════════════════════════════════════════
// ─── CREATE GROUPS FLOW ───────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

bot.action("create_groups_start", async (ctx) => {
  await ctx.answerCbQuery();
  if (getStatus(0) !== "connected") {
    await ctx.answerCbQuery("⚠️ WhatsApp is not connected!", { show_alert: true }); return;
  }
  updateSession(ctx.from.id, { groupFlow: defaultGroupFlow() });
  await reply(ctx,
    `➕ *Create Groups — Step 1/9*\n━━━━━━━━━━━━━━━━━━━━\n\n*What should the group name be?*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("❌ Cancel", "back_menu")]]) }
  );
});

async function askNumbering(ctx) {
  const flow = getSession(ctx.from.id).groupFlow;
  await reply(ctx,
    `➕ *Create Groups — Step 3/9*\n━━━━━━━━━━━━━━━━━━━━\n\n*Add numbering?*\n\nYes → _${flow.name} 1, ${flow.name} 2..._\nNo  → All named _${flow.name}_`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("✅ Yes", "gf_num_yes"), Markup.button.callback("❌ No", "gf_num_no")],
      [Markup.button.callback("❌ Cancel", "back_menu")]
    ]) }
  );
}
bot.action("gf_num_yes", async (ctx) => { await ctx.answerCbQuery(); const s = getSession(ctx.from.id); updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, numbering: true, step: "description" } }); await askDescription(ctx); });
bot.action("gf_num_no",  async (ctx) => { await ctx.answerCbQuery(); const s = getSession(ctx.from.id); updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, numbering: false, step: "description" } }); await askDescription(ctx); });

async function askDescription(ctx) {
  await reply(ctx,
    `➕ *Create Groups — Step 4/9*\n━━━━━━━━━━━━━━━━━━━━\n\n*Write a group description:*\n_Tap Skip to leave blank._`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("⏭ Skip", "gf_desc_skip")], [Markup.button.callback("❌ Cancel", "back_menu")]]) }
  );
}
bot.action("gf_desc_skip", async (ctx) => { await ctx.answerCbQuery(); const s = getSession(ctx.from.id); updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, description: "", step: "photo" } }); await askPhoto(ctx); });

async function askPhoto(ctx) {
  await reply(ctx,
    `➕ *Create Groups — Step 5/9*\n━━━━━━━━━━━━━━━━━━━━\n\n*Send a group photo:*\n_Tap Skip to leave blank._`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("⏭ Skip", "gf_photo_skip")], [Markup.button.callback("❌ Cancel", "back_menu")]]) }
  );
}
bot.action("gf_photo_skip", async (ctx) => { await ctx.answerCbQuery(); const s = getSession(ctx.from.id); updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, photo: null, step: "disappearing" } }); await askDisappearing(ctx); });

async function askDisappearing(ctx) {
  await reply(ctx,
    `➕ *Create Groups — Step 6/9*\n━━━━━━━━━━━━━━━━━━━━\n\n*Disappearing messages:*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("24h", "gf_dis_86400"), Markup.button.callback("7 Days", "gf_dis_604800"), Markup.button.callback("90 Days", "gf_dis_7776000")],
      [Markup.button.callback("⏭ Skip / Off", "gf_dis_0")],
      [Markup.button.callback("❌ Cancel", "back_menu")],
    ]) }
  );
}
[0, 86400, 604800, 7776000].forEach((s) => {
  bot.action(`gf_dis_${s}`, async (ctx) => { await ctx.answerCbQuery(); const ss = getSession(ctx.from.id); updateSession(ctx.from.id, { groupFlow: { ...ss.groupFlow, disappearing: s, step: "members" } }); await askMembers(ctx); });
});

async function askMembers(ctx) {
  await reply(ctx,
    `➕ *Create Groups — Step 7/9*\n━━━━━━━━━━━━━━━━━━━━\n\n*Add members? (one number per line):*\n\`\`\`\n919876543210\n\`\`\``,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("⏭ Skip", "gf_mem_skip")], [Markup.button.callback("❌ Cancel", "back_menu")]]) }
  );
}
bot.action("gf_mem_skip", async (ctx) => { await ctx.answerCbQuery(); const s = getSession(ctx.from.id); updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, members: [], makeAdmin: false, step: "permissions" } }); await askPermissions(ctx); });

async function askAdmin(ctx) {
  const flow = getSession(ctx.from.id).groupFlow;
  await reply(ctx,
    `➕ *Create Groups — Step 8/9*\n━━━━━━━━━━━━━━━━━━━━\n\n👥 *${flow.members.length} member(s)* will be added.\n\n*Make them Admin?*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("✅ Yes", "gf_admin_yes"), Markup.button.callback("❌ No", "gf_admin_no")], [Markup.button.callback("❌ Cancel", "back_menu")]]) }
  );
}
bot.action("gf_admin_yes", async (ctx) => { await ctx.answerCbQuery(); const s = getSession(ctx.from.id); updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, makeAdmin: true, step: "permissions" } }); await askPermissions(ctx); });
bot.action("gf_admin_no",  async (ctx) => { await ctx.answerCbQuery(); const s = getSession(ctx.from.id); updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, makeAdmin: false, step: "permissions" } }); await askPermissions(ctx); });

function permKb(p) {
  return Markup.inlineKeyboard([
    [Markup.button.callback(`💬 Messages: ${p.sendMessages ? "👥 All" : "👑 Admins"}`,         "gf_pt_sendMessages")],
    [Markup.button.callback(`✏️ Edit Info: ${p.editInfo ? "👥 All" : "👑 Admins"}`,            "gf_pt_editInfo")],
    [Markup.button.callback(`➕ Add Members: ${p.addMembers ? "👥 All" : "👑 Admins"}`,        "gf_pt_addMembers")],
    [Markup.button.callback(`🔐 Join Approval: ${p.approveMembers ? "✅ ON" : "❌ OFF"}`,      "gf_pt_approveMembers")],
    [Markup.button.callback("💾 Save & Continue", "gf_perm_save")],
    [Markup.button.callback("❌ Cancel", "back_menu")],
  ]);
}
async function askPermissions(ctx) {
  const p = getSession(ctx.from.id).groupFlow.permissions;
  await reply(ctx,
    `➕ *Create Groups — Step 9/9*\n━━━━━━━━━━━━━━━━━━━━\n\n*Set permissions:*\n_Tap to toggle, then Save._`,
    { parse_mode: "Markdown", ...permKb(p) }
  );
}
["sendMessages", "editInfo", "addMembers", "approveMembers"].forEach((key) => {
  bot.action(`gf_pt_${key}`, async (ctx) => {
    await ctx.answerCbQuery();
    const s = getSession(ctx.from.id);
    const p = { ...s.groupFlow.permissions, [key]: !s.groupFlow.permissions[key] };
    updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, permissions: p } });
    try { await ctx.editMessageReplyMarkup(permKb(p).reply_markup); } catch { await askPermissions(ctx); }
  });
});
bot.action("gf_perm_save", async (ctx) => {
  await ctx.answerCbQuery("✅ Saved!");
  const s = getSession(ctx.from.id);
  updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, step: "confirm" } });
  await showConfirm(ctx);
});

function fmtDis(s) { return !s ? "Off" : s === 86400 ? "24h" : s === 604800 ? "7 Days" : s === 7776000 ? "90 Days" : `${s}s`; }

async function showConfirm(ctx) {
  const flow = getSession(ctx.from.id).groupFlow, p = flow.permissions;
  const prev = flow.numbering
    ? Array.from({ length: Math.min(flow.count, 3) }, (_, i) => `${flow.name} ${i + 1}`).join(", ") + (flow.count > 3 ? ` ...(${flow.count})` : "")
    : `${flow.name} ×${flow.count}`;
  await reply(ctx,
    `✅ *Review — Create Groups*\n━━━━━━━━━━━━━━━━━━━━\n` +
    `📝 Name       : *${flow.name}*\n` +
    `🔢 Count      : ${flow.count} group(s)\n` +
    `🔢 Numbering  : ${flow.numbering ? "Yes" : "No"}\n` +
    `📋 Preview    : _${prev}_\n` +
    `📄 Description: ${flow.description ? `_${flow.description.slice(0, 40)}_` : "None"}\n` +
    `🖼️ Photo      : ${flow.photo ? "✅ Set" : "None"}\n` +
    `⏳ Disappear  : ${fmtDis(flow.disappearing)}\n` +
    `👥 Members    : ${flow.members.length || "None"}${flow.members.length ? ` | Admin: ${flow.makeAdmin ? "Yes" : "No"}` : ""}\n` +
    `━━━━━━━━━━━━━━━━━━━━\n` +
    `💬 Messages   : ${p.sendMessages ? "All" : "Admins"}\n` +
    `✏️ Edit Info   : ${p.editInfo ? "All" : "Admins"}\n` +
    `➕ Add Members : ${p.addMembers ? "All" : "Admins"}\n` +
    `🔐 Approval   : ${p.approveMembers ? "ON" : "OFF"}\n` +
    `━━━━━━━━━━━━━━━━━━━━\n_Everything OK? Tap Create Now._`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("✏️ Edit", "gf_edit_menu")],
      [Markup.button.callback("🚀 Create Now", "gf_create_now")],
      [Markup.button.callback("❌ Cancel", "back_menu")],
    ]) }
  );
}

bot.action("gf_edit_menu", async (ctx) => {
  await ctx.answerCbQuery();
  await reply(ctx, `✏️ *What do you want to edit?*`, { parse_mode: "Markdown", ...Markup.inlineKeyboard([
    [Markup.button.callback("📝 Name",         "ge_name"),         Markup.button.callback("🔢 Count",       "ge_count")],
    [Markup.button.callback("🔢 Numbering",    "ge_numbering"),    Markup.button.callback("📄 Description", "ge_desc")],
    [Markup.button.callback("🖼️ Photo",        "ge_photo"),        Markup.button.callback("⏳ Disappearing","ge_disappearing")],
    [Markup.button.callback("👥 Members",      "ge_members"),      Markup.button.callback("🔐 Permissions", "ge_perms")],
    [Markup.button.callback("🔙 Back to Summary", "gf_back_confirm")],
  ]) });
});
bot.action("gf_back_confirm",   async (ctx) => { await ctx.answerCbQuery(); await showConfirm(ctx); });
bot.action("ge_name",           async (ctx) => { await ctx.answerCbQuery(); updateSession(ctx.from.id, { groupFlow: { ...getSession(ctx.from.id).groupFlow, step: "name_edit" } });          await reply(ctx, `📝 *New name:*`,          { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🔙 Cancel", "gf_back_confirm")]]) }); });
bot.action("ge_count",          async (ctx) => { await ctx.answerCbQuery(); updateSession(ctx.from.id, { groupFlow: { ...getSession(ctx.from.id).groupFlow, step: "count_edit" } });         await reply(ctx, `🔢 *How many groups? (1–50):*`, { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🔙 Cancel", "gf_back_confirm")]]) }); });
bot.action("ge_numbering",      async (ctx) => { await ctx.answerCbQuery(); const s = getSession(ctx.from.id); updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, numbering: !s.groupFlow.numbering, step: "confirm" } }); await showConfirm(ctx); });
bot.action("ge_desc",           async (ctx) => { await ctx.answerCbQuery(); updateSession(ctx.from.id, { groupFlow: { ...getSession(ctx.from.id).groupFlow, step: "description_edit" } });   await reply(ctx, `📄 *New description (or remove):*`, { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("⏭ Remove", "ge_desc_rm")], [Markup.button.callback("🔙 Cancel", "gf_back_confirm")]]) }); });
bot.action("ge_desc_rm",        async (ctx) => { await ctx.answerCbQuery(); updateSession(ctx.from.id, { groupFlow: { ...getSession(ctx.from.id).groupFlow, description: "", step: "confirm" } }); await showConfirm(ctx); });
bot.action("ge_photo",          async (ctx) => { await ctx.answerCbQuery(); updateSession(ctx.from.id, { groupFlow: { ...getSession(ctx.from.id).groupFlow, step: "photo_edit" } });        await reply(ctx, `🖼️ *Send new photo (or remove):*`, { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🗑 Remove Photo", "ge_photo_rm")], [Markup.button.callback("🔙 Cancel", "gf_back_confirm")]]) }); });
bot.action("ge_photo_rm",       async (ctx) => { await ctx.answerCbQuery(); updateSession(ctx.from.id, { groupFlow: { ...getSession(ctx.from.id).groupFlow, photo: null, step: "confirm" } }); await showConfirm(ctx); });
bot.action("ge_disappearing",   async (ctx) => { await ctx.answerCbQuery(); updateSession(ctx.from.id, { groupFlow: { ...getSession(ctx.from.id).groupFlow, step: "disappearing_edit" } }); await reply(ctx, `⏳ *Set disappearing:*`, { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("24h", "ge_dis_86400"), Markup.button.callback("7d", "ge_dis_604800"), Markup.button.callback("90d", "ge_dis_7776000")], [Markup.button.callback("⏭ Off", "ge_dis_0")], [Markup.button.callback("🔙 Cancel", "gf_back_confirm")]]) }); });
[0, 86400, 604800, 7776000].forEach((s) => { bot.action(`ge_dis_${s}`, async (ctx) => { await ctx.answerCbQuery(); updateSession(ctx.from.id, { groupFlow: { ...getSession(ctx.from.id).groupFlow, disappearing: s, step: "confirm" } }); await showConfirm(ctx); }); });
bot.action("ge_members",        async (ctx) => { await ctx.answerCbQuery(); updateSession(ctx.from.id, { groupFlow: { ...getSession(ctx.from.id).groupFlow, step: "members_edit" } });      await reply(ctx, `👥 *New member numbers (one per line):*`, { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("⏭ Remove All", "ge_mem_rm")], [Markup.button.callback("🔙 Cancel", "gf_back_confirm")]]) }); });
bot.action("ge_mem_rm",         async (ctx) => { await ctx.answerCbQuery(); updateSession(ctx.from.id, { groupFlow: { ...getSession(ctx.from.id).groupFlow, members: [], makeAdmin: false, step: "confirm" } }); await showConfirm(ctx); });
bot.action("ge_perms",          async (ctx) => { await ctx.answerCbQuery(); updateSession(ctx.from.id, { groupFlow: { ...getSession(ctx.from.id).groupFlow, step: "permissions_edit" } }); await askPermissions(ctx); });

bot.action("gf_create_now", async (ctx) => {
  await ctx.answerCbQuery("🚀 Starting...");
  const uid  = ctx.from.id;
  const flow = getSession(uid).groupFlow;
  if (!flow?.name || !flow?.count) {
    await reply(ctx, "⚠️ Settings incomplete.", Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]])); return;
  }
  if (getStatus(0) !== "connected") {
    await reply(ctx, "❌ WhatsApp is not connected!", Markup.inlineKeyboard([[Markup.button.callback("📱 Connect", "menu_account")]])); return;
  }

  const jids = flow.members.map((n) => `${n.replace(/[^0-9]/g, "")}@s.whatsapp.net`);
  startTimes.set(uid, Date.now());
  updateSession(uid, { cancelPending: false });
  const pm = await ctx.reply(`🚀 *Creating ${flow.count} group(s)...*\n━━━━━━━━━━━━━━━━━━━━\n⏳ Starting...`, { parse_mode: "Markdown" });
  await showCancelBtn(ctx);
  const created = [], failed = [];
  let cancelled = false;

  for (let i = 0; i < flow.count; i++) {
    if (isCancelled(uid)) { cancelled = true; break; }
    const gname = flow.numbering ? `${flow.name} ${i + 1}` : flow.name;
    try {
      await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
        `🚀 *Creating...*\n━━━━━━━━━━━━━━━━━━━━\n✅ Done: ${i}/${flow.count}\n⚙️ ${gname}\n${bar(i, flow.count)}`,
        { parse_mode: "Markdown" });
    } catch (_) {}
    try {
      const r   = await createGroup(0, gname, jids);
      const gid = r.id;
      await sleep(2000);
      if (flow.description) { await updateGroupDescription(0, gid, flow.description).catch(() => {}); await sleep(500); }
      if (flow.photo)       { await updateGroupPhoto(0, gid, flow.photo).catch(() => {});               await sleep(500); }
      if (flow.disappearing){ await setDisappearingMessages(0, gid, flow.disappearing).catch(() => {}); await sleep(500); }
      if (flow.makeAdmin && jids.length) {
        await makeAdminByNumbers(0, gid, flow.members).catch(() => {});
        await sleep(1000);
      }
      await setGroupPermissions(0, gid, flow.permissions).catch(() => {});
      let link = ""; try { link = await getGroupInviteLink(0, gid); } catch { link = "(unavailable)"; }
      created.push({ name: gname, link });
    } catch (err) {
      console.error("[CreateGroup]", err.message);
      failed.push(gname);
    }
    await sleep(2000);
  }

  await removeCancelBtn(ctx);
  try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
    `✅ *Done!*  Created: ${created.length}  |  Failed: ${failed.length}`, { parse_mode: "Markdown" }); } catch (_) {}

  const linkLines = created.map((g, i) => `*${i + 1}.* ${g.name}\n   ${g.link}`);
  const extraLines = [];
  if (created.length > 0) {
    extraLines.push(`🔗 *Created Groups & Links:*`);
    extraLines.push(...linkLines.slice(0, 25));
    if (linkLines.length > 25) extraLines.push(`_...and ${linkLines.length - 25} more_`);
  }
  if (failed.length) extraLines.push(`❌ *Failed:* ${failed.slice(0, 10).join(", ")}`);

  await sendSummary(ctx, { feature: "create_groups", total: flow.count, success: created.length, failed: failed.length, cancelled, extra: extraLines });
  updateSession(uid, { groupFlow: null });
});

[1, 5, 10, 20, 50].forEach((n) => {
  bot.action(`gf_count_${n}`, async (ctx) => {
    await ctx.answerCbQuery();
    const s = getSession(ctx.from.id);
    updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, count: n, step: "numbering" } });
    await askNumbering(ctx);
  });
});

// ══════════════════════════════════════════════════════════════════════════
// ─── ADD MEMBERS — VCF FLOW ───────────────────────────────────────────────
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
  const flow  = getSession(ctx.from.id).featureFlow;
  const idx   = flow.currentVcfIdx || 0;
  const total = (flow.links || []).length;
  if (idx >= total) { await runAddMembersFromVcfs(ctx); return; }
  const code = flow.links[idx];
  updateSession(ctx.from.id, { awaitingVcf: { feature: "add_members", step: "am_vcf", linkIdx: idx } });
  await reply(ctx,
    `➕ *Add Members — VCF ${idx + 1}/${total}*\n━━━━━━━━━━━━━━━━━━━━\n\n📎 Send VCF file for Group ${idx + 1}:\n\`https://chat.whatsapp.com/${code}\``,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("⏭ Skip This Group", "am_skip_vcf")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
  );
}

bot.action("am_skip_vcf", async (ctx) => {
  await ctx.answerCbQuery("Skipped");
  const uid  = ctx.from.id;
  const flow = getSession(uid).featureFlow;
  const newVcfs = [...(flow.vcfs || [])];
  newVcfs[flow.currentVcfIdx || 0] = null;
  updateSession(uid, { featureFlow: { ...flow, currentVcfIdx: (flow.currentVcfIdx || 0) + 1, vcfs: newVcfs }, awaitingVcf: null });
  await askNextVcf(ctx);
});

async function runAddMembersFromVcfs(ctx) {
  const uid  = ctx.from.id;
  const flow = getSession(uid).featureFlow;
  const links = flow.links || [], vcfs = flow.vcfs || [], total = links.length;
  startTimes.set(uid, Date.now());
  updateSession(uid, { cancelPending: false, awaitingVcf: null });

  const pm = await ctx.reply(`➕ *Adding members — ${total} group(s)...*\n━━━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`, { parse_mode: "Markdown" });
  await showCancelBtn(ctx);

  let doneGroups = 0, failedGroups = 0, totAdded = 0, totFailed = 0, totSkipped = 0, cancelled = false;
  const summaryLines = [];

  for (let i = 0; i < total; i++) {
    if (isCancelled(uid)) { cancelled = true; break; }
    const contacts = vcfs[i];
    if (!contacts?.length) { summaryLines.push(`⏭ Group ${i + 1}: No VCF`); continue; }
    try {
      await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
        `➕ *Adding...*\n━━━━━━━━━━━━━━━━━━━━\n✅ Groups: ${doneGroups}/${total}\n⚙️ Group ${i + 1} — ${contacts.length} numbers\n${bar(i, total)}`,
        { parse_mode: "Markdown" });
    } catch (_) {}
    try {
      const info = await getGroupInfoFromLink(0, links[i]);
      if (!info) throw new Error("Invalid/expired link");
      const result = await addMembersToGroup(0, info.id, contacts.map((c) => c.phone), flow.addMode === "onebyone");
      totAdded += result.added; totFailed += result.failed; totSkipped += result.skipped; doneGroups++;
      summaryLines.push(`✅ *${info.name}*: +${result.added} added  ❌${result.failed}  ⏭${result.skipped}`);
    } catch (err) { failedGroups++; summaryLines.push(`❌ Group ${i + 1}: ${err.message}`); }
    await sleep(2000);
  }

  await removeCancelBtn(ctx);
  try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
    `✅ *Done!*  Added: ${totAdded}  |  Failed: ${totFailed}  |  Skipped: ${totSkipped}`, { parse_mode: "Markdown" }); } catch (_) {}

  const extraLines = [
    `➕ *Total added: ${totAdded}*`,
    `❌ *Failed: ${totFailed}*`,
    `⏭ *Privacy blocked: ${totSkipped}*`,
    ...summaryLines.slice(0, 20),
  ];
  if (summaryLines.length > 20) extraLines.push(`_...and ${summaryLines.length - 20} more_`);
  await sendSummary(ctx, { feature: "add_members", total, success: doneGroups, failed: failedGroups, cancelled, extra: extraLines });
  updateSession(uid, { featureFlow: null, awaitingVcf: null });
}

// ══════════════════════════════════════════════════════════════════════════
// ─── TEXT HANDLER ─────────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

bot.on("text", async (ctx) => {
  const uid  = ctx.from.id;
  const s    = getSession(uid);
  const text = ctx.message.text.trim();
  if (text.startsWith("/")) return;

  // ── WA phone input ────────────────────────────────────────────────────
  if (s.awaitingPhoneForIndex !== null && s.awaitingPhoneForIndex !== undefined) {
    const phone = text.replace(/[^0-9]/g, "");
    if (phone.length < 10) {
      await ctx.reply(`❌ Invalid number. Example: \`919876543210\``, { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) });
      return;
    }
    updateSession(uid, { awaitingPhoneForIndex: null });
    const wm = await ctx.reply(`⏳ *Generating pairing code...*`, { parse_mode: "Markdown" });

    pendingPairingCbs.set(0, async (code) => {
      try { await ctx.telegram.deleteMessage(ctx.chat.id, wm.message_id); } catch (_) {}
      if (!code) {
        await ctx.reply(`❌ *Failed to generate code. Please try again.*`, { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🔄 Try Again", "menu_account")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]) });
        return;
      }
      await ctx.reply(
        `🔑 *Pairing Code*\n━━━━━━━━━━━━━━━━━━━━\n\n\`${code}\`\n\n━━━━━━━━━━━━━━━━━━━━\n*How to link:*\n1️⃣ Open WhatsApp\n2️⃣ Settings → Linked Devices → Link a Device\n3️⃣ Tap "Link with phone number"\n4️⃣ Enter the code above\n\n⚠️ Code expires in *60 seconds*!\n⏳ Waiting for connection...`,
        { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🔄 New Code", "reset_wa")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
      );
    });
    pendingReadyCbs.set(0, async () => { await sendMainMenu(ctx); });
    connectAccount(0, phone).catch(async (err) => {
      pendingPairingCbs.delete(0); pendingReadyCbs.delete(0);
      await ctx.reply(`❌ Error: \`${err.message}\``, { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) });
    });
    return;
  }

  // ── Join Groups ───────────────────────────────────────────────────────
  if (s.joinFlow?.step === "links") {
    const codes = extractCodes(text);
    if (!codes.length) {
      await ctx.reply(`❌ *No valid link found.*\nFormat: \`https://chat.whatsapp.com/XXXXX\``, { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🔙 Try Again", "join_groups_start")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]) });
      return;
    }
    updateSession(uid, { joinFlow: null });
    startTimes.set(uid, Date.now());
    const pm = await ctx.reply(`🔗 *Joining ${codes.length} group(s)...*\n━━━━━━━━━━━━━━━━━━━━\n${bar(0, codes.length)}`, { parse_mode: "Markdown" });
    await showCancelBtn(ctx);
    let joined = 0, failed = 0, failedLinks = [], cancelled = false;
    for (let i = 0; i < codes.length; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
        `🔗 *Joining...*\n━━━━━━━━━━━━━━━━━━━━\n✅ ${joined}  ❌ ${failed}\n⚙️ Group ${i + 1}/${codes.length}\n${bar(i, codes.length)}`,
        { parse_mode: "Markdown" }); } catch (_) {}
      try { await joinGroupViaLink(0, codes[i]); joined++; }
      catch (_) { failed++; failedLinks.push(`https://chat.whatsapp.com/${codes[i]}`); }
      await sleep(2000);
    }
    await removeCancelBtn(ctx);
    try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
      `✅ *Done!*  Joined: ${joined}  |  Failed: ${failed}`, { parse_mode: "Markdown" }); } catch (_) {}
    const extraLines = [];
    if (failedLinks.length) extraLines.push(`❌ Failed links:\n${failedLinks.slice(0, 10).join("\n")}`);
    await sendSummary(ctx, { feature: "join_groups", total: codes.length, success: joined, failed, cancelled, extra: extraLines });
    return;
  }

  // ── Similar Groups — Custom Keyword ───────────────────────────────────
  if (s.featureFlow?.step === "similar_query") {
    const kw = text.toLowerCase();
    try {
      const allGroups = s.featureFlow.allGroups?.length ? s.featureFlow.allGroups : await getAllGroupsWithDetails(0);
      const filtered  = allGroups.filter((g) => g.name.toLowerCase().includes(kw));
      if (!filtered.length) {
        await ctx.reply(`❌ *"${text}"* matched no groups.`, { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🔙 Try Again", "gs_sim_custom")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]) });
        return;
      }
      updateSession(uid, { featureFlow: { ...s.featureFlow, allGroups, selectedIds: filtered.map((g) => g.id), keyword: kw, step: "confirm" } });
      await ctx.reply(
        `✅ *${filtered.length} group(s) matched:*\n━━━━━━━━━━━━━━━━━━━━\n${filtered.slice(0, 15).map((g, i) => `${i + 1}. ${g.name}`).join("\n")}${filtered.length > 15 ? `\n_...and ${filtered.length - 15} more_` : ""}`,
        { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🚀 Proceed", "gs_sim_proceed")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
      );
    } catch (err) { await ctx.reply(`❌ Error: ${err.message}`); }
    return;
  }

  // ── Make Admin numbers ────────────────────────────────────────────────
  if (s.featureFlow?.step === "admin_numbers") {
    const nums = text.split(/[\n,\s]+/).map((n) => n.replace(/[^0-9]/g, "")).filter((n) => n.length >= 10);
    if (!nums.length) { await ctx.reply("⚠️ No valid number found. Country code is required."); return; }
    const flow = s.featureFlow;
    updateSession(uid, { featureFlow: { ...flow, adminNumbers: nums, step: "executing" } });
    await ctx.reply(`✅ *${nums.length} number(s) found. Processing...*`, { parse_mode: "Markdown" });
    await runFeature(ctx, flow.feature, flow.selectedIds, flow.allGroups, nums);
    return;
  }

  // ── Demote Admin numbers ──────────────────────────────────────────────
  if (s.featureFlow?.step === "demote_numbers") {
    const nums = text.split(/[\n,\s]+/).map((n) => n.replace(/[^0-9]/g, "")).filter((n) => n.length >= 10);
    if (!nums.length) { await ctx.reply("⚠️ No valid number found."); return; }
    const flow = s.featureFlow;
    updateSession(uid, { featureFlow: { ...flow, adminNumbers: nums, step: "executing" } });
    await ctx.reply(`✅ *${nums.length} number(s) found. Processing...*`, { parse_mode: "Markdown" });
    await runFeature(ctx, "demote_admin", flow.selectedIds, flow.allGroups, nums);
    return;
  }

  // ── Auto Accept — Custom Duration ─────────────────────────────────────
  if (s.featureFlow?.step === "aa_custom_duration") {
    const mins = parseInt(text, 10);
    if (isNaN(mins) || mins < 1) { await ctx.reply("⚠️ Enter a valid number of minutes. Example: `120`", { parse_mode: "Markdown" }); return; }
    const flow  = s.featureFlow;
    const secs  = mins * 60;
    const label = mins >= 60 ? `${mins / 60}h` : `${mins}min`;
    updateSession(uid, { featureFlow: { ...flow, aaDuration: secs, step: "aa_confirm" } });
    await ctx.reply(
      `⏰ *Auto Accept — Confirm*\n━━━━━━━━━━━━━━━━━━━━\n📁 Groups  : *${flow.selectedIds.length}*\n⏱ Duration : *${label}*`,
      { parse_mode: "Markdown", ...Markup.inlineKeyboard([
        [Markup.button.callback("▶️ Start Auto Accept", "aa_start")],
        [Markup.button.callback("🔙 Change Duration",   "aa_back_duration")],
        [Markup.button.callback("🏠 Main Menu",         "back_menu")],
      ]) }
    );
    return;
  }

  // ── Add Members — Links ───────────────────────────────────────────────
  if (s.featureFlow?.step === "am_links") {
    const codes = extractCodes(text);
    if (!codes.length) { await ctx.reply("❌ No valid link found.\n`https://chat.whatsapp.com/ABC`", { parse_mode: "Markdown" }); return; }
    updateSession(uid, { featureFlow: { ...s.featureFlow, links: codes, currentVcfIdx: 0, vcfs: [], step: "am_mode" } });
    await ctx.reply(
      `➕ *Add Members — ${codes.length} group(s) detected*\n━━━━━━━━━━━━━━━━━━━━\n\n*How do you want to add members?*`,
      { parse_mode: "Markdown", ...Markup.inlineKeyboard([
        [Markup.button.callback("🐢 One-by-One (Safe, slow)", "am_mode_onebyone")],
        [Markup.button.callback("⚡ Bulk (Fast)",              "am_mode_bulk")],
        [Markup.button.callback("🏠 Main Menu",               "back_menu")],
      ]) }
    );
    return;
  }

  // ── Change Name — Base Name ───────────────────────────────────────────
  if (s.featureFlow?.step === "cn_random_name") {
    const name = text.slice(0, 100);
    updateSession(uid, { featureFlow: { ...s.featureFlow, cnBaseName: name, step: "cn_random_numbering" } });
    await ctx.reply(
      `✏️ *Change Name*\n━━━━━━━━━━━━━━━━━━━━\nBase name: *${name}*\n\n*Add numbering?*\nYes → _${name} 1, ${name} 2..._\nNo  → All groups same name`,
      { parse_mode: "Markdown", ...Markup.inlineKeyboard([
        [Markup.button.callback("✅ Yes — add numbers", "cn_numbering_yes"), Markup.button.callback("❌ No", "cn_numbering_no")],
        [Markup.button.callback("🏠 Main Menu", "back_menu")],
      ]) }
    );
    return;
  }

  // ── Change Name — Links ───────────────────────────────────────────────
  if (s.featureFlow?.step === "cn_random_links") {
    const codes = extractCodes(text);
    if (!codes.length) { await ctx.reply("❌ No valid link found."); return; }
    await runChangeNameRandom(ctx, codes, s.featureFlow.cnBaseName, s.featureFlow.numbering !== false);
    return;
  }

  // ── Change Name — VCF Links ───────────────────────────────────────────
  if (s.featureFlow?.step === "cn_vcf_links") {
    const codes = extractCodes(text);
    if (!codes.length) { await ctx.reply("❌ No valid link found."); return; }
    updateSession(uid, {
      featureFlow: { ...s.featureFlow, links: codes, currentVcfIdx: 0, vcfs: [], step: "cn_vcf_awaiting" },
      awaitingVcf: { feature: "change_name", step: "cn_vcf" },
    });
    await ctx.reply(
      `📛 *Change Name — ${codes.length} links detected*\n━━━━━━━━━━━━━━━━━━━━\n\nNow send VCF files one by one.\n\n📎 *VCF 1/${codes.length}:*`,
      { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
    );
    return;
  }

  // ── CTC Checker — Links ───────────────────────────────────────────────
  if (s.featureFlow?.step === "ctc_links") {
    const codes = extractCodes(text);
    if (!codes.length) { await ctx.reply("❌ No valid link found."); return; }
    updateSession(uid, {
      featureFlow: { ...s.featureFlow, links: codes, step: "ctc_vcf" },
      awaitingVcf: { feature: "ctc_checker", step: "ctc_vcf" },
    });
    await ctx.reply(
      `🔍 *CTC Checker — ${codes.length} links detected*\n━━━━━━━━━━━━━━━━━━━━\n\nNow send your *trusted contacts VCF* file:`,
      { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]) }
    );
    return;
  }

  // ── Create Groups — Text Steps ────────────────────────────────────────
  const flow = s.groupFlow;
  if (!flow) { await sendMainMenu(ctx); return; }

  if (flow.step === "name") {
    const name = text.slice(0, 100);
    updateSession(uid, { groupFlow: { ...flow, name, step: "count" } });
    await ctx.reply(
      `➕ *Create Groups — Step 2/9*\n━━━━━━━━━━━━━━━━━━━━\n\n✅ Name: *${name}*\n\n*How many groups? (1–50)*`,
      { parse_mode: "Markdown", ...Markup.inlineKeyboard([
        [1, 5, 10, 20, 50].map((n) => Markup.button.callback(`${n}`, `gf_count_${n}`)),
        [Markup.button.callback("❌ Cancel", "back_menu")],
      ]) }
    );
    return;
  }
  if (flow.step === "name_edit")  { updateSession(uid, { groupFlow: { ...flow, name: text.slice(0, 100), step: "confirm" } }); await showConfirm(ctx); return; }
  if (flow.step === "count" || flow.step === "count_edit") {
    const n = parseInt(text, 10);
    if (isNaN(n) || n < 1 || n > 50) { await ctx.reply("⚠️ Enter a number between 1 and 50."); return; }
    if (flow.step === "count_edit") { updateSession(uid, { groupFlow: { ...flow, count: n, step: "confirm" } }); await showConfirm(ctx); }
    else { updateSession(uid, { groupFlow: { ...flow, count: n, step: "numbering" } }); await askNumbering(ctx); }
    return;
  }
  if (flow.step === "description")      { updateSession(uid, { groupFlow: { ...flow, description: text.slice(0, 512), step: "photo" } }); await askPhoto(ctx); return; }
  if (flow.step === "description_edit") { updateSession(uid, { groupFlow: { ...flow, description: text.slice(0, 512), step: "confirm" } }); await showConfirm(ctx); return; }
  if (flow.step === "members" || flow.step === "members_edit") {
    const nums = text.split(/[\n,\s]+/).map((n) => n.replace(/[^0-9]/g, "")).filter((n) => n.length >= 10);
    if (!nums.length) { await ctx.reply("⚠️ No valid number found."); return; }
    if (flow.step === "members_edit") { updateSession(uid, { groupFlow: { ...flow, members: nums, step: "confirm" } }); await showConfirm(ctx); }
    else { updateSession(uid, { groupFlow: { ...flow, members: nums, step: "admin" } }); await askAdmin(ctx); }
    return;
  }

  await sendMainMenu(ctx);
});

// ══════════════════════════════════════════════════════════════════════════
// ─── DOCUMENT HANDLER (VCF) ───────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

bot.on("document", async (ctx) => {
  const uid = ctx.from.id, s = getSession(uid);
  const doc = ctx.message.document;
  const isVcf =
    doc.mime_type === "text/vcard" ||
    doc.mime_type === "text/x-vcard" ||
    doc.file_name?.toLowerCase().endsWith(".vcf");
  const awaitingVcf = s.awaitingVcf;

  // ── Add Members VCF ────────────────────────────────────────────────────
  if (awaitingVcf?.feature === "add_members" && s.featureFlow?.step === "am_awaiting_vcf") {
    if (!isVcf) { await ctx.reply("⚠️ Please send a .vcf file."); return; }
    try {
      const contacts = parseVcf((await downloadFile(ctx, doc.file_id)).toString("utf8"));
      if (!contacts.length) { await ctx.reply("⚠️ No valid numbers found in the VCF."); return; }
      const flow    = s.featureFlow;
      const idx     = flow.currentVcfIdx || 0;
      const newVcfs = [...(flow.vcfs || [])];
      newVcfs[idx] = contacts;
      const nextIdx    = idx + 1;
      const totalLinks = (flow.links || []).length;
      updateSession(uid, { featureFlow: { ...flow, vcfs: newVcfs, currentVcfIdx: nextIdx }, awaitingVcf: null });
      await ctx.reply(`✅ *VCF received!* ${contacts.length} numbers found.`, { parse_mode: "Markdown" });
      if (nextIdx >= totalLinks) { await runAddMembersFromVcfs(ctx); }
      else { await askNextVcf(ctx); }
    } catch (err) { await ctx.reply(`❌ VCF read error: ${err.message}`); }
    return;
  }

  // ── Change Name VCF ────────────────────────────────────────────────────
  if (awaitingVcf?.feature === "change_name" && s.featureFlow?.step === "cn_vcf_awaiting") {
    if (!isVcf) { await ctx.reply("⚠️ Please send a .vcf file."); return; }
    try {
      const vcfName = (doc.file_name || "").replace(/\.vcf$/i, "").trim() || "Unnamed";
      const contacts = parseVcf((await downloadFile(ctx, doc.file_id)).toString("utf8"));
      const flow    = s.featureFlow;
      const idx     = flow.currentVcfIdx || 0;
      const newVcfs = [...(flow.vcfs || [])];
      newVcfs[idx] = { name: vcfName, contacts };
      const nextIdx    = idx + 1;
      const totalLinks = (flow.links || []).length;
      updateSession(uid, { featureFlow: { ...flow, vcfs: newVcfs, currentVcfIdx: nextIdx } });
      await ctx.reply(`✅ *VCF "${vcfName}" received!*  ${contacts.length} numbers.`, { parse_mode: "Markdown" });
      if (nextIdx >= totalLinks) {
        updateSession(uid, { awaitingVcf: null });
        await runChangeNameAsVcf(ctx);
      } else {
        await ctx.reply(`📎 *VCF ${nextIdx + 1}/${totalLinks}:*`, { parse_mode: "Markdown",
          ...Markup.inlineKeyboard([[Markup.button.callback("⏭ Skip", "cn_vcf_skip_next")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]) });
      }
    } catch (err) { await ctx.reply(`❌ VCF read error: ${err.message}`); }
    return;
  }

  // ── CTC Checker VCF ────────────────────────────────────────────────────
  if (awaitingVcf?.feature === "ctc_checker" && s.featureFlow?.step === "ctc_vcf") {
    if (!isVcf) { await ctx.reply("⚠️ Please send a .vcf file."); return; }
    try {
      const contacts = parseVcf((await downloadFile(ctx, doc.file_id)).toString("utf8"));
      updateSession(uid, { featureFlow: { ...s.featureFlow, vcfContacts: contacts, step: "ctc_running" }, awaitingVcf: null });
      await ctx.reply(`✅ *VCF received!*  ${contacts.length} trusted contacts.\n\n⏳ *Checking...*`, { parse_mode: "Markdown" });
      await runCtcChecker(ctx);
    } catch (err) { await ctx.reply(`❌ VCF read error: ${err.message}`); }
    return;
  }
});

// ─── Change Name as VCF — Skip ────────────────────────────────────────────
bot.action("cn_vcf_skip_next", async (ctx) => {
  await ctx.answerCbQuery("Skipped");
  const uid  = ctx.from.id;
  const flow = getSession(uid).featureFlow;
  const idx  = flow.currentVcfIdx || 0;
  const newVcfs = [...(flow.vcfs || [])];
  newVcfs[idx] = null;
  const nextIdx    = idx + 1;
  const totalLinks = (flow.links || []).length;
  updateSession(uid, { featureFlow: { ...flow, vcfs: newVcfs, currentVcfIdx: nextIdx } });
  if (nextIdx >= totalLinks) { updateSession(uid, { awaitingVcf: null }); await runChangeNameAsVcf(ctx); }
  else {
    await ctx.reply(`📎 *VCF ${nextIdx + 1}/${totalLinks}:*`, { parse_mode: "Markdown",
      ...Markup.inlineKeyboard([[Markup.button.callback("⏭ Skip", "cn_vcf_skip_next")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]) });
  }
});

// ─── Change Name as VCF — Execution ──────────────────────────────────────
async function runChangeNameAsVcf(ctx) {
  const uid  = ctx.from.id;
  const flow = getSession(uid).featureFlow;
  const links = flow.links || [], vcfs = flow.vcfs || [], total = links.length;
  startTimes.set(uid, Date.now());
  updateSession(uid, { cancelPending: false });

  const pm = await ctx.reply(`📛 *Renaming ${total} group(s) by VCF...*\n━━━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`, { parse_mode: "Markdown" });
  await showCancelBtn(ctx);

  let done = 0, failed = 0, skipped = 0, cancelled = false;
  const details = [];

  for (let i = 0; i < total; i++) {
    if (isCancelled(uid)) { cancelled = true; break; }
    const vcfEntry = vcfs[i];
    if (!vcfEntry) { skipped++; details.push(`⏭ Group ${i + 1}: No VCF`); continue; }
    try {
      await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
        `📛 *Renaming...*\n━━━━━━━━━━━━━━━━━━━━\n✅ ${done}/${total}\n→ "${vcfEntry.name}"\n${bar(i, total)}`,
        { parse_mode: "Markdown" });
    } catch (_) {}
    try {
      const info = await getGroupInfoFromLink(0, links[i]);
      if (!info) throw new Error("Invalid link");
      const vcfPhones    = new Set(vcfEntry.contacts.map((c) => c.phone));
      const memberPhones = new Set(info.participants.map((p) => p.id.replace(/:\d+@/, "@").replace("@s.whatsapp.net", "")));
      const pending      = await getPendingForGroup(0, info.id);
      const pendingPhones = new Set(pending.map((p) => p.phone));
      const matches = [...vcfPhones].some((ph) => memberPhones.has(ph) || pendingPhones.has(ph));
      if (matches) {
        await renameGroup(0, info.id, vcfEntry.name);
        done++; details.push(`✅ ${info.name} → *${vcfEntry.name}*`);
      } else {
        skipped++; details.push(`⏭ ${info.name}: no contact match`);
      }
    } catch (err) { failed++; details.push(`❌ Group ${i + 1}: ${err.message}`); }
    await sleep(1200);
  }

  await removeCancelBtn(ctx);
  try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
    `✅ *Rename Done! ${done}/${total}*`, { parse_mode: "Markdown" }); } catch (_) {}

  const extraLines = [`⏭ *No match (skipped): ${skipped}*`, ...details.slice(0, 25)];
  if (details.length > 25) extraLines.push(`_...and ${details.length - 25} more_`);
  await sendSummary(ctx, { feature: "change_name", total, success: done, failed, cancelled, extra: extraLines });
  updateSession(uid, { featureFlow: null, awaitingVcf: null });
}

// ─── CTC Checker — Execution ──────────────────────────────────────────────
async function runCtcChecker(ctx) {
  const uid  = ctx.from.id;
  const flow = getSession(uid).featureFlow;
  const links = flow.links || [], total = links.length;
  const trustedPhones = new Set((flow.vcfContacts || []).map((c) => c.phone));
  startTimes.set(uid, Date.now());
  updateSession(uid, { cancelPending: false });

  const pm = await ctx.reply(`🔍 *CTC Check — ${total} group(s)...*\n━━━━━━━━━━━━━━━━━━━━\n${bar(0, total)}`, { parse_mode: "Markdown" });
  await showCancelBtn(ctx);

  let done = 0, failed = 0, cancelled = false;
  const reportLines = [], allUnknown = [];

  for (let i = 0; i < total; i++) {
    if (isCancelled(uid)) { cancelled = true; break; }
    try {
      await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
        `🔍 *Checking...*\n━━━━━━━━━━━━━━━━━━━━\n✅ ${done}/${total}\n⚙️ Group ${i + 1}/${total}\n${bar(i, total)}`,
        { parse_mode: "Markdown" });
    } catch (_) {}
    try {
      const info = await getGroupInfoFromLink(0, links[i]);
      if (!info) throw new Error("Invalid link");
      const pending = await getPendingForGroup(0, info.id);
      const unknown = pending.filter((p) => !trustedPhones.has(p.phone));
      done++;
      if (unknown.length) {
        allUnknown.push(...unknown.map((u) => u.phone));
        reportLines.push(`⚠️ *${info.name}*: ${unknown.length} unknown`);
      } else {
        reportLines.push(`✅ *${info.name}*: ${pending.length} pending, all trusted`);
      }
    } catch (err) { failed++; reportLines.push(`❌ Group ${i + 1}: ${err.message}`); }
    await sleep(800);
  }

  await removeCancelBtn(ctx);
  const uniqueUnknown = [...new Set(allUnknown)];
  try { await bot.telegram.editMessageText(ctx.chat.id, pm.message_id, undefined,
    `🔍 *CTC Check Done!*  ⚠️ Unknown: ${uniqueUnknown.length}`, { parse_mode: "Markdown" }); } catch (_) {}

  const extraLines = [
    `📁 Groups checked: *${done}*`,
    `✅ Trusted in VCF: *${trustedPhones.size}*`,
    `⚠️ Unknown numbers: *${uniqueUnknown.length}*`,
    ...reportLines.slice(0, 20),
  ];
  if (uniqueUnknown.length > 0) {
    extraLines.push(`━━━━━━━━━━━━━━━━━━━━`);
    extraLines.push(`*Unknown Numbers:*`);
    extraLines.push(...uniqueUnknown.slice(0, 20).map((p) => `+${p}`));
    if (uniqueUnknown.length > 20) extraLines.push(`_...and ${uniqueUnknown.length - 20} more_`);
  }
  await sendSummary(ctx, { feature: "ctc_checker", total, success: done, failed, cancelled, extra: extraLines });
  updateSession(uid, { featureFlow: null, awaitingVcf: null });
}

// ─── Photo Handler ─────────────────────────────────────────────────────────
bot.on("photo", async (ctx) => {
  const uid  = ctx.from.id;
  const flow = getSession(uid).groupFlow;
  if (!flow || (flow.step !== "photo" && flow.step !== "photo_edit")) return;
  try {
    const p = ctx.message.photo[ctx.message.photo.length - 1];
    const u = await ctx.telegram.getFileLink(p.file_id);
    const r = await fetch(u.href);
    const buf = Buffer.from(await r.arrayBuffer());
    const ns = flow.step === "photo_edit" ? "confirm" : "disappearing";
    updateSession(uid, { groupFlow: { ...flow, photo: buf, step: ns } });
    await ctx.reply("✅ *Photo saved!*", { parse_mode: "Markdown" });
    if (ns === "confirm") await showConfirm(ctx); else await askDisappearing(ctx);
  } catch (err) {
    console.error("[Photo]", err.message);
    await ctx.reply("❌ Failed to save photo. Please try again.");
  }
});

bot.catch((err) => console.error("[Bot Error]", err.message));

// ─── Health Server ─────────────────────────────────────────────────────────
const app  = express();
const PORT = process.env.PORT || 3000;

app.get("/", (_, res) => res.send(
  `<html><head><title>WA Bot</title></head>` +
  `<body style="font-family:sans-serif;text-align:center;padding:50px;background:#0f172a;color:#f1f5f9">` +
  `<h2 style="color:#38bdf8">🤖 WA Group Manager Bot</h2>` +
  `<p style="color:#4ade80;font-size:1.2em">Running 🟢</p>` +
  `<p>WhatsApp: ${getConnectedCount() > 0 ? '<span style="color:#4ade80">Connected ✅</span>' : '<span style="color:#f87171">Disconnected ❌</span>'}</p>` +
  `</body></html>`
));
app.get("/health", (_, res) => res.json({
  status: "ok",
  whatsapp: getStatus(0),
  phone: getPhone(0) || null,
  ts: new Date().toISOString(),
}));
app.listen(PORT, () => console.log(`[HTTP] Health server on port ${PORT}`));

function selfPing() {
  const url  = process.env.RENDER_EXTERNAL_URL || process.env.SELF_URL;
  if (!url) return;
  const full = url.startsWith("http") ? url : `https://${url}`;
  (full.startsWith("https") ? https : http)
    .get(`${full}/health`, (r) => console.log(`[Ping] ${r.statusCode}`))
    .on("error", (e) => console.error("[Ping]", e.message));
}
setTimeout(() => { selfPing(); setInterval(selfPing, 120000); }, 60000);

async function main() {
  await connectDB();
  await reconnectSavedAccounts();
  await bot.launch({ dropPendingUpdates: true });
  console.log(`[Bot] WA Group Manager Bot running! Owner: ${OWNER_ID || "NOT SET"}`);
}

main().catch((err) => { console.error("Fatal:", err.message); process.exit(1); });
process.once("SIGINT",  () => bot.stop("SIGINT"));
process.once("SIGTERM", () => bot.stop("SIGTERM"));
