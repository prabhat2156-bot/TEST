/**
 * WhatsApp Group Manager Bot — Improved UI Version
 *
 * IMPROVEMENTS:
 *  - New styled start/welcome message
 *  - Single message editing (no message spam — bot edits same message)
 *  - Feature-specific summaries with correct formats
 *  - All glitches fixed
 *  - withRetry: 3 retries + exponential backoff
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
  getPendingRawJids,
  resolveVcfPhones,
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
const aaLiveIntervals = new Map();

// ─── Per-feature delay constants (tuned for WA rate limits) ───────────────
const D = {
  getLinks:       1500,
  leave:          3000,
  removeMembers:  4000,
  makeAdmin:      3000,
  demoteAdmin:    2500,
  approvalToggle: 2000,
  approvePending: 4500,
  memberList:     1500,
  pendingList:    1500,
  resetLink:      2000,
  changeName:     2000,
  createGroup:    2500,
  joinGroup:      2500,
  addMembers:     2500,
  ctcCheck:       1200,
  vcfAutoMatch:   2000,
  pendingCheck:   1000,
};

// ─── Owner guard ───────────────────────────────────────────────────────────
bot.use(async (ctx, next) => {
  if (OWNER_ID && ctx.from?.id !== OWNER_ID) {
    if (ctx.callbackQuery) await ctx.answerCbQuery("⛔ Unauthorized.", { show_alert: true }).catch(() => {});
    else await ctx.reply("⛔ This bot is private.").catch(() => {});
    return;
  }
  return next();
});

// ─── Pairing callbacks ─────────────────────────────────────────────────────
const pendingPairingCbs = new Map();
const pendingReadyCbs   = new Map();

setCallbacks({
  onPairingCode:  async (i, code)  => { const cb = pendingPairingCbs.get(i); if (cb) { pendingPairingCbs.delete(i); await cb(code); } },
  onReady:        async (i)        => { const cb = pendingReadyCbs.get(i);   if (cb) { pendingReadyCbs.delete(i);   await cb();    } },
  onDisconnected: async ()         => {},
});

// ─── withRetry ────────────────────────────────────────────────────────────
async function withRetry(fn, retries = 3, baseDelay = 4000) {
  for (let attempt = 0; attempt <= retries; attempt++) {
    try { return await fn(); }
    catch (err) {
      if (attempt < retries) await sleep(Math.round(baseDelay * Math.pow(1.5, attempt)));
      else throw err;
    }
  }
}

// ═══════════════════════════════════════════════════════════════════════════
// ─── SINGLE-MESSAGE SYSTEM ─────────────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════
// Every user has one "main message" that gets edited on every action.
// sendMainMenu edits it. Features edit it. Summary edits it.
// Only the initial /start sends a fresh message (to establish the main msg).

async function getMainMsgId(uid) {
  return getSession(uid).mainMsgId || null;
}

async function editMain(chatId, msgId, text, extra = {}) {
  try {
    await bot.telegram.editMessageText(chatId, msgId, undefined, text, {
      parse_mode: "Markdown",
      ...extra,
    });
    return true;
  } catch { return false; }
}

// Edit the user's main message; if that fails, send a new one and track it
async function updateMain(ctx, text, extra = {}) {
  const uid    = ctx.from?.id;
  const chatId = ctx.chat?.id;
  const msgId  = uid ? getSession(uid).mainMsgId : null;
  if (msgId && chatId) {
    const ok = await editMain(chatId, msgId, text, extra);
    if (ok) return;
  }
  // Fallback: send new message and track it
  const m = await ctx.reply(text, { parse_mode: "Markdown", ...extra });
  if (uid) updateSession(uid, { mainMsgId: m.message_id });
}

// ─── Progress helpers ──────────────────────────────────────────────────────
async function startProgress(ctx, uid, text) {
  startTimes.set(uid, Date.now());
  updateSession(uid, { cancelPending: false });
  // Edit main message to show progress + cancel button
  await updateMain(ctx, text, {
    reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🛑 Cancel", "cancel_exec")]]).reply_markup,
  });
  return { message_id: getSession(uid).mainMsgId, chat: { id: ctx.chat?.id } };
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

// ─── Misc helpers ──────────────────────────────────────────────────────────
function bar(done, total) {
  const p = total > 0 ? Math.round((done / total) * 10) : 0;
  return `[${"█".repeat(p)}${"░".repeat(10 - p)}] ${total > 0 ? Math.round((done / total) * 100) : 0}%`;
}

function elapsed(uid) {
  const t = startTimes.get(uid);
  return t ? Math.round((Date.now() - t) / 1000) : 0;
}

function extractParticipantPhone(p) {
  const allJids = [p.jid, p.id, p.lid, p.userJid].filter((j) => j && typeof j === "string");
  const phoneJid = allJids.find((j) => j.endsWith("@s.whatsapp.net"));
  const displayJid = phoneJid || allJids[0] || "";
  return displayJid.split("@")[0].split(":")[0];
}

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

// ─── Main Menu Button ──────────────────────────────────────────────────────
const BACK_MENU_BTN = Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]);

// ═══════════════════════════════════════════════════════════════════════════
// ─── SUMMARY SYSTEM ────────────────────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════

/**
 * sendSummary — edits the main message with feature-specific summary,
 * then shows a "Main Menu" button so clicking it restores the menu IN THE SAME MESSAGE.
 */
async function sendSummary(ctx, opts) {
  const {
    feature,
    total,
    success,
    failed,
    cancelled,
    extra = [],       // array of extra lines
    links = [],       // array of {name, link} for white-box style
    groupDetails = [], // array of {name, detail} for group lists
  } = opts;

  const uid  = ctx.from?.id;
  const secs = uid ? elapsed(uid) : 0;
  if (uid) startTimes.delete(uid);

  const statusIcon = cancelled ? "🚫" : failed === 0 ? "✅" : "⚠️";
  const statusText = cancelled ? "Cancelled" : failed === 0 ? "Done!" : `Done (${failed} failed)`;

  let text = buildSummaryText(feature, total, success, failed, cancelled, secs, extra, links, groupDetails);

  if (text.length > 4000) text = text.slice(0, 3980) + "\n_...truncated_";

  const markup = Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]);

  const chatId = ctx.chat?.id;
  const msgId  = uid ? getSession(uid).mainMsgId : null;
  if (msgId && chatId) {
    try {
      await bot.telegram.editMessageText(chatId, msgId, undefined, text, {
        parse_mode: "Markdown",
        reply_markup: markup.reply_markup,
      });
      return;
    } catch {}
  }
  // Fallback
  const m = await ctx.reply(text, { parse_mode: "Markdown", ...markup });
  if (uid) updateSession(uid, { mainMsgId: m.message_id });
}

