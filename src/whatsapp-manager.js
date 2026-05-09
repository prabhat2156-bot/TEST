/**
 * WhatsApp Manager — v3.0 FREEZE-FIXED
 *
 * ROOT CAUSE FIXES:
 * 1. withTimeout() — wraps EVERY Baileys socket call so nothing hangs forever
 * 2. jidToPhone() — correctly strips device suffix ":5" from JIDs like "91xxx:5@s.whatsapp.net"
 * 3. phoneMatch() — robust matching: exact, endsWith, startsWith across country code variants
 * 4. getPendingJid() — checks all Baileys field names (jid/id/participant)
 * 5. removeAllMembers() — now uses timeout, removes in small batches with delays
 * 6. approveAllPending() — uses timeout on every step
 * 7. getAllGroupsWithDetails() — 45s timeout with fallback retry
 * 8. Auto-reconnect after WA kicks the socket
 */

const {
  default: makeWASocket,
  DisconnectReason,
  fetchLatestBaileysVersion,
} = require("@whiskeysockets/baileys");
const pino = require("pino");
const { useMongoAuthState, clearMongoAuth } = require("./mongoAuthState");
const { AccountInfo } = require("./models");

const logger = pino({ level: "silent" });
const accounts = [{ index: 0, socket: null, status: "disconnected", phoneNumber: "" }];

let onPairingCode  = async () => {};
let onReady        = async () => {};
let onDisconnected = async () => {};

function setCallbacks(opts) {
  if (opts.onPairingCode)  onPairingCode  = opts.onPairingCode;
  if (opts.onReady)        onReady        = opts.onReady;
  if (opts.onDisconnected) onDisconnected = opts.onDisconnected;
}

