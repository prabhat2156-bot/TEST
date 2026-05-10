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

// ─── JID Normalization ────────────────────────────────────────────────────
function normJid(jid) {
  return (jid || "").replace(/:\d+@/, "@").toLowerCase().trim();
}

// ─── FIX: LID-aware number extraction ────────────────────────────────────
// WhatsApp new LID system: participant id may be @lid instead of @s.whatsapp.net
// This helper finds the real phone JID from all available fields
function extractPhoneNumber(participant) {
  const allJids = [
    participant.id,
    participant.lid,
    participant.jid,
    participant.userJid,
    participant.participant,
  ].filter((j) => j && typeof j === "string");
  const phoneJid = allJids.find((j) => j.endsWith("@s.whatsapp.net"));
  const displayJid = phoneJid || allJids[0] || "";
  return displayJid.split("@")[0].split(":")[0];
}

// ─── FIX: Flexible number match (handles missing country code) ────────────
// e.g. stored: "919876543210", entered: "9876543210" → still matches (suffix)
function numberMatches(stored, input) {
  if (!stored || !input) return false;
  const s = stored.replace(/[^0-9]/g, "");
  const i = input.replace(/[^0-9]/g, "");
  if (s === i) return true;
  if (s.endsWith(i) && i.length >= 8) return true;
  if (i.endsWith(s) && s.length >= 8) return true;
  return false;
}

// ─── Auto Accept State (polling-based, reliable) ──────────────────────────
const autoAcceptGroups = new Map(); // gid -> { accepted: 0 }
let autoAcceptTimer = null;

// Helper: extract JID from a pending entry (Baileys field varies by version)
function getPendingJid(entry) {
  return entry.jid || entry.participant || entry.id || null;
}

// Helper: check if pending request came from an invite link
function isInviteLinkRequest(entry) {
  const method = entry.method || entry.requestMethod || entry.addedByJid || null;
  if (method === null || method === undefined) return true;
  return method === "invite_link";
}

function startAutoAcceptForGroups(groupIds, index = 0) {
  groupIds.forEach((gid) => autoAcceptGroups.set(gid, { accepted: 0 }));
  if (autoAcceptTimer) return;
  autoAcceptTimer = setInterval(async () => {
    const active = [...autoAcceptGroups.keys()];
    if (!active.length) {
      clearInterval(autoAcceptTimer);
      autoAcceptTimer = null;
      return;
    }
    const s = getSocket(index);
    if (!s) return;
    for (const gid of active) {
      if (!autoAcceptGroups.has(gid)) continue;
      try {
        const pending = await s.groupRequestParticipantsList(gid);
        const inviteLinkOnly = (pending || []).filter(isInviteLinkRequest);
        const jids = inviteLinkOnly.map(getPendingJid).filter(Boolean);
        if (jids.length) {
          await s.groupRequestParticipantsUpdate(gid, jids, "approve").catch(() => {});
          const rec = autoAcceptGroups.get(gid);
          if (rec) rec.accepted += jids.length;
          console.log(`[AutoAccept] ${gid}: approved ${jids.length} (invite-link only)`);
        }
      } catch {}
      await new Promise((r) => setTimeout(r, 600));
    }
  }, 8000);
}

function stopAutoAcceptForGroups(groupIds) {
  groupIds.forEach((gid) => autoAcceptGroups.delete(gid));
  if (autoAcceptGroups.size === 0 && autoAcceptTimer) {
    clearInterval(autoAcceptTimer);
    autoAcceptTimer = null;
  }
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
  const myJid = normJid(s.user?.id || "");
  const toRm  = meta.participants
    .filter((p) => !p.admin && normJid(p.id) !== myJid)
    .map((p) => p.id);
  if (!toRm.length) return 0;
  for (let i = 0; i < toRm.length; i += 5) {
    await s.groupParticipantsUpdate(gid, toRm.slice(i, i + 5), "remove").catch(() => {});
    await new Promise((r) => setTimeout(r, 1000));
  }
  return toRm.length;
}

