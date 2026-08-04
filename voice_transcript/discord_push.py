"""
discord_push.py — 在VOICE_TRANSCRIPT_CHANNEL_ID底下新建一個討論串，推播
逐字稿原稿與修正稿。沿用ip根目錄scripts/discord_admin.py的Bot API/重試
模式，不用webhook(webhook無法建討論串)。

需要.env(從ip根目錄讀取，見_load_root_env)裡的DISCORD_BOT_TOKEN跟
VOICE_TRANSCRIPT_CHANNEL_ID。
"""
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv
import os

API_BASE = "https://discord.com/api/v10"
MAX_RETRIES = 3
DISCORD_MSG_LIMIT = 2000


def _load_root_env():
    root_env = Path(__file__).parent.parent / ".env"
    load_dotenv(dotenv_path=root_env)


_load_root_env()
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
CHANNEL_ID = os.getenv("VOICE_TRANSCRIPT_CHANNEL_ID", "")


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


def _fail_if_error(resp: requests.Response):
    if resp.status_code not in (200, 201, 204):
        print(f"[Discord錯誤 {resp.status_code}] {resp.text}", file=sys.stderr)
        sys.exit(1)


def create_thread(name: str) -> str:
    body = {"name": name[:100], "type": 11, "auto_archive_duration": 1440}
    resp = _request("POST", f"/channels/{CHANNEL_ID}/threads", json_body=body)
    _fail_if_error(resp)
    thread = resp.json()
    return thread["id"]


def _split_for_discord(text: str) -> list[str]:
    """Discord單則訊息上限2000字元，超過就切成多則，盡量在句號/換行處切。"""
    if len(text) <= DISCORD_MSG_LIMIT:
        return [text]
    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= DISCORD_MSG_LIMIT:
            chunks.append(remaining)
            break
        cut = remaining.rfind("。", 0, DISCORD_MSG_LIMIT)
        if cut == -1:
            cut = DISCORD_MSG_LIMIT
        else:
            cut += 1
        chunks.append(remaining[:cut])
        remaining = remaining[cut:]
    return chunks


def post_message(thread_id: str, text: str):
    for chunk in _split_for_discord(text):
        resp = _request("POST", f"/channels/{thread_id}/messages", json_body={"content": chunk})
        _fail_if_error(resp)


def post_file(thread_id: str, file_path: str, message: str = ""):
    """上傳檔案(PDF等)——跟_request()不同，Discord檔案上傳要用multipart
    表單，不是JSON body，這裡不重用_request()(它固定送json_body)，另外
    用requests直接處理multipart(video_digest.py用，2026-08-05新增)。"""
    url = f"{API_BASE}/channels/{thread_id}/messages"
    with open(file_path, "rb") as f:
        for attempt in range(MAX_RETRIES):
            resp = requests.post(
                url,
                headers={"Authorization": f"Bot {BOT_TOKEN}"},  # multipart不能手動設Content-Type，requests自己算boundary
                data={"content": message} if message else None,
                files={"file": (os.path.basename(file_path), f, "application/octet-stream")},
                timeout=60,
            )
            if resp.status_code == 429:
                retry_after = resp.json().get("retry_after", 2 ** attempt)
                time.sleep(retry_after)
                f.seek(0)
                continue
            _fail_if_error(resp)
            return


def push_transcript(raw_text: str, corrected_text: str, summary: str | None) -> str:
    """建討論串 -> 推原稿 -> 推修正稿+摘要。回傳討論串ID。"""
    thread_name = f"逐字稿 {datetime.now():%Y-%m-%d %H:%M}"
    thread_id = create_thread(thread_name)
    print(f"已建立討論串：{thread_name}（ID: {thread_id}）")

    post_message(thread_id, f"【原稿】\n{raw_text}")

    followup = f"【修正稿】\n{corrected_text}"
    if summary:
        followup += f"\n\n【摘要】\n{summary}"
    post_message(thread_id, followup)

    return thread_id


if __name__ == "__main__":
    if not BOT_TOKEN or not CHANNEL_ID:
        print("缺少 DISCORD_BOT_TOKEN 或 VOICE_TRANSCRIPT_CHANNEL_ID，檢查根目錄.env", file=sys.stderr)
        sys.exit(1)
    if len(sys.argv) != 2:
        print("用法：python discord_push.py <逐字稿txt檔路徑>（測試用，正式流程由run_session.py呼叫）", file=sys.stderr)
        sys.exit(1)
    text = Path(sys.argv[1]).read_text(encoding="utf-8")
    push_transcript(text, text, None)