function getStatus(index = 0)  { return accounts[index]?.status ?? "disconnected"; }
function getPhone(index = 0)   { return accounts[index]?.phoneNumber ?? ""; }
function getConnectedCount()   { return accounts.filter((a) => a.status === "connected").length; }

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// FREEZE FIX #1: withTimeout — wrap every single socket call
// Without this, groupFetchAllParticipating / groupMetadata / etc. can
// hang for minutes if WhatsApp server is slow or connection is flaky.
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
function withTimeout(promise, ms = 20000, label = "operation") {
  return Promise.race([
    promise,
    new Promise((_, reject) =>
      setTimeout(() => reject(new Error(`Timeout: ${label} took >${ms / 1000}s`)), ms)
    ),
  ]);
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// FREEZE FIX #2: JID/Phone normalization
// Baileys JIDs can be:
//   "919876543210@s.whatsapp.net"        (normal)
//   "919876543210:5@s.whatsapp.net"      (with device suffix — BREAKS phone extract)
//   "120363xxxxxxxx@g.us"               (group)
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
function normJid(jid) {
  if (!jid) return "";
  // Strip device suffix FIRST, then lowercase
  return jid.replace(/:\d+@/, "@").toLowerCase().trim();
}

// Extract pure phone digits from JID
// "919876543210:5@s.whatsapp.net" → "919876543210"
// "919876543210@s.whatsapp.net"   → "919876543210"
function jidToPhone(jid) {
  if (!jid) return "";
  return normJid(jid)
    .replace(/@s\.whatsapp\.net$/, "")
    .replace(/@g\.us$/, "")
    .replace(/[^0-9]/g, "");
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// NUMBER FIX: phoneMatch — flexible matching handles country code variants
// E.g. input "9876543210" matches JID "919876543210" and vice versa
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
function phoneMatch(jidPhone, inputDigits) {
  if (!jidPhone || !inputDigits) return false;
  if (jidPhone === inputDigits) return true;
  if (jidPhone.endsWith(inputDigits)) return true;
  if (inputDigits.endsWith(jidPhone)) return true;
  // Handle 10-digit vs 12-digit Indian numbers
  if (jidPhone.length > inputDigits.length && jidPhone.endsWith(inputDigits)) return true;
  if (inputDigits.length > jidPhone.length && inputDigits.endsWith(jidPhone)) return true;
  return false;
}

// Parse phone digits robustly from user input
// Handles: +91-9876-543210, 91 98765 43210, (91)9876543210, etc.
function cleanPhone(raw) {
  return String(raw).replace(/[^0-9]/g, "");
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// PENDING FIX: getPendingJid — handles all Baileys field name variants
// Baileys has changed: .jid / .id / .participant across versions
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
function getPendingJid(entry) {
  if (!entry) return null;
  // Try all known field names
  const raw = entry.jid || entry.id || entry.participant || entry.userJid || null;
  return raw ? normJid(raw) : null;
}

// ─── Auto Accept State ────────────────────────────────────────────────────
const autoAcceptGroups = new Map();
let autoAcceptTimer = null;

function startAutoAcceptForGroups(groupIds, index = 0) {
  groupIds.forEach((gid) => {
    if (!autoAcceptGroups.has(gid)) autoAcceptGroups.set(gid, { accepted: 0 });
  });
  if (autoAcceptTimer) return;
  autoAcceptTimer = setInterval(async () => {
    const active = [...autoAcceptGroups.keys()];
    if (!active.length) { clearInterval(autoAcceptTimer); autoAcceptTimer = null; return; }
    const s = getSocket(index);
    if (!s) return;
    for (const gid of active) {
      if (!autoAcceptGroups.has(gid)) continue;
      try {
        const pending = await withTimeout(s.groupRequestParticipantsList(gid), 15000, "pending-list");
        const jids = (pending || []).map(getPendingJid).filter(Boolean);
        if (jids.length) {
          await withTimeout(
            s.groupRequestParticipantsUpdate(gid, jids, "approve"),
            15000, "approve-batch"
          ).catch(() => {});
          const rec = autoAcceptGroups.get(gid);
          if (rec) rec.accepted += jids.length;
        }
      } catch (e) {
        console.error(`[AutoAccept] ${gid}: ${e.message}`);
      }
      await new Promise((r) => setTimeout(r, 600));
    }
  }, 8000);
}

function stopAutoAcceptForGroups(groupIds) {
  groupIds.forEach((gid) => autoAcceptGroups.delete(gid));
  if (autoAcceptGroups.size === 0 && autoAcceptTimer) {
    clearInterval(autoAcceptTimer); autoAcceptTimer = null;
  }
}

function getAutoAcceptStats(groupIds) {
  const result = {};
  groupIds.forEach((gid) => { result[gid] = autoAcceptGroups.get(gid) || { accepted: 0 }; });
  return result;
}

// ─── Connect Account ──────────────────────────────────────────────────────
async function connectAccount(index, phoneNumber, freshStart = true) {
  const acc = accounts[index];
  if (!acc) throw new Error("Invalid account index");
  if (acc.socket) { try { acc.socket.end(undefined); } catch {} acc.socket = null; }

  const accountId = `account${index + 1}`;
  const clean = cleanPhone(phoneNumber);

  if (freshStart) {
    await clearMongoAuth(accountId);
    await AccountInfo.findOneAndUpdate(
      { accountIndex: index },
      { accountIndex: index, phoneNumber: clean, hasAuth: false },
      { upsert: true }
    );
  }

  acc.status = "connecting";
  acc.phoneNumber = clean;

  const { state, saveCreds } = await withTimeout(useMongoAuthState(accountId), 15000, "mongo-auth");
  const { version } = await withTimeout(fetchLatestBaileysVersion(), 15000, "baileys-version");

  const socket = makeWASocket({
    version,
    logger,
    auth: state,
    printQRInTerminal: false,
    browser: ["Ubuntu", "Chrome", "124.0.0.0"],
    syncFullHistory: false,
    generateHighQualityLinkPreview: false,
    connectTimeoutMs: 45000,
    defaultQueryTimeoutMs: 25000,  // Baileys built-in query timeout
    keepAliveIntervalMs: 20000,
    markOnlineOnConnect: false,
    getMessage: async () => undefined,
  });

  acc.socket = socket;
  socket.ev.on("creds.update", saveCreds);
  let pairingRequested = false;

  socket.ev.on("connection.update", async (update) => {
    const { connection, lastDisconnect, qr } = update;
    if (qr && !pairingRequested) {
      pairingRequested = true;
      await _requestPairingWithRetry(socket, index, clean);
    }
    if (connection === "open") {
      acc.status = "connected";
      await AccountInfo.findOneAndUpdate(
        { accountIndex: index },
        { accountIndex: index, phoneNumber: clean, hasAuth: true },
        { upsert: true }
      );
      console.log(`[WA] Connected — +${clean}`);
      await onReady(index);
    }
    if (connection === "close") {
      const code = lastDisconnect?.error?.output?.statusCode;
      acc.status = "disconnected";
      console.log(`[WA] Disconnected — code: ${code}`);
      if (code === DisconnectReason.loggedOut) {
        await clearMongoAuth(accountId);
        await AccountInfo.findOneAndUpdate(
          { accountIndex: index },
          { accountIndex: index, phoneNumber: "", hasAuth: false },
          { upsert: true }
        );
        acc.phoneNumber = "";
        await onDisconnected(index);
      } else if (acc.phoneNumber) {
        console.log("[WA] Auto-reconnecting in 5s...");
        setTimeout(() => connectAccount(index, acc.phoneNumber, false).catch(console.error), 5000);
      }
    }
  });
}

async function _requestPairingWithRetry(socket, index, clean, attempt = 1) {
  try {
    const code = await withTimeout(socket.requestPairingCode(clean), 30000, "pairing-code");
    if (code) {
      const formatted = code.replace(/[^A-Z0-9]/gi, "").match(/.{1,4}/g)?.join("-") ?? code;
      await onPairingCode(index, formatted);
    }
  } catch (e) {
    console.error("[Pairing] attempt", attempt, e.message);
    if (attempt < 3) {
      await new Promise((r) => setTimeout(r, 4000));
      await _requestPairingWithRetry(socket, index, clean, attempt + 1);
    } else {
      await onPairingCode(index, null);
    }
  }
}

async function disconnectAccount(index = 0) {
  const acc = accounts[index]; if (!acc) return;
  if (acc.socket) { try { acc.socket.end(undefined); } catch {} acc.socket = null; }
  const accountId = `account${index + 1}`;
  await clearMongoAuth(accountId).catch(() => {});
  await AccountInfo.findOneAndUpdate(
    { accountIndex: index },
    { accountIndex: index, phoneNumber: "", hasAuth: false },
    { upsert: true }
  ).catch(() => {});
  acc.status = "disconnected"; acc.phoneNumber = "";
}

async function reconnectSavedAccounts() {
  const saved = await AccountInfo.find({ hasAuth: true, accountIndex: 0 });
  if (!saved.length) return;
  await connectAccount(saved[0].accountIndex, saved[0].phoneNumber, false)
    .catch((e) => console.error("[Startup]", e.message));
}

function getSocket(index = 0) {
  const acc = accounts[index];
  if (!acc?.socket || acc.status !== "connected") return null;
  return acc.socket;
}

// ─── Group Creation ────────────────────────────────────────────────────────
async function createGroup(index, name, jids) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  return await withTimeout(s.groupCreate(name, jids), 25000, "createGroup");
}
async function updateGroupDescription(index, gid, desc) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  await withTimeout(s.groupUpdateDescription(gid, desc), 15000, "updateDesc");
}
async function updateGroupPhoto(index, gid, buf) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  await withTimeout(s.updateProfilePicture(gid, buf), 20000, "updatePhoto");
}
async function setDisappearingMessages(index, gid, sec) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  await withTimeout(s.groupToggleEphemeral(gid, sec), 15000, "setDisappearing");
}
async function promoteToAdmin(index, gid, jids) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  await withTimeout(s.groupParticipantsUpdate(gid, jids, "promote"), 15000, "promote");
}
async function getGroupInviteLink(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  const c = await withTimeout(s.groupInviteCode(gid), 15000, "inviteCode");
  return `https://chat.whatsapp.com/${c}`;
}
async function joinGroupViaLink(index, code) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  return await withTimeout(s.groupAcceptInvite(code), 20000, "joinGroup");
}