// ─── FIX: Get Members List (LID-aware) ────────────────────────────────────
async function getGroupMembers(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  const meta = await s.groupMetadata(gid);
  return (meta.participants || []).map((p) => {
    // FIX: Handle LID system — check all possible fields for a real phone JID
    const allJids = [p.id, p.lid, p.jid, p.userJid].filter(
      (j) => j && typeof j === "string"
    );
    const phoneJid = allJids.find((j) => j.endsWith("@s.whatsapp.net"));
    const displayJid = phoneJid || allJids[0] || "";
    const number = displayJid.split("@")[0].split(":")[0];

    return {
      id: p.id,          // original JID for API calls
      jid: p.id,         // backward compat
      number,            // FIX: clean phone number for display & search
      phone: number,     // backward compat alias
      admin: p.admin === "admin" || p.admin === "superadmin",
      superadmin: p.admin === "superadmin",
    };
  });
}

// ─── FIX: Get Pending Requests (LID-aware, returns {list, error}) ──────────
async function getGroupPendingRequests(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");

  // FIX: LID-aware entry parser
  const parseEntry = (p, defaultMethod) => {
    // Try to get the raw JID for API use (approve/reject)
    const rawJid = (typeof p === "string")
      ? p
      : (p.jid || p.id || p.participant || String(p));

    // FIX: Look for a real phone JID among all fields
    const allJids = typeof p === "object" && p !== null
      ? [p.jid, p.id, p.participant, p.userJid].filter((j) => j && typeof j === "string")
      : [rawJid];
    const phoneJid = allJids.find((j) => j.endsWith("@s.whatsapp.net"));
    const displayJid = phoneJid || allJids[0] || rawJid;
    const number = displayJid.split("@")[0].split(":")[0];

    const method = (typeof p === "object" && p !== null)
      ? (p.method || p.requestMethod || defaultMethod)
      : defaultMethod;

    return { id: rawJid, jid: rawJid, number, phone: number, method };
  };

  let results = [];
  let errorMsg = null;

  // Primary: groupRequestParticipantsList (link-join requests)
  try {
    const list = await s.groupRequestParticipantsList(gid);
    if (Array.isArray(list)) {
      results.push(...list.map((p) => parseEntry(p, "invite_link")));
    }
  } catch (err) {
    errorMsg = err.message;
  }

  // Fallback: groupMetadata.pendingParticipants (member-added requests)
  try {
    const meta = await s.groupMetadata(gid);
    const pending = meta.pendingParticipants || [];
    for (const p of pending) {
      const entry = parseEntry(p, "non_admin_add");
      if (!results.find((r) => r.id === entry.id)) {
        results.push(entry);
      }
    }
  } catch {}

  return { list: results, error: results.length === 0 ? errorMsg : null };
}

