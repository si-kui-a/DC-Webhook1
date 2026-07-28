const fs = require("fs");
const path = require("path");

const CONFIG_PATH = path.join(__dirname, "..", "config", "rules.json");
const PROFILE_PATH = path.join(__dirname, "..", "config", "profile.json");
const EXCLUDE_TERMS_PATH = path.join(__dirname, "..", "config", "exclude_terms.json");

// ===== 預設規則（config/rules.json 不存在時的 bootstrap fallback，正常情況下
// rules.json 本身就有進 git 追蹤，這裡只在意外被刪除時才會用到）=====
// [WHY] 中文關鍵字跟 config/rules.json 保持同步，避免 rules.json 意外遺失時
// 重新 bootstrap 回只有英文關鍵字的舊版預設值，等於讓這次的修復悄悄失效。
const DEFAULT_RULES = {
  weights: {
    phd: 3,
    master: 2,
    germany: 2,
    scholar: 1,
    獎學金: 2,
    助學金: 2,
    獎助學金: 2,
    優秀: 1,
    基金會: 1,
    清寒: 1,
    慈善: 1,
    文教: 1,
    紀念: 1,
    補助: 1,
    原住民: 1,
    僑生: 1,
    就學: 1,
    博士: 1,
    弱勢: 1,
    grant: 2,
    stipendium: 2,
  },
  threshold: 2,
};

// ===== 讀取規則 =====
function loadRules() {
  try {
    if (!fs.existsSync(CONFIG_PATH)) {
      fs.writeFileSync(CONFIG_PATH, JSON.stringify(DEFAULT_RULES, null, 2));
    }
    return JSON.parse(fs.readFileSync(CONFIG_PATH, "utf-8"));
  } catch (err) {
    console.error("ruleEngine load error:", err);
    return DEFAULT_RULES;
  }
}

// ===== 存規則（Discord 即時修改核心）=====
function saveRules(rules) {
  fs.writeFileSync(CONFIG_PATH, JSON.stringify(rules, null, 2));
}

// ===== scoring =====
function scoreItem(item, rules) {
  const title = normalizeTitle(item.title).toLowerCase();
  let score = 0;

  for (const [key, weight] of Object.entries(rules.weights)) {
    if (title.includes(key)) {
      score += weight;
    }
  }

  return score;
}

// ===== 讀取個人條件（第一層排除關鍵字）=====
// [WHY] 用 fs.readFileSync 每次現讀，不用 require()——require() 會快取模組結果，
// Discord /update 寫入 profile.json 後這裡若沒重讀就會用到舊資料，等於改了條件沒生效
// （README 已記錄過這是修過的 bug，這裡確保 exclude_keywords 也遵守同一原則）。
function loadProfile() {
  try {
    return JSON.parse(fs.readFileSync(PROFILE_PATH, "utf-8"));
  } catch (err) {
    console.error("ruleEngine loadProfile error:", err);
    return { exclude_keywords: [] };
  }
}

// ===== 多語言排除詞彙字典（本輪新建，詳見 config/exclude_terms.json 內的 [WHY] 說明）=====
// [WHY] 跟 loadRules()/loadProfile() 同一套「不快取、每次 filter() 現讀現解析」原則——
// 字典檔本身就是 Discord /update 之外，未來人工擴充語言時會直接編輯的檔案，
// 用 require() 快取的話，改了字典不重啟程式就不會生效，等於重蹈舊 bug。
const DEFAULT_EXCLUDE_TERMS = {
  income_restricted: {
    title_keywords: { zh: ["低收入戶", "中低收入戶", "清寒"] },
    qualifying_profile_values: ["low", "lower-middle", "低收入", "中低收入"],
  },
  special_status: {
    indigenous: { zh: ["原住民"] },
    overseas_student: { zh: ["僑生"] },
    refugee: { en: ["refugee"] },
    disability_illness: { zh: ["罕見疾病", "視障", "癌症", "身心障礙", "身障", "重大傷病", "特殊疾病"] },
    new_resident: { zh: ["新住民"] },
  },
  nationality_restricted: {
    taiwan_aliases: { zh: ["台灣", "臺灣", "中華民國"] },
    demonym_restricted: { en: ["Greeks", "Hellenes"] },
  },
  school_restricted: {
    name_suffix: { zh: ["大學", "學院"] },
  },
  grade_restricted: {
    graduate_only: {
      zh: ["博士生", "研究生", "碩士"],
      en_regex: ["\\bphd\\b", "\\bmaster(?:'|’)?s?\\b", "\\bdoctoral\\b", "\\bpostdoc(?:toral)?\\b"],
      de_regex_case_sensitive: ["\\bPromotion\\b"],
    },
    upperclass_only: { zh: ["限大三", "限大四"] },
    freshman_only: { zh: ["新生入學", "新生獎勵", "優秀新生"] },
  },
  residence_restricted: {},
};

