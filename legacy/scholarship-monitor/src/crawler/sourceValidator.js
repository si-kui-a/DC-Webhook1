// [WHY 本輪新增] 使用者要求「爬蟲應可自動延伸擴充全世界各國公開獎學金網站來源」，
// 但自動探索新網站需要語意判斷「這是不是獎學金公告列表頁」，等同需要 AI/語言偵測，
// 違反本專案的硬性限制。改為實作「使用者主導、系統輔助驗證」：使用者丟候選網址，
// 系統做批次驗證（連線/反爬蟲/SPA偵測/GenericCrawler 抓取診斷），通過與否都回報
// 具體理由，由使用者自己決定要不要正式加入——系統不自主探索、不自動納入。
const cheerio = require("cheerio");
const { fetchWithRetry, GenericCrawler } = require("./base");

// [WHY] 沿用既有 13 個候選來源 SCAN 時歸納出的失敗類型（見 README「來源清單」
// 段落的候選來源記錄），把當時人工判斷的標準寫成可重複執行的自動化檢查。
async function validateSource(url) {
  let html;
  try {
    html = await fetchWithRetry(url);
  } catch (err) {
    const status = err.response ? err.response.status : null;
    if (status === 403 || status === 429) {
      return { ok: false, reason: `連線被拒絕（HTTP ${status}），疑似反爬蟲阻擋（比照先前 British Council 遇到的情況）` };
    }
    return { ok: false, reason: `連線失敗：${err.message}` };
  }

  const $ = cheerio.load(html);
  const bodyText = $("body").clone().find("script, style").remove().end().text().replace(/\s+/g, " ").trim();
  const linkCount = $("a[href]").length;

  // [WHY 門檻] 100 字元、5 個連結是刻意寬鬆的下限——目的只是抓出「頁面幾乎沒有
  // 靜態內容、只有一個 <div id=root> 讓 JS 之後灌內容進去」這種明顯的 SPA 特徵，
  // 不是要精準判斷所有 SPA，寧可漏判（讓後續 GenericCrawler 診斷再抓一次真相）
  // 也不要誤判（把正常但內容較短的頁面錯誤地擋下來）。
  if (bodyText.length < 100 && linkCount < 5) {
    return {
      ok: false,
      reason: `頁面靜態內容過少（純文字僅 ${bodyText.length} 字、連結僅 ${linkCount} 個），疑似為前端動態渲染的 SPA，cheerio 只能解析伺服器端回應的原始 HTML，抓不到 JS 執行後才出現的內容（比照先前 ScholarshipsPortal.eu/Study in Sweden 遇到的情況）`,
    };
  }

  const crawler = new GenericCrawler("候選來源驗證", url);
  const result = await crawler.run();

  if (result.status !== "ok" || result.items.length === 0) {
    return {
      ok: false,
      reason: `GenericCrawler 保底邏輯抓取後沒有找到任何符合格式的項目（標題長度需 ≥ 6 字、且不含導覽詞黑名單如首頁/查詢/公車等），此網站可能只有大分類總覽連結、不是個別項目的清單頁（比照先前 Erasmus+ 官方站/JASSO 遇到的情況）`,
    };
  }

  return {
    ok: true,
    itemCount: result.items.length,
    rawLinkCount: linkCount,
    sampleTitles: result.items.slice(0, 5).map((i) => i.title),
  };
}

module.exports = { validateSource };
