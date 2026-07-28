// [WHY] 報告只用純字串模板組裝，不用額外的 templating library（如 handlebars）——
// 格式固定、變化量小，多一層模板引擎只會增加維護成本，不符合 EASY-MAINTENANCE。
//
// [技術債修正] 這個檔案原本假設的是已放棄的 SQLite 版本 schema
// （db.prepare().all()、match_score、deadline_confidence、crawl_runs 表），
// 但現在的 db.js 只是一個 JSON 檔案，執行下去必定拋錯（db.prepare is not a
// function）。已改為對應現在實際存在的資料：
//   - src/db.js：{link,title,source,score,publishDate,foundAt} 物件陣列
//   - src/runLog.js：每次 crawlAll() 的 {source,status,itemsFound} 執行紀錄
// 目前 ruleEngine 只回傳「已通過 threshold」的整數分數，沒有 0~1 的信心分數，
// 也沒有擷取 deadline，因此拿掉了原本 match_score 分兩層（matched/uncertain）
// 與 deadline_confidence 的邏輯——不是刪掉了功能，而是這些欄位在目前的架構裡
// 從未真正被計算過，繼續留著只會顯示假資料。
const db = require("../db");
const runLog = require("../runLog");
const logger = require("../logger");
const discordBot = require("../discordBot");
const { sourceDisplayName, renderGrouped, filterByDeadline } = require("./format");

// 依 runLog 紀錄找出「目前」（區間內最後一次執行）異常的來源
function summarizeHealth(sinceISO) {
  const runs = runLog.getRunsSince(sinceISO);

  const latestBySource = {};
  for (const run of runs) {
    for (const s of run.sources) {
      latestBySource[s.source] = { status: s.status, note: s.note };
    }
  }

  const failing = Object.entries(latestBySource)
    .filter(([, v]) => v.status !== "ok")
    .map(([source, v]) => ({ source, ...v }));

  return { totalRuns: runs.length, failing };
}

// [WHY 本輪新增] 使用者要求日報/週報跟 /查詢獎學金、/篩選獎學金 一致套用「距今
// 到截止日需至少 1 週」的規則，deadline=null 不算「不足 1 週」（見 format.js 的
// filterByDeadline() 說明）。放在 db.getRecordsSince() 之後、renderGrouped() 之前，
// 確保「今日新增：N 項」這個計數本身就是套用規則後的數字，不會跟實際顯示的清單筆數對不上。
function daily() {
  const since = new Date(Date.now() - 24 * 3600 * 1000).toISOString();
  const newItems = filterByDeadline(db.getRecordsSince(since)).sort((a, b) => b.score - a.score);
  const health = summarizeHealth(since);

  // [WHY 審閱重點] 系統健康摘要固定放在報告最上方，讓使用者每天第一眼就能確認
  // 「昨晚的爬蟲有沒有正常跑」，而不需要另外去查 log。
  let msg = `📋 **每日獎學金報告** (${new Date().toISOString().slice(0, 10)})\n\n`;
  msg += `系統狀態：${health.failing.length === 0 ? "✅ 全部來源正常" : `⚠️ ${health.failing.length} 個來源異常`}\n`;
  if (health.failing.length > 0) {
    msg += health.failing.map((f) => `  - ${f.source}: ${f.status}${f.note ? ` (${f.note})` : ""}`).join("\n") + "\n";
  }

  msg += `\n今日新增：${newItems.length} 項（已排除截止日不足 1 週的項目）\n\n━━━━━━━━━━\n`;
  msg += renderGrouped(newItems) || "（本次無新項目）";
  return msg;
}

function weekly() {
  const since = new Date(Date.now() - 7 * 24 * 3600 * 1000).toISOString();
  const newItems = filterByDeadline(db.getRecordsSince(since)).sort((a, b) => b.score - a.score);

  const countBySource = {};
  for (const item of newItems) {
    const category = sourceDisplayName(item.source);
    countBySource[category] = (countBySource[category] || 0) + 1;
  }

  let msg = `📊 **每週獎學金彙整**\n\n本週新增：${newItems.length} 項（已排除截止日不足 1 週的項目）\n\n`;
  msg += Object.entries(countBySource)
    .map(([category, count]) => `  - ${category}: ${count} 項`)
    .join("\n");
  msg += `\n\n━━━━━━━━━━\n`;
  msg += renderGrouped(newItems.slice(0, 30)) || "（本週無新項目）";
  return msg;
}

async function sendDaily() {
  const msg = daily();
  try {
    await discordBot.send(msg);
    logger.activity("report:daily", { status: "sent" });
  } catch (err) {
    logger.error("report:daily", err);
    throw err;
  }
}

async function sendWeekly() {
  const msg = weekly();
  try {
    await discordBot.send(msg);
    logger.activity("report:weekly", { status: "sent" });
  } catch (err) {
    logger.error("report:weekly", err);
    throw err;
  }
}

// CLI 獨立執行（npm run report:daily / report:weekly）：這個 process 裡沒有人
// 啟動過 discordBot，所以要自己 start() 並等 ready，跟 scheduler.js 長駐 process
// 裡（index.js 早就 start()+ready 過）的呼叫方式不同。
async function main() {
  const type = process.argv[2];

  discordBot.start();
  await discordBot.whenReady();

  console.log(type === "weekly" ? weekly() : daily());
  await (type === "weekly" ? sendWeekly() : sendDaily());
  process.exit(0);
}

if (require.main === module) {
  main().catch((err) => {
    console.error(err.message);
    process.exit(1);
  });
}

module.exports = { daily, weekly, sendDaily, sendWeekly };
