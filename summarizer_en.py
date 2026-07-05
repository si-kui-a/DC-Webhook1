"""
summarizer_en.py — fed/tsmc（英文）來源的抽取式摘要。純統計演算法（LexRank），
不是生成式 LLM，不呼叫任何雲端 API。

已驗證：sumy 的 LexRankSummarizer 回傳的句子本身就是照原文順序排列
（跟 textrank4zh 不同，那個是照重要性權重排序），這裡不需要額外重新排序。

nltk 的 punkt/punkt_tab 分詞資料需要先跑過一次
`python -c "import nltk; nltk.download('punkt'); nltk.download('punkt_tab')"`，
下載後永久快取在 ~/AppData/Roaming/nltk_data，之後執行不需要再連網
（已用中斷網路的方式實測驗證過）。
"""
from sumy.parsers.plaintext import PlaintextParser
from sumy.nlp.tokenizers import Tokenizer
from sumy.summarizers.lex_rank import LexRankSummarizer

MAX_LENGTH = 250
NUM_SENTENCES = 3


def summarize(text: str) -> str | None:
    """輸入已清過 nav/footer 雜訊的英文正文，回傳純文字摘要（2-3句、上限
    250字元，超過截斷加「...」）。文字太短、解析失敗或抓不到句子時回傳
    None，交由呼叫端（main.py）沿用「無摘要」的既有邏輯，不中斷整體推播
    流程。"""
    if not text or not text.strip():
        return None

    try:
        parser = PlaintextParser.from_string(text, Tokenizer("english"))
        summarizer = LexRankSummarizer()
        sentences = summarizer(parser.document, NUM_SENTENCES)
    except Exception:
        return None

    if not sentences:
        return None

    # 逐句累加、停在句子邊界，不對拼好的字串整段硬切——硬切會切到單字
    # 中間（例如 "...Productivity growth and capit..."），讀起來語意不通。
    # 只有第一句自己就超過上限這種極端情況才對單一句子做硬切。
    sentence_strs = [str(s) for s in sentences]
    parts = []
    length = 0
    for sentence in sentence_strs:
        added_length = len(sentence) + (1 if parts else 0)  # 1 for " " separator
        # 第一句也要檢查長度，不能無條件加入——見 summarizer_zh.py 同樣的修正。
        if length + added_length > MAX_LENGTH:
            break
        parts.append(sentence)
        length += added_length

    if not parts:
        first = sentence_strs[0]
        if len(first) <= MAX_LENGTH:
            return first
        # 單一句子自己就超過上限的極端情況：英文用字元硬切會切到單字
        # 中間（"paid-in-capit..."），退而求其次切在最後一個空白處，
        # 至少不會切斷單字。
        truncated = first[:MAX_LENGTH]
        last_space = truncated.rfind(" ")
        if last_space > 0:
            truncated = truncated[:last_space]
        return truncated + "..."

    summary = " ".join(parts)
    if len(sentence_strs) > len(parts):
        summary += "..."
    return summary