async function setGroupPermissions(index, gid, p) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  await withTimeout(s.groupSettingUpdate(gid, p.sendMessages ? "not_announcement" : "announcement"), 12000, "perm-msg").catch(() => {});
  await withTimeout(s.groupSettingUpdate(gid, p.editInfo ? "unlocked" : "locked"), 12000, "perm-edit").catch(() => {});
  await withTimeout(s.groupMemberAddMode(gid, p.addMembers ? "all_member_add" : "admin_add"), 12000, "perm-add").catch(() => {});
  await withTimeout(s.groupJoinApprovalMode(gid, p.approveMembers ? "on" : "off"), 12000, "perm-approval").catch(() => {});
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// FREEZE FIX: getAllGroupsWithDetails — 45s timeout + retry
// groupFetchAllParticipating is the most common freeze point
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async function getAllGroupsWithDetails(index, retries = 2) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  let lastErr;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const groups = await withTimeout(
        s.groupFetchAllParticipating(),
        45000,
        "fetchAllGroups"
      );
      return Object.entries(groups).map(([id, g]) => ({
        id,
        name: (g.subject || id).trim(),
        participantCount: g.participants?.length ?? 0,
        participants: g.participants ?? [],
        announce: g.announce ?? false,
        restrict: g.restrict ?? false,
        joinApprovalMode: g.joinApprovalMode,
        memberAddMode: g.memberAddMode,
      }));
    } catch (e) {
      lastErr = e;
      console.error(`[Groups] fetch attempt ${attempt + 1} failed: ${e.message}`);
      if (attempt < retries) await new Promise((r) => setTimeout(r, 3000));
    }
  }
  throw lastErr;
}

