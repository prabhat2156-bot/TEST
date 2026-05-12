/**
 * users.js — User Management & Premium System
 *
 * Plans:
 *   free    — bot mode "free" mein sab use kar sakte hain
 *   trial   — 24 hours full access (naye user ko auto milta hai)
 *   premium — paid, expiry date ke saath
 *
 * Bot Modes (BotConfig key="botMode"):
 *   free_mode — sab users use kar sakte hain
 *   paid_mode — sirf premium/trial users use kar sakte hain
 */

const { User, BotConfig } = require("./models");
const crypto = require("crypto");

// ─── Referral Code Generator ───────────────────────────────────────────────
function generateReferralCode(telegramId) {
  return "REF" + crypto.createHash("md5").update(String(telegramId)).digest("hex").slice(0, 7).toUpperCase();
}

// ─── Get or Create User ────────────────────────────────────────────────────
async function getOrCreateUser(telegramId, firstName = "", username = "") {
  let user = await User.findOne({ telegramId });
  if (!user) {
    const refCode = generateReferralCode(telegramId);
    // Auto give 24hr trial on first join
    const trialEnd = new Date(Date.now() + 24 * 60 * 60 * 1000);
    user = await User.create({
      telegramId,
      firstName,
      username,
      plan:         "trial",
      trialUsed:    true,
      trialStart:   new Date(),
      trialEnd,
      referralCode: refCode,
      waAccountId:  `wa_${telegramId}`,
    });
  }
  return user;
}

// ─── Check if User has Access ──────────────────────────────────────────────
async function userHasAccess(telegramId) {
  const user = await User.findOne({ telegramId });
  if (!user) return false;
  if (user.banned) return false;

  const now = Date.now();
  // trial active?
  if (user.plan === "trial" && user.trialEnd && user.trialEnd > now) return true;
  // premium active?
  if (user.plan === "premium" && user.premiumEnd && user.premiumEnd > now) return true;

  // Bot mode check
  const mode = await getBotMode();
  if (mode === "free_mode") return true;

  return false;
}

// ─── Is User Premium/Trial Active ─────────────────────────────────────────
function isPlanActive(user) {
  const now = Date.now();
  if (user.plan === "trial"   && user.trialEnd   && user.trialEnd   > now) return true;
  if (user.plan === "premium" && user.premiumEnd && user.premiumEnd > now) return true;
  return false;
}

function getPlanLabel(user) {
  const now = Date.now();
  if (user.plan === "trial" && user.trialEnd && user.trialEnd > now) {
    const hrs = Math.max(0, Math.ceil((user.trialEnd - now) / 3600000));
    return `🆓 Trial (${hrs}h left)`;
  }
  if (user.plan === "premium" && user.premiumEnd && user.premiumEnd > now) {
    const days = Math.max(0, Math.ceil((user.premiumEnd - now) / 86400000));
    return `⭐ Premium (${days}d left)`;
  }
  return "❌ No Active Plan";
}

// ─── Add Premium ───────────────────────────────────────────────────────────
async function addPremium(telegramId, days) {
  const user = await User.findOne({ telegramId });
  if (!user) return null;
  const now = Date.now();
  // Extend from current expiry if still active
  const base = (user.plan === "premium" && user.premiumEnd && user.premiumEnd > now)
    ? user.premiumEnd.getTime()
    : now;
  const newEnd = new Date(base + days * 86400000);
  await User.updateOne({ telegramId }, {
    plan: "premium",
    premiumStart: user.premiumStart || new Date(),
    premiumEnd:   newEnd,
  });
  return newEnd;
}

// ─── Add Temporary Premium (hours) ────────────────────────────────────────
async function addTempPremium(telegramId, hours) {
  return addPremium(telegramId, hours / 24);
}

// ─── Remove Premium ────────────────────────────────────────────────────────
async function removePremium(telegramId) {
  await User.updateOne({ telegramId }, {
    plan: "free", premiumStart: null, premiumEnd: null,
  });
}

// ─── Ban / Unban ───────────────────────────────────────────────────────────
async function banUser(telegramId, reason = "") {
  await User.updateOne({ telegramId }, { banned: true, bannedReason: reason });
}
async function unbanUser(telegramId) {
  await User.updateOne({ telegramId }, { banned: false, bannedReason: "" });
}

// ─── Referral System ───────────────────────────────────────────────────────
async function processReferral(newUserId, referralCode) {
  if (!referralCode) return null;
  const referrer = await User.findOne({ referralCode: referralCode.toUpperCase() });
  if (!referrer) return null;
  if (referrer.telegramId === newUserId) return null; // self-referral not allowed

  // Give referrer 1 day premium
  await addPremium(referrer.telegramId, 1);
  await User.updateOne({ telegramId: referrer.telegramId }, { $inc: { referralCount: 1 } });
  await User.updateOne({ telegramId: newUserId }, { referredBy: referrer.telegramId });
  return referrer;
}

// ─── Bot Mode ──────────────────────────────────────────────────────────────
async function getBotMode() {
  const doc = await BotConfig.findOne({ key: "botMode" });
  return doc?.value || "free_mode";
}
async function setBotMode(mode) {
  await BotConfig.findOneAndUpdate(
    { key: "botMode" },
    { key: "botMode", value: mode },
    { upsert: true }
  );
}

// ─── Stats for Admin ───────────────────────────────────────────────────────
async function getBotStats() {
  const now = new Date();
  const todayStart = new Date(); todayStart.setHours(0, 0, 0, 0);

  const [total, todayNew, premiumCount, trialCount, banned] = await Promise.all([
    User.countDocuments(),
    User.countDocuments({ joinedAt: { $gte: todayStart } }),
    User.countDocuments({ plan: "premium", premiumEnd: { $gt: now } }),
    User.countDocuments({ plan: "trial",   trialEnd:   { $gt: now } }),
    User.countDocuments({ banned: true }),
  ]);

  return { total, todayNew, premiumCount, trialCount, banned };
}

// ─── List Users (paginated) ────────────────────────────────────────────────
async function listUsers(page = 0, limit = 10) {
  return User.find().sort({ joinedAt: -1 }).skip(page * limit).limit(limit).lean();
}

// ─── Update last active ────────────────────────────────────────────────────
async function touchUser(telegramId) {
  await User.updateOne({ telegramId }, { lastActiveAt: new Date() });
}

// ─── 6hr Auto Logout Check ────────────────────────────────────────────────
async function getUsersForAutoLogout() {
  const cutoff = new Date(Date.now() - 6 * 60 * 60 * 1000);
  return User.find({ waConnected: true, waConnectedAt: { $lt: cutoff } }).lean();
}

async function markWaConnected(telegramId, phone) {
  await User.updateOne({ telegramId }, {
    waConnected:   true,
    waPhone:       phone,
    waConnectedAt: new Date(),
  });
}

async function markWaDisconnected(telegramId) {
  await User.updateOne({ telegramId }, {
    waConnected:   false,
    waConnectedAt: null,
  });
}

module.exports = {
  getOrCreateUser,
  userHasAccess,
  isPlanActive,
  getPlanLabel,
  addPremium,
  addTempPremium,
  removePremium,
  banUser,
  unbanUser,
  processReferral,
  getBotMode,
  setBotMode,
  getBotStats,
  listUsers,
  touchUser,
  getUsersForAutoLogout,
  markWaConnected,
  markWaDisconnected,
  generateReferralCode,
};
