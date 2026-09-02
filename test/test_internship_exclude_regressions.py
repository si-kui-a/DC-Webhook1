"""
test_internship_exclude_regressions.py — config/internship_keywords.json
的exclude_keywords「健檢」回歸測試(2026-09-02新增)。

背景：2026-09-02新增基礎勞動力/居家照護清潔/駕駛相關三類排除詞時，初稿
用了「駕駛」「搬運」「生產線」「保全」「清潔」等產業別裸詞，健檢時發現
這些是產業別泛稱，會誤殺同樣命中子字串的科技職缺——這個專案本身涵蓋
半導體供應鏈題材(見scrapers/tsmc.py, semi_supply_chain.py)，「自動駕駛
系統工程師」「生產線自動化工程師」「搬運機器人工程師」「保全系統工程師」
都是真實存在的職缺/實習頭銜，不該被行業別排除規則誤殺。改用職稱後綴
(員/工/人員)或完整複合詞縮小比對範圍後修正。

這支測試直接讀真實production config(不mock路徑)，目的是把這次健檢的
判斷固化成可重跑的回歸測試——之後任何人再改exclude_keywords，跑這支
測試就能立刻抓到「新增的裸詞是否誤殺技術職缺」，不用每次靠人工重新
推演。用is_relevant_job_search()(台灣求職頻道的公開API)而非私有的
_is_excluded_industry()，且測試字串刻意不含「實習」等關鍵字，這樣才是
單純測到行業別排除這一層，不會被其他排除規則(關鍵字命中/科系/資歷)
干擾判斷。

執行:python -m unittest discover -s test -v
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from scrapers import internship_util as iu  # noqa: E402


class ExcludeKeywordTruePositiveTests(unittest.TestCase):
    """這些應該被行業別排除規則擋下(is_relevant_job_search回傳False)。"""

    def setUp(self):
        iu.invalidate_cache()

    def test_basic_labor_excluded(self):
        self.assertFalse(iu.is_relevant_job_search("倉儲作業員招募(免經驗可,日班)"))

    def test_home_care_excluded(self):
        self.assertFalse(iu.is_relevant_job_search("居家照顧服務員徵才,時薪面議"))

    def test_cleaning_excluded(self):
        self.assertFalse(iu.is_relevant_job_search("到府清潔人員招募,彈性排班"))

    def test_driving_excluded(self):
        self.assertFalse(iu.is_relevant_job_search("貨車駕駛徵才,甲式聯結車經驗尤佳"))


class ExcludeKeywordFalsePositiveGuardTests(unittest.TestCase):
    """這些是科技/工程職缺,字面上帶到排除詞的子字串,不該被誤殺
    (is_relevant_job_search應回傳True)。"""

    def setUp(self):
        iu.invalidate_cache()

    def test_autonomous_driving_engineer_not_excluded(self):
        self.assertTrue(iu.is_relevant_job_search("自動駕駛系統工程師,負責感測器融合演算法開發"))

    def test_production_line_automation_engineer_not_excluded(self):
        self.assertTrue(iu.is_relevant_job_search("生產線自動化工程師,規劃設備產能提升方案"))

    def test_material_handling_robot_engineer_not_excluded(self):
        self.assertTrue(iu.is_relevant_job_search("搬運機器人控制工程師,負責AMHS天車系統整合"))

    def test_security_system_engineer_not_excluded(self):
        self.assertTrue(iu.is_relevant_job_search("保全系統整合工程師,負責門禁與監控系統建置"))

    def test_cleanroom_process_engineer_not_excluded(self):
        self.assertTrue(iu.is_relevant_job_search("清潔製程設備工程師,負責晶圓廠製程設備維護"))


if __name__ == "__main__":
    unittest.main()
