"""jobs/thu_events.py 的測試。信件格式照「東海大學-校園活動報名系統」的報名成功信，
個人資料與活動名稱換成假的(repo 是公開的)。"""
import unittest
from datetime import datetime, timedelta
from unittest import mock

from jobs import precise_send as ps
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
        self.booked = []
        self.dispatch_ok = True

        def fake_dispatch(job, key, when):
            if self.dispatch_ok:
                self.booked.append(when.strftime("%m-%d %H:%M"))
            return self.dispatch_ok
        patcher = mock.patch.object(ps, "dispatch", fake_dispatch)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tick(self, now, sent, state):
        to_send, new_sent, _ = te.due(self.events, sent, state, at(now))
        return [e["id"] for e in to_send], new_sent

    def hourly(self, minute):
        """Hourly ticks at a fixed minute from 8 days before to after the start: (booked, sent now)."""
        sent, state, fired = {}, {}, []
        start = at("2026-10-08T12:30")
        now = start - timedelta(days=8)
        while now < start + timedelta(hours=2):
            when = now.replace(minute=minute)
            ids, sent = self.tick(when.isoformat()[:16], sent, state)
            if ids:
                fired.append(when.strftime("%m-%d %H:%M"))
            now += timedelta(hours=1)
        return self.booked, fired

    def test_every_point_booked_once_on_the_dot(self):
        # whatever minute the tick lands on, each reminder is booked for its exact time
        for minute in (13, 43, 58):
            self.booked = []
            booked, fired = self.hourly(minute)
            self.assertEqual(booked, ["10-01 12:30", "10-07 12:30", "10-08 09:30", "10-08 10:30", "10-08 11:30"], minute)
            self.assertEqual(fired, [], minute)

    def test_failed_booking_sends_early_not_late(self):
        self.dispatch_ok = False
        _, fired = self.hourly(13)
        self.assertEqual(fired, ["10-01 12:13", "10-07 12:13", "10-08 09:13", "10-08 10:13", "10-08 11:13"])

    def test_late_start_sends_only_latest_point(self):
        state = {}
        ids, sent = self.tick("2026-10-08T10:50", {}, state)  # 7d, 1d, 3h, 2h passed; 1h (11:30) booked
        self.assertEqual(ids, ["2026000001"])
        self.assertEqual(sent["2026000001"], ["1d", "1h", "2h", "3h", "7d"])
        self.assertEqual(self.booked, ["10-08 11:30"])
        self.assertEqual(self.tick("2026-10-08T11:05", sent, state)[0], [])

    def test_started_activity_is_dropped(self):
        ids, sent = self.tick("2026-10-08T12:31", {"2026000001": ["1h"]}, {})
        self.assertEqual((ids, sent), ([], {}))

    def test_render_at_send_time_and_removed_event(self):
        key = ps.opaque_key("2026000001", "3h")
        with mock.patch.object(te, "load_events", lambda: self.events):
            self.assertIn("還有 3 小時</b>", te.render(key, at("2026-10-08T09:30")))
            self.assertIsNone(te.render(key, at("2026-10-08T12:31")))  # already started
        with mock.patch.object(te, "load_events", lambda: []):
            self.assertIsNone(te.render(key, at("2026-10-08T09:30")))  # removed from the list


class Appointment(unittest.TestCase):
    """A hand-added appointment with its own reminder times (arrival-based), not a campus activity."""
    APPT = {"id": "dentist-x", "name": "測試診所看診", "kind": "看診", "start": "2026-10-21T16:00",
            "place": "某路 1 號", "checkin": "15:45 前到場", "bring": ["健保卡", "掛號費 200 元"],
            "status": "已預約", "url": "https://example.com/",
            "reminders": [{"label": "3d", "at": "2026-10-18T16:00"}, {"label": "arrive-2h", "at": "2026-10-21T13:45"}]}

    def test_own_points_message_and_validation(self):
        import importlib
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))  # not cwd-relative
        add = importlib.import_module("add_thu_event")
        appt = dict(self.APPT)
        self.assertEqual(add.check_event(appt), [])
        self.assertEqual(appt["end"], appt["start"])  # filled in
        self.assertEqual([(l, a.strftime("%m-%d %H:%M")) for l, a in te.points(appt)],
                         [("3d", "10-18 16:00"), ("arrive-2h", "10-21 13:45")])
        msg = te.format_event(appt, at("2026-10-21T13:45")).splitlines()
        self.assertEqual(msg[0], "⏰ <b>看診提醒｜還有 2 小時 15 分</b>")
        self.assertIn("🗓 2026-10-21（三） 16:00", msg)
        self.assertIn("🎒 攜帶：健保卡、掛號費 200 元", msg)
        self.assertEqual(msg[-1], "✅ 已預約｜https://example.com/")
        late = dict(self.APPT, reminders=[{"label": "x", "at": "2026-10-21T16:30"}])
        self.assertTrue(add.check_event(late))