function loadExcludeTerms() {
  try {
    if (!fs.existsSync(EXCLUDE_TERMS_PATH)) {
      fs.writeFileSync(EXCLUDE_TERMS_PATH, JSON.stringify(DEFAULT_EXCLUDE_TERMS, null, 2));
    }
    return JSON.parse(fs.readFileSync(EXCLUDE_TERMS_PATH, "utf-8"));
  } catch (err) {
    console.error("ruleEngine loadExcludeTerms error:", err);
    return DEFAULT_EXCLUDE_TERMS;
  }
}

// [WHY 本輪新增，Discord 對話式管理 exclude_terms.json] 沿用 db.js 的
// saveQueued()/writeProfileAtomic() 同一套「先寫暫存檔再 rename」原子寫入模式——
// rename 在同一個檔案系統內是原子操作，避免寫到一半時 loadExcludeTerms() 現讀
// 現解析剛好讀到損毀的中間狀態。
function saveExcludeTerms(dict) {
  const tmpPath = EXCLUDE_TERMS_PATH + ".tmp";
  fs.writeFileSync(tmpPath, JSON.stringify(dict, null, 2));
  fs.renameSync(tmpPath, EXCLUDE_TERMS_PATH);
}

// [WHY 白名單設計] 比照 /update 的 UPDATABLE_FIELDS、/rule 只能改既有 weights 鍵值
// 的既有安全原則——只允許新增/移除到「已經存在的分類+子分類」底下，不允許呼叫端
// 憑空建立新的分類結構（那需要同時決定要不要接上對應的排除函式邏輯，不是單純加
// 詞彙就好，屬於程式碼層級的變動，不該透過 Discord 指令做）。找不到對應分類/子分類
// 時回傳明確錯誤，讓上層（discordBot.js）可以列出目前有效的分類清單。
function getExcludeTermsPath(dict, category, subcategory, lang) {
  const cat = dict[category];
  if (!cat || typeof cat !== "object") return { error: `找不到分類「${category}」` };

  const sub = cat[subcategory];
  if (!sub || typeof sub !== "object") return { error: `分類「${category}」底下找不到子分類「${subcategory}」` };

  return { cat, sub, lang };
}

function addExcludeTerm(category, subcategory, lang, term) {
  const dict = loadExcludeTerms();
  const located = getExcludeTermsPath(dict, category, subcategory, lang);
  if (located.error) return { success: false, error: located.error };

  if (!Array.isArray(located.sub[lang])) located.sub[lang] = [];
  if (located.sub[lang].includes(term)) {
    return { success: false, error: `「${term}」已經存在於 ${category}.${subcategory}.${lang}，不需要重複新增` };
  }

  located.sub[lang].push(term);
  saveExcludeTerms(dict);
  return { success: true, terms: located.sub[lang] };
}

function removeExcludeTerm(category, subcategory, lang, term) {
  const dict = loadExcludeTerms();
  const located = getExcludeTermsPath(dict, category, subcategory, lang);
  if (located.error) return { success: false, error: located.error };

  const list = located.sub[lang];
  if (!Array.isArray(list) || !list.includes(term)) {
    return { success: false, error: `「${term}」不存在於 ${category}.${subcategory}.${lang}，無法移除` };
  }

  located.sub[lang] = list.filter((t) => t !== term);
  saveExcludeTerms(dict);
  return { success: true, terms: located.sub[lang] };
}

// [WHY 本輪四維度體檢新增] 全形英數字（例如「Ｇｅｒｍａｎｙ」）跟半形版本
// （"germany"）是不同的 Unicode 字元，.toLowerCase() 不會把全形轉成半形，
// 實測發現：半形「Germany Scholarship Grant Program」正確算出 score=5，但同一句子
// 換成全形後算出 0 分，等於這類標題的關鍵字計分/排除規則完全失效、被靜默漏放。
// 用 NFKC normalize 把全形英數字/標點正規化成半形canonical形式，中文字（CJK
// Unified Ideographs）不受影響（已實測驗證）。457 筆真實語料目前 0 筆出現全形字元
// （[未驗證] 情境，依體檢實測的邏輯缺陷先修正，跟其他 [未驗證] 詞彙同一個處理原則）。
function normalizeTitle(title) {
  return String(title || "").normalize("NFKC");
}

