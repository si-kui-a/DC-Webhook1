import unittest
from unittest.mock import Mock, patch

from scrapers.link_health import check_url, is_allowed_url


class LinkHealthTests(unittest.TestCase):
    def test_classifies_redirect(self):
        response = Mock(status_code=200, url="https://example.test/new", history=[object()])
        response.close.return_value = None
        with patch("scrapers.link_health.http_client.get", return_value=response):
            result = check_url("https://example.test/old")
        self.assertEqual(result["status"], "REDIRECTED")
        self.assertEqual(result["resolved_url"], "https://example.test/new")

    def test_rejects_invalid_url(self):
        self.assertFalse(is_allowed_url("javascript:alert(1)"))
        self.assertTrue(is_allowed_url("https://example.test/item"))


if __name__ == "__main__":
    unittest.main()