function buildSummaryText(feature, total, success, failed, cancelled, secs, extra, links, groupDetails) {
  const label = FEAT_LABEL[feature] || feature;

  // Wraps lines in a Telegram code block — renders as white box with COPY CODE button
  function codeBox(lines) {
    if (!lines.length) return "";
    // Strip any backticks from content to avoid breaking the code block
    const safe = lines.map(l => String(l).replace(/`/g, "'"));
    return "```\n" + safe.join("\n") + "\n```";
  }

  // ── CREATE GROUPS ──────────────────────────────────────────────────────
  if (feature === "create_groups") {
    let t = `*${label}*\n`;
    t += `▰▰▰▰▰▰▰▰▰▰▰▰▰\n`;
    t += `✅ Created: ${success}   ❌ Failed: ${failed}   ⏱ ${secs}s\n`;
    if (links.length) {
      const rows = [`${label}`];
      for (const { name, link } of links) {
        rows.push(`${name} ✅`);
        if (link) rows.push(link);
      }
      t += "\n" + codeBox(rows) + "\n";
    }
    if (extra.length) t += extra.join("\n") + "\n";
    t += `\nPAYMENT DONE 👹`;
    return t;
  }

  // ── JOIN GROUPS ────────────────────────────────────────────────────────
  if (feature === "join_groups") {
    let t = `*${label}*\n`;
    t += `▰▰▰▰▰▰▰▰▰▰▰▰▰\n`;
    t += `📊 Total Links : ${total}\n`;
    t += `✅ Joined      : ${success}\n`;
    t += `❌ Failed      : ${failed}\n`;
    if (cancelled) t += `🚫 Cancelled\n`;
    t += `⏱ Time : ${secs}s`;
    return t;
  }

  // ── GET LINKS ──────────────────────────────────────────────────────────
  if (feature === "get_links") {
    let t = `*${label}*\n`;
    t += `▰▰▰▰▰▰▰▰▰▰▰▰▰\n`;
    t += `📊 Total : ${total}   ✅ Got : ${success}   ❌ Failed : ${failed}`;
    if (cancelled) t += `   🚫 Cancelled`;
    t += `   ⏱ ${secs}s`;
    return t;
  }

  // ── LEAVE GROUPS ───────────────────────────────────────────────────────
  if (feature === "leave") {
    const rows = [`${label}`];
    for (const g of (groupDetails.length ? groupDetails : [])) {
      rows.push(`${g.name} ${g.detail && g.detail.startsWith("❌") ? "❌" : "✅"}`);
    }
    let t = `*${label}*\n`;
    t += `▰▰▰▰▰▰▰▰▰▰▰▰▰\n`;
    t += `📊 Selected: ${total}   ✅ Left: ${success}   ❌ Failed: ${failed}\n`;
    if (cancelled) t += `🚫 Cancelled\n`;
    t += `⏱ Time : ${secs}s`;
    return t;
  }

  // ── REMOVE MEMBERS ─────────────────────────────────────────────────────
  if (feature === "remove_members") {
    let t = `*${label}*\n`;
    t += `▰▰▰▰▰▰▰▰▰▰▰▰▰\n`;
    if (extra.length) t += extra[0] + "\n";
    t += `📊 Groups: ${total}   ✅ Done: ${success}   ❌ Failed: ${failed}\n`;
    if (groupDetails.length) {
      const rows = [`${label}`];
      for (const { name, detail } of groupDetails) {
        rows.push(`${name} — ${detail}`);
      }
      t += "\n" + codeBox(rows) + "\n";
    }
    if (cancelled) t += `🚫 Cancelled\n`;
    t += `⏱ Time : ${secs}s`;
    return t;
  }

  // ── MAKE ADMIN ─────────────────────────────────────────────────────────
  if (feature === "make_admin") {
    let t = `*${label}*\n`;
    t += `▰▰▰▰▰▰▰▰▰▰▰▰▰\n`;
    if (extra.length) t += extra[0] + "\n";
    t += `📊 Groups: ${total}   ✅ Success: ${success}   ❌ Failed: ${failed}\n`;
    if (groupDetails.length) {
      const rows = [`${label}`];
      for (const { name, detail } of groupDetails) {
        rows.push(`${name} ${detail && detail.startsWith("❌") ? "❌" : "✅"}`);
      }
      t += "\n" + codeBox(rows) + "\n";
    }
    if (cancelled) t += `🚫 Cancelled\n`;
    t += `⏱ Time : ${secs}s`;
    return t;
  }

  // ── DEMOTE ADMIN ───────────────────────────────────────────────────────
  if (feature === "demote_admin") {
    let t = `*${label}*\n`;
    t += `▰▰▰▰▰▰▰▰▰▰▰▰▰\n`;
    if (extra.length) t += extra[0] + "\n";
    t += `📊 Groups: ${total}   ✅ Success: ${success}   ❌ Failed: ${failed}\n`;
    if (groupDetails.length) {
      const rows = [`${label}`];
      for (const { name, detail } of groupDetails) {
        rows.push(`${name} ${detail && detail.startsWith("❌") ? "❌" : "✅"}`);
      }
      t += "\n" + codeBox(rows) + "\n";
    }
    if (cancelled) t += `🚫 Cancelled\n`;
    t += `⏱ Time : ${secs}s`;
    return t;
  }

  // ── APPROVAL TOGGLE ────────────────────────────────────────────────────
  if (feature === "approval") {
    let t = `*${label}*\n`;
    t += `▰▰▰▰▰▰▰▰▰▰▰▰▰\n`;
    t += `📊 Groups: ${total}   ✅ Success: ${success}   ❌ Failed: ${failed}\n`;
    if (cancelled) t += `🚫 Cancelled\n`;
    t += `⏱ Time : ${secs}s`;
    return t;
  }

  // ── APPROVE PENDING ────────────────────────────────────────────────────
  if (feature === "approve_pending") {
    let t = `*${label}*\n`;
    t += `▰▰▰▰▰▰▰▰▰▰▰▰▰\n`;
    if (extra.length) t += extra[0] + "\n";
    if (groupDetails.length) {
      const rows = [`${label}`];
      for (const { name, detail } of groupDetails) {
        const isErr = String(detail).startsWith("❌");
        rows.push(`${name} — ${detail} ${isErr ? "❌" : "✅"}`);
      }
      t += "\n" + codeBox(rows) + "\n";
    }
    if (cancelled) t += `🚫 Cancelled\n`;
    t += `⏱ Time : ${secs}s`;
    return t;
  }

  // ── RESET LINK ─────────────────────────────────────────────────────────
  if (feature === "reset_link") {
    let t = `*${label}*\n`;
    t += `▰▰▰▰▰▰▰▰▰▰▰▰▰\n`;
    t += `📊 Selected: ${total}   ✅ Success: ${success}   ❌ Failed: ${failed}\n`;
    if (links.length) {
      const rows = [`${label}`];
      for (const { name, link } of links) {
        rows.push(`${name} ✅`);
        if (link) rows.push(link);
      }
      t += "\n" + codeBox(rows) + "\n";
    }
    if (cancelled) t += `🚫 Cancelled\n`;
    t += `⏱ Time : ${secs}s`;
    return t;
  }

  // ── MEMBER LIST ────────────────────────────────────────────────────────
  if (feature === "member_list") {
    let t = `*${label}*\n`;
    t += `▰▰▰▰▰▰▰▰▰▰▰▰▰\n`;
    t += `📊 Total Groups : ${total}   ⏱ ${secs}s\n`;
    if (groupDetails.length) {
      const rows = [`${label}`];
      for (const { name, detail } of groupDetails) {
        rows.push(`${name} — ${detail}`);
      }
      t += "\n" + codeBox(rows);
    }
    return t;
  }

  // ── PENDING LIST ───────────────────────────────────────────────────────
  if (feature === "pending_list") {
    let t = `*${label}*\n`;
    t += `▰▰▰▰▰▰▰▰▰▰▰▰▰\n`;
    t += `📊 Total Groups : ${total}   ⏱ ${secs}s\n`;
    if (groupDetails.length) {
      const rows = [`${label}`];
      for (const { name, detail } of groupDetails) {
        rows.push(`${name} — ${detail} pending`);
      }
      t += "\n" + codeBox(rows);
    }
    return t;
  }

  // ── ADD MEMBERS ────────────────────────────────────────────────────────
  if (feature === "add_members") {
    let t = `*${label}*\n`;
    t += `▰▰▰▰▰▰▰▰▰▰▰▰▰\n`;
    if (extra.length) t += extra[0] + "\n";
    if (groupDetails.length) {
      const rows = [`${label}`];
      for (const { name, detail } of groupDetails) {
        rows.push(`${name} — ${detail}`);
      }
      t += "\n" + codeBox(rows) + "\n";
    }
    if (cancelled) t += `🚫 Cancelled\n`;
    t += `⏱ Time : ${secs}s`;
    return t;
  }

  // ── EDIT SETTINGS ──────────────────────────────────────────────────────
  if (feature === "edit_settings") {
    let t = `*${label}*\n`;
    t += `▰▰▰▰▰▰▰▰▰▰▰▰▰\n`;
    t += `📊 Groups: ${total}   ✅ Changed: ${success}   ❌ Failed: ${failed}\n`;
    if (extra[0]) t += extra[0] + "\n";
    if (cancelled) t += `🚫 Cancelled\n`;
    t += `⏱ Time : ${secs}s`;
    return t;
  }

  // ── CHANGE NAME ────────────────────────────────────────────────────────
  if (feature === "change_name") {
    let t = `*${label}*\n`;
    t += `▰▰▰▰▰▰▰▰▰▰▰▰▰\n`;
    t += `✅ Renamed: ${success}   ❌ Failed: ${failed}   ⏱ ${secs}s\n`;
    if (groupDetails.length) {
      const rows = [`${label}`];
      for (const { name, detail } of groupDetails) {
        rows.push(`${name}${detail ? " (" + detail + ")" : ""}`);
      }
      t += "\n" + codeBox(rows) + "\n";
    }
    if (cancelled) t += `🚫 Cancelled\n`;
    return t;
  }

  // ── AUTO ACCEPT ────────────────────────────────────────────────────────
  if (feature === "auto_accept") {
    let t = `*${label}*\n`;
    t += `▰▰▰▰▰▰▰▰▰▰▰▰▰\n`;
    if (extra.length >= 2) { t += extra[0] + "\n"; t += extra[1] + "\n"; }
    if (groupDetails.length) {
      const rows = [`${label}`];
      for (const { name, detail } of groupDetails) {
        rows.push(`${name} — ${detail} accepted`);
      }
      t += "\n" + codeBox(rows) + "\n";
    }
    if (cancelled) t += `🚫 Stopped\n`;
    t += `⏱ Time : ${secs}s`;
    return t;
  }

  // ── CTC CHECKER ────────────────────────────────────────────────────────
  if (feature === "ctc_checker") {
    let t = `*${label}*\n`;
    t += `▰▰▰▰▰▰▰▰▰▰▰▰▰\n`;
    t += `📊 Groups Checked : ${total}   ⏱ ${secs}s\n`;
    if (groupDetails.length) {
      const rows = [`${label}`];
      for (const { name, detail } of groupDetails) {
        rows.push(`${name}`);
        rows.push(`  ${detail}`);
      }
      t += "\n" + codeBox(rows);
    }
    return t;
  }

  // ── DEFAULT FALLBACK ───────────────────────────────────────────────────
  let t = `*${label}*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n`;
  t += `📊 Total: ${total}   ✅ Success: ${success}   ❌ Failed: ${failed}\n`;
  if (cancelled) t += `🚫 Cancelled\n`;
  t += `⏱ Time : ${secs}s`;
  if (extra.length) t += "\n" + extra.join("\n");
  return t;
}

// ═══════════════════════════════════════════════════════════════════════════
// ─── MAIN MENU ──────────────────────────────────────────────────────────────
// ═══════════════════════════════════════════════════════════════════════════

function buildMainMenu() {
  const c = getStatus(0) === "connected", p = getPhone(0);
  const b = (label, cb) => Markup.button.callback(label, c ? cb : "need_connect");
  return Markup.inlineKeyboard([
    [Markup.button.callback(c ? `📱 +${p} ✅` : `📱 WhatsApp ❌ Not Connected`, "menu_account")],
    [b("➕ Create Groups",   "create_groups_start"), b("🔗 Join Groups",     "join_groups_start")],
    [b("🔗 Get Links",       "feat_getlinks"),       b("🚪 Leave Groups",    "feat_leave")],
    [b("🧹 Remove Members",  "feat_removemem"),      b("👑 Make Admin",      "feat_makeadmin")],
    [b("⬇️ Demote Admin",    "feat_demoteadmin"),    b("🔀 Approval Toggle", "feat_approval")],
    [b("✅ Approve Pending", "feat_approvepending"), b("🔄 Reset Link",      "feat_resetlink")],
    [b("📋 Member List",     "feat_memberlist"),     b("⏳ Pending List",    "feat_pendinglist")],
    [b("➕ Add Members",     "feat_addmembers"),     b("⚙️ Edit Settings",   "feat_editsettings")],
    [b("✏️ Change Name",     "feat_changename"),     b("⏰ Auto Accept",     "feat_autoaccept")],
    [b("🔍 CTC Checker",     "feat_ctcchecker")],
    [Markup.button.callback("📊 Status", "menu_status")],
  ]);
}

function buildMenuText(ctx) {
  const c = getStatus(0) === "connected", p = getPhone(0);
  const userName = [ctx.from?.first_name, ctx.from?.last_name].filter(Boolean).join(" ") || "User";
  return (
    `🤖 *ᴡꜱ ᴀᴜᴛᴏᴍᴀᴛɪᴏɴ* 🤖\n` +
    `▰▰▰▰▰▰▰▰▰▰▰▰▰\n` +
    `👋 Hey *${userName}*, Welcome!\n\n` +
    `╭─── 📡 Status ──────────╮\n` +
    `│ ${c ? `✅  WhatsApp: +${p}` : `❌  WhatsApp: Not Connected`}\n` +
    `╰───────────────────────╯\n\n` +
    `› *Choose an option:*`
  );
}

// Send/edit the main menu message
async function sendMainMenu(ctx) {
  const uid = ctx.from?.id;
  if (aaLiveIntervals.has(uid)) { clearInterval(aaLiveIntervals.get(uid)); aaLiveIntervals.delete(uid); }
  updateSession(uid, { cancelPending: false, awaitingVcf: null, featureFlow: null, groupFlow: null, joinFlow: null });

  const text   = buildMenuText(ctx);
  const markup = buildMainMenu();
  const chatId = ctx.chat?.id;
  const msgId  = uid ? getSession(uid).mainMsgId : null;

  if (msgId && chatId) {
    try {
      await bot.telegram.editMessageText(chatId, msgId, undefined, text, {
        parse_mode: "Markdown",
        reply_markup: markup.reply_markup,
      });
      return;
    } catch {}
  }

  // Send fresh message and track its ID
  const m = await ctx.reply(text, { parse_mode: "Markdown", ...markup });
  if (uid) updateSession(uid, { mainMsgId: m.message_id });
}

// ─── /start ───────────────────────────────────────────────────────────────
bot.start(async (ctx) => {
  resetSession(ctx.from.id);
  const m = await ctx.reply(buildMenuText(ctx), { parse_mode: "Markdown", ...buildMainMenu() });
  updateSession(ctx.from.id, { mainMsgId: m.message_id });
});

// ─── /menu command ────────────────────────────────────────────────────────
bot.command("menu", async (ctx) => {
  resetSession(ctx.from.id);
  const m = await ctx.reply(buildMenuText(ctx), { parse_mode: "Markdown", ...buildMainMenu() });
  updateSession(ctx.from.id, { mainMsgId: m.message_id });
});

bot.action("need_connect", async (ctx) => {
  await ctx.answerCbQuery("⚠️ Connect WhatsApp first!", { show_alert: true });
});

bot.action("back_menu", async (ctx) => {
  await ctx.answerCbQuery();
  const uid = ctx.from.id;
  if (aaLiveIntervals.has(uid)) { clearInterval(aaLiveIntervals.get(uid)); aaLiveIntervals.delete(uid); }
  updateSession(uid, {
    awaitingPhoneForIndex: null, groupFlow: null, joinFlow: null,
    featureFlow: null, cancelPending: false, awaitingVcf: null,
  });
  // Update mainMsgId to the current message being edited
  updateSession(uid, { mainMsgId: ctx.callbackQuery.message?.message_id || getSession(uid).mainMsgId });
  await sendMainMenu(ctx);
});

// ─── Status ────────────────────────────────────────────────────────────────
bot.action("menu_status", async (ctx) => {
  await ctx.answerCbQuery();
  const uid = ctx.from.id;
  updateSession(uid, { mainMsgId: ctx.callbackQuery.message?.message_id || getSession(uid).mainMsgId });
  const s = getStatus(0), p = getPhone(0);
  const icon = s === "connected" ? "✅" : s === "connecting" ? "⏳" : "❌";
  await updateMain(ctx,
    `📊 *Bot Status*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n${icon} WhatsApp : *${s}*${s === "connected" ? `\n📞 +${p}` : ""}\n▰▰▰▰▰▰▰▰▰▰▰▰▰`,
    { reply_markup: BACK_MENU_BTN.reply_markup }
  );
});

// ─── Account ───────────────────────────────────────────────────────────────
bot.action("menu_account", async (ctx) => {
  await ctx.answerCbQuery();
  const uid = ctx.from.id;
  updateSession(uid, { mainMsgId: ctx.callbackQuery.message?.message_id || getSession(uid).mainMsgId });
  const status = getStatus(0), phone = getPhone(0);
  if (status === "connected") {
    await updateMain(ctx,
      `📱 *WhatsApp Account*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n✅ Connected\n📞 +${phone}\n▰▰▰▰▰▰▰▰▰▰▰▰▰\nLogout?`,
      { reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🔌 Logout", "logout_wa")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]).reply_markup }
    );
  } else if (status === "connecting") {
    await updateMain(ctx,
      `📱 *WhatsApp Account*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n⏳ Connecting...\n▰▰▰▰▰▰▰▰▰▰▰▰▰`,
      { reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🔄 Reset", "reset_wa")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]).reply_markup }
    );
  } else {
    updateSession(uid, { awaitingPhoneForIndex: 0 });
    await updateMain(ctx,
      `📱 *Connect WhatsApp*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n\nSend your phone number with country code:\n\n*Example:* \`919876543210\`\n\n⚠️ Pairing code expires in 60 seconds!`,
      { reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]).reply_markup }
    );
  }
});

