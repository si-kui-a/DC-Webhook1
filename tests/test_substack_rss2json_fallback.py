"""Offline tests: substack_generic falls back to rss2json only on a Cloudflare challenge."""
import pytest
import requests

from scrapers import substack_generic as s


class _Resp:
    def __init__(self, status=200, headers=None, content=b"", payload=None):
        self.status_code, self.headers, self.content, self._payload = status, headers or {}, content, payload

    def json(self):
        return self._payload


def _http_error(status, headers):
    return requests.HTTPError(f"{status}", response=_Resp(status, headers))


RSS2JSON_OK = {"status": "ok", "items": [{
    "link": "https://x.substack.com/p/a", "title": "A", "pubDate": "2026-09-24 01:55:28",
    "content": "<p>Hello</p> Read more",
}]}


def test_cloudflare_challenge_uses_rss2json(monkeypatch):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        if url == s.RSS2JSON_ENDPOINT:
            return _Resp(payload=RSS2JSON_OK)
        raise _http_error(403, {"cf-mitigated": "challenge"})

    monkeypatch.setattr(s.http_client, "get", fake_get)
    items = s.fetch_feed("sid", "name", "https://x.substack.com/feed")
    assert calls == ["https://x.substack.com/feed", s.RSS2JSON_ENDPOINT]
    assert items[0]["summary"] == "Hello"
    assert items[0]["published_at"] == "Thu, 24 Sep 2026 01:55:28 GMT"  # same format as the direct feed


def test_api_key_is_sent_only_when_configured(monkeypatch):
    seen = []

    def fake_get(url, **kwargs):
        if url == s.RSS2JSON_ENDPOINT:
            seen.append(kwargs["params"])
            return _Resp(payload=RSS2JSON_OK)
        raise _http_error(403, {"cf-mitigated": "challenge"})

    monkeypatch.setattr(s.http_client, "get", fake_get)
    monkeypatch.delenv("RSS2JSON_API_KEY", raising=False)
    s.fetch_feed("sid", "name", "https://x.substack.com/feed")
    monkeypatch.setenv("RSS2JSON_API_KEY", "k")
    s.fetch_feed("sid", "name", "https://x.substack.com/feed")
    assert "api_key" not in seen[0]
    assert seen[1]["api_key"] == "k" and seen[1]["count"] == "20"


def test_plain_403_is_not_rerouted(monkeypatch):
    def fake_get(url, **kwargs):
        raise _http_error(403, {})

    monkeypatch.setattr(s.http_client, "get", fake_get)
    with pytest.raises(requests.HTTPError):
        s.fetch_feed("sid", "name", "https://x.substack.com/feed")


def test_rss2json_error_status_raises(monkeypatch):
    def fake_get(url, **kwargs):
        if url == s.RSS2JSON_ENDPOINT:
            return _Resp(payload={"status": "error", "message": "limit"})
        raise _http_error(403, {"cf-mitigated": "challenge"})

    monkeypatch.setattr(s.http_client, "get", fake_get)
    with pytest.raises(RuntimeError, match="limit"):
        s.fetch_feed("sid", "name", "https://x.substack.com/feed")