// ─── Leave Group ─────────────────────────────────────────────────────────
async function leaveGroup(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  await withTimeout(s.groupLeave(gid), 15000, "leaveGroup");
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// REMOVE FIX: removeAllMembers — timeout + smaller batches
// groupParticipantsUpdate can also hang; now wrapped with timeout
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async function removeAllMembers(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  const meta  = await withTimeout(s.groupMetadata(gid), 20000, "groupMetadata");
  const myJid = normJid(s.user?.id || "");
  const toRm  = meta.participants
    .filter((p) => {
      const isAdmin = p.admin === "admin" || p.admin === "superadmin" || p.admin === true;
      return !isAdmin && normJid(p.id) !== myJid;
    })
    .map((p) => normJid(p.id));

  if (!toRm.length) return 0;
  let removed = 0;
  // Remove in batches of 3 with timeout on each batch
  for (let i = 0; i < toRm.length; i += 3) {
    const batch = toRm.slice(i, i + 3);
    try {
      await withTimeout(s.groupParticipantsUpdate(gid, batch, "remove"), 15000, "remove-batch");
      removed += batch.length;
    } catch (e) {
      // Try one by one
      for (const jid of batch) {
        try {
          await withTimeout(s.groupParticipantsUpdate(gid, [jid], "remove"), 10000, "remove-single");
          removed++;
        } catch {}
        await new Promise((r) => setTimeout(r, 800));
      }
    }
    await new Promise((r) => setTimeout(r, 1200));
  }
  return removed;
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// NUMBER FIX: makeAdminByNumbers — phoneMatch() + timeout on every call
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async function makeAdminByNumbers(index, gid, phones) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  let promoted = 0;

  for (const phone of phones) {
    const digits = cleanPhone(phone);
    if (!digits || digits.length < 7) continue;

    try {
      const meta = await withTimeout(s.groupMetadata(gid), 20000, "meta-makeAdmin");

      // Use improved phoneMatch() — handles country code variants
      const member = meta.participants.find((p) => phoneMatch(jidToPhone(p.id), digits));

      if (member) {
        await withTimeout(
          s.groupParticipantsUpdate(gid, [normJid(member.id)], "promote"),
          15000, "promote"
        ).catch((e) => console.error("[makeAdmin] promote:", e.message));
        promoted++;
      } else {
        // Check pending list
        let pendingJid = null;
        try {
          const pendingList = await withTimeout(
            s.groupRequestParticipantsList(gid), 15000, "pending-makeAdmin"
          );
          const match = (pendingList || []).find((p) => {
            const pjid = getPendingJid(p);
            return pjid && phoneMatch(jidToPhone(pjid), digits);
          });
          if (match) pendingJid = getPendingJid(match);
        } catch {}

        if (pendingJid) {
          await withTimeout(
            s.groupRequestParticipantsUpdate(gid, [pendingJid], "approve"),
            15000, "approve-pending"
          ).catch(() => {});
          await new Promise((r) => setTimeout(r, 4000));
          try {
            const newMeta = await withTimeout(s.groupMetadata(gid), 15000, "meta-after-approve");
            const newMember = newMeta.participants.find((p) => phoneMatch(jidToPhone(p.id), digits));
            const jidToUse = newMember ? normJid(newMember.id) : pendingJid;
            await withTimeout(
              s.groupParticipantsUpdate(gid, [jidToUse], "promote"),
              15000, "promote-after-approve"
            ).catch(() => {});
            promoted++;
          } catch {}
        }
      }
    } catch (e) {
      console.error("[makeAdmin]", digits, e.message);
    }
    await new Promise((r) => setTimeout(r, 600));
  }
  return promoted;
}

// ─── Demote Admin ─────────────────────────────────────────────────────────
async function demoteAdminInGroup(index, gid, phones) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  let demoted = 0;

  for (const phone of phones) {
    const digits = cleanPhone(phone);
    if (!digits || digits.length < 7) continue;
    try {
      const meta = await withTimeout(s.groupMetadata(gid), 20000, "meta-demote");
      const member = meta.participants.find((p) => phoneMatch(jidToPhone(p.id), digits));
      if (!member) continue;
      const isAdmin = member.admin === "admin" || member.admin === "superadmin" || member.admin === true;
      if (!isAdmin) continue;
      await withTimeout(
        s.groupParticipantsUpdate(gid, [normJid(member.id)], "demote"),
        15000, "demote"
      ).catch(() => {});
      demoted++;
    } catch (e) {
      console.error("[demoteAdmin]", digits, e.message);
    }
    await new Promise((r) => setTimeout(r, 600));
  }
  return demoted;
}

