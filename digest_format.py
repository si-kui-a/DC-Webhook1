"""
digest_format.py — 晚間彙整頻道(甲類,AI敘事交叉比對)的訊息組裝。

只負責把 ai_insight.build_channel_digest() 回傳的結構化JSON排版組裝成
Discord embed payload,不呼叫任何AI——排版邏輯由程式碼決定,不讓AI自由
發揮格式,降低跑版風險(見設計討論)。

長度控制:Discord單一embed(description+所有fields加總)硬性上限6000字元、
單一field.value上限1024字元。最多輸出2則embed(見設計討論——訊息1依優先度
貪婪塞滿,溢出的續到訊息2,訊息2還裝不下的才真的省略)。points已經是
ai_insight回傳的優先度排序,這裡不重新排序,只依序塞入直到超過預算。
"""

MSG_BUDGET = 5500  # 留緩衝空間給Discord 6000上限,扣掉title/footer開銷
DESC_MAX = 3000    # overview上限,避免總覽本身就把整個預算吃光,沒空間給重點
FIELD_VALUE_MAX = 1024


def _point_to_lines(p: dict) -> str:
    """來源網址獨立於quote之外一律顯示(2026-07-31修正)：原本url只在有
    quote時才輸出，但AI允許quote留空("沒有適合的引用就留空字串")，導致
    沒有引用的重點完全不附來源網址——使用者確認多篇貼文統整的推播都
    要附來源網址，不應該取決於有沒有引用原文。引用文字另起一行放來源
    連結(不是接在引用同一行後面)，方便閱讀時一眼看到「這句話出自哪裡」。"""
    lines = [f"• {p['point']}"]
    if p.get("quote"):
        src = p.get("source_name") or ""
        title = p.get("source_title") or ""
        attribution = f" —— {src}" if src else ""
        attribution += f"《{title}》" if title else ""
        lines.append(f'  > "{p["quote"]}"' + attribution)
    url = p.get("source_url") or ""
    if url:
        lines.append(f"  {url}")
    return "\n".join(lines)


def _group_by_category(points: list[dict]) -> list[tuple[str, list[dict]]]:
    """依points出現順序(已經是ai_insight回傳的優先度排序)分組,同分類的
    points不管是否連續一律歸在一起,分類本身的順序取決於該分類第一次
    出現(也就是該分類最高優先度的重點)的位置。"""
    order = []
    groups: dict[str, list[dict]] = {}
    for p in points:
        cat = p["category"]
        if cat not in groups:
            groups[cat] = []
            order.append(cat)
        groups[cat].append(p)
    return [(cat, groups[cat]) for cat in order]


def _fit_groups(groups: list[tuple[str, list[dict]]], budget: int) -> tuple[list[dict], list[tuple[str, list[dict]]]]:
    """貪婪把分類塞進budget,回傳(已收錄的field清單, 還沒收錄的分類清單)。
    至少收錄第一個分類(即使超過budget也收,避免完全空白的訊息)。"""
    fields = []
    used = 0
    for i, (cat, pts) in enumerate(groups):
        value = "\n".join(_point_to_lines(p) for p in pts)
        if len(value) > FIELD_VALUE_MAX:
            value = value[: FIELD_VALUE_MAX - 6].rstrip() + "…(略)"
        cost = len(cat) + len(value)
        if fields and used + cost > budget:
            return fields, groups[i:]
        fields.append({"name": cat[:256], "value": value})
        used += cost
    return fields, []


def build_digest_embeds(digest: dict, channel_title: str, date_str: str) -> tuple[list[dict], int]:
    """
    回傳 (embed payload 清單, 因篇幅省略的重點數)。最多2則embed。
    digest 格式見 ai_insight.build_channel_digest() 回傳值。
    """
    groups = _group_by_category(digest["points"])

    overview = digest["overview"]
    if len(overview) > DESC_MAX:
        overview = overview[:DESC_MAX].rstrip() + "…"

    budget1 = MSG_BUDGET - len(overview)
    fields1, remaining = _fit_groups(groups, budget1)

    embeds = [{
        "title": f"{channel_title} 每日彙整 — {date_str}"[:256],
        "description": overview,
        "fields": fields1,
        "color": 15105570,  # 橙色,呼應push_webhook.py既有「推論性內容」色碼慣例
    }]

    omitted_count = 0
    if remaining:
        fields2, still_remaining = _fit_groups(remaining, MSG_BUDGET)
        embeds.append({
            "title": f"{channel_title} 每日彙整（續）— {date_str}"[:256],
            "description": "",
            "fields": fields2,
            "color": 15105570,
        })
        omitted_count = sum(len(pts) for _, pts in still_remaining)

    if omitted_count:
        footer_note = f"另有 {omitted_count} 則重點因篇幅省略"
        embeds[-1]["footer"] = {"text": footer_note}

    return embeds, omitted_count


def build_portfolio_embed(channel_title: str, date_str: str, portfolio: dict,
                           positions: list[dict], action_lines: list[str]) -> dict:
    """模擬持倉頻道的訊息組裝。跟build_digest_embeds()不同——內容量本身有界
    (持倉數/今日動作數都不會失控成百筆)，不需要2則訊息的分頁邏輯，只做
    單一embed的欄位長度保險截斷。"""
    total_value = portfolio["current_cash"] + sum(p.get("market_value", 0) for p in positions)
    starting = portfolio["starting_capital"]
    return_pct = (total_value - starting) / starting * 100 if starting else 0.0

    overview = (
        f"總資產:{total_value:,.2f} {portfolio['currency']}"
        f"(起始本金{starting:,.2f}，累計報酬率{return_pct:+.1f}%)\n"
        f"可用現金:{portfolio['current_cash']:,.2f} {portfolio['currency']}"
    )

    fields = []
    if action_lines:
        value = "\n".join(action_lines)
        if len(value) > FIELD_VALUE_MAX:
            value = value[: FIELD_VALUE_MAX - 6].rstrip() + "…(略)"
        fields.append({"name": "今日動作", "value": value})

    if positions:
        pos_lines = [
            f"• {p['symbol']} {p['side']} 數量{p['quantity']:g} 均價{p['avg_cost']:g}"
            f" 現價{p.get('current_price', '?')} 未實現損益{p.get('unrealized_pnl', 0):+,.2f}"
            for p in positions
        ]
        value = "\n".join(pos_lines)
        if len(value) > FIELD_VALUE_MAX:
            value = value[: FIELD_VALUE_MAX - 6].rstrip() + "…(略)"
        fields.append({"name": "目前持倉", "value": value})
    else:
        fields.append({"name": "目前持倉", "value": "(空手)"})

    return {
        "title": f"{channel_title} — {date_str}"[:256],
        "description": overview[:DESC_MAX],
        "fields": fields,
        "color": 15105570,
    }