// ─── FIX: Make Admin — LID-aware + flexible number matching ───────────────
async function makeAdminByNumbers(index, gid, phones) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  let promoted = 0;

  for (const phone of phones) {
    const digits = phone.replace(/[^0-9]/g, "");
    if (!digits || digits.length < 7) continue;
    const baseJid = `${digits}@s.whatsapp.net`;

    try {
      // ── Step 1: Fetch fresh metadata, FIX: use numberMatches ──
      const meta   = await s.groupMetadata(gid);
      const member = meta.participants.find((p) => {
        const num = extractPhoneNumber(p);
        return numberMatches(num, digits);
      });

      if (member) {
        // Already in group → promote using their actual JID
        await s.groupParticipantsUpdate(gid, [member.id], "promote").catch((e) =>
          console.error("[makeAdmin] promote error:", e.message)
        );
        promoted++;
      } else {
        // ── Step 2: Not in group → check pending requests ──
        let pendingRawJid = null;
        try {
          const pendingList = await s.groupRequestParticipantsList(gid);
          for (const p of (pendingList || [])) {
            // FIX: LID-aware number extraction from pending entry
            const allJids = [p.jid, p.id, p.participant, p.userJid].filter(
              (j) => j && typeof j === "string"
            );
            const phoneJid = allJids.find((j) => j.endsWith("@s.whatsapp.net"));
            const displayJid = phoneJid || allJids[0] || "";
            const num = displayJid.split("@")[0].split(":")[0];
            if (numberMatches(num, digits)) {
              pendingRawJid = p.jid || p.id || p.participant || null;
              break;
            }
          }
        } catch {}

        if (pendingRawJid) {
          // Found in pending → approve first
          await s.groupRequestParticipantsUpdate(gid, [pendingRawJid], "approve").catch(() => {});

          // Wait for them to actually join (6 seconds)
          await new Promise((r) => setTimeout(r, 6000));

          // Re-fetch metadata to get their real JID
          try {
            const newMeta   = await s.groupMetadata(gid);
            const newMember = newMeta.participants.find((p) => {
              const num = extractPhoneNumber(p);
              return numberMatches(num, digits);
            });
            const jidToUse = newMember ? newMember.id : pendingRawJid;
            await s.groupParticipantsUpdate(gid, [jidToUse], "promote").catch((e) =>
              console.error("[makeAdmin] promote-after-approve error:", e.message)
            );
            promoted++;
          } catch (e) {
            console.error("[makeAdmin] post-approve error:", e.message);
          }
        }
      }
    } catch (e) {
      console.error("[makeAdmin] error for", digits, ":", e.message);
    }

    await new Promise((r) => setTimeout(r, 600));
  }

  return promoted;
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
    pendingJids = (pending ?? []).map((p) => p.jid || p.id).filter(Boolean);
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

// ─── Reset Group Invite Link ───────────────────────────────────────────────
async function resetGroupInviteLink(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  await s.groupRevokeInvite(gid);
  await new Promise((r) => setTimeout(r, 1200));
  const code = await s.groupInviteCode(gid);
  return `https://chat.whatsapp.com/${code}`;
}

// ─── FIX: Demote Admin — LID-aware + flexible number matching ─────────────
async function demoteAdminInGroup(index, gid, phones) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  let demoted = 0;

  for (const phone of phones) {
    const digits = phone.replace(/[^0-9]/g, "");
    if (!digits || digits.length < 7) continue;

    try {
      // FIX: Use LID-aware extractPhoneNumber + numberMatches
      const meta   = await s.groupMetadata(gid);
      const member = meta.participants.find((p) => {
        const num = extractPhoneNumber(p);
        return numberMatches(num, digits);
      });

      if (!member) continue;

      const isAdmin = member.admin === "admin" || member.admin === "superadmin" || member.admin === true;
      if (!isAdmin) continue;

      await s.groupParticipantsUpdate(gid, [member.id], "demote").catch((e) =>
        console.error("[demoteAdmin] demote error:", e.message)
      );
      demoted++;
    } catch (e) {
      console.error("[demoteAdmin] error for", digits, ":", e.message);
    }

    await new Promise((r) => setTimeout(r, 600));
  }

  return demoted;
}

// ─── Get Group Settings ───────────────────────────────────────────────────
async function getGroupSettings(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  const meta = await s.groupMetadata(gid);
  return {
    announce:      meta.announce === true,
    restrict:      meta.restrict === true,
    joinApproval:  meta.joinApprovalMode === "on" || meta.joinApprovalMode === true,
    memberAddMode: meta.memberAddMode === "all_member_add",
  };
}

