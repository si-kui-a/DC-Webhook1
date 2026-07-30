"""
notify_telegram.py — Telegram Bot 通知層。

預設共用 finfeed 既有的 bot token(財經類推播)，讓 intel-pusher 每筆推播
同時送到 Telegram。獎學金/實習屬於教育類內容，跟財經無關，改用獨立的
「Schule mithelfer」bot(使用者確認2026-07-30)——send_message()可傳入
bot_token/chat_id覆寫預設值，不用另開一支重複的模組。
"""
import logging
import re
import time
import os

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("notify_telegram")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# 獎學金/實習(教育類，非財經)專用bot——與finfeed bot分開，使用者確認2026-07-30。
TELEGRAM_EDU_BOT_TOKEN = os.getenv("TELEGRAM_EDU_BOT_TOKEN", "")
TELEGRAM_EDU_CHAT_ID = os.getenv("TELEGRAM_EDU_CHAT_ID", "")

MAX_RETRIES = 3


def _to_telegram_markdown(text: str) -> str:
    """Discord/CommonMark用**粗體**(雙星號),Telegram舊版Markdown只認得
    *粗體*(單星號)——雙星號原樣送出會顯示成字面上的星號黏在文字旁邊，
    不會被當成粗體渲染。這裡把雙星號轉成單星號，其餘文字不動。"""
    return re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)


def build_message(title: str, body: str, url: str,
                   sentiment: str | None = None, sentiment_reason: str | None = None) -> str:
    """組出排版清楚的Telegram訊息：標題/內文/情緒判斷(可選)/連結各自分段，
    用分隔線隔開，不會黏成一團。body內若含Discord風格的**粗體**會轉成
    Telegram認得的*粗體*。"""
    sep = "\n" + "─" * 20 + "\n"
    parts = [f"*{title}*", sep.strip("\n"), _to_telegram_markdown(body.strip())]
    if sentiment:
        parts.append(f"\n📊 AI 情緒判斷：{sentiment}（{sentiment_reason or '無理由'}）")
    parts.append(f"\n🔗 {url}")
    return "\n".join(parts)


def send_message(text: str, parse_mode: str = "Markdown",
                  bot_token: str | None = None, chat_id: str | None = None) -> bool:
    """推送文字到Telegram bot。預設用finfeed bot，傳入bot_token/chat_id
    可送到其他bot(例如TELEGRAM_EDU_BOT_TOKEN/TELEGRAM_EDU_CHAT_ID)。
    成功回傳 True，失敗回傳 False。"""
    token = bot_token if bot_token is not None else TELEGRAM_BOT_TOKEN
    chat = chat_id if chat_id is not None else TELEGRAM_CHAT_ID
    if not token or not chat:
        logger.warning("bot token 或 chat_id 未設定，跳過 Telegram 通知")
        return False

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    if len(text) > 4000:
        text = text[:4000] + "\n\n...(內容過長，已截斷)"

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(
                url,
                json={
                    "chat_id": chat,
                    "text": text,
                    "parse_mode": parse_mode,
                },
                timeout=20,
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            wait = 2 ** attempt
            logger.warning(f"Telegram 發送失敗（第 {attempt+1}/{MAX_RETRIES} 次）：{e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(wait)

    return False
