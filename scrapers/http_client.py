"""Small, dependency-light HTTP client for scraper sources."""
from __future__ import annotations

import random
import time
from email.utils import parsedate_to_datetime
from datetime import datetime, timezone
from typing import Callable

import requests

RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504, 520, 521, 522, 524})


def _retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            target = parsedate_to_datetime(value)
            if target.tzinfo is None:
                target = target.replace(tzinfo=timezone.utc)
            return max(0.0, (target - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def request(
    method: str,
    url: str,
    *,
    session: requests.Session | None = None,
    attempts: int = 3,
    timeout: float = 15.0,
    headers: dict[str, str] | None = None,
    params: dict | None = None,
    data=None,
    json=None,
    sleep: Callable[[float], None] = time.sleep,
    random_fn: Callable[[], float] = random.random,
) -> requests.Response:
    """Request with bounded retries for transient failures only."""
    if attempts < 1:
        raise ValueError("attempts must be >= 1")
    client = session or requests.Session()
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = client.request(
                method, url, headers=headers, params=params, data=data, json=json, timeout=timeout
            )
        except requests.RequestException as exc:
            last_error = exc
            if attempt == attempts:
                raise
            delay = min(30.0, 0.5 * (2 ** (attempt - 1))) + random_fn() * 0.25
            sleep(delay)
            continue

        if response.status_code not in RETRYABLE_STATUSES:
            response.raise_for_status()
            return response
        if attempt == attempts:
            response.raise_for_status()
            return response

        last_error = requests.HTTPError(f"transient HTTP {response.status_code}", response=response)
        delay = _retry_after(response.headers.get("Retry-After"))
        if delay is None:
            delay = min(30.0, 0.5 * (2 ** (attempt - 1))) + random_fn() * 0.25
        sleep(delay)
    raise requests.RequestException(f"request failed after {attempts} attempts: {url}") from last_error


def get(url: str, **kwargs) -> requests.Response:
    return request("GET", url, **kwargs)