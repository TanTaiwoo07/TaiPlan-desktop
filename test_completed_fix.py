"""7B 修复验证：Completed 列表 ValueError 与 occurrence 完成状态串台。

运行方式：
    cd <PROJECT_ROOT>
    .venv\\Scripts\\python -m unittest test_completed_fix
"""

import os
import tempfile
import unittest

import database
import recurrence
import services


class CompletedFixTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        database.DB_PATH = self.tmp
        database.init_database()

    def tearDown(self):
        try:
            os.remove(self.tmp)
        except OSError:
            pass

    def test_plain_task_without_date_does_not_raise(self):
        services.create_task("买一本书")  # 无日期
        t = [x for x in services.get_tasks() if x["title"] == "买一本书"][0]
        services.toggle_task(t["id"], True)
        completed = services.get_completed_items()
        plain = [i for i in completed if i.title == "买一本书"]
        self.assertEqual(len(plain), 1)
        self.assertFalse(plain[0].is_recurring)
        self.assertIsNone(plain[0].occurrence_key)
        self.assertIsNone(plain[0].date)

    def test_complete_one_daily_occurrence_does_not_affect_others(self):
        services.create_task("每天吃饭", date="2026-10-02", time="18:00",
                             is_recurring=True, recurrence_frequency="daily", recurrence_interval=1)
        t = [x for x in services.get_tasks() if x["title"] == "每天吃饭"][0]
        services.complete_occurrence(t["id"], "2026-10-02T18:00", True)

        d2 = {i.occurrence_key: i for i in services.get_items_for_date("2026-10-02") if i.title == "每天吃饭"}
        d3 = {i.occurrence_key: i for i in services.get_items_for_date("2026-10-03") if i.title == "每天吃饭"}
        self.assertTrue(d2["2026-10-02T18:00"].is_completed)
        self.assertFalse(d3["2026-10-03T18:00"].is_completed)

    def test_completed_occurrence_restores_date_time_from_key(self):
        services.create_task("每天吃饭", date="2026-10-02", time="18:00",
                             is_recurring=True, recurrence_frequency="daily", recurrence_interval=1)
        t = [x for x in services.get_tasks() if x["title"] == "每天吃饭"][0]
        services.complete_occurrence(t["id"], "2026-10-02T18:00", True)

        completed = [i for i in services.get_completed_items() if i.title == "每天吃饭"]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].date, "2026-10-02")
        self.assertEqual(completed[0].time, "18:00")

    def test_uncomplete_only_that_occurrence(self):
        services.create_task("每天吃饭", date="2026-10-02", time="18:00",
                             is_recurring=True, recurrence_frequency="daily", recurrence_interval=1)
        t = [x for x in services.get_tasks() if x["title"] == "每天吃饭"][0]
        services.complete_occurrence(t["id"], "2026-10-02T18:00", True)
        services.complete_occurrence(t["id"], "2026-10-02T18:00", False)

        d2 = {i.occurrence_key: i for i in services.get_items_for_date("2026-10-02") if i.title == "每天吃饭"}
        d3 = {i.occurrence_key: i for i in services.get_items_for_date("2026-10-03") if i.title == "每天吃饭"}
        self.assertFalse(d2["2026-10-02T18:00"].is_completed)
        self.assertFalse(d3["2026-10-03T18:00"].is_completed)

    def test_hourly_occurrences_independent(self):
        services.create_task("每小时吃药", date="2026-10-02", time="09:30",
                             is_recurring=True, recurrence_frequency="hourly", recurrence_interval=1)
        t = [x for x in services.get_tasks() if x["title"] == "每小时吃药"][0]
        services.complete_occurrence(t["id"], "2026-10-02T09:30", True)

        items = {i.occurrence_key: i for i in services.get_items_for_date("2026-10-02") if i.title == "每小时吃药"}
        self.assertTrue(items["2026-10-02T09:30"].is_completed)
        self.assertFalse(items["2026-10-02T11:30"].is_completed)

    def test_parse_occurrence_key_roundtrip(self):
        self.assertEqual(recurrence.parse_occurrence_key("2026-10-05"), ("2026-10-05", None))
        self.assertEqual(recurrence.parse_occurrence_key("2026-10-05T18:00"), ("2026-10-05", "18:00"))


if __name__ == "__main__":
    unittest.main()
