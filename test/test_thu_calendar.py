import unittest
from datetime import date
from unittest.mock import patch

from scrapers import thu_calendar


class UnescapeIcsTextTests(unittest.TestCase):
    def test_unescapes_comma_semicolon_backslash_newline(self):
        raw = r"A\, B\; C\\D\nE"
        self.assertEqual(thu_calendar._unescape_ics_text(raw), "A, B; C\\D\nE")


class ExamOrCourseSelectionTests(unittest.TestCase):
    def test_matches_exam_and_course_selection_keywords(self):
        for title in ["期中考試週", "學期考試週", "加退選課程開始（大一新生）",
                      "特殊退選課程申請截止", "第 2 學期課程預選",
                      "本學期申請停修課程開始", "學生上網確認本學期所選課程開始"]:
            self.assertTrue(thu_calendar.is_exam_or_course_selection(title), title)

    def test_does_not_match_general_events(self):
        for title in ["中秋節（放假一天）", "暑假結束", "雙主修、輔系、學分學程申請",
                      "全球校友返校日", "研究生繳交論文截止日"]:
            self.assertFalse(thu_calendar.is_exam_or_course_selection(title), title)


class ReminderTriggerDatesTests(unittest.TestCase):
    def test_general_event_reminds_one_week_before_and_day_of(self):
        d = date(2026, 9, 11)
        self.assertEqual(
            thu_calendar.get_reminder_trigger_dates(d, "註冊繳費截止日"),
            {date(2026, 9, 4), date(2026, 9, 11)},
        )

    def test_exam_event_reminds_month_two_weeks_monday_and_day_of(self):
        d = date(2026, 11, 3)  # Tuesday
        self.assertEqual(d.weekday(), 1)
        self.assertEqual(
            thu_calendar.get_reminder_trigger_dates(d, "期中考試週"),
            {date(2026, 10, 4), date(2026, 10, 20), date(2026, 11, 2), date(2026, 11, 3)},
        )

    def test_exam_event_on_a_monday_collapses_monday_and_day_of(self):
        d = date(2026, 11, 2)  # Monday
        self.assertEqual(d.weekday(), 0)
        triggers = thu_calendar.get_reminder_trigger_dates(d, "期中考試週")
        self.assertIn(d, triggers)
        self.assertEqual(len(triggers), 3)  # monday == day-of, so only 3 distinct dates


class IsRelevantToStudentsTests(unittest.TestCase):
    """用真實的config/thu_calendar_exclude.json(不mock)驗證2026-09-10
    使用者陸續確認的篩選條件仍然生效——這份設定檔本身就是這次改動的
    產出，值得對真實內容斷言，不用合成假設定檔繞過。"""

    def setUp(self):
        thu_calendar.invalidate_cache()

    def tearDown(self):
        thu_calendar.invalidate_cache()

    def test_excludes_staff_admin_and_facility_events(self):
        for title in ["本學期教務會議", "本學期第 1 次校務會議", "導師會議",
                       "教職員恢復正常辦公（上午 8:00～10:00 單位清潔日）",
                       "全校分區停電進行高壓設備保養開始"]:
            self.assertFalse(thu_calendar.is_relevant_to_students(title), title)

    def test_excludes_new_international_withdrawal_and_graduate_only(self):
        for title in ["境外新生註冊及繳費開始", "日間部大一新生大學入門課程",
                       "學生申請休退學退費三分之二截止日", "本學期申請休學截止日",
                       "研究生學位考試申請開始"]:
            self.assertFalse(thu_calendar.is_relevant_to_students(title), title)

    def test_keeps_general_student_and_celebration_events(self):
        for title in ["期中考試週", "中秋節（放假一天）", "第 1 學期上課開始",
                       "全球校友返校日", "東海牛奶節", "慶祝教師節暨表揚大會"]:
            self.assertTrue(thu_calendar.is_relevant_to_students(title), title)

    def test_force_include_overrides_graduate_only_exclusion(self):
        # 同時涵蓋「大二以上」一般生跟研究生，使用者2026-09-10確認要保留
        self.assertTrue(
            thu_calendar.is_relevant_to_students("加退選課程開始（大二以上及研究生）")
        )
        # 但純研究生事項仍然排除
        self.assertFalse(
            thu_calendar.is_relevant_to_students("研究生繳交論文截止日（114 學年度第 2 學期）")
        )


class GetCurrentSemesterCalendarTests(unittest.TestCase):
    """用合成事件清單(不打真實網路)驗證學期邊界判定+排除詞篩選+學期
    起訖標記事件本身不重複列出的邏輯。"""

    def _events(self):
        return [
            {"start": date(2025, 8, 1), "end": date(2025, 8, 1), "title": "114學年度第1學期開始"},
            {"start": date(2026, 2, 1), "end": date(2026, 2, 1), "title": "114學年度第2學期開始"},
            {"start": date(2026, 8, 1), "end": date(2026, 8, 1), "title": "115學年度第1學期開始"},
            {"start": date(2026, 8, 5), "end": date(2026, 8, 5), "title": "本學期教務會議"},  # 排除
            {"start": date(2026, 9, 14), "end": date(2026, 9, 14), "title": "第 1 學期上課開始"},
            {"start": date(2026, 11, 3), "end": date(2026, 11, 9), "title": "期中考試週"},
            {"start": date(2027, 2, 1), "end": date(2027, 2, 1), "title": "115學年度第2學期開始"},
            {"start": date(2027, 3, 1), "end": date(2027, 3, 1), "title": "不該出現的下學期事項"},
        ]

    def test_selects_current_semester_and_filters_and_drops_boundary_markers(self):
        thu_calendar.invalidate_cache()
        with patch.object(thu_calendar, "fetch_raw_events", return_value=self._events()):
            result = thu_calendar.get_current_semester_calendar(today=date(2026, 9, 10))

        self.assertEqual(result["semester"], "115學年度第1學期")
        self.assertEqual(result["range_start"], "2026-08-01")
        self.assertEqual(result["range_end"], "2027-02-01")

        titles = [e["title"] for e in result["events"]]
        self.assertNotIn("115學年度第1學期開始", titles)  # 起訖標記不重複列出
        self.assertNotIn("本學期教務會議", titles)  # 排除詞篩掉
        self.assertNotIn("不該出現的下學期事項", titles)  # 超出當期學期範圍
        self.assertEqual(titles, ["第 1 學期上課開始", "期中考試週"])

        exam_event = next(e for e in result["events"] if e["title"] == "期中考試週")
        self.assertEqual(exam_event["date"], "2026-11-03")
        self.assertEqual(exam_event["end_date"], "2026-11-09")


if __name__ == "__main__":
    unittest.main()
