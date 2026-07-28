const fs = require("fs");
const path = require("path");

const DB_PATH = path.join(__dirname, "..", "data", "db.json");

// [WHY] 存完整 metadata（不只是連結字串）：只存連結陣列會導致 generateReport
// 完全沒有 title/score 可用，任何統計/日報都做不到。改成物件陣列，
// 每筆記錄仍以 link 當唯一鍵做 dedup，跟舊版行為一致。
let writing = false;
let queue = [];

function load() {
  if (!fs.existsSync(DB_PATH)) return [];
  return JSON.parse(fs.readFileSync(DB_PATH, "utf-8"));
}

function saveQueued(data) {
  return new Promise((resolve) => {
    queue.push({ data, resolve });
    processQueue();
  });
}

async function processQueue() {
  if (writing) return;
  writing = true;

  while (queue.length > 0) {
    const { data, resolve } = queue.shift();
    // [WHY] 先寫暫存檔再 rename：rename 在同一個檔案系統內是原子操作，
    // 避免多個非同步寫入交錯時讓 db.json 停在「寫到一半」的損毀狀態。
    const tmpPath = DB_PATH + ".tmp";
    fs.writeFileSync(tmpPath, JSON.stringify(data, null, 2));
    fs.renameSync(tmpPath, DB_PATH);
    resolve();
  }

  writing = false;
}

function isSeen(link) {
  return load().some((r) => r.link === link);
}

// [WHY 本輪擴充] 新增 eligibility/conditions/applicationMethod/deadline 四欄位，
// 供 Discord 訊息四欄位格式使用（見 src/crawler/detailParser.js）。這四個欄位
// 可能是 null（來源沒有對應的詳情頁欄位，或抓取失敗）——渲染層負責把 null
// 顯示成「未提供」，這裡如實存 null，不在資料層造假掰內容。
function markSeen(item) {
  const db = load();

  if (!db.some((r) => r.link === item.link)) {
    db.push({
      link: item.link,
      title: item.title,
      source: item.source,
      score: item.score,
      publishDate: item.publishDate || null,
      eligibility: item.eligibility || null,
      conditions: item.conditions || null,
      applicationMethod: item.applicationMethod || null,
      deadline: item.deadline || null,
      foundAt: new Date().toISOString(),
    });
    saveQueued(db);
  }
}

function getRecordsSince(sinceISO) {
  return load().filter((r) => r.foundAt >= sinceISO);
}

// [WHY 本輪新增] /查詢獎學金、/篩選獎學金 需要對「目前資料庫累積的全部已知項目」
// 做關鍵字/分類篩選，不像 daily()/weekly() 只看最近 24h/7 天的新項目。
function getAll() {
  return load();
}

module.exports = {
  isSeen,
  markSeen,
  getRecordsSince,
  getAll,
};