// ─── Approval Toggle ──────────────────────────────────────────────────────
async function getGroupApprovalStatus(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  const meta = await withTimeout(s.groupMetadata(gid), 20000, "meta-approval");
  return meta.joinApprovalMode === "on" || meta.joinApprovalMode === true;
}
async function setGroupApproval(index, gid, enable) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  await withTimeout(s.groupJoinApprovalMode(gid, enable ? "on" : "off"), 15000, "setApproval");
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// PENDING FIX: approveAllPending — timeout + getPendingJid() on ALL Baileys versions
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async function approveAllPending(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");

  const metaBefore = await withTimeout(s.groupMetadata(gid), 20000, "meta-before");
  const beforeCount = metaBefore.participants?.length ?? 0;

  let pendingJids = [];
  try {
    const pending = await withTimeout(
      s.groupRequestParticipantsList(gid), 20000, "pending-list"
    );
    // Use getPendingJid — handles all field name variants (.jid/.id/.participant)
    pendingJids = (pending ?? []).map(getPendingJid).filter(Boolean);
  } catch (e) {
    console.error("[approvePending] list error:", e.message);
    return { pendingCount: 0, approved: 0, failed: 0, beforeCount, afterCount: beforeCount };
  }

  if (!pendingJids.length) {
    return { pendingCount: 0, approved: 0, failed: 0, beforeCount, afterCount: beforeCount };
  }

  let approved = 0, failed = 0;
  // Approve in batches of 10 with timeout
  for (let i = 0; i < pendingJids.length; i += 10) {
    const batch = pendingJids.slice(i, i + 10);
    try {
      await withTimeout(
        s.groupRequestParticipantsUpdate(gid, batch, "approve"),
        20000, "approve-batch"
      );
      approved += batch.length;
    } catch {
      // Fallback: one by one
      for (const jid of batch) {
        try {
          await withTimeout(
            s.groupRequestParticipantsUpdate(gid, [jid], "approve"),
            10000, "approve-single"
          );
          approved++;
        } catch { failed++; }
        await new Promise((r) => setTimeout(r, 500));
      }
    }
    await new Promise((r) => setTimeout(r, 1500));
  }

  await new Promise((r) => setTimeout(r, 2500));
  let afterCount = beforeCount;
  try {
    const metaAfter = await withTimeout(s.groupMetadata(gid), 15000, "meta-after");
    afterCount = metaAfter.participants?.length ?? beforeCount;
  } catch {}

  return { pendingCount: pendingJids.length, approved, failed, actuallyJoined: afterCount - beforeCount, beforeCount, afterCount };
}

