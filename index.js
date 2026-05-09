/**
 * WhatsApp Group Creator Bot
 * - 1 WhatsApp account
 * - Create up to 50 groups at once with full settings
 * - MongoDB persistence
 * - Owner-only access
 */

const { Telegraf, Markup } = require("telegraf");
const { connectDB } = require("./src/db");
const { getSession, updateSession, resetSession, defaultGroupFlow } = require("./src/session");
const {
  setCallbacks, getStatus, getPhone, getConnectedCount,
  connectAccount, disconnectAccount, reconnectSavedAccounts,
  createGroup, updateGroupDescription, updateGroupPhoto,
  setDisappearingMessages, promoteToAdmin, setGroupPermissions, getGroupInviteLink,
} = require("./src/whatsapp-manager");
const express = require("express");
const http = require("http");
const https = require("https");

// ─── Env ─────────────────────────────────────────────────────────────────
const TOKEN = process.env.TELEGRAM_BOT_TOKEN;
if (!TOKEN) { console.error("TELEGRAM_BOT_TOKEN not set!"); process.exit(1); }
const OWNER_ID = parseInt(process.env.OWNER_ID || "0", 10);
if (!OWNER_ID) console.warn("OWNER_ID not set — anyone can use this bot!");

const bot = new Telegraf(TOKEN);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ─── Owner guard ──────────────────────────────────────────────────────────
bot.use(async (ctx, next) => {
  if (OWNER_ID && ctx.from?.id !== OWNER_ID) {
    if (ctx.callbackQuery) await ctx.answerCbQuery("Unauthorized.", { show_alert: true }).catch(() => {});
    else await ctx.reply("This bot is for the owner only.").catch(() => {});
    return;
  }
  return next();
});

// ─── Pairing callbacks ────────────────────────────────────────────────────
const pendingPairingCbs = new Map();
const pendingReadyCbs   = new Map();

setCallbacks({
  onPairingCode: async (index, code) => {
    const cb = pendingPairingCbs.get(index);
    if (cb) { pendingPairingCbs.delete(index); await cb(code); }
  },
  onReady: async (index) => {
    const cb = pendingReadyCbs.get(index);
    if (cb) { pendingReadyCbs.delete(index); await cb(); }
  },
  onDisconnected: async () => {
    console.log("[Bot] WhatsApp disconnected");
  },
});

// ─── Main Menu ────────────────────────────────────────────────────────────
function mainMenu() {
  const connected = getStatus(0) === "connected";
  const phone     = getPhone(0);
  const statusBtn = connected
    ? `✅ WhatsApp: ...${phone.slice(-5)}`
    : `❌ WhatsApp: Not Connected`;

  return Markup.inlineKeyboard([
    [Markup.button.callback(statusBtn, "menu_account")],
    [Markup.button.callback("📋 Create Groups", connected ? "create_groups_start" : "need_connect")],
    [Markup.button.callback("📊 Status", "menu_status")],
  ]);
}

async function sendMainMenu(ctx, text) {
  await ctx.reply(
    text || "👋 *WhatsApp Group Creator Bot*\n\nSelect an option from the menu below:",
    { parse_mode: "Markdown", ...mainMenu() }
  );
}

// ─── Commands ─────────────────────────────────────────────────────────────
bot.start(async (ctx) => sendMainMenu(ctx));
bot.command("menu", async (ctx) => sendMainMenu(ctx));

// ─── Status ───────────────────────────────────────────────────────────────
bot.action("menu_status", async (ctx) => {
  await ctx.answerCbQuery();
  const s    = getStatus(0);
  const p    = getPhone(0);
  const icon = s === "connected" ? "✅" : s === "connecting" ? "⏳" : "❌";
  await ctx.reply(
    `📊 *Status*\n\n${icon} WhatsApp: ${s === "connected" ? `Connected — \`${p}\`` : s}`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🔙 Back to Menu", "back_menu")]]) }
  );
});

// ─── Need connect ─────────────────────────────────────────────────────────
bot.action("need_connect", async (ctx) => {
  await ctx.answerCbQuery("Please connect WhatsApp first!", { show_alert: true });
});

// ─── Account ──────────────────────────────────────────────────────────────
bot.action("menu_account", async (ctx) => {
  await ctx.answerCbQuery();
  const status = getStatus(0);
  const phone  = getPhone(0);

  if (status === "connected") {
    const text = `📱 *WhatsApp Account*\n\n✅ Connected: \`${phone}\`\n\nDo you want to logout?`;
    const kb   = Markup.inlineKeyboard([
      [Markup.button.callback("🔌 Logout", "logout_wa")],
      [Markup.button.callback("🔙 Back to Menu", "back_menu")],
    ]);
    try { await ctx.editMessageText(text, { parse_mode: "Markdown", reply_markup: kb.reply_markup }); }
    catch { await ctx.reply(text, { parse_mode: "Markdown", ...kb }); }

  } else if (status === "connecting") {
    const text = `⏳ *WhatsApp is connecting...*`;
    const kb   = Markup.inlineKeyboard([
      [Markup.button.callback("🔄 Reset", "reset_wa")],
      [Markup.button.callback("🔙 Back to Menu", "back_menu")],
    ]);
    try { await ctx.editMessageText(text, { parse_mode: "Markdown", reply_markup: kb.reply_markup }); }
    catch { await ctx.reply(text, { parse_mode: "Markdown", ...kb }); }

  } else {
    updateSession(ctx.from.id, { awaitingPhoneForIndex: 0 });
    const text =
      `📱 *Connect WhatsApp*\n\n` +
      `Enter your phone number with country code:\n` +
      `Example: \`919876543210\`\n\n` +
      `⚠️ *The pairing code expires in 60 seconds — enter it quickly!*`;
    const kb = Markup.inlineKeyboard([[Markup.button.callback("🔙 Back to Menu", "back_menu")]]);
    try { await ctx.editMessageText(text, { parse_mode: "Markdown", reply_markup: kb.reply_markup }); }
    catch { await ctx.reply(text, { parse_mode: "Markdown", ...kb }); }
  }
});

