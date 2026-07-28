// [WHY] 原檔案的 selector（table tr / .list-item / .announce-list li）是未經驗證的樣板，
// 而且 https://www.dream.tcte.edu.tw/ 這個網址目前 DNS 已失效（ERR_NAME_NOT_RESOLVED）。
// 教育部圓夢助學網目前的正確網址是 https://www.edu.tw/helpdreams/，實際抓取後確認：
// 分類清單頁（Content_List.aspx?n=D50A7AEB3C165858）列出「民間團體獎助學金」與
// 「政府機關獎助學金」兩個分類，各自的清單頁用 ASP.NET GridView 渲染，
// 真正的公告列表在 table#ContentPlaceHolder1_gvIndex 裡，每列一筆，
// 標題連結固定用 class="css_mark"，藉此排除表頭與雜訊。
//
// [WHY 多分類參數化] 兩個分類的頁面結構完全相同，只有 n/sms 這兩個 query 參數不同，
// 不寫成兩個 class 複製貼上——不然日後 selector 改版（例如 css_mark 換掉）要改兩個
// 檔案、容易漏改一個。用 CATEGORIES 陣列 + 迴圈跑同一套抓取/解析邏輯。
const cheerio = require("cheerio");
const { BaseCrawler, fetchWithRetry, fetchPostWithRetry } = require("../base");

const BASE_URL = "https://www.edu.tw/helpdreams/";

const CATEGORIES = [
  { label: "民間團體獎助學金", n: "2BBF7170197CE7D3", sms: "0A01A72AAB9E5CD4" },
  { label: "政府機關獎助學金", n: "11EFF33070D6DF4B", sms: "931FF851D2FB2128" },
];

// [WHY 分頁安全上限] 就算真的遇到多頁分類，也不能無限跟著分頁一路爬下去——
// 上限 + 「這頁抓不到任何項目就停」雙重終止條件，避免意外把整站分頁全爬完。
const MAX_PAGES_PER_CATEGORY = 5;
// [WHY] 對目標伺服器的基本禮貌：分頁之間的請求不要無間隔連發，降低被視為
// 異常流量而封鎖 IP 的機率，也是這個爬蟲能長期穩定運作的前提。
const PAGE_DELAY_MS = 1000;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function categoryUrl(cat) {
  return `${BASE_URL}Grants.aspx?n=${cat.n}&sms=${cat.sms}`;
}

// 從單一頁面的 HTML 抓出公告列表（標題/連結）
function extractRows($) {
  const items = [];

  $("#ContentPlaceHolder1_gvIndex tr").each((_, row) => {
    const link = $(row).find("a.css_mark").first();
    const title = link.text().trim();
    const href = link.attr("href");
    if (!title || !href) return; // 跳過表頭列（沒有 a.css_mark）

    items.push({
      title,
      url: new URL(href, BASE_URL).toString(),
      publishDate: null, // 頁面只列「申請期間(迄)」，沒有公告發布日期
    });
  });

  return items;
}

// [WHY][未驗證] 這個分類清單頁是標準 ASP.NET WebForms + GridView，分頁通常靠
// __doPostBack('ctl00$ContentPlaceHolder1$gvIndex', 'Page$N') 觸發，帶著上一頁回應
// 裡的 __VIEWSTATE/__EVENTVALIDATION 等隱藏欄位一起 POST 回去。實測目前兩個分類
// （民間團體 15 筆單頁、政府機關 0 筆）都沒有渲染出分頁列（HTML 裡找不到任何
// "Page$" 的 __doPostBack 呼叫），所以這段邏輯在目前的真實網站行為下不會被觸發、
// 也就沒有真實的多頁資料可以驗證它是否正確。保留這段是為了在分類累積超過一頁
// 資料時能自動跟上，但第一次真的被觸發時，必須人工核對抓到的結果是否正確，
// 不能假設它一定沒問題。
function findPager($, targetPage) {
  let found = null;

  $("a[href*='__doPostBack']").each((_, el) => {
    const href = $(el).attr("href") || "";
    const m = href.match(/__doPostBack\('([^']+)'\s*,\s*'Page\$(\d+)'\)/);
    if (m && Number(m[2]) === targetPage) {
      found = { eventTarget: m[1], eventArgument: `Page$${targetPage}` };
    }
  });

  return found;
}