// [WHY] 純字串比對輔助函式，不是翻譯也不是語言偵測——只是把「用哪些字詞判斷」這件事
// 從程式碼裡搬到字典檔，比對邏輯本身（substring includes）完全沒變。
function matchesAnyPlainTerm(text, terms) {
  return (terms || []).some((t) => t && text.includes(t));
}

// [WHY] 英文/德文詞彙原本就是用 \b 詞界 + 大小寫（不）敏感的 regex（例如 /\bphd\b/i），
// 不能直接改成 substring includes()，否則會喪失詞界保護（例如 "phd" 誤判成某個更長
// 英文字的一部分）。這裡讓字典裡的 regex 字串比對维持跟原本寫死的 regex 完全一樣的行為。
function matchesAnyRegexTerm(text, patterns, flags) {
  return (patterns || []).some((p) => new RegExp(p, flags).test(text));
}

// [WHY] 台/臺是同一個字的兩種寫法（教育部公文常用「臺」，一般口語常用「台」），
// 公告標題可能用任一種，比對戶籍地/縣市限定時要先正規化，否則「台中市」跟
// 「臺中市」會被誤判成不同縣市而錯殺符合資格的項目。
function normalizeCityName(name) {
  return String(name || "").replace(/^台/, "臺").replace(/[市縣]$/, "");
}

// [WHY 這三條硬性排除規則的共同限制] 目前 crawler 只擷取公告「標題」，沒有擷取
// 內文（MOE/DAAD 都沒有可靠的內文欄位可用），所以這裡只能比對「限制條件寫在標題
// 本身」的情況——常見於地方政府/學校自辦的獎學金標題（例如「限台中市設籍」）。
// 若限制條件只寫在公告內文，這幾條規則會漏放，這是目前架構的已知限制，不是本次
// 要解決的範圍（擴大擷取內文屬於 crawler 層的改動，非 ruleEngine 的職責）。

// 排除「限OO市/縣設籍」但不是使用者戶籍地的項目
function isExcludedByResidence(title, profile) {
  const userCity = profile.residence_city;
  if (!userCity) return false; // 沒設定戶籍地就不啟用這條規則

  const m = title.match(/限([一-龥]{2,3}[市縣])(?:設籍|戶籍)/);
  if (!m) return false;

  return normalizeCityName(m[1]) !== normalizeCityName(userCity);
}

// [WHY 根因說明] 使用者回報「仍被推薦限他校獎學金」後，直接拿舊版 regex
// （/限([一-龥]{2,10}(?:大學|學院))(?:在學)?學生/）測試任務給的例句，結果是完全
// 不會觸發（match 為 null），確認三個具體缺口，不是憑空重寫：
// 1. 「限本校（清華大學）在校生」：「限」跟真正校名之間插了「本校（」三個字，
//    原本要求「限」緊接校名的比對邏輯直接斷裂
// 2. 「僅限台大、政大在學生」：台大/政大是常見簡稱（取全名前兩字），完全不含
//    「大學」「學院」這兩個字，原本要求的尾綴比對不到；而且是頓號列舉兩間學校，
//    原本的 regex 設計只處理單一校名
// 3. 「在校生」是「在學學生/學生」的常見同義詞，原本完全沒有涵蓋
// [WHY 本輪改動] 尾綴詞（大學/學院）原本寫死在這個常數字串裡，改成從
// excludeTerms.school_restricted.name_suffix.zh 動態組出來，供未來擴充語言使用
// （例如某天需要支援「XX University」這種英文校名時，加一個 en 語言鍵即可，
// 不用改這裡的組字邏輯）。頓號列舉需≥2項才放寬尾綴的判斷是結構性防假陽性邏輯，
// 不是詞彙清單，維持寫在 isExcludedBySchool() 裡不搬動。
function buildSchoolNamePattern(excludeTerms) {
  const suffixWords =
    (excludeTerms.school_restricted &&
      excludeTerms.school_restricted.name_suffix &&
      excludeTerms.school_restricted.name_suffix.zh) ||
    DEFAULT_EXCLUDE_TERMS.school_restricted.name_suffix.zh;
  return `[一-龥]{2,10}(?:${suffixWords.join("|")})`;
}

