"""
push_webhook.py — 通用 Discord Webhook 發送器。
各推播頻道共用同一份邏輯：只是 URL 不同，格式一致(頻道數量會隨新增
來源增減，見scripts/show_source_status.py，不在此寫死數字)。
含 429 退避重試，避免短時間大量推播觸發 rate limit。
"""
import time
import logging
import requests

logger = logging.getLogger("push_webhook")

MAX_RETRIES = 3
BASE_BACKOFF_SECONDS = 2


def build_embed(title: str, description: str, url: str, color: int = 5793266,
                 fields: list[dict] | None = None, footer: str | None = None,
                 published_at: str | None = None) -> dict:
    """
    建構標準 Discord Embed payload。
    color 預設為藍紫色（一般事實類），呼叫端可依頻道類型覆寫：
      - 統整/分析類建議用橙色（15105570）標示「推論性內容」
      - #china-military-actions 紅標建議用紅色（15158332）

    published_at 是這篇公告本身的發布/會議日期（來自 scraper 的
    item["published_at"]），不是推播時間。各來源格式不統一
    （fed 是 "6/17/2026"、cbc 是 RFC822、tsmc 是 "2026/06/10"），刻意不解析
    成統一格式直接塞進去顯示——同一標題出現多次時（例如 fed 同一份
    "Federal Reserve issues FOMC statement" 標題對應不同月份的會議），
    沒有日期會完全無法分辨是哪一次，這裡只求「看得到、對得上原文」，
    不追求跨來源日期格式一致。
    """
    embed = {
        "title": title[:256],          # Discord embed title 上限
        "description": description[:4096],
        "url": url,
        "color": color,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
    }
    if fields:
        embed["fields"] = [{"name": f["name"][:256], "value": f["value"][:1024], "inline": f.get("inline", False)}
                            for f in fields]
    footer_text = footer
    if published_at:
        footer_text = f"{footer} · {published_at}" if footer else published_at
    if footer_text:
        embed["footer"] = {"text": footer_text[:2048]}
    return embed


def send_webhook(webhook_url: str, embed: dict, username: str = "Intel Pusher") -> tuple[bool, int, str | None]:
    """
    發送單則 Embed。回傳 (是否成功, HTTP狀態碼, 錯誤訊息)。
    429 時依 Discord 回傳的 retry_after 等待後重試，最多 MAX_RETRIES 次。
    """
    payload = {"username": username, "embeds": [embed]}

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.post(webhook_url, json=payload, timeout=10)
        except requests.RequestException as e:
            logger.error(f"連線失敗（第 {attempt} 次）：{e}")
            time.sleep(BASE_BACKOFF_SECONDS * attempt)
            continue

        if resp.status_code in (200, 204):
            return True, resp.status_code, None

        if resp.status_code == 429:
            retry_after = resp.json().get("retry_after", BASE_BACKOFF_SECONDS * attempt)
            logger.warning(f"觸發 rate limit，{retry_after} 秒後重試（第 {attempt} 次）")
            time.sleep(float(retry_after) + 0.5)
            continue

        # 其他錯誤（403/404/5xx）：不重試，直接回報，讓上層記錄到 delivery_log
        return False, resp.status_code, resp.text[:500]

    return False, 429, "重試次數用盡，仍受 rate limit 阻擋"
