const fs = require("fs");
const path = require("path");

// [WHY] 原本 crawlAll() 每次跑完的 runResults（各 source 的 status/筆數）只存在
// 記憶體裡，跑完就消失。scheduler 的「距上次成功執行超過 8 小時就補跑」跟
// generateReport 的「系統狀態：來源正常/異常」都需要跨 process 重啟後仍讀得到
// 的歷史紀錄，因此另存一份 data/runs.json。
const RUNLOG_PATH = path.join(__dirname, "..", "data", "runs.json");
const MAX_ENTRIES = 500; // 避免長期執行下檔案無限成長

function load() {
  if (!fs.existsSync(RUNLOG_PATH)) return [];
  try {
    return JSON.parse(fs.readFileSync(RUNLOG_PATH, "utf-8"));
  } catch {
    return [];
  }
}

function saveAtomic(data) {
  const tmpPath = RUNLOG_PATH + ".tmp";
  fs.writeFileSync(tmpPath, JSON.stringify(data, null, 2));
  fs.renameSync(tmpPath, RUNLOG_PATH);
}

// runResults: crawler/index.js 的 [{source, name, status, items, error}]
function record(runResults) {
  const at = new Date().toISOString();
  const sources = runResults.map((r) => ({
    source: r.source,
    name: r.name,
    status: r.status,
    itemsFound: r.items.length,
    note: r.error || null,
  }));

  const overallStatus = sources.every((s) => s.status === "error")
    ? "error"
    : sources.some((s) => s.status === "error")
      ? "partial"
      : "success";

  const entry = { at, overallStatus, sources };

  const runs = load();
  runs.push(entry);
  saveAtomic(runs.slice(-MAX_ENTRIES));

  return entry;
}

function getRunsSince(sinceISO) {
  return load().filter((r) => r.at >= sinceISO);
}

// 單一來源的歷史紀錄（新到舊），供判斷「這次 status 跟上次比起來有沒有轉變」用。
// [WHY] 不另外維護一份「上次狀態」的狀態檔——runs.json 本身就是完整歷史，
// 從裡面篩選單一 source 就能推導出轉變/已持續多久，狀態自然不會跟歷史對不上。
function getSourceHistory(sourceUrl, limit = 50) {
  const runs = load();
  const history = [];

  for (let i = runs.length - 1; i >= 0 && history.length < limit; i--) {
    const s = runs[i].sources.find((x) => x.source === sourceUrl);
    if (s) history.push({ at: runs[i].at, status: s.status, itemsFound: s.itemsFound, note: s.note });
  }

  return history;
}

// 最近一次「非全部失敗」的執行時間；用來判斷是否需要補跑
function getLastSuccessAt() {
  const runs = load();
  for (let i = runs.length - 1; i >= 0; i--) {
    if (runs[i].overallStatus !== "error") return runs[i].at;
  }
  return null;
}

module.exports = { record, getRunsSince, getLastSuccessAt, getSourceHistory };
