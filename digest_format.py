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
    """引用文字另起一行放來源連結(不是接在引用同一行後面)，方便閱讀時
    視覺上一眼看到「這句話出自哪裡、點哪裡查證」。"""
    lines = [f"• {p['point']}"]
    if p.get("quote"):
        src = p.get("source_name") or ""
        title = p.get("source_title") or ""
        url = p.get("source_url") or ""
        attribution = f" —— {src}" if src else ""
        attribution += f"《{title}》" if title else ""
        lines.append(f'  > "{p["quote"]}"' + attribution)
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
