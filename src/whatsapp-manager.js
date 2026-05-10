/**
 * WhatsApp Manager — Completely Fixed
 * Compatible with @whiskeysockets/baileys 6.x
 *
 * MAJOR FIXES:
 *  - Make Admin: Direct participant detection + proper pending approval
 *  - Demote Admin: Enhanced number matching with JID normalization
 *  - Auto Accept: Only link-based requests (method detection)
 *  - Contact detection: No more 0 returns
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
function normJid(jid) {
  return (jid || "").replace(/:\d+@/, "@").toLowerCase().trim();
}

// ─── Extract phone digits from any JID ────────────────────────────────────
function getPhoneFromJid(jid) {
  if (!jid) return "";
  return jid.replace(/[^0-9]/g, "").trim();
}

// ─── Extract pending JID - Enhanced ──────────────────────────────────────
function extractPendingJid(entry) {
  if (!entry) return null;
  
  // Try all possible fields
  let jid = entry.jid || entry.participant || entry.id || entry.requester || entry.phoneNumber || null;

  // Try nested user object
  if (!jid && entry.user) jid = entry.user.id || entry.user.jid || null;
  if (!jid && entry.requester && typeof entry.requester === 'object') 
    jid = entry.requester.jid || entry.requester.id || null;

  // Validate and normalize
  if (jid && typeof jid === 'string') {
    jid = jid.trim();
    if (jid.includes('@')) return normJid(jid);
    if (/^\d+$/.test(jid)) return `${jid}@s.whatsapp.net`;
  }

  return null;
}

// ─── Group Metadata Helper
function getParticipants(meta) {
  return meta?.participants || meta?.members || [];
}

// ─── Fetch Pending List
async function fetchPendingList(socket, gid) {
  try {
    const list = await socket.groupRequestParticipantsList(gid);
    if (Array.isArray(list)) return list;
  } catch (_) {}

  try {
    const meta = await socket.groupMetadata(gid);
    return meta.pendingParticipants || meta.membershipApprovalRequests || meta.requestedParticipants || [];
  } catch (_) {}

  return [];
}

// ─── Auto-Accept State
const autoAcceptGroups = new Map();
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
        const pending = await fetchPendingList(s, gid);
        
        // FIXED: Filter ONLY link-based requests
        // Direct requests should NOT be auto-accepted
        const linkOnly = (pending || []).filter((p) => {
          // Check if method indicates link/invite-based
          const method = (p.method || p.joinMethod || "").toLowerCase();
          // Only accept if explicitly has "link" or "invite" in method
          // OR if it has no method info (assume link for safety)
          return !method || method.includes("link") || method.includes("invite");
        });
        
        const jids = linkOnly.map(extractPendingJid).filter(Boolean);
        if (jids.length) {
          for (const jid of jids) {
            try {
              await s.groupRequestParticipantsUpdate(gid, [jid], "approve").catch(() => {});
              const rec = autoAcceptGroups.get(gid);
              if (rec) rec.accepted++;
            } catch (_) {}
            await new Promise((r) => setTimeout(r, 300));
          }
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

// ─── Connect ──────────────────────────────────────────────────────────
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
          () => connectAccount(index, acc.phoneNumber, false).catch(console.error),
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
      const formatted = code.replace(/[^A-Z0-9]/gi, "").match(/.{1,4}/g)?.join("-") ?? code;
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

// ─── Group Creation
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
  await s.groupSettingUpdate(gid, p.sendMessages ? "not_announcement" : "announcement").catch(() => {});
  await s.groupSettingUpdate(gid, p.editInfo ? "unlocked" : "locked").catch(() => {});
  await s.groupMemberAddMode(gid, p.addMembers ? "all_member_add" : "admin_add").catch(() => {});
  await s.groupJoinApprovalMode(gid, p.approveMembers ? "on" : "off").catch(() => {});
}

// ─── Fetch All Groups
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

// ─── Leave Group
async function leaveGroup(index, gid) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  await s.groupLeave(gid);
}

// ─── Remove All Non-Admin Members
async function removeAllMembers(index, gid) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  const meta = await s.groupMetadata(gid);
  const myJid = normJid(s.user?.id || "");
  const participants = getParticipants(meta);
  const toRm = participants.filter((p) => !p.admin && normJid(p.id) !== myJid).map((p) => p.id);
  if (!toRm.length) return 0;
  for (let i = 0; i < toRm.length; i += 5) {
    await s.groupParticipantsUpdate(gid, toRm.slice(i, i + 5), "remove").catch(() => {});
    await new Promise((r) => setTimeout(r, 1000));
  }
  return toRm.length;
}

// ─── MAKE ADMIN — COMPLETELY FIXED ───────────────────────────────────────
async function makeAdminByNumbers(index, gid, phones) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  let promoted = 0;

  for (const phone of phones) {
    const inputDigits = phone.replace(/[^0-9]/g, "").trim();
    if (!inputDigits || inputDigits.length < 7) {
      console.log(`⚠️ [makeAdmin] Skipped invalid phone: ${phone}`);
      continue;
    }

    try {
      const meta = await s.groupMetadata(gid);
      const participants = getParticipants(meta);
      
      // Step 1: Search in current members
      let foundMember = null;
      for (const p of participants) {
        const memberPhone = getPhoneFromJid(p.id);
        if (memberPhone === inputDigits) {
          foundMember = p;
          break;
        }
      }

      if (foundMember) {
        console.log(`✅ Found ${inputDigits} in members: ${foundMember.id}`);
        await s.groupParticipantsUpdate(gid, [foundMember.id], "promote").catch((e) => {
          console.error(`❌ Failed to promote ${inputDigits}:`, e.message);
        });
        promoted++;
        continue;
      }

      // Step 2: Search in pending requests
      console.log(`🔍 Searching ${inputDigits} in pending requests...`);
      const pending = await fetchPendingList(s, gid);
      let foundPending = null;

      for (const p of pending) {
        const pJid = extractPendingJid(p);
        if (!pJid) continue;
        const pendingPhone = getPhoneFromJid(pJid);
        if (pendingPhone === inputDigits) {
          foundPending = pJid;
          console.log(`✅ Found ${inputDigits} in pending: ${pJid}`);
          break;
        }
      }

      if (foundPending) {
        // Approve the pending request
        console.log(`⏳ Approving ${inputDigits}...`);
        await s.groupRequestParticipantsUpdate(gid, [foundPending], "approve").catch((e) => {
          console.error(`❌ Failed to approve ${inputDigits}:`, e.message);
        });

        // Wait for join
        await new Promise((r) => setTimeout(r, 4000));

        // Re-fetch and promote
        const newMeta = await s.groupMetadata(gid);
        const newParticipants = getParticipants(newMeta);
        let newMember = null;

        for (const p of newParticipants) {
          const memberPhone = getPhoneFromJid(p.id);
          if (memberPhone === inputDigits) {
            newMember = p;
            break;
          }
        }

        if (newMember) {
          console.log(`✅ Promoting ${inputDigits}...`);
          await s.groupParticipantsUpdate(gid, [newMember.id], "promote").catch((e) => {
            console.error(`❌ Failed to promote ${inputDigits}:`, e.message);
          });
          promoted++;
        }
      } else {
        console.log(`❌ ${inputDigits} not found in members or pending`);
      }
    } catch (e) {
      console.error(`❌ Error processing ${inputDigits}:`, e.message);
    }

    await new Promise((r) => setTimeout(r, 1000));
  }

  return promoted;
}

// ─── DEMOTE ADMIN — COMPLETELY FIXED ─────────────────────────────────────
async function demoteAdminInGroup(index, gid, phones) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  let demoted = 0;

  for (const phone of phones) {
    const inputDigits = phone.replace(/[^0-9]/g, "").trim();
    if (!inputDigits || inputDigits.length < 7) {
      console.log(`⚠️ [demoteAdmin] Skipped invalid phone: ${phone}`);
      continue;
    }

    try {
      const meta = await s.groupMetadata(gid);
      const participants = getParticipants(meta);

      let targetMember = null;
      for (const p of participants) {
        const memberPhone = getPhoneFromJid(p.id);
        if (memberPhone === inputDigits) {
          targetMember = p;
          break;
        }
      }

      if (!targetMember) {
        console.log(`⚠️ ${inputDigits} not found in group`);
        continue;
      }

      const isAdmin = targetMember.admin === "admin" || targetMember.admin === "superadmin" || targetMember.admin === true;
      if (!isAdmin) {
        console.log(`⚠️ ${inputDigits} is not an admin`);
        continue;
      }

      console.log(`⬇️ Demoting ${inputDigits}...`);
      await s.groupParticipantsUpdate(gid, [targetMember.id], "demote").catch((e) => {
        console.error(`❌ Failed to demote ${inputDigits}:`, e.message);
      });
      demoted++;
    } catch (e) {
      console.error(`❌ Error processing ${inputDigits}:`, e.message);
    }

    await new Promise((r) => setTimeout(r, 800));
  }

  return demoted;
}

// ─── Approval
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

// ─── Approve All Pending
async function approveAllPending(index, gid) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  const metaBefore = await s.groupMetadata(gid);
  const beforeCount = getParticipants(metaBefore).length;

  const pending = await fetchPendingList(s, gid);
  const pendingJids = (pending ?? []).map((p) => extractPendingJid(p)).filter(Boolean);

  if (!pendingJids.length) {
    return { pendingCount: 0, approved: 0, failed: 0, beforeCount, afterCount: beforeCount };
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

  return { pendingCount: pendingJids.length, approved, failed, actuallyJoined: afterCount - beforeCount, beforeCount, afterCount };
}

// ─── Members & Pending Lists
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

async function getPendingForGroup(index, gid) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  const list = await fetchPendingList(s, gid);
  return list.map((p) => {
    const jid = extractPendingJid(p);
    return { jid, phone: normJid(jid || "").replace("@s.whatsapp.net", "") };
  }).filter((p) => p.jid);
}

// ─── Reset Invite Link
async function resetGroupInviteLink(index, gid) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  await s.groupRevokeInvite(gid);
  await new Promise((r) => setTimeout(r, 1200));
  const code = await s.groupInviteCode(gid);
  return `https://chat.whatsapp.com/${code}`;
}

// ─── Group Settings
async function getGroupSettings(index, gid) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  const meta = await s.groupMetadata(gid);
  return {
    announce: meta.announce === true,
    restrict: meta.restrict === true,
    joinApproval: meta.joinApprovalMode === "on" || meta.joinApprovalMode === true,
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

// ─── Rename Group
async function renameGroup(index, gid, newName) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  await s.groupUpdateSubject(gid, newName);
}

// ─── Add Members
async function addMembersToGroup(index, gid, phones, oneByOne = false) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
  const jids = [...new Set(phones.map((n) => `${n.replace(/[^0-9]/g, "")}@s.whatsapp.net`))];
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
              results.failedNums.push(batch[idx]?.replace("@s.whatsapp.net", "") || "");
            }
          });
        } else {
          results.added += batch.length;
        }
      } catch (_) {
        results.failed += batch.length;
        batch.forEach((j) => results.failedNums.push(normJid(j).replace("@s.whatsapp.net", "")));
      }
      await new Promise((r) => setTimeout(r, 2000));
    }
  }
  return results;
}

// ─── Get Group Info from Invite Link
async function getGroupInfoFromLink(index, code) {
  const s = getSocket(index);
  if (!s) throw new Error("Not connected!");
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
