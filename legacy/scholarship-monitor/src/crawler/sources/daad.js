// [WHY] DAAD 的獎學金資料庫頁面（/en/study-and-research-in-germany/scholarships/）
// 是純前端渲染的頁面：伺服器回應的 HTML 只有篩選表單外殼，實際清單由瀏覽器端 JS
// 抓取一份靜態資料檔後在畫面上組出來（用 TaffyDB，可在頁面原始碼的
// <script src=".../data/a/js/scholarships.js"> 找到），cheerio 對它的原始 HTML
// 解析不到任何項目。因為專案沒有 headless browser，改為直接抓這份 DAAD 自己
// 公開提供、免驗證的靜態資料檔（本質上就是該頁面自己會下載的資料），
// 內容格式是 `var scholarships = TAFFY([...]);`，剝掉外層後就是一份 JSON 陣列。
const axios = require("axios");
const { BaseCrawler } = require("../base");

const DAAD_PAGE_URL = "https://www.daad.de/en/study-and-research-in-germany/scholarships/";
// [WHY] www.daad.de → www2.daad.de 301 redirect（2026-07 確認），
// 主頁面已知需要直接走 www2 避免轉跳，2026-07-27 實測發現資料檔
// 也開始需要走 www2（www.daad.de 版本回傳 301 但 axios 跟不到最終資料）。
// 直接指向 www2 版本避免轉跳失效。
const DAAD_DATA_URL =
  "https://www2.daad.de/bundles/daadstipendiendatenbanklsh/data/a/js/scholarships.js";
// [WHY] 詳情頁連結格式取自該頁面自己載入的 stipdb-a-lib.js（basicLink + ?detail=<sapProgid>），
// 是使用者點進去實際會看到的網址，而非憑空編造。
const DAAD_DETAIL_BASE =
  "https://www.daad.de/deutschland/stipendium/datenbank/en/21148-scholarship-database/";

class DaadCrawler extends BaseCrawler {
  // [WHY 本輪修復，single source of truth] url 由 createCrawler(source) 傳入
  // config/sources.seed.json 儲存的網址，不再讓「這個 crawler 回報的 source 標籤」
  // 跟 sources.json 各自維護一份可能不同步的網址——這正是 MOE 那次健康分數
  // 靜默失效的根因（兩處分別寫死同一個概念上的值，改了一邊沒改另一邊，
  // updateSourceHealth() 的完全字串比對就永遠對不上）。沒有傳入 url 時
  // （例如直接 new DaadCrawler() 做單元測試/手動除錯）才 fallback 到這個常數，
  // 不影響正式排程行為（排程一律透過 createCrawler 傳入）。DAAD_PAGE_URL 只是
  // 「這個 crawler 代表哪個人類可見頁面」的標籤，真正抓取的資料一律來自
  // DAAD_DATA_URL（見 fetchRaw()），跟 this.url 無關，所以覆寫 url 不影響抓取邏輯。
  constructor(url) {
    super("DAAD", url || DAAD_PAGE_URL);
  }

  // [WHY] DAAD 伺服器會擋帶有 ScholarshipMonitorBot UA 的請求（403），
  // 但不帶 User-Agent 反而正常回傳資料。不共用 base.fetchWithRetry() 的 bot UA，
  // 改用自己的 axios 呼叫。
  async fetchRaw() {
    const res = await axios.get(DAAD_DATA_URL, { timeout: 15000 });
    return res.data;
  }

  async parse(raw) {
    const jsonText = String(raw)
      .trim()
      .replace(/^var\s+scholarships\s*=\s*TAFFY\(/, "")
      .replace(/\);\s*$/, "");

    const records = JSON.parse(jsonText);

    return records.map((r) => ({
      title: r.programmnameEn || r.nameEn || r.programmnameDe || r.nameDe,
      url: `${DAAD_DETAIL_BASE}?detail=${r.sapProgid}&lang=en`,
      publishDate: null, // 資料檔沒有公告發布日期欄位
    }));
  }
}

module.exports = DaadCrawler;