bot.action("logout_wa", async (ctx) => {
  await ctx.answerCbQuery("Logging out...");
  await ctx.editMessageText("⏳ Logging out...");
  await disconnectAccount(0);
  await ctx.editMessageText("✅ *Logged out successfully!*", { parse_mode: "Markdown" });
  await sleep(600);
  await sendMainMenu(ctx);
});

bot.action("reset_wa", async (ctx) => {
  await ctx.answerCbQuery("Resetting...");
  await disconnectAccount(0);
  updateSession(ctx.from.id, { awaitingPhoneForIndex: 0 });
  await ctx.editMessageText("✅ *Reset done!* Now enter your number:", { parse_mode: "Markdown" });
  await ctx.reply(
    `📱 Enter your number (example: \`919876543210\`):`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🔙 Back to Menu", "back_menu")]]) }
  );
});

// ─── Back ─────────────────────────────────────────────────────────────────
bot.action("back_menu", async (ctx) => {
  await ctx.answerCbQuery();
  updateSession(ctx.from.id, { awaitingPhoneForIndex: null, groupFlow: null });
  await ctx.reply("🏠 *Main Menu:*", { parse_mode: "Markdown", ...mainMenu() });
});

// ═══════════════════════════════════════════════════════════════════════════
// ─── CREATE GROUPS FLOW ───────────────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════

// ─── Step 1: Start & Ask Name ─────────────────────────────────────────────
bot.action("create_groups_start", async (ctx) => {
  await ctx.answerCbQuery();
  if (getStatus(0) !== "connected") {
    await ctx.answerCbQuery("Please connect WhatsApp first!", { show_alert: true });
    return;
  }
  const flow = defaultGroupFlow();
  updateSession(ctx.from.id, { groupFlow: flow });

  await ctx.reply(
    `📋 *Create Groups — Step 1 of 9*\n\n` +
    `*What should the group name be?*\n\n` +
    `_Type a name. Numbering can be added automatically (e.g. MyGroup 1, MyGroup 2)_`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("❌ Cancel", "back_menu")]]) }
  );
});

// ─── Step 3: Numbering ────────────────────────────────────────────────────
function askNumbering(ctx) {
  const flow = getSession(ctx.from.id).groupFlow;
  return ctx.reply(
    `📋 *Create Groups — Step 3 of 9*\n\n` +
    `*Add numbering to group names?*\n\n` +
    `If Yes, groups will be named:\n` +
    `_${flow.name} 1, ${flow.name} 2, ${flow.name} 3..._\n\n` +
    `If No, all groups will be named: _${flow.name}_`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [
        Markup.button.callback("✅ Yes, Add Numbering", "gf_numbering_yes"),
        Markup.button.callback("❌ No", "gf_numbering_no"),
      ],
      [Markup.button.callback("❌ Cancel", "back_menu")],
    ]) }
  );
}

bot.action("gf_numbering_yes", async (ctx) => {
  await ctx.answerCbQuery();
  const s = getSession(ctx.from.id);
  updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, numbering: true, step: "description" } });
  await askDescription(ctx);
});

bot.action("gf_numbering_no", async (ctx) => {
  await ctx.answerCbQuery();
  const s = getSession(ctx.from.id);
  updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, numbering: false, step: "description" } });
  await askDescription(ctx);
});

// ─── Step 4: Description ─────────────────────────────────────────────────
function askDescription(ctx) {
  return ctx.reply(
    `📋 *Create Groups — Step 4 of 9*\n\n` +
    `*Enter a Group Description:*\n\n` +
    `_This description will be set for all groups. You can skip this._`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("⏭️ Skip", "gf_desc_skip")],
      [Markup.button.callback("❌ Cancel", "back_menu")],
    ]) }
  );
}

