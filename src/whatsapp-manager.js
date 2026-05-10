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
// WhatsApp JIDs can have device suffix e.g. "919876543210:5@s.whatsapp.net"
// Normalize to "919876543210@s.whatsapp.net" for comparison
function normJid(jid) {
  return (jid || "").replace(/:\d+@/, "@").toLowerCase().trim();
}

// ─── Auto Accept State (polling-based, reliable) ──────────────────────────
const autoAcceptGroups = new Map(); // gid -> { accepted: 0 }
let autoAcceptTimer = null;

// Helper: extract JID from a pending entry (Baileys field varies by version)
function getPendingJid(entry) {
  return entry.jid || entry.participant || entry.id || null;
}

// Helper: check if pending request came from an invite link
// WhatsApp pending entry has a "method" field:
//   "invite_link"   → joined via group invite link  ← we want ONLY this
//   "non_admin_add" → added by another member
//   "linked_group_join" → linked group
// IMPORTANT: Agar method field missing hai (purani Baileys) toh REJECT karo — approve mat karo
// kyunki method nahi pata toh safe nahi hai sab ko approve karna
function isInviteLinkRequest(entry) {
  const method = entry.method || entry.requestMethod || null;
  // Sirf explicitly "invite_link" wale approve hone chahiye
  // Missing method = unknown source = SKIP (approve nahi karna)
  return method === "invite_link";
}

function startAutoAcceptForGroups(groupIds, index = 0) {
  groupIds.forEach((gid) => autoAcceptGroups.set(gid, { accepted: 0 }));
  if (autoAcceptTimer) return; // already polling
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
        // ✅ FIX: Only approve "From invite link" requests — not contacts or "added by others"
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
  }, 8000); // poll every 8 seconds
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

// ─── Helper: extract JID string safely from pending list entry ────────────
// Baileys versions differ: field may be .jid or .participant or .id
function extractPendingJid(entry) {
  return entry.jid || entry.participant || entry.id || null;
}

// ─── Make Admin — exact WhatsApp flow ─────────────────────────────────────
// For each number:
//   1. Check if already in group → if yes, promote directly
//   2. If not in group → check pending requests → approve → wait → promote
async function makeAdminByNumbers(index, gid, phones) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  let promoted = 0;

  for (const phone of phones) {
    const digits  = phone.replace(/[^0-9]/g, "");
    if (!digits || digits.length < 7) continue;
    const baseJid = `${digits}@s.whatsapp.net`;

    try {
      // ── Step 1: Fetch fresh metadata and look for this number in group ──
      const meta   = await s.groupMetadata(gid);
      const member = meta.participants.find(
        (p) => normJid(p.id) === normJid(baseJid)
      );

      if (member) {
        // ✅ Already in group → promote using their actual JID (with device suffix)
        await s.groupParticipantsUpdate(gid, [member.id], "promote").catch((e) =>
          console.error("[makeAdmin] promote error:", e.message)
        );
        promoted++;
      } else {
        // ── Step 2: Not in group → check pending requests ──
        let pendingJid = null;
        try {
          const pendingList = await s.groupRequestParticipantsList(gid);
          const match = (pendingList || []).find(
            (p) => normJid(extractPendingJid(p)) === normJid(baseJid)
          );
          if (match) pendingJid = extractPendingJid(match);
        } catch {}

        if (pendingJid) {
          // ✅ Found in pending → approve first
          await s.groupRequestParticipantsUpdate(gid, [pendingJid], "approve").catch(() => {});

          // Wait for them to actually join (6 seconds)
          await new Promise((r) => setTimeout(r, 6000));

          // Re-fetch metadata to get their real JID (may have device suffix now)
          try {
            const newMeta   = await s.groupMetadata(gid);
            const newMember = newMeta.participants.find(
              (p) => normJid(p.id) === normJid(baseJid)
            );
            const jidToUse  = newMember ? newMember.id : pendingJid;
            await s.groupParticipantsUpdate(gid, [jidToUse], "promote").catch((e) =>
              console.error("[makeAdmin] promote-after-approve error:", e.message)
            );
            promoted++;
          } catch (e) {
            console.error("[makeAdmin] post-approve error:", e.message);
          }
        }
        // else: not in group and not in pending → skip this number
      }
    } catch (e) {
      console.error("[makeAdmin] error for", digits, ":", e.message);
    }

    // Small delay between each number
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
    // FIX: p.jid field Baileys version pe depend karta hai — p.participant ya p.id bhi ho sakta hai
    pendingJids = (pending ?? []).map((p) => p.jid || p.participant || p.id || null).filter(Boolean);
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
    jid: p.id,
    phone: normJid(p.id).replace("@s.whatsapp.net", ""),
    admin: p.admin || null,
  }));
}

