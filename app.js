// app.js
// Run: npm start

require("dotenv").config();

const express = require("express");
const axios = require("axios");
const chalk = require("chalk");
const figlet = require("figlet");
const moment = require("moment");

const app = express();
const PORT = process.env.PORT || 3000;

console.log(
  chalk.green(
    figlet.textSync("MADARA BOT", {
      horizontalLayout: "default"
    })
  )
);

app.get("/", async (req, res) => {
  try {
    const response = await axios.get("https://api.github.com");

    res.json({
      status: "Running",
      time: moment().format("YYYY-MM-DD HH:mm:ss"),
      github_status: response.status
    });

    console.log(
      chalk.blue(`[${moment().format("HH:mm:ss")}] Request received`)
    );
  } catch (error) {
    res.json({
      error: "API failed"
    });

    console.log(chalk.red("Error fetching API"));
  }
});

app.listen(PORT, () => {
  console.log(
    chalk.yellow(`Server started on http://localhost:${PORT}`)
  );
});