// [WHY] 台/臺是同一個字的兩種寫法，跟戶籍地比對同一個原則。
function normalizeSchoolName(name) {
  return String(name || "").replace(/^台/, "臺");
}

// [WHY] 判斷擷取到的校名是否指涉使用者自己的學校，除了完全相同，也接受
// 「取全名前幾個字當簡稱」這個台灣大學校名的通用慣例（東海大學↔東海、
// 台灣大學↔台大）。用 prefix 比對而非寫死一份簡稱對照表，才能適用任何
// profile.school 設定的學校，不用為每個可能的簡稱寫死清單。
function isSameSchool(candidate, userSchool) {
  const a = normalizeSchoolName(candidate);
  const b = normalizeSchoolName(userSchool);
  return a === b || a.startsWith(b) || b.startsWith(a);
}

// 排除「限OO大學(在學)學生」但不是使用者就讀學校的項目
function isExcludedBySchool(title, profile, excludeTerms) {
  const userSchool = profile.school;
  if (!userSchool) return false; // 沒設定就讀學校就不啟用這條規則

  const schoolNamePattern = buildSchoolNamePattern(excludeTerms);

  // 情境一：「限本校（實際校名）」括號澄清句型
  const parenMatch = title.match(new RegExp(`限本校[（(](${schoolNamePattern})[）)]`));
  if (parenMatch) return !isSameSchool(parenMatch[1], userSchool);

  // 情境二：單一完整校名（要求「大學/學院」尾綴，避免誤判「限台北市設籍學生」
  // 「限清寒學生」「限研究所學生」這類跟學校完全無關但同樣以「限...學生」結尾的句型）
  const singleMatch = title.match(new RegExp(`限(${schoolNamePattern})(?:在學)?學生`));
  if (singleMatch) return !isSameSchool(singleMatch[1], userSchool);

  // 情境三：頓號列舉多校簡稱，例如「僅限台大、政大在學生」。[WHY] 要求至少一個
  // 頓號（代表至少 2 個項目）才觸發，這是跟「限台中市設籍」「限清寒學生」這類
  // 單一數值限定句型的關鍵區別——城市/收入/年級限定在真實語料庫裡都是單一數值，
  // 不會用頓號列舉多個候選，這樣才能安全地放寬校名不需要「大學/學院」尾綴，
  // 又不會反過來誤判其他排除規則的範圍。
  const listMatch = title.match(/(?:僅)?限([一-龥]{2,4}(?:、[一-龥]{2,4})+)(?:在學|在校)?(?:學生|在校生)/);
  if (listMatch) {
    const names = listMatch[1].split("、").map((s) => s.trim()).filter(Boolean);
    return !names.some((n) => isSameSchool(n, userSchool));
  }

  return false;
}

// 排除「限低收入戶/中低收入戶/清寒」但使用者不是這幾個收入層級的項目
// [WHY SCAN 結果] 抽樣 DAAD(163)+EFG(125) 語料後確認 0 筆出現外語版本的低收入/
// need-based 限定用語，此類別本輪無新增，僅將原本寫死的兩個陣列搬進字典檔。
function isExcludedByIncome(title, profile, excludeTerms) {
  const dict = excludeTerms.income_restricted || DEFAULT_EXCLUDE_TERMS.income_restricted;
  const tierKeywords = (dict.title_keywords && dict.title_keywords.zh) || [];
  const qualifyingValues = dict.qualifying_profile_values || [];

  if (qualifyingValues.includes(profile.income_status)) return false;

  return matchesAnyPlainTerm(title, tierKeywords);
}

