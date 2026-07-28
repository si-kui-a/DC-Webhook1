require('dotenv').config();
const fs = require('fs');
const path = require('path');
const { Client, GatewayIntentBits, Partials, ChannelType } = require('discord.js');

const logger = require('./logger');
const { updateRule, getRules, loadExcludeTerms, addExcludeTerm, removeExcludeTerm } = require('./ruleEngine');
const { validateSource } = require('./crawler/sourceValidator');
const { extractReferencedLinks } = require('./crawler/linkExtractor');
const sourceManager = require('./sourceManager');
const db = require('./db');
const { fmtItem, sourceDisplayName, filterByDeadline, regionForSource, getAllRegions } = require('./report/format');

const PROFILE_PATH = path.join(__dirname, '..', 'config', 'profile.json');
const AUTHORIZED_USER_ID = String(process.env.DISCORD_USER_ID || '');

// [WHY] /獎學金說明 [編號] 對應上一次 /查詢獎學金 或 /篩選獎學金 的清單順序，
// 見 handleMessage() 裡的完整說明。
let lastQueryResults = [];

const UPDATABLE_FIELDS = new Set([
  'country',
  'current_country',
  'degree',
  'major',
  'grade',
  'gpa',
  'income_status',
  'residence_city',
  'school',
  'is_indigenous',
  'is_overseas_student',
  'is_refugee',
  'is_disability_or_illness',
]);

const DISCORD_CONTENT_LIMIT = 2000;

// =========================
// 🚦 Rate limit（寫入類指令）
// =========================
// [WHY] DISCORD_USER_ID 白名單只能擋「非本人」的訊息，如果 Token 外洩導致
// 本人帳號被盜用，盜用者發出的指令一樣會通過白名單檢查——rate limit 是這個情境下
// 唯一還能發揮作用的防線。只限制「會寫入/改變系統狀態」的指令（/update、/rule、
// /addsource、/removesource），純查詢類（/myprofile、/rules、/sources、/help）不受影響。
// [WHY 閾值] 個人專用系統，正常使用情境下一天可能改個幾次 profile/rules，
// 但剛設定或調整權重時可能連續試幾組數值——每分鐘 5 次的滑動視窗足夠覆蓋這種
// 手動連續操作，同時把異常高頻（每秒數十次）的自動化濫用限制到近乎無效。
const RATE_LIMIT_WINDOW_MS = 60 * 1000;
const RATE_LIMIT_MAX = 5;

const WRITE_COMMAND_PATTERNS = [
  /^\/update \S+=.+$/,
  // [WHY] 原本用 \w+ 比對 key，但 \w 只匹配 [A-Za-z0-9_]，中文權重名稱
  // （例如「獎學金」）完全比對不到，會讓 /rule 獎學金 3 這種指令連 rate limit
  // 都偵測不到（進而導致下面 handleMessage 的同一組 regex 也比對失敗、指令整個
  // 不會被執行）。改用 \S+（非空白字元）支援任何語言的關鍵字名稱。
  /^\/rule \S+ \d+$/,
  /^\/addsource \S+ https?:\/\/\S+$/,
  /^\/removesource https?:\/\/\S+$/,
  /^\/排除關鍵字新增 \S+ \S+ \S+ .+$/,
  /^\/排除關鍵字移除 \S+ \S+ \S+ .+$/,
  // [WHY 本輪新增] 簡化版指令，寫入 profile.exclude_keywords（純字串陣列，
  // 不需要分類/子分類/語言三個參數），同樣是會改變系統狀態的寫入類指令，
  // 納入同一套 rate limit 保護。
  /^\/排除關鍵字\s+.+$/,
  /^\/移除排除關鍵字\s+.+$/,
];

// userId -> 該使用者最近寫入類指令的 timestamp 陣列（記憶體內，重啟即清空）
const writeCommandTimestamps = new Map();

// [WHY] 用陣列 + filter 做滑動視窗，而不是固定時間窗重置的計數器——
// 固定視窗在窗口邊界會有「一瞬間發兩倍量」的漏洞，滑動視窗才是真的每分鐘限流。
function checkRateLimit(userId) {
  const now = Date.now();
  const recent = (writeCommandTimestamps.get(userId) || []).filter(
    (t) => now - t < RATE_LIMIT_WINDOW_MS,
  );

  if (recent.length >= RATE_LIMIT_MAX) {
    writeCommandTimestamps.set(userId, recent);
    return false;
  }

  recent.push(now);
  writeCommandTimestamps.set(userId, recent);
  return true;
}

// =========================
// 🧠 Singleton guards
// =========================
let _clientInstance = null;
let _started = false;
let _listenerBound = false;

// =========================
// utils
// =========================
function splitMessage(message) {
  if (message.length <= DISCORD_CONTENT_LIMIT) return [message];

  const segments = message.split('━━━━━━━━━━');
  const chunks = [];
  let current = '';

  for (const seg of segments) {
    const candidate = current ? `${current}━━━━━━━━━━${seg}` : seg;
    if (candidate.length > DISCORD_CONTENT_LIMIT) {
      if (current) chunks.push(current);
      current = seg;
    } else {
      current = candidate;
    }
  }

  if (current) chunks.push(current);
  return chunks;
}

