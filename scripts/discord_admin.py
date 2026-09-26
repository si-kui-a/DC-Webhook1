"""
scripts/discord_admin.py — Discord 伺服器頻道/webhook 管理小工具。

只用 Bot Token 呼叫 Discord REST API（不建立 Gateway 連線，不需要任何
Privileged Intent）。Bot 只需要 View Channels / Manage Channels /
Manage Webhooks 三個權限。

用法：
    python scripts/discord_admin.py list-channels
    python scripts/discord_admin.py create-channel --name "總經指標追蹤"
    python scripts/discord_admin.py create-webhook --channel-id 123456789 --name "總經指標追蹤"
    python scripts/discord_admin.py delete-webhook --webhook-id 123456789
    python scripts/discord_admin.py rotate-webhook --env-key WEBHOOK_INSTITUTIONAL_TSMC

rotate-webhook（2026-09-26）：webhook 網址本身就是憑證，外洩後只能換新。它在同一頻道
建立同名的新 webhook、直接改寫 .env 那一行、確認新的可用後刪掉舊的，全程只印遮蔽過的
ID——網址不會出現在終端機或 AI 的對話紀錄裡（起因：07-05 四個 webhook 被自動備份寫進
.env.example 的 git 歷史，repo 公開前必須換掉）。
"""
import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()

API_BASE = "https://discord.com/api/v10"
BOT_TOKEN = os.getenv("DISCORD_BOT_TOKEN", "")
GUILD_ID = os.getenv("DISCORD_GUILD_ID", "")

MAX_RETRIES = 3
_NO_WINDOW = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0


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


def delete_webhook(webhook_id: str):
    resp = _request("DELETE", f"/webhooks/{webhook_id}")
    _fail_if_error(resp)
    print(f"已刪除 webhook（ID: {webhook_id}）")


ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
WEBHOOK_URL_RE = re.compile(r"^https://discord(?:app)?\.com/api/webhooks/(\d+)/([\w-]+)$")


def _mask(webhook_id: str) -> str:
    return f"{webhook_id[:4]}…"


def _clipboard_webhook() -> tuple[str, str]:
    """New webhook URL copied in the Discord UI ("Copy Webhook URL"), read straight from the
    Windows clipboard so it is never typed, echoed or pasted into a terminal."""
    out = subprocess.run(["powershell", "-NoProfile", "-Command", "Get-Clipboard"], capture_output=True,
                         text=True, encoding="utf-8", creationflags=_NO_WINDOW).stdout.strip()
    match = WEBHOOK_URL_RE.match(out)
    if not match:
        print("剪貼簿裡不是 Discord webhook 網址（在頻道設定 → 整合 → Webhook 按「複製 Webhook 網址」）",
              file=sys.stderr)
        sys.exit(1)
    return out, match.group(1)


