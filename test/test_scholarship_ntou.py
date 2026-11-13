"""海洋大學校外獎學金公告：列表與詳細頁解析。用合成 HTML，不連網。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scrapers import scholarship_ntou as n  # noqa: E402

LIST = (
    '<a href="/p/406-1023-1,r1039.php?Lang=zh-tw">【115-1學期-民間】某基金會獎學金(12月15日止)</a>'
    '<a href="/p/406-1023-1,r1039.php?Lang=zh-tw">同一筆重複</a>'
    '<a href="https://stu.ntou.edu.tw/p/406-1023-2,r1039.php?Lang=zh-tw">政府獎學金</a>'
    '<a href="/p/403-1023-1042.php">不是公告</a>'
    '<a href="/p/406-1023-3,r1039.php"> </a>'
)


def row(label, body):
    return f"<tr><td><p>{label}</p></td><td><p>{body}</p></td></tr>"


class Parse(unittest.TestCase):
    def test_list_keeps_announcements_once_with_absolute_urls(self):
        items = n.parse_list(LIST)
        self.assertEqual([i["title"] for i in items],
                         ["【115-1學期-民間】某基金會獎學金(12月15日止)", "政府獎學金"])
        self.assertTrue(all(i["url"].startswith("https://stu.ntou.edu.tw/p/406-1023-") for i in items))

    def test_detail_reads_label_to_content(self):
        html = "<table>" + row("申請日期", "即日起") + row("申請資格", "1.操行80分<br>2.公費生不得申請") + "</table>"
        f = n.parse_detail(html)
        self.assertEqual(f["申請日期"], "即日起")
        self.assertIn("公費生不得申請", f["申請資格"])

    def test_long_left_cell_is_content_not_label(self):
        html = "<table>" + row("這是一段很長很長的內文不是欄位標籤", "x") + "</table>"
        self.assertEqual(n.parse_detail(html), {})

    def test_page_without_table_is_unknown(self):
        self.assertEqual(n.parse_detail("<p>nothing</p>"), {})


class Registered(unittest.TestCase):
    def test_job_and_reachability_probe_know_the_source(self):
        from jobs import scholarship
        from scripts import probe_cloud_reachability as probe
        self.assertEqual(scholarship.SCHOLARSHIP_REGISTRY["scholarship_ntou"][2], n.SOURCE_ID)
        self.assertIn(("scrapers.scholarship_ntou", "fetch"), probe.TARGETS)


if __name__ == "__main__":
    unittest.main()
