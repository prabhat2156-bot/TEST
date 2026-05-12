const mongoose = require("mongoose");
const { Schema } = mongoose;

// ─── WhatsApp Auth ─────────────────────────────────────────────────────────
const AuthStateSchema = new Schema(
  {
    accountId: { type: String, required: true },
    type:      { type: String, required: true },
    data:      { type: String, required: true },
  },
  { collection: "auth_states" }
);
AuthStateSchema.index({ accountId: 1, type: 1 }, { unique: true });

const AccountInfoSchema = new Schema(
  {
    accountIndex: { type: Number, required: true, unique: true },
    phoneNumber:  { type: String, default: "" },
    hasAuth:      { type: Boolean, default: false },
  },
  { collection: "account_infos" }
);

// ─── Bot Users ─────────────────────────────────────────────────────────────
const UserSchema = new Schema(
  {
    telegramId:    { type: Number, required: true, unique: true },
    firstName:     { type: String, default: "" },
    username:      { type: String, default: "" },
    // premium: "free" | "trial" | "premium"
    plan:          { type: String, default: "free" },
    trialUsed:     { type: Boolean, default: false },
    trialStart:    { type: Date, default: null },
    trialEnd:      { type: Date, default: null },
    premiumStart:  { type: Date, default: null },
    premiumEnd:    { type: Date, default: null },
    // referral
    referralCode:  { type: String, unique: true, sparse: true },
    referredBy:    { type: Number, default: null },
    referralCount: { type: Number, default: 0 },
    // WhatsApp session — each user has their OWN accountId
    waAccountId:   { type: String, default: null },
    waPhone:       { type: String, default: "" },
    waConnected:   { type: Boolean, default: false },
    // 6hr auto logout
    waConnectedAt: { type: Date, default: null },
    // ban
    banned:        { type: Boolean, default: false },
    bannedReason:  { type: String, default: "" },
    // stats
    joinedAt:      { type: Date, default: Date.now },
    lastActiveAt:  { type: Date, default: Date.now },
  },
  { collection: "bot_users" }
);

// ─── Bot Config (single doc) ───────────────────────────────────────────────
const BotConfigSchema = new Schema(
  {
    key:   { type: String, required: true, unique: true },
    value: { type: Schema.Types.Mixed },
  },
  { collection: "bot_config" }
);

const AuthState   = mongoose.model("AuthState",   AuthStateSchema);
const AccountInfo = mongoose.model("AccountInfo", AccountInfoSchema);
const User        = mongoose.model("User",        UserSchema);
const BotConfig   = mongoose.model("BotConfig",   BotConfigSchema);

module.exports = { AuthState, AccountInfo, User, BotConfig };
