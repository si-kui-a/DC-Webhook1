"""Generated source adapter; review fixture and schema before enabling."""
from __future__ import annotations

from scrapers import http_client

SOURCE_ID = "__SOURCE_ID__"
SOURCE_KIND = "__KIND__"
ENDPOINT = "__ENDPOINT__"


def fetch() -> list[dict]:
    response = http_client.get(ENDPOINT, headers={"User-Agent": "IntelPusher-SourceAdapter/1.0"})
    if SOURCE_KIND == "rss":
        raise NotImplementedError("parse RSS with a source fixture before enabling")
    if SOURCE_KIND == "json":
        raise NotImplementedError("map JSON fields with a source fixture before enabling")
    if SOURCE_KIND == "csv":
        raise NotImplementedError("map CSV fields with a source fixture before enabling")
    raise NotImplementedError("implement fixed HTML parser with a source fixture before enabling")