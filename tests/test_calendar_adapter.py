"""Calendar Adapter 单元测试（纯转换，不碰数据库）。"""

import unittest
from datetime import date

from taiplan import calendar_adapter as ca
from taiplan import calendar_settings
from taiplan.models import TaskOccurrence


def _occ(**kw):
    base = dict(task_id=1, title="高数", date="2026-10-05", time="14:00")
    base.update(kw)
    return TaskOccurrence(**base)


class EventIdTest(unittest.TestCase):

    def test_task_event_id(self):
        self.assertEqual(ca.make_event_id(_occ(is_recurring=False)), "task:1")

    def test_occurrence_event_id(self):
        item = _occ(task_id=20, is_recurring=True, occurrence_key="2026-10-05T09:30")
        self.assertEqual(ca.make_event_id(item), "occurrence:20:2026-10-05T09:30")

    def test_parse_task_id(self):
        self.assertEqual(ca.parse_event_id("task:17"),
                         {"kind": "task", "task_id": 17, "occurrence_key": None})

    def test_parse_occurrence_id(self):
        self.assertEqual(
            ca.parse_event_id("occurrence:20:2026-10-05T09:30"),
            {"kind": "occurrence", "task_id": 20, "occurrence_key": "2026-10-05T09:30"})

    def test_parse_invalid(self):
        self.assertIsNone(ca.parse_event_id(""))
        self.assertIsNone(ca.parse_event_id("bogus"))


class EventTimeTest(unittest.TestCase):

    def test_60_minutes(self):
        ev = ca.item_to_event(_occ(duration_minutes=60))
        self.assertEqual(ev["start"], "2026-10-05T14:00")
        self.assertEqual(ev["end"], "2026-10-05T15:00")
        self.assertFalse(ev["allDay"])

    def test_90_minutes(self):
        ev = ca.item_to_event(_occ(time="16:00", duration_minutes=90))
        self.assertEqual(ev["start"], "2026-10-05T16:00")
        self.assertEqual(ev["end"], "2026-10-05T17:30")

    def test_cross_midnight(self):
        ev = ca.item_to_event(_occ(time="23:00", duration_minutes=120))
        self.assertEqual(ev["start"], "2026-10-05T23:00")
        self.assertEqual(ev["end"], "2026-10-06T01:00")

    def test_legacy_task_defaults_to_60(self):
        """旧任务 duration_minutes=None → 默认 60 分钟渲染。"""
        ev = ca.item_to_event(_occ(duration_minutes=None))
        self.assertEqual(ev["start"], "2026-10-05T14:00")
        self.assertEqual(ev["end"], "2026-10-05T15:00")

    def test_all_day_no_fake_time(self):
        ev = ca.item_to_event(_occ(time=None, duration_minutes=None))
        self.assertTrue(ev["allDay"])
        self.assertEqual(ev["start"], "2026-10-05")
        self.assertNotIn("end", ev)
        self.assertNotIn("T", ev["start"])

    def test_completed_visual(self):
        ev = ca.item_to_event(_occ(is_completed=True))
        self.assertIn("✅", ev["title"])

    def test_recurring_visual(self):
        ev = ca.item_to_event(_occ(is_recurring=True, occurrence_key="2026-10-05T14:00"))
        self.assertIn("🔁", ev["title"])

    def test_urgent_color(self):
        ev = ca.item_to_event(_occ(priority="urgent"))
        self.assertEqual(ev["backgroundColor"], "#d1242f")

    def test_conflict_flag(self):
        ev = ca.item_to_event(_occ(), conflict=True)
        self.assertTrue(ev["extendedProps"]["hasConflict"])


