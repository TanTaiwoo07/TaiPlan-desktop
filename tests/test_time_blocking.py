"""Time Blocking（12 阶段）单元测试。

覆盖：duration、跨午夜、all-day、override（仅修改这一次）、
系列调整、冲突检测、NLP duration、AI duration 校验。

全部使用临时数据库，不碰真实 todo.db。
"""

import os
import tempfile
import unittest
from datetime import datetime, timedelta

from taiplan import database


class _TempDb(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        database.DB_PATH = self.tmp
        database.init_database()

    def tearDown(self):
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.tmp + suffix)
            except OSError:
                pass


class DurationTest(_TempDb):

    def test_create_with_duration(self):
        from taiplan import services
        services.create_task("高数", date="2026-10-05", time="14:00", duration_minutes=90)
        item = services.get_items_for_range("2026-10-05", "2026-10-05")[0]
        self.assertEqual(item.duration_minutes, 90)

    def test_duration_rejected_without_time(self):
        """全天任务不能有 duration。"""
        from taiplan import services
        services.create_task("交作业", date="2026-10-05", duration_minutes=60)
        item = services.get_items_for_range("2026-10-05", "2026-10-05")[0]
        self.assertIsNone(item.duration_minutes)

    def test_duration_range_validation(self):
        from taiplan import services
        with self.assertRaises(ValueError):
            services.create_task("x", date="2026-10-05", time="10:00", duration_minutes=1)
        with self.assertRaises(ValueError):
            services.create_task("x", date="2026-10-05", time="10:00", duration_minutes=2000)

    def test_legacy_default_is_not_persisted(self):
        """旧任务 duration_minutes 仍为 NULL（不因显示而写库）。"""
        from taiplan import services
        services.create_task("旧任务", date="2026-10-05", time="09:00")
        task = [t for t in services.get_tasks() if t["title"] == "旧任务"][0]
        self.assertIsNone(task["duration_minutes"])
        item = services.get_items_for_range("2026-10-05", "2026-10-05")[0]
        self.assertEqual(services.resolve_duration(item), 60)


class DragResizeTest(_TempDb):

    def test_plain_task_drag_changes_date_time(self):
        from taiplan import services
        services.create_task("高数", date="2026-10-05", time="14:30", duration_minutes=90)
        tid = [t["id"] for t in services.get_tasks()][0]
        services.move_plain_task(tid, new_date="2026-10-06", new_time="16:00",
                                 new_duration_minutes=90)
        task = services.get_task(tid)
        self.assertEqual(task["date"], "2026-10-06")
        self.assertEqual(task["time"], "16:00")

    def test_plain_task_resize(self):
        from taiplan import services
        services.create_task("高数", date="2026-10-05", time="14:30", duration_minutes=90)
        tid = [t["id"] for t in services.get_tasks()][0]
        services.move_plain_task(tid, new_duration_minutes=180)
        self.assertEqual(services.get_task(tid)["duration_minutes"], 180)

    def test_timed_to_all_day(self):
        from taiplan import services
        services.create_task("高数", date="2026-10-05", time="14:30", duration_minutes=90)
        tid = [t["id"] for t in services.get_tasks()][0]
        services.move_plain_task(tid, new_date="2026-10-07", clear_time=True)
        task = services.get_task(tid)
        self.assertEqual(task["date"], "2026-10-07")
        self.assertIsNone(task["time"])
        self.assertIsNone(task["duration_minutes"])

    def test_all_day_to_timed_uses_default(self):
        from taiplan import services
        services.create_task("交作业", date="2026-10-05")
        tid = [t["id"] for t in services.get_tasks()][0]
        services.move_plain_task(tid, new_date="2026-10-05", new_time="14:00")
        task = services.get_task(tid)
        self.assertEqual(task["time"], "14:00")
        self.assertEqual(task["duration_minutes"], services.DEFAULT_DURATION_MINUTES)


