import unittest
from unittest.mock import Mock

import requests

from scrapers.http_client import get


class HttpClientTests(unittest.TestCase):
    def test_retries_transient_status_then_succeeds(self):
        session = Mock()
        failed = Mock(status_code=503, headers={})
        success = Mock(status_code=200, headers={})
        success.raise_for_status.return_value = None
        session.request.side_effect = [failed, success]
        sleeps = []
        response = get("https://example.test", session=session, sleep=sleeps.append, random_fn=lambda: 0)
        self.assertIs(response, success)
        self.assertEqual(session.request.call_count, 2)
        self.assertEqual(sleeps, [0.5])

    def test_honors_retry_after(self):
        session = Mock()
        failed = Mock(status_code=429, headers={"Retry-After": "3"})
        success = Mock(status_code=200, headers={})
        success.raise_for_status.return_value = None
        session.request.side_effect = [failed, success]
        sleeps = []
        get("https://example.test", session=session, sleep=sleeps.append, random_fn=lambda: 0)
        self.assertEqual(sleeps, [3.0])

    def test_does_not_retry_client_errors(self):
        session = Mock()
        response = Mock(status_code=404, headers={})
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
        session.request.return_value = response
        with self.assertRaises(requests.HTTPError):
            get("https://example.test", session=session, sleep=Mock())
        session.request.assert_called_once()


if __name__ == "__main__":
    unittest.main()