// [WHY 本輪新增] 使用者回報 /查詢獎學金 找到 59 筆卻只顯示 20 筆，其餘被砍掉——
// 這是舊版 `.slice(0, 20)` 的人為硬性上限，不是 Discord 本身的限制。splitMessage()
// 是針對「用 ━━━━━━━━━━ 分隔的完整四欄位報告」設計的（見 fmtItem()/send()），
// /查詢獎學金、/篩選獎學金 的清單是「一行一筆」的短格式、行與行之間沒有這個分隔線，
// 直接套用 splitMessage() 不會產生任何切割點。改用這個以「行」為單位的通用分段
// 函式：把清單依序塞進訊息，超過 2000 字元上限就開新的一則訊息，確保全部筆數都會
// 送出，只是拆成多則訊息，不會像過去那樣直接砍掉後面的項目。
function chunkLines(lines, limit = DISCORD_CONTENT_LIMIT) {
  const chunks = [];
  let current = '';
  for (const line of lines) {
    const candidate = current ? `${current}\n${line}` : line;
    if (candidate.length > limit) {
      if (current) chunks.push(current);
      current = line;
    } else {
      current = candidate;
    }
  }
  if (current) chunks.push(current);
  return chunks;
}

// [WHY 本輪統一修復，Universal Principle 見 Meta_Dev_Knowledge.md] 先前每個指令
// 各自用 `if (fullRegex.test(content))` 判斷是否要處理——只要輸入沒有完全符合
// 完整格式（缺參數、多打一個字、格式錯誤），就直接落空、不進到任何分支，等於
// 完全無回應。兩輪 SCAN 分別發現這是系統性落差，不是單一指令的個案。統一用這個
// helper：只要輸入「看起來是打算呼叫這個指令」（用指令名稱本身比對，容許後面
// 接空白或到此為止，藉此避免 /rule 誤判成 /rules 的前綴衝突），但不符合完整格式，
// 就明確回覆用法錯誤，不會再有「打對指令名稱但格式錯一點就完全沒反應」的情況。
function matchOrUsageError(commandName) {
  return (content, fullRegex) => {
    const looksIntended = content === `/${commandName}` || content.startsWith(`/${commandName} `);
    if (!looksIntended) return { intended: false };

    const match = content.match(fullRegex);
    if (!match) return { intended: true, match: null };

    return { intended: true, match };
  };
}

function readProfile() {
  return JSON.parse(fs.readFileSync(PROFILE_PATH, 'utf-8'));
}

function writeProfileAtomic(profile) {
  const tmpPath = PROFILE_PATH + '.tmp';
  fs.writeFileSync(tmpPath, JSON.stringify(profile, null, 2));
  fs.renameSync(tmpPath, PROFILE_PATH);
}

function isAuthorized(msg) {
  return AUTHORIZED_USER_ID && String(msg.author.id) === AUTHORIZED_USER_ID;
}

// =========================
// Discord client
// =========================
const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.DirectMessages,
    GatewayIntentBits.MessageContent,
  ],
  partials: [Partials.Channel, Partials.Message],
});

let resolveReady;
const readyPromise = new Promise((r) => (resolveReady = r));

// 供 index.js 在 --once 測試模式下確認 Bot 真的 ready 過，而不只是呼叫了 login()
function whenReady() {
  return readyPromise;
}

// =========================
// send
// =========================
async function send(message) {
  await readyPromise;

  const user = await client.users.fetch(AUTHORIZED_USER_ID);
  const chunks = splitMessage(message);

  for (const chunk of chunks) {
    await user.send(chunk);
  }
}

// =========================
// alerts
// =========================
async function sendHealthAlert(details) {
  return send(`⚠️ 系統健康告警\n\n${details}`);
}

async function sendRedesignAlert(details) {
  return send(`🔧 網站改版警告\n\n${details}`);
}