bot.action("gf_desc_skip", async (ctx) => {
  await ctx.answerCbQuery();
  const s = getSession(ctx.from.id);
  updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, description: "", step: "photo" } });
  await askPhoto(ctx);
});

// ─── Step 5: Photo ───────────────────────────────────────────────────────
function askPhoto(ctx) {
  return ctx.reply(
    `📋 *Create Groups — Step 5 of 9*\n\n` +
    `*Send a Group Photo:*\n\n` +
    `_This photo will be set for all groups. You can skip this._`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("⏭️ Skip", "gf_photo_skip")],
      [Markup.button.callback("❌ Cancel", "back_menu")],
    ]) }
  );
}

bot.action("gf_photo_skip", async (ctx) => {
  await ctx.answerCbQuery();
  const s = getSession(ctx.from.id);
  updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, photo: null, step: "disappearing" } });
  await askDisappearing(ctx);
});

// ─── Step 6: Disappearing Messages ───────────────────────────────────────
function askDisappearing(ctx) {
  return ctx.reply(
    `📋 *Create Groups — Step 6 of 9*\n\n` +
    `*Set Disappearing Messages:*\n\n` +
    `_Choose a duration or skip._`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [
        Markup.button.callback("24 Hours",  "gf_dis_86400"),
        Markup.button.callback("7 Days",    "gf_dis_604800"),
        Markup.button.callback("90 Days",   "gf_dis_7776000"),
      ],
      [Markup.button.callback("⏭️ Skip / Off", "gf_dis_0")],
      [Markup.button.callback("❌ Cancel", "back_menu")],
    ]) }
  );
}

[0, 86400, 604800, 7776000].forEach((sec) => {
  bot.action(`gf_dis_${sec}`, async (ctx) => {
    await ctx.answerCbQuery();
    const s = getSession(ctx.from.id);
    updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, disappearing: sec, step: "members" } });
    await askMembers(ctx);
  });
});

// ─── Step 7: Members ─────────────────────────────────────────────────────
function askMembers(ctx) {
  return ctx.reply(
    `📋 *Create Groups — Step 7 of 9*\n\n` +
    `*Add members to the groups?*\n\n` +
    `Enter phone numbers, one per line (with country code):\n` +
    `\`\`\`\n919876543210\n918765432109\n\`\`\`\n\n` +
    `_You can skip this step._`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("⏭️ Skip", "gf_members_skip")],
      [Markup.button.callback("❌ Cancel", "back_menu")],
    ]) }
  );
}

bot.action("gf_members_skip", async (ctx) => {
  await ctx.answerCbQuery();
  const s = getSession(ctx.from.id);
  updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, members: [], makeAdmin: false, step: "permissions" } });
  await askPermissions(ctx);
});

