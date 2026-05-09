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

async function connectAccount(index, phoneNumber, freshStart = true) {
  const acc = accounts[index];
  if (!acc) throw new Error("Invalid account index");
  if (acc.socket) { try { acc.socket.end(undefined); } catch {} acc.socket = null; }

  const accountId = `account${index + 1}`;
  if (freshStart) {
    await clearMongoAuth(accountId);
    await AccountInfo.findOneAndUpdate(
      { accountIndex: index },
      { accountIndex: index, phoneNumber, hasAuth: false },
      { upsert: true }
    );
  }

  acc.status = "connecting"; acc.phoneNumber = phoneNumber;
  const { state, saveCreds } = await useMongoAuthState(accountId);
  const { version }          = await fetchLatestBaileysVersion();

  const socket = makeWASocket({
    version, logger, auth: state, printQRInTerminal: false,
    browser: ["Ubuntu", "Chrome", "120.0.0.0"], syncFullHistory: false,
    generateHighQualityLinkPreview: false, connectTimeoutMs: 60000,
    defaultQueryTimeoutMs: 60000, keepAliveIntervalMs: 15000, markOnlineOnConnect: false,
  });

  acc.socket = socket;
  socket.ev.on("creds.update", saveCreds);
  const clean = phoneNumber.replace(/[^0-9]/g, "");
  let pairingRequested = false;

  socket.ev.on("connection.update", async (update) => {
    const { connection, lastDisconnect, qr } = update;
    if (qr && !pairingRequested) { pairingRequested = true; await _requestPairingWithRetry(socket, index, clean); }
    if (connection === "open") {
      acc.status = "connected";
      await AccountInfo.findOneAndUpdate(
        { accountIndex: index },
        { accountIndex: index, phoneNumber: clean, hasAuth: true },
        { upsert: true }
      );
      console.log(`[WA] Connected — ${clean}`);
      await onReady(index);
    }
    if (connection === "close") {
      const code = lastDisconnect?.error?.output?.statusCode;
      acc.status = "disconnected";
      if (code === DisconnectReason.loggedOut) {
        await clearMongoAuth(accountId);
        await AccountInfo.findOneAndUpdate(
          { accountIndex: index },
          { accountIndex: index, phoneNumber: "", hasAuth: false },
          { upsert: true }
        );
        acc.phoneNumber = ""; await onDisconnected(index);
      } else if (acc.phoneNumber) {
        setTimeout(() => connectAccount(index, acc.phoneNumber, false).catch(console.error), 5000);
      }
    }
  });
}

async function _requestPairingWithRetry(socket, index, clean, attempt = 1) {
  try {
    const code = await socket.requestPairingCode(clean);
    if (code) {
      const formatted = code.replace(/[^A-Z0-9]/gi, "").match(/.{1,4}/g)?.join("-") ?? code;
      await onPairingCode(index, formatted);
    }
  } catch {
    if (attempt < 3) { await new Promise((r) => setTimeout(r, 4000)); await _requestPairingWithRetry(socket, index, clean, attempt + 1); }
    else await onPairingCode(index, null);
  }
}

async function disconnectAccount(index = 0) {
  const acc = accounts[index]; if (!acc) return;
  if (acc.socket) { try { acc.socket.end(undefined); } catch {} acc.socket = null; }
  const accountId = `account${index + 1}`;
  await clearMongoAuth(accountId);
  await AccountInfo.findOneAndUpdate(
    { accountIndex: index },
    { accountIndex: index, phoneNumber: "", hasAuth: false },
    { upsert: true }
  );
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
async function createGroup(index, name, jids)           { const s=getSocket(index); if(!s) throw new Error("Not connected!"); return await s.groupCreate(name, jids); }
async function updateGroupDescription(index, gid, desc) { const s=getSocket(index); if(!s) throw new Error("Not connected!"); await s.groupUpdateDescription(gid, desc); }
async function updateGroupPhoto(index, gid, buf)        { const s=getSocket(index); if(!s) throw new Error("Not connected!"); await s.updateProfilePicture(gid, buf); }
async function setDisappearingMessages(index, gid, sec) { const s=getSocket(index); if(!s) throw new Error("Not connected!"); await s.groupToggleEphemeral(gid, sec); }
async function promoteToAdmin(index, gid, jids)         { const s=getSocket(index); if(!s) throw new Error("Not connected!"); await s.groupParticipantsUpdate(gid, jids, "promote"); }
async function getGroupInviteLink(index, gid)           { const s=getSocket(index); if(!s) throw new Error("Not connected!"); const c=await s.groupInviteCode(gid); return `https://chat.whatsapp.com/${c}`; }
async function joinGroupViaLink(index, code)            { const s=getSocket(index); if(!s) throw new Error("Not connected!"); return await s.groupAcceptInvite(code); }

async function setGroupPermissions(index, gid, p) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  await s.groupSettingUpdate(gid, p.sendMessages ? "not_announcement" : "announcement").catch(() => {});
  await s.groupSettingUpdate(gid, p.editInfo ? "unlocked" : "locked").catch(() => {});
  await s.groupMemberAddMode(gid, p.addMembers ? "all_member_add" : "admin_add").catch(() => {});
  await s.groupJoinApprovalMode(gid, p.approveMembers ? "on" : "off").catch(() => {});
}

