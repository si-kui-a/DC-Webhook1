"""居住地（台中以外一律排除）與特殊身分（軍眷/榮眷等）排除規則的單元測試。

直接測排除函式，不經關鍵字計分，避免標題分數不足而「碰巧被擋」造成假通過。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scrapers import scholarship_util as su  # noqa: E402

PROFILE = {"residence_city": "台中市"}

EXCLUDE_RESIDENCE = [
    "限台北市設籍學生獎學金",
    "高雄市市民子女獎學金",
    "設籍新竹縣大專生助學金",
    "限彰化縣戶籍學生獎學金",
    "居住於花蓮縣學生獎學金",
    "桃園市青年獎學金",
]
KEEP_RESIDENCE = [
    "限台中市設籍學生獎學金",
    "臺中市市民獎學金",
    "東海大學外國學生獎學金",
    "臺北大學在學生獎學金",  # 校名含縣市字，但不是居住限定
    "清寒優秀學生獎學金",
]
EXCLUDE_IDENTITY = [
    "軍眷子女獎學金",
    "榮眷及榮民子女助學金",
    "軍公教遺族獎學金",
    "單親家庭學生獎學金",
]


class ResidenceIdentityRules(unittest.TestCase):
    def setUp(self):
        su.invalidate_cache()

    def test_residence_excluded(self):
        for t in EXCLUDE_RESIDENCE:
            self.assertTrue(su.is_excluded_by_residence(t, PROFILE), t)

    def test_residence_kept(self):
        for t in KEEP_RESIDENCE:
            self.assertFalse(su.is_excluded_by_residence(t, PROFILE), t)

    def test_identity_excluded(self):
        for t in EXCLUDE_IDENTITY:
            self.assertTrue(su.is_excluded_by_special_status(t, PROFILE), t)


if __name__ == "__main__":
    unittest.main()