class PreciseSend(unittest.TestCase):
    def setUp(self):
        self.events = [te.parse_registration(EMAIL)]

    def test_round_down_to_five_minutes(self):
        self.assertEqual(ps.round_down(at("2026-10-08T12:33")), at("2026-10-08T12:30"))
        self.assertEqual(ps.round_down(at("2026-10-08T12:30")), at("2026-10-08T12:30"))

    def test_request_window(self):
        now = at("2026-10-08T05:13")
        with mock.patch.object(ps, "dispatch", return_value=True):
            state = {}
            self.assertEqual(ps.request(state, "j", "k", at("2026-10-08T06:30"), now), "later")  # 77 min ahead
            self.assertEqual(ps.request(state, "j", "k", at("2026-10-08T06:00"), now), "scheduled")
            self.assertEqual(ps.request(state, "j", "k", at("2026-10-08T06:00"), now), "done")
            self.assertEqual(ps.request({}, "j", "k", at("2026-10-08T05:15"), now), "send_now")  # too close to book
        with mock.patch.object(ps, "dispatch", return_value=False):
            self.assertEqual(ps.request({}, "j", "k", at("2026-10-08T06:00"), now), "later")  # retry next tick
            self.assertEqual(ps.request({}, "j", "k", at("2026-10-08T05:30"), now), "send_now")

    def test_verify_confirms_success_and_resends_failure(self):
        now = at("2026-10-08T07:00")
        state = {"thu_lixue|2026-10-08": {"at": "2026-10-08T06:00:00+08:00", "status": "scheduled"},
                 "thu_events|abc": {"at": "2026-10-08T06:30:00+08:00", "status": "scheduled"},
                 "thu_events|new": {"at": "2026-10-08T06:55:00+08:00", "status": "scheduled"}}  # within GRACE
        runs = {"workflow_runs": [
            {"display_title": "send thu_lixue 2026-10-08", "status": "completed", "conclusion": "success"},
            {"display_title": "send thu_events abc", "status": "completed", "conclusion": "failure"}]}
        sent = []
        with mock.patch.object(ps, "load_state", return_value=state), \
                mock.patch.object(ps, "save_state"), \
                mock.patch.dict("os.environ", {"GH_TOKEN": "x", "GITHUB_REPOSITORY": "o/r"}), \
                mock.patch.object(ps, "_api", return_value=mock.Mock(ok=True, json=lambda: runs)), \
                mock.patch.object(ps, "renderer", return_value=lambda key, now: f"msg {key}"), \
                mock.patch.object(ps, "send", side_effect=lambda text: sent.append(text) or True):
            self.assertTrue(ps.run_verify(now))
        self.assertEqual(state["thu_lixue|2026-10-08"]["status"], "ok")
        self.assertEqual(state["thu_events|abc"]["status"], "resent")
        self.assertEqual(state["thu_events|new"]["status"], "scheduled")
        self.assertEqual(sent, ["（補送：預約送出沒有成功）\nmsg abc"])

    def test_send_at_sleeps_then_sends_rendered_text(self):
        slept, sent = [], []
        target = (datetime.now(ps.TAIWAN_TZ) + timedelta(minutes=10)).isoformat()
        with mock.patch.object(ps.time, "sleep", side_effect=slept.append), \
                mock.patch.object(ps, "renderer", return_value=lambda key, now: "hello"), \
                mock.patch.object(ps, "send", side_effect=lambda text: sent.append(text) or True):
            self.assertTrue(ps.run_send_at("thu_events", "k", target))
        self.assertTrue(590 <= slept[0] <= 600)
        self.assertEqual(sent, ["hello"])

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
