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

使用方式：私訊(DM)這個bot，附上履歷檔案(pdf/docx/txt/md)，或直接把履歷
內容貼成文字訊息(超過100字才視為履歷內容，避免把「hi」這種閒聊誤判)。
bot會回覆最適合的實習職缺推薦+履歷修改建議。

低成本設計：呼叫Gemini只在使用者主動私訊時才觸發，不是背景輪詢，跟
resume_matcher.py核心邏輯的低成本原則一致；resume_matcher的AI呼叫本身
是同步阻塞的(urllib)，這裡用run_in_executor丟到thread pool執行，避免
擋住discord.py的事件迴圈(擋住會導致心跳逾時、bot被Discord判定斷線)。
"""
import asyncio
import logging
import os
import tempfile
from pathlib import Path

import discord
from dotenv import load_dotenv

load_dotenv()

import db
import resume_matcher

logger = logging.getLogger("resume_bot")

BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
SUPPORTED_SUFFIXES = (".pdf", ".docx", ".txt", ".md")
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10MB，履歷不可能超過這個大小，防止異常大檔拖垮process
MIN_TEXT_RESUME_CHARS = 100  # 純文字訊息要超過這個長度才當履歷處理，避免把閒聊訊息誤判
DISCORD_MSG_LIMIT = 1900  # Discord單則訊息2000字上限，留一點餘裕

intents = discord.Intents.default()
intents.message_content = True  # 需要Developer Portal手動啟用的privileged intent
intents.dm_messages = True

client = discord.Client(intents=intents)


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


@client.event
async def on_ready():
    logger.info("resume_bot已上線：%s", client.user)


@client.event
async def on_message(message: discord.Message):
    if message.author == client.user:
        return
    if not isinstance(message.channel, discord.DMChannel):
        return  # 只處理私訊，避免在公開頻道誤觸發或洩漏他人履歷內容

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

    if message.content and len(message.content.strip()) >= MIN_TEXT_RESUME_CHARS:
        await _handle_resume(message.channel, None, message.content)
        return

    await message.channel.send(
        "私訊我履歷檔案(pdf/docx/txt/md)，或直接貼上履歷全文(需超過100字)，"
        "我會比對目前的實習職缺+給修改建議。"
    )


def main():
    if not BOT_TOKEN:
        raise SystemExit("缺少環境變數 DISCORD_BOT_TOKEN，請確認 .env 已設定")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    db.init_db()
    client.run(BOT_TOKEN)


if __name__ == "__main__":
    main()
