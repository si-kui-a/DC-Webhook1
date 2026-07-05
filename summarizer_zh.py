"""
summarizer_zh.py — cbc（中文）來源的抽取式摘要。純統計演算法（TextRank），
不是生成式 LLM，不呼叫任何雲端 API，符合「最低算力、本地端無 AI 協助」原則。

已驗證（見 Meta_Dev_Knowledge.md PAT-06）：
- jieba.analyse.textrank() 只做「關鍵字擷取」，回傳的是單詞列表，不是句子，
  沒辦法直接拿來「取前2-3句」。改用 textrank4zh，這是專門做中文句子級
  TextRank 摘要的套件。
- textrank4zh 0.3（2019年最後一版）呼叫的是舊版 networkx API
  nx.from_numpy_matrix，新版 networkx（>=3.0）已經把它改名成
  nx.from_numpy_array，簽章相容，用別名補上即可，不需要固定 networkx 版本。
- textrank4zh 回傳的句子是照「重要性權重」排序，不是照原文順序——直接
  照回傳順序拼接會讀起來語序錯亂，所以這裡會先照 item.index（原文位置）
  重新排序再拼接。

樣板清理原則（PAT-06）：清洗階段用 regex 排除已知樣板，不修改
textrank4zh/networkx 等第三方套件的內部行為。cbc RSS description 清成
純文字後，開頭常是「機構名稱 + 發布日期 + <網址...> + 選填的公告編號」
黏在一起、中間沒有標點的一段文字，加上緊接著的標題重複——這段文字沒有
句子邊界，會被 TextRank 的分句器當成單一「句子」，而且常因為關鍵字密度
高（日期數字、標題詞彙）被排到摘要最前面，擠掉真正的內文。處理方式分兩層：
1. 已確認的固定格式（機構名稱/日期/網址/編號）用 regex 直接砍掉。
2. title 若在殘餘文字開頭重複出現，也砍掉（用呼叫端傳進來的 RSS 標題
   做精確比對，不是用猜的）。
3. 上面兩層没顾到的情況，用更通用、不用窮舉樣板文字的規則兜底：
   TextRank 選出的候選句子裡，凡是完全不含「。！？」的，視為疑似樣板
   黏合片段，優先跳過。
"""
import re

from bs4 import BeautifulSoup
import networkx as nx

if not hasattr(nx, "from_numpy_matrix"):
    nx.from_numpy_matrix = nx.from_numpy_array

from textrank4zh import TextRank4Sentence

MAX_LENGTH = 250
NUM_SENTENCES = 3

# 已確認樣板（見本檔案 docstring）：機構名稱 + 發布日期 + <網址...> +
# 選填的公告編號。已觀察到的變化：機構名稱有「中央銀行新聞稿」和
# 「中央銀行新聞參考資料」兩種；「網址」後面的冒號有半形、全形、或完全
# 沒有冒號三種都出現過（實測樣本 5 個裡就有一個是 <網址https://...>
# 完全沒冒號）；公告編號不是每篇都有。
BOILERPLATE_HEADER_RE = re.compile(
    r"^(?:中央銀行新聞稿|中央銀行新聞參考資料)\s*"
    r"\d+年\d+月\d+日發布\s*"
    r"<網址[:：]?https?://www\.cbc\.gov\.tw>\s*"
    r"(?:[（(]\d+[）)]新聞發布第\d+號)?\s*"
)

SENTENCE_END_CHARS = "。！？"


def clean_html(html: str) -> str:
    """去除 HTML 標籤，只留純文字。"""
    if not html:
        return ""
    return BeautifulSoup(html, "html.parser").get_text(separator=" ", strip=True)


def _strip_boilerplate(text: str, title: str | None) -> str:
    text = BOILERPLATE_HEADER_RE.sub("", text, count=1)
    if title:
        title = title.strip()
        if title and text.startswith(title):
            text = text[len(title):].lstrip()
    return text


def summarize(html: str, title: str | None = None) -> str | None:
    """輸入 RSS description 的原始 HTML 字串，回傳純文字摘要（2-3句、上限
    250字元，超過截斷加「...」）。title 是同一則新聞的 RSS 標題，用來
    把「內文開頭重複一次標題」這段去掉；不傳的話只做樣板 regex 清理。
    清洗/分析失敗或抓不到句子時回傳 None，交由呼叫端（main.py）沿用
    「無摘要」的既有邏輯，不中斷整體推播流程。"""
    text = clean_html(html)
    if not text:
        return None

    text = _strip_boilerplate(text, title)
    if not text:
        return None

    try:
        tr4s = TextRank4Sentence()
        tr4s.analyze(text=text, lower=True, source="all_filters")
        # 多要幾句候選，因為接下來會過濾掉疑似樣板的無標點片段，
        # 篩掉之後才有足夠的真句子可選。
        candidates = tr4s.get_key_sentences(num=NUM_SENTENCES + 2)
    except Exception:
        return None

    if not candidates:
        return None

    # 通用兜底規則：候選句子完全不含「。！？」的，視為疑似樣板黏合片段，
    # 優先排除（見本檔案 docstring 第3層）。如果全部候選都被排除
    # （理論上不太可能，但避免回傳空摘要），退回原始候選清單。
    filtered = [c for c in candidates if any(ch in c.sentence for ch in SENTENCE_END_CHARS)]
    items = (filtered or candidates)[:NUM_SENTENCES]

    items_in_order = sorted(items, key=lambda item: item.index)

    # 逐句累加，一旦加下一句會超過 MAX_LENGTH 就停在句子邊界，不要把已經
    # 拼好的字串整段硬切——硬切會切到句子中間，出現「...capit...」這種
    # 斷詞斷字的結果，讀起來語意不通。
    parts = []
    length = 0
    for item in items_in_order:
        sentence = item.sentence
        added_length = len(sentence) + (1 if parts else 0)  # 1 for "。" separator
        # 這裡故意不寫成「if parts and ...」——那樣會讓第一句永遠跳過長度
        # 檢查、無條件加進去，若第一句自己就超過 MAX_LENGTH，結果會整段
        # 超標。第一句也要檢查。
        if length + added_length > MAX_LENGTH:
            break
        parts.append(sentence)
        length += added_length

    if not parts:
        first = items_in_order[0].sentence
        return first[:MAX_LENGTH] + "..." if len(first) > MAX_LENGTH else first

    summary = "。".join(parts)
    if len(items_in_order) > len(parts):
        summary += "..."
    return summary
