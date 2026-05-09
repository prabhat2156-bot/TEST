const mongoose = require("mongoose");
const { Schema } = mongoose;

// ─── WhatsApp auth state ──────────────────────────────────────────────
const AuthStateSchema = new Schema(
  {
    accountId: { type: String, required: true },
    type:      { type: String, required: true },
    data:      { type: String, required: true },
  },
  { collection: "auth_states" }
);
AuthStateSchema.index({ accountId: 1, type: 1 }, { unique: true });

// ─── Saved account info ───────────────────────────────────────────────
const AccountInfoSchema = new Schema(
  {
    accountIndex: { type: Number, required: true, unique: true },
    phoneNumber:  { type: String, default: "" },
    hasAuth:      { type: Boolean, default: false },
  },
  { collection: "account_infos" }
);

const AuthState   = mongoose.model("AuthState", AuthStateSchema);
const AccountInfo = mongoose.model("AccountInfo", AccountInfoSchema);

module.exports = { AuthState, AccountInfo };
