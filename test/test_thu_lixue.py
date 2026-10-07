"""scrapers/thu_lixue.py 與 jobs/thu_lixue.py 的測試。公告文字取自 src.thu.edu.tw 的真實公告
(2026-10-07 抓取：#185、#186、#187、#189)。"""
import unittest
from datetime import date

from jobs import thu_lixue as job
from scrapers import thu_lixue

# 186：期間跨三行
ANN_186 = ["【重要公告】115學年度第1學期勵學基金申請辦法", "★ 申請核心資訊速覽", "開放受理時間:",
           "115年8月24日 (一) 09:00", "至", "115年9月24日(四) 17:00", "必要條件:",
           "執行期間:", "統一審查後於官網公告,", "「審核通過」當天", "始得開始執行計畫。本學期執行至 115年12月5日止。"]
# 187：同一行的期間、沒有年份的「10/16 前」
ANN_187 = ["學年初", "申請收件:", "115年09月14日(一)至115年10月02日(五)17:00", "學年末", "成果收件:",
           "116年05月24日(一)至116年06月18日(五)17:00",
           "於申請期間內完成申請表並繳交予勵學基金承辦單位，經確認後，將另行開放共通職能專屬iLearn平台"
           "供申請人上傳申請表(請於10/16前上傳完成)，並於10/16(五)前至iLearn平台填寫共通職能前測問卷，方完成申請程序。"]
# 189：日期在「即日起至」之後，說明在上一行
ANN_189 = ["四、 施測與繳件時程", "【第 1 次/期初】截止日期:", "即日起至 115 年10 月 8 日(星期四)23:59 止",
           "【第 2 次/期末】截止日期:", "待公告,將於11月中公告", "4月累積12小時,僅核發10小時"]


def ann(id_, title, posted, events):
    return {"id": id_, "title": title, "date": posted, "url": f"https://src.thu.edu.tw/web/news/detail.php?cid=&id={id_}",
            "events": events}


class Extract(unittest.TestCase):
    def test_range_split_over_lines_and_execution_deadline(self):
        ev = thu_lixue.extract_events(ANN_186, date(2026, 8, 20))
        got = [(e["date"], e["kind"], e["label"]) for e in ev]
        self.assertIn((date(2026, 8, 24), "start", "開放受理時間"), got)
        self.assertIn((date(2026, 9, 24), "end", "開放受理時間"), got)
        self.assertIn((date(2026, 12, 5), "deadline", "本學期執行"), got)
        self.assertEqual(len(got), 3)

    def test_same_line_ranges_next_year_and_month_day_deadlines(self):
        ev = thu_lixue.extract_events(ANN_187, date(2026, 9, 7))
        got = {(e["date"], e["kind"]): e["label"] for e in ev}
        self.assertEqual(got[(date(2026, 9, 14), "start")], got[(date(2026, 10, 2), "end")])
        self.assertIn("申請收件", got[(date(2026, 9, 14), "start")])
        self.assertIn("成果收件", got[(date(2027, 6, 18), "end")])
        labels_1016 = [e["label"] for e in ev if e["date"] == date(2026, 10, 16)]
        self.assertEqual(labels_1016, ["將另行開放共通職能專屬iLearn平台供申請人上傳申請表", "至iLearn平台填寫共通職能前測問卷"])

    def test_label_from_previous_line_and_no_false_dates(self):
        ev = thu_lixue.extract_events(ANN_189, date(2026, 9, 10))
        self.assertEqual([(e["date"], e["kind"], e["label"]) for e in ev],
                         [(date(2026, 10, 8), "deadline", "【第 1 次/期初】截止日期")])

    def test_december_post_month_day_is_next_year(self):
        ev = thu_lixue.extract_events(["請於1/10前繳交學習紀錄"], date(2025, 12, 18))
        self.assertEqual(ev[0]["date"], date(2026, 1, 10))

    def test_list_page_cards(self):
        page = ("<h5 class='list-title'><a  href = 'https://src.thu.edu.tw/web/news/detail.php?cid=&id=189'>"
                "【重要公告】115學年度勵學基金-樂學培育申請時程與說明</a></h5>\n<p class='list-info '>\n"
                "<span>日期 : 2026-09-10</span>")
        self.assertEqual(thu_lixue.parse_list(page),
                         [{"id": 189, "title": "【重要公告】115學年度勵學基金-樂學培育申請時程與說明", "date": "2026-09-10"}])
        self.assertEqual(thu_lixue.short_title("【重要公告】115學年度勵學基金-樂學培育申請時程與說明"),
                         "115學年度勵學基金-樂學培育申請時程與說明")


class Reminders(unittest.TestCase):
    def setUp(self):
        self.anns = [ann(189, "【重要公告】115學年度勵學基金-樂學培育申請時程與說明", "2026-09-10",
                         thu_lixue.extract_events(ANN_189, date(2026, 9, 10))),
                     ann(187, "【重要公告】115學年度勵學基金-共通職能申請時程與說明", "2026-09-07",
                         thu_lixue.extract_events(ANN_187, date(2026, 9, 7)))]

    def test_sixty_thirty_seven_one_days_before_only(self):
        deadline = date(2026, 10, 8)
        for days in (60, 30, 7, 1):
            today = date.fromordinal(deadline.toordinal() - days)
            got = [(d, e["date"]) for d, e, _ in job.due_reminders(self.anns, today)]
            self.assertIn((days, deadline), got)
        for days in (0, 2, 14, 59):
            today = date.fromordinal(deadline.toordinal() - days)
            self.assertNotIn(deadline, [e["date"] for _, e, _ in job.due_reminders(self.anns, today)])

    def test_message_today_and_quiet_day(self):
        msg = job.build_message(self.anns, date(2026, 10, 7))
        self.assertIn("[明天] 2026-10-08（截止） 115學年度勵學基金-樂學培育申請時程與說明｜【第 1 次/期初】截止日期", msg)
        self.assertIn("id=189", msg)
        self.assertIsNone(job.build_message(self.anns, date(2026, 10, 5)))

    def test_new_announcement_pushed_the_day_after_with_or_without_dates(self):
        anns = self.anns + [ann(190, "《勵籽・勵程》季刊 <2026.9>", "2026-10-06", [])]
        msg = job.build_message(anns, date(2026, 10, 7))
        self.assertIn("🆕 新公告：《勵籽・勵程》季刊 &lt;2026.9&gt;", msg)  # HTML parse mode: escaped
        self.assertIn("沒有抽到之後的日期", msg)
        self.assertNotIn("新公告", job.build_message(anns, date(2026, 10, 8)) or "")


if __name__ == "__main__":
    unittest.main()
