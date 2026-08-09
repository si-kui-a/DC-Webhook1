import json
import sys
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scrapers.rental_search import fetch


def response(body, content_type):
    result = Mock()
    result.text = body
    result.headers = {"Content-Type": content_type}
    try:
        result.json.return_value = json.loads(body)
    except json.JSONDecodeError:
        result.json.side_effect = json.JSONDecodeError("not json", body, 0)
    return result


def test_json_feed_is_normalized_filtered_and_deduplicated():
    body = json.dumps({"results": [
        {"title": "台中西屯兩房", "url": "https://rent.example/a", "rent": "18000", "size_ping": 18, "location": "台中市西屯區"},
        {"title": "台中西屯兩房（重複）", "url": "https://rent.example/a/", "rent": 19000, "size_ping": 20, "location": "台中市西屯區"},
        {"title": "台北套房", "url": "https://rent.example/b", "rent": 12000, "size_ping": 8, "location": "台北市"},
    ]})
    with patch("scrapers.rental_search.get", return_value=response(body, "application/json")):
        items = fetch(urls=["https://rent.example/feed.json"], areas=["台中"], max_rent=20000, min_size=10)
    assert len(items) == 1
    assert items[0]["rent_monthly"] == 18000


def test_rss_feed_is_supported():
    body = "<rss><channel><item><title>西屯雅房</title><link>https://rent.example/r1</link><description>近捷運</description></item></channel></rss>"
    with patch("scrapers.rental_search.get", return_value=response(body, "application/rss+xml")):
        items = fetch(urls=["https://rent.example/feed.xml"])
    assert items[0]["title"] == "西屯雅房"


def test_missing_configuration_is_explicit():
    with patch.dict("os.environ", {"RENTAL_FEED_URLS": ""}, clear=False):
        try:
            fetch()
        except NotImplementedError as exc:
            assert "RENTAL_FEED_URLS" in str(exc)
        else:
            raise AssertionError("missing rental feed must not silently succeed")
