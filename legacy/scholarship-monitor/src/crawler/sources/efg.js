// [WHY] SCAN 過 13 個國際獎學金候選來源，涵蓋北美（Fulbright Foreign Student Program、
// Killam Fellowships、Rotary Foundation）、英國/大英國協（Commonwealth Scholarship
// Commission、British Council）、亞太（JASSO、MOE Singapore）、國際組織型
// （Erasmus+ 官方站、Study in Sweden、ScholarshipsPortal.eu），全部因為具體、可驗證的
// 原因被排除（單一旗艦專案頁不是清單頁、純類別總覽只有 3-12 個大分類不是個別項目、
// 403 反爬蟲擋掉、純研究所限定會被年級閘門排空、服務對象非台灣籍申請人等，
// 詳見 README「來源清單」段落的候選來源記錄）。
//
// European Funding Guide 的 /scholarship/abroad 頁面是實測後唯一同時符合「公開免登入」
// 「無反爬蟲阻擋」「個別項目清單非類別總覽」「涵蓋大學部等級」四項篩選標準的候選。
// DOM 結構是標準 Drupal Views table（class="views-table"），用 ?page=N 做 GET 分頁
// （不像 MOE 的 ASP.NET postback 需要帶 __VIEWSTATE，簡單很多）。
const cheerio = require("cheerio");
const { BaseCrawler, fetchWithRetry } = require("../base");

const BASE_URL = "https://www.european-funding-guide.eu";
const LIST_PATH = "/scholarship/abroad";

// [WHY] 網站實測有 91 頁（約 2275 筆，全站宣稱共 12,320 筆但這是其中一個過濾分類）。
// 只抓前幾頁，避免每次排程都發 91 次請求；跟 MOE 分頁的安全上限同一個設計原則。
const MAX_PAGES = 5;
const PAGE_DELAY_MS = 1000; // 對目標伺服器的基本禮貌，沿用既有來源的慣例

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

class EfgCrawler extends BaseCrawler {
  // [WHY 本輪修復，single source of truth] 同 daad.js 的說明——url 由
  // createCrawler(source) 傳入 sources.seed.json 儲存的網址。fetchRaw() 是自己
  // 覆寫的分頁邏輯（見下方），用的是 BASE_URL/LIST_PATH 常數組出每一頁的網址，
  // 不是 this.url，所以這裡的 url 只影響標籤，不影響實際抓取的頁碼組成方式，
  // 跟 daad.js 是同一種情況（標籤與抓取目標分離）。
  constructor(url) {
    super("European Funding Guide（歐洲獎學金資料庫）", url || `${BASE_URL}${LIST_PATH}`);
  }

  extractRows($) {
    const items = [];

    $("table.views-table tr").each((i, row) => {
      if (i === 0) return; // 表頭列（# / Institution / Name / Amount / Match）

      const link = $(row).find("td.views-field-title a").first();
      const title = link.text().trim();
      const href = link.attr("href");
      if (!title || !href) return;

      items.push({
        title,
        url: new URL(href, BASE_URL).toString(),
        publishDate: null, // 頁面只有金額/媒合度欄位，沒有公告發布日期
      });
    });

    return items;
  }

  // 覆寫 fetchRaw()：跟 MOE 一樣需要跨頁抓取，但這裡是簡單的 ?page=N GET 分頁
  async fetchRaw() {
    let allItems = [];

    for (let page = 0; page < MAX_PAGES; page++) {
      const url = page === 0 ? `${BASE_URL}${LIST_PATH}` : `${BASE_URL}${LIST_PATH}?page=${page}`;
      const html = await fetchWithRetry(url);
      const $ = cheerio.load(html);
      const items = this.extractRows($);

      if (items.length === 0) break; // 空頁就停止（雙重終止條件之一）

      allItems = allItems.concat(items);

      if (page < MAX_PAGES - 1) await sleep(PAGE_DELAY_MS);
    }

    return allItems;
  }

  // fetchRaw() 已回傳最終格式的項目陣列，parse() 不用再轉換
  async parse(rawItems) {
    return rawItems;
  }
}

module.exports = EfgCrawler;
