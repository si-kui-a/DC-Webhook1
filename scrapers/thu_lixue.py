"""scrapers/thu_lixue.py — 東海大學勵學基金(src.thu.edu.tw)公告的申請時程抽取
(2026-10-07新增)。

勵學基金是東海大學高教深耕附錄一的經濟不利學生輔導計畫(課業輔導、
各系專業學術實踐、跨域學習、樂學培育、職涯輔導、共通職能、師長推薦等
輔導面向)。各面向的「細則」頁面是圖片，申請時程只寫在最新消息的公告
文字裡，所以這支只讀公告：

1. 讀最新消息列表(id、標題、公告日期)。
2. 只讀近 LOOKBACK_DAYS 天內的公告內文，抽出民國年月日(含「A至B」
   期間)與「10/16 前」這類截止寫法，連同日期前的說明文字當成事項標題。
3. 推播節奏在jobs/thu_lixue.py。

抽取是規則式的，說明文字取自公告原句，所以推播訊息一律附公告連結，
讓人點進去核對。新公告格式若抽不到日期，job會在公告隔天推播「新公告」
並註明沒有抽到日期，不會靜默漏掉。
"""
from __future__ import annotations

import html
import re
from datetime import date, timedelta

from scrapers.http_client import get

BASE = "https://src.thu.edu.tw/web/news/"
LIST_URL = BASE + "list.php?page={page}"
DETAIL_URL = BASE + "detail.php?cid=&id={id}"
# 一頁約20則、一學年約40則：學年初公告到學年末還要讀得到(LOOKBACK_DAYS)，讀3頁
LIST_PAGES = 3
# 學年初的公告會一次寫到學年末的日期(共通職能9/7公告116年6月的成果收件)，
# 回看要涵蓋一整個學年
LOOKBACK_DAYS = 330

_WEEKDAY = r"(?:\s*[（(](?:星期)?[一二三四五六日][)）])?"
_TIME = r"(?:\s*\d{1,2}:\d{2})?"
_ROC = r"(1\d\d)\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日" + _WEEKDAY + _TIME
_RANGE_SEP = r"\s*(?:至|到|~|～|－|—|-)\s*"
_ROC_RANGE_RE = re.compile(_ROC + _RANGE_SEP + _ROC)
_ROC_RE = re.compile(_ROC)
# 「請於10/16前」「10/16(五)前」：沒寫年份的截止日，只認後面接「前/止」的寫法，
# 避免把「4月累積12小時」之類的數字當日期
_MD_DEADLINE_RE = re.compile(r"(?<![\d/])(\d{1,2})/(\d{1,2})" + _WEEKDAY + r"\s*(?:前|止)")
_CARD_RE = re.compile(
    r"<h5 class='list-title'><a\s+href\s*=\s*'[^']*?id=(\d+)'>(.*?)</a></h5>.*?日期\s*:\s*(\d{4}-\d{2}-\d{2})",
    re.S)
_TITLE_NOISE_RE = re.compile(r"^【[^】]*公告】\s*|^\(\d{3}-\d\)\s*")
_LABEL_STRIP = " :：，,。.　\t-–—★→※●▶"
_LEAD_WORDS = ("即日起至", "即日起", "請於", "於", "至")


def _roc(y: str, m: str, d: str) -> date:
    return date(int(y) + 1911, int(m), int(d))


def parse_list(page_html: str) -> list[dict]:
    """列表頁 -> [{"id", "title", "date"}]。"""
    out = []
    for m in _CARD_RE.finditer(page_html):
        title = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", m.group(2)))).strip()
        out.append({"id": int(m.group(1)), "title": title, "date": m.group(3)})
    return out


def html_to_lines(page_html: str) -> list[str]:
    """公告內文 -> 去掉標籤的非空行(只取「點閱」之後到「下一則」之前的正文)。"""
    body = re.sub(r"<script.*?</script>|<style.*?</style>", "", page_html, flags=re.S | re.I)
    body = re.sub(r"<br\s*/?>|</p>|</div>|</li>|</h\d>|</tr>", "\n", body, flags=re.I)
    text = html.unescape(re.sub(r"<[^>]+>", "", body))
    lines = [re.sub(r"[ \t　\xa0]+", " ", ln).strip() for ln in text.splitlines()]
    lines = [ln for ln in lines if ln]
    start = next((i + 1 for i, ln in enumerate(lines) if ln.startswith("點閱")), 0)
    end = next((i for i, ln in enumerate(lines) if i >= start and ln.startswith("下一則")), len(lines))
    return lines[start:end]


def _cjk(text: str) -> int:
    return len(re.findall(r"[一-鿿]", text))


