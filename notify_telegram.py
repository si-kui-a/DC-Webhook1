"""
notify_telegram.py — Telegram Bot 通知層。

共用 finfeed 既有的 bot token，讓 intel-pusher 每筆推播同時送到 Telegram。
"""
import logging
import time
import os

import requests
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger("notify_telegram")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

MAX_RETRIES = 3


def send_message(text: str, parse_mode: str = "Markdown") -> bool:
    """推送文字到 Telegram market bot。成功回傳 True，失敗回傳 False。"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID 未設定，跳過 Telegram 通知")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    if len(text) > 4000:
        text = text[:4000] + "\n\n...(內容過長，已截斷)"

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(
                url,
                json={
                    "chat_id": TELEGRAM_CHAT_ID,
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