// ─── Step 8: Admin ───────────────────────────────────────────────────────
function askAdmin(ctx) {
  const flow = getSession(ctx.from.id).groupFlow;
  return ctx.reply(
    `📋 *Create Groups — Step 8 of 9*\n\n` +
    `*${flow.members.length} member(s) will be added.*\n\n` +
    `*Make them Admin?*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [
        Markup.button.callback("✅ Yes, Make Admin", "gf_admin_yes"),
        Markup.button.callback("❌ No", "gf_admin_no"),
      ],
      [Markup.button.callback("❌ Cancel", "back_menu")],
    ]) }
  );
}

bot.action("gf_admin_yes", async (ctx) => {
  await ctx.answerCbQuery();
  const s = getSession(ctx.from.id);
  updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, makeAdmin: true, step: "permissions" } });
  await askPermissions(ctx);
});

bot.action("gf_admin_no", async (ctx) => {
  await ctx.answerCbQuery();
  const s = getSession(ctx.from.id);
  updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, makeAdmin: false, step: "permissions" } });
  await askPermissions(ctx);
});

// ─── Step 9: Permissions ─────────────────────────────────────────────────
function buildPermissionsKeyboard(permissions) {
  return Markup.inlineKeyboard([
    [Markup.button.callback(
      `💬 Send Messages: ${permissions.sendMessages ? "✅ Everyone" : "👑 Admins Only"}`,
      "gf_perm_toggle_sendMessages"
    )],
    [Markup.button.callback(
      `✏️ Edit Group Info: ${permissions.editInfo ? "✅ Everyone" : "👑 Admins Only"}`,
      "gf_perm_toggle_editInfo"
    )],
    [Markup.button.callback(
      `➕ Add Members: ${permissions.addMembers ? "✅ Everyone" : "👑 Admins Only"}`,
      "gf_perm_toggle_addMembers"
    )],
    [Markup.button.callback(
      `🔐 Approve New Members: ${permissions.approveMembers ? "✅ On" : "❌ Off"}`,
      "gf_perm_toggle_approveMembers"
    )],
    [Markup.button.callback("💾 Save Settings", "gf_perm_save")],
    [Markup.button.callback("❌ Cancel", "back_menu")],
  ]);
}

async function askPermissions(ctx) {
  const perm = getSession(ctx.from.id).groupFlow.permissions;
  await ctx.reply(
    `📋 *Create Groups — Step 9 of 9*\n\n` +
    `*Set Group Permissions:*\n\n` +
    `_Tap any button to toggle. Press Save when done._`,
    { parse_mode: "Markdown", ...buildPermissionsKeyboard(perm) }
  );
}

["sendMessages", "editInfo", "addMembers", "approveMembers"].forEach((key) => {
  bot.action(`gf_perm_toggle_${key}`, async (ctx) => {
    await ctx.answerCbQuery();
    const s    = getSession(ctx.from.id);
    const perm = { ...s.groupFlow.permissions };
    perm[key]  = !perm[key];
    updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, permissions: perm } });
    try {
      await ctx.editMessageReplyMarkup(buildPermissionsKeyboard(perm).reply_markup);
    } catch {
      await askPermissions(ctx);
    }
  });
});

bot.action("gf_perm_save", async (ctx) => {
  await ctx.answerCbQuery("Settings saved!");
  const s = getSession(ctx.from.id);
  updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, step: "confirm" } });
  await showConfirm(ctx);
});

// ─── Summary / Confirm ────────────────────────────────────────────────────
function fmtDisappearing(sec) {
  if (!sec || sec === 0) return "Off";
  if (sec === 86400)   return "24 Hours";
  if (sec === 604800)  return "7 Days";
  if (sec === 7776000) return "90 Days";
  return `${sec}s`;
}

async function showConfirm(ctx) {
  const flow = getSession(ctx.from.id).groupFlow;
  const perm = flow.permissions;

  const previewNames = flow.numbering
    ? Array.from({ length: Math.min(flow.count, 3) }, (_, i) => `${flow.name} ${i + 1}`).join(", ") +
      (flow.count > 3 ? ` ... (${flow.count} total)` : "")
    : `${flow.name} × ${flow.count}`;

  const summary =
    `✅ *Review Your Settings:*\n` +
    `━━━━━━━━━━━━━━━━━━━━━\n` +
    `📝 *Name:* ${flow.name}\n` +
    `🔢 *Groups:* ${flow.count}\n` +
    `🔢 *Numbering:* ${flow.numbering ? "On" : "Off"}\n` +
    `📋 *Preview:* _${previewNames}_\n` +
    `📄 *Description:* ${flow.description || "_None_"}\n` +
    `🖼️ *Photo:* ${flow.photo ? "Set ✅" : "_None_"}\n` +
    `⏳ *Disappearing:* ${fmtDisappearing(flow.disappearing)}\n` +
    `👥 *Members:* ${flow.members.length > 0 ? `${flow.members.length} number(s)` : "_None_"}\n` +
    `👑 *Make Admin:* ${flow.members.length > 0 ? (flow.makeAdmin ? "Yes" : "No") : "_N/A_"}\n` +
    `━━━━━━━━━━━━━━━━━━━━━\n` +
    `*Permissions:*\n` +
    `💬 Send Messages: ${perm.sendMessages ? "Everyone" : "Admins Only"}\n` +
    `✏️ Edit Group Info: ${perm.editInfo ? "Everyone" : "Admins Only"}\n` +
    `➕ Add Members: ${perm.addMembers ? "Everyone" : "Admins Only"}\n` +
    `🔐 Approve Members: ${perm.approveMembers ? "On" : "Off"}\n` +
    `━━━━━━━━━━━━━━━━━━━━━\n` +
    `_All correct? Press Create Now to start._`;

  await ctx.reply(summary, {
    parse_mode: "Markdown",
    ...Markup.inlineKeyboard([
      [Markup.button.callback("✏️ Edit", "gf_edit_menu")],
      [Markup.button.callback("🚀 Create Now", "gf_create_now")],
      [Markup.button.callback("❌ Cancel", "back_menu")],
    ]),
  });
}

// ─── Edit Menu ────────────────────────────────────────────────────────────
bot.action("gf_edit_menu", async (ctx) => {
  await ctx.answerCbQuery();
  await ctx.reply(
    `✏️ *What would you like to edit?*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("📝 Name", "gf_edit_name"), Markup.button.callback("🔢 Count", "gf_edit_count")],
      [Markup.button.callback("🔢 Numbering", "gf_edit_numbering"), Markup.button.callback("📄 Description", "gf_edit_desc")],
      [Markup.button.callback("🖼️ Photo", "gf_edit_photo"), Markup.button.callback("⏳ Disappearing", "gf_edit_disappearing")],
      [Markup.button.callback("👥 Members", "gf_edit_members"), Markup.button.callback("🔐 Permissions", "gf_edit_perms")],
      [Markup.button.callback("🔙 Back to Summary", "gf_back_confirm")],
    ]) }
  );
});

bot.action("gf_back_confirm", async (ctx) => { await ctx.answerCbQuery(); await showConfirm(ctx); });

