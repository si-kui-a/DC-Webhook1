"""jobs/thu_events.py 的測試。信件格式照「東海大學-校園活動報名系統」的報名成功信，
個人資料與活動名稱換成假的(repo 是公開的)。"""
import unittest
from datetime import datetime, timedelta

from jobs import thu_events as te

EMAIL = """您好：

您已於『東海大學-校園活動報名系統』完成活動報名。
以下為您的報名資料Registration Information：
活動名稱：    【認列博雅認證】測試工作坊
姓名：    王小明
連絡電話：    0900000000
行動電話：    0900000000
E-Mail：    s00000000@thu.edu.tw

備註：
《 注意事項 》

此項課程為學生講師培訓工作坊，凡有報名學生講師之學生需參加此場活動。
為確保他人權益，請務必確認能完成課程規定再行報名作業，否則請勿佔用學習資源及名額。
請報名夥伴於12:20至12:30準時至活動場地報到。
活動提供餐食。
如有任何疑問，請洽學務處課外活動承辦人
認列博雅認證


以下為您所報名的場次：
活動名稱：    【認列博雅認證】測試工作坊
活動地點：    北側會議室
場次名稱：    測試工作坊
活動日期：    2026-10-08～2026-10-08
活動時間：    12:30～15:30
報到時間：    12:00～12:30

您可以隨時登入
“活動報名系統http://event.ithu.tw/2026000001”，查詢或修改相關資訊！
"""


def at(s):
    return datetime.fromisoformat(s).replace(tzinfo=te.TAIWAN_TZ)


class Parse(unittest.TestCase):
    def test_keeps_activity_details_and_drops_personal_data(self):
        e = te.parse_registration(EMAIL)
        self.assertEqual(e, {
            "id": "2026000001", "name": "【認列博雅認證】測試工作坊", "place": "北側會議室",
            "start": "2026-10-08T12:30", "end": "2026-10-08T15:30", "checkin": "12:00–12:30",
            "checkin_note": "12:20–12:30", "benefits": ["認列博雅認證", "活動提供餐食"],
            "status": "已報名", "url": "https://event.ithu.tw/2026000001"})
        dumped = str(e)
        for personal in ("王小明", "0900000000", "s00000000"):
            self.assertNotIn(personal, dumped)

    def test_incomplete_email_is_refused(self):
        with self.assertRaises(ValueError):
            te.parse_registration("活動名稱：x\n")


class Due(unittest.TestCase):
    def setUp(self):
        self.events = [te.parse_registration(EMAIL)]

    def tick(self, now, sent):
        to_send, new_sent = te.due(self.events, sent, at(now))
        return [e["id"] for e in to_send], new_sent

    def test_each_reminder_point_once(self):
        sent, fired = {}, []
        start = at("2026-10-08T12:30")
        now = start - timedelta(days=8)
        while now < start + timedelta(hours=2):  # hourly ticks at :43
            ids, sent = self.tick((now.replace(minute=43)).isoformat()[:16], sent)
            if ids:
                fired.append(now.replace(minute=43))
            now += timedelta(hours=1)
        self.assertEqual([f.strftime("%m-%d %H:%M") for f in fired],
                         ["10-01 12:43", "10-07 12:43", "10-08 09:43", "10-08 10:43", "10-08 11:43"])

    def test_late_start_sends_only_latest_point(self):
        ids, sent = self.tick("2026-10-08T11:00", {})  # 7d, 1d, 3h, 2h all passed, nothing sent yet
        self.assertEqual(ids, ["2026000001"])
        self.assertEqual(sent["2026000001"], ["1d", "2h", "3h", "7d"])
        self.assertEqual(self.tick("2026-10-08T11:20", sent)[0], [])
        self.assertEqual(self.tick("2026-10-08T11:40", sent)[0], ["2026000001"])  # 1h

    def test_started_activity_is_dropped(self):
        ids, sent = self.tick("2026-10-08T12:31", {"2026000001": ["1h"]})
        self.assertEqual((ids, sent), ([], {}))

    def test_message_shows_details_not_personal_data(self):
        msg = te.format_event(self.events[0], at("2026-10-08T09:43"))
        self.assertEqual(msg.splitlines(), [
            "⏰ <b>活動提醒｜還有 2 小時 47 分</b>",
            "<b>【認列博雅認證】測試工作坊</b>",
            "🗓 2026-10-08（四） 12:30–15:30",
            "📍 北側會議室",
            "📝 報到 12:00–12:30（請於 12:20–12:30 準時報到）",
            "🎁 認列博雅認證；活動提供餐食",
            "✅ 已報名｜https://event.ithu.tw/2026000001"])
        self.assertIn("還有 7 天", te.format_event(self.events[0], at("2026-10-01T12:43")))


if __name__ == "__main__":
    unittest.main()
