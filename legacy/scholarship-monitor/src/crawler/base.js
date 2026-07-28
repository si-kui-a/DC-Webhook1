// [WHY] axios + cheerio：不需要啟動瀏覽器就能解析伺服器端渲染的 HTML，
//       比 puppeteer/headless browser 省資源，適合單機低頻爬蟲場景。
const axios = require("axios");
const cheerio = require("cheerio");

const USER_AGENT =
  "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) " +
  "Chrome/124.0.0.0 Safari/537.36 ScholarshipMonitorBot/1.0";
const TIMEOUT_MS = 15000;
const MAX_RETRIES = 2;

/**
 * 對單一 URL 發送 GET，失敗時重試（最多 MAX_RETRIES 次），
 * 每次都帶 timeout 與偽裝 User-Agent，避免被視為爬蟲直接拒絕。
 */
async function fetchWithRetry(url) {
  let lastErr;
  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    try {
      const res = await axios.get(url, {
        timeout: TIMEOUT_MS,
        headers: { "User-Agent": USER_AGENT },
      });
      return res.data;
    } catch (err) {
      lastErr = err;
    }
  }
  throw lastErr;
}

/**
 * 對單一 URL 發送 form-urlencoded POST，失敗時重試——供需要跟隨 ASP.NET
 * WebForms postback（例如 GridView 分頁）的來源使用。
 */
async function fetchPostWithRetry(url, formBody) {
  let lastErr;
  for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
    try {
      const res = await axios.post(url, formBody, {
        timeout: TIMEOUT_MS,
        headers: {
          "User-Agent": USER_AGENT,
          "Content-Type": "application/x-www-form-urlencoded",
        },
      });
      return res.data;
    } catch (err) {
      lastErr = err;
    }
  }
  throw lastErr;
}

/**
 * 各來源的共同骨架：抓取 -> 解析 -> 正規化成 {title, link, source, publishDate}。
 * 子類別預設只需覆寫 parseList($)；若資料不是走一般 HTML（例如 DAAD 的
 * 靜態 JS 資料檔），可整個覆寫 fetchRaw()/parse()。
 */
class BaseCrawler {
  constructor(name, url) {
    this.name = name;
    this.url = url;
  }

  async fetchRaw() {
    return fetchWithRetry(this.url);
  }

  // 預設假設 raw 是 HTML，交給 cheerio 解析後呼叫 parseList($)
  async parse(raw) {
    const $ = cheerio.load(raw);
    return this.parseList($);
  }

  // 子類別覆寫：從 cheerio 物件中抓出 [{title, url, publishDate}]
  async parseList($) {
    return [];
  }

  async run() {
    try {
      const rawItems = (await this.parse(await this.fetchRaw())) || [];

      const items = rawItems
        .filter((i) => i && i.title && i.url)
        .map((i) => ({
          title: String(i.title).trim(),
          link: i.url,
          source: this.url,
          publishDate: i.publishDate || null,
        }));

      // 抓不到任何項目時記錄 status='empty'，不當成錯誤讓整個 pipeline 掛掉
      return { source: this.url, name: this.name, status: items.length > 0 ? "ok" : "empty", items };
    } catch (err) {
      return { source: this.url, name: this.name, status: "error", items: [], error: err.message };
    }
  }
}

// [WHY] 實測對東海大學獎學金頁面跑 GenericCrawler 診斷，165 筆裡有 18 筆（10.9%）
// 是「課程相關查詢」「校內公車查詢」「東海大學首頁」這類導覽選單雜訊，而非直接列舉
// 未實際出現過的詞——這批詞是從那次實測的 18 筆雜訊裡逐一歸納出來的，且已針對
// 154 筆真實獎學金標題確認過 0 筆誤判（不會不小心排掉真正的獎學金）。
const NAV_NOISE_WORDS = [
  "首頁", "關於我們", "聯絡我們", "網站導覽", "隱私權", "著作權", "無障礙",
  "查詢", "登入", "登出", "公車", "課程", "選課", "其他單位",
  "sitemap", "home", "about us", "contact us", "privacy", "course",
];

/**
 * 沒有專屬 parser 的來源（例如 Discord /addsource 動態新增的任意網址）的保底方案。
 * [WHY] 不用同一套 $('a').each() 硬掃全站連結（會抓到選單/頁尾雜訊），
 * 改為優先在常見的「主要內容」容器內找連結；仍是低信心的通用啟發式做法。
 */
class GenericCrawler extends BaseCrawler {
  async parseList($) {
    const scope = $("main, article, #content, .content").first();
    const root = scope.length ? scope : $("body");

    const items = [];
    const seen = new Set(); // [WHY] 同一個 title+href 重複出現（例如下拉選單產生的重複連結）只算一筆

    root.find("a").each((_, el) => {
      const title = $(el).text().trim();
      const href = $(el).attr("href");
      if (!title || !href || title.length < 6) return;

      const lowerTitle = title.toLowerCase();
      if (NAV_NOISE_WORDS.some((w) => lowerTitle.includes(w.toLowerCase()))) return;

      let absUrl;
      try {
        absUrl = new URL(href, this.url).toString();
      } catch {
        return;
      }

      const dedupeKey = `${title}::${absUrl}`;
      if (seen.has(dedupeKey)) return;
      seen.add(dedupeKey);

      items.push({ title, url: absUrl, publishDate: null });
    });
    return items;
  }
}

module.exports = { BaseCrawler, GenericCrawler, fetchWithRetry, fetchPostWithRetry };
