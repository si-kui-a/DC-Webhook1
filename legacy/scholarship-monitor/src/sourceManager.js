const fs = require("fs");
const path = require("path");
const logger = require("./logger");

// ✅ 安全引入 AI 模組（避免 crash）
let sourceAI = null;

try {
  sourceAI = require("./sourceAI");
} catch (e) {
  console.log("⚠️ sourceAI not found → AI scoring disabled");
}

const DB_PATH = path.join(__dirname, "..", "data", "sources.json");
const SEED_PATH = path.join(__dirname, "..", "config", "sources.seed.json");

// =========================
// 🔧 基礎 IO
// =========================

// [WHY] data/sources.json 混合了「靜態設定」（來源名稱/網址）跟「執行期產物」
// （score/lastChecked 這些每次爬完都會變動的健康分數），整份進 git 追蹤的話，
// 每跑一次 --once 或排程就會讓 git status 變髒。改成：靜態的初始清單放
// config/sources.seed.json（進 git），data/sources.json 是純執行期狀態
// （gitignored），首次啟動時若不存在就從 seed 檔案初始化一次。
// 之後透過 /addsource、/removesource、健康分數更新等操作只會動 data/sources.json，
// seed 檔案不會再被自動修改（保留「初始安裝清單」的意義）。
function seedIfMissing() {
  if (fs.existsSync(DB_PATH)) return;
  if (!fs.existsSync(SEED_PATH)) return;

  const seed = JSON.parse(fs.readFileSync(SEED_PATH, "utf-8"));
  const initial = seed.map((s) => ({
    name: s.name,
    url: s.url,
    enabled: s.enabled !== false,
    score: 1,
    lastChecked: null,
    disabledReason: null,
  }));

  save(initial);
}

function load() {
  seedIfMissing();
  if (!fs.existsSync(DB_PATH)) return [];
  return JSON.parse(fs.readFileSync(DB_PATH, "utf-8"));
}

function save(data) {
  // [WHY] 先寫暫存檔再 rename（同檔案系統內為原子操作），避免 Discord 指令
  // （/addsource、/removesource）與爬蟲的健康分數更新同時寫入時互相截斷內容。
  const tmpPath = DB_PATH + ".tmp";
  fs.writeFileSync(tmpPath, JSON.stringify(data, null, 2));
  fs.renameSync(tmpPath, DB_PATH);
}

// =========================
// 📡 sources CRUD
// =========================

function getSources() {
  return load();
}

function getEnabledSources() {
  return load().filter(s => s.enabled !== false);
}

function addSource(name, url) {
  const list = load();

  const exists = list.find(s => s.url === url);
  if (exists) return { success: false, reason: "exists" };

  list.push({
    name,
    url,
    enabled: true,
    score: 1,
    lastChecked: null,
    disabledReason: null,
  });

  save(list);

  return { success: true };
}

function removeSource(url) {
  const list = load();
  const filtered = list.filter(s => s.url !== url);

  save(filtered);

  return { success: true };
}

// =========================
// 🧠 AI health update（核心）
// =========================

// [WHY 本輪新增防呆機制] 這裡是完全字串比對——crawler 回報的 sourceUrl 跟
// data/sources.json 儲存的網址只要有一個字不同就找不到，過去 MOE 的 this.url
// 跟 sources.seed.json 不一致時，這裡靜默 return，健康分數/lastChecked 從此
// 凍結，數月都沒被發現（詳見 Meta_Dev_Knowledge.md 的根因分析）。本輪已經用
// 「single source of truth」從源頭解決 MOE 的問題（見 crawler/index.js 的
// createCrawler()），但這裡仍加一層防呆：找不到對應紀錄時記錄警告到
// error.log，避免未來若又有人在某個 crawler 裡寫死一個跟設定檔不同步的網址，
// 這類問題會再度靜默潛伏，而不是像這次一樣要等到人工逐項稽核才發現。
function updateSourceHealth(sourceUrl, recentItems = []) {
  const list = load();

  const source = list.find(s => s.url === sourceUrl);
  if (!source) {
    logger.error(
      "sourceManager:updateSourceHealth",
      new Error(`找不到對應的來源紀錄，健康分數未更新：sourceUrl=${sourceUrl}`),
      { knownUrls: list.map((s) => s.url) },
    );
    return;
  }

  // ❗ 沒 AI 就跳過
  if (!sourceAI) return;

  const { scoreSource, shouldDisable } = sourceAI;

  const score = scoreSource(source, recentItems);

  source.score = score;
  source.lastChecked = new Date().toISOString();

  if (shouldDisable(score)) {
    source.enabled = false;
    source.disabledReason = "auto-disabled (low score)";
  }

  save(list);
}

// =========================
// 📤 export
// =========================

module.exports = {
  getSources,
  getEnabledSources,
  addSource,
  removeSource,
  updateSourceHealth,
};