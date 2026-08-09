import unittest
from scrapers.contracts import validate_items


class ContractTests(unittest.TestCase):
    def test_accepts_normalized_item(self):
        report = validate_items([{"title": "x", "url": "https://example.test/a"}])
        self.assertEqual(len(report.accepted), 1)
        self.assertEqual(report.rejected, 0)

    def test_rejects_missing_title_and_bad_url(self):
        report = validate_items([
            {"title": "", "url": "https://example.test"},
            {"title": "x", "url": "not-a-url"},
        ])
        self.assertEqual(report.accepted, [])
        self.assertEqual(report.rejected, 2)

    def test_none_is_valid_empty_result(self):
        report = validate_items(None)
        self.assertEqual(report.accepted, [])
        self.assertEqual(report.rejected, 0)


if __name__ == "__main__":
    unittest.main()