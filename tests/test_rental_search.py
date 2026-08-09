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

def test_explicit_male_only_listing_is_excluded():
    body = json.dumps({"results": [
        {"title": "套房 限男性", "url": "https://rent.example/male", "rent": 3500, "location": "台中西屯"},
        {"title": "套房 男女皆可", "url": "https://rent.example/open", "rent": 3500, "location": "台中西屯"},
    ]})
    with patch("scrapers.rental_search.get", return_value=response(body, "application/json")):
        items = fetch(urls=["https://rent.example/feed.json"], areas=["台中"], min_rent=3000, max_rent=4500, room_types=["套房"])
    assert [item["url"] for item in items] == ["https://rent.example/open"]

def test_subsidy_after_rent_is_used_for_budget():
    body = json.dumps({"results": [
        {"title": "套房 補助後", "url": "https://rent.example/subsidy", "rent": 8500, "subsidy": 4500, "location": "台中西屯"},
        {"title": "套房 未知補助", "url": "https://rent.example/unknown", "rent": 8500, "location": "台中西屯"},
    ]})
    with patch("scrapers.rental_search.get", return_value=response(body, "application/json")):
        items = fetch(urls=["https://rent.example/feed.json"], areas=["台中"], min_rent=3000, max_rent=4500, room_types=["套房"])
    assert [item["url"] for item in items] == ["https://rent.example/subsidy"]
    assert items[0]["rent_after_subsidy"] == 4000

def test_cooking_allowed_is_preferred_but_not_required():
    body = json.dumps({"results": [
        {"title": "套房 不可開伙", "url": "https://rent.example/no-cook", "rent": 3500, "cooking": "不可開伙", "location": "台中西屯"},
        {"title": "套房 可開伙", "url": "https://rent.example/cook", "rent": 3500, "cooking": "可開伙", "location": "台中西屯"},
    ]})
    with patch("scrapers.rental_search.get", return_value=response(body, "application/json")):
        items = fetch(urls=["https://rent.example/feed.json"], areas=["台中"], min_rent=3000, max_rent=4500, room_types=["套房"])
    assert [item["url"] for item in items] == ["https://rent.example/cook", "https://rent.example/no-cook"]
