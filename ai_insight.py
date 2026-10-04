"""
ai_insight.py — fed/tsmc（英文機構新聞稿）專用的Gemini免費層輔助：中文翻譯+
利多利空判斷。純requests呼叫,不新增任何套件依賴。

設計原則(呼應summarizer_en.py/summarizer_zh.py既有的「單筆失敗不中斷整體
推播」原則):
- 任何一步失敗(額度用盡/網路錯誤/回應格式不對)一律回傳None,呼叫端
  (main.py)沿用「無AI內容,退回既有抽取式摘要」的邏輯。
- 只在main.py確定「這筆真的會被推播」之後才呼叫,不對首次執行安全閘門
  擋下的歷史項目呼叫,避免浪費免費額度。
- cbc本來就是中文來源,不適用此模組(呼叫端只在key in ('fed','tsmc')時
  才呼叫)。
"""
import json
import logging
import os
import urllib.error
import urllib.request

import gemini_client

logger = logging.getLogger("ai_insight")

ALLOWED_SENTIMENTS = ("利多", "利空", "中性")
TIMEOUT_SECONDS = 30

# Retry rules live in gemini_client.py (shared with resume_matcher.py).
_with_retry = gemini_client.with_retry


# ── GPT備援(僅供非交易用途:翻譯/情緒判斷、頻道彙整、大總結) ──────────────
# build_trade_decision()刻意不套用這一段,交易判斷AI失敗時維持既有「這次
# 先不動作」邏輯,不接GPT備援(2026-08-29交接指令的安全邊界)。
def _call_gemini_json_text(prompt: str, temperature: float, timeout: int) -> str:
    """呼叫Gemini generateContent,回傳AI原始回應文字(未parse JSON)。
    失敗時原樣往外拋(HTTPError/URLError等),不吞例外,由_call_ai_json_text
    決定要不要切GPT。"""
    return gemini_client.call_json_text(prompt, temperature=temperature, timeout=timeout)