def _label(lines: list[str], line_no: int, prefix: str, suffix: str = "") -> str:
    """日期所在子句的說明文字。依序：日期前的子句(夠長就用)、日期前＋日期後的子句、
    往上最近一行沒有日期的文字。長句只取日期所在的子句，不從中間截斷。"""
    prefix = re.split(r"[。；！？]", prefix)[-1]
    if len(prefix) > 30:
        prefix = re.split(r"[，,]", prefix)[-1]
    prefix = prefix.strip(_LABEL_STRIP + "(（")
    for word in _LEAD_WORDS:
        if prefix.endswith(word):
            prefix = prefix[: -len(word)].strip(_LABEL_STRIP + "(（")
    suffix = re.split(r"[，,。；！？)）\n]", suffix)[0].strip(_LABEL_STRIP)
    if _cjk(prefix) >= 4:
        return prefix[-40:]
    if _cjk(suffix) >= 2:
        prefix = re.sub(r"^(?:並|且|請)$", "", prefix)  # 「並於10/16前至iLearn…」的連接詞
        return f"{prefix} {suffix}".strip()[:40]
    for ln in reversed(lines[max(0, line_no - 4):line_no]):
        cand = ln.strip(_LABEL_STRIP)
        if cand in ("至", "到", "~") or _ROC_RE.search(cand) or len(cand) < 2:
            continue
        return (cand + (" " + prefix if prefix else ""))[-40:]
    return prefix or "（公告未寫說明）"


def extract_events(lines: list[str], posted: date) -> list[dict]:
    """公告正文行 -> [{"date", "label", "kind"}]；kind: start/end/deadline/date。

    「A至B」拆成開始與截止兩筆。跨行的期間(「115年8月24日 (一) 09:00 / 至 /
    115年9月24日」)先把行接起來再比對，再用字元位置換回行號取說明文字。"""
    joined, offsets = "", []
    for ln in lines:
        offsets.append(len(joined))
        joined += ln + "\n"

    def line_of(pos: int) -> int:
        return max(i for i, off in enumerate(offsets) if off <= pos)

    def prefix_of(pos: int) -> str:
        return joined[offsets[line_of(pos)]:pos]

    events, taken = [], []
    for m in _ROC_RANGE_RE.finditer(joined):
        n = line_of(m.start())
        label = _label(lines, n, prefix_of(m.start()))
        events.append({"date": _roc(*m.group(1, 2, 3)), "label": label, "kind": "start"})
        events.append({"date": _roc(*m.group(4, 5, 6)), "label": label, "kind": "end"})
        taken.append((m.start(), m.end()))
    for m in _ROC_RE.finditer(joined):
        if any(a <= m.start() < b for a, b in taken):
            continue
        n = line_of(m.start())
        tail = joined[m.end():m.end() + 6]
        kind = "deadline" if re.match(r"\s*(?:前|止)", tail) or "截止" in lines[n] else "date"
        label = _label(lines, n, prefix_of(m.start()), joined[m.end():m.end() + 40])
        events.append({"date": _roc(*m.group(1, 2, 3)), "label": label, "kind": kind})
    for m in _MD_DEADLINE_RE.finditer(joined):
        month, day = int(m.group(1)), int(m.group(2))
        try:
            when = date(posted.year, month, day)
        except ValueError:
            continue
        if when < posted - timedelta(days=30):  # 12月公告寫「1/10前」= 隔年
            when = date(posted.year + 1, month, day)
        label = _label(lines, line_of(m.start()), prefix_of(m.start()), joined[m.end():m.end() + 40])
        events.append({"date": when, "label": label, "kind": "deadline"})
    seen, unique = set(), []
    for e in events:
        key = (e["date"], e["kind"], e["label"])
        if key not in seen:
            seen.add(key)
            unique.append(e)
    return unique


def short_title(title: str) -> str:
    return _TITLE_NOISE_RE.sub("", title).strip()


def fetch_announcements(today: date | None = None) -> list[dict]:
    """近 LOOKBACK_DAYS 天的公告，每則附上抽出的事項：
    [{"id", "title", "date", "url", "events": [...]}]。"""
    today = today or date.today()
    seen: dict[int, dict] = {}
    for page in range(1, LIST_PAGES + 1):
        for item in parse_list(get(LIST_URL.format(page=page)).text):
            seen.setdefault(item["id"], item)
    out = []
    for item in sorted(seen.values(), key=lambda x: x["date"], reverse=True):
        posted = date.fromisoformat(item["date"])
        if (today - posted).days > LOOKBACK_DAYS:
            continue
        url = DETAIL_URL.format(id=item["id"])
        lines = html_to_lines(get(url).text)
        out.append({**item, "url": url, "events": extract_events(lines, posted)})
    return out
