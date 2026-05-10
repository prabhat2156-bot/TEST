/**
 * WhatsApp Manager — Fixed & Updated
 * Compatible with @whiskeysockets/baileys 6.x
 *
 * Key fixes:
 *  - groupMetadata().participants with .members fallback
 *  - groupRequestParticipantsList with pendingParticipants fallback
 *  - extractPendingJid handles .jid / .id / .participant across versions
 *  - getGroupInfoFromLink cleans code before calling groupGetInviteInfo
 *  - ENHANCED: Better pending entry detection with nested field support
 *  - ENHANCED: Improved JID digit extraction with proper null handling
 *  - ENHANCED: Debug logging to track number detection issues
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
const accounts = [
  { index: 0, socket: null, status: "disconnected", phoneNumber: "" },
];

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
function getSocket(index = 0)  {
  const acc = accounts[index];
  if (!acc?.socket || acc.status !== "connected") return null;
  return acc.socket;
}

// ─── JID Normalization ────────────────────────────────────────────────────
// WhatsApp JIDs can have a device suffix: "919876543210:5@s.whatsapp.net"
// Normalise to "919876543210@s.whatsapp.net" for comparison
function normJid(jid) {
  return (jid || "").replace(/:\d+@/, "@").toLowerCase().trim();
}

// ─── Enhanced JID Extraction ──────────────────────────────────────────────
// Baileys versions differ in which field holds the JID inside pending entries.
// Try every known field name AND nested structures.
function extractPendingJid(entry) {
  if (!entry) return null;
  
  // Try primary fields first
  let jid = entry.jid 
    || entry.participant 
    || entry.id 
    || entry.requester
    || entry.phoneNumber
    || null;

  // If still no JID, try nested structures
  if (!jid && typeof entry === 'object') {
    // Try user object
    if (entry.user?.id) jid = entry.user.id;
    else if (entry.user?.jid) jid = entry.user.jid;
    // Try additional nested fields
    else if (entry.requester?.jid) jid = entry.requester.jid;
    else if (entry.requester?.id) jid = entry.requester.id;
  }

  // Validate that extracted value looks like a JID
  if (jid && typeof jid === 'string') {
    jid = jid.trim();
    // Check if it contains @ (WhatsApp JID format)
    if (jid.includes('@')) {
      return jid;
    }
    // If it's pure digits, convert to proper format
    if (/^\d+$/.test(jid)) {
      return `${jid}@s.whatsapp.net`;
    }
  }

  return null;
}

// ─── Group Metadata Helper ────────────────────────────────────────────────
// Safely extract participants array from metadata regardless of field name.
function getParticipants(meta) {
  return meta?.participants || meta?.members || [];
}

// ─── Pending Requests with Fallback ──────────────────────────────────────
// Primary  : s.groupRequestParticipantsList(gid)           — Baileys 6.x
// Fallback : meta.pendingParticipants / membershipApprovalRequests
async function fetchPendingList(socket, gid) {
  // 1) primary API
  try {
    const list = await socket.groupRequestParticipantsList(gid);
    if (Array.isArray(list)) return list;
  } catch (_) {}

  // 2) fallback — pull from groupMetadata
  try {
    const meta = await socket.groupMetadata(gid);
    const fb =
      meta.pendingParticipants ||
      meta.membershipApprovalRequests ||
      meta.requestedParticipants ||
      [];
    return fb;
  } catch (_) {}

  return [];
}

// ─── Helper: Extract digits from JID ──────────────────────────────────────
function jidDigits(jid) {
  if (!jid || typeof jid !== 'string') return "";
  return jid.replace(/[^0-9]/g, "").trim();
}

// ─── Auto-Accept State ────────────────────────────────────────────────────
const autoAcceptGroups = new Map(); // gid → { accepted: 0 }
let autoAcceptTimer = null;

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
        // Accept ALL pending requests — no filter by request method
        const pending = await fetchPendingList(s, gid);
        const jids = (pending || []).map(extractPendingJid).filter(Boolean);
        if (jids.length) {
          await s
            .groupRequestParticipantsUpdate(gid, jids, "approve")
            .catch(() => {});
          const rec = autoAcceptGroups.get(gid);
          if (rec) rec.accepted += jids.length;
        }
      } catch (_) {}
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
  groupIds.forEach((gid) => {
    result[gid] = autoAcceptGroups.get(gid) || { accepted: 0 };
  });
  return result;
}

// ─── Connect ───────────────────────────────────────────────────────────
async function connectAccount(index, phoneNumber, freshStart = true) {
  const acc = accounts[index];
  if (!acc) throw new Error("Invalid account index");
  if (acc.socket) {
    try { acc.socket.end(undefined); } catch (_) {}
    acc.socket = null;
  }

  const accountId = `account${index + 1}`;
  if (freshStart) {
    await clearMongoAuth(accountId);
    await AccountInfo.findOneAndUpdate(
      { accountIndex: index },
      { accountIndex: index, phoneNumber, hasAuth: false },
      { upsert: true }
    );
  }

  acc.status = "connecting";
  acc.phoneNumber = phoneNumber;
  const { state, saveCreds } = await useMongoAuthState(accountId);
  const { version } = await fetchLatestBaileysVersion();

  const socket = makeWASocket({
    version,
    logger,
    auth: state,
    printQRInTerminal: false,
    browser: ["Ubuntu", "Chrome", "120.0.0.0"],
    syncFullHistory: false,
    generateHighQualityLinkPreview: false,
    connectTimeoutMs: 60000,
    defaultQueryTimeoutMs: 60000,
    keepAliveIntervalMs: 15000,
    markOnlineOnConnect: false,
  });

  acc.socket = socket;
  socket.ev.on("creds.update", saveCreds);
  const clean = phoneNumber.replace(/[^0-9]/g, "");
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
        acc.phoneNumber = "";
        await onDisconnected(index);
      } else if (acc.phoneNumber) {
        setTimeout(
          () =>
            connectAccount(index, acc.phoneNumber, false).catch(console.error),
          5000
        );
      }
    }
  });
}

async function _requestPairingWithRetry(socket, index, clean, attempt = 1) {
  try {
    const code = await socket.requestPairingCode(clean);
    if (code) {
      const formatted =
        code
          .replace(/[^A-Z0-9]/gi, "")
          .match(/.{1,4}/g)
          ?.join("-") ?? code;
      await onPairingCode(index, formatted);
    }
  } catch (_) {
    if (attempt < 3) {
      await new Promise((r) => setTimeout(r, 4000));
      await _requestPairingWithRetry(socket, index, clean, attempt + 1);
    } else {
      await onPairingCode(index, null);
    }
  }
}

async function disconnectAccount(index = 0) {
  const acc = accounts[index];
  if (!acc) return;
  if (acc.socket) {
    try { acc.socket.end(undefined); } catch (_) {}
    acc.socket = null;
  }
  const accountId = `account${index + 1}`;
  await clearMongoAuth(accountId);
  await AccountInfo.findOneAndUpdate(
    { accountIndex: index },
    { accountIndex: index, phoneNumber: "", hasAuth: false },
    { upsert: true }
  );
  acc.status = "disconnected";
  acc.phoneNumber = "";
}

async function reconnectSavedAccounts() {
  const saved = await AccountInfo.find({ hasAuth: true, accountIndex: 0 });
  if (!saved.length) return;
  await connectAccount(saved[0].accountIndex, saved[0].phoneNumber, false).catch(
    (e) => console.error("[Startup]", e.message)
  );
}

// ─── Group Creation ───────────────────────────────────────────────────────
async function createGroup(index, name, jids) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  return await s.groupCreate(name, jids);
}

async function updateGroupDescription(index, gid, desc) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  await s.groupUpdateDescription(gid, desc);
}

async function updateGroupPhoto(index, gid, buf) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  await s.updateProfilePicture(gid, buf);
}

async function setDisappearingMessages(index, gid, sec) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  await s.groupToggleEphemeral(gid, sec);
}

async function promoteToAdmin(index, gid, jids) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  await s.groupParticipantsUpdate(gid, jids, "promote");
}

async function getGroupInviteLink(index, gid) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  const c = await s.groupInviteCode(gid);
  return `https://chat.whatsapp.com/${c}`;
}

async function joinGroupViaLink(index, code) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  const cleanCode = code.replace(/.*chat\.whatsapp\.com\//i, "").trim();
  return await s.groupAcceptInvite(cleanCode);
}

async function setGroupPermissions(index, gid, p) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  await s
    .groupSettingUpdate(gid, p.sendMessages ? "not_announcement" : "announcement")
    .catch(() => {});
  await s
    .groupSettingUpdate(gid, p.editInfo ? "unlocked" : "locked")
    .catch(() => {});
  await s
    .groupMemberAddMode(gid, p.addMembers ? "all_member_add" : "admin_add")
    .catch(() => {});
  await s
    .groupJoinApprovalMode(gid, p.approveMembers ? "on" : "off")
    .catch(() => {});
}

// ─── Fetch All Groups ──────────────────────────────────────────────────────
async function getAllGroupsWithDetails(index) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  const groups = await s.groupFetchAllParticipating();
  return Object.entries(groups).map(([id, g]) => ({
    id,
    name: g.subject || id,
    participantCount: getParticipants(g).length,
    participants: getParticipants(g),
    announce: g.announce ?? false,
    restrict: g.restrict ?? false,
    joinApprovalMode: g.joinApprovalMode,
    memberAddMode: g.memberAddMode,
  }));
}

// ─── Leave Group ──────────────────────────────────────────────────────────
async function leaveGroup(index, gid) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  await s.groupLeave(gid);
}

// ─── Remove All Non-Admin Members ─────────────────────────────────────────
async function removeAllMembers(index, gid) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  const meta = await s.groupMetadata(gid);
  const myJid = normJid(s.user?.id || "");
  const participants = getParticipants(meta);
  const toRm = participants
    .filter((p) => !p.admin && normJid(p.id) !== myJid)
    .map((p) => p.id);
  if (!toRm.length) return 0;
  for (let i = 0; i < toRm.length; i += 5) {
    await s
      .groupParticipantsUpdate(gid, toRm.slice(i, i + 5), "remove")
      .catch(() => {});
    await new Promise((r) => setTimeout(r, 1000));
  }
  return toRm.length;
}

// ─── Make Admin ───────────────────────────────────────────────────────────
// Logic (per number):
//   Step 1 — Search group members list (compare pure digits only)
//             If found → promote directly
//   Step 2 — If not in members, search pending list ONE BY ONE (compare digits)
//             If found → approve that one entry → wait 6s → promote
async function makeAdminByNumbers(index, gid, phones) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  let promoted = 0;

  for (const phone of phones) {
    const digits = phone.replace(/[^0-9]/g, "").trim();
    if (!digits || digits.length < 7) {
      console.log(`⚠️ [makeAdmin] Invalid phone: ${phone} (too short)`);
      continue;
    }

    try {
      // ── Step 1: Check group members ─────────────────────────────────
      const meta = await s.groupMetadata(gid);
      const participants = getParticipants(meta);
      
      console.log(`🔍 [makeAdmin] Searching for ${digits} in ${participants.length} members`);

      const member = participants.find((p) => {
        const memberDigits = jidDigits(p.id);
        return memberDigits === digits;
      });

      if (member) {
        // Found in members → promote directly
        console.log(`✅ [makeAdmin] Found ${digits} in members as: ${member.id}`);
        await s
          .groupParticipantsUpdate(gid, [member.id], "promote")
          .catch((e) => console.error("[makeAdmin] promote error:", e.message));
        promoted++;
        console.log(`✅ [makeAdmin] Promoted ${digits} from members`);
      } else {
        // ── Step 2: Check pending list one by one ──────────────────────
        console.log(`🔎 [makeAdmin] ${digits} NOT in members, checking pending list...`);
        let pendingJid = null;
        try {
          const pendingList = await fetchPendingList(s, gid);
          console.log(`📋 [makeAdmin] Found ${pendingList?.length || 0} pending requests`);
          
          // Loop through each pending entry individually
          for (let i = 0; i < (pendingList || []).length; i++) {
            const entry = pendingList[i];
            const pJid = extractPendingJid(entry);
            
            if (!pJid) {
              console.log(`⚠️ [makeAdmin] Pending entry #${i} has no extractable JID:`, JSON.stringify(entry));
              continue;
            }

            const pendingDigits = jidDigits(pJid);
            console.log(`  [DEBUG] Pending #${i}: JID=${pJid}, digits=${pendingDigits}`);
            
            if (pendingDigits === digits) {
              pendingJid = pJid;
              console.log(`🎯 [makeAdmin] Found match! ${digits} in pending as: ${pJid}`);
              break;
            }
          }
        } catch (e) {
          console.error(`❌ [makeAdmin] Error fetching pending:`, e.message);
        }

        if (pendingJid) {
          // Found in pending → approve first, then promote
          console.log(`🔄 [makeAdmin] Approving ${digits} (${pendingJid})...`);
          await s
            .groupRequestParticipantsUpdate(gid, [pendingJid], "approve")
            .catch((e) => console.error(`[makeAdmin] Approve error:`, e.message));

          // Wait for them to actually join
          console.log(`⏳ [makeAdmin] Waiting 6 seconds for ${digits} to join...`);
          await new Promise((r) => setTimeout(r, 6000));

          // Re-fetch metadata to get the actual joined JID (may have device suffix)
          try {
            const newMeta = await s.groupMetadata(gid);
            const newParticipants = getParticipants(newMeta);
            const newMember = newParticipants.find((p) => jidDigits(p.id) === digits);
            
            if (newMember) {
              console.log(`✅ [makeAdmin] ${digits} joined! Found as: ${newMember.id}`);
            } else {
              console.log(`⚠️ [makeAdmin] ${digits} not yet in members after approval`);
            }

            const jidToUse = newMember ? newMember.id : pendingJid;
            await s
              .groupParticipantsUpdate(gid, [jidToUse], "promote")
              .catch((e) =>
                console.error("[makeAdmin] post-approve promote error:", e.message)
              );
            promoted++;
            console.log(`✅ [makeAdmin] Promoted ${digits} after approve`);
          } catch (e) {
            console.error("[makeAdmin] post-approve metadata error:", e.message);
          }
        } else {
          console.log(`❓ [makeAdmin] ${digits} NOT FOUND in members OR pending`);
        }
      }
    } catch (e) {
      console.error("[makeAdmin] error for", digits, ":", e.message);
    }

    // Small delay between each number
    await new Promise((r) => setTimeout(r, 800));
  }

  console.log(`📊 [makeAdmin] Total promoted: ${promoted}/${phones.length}`);
  return promoted;
}

// ─── Demote Admin ──────────────────────────────────────────────────────────
async function demoteAdminInGroup(index, gid, phones) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  let demoted = 0;

  for (const phone of phones) {
    const digits = phone.replace(/[^0-9]/g, "");
    if (!digits || digits.length < 7) continue;
    const baseJid = `${digits}@s.whatsapp.net`;

    try {
      const meta = await s.groupMetadata(gid);
      const participants = getParticipants(meta);
      const member = participants.find(
        (p) => normJid(p.id) === normJid(baseJid)
      );
      if (!member) continue;
      const isAdmin =
        member.admin === "admin" ||
        member.admin === "superadmin" ||
        member.admin === true;
      if (!isAdmin) continue;
      await s
        .groupParticipantsUpdate(gid, [member.id], "demote")
        .catch((e) => console.error("[demoteAdmin] error:", e.message));
      demoted++;
    } catch (e) {
      console.error("[demoteAdmin] error for", digits, ":", e.message);
    }

    await new Promise((r) => setTimeout(r, 600));
  }

  return demoted;
}

// ─── Approval ──────────────────────────────────────────────────────────────
async function getGroupApprovalStatus(index, gid) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  const meta = await s.groupMetadata(gid);
  return meta.joinApprovalMode === "on" || meta.joinApprovalMode === true;
}

async function setGroupApproval(index, gid, enable) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  await s.groupJoinApprovalMode(gid, enable ? "on" : "off");
}

// ─── Approve All Pending ───────────────────────────────────────────────────
async function approveAllPending(index, gid) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  const metaBefore = await s.groupMetadata(gid);
  const beforeCount = getParticipants(metaBefore).length;

  const pending = await fetchPendingList(s, gid);
  const pendingJids = (pending ?? [])
    .map((p) => extractPendingJid(p))
    .filter(Boolean);

  if (!pendingJids.length) {
    return {
      pendingCount: 0,
      approved: 0,
      failed: 0,
      beforeCount,
      afterCount: beforeCount,
    };
  }

  let approved = 0, failed = 0;
  for (let i = 0; i < pendingJids.length; i += 20) {
    const batch = pendingJids.slice(i, i + 20);
    try {
      await s.groupRequestParticipantsUpdate(gid, batch, "approve");
      approved += batch.length;
    } catch (_) {
      for (const jid of batch) {
        try {
          await s.groupRequestParticipantsUpdate(gid, [jid], "approve");
          approved++;
        } catch (_2) {
          failed++;
        }
        await new Promise((r) => setTimeout(r, 400));
      }
    }
    await new Promise((r) => setTimeout(r, 1500));
  }

  await new Promise((r) => setTimeout(r, 3000));
  let afterCount = beforeCount;
  try {
    const metaAfter = await s.groupMetadata(gid);
    afterCount = getParticipants(metaAfter).length ?? beforeCount;
  } catch (_) {}

  return {
    pendingCount: pendingJids.length,
    approved,
    failed,
    actuallyJoined: afterCount - beforeCount,
    beforeCount,
    afterCount,
  };
}

// ─── Members & Pending Lists ───────────────────────────────────────────────
async function getGroupMembers(index, gid) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  const meta = await s.groupMetadata(gid);
  return getParticipants(meta).map((p) => ({
    jid: p.id,
    phone: normJid(p.id).replace("@s.whatsapp.net", ""),
    admin: p.admin || null,
  }));
}

async function getGroupPendingRequests(index, gid) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  const list = await fetchPendingList(s, gid);
  return list.map((p) => {
    const jid = extractPendingJid(p);
    return { jid, phone: normJid(jid || "").replace("@s.whatsapp.net", "") };
  }).filter((p) => p.jid);
}

// ─── Get Pending for CTC Checker ──────────────────────────────────────────
async function getPendingForGroup(index, gid) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  const list = await fetchPendingList(s, gid);
  return list.map((p) => {
    const jid = extractPendingJid(p);
    return { jid, phone: normJid(jid || "").replace("@s.whatsapp.net", "") };
  }).filter((p) => p.jid);
}

// ─── Reset Invite Link ─────────────────────────────────────────────────────
async function resetGroupInviteLink(index, gid) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  await s.groupRevokeInvite(gid);
  await new Promise((r) => setTimeout(r, 1200));
  const code = await s.groupInviteCode(gid);
  return `https://chat.whatsapp.com/${code}`;
}

// ─── Group Settings ───────────────────────────────────────────────────────
async function getGroupSettings(index, gid) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  const meta = await s.groupMetadata(gid);
  return {
    announce: meta.announce === true,
    restrict: meta.restrict === true,
    joinApproval:
      meta.joinApprovalMode === "on" || meta.joinApprovalMode === true,
    memberAddMode: meta.memberAddMode === "all_member_add",
  };
}

async function applyGroupSettings(index, gid, desired) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  const cur = await getGroupSettings(index, gid);
  const changes = [], skipped = [];

  if (desired.announce !== undefined && desired.announce !== null) {
    if (desired.announce !== cur.announce) {
      await s
        .groupSettingUpdate(
          gid,
          desired.announce ? "announcement" : "not_announcement"
        )
        .catch(() => {});
      changes.push(`💬 Messages: ${desired.announce ? "Admins Only" : "All Members"}`);
      await new Promise((r) => setTimeout(r, 600));
    } else {
      skipped.push(
        `💬 Messages already: ${cur.announce ? "Admins Only" : "All Members"}`
      );
    }
  }

  if (desired.restrict !== undefined && desired.restrict !== null) {
    if (desired.restrict !== cur.restrict) {
      await s
        .groupSettingUpdate(gid, desired.restrict ? "locked" : "unlocked")
        .catch(() => {});
      changes.push(`✏️ Edit Info: ${desired.restrict ? "Admins Only" : "All Members"}`);
      await new Promise((r) => setTimeout(r, 600));
    } else {
      skipped.push(
        `✏️ Edit Info already: ${cur.restrict ? "Admins Only" : "All Members"}`
      );
    }
  }

  if (desired.joinApproval !== undefined && desired.joinApproval !== null) {
    if (desired.joinApproval !== cur.joinApproval) {
      await s
        .groupJoinApprovalMode(gid, desired.joinApproval ? "on" : "off")
        .catch(() => {});
      changes.push(`🔐 Join Approval: ${desired.joinApproval ? "On" : "Off"}`);
      await new Promise((r) => setTimeout(r, 600));
    } else {
      skipped.push(
        `🔐 Join Approval already: ${cur.joinApproval ? "On" : "Off"}`
      );
    }
  }

  if (desired.memberAddMode !== undefined && desired.memberAddMode !== null) {
    if (desired.memberAddMode !== cur.memberAddMode) {
      await s
        .groupMemberAddMode(
          gid,
          desired.memberAddMode ? "all_member_add" : "admin_add"
        )
        .catch(() => {});
      changes.push(
        `➕ Add Members: ${desired.memberAddMode ? "All Members" : "Admins Only"}`
      );
      await new Promise((r) => setTimeout(r, 600));
    } else {
      skipped.push(
        `➕ Add Members already: ${cur.memberAddMode ? "All Members" : "Admins Only"}`
      );
    }
  }

  return { changes, skipped };
}

// ─── Rename Group ─────────────────────────────────────────────────────────
async function renameGroup(index, gid, newName) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  await s.groupUpdateSubject(gid, newName);
}

// ─── Add Members ──────────────────────────────────────────────────────────
async function addMembersToGroup(index, gid, phones, oneByOne = false) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  const jids = [
    ...new Set(
      phones.map((n) => `${n.replace(/[^0-9]/g, "")}@s.whatsapp.net`)
    ),
  ];
  const results = { added: 0, failed: 0, skipped: 0, failedNums: [] };

  if (oneByOne) {
    for (const jid of jids) {
      try {
        const res = await s.groupParticipantsUpdate(gid, [jid], "add");
        const status = String(res?.[0]?.status ?? "200");
        if (status === "200") results.added++;
        else if (status === "403" || status === "409") results.skipped++;
        else {
          results.failed++;
          results.failedNums.push(normJid(jid).replace("@s.whatsapp.net", ""));
        }
      } catch (_) {
        results.failed++;
        results.failedNums.push(normJid(jid).replace("@s.whatsapp.net", ""));
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
            else {
              results.failed++;
              results.failedNums.push(
                batch[idx]?.replace("@s.whatsapp.net", "") || ""
              );
            }
          });
        } else {
          results.added += batch.length;
        }
      } catch (_) {
        results.failed += batch.length;
        batch.forEach((j) =>
          results.failedNums.push(normJid(j).replace("@s.whatsapp.net", ""))
        );
      }
      await new Promise((r) => setTimeout(r, 2000));
    }
  }
  return results;
}

// ─── Get Group Info from Invite Link ──────────────────────────────────────
async function getGroupInfoFromLink(index, code) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  // Strip URL prefix — keep only the invite code itself
  const cleanCode = code.replace(/.*chat\.whatsapp\.com\//i, "").trim();
  try {
    const info = await s.groupGetInviteInfo(cleanCode);
    return {
      id: info.id,
      name: info.subject || info.name || cleanCode,
      participants: getParticipants(info),
    };
  } catch (_) {
    return null;
  }
}

module.exports = {
  setCallbacks,
  getStatus,
  getPhone,
  getConnectedCount,
  connectAccount,
  disconnectAccount,
  reconnectSavedAccounts,
  createGroup,
  updateGroupDescription,
  updateGroupPhoto,
  setDisappearingMessages,
  promoteToAdmin,
  setGroupPermissions,
  getGroupInviteLink,
  joinGroupViaLink,
  getAllGroupsWithDetails,
  leaveGroup,
  removeAllMembers,
  makeAdminByNumbers,
  getGroupApprovalStatus,
  setGroupApproval,
  approveAllPending,
  getGroupMembers,
  getGroupPendingRequests,
  resetGroupInviteLink,
  demoteAdminInGroup,
  getGroupSettings,
  applyGroupSettings,
  renameGroup,
  addMembersToGroup,
  getGroupInfoFromLink,
  getPendingForGroup,
  startAutoAcceptForGroups,
  stopAutoAcceptForGroups,
  getAutoAcceptStats,
};
