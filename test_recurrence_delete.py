"""8C 阶段：重复任务删除语义单元测试。

运行方式：
    cd <PROJECT_ROOT>
    .venv\\Scripts\\python -m unittest test_recurrence_delete
"""

import os
import tempfile
import unittest

import database
import recurrence
import services


class RecurrenceDeleteTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        database.DB_PATH = self.tmp
        database.init_database()

    def tearDown(self):
        try:
            os.remove(self.tmp)
        except OSError:
            pass

    def _create_daily(self):
        services.create_task("每天吃饭", date="2026-10-02", time="18:00",
                             is_recurring=True, recurrence_frequency="daily",
                             recurrence_interval=1)
        return [t for t in services.get_tasks() if t["title"] == "每天吃饭"][0]

    def test_daily_cancel_one_occurrence(self):
        t = self._create_daily()
        services.delete_task_occurrence(t["id"], "2026-10-03T18:00", "this")
        # 10-02 正常，10-03 消失，10-04 正常
        items2 = [i.occurrence_key for i in services.get_items_for_date("2026-10-02") if i.title == "每天吃饭"]
        items3 = [i.occurrence_key for i in services.get_items_for_date("2026-10-03") if i.title == "每天吃饭"]
        items4 = [i.occurrence_key for i in services.get_items_for_date("2026-10-04") if i.title == "每天吃饭"]
        self.assertIn("2026-10-02T18:00", items2)
        self.assertNotIn("2026-10-03T18:00", items3)
        self.assertIn("2026-10-04T18:00", items4)

    def test_weekly_cancel_one_occurrence(self):
        services.create_task("每周会议", date="2026-10-05", time="09:30",
                             is_recurring=True, recurrence_frequency="weekly", recurrence_interval=1)
        t = [x for x in services.get_tasks() if x["title"] == "每周会议"][0]
        services.delete_task_occurrence(t["id"], "2026-10-12T09:30", "this")
        month = [i.occurrence_key for i in services.get_items_for_month(2026, 10) if i.title == "每周会议"]
        self.assertIn("2026-10-05T09:30", month)
        self.assertNotIn("2026-10-12T09:30", month)
        self.assertIn("2026-10-19T09:30", month)

    def test_hourly_cancel_one(self):
        services.create_task("每小时吃药", date="2026-10-02", time="09:00",
                             is_recurring=True, recurrence_frequency="hourly", recurrence_interval=1)
        t = [x for x in services.get_tasks() if x["title"] == "每小时吃药"][0]
        services.delete_task_occurrence(t["id"], "2026-10-02T11:00", "this")
        items = {i.occurrence_key for i in services.get_items_for_date("2026-10-02") if i.title == "每小时吃药"}
        self.assertIn("2026-10-02T09:00", items)
        self.assertNotIn("2026-10-02T11:00", items)
        self.assertIn("2026-10-02T13:00", items)

    def test_daily_cancel_from_occurrence(self):
        t = self._create_daily()
        services.delete_task_occurrence(t["id"], "2026-10-03T18:00", "after")
        # 10-02 保留，10-03 起消失
        items2 = [i.occurrence_key for i in services.get_items_for_date("2026-10-02") if i.title == "每天吃饭"]
        items3 = [i.occurrence_key for i in services.get_items_for_date("2026-10-03") if i.title == "每天吃饭"]
        items4 = [i.occurrence_key for i in services.get_items_for_date("2026-10-04") if i.title == "每天吃饭"]
        self.assertIn("2026-10-02T18:00", items2)
        self.assertNotIn("2026-10-03T18:00", items3)
        self.assertNotIn("2026-10-04T18:00", items4)

    def test_hourly_cancel_from_occurrence(self):
        services.create_task("每2小时喝水", date="2026-10-03", time="09:00",
                             is_recurring=True, recurrence_frequency="hourly", recurrence_interval=2)
        t = [x for x in services.get_tasks() if x["title"] == "每2小时喝水"][0]
        services.delete_task_occurrence(t["id"], "2026-10-03T13:00", "after")
        items = {i.occurrence_key for i in services.get_items_for_date("2026-10-03") if i.title == "每2小时喝水"}
        self.assertIn("2026-10-03T09:00", items)
        self.assertIn("2026-10-03T11:00", items)
        self.assertNotIn("2026-10-03T13:00", items)
        self.assertNotIn("2026-10-03T15:00", items)

    def test_delete_series_removes_master(self):
        t = self._create_daily()
        services.delete_task_occurrence(t["id"], "2026-10-03T18:00", "series")
        self.assertIsNone(services.get_task(t["id"]))

    def test_delete_series_cleans_states(self):
        t = self._create_daily()
        services.complete_occurrence(t["id"], "2026-10-02T18:00", True)
        services.delete_task_occurrence(t["id"], "2026-10-03T18:00", "series")
        states = database.get_occurrence_states()
        self.assertTrue(all(s["task_id"] != t["id"] for s in states))

    def test_completed_to_cancelled_not_in_completed(self):
        t = self._create_daily()
        services.complete_occurrence(t["id"], "2026-10-02T18:00", True)
        # 改为 cancelled
        services.delete_task_occurrence(t["id"], "2026-10-02T18:00", "this")
        completed = [i for i in services.get_completed_items() if i.title == "每天吃饭"]
        self.assertEqual(len(completed), 0)

    def test_get_next_occurrence_skips_cancelled(self):
        from datetime import datetime
        t = self._create_daily()
        services.delete_task_occurrence(t["id"], "2026-10-03T18:00", "this")
        # 重复系列只返回下一次 occurrence，且应跳过被取消的那一次
        upcoming = [i for i in services.get_upcoming_items() if i.title == "每天吃饭"]
        self.assertEqual(len(upcoming), 1)
        self.assertNotEqual(upcoming[0].date, "2026-10-03")
        # 下一次必须严格晚于今天（不依赖固定的“今天”）
        self.assertGreater(
            datetime.strptime(upcoming[0].date, "%Y-%m-%d").date(),
            datetime.now().date(),
        )

    def test_plain_task_delete_unchanged(self):
        services.create_task("普通任务", date="2026-10-10", time="08:00")
        t = [x for x in services.get_tasks() if x["title"] == "普通任务"][0]
        services.remove_task(t["id"])
        self.assertIsNone(services.get_task(t["id"]))


if __name__ == "__main__":
    unittest.main()
