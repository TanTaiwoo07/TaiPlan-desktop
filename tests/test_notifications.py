"""提醒系统单元测试（使用临时数据库，不碰真实 todo.db）。

运行方式：
    cd <PROJECT_ROOT>
    .venv\\Scripts\\python -m unittest test_notifications
"""

import os
import tempfile
import unittest
from datetime import datetime

from taiplan import database
from taiplan import services
from taiplan import notification_service as ns


def dt(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi)


class NotificationTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        database.DB_PATH = self.tmp
        database.init_database()

    def tearDown(self):
        try:
            os.remove(self.tmp)
        except OSError:
            pass

    def _config(self, **kw):
        cfg = dict(ns.DEFAULT_NOTIFICATION_CONFIG)
        cfg.update(kw)
        return cfg

    # 1. 15:00 普通任务，15:00 检查 → 产生提醒
    def test_due_task_fires(self):
        services.create_task("开会", date="2026-10-03", time="15:00")
        now = dt(2026, 10, 3, 15, 0)
        notifs = ns.get_due_notifications(now=now)
        self.assertEqual(len(notifs), 1)
        self.assertEqual(notifs[0].title, "开会")

    # 2. 同一任务再次检查 → 不重复
    def test_no_duplicate(self):
        services.create_task("开会", date="2026-10-03", time="15:00")
        now = dt(2026, 10, 3, 15, 0)
        notifs = ns.get_due_notifications(now=now)
        self.assertEqual(len(notifs), 1)
        ns.mark_notification_fired(notifs[0])
        notifs2 = ns.get_due_notifications(now=dt(2026, 10, 3, 15, 1))
        self.assertEqual(len(notifs2), 0)

    # 3. 14:55 已完成 → 15:00 不提醒
    def test_completed_no_fire(self):
        services.create_task("开会", date="2026-10-03", time="15:00")
        t = [x for x in services.get_tasks() if x["title"] == "开会"][0]
        services.toggle_task(t["id"], True)
        notifs = ns.get_due_notifications(now=dt(2026, 10, 3, 15, 0))
        self.assertEqual(len(notifs), 0)

    # 4. urgent 标记
    def test_urgent_flag(self):
        services.create_task("提交报告", date="2026-10-03", time="15:00", priority="urgent")
        notifs = ns.get_due_notifications(now=dt(2026, 10, 3, 15, 0))
        self.assertEqual(len(notifs), 1)
        self.assertTrue(notifs[0].urgent)

    # 5. grace=60，14:30 打开补发 14:00 任务
    def test_grace_补发(self):
        services.create_task("开会", date="2026-10-03", time="14:00")
        notifs = ns.get_due_notifications(now=dt(2026, 10, 3, 14, 30), config=self._config(grace_minutes=60))
        self.assertEqual(len(notifs), 1)

    # 6. grace=60，16:00 不补发 14:00 任务
    def test_grace_no_补发(self):
        services.create_task("开会", date="2026-10-03", time="14:00")
        notifs = ns.get_due_notifications(now=dt(2026, 10, 3, 16, 0), config=self._config(grace_minutes=60))
        self.assertEqual(len(notifs), 0)

    # 7. date-only task，09:00 后 daily summary
    def test_daily_summary(self):
        services.create_task("提交论文", date="2026-10-03")
        services.create_task("缴费", date="2026-10-03")
        summary = ns.get_daily_summary(now=dt(2026, 10, 3, 9, 0))
        self.assertIsNotNone(summary)
        self.assertIn("2 项", summary.title)

    # 8. 同一天 daily summary 只一次
    def test_daily_summary_once(self):
        services.create_task("提交论文", date="2026-10-03")
        s1 = ns.get_daily_summary(now=dt(2026, 10, 3, 9, 0))
        self.assertIsNotNone(s1)
        ns.mark_notification_fired(s1)
        s2 = ns.get_daily_summary(now=dt(2026, 10, 3, 10, 0))
        self.assertIsNone(s2)

    # 9. recurring 今天 occurrence 到点提醒
    def test_recurring_fires(self):
        services.create_task("每天吃饭", date="2026-10-02", time="18:00",
                             is_recurring=True, recurrence_frequency="daily", recurrence_interval=1)
        notifs = ns.get_due_notifications(now=dt(2026, 10, 3, 18, 0))
        self.assertTrue(any(n.title == "每天吃饭" for n in notifs))

    # 10. 完成今天 recurring occurrence → 不提醒
    def test_recurring_completed_no_fire(self):
        services.create_task("每天吃饭", date="2026-10-02", time="18:00",
                             is_recurring=True, recurrence_frequency="daily", recurrence_interval=1)
        t = [x for x in services.get_tasks() if x["title"] == "每天吃饭"][0]
        services.complete_occurrence(t["id"], "2026-10-03T18:00", True)
        notifs = ns.get_due_notifications(now=dt(2026, 10, 3, 18, 0))
        self.assertFalse(any(n.title == "每天吃饭" for n in notifs))

    # 11. cancelled occurrence → 不提醒
    def test_recurring_cancelled_no_fire(self):
        services.create_task("每天吃饭", date="2026-10-02", time="18:00",
                             is_recurring=True, recurrence_frequency="daily", recurrence_interval=1)
        t = [x for x in services.get_tasks() if x["title"] == "每天吃饭"][0]
        services.delete_task_occurrence(t["id"], "2026-10-03T18:00", "this")
        notifs = ns.get_due_notifications(now=dt(2026, 10, 3, 18, 0))
        self.assertFalse(any(n.title == "每天吃饭" for n in notifs))

    # 12. 今天提醒过 recurring → 明天新 occurrence 仍可提醒
    def test_recurring_next_day_fires(self):
        services.create_task("每天吃饭", date="2026-10-02", time="18:00",
                             is_recurring=True, recurrence_frequency="daily", recurrence_interval=1)
        notifs_today = ns.get_due_notifications(now=dt(2026, 10, 3, 18, 0))
        for n in notifs_today:
            ns.mark_notification_fired(n)
        # 明天（10-04）应产生新提醒
        notifs_tomorrow = ns.get_due_notifications(now=dt(2026, 10, 4, 18, 0))
        self.assertTrue(any(n.title == "每天吃饭" for n in notifs_tomorrow))

    # 13. 编辑时间后 → 新时间产生新 key
    def test_edit_time_new_key(self):
        services.create_task("开会", date="2026-10-03", time="15:00")
        t = [x for x in services.get_tasks() if x["title"] == "开会"][0]
        # 15:00 提醒
        notifs = ns.get_due_notifications(now=dt(2026, 10, 3, 15, 0))
        self.assertEqual(len(notifs), 1)
        ns.mark_notification_fired(notifs[0])
        # 编辑到 18:00
        services.edit_task(t["id"], "开会", date="2026-10-03", time="18:00")
        notifs2 = ns.get_due_notifications(now=dt(2026, 10, 3, 18, 0))
        self.assertEqual(len(notifs2), 1)

    # 14. 提醒关闭 → 无提醒
    def test_disabled(self):
        services.create_task("开会", date="2026-10-03", time="15:00")
        notifs = ns.get_due_notifications(now=dt(2026, 10, 3, 15, 0), config=self._config(enabled=False))
        self.assertEqual(len(notifs), 0)


if __name__ == "__main__":
    unittest.main()