class OccurrenceOverrideTest(_TempDb):

    def _daily(self, title="每天吃药", date="2026-10-05", time="09:00"):
        from taiplan import services
        services.create_task(title, date=date, time=time, duration_minutes=30,
                             is_recurring=True, recurrence_frequency="daily",
                             recurrence_interval=1)
        return [t["id"] for t in services.get_tasks() if t["title"] == title][0]

    def test_override_moves_occurrence_to_new_date(self):
        from taiplan import services
        tid = self._daily()
        services.set_occurrence_override(tid, "2026-10-05T09:00",
                                         date="2026-10-06", time="14:00")
        moved = [i for i in services.get_items_for_range("2026-10-06", "2026-10-06")
                 if i.occurrence_key == "2026-10-05T09:00"]
        self.assertEqual(len(moved), 1)
        self.assertEqual(moved[0].date, "2026-10-06")
        self.assertEqual(moved[0].time, "14:00")

    def test_original_date_no_longer_shows(self):
        from taiplan import services
        tid = self._daily()
        services.set_occurrence_override(tid, "2026-10-05T09:00", date="2026-10-06", time="14:00")
        same_day = [i for i in services.get_items_for_range("2026-10-05", "2026-10-05")
                    if i.occurrence_key == "2026-10-05T09:00"]
        self.assertEqual(same_day, [])

    def test_override_keeps_original_occurrence_key(self):
        from taiplan import services
        tid = self._daily()
        services.set_occurrence_override(tid, "2026-10-05T09:00", date="2026-10-08", time="11:00")
        rows = services.get_occurrence_override(tid, "2026-10-05T09:00")
        self.assertIsNotNone(rows)
        self.assertEqual(rows["occurrence_key"], "2026-10-05T09:00")
        # 其他 occurrence 不受影响
        other = [i for i in services.get_items_for_range("2026-10-06", "2026-10-06")
                 if i.occurrence_key == "2026-10-06T09:00"]
        self.assertEqual(len(other), 1)
        self.assertEqual(other[0].time, "09:00")

    def test_override_duration_only(self):
        from taiplan import services
        tid = self._daily()
        services.set_occurrence_override(tid, "2026-10-06T09:00", duration_minutes=90)
        item = [i for i in services.get_items_for_range("2026-10-06", "2026-10-06")
                if i.occurrence_key == "2026-10-06T09:00"][0]
        self.assertEqual(item.date, "2026-10-06")
        self.assertEqual(item.duration_minutes, 90)

    def test_completed_override_still_completed_on_new_date(self):
        from taiplan import services
        tid = self._daily()
        services.set_occurrence_override(tid, "2026-10-05T09:00", date="2026-10-06", time="14:00")
        services.complete_occurrence(tid, "2026-10-05T09:00", True)
        item = [i for i in services.get_items_for_range("2026-10-06", "2026-10-06")
                if i.occurrence_key == "2026-10-05T09:00"][0]
        self.assertTrue(item.is_completed)

    def test_cancelled_override_not_shown(self):
        from taiplan import services
        tid = self._daily()
        services.set_occurrence_override(tid, "2026-10-05T09:00", date="2026-10-06", time="14:00")
        services.cancel_occurrence(tid, "2026-10-05T09:00")
        shown = [i for i in services.get_items_for_range("2026-10-06", "2026-10-06")
                 if i.occurrence_key == "2026-10-05T09:00"]
        self.assertEqual(shown, [])

    def test_clear_override_restores_original(self):
        from taiplan import services
        tid = self._daily()
        services.set_occurrence_override(tid, "2026-10-05T09:00", date="2026-10-06", time="14:00")
        services.clear_occurrence_override(tid, "2026-10-05T09:00")
        restored = [i for i in services.get_items_for_range("2026-10-05", "2026-10-05")
                    if i.occurrence_key == "2026-10-05T09:00"]
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0].time, "09:00")


class SeriesChangeTest(_TempDb):

    def test_series_move_shifts_anchor(self):
        from taiplan import services
        # 每周一 09:00
        services.create_task("周会", date="2026-10-05", time="09:00", duration_minutes=60,
                             is_recurring=True, recurrence_frequency="weekly",
                             recurrence_interval=1)
        tid = [t["id"] for t in services.get_tasks()][0]
        # 把 10-05 这次移到 10-06（周二）10:00，选择整个系列
        services.move_series_by_occurrence(tid, "2026-10-05T09:00", "2026-10-06", "10:00", 60)
        task = services.get_task(tid)
        self.assertEqual(task["date"], "2026-10-06")
        self.assertEqual(task["time"], "10:00")
        # 下一次 occurrence 应为 10-13
        nxt = [i for i in services.get_items_for_range("2026-10-13", "2026-10-13")
               if i.title == "周会"]
        self.assertEqual(len(nxt), 1)

    def test_series_resize_changes_master_duration(self):
        from taiplan import services
        services.create_task("周会", date="2026-10-05", time="09:00", duration_minutes=60,
                             is_recurring=True, recurrence_frequency="weekly",
                             recurrence_interval=1)
        tid = [t["id"] for t in services.get_tasks()][0]
        services.resize_series(tid, 90)
        self.assertEqual(services.get_task(tid)["duration_minutes"], 90)
        nxt = [i for i in services.get_items_for_range("2026-10-12", "2026-10-12")
               if i.title == "周会"][0]
        self.assertEqual(nxt.duration_minutes, 90)

    def test_series_change_keeps_existing_override(self):
        from taiplan import services
        services.create_task("每天吃药", date="2026-10-05", time="09:00", duration_minutes=30,
                             is_recurring=True, recurrence_frequency="daily", recurrence_interval=1)
        tid = [t["id"] for t in services.get_tasks()][0]
        services.set_occurrence_override(tid, "2026-10-06T09:00", time="15:00")
        services.resize_series(tid, 45)
        item = [i for i in services.get_items_for_range("2026-10-06", "2026-10-06")
                if i.occurrence_key == "2026-10-06T09:00"][0]
        self.assertEqual(item.time, "15:00")     # override 优先
        self.assertEqual(item.duration_minutes, 45)


