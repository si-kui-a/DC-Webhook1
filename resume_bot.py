"""
resume_bot.py — 履歷配對/AI修改建議的Discord常駐互動入口(DC+CLI並行，
CLI版見resume_matcher.py的__main__)。

使用者確認2026-07-31：需要一個能接收訊息/附件的bot，跟本專案其餘功能
「排程批次+webhook單向推送」的架構不同，這支是唯一需要WebSocket常駐
連線的程式，必須手動啟動或設定開機自動啟動的排程(不是main.py --source X
那種一次性批次執行)。

前置需求：
1. 沿用DISCORD_BOT_TOKEN(跟scripts/discord_admin.py同一個bot)。
2. 這個bot必須在Discord Developer Portal啟用「Message Content Intent」
   (privileged intent，API無法自動開，只能人工去portal勾選)，否則
   on_message收不到訊息內容/附件。
3. RESUME_BOT_CHANNEL_ID(獨立頻道"履歷配對顧問")。

使用者2026-07-31確認：走專屬頻道而非DM(原設計是DM，使用者改要頻道互動)。
履歷內容屬PII(比照專案既有my_resume.json/academic_progress.md的PII
處理原則)，只在RESUME_BOT_CHANNEL_ID這個頻道回應，忽略其餘頻道與DM的
訊息，該頻道的可見範圍(只限本人/信任對象)由使用者自行在Discord伺服器
權限設定管控，不是這支程式的責任範圍。

使用方式(兩種模式，使用者2026-07-31確認新增模式2)：
1. DB比對模式：上傳履歷檔案(pdf/docx/txt/md)，或直接貼履歷內容文字
   (超過100字才視為履歷內容，避免把閒聊誤判)。bot會回覆最適合的實習
   職缺推薦+履歷修改建議。
2. 單一職缺評估模式：貼兩個網址，一行標「履歷：」一行標「職缺：」
   (順序不拘，看標籤不看順序)，例如：
   履歷：https://...
   職缺：https://...
   bot會抓兩個網址內容，評估這份履歷跟這個職缺搭不搭+給針對性建議。
   若沒標籤但訊息裡剛好有2個網址，退回「第一個是履歷、第二個是職缺」
   的假設順序，並在回覆裡明講這個假設，讓使用者能發現順序猜錯。

低成本設計：呼叫Gemini只在使用者主動私訊時才觸發，不是背景輪詢，跟
resume_matcher.py核心邏輯的低成本原則一致；resume_matcher的AI呼叫本身
是同步阻塞的(urllib)，這裡用run_in_executor丟到thread pool執行，避免
擋住discord.py的事件迴圈(擋住會導致心跳逾時、bot被Discord判定斷線)。
"""
import asyncio
import logging
import os
import re
import tempfile
from pathlib import Path

import discord
from dotenv import load_dotenv

load_dotenv()

import career_alignment
import db
import resume_matcher
from scrapers import internship_util

logger = logging.getLogger("resume_bot")

BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
RESUME_BOT_CHANNEL_ID = os.getenv("RESUME_BOT_CHANNEL_ID", "")
SUPPORTED_SUFFIXES = (".pdf", ".docx", ".txt", ".md")
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10MB，履歷不可能超過這個大小，防止異常大檔拖垮process
MIN_TEXT_RESUME_CHARS = 100  # 純文字訊息要超過這個長度才當履歷處理，避免把閒聊訊息誤判
DISCORD_MSG_LIMIT = 1900  # Discord單則訊息2000字上限，留一點餘裕

intents = discord.Intents.default()
intents.message_content = True  # 需要Developer Portal手動啟用的privileged intent

client = discord.Client(intents=intents)

_URL_RE = re.compile(r"https?://\S+")
_RESUME_LABEL_RE = re.compile(r"履歷[：:]\s*(https?://\S+)")
_JOB_LABEL_RE = re.compile(r"職缺[：:]\s*(https?://\S+)")