// ─── Fetch All Groups ──────────────────────────────────────────────────────
async function getAllGroupsWithDetails(index) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  const groups = await s.groupFetchAllParticipating();
  return Object.entries(groups).map(([id, g]) => ({
    id, name: g.subject || id,
    participantCount: g.participants?.length ?? 0,
    participants: g.participants ?? [],
    announce: g.announce ?? false,
    restrict: g.restrict ?? false,
  }));
}

// ─── Leave Group ───────────────────────────────────────────────────────────
async function leaveGroup(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  await s.groupLeave(gid);
}

// ─── Remove All Non-Admin Members ─────────────────────────────────────────
async function removeAllMembers(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  const meta  = await s.groupMetadata(gid);
  const myJid = (s.user?.id || "").replace(/:.*@/, "@");
  const toRm  = meta.participants.filter((p) => !p.admin && p.id !== myJid).map((p) => p.id);
  if (!toRm.length) return 0;
  for (let i = 0; i < toRm.length; i += 5) {
    await s.groupParticipantsUpdate(gid, toRm.slice(i, i + 5), "remove").catch(() => {});
    await new Promise((r) => setTimeout(r, 1000));
  }
  return toRm.length;
}

// ─── Make Admin ────────────────────────────────────────────────────────────
async function makeAdminByNumbers(index, gid, phones) {
  const s    = getSocket(index); if (!s) throw new Error("Not connected!");
  const jids = phones.map((n) => `${n.replace(/[^0-9]/g, "")}@s.whatsapp.net`);
  const meta  = await s.groupMetadata(gid);
  const exist = meta.participants.map((p) => p.id);
  const toUp  = jids.filter((j) => exist.includes(j));

  let approved = [];
  try {
    const pending   = await s.groupRequestParticipantsList(gid);
    const pendIds   = (pending ?? []).map((p) => p.jid);
    const toApprove = jids.filter((j) => pendIds.includes(j));
    if (toApprove.length) {
      await s.groupRequestParticipantsUpdate(gid, toApprove, "approve").catch(() => {});
      approved = toApprove;
    }
  } catch {}

  const all = [...new Set([...toUp, ...approved])];
  if (all.length) await s.groupParticipantsUpdate(gid, all, "promote").catch(() => {});
  return all.length;
}

// ─── Approval Toggle ───────────────────────────────────────────────────────
async function getGroupApprovalStatus(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  const meta = await s.groupMetadata(gid);
  return meta.joinApprovalMode === "on" || meta.joinApprovalMode === true;
}
async function setGroupApproval(index, gid, enable) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  await s.groupJoinApprovalMode(gid, enable ? "on" : "off");
}

// ─── Approve All Pending Members ───────────────────────────────────────────
async function approveAllPending(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");

  // Member count before
  const metaBefore  = await s.groupMetadata(gid);
  const beforeCount = metaBefore.participants?.length ?? 0;

  // Get all pending requests
  let pendingJids = [];
  try {
    const pending = await s.groupRequestParticipantsList(gid);
    pendingJids   = (pending ?? []).map((p) => p.jid).filter(Boolean);
  } catch { return { pendingCount: 0, approved: 0, failed: 0, beforeCount, afterCount: beforeCount }; }

  if (!pendingJids.length) {
    return { pendingCount: 0, approved: 0, failed: 0, beforeCount, afterCount: beforeCount };
  }

  let approved = 0, failed = 0;

  // Approve in batches of 20
  for (let i = 0; i < pendingJids.length; i += 20) {
    const batch = pendingJids.slice(i, i + 20);
    try {
      await s.groupRequestParticipantsUpdate(gid, batch, "approve");
      approved += batch.length;
    } catch {
      // Fallback: try one by one
      for (const jid of batch) {
        try {
          await s.groupRequestParticipantsUpdate(gid, [jid], "approve");
          approved++;
        } catch { failed++; }
        await new Promise((r) => setTimeout(r, 400));
      }
    }
    await new Promise((r) => setTimeout(r, 1500));
  }

  // Wait 3s then re-fetch to get accurate after count
  await new Promise((r) => setTimeout(r, 3000));
  let afterCount = beforeCount;
  try {
    const metaAfter = await s.groupMetadata(gid);
    afterCount = metaAfter.participants?.length ?? beforeCount;
  } catch {}

  return {
    pendingCount: pendingJids.length,
    approved,
    failed,
    actuallyJoined: afterCount - beforeCount,
    beforeCount,
    afterCount,
  };
}

// ─── Get Members List ─────────────────────────────────────────────────────
async function getGroupMembers(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  const meta = await s.groupMetadata(gid);
  return (meta.participants || []).map((p) => ({
    jid:   p.id,
    phone: p.id.replace("@s.whatsapp.net", ""),
    admin: p.admin || null,
  }));
}

// ─── Get Pending Requests ─────────────────────────────────────────────────
async function getGroupPendingRequests(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  try {
    const list = await s.groupRequestParticipantsList(gid);
    return (list ?? []).map((p) => ({ jid: p.jid, phone: p.jid.replace("@s.whatsapp.net", "") }));
  } catch { return []; }
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
};