// =========================
// messageCreate 處理邏輯
// [WHY] 抽成獨立函式（而非匿名 callback 直接塞進 client.on）：方便直接餵假的
// message 物件做單元測試（稽核 log 的成功/未授權情境），不需要真的連上 Discord。
// =========================
async function handleMessage(msg) {
  if (msg.author.bot) return;
  if (msg.channel.type !== ChannelType.DM) return;

  if (!isAuthorized(msg)) {
    // [WHY] 這不是系統錯誤，是需要稽核追蹤的存取嘗試（例如 Token 外洩後的探測），
    // 所以寫進 activity.log 而非 error.log。訊息內容只取前 200 字摘要，
    // 足以事後判斷是不是惡意嘗試，同時避免整段訊息把 log 灌爆。
    logger.activity('discordBot:unauthorized', {
      userId: msg.author.id,
      messageSummary: (msg.content || '').slice(0, 200),
    });
    return;
  }

  const content = (msg.content || '').trim();

  // -------------------------
  // rate limit（只擋寫入類指令，查詢類指令不受影響）
  // -------------------------
  if (WRITE_COMMAND_PATTERNS.some((p) => p.test(content))) {
    if (!checkRateLimit(msg.author.id)) {
      logger.activity('discordBot:rateLimited', { userId: msg.author.id, command: content });
      await msg.reply(`⏳ 操作過於頻繁，請稍後再試（寫入類指令每 ${RATE_LIMIT_WINDOW_MS / 1000} 秒最多 ${RATE_LIMIT_MAX} 次）。`);
      return;
    }
  }

  // -------------------------
  // profile
  // -------------------------
  if (/^\/myprofile$/.test(content)) {
    const profile = readProfile();
    const lines = Array.from(UPDATABLE_FIELDS)
      .map(k => `• ${k}: ${profile[k]}`);

    // [WHY 本輪新增] exclude_keywords 是陣列，不屬於 UPDATABLE_FIELDS（那是給
    // /update 單一純量欄位用的白名單），但既然新增了 /排除關鍵字、/移除排除
    // 關鍵字 這兩個操作它的指令，/myprofile 也該讓使用者看得到目前內容，
    // 否則只能透過每次新增/移除的回覆訊息片段拼湊，沒有一次性總覽的地方。
    const excludeKeywords = Array.isArray(profile.exclude_keywords) ? profile.exclude_keywords : [];
    lines.push(`• exclude_keywords: ${excludeKeywords.length > 0 ? excludeKeywords.join('、') : '（空）'}`);

    await msg.reply("📋 Profile:\n" + lines.join('\n'));
    return;
  }

  // -------------------------
  // update profile
  // -------------------------
  const updateAttempt = matchOrUsageError('update')(content, /^\/update (\S+)=(.+)$/);
  if (updateAttempt.intended) {
    if (!updateAttempt.match) {
      await msg.reply('❌ 格式錯誤，正確用法：/update 欄位=值，例如 /update gpa=3.8');
      return;
    }
    const [, key, rawValue] = updateAttempt.match;

    if (!UPDATABLE_FIELDS.has(key)) {
      await msg.reply(`❌ 不支援欄位: ${key}`);
      return;
    }

    const profile = readProfile();
    const oldValue = profile[key];

    // [WHY] is_indigenous/is_overseas_student/is_refugee 是 ruleEngine.js 用 === true
    // 嚴格比對的布林值，如果跟其他欄位一樣存成原始字串（例如 "true"），比對永遠不會
    // 成立，這條規則會悄悄失效。比照 gpa 用 parseFloat 的既有做法，這三個欄位也要轉型。
    const BOOLEAN_FIELDS = new Set(['is_indigenous', 'is_overseas_student', 'is_refugee', 'is_disability_or_illness']);
    if (key === 'gpa') {
      profile[key] = parseFloat(rawValue);
    } else if (BOOLEAN_FIELDS.has(key)) {
      profile[key] = rawValue.toLowerCase() === 'true';
    } else {
      profile[key] = rawValue;
    }

    writeProfileAtomic(profile);

    // [WHY] 稽核紀錄：誰（授權使用者，白名單內只有一人，但仍記錄 userId 供比對）
    // 在什麼時候把哪個欄位從什麼值改成什麼值——跟 discordBot:rateLimited 用同一套
    // logger.activity(eventName, detail) 格式，事件名稱同樣以 discordBot: 開頭。
    logger.activity('discordBot:update', {
      userId: msg.author.id,
      field: key,
      oldValue,
      newValue: profile[key],
    });

    await msg.reply(`✅ 更新完成\n${key}: ${oldValue} → ${profile[key]}`);
    return;
  }

  // -------------------------
  // rules
  // -------------------------
  if (/^\/rules$/.test(content)) {
    const rules = getRules();
    await msg.reply("📊 Rules:\n" + JSON.stringify(rules, null, 2));
    return;
  }

  // [WHY] \S+（非空白字元）取代原本的 \w+（只匹配 [A-Za-z0-9_]），
  // 否則中文權重名稱（例如「獎學金」）永遠無法透過 /rule 指令調整。
  // [WHY looksIntended 用「/rule」+空白判斷] 避免跟 /rules 前綴衝突——"/rules"
  // 不會被誤判成 /rule 指令，因為 "/rule" 後面接的是 "s" 不是空白。
  const ruleAttempt = matchOrUsageError('rule')(content, /^\/rule (\S+) (\d+)$/);
  if (ruleAttempt.intended) {
    if (!ruleAttempt.match) {
      await msg.reply('❌ 格式錯誤，正確用法：/rule 關鍵字 分數（分數須為整數），例如 /rule 獎學金 3');
      return;
    }
    const [, key, value] = ruleAttempt.match;

    const rules = updateRule(key, value);

    await msg.reply("✅ Updated rules\n" + JSON.stringify(rules, null, 2));
    return;
  }

  // -------------------------
  // 排除關鍵字管理（本輪新增，對話式管理 config/exclude_terms.json）
  // -------------------------
  // [WHY 沿用既有安全機制] 跟 /update（UPDATABLE_FIELDS 白名單）、/rule（只能改
  // 已存在的 weights 鍵）同一套原則：只允許新增/移除到「已經存在的分類+子分類」，
  // 不開放憑空建立新分類結構（那牽涉要不要接上對應排除函式邏輯，是程式碼層級的
  // 決定，不該透過 Discord 指令做）。這兩個指令已加進 WRITE_COMMAND_PATTERNS，
  // 跟 /update、/rule 一樣受 rate limit 保護，並寫入 activity.log 稽核紀錄。
  //
  // 語法：/排除關鍵字新增 分類 子分類 語言 詞彙（4 個參數，空格分隔，詞彙可以
  // 含空白，例如英文片語）。使用者原本建議的例句是 3 參數（分類/語言/詞彙），
  // 但 exclude_terms.json 實際結構是「分類 → 子分類 → 語言 → 詞彙陣列」的巢狀
  // 結構（例如 school_restricted 底下還有 name_suffix 子分類），3 參數無法定位到
  // 正確位置，因此擴充為 4 參數，在此明確記錄與使用者原始建議的差異。
  const addTermAttempt = matchOrUsageError('排除關鍵字新增')(content, /^\/排除關鍵字新增 (\S+) (\S+) (\S+) (.+)$/);
  if (addTermAttempt.intended) {
    if (!addTermAttempt.match) {
      await msg.reply('❌ 格式錯誤，正確用法：/排除關鍵字新增 分類 子分類 語言 詞彙，例如 /排除關鍵字新增 school_restricted name_suffix zh 測試學院');
      return;
    }
    const [, category, subcategory, lang, term] = addTermAttempt.match;
    const result = addExcludeTerm(category, subcategory, lang, term.trim());

    if (!result.success) {
      await msg.reply(`❌ ${result.error}`);
      return;
    }

    logger.activity('discordBot:excludeTermAdd', {
      userId: msg.author.id,
      category, subcategory, lang, term: term.trim(),
    });

    await msg.reply(`✅ 已新增「${term.trim()}」至 ${category}.${subcategory}.${lang}\n目前清單：${result.terms.join('、')}`);
    return;
  }

  const removeTermAttempt = matchOrUsageError('排除關鍵字移除')(content, /^\/排除關鍵字移除 (\S+) (\S+) (\S+) (.+)$/);
  if (removeTermAttempt.intended) {
    if (!removeTermAttempt.match) {
      await msg.reply('❌ 格式錯誤，正確用法：/排除關鍵字移除 分類 子分類 語言 詞彙，例如 /排除關鍵字移除 school_restricted name_suffix zh 測試學院');
      return;
    }
    const [, category, subcategory, lang, term] = removeTermAttempt.match;
    const result = removeExcludeTerm(category, subcategory, lang, term.trim());

    if (!result.success) {
      await msg.reply(`❌ ${result.error}`);
      return;
    }

    logger.activity('discordBot:excludeTermRemove', {
      userId: msg.author.id,
      category, subcategory, lang, term: term.trim(),
    });

    await msg.reply(`🗑️ 已從 ${category}.${subcategory}.${lang} 移除「${term.trim()}」\n目前清單：${result.terms.length > 0 ? result.terms.join('、') : '（空）'}`);
    return;
  }

  if (/^\/排除關鍵字清單$/.test(content)) {
    const dict = loadExcludeTerms();
    const lines = [];
    for (const [category, subcats] of Object.entries(dict)) {
      if (!subcats || typeof subcats !== 'object') continue;
      for (const [subcategory, langs] of Object.entries(subcats)) {
        if (!langs || typeof langs !== 'object') continue;
        for (const [lang, terms] of Object.entries(langs)) {
          if (!Array.isArray(terms) || terms.length === 0) continue;
          lines.push(`${category}.${subcategory}.${lang}：${terms.join('、')}`);
        }
      }
    }
    const body = lines.length > 0 ? lines.join('\n') : '（目前沒有任何排除詞彙）';
    for (const chunk of chunkLines([`📋 目前排除關鍵字清單（依 分類.子分類.語言 分組）：`, body])) {
      await msg.reply(chunk);
    }
    return;
  }

  // [WHY 本輪新增，簡化參數版] 使用者要求 /排除關鍵字新增 不需要每次都指定
  // 「分類 子分類 語言」三個參數，只需提供關鍵字本身。決策：採用簡化參數方案，
  // 而非「系統自動判斷關鍵字該歸屬學校/科系/身份別哪一類」的自動分類方案——
  // 後者屬於語意判斷任務，會逾越使用者既有「AI 僅限來源合法性判斷」的硬性限制
  // 範圍，不得開發。改為固定寫入 profile.exclude_keywords（系統最初設計的
  // 「第一層硬性排除」，純字串比對，涵蓋範圍最廣、風險最低，見 ruleEngine.js
  // filter() 的 excludeKeywords.some(...) 邏輯）；完整版 /排除關鍵字新增（4 參數，
  // 寫入 exclude_terms.json 的三層分類結構）保留給需要精確控制歸類時使用，兩者
  // 並存、互不取代。沿用 /update 既有的 readProfile()/writeProfileAtomic() 讀寫
  // 機制（不另開一套平行邏輯），以及跟其他寫入類指令一致的 rate limit/稽核 log。
  const quickAddAttempt = matchOrUsageError('排除關鍵字')(content, /^\/排除關鍵字\s+(.+)$/);
  if (quickAddAttempt.intended) {
    if (!quickAddAttempt.match) {
      await msg.reply('❌ 格式錯誤，正確用法：/排除關鍵字 詞彙，例如 /排除關鍵字 限清華大學');
      return;
    }
    const term = quickAddAttempt.match[1].trim();
    const profile = readProfile();
    const keywords = Array.isArray(profile.exclude_keywords) ? profile.exclude_keywords : [];

    if (keywords.includes(term)) {
      await msg.reply(`❌ 「${term}」已經存在於排除關鍵字清單，不需要重複新增`);
      return;
    }

    keywords.push(term);
    profile.exclude_keywords = keywords;
    writeProfileAtomic(profile);

    logger.activity('discordBot:excludeKeywordAdd', { userId: msg.author.id, term });

    await msg.reply(`✅ 已新增排除關鍵字「${term}」\n目前清單：${keywords.join('、')}`);
    return;
  }

  const quickRemoveAttempt = matchOrUsageError('移除排除關鍵字')(content, /^\/移除排除關鍵字\s+(.+)$/);
  if (quickRemoveAttempt.intended) {
    if (!quickRemoveAttempt.match) {
      await msg.reply('❌ 格式錯誤，正確用法：/移除排除關鍵字 詞彙，例如 /移除排除關鍵字 限清華大學');
      return;
    }
    const term = quickRemoveAttempt.match[1].trim();
    const profile = readProfile();
    const keywords = Array.isArray(profile.exclude_keywords) ? profile.exclude_keywords : [];

    if (!keywords.includes(term)) {
      await msg.reply(`❌ 「${term}」不存在於排除關鍵字清單，無法移除`);
      return;
    }

    profile.exclude_keywords = keywords.filter((k) => k !== term);
    writeProfileAtomic(profile);

    logger.activity('discordBot:excludeKeywordRemove', { userId: msg.author.id, term });

    await msg.reply(`🗑️ 已移除排除關鍵字「${term}」\n目前清單：${profile.exclude_keywords.length > 0 ? profile.exclude_keywords.join('、') : '（空）'}`);
    return;
  }

  // -------------------------
  // sources
  // -------------------------
  if (/^\/sources$/.test(content)) {
    const list = sourceManager.getSources();

    await msg.reply(
      "📡 Sources:\n" +
      list.map(s =>
        `• ${s.enabled ? "🟢" : "🔴"} ${s.name}\n${s.url}`
      ).join("\n\n")
    );
    return;
  }

  // [WHY 使用者導向的批次驗證，非系統自主探索] 使用者要求「爬蟲應可自動延伸擴充
  // 全世界各國公開獎學金網站來源」，但自動探索需要語意判斷「這是不是獎學金公告
  // 列表頁」，等同需要 AI，違反硬性限制。這裡做的是「使用者丟候選網址 → 系統跑
  // 批次驗證（連線/反爬蟲/SPA偵測/GenericCrawler 抓取診斷）→ 通過與否都回報具體
  // 理由」，全程由使用者主導：驗證通過後**不會自動加入**，需要使用者自己再執行
  // 既有的 /addsource 指令二次確認——沿用既有指令而非另外做一個「確認新增」指令，
  // 避免重複維護寫入邏輯，也讓「驗證」與「真的寫入設定檔」在權責上明確分開。
  const validateAttempt = matchOrUsageError('驗證來源')(content, /^\/驗證來源 (https?:\/\/\S+)$/);
  if (validateAttempt.intended) {
    if (!validateAttempt.match) {
      await msg.reply('❌ 格式錯誤，正確用法：/驗證來源 網址（須以 http:// 或 https:// 開頭），例如 /驗證來源 https://example.com/scholarships');
      return;
    }
    const url = validateAttempt.match[1];
    await msg.reply(`🔎 正在驗證 ${url}，請稍候...`);

    const result = await validateSource(url);

    if (!result.ok) {
      await msg.reply(`❌ 驗證未通過：${result.reason}`);
      return;
    }

    await msg.reply(
      `✅ 驗證通過\n抓到項目數：${result.itemCount}（掃描到的原始連結數：${result.rawLinkCount}，比例約 ${(result.itemCount / result.rawLinkCount * 100).toFixed(1)}% 通過 GenericCrawler 保底邏輯的雜訊過濾）\n範例標題：\n` +
      result.sampleTitles.map((t) => `• ${t}`).join('\n') +
      `\n\n⚠️ 這只是自動化基本檢查，GenericCrawler 是低信心的保底解析（沒有專屬 selector），實際精準度需要你自己判斷範例標題是否合理。若要正式加入，請執行：\n/addsource 名稱 ${url}`
    );
    return;
  }

  // [WHY 邊界界定，詳見 Meta_Dev_Knowledge.md] 使用者要求「爬蟲應可逐步按照引用
  // 來源自動延伸擴充全世界公開獎學金網站」。這裡做的是純 DOM/正則抽取既有來源
  // 頁面內出現的外部連結（排除內部連結/社群媒體/廣告追蹤網域），**不判斷**這些
  // 連結是不是獎學金網站——那需要語意判斷，等同 AI，違反硬性限制（Gemini 功能
  // 目前使用者已決定暫緩）。抽出的候選連結仍需使用者自己逐一 /驗證來源 + /addsource
  // 二次確認，系統不會自動收錄。
  // [WHY 用 .+ 而非 \S+] 實測發現 sourceDisplayName() 產生的類別名稱可能含空白
  // （例如「European Funding Guide（歐洲）」），且這個指令只有一個尾端參數、沒有
  // 後續參數的歧義問題，改用 .+ 才能正確比對含空白的類別名稱——這正是先前
  // /篩選獎學金 用 \S+ 導致同一個類別完全無回應的同根成因，這裡不重蹈覆轍
  // （/篩選獎學金 本身的修復不在本輪範圍內，記入 DEBT）。
  const linkExtractAttempt = matchOrUsageError('擷取引用連結')(content, /^\/擷取引用連結\s+(.+)$/);
  if (linkExtractAttempt.intended) {
    if (!linkExtractAttempt.match) {
      await msg.reply('❌ 格式錯誤，正確用法：/擷取引用連結 類別（類別需與 /獎學金類別 查到的其中一項一致），例如 /擷取引用連結 東海大學獎助學金');
      return;
    }
    const categoryOrName = stripDecoration(linkExtractAttempt.match[1]);
    const sources = sourceManager.getSources();
    const target = sources.find((s) => sourceDisplayName(s.url) === categoryOrName || s.name === categoryOrName);

    if (!target) {
      await msg.reply(`❌ 找不到來源「${categoryOrName}」，可用來源見 /獎學金類別 或 /sources`);
      return;
    }

    await msg.reply(`🔗 正在擷取「${target.name}」頁面內的引用連結，請稍候...`);

    try {
      const existingUrls = new Set(sources.map((s) => s.url));
      const candidates = await extractReferencedLinks(target.url, existingUrls);

      if (candidates.length === 0) {
        await msg.reply(`未找到任何新的候選連結（頁面內連結皆為內部連結/社群媒體/廣告追蹤網域，或已經在追蹤清單裡）`);
        return;
      }

      const lines = candidates.map((c, i) => `${i + 1}. ${c.text || '(無連結文字)'}\n   ${c.url}`);
      const allLines = [
        `🔗 從「${target.name}」頁面擷取到 ${candidates.length} 個候選連結（已排除內部連結/社群媒體/廣告追蹤網域/已追蹤網址）：`,
        ...lines,
        '⚠️ 這只是純 DOM/正則抽取，不代表這些連結真的是獎學金網站——請逐一用 /驗證來源 網址 驗證後再決定是否 /addsource',
      ];
      for (const chunk of chunkLines(allLines)) {
        await msg.reply(chunk);
      }
    } catch (err) {
      logger.error('discordBot:linkExtract', err, { source: target.url });
      await msg.reply(`❌ 擷取失敗：${err.message}`);
    }
    return;
  }

  const addsourceAttempt = matchOrUsageError('addsource')(content, /^\/addsource (\S+) (https?:\/\/\S+)$/);
  if (addsourceAttempt.intended) {
    if (!addsourceAttempt.match) {
      await msg.reply('❌ 格式錯誤，正確用法：/addsource 名稱 網址（網址須以 http:// 或 https:// 開頭），例如 /addsource Erasmus https://example.com/scholarships');
      return;
    }
    const [, name, url] = addsourceAttempt.match;
    const result = sourceManager.addSource(name, url);

    await msg.reply(result.success ? "✅ Added" : "⚠️ Exists");
    return;
  }

  const removesourceAttempt = matchOrUsageError('removesource')(content, /^\/removesource (https?:\/\/\S+)$/);
  if (removesourceAttempt.intended) {
    if (!removesourceAttempt.match) {
      await msg.reply('❌ 格式錯誤，正確用法：/removesource 網址（須為完整 http(s):// 網址，與 /sources 查到的一致），例如 /removesource https://example.com/scholarships');
      return;
    }
    const [, url] = removesourceAttempt.match;
    const result = sourceManager.removeSource(url);

    await msg.reply(result.success ? "🗑️ Removed" : "❌ Not found");
    return;
  }

  // -------------------------
  // 查詢/篩選/說明（本輪新增）
  // -------------------------
  // [WHY] 這個機器人從第一輪開始就是「私訊裡直接打純文字 /指令」的設計（見上面
  // /myprofile、/update、/rule 等既有指令），完全沒有用 discord.js 的
  // SlashCommandBuilder/interactionCreate 註冊過任何真正的 Discord 應用程式指令。
  // 為了跟既有指令風格一致（也避免同時維護「文字指令」跟「原生 slash 指令」兩套
  // 平行機制），這三個新指令沿用同一套 messageCreate + 正則比對設計，只是把
  // 指令名稱換成繁體中文。
  //
  // [WHY lastQueryResults 記憶體暫存] /獎學金說明 [編號] 的「編號」對應到上一次
  // /查詢獎學金 或 /篩選獎學金 顯示的清單順序，用記憶體陣列暫存（重啟後清空）。
  // 這是單機個人使用機器人，不需要跨重啟持久化查詢狀態，屬已知、可接受的限制。
  // [WHY 本輪修復] 使用者實測回報 /獎學金說明 [11]、11.、11, 皆完全沒有回應。
  // 根因：舊版 regex `/^\/獎學金說明 (\d+)$/` 只接受純數字，前面說明文字裡寫的
  // "/獎學金說明 [編號]" 用中括號代表「這裡填編號」的佔位符慣例，但使用者依字面
  // 照打導致夾帶了裝飾符號，無法比對，且沒有任何錯誤提示（無反應是最差的體驗）。
  // 修法：正則放寬到抓取「/獎學金說明」後面的整段文字，去除常見的裝飾符號
  // （中括號、句點、逗號、空白）後才解析數字；解析不出合法正整數時明確回覆錯誤
  // 訊息並附正確範例，而非靜默忽略。/查詢獎學金、/篩選獎學金 的參數也做同樣的
  // 中括號容錯（使用者可能對這兩個指令犯一樣的錯），行為改成「自動去除外層中
  // 括號再處理」而非要求逐字比對。
  function stripDecoration(raw) {
    return raw.trim().replace(/^\[|\]$/g, '').trim();
  }

  const queryAttempt = matchOrUsageError('查詢獎學金')(content, /^\/查詢獎學金\s+(\S+)$/);
  if (queryAttempt.intended) {
    if (!queryAttempt.match) {
      await msg.reply('❌ 格式錯誤，正確用法：/查詢獎學金 關鍵字（關鍵字不可為空、不可含空白），例如 /查詢獎學金 獎學金');
      return;
    }
    const keyword = stripDecoration(queryAttempt.match[1]).toLowerCase();
    const results = filterByDeadline(db.getAll().filter((r) => (r.title || '').toLowerCase().includes(keyword)));
    lastQueryResults = results;

    if (results.length === 0) {
      await msg.reply(`🔍 沒有找到包含「${keyword}」的獎學金（截止日不足 1 週的項目已排除，不確定要搜尋什麼關鍵字可以先用 /獎學金關鍵字 查看建議）`);
      return;
    }

    const lines = results.map((r, i) => `${i + 1}. [${sourceDisplayName(r.source)}] ${r.title}`);
    const allLines = [
      `🔍 找到 ${results.length} 筆（已排除截止日不足 1 週的項目）：`,
      ...lines,
      '輸入 /獎學金說明 加編號查看完整詳情，例如：/獎學金說明 1（純數字，不需要加中括號）',
    ];
    for (const chunk of chunkLines(allLines)) {
      await msg.reply(chunk);
    }
    return;
  }

  // [WHY 本輪修復] 實測發現 sourceDisplayName() 產生的類別名稱可能含空白
  // （例如「European Funding Guide（歐洲）」），原本用 \S+ 只接受不含空白的參數，
  // 導致這個類別完全無回應——跟 /擷取引用連結 先前踩過的同一個根因，這裡沿用
  // 已驗證過的修法（改用 .+，因為只有一個尾端參數、無後續參數歧義）。
  const filterAttempt = matchOrUsageError('篩選獎學金')(content, /^\/篩選獎學金\s+(.+)$/);
  if (filterAttempt.intended) {
    if (!filterAttempt.match) {
      await msg.reply('❌ 格式錯誤，正確用法：/篩選獎學金 類別（類別需與 /獎學金類別 查到的其中一項一致），例如 /篩選獎學金 東海大學獎助學金');
      return;
    }
    const category = stripDecoration(filterAttempt.match[1]);
    // [WHY] db.json 裡的項目都是通過 ruleEngine.filter()（含本輪新增的身心障礙/
    // 疾病類別排除規則）後才寫入的，已經是排除後的結果，這裡不需要也不應該
    // 重複套用一次排除邏輯——重複套用只會白白重算，結果不會改變。
    const results = filterByDeadline(db.getAll().filter((r) => sourceDisplayName(r.source) === category || r.source === category));
    lastQueryResults = results;

    if (results.length === 0) {
      await msg.reply(`📂 分類「${category}」沒有項目（截止日不足 1 週的項目已排除）。輸入 /獎學金類別 查看目前可用的類別清單`);
      return;
    }

    const lines = results.map((r, i) => `${i + 1}. ${r.title}`);
    const allLines = [
      `📂 ${category}：共 ${results.length} 筆（已排除截止日不足 1 週的項目）：`,
      ...lines,
      '輸入 /獎學金說明 加編號查看完整詳情，例如：/獎學金說明 1（純數字，不需要加中括號）',
    ];
    for (const chunk of chunkLines(allLines)) {
      await msg.reply(chunk);
    }
    return;
  }

  // -------------------------
  // 依地區查詢（本輪新增，EXEC C）
  // -------------------------
  // [WHY 跟 /篩選獎學金 的差異] /篩選獎學金 只能選單一來源（例如「東海大學獎助
  // 學金」），這裡的「地區」是更粗的分組——可以一次涵蓋多個來源（例如「台灣」
  // 同時包含教育部圓夢助學網跟東海大學獎助學金），提供不同的查詢粒度，不是重複
  // 功能。地區對應寫在 format.js 的 SOURCE_REGION_MAP，未來新增來源時需要人工
  // 決定它屬於哪個地區（這是地理事實判斷，不是「這是不是獎學金網站」的語意判斷，
  // 兩者性質不同，不違反 AI 使用限制）。
  const regionAttempt = matchOrUsageError('依地區查詢')(content, /^\/依地區查詢\s+(.+)$/);
  if (regionAttempt.intended) {
    if (!regionAttempt.match) {
      await msg.reply('❌ 格式錯誤，正確用法：/依地區查詢 地區（地區需與 /獎學金地區 查到的其中一項一致），例如 /依地區查詢 台灣');
      return;
    }
    const region = stripDecoration(regionAttempt.match[1]);
    const results = filterByDeadline(db.getAll().filter((r) => regionForSource(r.source) === region));
    lastQueryResults = results;

    if (results.length === 0) {
      await msg.reply(`🌍 地區「${region}」沒有項目（截止日不足 1 週的項目已排除）。輸入 /獎學金地區 查看目前可用的地區清單`);
      return;
    }

    const lines = results.map((r, i) => `${i + 1}. [${sourceDisplayName(r.source)}] ${r.title}`);
    const allLines = [
      `🌍 ${region}：共 ${results.length} 筆（已排除截止日不足 1 週的項目）：`,
      ...lines,
      '輸入 /獎學金說明 加編號查看完整詳情，例如：/獎學金說明 1（純數字，不需要加中括號）',
    ];
    for (const chunk of chunkLines(allLines)) {
      await msg.reply(chunk);
    }
    return;
  }

  if (/^\/獎學金地區$/.test(content)) {
    const regions = getAllRegions();
    await msg.reply(`🌍 可用地區（供 /依地區查詢 使用）：\n` + regions.map((r) => `• ${r}`).join('\n') + '\n\n例如：/依地區查詢 ' + regions[0]);
    return;
  }

  // [WHY 本輪補強] /獎學金說明 早就對「參數解析不出數字」（例如 abc）有明確錯誤
  // 訊息，但完全沒打參數（純打 "/獎學金說明" 沒有任何後綴）不會匹配 \s+(.+)，
  // 之前會落到這裡以外、變成靜默無回應——用 matchOrUsageError 補上這個邊界情況，
  // 跟其他指令的 catch-all 邏輯一致。
  const detailArgAttempt = matchOrUsageError('獎學金說明')(content, /^\/獎學金說明\s+(.+)$/);
  if (detailArgAttempt.intended) {
    if (!detailArgAttempt.match) {
      await msg.reply('❌ 格式錯誤，正確用法：/獎學金說明 編號（純數字，需先用 /查詢獎學金 或 /篩選獎學金 產生清單），例如 /獎學金說明 1');
      return;
    }
    const detailArgMatch = detailArgAttempt.match;
    const cleaned = stripDecoration(detailArgMatch[1]).replace(/[，,。.\s]/g, '');
    const idx = Number(cleaned);

    if (!cleaned || !Number.isInteger(idx) || idx < 1) {
      await msg.reply(`❌ 無法辨識編號「${detailArgMatch[1].trim()}」，請直接輸入純數字，例如：/獎學金說明 1（不需要加中括號、句點或逗號）`);
      return;
    }

    const row = lastQueryResults[idx - 1];
    if (!row) {
      await msg.reply(`❌ 編號 ${idx} 不存在，請先用 /查詢獎學金 或 /篩選獎學金 產生清單`);
      return;
    }

    await msg.reply(fmtItem(row, idx));
    return;
  }

  // -------------------------
  // 可用選項查詢（本輪新增，EXEC B）
  // -------------------------
  // [WHY 命名與設計] 使用者要求能查詢「有哪些關鍵字/類別可用」，避免盲猜。
  // 選擇拆成兩個指令（/獎學金類別、/獎學金關鍵字）而非整合進 /獎學金說明 的無參數
  // 版本，因為「類別」跟「關鍵字」是兩種不同性質的查詢，合併成一個指令要嘛得再
  // 加一個參數區分種類（等於還是兩個指令，只是換了個殼），要嘛混在一起顯示反而
  // 更難讀；獨立兩個指令更符合現有指令「一個指令做一件事」的風格（比照
  // /sources 跟 /rules 本來就是分開的）。
  // 「類別」直接複用 sourceManager.getSources()（/sources 指令背後的同一份資料），
  // 「關鍵字」直接複用 getRules().weights（/rules 指令背後的同一份資料）——
  // 兩者都不是另外寫死一份清單，資料來源跟 /sources、/rules 保證一致，不會脫節。
  if (/^\/獎學金類別$/.test(content)) {
    const list = sourceManager.getSources();
    const lines = list.map((s) => `• ${sourceDisplayName(s.url)}`);
    await msg.reply(`📂 可用類別（供 /篩選獎學金 使用）：\n` + lines.join('\n') + '\n\n例如：/篩選獎學金 ' + sourceDisplayName(list[0].url));
    return;
  }

  if (/^\/獎學金關鍵字$/.test(content)) {
    const rules = getRules();
    const keywords = Object.keys(rules.weights || {});
    await msg.reply(`🔑 可用關鍵字建議（供 /查詢獎學金 使用，來自目前的計分權重清單）：\n` + keywords.map((k) => `• ${k}`).join('\n') + '\n\n例如：/查詢獎學金 ' + keywords[0] + '\n（/查詢獎學金 不限於這份清單，任何標題裡出現的字都能搜尋，這只是常見關鍵字建議）');
    return;
  }

  // -------------------------
  // help
  // -------------------------
  if (/^\/help$/.test(content)) {
    await msg.reply(
      [
        "/myprofile",
        "/update key=value（例如 /update gpa=3.8）",
        "/rules",
        "/rule 關鍵字 分數（例如 /rule 獎學金 3，兩個參數用空格分隔）",
        "/排除關鍵字 詞彙（簡化版，例如 /排除關鍵字 限清華大學——不需指定分類，直接寫入 profile.exclude_keywords，適合快速排除、不需要精確歸類）",
        "/移除排除關鍵字 詞彙（簡化版的對應移除指令）",
        "/排除關鍵字新增 分類 子分類 語言 詞彙（完整版，例如 /排除關鍵字新增 school_restricted name_suffix zh 測試學院——需要精確控制歸類到 exclude_terms.json 的分類/子分類/語言結構時使用）",
        "/排除關鍵字移除 分類 子分類 語言 詞彙（完整版的對應移除指令）",
        "/排除關鍵字清單（查看目前所有排除詞彙，依分類分組）",
        "/sources",
        "/擷取引用連結 類別（從既有來源頁面抽取候選外部連結，例如 /擷取引用連結 東海大學獎助學金，抽出後仍需用 /驗證來源 逐一驗證）",
        "/驗證來源 網址（批次驗證候選來源，通過後需自行執行 /addsource 才會真的加入）",
        "/addsource 名稱 網址",
        "/removesource 網址",
        "/查詢獎學金 關鍵字（純文字，不需要加中括號，例如 /查詢獎學金 獎學金）",
        "/篩選獎學金 類別（例如 /篩選獎學金 東海大學獎助學金，可用類別見 /獎學金類別）",
        "/依地區查詢 地區（例如 /依地區查詢 台灣，可一次涵蓋多個來源，可用地區見 /獎學金地區）",
        "/獎學金地區（查詢 /依地區查詢 可用的地區清單）",
        "/獎學金說明 編號（純數字，不需要加中括號，例如 /獎學金說明 1）",
        "/獎學金類別（查詢 /篩選獎學金 可用的類別清單）",
        "/獎學金關鍵字（查詢 /查詢獎學金 的常見關鍵字建議）",
      ].join('\n')
    );
  }
}

