"""DAAD 結構化資格過濾（scholarship_daad.is_eligible）。用合成記錄，不連網。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrapers import scholarship_daad as d  # noqa: E402

RULE = {"status_id": 1, "origin_id": 103, "subject_groups": ["A", "B"]}


def rec(**kw):
    base = {"status": [1], "origin": [103, 5], "subjectGrps": ["B"]}
    base.update(kw)
    return base


class DaadEligibility(unittest.TestCase):
    def test_eligible(self):
        self.assertTrue(d.is_eligible(rec(), RULE))

    def test_graduate_only_rejected(self):
        self.assertFalse(d.is_eligible(rec(status=[2, 3, 4]), RULE))

    def test_other_origin_rejected(self):
        self.assertFalse(d.is_eligible(rec(origin=[5, 6]), RULE))

    def test_other_subject_rejected(self):
        self.assertFalse(d.is_eligible(rec(subjectGrps=["C", "F"]), RULE))

    def test_missing_fields_pass(self):
        self.assertTrue(d.is_eligible({}, RULE))

    def test_empty_rule_passes_all(self):
        self.assertTrue(d.is_eligible(rec(status=[4]), {}))

    def test_real_profile_has_rule(self):
        from scrapers import scholarship_util as su
        su.invalidate_cache()
        self.assertEqual(su.load_profile()["daad_eligibility"]["origin_id"], 103)


if __name__ == "__main__":
    unittest.main()
