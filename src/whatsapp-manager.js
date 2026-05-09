const {
  default: makeWASocket,
  DisconnectReason,
  fetchLatestBaileysVersion,
} = require("@whiskeysockets/baileys");
const pino = require("pino");
const { useMongoAuthState, clearMongoAuth } = require("./mongoAuthState");
const { AccountInfo } = require("./models");

const logger = pino({ level: "silent" });
const MAX_ACCOUNTS = 1;

const accounts = [
  { index: 0, socket: null, status: "disconnected", phoneNumber: "" },
];

let onPairingCode = async () => {};
let onReady = async () => {};
let onDisconnected = async () => {};

function setCallbacks(opts) {
  if (opts.onPairingCode) onPairingCode = opts.onPairingCode;
  if (opts.onReady) onReady = opts.onReady;
  if (opts.onDisconnected) onDisconnected = opts.onDisconnected;
}

function getStatus(index = 0) { return accounts[index]?.status ?? "disconnected"; }
function getPhone(index = 0) { return accounts[index]?.phoneNumber ?? ""; }
function getAllStatuses() {
  return accounts.map((a) => ({ index: a.index, status: a.status, phone: a.phoneNumber }));
}
function getConnectedCount() { return accounts.filter((a) => a.status === "connected").length; }

async function connectAccount(index, phoneNumber, freshStart = true) {
  const acc = accounts[index];
  if (!acc) throw new Error("Invalid account index");

  if (acc.socket) {
    try { acc.socket.end(undefined); } catch {}
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
      const loggedOut = code === DisconnectReason.loggedOut;
      acc.status = "disconnected";
      console.log(`[WA] Disconnected — reason: ${code}`);

      if (loggedOut) {
        await clearMongoAuth(accountId);
        await AccountInfo.findOneAndUpdate(
          { accountIndex: index },
          { accountIndex: index, phoneNumber: "", hasAuth: false },
          { upsert: true }
        );
        acc.phoneNumber = "";
        await onDisconnected(index);
      } else if (acc.phoneNumber) {
        console.log(`[WA] Reconnecting in 5s...`);
        setTimeout(() => {
          connectAccount(index, acc.phoneNumber, false).catch(console.error);
        }, 5000);
      }
    }
  });
}

async function _requestPairingWithRetry(socket, index, clean, attempt = 1) {
  try {
    console.log(`[WA] Requesting pairing code for ${clean} (attempt ${attempt})...`);
    const code = await socket.requestPairingCode(clean);
    if (code) {
      const formatted = code.replace(/[^A-Z0-9]/gi, "").match(/.{1,4}/g)?.join("-") ?? code;
      console.log(`[WA] Pairing code: ${formatted}`);
      await onPairingCode(index, formatted);
    }
  } catch (err) {
    console.error(`[WA] Pairing error (attempt ${attempt}):`, err.message);
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
    try { acc.socket.end(undefined); } catch {}
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
  const savedAccounts = await AccountInfo.find({ hasAuth: true, accountIndex: 0 });
  if (!savedAccounts.length) return;
  console.log(`[Startup] Reconnecting saved account...`);
  const ai = savedAccounts[0];
  await connectAccount(ai.accountIndex, ai.phoneNumber, false).catch((e) =>
    console.error(`[Startup] Reconnect failed:`, e.message)
  );
}

// ─── Group Functions ─────────────────────────────────────────────────────

function getSocket(index = 0) {
  const acc = accounts[index];
  if (!acc?.socket || acc.status !== "connected") return null;
  return acc.socket;
}

async function createGroup(index, name, participantJids) {
  const sock = getSocket(index);
  if (!sock) throw new Error("WhatsApp connected nahi hai!");
  const result = await sock.groupCreate(name, participantJids);
  return result;
}

async function updateGroupDescription(index, groupId, description) {
  const sock = getSocket(index);
  if (!sock) throw new Error("WhatsApp connected nahi hai!");
  await sock.groupUpdateDescription(groupId, description);
}

async function updateGroupPhoto(index, groupId, imageBuffer) {
  const sock = getSocket(index);
  if (!sock) throw new Error("WhatsApp connected nahi hai!");
  await sock.updateProfilePicture(groupId, imageBuffer);
}

async function setDisappearingMessages(index, groupId, seconds) {
  const sock = getSocket(index);
  if (!sock) throw new Error("WhatsApp connected nahi hai!");
  await sock.groupToggleEphemeral(groupId, seconds);
}

async function promoteToAdmin(index, groupId, participantJids) {
  const sock = getSocket(index);
  if (!sock) throw new Error("WhatsApp connected nahi hai!");
  await sock.groupParticipantsUpdate(groupId, participantJids, "promote");
}

async function setGroupPermissions(index, groupId, permissions) {
  const sock = getSocket(index);
  if (!sock) throw new Error("WhatsApp connected nahi hai!");

  // Send Messages: true = everyone, false = only admins
  await sock.groupSettingUpdate(groupId, permissions.sendMessages ? "not_announcement" : "announcement")
    .catch((e) => console.error("[Permissions] sendMessages error:", e.message));

  // Edit Group Info: true = everyone, false = only admins
  await sock.groupSettingUpdate(groupId, permissions.editInfo ? "unlocked" : "locked")
    .catch((e) => console.error("[Permissions] editInfo error:", e.message));

  // Add Members: true = all_member_add, false = admin_add
  await sock.groupMemberAddMode(groupId, permissions.addMembers ? "all_member_add" : "admin_add")
    .catch((e) => console.error("[Permissions] addMembers error:", e.message));

  // Approve New Members: true = on, false = off
  await sock.groupJoinApprovalMode(groupId, permissions.approveMembers ? "on" : "off")
    .catch((e) => console.error("[Permissions] approveMembers error:", e.message));
}

async function getGroupInviteLink(index, groupId) {
  const sock = getSocket(index);
  if (!sock) throw new Error("WhatsApp connected nahi hai!");
  const code = await sock.groupInviteCode(groupId);
  return `https://chat.whatsapp.com/${code}`;
}

module.exports = {
  MAX_ACCOUNTS,
  setCallbacks,
  getStatus,
  getPhone,
  getAllStatuses,
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
};