// =========================
// start (singleton safe)
// =========================
function start() {
  if (_started) {
    console.log("⚠️ Discord bot already started");
    return _clientInstance;
  }
  _started = true;

  if (!process.env.DISCORD_BOT_TOKEN) {
    console.error("❌ DISCORD_BOT_TOKEN missing");
    return;
  }

  if (!AUTHORIZED_USER_ID) {
    console.error("❌ DISCORD_USER_ID missing");
  }

  // =========================
  // ready
  // [WHY] discord.js v14.26 起 'ready' 已改名為 'clientReady'（'ready' 仍會觸發但
  // 會印出 deprecation warning，v15 起將完全移除），改用新事件名避免噪音與未來斷線。
  // =========================
  client.once('clientReady', () => {
    console.log("🟢 BOT READY =", client.user.tag);
    resolveReady();
  });

  // =========================
  // messageCreate (bind once)
  // =========================
  if (!_listenerBound) {
    _listenerBound = true;
    client.on('messageCreate', handleMessage);
  }

  client.login(process.env.DISCORD_BOT_TOKEN);

  _clientInstance = client;
  return client;
}

module.exports = {
  start,
  send,
  whenReady,
  sendHealthAlert,
  sendRedesignAlert,
  // 供測試直接驅動，不需要真的連 Discord 或模擬完整的 Discord 訊息物件
  checkRateLimit,
  handleMessage,
  WRITE_COMMAND_PATTERNS,
  RATE_LIMIT_WINDOW_MS,
  RATE_LIMIT_MAX,
};
