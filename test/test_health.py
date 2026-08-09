import json
import tempfile
import unittest
from pathlib import Path

from scrapers.health import record


class HealthTests(unittest.TestCase):
    def test_records_status_and_is_readable(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "source_health.json"
            record(path, "demo", "PARTIAL", item_count=4, rejected_count=1)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["sources"]["demo"]["status"], "PARTIAL")
            self.assertEqual(payload["sources"]["demo"]["item_count"], 4)

    def test_corrupt_file_is_replaced(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "source_health.json"
            path.write_text("not-json", encoding="utf-8")
            record(path, "demo", "FAILED", error="timeout")
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["sources"]["demo"]["error"], "timeout")


if __name__ == "__main__":
    unittest.main()