bot.action("logout_wa", async (ctx) => {
  await ctx.answerCbQuery("Logging out...");
  await updateMain(ctx, `⏳ *Logging out...*`);
  await disconnectAccount(0);
  await sleep(800);
  await sendMainMenu(ctx);
});

bot.action("reset_wa", async (ctx) => {
  await ctx.answerCbQuery("Resetting...");
  await disconnectAccount(0);
  updateSession(ctx.from.id, { awaitingPhoneForIndex: 0 });
  await updateMain(ctx,
    `📱 *Connect WhatsApp*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n\nSend your phone number:\n*Example:* \`919876543210\``,
    { reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🏠 Main Menu", "back_menu")]]).reply_markup }
  );
});

// ══════════════════════════════════════════════════════════════════════════
// ─── GROUP SELECTION SYSTEM ───────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

function featBtn(feature) {
  return [
    Markup.button.callback("🔍 Similar Groups", `gs_similar_${feature}`),
    Markup.button.callback("📋 All Groups",      `gs_all_${feature}`),
    Markup.button.callback("☑️ Select Groups",   `gs_select_${feature}`),
  ];
}

async function showGroupTypeSelect(ctx, feature) {
  const uid = ctx.from.id;
  updateSession(uid, { mainMsgId: ctx.callbackQuery?.message?.message_id || getSession(uid).mainMsgId });
  const label = FEAT_LABEL[feature] || feature;
  await updateMain(ctx,
    `${label}\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n*Select groups:*`,
    { reply_markup: Markup.inlineKeyboard([
      [Markup.button.callback("🔍 Similar Groups", `gs_similar_${feature}`)],
      [Markup.button.callback("📋 All Groups",      `gs_all_${feature}`)],
      [Markup.button.callback("☑️ Select Groups",   `gs_select_${feature}`)],
      [Markup.button.callback("🏠 Main Menu", "back_menu")],
    ]).reply_markup }
  );
}

// ─── Feature entry points ──────────────────────────────────────────────────
const simpleFeatures = {
  feat_getlinks:       "get_links",
  feat_leave:          "leave",
  feat_removemem:      "remove_members",
  feat_makeadmin:      "make_admin",
  feat_demoteadmin:    "demote_admin",
  feat_approval:       "approval",
  feat_approvepending: "approve_pending",
  feat_resetlink:      "reset_link",
  feat_memberlist:     "member_list",
  feat_pendinglist:    "pending_list",
  feat_addmembers:     "add_members",
  feat_editsettings:   "edit_settings",
  feat_changename:     "change_name",
  feat_autoaccept:     "auto_accept",
  feat_ctcchecker:     "ctc_checker",
};

Object.entries(simpleFeatures).forEach(([action, feature]) => {
  bot.action(action, async (ctx) => {
    await ctx.answerCbQuery();
    const uid = ctx.from.id;
    updateSession(uid, {
      mainMsgId: ctx.callbackQuery.message?.message_id || getSession(uid).mainMsgId,
      featureFlow: defaultFeatureFlow(feature),
    });
    await showGroupTypeSelect(ctx, feature);
  });
});

// ─── All groups ────────────────────────────────────────────────────────────
bot.action(/^gs_all_(.+)$/, async (ctx) => {
  await ctx.answerCbQuery("Loading all groups...");
  const feature = ctx.match[1];
  const uid = ctx.from.id;
  try {
    const groups = await getAllGroupsWithDetails(0);
    if (!groups.length) {
      await updateMain(ctx, `❌ No groups found.`, { reply_markup: BACK_MENU_BTN.reply_markup });
      return;
    }
    updateSession(uid, { featureFlow: { ...getSession(uid).featureFlow, feature, allGroups: groups, selectedIds: groups.map(g => g.id) } });
    await onGroupsConfirmed(ctx, feature, groups.map(g => g.id), groups);
  } catch (err) {
    await updateMain(ctx, `❌ Error: ${err.message}`, { reply_markup: BACK_MENU_BTN.reply_markup });
  }
});

// ─── Similar groups ────────────────────────────────────────────────────────
bot.action(/^gs_similar_(.+)$/, async (ctx) => {
  await ctx.answerCbQuery("Choose similar method...");
  const feature = ctx.match[1];
  const uid = ctx.from.id;
  updateSession(uid, { featureFlow: { ...getSession(uid).featureFlow, feature, step: "similar_method" } });
  await updateMain(ctx,
    `🔍 *Similar Groups*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\nChoose method:`,
    { reply_markup: Markup.inlineKeyboard([
      [Markup.button.callback("🔤 By Keyword", `gs_sim_keyword_${feature}`)],
      [Markup.button.callback("📂 All Groups (auto-detect)", `gs_all_${feature}`)],
      [Markup.button.callback("🏠 Main Menu", "back_menu")],
    ]).reply_markup }
  );
});

bot.action(/^gs_sim_keyword_(.+)$/, async (ctx) => {
  await ctx.answerCbQuery();
  const feature = ctx.match[1];
  const uid = ctx.from.id;
  updateSession(uid, { featureFlow: { ...getSession(uid).featureFlow, feature, step: "similar_query" } });
  await updateMain(ctx,
    `🔍 *Similar Groups — Keyword*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\nType a keyword to search group names:`,
    { reply_markup: BACK_MENU_BTN.reply_markup }
  );
});

// ─── Select groups (paginated) ─────────────────────────────────────────────
bot.action(/^gs_select_(.+)$/, async (ctx) => {
  await ctx.answerCbQuery("Loading...");
  const feature = ctx.match[1];
  const uid = ctx.from.id;
  try {
    const groups = await getAllGroupsWithDetails(0);
    if (!groups.length) {
      await updateMain(ctx, `❌ No groups found.`, { reply_markup: BACK_MENU_BTN.reply_markup });
      return;
    }
    updateSession(uid, {
      featureFlow: { ...getSession(uid).featureFlow, feature, allGroups: groups, selectedIds: [], page: 0, step: "select" },
    });
    await showPaginatedGroups(ctx);
  } catch (err) {
    await updateMain(ctx, `❌ Error: ${err.message}`, { reply_markup: BACK_MENU_BTN.reply_markup });
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
    const name = g.name.length > 38 ? g.name.slice(0, 37) + "…" : g.name;
    rows.push([Markup.button.callback(`${selSet.has(g.id) ? "✅" : "◻️"} ${name}`, `gs_tog_${idx}`)]);
  }
  const nav = [];
  if (page > 0)              nav.push(Markup.button.callback("◀️ Prev", "gs_prev"));
  nav.push(Markup.button.callback(`${page + 1}/${totalPages}`, "gs_noop"));
  if (page < totalPages - 1) nav.push(Markup.button.callback("Next ▶️", "gs_next"));
  if (nav.length) rows.push(nav);
  rows.push([Markup.button.callback(`✅ Confirm (${selSet.size})`, "gs_confirm")]);
  rows.push([Markup.button.callback("🏠 Main Menu", "back_menu")]);

  const text = `☑️ *Select Groups* — Page ${page + 1}/${totalPages}\n▰▰▰▰▰▰▰▰▰▰▰▰▰\nTotal: *${allGroups.length}*  Selected: *${selSet.size}*\n_Tap to select/deselect_`;
  await updateMain(ctx, text, { reply_markup: Markup.inlineKeyboard(rows).reply_markup });
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
  const idx  = parseInt(ctx.match[1]);
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
    await ctx.answerCbQuery("⚠️ Select at least 1 group!", { show_alert: true });
    return;
  }
  await onGroupsConfirmed(ctx, flow.feature, flow.selectedIds, flow.allGroups);
});
bot.action("gs_sim_proceed", async (ctx) => {
  await ctx.answerCbQuery();
  const flow = getSession(ctx.from.id).featureFlow;
  await onGroupsConfirmed(ctx, flow.feature, flow.selectedIds, flow.allGroups);
});

