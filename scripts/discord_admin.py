"""
scripts/discord_admin.py — Discord 伺服器頻道/webhook 管理小工具。

只用 Bot Token 呼叫 Discord REST API（不建立 Gateway 連線，不需要任何
Privileged Intent）。Bot 只需要 View Channels / Manage Channels /
Manage Webhooks 三個權限。

用法：
    python scripts/discord_admin.py list-channels
    python scripts/discord_admin.py create-channel --name "總經指標追蹤"
    python scripts/discord_admin.py create-webhook --channel-id 123456789 --name "總經指標追蹤"
"""
import argparse
import os
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv()

API_BASE = "https://discord.com/api/v10"
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
GUILD_ID = os.getenv("DISCORD_GUILD_ID", "")

MAX_RETRIES = 3


def _headers() -> dict:
    return {
        "Authorization": f"Bot {BOT_TOKEN}",
        "Content-Type": "application/json",
    }


def _request(method: str, path: str, json_body: dict | None = None) -> requests.Response:
    """呼叫 Discord API，對 429（限流）與 5xx 做指數退避重試。"""
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
            wait = 2 ** attempt
            print(f"[伺服器錯誤 {resp.status_code}] 第 {attempt + 1}/{MAX_RETRIES} 次重試，等待 {wait} 秒", file=sys.stderr)
            time.sleep(wait)
            continue
        return resp
    return resp


def _fail_if_error(resp: requests.Response):
    if resp.status_code not in (200, 201, 204):
        hint = ""
        if resp.status_code == 401:
            hint = "（DISCORD_BOT_TOKEN 錯誤或已失效）"
        elif resp.status_code == 403:
            hint = "（Bot 缺少對應權限，檢查身分組的 Manage Channels / Manage Webhooks）"
        elif resp.status_code == 404:
            hint = "（伺服器/頻道 ID 錯誤，或 Bot 沒被加進這個伺服器）"
        print(f"錯誤 {resp.status_code}{hint}：{resp.text}", file=sys.stderr)
        sys.exit(1)


def list_channels():
    resp = _request("GET", f"/guilds/{GUILD_ID}/channels")
    _fail_if_error(resp)
    channels = resp.json()
    text_channels = [c for c in channels if c.get("type") == 0]
    print(f"共 {len(text_channels)} 個文字頻道：")
    for c in sorted(text_channels, key=lambda c: c.get("position", 0)):
        print(f"  {c['id']}  #{c['name']}")


def create_channel(name: str, category_id: str | None = None) -> str:
    body = {"name": name, "type": 0}
    if category_id:
        body["parent_id"] = category_id
    resp = _request("POST", f"/guilds/{GUILD_ID}/channels", json_body=body)
    _fail_if_error(resp)
    channel = resp.json()
    print(f"已建立頻道 #{channel['name']}（ID: {channel['id']}）")
    return channel["id"]


def rename_channel(channel_id: str, name: str):
    resp = _request("PATCH", f"/channels/{channel_id}", json_body={"name": name})
    _fail_if_error(resp)
    channel = resp.json()
    print(f"已改名為 #{channel['name']}（ID: {channel['id']}）")


def create_webhook(channel_id: str, name: str) -> str:
    resp = _request("POST", f"/channels/{channel_id}/webhooks", json_body={"name": name})
    _fail_if_error(resp)
    webhook = resp.json()
    url = f"https://discord.com/api/webhooks/{webhook['id']}/{webhook['token']}"
    print(f"已建立 webhook：{url}")
    return url


def main():
    if sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

    if not BOT_TOKEN:
        print("缺少環境變數 DISCORD_BOT_TOKEN，請確認 .env 已設定", file=sys.stderr)
        sys.exit(1)
    if not GUILD_ID:
        print("缺少環境變數 DISCORD_GUILD_ID，請確認 .env 已設定", file=sys.stderr)
        sys.exit(1)

    parser = argparse.ArgumentParser(description="Discord 伺服器頻道/webhook 管理")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list-channels", help="列出目前所有文字頻道（唯讀，安全測試用）")

    p_create_channel = sub.add_parser("create-channel", help="建立新文字頻道")
    p_create_channel.add_argument("--name", required=True)
    p_create_channel.add_argument("--category-id", default=None, help="可選，放進指定分類底下")

    p_rename_channel = sub.add_parser("rename-channel", help="重新命名既有頻道")
    p_rename_channel.add_argument("--channel-id", required=True)
    p_rename_channel.add_argument("--name", required=True)

    p_create_webhook = sub.add_parser("create-webhook", help="在指定頻道建立 webhook")
    p_create_webhook.add_argument("--channel-id", required=True)
    p_create_webhook.add_argument("--name", required=True)

    args = parser.parse_args()

    if args.command == "list-channels":
        list_channels()
    elif args.command == "create-channel":
        create_channel(args.name, args.category_id)
    elif args.command == "rename-channel":
        rename_channel(args.channel_id, args.name)
    elif args.command == "create-webhook":
        create_webhook(args.channel_id, args.name)


if __name__ == "__main__":
    main()