class ConflictTest(_TempDb):

    def test_touching_boundaries_not_conflict(self):
        from taiplan import services
        services.create_task("A", date="2026-10-05", time="14:00", duration_minutes=60)
        conflicts = services.detect_time_conflicts("2026-10-05", "15:00", 60)
        self.assertEqual(conflicts, [])

    def test_overlapping_is_conflict(self):
        from taiplan import services
        services.create_task("项目会议", date="2026-10-05", time="15:30", duration_minutes=90)
        conflicts = services.detect_time_conflicts("2026-10-05", "15:00", 60)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0].title, "项目会议")

    def test_completed_not_counted(self):
        from taiplan import services
        services.create_task("已完成", date="2026-10-05", time="15:30", duration_minutes=60)
        tid = [t["id"] for t in services.get_tasks()][0]
        services.toggle_task(tid, True)
        conflicts = services.detect_time_conflicts("2026-10-05", "15:00", 60)
        self.assertEqual(conflicts, [])

    def test_recurring_occurrence_participates(self):
        from taiplan import services
        services.create_task("每天吃药", date="2026-10-05", time="15:30", duration_minutes=30,
                             is_recurring=True, recurrence_frequency="daily", recurrence_interval=1)
        conflicts = services.detect_time_conflicts("2026-10-05", "15:00", 60)
        self.assertEqual(len(conflicts), 1)

    def test_cancelled_occurrence_excluded(self):
        from taiplan import services
        services.create_task("每天吃药", date="2026-10-05", time="15:30", duration_minutes=30,
                             is_recurring=True, recurrence_frequency="daily", recurrence_interval=1)
        tid = [t["id"] for t in services.get_tasks()][0]
        services.cancel_occurrence(tid, "2026-10-05T15:30")
        conflicts = services.detect_time_conflicts("2026-10-05", "15:00", 60)
        self.assertEqual(conflicts, [])

    def test_self_excluded(self):
        from taiplan import services
        services.create_task("A", date="2026-10-05", time="15:00", duration_minutes=60)
        tid = [t["id"] for t in services.get_tasks()][0]
        conflicts = services.detect_time_conflicts(
            "2026-10-05", "15:00", 60, exclude_task_id=tid, exclude_occurrence_key=None)
        self.assertEqual(conflicts, [])

    def test_override_occurrence_participates_on_new_date(self):
        from taiplan import services
        services.create_task("每天吃药", date="2026-10-05", time="09:00", duration_minutes=30,
                             is_recurring=True, recurrence_frequency="daily", recurrence_interval=1)
        tid = [t["id"] for t in services.get_tasks()][0]
        services.set_occurrence_override(tid, "2026-10-05T09:00", date="2026-10-06", time="15:30")
        conflicts = services.detect_time_conflicts("2026-10-06", "15:00", 60)
        self.assertTrue(any(c.occurrence_key == "2026-10-05T09:00" for c in conflicts))


class NlpDurationTest(unittest.TestCase):

    def test_hours_suffix(self):
        from taiplan import nlp_parser
        p = nlp_parser.parse_quick_task("下午3点开会2小时")
        self.assertEqual(p.title, "开会")
        self.assertEqual(p.time, "15:00")
        self.assertEqual(p.duration_minutes, 120)

    def test_range_expression(self):
        from taiplan import nlp_parser
        now = datetime(2026, 10, 4, 8, 0)
        p = nlp_parser.parse_quick_task("明天9点到10点上课", now=now)
        self.assertEqual(p.title, "上课")
        self.assertEqual(p.time, "09:00")
        self.assertEqual(p.duration_minutes, 60)
        self.assertEqual(p.date, "2026-10-05")

    def test_pm_range_with_half(self):
        from taiplan import nlp_parser
        p = nlp_parser.parse_quick_task("晚上8点到9点半学习")
        self.assertEqual(p.title, "学习")
        self.assertEqual(p.time, "20:00")
        self.assertEqual(p.duration_minutes, 90)

    def test_half_hour_word(self):
        from taiplan import nlp_parser
        p = nlp_parser.parse_quick_task("下午3点半小时复盘")
        self.assertEqual(p.time, "15:00")
        self.assertEqual(p.duration_minutes, 30)

    def test_minutes_word(self):
        from taiplan import nlp_parser
        p = nlp_parser.parse_quick_task("上午10点站会45分钟")
        self.assertEqual(p.duration_minutes, 45)

    def test_no_duration_returns_none(self):
        from taiplan import nlp_parser
        p = nlp_parser.parse_quick_task("明天下午3点开会")
        self.assertIsNone(p.duration_minutes)


class AiDurationSchemaTest(unittest.TestCase):

    def test_valid(self):
        import app
        self.assertEqual(app._coerce_ai_duration(105), 105)
        self.assertEqual(app._coerce_ai_duration("90"), 90)

    def test_invalid(self):
        import app
        self.assertIsNone(app._coerce_ai_duration(None))
        self.assertIsNone(app._coerce_ai_duration("abc"))
        self.assertIsNone(app._coerce_ai_duration(1))
        self.assertEqual(app._coerce_ai_duration(99999), 1440)


if __name__ == "__main__":
    unittest.main()