function collectHiddenFields($) {
  const fields = {};
  $("input[type=hidden]").each((_, el) => {
    const name = $(el).attr("name");
    if (name) fields[name] = $(el).attr("value") || "";
  });
  return fields;
}

async function fetchNextPage(url, $current, pager) {
  const hidden = collectHiddenFields($current);
  const form = new URLSearchParams({
    ...hidden,
    __EVENTTARGET: pager.eventTarget,
    __EVENTARGUMENT: pager.eventArgument,
  });

  return fetchPostWithRetry(url, form.toString());
}

class MoeCrawler extends BaseCrawler {
  // [WHY 本輪修復根因] 這個 crawler 橫跨多個分類，沒有單一固定目標網址；this.url
  // 只作為 run() 正規化結果時的 source 標籤，實際抓取在 fetchRaw() 對每個分類各自
  // 處理（跟 this.url 完全無關）。**根因**：先前這裡寫死一個獨立的「分類清單頁」
  // 網址（Content_List.aspx，用來人工瀏覽分類入口的頁面），但
  // config/sources.seed.json 儲存的是其中一個分類的實際網址（Grants.aspx），
  // 兩者不同，導致 sourceManager.updateSourceHealth() 用完全字串比對永遠找不到
  // 對應紀錄，MOE 的健康分數/lastChecked 從未真正更新過（實測發現，詳見
  // Meta_Dev_Knowledge.md）。**修復**：改成跟其他 3 個 crawler 一致的「single
  // source of truth」模式——url 由 createCrawler(source) 傳入 sources.seed.json
  // 儲存的網址，這樣不管 sources.seed.json 存哪個分類的網址，都保證跟
  // updateSourceHealth() 比對時用的值完全一致，不會再各自維護出兩份可能不同步
  // 的值。沒有傳入 url 時（例如直接 new MoeCrawler() 做單元測試）才 fallback 到
  // 分類清單頁常數，純粹是保留舊行為的便利性，不影響正式排程行為。
  constructor(url) {
    super("教育部圓夢助學網", url || `${BASE_URL}Content_List.aspx?n=D50A7AEB3C165858`);
  }

  async fetchOneCategory(cat) {
    const url = categoryUrl(cat);
    let html = await fetchWithRetry(url);
    let $ = cheerio.load(html);
    let items = extractRows($);

    let page = 1;
    while (page < MAX_PAGES_PER_CATEGORY) {
      const pager = findPager($, page + 1);
      if (!pager) break; // 沒有分頁列（目前兩個分類的真實情況）就到此為止

      await sleep(PAGE_DELAY_MS);

      html = await fetchNextPage(url, $, pager);
      $ = cheerio.load(html);
      const nextItems = extractRows($);
      if (nextItems.length === 0) break; // 空頁，停止（雙重終止條件之一）

      items = items.concat(nextItems);
      page += 1;
    }

    return items;
  }

  // 覆寫 fetchRaw()：橫跨多個分類抓取，而不是 BaseCrawler 預設的單一 URL
  async fetchRaw() {
    const allItems = [];

    for (const cat of CATEGORIES) {
      const items = await this.fetchOneCategory(cat);
      allItems.push(...items);
      // 分類之間也保持間隔，跟分頁之間的禮貌延遲同一個原則
      await sleep(PAGE_DELAY_MS);
    }

    return allItems;
  }

  // fetchRaw() 已經回傳最終格式的項目陣列，parse() 這裡不用再做任何轉換
  async parse(rawItems) {
    return rawItems;
  }
}

module.exports = MoeCrawler;
