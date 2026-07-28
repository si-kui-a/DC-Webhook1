// [WHY 本輪新增，邊界界定見 Meta_Dev_Knowledge.md] 使用者要求「爬蟲應可逐步按照
// 引用來源自動延伸擴充全世界公開獎學金網站」。這裡做的是「從既有已監測來源的頁面
// 內容裡，用純 DOM/正則抽取頁面內出現的外部連結，整理成候選清單」——純粹是連結
// 收集，不判斷「這個連結是不是獎學金網站」（那需要語意判斷，等同 AI，違反硬性
// 限制）。候選清單仍需使用者自己逐一用 /驗證來源 驗證、用 /addsource 二次確認，
// 系統不會自動收錄任何連結。
const cheerio = require("cheerio");
const { fetchWithRetry } = require("./base");

// [WHY] 常見雜訊網域黑名單，沿用 GenericCrawler 的 NAV_NOISE_WORDS 精神
// （從實測歸納出的雜訊特徵，而非臆測），這裡改用網域比對，因為社群媒體/廣告
// 追蹤連結的特徵在網域而非連結文字。
const SOCIAL_MEDIA_DOMAINS = [
  "facebook.com", "twitter.com", "x.com", "instagram.com", "line.me",
  "youtube.com", "linkedin.com", "pinterest.com", "tiktok.com", "whatsapp.com",
];

const AD_TRACKING_DOMAINS = [
  "google-analytics.com", "doubleclick.net", "googletagmanager.com",
  "googlesyndication.com", "googleadservices.com", "google.com",
];

// [WHY 本輪實測發現] 第一版只比對「完全相同的 hostname」，實測東海大學來源後發現
// 抓到 5 個「候選外部連結」全部都是同一個機構的其他子網域（cross.service.thu.edu.tw、
// bus.service.thu.edu.tw、www.thu.edu.tw 等），連結文字正是「輔系雙主修查詢」「校內
// 公車查詢」「東海大學首頁」——這正是 GenericCrawler 的 NAV_NOISE_WORDS 黑名單已經
// 歸納過的同一批雜訊，只是這裡用網域比對抓不到子網域的情況。改用「根網域」比對：
// 同一個機構的不同子網域（例如 fsis.thu.edu.tw 與 www.thu.edu.tw 根網域都是
// thu.edu.tw）視為內部連結，不是真正的「引用外部來源」。
const COMPOUND_TLDS = ["edu.tw", "com.tw", "gov.tw", "org.tw", "net.tw", "co.uk", "ac.uk", "co.jp"];

function getRootDomain(hostname) {
  const labels = hostname.split(".");
  for (const tld of COMPOUND_TLDS) {
    if (hostname === tld || hostname.endsWith(`.${tld}`)) {
      const tldLabelCount = tld.split(".").length;
      return labels.slice(-(tldLabelCount + 1)).join(".");
    }
  }
  return labels.slice(-2).join(".");
}

function isNoiseLink(href, baseRootDomain) {
  if (!href) return true;
  if (/^(mailto:|tel:|javascript:|#)/i.test(href.trim())) return true;

  let url;
  try {
    url = new URL(href, `https://${baseRootDomain}`);
  } catch {
    return true; // 無法解析成合法網址，視為雜訊
  }

  if (getRootDomain(url.hostname) === baseRootDomain) return true; // 同機構的其他子網域＝內部連結
  if (SOCIAL_MEDIA_DOMAINS.some((d) => url.hostname.endsWith(d))) return true;
  if (AD_TRACKING_DOMAINS.some((d) => url.hostname.endsWith(d))) return true;

  return false;
}

// [WHY existingUrls 參數化] 不在這支檔案裡直接 require sourceManager，讓呼叫端
// （discordBot.js）決定要排除哪些網址——保持這支模組單純只做「抓取＋DOM 解析」，
// 不涉及來源清單這個獨立關注點，方便未來若排除邏輯要調整時不用碰這支檔案。
async function extractReferencedLinks(sourceUrl, existingUrls = new Set()) {
  const html = await fetchWithRetry(sourceUrl);
  const $ = cheerio.load(html);
  const baseRootDomain = getRootDomain(new URL(sourceUrl).hostname);

  const found = new Map(); // url -> 連結文字（供使用者判斷用）

  $("a[href]").each((_, el) => {
    const href = $(el).attr("href");
    if (isNoiseLink(href, baseRootDomain)) return;

    let absUrl;
    try {
      absUrl = new URL(href, sourceUrl).toString();
    } catch {
      return;
    }

    if (existingUrls.has(absUrl)) return; // 已經在追蹤清單裡，不是候選
    if (!found.has(absUrl)) {
      found.set(absUrl, $(el).text().replace(/\s+/g, " ").trim().slice(0, 60));
    }
  });

  return Array.from(found.entries()).map(([url, text]) => ({ url, text }));
}

module.exports = { extractReferencedLinks };
