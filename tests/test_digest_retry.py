"""A failed digest (AI or every push) must leave its items retryable and report failure."""
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import db
from jobs import digest

SOURCE = "test.source"
CHANNEL = "test_digest"


def _raw_items():
    now = datetime.now(timezone.utc).isoformat()
    return [{"title": f"t{i}", "url": f"https://example.test/{i}", "published_at": now} for i in range(2)]


def _normalize(raw):
    return {"source_id": SOURCE, "source_name": "Test", "url": raw["url"],
            "title": raw["title"], "published_at": raw["published_at"]}


GOOD_DIGEST = {"overview": "o", "points": [{"category": "c", "point": "p"}]}


class DigestRetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(db, "DB_PATH", Path(self.tmp.name) / "t.db")
        self.db_patch.start()
        db.init_db()
        db.upsert_source(SOURCE, "Test", "digest", "")
        # An older item so the first-run cap path is not taken.
        db.insert_item_if_new(SOURCE, "old", "s", "https://example.test/old", "2020-01-01")
        config = {"webhook_env": "TEST_WEBHOOK", "channel_title": "T", "source_ids": [SOURCE],
                  "angle": "a", "fetch_fn": _raw_items, "normalize_fn": _normalize,
                  "summarize_fn": lambda raw: "summary"}
        self.cfg_patch = patch.dict(digest.DIGEST_CHANNELS, {CHANNEL: config})
        self.cfg_patch.start()
        self.env_patch = patch.dict("os.environ", {"TEST_WEBHOOK": "https://example.test/hook"})
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()
        self.cfg_patch.stop()
        self.db_patch.stop()
        self.tmp.cleanup()

    def _run(self, ai_result, push_ok):
        with patch.object(digest.ai_insight, "build_channel_digest", return_value=ai_result), \
             patch.object(digest, "send_webhook", return_value=(push_ok, 200 if push_ok else 500, None)):
            return digest.run_digest_channel(CHANNEL)

    def test_ai_failure_then_retry_succeeds(self):
        self.assertIs(self._run(None, True), False)
        # Same items come back on the next run instead of being treated as already seen.
        with patch.object(digest.ai_insight, "build_channel_digest", return_value=GOOD_DIGEST) as ai, \
             patch.object(digest, "send_webhook", return_value=(True, 204, None)):
            self.assertIsNot(digest.run_digest_channel(CHANNEL), False)
        self.assertEqual(len(ai.call_args.args[1]), 2)

    def test_all_pushes_failed_is_retryable(self):
        self.assertIs(self._run(GOOD_DIGEST, False), False)
        self.assertEqual(db.count_items_for_source(SOURCE), 1)  # only the seeded old item remains

    def test_success_keeps_items(self):
        self.assertIsNot(self._run(GOOD_DIGEST, True), False)
        self.assertEqual(db.count_items_for_source(SOURCE), 3)


if __name__ == "__main__":
    unittest.main()