class EventChangeTest(unittest.TestCase):

    def test_normal_drag_changes_date_time(self):
        payload = {"id": "task:5", "start": "2026-10-06T16:00:00",
                   "end": "2026-10-06T17:00:00", "allDay": False}
        change = ca.compute_event_change(payload)
        self.assertEqual(change["kind"], "task")
        self.assertEqual(change["task_id"], 5)
        self.assertEqual(change["date"], "2026-10-06")
        self.assertEqual(change["time"], "16:00")
        self.assertEqual(change["duration_minutes"], 60)

    def test_resize_changes_duration(self):
        payload = {"id": "task:5", "start": "2026-10-05T14:30:00",
                   "end": "2026-10-05T17:30:00", "allDay": False}
        change = ca.compute_event_change(payload)
        self.assertEqual(change["time"], "14:30")
        self.assertEqual(change["duration_minutes"], 180)

    def test_drag_to_all_day(self):
        payload = {"id": "task:5", "start": "2026-10-07", "allDay": True}
        change = ca.compute_event_change(payload)
        self.assertTrue(change["all_day"])
        self.assertIsNone(change["time"])
        self.assertIsNone(change["duration_minutes"])
        self.assertEqual(change["date"], "2026-10-07")

    def test_all_day_to_timed(self):
        payload = {"id": "task:5", "start": "2026-10-07T14:00:00",
                   "end": "2026-10-07T15:00:00", "allDay": False}
        change = ca.compute_event_change(payload)
        self.assertFalse(change["all_day"])
        self.assertEqual(change["time"], "14:00")
        self.assertEqual(change["duration_minutes"], 60)

    def test_occurrence_drag_keeps_original_key(self):
        payload = {"id": "occurrence:20:2026-10-05T09:00",
                   "start": "2026-10-06T14:00:00",
                   "end": "2026-10-06T15:00:00", "allDay": False}
        change = ca.compute_event_change(payload)
        self.assertEqual(change["kind"], "occurrence")
        self.assertEqual(change["occurrence_key"], "2026-10-05T09:00")
        self.assertEqual(change["date"], "2026-10-06")

    def test_invalid_event(self):
        self.assertIsNone(ca.compute_event_change(None))
        self.assertIsNone(ca.compute_event_change({"start": "x"}))


class ClickTest(unittest.TestCase):

    def test_date_click_timed(self):
        info = ca.compute_date_click({"date": "2026-10-05T15:00:00", "allDay": False})
        self.assertEqual(info["date"], "2026-10-05")
        self.assertEqual(info["time"], "15:00")
        self.assertEqual(info["duration_minutes"], 60)

    def test_date_click_month(self):
        info = ca.compute_date_click({"date": "2026-10-05", "allDay": True})
        self.assertTrue(info["all_day"])
        self.assertIsNone(info["time"])

    def test_select_range(self):
        info = ca.compute_select({"start": "2026-10-05T15:00:00",
                                  "end": "2026-10-05T16:30:00", "allDay": False})
        self.assertEqual(info["time"], "15:00")
        self.assertEqual(info["duration_minutes"], 90)

    def test_event_click(self):
        parsed = ca.compute_event_click({"event": {"id": "occurrence:9:2026-10-05T09:00"}})
        self.assertEqual(parsed["task_id"], 9)
        self.assertEqual(parsed["occurrence_key"], "2026-10-05T09:00")

    def test_fingerprint_stable(self):
        p = {"id": "task:5", "start": "2026-10-06T16:00:00",
             "end": "2026-10-06T17:00:00", "allDay": False}
        self.assertEqual(ca.change_fingerprint(p), ca.change_fingerprint(dict(p)))
        p2 = dict(p, start="2026-10-06T18:00:00")
        self.assertNotEqual(ca.change_fingerprint(p), ca.change_fingerprint(p2))


class RangeTest(unittest.TestCase):

    def test_day_range(self):
        self.assertEqual(ca.range_for_view(calendar_settings.VIEW_DAY, "2026-10-05"),
                         ("2026-10-05", "2026-10-05"))

    def test_week_range(self):
        start, end = ca.range_for_view(calendar_settings.VIEW_WEEK, "2026-10-05")
        # 2026-10-05 是周一
        self.assertEqual(start, "2026-10-05")
        self.assertEqual(end, "2026-10-11")

    def test_month_range_covers_month(self):
        start, end = ca.range_for_view(calendar_settings.VIEW_MONTH, "2026-10-15")
        self.assertLess(start, "2026-10-01")
        self.assertGreater(end, "2026-10-31")

    def test_invalid_date_falls_back(self):
        start, end = ca.range_for_view(calendar_settings.VIEW_DAY, "not-a-date")
        self.assertEqual(start, end)


if __name__ == "__main__":
    unittest.main()
