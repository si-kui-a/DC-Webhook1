// [WHY 獨立成檔] fmtItem/sourceDisplayName/groupByCategory/renderGrouped 同時被
// generateReport.js（排程報告）與 discordBot.js（/查詢獎學金、/篩選獎學金、
// /獎學金說明 指令）使用。generateReport.js 本身會 require('../discordBot')
// （呼叫 discordBot.send() 發報告），若把這些函式留在 generateReport.js 裡再讓
// discordBot.js 反過來 require generateReport.js，會形成循環依賴
// （discordBot → generateReport → discordBot）：Node.js 遇到循環 require 時，
// 較晚完成的一方拿到的會是對方「當下尚未執行完 module.exports」的殘缺物件，
// 這裡實際會讓 discordBot.js 拿到 generateReport.js 一個空的 exports，
// send/start/whenReady 都會是 undefined。獨立成這支不依賴 discordBot.js 的檔案，
// 兩邊都能安全直接 require，不會有循環依賴風險。
const ONE_WEEK_MS = 7 * 24 * 3600 * 1000;

// [WHY 本輪新增] 使用者要求「距今到截止日需至少 1 週，不足的不列出」。SCAN 發現
// data/db.json 目前 140 筆真實記錄的 deadline 欄位 100%（0/140）是 null——四欄位
// 詳情頁擷取自上輪上線以來，還沒有一次真的通過六項閘門的新項目觸發過，deadline
// 從未被真實填過值。若把 deadline=null 也當「不足 1 週」排除，會讓目前所有查詢/
// 報告瞬間變成 0 筆結果，這既不合理也不是使用者的原意——「缺乏日期資訊」不等於
// 「已過期」，該由使用者自己決定要不要點進原始連結確認，不該由系統代為隱藏。
// 因此：deadline 為 null 一律正常顯示，只在文字裡加註提醒；只有 deadline 有實際
// 值且距今 < 7 天（含已過期，即距今為負數）才排除。
function isExpiringTooSoon(deadline) {
  if (!deadline) return false;
  const deadlineDate = new Date(`${deadline}T23:59:59`);
  if (Number.isNaN(deadlineDate.getTime())) return false; // 防禦：格式不是合法日期就不排除，避免誤殺
  return deadlineDate.getTime() - Date.now() < ONE_WEEK_MS;
}

// 供 /查詢獎學金、/篩選獎學金、daily()/weekly() 共用的「排除即將到期/已過期」邏輯，
// 套用範圍一致，避免各處各自實作出不同標準
function filterByDeadline(items) {
  return items.filter((r) => !isExpiringTooSoon(r.deadline));
}

// [WHY 本輪修復] 使用者回報輸出裡「截止日期：未提供」後面緊接著又一行
// 「截止日未提供，請自行至原始連結確認」，兩行都在講同一件事（沒有日期資訊），
// 造成重複冗長。整併成同一行，資訊沒有減少（仍然告知未提供＋建議動作），
// 只是不再用兩行各講一次。
function fmtItem(row, idx) {
  const deadlineLine = row.deadline
    ? `截止日期：${row.deadline}`
    : `截止日期：未提供（⚠️ 請自行至原始連結確認）`;
  return `${idx}. ${row.title}
身份要求：${row.eligibility || "未提供"}
申請條件：${row.conditions || "未提供"}
申請辦法：${row.applicationMethod || "未提供"}
${deadlineLine}
連結：${row.link}
━━━━━━━━━━`;
}

// [WHY] 資料管線裡唯一穩定存在、四個來源都會填的分類欄位是 item.source
// （list 頁網址）——SCAN 確認沒有更細緻、跨來源一致的獎學金類別欄位（MOE 的
// 學制/學門、THU 的獎助學門等都只存在單一來源，格式也不統一，無法拿來當
// 跨來源共用的分類鍵）。這裡把網址正規化成人類可讀的來源名稱，供分類標題使用，
// 不影響 db.js 既有以網址為鍵的 source 欄位語意。
function sourceDisplayName(url) {
  if (/daad\.de/i.test(url)) return "DAAD（德國）";
  if (/edu\.tw\/helpdreams/i.test(url)) return "教育部圓夢助學網";
  if (/fsis\.thu\.edu\.tw/i.test(url)) return "東海大學獎助學金";
  if (/european-funding-guide\.eu/i.test(url)) return "European Funding Guide（歐洲）";
  return url; // Discord /addsource 動態新增、無已知簡稱的來源，顯示原始網址
}

// [WHY 本輪新增] 使用者要求能依「領域/地區」查詢，但資料管線裡沒有結構化的
// 地區/國家欄位（見上方 sourceDisplayName 的說明）。這裡用「地區」做比
// sourceDisplayName 更粗的分組——地區可以涵蓋多個來源（例如「台灣」同時涵蓋
// 教育部圓夢助學網跟東海大學獎助學金），跟既有的 /篩選獎學金（只能選單一
// 來源）提供不同的查詢粒度，不是重複功能。地區對應直接寫死（4 個來源、
// 4 個地區，一對一或多對一關係單純，不需要另外的設定檔）；若未來新增來源，
// 需要同時決定它屬於哪個地區——這是人工判斷（哪個國家/地區），不是 AI 判斷
// 「這是不是獎學金網站」，兩者性質不同。
const SOURCE_REGION_MAP = [
  { test: (url) => /daad\.de/i.test(url), region: "德國" },
  { test: (url) => /edu\.tw\/helpdreams/i.test(url), region: "台灣" },
  { test: (url) => /fsis\.thu\.edu\.tw/i.test(url), region: "台灣" },
  { test: (url) => /european-funding-guide\.eu/i.test(url), region: "歐洲" },
];

function regionForSource(url) {
  const match = SOURCE_REGION_MAP.find((m) => m.test(url));
  return match ? match.region : "未分類";
}

function getAllRegions() {
  return Array.from(new Set(SOURCE_REGION_MAP.map((m) => m.region)));
}

// 依來源分類分組，同分類的項目相鄰呈現，而非依原始爬取順序交錯排列
function groupByCategory(items) {
  const groups = new Map();
  for (const item of items) {
    const category = sourceDisplayName(item.source);
    if (!groups.has(category)) groups.set(category, []);
    groups.get(category).push(item);
  }
  return groups;
}

function renderGrouped(items) {
  if (items.length === 0) return null;

  const groups = groupByCategory(items);
  let idx = 1;
  const sections = [];
  for (const [category, catItems] of groups) {
    const lines = catItems.map((r) => fmtItem(r, idx++));
    sections.push(`【${category}】（${catItems.length} 項）\n\n${lines.join("\n")}`);
  }
  return sections.join("\n");
}

module.exports = { fmtItem, sourceDisplayName, groupByCategory, renderGrouped, filterByDeadline, isExpiringTooSoon, regionForSource, getAllRegions };