def _call_openai_json_text(prompt: str, temperature: float, timeout: int) -> str:
    """OPENAI_API_KEY為選填備援金鑰,只在Gemini失敗且此鍵有設定時才會被呼叫
    (見_call_ai_json_text)。未設定時由呼叫端判斷略過,不強制使用者設定。"""
    api_key = os.getenv("OPENAI_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY 未設定")
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    url = "https://api.openai.com/v1/chat/completions"
    body = json.dumps({
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": "application/json", "Authorization": "Bearer %s" % api_key},
        method="POST",
    )

    def _do():
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    data = _with_retry(_do)
    return data["choices"][0]["message"]["content"]


def _is_failover_eligible(exc: Exception) -> bool:
    """只在429/5xx/網路錯誤/金鑰被拒(401/403)時才允許切GPT,4xx其他情況
    (如400請求格式問題)、JSON解析失敗等內容面問題不切——重試/切供應商
    都不會讓這類問題變好,不因單純品質疑慮就無限制切換供應商。"""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in (401, 403, 429, 500, 502, 503, 504)
    if isinstance(exc, urllib.error.URLError):
        return True
    return False


def _call_ai_json_text(prompt: str, temperature: float, timeout: int) -> str:
    """Gemini為主要供應商,失敗且符合_is_failover_eligible、且設有
    OPENAI_API_KEY時才改呼叫GPT當第二順位。GPT也失敗時,往外拋Gemini的
    原始例外,呼叫端既有的except Exception記錄邏輯不用改。"""
    try:
        return _call_gemini_json_text(prompt, temperature, timeout)
    except Exception as primary_exc:
        if not _is_failover_eligible(primary_exc) or not os.getenv("OPENAI_API_KEY"):
            raise
        try:
            return _call_openai_json_text(prompt, temperature, timeout)
        except Exception:
            raise primary_exc


def get_translation_and_sentiment(english_summary: str) -> dict | None:
    """
    輸入已由summarizer_en.py抽取式摘要出的英文重點句(LexRank已挑出3句內、
    250字內的關鍵句子,不是完整正文——避免為了餵給Gemini而重新對detail頁
    多發一次HTTP請求),回傳
    {"zh_summary": str, "sentiment": "利多"|"利空"|"中性", "sentiment_reason": str}
    或None(任何失敗情況)。
    """
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key or not english_summary or not english_summary.strip():
        return None

    prompt = (
        "以下是一則機構(聯準會或台積電)英文新聞稿裡,已用抽取式演算法挑出的"
        "重點句(不是完整正文,只有最關鍵的幾句),供台灣投資人參考。\n"
        "請完成兩件事:\n"
        "1. 用繁體中文(台灣用語)翻譯並精簡整理成一段摘要,150字以內。\n"
        "2. 依這些重點句判斷這則新聞對股市/總經情勢屬於「利多」「利空」還是"
        "「中性」,只能三選一,並給一句話(30字以內)的判斷理由。\n"
        "只能根據提供的內容判斷,不可揣測或補充未載明的資訊。\n"
        '只回傳JSON,格式:{"zh_summary": "...", "sentiment": "...", "sentiment_reason": "..."}\n\n'
        "重點句:\n" + english_summary
    )
    try:
        text = _call_ai_json_text(prompt, temperature=0.2, timeout=TIMEOUT_SECONDS)
        parsed = json.loads(text)
        if parsed.get("sentiment") not in ALLOWED_SENTIMENTS:
            logger.warning(f"gemini回傳不合法的sentiment: {parsed.get('sentiment')!r}")
            return None
        zh_summary = str(parsed.get("zh_summary", "")).strip()
        if not zh_summary:
            return None
        return {
            "zh_summary": zh_summary[:250],
            "sentiment": parsed["sentiment"],
            "sentiment_reason": str(parsed.get("sentiment_reason", "")).strip()[:60],
        }
    except Exception as e:
        logger.warning(f"gemini翻譯/情緒判斷失敗: {e}")
        return None


# ── 晚間彙整頻道用:AI敘事交叉比對(甲類頻道,見設計討論) ──────────────

QUOTE_MAX_CHARS = 60  # 引用片段硬性上限(不管prompt裡怎麼要求AI自律,程式碼再截斷一次做保險)
DIGEST_TIMEOUT_SECONDS = 90  # 多篇文章合併彙整,prompt比單筆翻譯大很多,沿用30秒容易逾時(實測踩過)


def build_channel_digest(channel_angle: str, items: list[dict]) -> dict | None:
    """
    輸入當天某頻道所有新文章(items需含title/summary/url/source_name),依
    channel_angle指定的切入角度呼叫Gemini做交叉比對彙整。

    回傳 {"overview": str, "points": [{"category","point","quote",
    "source_title","source_name","source_url"}, ...]} 或 None(任何失敗)。
    points已依AI判斷的重要性由高到低排序,呼叫端(main.py)依字數預算
    貪婪篩選要不要收錄,不是這裡決定。quote欄位已在此函式內強制截斷,
    不管AI有沒有乖乖遵守prompt裡的引用長度限制。
    """
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key or not items:
        return None

    articles_text = "\n\n".join(
        f"[來源{i + 1}] {item.get('source_name', '')}\n"
        f"標題:{item['title']}\n"
        f"內容:{item.get('summary') or '(無預覽)'}"
        for i, item in enumerate(items)
    )

    prompt = (
        "你是財經內容編輯,要把當天多篇文章整理成一份給讀者的彙整報告。\n"
        f"這個頻道的切入角度:{channel_angle}\n\n"
        "請完成:\n"
        "1. overview:總覽,總結當天這些文章之間的共同觀點、分歧、或互補之處"
        "——這是交叉比對後的結論,不是逐篇摘要堆疊。以易讀為原則,適時搭配"
        "條列式列點(例如逐一列出幾個不同來源的立場對比、或幾個並列的重點)"
        ",不要每次都硬寫成一整段連續散文,條列跟段落可以視內容混合使用。\n"
        "2. points:依內容動態分類(不要套用固定分類清單,依當天實際內容決定"
        "類別名稱),每個重點包含:\n"
        "   - category:這個重點屬於的分類名稱\n"
        "   - point:用你自己的話重新表達的論述,可以是完整段落——這必須是你"
        "消化後的分析整理,不可逐句翻譯原文或只是換句話說後拉長,不可貼近"
        "原文的敘述順序或比例改寫\n"
        "   - quote:如果有需要佐證的原文片段,只能是一句話以內的簡短引用,"
        "沒有適合的引用就留空字串\"\",不可放整段原文\n"
        "   - source_title/source_name/source_url:對應的原文出處\n"
        "3. 所有points依重要性(投資影響程度／是否多來源談同一件事／跟頻道"
        "切入角度的相關度)由高到低排序,最重要的排最前面。\n\n"
        f"當天文章:\n{articles_text}\n\n"
        '只回傳JSON,格式:{"overview": "...", "points": [{"category":"...",'
        '"point":"...","quote":"...","source_title":"...","source_name":"...",'
        '"source_url":"..."}]}'
    )

    try:
        text = _call_ai_json_text(prompt, temperature=0.3, timeout=DIGEST_TIMEOUT_SECONDS)
        parsed = json.loads(text)
        overview = str(parsed.get("overview", "")).strip()
        raw_points = parsed.get("points", [])
        if not overview or not isinstance(raw_points, list):
            return None

        points = []
        for p in raw_points:
            quote = str(p.get("quote", "")).strip()
            if len(quote) > QUOTE_MAX_CHARS:
                quote = quote[:QUOTE_MAX_CHARS].rstrip() + "…"
            points.append({
                "category": str(p.get("category", "")).strip() or "其他",
                "point": str(p.get("point", "")).strip(),
                "quote": quote,
                "source_title": str(p.get("source_title", "")).strip(),
                "source_name": str(p.get("source_name", "")).strip(),
                "source_url": str(p.get("source_url", "")).strip(),
            })
        points = [p for p in points if p["point"]]
        if not points:
            return None

        return {"overview": overview, "points": points}
    except Exception as e:
        logger.warning(f"gemini頻道彙整失敗: {e}")
        return None


# ── 大總結頻道用:彙整多個頻道當天已產出的報告,做進出場/情緒綜合研判 ──

def build_meta_summary(angle: str, channel_reports: list[dict]) -> dict | None:
    """
    輸入當天多個頻道各自已經產出的完整報告(channel_reports每筆為
    {"channel_name": str, "report_text": str}，來自main.py的
    db.get_summary_for_date()查詢，不是原始新聞)，綜合研判進出場/市場
    情緒，回傳跟build_channel_digest()相同的{"overview","points"}結構
    (共用digest_format.py的組裝邏輯)。

    這裡輸入的report_text本身已經是各頻道AI敘事彙整(已有引用長度硬性
    截斷)或純事實報告，不是原始付費文章全文，故不需要重複套用quote長度
    限制的「防止逐段複寫原文」考量——但仍統一用QUOTE_MAX_CHARS截斷quote
    欄位，維持格式一致性。
    """
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key or not channel_reports:
        return None

    reports_text = "\n\n".join(
        f"[來源頻道:{r['channel_name']}]\n{r['report_text']}"
        for r in channel_reports
    )

    prompt = (
        "你是財經內容編輯,要把當天多個頻道已經產出的報告，綜合研判成一份"
        "「大總結」，回答讀者最關心的問題:現在該進場、觀望、還是出場？\n"
        f"研判範圍:{angle}\n\n"
        "請完成:\n"
        "1. overview:2-3段總覽，直接給出進出場方向的研判結論、市場情緒"
        "(樂觀/悲觀/中性)、理性面(數據支持什麼)與實際面(市場實際反應是"
        "什麼，兩者是否一致)——這是跨頻道交叉比對後的綜合判斷，不是逐"
        "頻道摘要堆疊。\n"
        "2. points:依內容動態分類(例如「進出場訊號」「產業/法人動向」"
        "「總經背景」等，依當天實際內容決定類別名稱)，每個重點包含:\n"
        "   - category:這個重點屬於的分類名稱\n"
        "   - point:用你自己的話重新表達的綜合研判，可以是完整段落\n"
        "   - quote:如果有需要引用某頻道報告裡的關鍵數字或結論，簡短"
        "一句話即可，沒有適合的就留空字串\"\"\n"
        "   - source_title:留空字串\"\"\n"
        "   - source_name:對應的來源頻道名稱\n"
        "   - source_url:留空字串\"\"\n"
        "3. 所有points依重要性排序，最重要的排最前面。\n\n"
        f"當天各頻道報告:\n{reports_text}\n\n"
        '只回傳JSON,格式:{"overview": "...", "points": [{"category":"...",'
        '"point":"...","quote":"...","source_title":"","source_name":"...",'
        '"source_url":""}]}'
    )

    try:
        text = _call_ai_json_text(prompt, temperature=0.3, timeout=DIGEST_TIMEOUT_SECONDS)
        parsed = json.loads(text)
        overview = str(parsed.get("overview", "")).strip()
        raw_points = parsed.get("points", [])
        if not overview or not isinstance(raw_points, list):
            return None

        points = []
        for p in raw_points:
            quote = str(p.get("quote", "")).strip()
            if len(quote) > QUOTE_MAX_CHARS:
                quote = quote[:QUOTE_MAX_CHARS].rstrip() + "…"
            points.append({
                "category": str(p.get("category", "")).strip() or "其他",
                "point": str(p.get("point", "")).strip(),
                "quote": quote,
                "source_title": str(p.get("source_title", "")).strip(),
                "source_name": str(p.get("source_name", "")).strip(),
                "source_url": str(p.get("source_url", "")).strip(),
            })
        points = [p for p in points if p["point"]]
        if not points:
            return None

        return {"overview": overview, "points": points}
    except Exception as e:
        logger.warning(f"gemini大總結彙整失敗: {e}")
        return None
