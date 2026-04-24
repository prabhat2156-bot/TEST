require("dotenv").config();

const TelegramBot = require("node-telegram-bot-api");
const express = require("express");
const axios = require("axios");
const moment = require("moment");
const figlet = require("figlet");
const fs = require("fs-extra");
const os = require("os");

const token = process.env.BOT_TOKEN;
const bot = new TelegramBot(token, { polling: true });

const app = express();
app.get("/", (req, res) => {
    res.send("Bot Running 24/7");
});

app.listen(process.env.PORT || 3000);

console.log(figlet.textSync("TEST BOT"));

bot.onText(/\/start/, (msg) => {
    bot.sendMessage(msg.chat.id,
`🚀 Test Bot Running

🕒 Time: ${moment().format("HH:mm:ss")}
💻 Host: ${os.hostname()}
📱 Platform: ${os.platform()}`
    );
});

bot.onText(/\/ping/, async (msg) => {
    const start = Date.now();
    await axios.get("https://api.github.com");
    const end = Date.now();

    bot.sendMessage(msg.chat.id, `🏓 Pong: ${end - start}ms`);
});

bot.onText(/\/file/, async (msg) => {
    await fs.writeFile("test.txt", "Bot working fine");
    bot.sendDocument(msg.chat.id, "test.txt");
});