# 排除/搜尋關鍵字自行迭代指令(2026-08-30新增，見career-profile整合設計
# 文件)。用^錨定開頭，避免"移除排除：詞"被_ADD_EXCLUDE_RE等短版regex
# 誤判命中(如果不錨定，"排除："這個子字串會出現在"移除排除："中間)。
_ADD_EXCLUDE_RE = re.compile(r"^排除[：:]\s*(.+)$")
_REMOVE_EXCLUDE_RE = re.compile(r"^移除排除[：:]\s*(.+)$")
_ADD_WEIGHT_RE = re.compile(r"^關鍵字[：:]\s*(\S+?)(?:\s+(\d+))?$")
_REMOVE_WEIGHT_RE = re.compile(r"^移除關鍵字[：:]\s*(.+)$")
_ALIGNMENT_TRIGGER = "更新履歷分析"


def _parse_resume_job_urls(content: str) -> tuple[str, str, bool] | None:
    """從訊息文字裡解析出(履歷網址, 職缺網址, 是否為用標籤明確指定)。
    優先看「履歷：」「職缺：」標籤；沒有標籤但剛好抓到2個網址時，退回
    「第一個是履歷、第二個是職缺」的假設順序(第三個回傳值標記為
    False,呼叫端要在回覆裡明講這個假設)。都對不上回傳None。"""
    resume_m = _RESUME_LABEL_RE.search(content)
    job_m = _JOB_LABEL_RE.search(content)
    if resume_m and job_m:
        return resume_m.group(1), job_m.group(1), True

    urls = _URL_RE.findall(content)
    if len(urls) == 2:
        return urls[0], urls[1], False

    return None


def _chunk_text(text: str, limit: int = DISCORD_MSG_LIMIT) -> list[str]:
    """依段落邊界切分長文字成多則訊息，避免超過Discord單則訊息長度上限。
    優先在換行處切，段落本身超過limit才強制硬切。"""
    if len(text) <= limit:
        return [text]

    chunks = []
    current = ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        if len(line) <= limit:
            current = line
        else:
            for i in range(0, len(line), limit):
                chunks.append(line[i:i + limit])
            current = ""
    if current:
        chunks.append(current)
    return chunks


async def _send_chunked(channel, text: str):
    for chunk in _chunk_text(text):
        await channel.send(chunk)


async def _handle_resume(channel, file_path: str | None, text_content: str | None):
    """核心處理邏輯：file_path/text_content二擇一(呼叫端保證至少一個不為
    None)。extract_text/match_and_advise都是同步阻塞函式，丟進thread pool
    執行，不擋住事件迴圈。"""
    loop = asyncio.get_running_loop()

    if file_path:
        try:
            resume_text = await loop.run_in_executor(None, resume_matcher.extract_text, file_path)
        except Exception as e:
            await channel.send(f"履歷解析失敗：{e}")
            return
    else:
        resume_text = text_content

    if not resume_text or not resume_text.strip():
        await channel.send("解析出來是空白內容，請確認檔案本身有文字(不是純掃描圖片PDF)，或直接貼上履歷文字。")
        return

    await channel.send("收到了，正在比對職缺+分析履歷，請稍候（約需10~30秒）...")

    result = await loop.run_in_executor(None, resume_matcher.match_and_advise, resume_text)
    if result is None:
        await channel.send("AI比對失敗（額度用盡/網路錯誤/回應格式不對），請稍後再試。")
        return

    await _send_chunked(channel, resume_matcher.format_result_text(result))


