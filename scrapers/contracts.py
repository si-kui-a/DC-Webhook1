"""Validation contract for normalized scraper items."""
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class ValidationReport:
    accepted: list[dict]
    rejected: int
    errors: tuple[str, ...]


def validate_items(items) -> ValidationReport:
    accepted = []
    errors = []
    if items is None:
        return ValidationReport([], 0, ())
    if not isinstance(items, list):
        return ValidationReport([], 1, ("result is not a list",))
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            errors.append(f"item[{index}] is not an object")
            continue
        title = str(item.get("title") or "").strip()
        url = str(item.get("url") or "").strip()
        parsed = urlparse(url)
        if not title:
            errors.append(f"item[{index}] missing title")
            continue
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            errors.append(f"item[{index}] invalid url")
            continue
        normalized = dict(item)
        normalized["title"] = title
        normalized["url"] = url
        accepted.append(normalized)
    return ValidationReport(accepted, len(items) - len(accepted), tuple(errors))