// ─── Get Members List ─────────────────────────────────────────────────────
async function getGroupMembers(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  const meta = await withTimeout(s.groupMetadata(gid), 20000, "meta-members");
  return (meta.participants || []).map((p) => ({
    jid: normJid(p.id),
    phone: jidToPhone(p.id),
    admin: p.admin || null,
  }));
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// PENDING FIX: getGroupPendingRequests — timeout + getPendingJid()
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async function getGroupPendingRequests(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  try {
    const list = await withTimeout(s.groupRequestParticipantsList(gid), 20000, "pending-requests");
    return (list ?? [])
      .map((p) => { const jid = getPendingJid(p); return jid ? { jid, phone: jidToPhone(jid) } : null; })
      .filter(Boolean);
  } catch (e) {
    console.error("[getPendingRequests]", gid, e.message);
    return [];
  }
}

async function getPendingForGroup(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  try {
    const pending = await withTimeout(s.groupRequestParticipantsList(gid), 20000, "pending-ctc");
    return (pending ?? [])
      .map((p) => { const jid = getPendingJid(p); return jid ? { jid, phone: jidToPhone(jid) } : null; })
      .filter(Boolean);
  } catch { return []; }
}

// ─── Reset Group Invite Link ──────────────────────────────────────────────
async function resetGroupInviteLink(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  await withTimeout(s.groupRevokeInvite(gid), 15000, "revokeLink");
  await new Promise((r) => setTimeout(r, 1200));
  const code = await withTimeout(s.groupInviteCode(gid), 15000, "newCode");
  return `https://chat.whatsapp.com/${code}`;
}

// ─── Get/Apply Group Settings ─────────────────────────────────────────────
async function getGroupSettings(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  const meta = await withTimeout(s.groupMetadata(gid), 20000, "meta-settings");
  return {
    announce:      meta.announce === true,
    restrict:      meta.restrict === true,
    joinApproval:  meta.joinApprovalMode === "on" || meta.joinApprovalMode === true,
    memberAddMode: meta.memberAddMode === "all_member_add",
  };
}

async function applyGroupSettings(index, gid, desired) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  const cur = await getGroupSettings(index, gid);
  const changes = [], skipped = [];

  if (desired.announce !== null && desired.announce !== undefined) {
    if (desired.announce !== cur.announce) {
      await withTimeout(s.groupSettingUpdate(gid, desired.announce ? "announcement" : "not_announcement"), 12000, "setting-announce").catch(() => {});
      changes.push(`💬 Messages: ${desired.announce ? "Admins Only" : "All Members"}`);
      await new Promise((r) => setTimeout(r, 600));
    } else skipped.push(`💬 Messages already: ${cur.announce ? "Admins Only" : "All Members"}`);
  }
  if (desired.restrict !== null && desired.restrict !== undefined) {
    if (desired.restrict !== cur.restrict) {
      await withTimeout(s.groupSettingUpdate(gid, desired.restrict ? "locked" : "unlocked"), 12000, "setting-restrict").catch(() => {});
      changes.push(`✏️ Edit Info: ${desired.restrict ? "Admins Only" : "All Members"}`);
      await new Promise((r) => setTimeout(r, 600));
    } else skipped.push(`✏️ Edit Info already: ${cur.restrict ? "Admins Only" : "All Members"}`);
  }
  if (desired.joinApproval !== null && desired.joinApproval !== undefined) {
    if (desired.joinApproval !== cur.joinApproval) {
      await withTimeout(s.groupJoinApprovalMode(gid, desired.joinApproval ? "on" : "off"), 12000, "setting-approval").catch(() => {});
      changes.push(`🔐 Join Approval: ${desired.joinApproval ? "On" : "Off"}`);
      await new Promise((r) => setTimeout(r, 600));
    } else skipped.push(`🔐 Join Approval already: ${cur.joinApproval ? "On" : "Off"}`);
  }
  if (desired.memberAddMode !== null && desired.memberAddMode !== undefined) {
    if (desired.memberAddMode !== cur.memberAddMode) {
      await withTimeout(s.groupMemberAddMode(gid, desired.memberAddMode ? "all_member_add" : "admin_add"), 12000, "setting-addMode").catch(() => {});
      changes.push(`➕ Add Members: ${desired.memberAddMode ? "All Members" : "Admins Only"}`);
      await new Promise((r) => setTimeout(r, 600));
    } else skipped.push(`➕ Add Members already: ${cur.memberAddMode ? "All Members" : "Admins Only"}`);
  }
  return { changes, skipped };
}