// ─── Apply Group Settings (skip if already matching) ─────────────────────
async function applyGroupSettings(index, gid, desired) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  const cur = await getGroupSettings(index, gid);
  const changes = [], skipped = [];

  if (desired.announce !== undefined && desired.announce !== null) {
    if (desired.announce !== cur.announce) {
      await s.groupSettingUpdate(gid, desired.announce ? "announcement" : "not_announcement").catch(() => {});
      changes.push(`💬 Messages: ${desired.announce ? "Admins Only" : "All Members"}`);
      await new Promise((r) => setTimeout(r, 600));
    } else {
      skipped.push(`💬 Messages already: ${cur.announce ? "Admins Only" : "All Members"}`);
    }
  }

  if (desired.restrict !== undefined && desired.restrict !== null) {
    if (desired.restrict !== cur.restrict) {
      await s.groupSettingUpdate(gid, desired.restrict ? "locked" : "unlocked").catch(() => {});
      changes.push(`✏️ Edit Info: ${desired.restrict ? "Admins Only" : "All Members"}`);
      await new Promise((r) => setTimeout(r, 600));
    } else {
      skipped.push(`✏️ Edit Info already: ${cur.restrict ? "Admins Only" : "All Members"}`);
    }
  }

  if (desired.joinApproval !== undefined && desired.joinApproval !== null) {
    if (desired.joinApproval !== cur.joinApproval) {
      await s.groupJoinApprovalMode(gid, desired.joinApproval ? "on" : "off").catch(() => {});
      changes.push(`🔐 Join Approval: ${desired.joinApproval ? "On" : "Off"}`);
      await new Promise((r) => setTimeout(r, 600));
    } else {
      skipped.push(`🔐 Join Approval already: ${cur.joinApproval ? "On" : "Off"}`);
    }
  }

  if (desired.memberAddMode !== undefined && desired.memberAddMode !== null) {
    if (desired.memberAddMode !== cur.memberAddMode) {
      await s.groupMemberAddMode(gid, desired.memberAddMode ? "all_member_add" : "admin_add").catch(() => {});
      changes.push(`➕ Add Members: ${desired.memberAddMode ? "All Members" : "Admins Only"}`);
      await new Promise((r) => setTimeout(r, 600));
    } else {
      skipped.push(`➕ Add Members already: ${cur.memberAddMode ? "All Members" : "Admins Only"}`);
    }
  }

  return { changes, skipped };
}

// ─── Rename Group ─────────────────────────────────────────────────────────
async function renameGroup(index, gid, newName) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  await s.groupUpdateSubject(gid, newName);
}

// ─── Add Members to Group ─────────────────────────────────────────────────
async function addMembersToGroup(index, gid, phones, oneByOne = false) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  const jids = [...new Set(phones.map((n) => `${n.replace(/[^0-9]/g, "")}@s.whatsapp.net`))];
  const results = { added: 0, failed: 0, skipped: 0, failedNums: [] };

  if (oneByOne) {
    for (const jid of jids) {
      try {
        const res = await s.groupParticipantsUpdate(gid, [jid], "add");
        const status = String(res?.[0]?.status ?? "200");
        if (status === "200") results.added++;
        else if (status === "403" || status === "409") results.skipped++;
        else { results.failed++; results.failedNums.push(normJid(jid).replace("@s.whatsapp.net", "")); }
      } catch {
        results.failed++; results.failedNums.push(normJid(jid).replace("@s.whatsapp.net", ""));
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
        batch.forEach((j) => results.failedNums.push(normJid(j).replace("@s.whatsapp.net", "")));
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

// ─── FIX: Get Pending for CTC Checker (LID-aware) ────────────────────────
async function getPendingForGroup(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  try {
    const pending = await s.groupRequestParticipantsList(gid);
    return (pending ?? []).map((p) => {
      // FIX: LID-aware extraction
      const allJids = [p.jid, p.id, p.participant, p.userJid].filter(
        (j) => j && typeof j === "string"
      );
      const phoneJid = allJids.find((j) => j.endsWith("@s.whatsapp.net"));
      const displayJid = phoneJid || allJids[0] || "";
      const phone = displayJid.split("@")[0].split(":")[0];
      return { jid: p.jid || p.id, phone };
    });
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
  getPendingForGroup,
  startAutoAcceptForGroups, stopAutoAcceptForGroups, getAutoAcceptStats,
};
