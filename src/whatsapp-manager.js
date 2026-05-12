/**
 * whatsapp-manager.js — Public Multi-User Edition
 *
 * - Unlimited concurrent WhatsApp connections (one per Telegram user)
 * - Auth persists in MongoDB — users never need to re-pair after restart
 * - Idle sockets auto-disconnected after IDLE_TIMEOUT_MIN to free memory
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

// ─── Config ───────────────────────────────────────────────────────────────
// Idle disconnect: free socket RAM when user is inactive (auth stays in MongoDB)
const IDLE_TIMEOUT_MS = parseInt(process.env.IDLE_TIMEOUT_MIN || "30") * 60 * 1000;

// ─── Per-user account store ───────────────────────────────────────────────
// Key: uid (string = Telegram user id)
// Value: { socket, status, phoneNumber, lastActivity }
const accounts = new Map();

function getConnectedCount() { return [...accounts.values()].filter(a => a.status === "connected").length; }
function getTotalTracked()   { return accounts.size; }

function getAcc(uid) {
  const k = String(uid);
  if (!accounts.has(k)) {
    accounts.set(k, { socket: null, status: "disconnected", phoneNumber: "", lastActivity: Date.now(), connectedAt: null });
  }
  return accounts.get(k);
}

function touchAcc(uid) {
  const acc = accounts.get(String(uid));
  if (acc) acc.lastActivity = Date.now();
}

function getSocket(uid) {
  const acc = getAcc(uid);
  if (!acc.socket || acc.status !== "connected") return null;
  touchAcc(uid);
  return acc.socket;
}

// ─── Idle cleanup — runs every 5 min ─────────────────────────────────────
// Disconnects idle sockets to free RAM; auth stays in MongoDB for auto-resume.
setInterval(() => {
  const now = Date.now();
  for (const [uid, acc] of accounts) {
    if (acc.status === "connected" && now - acc.lastActivity > IDLE_TIMEOUT_MS) {
      if (acc.socket) { try { acc.socket.end(undefined); } catch {} acc.socket = null; }
      acc.status = "disconnected";
      console.log(`[Idle] uid=${uid} disconnected after ${Math.round(IDLE_TIMEOUT_MS/60000)}min idle.`);
    }
    // Purge stale disconnected entries older than 1 hour
    if (acc.status === "disconnected" && acc.socket === null && now - acc.lastActivity > 60 * 60 * 1000) {
      accounts.delete(uid);
    }
  }
}, 5 * 60 * 1000);

// ─── Callbacks ────────────────────────────────────────────────────────────
let onPairingCode  = async () => {};
let onReady        = async () => {};
let onDisconnected = async () => {};

function setCallbacks(opts) {
  if (opts.onPairingCode)  onPairingCode  = opts.onPairingCode;
  if (opts.onReady)        onReady        = opts.onReady;
  if (opts.onDisconnected) onDisconnected = opts.onDisconnected;
}

function getStatus(uid)        { return getAcc(uid).status; }
function getPhone(uid)         { return getAcc(uid).phoneNumber; }
function getConnectedCount()   { return [...accounts.values()].filter((a) => a.status === "connected").length; }

// ─── JID helpers ─────────────────────────────────────────────────────────
function normJid(jid) {
  return (jid || "").replace(/:\d+@/, "@").toLowerCase().trim();
}

function extractPhoneNumber(p) {
  const allJids = [p.id, p.lid, p.jid, p.userJid, p.participant]
    .filter((j) => j && typeof j === "string");
  const phoneJid = allJids.find((j) => j.endsWith("@s.whatsapp.net"));
  const displayJid = phoneJid || allJids[0] || "";
  return displayJid.split("@")[0].split(":")[0];
}

function numberMatches(stored, input) {
  if (!stored || !input) return false;
  const s = stored.replace(/\D/g, "");
  const i = input.replace(/\D/g, "");
  if (s === i) return true;
  if (i.length >= 8 && s.endsWith(i)) return true;
  if (s.length >= 8 && i.endsWith(s)) return true;
  return false;
}

async function resolvePhoneJid(socket, digits) {
  try {
    const results = await socket.onWhatsApp(digits);
    if (Array.isArray(results) && results.length > 0) {
      const found = results.find((r) => r.exists && r.jid);
      return found ? found.jid : null;
    }
  } catch {}
  return null;
}

// ─── Auto Accept ──────────────────────────────────────────────────────────
// autoAcceptGroups: Map<gid, { accepted, uid }>
const autoAcceptGroups = new Map();
let autoAcceptTimer = null;

function isSelfJoinRequest(entry) {
  if (typeof entry === "string") return true;
  const method = entry.method ?? entry.requestMethod ?? entry.request_method ?? null;
  if (method === "non_admin_add") return false;
  if (entry.addedBy || entry.adder || entry.requestedBy || entry.addRequest || entry.add_request || entry.addedByJid || entry.addRequestJid) return false;
  return method === "invite_link" || method === null || method === undefined;
}

function startAutoAcceptForGroups(groupIds, uid) {
  groupIds.forEach((gid) => autoAcceptGroups.set(gid, { accepted: 0, uid: String(uid) }));
  if (autoAcceptTimer) return;
  autoAcceptTimer = setInterval(async () => {
    const active = [...autoAcceptGroups.keys()];
    if (!active.length) { clearInterval(autoAcceptTimer); autoAcceptTimer = null; return; }
    for (const gid of active) {
      const rec = autoAcceptGroups.get(gid);
      if (!rec) continue;
      const s = getSocket(rec.uid);
      if (!s) continue;
      try {
        const pending = await s.groupRequestParticipantsList(gid);
        const nonAdminAddSet = new Set();
        try {
          const meta = await s.groupMetadata(gid);
          for (const p of (meta.pendingParticipants || [])) {
            const j = normJid(typeof p === "string" ? p : (p.jid || p.id || p.participant || ""));
            if (j) nonAdminAddSet.add(j);
          }
        } catch {}
        const toAccept = (pending || []).filter((p) => {
          const jid = normJid(typeof p === "string" ? p : (p.jid || p.id || ""));
          if (jid && nonAdminAddSet.has(jid)) return false;
          return isSelfJoinRequest(p);
        });
        const jids = toAccept.map((p) => (typeof p === "string" ? p : (p.jid || p.id))).filter(Boolean);
        if (jids.length) {
          await s.groupRequestParticipantsUpdate(gid, jids, "approve").catch(() => {});
          if (rec) rec.accepted += jids.length;
        }
      } catch {}
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

// ─── Connection ───────────────────────────────────────────────────────────
async function connectAccount(uid, phoneNumber, freshStart = true) {
  uid = String(uid);
  const acc = getAcc(uid);

  if (acc.socket) { try { acc.socket.end(undefined); } catch {} acc.socket = null; }

  const accountId = `user_${uid}`;
  if (freshStart) {
    await clearMongoAuth(accountId);
    await AccountInfo.findOneAndUpdate(
      { accountId: uid },
      { accountId: uid, phoneNumber, hasAuth: false },
      { upsert: true }
    );
  }

  acc.status = "connecting";
  acc.phoneNumber = phoneNumber;
  acc.lastActivity = Date.now();

  const { state, saveCreds } = await useMongoAuthState(accountId);
  const { version }          = await fetchLatestBaileysVersion();

  const socket = makeWASocket({
    version, logger, auth: state, printQRInTerminal: false,
    browser: ["Ubuntu", "Chrome", "120.0.0.0"],
    syncFullHistory: false, generateHighQualityLinkPreview: false,
    connectTimeoutMs: 60000, defaultQueryTimeoutMs: 60000,
    keepAliveIntervalMs: 15000, markOnlineOnConnect: false,
  });

  acc.socket = socket;
  socket.ev.on("creds.update", saveCreds);
  const clean = phoneNumber.replace(/[^0-9]/g, "");
  let pairingRequested = false;

  socket.ev.on("connection.update", async (update) => {
    const { connection, lastDisconnect, qr } = update;
    if (qr && !pairingRequested) {
      pairingRequested = true;
      await _requestPairingWithRetry(socket, uid, clean);
    }
    if (connection === "open") {
      acc.status = "connected";
      acc.lastActivity = Date.now();
      await AccountInfo.findOneAndUpdate(
        { accountId: uid },
        { accountId: uid, phoneNumber: clean, hasAuth: true },
        { upsert: true }
      );
      await onReady(uid);
    }
    if (connection === "close") {
      const code = lastDisconnect?.error?.output?.statusCode;
      acc.status = "disconnected";
      if (code === DisconnectReason.loggedOut) {
        await clearMongoAuth(accountId);
        await AccountInfo.findOneAndUpdate(
          { accountId: uid },
          { accountId: uid, phoneNumber: "", hasAuth: false },
          { upsert: true }
        );
        acc.phoneNumber = "";
        await onDisconnected(uid);
      } else if (acc.phoneNumber) {
        setTimeout(() => connectAccount(uid, acc.phoneNumber, false).catch(console.error), 5000);
      }
    }
  });
}

async function _requestPairingWithRetry(socket, uid, clean, attempt = 1) {
  try {
    const code = await socket.requestPairingCode(clean);
    if (code) {
      const formatted = code.replace(/[^A-Z0-9]/gi, "").match(/.{1,4}/g)?.join("-") ?? code;
      await onPairingCode(uid, formatted);
    }
  } catch {
    if (attempt < 3) {
      await new Promise((r) => setTimeout(r, 4000));
      await _requestPairingWithRetry(socket, uid, clean, attempt + 1);
    } else {
      await onPairingCode(uid, null);
    }
  }
}

async function disconnectAccount(uid) {
  uid = String(uid);
  const acc = getAcc(uid);
  if (acc.socket) { try { acc.socket.end(undefined); } catch {} acc.socket = null; }
  const accountId = `user_${uid}`;
  await clearMongoAuth(accountId);
  await AccountInfo.findOneAndUpdate(
    { accountId: uid },
    { accountId: uid, phoneNumber: "", hasAuth: false },
    { upsert: true }
  );
  acc.status = "disconnected";
  acc.phoneNumber = "";
}

// reconnectSavedAccounts — reconnect users who were connected before restart
async function reconnectSavedAccounts() {
  const saved = await AccountInfo.find({ hasAuth: true }).lean();
  console.log(`[Startup] Found ${saved.length} saved WA account(s) — reconnecting...`);
  for (const s of saved) {
    try {
      await connectAccount(s.accountId, s.phoneNumber, false);
      await new Promise(r => setTimeout(r, 1500));
    } catch (e) {
      console.error(`[Startup] reconnect failed for ${s.accountId}:`, e.message);
    }
  }
}

// ensureConnected — used by scheduled tasks to auto-reconnect before running
// Returns true if connected, false if reconnect failed
async function ensureConnected(uid) {
  uid = String(uid);
  if (getStatus(uid) === "connected") return true;
  const saved = await AccountInfo.findOne({ accountId: uid, hasAuth: true }).lean();
  if (!saved || !saved.phoneNumber) return false;
  try {
    await connectAccount(uid, saved.phoneNumber, false);
    // Wait up to 20 seconds for WA to come online
    for (let i = 0; i < 20; i++) {
      await new Promise(r => setTimeout(r, 1000));
      if (getStatus(uid) === "connected") return true;
    }
  } catch {}
  return getStatus(uid) === "connected";
}

// ─── Server stats helper ──────────────────────────────────────────────────
function getMemStats() {
  const mem = process.memoryUsage();
  return {
    rss:       Math.round(mem.rss / 1024 / 1024),
    heap:      Math.round(mem.heapUsed / 1024 / 1024),
    accounts:  accounts.size,
    connected: getConnectedCount(),
  };
}

// ─── Group utilities ──────────────────────────────────────────────────────
async function getAllGroupsWithDetails(uid) {
  const s = getSocket(uid); if (!s) throw new Error("Not connected!");
  const groups = await s.groupFetchAllParticipating();
  return Object.entries(groups).map(([id, g]) => ({
    id, name: g.subject || id,
    participantCount: g.participants?.length ?? 0,
    participants: g.participants ?? [],
    announce: g.announce ?? false,
    restrict: g.restrict ?? false,
    joinApprovalMode: g.joinApprovalMode,
    memberAddMode: g.memberAddMode,
  }));
}

async function createGroup(uid, name, jids) {
  const s = getSocket(uid); if (!s) throw new Error("Not connected!");
  return await s.groupCreate(name, jids);
}
async function updateGroupDescription(uid, gid, desc) {
  const s = getSocket(uid); if (!s) throw new Error("Not connected!");
  await s.groupUpdateDescription(gid, desc);
}
async function updateGroupPhoto(uid, gid, buf) {
  const s = getSocket(uid); if (!s) throw new Error("Not connected!");
  await s.updateProfilePicture(gid, buf);
}
async function setDisappearingMessages(uid, gid, sec) {
  const s = getSocket(uid); if (!s) throw new Error("Not connected!");
  await s.groupToggleEphemeral(gid, sec);
}
async function promoteToAdmin(uid, gid, jids) {
  const s = getSocket(uid); if (!s) throw new Error("Not connected!");
  await s.groupParticipantsUpdate(gid, jids, "promote");
}
async function getGroupInviteLink(uid, gid) {
  const s = getSocket(uid); if (!s) throw new Error("Not connected!");
  const c = await s.groupInviteCode(gid);
  return `https://chat.whatsapp.com/${c}`;
}
async function joinGroupViaLink(uid, code) {
  const s = getSocket(uid); if (!s) throw new Error("Not connected!");
  return await s.groupAcceptInvite(code);
}
async function leaveGroup(uid, gid) {
  const s = getSocket(uid); if (!s) throw new Error("Not connected!");
  await s.groupLeave(gid);
}
async function getGroupInfoFromLink(uid, code) {
  const s = getSocket(uid); if (!s) throw new Error("Not connected!");
  try {
    const info = await s.groupGetInviteInfo(code);
    return { id: info.id, name: info.subject, participants: info.participants || [] };
  } catch { return null; }
}
async function renameGroup(uid, gid, newName) {
  const s = getSocket(uid); if (!s) throw new Error("Not connected!");
  await s.groupUpdateSubject(gid, newName);
}
async function setGroupPermissions(uid, gid, p) {
  const s = getSocket(uid); if (!s) throw new Error("Not connected!");
  await s.groupSettingUpdate(gid, p.sendMessages ? "not_announcement" : "announcement").catch(() => {});
  await s.groupSettingUpdate(gid, p.editInfo ? "unlocked" : "locked").catch(() => {});
  await s.groupMemberAddMode(gid, p.addMembers ? "all_member_add" : "admin_add").catch(() => {});
  await s.groupJoinApprovalMode(gid, p.approveMembers ? "on" : "off").catch(() => {});
}

async function removeAllMembers(uid, gid, batchSize = 5, skipAdmins = false) {
  const s = getSocket(uid); if (!s) throw new Error("Not connected!");
  const meta  = await s.groupMetadata(gid);
  const myJid = normJid(s.user?.id || "");
  const toRm  = meta.participants
    .filter((p) => {
      if (normJid(p.id) === myJid) return false;
      if (skipAdmins && (p.admin === "admin" || p.admin === "superadmin")) return false;
      return true;
    })
    .map((p) => p.id);
  if (!toRm.length) return 0;
  for (let i = 0; i < toRm.length; i += batchSize) {
    await s.groupParticipantsUpdate(gid, toRm.slice(i, i + batchSize), "remove").catch(() => {});
    await new Promise((r) => setTimeout(r, batchSize === 1 ? 1500 : 1000));
  }
  return toRm.length;
}

async function getGroupMembers(uid, gid) {
  const s = getSocket(uid); if (!s) throw new Error("Not connected!");
  const meta = await s.groupMetadata(gid);
  return (meta.participants || []).map((p) => {
    const number = extractPhoneNumber(p);
    return { id: p.id, jid: p.id, number, phone: number, admin: p.admin === "admin" || p.admin === "superadmin", superadmin: p.admin === "superadmin" };
  });
}

async function getGroupPendingRequests(uid, gid) {
  const s = getSocket(uid); if (!s) throw new Error("Not connected!");
  const parseEntry = (p, defaultMethod) => {
    const rawJid = typeof p === "string" ? p : (p.jid || p.id || p.participant || String(p));
    const allJids = typeof p === "object" && p !== null
      ? [p.jid, p.id, p.participant, p.userJid].filter((j) => j && typeof j === "string")
      : [rawJid];
    const phoneJid   = allJids.find((j) => j.endsWith("@s.whatsapp.net"));
    const displayJid = phoneJid || allJids[0] || rawJid;
    const number     = displayJid.split("@")[0].split(":")[0];
    const isLid      = !phoneJid && rawJid.endsWith("@lid");
    const method     = typeof p === "object" && p !== null ? (p.method || p.requestMethod || defaultMethod) : defaultMethod;
    return { id: rawJid, jid: rawJid, number, phone: number, method, isLid };
  };
  let results = [], errorMsg = null;
  try {
    const list = await s.groupRequestParticipantsList(gid);
    if (Array.isArray(list)) results.push(...list.map((p) => parseEntry(p, "invite_link")));
  } catch (err) { errorMsg = err.message; }
  try {
    const meta  = await s.groupMetadata(gid);
    const extra = meta.pendingParticipants || [];
    for (const p of extra) {
      const entry = parseEntry(p, "non_admin_add");
      if (!results.find((r) => r.id === entry.id)) results.push(entry);
    }
  } catch {}
  return { list: results, error: results.length === 0 ? errorMsg : null };
}

async function makeAdminByNumbers(uid, gid, phones) {
  const s = getSocket(uid); if (!s) throw new Error("Not connected!");
  let promoted = 0;
  for (const phone of phones) {
    const digits = phone.replace(/\D/g, "");
    if (!digits || digits.length < 7) continue;
    try {
      let realPhoneJid = await resolvePhoneJid(s, digits);
      const meta = await s.groupMetadata(gid);
      const memberInGroup = meta.participants.find((p) => {
        if (realPhoneJid && normJid(p.id) === normJid(realPhoneJid)) return true;
        return numberMatches(extractPhoneNumber(p), digits);
      });
      if (memberInGroup) {
        await s.groupParticipantsUpdate(gid, [memberInGroup.id], "promote");
        promoted++; continue;
      }
      const approveJid = realPhoneJid || `${digits}@s.whatsapp.net`;
      let pendingRawJid = null;
      try {
        const pendingList = await s.groupRequestParticipantsList(gid);
        for (const p of (pendingList || [])) {
          const allJids = [p.jid, p.id, p.participant, p.userJid].filter((j) => j && typeof j === "string");
          if (realPhoneJid && allJids.some((j) => normJid(j) === normJid(realPhoneJid))) { pendingRawJid = p.jid || p.id; break; }
          const phoneJid = allJids.find((j) => j.endsWith("@s.whatsapp.net"));
          if (phoneJid && numberMatches(phoneJid.split("@")[0].split(":")[0], digits)) { pendingRawJid = p.jid || p.id; break; }
        }
      } catch {}
      const jidToApprove = pendingRawJid || approveJid;
      let approveOk = false;
      try { await s.groupRequestParticipantsUpdate(gid, [jidToApprove], "approve"); approveOk = true; } catch {
        if (pendingRawJid && pendingRawJid !== approveJid) {
          try { await s.groupRequestParticipantsUpdate(gid, [approveJid], "approve"); approveOk = true; } catch {}
        }
      }
      if (!approveOk) continue;
      await new Promise((r) => setTimeout(r, 5000));
      const metaAfter = await s.groupMetadata(gid);
      const newMember = metaAfter.participants.find((p) => {
        if (realPhoneJid && normJid(p.id) === normJid(realPhoneJid)) return true;
        return numberMatches(extractPhoneNumber(p), digits);
      });
      if (newMember) { await s.groupParticipantsUpdate(gid, [newMember.id], "promote"); promoted++; }
    } catch (err) { console.error("[makeAdmin]", digits, err.message); }
    await new Promise((r) => setTimeout(r, 800));
  }
  return promoted;
}

async function demoteAdminInGroup(uid, gid, phones) {
  const s = getSocket(uid); if (!s) throw new Error("Not connected!");
  let demoted = 0;
  for (const phone of phones) {
    const digits = phone.replace(/\D/g, "");
    if (!digits || digits.length < 7) continue;
    try {
      const realPhoneJid = await resolvePhoneJid(s, digits);
      const meta   = await s.groupMetadata(gid);
      const member = meta.participants.find((p) => {
        if (realPhoneJid && normJid(p.id) === normJid(realPhoneJid)) return true;
        return numberMatches(extractPhoneNumber(p), digits);
      });
      if (!member) continue;
      const isAdmin = member.admin === "admin" || member.admin === "superadmin" || member.admin === true;
      if (!isAdmin) continue;
      await s.groupParticipantsUpdate(gid, [member.id], "demote");
      demoted++;
    } catch (err) { console.error("[demoteAdmin]", digits, err.message); }
    await new Promise((r) => setTimeout(r, 600));
  }
  return demoted;
}

async function getGroupApprovalStatus(uid, gid) {
  const s = getSocket(uid); if (!s) throw new Error("Not connected!");
  const meta = await s.groupMetadata(gid);
  return meta.joinApprovalMode === "on" || meta.joinApprovalMode === true;
}
async function setGroupApproval(uid, gid, enable) {
  const s = getSocket(uid); if (!s) throw new Error("Not connected!");
  await s.groupJoinApprovalMode(gid, enable ? "on" : "off");
}

async function approveAllPending(uid, gid) {
  const s = getSocket(uid); if (!s) throw new Error("Not connected!");
  const metaBefore  = await s.groupMetadata(gid);
  const beforeCount = metaBefore.participants?.length ?? 0;
  let pendingJids = [];
  try {
    const pending = await s.groupRequestParticipantsList(gid);
    pendingJids = (pending ?? []).map((p) => p.jid || p.id).filter(Boolean);
  } catch { return { pendingCount: 0, approved: 0, failed: 0, beforeCount, afterCount: beforeCount }; }
  if (!pendingJids.length) return { pendingCount: 0, approved: 0, failed: 0, beforeCount, afterCount: beforeCount };
  let approved = 0, failed = 0;
  for (let i = 0; i < pendingJids.length; i += 20) {
    const batch = pendingJids.slice(i, i + 20);
    try { await s.groupRequestParticipantsUpdate(gid, batch, "approve"); approved += batch.length; }
    catch {
      for (const jid of batch) {
        try { await s.groupRequestParticipantsUpdate(gid, [jid], "approve"); approved++; }
        catch { failed++; }
        await new Promise((r) => setTimeout(r, 400));
      }
    }
    await new Promise((r) => setTimeout(r, 1500));
  }
  await new Promise((r) => setTimeout(r, 3000));
  let afterCount = beforeCount;
  try { const metaAfter = await s.groupMetadata(gid); afterCount = metaAfter.participants?.length ?? beforeCount; } catch {}
  return { pendingCount: pendingJids.length, approved, failed, actuallyJoined: afterCount - beforeCount, beforeCount, afterCount };
}

async function resetGroupInviteLink(uid, gid) {
  const s = getSocket(uid); if (!s) throw new Error("Not connected!");
  await s.groupRevokeInvite(gid);
  await new Promise((r) => setTimeout(r, 1200));
  const code = await s.groupInviteCode(gid);
  return `https://chat.whatsapp.com/${code}`;
}

async function getGroupSettings(uid, gid) {
  const s = getSocket(uid); if (!s) throw new Error("Not connected!");
  const meta = await s.groupMetadata(gid);
  return {
    announce:      meta.announce === true,
    restrict:      meta.restrict === true,
    joinApproval:  meta.joinApprovalMode === "on" || meta.joinApprovalMode === true,
    memberAddMode: meta.memberAddMode === "all_member_add",
  };
}

async function applyGroupSettings(uid, gid, desired) {
  const s = getSocket(uid); if (!s) throw new Error("Not connected!");
  const cur = await getGroupSettings(uid, gid);
  const changes = [], skipped = [];
  const applyOne = async (key, settingFn, label, trueLabel, falseLabel) => {
    if (desired[key] === null || desired[key] === undefined) return;
    if (desired[key] !== cur[key]) {
      await settingFn(desired[key]).catch(() => {});
      changes.push(`${label}: ${desired[key] ? trueLabel : falseLabel}`);
      await new Promise((r) => setTimeout(r, 600));
    } else { skipped.push(`${label} already: ${cur[key] ? trueLabel : falseLabel}`); }
  };
  await applyOne("announce",      (v) => s.groupSettingUpdate(gid, v ? "announcement" : "not_announcement"), "💬 Messages",     "Admins Only", "All Members");
  await applyOne("restrict",      (v) => s.groupSettingUpdate(gid, v ? "locked" : "unlocked"),               "✏️ Edit Info",    "Admins Only", "All Members");
  await applyOne("joinApproval",  (v) => s.groupJoinApprovalMode(gid, v ? "on" : "off"),                     "🔐 Join Approval","On",          "Off");
  await applyOne("memberAddMode", (v) => s.groupMemberAddMode(gid, v ? "all_member_add" : "admin_add"),      "➕ Add Members",  "All Members", "Admins Only");
  return { changes, skipped };
}

async function addMembersToGroup(uid, gid, phones, oneByOne = false) {
  const s = getSocket(uid); if (!s) throw new Error("Not connected!");
  const jids    = [...new Set(phones.map((n) => `${n.replace(/\D/g, "")}@s.whatsapp.net`))];
  const results = { added: 0, failed: 0, skipped: 0, failedNums: [] };
  if (oneByOne) {
    for (const jid of jids) {
      try {
        const res    = await s.groupParticipantsUpdate(gid, [jid], "add");
        const status = String(res?.[0]?.status ?? "200");
        if (status === "200") results.added++;
        else if (["403","409"].includes(status)) results.skipped++;
        else { results.failed++; results.failedNums.push(normJid(jid).replace("@s.whatsapp.net","")); }
      } catch { results.failed++; results.failedNums.push(normJid(jid).replace("@s.whatsapp.net","")); }
      await new Promise((r) => setTimeout(r, 2500));
    }
  } else {
    for (let i = 0; i < jids.length; i += 5) {
      const batch = jids.slice(i, i + 5);
      try {
        const res = await s.groupParticipantsUpdate(gid, batch, "add");
        if (Array.isArray(res)) {
          res.forEach((r, idx) => {
            const st = String(r.status ?? "200");
            if (st === "200") results.added++;
            else if (["403","409"].includes(st)) results.skipped++;
            else { results.failed++; results.failedNums.push(batch[idx]?.replace("@s.whatsapp.net","") || ""); }
          });
        } else { results.added += batch.length; }
      } catch { results.failed += batch.length; batch.forEach((j) => results.failedNums.push(normJid(j).replace("@s.whatsapp.net",""))); }
      await new Promise((r) => setTimeout(r, 2000));
    }
  }
  return results;
}

async function getPendingRawJids(uid, gid) {
  const s = getSocket(uid); if (!s) throw new Error("Not connected!");
  const jidSet = new Set(), phoneSet = new Set();
  let pendingCount = 0;
  const addEntry = (p) => {
    const candidates = typeof p === "string" ? [p]
      : [p.jid, p.id, p.participant, p.userJid, p.lid].filter((j) => j && typeof j === "string");
    for (const j of candidates) {
      const norm = normJid(j);
      if (norm) jidSet.add(norm);
      if (norm.endsWith("@s.whatsapp.net")) {
        const ph = norm.split("@")[0].split(":")[0];
        if (ph && ph.length >= 7) phoneSet.add(ph);
      }
    }
  };
  try { const list = await s.groupRequestParticipantsList(gid); pendingCount = (list || []).length; (list || []).forEach(addEntry); } catch {}
  try {
    const meta = await s.groupMetadata(gid);
    for (const p of (meta.pendingParticipants || [])) {
      const primary = normJid(typeof p === "string" ? p : (p.jid || p.id || ""));
      if (!jidSet.has(primary)) pendingCount++;
      addEntry(p);
    }
  } catch {}
  return { jids: jidSet, phones: phoneSet, count: pendingCount };
}

async function resolveVcfPhones(uid, phones) {
  const s = getSocket(uid); if (!s) throw new Error("Not connected!");
  const digits = [...new Set(phones.map((p) => String(p).replace(/\D/g, "")).filter((p) => p.length >= 7))];
  if (!digits.length) return [];
  const out = [];
  try {
    const res = await s.onWhatsApp(...digits);
    for (const r of (res || [])) {
      if (r.exists && r.jid) out.push({ phone: r.jid.split("@")[0].split(":")[0], phoneJid: normJid(r.jid), lid: normJid(r.lid || "") });
    }
    if (out.length > 0) return out;
  } catch {}
  for (const d of digits) {
    try {
      const res   = await s.onWhatsApp(d);
      const found = (res || []).find((r) => r.exists && r.jid);
      if (found) out.push({ phone: d, phoneJid: normJid(found.jid), lid: normJid(found.lid || "") });
    } catch {}
  }
  return out;
}

// ─── Group Stats (members + pending + approval) ───────────────────────────
async function getGroupStats(uid, gid) {
  const s = getSocket(uid); if (!s) throw new Error("Not connected!");
  const meta = await s.groupMetadata(gid);
  const membersCount = meta.participants?.length ?? 0;
  const approval = meta.joinApprovalMode === "on" || meta.joinApprovalMode === true;
  let pendingCount = 0;
  try {
    const pending = await s.groupRequestParticipantsList(gid);
    pendingCount = (pending || []).length;
    const pendingMeta = (meta.pendingParticipants || []);
    pendingCount = Math.max(pendingCount, pendingMeta.length);
  } catch {}
  return { membersCount, pendingCount, approval };
}

// ─── Exports ──────────────────────────────────────────────────────────────
module.exports = {
  setCallbacks,
  getStatus, getPhone, getConnectedCount,
  connectAccount, disconnectAccount, reconnectSavedAccounts, ensureConnected,
  createGroup, updateGroupDescription, updateGroupPhoto,
  setDisappearingMessages, promoteToAdmin, setGroupPermissions,
  getGroupInviteLink, joinGroupViaLink,
  getAllGroupsWithDetails,
  leaveGroup, removeAllMembers,
  makeAdminByNumbers,
  numberMatches,
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
  getGroupStats,
  touchAcc,
};
