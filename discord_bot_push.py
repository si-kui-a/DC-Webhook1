"""
discord_bot_push.py — 共用的Discord Bot API小工具(建討論串/推訊息)。

跟scripts/discord_admin.py、voice_transcript/discord_push.py是同一套
auth+重試邏輯，這裡不合併那兩支(各自獨立venv/獨立用途，這個專案裡這種
小工具本來就允許各自一份，見discord_admin.py/voice_transcript/discord_
push.py既有的重複模式)，但youtube_digest.py這個新功能是main.py(root
venv)自己要用的，另開一份給main.py專用，避免main.py去import voice_
transcript那個獨立venv底下的模組。
"""
import sys
import time

import requests
from dotenv import load_dotenv
import os

load_dotenv()

API_BASE = "https://discord.com/api/v10"
MAX_RETRIES = 3
DISCORD_MSG_LIMIT = 2000

BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")


def _headers() -> dict:
    return {
        "Authorization": f"Bot {BOT_TOKEN}",
        "Content-Type": "application/json",
    }


def _request(method: str, path: str, json_body: dict | None = None) -> requests.Response:
    url = f"{API_BASE}{path}"
    resp = None
    for attempt in range(MAX_RETRIES):
        resp = requests.request(method, url, headers=_headers(), json=json_body, timeout=15)
        if resp.status_code == 429:
            retry_after = resp.json().get("retry_after", 2 ** attempt)
            print(f"[限流] 等待 {retry_after} 秒後重試...", file=sys.stderr)
            time.sleep(retry_after)
            continue
        if resp.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        return resp
    return resp


def create_thread(channel_id: str, name: str) -> str | None:
    """在指定頻道下建一個公開討論串，回傳thread_id；失敗回傳None(呼叫端
    決定要不要中斷，不在這裡拋例外中斷整個批次)。"""
    body = {"name": name[:100], "type": 11, "auto_archive_duration": 10080}  # 7天
    resp = _request("POST", f"/channels/{channel_id}/threads", json_body=body)
    if resp is None or resp.status_code not in (200, 201):
        print(f"[Discord錯誤] 建討論串失敗：{resp.status_code if resp else 'no response'} {resp.text if resp else ''}", file=sys.stderr)
        return None
    return resp.json()["id"]


def _split_for_discord(text: str) -> list[str]:
    if len(text) <= DISCORD_MSG_LIMIT:
        return [text]
    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= DISCORD_MSG_LIMIT:
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, DISCORD_MSG_LIMIT)
        if cut == -1:
            cut = DISCORD_MSG_LIMIT
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    return chunks


def post_message(channel_or_thread_id: str, text: str) -> bool:
    """對頻道或討論串都適用(Discord的訊息API對兩者是同一個端點)。回傳
    是否全部分段都成功。"""
    all_ok = True
    for chunk in _split_for_discord(text):
        resp = _request("POST", f"/channels/{channel_or_thread_id}/messages", json_body={"content": chunk})
        if resp is None or resp.status_code not in (200, 201):
            print(f"[Discord錯誤] 推播失敗：{resp.status_code if resp else 'no response'} {resp.text if resp else ''}", file=sys.stderr)
            all_ok = False
    return all_ok