bot.action("gf_edit_name", async (ctx) => {
  await ctx.answerCbQuery();
  const s = getSession(ctx.from.id);
  updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, step: "name_edit" } });
  await ctx.reply(`📝 *Enter a new group name:*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🔙 Cancel Edit", "gf_back_confirm")]]) });
});

bot.action("gf_edit_count", async (ctx) => {
  await ctx.answerCbQuery();
  const s = getSession(ctx.from.id);
  updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, step: "count_edit" } });
  await ctx.reply(`🔢 *How many groups? (1–50):*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🔙 Cancel Edit", "gf_back_confirm")]]) });
});

bot.action("gf_edit_numbering", async (ctx) => {
  await ctx.answerCbQuery();
  const s = getSession(ctx.from.id);
  updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, step: "numbering_edit" } });
  await ctx.reply(`🔢 *Add numbering?*`, { parse_mode: "Markdown",
    ...Markup.inlineKeyboard([
      [Markup.button.callback("✅ Yes", "gf_edit_num_yes"), Markup.button.callback("❌ No", "gf_edit_num_no")],
      [Markup.button.callback("🔙 Cancel Edit", "gf_back_confirm")],
    ]) });
});
bot.action("gf_edit_num_yes", async (ctx) => {
  await ctx.answerCbQuery();
  const s = getSession(ctx.from.id);
  updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, numbering: true, step: "confirm" } });
  await showConfirm(ctx);
});
bot.action("gf_edit_num_no", async (ctx) => {
  await ctx.answerCbQuery();
  const s = getSession(ctx.from.id);
  updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, numbering: false, step: "confirm" } });
  await showConfirm(ctx);
});

bot.action("gf_edit_desc", async (ctx) => {
  await ctx.answerCbQuery();
  const s = getSession(ctx.from.id);
  updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, step: "description_edit" } });
  await ctx.reply(`📄 *Enter a new description or skip:*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("⏭️ Skip (Remove)", "gf_edit_desc_skip")],
      [Markup.button.callback("🔙 Cancel Edit", "gf_back_confirm")],
    ]) });
});
bot.action("gf_edit_desc_skip", async (ctx) => {
  await ctx.answerCbQuery();
  const s = getSession(ctx.from.id);
  updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, description: "", step: "confirm" } });
  await showConfirm(ctx);
});

bot.action("gf_edit_photo", async (ctx) => {
  await ctx.answerCbQuery();
  const s = getSession(ctx.from.id);
  updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, step: "photo_edit" } });
  await ctx.reply(`🖼️ *Send a new photo or skip:*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("⏭️ Skip (Remove)", "gf_edit_photo_skip")],
      [Markup.button.callback("🔙 Cancel Edit", "gf_back_confirm")],
    ]) });
});
bot.action("gf_edit_photo_skip", async (ctx) => {
  await ctx.answerCbQuery();
  const s = getSession(ctx.from.id);
  updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, photo: null, step: "confirm" } });
  await showConfirm(ctx);
});

bot.action("gf_edit_disappearing", async (ctx) => {
  await ctx.answerCbQuery();
  const s = getSession(ctx.from.id);
  updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, step: "disappearing_edit" } });
  await ctx.reply(`⏳ *Set Disappearing Messages:*`, { parse_mode: "Markdown",
    ...Markup.inlineKeyboard([
      [
        Markup.button.callback("24 Hours",  "gf_edit_dis_86400"),
        Markup.button.callback("7 Days",    "gf_edit_dis_604800"),
        Markup.button.callback("90 Days",   "gf_edit_dis_7776000"),
      ],
      [Markup.button.callback("⏭️ Off", "gf_edit_dis_0")],
      [Markup.button.callback("🔙 Cancel Edit", "gf_back_confirm")],
    ]) });
});
[0, 86400, 604800, 7776000].forEach((sec) => {
  bot.action(`gf_edit_dis_${sec}`, async (ctx) => {
    await ctx.answerCbQuery();
    const s = getSession(ctx.from.id);
    updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, disappearing: sec, step: "confirm" } });
    await showConfirm(ctx);
  });
});

bot.action("gf_edit_members", async (ctx) => {
  await ctx.answerCbQuery();
  const s = getSession(ctx.from.id);
  updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, step: "members_edit" } });
  await ctx.reply(
    `👥 *Enter member numbers (one per line) or skip:*\n\nExample:\n\`\`\`\n919876543210\n918765432109\n\`\`\``,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("⏭️ Skip (Remove All)", "gf_edit_members_skip")],
      [Markup.button.callback("🔙 Cancel Edit", "gf_back_confirm")],
    ]) });
});
bot.action("gf_edit_members_skip", async (ctx) => {
  await ctx.answerCbQuery();
  const s = getSession(ctx.from.id);
  updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, members: [], makeAdmin: false, step: "confirm" } });
  await showConfirm(ctx);
});