// [WHY] 跟戶籍/學校排除規則同一套字串比對邏輯，非新機制。
// 抽樣 MOE(15)+THU(154)+DAAD(163)+EFG(125) 共 457 筆真實標題，「限X國籍/限外籍生」
// 這種結構化句型目前完全沒出現過（唯一命中"german national"的是贊助機構名稱
// "German National Academic Foundation"，不是資格限定，證實不能只比對裸字）。
// 「限OO國籍」的結構化 pattern 依任務指示的定義先寫好（[未驗證]，要求「限」+
// 國籍/籍 + 學生/生 的中文句型，避免像 "german national" 這種贊助機構名稱裡
// 剛好出現相關字詞就誤判排除）。
// [WHY 不含「僑生」] 僑生原本放在這裡（先前幾輪的權宜做法），這輪新增「原住民」
// 排除規則時重新檢視，兩者都是「特定身份別」限定，跟「國籍」是不同概念——
// 原住民一樣持有台灣國籍，僑生資格也是依「僑居地」的行政認定、不是嚴格的國籍
// 分類。因此「僑生」搬到新的 isExcludedBySpecialStatus()，跟「原住民」歸類在一起，
// 分類更準確；這裡的 isExcludedByNationality() 只處理真正的國籍/籍別限定。
// [WHY 本輪新增 demonym 判斷] SCAN 發現中文「限OO國籍」regex 抓不到英文「for
// {民族/國籍形容詞}」這種句型（EFG 語料實測 2 筆：「for Greeks」「for Hellenes」）。
// 刻意只收錄實測出現過的 demonym，不做通用民族形容詞偵測，避免誤判「for
// Foreigners」「for International Students」這類對使用者有利、不該排除的用詞
// （語料裡確實同時存在這兩種相鄰案例，測試會涵蓋）。
function isExcludedByNationality(title, profile, excludeTerms) {
  const userCountry = profile.country;
  if (!userCountry) return false; // 沒設定國籍就不啟用這條規則

  const dict = excludeTerms.nationality_restricted || DEFAULT_EXCLUDE_TERMS.nationality_restricted;
  const taiwanAliases = (dict.taiwan_aliases && dict.taiwan_aliases.zh) || ["台灣", "臺灣", "中華民國"];
  const demonyms = (dict.demonym_restricted && dict.demonym_restricted.en) || [];

  // [WHY 本輪修復根因] 本輪回歸測試「限台中市設籍學生獎學金」時發現這條 regex
  // 既有的真實 bug（跟本次字典重構本身無關，是原本就存在的邏輯，重構前後行為
  // 一致，用同一份 regex 直接測試可重現）：`限([一-龥]{2,6})(?:國籍|籍)` 的
  // 「籍」分支太寬鬆，貪婪比對加回溯會把「限台中市設籍」誤擷取成「台中市設」+
  // 「籍」，把戶籍地登記用語「設籍」誤判成國籍限定。真正的國名不會包含
  // 市/縣/設/戶這些戶籍登記用字，用這個特徵排除誤判，不影響任何真正的國籍限定
  // 句型（例如「限日本籍」「限美國籍」）。
  const m = title.match(/限([一-龥]{2,6})(?:國籍|籍)(?:學生|生)?/);
  if (m && !/[市縣設戶]/.test(m[1])) {
    const isTaiwan = new RegExp(taiwanAliases.join("|")).test(m[1]);
    if (!isTaiwan) return true;
  }

  if (demonyms.length > 0 && new RegExp(`\\bfor (?:${demonyms.join("|")})\\b`, "i").test(title)) {
    return true;
  }

  return false;
}

