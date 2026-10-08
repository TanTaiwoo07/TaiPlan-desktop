"""7B 阶段单元测试：occurrence 投影、完成单次、新表等。

运行方式：
    cd <PROJECT_ROOT>
    .venv\\Scripts\\python -m unittest test_recurrence_occurrence
"""

import os
import sqlite3
import tempfile
import unittest

import database
import recurrence
import services


class OccurrenceProjectionTest(unittest.TestCase):

    def setUp(self):
        # 每个测试用独立临时数据库，不碰真实 todo.db
        self.tmp = tempfile.mktemp(suffix=".db")
        database.DB_PATH = self.tmp
        database.init_database()

    def tearDown(self):
        try:
            os.remove(self.tmp)
        except OSError:
            pass

    def test_new_table_exists(self):
        conn = sqlite3.connect(self.tmp)
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        conn.close()
        self.assertIn("task_occurrence_states", tables)

    def test_anchor_occurrence_not_duplicated(self):
        # 每周任务：原始 date 不应与 recurrence occurrence 重复显示
        services.create_task("每周会议", date="2026-10-05", time="09:30",
                             is_recurring=True, recurrence_frequency="weekly", recurrence_interval=1)
        items = services.get_items_for_month(2026, 10)
        meeting = [i for i in items if i.title == "每周会议"]
        dates = [i.date for i in meeting]
        self.assertEqual(dates, ["2026-10-05", "2026-10-12", "2026-10-19", "2026-10-26"])
        self.assertEqual(len(dates), len(set(dates)))

    def test_weekly_projected_multiple_times_in_month(self):
        services.create_task("每周会议", date="2026-10-05", time="09:30",
                             is_recurring=True, recurrence_frequency="weekly", recurrence_interval=1)
        items = services.get_items_for_month(2026, 10)
        meeting = [i for i in items if i.title == "每周会议"]
        self.assertEqual(len(meeting), 4)

    def test_complete_one_occurrence_does_not_affect_next(self):
        services.create_task("每周会议", date="2026-10-05", time="09:30",
                             is_recurring=True, recurrence_frequency="weekly", recurrence_interval=1)
        task = [t for t in services.get_tasks() if t["title"] == "每周会议"][0]
        services.complete_occurrence(task["id"], "2026-10-12T09:30", True)

        items = services.get_items_for_month(2026, 10)
        meeting = {i.date: i for i in items if i.title == "每周会议"}
        self.assertTrue(meeting["2026-10-12"].is_completed)
        self.assertFalse(meeting["2026-10-19"].is_completed)

        # 取消完成
        services.complete_occurrence(task["id"], "2026-10-12T09:30", False)
        items = services.get_items_for_month(2026, 10)
        meeting = {i.date: i for i in items if i.title == "每周会议"}
        self.assertFalse(meeting["2026-10-12"].is_completed)

    def test_hourly_occurrence_keys_do_not_conflict(self):
        services.create_task("每小时吃药", date="2026-10-02", time="09:30",
                             is_recurring=True, recurrence_frequency="hourly", recurrence_interval=1)
        items = services.get_items_for_date("2026-10-02")
        hourly = [i for i in items if i.title == "每小时吃药"]
        keys = [i.occurrence_key for i in hourly]
        self.assertEqual(len(keys), len(set(keys)))

        # 完成 09:30 不影响 10:30
        task_id = hourly[0].task_id
        services.complete_occurrence(task_id, "2026-10-02T09:30", True)
        items2 = services.get_items_for_date("2026-10-02")
        hourly2 = {i.time: i for i in items2 if i.title == "每小时吃药"}
        self.assertTrue(hourly2["09:30"].is_completed)
        self.assertFalse(hourly2["10:30"].is_completed)

    def test_today_summary_counts_recurring_occurrence(self):
        today = services._today_str()
        services.create_task("普通", date=today)
        services.create_task("重复", date=today, is_recurring=True,
                             recurrence_frequency="daily", recurrence_interval=1)
        all_items = services.get_items_for_date(today)
        # 普通1 + 重复1 = 2
        self.assertEqual(len(all_items), 2)

    def test_get_next_occurrence(self):
        services.create_task("每天跑步", date="2026-10-01", is_recurring=True,
                             recurrence_frequency="daily", recurrence_interval=1)
        t = [t for t in services.get_tasks() if t["title"] == "每天跑步"][0]
        nxt = recurrence.get_next_occurrence(t, "2026-10-01")
        self.assertEqual(nxt.date, "2026-10-02")

    def test_no_next_occurrence_after_end_date(self):
        services.create_task("会结束", date="2026-10-01", is_recurring=True,
                             recurrence_frequency="daily", recurrence_interval=1,
                             recurrence_end_type="on_date", recurrence_end_date="2026-10-03")
        t = [t for t in services.get_tasks() if t["title"] == "会结束"][0]
        nxt = recurrence.get_next_occurrence(t, "2026-10-03")
        self.assertIsNone(nxt)

    def test_delete_series_cleans_occurrence_state(self):
        services.create_task("系列", date="2026-10-05", is_recurring=True,
                             recurrence_frequency="weekly", recurrence_interval=1)
        t = [t for t in services.get_tasks() if t["title"] == "系列"][0]
        services.complete_occurrence(t["id"], "2026-10-05T09:30", True)
        services.remove_task(t["id"])
        states = database.get_occurrence_states()
        self.assertTrue(all(s["task_id"] != t["id"] for s in states))

    def test_non_recurring_task_unchanged(self):
        services.create_task("普通任务", date="2026-10-10", time="08:00")
        items = services.get_items_for_date("2026-10-10")
        plain = [i for i in items if i.title == "普通任务"]
        self.assertEqual(len(plain), 1)
        self.assertFalse(plain[0].is_recurring)
        self.assertIsNone(plain[0].occurrence_key)  # 普通任务不需要 occurrence_key


if __name__ == "__main__":
    unittest.main()


