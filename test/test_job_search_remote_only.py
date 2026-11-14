"""求職頻道只收全遠端職缺（2026-11-14 使用者決定）的單元測試。"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scrapers import internship_util as iu  # noqa: E402

KEEP = [
    "社群小編(兼職,完全遠端)",
    "文案企劃 全遠端 時間彈性",
    "Content Writer (fully remote)",
    "線上家教 在家工作",
]
DROP = [
    "社群小編(兼職)",  # 沒寫遠端
    "文案企劃 部分遠端",  # 部分遠端不算
    "行銷專員 混合辦公,每週2天遠端",
    "Marketing Specialist (hybrid)",
    "倉儲作業員 全遠端",  # 全遠端但被行業排除
]


class RemoteOnlyJobSearch(unittest.TestCase):
    def setUp(self):
        iu.invalidate_cache()

    def test_kept(self):
        for t in KEEP:
            self.assertTrue(iu.is_relevant_remote_job_search(t), t)

    def test_dropped(self):
        for t in DROP:
            self.assertFalse(iu.is_relevant_remote_job_search(t), t)


if __name__ == "__main__":
    unittest.main()