// [WHY] 原住民（原住民族身份）與僑生（僑委會定義的旅外僑胞學生身份）都是
// 跟「國籍」無關的特定身份別限定，獨立成一個函式而非塞進 isExcludedByNationality()，
// 分類更準確。抽樣 457 筆真實標題實測：「原住民」出現 5 次（例如「東海大學原住民
// 學生優秀獎學金」，標題本身就是限定對象）、「僑生」出現 5 次，皆為明確限定該
// 身份專屬的獎學金名稱，不是「未提及」的情況。
// [WHY 本輪新增 refugee] SCAN 發現 DAAD/EFG 語料裡有 2 筆明確限定「難民身份」的
// 真實標題（Evangelisches Studienwerk「Scholarship for Students With Refugee
// Status」、DAAD「HessenFonds for Refugees and Researchers at Risk」），跟原住民/
// 僑生同屬「申請人特定身份別」限定、非國籍限定，歸在同一個 special_status 分類。
// 用 profile.is_refugee（本輪新增欄位，預設 false，比照 is_indigenous 的模式）判斷。
// [WHY 本輪新增 disability_illness] 使用者要求排除身心障礙/疾病類別限定的獎學金。
// 抽樣 457 筆真實標題找到 3 筆真實命中：「罕見疾病」「視障」（標題本身就是身份
// 限定字樣）、「癌症」（THU「台灣癌症基金會VS遠雄人壽」標題本身看不出來，抓取
// 詳情頁後確認其他限制條件寫明「父、母或本人罹患癌症目前治療中或完成治療兩年內」，
// 證實非單純贊助機構掛名）。「癌症」這類詞跟先前 nationality 規則踩過的「German
// National Academic Foundation」陷阱同一種風險類型（贊助機構名稱剛好含關鍵字，
// 不代表真的有身份限定）——只在只有 1 筆真實樣本、且已用詳情頁驗證過的情況下收錄，
// 若未來出現「XX癌症基金會」贊助但無疾病限定的反例，需要重新檢視。
// [WHY 本輪新增 new_resident] SCAN 發現上輪誤放進 school_restricted.name_suffix.zh
// 的「新住民」（東南亞/大陸籍配偶及其子女的行政認定身份）跟原住民/僑生/難民同屬
// 『申請人特定身份別』限定，不是學校或科系限定，移回 special_status 分類。實測
// THU 語料 2 筆真實命中：「新住民及其子女培力與獎助學金」「新應材新住民子女獎學金
// 計畫」，皆為明確限定該身份的獎學金名稱。用 profile.is_new_resident（本輪新增
// 欄位，預設 false，比照 is_indigenous 的模式）判斷。
function isExcludedBySpecialStatus(title, profile, excludeTerms) {
  const isIndigenous = profile.is_indigenous === true;
  const isOverseasStudent = profile.is_overseas_student === true;
  const isRefugee = profile.is_refugee === true;
  const isDisabilityOrIllness = profile.is_disability_or_illness === true;
  const isNewResident = profile.is_new_resident === true;

  const dict = excludeTerms.special_status || DEFAULT_EXCLUDE_TERMS.special_status;
  const indigenousTerms = (dict.indigenous && dict.indigenous.zh) || ["原住民"];
  const overseasTerms = (dict.overseas_student && dict.overseas_student.zh) || ["僑生"];
  const refugeeTerms = (dict.refugee && dict.refugee.en) || [];
  const disabilityIllnessTerms = (dict.disability_illness && dict.disability_illness.zh) || [];
  const newResidentTerms = (dict.new_resident && dict.new_resident.zh) || [];

  // [WHY 邊界案例] 「原住民/僑生優先，餘缺開放一般生」代表非該身份的申請人
  // 仍然可以申請（只是排序在後面），不是「限定該身份才能申請」，不應該排除；
  // 只有「明確限定僅該身份」才排除。目前 457 筆真實資料裡沒有出現這種句型
  // （[未驗證]），依任務指示的定義先寫好。這個防護對四個身份類別（原住民/僑生/
  // 難民/新住民）共用，因為都是同一種「優先非排他」的語意風險。
  const hasFallbackForGeneral = /餘缺.{0,6}(一般生|開放|皆可)|優先.{0,6}(餘缺|其餘|開放)/.test(title);
  if (hasFallbackForGeneral) return false;

  if (!isIndigenous && matchesAnyPlainTerm(title, indigenousTerms)) return true;
  if (!isOverseasStudent && matchesAnyPlainTerm(title, overseasTerms)) return true;
  if (!isRefugee && matchesAnyPlainTerm(title.toLowerCase(), refugeeTerms)) return true;
  if (!isDisabilityOrIllness && matchesAnyPlainTerm(title, disabilityIllnessTerms)) return true;
  if (!isNewResident && matchesAnyPlainTerm(title, newResidentTerms)) return true;

  return false;
}

// [WHY] 不是寫死「一律排除博士生/研究所獎學金」，而是動態比對 profile.degree/
// profile.grade——如果使用者之後真的變成碩士生/博士生，這條規則會自動不再排除
// 對應等級的獎學金，不需要改程式碼（跟戶籍/學校比對 profile 實際值的精神一致）。
// 抽樣 332 筆真實標題實測驗證：DAAD 36 筆現有通過項目裡，30 筆（83%）標題含
// PhD/Master/Doctoral 等研究所層級字樣；MOE/THU 則有「博士生」「研究生」（各 2 筆）、
// 「新生入學」（1 筆，且必須排除「新生代」這種完全不同語意的詞，兩者字面上都含
// "新生" 但「新生代」是「新一代」的意思，不是「剛入學新生」）。
// [WHY 本輪改動] GRADUATE_ONLY_PATTERNS/UPPERCLASS_ONLY_PATTERN/FRESHMAN_ONLY_PATTERN
// 原本寫死的中英文詞彙搬進 excludeTerms.grade_restricted 字典；「使用者本身是研究所
// 學生時不排除」「年級數字 vs 限定條件」這些年級 vs 學制混淆防護是結構性邏輯判斷，
// 不是詞彙清單，維持寫在 isExcludedByGrade() 裡不搬動。
// [WHY 本輪新增 de Promotion] SCAN 發現 DAAD 語料裡有 3 筆德文「Promotion」
// （德文學術用語，意指攻讀博士學位），且都能找到同方案的「...Master」姊妹項目
// 互相對照，證實是穩定的博士班軌道標籤。大小寫敏感比對（不加 i flag），因為德文
// 名詞字首大寫本身就是跟英文一般語意小寫用詞「promotion（升遷/促銷）」的天然區別。

