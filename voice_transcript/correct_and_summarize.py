"""
correct_and_summarize.py — 純規則式(非AI)口語清理 + 摘要。

**2026-07-31設計變更記錄**：最初版本嘗試用jieba靜態詞典的單字頻率+pypinyin
同音字分組做「同音字替換」修正錯字，實測發現這個做法不可靠——jieba預設詞典
是偏簡體語料校準的詞頻表，拿來衡量繁體字在某個位置合不合理會系統性判斷
錯誤，實測把「在公司」錯改成「灾公司」、「整體」錯改成「政体」，等於讓
文字比原本更差，不是「準確度有上限」而是淨負值，因此整個同音字替換法
已經拿掉，不要重新加回來(除非先解決詞頻表偏誤的根本問題)。

改用風險低很多的口語清理：只處理語音轉錄真正常見的問題——即時口語的
重複詞(stutter，如「我我我要」)、贅字(呃/嗯/那個那個)、標點正規化。這些
操作的共同點是「只會讓文字變乾淨，不會憑空把對的字改錯」，跟同音字替換
的風險等級完全不同。

摘要：TextRank抽取式句子摘要，跟ip根目錄summarizer_zh.py同一套演算法
(textrank4zh)，這裡不重用summarizer_zh.summarize()本體，因為那支函式是
為RSS description(HTML、需要標題去重、機構樣板清理)設計的，即時語音逐字稿
是純文字、沒有這些前處理需求，直接呼叫TextRank4Sentence比較乾淨。
"""
import re

import networkx as nx

if not hasattr(nx, "from_numpy_matrix"):
    nx.from_numpy_matrix = nx.from_numpy_array

from textrank4zh import TextRank4Sentence

# 純語氣贅字(本身無語意內容，可安全整段移除)。刻意不含「那個」——「那個」
# 常是有意義的指示詞(「那個報告」)，只有連續重複(「那個那個」)才視為口吃。
#
# 2026-09-11發現的已知限制(未修改，需要真實逐字稿樣本才能安全調整，
# 不是regex能單方面解的問題)：這個lookahead要求贅字後面緊接標點/
# 空白/結尾才會被移除，效果實測起來可能偏保守/方向可能不理想——
# 口語裡贅字更常見的位置反而是句中緊接下一個字(如「呃這個東西」，
# 呃後面直接接字沒有標點)，這種情況現在的regex完全不會處理；而句尾
# 語助詞的「啊/呃」有時反而帶語氣(「知道啊」)，句尾恰好常常緊跟標點，
# 現在的條件反而容易命中這種有語意的用法。到底哪個方向的取捨對，
# 需要真實faster-whisper逐字稿樣本才能判斷標點插入的實際習慣，不是
# 憑空猜規則能決定的——先記錄不動，累積真實樣本後再校準。
FILLER_WORDS_RE = re.compile(r"(?:呃|嗯|啊|欸)+(?=[，。！？\s]|$)")

# 連續重複3次以上的單一漢字(口吃)，例如「我我我要」收斂成「我要」。刻意
# 只吃3次以上、且限單字——中文有大量2次重複的合法構詞(謝謝/看看/慢慢/
# 天天)，門檻設2次會把這些正常詞誤砍成一個字，3次以上才是明顯的口吃訊號。
STUTTER_RE = re.compile(r"([一-鿿])\1{2,}")

# 標點正規化：連續相同標點只留一個(語音辨識偶爾會重複標點)。
DUP_PUNCT_RE = re.compile(r"([，。！？、])\1+")


LEADING_PUNCT_RE = re.compile(r"^[，、\s]+")


def correct_typos(text: str) -> str:
    text = FILLER_WORDS_RE.sub("", text)
    text = STUTTER_RE.sub(r"\1", text)
    text = DUP_PUNCT_RE.sub(r"\1", text)
    text = LEADING_PUNCT_RE.sub("", text)
    return text.strip()


def summarize(text: str, num_sentences: int = 5) -> str | None:
    if not text.strip():
        return None
    try:
        tr4s = TextRank4Sentence()
        tr4s.analyze(text=text, lower=True, source="all_filters")
        candidates = tr4s.get_key_sentences(num=num_sentences)
    except Exception:
        return None
    if not candidates:
        return None
    items_in_order = sorted(candidates, key=lambda item: item.index)
    return "。".join(item.sentence for item in items_in_order)


def correct_and_summarize(raw_text: str) -> tuple[str, str | None]:
    """回傳 (修正後全文, 摘要或None)。"""
    corrected = correct_typos(raw_text)
    summary = summarize(corrected)
    return corrected, summary
