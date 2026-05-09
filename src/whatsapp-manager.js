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

// ─── Auto Accept State ────────────────────────────────────────────────────
const autoAcceptGroups = new Map(); // gid -> { accepted: 0 }

function startAutoAcceptForGroups(groupIds) {
  groupIds.forEach((gid) => autoAcceptGroups.set(gid, { accepted: 0 }));
}

function stopAutoAcceptForGroups(groupIds) {
  groupIds.forEach((gid) => autoAcceptGroups.delete(gid));
}

function getAutoAcceptStats(groupIds) {
  const result = {};
  groupIds.forEach((gid) => { result[gid] = autoAcceptGroups.get(gid) || { accepted: 0 }; });
  return result;
}

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

  // ─── Auto Accept: only approve link-based join requests ──────────────
  socket.ev.on("group-participants.update", async (update) => {
    const { id: gid, participants, action } = update;
    if (action !== "request_join") return;
    const session = autoAcceptGroups.get(gid);
    if (!session) return;
    try {
      await socket.groupRequestParticipantsUpdate(gid, participants, "approve");
      session.accepted += participants.length;
      console.log(`[AutoAccept] ${gid}: approved ${participants.length}`);
    } catch (e) { console.error("[AutoAccept] error:", e.message); }
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
    joinApprovalMode: g.joinApprovalMode,
    memberAddMode: g.memberAddMode,
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
  const metaBefore  = await s.groupMetadata(gid);
  const beforeCount = metaBefore.participants?.length ?? 0;

  let pendingJids = [];
  try {
    const pending = await s.groupRequestParticipantsList(gid);
    pendingJids   = (pending ?? []).map((p) => p.jid).filter(Boolean);
  } catch { return { pendingCount: 0, approved: 0, failed: 0, beforeCount, afterCount: beforeCount }; }

  if (!pendingJids.length) {
    return { pendingCount: 0, approved: 0, failed: 0, beforeCount, afterCount: beforeCount };
  }

  let approved = 0, failed = 0;
  for (let i = 0; i < pendingJids.length; i += 20) {
    const batch = pendingJids.slice(i, i + 20);
    try {
      await s.groupRequestParticipantsUpdate(gid, batch, "approve");
      approved += batch.length;
    } catch {
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

// ─── Get Members List ─────────────────────────────────────────────────────
async function getGroupMembers(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  const meta = await s.groupMetadata(gid);
  return (meta.participants || []).map((p) => ({
    jid: p.id, phone: p.id.replace("@s.whatsapp.net", ""), admin: p.admin || null,
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

// ─── Reset Group Invite Link ───────────────────────────────────────────────
async function resetGroupInviteLink(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  await s.groupRevokeInvite(gid);
  await new Promise((r) => setTimeout(r, 1200));
  const code = await s.groupInviteCode(gid);
  return `https://chat.whatsapp.com/${code}`;
}

// ─── Demote Admin ──────────────────────────────────────────────────────────
async function demoteAdminInGroup(index, gid, phones) {
  const s    = getSocket(index); if (!s) throw new Error("Not connected!");
  const jids = phones.map((n) => `${n.replace(/[^0-9]/g, "")}@s.whatsapp.net`);
  const meta  = await s.groupMetadata(gid);
  const admins = meta.participants.filter((p) => p.admin).map((p) => p.id);
  const toDemote = jids.filter((j) => admins.includes(j));
  if (toDemote.length) await s.groupParticipantsUpdate(gid, toDemote, "demote");
  return toDemote.length;
}

// ─── Get Group Settings ───────────────────────────────────────────────────
async function getGroupSettings(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  const meta = await s.groupMetadata(gid);
  return {
    announce:     meta.announce ?? false,
    restrict:     meta.restrict ?? false,
    joinApproval: meta.joinApprovalMode === "on" || meta.joinApprovalMode === true,
    memberAddMode: meta.memberAddMode === "all_member_add",
  };
}

// ─── Apply Group Settings ─────────────────────────────────────────────────
async function applyGroupSettings(index, gid, desired) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  const cur = await getGroupSettings(index, gid);
  const changes = [], skippedReasons = [];

  if (desired.announce !== undefined) {
    if (desired.announce !== cur.announce) {
      await s.groupSettingUpdate(gid, desired.announce ? "announcement" : "not_announcement").catch(() => {});
      changes.push(`📢 Messages: ${desired.announce ? "Admins only" : "All members"}`);
      await new Promise((r) => setTimeout(r, 500));
    } else { skippedReasons.push(`📢 Messages already ${cur.announce ? "Admins only" : "All members"}`); }
  }
  if (desired.restrict !== undefined) {
    if (desired.restrict !== cur.restrict) {
      await s.groupSettingUpdate(gid, desired.restrict ? "locked" : "unlocked").catch(() => {});
      changes.push(`✏️ Edit Info: ${desired.restrict ? "Admins only" : "All members"}`);
      await new Promise((r) => setTimeout(r, 500));
    } else { skippedReasons.push(`✏️ Edit Info already ${cur.restrict ? "Admins only" : "All members"}`); }
  }
  if (desired.joinApproval !== undefined) {
    if (desired.joinApproval !== cur.joinApproval) {
      await s.groupJoinApprovalMode(gid, desired.joinApproval ? "on" : "off").catch(() => {});
      changes.push(`✅ Join Approval: ${desired.joinApproval ? "On" : "Off"}`);
      await new Promise((r) => setTimeout(r, 500));
    } else { skippedReasons.push(`✅ Join Approval already ${cur.joinApproval ? "On" : "Off"}`); }
  }
  if (desired.memberAddMode !== undefined) {
    if (desired.memberAddMode !== cur.memberAddMode) {
      await s.groupMemberAddMode(gid, desired.memberAddMode ? "all_member_add" : "admin_add").catch(() => {});
      changes.push(`➕ Add Members: ${desired.memberAddMode ? "All members" : "Admins only"}`);
      await new Promise((r) => setTimeout(r, 500));
    } else { skippedReasons.push(`➕ Add Members already ${cur.memberAddMode ? "All members" : "Admins only"}`); }
  }
  return { changes, skippedReasons };
}

// ─── Rename Group ─────────────────────────────────────────────────────────
async function renameGroup(index, gid, newName) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  await s.groupUpdateSubject(gid, newName);
}

// ─── Add Members to Group ─────────────────────────────────────────────────
async function addMembersToGroup(index, gid, phones, oneByOne = false) {
  const s    = getSocket(index); if (!s) throw new Error("Not connected!");
  const jids = [...new Set(phones.map((n) => `${n.replace(/[^0-9]/g, "")}@s.whatsapp.net`))];
  const results = { added: 0, failed: 0, skipped: 0, failedNums: [] };

  if (oneByOne) {
    for (const jid of jids) {
      try {
        const res = await s.groupParticipantsUpdate(gid, [jid], "add");
        const status = String(res?.[0]?.status ?? "200");
        if (status === "200") results.added++;
        else if (status === "403" || status === "409") results.skipped++;
        else { results.failed++; results.failedNums.push(jid.replace("@s.whatsapp.net", "")); }
      } catch {
        results.failed++; results.failedNums.push(jid.replace("@s.whatsapp.net", ""));
      }
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
            else if (st === "403" || st === "409") results.skipped++;
            else { results.failed++; results.failedNums.push(batch[idx]?.replace("@s.whatsapp.net", "") || ""); }
          });
        } else { results.added += batch.length; }
      } catch {
        results.failed += batch.length;
        batch.forEach((j) => results.failedNums.push(j.replace("@s.whatsapp.net", "")));
      }
      await new Promise((r) => setTimeout(r, 2000));
    }
  }
  return results;
}

// ─── Get Group Info from Invite Link ──────────────────────────────────────
async function getGroupInfoFromLink(index, code) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  try {
    const info = await s.groupGetInviteInfo(code);
    return { id: info.id, name: info.subject, participants: info.participants || [] };
  } catch { return null; }
}

// ─── Get Pending for CTC Checker ─────────────────────────────────────────
async function getPendingForGroup(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  try {
    const pending = await s.groupRequestParticipantsList(gid);
    return (pending ?? []).map((p) => ({ jid: p.jid, phone: p.jid.replace("@s.whatsapp.net", "") }));
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
  resetGroupInviteLink,
  demoteAdminInGroup,
  getGroupSettings, applyGroupSettings,
  renameGroup,
  addMembersToGroup,
  getGroupInfoFromLink,
  getPendingForGroup,
  startAutoAcceptForGroups, stopAutoAcceptForGroups, getAutoAcceptStats,
};
