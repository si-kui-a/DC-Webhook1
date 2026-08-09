"""Deterministic rental-listing collector.

Sources are supplied through ``RENTAL_FEED_URLS`` (comma-separated).  The
collector intentionally does not guess URLs or use an AI parser: each feed
must be explicitly configured and every result is normalized and filtered
before it reaches the common item contract.
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from .http_client import get

SOURCE_ID = "rental.search"
SOURCE_NAME = "租屋搜尋（明確設定來源）"
SOURCE_KIND = "rental"


def _csv_env(name: str) -> list[str]:
    return [value.strip() for value in os.getenv(name, "").split(",") if value.strip()]


def _number(value) -> float | None:
    if value is None:
        return None
    match = re.search(r"[0-9]+(?:\.[0-9]+)?", str(value).replace(",", ""))
    return float(match.group()) if match else None


def _published(value) -> str | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = parsedate_to_datetime(text)
        return parsed.isoformat()
    except (TypeError, ValueError, OverflowError):
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
        except ValueError:
            return text


def _first(mapping: dict, *keys):
    for key in keys:
        value = mapping.get(key)
        if value not in (None, ""):
            return value
    return None


def _json_rows(payload) -> list[dict]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("items", "results", "listings", "data"):
        rows = _json_rows(payload.get(key))
        if rows:
            return rows
    return [payload] if _first(payload, "title", "name", "url", "link") else []


def _normalize(row: dict, base_url: str, source_url: str) -> dict | None:
    title = str(_first(row, "title", "name", "subject") or "").strip()
    href = str(_first(row, "url", "link", "href") or "").strip()
    if not title or not href:
        return None
    url = urljoin(base_url, href)
    if not url.startswith(("http://", "https://")):
        return None
    summary = str(_first(row, "summary", "description", "content", "text") or "").strip()
    location = str(_first(row, "location", "address", "area", "district") or "").strip()
    rent = _number(_first(row, "rent", "price", "monthly_rent", "月租"))
    size = _number(_first(row, "size", "size_ping", "area_ping", "ping", "坪數"))
    item = {
        "title": title,
        "url": url,
        "summary": summary or None,
        "published_at": _published(_first(row, "published_at", "published", "pubDate", "date")),
        "source_url": source_url,
        "location": location or None,
        "rent_monthly": rent,
        "size_ping": size,
        "room_type": str(_first(row, "room_type", "layout", "rooms", "格局") or "").strip() or None,
    }
    return item


def _parse_xml(text: str, source_url: str) -> list[dict]:
    root = ET.fromstring(text)
    rows = []
    for entry in root.findall(".//item") + root.findall(".//{*}entry"):
        row = {child.tag.rsplit("}", 1)[-1]: (child.text or "").strip() for child in entry}
        item = _normalize(row, source_url, source_url)
        if item:
            rows.append(item)
    return rows


def _parse_html(text: str, source_url: str) -> list[dict]:
    soup = BeautifulSoup(text, "html.parser")
    rows = []
    for link in soup.select("a[href]"):
        title = link.get_text(" ", strip=True)
        if len(title) < 4:
            continue
        item = _normalize({"title": title, "url": link.get("href"), "description": title}, source_url, source_url)
        if item:
            rows.append(item)
    return rows



def _parse_csv(text: str, source_url: str) -> list[dict]:
    """Parse government/open CSV exports with Chinese or English headers."""
    rows = []
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    for row in reader:
        normalized = {
            "title": _first(row, "交易標的", "建物型態", "租賃標的", "title") or "租賃實價資料",
            "url": _first(row, "url", "網址", "link") or source_url,
            "location": " ".join(str(_first(row, key) or "") for key in ("縣市", "鄉鎮市區", "土地位置建物門牌", "location")),
            "rent": _first(row, "租金總額(元)", "租賃總價(元)", "每月租金", "rent", "price"),
            "size": _first(row, "建物租賃總面積平方公尺", "建物租賃面積(平方公尺)", "建物面積", "size_ping"),
            "published_at": _first(row, "交易年月日", "租賃日期", "date"),
            "description": "；".join(f"{key}:{value}" for key, value in row.items() if value and key not in {"url", "link"}),
        }
        item = _normalize(normalized, source_url, source_url)
        if item:
            rows.append(item)
    return rows


def _parse_response(response, source_url: str) -> list[dict]:
    content_type = response.headers.get("Content-Type", "").lower()
    raw_content = getattr(response, "content", b"")
    payload = raw_content if isinstance(raw_content, (bytes, bytearray)) else response.text.encode("utf-8", errors="ignore")
    if "zip" in content_type or payload[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            names = [name for name in archive.namelist() if name.lower().endswith((".csv", ".txt"))]
            return _parse_csv(archive.read(names[0]).decode("utf-8-sig", errors="replace"), source_url) if names else []
    if "csv" in content_type or source_url.lower().endswith((".csv", ".txt")):
        return _parse_csv(response.text, source_url)
    if "json" in content_type or source_url.lower().endswith(".json"):
        return [_normalize(row, source_url, source_url) for row in _json_rows(response.json())]
    if "xml" in content_type or "rss" in content_type or "atom" in content_type or response.text.lstrip().startswith("<rss"):
        return _parse_xml(response.text, source_url)
    return _parse_html(response.text, source_url)
def _matches(item: dict, *, areas: list[str], keywords: list[str], excludes: list[str],
             max_rent: float | None, min_rent: float | None, room_types: list[str], min_size: float | None) -> bool:
    haystack = " ".join(str(item.get(key) or "") for key in ("title", "summary", "location", "room_type")).lower()
    if areas and not any(area.lower() in haystack for area in areas):
        return False
    if keywords and not all(word.lower() in haystack for word in keywords):
        return False
    if excludes and any(word.lower() in haystack for word in excludes):
        return False
    rent = item.get("rent_monthly")
    if min_rent is not None and (rent is None or rent < min_rent):
        return False
    if max_rent is not None and (rent is None or rent > max_rent):
        return False
    if room_types and not any(room.lower() in haystack for room in room_types):
        return False
    size = item.get("size_ping")
    if min_size is not None and (size is None or size < min_size):
        return False
    return True


def fetch(*, urls: list[str] | None = None, session=None, areas: list[str] | None = None,
          keywords: list[str] | None = None, excludes: list[str] | None = None,
          max_rent: float | None = None, min_rent: float | None = None,
          room_types: list[str] | None = None, min_size: float | None = None) -> list[dict]:
    """Fetch explicitly configured feeds and return normalized, filtered items."""
    urls = urls or _csv_env("RENTAL_FEED_URLS")
    if not urls:
        raise NotImplementedError("RENTAL_FEED_URLS is not configured")
    areas = _csv_env("RENTAL_AREAS") if areas is None else areas
    keywords = _csv_env("RENTAL_KEYWORDS") if keywords is None else keywords
    excludes = _csv_env("RENTAL_EXCLUDE") if excludes is None else excludes
    max_rent = _number(os.getenv("RENTAL_MAX_MONTHLY")) if max_rent is None else max_rent
    min_rent = _number(os.getenv("RENTAL_MIN_MONTHLY")) if min_rent is None else min_rent
    room_types = _csv_env("RENTAL_ROOM_TYPES") if room_types is None else room_types
    min_size = _number(os.getenv("RENTAL_MIN_PING")) if min_size is None else min_size
    output = []
    seen = set()
    for source_url in urls:
        response = get(source_url, session=session, headers={"Accept": "application/rss+xml, application/json, text/html"})
        try:
            rows = _parse_response(response, source_url)
        except (ValueError, ET.ParseError, OSError, zipfile.BadZipFile):
            rows = []
        for item in rows:
            if item and _matches(item, areas=areas, keywords=keywords, excludes=excludes, max_rent=max_rent, min_rent=min_rent, room_types=room_types, min_size=min_size):
                key = item["url"].split("#", 1)[0].rstrip("/").lower()
                if key not in seen:
                    seen.add(key)
                    output.append(item)
    return output

