/**
 * WhatsApp Manager — Fixed & Latest Version v2.0
 *
 * KEY FIXES:
 * 1. getPendingJid() — checks ALL possible Baileys field names (jid, id, participant)
 * 2. approveAllPending() — was using p.jid only → now uses getPendingJid()
 * 3. getGroupPendingRequests() — same fix
 * 4. normJid() — improved to strip device suffix correctly
 * 5. getAllGroupsWithDetails() — added error resilience + retry
 * 6. makeAdminByNumbers() — improved phone matching (handles country code variants)
 * 7. addMembersToGroup() — fixed status code parsing
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

// ─── JID Normalization ────────────────────────────────────────────────────
// FIX: Strip device suffix (e.g. "919876543210:5@s.whatsapp.net" → "919876543210@s.whatsapp.net")
function normJid(jid) {
  if (!jid) return "";
  return jid.replace(/:\d+@/, "@").toLowerCase().trim();
}

// FIX: Extract phone digits only (strip @s.whatsapp.net and device suffix)
function jidToPhone(jid) {
  return normJid(jid).replace("@s.whatsapp.net", "").replace("@g.us", "");
}

// FIX: Master helper — extract JID from any pending entry regardless of Baileys version
// Baileys has changed this field across versions: .jid / .id / .participant
function getPendingJid(entry) {
  if (!entry) return null;
  return entry.jid || entry.id || entry.participant || null;
}

// FIX: Helper to check if pending request is from invite link (not added by another member)
// Only approve invite-link requests in auto-accept mode
function isInviteLinkRequest(entry) {
  const method = entry.method || entry.requestMethod || entry.addedBy || null;
  if (method === null || method === undefined) return true; // fallback: approve all
  return method === "invite_link";
}

// ─── Auto Accept State ────────────────────────────────────────────────────
const autoAcceptGroups = new Map(); // gid -> { accepted: 0 }
let autoAcceptTimer = null;

function startAutoAcceptForGroups(groupIds, index = 0) {
  groupIds.forEach((gid) => {
    if (!autoAcceptGroups.has(gid)) autoAcceptGroups.set(gid, { accepted: 0 });
  });
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
        // FIX: Use getPendingJid() and filter invite-link only
        const inviteOnly = (pending || []).filter(isInviteLinkRequest);
        const jids = inviteOnly.map(getPendingJid).filter(Boolean);
        if (jids.length) {
          await s.groupRequestParticipantsUpdate(gid, jids, "approve").catch(() => {});
          const rec = autoAcceptGroups.get(gid);
          if (rec) rec.accepted += jids.length;
          console.log(`[AutoAccept] ${gid}: approved ${jids.length}`);
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
    clearInterval(autoAcceptTimer);
    autoAcceptTimer = null;
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
  const clean = phoneNumber.replace(/[^0-9]/g, "");

  if (freshStart) {
    await clearMongoAuth(accountId);
    await AccountInfo.findOneAndUpdate(
      { accountIndex: index },
      { accountIndex: index, phoneNumber: clean, hasAuth: false },
      { upsert: true }
    );
  }

  acc.status = "connecting"; acc.phoneNumber = clean;
  const { state, saveCreds } = await useMongoAuthState(accountId);
  const { version } = await fetchLatestBaileysVersion();

  const socket = makeWASocket({
    version,
    logger,
    auth: state,
    printQRInTerminal: false,
    browser: ["Ubuntu", "Chrome", "124.0.0.0"],
    syncFullHistory: false,
    generateHighQualityLinkPreview: false,
    connectTimeoutMs: 60000,
    defaultQueryTimeoutMs: 60000,
    keepAliveIntervalMs: 15000,
    markOnlineOnConnect: false,
    // FIX: Disable message handling to save memory (we only manage groups)
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
        // Auto-reconnect with backoff
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
// FIX: Added retry + error resilience for large group lists
async function getAllGroupsWithDetails(index, retries = 2) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  let lastErr;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try {
      const groups = await s.groupFetchAllParticipating();
      return Object.entries(groups).map(([id, g]) => ({
        id,
        name: g.subject || id,
        participantCount: g.participants?.length ?? 0,
        participants: g.participants ?? [],
        announce: g.announce ?? false,
        restrict: g.restrict ?? false,
        joinApprovalMode: g.joinApprovalMode,
        memberAddMode: g.memberAddMode,
      }));
    } catch (e) {
      lastErr = e;
      if (attempt < retries) await new Promise((r) => setTimeout(r, 3000));
    }
  }
  throw lastErr;
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

// ─── Make Admin ───────────────────────────────────────────────────────────
// FIX: Improved phone matching — handles country code prefix variations
async function makeAdminByNumbers(index, gid, phones) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  let promoted = 0;

  for (const phone of phones) {
    const digits = phone.replace(/[^0-9]/g, "");
    if (!digits || digits.length < 7) continue;

    try {
      const meta = await s.groupMetadata(gid);

      // FIX: Match by checking if participant JID ENDS WITH the phone digits
      // This handles both "91xxxxxxxxxx" and "xxxxxxxxxx" (without country code)
      const member = meta.participants.find((p) => {
        const ph = jidToPhone(p.id);
        return ph === digits || ph.endsWith(digits) || digits.endsWith(ph);
      });

      if (member) {
        await s.groupParticipantsUpdate(gid, [member.id], "promote").catch((e) =>
          console.error("[makeAdmin] promote error:", e.message)
        );
        promoted++;
      } else {
        // Not in group → check pending
        let pendingJid = null;
        try {
          const pendingList = await s.groupRequestParticipantsList(gid);
          // FIX: Use getPendingJid() to extract JID from any field
          const match = (pendingList || []).find((p) => {
            const pjid = getPendingJid(p);
            if (!pjid) return false;
            const ph = jidToPhone(pjid);
            return ph === digits || ph.endsWith(digits) || digits.endsWith(ph);
          });
          if (match) pendingJid = getPendingJid(match);
        } catch {}

        if (pendingJid) {
          await s.groupRequestParticipantsUpdate(gid, [pendingJid], "approve").catch(() => {});
          await new Promise((r) => setTimeout(r, 6000));
          try {
            const newMeta = await s.groupMetadata(gid);
            const newMember = newMeta.participants.find((p) => {
              const ph = jidToPhone(p.id);
              return ph === digits || ph.endsWith(digits) || digits.endsWith(ph);
            });
            const jidToUse = newMember ? newMember.id : pendingJid;
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

// ─── Demote Admin ─────────────────────────────────────────────────────────
// FIX: Same improved phone matching as makeAdmin
async function demoteAdminInGroup(index, gid, phones) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  let demoted = 0;

  for (const phone of phones) {
    const digits = phone.replace(/[^0-9]/g, "");
    if (!digits || digits.length < 7) continue;
    try {
      const meta = await s.groupMetadata(gid);
      const member = meta.participants.find((p) => {
        const ph = jidToPhone(p.id);
        return ph === digits || ph.endsWith(digits) || digits.endsWith(ph);
      });
      if (!member) continue;
      const isAdmin = member.admin === "admin" || member.admin === "superadmin" || member.admin === true;
      if (!isAdmin) continue;
      await s.groupParticipantsUpdate(gid, [member.id], "demote").catch((e) =>
        console.error("[demoteAdmin] error:", e.message)
      );
      demoted++;
    } catch (e) {
      console.error("[demoteAdmin] error for", digits, ":", e.message);
    }
    await new Promise((r) => setTimeout(r, 600));
  }
  return demoted;
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

// ─── Approve All Pending Members ──────────────────────────────────────────
// FIX: Was using p.jid only — now uses getPendingJid() to handle all Baileys versions
async function approveAllPending(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  const metaBefore  = await s.groupMetadata(gid);
  const beforeCount = metaBefore.participants?.length ?? 0;

  let pendingJids = [];
  try {
    const pending = await s.groupRequestParticipantsList(gid);
    // FIX: Use getPendingJid() instead of p.jid directly
    pendingJids = (pending ?? []).map(getPendingJid).filter(Boolean);
  } catch {
    return { pendingCount: 0, approved: 0, failed: 0, beforeCount, afterCount: beforeCount };
  }

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
      // Fallback: one by one
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
    jid: p.id,
    phone: jidToPhone(p.id),
    admin: p.admin || null,
  }));
}

// ─── Get Pending Requests ─────────────────────────────────────────────────
// FIX: Was using p.jid only — now uses getPendingJid() to handle all Baileys versions
async function getGroupPendingRequests(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  try {
    const list = await s.groupRequestParticipantsList(gid);
    return (list ?? [])
      .map((p) => {
        const jid = getPendingJid(p);
        if (!jid) return null;
        return { jid, phone: jidToPhone(jid) };
      })
      .filter(Boolean);
  } catch { return []; }
}

// ─── Get Pending for CTC Checker ─────────────────────────────────────────
// FIX: Same fix applied here
async function getPendingForGroup(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  try {
    const pending = await s.groupRequestParticipantsList(gid);
    return (pending ?? [])
      .map((p) => {
        const jid = getPendingJid(p);
        if (!jid) return null;
        return { jid, phone: jidToPhone(jid) };
      })
      .filter(Boolean);
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

// ─── Apply Group Settings ─────────────────────────────────────────────────
async function applyGroupSettings(index, gid, desired) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  const cur = await getGroupSettings(index, gid);
  const changes = [], skipped = [];

  if (desired.announce !== undefined && desired.announce !== null) {
    if (desired.announce !== cur.announce) {
      await s.groupSettingUpdate(gid, desired.announce ? "announcement" : "not_announcement").catch(() => {});
      changes.push(`💬 Messages: ${desired.announce ? "Admins Only" : "All Members"}`);
      await new Promise((r) => setTimeout(r, 600));
    } else { skipped.push(`💬 Messages already: ${cur.announce ? "Admins Only" : "All Members"}`); }
  }

  if (desired.restrict !== undefined && desired.restrict !== null) {
    if (desired.restrict !== cur.restrict) {
      await s.groupSettingUpdate(gid, desired.restrict ? "locked" : "unlocked").catch(() => {});
      changes.push(`✏️ Edit Info: ${desired.restrict ? "Admins Only" : "All Members"}`);
      await new Promise((r) => setTimeout(r, 600));
    } else { skipped.push(`✏️ Edit Info already: ${cur.restrict ? "Admins Only" : "All Members"}`); }
  }

  if (desired.joinApproval !== undefined && desired.joinApproval !== null) {
    if (desired.joinApproval !== cur.joinApproval) {
      await s.groupJoinApprovalMode(gid, desired.joinApproval ? "on" : "off").catch(() => {});
      changes.push(`🔐 Join Approval: ${desired.joinApproval ? "On" : "Off"}`);
      await new Promise((r) => setTimeout(r, 600));
    } else { skipped.push(`🔐 Join Approval already: ${cur.joinApproval ? "On" : "Off"}`); }
  }

  if (desired.memberAddMode !== undefined && desired.memberAddMode !== null) {
    if (desired.memberAddMode !== cur.memberAddMode) {
      await s.groupMemberAddMode(gid, desired.memberAddMode ? "all_member_add" : "admin_add").catch(() => {});
      changes.push(`➕ Add Members: ${desired.memberAddMode ? "All Members" : "Admins Only"}`);
      await new Promise((r) => setTimeout(r, 600));
    } else { skipped.push(`➕ Add Members already: ${cur.memberAddMode ? "All Members" : "Admins Only"}`); }
  }

  return { changes, skipped };
}

// ─── Rename Group ─────────────────────────────────────────────────────────
async function renameGroup(index, gid, newName) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  await s.groupUpdateSubject(gid, newName);
}

// ─── Add Members to Group ─────────────────────────────────────────────────
// FIX: Better status code parsing — Baileys can return number or string
async function addMembersToGroup(index, gid, phones, oneByOne = false) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");

  // FIX: Deduplicate and clean numbers properly
  const jids = [...new Set(
    phones
      .map((n) => n.replace(/[^0-9]/g, ""))
      .filter((n) => n.length >= 7)
      .map((n) => `${n}@s.whatsapp.net`)
  )];

  const results = { added: 0, failed: 0, skipped: 0, failedNums: [] };

  const parseStatus = (st) => String(st ?? "200").trim();

  if (oneByOne) {
    for (const jid of jids) {
      try {
        const res = await s.groupParticipantsUpdate(gid, [jid], "add");
        const status = parseStatus(res?.[0]?.status);
        if (status === "200" || status === "201") results.added++;
        else if (status === "403" || status === "409") results.skipped++;
        else { results.failed++; results.failedNums.push(jidToPhone(jid)); }
      } catch {
        results.failed++; results.failedNums.push(jidToPhone(jid));
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

// ─── Get Group Info from Invite Link ──────────────────────────────────────
async function getGroupInfoFromLink(index, code) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  try {
    const info = await s.groupGetInviteInfo(code);
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
};