async def _handle_specific_match(channel, resume_url: str, job_url: str, order_assumed: bool):
    """單一職缺評估模式：抓履歷網址+職缺網址的內容，評估契合度。"""
    loop = asyncio.get_running_loop()

    note = "沒看到「履歷：」「職缺：」標籤，假設第一個網址是履歷、第二個是職缺——如果猜錯了請重傳並加上標籤。\n" if order_assumed else ""
    await channel.send(f"{note}收到兩個連結，正在抓取內容+評估契合度，請稍候（約需10~30秒）...")

    try:
        resume_text = await loop.run_in_executor(None, resume_matcher.fetch_url_text, resume_url)
    except Exception as e:
        await channel.send(f"履歷連結抓取失敗：{e}")
        return
    if not resume_text.strip():
        await channel.send("履歷連結抓出來是空白內容，可能是純JS渲染的網頁(目前抓不到這種)，或連結已失效。")
        return

    try:
        job_text = await loop.run_in_executor(None, resume_matcher.fetch_url_text, job_url)
    except Exception as e:
        await channel.send(f"職缺連結抓取失敗：{e}")
        return
    if not job_text.strip():
        await channel.send("職缺連結抓出來是空白內容，可能是純JS渲染的網頁(目前抓不到這種)。")
        return

    result = await loop.run_in_executor(None, resume_matcher.evaluate_specific_match, resume_text, job_text)
    if result is None:
        await channel.send("AI評估失敗（額度用盡/網路錯誤/回應格式不對），請稍後再試。")
        return

    await _send_chunked(channel, resume_matcher.format_specific_match_result(result))


async def _handle_keyword_command(channel, content: str) -> bool:
    """比對排除/關鍵字自行迭代的4種文字指令，命中就直接套用(使用者自己
    講的詞，不是AI建議，不需要確認步驟——2026-08-01設計定案)並回覆，
    回傳True。沒命中任何指令回傳False，呼叫端繼續往下走既有的履歷/URL
    判斷邏輯。這幾個regex必須排在既有的100字履歷長度判斷、URL-pair
    判斷之前檢查，否則這類短指令會被誤判成「太短不是履歷」，掉進最後
    的說明文字分支(見設計文件「實作順序陷阱」)。"""
    m = _REMOVE_EXCLUDE_RE.match(content)
    if m:
        word = m.group(1).strip()
        existed = internship_util.remove_exclude_keyword(word)
        internship_util.invalidate_cache()
        await channel.send(f"已從排除清單移除「{word}」。" if existed else f"「{word}」本來就不在排除清單裡，沒有變動。")
        return True

    m = _REMOVE_WEIGHT_RE.match(content)
    if m:
        word = m.group(1).strip()
        existed = internship_util.remove_weight_keyword(word)
        internship_util.invalidate_cache()
        await channel.send(f"已移除關鍵字「{word}」。" if existed else f"「{word}」本來就不在關鍵字清單裡，沒有變動。")
        return True

    m = _ADD_EXCLUDE_RE.match(content)
    if m:
        word = m.group(1).strip()
        internship_util.add_exclude_keyword(word)
        internship_util.invalidate_cache()
        await channel.send(f"已加入排除清單：「{word}」。")
        return True

    m = _ADD_WEIGHT_RE.match(content)
    if m:
        word = m.group(1).strip()
        weight = int(m.group(2)) if m.group(2) else internship_util.DEFAULT_KEYWORD_WEIGHT
        internship_util.add_weight_keyword(word, weight)
        internship_util.invalidate_cache()
        await channel.send(f"已加入關鍵字：「{word}」（權重{weight}）。")
        return True

    return False