def rotate_webhook(env_key: str, from_clipboard: bool = False):
    old_url = os.getenv(env_key, "").strip().strip('"')
    match = WEBHOOK_URL_RE.match(old_url)
    if not match:
        print(f"{env_key} 在 .env 裡不是 Discord webhook 網址，未變更", file=sys.stderr)
        sys.exit(1)
    old_id = match.group(1)
    # A webhook's own URL authorizes reading and deleting it; no bot permission needed.
    info = requests.get(old_url, timeout=15)
    if info.status_code != 200:
        print(f"{env_key}：舊 webhook {_mask(old_id)} 查詢失敗（HTTP {info.status_code}），未變更", file=sys.stderr)
        sys.exit(1)
    channel_id, name = info.json()["channel_id"], info.json().get("name") or env_key

    if from_clipboard:
        new_url, new_id = _clipboard_webhook()
        if new_id == old_id:
            print(f"{env_key}：剪貼簿裡還是舊的 webhook，未變更", file=sys.stderr)
            sys.exit(1)
    else:
        if not BOT_TOKEN:
            print("沒有 DISCORD_BOT_TOKEN：改在 Discord 頻道設定建立新 webhook、複製網址後加 --from-clipboard",
                  file=sys.stderr)
            sys.exit(1)
        resp = _request("POST", f"/channels/{channel_id}/webhooks", json_body={"name": name})
        _fail_if_error(resp)
        new_id = resp.json()["id"]
        new_url = f"https://discord.com/api/webhooks/{new_id}/{resp.json()['token']}"
    check = requests.get(new_url, timeout=15)
    if check.status_code != 200 or check.json().get("channel_id") != channel_id:
        print(f"{env_key}：新 webhook {_mask(new_id)} 查詢失敗或不在同一頻道，.env 未變更、舊的保留", file=sys.stderr)
        sys.exit(1)
    # Keep the name the old one had (a webhook's own URL may rename it; no bot needed).
    requests.patch(new_url, json={"name": name}, timeout=15)

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines(keepends=True)
    hits = [i for i, line in enumerate(lines) if line.split("=", 1)[0].strip() == env_key]
    if len(hits) != 1:
        print(f"{env_key} 在 .env 出現 {len(hits)} 次，無法安全改寫；新 webhook {_mask(new_id)} 已建立但未寫入",
              file=sys.stderr)
        sys.exit(1)
    newline = "\n" if lines[hits[0]].endswith("\n") else ""
    lines[hits[0]] = f"{env_key}={new_url}{newline}"
    ENV_PATH.write_text("".join(lines), encoding="utf-8")

    deleted = requests.delete(old_url, timeout=15).status_code
    status = "已刪除" if deleted == 204 else f"刪除失敗（HTTP {deleted}），請手動刪除"
    print(f"{env_key}：頻道 {channel_id} 的 webhook {_mask(old_id)} → {_mask(new_id)}，.env 已更新，舊的{status}")


def main():
    if sys.stdout.encoding.lower() != "utf-8":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")

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

    p_delete_webhook = sub.add_parser("delete-webhook", help="刪除指定 webhook")
    p_delete_webhook.add_argument("--webhook-id", required=True)

    p_rotate_webhook = sub.add_parser("rotate-webhook", help="換新 .env 裡某個 webhook（同頻道同名），不印出網址")
    p_rotate_webhook.add_argument("--env-key", required=True, action="append",
                                  help="可重複，例如 --env-key WEBHOOK_A --env-key WEBHOOK_B")
    p_rotate_webhook.add_argument("--from-clipboard", action="store_true",
                                  help="沒有 bot 時：新 webhook 在 Discord 介面建立並複製網址，由剪貼簿讀入（一次一個）")

    args = parser.parse_args()

    # rotate-webhook works from the webhook URLs themselves; everything else needs the bot.
    if args.command != "rotate-webhook":
        if not BOT_TOKEN:
            print("缺少環境變數 DISCORD_BOT_TOKEN，請確認 .env 已設定", file=sys.stderr)
            sys.exit(1)
        if not GUILD_ID:
            print("缺少環境變數 DISCORD_GUILD_ID，請確認 .env 已設定", file=sys.stderr)
            sys.exit(1)
    if args.command == "rotate-webhook" and args.from_clipboard and len(args.env_key) != 1:
        print("--from-clipboard 一次只能換一個（剪貼簿只有一個網址）", file=sys.stderr)
        sys.exit(1)

    if args.command == "list-channels":
        list_channels()
    elif args.command == "create-channel":
        create_channel(args.name, args.category_id)
    elif args.command == "rename-channel":
        rename_channel(args.channel_id, args.name)
    elif args.command == "create-webhook":
        create_webhook(args.channel_id, args.name)
    elif args.command == "delete-webhook":
        delete_webhook(args.webhook_id)
    elif args.command == "rotate-webhook":
        for key in args.env_key:
            rotate_webhook(key, from_clipboard=args.from_clipboard)


if __name__ == "__main__":
    main()
