const mongoose = require("mongoose");

let connected = false;

async function connectDB() {
  if (connected) return;
  const uri = process.env.MONGODB_URI;
  if (!uri) throw new Error("❌ MONGODB_URI environment variable not set!");

  mongoose.connection.on("disconnected", () => {
    connected = false;
    console.log("[DB] Disconnected — will auto-reconnect");
  });
  mongoose.connection.on("reconnected", () => {
    connected = true;
    console.log("[DB] Reconnected");
  });
  mongoose.connection.on("error", (err) => {
    console.error("[DB] Error:", err.message);
  });

  await mongoose.connect(uri, {
    serverSelectionTimeoutMS: 15000,
    heartbeatFrequencyMS:     10000,
    autoReconnect:            true,
  });
  connected = true;
  console.log("✅ MongoDB connected");
}

module.exports = { connectDB };
