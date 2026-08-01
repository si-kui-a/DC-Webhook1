"""
scrapers/youtube_digest.py — YouTube頻道字幕摘要頻道。

只抓「已經有字幕」的影片(手動或自動字幕皆可)，零AI零GPU——用
youtube-transcript-api直接讀官方字幕文字，不下載影片/音訊。字幕本身
不存在的影片(頻道沒開字幕功能)直接跳過、記log，**不會退回Whisper
轉錄**——這是刻意的邊界，維持這支功能的零AI/零GPU原則；真的想看沒字幕
影片的內容，使用者可以自行用voice_transcript功能對著螢幕/喇叭口述，
不整合進這支自動化排程(見config/youtube_channels.json的清單原則)。

2026-08-01實測：原本選yt-dlp(--skip-download抓字幕檔)，但yt-dlp現在
需要額外裝JS runtime(deno)才能穩定運作，在這個環境的Bash工具裡PATH
解析不到，反而卡關。改用youtube-transcript-api直接查驗——同一批影片
用這個套件測試一次到位，且更輕量(純Python，無外部binary依賴)。

摘要用TextRank(跟summarizer_zh.py同一套textrank4zh引擎)，但影片逐字稿
比新聞RSS description長很多(實測34分鐘影片有11744字)，句數/長度上限
用專屬參數，不共用summarizer_zh.py的預設值(那是為短新聞調的)。
"""
import logging
import xml.etree.ElementTree as ET

import networkx as nx
import requests

if not hasattr(nx, "from_numpy_matrix"):
    nx.from_numpy_matrix = nx.from_numpy_array

from textrank4zh import TextRank4Sentence
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import CouldNotRetrieveTranscript

logger = logging.getLogger("scrapers.youtube_digest")

RSS_URL = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
TRANSCRIPT_LANGS = ["zh-TW", "zh-Hant", "zh", "zh-Hans", "en"]

# 影片逐字稿比新聞長很多，摘要句數/長度上限比summarizer_zh.py(2-3句/250字)
# 大幅放寬，抓重點但不會壓到只剩一兩句。
NUM_SENTENCES = 8
MAX_LENGTH = 800

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
}


def fetch_feed_entries(channel_id: str) -> list[dict]:
    resp = requests.get(RSS_URL.format(channel_id=channel_id), timeout=15)
    resp.raise_for_status()
    root = ET.fromstring(resp.text)
    entries = []
    for entry in root.findall("atom:entry", _NS):
        video_id_el = entry.find("yt:videoId", _NS)
        title_el = entry.find("atom:title", _NS)
        link_el = entry.find("atom:link", _NS)
        published_el = entry.find("atom:published", _NS)
        if video_id_el is None or title_el is None or link_el is None:
            continue
        entries.append({
            "video_id": video_id_el.text,
            "title": title_el.text,
            "url": link_el.attrib.get("href"),
            "published_at": published_el.text if published_el is not None else None,
        })
    return entries


def fetch_transcript_text(video_id: str) -> str | None:
    """抓不到字幕(頻道沒開字幕/影片本身沒有這個語言)一律回傳None，讓
    呼叫端跳過這則、不中斷整個頻道的批次處理。"""
    api = YouTubeTranscriptApi()
    try:
        transcript = api.fetch(video_id, languages=TRANSCRIPT_LANGS)
    except CouldNotRetrieveTranscript:
        return None
    except Exception as e:
        logger.warning("[youtube_digest] %s 字幕抓取例外：%s", video_id, e)
        return None
    return " ".join(seg.text for seg in transcript)


def summarize_transcript(text: str) -> str | None:
    """TextRank抽取式摘要，純統計演算法，不呼叫任何AI/雲端API。"""
    if not text or not text.strip():
        return None
    try:
        tr4s = TextRank4Sentence()
        tr4s.analyze(text=text, lower=True, source="all_filters")
        candidates = tr4s.get_key_sentences(num=NUM_SENTENCES)
    except Exception as e:
        logger.warning("[youtube_digest] 摘要產生失敗：%s", e)
        return None
    if not candidates:
        return None

    items_in_order = sorted(candidates, key=lambda item: item.index)

    parts = []
    length = 0
    for item in items_in_order:
        sentence = item.sentence
        added_length = len(sentence) + (1 if parts else 0)
        if length + added_length > MAX_LENGTH:
            break
        parts.append(sentence)
        length += added_length

    if not parts:
        first = items_in_order[0].sentence
        return first[:MAX_LENGTH] + "..." if len(first) > MAX_LENGTH else first

    summary = "。".join(parts)
    if len(items_in_order) > len(parts):
        summary += "..."
    return summary


if __name__ == "__main__":
    import json
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
    with open("config/youtube_channels.json", encoding="utf-8") as f:
        channels = json.load(f)["channels"]
    for ch in channels:
        entries = fetch_feed_entries(ch["channel_id"])
        with_transcript = sum(1 for e in entries if fetch_transcript_text(e["video_id"]))
        print(f"{ch['name']}: {len(entries)} 筆，有字幕 {with_transcript} 筆")
