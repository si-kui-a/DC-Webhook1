"""Deterministic link health checks; never guesses replacement URLs."""
from __future__ import annotations

from urllib.parse import urlparse

from scrapers import http_client


def check_url(url: str) -> dict:
    try:
        response = http_client.get(
            url,
            timeout=10,
            attempts=2,
            headers={"User-Agent": "IntelPusher-LinkHealth/1.0"},
        )
        status = "REDIRECTED" if response.history else "OK"
        resolved = response.url
        response.close()
        return {"status": status, "resolved_url": resolved, "http_status": response.status_code}
    except Exception as exc:
        response = getattr(exc, "response", None)
        code = getattr(response, "status_code", None)
        status = "BLOCKED_EXTERNAL" if code in {401, 403, 406, 429} else ("NOT_FOUND" if code == 404 else "TEMPORARY_FAILURE")
        return {"status": status, "resolved_url": None, "http_status": code, "error": str(exc)[:500]}


def is_allowed_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)