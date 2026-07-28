const { GenericCrawler } = require("./base");
const MoeCrawler = require("./sources/moe");
const DaadCrawler = require("./sources/daad");
const ThuCrawler = require("./sources/thu");
const EfgCrawler = require("./sources/efg");
const ruleEngine = require("../ruleEngine");
const db = require("../db");
const sourceManager = require("../sourceManager");
const detailParser = require("./detailParser");

// [WHY] 依網址對應到專屬 parser；sourceManager 裡由 Discord /addsource
// 動態新增、沒有專屬 parser 的來源，才 fallback 到 GenericCrawler 的低信心啟發式抓取。
// [WHY 本輪修復，single source of truth] 把 source.url（config/sources.seed.json
// 儲存的網址）傳進每個專屬 crawler 的建構子，取代先前「crawler 自己在程式碼裡
// 寫死一份網址」的模式——後者曾在 MOE 身上造成 this.url 跟 sources.json 儲存
// 的網址不一致，讓 updateSourceHealth() 的完全字串比對永遠找不到對應紀錄，
// 健康分數/lastChecked 從未真正更新過（詳見 Meta_Dev_Knowledge.md 的根因分析）。
// 這裡統一傳入 source.url 後，不管未來哪個來源的網址在 sources.seed.json 裡改了，
// 這個 crawler 回報的 source 標籤都會自動跟著一致，不會再出現同一份概念
// 分別維護兩處卻忘記同步更新的情況。
function createCrawler(source) {
  if (/daad\.de/i.test(source.url)) return new DaadCrawler(source.url);
  if (/edu\.tw\/helpdreams|dream\.tcte\.edu\.tw/i.test(source.url)) return new MoeCrawler(source.url);
  if (/fsis\.thu\.edu\.tw/i.test(source.url)) return new ThuCrawler(source.url);
  if (/european-funding-guide\.eu/i.test(source.url)) return new EfgCrawler(source.url);
  return new GenericCrawler(source.name, source.url);
}

async function runAllSources(sources) {
  const crawlers = sources.map(createCrawler);
  return Promise.all(crawlers.map((c) => c.run()));
}

async function crawlAll() {
  const sources = sourceManager.getEnabledSources();

  const runResults = await runAllSources(sources);
  const raw = runResults.flatMap((r) => r.items);

  const unseen = raw.filter((i) => !db.isSeen(i.link));
  const filtered = ruleEngine.filter(unseen);
  // [WHY] 詳情頁擷取（身份要求/申請條件/申請辦法/截止日期）只對「已通過 filter()
  // 的存活項目」做，不是對全部 unseen 項目——見 detailParser.js 頂端說明，
  // 避免爬蟲耗時隨候選筆數（unseen 常有數十~數百筆）暴增。
  const enriched = await detailParser.enrichWithDetails(filtered);
  enriched.forEach((i) => db.markSeen(i));

  // 🧠 health update：依每個 source 實際跑出的結果（含 status/items）更新健康分數
  for (const r of runResults) {
    sourceManager.updateSourceHealth(r.source, r.items);
  }

  // [WHY] runLog 的寫入與告警判斷都交給呼叫端（scheduler.js）處理：
  // 轉變偵測需要在「寫入這次紀錄之前」讀到上一次的狀態，
  // 若在這裡就 record() 會讓呼叫端讀到的歷史已經包含這次結果，比對不出轉變。

  return {
    success: true,
    count: enriched.length,
    data: enriched,
    runResults,
  };
}

module.exports = {
  crawlAll,
};
