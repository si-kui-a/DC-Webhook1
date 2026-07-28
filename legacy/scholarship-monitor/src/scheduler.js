// [WHY] node-cron：單一 npm package 就能在同一個 process 裡註冊排程，
// 不需要另外跑 n8n 或系統層級的 cron/Task Scheduler，符合專案「減少維運面」的取捨。
const cron = require("node-cron");
const { runCrawlerSafe } = require("./crawler/runner");
const discordBot = require("./discordBot");
const generateReport = require("./report/generateReport");
const runLog = require("./runLog");
const logger = require("./logger");

// [WHY] 個人電腦不會 24/7 開機，關機期間排定的執行時間會直接錯過；
// 啟動時若發現距離上次「非全部失敗」的執行已超過 8 小時，就視為錯過了正常排程，
// 立即補跑一次，而不是乾等到下一個排程時間點。
const CATCHUP_THRESHOLD_MS = 8 * 3600 * 1000;

function formatHours(ms) {
  const hours = ms / 3600000;
  return hours < 1 ? "不到 1 小時" : `約 ${hours.toFixed(1)} 小時`;
}

// [WHY] 邊緣觸發（只在「上次不是 empty、這次是 empty」的轉變當下發一次），
// 避免長期是 0 筆的來源每次排程都洗一次版，最後讓使用者連真正異常的告警都忽略掉。
// 判斷「上次」狀態一定要在 runLog.record() 寫入這次結果之前做，
// 否則讀到的歷史會包含這次自己，永遠比對不出轉變。
async function checkSourceTransitions(runResults) {
  for (const r of runResults) {
    const history = runLog.getSourceHistory(r.source, 30); // 新到舊，不含這次
    const prevStatus = history[0]?.status;
    const label = r.name || r.source;

    if (r.status === "empty" && prevStatus !== "empty") {
      const lastOk = history.find((h) => h.status === "ok");
      const sinceText = lastOk
        ? `，上次成功是 ${lastOk.at}（抓到 ${lastOk.itemsFound} 筆），距今${formatHours(Date.now() - new Date(lastOk.at).getTime())}`
        : "，過去紀錄裡從未成功抓到過項目";

      await discordBot.sendRedesignAlert(
        `來源「${label}」這次爬取連線正常，但一筆項目都沒抓到（status='empty'）` +
          `${sinceText}。可能是網站改版導致 selector 失效，請檢查。`,
      );
    } else if (r.status === "ok" && prevStatus === "empty") {
      await discordBot.sendRedesignAlert(`來源「${label}」已恢復正常，這次抓到 ${r.items.length} 筆。`);
    }
  }
}

async function runCrawler() {
  try {
    const result = await runCrawlerSafe();
    const runResults = result?.runResults;

    if (runResults) {
      // 順序重要：轉變偵測必須在 record() 寫入這次結果「之前」讀歷史。
      await checkSourceTransitions(runResults);
      runLog.record(runResults);
    }

    // [WHY] crawlAll() 回傳 { success, count, data, runResults }，runResults 才是
    // 每個 source 各自的 {status, items} 陣列——之前這裡誤把整個 result 物件當陣列
    // 呼叫 .some()，只要跑過一次就會丟出 "result.some is not a function"。
    const hasError = runResults?.some((r) => r.status === "error");

    if (hasError) {
      await discordBot.sendHealthAlert("Crawler error detected");
    }

    return result;
  } catch (err) {
    await discordBot.sendHealthAlert("Crawler crash: " + err.message);
    throw err;
  }
}

let started = false;

function start() {
  if (started) {
    console.log("⚠️ scheduler already started");
    return;
  }
  started = true;

  cron.schedule("0 */6 * * *", () => {
    runCrawler().catch((err) => logger.error("scheduler:crawl", err));
  });
  console.log("🕕 scheduler: crawlAll registered every 6 hours");

  cron.schedule("0 9 * * *", () => {
    generateReport.sendDaily().catch((err) => logger.error("scheduler:report:daily", err));
  });
  console.log("🗓️ scheduler: daily report registered at 09:00");

  cron.schedule("0 18 * * 0", () => {
    generateReport.sendWeekly().catch((err) => logger.error("scheduler:report:weekly", err));
  });
  console.log("🗓️ scheduler: weekly report registered at Sunday 18:00");

  const lastSuccessAt = runLog.getLastSuccessAt();
  const needsCatchup =
    !lastSuccessAt || Date.now() - new Date(lastSuccessAt).getTime() > CATCHUP_THRESHOLD_MS;

  if (needsCatchup) {
    console.log("⏩ scheduler: last successful run > 8h ago (or none yet) — running catch-up crawl now");
    runCrawler().catch((err) => logger.error("scheduler:catchup", err));
  }
}

module.exports = {
  start,
  crawlAll: runCrawler,
  // 供測試直接驅動邊緣觸發判斷，不需要真的跑一次爬蟲
  checkSourceTransitions,
};