bot.action("gf_edit_perms", async (ctx) => {
  await ctx.answerCbQuery();
  const s = getSession(ctx.from.id);
  updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, step: "permissions_edit" } });
  await askPermissions(ctx);
});

// ─── Create Now ───────────────────────────────────────────────────────────
bot.action("gf_create_now", async (ctx) => {
  await ctx.answerCbQuery("Starting group creation...");
  const userId = ctx.from.id;
  const flow   = getSession(userId).groupFlow;

  if (!flow || !flow.name || !flow.count) {
    await ctx.reply("Settings are incomplete. Please try again.",
      Markup.inlineKeyboard([[Markup.button.callback("🔙 Back", "back_menu")]]));
    return;
  }

  if (getStatus(0) !== "connected") {
    await ctx.reply("WhatsApp is not connected!",
      Markup.inlineKeyboard([[Markup.button.callback("📱 Connect", "menu_account")]]));
    return;
  }

  const participantJids = flow.members.map((num) =>
    `${num.replace(/[^0-9]/g, "")}@s.whatsapp.net`
  );

  const progressMsg = await ctx.reply(
    `⏳ *Starting to create ${flow.count} group(s)...*\n\n_Please wait..._`,
    { parse_mode: "Markdown" }
  );

  const createdGroups = [];
  const failedGroups  = [];

  for (let i = 0; i < flow.count; i++) {
    const groupName = flow.numbering ? `${flow.name} ${i + 1}` : flow.name;

    try {
      try {
        await bot.telegram.editMessageText(
          ctx.chat.id, progressMsg.message_id, undefined,
          `⏳ *Creating Groups...*\n\n` +
          `✅ Done: ${i} / ${flow.count}\n` +
          `⚙️ Now creating: ${groupName}`,
          { parse_mode: "Markdown" }
        );
      } catch {}

      // 1. Create group
      const result = await createGroup(0, groupName, participantJids);
      const gid    = result.id;
      await sleep(1500);

      // 2. Set description
      if (flow.description) {
        await updateGroupDescription(0, gid, flow.description)
          .catch((e) => console.error(`[CreateGroup] Description error:`, e.message));
        await sleep(800);
      }

      // 3. Set photo
      if (flow.photo) {
        await updateGroupPhoto(0, gid, flow.photo)
          .catch((e) => console.error(`[CreateGroup] Photo error:`, e.message));
        await sleep(800);
      }

      // 4. Set disappearing messages
      if (flow.disappearing > 0) {
        await setDisappearingMessages(0, gid, flow.disappearing)
          .catch((e) => console.error(`[CreateGroup] Disappearing error:`, e.message));
        await sleep(800);
      }

      // 5. Promote to admin
      if (flow.makeAdmin && participantJids.length > 0) {
        await promoteToAdmin(0, gid, participantJids)
          .catch((e) => console.error(`[CreateGroup] Admin error:`, e.message));
        await sleep(800);
      }

      // 6. Set permissions
      await setGroupPermissions(0, gid, flow.permissions)
        .catch((e) => console.error(`[CreateGroup] Permissions error:`, e.message));
      await sleep(800);

      // 7. Get invite link
      let link = "";
      try { link = await getGroupInviteLink(0, gid); }
      catch (e) { link = `(Link unavailable — Group ID: ${gid})`; }

      createdGroups.push({ name: groupName, link });
      await sleep(2000);

    } catch (err) {
      console.error(`[CreateGroup] Error for ${groupName}:`, err.message);
      failedGroups.push(groupName);
      await sleep(2000);
    }
  }

  // Final progress update
  try {
    await bot.telegram.editMessageText(
      ctx.chat.id, progressMsg.message_id, undefined,
      `✅ *Group Creation Complete!*\n\n✅ Created: ${createdGroups.length}\n❌ Failed: ${failedGroups.length}`,
      { parse_mode: "Markdown" }
    );
  } catch {}

  // Send list of groups and invite links
  if (createdGroups.length > 0) {
    const chunkSize = 20;
    for (let c = 0; c < createdGroups.length; c += chunkSize) {
      const chunk = createdGroups.slice(c, c + chunkSize);
      const listText =
        `📋 *Created Groups (${c + 1}–${Math.min(c + chunkSize, createdGroups.length)} of ${createdGroups.length}):*\n\n` +
        chunk.map((g, idx) => `${c + idx + 1}. *${g.name}*\n${g.link}`).join("\n\n");
      await ctx.reply(listText, { parse_mode: "Markdown" });
      await sleep(500);
    }
  }

  if (failedGroups.length > 0) {
    await ctx.reply(
      `❌ *These groups could not be created:*\n\n${failedGroups.join("\n")}`,
      { parse_mode: "Markdown" }
    );
  }

  updateSession(userId, { groupFlow: null });
  await sleep(500);
  await ctx.reply("🏠 *Main Menu:*", { parse_mode: "Markdown", ...mainMenu() });
});

// ═══════════════════════════════════════════════════════════════════════════
// ─── TEXT MESSAGE HANDLER ─────────────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════

