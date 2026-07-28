require("dotenv").config();
const discordBot = require("./discordBot");
const scheduler = require("./scheduler");
const logger = require("./logger");

const isOnce = process.argv.includes("--once");

async function runOnce() {
  const result = await scheduler.crawlAll();
  const data = result?.data || [];

  console.log(`📦 crawl finished: ${data.length} qualifying item(s)`);
  logger.activity("index:crawl", { count: data.length, once: true });

  if (data.length === 0) {
    console.log("no new results");
    return;
  }

  const msg =
    "🎓 Scholarships Found:\n\n" +
    data
      .slice(0, 10)
      .map((i) => `• ${i.title}\n${i.link}`)
      .join("\n\n");

  await discordBot.send(msg);
}

async function main() {
  discordBot.start();

  console.log("⏳ waiting for Discord bot to become ready...");
  await discordBot.whenReady();
  console.log("✅ Discord bot ready");

  if (isOnce) {
    console.log("🧪 crawl running (once)...");
    try {
      await runOnce();
    } catch (err) {
      console.error("crawl error:", err.message);
      logger.error("index:crawl", err);
    }
    process.exit(0);
    return;
  }

  // 常駐模式：實際的排程（每 6 小時爬取、每日/週報、8 小時補跑）交給 scheduler.js，
  // 這裡不再重複手動跑一次，避免跟 scheduler 啟動時的補跑邏輯重複執行。
  console.log("🔁 starting scheduler (recurring mode)...");
  scheduler.start();
}

main();
