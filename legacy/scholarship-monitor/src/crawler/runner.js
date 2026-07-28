const { crawlAll } = require("./index");

let running = false;

async function runCrawlerSafe() {
  if (running) {
    console.log("⚠️ crawler already running");
    return null;
  }

  running = true;

  try {
    return await crawlAll();
  } finally {
    running = false;
  }
}

module.exports = { runCrawlerSafe };