bot.on("text", async (ctx) => {
  const userId = ctx.from.id;
  const s      = getSession(userId);
  const text   = ctx.message.text.trim();
  if (text.startsWith("/")) return;

  // ── WhatsApp phone number input ──────────────────────────────────────
  if (s.awaitingPhoneForIndex !== null && s.awaitingPhoneForIndex !== undefined) {
    const phone = text.replace(/[^0-9]/g, "");
    if (phone.length < 10) {
      await ctx.reply("Invalid number. Example: `919876543210`",
        { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🔙 Back", "back_menu")]]) });
      return;
    }
    updateSession(userId, { awaitingPhoneForIndex: null });

    const waitMsg = await ctx.reply(
      `⏳ *Generating pairing code...*\n_This will take 15–30 seconds._`,
      { parse_mode: "Markdown" }
    );

    pendingPairingCbs.set(0, async (code) => {
      try { await ctx.telegram.deleteMessage(ctx.chat.id, waitMsg.message_id); } catch {}
      if (!code) {
        await ctx.reply("Code could not be generated. Please try again.",
          { parse_mode: "Markdown", ...Markup.inlineKeyboard([
            [Markup.button.callback("🔄 Try Again", "menu_account")],
            [Markup.button.callback("🔙 Back", "back_menu")],
          ]) });
        return;
      }
      await ctx.reply(
        `🔑 *Pairing Code*\n\n\`${code}\`\n\n` +
        `*How to link:*\n` +
        `1. Open WhatsApp\n` +
        `2. Go to *Settings → Linked Devices → Link a Device*\n` +
        `3. Tap *Link with phone number*\n` +
        `4. Enter the code above\n\n` +
        `⚠️ *Code is valid for 60 seconds only!*\n⏳ Waiting for connection...`,
        { parse_mode: "Markdown", ...Markup.inlineKeyboard([
          [Markup.button.callback("🔄 New Code", "reset_wa")],
          [Markup.button.callback("🔙 Menu", "back_menu")],
        ]) }
      );
    });

    pendingReadyCbs.set(0, async () => {
      await ctx.reply(`✅ *WhatsApp Connected!*\n📱 \`${phone}\``,
        { parse_mode: "Markdown", ...mainMenu() });
    });

    connectAccount(0, phone).catch(async (err) => {
      pendingPairingCbs.delete(0);
      pendingReadyCbs.delete(0);
      await ctx.reply(`Error: \`${err.message}\``,
        { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🔙 Back", "back_menu")]]) });
    });
    return;
  }

  // ── Group flow text inputs ─────────────────────────────────────────────
  const flow = s.groupFlow;
  if (!flow) {
    await sendMainMenu(ctx, "Select an option from the menu:");
    return;
  }

  // Step 1: Name
  if (flow.step === "name") {
    const name = text.slice(0, 100);
    updateSession(userId, { groupFlow: { ...flow, name, step: "count" } });
    await ctx.reply(
      `📋 *Create Groups — Step 2 of 9*\n\n` +
      `✅ Name: *${name}*\n\n` +
      `*How many groups do you want to create? (1–50)*\n\n` +
      `_Tap a number or type a custom count._`,
      { parse_mode: "Markdown", ...Markup.inlineKeyboard([
        [1, 5, 10, 20, 50].map((n) => Markup.button.callback(`${n}`, `gf_count_${n}`)),
        [Markup.button.callback("❌ Cancel", "back_menu")],
      ]) }
    );
    return;
  }

  // Step 1 Edit: Name
  if (flow.step === "name_edit") {
    const name = text.slice(0, 100);
    updateSession(userId, { groupFlow: { ...flow, name, step: "confirm" } });
    await showConfirm(ctx);
    return;
  }

  // Step 2: Count (typed manually)
  if (flow.step === "count") {
    const count = parseInt(text, 10);
    if (isNaN(count) || count < 1 || count > 50) {
      await ctx.reply("Please enter a number between 1 and 50.");
      return;
    }
    updateSession(userId, { groupFlow: { ...flow, count, step: "numbering" } });
    await askNumbering(ctx);
    return;
  }

  // Step 2 Edit: Count
  if (flow.step === "count_edit") {
    const count = parseInt(text, 10);
    if (isNaN(count) || count < 1 || count > 50) {
      await ctx.reply("Please enter a number between 1 and 50.");
      return;
    }
    updateSession(userId, { groupFlow: { ...flow, count, step: "confirm" } });
    await showConfirm(ctx);
    return;
  }

  // Step 4: Description
  if (flow.step === "description") {
    const desc = text.slice(0, 512);
    updateSession(userId, { groupFlow: { ...flow, description: desc, step: "photo" } });
    await ctx.reply(`✅ Description saved!`);
    await askPhoto(ctx);
    return;
  }

  // Step 4 Edit: Description
  if (flow.step === "description_edit") {
    const desc = text.slice(0, 512);
    updateSession(userId, { groupFlow: { ...flow, description: desc, step: "confirm" } });
    await showConfirm(ctx);
    return;
  }

  // Step 7: Members
  if (flow.step === "members") {
    const nums = text.split(/[\n,\s]+/)
      .map((n) => n.replace(/[^0-9]/g, ""))
      .filter((n) => n.length >= 10);
    if (nums.length === 0) {
      await ctx.reply("No valid numbers found. Include the country code (e.g. 919876543210).\nOr press Skip.");
      return;
    }
    updateSession(userId, { groupFlow: { ...flow, members: nums, step: "admin" } });
    await ctx.reply(`✅ *${nums.length} number(s) added.*`, { parse_mode: "Markdown" });
    await askAdmin(ctx);
    return;
  }

  // Step 7 Edit: Members
  if (flow.step === "members_edit") {
    const nums = text.split(/[\n,\s]+/)
      .map((n) => n.replace(/[^0-9]/g, ""))
      .filter((n) => n.length >= 10);
    if (nums.length === 0) {
      await ctx.reply("No valid numbers found. Include the country code.\nOr press Skip.");
      return;
    }
    updateSession(userId, { groupFlow: { ...flow, members: nums, step: "confirm" } });
    await showConfirm(ctx);
    return;
  }

  await sendMainMenu(ctx, "Select an option from the menu:");
});

// ─── Count quick-select buttons ───────────────────────────────────────────
[1, 5, 10, 20, 50].forEach((n) => {
  bot.action(`gf_count_${n}`, async (ctx) => {
    await ctx.answerCbQuery();
    const s = getSession(ctx.from.id);
    updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, count: n, step: "numbering" } });
    await askNumbering(ctx);
  });
});

// ─── Photo Handler ────────────────────────────────────────────────────────
bot.on("photo", async (ctx) => {
  const userId = ctx.from.id;
  const flow   = getSession(userId).groupFlow;

  if (!flow || (flow.step !== "photo" && flow.step !== "photo_edit")) return;

  try {
    const photo   = ctx.message.photo[ctx.message.photo.length - 1];
    const fileUrl = await ctx.telegram.getFileLink(photo.file_id);
    const resp    = await fetch(fileUrl.href);
    const buffer  = Buffer.from(await resp.arrayBuffer());
    const newStep = flow.step === "photo_edit" ? "confirm" : "disappearing";

    updateSession(userId, { groupFlow: { ...flow, photo: buffer, step: newStep } });
    await ctx.reply("✅ *Photo saved!*", { parse_mode: "Markdown" });

    if (newStep === "confirm") await showConfirm(ctx);
    else await askDisappearing(ctx);
  } catch (err) {
    console.error("[Photo] Error:", err.message);
    await ctx.reply("Could not save photo. Please try sending it again.");
  }
});

// ─── Error handler ────────────────────────────────────────────────────────
bot.catch((err) => console.error("[Bot Error]", err.message));

// ─── Express health server ────────────────────────────────────────────────
const app  = express();
const PORT = process.env.PORT || 3000;

app.get("/", (_req, res) => res.send(`
  <html><body style="font-family:sans-serif;text-align:center;padding:50px;background:#0a0a0a;color:#fff">
    <h2>✅ WhatsApp Group Creator Bot</h2>
    <p style="color:#4ade80">Running 🟢</p>
    <p>Uptime: ${Math.floor(process.uptime())}s | WhatsApp: ${getConnectedCount() > 0 ? "Connected ✅" : "Disconnected ❌"}</p>
  </body></html>`));

app.get("/health", (_req, res) => res.json({
  status: "ok",
  uptime: `${Math.floor(process.uptime())}s`,
  whatsapp: getStatus(0),
  phone: getPhone(0) || null,
  ts: new Date().toISOString(),
}));

app.listen(PORT, () => console.log(`HTTP server running on port ${PORT}`));

// ─── Self-ping ────────────────────────────────────────────────────────────
function selfPing() {
  const url = process.env.RENDER_EXTERNAL_URL || process.env.SELF_URL;
  if (!url) return;
  const fullUrl = url.startsWith("http") ? url : `https://${url}`;
  const client  = fullUrl.startsWith("https") ? https : http;
  client.get(`${fullUrl}/health`, (r) => console.log(`[Ping] ${r.statusCode}`))
        .on("error", (e) => console.error("[Ping Error]", e.message));
}
setTimeout(() => { selfPing(); setInterval(selfPing, 120000); }, 60000);

// ─── Main ─────────────────────────────────────────────────────────────────
async function main() {
  await connectDB();
  await reconnectSavedAccounts();
  await bot.launch({ dropPendingUpdates: true });
  console.log(`WhatsApp Group Creator Bot running! Owner: ${OWNER_ID || "NOT SET"}`);
}

main().catch((err) => { console.error("Fatal:", err.message); process.exit(1); });
process.once("SIGINT",  () => bot.stop("SIGINT"));
process.once("SIGTERM", () => bot.stop("SIGTERM"));
