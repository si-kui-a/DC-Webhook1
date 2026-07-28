// [WHY] 東海大學自己的獎助學金查詢系統（http://fsis.thu.edu.tw/wwwstud/frontend/
// Scholarship.php）直接對應 config/profile.json 的 school="東海大學"，命中率理論上
// 高於教育部圓夢助學網的泛用清單。實測 DOM 結構：單一 table#scholarship，154 筆
// 資料在同一頁（沒有分頁），結構清楚穩定，因此寫成專屬 class（精準度較高），
// 沒有走 GenericCrawler 保底邏輯（GenericCrawler 的實測結果另見
// docs/RUNBOOK.md「Phase 2」的診斷紀錄）。
//
// [WHY 抓取邏輯] 每列有兩個 `Scholarship_detail.php?...` 連結：第一個文字是純數字
// 編號（schno，帶 class="button-xs"），第二個文字才是真正的獎學金名稱。原始 HTML
// 的 data-title 屬性標錯欄位（例如「名稱」欄實際放編號、「日期」欄實際放名稱），
// 不能依賴 data-title，改用「第二個 Scholarship_detail.php 連結」這個穩定的結構
// 特徵來抓標題。
const { BaseCrawler } = require("../base");

const LIST_URL = "http://fsis.thu.edu.tw/wwwstud/frontend/Scholarship.php";

class ThuCrawler extends BaseCrawler {
  // [WHY 本輪修復，single source of truth] 同 daad.js 的說明——url 由
  // createCrawler(source) 傳入 sources.seed.json 儲存的網址，避免兩處分別維護
  // 同一個網址值而不同步（MOE 那次健康分數靜默失效的根因）。⚠️ 與 DAAD/EFG 不同：
  // ThuCrawler 沒有覆寫 fetchRaw()，會用 BaseCrawler 預設的 fetchWithRetry(this.url)，
  // 所以這裡的 url 同時是「標籤」也是「真正抓取的目標網址」——這正是 single source
  // of truth 的用意：若未來 sources.seed.json 的網址改了，抓取行為也會跟著正確更新，
  // 不用同時去改這裡的常數。沒有傳入 url 時才 fallback 到 LIST_URL 常數。
  constructor(url) {
    super("東海大學獎助學金查詢", url || LIST_URL);
  }

  async parseList($) {
    const items = [];

    $("#scholarship tr").each((i, row) => {
      if (i === 0) return; // 表頭列

      const links = $(row).find('a[href^="Scholarship_detail.php"]');
      if (links.length < 2) return; // 沒有兩個連結就不是正常資料列

      const nameLink = links.eq(1); // 第二個連結才是真正的獎學金名稱
      const title = nameLink.text().trim();
      const href = nameLink.attr("href");
      if (!title || !href) return;

      items.push({
        title,
        url: new URL(href, LIST_URL).toString(),
        publishDate: null, // 頁面有「申請日期」區間，但那是截止資訊不是發布日期，維持既有 schema 不擴充
      });
    });

    return items;
  }
}

module.exports = ThuCrawler;
