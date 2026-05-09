/**
 * WhatsApp Group Creator Bot
 * ─ 1 WhatsApp account
 * ─ Create up to 50 groups at once with full settings
 * ─ MongoDB persistence
 * ─ Owner-only access
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
if (!TOKEN) { console.error("❌ TELEGRAM_BOT_TOKEN not set!"); process.exit(1); }
const OWNER_ID = parseInt(process.env.OWNER_ID || "0", 10);
if (!OWNER_ID) console.warn("⚠️  OWNER_ID not set — anyone can use this bot!");

const bot = new Telegraf(TOKEN);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ─── Owner guard ──────────────────────────────────────────────────────────
bot.use(async (ctx, next) => {
  if (OWNER_ID && ctx.from?.id !== OWNER_ID) {
    if (ctx.callbackQuery) await ctx.answerCbQuery("❌ Unauthorized.", { show_alert: true }).catch(() => {});
    else await ctx.reply("❌ Yeh bot sirf owner ke liye hai.").catch(() => {});
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
  onDisconnected: async (index) => {
    console.log(`[Bot] WA disconnected`);
  },
});

// ─── Main Menu ────────────────────────────────────────────────────────────
function mainMenu() {
  const connected = getStatus(0) === "connected";
  const phone     = getPhone(0);
  const statusBtn = connected
    ? `✅ WhatsApp: ${phone.slice(-5) || "Connected"}`
    : `❌ WhatsApp: Connect Karein`;

  return Markup.inlineKeyboard([
    [Markup.button.callback(statusBtn, "menu_account")],
    [Markup.button.callback("📋 Groups Banayein", connected ? "create_groups_start" : "need_connect")],
    [Markup.button.callback("📊 Status", "menu_status")],
  ]);
}

async function sendMainMenu(ctx, text) {
  await ctx.reply(
    text || "👋 *WhatsApp Group Creator Bot*\n\nMenu se option chunein:",
    { parse_mode: "Markdown", ...mainMenu() }
  );
}

// ─── Commands ─────────────────────────────────────────────────────────────
bot.start(async (ctx) => sendMainMenu(ctx));
bot.command("menu",  async (ctx) => sendMainMenu(ctx));

// ─── Status ───────────────────────────────────────────────────────────────
bot.action("menu_status", async (ctx) => {
  await ctx.answerCbQuery();
  const s = getStatus(0);
  const p = getPhone(0);
  const icon = s === "connected" ? "✅" : s === "connecting" ? "⏳" : "❌";
  await ctx.reply(
    `📊 *Status*\n\n${icon} WhatsApp: ${s === "connected" ? `Connected — \`${p}\`` : s}\n`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🔙 Menu", "back_menu")]]) }
  );
});

// ─── Need connect message ─────────────────────────────────────────────────
bot.action("need_connect", async (ctx) => {
  await ctx.answerCbQuery("Pehle WhatsApp connect karein!", { show_alert: true });
});

// ─── Account ──────────────────────────────────────────────────────────────
bot.action("menu_account", async (ctx) => {
  await ctx.answerCbQuery();
  const status = getStatus(0);
  const phone  = getPhone(0);

  if (status === "connected") {
    try {
      await ctx.editMessageText(
        `📱 *WhatsApp Account*\n\n✅ Connected: \`${phone}\`\n\nLogout karna chahte hain?`,
        { parse_mode: "Markdown", reply_markup: Markup.inlineKeyboard([
          [Markup.button.callback("🔌 Logout", "logout_wa")],
          [Markup.button.callback("🔙 Menu", "back_menu")],
        ]).reply_markup }
      );
    } catch {
      await ctx.reply(
        `📱 *WhatsApp Account*\n\n✅ Connected: \`${phone}\`\n\nLogout karna chahte hain?`,
        { parse_mode: "Markdown", ...Markup.inlineKeyboard([
          [Markup.button.callback("🔌 Logout", "logout_wa")],
          [Markup.button.callback("🔙 Menu", "back_menu")],
        ]) }
      );
    }
  } else if (status === "connecting") {
    try {
      await ctx.editMessageText(
        `⏳ *WhatsApp connect ho raha hai...*`,
        { parse_mode: "Markdown", reply_markup: Markup.inlineKeyboard([
          [Markup.button.callback("🔄 Reset", "reset_wa")],
          [Markup.button.callback("🔙 Menu", "back_menu")],
        ]).reply_markup }
      );
    } catch {
      await ctx.reply(`⏳ WhatsApp connect ho raha hai...`,
        { ...Markup.inlineKeyboard([
          [Markup.button.callback("🔄 Reset", "reset_wa")],
          [Markup.button.callback("🔙 Menu", "back_menu")],
        ]) }
      );
    }
  } else {
    updateSession(ctx.from.id, { awaitingPhoneForIndex: 0 });
    try {
      await ctx.editMessageText(
        `📱 *WhatsApp Connect Karein*\n\n` +
        `Phone number dalein (country code ke saath):\n` +
        `Example: \`919876543210\`\n\n` +
        `⚠️ *Code 60 seconds mein expire hota hai!*`,
        { parse_mode: "Markdown", reply_markup: Markup.inlineKeyboard([
          [Markup.button.callback("🔙 Menu", "back_menu")],
        ]).reply_markup }
      );
    } catch {
      await ctx.reply(
        `📱 *WhatsApp Connect Karein*\n\n` +
        `Phone number dalein (country code ke saath):\n` +
        `Example: \`919876543210\`\n\n` +
        `⚠️ *Code 60 seconds mein expire hota hai!*`,
        { parse_mode: "Markdown", ...Markup.inlineKeyboard([
          [Markup.button.callback("🔙 Menu", "back_menu")],
        ]) }
      );
    }
  }
});

bot.action("logout_wa", async (ctx) => {
  await ctx.answerCbQuery("Logout ho raha hai...");
  await ctx.editMessageText("⏳ Logout ho raha hai...");
  await disconnectAccount(0);
  await ctx.editMessageText("✅ *Logout ho gaya!*", { parse_mode: "Markdown" });
  await sleep(600);
  await sendMainMenu(ctx);
});

bot.action("reset_wa", async (ctx) => {
  await ctx.answerCbQuery("Reset ho raha hai...");
  await disconnectAccount(0);
  updateSession(ctx.from.id, { awaitingPhoneForIndex: 0 });
  await ctx.editMessageText("✅ *Reset ho gaya!* Ab number dalein:", { parse_mode: "Markdown" });
  await ctx.reply(
    `📱 Number dalein (example: \`919876543210\`):`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🔙 Menu", "back_menu")]]) }
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
    await ctx.answerCbQuery("❌ Pehle WhatsApp connect karein!", { show_alert: true });
    return;
  }
  const flow = defaultGroupFlow();
  updateSession(ctx.from.id, { groupFlow: flow });

  await ctx.reply(
    `📋 *Groups Banana — Step 1/9*\n\n` +
    `*Group ka naam kya rakhen?*\n\n` +
    `_Ek naam likhein, numbering automatic add ho sakti hai (jaise: MyGroup 1, MyGroup 2)_`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("❌ Cancel", "back_menu")]]) }
  );
});

// ─── Step 3: Numbering ────────────────────────────────────────────────────
function askNumbering(ctx) {
  const s    = getSession(ctx.from.id);
  const flow = s.groupFlow;
  return ctx.reply(
    `📋 *Groups Banana — Step 3/9*\n\n` +
    `*Groups mein numbering add karein?*\n\n` +
    `Agar haan, to groups ke naam honge:\n` +
    `_${flow.name} 1, ${flow.name} 2, ${flow.name} 3..._\n\n` +
    `Agar nahi, to sab ka naam: _${flow.name}_`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [
        Markup.button.callback("✅ Haan, Numbering Chahiye", "gf_numbering_yes"),
        Markup.button.callback("❌ Nahi", "gf_numbering_no"),
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
    `📋 *Groups Banana — Step 4/9*\n\n` +
    `*Group Description daalen:*\n\n` +
    `_Sab groups mein yahi description jayega. Skip bhi kar sakte hain._`,
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
    `📋 *Groups Banana — Step 5/9*\n\n` +
    `*Group Photo bhejein:*\n\n` +
    `_Sab groups mein yahi photo set hogi. Skip bhi kar sakte hain._`,
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
    `📋 *Groups Banana — Step 6/9*\n\n` +
    `*Disappearing Messages set karein:*\n\n` +
    `_Ek time chunein ya skip karein._`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [
        Markup.button.callback("24 Ghante", "gf_dis_86400"),
        Markup.button.callback("7 Din",     "gf_dis_604800"),
        Markup.button.callback("90 Din",    "gf_dis_7776000"),
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
    `📋 *Groups Banana — Step 7/9*\n\n` +
    `*Members add karne hain?*\n\n` +
    `Jinhe add karna hai unke numbers daalen, ek per line:\n` +
    `\`\`\`\n919876543210\n918765432109\n\`\`\`\n\n` +
    `_Country code ke saath likhen. Skip bhi kar sakte hain._`,
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
  const s    = getSession(ctx.from.id);
  const nums = s.groupFlow.members;
  return ctx.reply(
    `📋 *Groups Banana — Step 8/9*\n\n` +
    `*${nums.length} member(s) add honge.*\n\n` +
    `*Kya inhe Admin banana hai?*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [
        Markup.button.callback("✅ Haan, Admin Banayein", "gf_admin_yes"),
        Markup.button.callback("❌ Nahi", "gf_admin_no"),
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
function permLabel(on, trueLabel, falseLabel) {
  return on ? `✅ ${trueLabel}` : `❌ ${falseLabel}`;
}

function buildPermissionsKeyboard(permissions) {
  return Markup.inlineKeyboard([
    [Markup.button.callback(
      `💬 Messages: ${permLabel(permissions.sendMessages, "Sabhi", "Sirf Admin")}`,
      "gf_perm_toggle_sendMessages"
    )],
    [Markup.button.callback(
      `✏️ Group Info: ${permLabel(permissions.editInfo, "Sabhi", "Sirf Admin")}`,
      "gf_perm_toggle_editInfo"
    )],
    [Markup.button.callback(
      `➕ Members Add: ${permLabel(permissions.addMembers, "Sabhi", "Sirf Admin")}`,
      "gf_perm_toggle_addMembers"
    )],
    [Markup.button.callback(
      `🔐 New Member Approval: ${permLabel(permissions.approveMembers, "On", "Off")}`,
      "gf_perm_toggle_approveMembers"
    )],
    [Markup.button.callback("💾 Settings Save Karein", "gf_perm_save")],
    [Markup.button.callback("❌ Cancel", "back_menu")],
  ]);
}

async function askPermissions(ctx) {
  const s    = getSession(ctx.from.id);
  const perm = s.groupFlow.permissions;
  await ctx.reply(
    `📋 *Groups Banana — Step 9/9*\n\n` +
    `*Group Permissions set karein:*\n\n` +
    `_Button dabake toggle karein. Sab settings save karein button se._`,
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
  await ctx.answerCbQuery("Settings save ho gayi!");
  const s = getSession(ctx.from.id);
  updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, step: "confirm" } });
  await showConfirm(ctx);
});

// ─── Confirm / Summary ────────────────────────────────────────────────────
function fmtDisappearing(sec) {
  if (!sec || sec === 0) return "Off";
  if (sec === 86400)  return "24 Ghante";
  if (sec === 604800) return "7 Din";
  if (sec === 7776000) return "90 Din";
  return `${sec}s`;
}

async function showConfirm(ctx) {
  const s    = getSession(ctx.from.id);
  const flow = s.groupFlow;
  const perm = flow.permissions;

  const groupNames = flow.numbering
    ? Array.from({ length: Math.min(flow.count, 3) }, (_, i) => `${flow.name} ${i + 1}`).join(", ") +
      (flow.count > 3 ? `... (${flow.count} groups)` : "")
    : `${flow.name} (${flow.count} groups)`;

  const summary =
    `✅ *Sab Settings Review Karein:*\n` +
    `━━━━━━━━━━━━━━━━━━━━━\n` +
    `📝 *Naam:* ${flow.name}\n` +
    `🔢 *Groups:* ${flow.count}\n` +
    `🔢 *Numbering:* ${flow.numbering ? "On" : "Off"}\n` +
    `📋 *Names:* _${groupNames}_\n` +
    `📄 *Description:* ${flow.description || "_Koi nahi_"}\n` +
    `🖼️ *Photo:* ${flow.photo ? "Set hai ✅" : "_Koi nahi_"}\n` +
    `⏳ *Disappearing:* ${fmtDisappearing(flow.disappearing)}\n` +
    `👥 *Members:* ${flow.members.length > 0 ? `${flow.members.length} numbers` : "_Koi nahi_"}\n` +
    `👑 *Admin:* ${flow.members.length > 0 ? (flow.makeAdmin ? "Haan" : "Nahi") : "_N/A_"}\n` +
    `━━━━━━━━━━━━━━━━━━━━━\n` +
    `*Permissions:*\n` +
    `💬 Messages: ${perm.sendMessages ? "Sabhi" : "Sirf Admin"}\n` +
    `✏️ Group Info: ${perm.editInfo ? "Sabhi" : "Sirf Admin"}\n` +
    `➕ Members Add: ${perm.addMembers ? "Sabhi" : "Sirf Admin"}\n` +
    `🔐 Approval: ${perm.approveMembers ? "On" : "Off"}\n` +
    `━━━━━━━━━━━━━━━━━━━━━\n` +
    `_Sab sahi hai? "Create Now" dabain._`;

  await ctx.reply(summary, {
    parse_mode: "Markdown",
    ...Markup.inlineKeyboard([
      [Markup.button.callback("✏️ Edit Karein", "gf_edit_menu")],
      [Markup.button.callback("🚀 Create Now", "gf_create_now")],
      [Markup.button.callback("❌ Cancel", "back_menu")],
    ]),
  });
}

// ─── Edit Menu ────────────────────────────────────────────────────────────
bot.action("gf_edit_menu", async (ctx) => {
  await ctx.answerCbQuery();
  await ctx.reply(
    `✏️ *Kya edit karna hai?*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("📝 Naam", "gf_edit_name"), Markup.button.callback("🔢 Count", "gf_edit_count")],
      [Markup.button.callback("🔢 Numbering", "gf_edit_numbering"), Markup.button.callback("📄 Description", "gf_edit_desc")],
      [Markup.button.callback("🖼️ Photo", "gf_edit_photo"), Markup.button.callback("⏳ Disappearing", "gf_edit_disappearing")],
      [Markup.button.callback("👥 Members", "gf_edit_members"), Markup.button.callback("🔐 Permissions", "gf_edit_perms")],
      [Markup.button.callback("🔙 Back to Summary", "gf_back_confirm")],
    ]) }
  );
});

bot.action("gf_back_confirm", async (ctx) => {
  await ctx.answerCbQuery();
  await showConfirm(ctx);
});

bot.action("gf_edit_name", async (ctx) => {
  await ctx.answerCbQuery();
  const s = getSession(ctx.from.id);
  updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, step: "name_edit" } });
  await ctx.reply(
    `📝 *Naya Group Naam daalen:*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🔙 Cancel Edit", "gf_back_confirm")]]) }
  );
});

bot.action("gf_edit_count", async (ctx) => {
  await ctx.answerCbQuery();
  const s = getSession(ctx.from.id);
  updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, step: "count_edit" } });
  await ctx.reply(
    `🔢 *Kitne groups banana hai? (1-50):*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🔙 Cancel Edit", "gf_back_confirm")]]) }
  );
});

bot.action("gf_edit_numbering", async (ctx) => {
  await ctx.answerCbQuery();
  const s = getSession(ctx.from.id);
  updateSession(ctx.from.id, { groupFlow: { ...s.groupFlow, step: "numbering_edit" } });
  await ctx.reply(
    `🔢 *Numbering chahiye?*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("✅ Haan", "gf_edit_num_yes"), Markup.button.callback("❌ Nahi", "gf_edit_num_no")],
      [Markup.button.callback("🔙 Cancel Edit", "gf_back_confirm")],
    ]) }
  );
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
  await ctx.reply(
    `📄 *Naya Description daalen ya skip karein:*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("⏭️ Skip (Hata dein)", "gf_edit_desc_skip")],
      [Markup.button.callback("🔙 Cancel Edit", "gf_back_confirm")],
    ]) }
  );
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
  await ctx.reply(
    `🖼️ *Naya Photo bhejein ya skip karein:*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("⏭️ Skip (Hata dein)", "gf_edit_photo_skip")],
      [Markup.button.callback("🔙 Cancel Edit", "gf_back_confirm")],
    ]) }
  );
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
  await ctx.reply(
    `⏳ *Disappearing Messages:*`,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [
        Markup.button.callback("24 Ghante", "gf_edit_dis_86400"),
        Markup.button.callback("7 Din",     "gf_edit_dis_604800"),
        Markup.button.callback("90 Din",    "gf_edit_dis_7776000"),
      ],
      [Markup.button.callback("⏭️ Off", "gf_edit_dis_0")],
      [Markup.button.callback("🔙 Cancel Edit", "gf_back_confirm")],
    ]) }
  );
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
    `👥 *Members ke numbers daalen (ek per line) ya skip karein:*\n\nExample:\n\`\`\`\n919876543210\n918765432109\n\`\`\``,
    { parse_mode: "Markdown", ...Markup.inlineKeyboard([
      [Markup.button.callback("⏭️ Skip (Hata dein)", "gf_edit_members_skip")],
      [Markup.button.callback("🔙 Cancel Edit", "gf_back_confirm")],
    ]) }
  );
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
  await ctx.answerCbQuery("Groups banana shuru ho raha hai...");
  const userId = ctx.from.id;
  const s      = getSession(userId);
  const flow   = s.groupFlow;

  if (!flow || !flow.name || !flow.count) {
    await ctx.reply("❌ Settings incomplete hain. Dobara try karein.", Markup.inlineKeyboard([[Markup.button.callback("🔙 Menu", "back_menu")]]));
    return;
  }

  if (getStatus(0) !== "connected") {
    await ctx.reply("❌ WhatsApp connected nahi hai!", Markup.inlineKeyboard([[Markup.button.callback("📱 Connect Karein", "menu_account")]]));
    return;
  }

  // Prepare participants JIDs
  const participantJids = flow.members.map((num) => {
    const clean = num.replace(/[^0-9]/g, "");
    return `${clean}@s.whatsapp.net`;
  });

  const progressMsg = await ctx.reply(
    `⏳ *${flow.count} groups banana shuru ho raha hai...*\n\n_Kripya wait karein..._`,
    { parse_mode: "Markdown" }
  );

  const createdGroups = [];
  const failedGroups  = [];

  for (let i = 0; i < flow.count; i++) {
    const groupName = flow.numbering ? `${flow.name} ${i + 1}` : flow.name;

    try {
      // Update progress
      try {
        await bot.telegram.editMessageText(
          ctx.chat.id, progressMsg.message_id, undefined,
          `⏳ *Groups Ban Rahe Hain...*\n\n` +
          `✅ Complete: ${i}/${flow.count}\n` +
          `⚙️ Abhi: ${groupName} bana raha hai...`,
          { parse_mode: "Markdown" }
        );
      } catch {}

      // 1. Create group
      const result = await createGroup(0, groupName, participantJids);
      const gid    = result.id;

      await sleep(1500);

      // 2. Set description
      if (flow.description) {
        await updateGroupDescription(0, gid, flow.description).catch((e) =>
          console.error(`[CreateGroup] Description error:`, e.message)
        );
        await sleep(800);
      }

      // 3. Set photo
      if (flow.photo) {
        await updateGroupPhoto(0, gid, flow.photo).catch((e) =>
          console.error(`[CreateGroup] Photo error:`, e.message)
        );
        await sleep(800);
      }

      // 4. Set disappearing messages
      if (flow.disappearing && flow.disappearing > 0) {
        await setDisappearingMessages(0, gid, flow.disappearing).catch((e) =>
          console.error(`[CreateGroup] Disappearing error:`, e.message)
        );
        await sleep(800);
      }

      // 5. Promote to admin if requested
      if (flow.makeAdmin && participantJids.length > 0) {
        await promoteToAdmin(0, gid, participantJids).catch((e) =>
          console.error(`[CreateGroup] Admin error:`, e.message)
        );
        await sleep(800);
      }

      // 6. Set permissions
      await setGroupPermissions(0, gid, flow.permissions).catch((e) =>
        console.error(`[CreateGroup] Permissions error:`, e.message)
      );
      await sleep(800);

      // 7. Get invite link
      let link = "";
      try {
        link = await getGroupInviteLink(0, gid);
      } catch (e) {
        console.error(`[CreateGroup] Link error:`, e.message);
        link = `Group ID: ${gid}`;
      }

      createdGroups.push({ name: groupName, link });
      await sleep(2000);

    } catch (err) {
      console.error(`[CreateGroup] Error creating ${groupName}:`, err.message);
      failedGroups.push(groupName);
      await sleep(2000);
    }
  }

  // Done — update progress message
  try {
    await bot.telegram.editMessageText(
      ctx.chat.id, progressMsg.message_id, undefined,
      `✅ *Groups Create Ho Gaye!*\n\n` +
      `✅ Success: ${createdGroups.length}\n` +
      `❌ Failed: ${failedGroups.length}`,
      { parse_mode: "Markdown" }
    );
  } catch {}

  // Send list of created groups with links
  if (createdGroups.length > 0) {
    const chunkSize = 20;
    for (let c = 0; c < createdGroups.length; c += chunkSize) {
      const chunk = createdGroups.slice(c, c + chunkSize);
      const listText =
        `📋 *Banaye Gaye Groups (${c + 1}–${Math.min(c + chunkSize, createdGroups.length)} / ${createdGroups.length}):*\n\n` +
        chunk.map((g, idx) => `${c + idx + 1}. *${g.name}*\n${g.link}`).join("\n\n");

      await ctx.reply(listText, { parse_mode: "Markdown" });
      await sleep(500);
    }
  }

  if (failedGroups.length > 0) {
    await ctx.reply(
      `❌ *Yeh groups nahi ban sake:*\n\n${failedGroups.join("\n")}`,
      { parse_mode: "Markdown" }
    );
  }

  // Reset flow and show main menu
  updateSession(userId, { groupFlow: null });
  await sleep(500);
  await ctx.reply("🏠 *Main Menu:*", { parse_mode: "Markdown", ...mainMenu() });
});

// ─── Text Message Handler ─────────────────────────────────────────────────
bot.on("text", async (ctx) => {
  const userId = ctx.from.id;
  const s      = getSession(userId);
  const text   = ctx.message.text.trim();
  if (text.startsWith("/")) return;

  // ── WhatsApp phone number input ──────────────────────────────────────
  if (s.awaitingPhoneForIndex !== null && s.awaitingPhoneForIndex !== undefined) {
    const phone = text.replace(/[^0-9]/g, "");
    if (phone.length < 10) {
      await ctx.reply("❌ Invalid number. Example: `919876543210`",
        { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🔙 Menu", "back_menu")]]) });
      return;
    }
    updateSession(userId, { awaitingPhoneForIndex: null });

    const waitMsg = await ctx.reply(
      `⏳ *Pairing code generate ho raha hai...*\n_15-30 sec mein aayega_`,
      { parse_mode: "Markdown" }
    );

    pendingPairingCbs.set(0, async (code) => {
      try { await ctx.telegram.deleteMessage(ctx.chat.id, waitMsg.message_id); } catch {}
      if (!code) {
        await ctx.reply("❌ *Code generate nahi hua.* Dobara try karein.",
          { parse_mode: "Markdown", ...Markup.inlineKeyboard([
            [Markup.button.callback("🔄 Try Again", "menu_account")],
            [Markup.button.callback("🔙 Menu", "back_menu")],
          ]) });
        return;
      }
      await ctx.reply(
        `🔑 *Pairing Code*\n\n\`${code}\`\n\n` +
        `*Steps:*\n` +
        `1. WhatsApp open karein\n` +
        `2. *Settings → Linked Devices → Link a Device*\n` +
        `3. *Link with phone number* tap karein\n` +
        `4. Upar ka code enter karein\n\n` +
        `⚠️ *Code sirf 60 seconds valid hai!*\n⏳ Connect hone ka wait ho raha hai...`,
        { parse_mode: "Markdown", ...Markup.inlineKeyboard([
          [Markup.button.callback("🔄 Naya Code", "reset_wa")],
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
      await ctx.reply(`❌ Error: \`${err.message}\``,
        { parse_mode: "Markdown", ...Markup.inlineKeyboard([[Markup.button.callback("🔙 Menu", "back_menu")]]) });
    });
    return;
  }

  // ── Group flow text inputs ─────────────────────────────────────────────
  const flow = s.groupFlow;
  if (!flow) {
    await sendMainMenu(ctx, "👇 Menu se option chunein:");
    return;
  }

  // Step: Name
  if (flow.step === "name") {
    const name = text.slice(0, 100);
    updateSession(userId, { groupFlow: { ...flow, name, step: "count" } });
    await ctx.reply(
      `📋 *Groups Banana — Step 2/9*\n\n` +
      `✅ Naam: *${name}*\n\n` +
      `*Kitne groups banana hai? (1 se 50 tak):*`,
      { parse_mode: "Markdown", ...Markup.inlineKeyboard([
        [[1,5,10,20,50].map((n) => Markup.button.callback(`${n}`, `gf_count_${n}`))],
        [Markup.button.callback("❌ Cancel", "back_menu")],
      ]) }
    );
    return;
  }

  // Step: Name (edit)
  if (flow.step === "name_edit") {
    const name = text.slice(0, 100);
    updateSession(userId, { groupFlow: { ...flow, name, step: "confirm" } });
    await showConfirm(ctx);
    return;
  }

  // Step: Count (custom input)
  if (flow.step === "count") {
    const count = parseInt(text, 10);
    if (isNaN(count) || count < 1 || count > 50) {
      await ctx.reply("❌ 1 se 50 ke beech number daalen.");
      return;
    }
    updateSession(userId, { groupFlow: { ...flow, count, step: "numbering" } });
    await askNumbering(ctx);
    return;
  }

  // Step: Count (edit)
  if (flow.step === "count_edit") {
    const count = parseInt(text, 10);
    if (isNaN(count) || count < 1 || count > 50) {
      await ctx.reply("❌ 1 se 50 ke beech number daalen.");
      return;
    }
    updateSession(userId, { groupFlow: { ...flow, count, step: "confirm" } });
    await showConfirm(ctx);
    return;
  }

  // Step: Description
  if (flow.step === "description") {
    const desc = text.slice(0, 512);
    updateSession(userId, { groupFlow: { ...flow, description: desc, step: "photo" } });
    await ctx.reply(`✅ Description set ho gayi!`);
    await askPhoto(ctx);
    return;
  }

  // Step: Description (edit)
  if (flow.step === "description_edit") {
    const desc = text.slice(0, 512);
    updateSession(userId, { groupFlow: { ...flow, description: desc, step: "confirm" } });
    await showConfirm(ctx);
    return;
  }

  // Step: Members
  if (flow.step === "members") {
    const nums = text.split(/[\n,\s]+/).map((n) => n.replace(/[^0-9]/g, "")).filter((n) => n.length >= 10);
    if (nums.length === 0) {
      await ctx.reply("❌ Valid numbers nahi mile. Country code ke saath likhen.\nYa Skip karein.");
      return;
    }
    updateSession(userId, { groupFlow: { ...flow, members: nums, step: "admin" } });
    await ctx.reply(`✅ *${nums.length} numbers add honge.*`);
    await askAdmin(ctx);
    return;
  }

  // Step: Members (edit)
  if (flow.step === "members_edit") {
    const nums = text.split(/[\n,\s]+/).map((n) => n.replace(/[^0-9]/g, "")).filter((n) => n.length >= 10);
    if (nums.length === 0) {
      await ctx.reply("❌ Valid numbers nahi mile. Country code ke saath likhen.\nYa Skip karein.");
      return;
    }
    updateSession(userId, { groupFlow: { ...flow, members: nums, step: "confirm" } });
    await showConfirm(ctx);
    return;
  }

  await sendMainMenu(ctx, "👇 Menu se option chunein:");
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
  const s      = getSession(userId);
  const flow   = s.groupFlow;

  if (!flow || (flow.step !== "photo" && flow.step !== "photo_edit")) {
    return;
  }

  try {
    const photo   = ctx.message.photo[ctx.message.photo.length - 1];
    const fileUrl = await ctx.telegram.getFileLink(photo.file_id);
    const resp    = await fetch(fileUrl.href);
    const buffer  = Buffer.from(await resp.arrayBuffer());

    const newStep = flow.step === "photo_edit" ? "confirm" : "disappearing";
    updateSession(userId, { groupFlow: { ...flow, photo: buffer, step: newStep } });

    await ctx.reply("✅ *Photo save ho gayi!*", { parse_mode: "Markdown" });

    if (newStep === "confirm") {
      await showConfirm(ctx);
    } else {
      await askDisappearing(ctx);
    }
  } catch (err) {
    console.error("[Photo] Error:", err.message);
    await ctx.reply("❌ Photo save nahi ho payi. Dobara bhejein.");
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
    <p style="color:#4ade80">Chal raha hai 🟢</p>
    <p>Uptime: ${Math.floor(process.uptime())}s | WA: ${getConnectedCount() > 0 ? "Connected ✅" : "Disconnected ❌"}</p>
  </body></html>`));

app.get("/health", (_req, res) => res.json({
  status: "ok",
  uptime: `${Math.floor(process.uptime())}s`,
  whatsapp: getStatus(0),
  phone: getPhone(0) || null,
  ts: new Date().toISOString(),
}));

app.listen(PORT, () => console.log(`🌐 HTTP server — port ${PORT}`));

// ─── Self-ping (Render free tier keep-alive) ──────────────────────────────
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
  console.log(`✅ WhatsApp Group Creator Bot running! Owner: ${OWNER_ID || "NOT SET"}`);
}

main().catch((err) => { console.error("❌ Fatal:", err.message); process.exit(1); });
process.once("SIGINT",  () => bot.stop("SIGINT"));
process.once("SIGTERM", () => bot.stop("SIGTERM"));
