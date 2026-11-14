"""求職頻道 = 原本非實習職缺 + 全遠端類別（2026-11-14 使用者更正：加類別，不是限縮）。"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scrapers import internship_util as iu  # noqa: E402

KEEP = [
    "社群小編(兼職)",  # 沒寫遠端，原本就收，不能被限縮
    "文案企劃 部分遠端",  # 部分遠端仍是一般職缺
    "社群小編(兼職,完全遠端)",
    "Content Writer (fully remote)",
    "線上家教 在家工作",
    "行銷助理實習 完全遠端 實習期間3個月",  # 實習字樣但全遠端，加入類別
]
DROP = [
    "倉儲作業員 全遠端",  # 行業排除仍套用
    "倉儲作業員招募(免經驗可,日班)",
]


class RemoteAdditiveJobSearch(unittest.TestCase):
    def setUp(self):
        iu.invalidate_cache()

    def test_kept(self):
        for t in KEEP:
            self.assertTrue(iu.is_relevant_remote_job_search(t), t)

    def test_dropped(self):
        for t in DROP:
            self.assertFalse(iu.is_relevant_remote_job_search(t), t)

    def test_hybrid_not_fully_remote(self):
        self.assertFalse(iu.is_fully_remote("行銷專員 混合辦公,每週2天遠端"))
        self.assertTrue(iu.is_fully_remote("文案企劃 全遠端"))


if __name__ == "__main__":
    unittest.main()