// ─── Get Pending Requests ─────────────────────────────────────────────────
async function getGroupPendingRequests(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  try {
    const list = await s.groupRequestParticipantsList(gid);
    return (list ?? []).map((p) => {
      // FIX: Baileys version ke hisaab se field alag hoti hai — p.jid, p.participant, ya p.id
      const jid = p.jid || p.participant || p.id || null;
      if (!jid) return null;
      return { jid, phone: normJid(jid).replace("@s.whatsapp.net", "") };
    }).filter(Boolean);
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

// ─── Demote Admin — exact WhatsApp flow ──────────────────────────────────
// For each number:
//   1. Search them in group participants
//   2. If found AND they are admin → demote (Dismiss as Admin)
//   3. If not found or not an admin → skip
async function demoteAdminInGroup(index, gid, phones) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  let demoted = 0;

  for (const phone of phones) {
    const digits  = phone.replace(/[^0-9]/g, "");
    if (!digits || digits.length < 7) continue;
    const baseJid = `${digits}@s.whatsapp.net`;

    try {
      // ── Step 1: Fetch fresh metadata and search for this number ──
      const meta   = await s.groupMetadata(gid);
      const member = meta.participants.find(
        (p) => normJid(p.id) === normJid(baseJid)
      );

      if (!member) {
        // Not in group at all — skip
        continue;
      }

      // ── Step 2: Check if they are admin (admin = "admin" or "superadmin") ──
      const isAdmin = member.admin === "admin" || member.admin === "superadmin" || member.admin === true;
      if (!isAdmin) {
        // In group but not an admin — skip
        continue;
      }

      // ── Step 3: Demote using their actual JID (with device suffix if present) ──
      await s.groupParticipantsUpdate(gid, [member.id], "demote").catch((e) =>
        console.error("[demoteAdmin] demote error:", e.message)
      );
      demoted++;
    } catch (e) {
      console.error("[demoteAdmin] error for", digits, ":", e.message);
    }

    // Small delay between each number
    await new Promise((r) => setTimeout(r, 600));
  }

  return demoted;
}

// ─── Get Group Settings ───────────────────────────────────────────────────
async function getGroupSettings(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  const meta = await s.groupMetadata(gid);
  return {
    // announce=true means ONLY admins can send (group is in "announcement" mode)
    announce:      meta.announce === true,
    // restrict=true means ONLY admins can edit group info
    restrict:      meta.restrict === true,
    // joinApproval=true means approval required to join
    joinApproval:  meta.joinApprovalMode === "on" || meta.joinApprovalMode === true,
    // memberAddMode=true means ALL members can add others (not just admins)
    memberAddMode: meta.memberAddMode === "all_member_add",
  };
}

// ─── Apply Group Settings (skip if already matching) ─────────────────────
async function applyGroupSettings(index, gid, desired) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  const cur = await getGroupSettings(index, gid);
  const changes = [], skipped = [];

  // announce: true = admins only send | false = all members send
  if (desired.announce !== undefined && desired.announce !== null) {
    if (desired.announce !== cur.announce) {
      await s.groupSettingUpdate(gid, desired.announce ? "announcement" : "not_announcement").catch(() => {});
      changes.push(`💬 Messages: ${desired.announce ? "Admins Only" : "All Members"}`);
      await new Promise((r) => setTimeout(r, 600));
    } else {
      skipped.push(`💬 Messages already: ${cur.announce ? "Admins Only" : "All Members"}`);
    }
  }

  // restrict: true = admins only edit | false = all can edit
  if (desired.restrict !== undefined && desired.restrict !== null) {
    if (desired.restrict !== cur.restrict) {
      await s.groupSettingUpdate(gid, desired.restrict ? "locked" : "unlocked").catch(() => {});
      changes.push(`✏️ Edit Info: ${desired.restrict ? "Admins Only" : "All Members"}`);
      await new Promise((r) => setTimeout(r, 600));
    } else {
      skipped.push(`✏️ Edit Info already: ${cur.restrict ? "Admins Only" : "All Members"}`);
    }
  }

  // joinApproval: true = approval required | false = direct join
  if (desired.joinApproval !== undefined && desired.joinApproval !== null) {
    if (desired.joinApproval !== cur.joinApproval) {
      await s.groupJoinApprovalMode(gid, desired.joinApproval ? "on" : "off").catch(() => {});
      changes.push(`🔐 Join Approval: ${desired.joinApproval ? "On" : "Off"}`);
      await new Promise((r) => setTimeout(r, 600));
    } else {
      skipped.push(`🔐 Join Approval already: ${cur.joinApproval ? "On" : "Off"}`);
    }
  }

  // memberAddMode: true = all members can add | false = admins only
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

// ─── Get Pending for CTC Checker ─────────────────────────────────────────
async function getPendingForGroup(index, gid) {
  const s = getSocket(index); if (!s) throw new Error("Not connected!");
  try {
    const pending = await s.groupRequestParticipantsList(gid);
    return (pending ?? []).map((p) => {
      // FIX: Baileys version ke hisaab se field alag hoti hai
      const jid = p.jid || p.participant || p.id || null;
      if (!jid) return null;
      return { jid, phone: normJid(jid).replace("@s.whatsapp.net", "") };
    }).filter(Boolean);
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