// ─── Rename Group ─────────────────────────────────────────────────────────
async function renameGroup(index, gid, newName) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  await withTimeout(s.groupUpdateSubject(gid, newName), 15000, "renameGroup");
}

// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
// ADD MEMBERS FIX: timeout + deduplicate + robust JID building
// ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
async function addMembersToGroup(index, gid, phones, oneByOne = false) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");

  const jids = [...new Set(
    phones
      .map(cleanPhone)
      .filter((n) => n.length >= 7)
      .map((n) => `${n}@s.whatsapp.net`)
  )];

  const results = { added: 0, failed: 0, skipped: 0, failedNums: [] };
  const parseStatus = (st) => String(st ?? "200").trim();

  if (oneByOne) {
    for (const jid of jids) {
      try {
        const res = await withTimeout(s.groupParticipantsUpdate(gid, [jid], "add"), 15000, "add-single");
        const status = parseStatus(res?.[0]?.status);
        if (status === "200" || status === "201") results.added++;
        else if (status === "403" || status === "409") results.skipped++;
        else { results.failed++; results.failedNums.push(jidToPhone(jid)); }
      } catch { results.failed++; results.failedNums.push(jidToPhone(jid)); }
      await new Promise((r) => setTimeout(r, 2500));
    }
  } else {
    for (let i = 0; i < jids.length; i += 5) {
      const batch = jids.slice(i, i + 5);
      try {
        const res = await withTimeout(s.groupParticipantsUpdate(gid, batch, "add"), 20000, "add-bulk");
        if (Array.isArray(res)) {
          res.forEach((r, idx) => {
            const st = parseStatus(r.status);
            if (st === "200" || st === "201") results.added++;
            else if (st === "403" || st === "409") results.skipped++;
            else { results.failed++; results.failedNums.push(jidToPhone(batch[idx]) || ""); }
          });
        } else { results.added += batch.length; }
      } catch {
        results.failed += batch.length;
        batch.forEach((j) => results.failedNums.push(jidToPhone(j)));
      }
      await new Promise((r) => setTimeout(r, 2000));
    }
  }
  return results;
}

// ─── Get Group Info from Invite Link ─────────────────────────────────────
async function getGroupInfoFromLink(index, code) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  try {
    const info = await withTimeout(s.groupGetInviteInfo(code), 20000, "groupInviteInfo");
    return { id: info.id, name: info.subject, participants: info.participants || [] };
  } catch { return null; }
}

module.exports = {
  setCallbacks,
  getStatus, getPhone, getConnectedCount,
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
  // Expose helpers for index.js use
  cleanPhone, phoneMatch, jidToPhone,
};
