// [WHY] 使用者要求把 Discord 訊息改成「身份要求/申請條件/申請辦法/截止日期」四欄位
// 固定格式，但既有 crawler 只擷取公告「標題」，四個欄位在資料管線裡完全不存在
// （SCAN 已確認：db.js schema 只有 link/title/source/score/publishDate/foundAt）。
// 本檔案新增「抓詳情頁」這一層：對 filter() 之後真正會顯示給使用者的項目
// （不是全部 unseen 項目），額外發一次 request 到 item.link 本身，解析出四欄位。
// [WHY 只對 filter() 後的存活項目做] 詳情頁擷取比抓列表頁貴很多（457 筆全抓 vs
// 目前每輪只有個位數到數十筆通過），放在 ruleEngine.filter() 之後才做，
// 符合既有 Low-Compute/Lightweight 原則，避免爬蟲耗時暴增。
const cheerio = require("cheerio");
const { fetchWithRetry } = require("./base");
const logger = require("../logger");

const PAGE_DELAY_MS = 500; // 同來源內併發請求的間隔延遲（比原本 1s 降一半，併發後整體更快）
const BATCH_SIZE = 5; // 同時最多 5 個詳情頁請求，避免對來源伺服器造成壓力

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function joinNonEmpty(parts, sep) {
  const filtered = parts.filter((p) => p && String(p).trim());
  return filtered.length > 0 ? filtered.join(sep) : null;
}

// ===== MOE：`.title`（標籤）+ 緊接的 `.content`（值）成對出現 =====
function moeLabelMap($) {
  const map = {};
  $(".title").each((i, el) => {
    const label = $(el).text().replace(/\s+/g, "");
    const content = $(el).next(".content").text().trim().replace(/[ \t]+/g, " ");
    if (label) map[label] = content;
  });
  return map;
}

// [WHY] 教育部圓夢助學網的「申請期間」是民國年格式（例如「115/05/21～116/12/31」），
// 跟 THU 的西元年格式不同，需要 +1911 轉換，否則會產生錯誤的西元年份。
function parseMoeDeadline(range) {
  const m = String(range || "").match(/(\d{2,3})\/(\d{1,2})\/(\d{1,2})\s*[~～]\s*(\d{2,3})\/(\d{1,2})\/(\d{1,2})/);
  if (!m) return null;
  const adYear = Number(m[4]) + 1911;
  return `${adYear}-${String(m[5]).padStart(2, "0")}-${String(m[6]).padStart(2, "0")}`;
}

function parseMoeDetail($) {
  const map = moeLabelMap($);
  return {
    eligibility: joinNonEmpty([map["獎助身分"], map["獎助資格"], map["戶籍地限制"], map["學制"]], "；"),
    conditions: joinNonEmpty([map["成績"], map["限制條件"]], "；"),
    applicationMethod: joinNonEmpty([map["申請方式"], map["申請說明"]], "；"),
    deadline: parseMoeDeadline(map["申請期間"]),
  };
}

// ===== THU：`<td>` 純文字以「標籤：內容」形狀出現 =====
function thuLabelMap($) {
  const map = {};
  $("td").each((i, el) => {
    const text = $(el).text().trim();
    const m = text.match(/^([^：]{2,10})：([\s\S]*)$/);
    if (m) map[m[1].replace(/\s+/g, "")] = m[2].trim();
  });
  return map;
}

// [WHY] 東海大學的「學生申請日期」已經是西元年格式（例如「2026/02/23~2026/03/19」），
// 不需要像 MOE 那樣做民國年轉換。
function parseThuDeadline(range) {
  const m = String(range || "").match(/(\d{4})\/(\d{1,2})\/(\d{1,2})\s*[~～]\s*(\d{4})\/(\d{1,2})\/(\d{1,2})/);
  if (!m) return null;
  return `${m[4]}-${String(m[5]).padStart(2, "0")}-${String(m[6]).padStart(2, "0")}`;
}

function parseThuDetail($) {
  const map = thuLabelMap($);
  return {
    eligibility: joinNonEmpty([map["獎助學門"], map["獎助對象"]], "；"),
    conditions: joinNonEmpty([map["成績條件"], map["其他限制條件"]], "；"),
    applicationMethod: joinNonEmpty([map["申請說明"], map["應繳證件或附件"]], "；"),
    deadline: parseThuDeadline(map["學生申請日期"]),
  };
}

// ===== DAAD：`<h3>標籤</h3><p>內容</p>` 成對出現 =====
function daadLabelMap($) {
  const map = {};
  $("h3").each((i, el) => {
    const label = $(el).text().trim();
    const p = $(el).next("p");
    if (label && p.length) map[label] = p.text().trim();
  });
  return map;
}

const MONTH_NAMES = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december"];