async def _handle_resume_alignment(message: discord.Message):
    """「更新履歷分析」觸發：git pull career-profile → 解析target_roles+
    履歷 → 爬+算詞頻+比對 → 分段回覆，全部包進run_in_executor(2026-08-01
    設計定案)。履歷來源比照_handle_resume的附件解析——這個bot不持久化
    履歷內容(見docstring PII處理原則)，需要在同一則訊息附上履歷檔案。"""
    channel = message.channel
    supported_attachments = [
        a for a in message.attachments
        if Path(a.filename).suffix.lower() in SUPPORTED_SUFFIXES
    ]
    if not supported_attachments:
        await channel.send(
            f"「{_ALIGNMENT_TRIGGER}」要跟履歷檔案一起附上"
            f"({'/'.join(SUPPORTED_SUFFIXES)})，這個bot不會記住之前上傳過的履歷內容。"
        )
        return

    attachment = supported_attachments[0]
    with tempfile.NamedTemporaryFile(suffix=Path(attachment.filename).suffix, delete=False) as tmp:
        tmp_path = tmp.name

    loop = asyncio.get_running_loop()
    try:
        await attachment.save(Path(tmp_path))
        try:
            resume_text = await loop.run_in_executor(None, resume_matcher.extract_text, tmp_path)
        except Exception as e:
            await channel.send(f"履歷解析失敗：{e}")
            return
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    if not resume_text or not resume_text.strip():
        await channel.send("解析出來是空白內容，請確認檔案本身有文字(不是純掃描圖片PDF)。")
        return

    await channel.send(
        "收到了，正在git pull career-profile+爬取真實招募文案分析落差，"
        "這會花一點時間（每個target_role都要跑一輪104/518/yes123/gift的抓取）..."
    )

    pull_err = await loop.run_in_executor(None, career_alignment.pull_latest)
    if pull_err:
        await channel.send(f"警告：career-profile git pull失敗，沿用本機既有清單：{pull_err}")

    result = await loop.run_in_executor(None, career_alignment.analyze, resume_text)
    await _send_chunked(channel, career_alignment.format_report_text(result))


@client.event
async def on_ready():
    logger.info("resume_bot已上線：%s", client.user)


@client.event
async def on_message(message: discord.Message):
    if message.author == client.user:
        return
    if str(message.channel.id) != RESUME_BOT_CHANNEL_ID:
        return  # 只處理指定頻道，避免在其他頻道誤觸發(履歷內容屬PII，見docstring)

    content = (message.content or "").strip()

    # 排除/關鍵字自行迭代指令 + 履歷對齊分析觸發，必須排在下面的附件/
    # URL-pair/100字履歷長度判斷之前檢查(見_handle_keyword_command
    # docstring「實作順序陷阱」)。
    if content:
        if await _handle_keyword_command(message.channel, content):
            return
        if content == _ALIGNMENT_TRIGGER:
            await _handle_resume_alignment(message)
            return

    supported_attachments = [
        a for a in message.attachments
        if Path(a.filename).suffix.lower() in SUPPORTED_SUFFIXES
    ]

    if supported_attachments:
        attachment = supported_attachments[0]
        if attachment.size > MAX_ATTACHMENT_BYTES:
            await message.channel.send("檔案太大了(超過10MB)，履歷不需要這麼大，請確認檔案內容。")
            return
        with tempfile.NamedTemporaryFile(
            suffix=Path(attachment.filename).suffix, delete=False
        ) as tmp:
            tmp_path = tmp.name
        try:
            await attachment.save(Path(tmp_path))
            await _handle_resume(message.channel, tmp_path, None)
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        return

    if message.attachments:
        # 有附件但副檔名不支援，明確告知而不是靜默忽略。
        await message.channel.send(f"不支援的履歷檔案格式，請上傳 {'/'.join(SUPPORTED_SUFFIXES)} 其中一種。")
        return

    if message.content:
        parsed_urls = _parse_resume_job_urls(message.content)
        if parsed_urls:
            resume_url, job_url, labeled = parsed_urls
            await _handle_specific_match(message.channel, resume_url, job_url, order_assumed=not labeled)
            return

    if message.content and len(message.content.strip()) >= MIN_TEXT_RESUME_CHARS:
        await _handle_resume(message.channel, None, message.content)
        return

    await message.channel.send(
        "在這裡上傳履歷檔案(pdf/docx/txt/md)，或直接貼上履歷全文(需超過100字)，"
        "我會比對目前的實習職缺+給修改建議。\n\n"
        "或者貼兩個網址評估單一職缺：\n履歷：<連結>\n職缺：<連結>"
    )


def main():
    if not BOT_TOKEN:
        raise SystemExit("缺少環境變數 DISCORD_BOT_TOKEN，請確認 .env 已設定")
    if not RESUME_BOT_CHANNEL_ID:
        raise SystemExit("缺少環境變數 RESUME_BOT_CHANNEL_ID，請確認 .env 已設定")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    db.init_db()
    client.run(BOT_TOKEN)


if __name__ == "__main__":
    main()