// ─── After group selection confirmed ──────────────────────────────────────
async function onGroupsConfirmed(ctx, feature, selectedIds, allGroups) {
  const s = getSession(ctx.from.id);

  if (feature === "make_admin") {
    updateSession(ctx.from.id, { featureFlow: { ...s.featureFlow, selectedIds, allGroups, step: "admin_numbers" } });
    await updateMain(ctx,
      `👑 *Make Admin*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n*${selectedIds.length} group(s) selected*\n\nSend phone numbers to make admin — one per line:\n\`\`\`\n919876543210\n918765432109\n\`\`\`\n_Country code required_`,
      { reply_markup: BACK_MENU_BTN.reply_markup }
    );
    return;
  }
  if (feature === "demote_admin") {
    updateSession(ctx.from.id, { featureFlow: { ...s.featureFlow, selectedIds, allGroups, step: "demote_numbers" } });
    await updateMain(ctx,
      `⬇️ *Demote Admin*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n*${selectedIds.length} group(s) selected*\n\nSend admin phone numbers to demote — one per line:`,
      { reply_markup: BACK_MENU_BTN.reply_markup }
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
  if (feature === "add_members") {
    updateSession(ctx.from.id, { featureFlow: { ...s.featureFlow, selectedIds, allGroups, step: "am_links" } });
    await updateMain(ctx,
      `➕ *Add Members*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n*${selectedIds.length} group(s) selected*\n\nSend group invite links (one per line) — VCF will be matched per group:`,
      { reply_markup: BACK_MENU_BTN.reply_markup }
    );
    return;
  }
  if (feature === "change_name") {
    updateSession(ctx.from.id, { featureFlow: { ...s.featureFlow, selectedIds, allGroups, step: "cn_method" } });
    await updateMain(ctx,
      `✏️ *Change Name*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n*${selectedIds.length} group(s) selected*\n\nChoose naming method:`,
      { reply_markup: Markup.inlineKeyboard([
        [Markup.button.callback("🔤 Custom Name (sequential)", "cn_random")],
        [Markup.button.callback("📁 VCF File Match",           "cn_vcf_match")],
        [Markup.button.callback("🏠 Main Menu", "back_menu")],
      ]).reply_markup }
    );
    return;
  }
  if (feature === "ctc_checker") {
    updateSession(ctx.from.id, { featureFlow: { ...s.featureFlow, selectedIds, allGroups, step: "ctc_links" }, awaitingVcf: { feature: "ctc_checker", step: "ctc_vcf" } });
    await updateMain(ctx,
      `🔍 *CTC Checker*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n*${selectedIds.length} group(s) selected*\n\nNow send your VCF file:`,
      { reply_markup: BACK_MENU_BTN.reply_markup }
    );
    return;
  }

  updateSession(ctx.from.id, { featureFlow: { ...s.featureFlow, selectedIds, allGroups } });
  await runFeature(ctx, feature, selectedIds, allGroups, []);
}

// ══════════════════════════════════════════════════════════════════════════
// ─── EDIT SETTINGS ────────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

function esFmt(val) { return val === null ? "─ Skip" : val ? "✅ ON" : "❌ OFF"; }
function esFmtSend(val) { return val === null ? "─ Skip" : val === false ? "✅ ON" : "❌ OFF"; }

function settingsKb(d) {
  return Markup.inlineKeyboard([
    [Markup.button.callback(`💬 All Can Send  : ${esFmtSend(d.announce)}`,   "es_tog_announce")],
    [Markup.button.callback(`✏️ Edit Info Lock : ${esFmt(d.restrict)}`,       "es_tog_restrict")],
    [Markup.button.callback(`🔐 Join Approval : ${esFmt(d.joinApproval)}`,   "es_tog_joinApproval")],
    [Markup.button.callback(`➕ All Can Add   : ${esFmt(d.memberAddMode)}`,  "es_tog_memberAddMode")],
    [Markup.button.callback("💾 Apply Settings", "es_apply")],
    [Markup.button.callback("🏠 Main Menu", "back_menu")],
  ]);
}

async function showEditSettingsConfig(ctx) {
  const flow = getSession(ctx.from.id).featureFlow;
  const d    = flow.desiredSettings;
  await updateMain(ctx,
    `⚙️ *Edit Settings*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n*${flow.selectedIds.length} group(s)*\n\nTap to toggle — cycles: Skip → ON → OFF\n*Skip* = don't change this setting`,
    { reply_markup: settingsKb(d).reply_markup }
  );
}

["announce", "restrict", "joinApproval", "memberAddMode"].forEach((key) => {
  bot.action(`es_tog_${key}`, async (ctx) => {
    await ctx.answerCbQuery();
    const flow = getSession(ctx.from.id).featureFlow;
    const cur  = flow.desiredSettings[key];
    let next;
    if (key === "announce") {
      next = cur === null ? false : cur === false ? true : null;
    } else {
      next = cur === null ? true : cur === true ? false : null;
    }
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
    await ctx.answerCbQuery("⚠️ No settings selected!", { show_alert: true });
    return;
  }
  const sel   = flow.allGroups.filter((g) => flow.selectedIds.includes(g.id));
  const total = sel.length;
  updateSession(uid, { cancelPending: false });
  const pm = await startProgress(ctx, uid, `⚙️ Applying settings...\n${bar(0, total)}`);
  let changed = 0, alreadyOk = 0, failed = 0, cancelled = false;

  for (let i = 0; i < total; i++) {
    if (isCancelled(uid)) { cancelled = true; break; }
    const g = sel[i];
    await editProgress(ctx.chat.id, pm.message_id,
      `⚙️ Applying settings...\nDone: ${i}/${total}  ❌ ${failed}\n→ ${g.name}\n${bar(i, total)}`);
    try {
      const result = await withRetry(() => applyGroupSettings(0, g.id, d));
      if (result.changes.length) changed++;
      else alreadyOk++;
    } catch { failed++; }
    await sleep(D.approvalToggle);
  }

  await sendSummary(ctx, {
    feature: "edit_settings",
    total, success: changed, failed, cancelled,
    extra: [`⏭ Already OK : ${alreadyOk}`],
  });
  updateSession(uid, { featureFlow: null });
});

// ══════════════════════════════════════════════════════════════════════════
// ─── CHANGE NAME ──────────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

bot.action("cn_random", async (ctx) => {
  await ctx.answerCbQuery();
  const flow = getSession(ctx.from.id).featureFlow;
  updateSession(ctx.from.id, { featureFlow: { ...flow, step: "cn_random_name", cnMethod: "random" } });
  await updateMain(ctx,
    `✏️ *Change Name — Custom*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n\nType the base name:\n_Example:_ \`Madara\` → groups become _Madara 1, Madara 2..._`,
    { reply_markup: BACK_MENU_BTN.reply_markup }
  );
});

bot.action("cn_vcf_match", async (ctx) => {
  await ctx.answerCbQuery();
  const flow = getSession(ctx.from.id).featureFlow;
  updateSession(ctx.from.id, { featureFlow: { ...flow, step: "cn_vcf_match", cnMethod: "vcf" }, awaitingVcf: { feature: "change_name", step: "cn_vcf_files" } });
  await updateMain(ctx,
    `✏️ *Change Name — VCF Match*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n\nSend VCF file(s). Bot will match group members to VCF name and rename accordingly.`,
    { reply_markup: Markup.inlineKeyboard([
      [Markup.button.callback("▶️ Done — Run Match", "cn_run_vcf")],
      [Markup.button.callback("🏠 Main Menu", "back_menu")],
    ]).reply_markup }
  );
});

bot.action("cn_numbering_yes", async (ctx) => {
  await ctx.answerCbQuery();
  const flow = getSession(ctx.from.id).featureFlow;
  updateSession(ctx.from.id, { featureFlow: { ...flow, numbering: true, step: "cn_random_links" } });
  await updateMain(ctx,
    `✏️ *Numbering: ON*\nNames: _${flow.cnBaseName} 1, ${flow.cnBaseName} 2..._\n\nNow send group invite links (one per line):`,
    { reply_markup: BACK_MENU_BTN.reply_markup }
  );
});

bot.action("cn_numbering_no", async (ctx) => {
  await ctx.answerCbQuery();
  const flow = getSession(ctx.from.id).featureFlow;
  updateSession(ctx.from.id, { featureFlow: { ...flow, numbering: false, step: "cn_random_links" } });
  await updateMain(ctx,
    `✏️ *Numbering: OFF*\nAll groups: _${flow.cnBaseName}_\n\nNow send group invite links (one per line):`,
    { reply_markup: BACK_MENU_BTN.reply_markup }
  );
});

async function runChangeNameRandom(ctx, links, baseName, numbering) {
  const uid = ctx.from.id;
  const total = links.length;
  updateSession(uid, { cancelPending: false });
  const pm = await startProgress(ctx, uid, `✏️ Renaming ${total} group(s)...\n${bar(0, total)}`);
  let done = 0, failed = 0, cancelled = false;
  const groupDetails = [];
  for (let i = 0; i < total; i++) {
    if (isCancelled(uid)) { cancelled = true; break; }
    const code    = links[i];
    const newName = numbering ? `${baseName} ${i + 1}` : baseName;
    await editProgress(ctx.chat.id, pm.message_id,
      `✏️ Renaming...\nDone: ${done}/${total}  ❌ ${failed}\n→ "${newName}"\n${bar(i, total)}`);
    try {
      const info = await withRetry(() => getGroupInfoFromLink(0, code));
      if (!info) throw new Error("Invalid/expired link");
      const oldName = info.name;
      await withRetry(() => renameGroup(0, info.id, newName));
      done++;
      groupDetails.push({ name: `${oldName} ➡️ ${newName}`, detail: "" });
    } catch (err) {
      failed++;
      groupDetails.push({ name: `Group ${i + 1}`, detail: `❌ ${err.message}` });
    }
    await sleep(D.changeName);
  }
  await sendSummary(ctx, { feature: "change_name", total, success: done, failed, cancelled, groupDetails });
  updateSession(uid, { featureFlow: null });
}

bot.action("cn_run_vcf", async (ctx) => {
  await ctx.answerCbQuery("Running VCF match...");
  const uid  = ctx.from.id;
  const flow = getSession(uid).featureFlow;
  if (!flow?.vcfFiles?.length) {
    await updateMain(ctx, `❌ No VCF files received yet. Send at least one VCF file first.`, { reply_markup: BACK_MENU_BTN.reply_markup });
    return;
  }
  await runChangeNameVcfMatch(ctx);
});

async function runChangeNameVcfMatch(ctx) {
  const uid  = ctx.from.id;
  const flow = getSession(uid).featureFlow;
  const allGroups = flow.allGroups || await getAllGroupsWithDetails(0);
  const selectedIds = flow.selectedIds || [];
  const groups = selectedIds.length ? allGroups.filter(g => selectedIds.includes(g.id)) : allGroups;
  const totalGroups = groups.length;

  updateSession(uid, { cancelPending: false, awaitingVcf: null });
  const pm = await startProgress(ctx, uid, `✏️ Matching VCF → Group names...\n${bar(0, totalGroups)}`);

  const resolvedVcfs = [];
  for (const vcfContent of (flow.vcfFiles || [])) {
    const contacts = parseVcf(vcfContent);
    for (const c of contacts) {
      try {
        const resolved = await resolveVcfPhones(0, [c.phone]);
        resolvedVcfs.push({ name: c.name, resolved });
      } catch {
        resolvedVcfs.push({ name: c.name, resolved: [{ phone: c.phone }] });
      }
    }
    await sleep(D.vcfAutoMatch);
  }

  let renamed = 0, skipped = 0, failed = 0, cancelled = false;
  const groupDetails = [];

  for (let gi = 0; gi < totalGroups; gi++) {
    if (isCancelled(uid)) { cancelled = true; break; }
    const g = groups[gi];
    await editProgress(ctx.chat.id, pm.message_id,
      `✏️ Matching...\nGroup ${gi + 1}/${totalGroups}\n→ ${g.name}\n${bar(gi, totalGroups)}`);
    try {
      const members = await withRetry(() => getGroupMembers(0, g.id));
      const groupPhones = new Set(members.map(m => m.phone));

      function numberMatches(stored, input) {
        if (!stored || !input) return false;
        const s = stored.replace(/\D/g, ""), i = input.replace(/\D/g, "");
        if (s === i) return true;
        if (i.length >= 8 && s.endsWith(i)) return true;
        if (s.length >= 8 && i.endsWith(s)) return true;
        return false;
      }

      let bestVcf = null, bestCount = 0;
      for (const vcf of resolvedVcfs) {
        let count = 0;
        for (const r of (vcf.resolved || [])) {
          if (r.phone && groupPhones.size > 0) {
            for (const gph of groupPhones) {
              if (numberMatches(gph, r.phone)) { count++; break; }
            }
          }
        }
        if (count > bestCount) { bestCount = count; bestVcf = vcf; }
      }

      if (bestVcf && bestCount > 0) {
        await withRetry(() => renameGroup(0, g.id, bestVcf.name));
        renamed++;
        groupDetails.push({ name: `${g.name} ➡️ ${bestVcf.name}`, detail: `${bestCount} match(es)` });
      } else {
        skipped++;
      }
    } catch (err) {
      failed++;
    }
    await sleep(D.vcfAutoMatch);
  }

  await sendSummary(ctx, { feature: "change_name", total: totalGroups, success: renamed, failed, cancelled, groupDetails });
  updateSession(uid, { featureFlow: null, awaitingVcf: null });
}

// ══════════════════════════════════════════════════════════════════════════
// ─── AUTO ACCEPT ──────────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

async function showAutoAcceptDuration(ctx) {
  const flow = getSession(ctx.from.id).featureFlow;
  await updateMain(ctx,
    `⏰ *Auto Accept*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n*${flow.selectedIds.length} group(s) selected*\n\nSelect duration:`,
    { reply_markup: Markup.inlineKeyboard([
      [Markup.button.callback("5 min",  "aa_dur_300"),    Markup.button.callback("10 min", "aa_dur_600")],
      [Markup.button.callback("30 min", "aa_dur_1800"),   Markup.button.callback("1 hour", "aa_dur_3600")],
      [Markup.button.callback("2 hrs",  "aa_dur_7200"),   Markup.button.callback("6 hrs",  "aa_dur_21600")],
      [Markup.button.callback("✏️ Custom (minutes)", "aa_dur_custom")],
      [Markup.button.callback("🏠 Main Menu", "back_menu")],
    ]).reply_markup }
  );
}

bot.action(/^aa_dur_(\d+)$/, async (ctx) => {
  await ctx.answerCbQuery();
  const secs = parseInt(ctx.match[1]);
  const flow = getSession(ctx.from.id).featureFlow;
  updateSession(ctx.from.id, { featureFlow: { ...flow, aaDuration: secs, step: "aa_confirm" } });
  const mins = secs / 60, label = mins >= 60 ? `${mins / 60}h` : `${mins}min`;
  await updateMain(ctx,
    `⏰ *Auto Accept — Confirm*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\nGroups   : *${flow.selectedIds.length}*\nDuration : *${label}*\n\n_Checks every 8 seconds. Approval must be ON._`,
    { reply_markup: Markup.inlineKeyboard([
      [Markup.button.callback("▶️ Start", "aa_start")],
      [Markup.button.callback("🔙 Change Duration", "aa_back_duration")],
      [Markup.button.callback("🏠 Main Menu", "back_menu")],
    ]).reply_markup }
  );
});

bot.action("aa_dur_custom", async (ctx) => {
  await ctx.answerCbQuery();
  const flow = getSession(ctx.from.id).featureFlow;
  updateSession(ctx.from.id, { featureFlow: { ...flow, step: "aa_custom_duration" } });
  await updateMain(ctx,
    `⏰ *Custom Duration*\nType duration in minutes:\n_Example:_ \`120\` = 2 hours`,
    { reply_markup: BACK_MENU_BTN.reply_markup }
  );
});

bot.action("aa_back_duration", async (ctx) => { await ctx.answerCbQuery(); await showAutoAcceptDuration(ctx); });

function buildLiveAAText(sel, label, endTime, stats) {
  const totalAccepted = Object.values(stats).reduce((s, v) => s + (v?.accepted || 0), 0);
  const groupLines = sel.map((g) => `• *${g.name}*: ${stats[g.id]?.accepted || 0} accepted`).join("\n");
  return (
    `⏰ *Auto Accept — ACTIVE* 🟢\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n` +
    `Groups   : *${sel.length}*\nDuration : *${label}*\nEnds at  : ${endTime}\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n` +
    `✅ *Total Accepted: ${totalAccepted}*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n${groupLines}\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n_Updates every 5s_`
  );
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

  startAutoAcceptForGroups(flow.selectedIds);
  updateSession(uid, { featureFlow: { ...flow, step: "aa_running" } });

  const initialStats = getAutoAcceptStats(flow.selectedIds);
  await updateMain(ctx,
    buildLiveAAText(sel, label, endTime, initialStats),
    { reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🛑 Stop", "aa_stop")]]).reply_markup }
  );

  // Live update every 5 seconds
  const liveInterval = setInterval(async () => {
    try {
      const stats = getAutoAcceptStats(flow.selectedIds);
      const msgId = getSession(uid).mainMsgId;
      if (msgId) {
        await bot.telegram.editMessageText(ctx.chat.id, msgId, undefined,
          buildLiveAAText(sel, label, endTime, stats),
          { parse_mode: "Markdown", reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🛑 Stop", "aa_stop")]]).reply_markup }
        );
      }
    } catch {}
  }, 5000);

  aaLiveIntervals.set(uid, liveInterval);

  setTimeout(async () => {
    if (!aaLiveIntervals.has(uid)) return;
    clearInterval(aaLiveIntervals.get(uid));
    aaLiveIntervals.delete(uid);
    const stats = getAutoAcceptStats(flow.selectedIds);
    stopAutoAcceptForGroups(flow.selectedIds);
    const totalAccepted = Object.values(stats).reduce((s, v) => s + (v?.accepted || 0), 0);
    const groupDetails = sel.map((g) => ({ name: g.name, detail: String(stats[g.id]?.accepted || 0) }));
    await sendSummary(ctx, {
      feature: "auto_accept", total: sel.length, success: sel.length, failed: 0, cancelled: false,
      extra: [`✅ Total Accepted: ${totalAccepted}`, `Duration: ${label}`],
      groupDetails,
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
  const total = Object.values(stats).reduce((s, v) => s + (v?.accepted || 0), 0);
  const sel   = (flow.allGroups || []).filter((g) => flow.selectedIds.includes(g.id));
  const groupDetails = sel.map((g) => ({ name: g.name, detail: String(stats[g.id]?.accepted || 0) }));
  const mins = (flow.aaDuration || 0) / 60, label = mins >= 60 ? `${mins / 60}h` : `${mins}min`;
  await sendSummary(ctx, {
    feature: "auto_accept", total: sel.length, success: sel.length, failed: 0, cancelled: true,
    extra: [`Total Accepted: ${total}`, `Duration: ${label}`],
    groupDetails,
  });
  updateSession(uid, { featureFlow: null });
});

// ══════════════════════════════════════════════════════════════════════════
// ─── MAIN FEATURE RUNNER ──────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

async function runFeature(ctx, feature, selectedIds, allGroups, extraNums) {
  const uid   = ctx.from.id;
  const sel   = allGroups.filter((g) => selectedIds.includes(g.id));
  const total = sel.length;
  if (!total) {
    await updateMain(ctx, "❌ No groups selected.", { reply_markup: BACK_MENU_BTN.reply_markup });
    return;
  }
  updateSession(uid, { cancelPending: false });

  // ── GET LINKS ─────────────────────────────────────────────────────────
  if (feature === "get_links") {
    const pm = await startProgress(ctx, uid, `🔗 Getting links — ${total} group(s)...\n${bar(0, total)}`);
    let done = 0, failed = 0, cancelled = false;
    const linkResults = [], fails = [];
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      await editProgress(ctx.chat.id, pm.message_id,
        `🔗 Getting links...\n✅ ${done}  ❌ ${failed}\n→ ${g.name}\n${bar(i, total)}`);
      try {
        const link = await withRetry(() => getGroupInviteLink(0, g.id));
        linkResults.push({ name: g.name, link }); done++;
      } catch { fails.push(g.name); failed++; }
      await sleep(D.getLinks);
    }
    // For get_links: send separate messages for the full link list (may be large)
    // Summary in main message, links in separate chunks
    const CHUNK = 20;
    for (let c = 0; c < linkResults.length; c += CHUNK) {
      const chunk = linkResults.slice(c, c + CHUNK);
      const rows = [`🔗 Group Links (${c + 1}–${c + chunk.length}/${linkResults.length})`];
      for (const r of chunk) {
        rows.push(`${r.name} ✅`);
        rows.push(r.link);
      }
      const safe = rows.map(l => String(l).replace(/`/g, "'"));
      const block = "```\n" + safe.join("\n") + "\n```";
      await ctx.reply(block, { parse_mode: "Markdown" });
      if (c + CHUNK < linkResults.length) await sleep(300);
    }
    await sendSummary(ctx, {
      feature: "get_links", total, success: done, failed, cancelled,
    });
    updateSession(uid, { featureFlow: null });
    return;
  }

  // ── LEAVE ─────────────────────────────────────────────────────────────
  if (feature === "leave") {
    const pm = await startProgress(ctx, uid, `🚪 Leaving ${total} group(s)...\n${bar(0, total)}`);
    let done = 0, failed = 0, cancelled = false;
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      await editProgress(ctx.chat.id, pm.message_id,
        `🚪 Leaving groups...\n✅ ${done}  ❌ ${failed}\n→ ${g.name}\n${bar(i, total)}`);
      try {
        await removeAllMembers(0, g.id).catch(() => {});
        await sleep(1500);
        await withRetry(() => leaveGroup(0, g.id));
        done++;
      } catch { failed++; }
      await sleep(D.leave);
    }
    await sendSummary(ctx, { feature: "leave", total, success: done, failed, cancelled });
    updateSession(uid, { featureFlow: null });
    return;
  }

  // ── REMOVE MEMBERS ────────────────────────────────────────────────────
  if (feature === "remove_members") {
    const pm = await startProgress(ctx, uid, `🧹 Removing members — ${total} group(s)...\n${bar(0, total)}`);
    let done = 0, failed = 0, totalRem = 0, cancelled = false;
    const groupDetails = [];
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      await editProgress(ctx.chat.id, pm.message_id,
        `🧹 Removing members...\nGroup: ${i + 1}/${total}  ❌ ${failed}\n→ ${g.name}\n${bar(i, total)}`);
      try {
        const count = await withRetry(() => removeAllMembers(0, g.id));
        totalRem += count; done++;
        groupDetails.push({ name: g.name, detail: `${count} members removed` });
      } catch (err) {
        failed++;
        groupDetails.push({ name: g.name, detail: `❌ ${err.message}` });
      }
      await sleep(D.removeMembers);
    }
    await sendSummary(ctx, {
      feature: "remove_members", total, success: done, failed, cancelled,
      extra: [`🧹 Total Removed: ${totalRem}`],
      groupDetails,
    });
    updateSession(uid, { featureFlow: null });
    return;
  }

  // ── MAKE ADMIN ────────────────────────────────────────────────────────
  if (feature === "make_admin") {
    const nums = extraNums || [];
    const pm = await startProgress(ctx, uid, `👑 Making admins — ${total} group(s)...\n${bar(0, total)}`);
    let done = 0, failed = 0, cancelled = false;
    const groupDetails = [];
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      await editProgress(ctx.chat.id, pm.message_id,
        `👑 Making admins...\n✅ ${done}  ❌ ${failed}\n→ ${g.name}\n${bar(i, total)}`);
      try {
        await withRetry(() => makeAdminByNumbers(0, g.id, nums));
        done++;
        groupDetails.push({ name: g.name, detail: `✅ Admin made` });
      } catch (err) {
        failed++;
        groupDetails.push({ name: g.name, detail: `❌ ${err.message}` });
      }
      await sleep(D.makeAdmin);
    }
    await sendSummary(ctx, {
      feature: "make_admin", total, success: done, failed, cancelled,
      extra: [`👑 Numbers: ${nums.join(", ")}`],
      groupDetails,
    });
    updateSession(uid, { featureFlow: null });
    return;
  }

  // ── DEMOTE ADMIN ──────────────────────────────────────────────────────
  if (feature === "demote_admin") {
    const nums = extraNums || [];
    const pm = await startProgress(ctx, uid, `⬇️ Demoting admins — ${total} group(s)...\n${bar(0, total)}`);
    let done = 0, failed = 0, cancelled = false;
    const groupDetails = [];
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      await editProgress(ctx.chat.id, pm.message_id,
        `⬇️ Demoting admins...\n✅ ${done}  ❌ ${failed}\n→ ${g.name}\n${bar(i, total)}`);
      try {
        await withRetry(() => demoteAdminInGroup(0, g.id, nums));
        done++;
        groupDetails.push({ name: g.name, detail: `✅ Demoted` });
      } catch (err) {
        failed++;
        groupDetails.push({ name: g.name, detail: `❌ ${err.message}` });
      }
      await sleep(D.demoteAdmin);
    }
    await sendSummary(ctx, {
      feature: "demote_admin", total, success: done, failed, cancelled,
      extra: [`⬇️ Numbers: ${nums.join(", ")}`],
      groupDetails,
    });
    updateSession(uid, { featureFlow: null });
    return;
  }

  // ── APPROVAL TOGGLE ───────────────────────────────────────────────────
  if (feature === "approval") {
    // Ask ON or OFF first
    updateSession(uid, { featureFlow: { ...getSession(uid).featureFlow, step: "approval_confirm", selectedIds, allGroups } });
    await updateMain(ctx,
      `🔀 *Approval Toggle*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n*${total} group(s) selected*\n\nSet approval to:`,
      { reply_markup: Markup.inlineKeyboard([
        [Markup.button.callback("✅ ON", "approval_on"), Markup.button.callback("❌ OFF", "approval_off")],
        [Markup.button.callback("🏠 Main Menu", "back_menu")],
      ]).reply_markup }
    );
    return;
  }

  // ── APPROVE PENDING ───────────────────────────────────────────────────
  if (feature === "approve_pending") {
    const pm = await startProgress(ctx, uid, `✅ Approving pending — ${total} group(s)...\n${bar(0, total)}`);
    let done = 0, failed = 0, totalApproved = 0, cancelled = false;
    const groupDetails = [];
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      await editProgress(ctx.chat.id, pm.message_id,
        `✅ Approving...\n✅ ${done}  ❌ ${failed}\n→ ${g.name}\n${bar(i, total)}`);
      try {
        const count = await withRetry(() => approveAllPending(0, g.id));
        totalApproved += (count || 0); done++;
        groupDetails.push({ name: g.name, detail: String(count || 0) });
      } catch (err) {
        failed++;
        groupDetails.push({ name: g.name, detail: `❌ ${err.message}` });
      }
      await sleep(D.approvePending);
    }
    await sendSummary(ctx, {
      feature: "approve_pending", total, success: done, failed, cancelled,
      extra: [`✅ Total Approved: ${totalApproved}`],
      groupDetails,
    });
    updateSession(uid, { featureFlow: null });
    return;
  }

  // ── RESET LINK ────────────────────────────────────────────────────────
  if (feature === "reset_link") {
    const pm = await startProgress(ctx, uid, `🔄 Resetting links — ${total} group(s)...\n${bar(0, total)}`);
    let done = 0, failed = 0, cancelled = false;
    const newLinks = [];
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      await editProgress(ctx.chat.id, pm.message_id,
        `🔄 Resetting links...\n✅ ${done}  ❌ ${failed}\n→ ${g.name}\n${bar(i, total)}`);
      try {
        await withRetry(() => resetGroupInviteLink(0, g.id));
        const newLink = await withRetry(() => getGroupInviteLink(0, g.id));
        newLinks.push({ name: g.name, link: newLink }); done++;
      } catch { failed++; }
      await sleep(D.resetLink);
    }
    await sendSummary(ctx, { feature: "reset_link", total, success: done, failed, cancelled, links: newLinks });
    updateSession(uid, { featureFlow: null });
    return;
  }

  // ── MEMBER LIST ───────────────────────────────────────────────────────
  if (feature === "member_list") {
    const pm = await startProgress(ctx, uid, `📋 Getting member list — ${total} group(s)...\n${bar(0, total)}`);
    let cancelled = false;
    const groupDetails = [];
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      await editProgress(ctx.chat.id, pm.message_id,
        `📋 Fetching members...\nGroup ${i + 1}/${total}\n→ ${g.name}\n${bar(i, total)}`);
      try {
        const members = await withRetry(() => getGroupMembers(0, g.id));
        groupDetails.push({ name: g.name, detail: `${members.length} member` });
      } catch {
        groupDetails.push({ name: g.name, detail: `❌ Error` });
      }
      await sleep(D.memberList);
    }
    await sendSummary(ctx, { feature: "member_list", total, success: total, failed: 0, cancelled, groupDetails });
    updateSession(uid, { featureFlow: null });
    return;
  }

  // ── PENDING LIST ──────────────────────────────────────────────────────
  if (feature === "pending_list") {
    const pm = await startProgress(ctx, uid, `⏳ Getting pending list — ${total} group(s)...\n${bar(0, total)}`);
    let cancelled = false;
    const groupDetails = [];
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      await editProgress(ctx.chat.id, pm.message_id,
        `⏳ Fetching pending...\nGroup ${i + 1}/${total}\n→ ${g.name}\n${bar(i, total)}`);
      try {
        const result = await withRetry(() => getGroupPendingRequests(0, g.id));
        const count  = result?.list?.length ?? 0;
        groupDetails.push({ name: g.name, detail: String(count) });
      } catch {
        groupDetails.push({ name: g.name, detail: `0` });
      }
      await sleep(D.pendingList);
    }
    await sendSummary(ctx, { feature: "pending_list", total, success: total, failed: 0, cancelled, groupDetails });
    updateSession(uid, { featureFlow: null });
    return;
  }

  // ── CTC CHECKER ───────────────────────────────────────────────────────
  if (feature === "ctc_checker") {
    const vcfContent = getSession(uid).featureFlow?.vcfContent;
    if (!vcfContent) {
      await updateMain(ctx, "❌ No VCF file received.", { reply_markup: BACK_MENU_BTN.reply_markup });
      return;
    }
    const contacts = parseVcf(vcfContent);
    const vcfPhones = new Set(contacts.map(c => c.phone));
    const pm = await startProgress(ctx, uid, `🔍 CTC Check — ${total} group(s)...\n${bar(0, total)}`);
    let cancelled = false;
    const groupDetails = [];
    for (let i = 0; i < total; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      const g = sel[i];
      await editProgress(ctx.chat.id, pm.message_id,
        `🔍 Checking...\nGroup ${i + 1}/${total}\n→ ${g.name}\n${bar(i, total)}`);
      try {
        const pending = await withRetry(() => getGroupPendingRequests(0, g.id));
        const list = pending?.list || [];
        let valid = 0, invalid = 0;
        for (const p of list) {
          const phone = p.phone || p.number || "";
          let found = false;
          for (const vp of vcfPhones) {
            const s = vp.replace(/\D/g, ""), i2 = phone.replace(/\D/g, "");
            if (s === i2 || (i2.length >= 8 && s.endsWith(i2)) || (s.length >= 8 && i2.endsWith(s))) {
              found = true; break;
            }
          }
          found ? valid++ : invalid++;
        }
        groupDetails.push({ name: g.name, detail: `valid ${valid} member, ${invalid} wrong contact number` });
      } catch (err) {
        groupDetails.push({ name: g.name, detail: `❌ ${err.message}` });
      }
      await sleep(D.ctcCheck);
    }
    await sendSummary(ctx, { feature: "ctc_checker", total, success: total, failed: 0, cancelled, groupDetails });
    updateSession(uid, { featureFlow: null, awaitingVcf: null });
    return;
  }
}

// ─── Approval ON/OFF handlers ─────────────────────────────────────────────
async function runApproval(ctx, mode) {
  const uid  = ctx.from.id;
  const flow = getSession(uid).featureFlow;
  const sel  = (flow.allGroups || []).filter(g => flow.selectedIds.includes(g.id));
  const total = sel.length;
  updateSession(uid, { cancelPending: false });
  const pm = await startProgress(ctx, uid, `🔀 Setting approval ${mode}...\n${bar(0, total)}`);
  let done = 0, failed = 0, cancelled = false;
  for (let i = 0; i < total; i++) {
    if (isCancelled(uid)) { cancelled = true; break; }
    const g = sel[i];
    await editProgress(ctx.chat.id, pm.message_id,
      `🔀 Setting approval ${mode}...\n✅ ${done}  ❌ ${failed}\n→ ${g.name}\n${bar(i, total)}`);
    try {
      await withRetry(() => setGroupApproval(0, g.id, mode === "on"));
      done++;
    } catch { failed++; }
    await sleep(D.approvalToggle);
  }
  await sendSummary(ctx, { feature: "approval", total, success: done, failed, cancelled });
  updateSession(uid, { featureFlow: null });
}

bot.action("approval_on",  async (ctx) => { await ctx.answerCbQuery("Setting ON..."); await runApproval(ctx, "on"); });
bot.action("approval_off", async (ctx) => { await ctx.answerCbQuery("Setting OFF..."); await runApproval(ctx, "off"); });

// ══════════════════════════════════════════════════════════════════════════
// ─── CREATE GROUPS ────────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

bot.action("create_groups_start", async (ctx) => {
  await ctx.answerCbQuery();
  const uid = ctx.from.id;
  updateSession(uid, {
    mainMsgId: ctx.callbackQuery.message?.message_id || getSession(uid).mainMsgId,
    groupFlow: defaultGroupFlow(),
  });
  await updateMain(ctx,
    `➕ *Create Groups*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n\nEnter group name:`,
    { reply_markup: BACK_MENU_BTN.reply_markup }
  );
});

async function askCount(ctx) {
  const flow = getSession(ctx.from.id).groupFlow;
  await updateMain(ctx,
    `➕ *Create Groups*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n*Name:* ${flow.name}\nHow many groups?`,
    { reply_markup: Markup.inlineKeyboard([
      [Markup.button.callback("1", "gf_count_1"), Markup.button.callback("5", "gf_count_5"), Markup.button.callback("10", "gf_count_10")],
      [Markup.button.callback("20", "gf_count_20"), Markup.button.callback("50", "gf_count_50"), Markup.button.callback("✏️ Custom", "gf_count_custom")],
      [Markup.button.callback("🏠 Main Menu", "back_menu")],
    ]).reply_markup }
  );
}

async function askNumbering(ctx) {
  const flow = getSession(ctx.from.id).groupFlow;
  await updateMain(ctx,
    `➕ *Create Groups*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n*Name:* ${flow.name}  *Count:* ${flow.count}\n\nAdd numbering? (e.g. ${flow.name} 1, ${flow.name} 2...)`,
    { reply_markup: Markup.inlineKeyboard([
      [Markup.button.callback("✅ Yes (numbered)", "gf_num_yes"), Markup.button.callback("❌ No (same name)", "gf_num_no")],
      [Markup.button.callback("🏠 Main Menu", "back_menu")],
    ]).reply_markup }
  );
}

async function askPermissions(ctx) {
  const flow = getSession(ctx.from.id).groupFlow;
  const p = flow.permissions;
  const fmt = (v) => v ? "✅" : "❌";
  await updateMain(ctx,
    `⚙️ *Permissions*\n▰▰▰▰▰▰▰▰▰▰▰▰▰`,
    { reply_markup: Markup.inlineKeyboard([
      [Markup.button.callback(`💬 All Send : ${fmt(p.sendMessages)}`,     "gp_sendMessages")],
      [Markup.button.callback(`✏️ Edit Info : ${fmt(p.editInfo)}`,         "gp_editInfo")],
      [Markup.button.callback(`➕ Add Members : ${fmt(p.addMembers)}`,     "gp_addMembers")],
      [Markup.button.callback(`🔐 Approval : ${fmt(p.approveMembers)}`,   "gp_approveMembers")],
      [Markup.button.callback("💾 Save", "gp_save")],
      [Markup.button.callback("🏠 Main Menu", "back_menu")],
    ]).reply_markup }
  );
}

["sendMessages", "editInfo", "addMembers", "approveMembers"].forEach((k) => {
  bot.action(`gp_${k}`, async (ctx) => {
    await ctx.answerCbQuery();
    const flow = getSession(ctx.from.id).groupFlow;
    const p = { ...flow.permissions, [k]: !flow.permissions[k] };
    updateSession(ctx.from.id, { groupFlow: { ...flow, permissions: p } });
    await askPermissions(ctx);
  });
});

bot.action("gp_save", async (ctx) => {
  await ctx.answerCbQuery();
  updateSession(ctx.from.id, { groupFlow: { ...getSession(ctx.from.id).groupFlow, step: "confirm" } });
  await showConfirm(ctx);
});

async function showConfirm(ctx) {
  const flow = getSession(ctx.from.id).groupFlow;
  const p = flow.permissions;
  const dis = flow.disappearing === 86400 ? "24h" : flow.disappearing === 604800 ? "7d" : flow.disappearing === 7776000 ? "90d" : "Off";
  await updateMain(ctx,
    `➕ *Create Groups — Confirm*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n` +
    `Name     : *${flow.name}*\n` +
    `Count    : *${flow.count}*\n` +
    `Numbered : *${flow.numbering ? "Yes" : "No"}*\n` +
    `Members  : *${flow.members.length}*\n` +
    `Admin    : *${flow.makeAdmin ? "Yes" : "No"}*\n` +
    `Disappear: *${dis}*\n` +
    `Photo    : *${flow.photo ? "Yes" : "No"}*\n` +
    `Perms    : Send:${p.sendMessages ? "✅" : "❌"} EditInfo:${p.editInfo ? "✅" : "❌"} Add:${p.addMembers ? "✅" : "❌"} Approval:${p.approveMembers ? "✅" : "❌"}`,
    { reply_markup: Markup.inlineKeyboard([
      [Markup.button.callback("🚀 Create Now", "gf_create_now")],
      [Markup.button.callback("✏️ Name",        "ge_name"),       Markup.button.callback("🔢 Count",       "ge_count")],
      [Markup.button.callback("👥 Members",     "ge_members"),    Markup.button.callback("🖼 Photo",       "ge_photo")],
      [Markup.button.callback("⏳ Disappearing","ge_disappearing"),Markup.button.callback("⚙️ Permissions","ge_perms")],
      [Markup.button.callback("🏠 Main Menu", "back_menu")],
    ]).reply_markup }
  );
}

[1, 5, 10, 20, 50].forEach((n) => {
  bot.action(`gf_count_${n}`, async (ctx) => {
    await ctx.answerCbQuery();
    updateSession(ctx.from.id, { groupFlow: { ...getSession(ctx.from.id).groupFlow, count: n, step: "numbering" } });
    await askNumbering(ctx);
  });
});

bot.action("gf_count_custom", async (ctx) => {
  await ctx.answerCbQuery();
  updateSession(ctx.from.id, { groupFlow: { ...getSession(ctx.from.id).groupFlow, step: "count_custom" } });
  await updateMain(ctx, `➕ *Custom Count*\nType number of groups to create:`, { reply_markup: BACK_MENU_BTN.reply_markup });
});

bot.action("gf_num_yes", async (ctx) => {
  await ctx.answerCbQuery();
  updateSession(ctx.from.id, { groupFlow: { ...getSession(ctx.from.id).groupFlow, numbering: true, step: "confirm" } });
  await showConfirm(ctx);
});
bot.action("gf_num_no", async (ctx) => {
  await ctx.answerCbQuery();
  updateSession(ctx.from.id, { groupFlow: { ...getSession(ctx.from.id).groupFlow, numbering: false, step: "confirm" } });
  await showConfirm(ctx);
});
bot.action("gf_back_confirm", async (ctx) => { await ctx.answerCbQuery(); await showConfirm(ctx); });

// ─── Confirm group creation ────────────────────────────────────────────────
bot.action("gf_create_now", async (ctx) => {
  await ctx.answerCbQuery("Starting...");
  const uid  = ctx.from.id;
  const flow = getSession(uid).groupFlow;
  if (!flow?.name || !flow?.count) {
    await updateMain(ctx, "⚠️ Settings incomplete.", { reply_markup: BACK_MENU_BTN.reply_markup });
    return;
  }
  if (getStatus(0) !== "connected") {
    await updateMain(ctx, "❌ WhatsApp not connected!", { reply_markup: Markup.inlineKeyboard([[Markup.button.callback("📱 Connect", "menu_account")]]).reply_markup });
    return;
  }
  const jids = flow.members.map((n) => `${n.replace(/[^0-9]/g, "")}@s.whatsapp.net`);
  updateSession(uid, { cancelPending: false });
  const pm = await startProgress(ctx, uid, `🚀 Creating ${flow.count} group(s)...\n${bar(0, flow.count)}`);
  const created = [], failed = [];
  let cancelled = false;

  for (let i = 0; i < flow.count; i++) {
    if (isCancelled(uid)) { cancelled = true; break; }
    const gname = flow.numbering ? `${flow.name} ${i + 1}` : flow.name;
    await editProgress(ctx.chat.id, pm.message_id,
      `🚀 Creating groups...\nDone: ${i}/${flow.count}\n→ ${gname}\n${bar(i, flow.count)}`);
    try {
      const r = await withRetry(() => createGroup(0, gname, jids));
      const gid = r.id;
      await sleep(3000);
      if (flow.description) { await updateGroupDescription(0, gid, flow.description).catch(() => {}); await sleep(600); }
      if (flow.photo) {
        const photoBuf = Buffer.isBuffer(flow.photo) ? flow.photo
          : (flow.photo?.data ? Buffer.from(flow.photo.data) : Buffer.from(Object.values(flow.photo)));
        await withRetry(() => updateGroupPhoto(0, gid, photoBuf), 3, 3000).catch(() => {});
        await sleep(800);
      }
      if (flow.disappearing) { await setDisappearingMessages(0, gid, flow.disappearing).catch(() => {}); await sleep(500); }
      if (flow.makeAdmin && jids.length) { await makeAdminByNumbers(0, gid, flow.members).catch(() => {}); await sleep(1000); }
      await setGroupPermissions(0, gid, flow.permissions).catch(() => {});
      let link = "";
      try { link = await getGroupInviteLink(0, gid); } catch { link = "(unavailable)"; }
      created.push({ name: gname, link });
    } catch { failed.push(gname); }
    await sleep(D.createGroup);
  }

  await sendSummary(ctx, {
    feature: "create_groups",
    total: flow.count,
    success: created.length,
    failed: failed.length,
    cancelled,
    links: created,
    extra: failed.length ? [`❌ Failed: ${failed.join(", ")}`] : [],
  });
  updateSession(uid, { groupFlow: null });
});

// ─── Edit group flow fields ────────────────────────────────────────────────
bot.action("ge_name", async (ctx) => {
  await ctx.answerCbQuery();
  updateSession(ctx.from.id, { groupFlow: { ...getSession(ctx.from.id).groupFlow, step: "name_edit" } });
  await updateMain(ctx, `✏️ *New group name:*`, { reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🔙 Back", "gf_back_confirm")]]).reply_markup });
});
bot.action("ge_count", async (ctx) => {
  await ctx.answerCbQuery();
  updateSession(ctx.from.id, { groupFlow: { ...getSession(ctx.from.id).groupFlow, step: "count_edit" } });
  await askCount(ctx);
});
bot.action("ge_photo", async (ctx) => {
  await ctx.answerCbQuery();
  updateSession(ctx.from.id, { groupFlow: { ...getSession(ctx.from.id).groupFlow, step: "photo_edit" } });
  await updateMain(ctx, `🖼 *Send new group photo* (image):`, {
    reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🗑 Remove Photo", "ge_photo_rm")], [Markup.button.callback("🔙 Back", "gf_back_confirm")]]).reply_markup });
});
bot.action("ge_photo_rm", async (ctx) => {
  await ctx.answerCbQuery();
  updateSession(ctx.from.id, { groupFlow: { ...getSession(ctx.from.id).groupFlow, photo: null, step: "confirm" } });
  await showConfirm(ctx);
});
bot.action("ge_disappearing", async (ctx) => {
  await ctx.answerCbQuery();
  updateSession(ctx.from.id, { groupFlow: { ...getSession(ctx.from.id).groupFlow, step: "disappearing_edit" } });
  await updateMain(ctx, `⏳ *Disappearing Messages:*`, {
    reply_markup: Markup.inlineKeyboard([
      [Markup.button.callback("24h", "ge_dis_86400"), Markup.button.callback("7d", "ge_dis_604800"), Markup.button.callback("90d", "ge_dis_7776000")],
      [Markup.button.callback("⏭ Off", "ge_dis_0")],
      [Markup.button.callback("🔙 Back", "gf_back_confirm")],
    ]).reply_markup });
});
[0, 86400, 604800, 7776000].forEach((s) => {
  bot.action(`ge_dis_${s}`, async (ctx) => {
    await ctx.answerCbQuery();
    updateSession(ctx.from.id, { groupFlow: { ...getSession(ctx.from.id).groupFlow, disappearing: s, step: "confirm" } });
    await showConfirm(ctx);
  });
});
bot.action("ge_members", async (ctx) => {
  await ctx.answerCbQuery();
  updateSession(ctx.from.id, { groupFlow: { ...getSession(ctx.from.id).groupFlow, step: "members_edit" } });
  await updateMain(ctx, `👥 *New member numbers (one per line):*`, {
    reply_markup: Markup.inlineKeyboard([[Markup.button.callback("⏭ Remove All", "ge_mem_rm")], [Markup.button.callback("🔙 Back", "gf_back_confirm")]]).reply_markup });
});
bot.action("ge_mem_rm", async (ctx) => {
  await ctx.answerCbQuery();
  updateSession(ctx.from.id, { groupFlow: { ...getSession(ctx.from.id).groupFlow, members: [], makeAdmin: false, step: "confirm" } });
  await showConfirm(ctx);
});
bot.action("ge_perms", async (ctx) => { await ctx.answerCbQuery(); await askPermissions(ctx); });

// ══════════════════════════════════════════════════════════════════════════
// ─── JOIN GROUPS ──────────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

bot.action("join_groups_start", async (ctx) => {
  await ctx.answerCbQuery();
  const uid = ctx.from.id;
  updateSession(uid, {
    mainMsgId: ctx.callbackQuery.message?.message_id || getSession(uid).mainMsgId,
    joinFlow: { step: "links" },
  });
  await updateMain(ctx,
    `🔗 *Join Groups*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n\nSend invite links (one per line):\n_Example:_ \`https://chat.whatsapp.com/ABC123\``,
    { reply_markup: BACK_MENU_BTN.reply_markup }
  );
});

// ══════════════════════════════════════════════════════════════════════════
// ─── ADD MEMBERS VCF FLOW ─────────────────────────────────────────────────
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
  const code  = flow.links[idx];
  updateSession(ctx.from.id, { awaitingVcf: { feature: "add_members", step: "am_vcf", linkIdx: idx } });
  await updateMain(ctx,
    `➕ *Add Members — VCF ${idx + 1}/${total}*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n\nSend VCF for group ${idx + 1}:\n\`https://chat.whatsapp.com/${code}\``,
    { reply_markup: Markup.inlineKeyboard([[Markup.button.callback("⏭ Skip This Group", "am_skip_vcf")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]).reply_markup }
  );
}

bot.action("am_skip_vcf", async (ctx) => {
  await ctx.answerCbQuery("Skipped");
  const uid  = ctx.from.id, flow = getSession(uid).featureFlow;
  const newVcfs = [...(flow.vcfs || [])];
  newVcfs[flow.currentVcfIdx || 0] = null;
  updateSession(uid, { featureFlow: { ...flow, currentVcfIdx: (flow.currentVcfIdx || 0) + 1, vcfs: newVcfs }, awaitingVcf: null });
  await askNextVcf(ctx);
});

async function runAddMembersFromVcfs(ctx) {
  const uid  = ctx.from.id;
  const flow = getSession(uid).featureFlow;
  const links = flow.links || [], vcfs = flow.vcfs || [], total = links.length;
  updateSession(uid, { cancelPending: false, awaitingVcf: null });
  const pm = await startProgress(ctx, uid, `➕ Adding members — ${total} group(s)...\n${bar(0, total)}`);
  let doneGroups = 0, failedGroups = 0, totAdded = 0, totFailed = 0, cancelled = false;
  const groupDetails = [];
  for (let i = 0; i < total; i++) {
    if (isCancelled(uid)) { cancelled = true; break; }
    const vcfEntry = vcfs[i];
    if (!vcfEntry) { doneGroups++; groupDetails.push({ name: `Group ${i + 1}`, detail: "⏭ skipped" }); continue; }
    const contacts = Array.isArray(vcfEntry) ? vcfEntry : (vcfEntry.contacts || []);
    await editProgress(ctx.chat.id, pm.message_id,
      `➕ Adding members...\nGroup: ${i + 1}/${total}  Added: ${totAdded}\n${bar(i, total)}`);
    try {
      const info = await withRetry(() => getGroupInfoFromLink(0, links[i]));
      if (!info) throw new Error("Invalid link");
      const result = await addMembersToGroup(0, info.id, contacts.map(c => c.phone), flow.addMode === "onebyone");
      totAdded += result.added; totFailed += result.failed;
      doneGroups++;
      groupDetails.push({ name: info.name, detail: `+${result.added} added, ${result.failed} failed` });
    } catch (err) {
      failedGroups++;
      groupDetails.push({ name: `Group ${i + 1}`, detail: `❌ ${err.message}` });
    }
    await sleep(D.addMembers);
  }
  await sendSummary(ctx, {
    feature: "add_members", total, success: doneGroups, failed: failedGroups, cancelled,
    extra: [`➕ Total Added: ${totAdded}  ❌ Failed: ${totFailed}`],
    groupDetails,
  });
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

  // ── WA Phone Input ────────────────────────────────────────────────────
  if (s.awaitingPhoneForIndex !== null && s.awaitingPhoneForIndex !== undefined) {
    const phone = text.replace(/[^0-9]/g, "");
    if (phone.length < 10) {
      await updateMain(ctx,
        `❌ Invalid number format.\nExample: \`919876543210\`\n\nTry again:`,
        { reply_markup: BACK_MENU_BTN.reply_markup }
      );
      return;
    }
    updateSession(uid, { awaitingPhoneForIndex: null });
    await updateMain(ctx, `⏳ *Generating pairing code...*`);

    pendingPairingCbs.set(0, async (code) => {
      if (!code) {
        await updateMain(ctx,
          `❌ *Failed to generate code. Try again.*`,
          { reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🔄 Try Again", "menu_account")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]).reply_markup }
        );
        return;
      }
      await updateMain(ctx,
        `🔑 *Pairing Code*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n\n\`${code}\`\n\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n*How to link:*\n1. Open WhatsApp\n2. Settings → Linked Devices\n3. Link a Device → Phone number\n4. Enter the code above\n\n⚠️ Expires in *60 seconds*!\n⏳ Waiting...`,
        { reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🔄 New Code", "reset_wa")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]).reply_markup }
      );
    });

    pendingReadyCbs.set(0, async () => { await sendMainMenu(ctx); });

    connectAccount(0, phone).catch(async (err) => {
      pendingPairingCbs.delete(0); pendingReadyCbs.delete(0);
      await updateMain(ctx, `❌ Error: \`${err.message}\``, { reply_markup: BACK_MENU_BTN.reply_markup });
    });
    return;
  }

  // ── Join Groups Links ─────────────────────────────────────────────────
  if (s.joinFlow?.step === "links") {
    const codes = extractCodes(text);
    if (!codes.length) {
      await updateMain(ctx, `❌ No valid WhatsApp links found.\n\nSend links like:\nhttps://chat.whatsapp.com/ABC123`, { reply_markup: BACK_MENU_BTN.reply_markup });
      return;
    }
    updateSession(uid, { joinFlow: null });
    const pm = await startProgress(ctx, uid, `🔗 Joining ${codes.length} group(s)...\n${bar(0, codes.length)}`);
    let joined = 0, failed = 0, cancelled = false;
    for (let i = 0; i < codes.length; i++) {
      if (isCancelled(uid)) { cancelled = true; break; }
      await editProgress(ctx.chat.id, pm.message_id,
        `🔗 Joining groups...\n✅ ${joined}  ❌ ${failed}\nGroup ${i + 1}/${codes.length}\n${bar(i, codes.length)}`);
      try { await withRetry(() => joinGroupViaLink(0, codes[i])); joined++; }
      catch { failed++; }
      await sleep(D.joinGroup);
    }
    await sendSummary(ctx, { feature: "join_groups", total: codes.length, success: joined, failed, cancelled });
    return;
  }

  // ── Similar keyword ────────────────────────────────────────────────────
  if (s.featureFlow?.step === "similar_query") {
    const kw = text.toLowerCase();
    try {
      const allGroups = s.featureFlow.allGroups?.length ? s.featureFlow.allGroups : await getAllGroupsWithDetails(0);
      const filtered  = allGroups.filter((g) => g.name.toLowerCase().includes(kw));
      if (!filtered.length) {
        await updateMain(ctx, `❌ No groups match *"${text}"*.\n\nType a different keyword:`, { reply_markup: BACK_MENU_BTN.reply_markup });
        return;
      }
      updateSession(uid, { featureFlow: { ...s.featureFlow, allGroups, selectedIds: filtered.map(g => g.id), keyword: kw, step: "confirm" } });
      await updateMain(ctx,
        `✅ *${filtered.length} group(s) matched:*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n${filtered.slice(0, 15).map((g, i) => `${i + 1}. ${g.name}`).join("\n")}${filtered.length > 15 ? `\n_...and ${filtered.length - 15} more_` : ""}`,
        { reply_markup: Markup.inlineKeyboard([[Markup.button.callback("🚀 Proceed", "gs_sim_proceed")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]).reply_markup }
      );
    } catch (err) {
      await updateMain(ctx, `❌ Error: ${err.message}`, { reply_markup: BACK_MENU_BTN.reply_markup });
    }
    return;
  }

  // ── Make Admin numbers ────────────────────────────────────────────────
  if (s.featureFlow?.step === "admin_numbers") {
    const nums = text.split(/[\n,\s]+/).map(n => n.replace(/[^0-9]/g, "")).filter(n => n.length >= 10);
    if (!nums.length) {
      await updateMain(ctx, `❌ No valid numbers. Try again:`, { reply_markup: BACK_MENU_BTN.reply_markup });
      return;
    }
    const flow = s.featureFlow;
    updateSession(uid, { featureFlow: { ...flow, adminNumbers: nums } });
    await runFeature(ctx, "make_admin", flow.selectedIds, flow.allGroups, nums);
    return;
  }

  // ── Demote Admin numbers ──────────────────────────────────────────────
  if (s.featureFlow?.step === "demote_numbers") {
    const nums = text.split(/[\n,\s]+/).map(n => n.replace(/[^0-9]/g, "")).filter(n => n.length >= 10);
    if (!nums.length) {
      await updateMain(ctx, `❌ No valid numbers. Try again:`, { reply_markup: BACK_MENU_BTN.reply_markup });
      return;
    }
    const flow = s.featureFlow;
    updateSession(uid, { featureFlow: { ...flow, adminNumbers: nums } });
    await runFeature(ctx, "demote_admin", flow.selectedIds, flow.allGroups, nums);
    return;
  }

  // ── Group flow name ───────────────────────────────────────────────────
  if (s.groupFlow?.step === "name" || s.groupFlow?.step === "name_edit") {
    updateSession(uid, { groupFlow: { ...s.groupFlow, name: text, step: s.groupFlow.step === "name_edit" ? "confirm" : "count" } });
    if (s.groupFlow.step === "name_edit") { await showConfirm(ctx); return; }
    await askCount(ctx);
    return;
  }

  // ── Group flow custom count ───────────────────────────────────────────
  if (s.groupFlow?.step === "count_custom" || s.groupFlow?.step === "count_edit") {
    const n = parseInt(text);
    if (!n || n < 1 || n > 200) {
      await updateMain(ctx, `❌ Enter a number between 1 and 200:`, { reply_markup: BACK_MENU_BTN.reply_markup });
      return;
    }
    updateSession(uid, { groupFlow: { ...s.groupFlow, count: n, step: s.groupFlow.step === "count_edit" ? "confirm" : "numbering" } });
    if (s.groupFlow.step === "count_edit") { await showConfirm(ctx); return; }
    await askNumbering(ctx);
    return;
  }

  // ── Group flow members edit ───────────────────────────────────────────
  if (s.groupFlow?.step === "members_edit") {
    const nums = text.split(/[\n,\s]+/).map(n => n.replace(/[^0-9]/g, "")).filter(n => n.length >= 10);
    updateSession(uid, { groupFlow: { ...s.groupFlow, members: nums, makeAdmin: nums.length > 0, step: "confirm" } });
    await showConfirm(ctx);
    return;
  }

  // ── AA custom duration ────────────────────────────────────────────────
  if (s.featureFlow?.step === "aa_custom_duration") {
    const mins = parseInt(text);
    if (!mins || mins < 1) {
      await updateMain(ctx, `❌ Invalid. Type minutes (e.g. 30):`, { reply_markup: BACK_MENU_BTN.reply_markup });
      return;
    }
    const flow = s.featureFlow;
    updateSession(uid, { featureFlow: { ...flow, aaDuration: mins * 60, step: "aa_confirm" } });
    const label = mins >= 60 ? `${mins / 60}h` : `${mins}min`;
    await updateMain(ctx,
      `⏰ *Auto Accept — Confirm*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\nGroups   : *${flow.selectedIds.length}*\nDuration : *${label}*`,
      { reply_markup: Markup.inlineKeyboard([[Markup.button.callback("▶️ Start", "aa_start")], [Markup.button.callback("🏠 Main Menu", "back_menu")]]).reply_markup }
    );
    return;
  }

  // ── Change Name — custom base ─────────────────────────────────────────
  if (s.featureFlow?.step === "cn_random_name") {
    const flow = s.featureFlow;
    updateSession(uid, { featureFlow: { ...flow, cnBaseName: text, step: "cn_numbering" } });
    await updateMain(ctx,
      `✏️ *Change Name*\nBase name: *${text}*\n\nAdd numbering?\n_${text} 1, ${text} 2..._`,
      { reply_markup: Markup.inlineKeyboard([
        [Markup.button.callback("✅ Yes (numbered)", "cn_numbering_yes"), Markup.button.callback("❌ No (same name)", "cn_numbering_no")],
        [Markup.button.callback("🏠 Main Menu", "back_menu")],
      ]).reply_markup }
    );
    return;
  }

  // ── Change Name — invite links ────────────────────────────────────────
  if (s.featureFlow?.step === "cn_random_links") {
    const codes = extractCodes(text);
    if (!codes.length) {
      await updateMain(ctx, `❌ No valid links. Send invite links (one per line):`, { reply_markup: BACK_MENU_BTN.reply_markup });
      return;
    }
    const flow = s.featureFlow;
    await runChangeNameRandom(ctx, codes, flow.cnBaseName, flow.numbering);
    return;
  }

  // ── Add Members — invite links ────────────────────────────────────────
  if (s.featureFlow?.step === "am_links") {
    const codes = extractCodes(text);
    if (!codes.length) {
      await updateMain(ctx, `❌ No valid links found. Send invite links:`, { reply_markup: BACK_MENU_BTN.reply_markup });
      return;
    }
    const flow = s.featureFlow;
    updateSession(uid, { featureFlow: { ...flow, links: codes, vcfs: [], currentVcfIdx: 0, step: "am_mode" } });
    await updateMain(ctx,
      `➕ *Add Members*\n▰▰▰▰▰▰▰▰▰▰▰▰▰\n*${codes.length} groups*\n\nAdd mode:`,
      { reply_markup: Markup.inlineKeyboard([
        [Markup.button.callback("1️⃣ One by one", "am_mode_onebyone")],
        [Markup.button.callback("📦 Bulk add",    "am_mode_bulk")],
        [Markup.button.callback("🏠 Main Menu", "back_menu")],
      ]).reply_markup }
    );
    return;
  }
});

// ══════════════════════════════════════════════════════════════════════════
// ─── DOCUMENT/FILE HANDLER (VCF + Photos) ─────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

bot.on(["document", "photo"], async (ctx) => {
  const uid = ctx.from.id, s = getSession(uid);
  const isPhoto = !!ctx.message.photo;

  // Group photo upload
  if (s.groupFlow?.step === "photo_edit" && isPhoto) {
    const photos = ctx.message.photo;
    const best   = photos[photos.length - 1];
    try {
      const buf = await downloadFile(ctx, best.file_id);
      updateSession(uid, { groupFlow: { ...s.groupFlow, photo: buf, step: "confirm" } });
      await showConfirm(ctx);
    } catch {
      await updateMain(ctx, `❌ Failed to download photo. Try again.`, { reply_markup: BACK_MENU_BTN.reply_markup });
    }
    return;
  }

  if (!ctx.message.document) return;
  const doc = ctx.message.document;
  const name = (doc.file_name || "").toLowerCase();

  // Only handle VCF files
  if (!name.endsWith(".vcf") && doc.mime_type !== "text/vcard" && doc.mime_type !== "text/x-vcard") return;

  // ── Change Name VCF files ─────────────────────────────────────────────
  if (s.awaitingVcf?.feature === "change_name" && s.awaitingVcf?.step === "cn_vcf_files") {
    try {
      const buf     = await downloadFile(ctx, doc.file_id);
      const content = buf.toString("utf8");
      const flow    = s.featureFlow;
      const vcfFiles = [...(flow.vcfFiles || []), content];
      updateSession(uid, { featureFlow: { ...flow, vcfFiles } });
      await updateMain(ctx,
        `✏️ *VCF received* (${vcfFiles.length} file${vcfFiles.length > 1 ? "s" : ""})\n\nSend more VCFs or tap Done:`,
        { reply_markup: Markup.inlineKeyboard([
          [Markup.button.callback("▶️ Done — Run Match", "cn_run_vcf")],
          [Markup.button.callback("🏠 Main Menu", "back_menu")],
        ]).reply_markup }
      );
    } catch {
      await updateMain(ctx, `❌ Failed to read VCF. Try again.`, { reply_markup: BACK_MENU_BTN.reply_markup });
    }
    return;
  }

  // ── CTC Checker VCF ───────────────────────────────────────────────────
  if (s.awaitingVcf?.feature === "ctc_checker") {
    try {
      const buf  = await downloadFile(ctx, doc.file_id);
      const text = buf.toString("utf8");
      const flow = s.featureFlow;
      updateSession(uid, { featureFlow: { ...flow, vcfContent: text }, awaitingVcf: null });
      await runFeature(ctx, "ctc_checker", flow.selectedIds, flow.allGroups, []);
    } catch {
      await updateMain(ctx, `❌ Failed to read VCF. Try again.`, { reply_markup: BACK_MENU_BTN.reply_markup });
    }
    return;
  }

  // ── Add Members VCF ───────────────────────────────────────────────────
  if (s.awaitingVcf?.feature === "add_members" && s.awaitingVcf?.step === "am_vcf") {
    try {
      const buf      = await downloadFile(ctx, doc.file_id);
      const content  = buf.toString("utf8");
      const contacts = parseVcf(content);
      const flow     = s.featureFlow;
      const idx      = s.awaitingVcf.linkIdx;
      const newVcfs  = [...(flow.vcfs || [])];
      newVcfs[idx]   = contacts;
      const nextIdx  = idx + 1;
      updateSession(uid, { featureFlow: { ...flow, vcfs: newVcfs, currentVcfIdx: nextIdx }, awaitingVcf: null });
      await askNextVcf(ctx);
    } catch {
      await updateMain(ctx, `❌ Failed to read VCF. Try again.`, { reply_markup: BACK_MENU_BTN.reply_markup });
    }
    return;
  }
});

// ══════════════════════════════════════════════════════════════════════════
// ─── EXPRESS KEEPALIVE ────────────────────────────────────────────────────
// ══════════════════════════════════════════════════════════════════════════

const app = express();
app.get("/", (req, res) => res.send("WS Automation Bot — Running ✅"));
app.get("/health", (req, res) => res.json({ status: "ok", wa: getStatus(0) }));

// ─── Self-ping to prevent Render sleep ────────────────────────────────────
function startSelfPing() {
  const url = process.env.RENDER_EXTERNAL_URL;
  if (!url) return;
  setInterval(() => {
    const opts = url.startsWith("https") ? https : http;
    opts.get(url, () => {}).on("error", () => {});
  }, 14 * 60 * 1000);
}

// ─── Main bootstrap ────────────────────────────────────────────────────────
async function main() {
  await connectDB();
  await reconnectSavedAccounts();

  const PORT = process.env.PORT || 3000;
  app.listen(PORT, () => console.log(`[Server] Listening on port ${PORT}`));
  startSelfPing();

  bot.launch();
  console.log("🤖 WS Automation Bot started!");

  process.once("SIGINT",  () => bot.stop("SIGINT"));
  process.once("SIGTERM", () => bot.stop("SIGTERM"));
}

main().catch(console.error);
