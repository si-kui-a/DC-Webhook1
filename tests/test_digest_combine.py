"""Merged digest channels dispatch to each part's own functions and survive one part failing."""
import unittest
from unittest.mock import patch

from jobs import digest


def _part(items=None, error=None, tag=""):
    def fetch():
        if error:
            raise error
        return list(items or [])
    return {
        "fetch_fn": fetch,
        "normalize_fn": lambda raw: {**raw, "normalized_by": tag},
        "summarize_fn": lambda raw: f"summary-{tag}",
        "source_ids": [f"src.{tag}"],
    }


class CombineTests(unittest.TestCase):
    def _channel(self, parts):
        with patch.dict(digest._PARTS, parts):
            return digest._combine("T", "ENV", "angle", list(parts))

    def test_dispatches_to_each_part(self):
        parts = {"a": _part([{"title": "x"}], tag="a"), "b": _part([{"title": "y"}], tag="b")}
        channel = self._channel(parts)
        with patch.dict(digest._PARTS, parts):
            raws = channel["fetch_fn"]()
            self.assertEqual([r["_part"] for r in raws], ["a", "b"])
            self.assertEqual([channel["normalize_fn"](r)["normalized_by"] for r in raws], ["a", "b"])
            self.assertEqual([channel["summarize_fn"](r) for r in raws], ["summary-a", "summary-b"])
        self.assertEqual(channel["source_ids"], ["src.a", "src.b"])

    def test_one_part_failing_keeps_the_others(self):
        parts = {"a": _part(error=RuntimeError("down"), tag="a"), "b": _part([{"title": "y"}], tag="b")}
        channel = self._channel(parts)
        with patch.dict(digest._PARTS, parts):
            raws = channel["fetch_fn"]()
        self.assertEqual([r["_part"] for r in raws], ["b"])

    def test_all_parts_failing_raises(self):
        parts = {"a": _part(error=RuntimeError("down"), tag="a")}
        channel = self._channel(parts)
        with patch.dict(digest._PARTS, parts), self.assertRaises(RuntimeError):
            channel["fetch_fn"]()

    def test_channel_set(self):
        self.assertEqual(set(digest.DIGEST_CHANNELS), {"crypto_digest", "us_macro_digest", "tw_semi_digest"})


if __name__ == "__main__":
    unittest.main()