function isGraduateStudent(profile) {
  const degree = String(profile.degree || "").toLowerCase();
  return degree === "master" || degree === "phd" || degree === "doctoral";
}

function isExcludedByGrade(title, profile, excludeTerms) {
  if (!profile.degree && !profile.grade) return false; // 沒設定學制/年級就不啟用

  const dict = excludeTerms.grade_restricted || DEFAULT_EXCLUDE_TERMS.grade_restricted;
  const graduateOnly = dict.graduate_only || {};
  const upperclassTerms = (dict.upperclass_only && dict.upperclass_only.zh) || [];
  const freshmanTerms = (dict.freshman_only && dict.freshman_only.zh) || [];

  const isGraduateOnlyTitle =
    matchesAnyPlainTerm(title, graduateOnly.zh) ||
    matchesAnyRegexTerm(title, graduateOnly.en_regex, "i") ||
    matchesAnyRegexTerm(title, graduateOnly.de_regex_case_sensitive, "");

  // 使用者本身是研究所學生的話，博士生/研究生類限定不算排除條件（他本來就符合）
  if (!isGraduateStudent(profile) && isGraduateOnlyTitle) {
    return true;
  }

  const grade = Number(profile.grade);

  // 「限大三以上」：只有大二（含以下）的大學部學生會被排除
  if (matchesAnyPlainTerm(title, upperclassTerms) && !isGraduateStudent(profile) && grade && grade < 3) {
    return true;
  }

  // 「新生入學」（剛入學新生限定）：只有非大一新生會被排除
  if (matchesAnyPlainTerm(title, freshmanTerms) && grade && grade !== 1) {
    return true;
  }

  return false;
}

// ===== filter 主邏輯 =====
function filter(items) {
  const rules = loadRules();
  const profile = loadProfile();
  const excludeTerms = loadExcludeTerms();
  const excludeKeywords = profile.exclude_keywords || [];

  // 第一層過濾：命中排除關鍵字，或命中戶籍地/學校/收入層級/國籍/特定身份別/年級的
  // 硬性排除規則，就直接剔除，不計分，無論第二層關鍵字分數多高都不放行
  const survivors = items.filter((i) => {
    const rawTitle = normalizeTitle(i.title);
    const lowerTitle = rawTitle.toLowerCase();

    if (excludeKeywords.some((kw) => kw && lowerTitle.includes(String(kw).toLowerCase()))) return false;
    if (isExcludedByResidence(rawTitle, profile)) return false;
    if (isExcludedBySchool(rawTitle, profile, excludeTerms)) return false;
    if (isExcludedByIncome(rawTitle, profile, excludeTerms)) return false;
    if (isExcludedByNationality(rawTitle, profile, excludeTerms)) return false;
    if (isExcludedBySpecialStatus(rawTitle, profile, excludeTerms)) return false;
    if (isExcludedByGrade(rawTitle, profile, excludeTerms)) return false;

    return true;
  });

  return survivors
    .map((i) => {
      const score = scoreItem(i, rules);
      return { ...i, score };
    })
    .filter((i) => i.score >= rules.threshold)
    .sort((a, b) => b.score - a.score);
}

// ===== Discord 可調整 API =====
function updateRule(key, value) {
  const rules = loadRules();

  if (key === "threshold") {
    rules.threshold = Number(value);
  } else {
    // weight 更新
    rules.weights[key] = Number(value);
  }

  saveRules(rules);
  return rules;
}

function getRules() {
  return loadRules();
}

module.exports = {
  filter,
  updateRule,
  getRules,
  loadExcludeTerms,
  addExcludeTerm,
  removeExcludeTerm,
};