// [WHY] DAAD 的 Application Deadline 常是自由文字（例如「deadlines differ and may
// be requested at the individual institutions」），無法可靠轉成 YYYY-MM-DD；只在
// 文字裡真的找得到具體日期時才轉換，找不到就回傳 null（渲染層顯示「未提供」），
// 不得為了湊格式而臆測一個不存在的日期。
function parseDaadDeadlineText(text) {
  if (!text) return null;
  const m = text.match(new RegExp(`(\\d{1,2})\\s+(${MONTH_NAMES.join("|")})\\s+(\\d{4})`, "i"));
  if (m) {
    const mo = MONTH_NAMES.indexOf(m[2].toLowerCase()) + 1;
    return `${m[3]}-${String(mo).padStart(2, "0")}-${String(m[1]).padStart(2, "0")}`;
  }
  const m2 = text.match(/(\d{1,2})\.(\d{1,2})\.(\d{4})/);
  if (m2) return `${m2[3]}-${String(m2[2]).padStart(2, "0")}-${String(m2[1]).padStart(2, "0")}`;
  return null;
}

function parseDaadDetail($) {
  const map = daadLabelMap($);
  return {
    eligibility: joinNonEmpty([map["Target Group"], map["Academic Requirements"]], "; "),
    conditions: map["Application Requirements"] || null,
    applicationMethod: map["Application Papers"] || null,
    deadline: parseDaadDeadlineText(map["Application Deadline"]),
  };
}

// ===== EFG：Drupal `.field-name-field-*` class（label 文字語言依投稿者而異，
// 例如英文 "Deadline:" 或德文 "Bewerbungsschluss:"，但 class 名稱本身穩定，
// 因此改用 class 選取而非 label 文字，見 SCAN 對照 EFG_3/EFG_4 樣本的結論）=====
function efgFieldValue($, className) {
  const el = $(`.${className}`).first();
  if (!el.length) return null;
  const text = el.find(".field-item").first().text().trim();
  return text || null;
}

// [WHY] EFG 的 Deadline 欄位常是「日.月.」無年份格式（例如「01.09.」，代表每年
// 固定週期截止，年份不明確），無法安全推算是哪一年，寧可留白也不猜測；只有
// 「日.月.年」完整格式才轉換。
function parseEfgDeadlineText(text) {
  if (!text) return null;
  const m = text.match(/(\d{1,2})\.(\d{1,2})\.(\d{4})/);
  if (m) return `${m[3]}-${String(m[2]).padStart(2, "0")}-${String(m[1]).padStart(2, "0")}`;
  return null;
}

function parseEfgDetail($) {
  const deadlineRaw = efgFieldValue($, "field-name-field-deadline");
  return {
    eligibility: efgFieldValue($, "field-name-field-eligible-country"),
    conditions: null, // [WHY][未驗證] EFG 詳情頁沒有穩定的 conditions 專屬欄位，內容混在自由文字說明裡，無法可靠擷取
    applicationMethod: null, // 同上，HOW TO APPLY 只是內文裡的自由格式標題，非穩定 DOM 欄位
    deadline: parseEfgDeadlineText(deadlineRaw),
  };
}

// [WHY] 沿用 crawler/index.js 的 createCrawler() 同一套「依網址正則比對」原則，
// 避免另外維護一份不同步的來源判斷邏輯。
function detectParser(link) {
  if (/daad\.de/i.test(link)) return parseDaadDetail;
  if (/edu\.tw\/helpdreams/i.test(link)) return parseMoeDetail;
  if (/fsis\.thu\.edu\.tw/i.test(link)) return parseThuDetail;
  if (/european-funding-guide\.eu/i.test(link)) return parseEfgDetail;
  return null; // GenericCrawler 來源沒有已知的詳情頁結構，不嘗試擷取
}

const EMPTY_DETAIL = { eligibility: null, conditions: null, applicationMethod: null, deadline: null };

async function fetchDetail(item) {
  const parser = detectParser(item.link);
  if (!parser) return { ...item, ...EMPTY_DETAIL };

  try {
    const html = await fetchWithRetry(item.link);
    const $ = cheerio.load(html);
    const detail = parser($);
    return { ...item, ...detail };
  } catch (err) {
    logger.error("detailParser:fetchDetail", err, { link: item.link });
    return { ...item, ...EMPTY_DETAIL };
  }
}

// [WHY] 只對「已通過 filter() 的存活項目」呼叫，不是對所有 unseen 項目——見檔案頂端說明。
// 改為批次併發：每批最多 BATCH_SIZE 筆同時抓，批次間間隔 PAGE_DELAY_MS，大幅縮短
// 大量項目的總等待時間（例如 91 筆 DAAD 從 ~3min 降至 ~30s），同時避免對來源伺服器
// 送出超過 BATCH_SIZE 的同時連線。
async function enrichWithDetails(items) {
  const enriched = [];
  for (let i = 0; i < items.length; i += BATCH_SIZE) {
    const batch = items.slice(i, i + BATCH_SIZE);
    const results = await Promise.all(batch.map((item) => fetchDetail(item)));
    enriched.push(...results);
    if (i + BATCH_SIZE < items.length) await sleep(PAGE_DELAY_MS);
  }
  return enriched;
}

module.exports = { enrichWithDetails, parseMoeDetail, parseThuDetail, parseDaadDetail, parseEfgDetail };
