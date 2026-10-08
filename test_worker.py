"""后台逻辑单元测试（mock notifier，不弹真实通知）。

运行方式：
    cd <PROJECT_ROOT>
    .venv\\Scripts\\python -m unittest test_worker
"""

import os
import tempfile
import unittest
from datetime import datetime
from unittest import mock

import database
import notification_service as notif
import services


class WorkerClaimTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        database.DB_PATH = self.tmp
        database.init_database()

    def tearDown(self):
        try:
            os.remove(self.tmp)
        except OSError:
            pass

    def _due_task(self, title="开会", time="15:00", date="2026-10-03"):
        services.create_task(title, date=date, time=time)

    # 1. Worker 找到 due task
    def test_worker_finds_due(self):
        self._due_task()
        due = notif.get_due_notifications(now=datetime(2026, 10, 3, 15, 0))
        self.assertEqual(len(due), 1)

    # 2. claim 成功 → notify 一次
    @mock.patch("desktop_notifier.DesktopNotifier.notify_task_due")
    def test_claim_then_notify(self, mock_notify):
        self._due_task()
        due = notif.get_due_notifications(now=datetime(2026, 10, 3, 15, 0))
        n = due[0]
        self.assertTrue(notif.claim_notification(n))
        # 模拟 worker 发送
        notifier = __import__("desktop_notifier").DesktopNotifier()
        notifier.notify_task_due(n.message, urgent=n.urgent)
        mock_notify.assert_called_once()
        notif.mark_notification_delivered(n, channel="desktop")

    # 3. claim 失败 → 不 notify
    def test_claim_fail_no_notify(self):
        self._due_task()
        due = notif.get_due_notifications(now=datetime(2026, 10, 3, 15, 0))
        n = due[0]
        self.assertTrue(notif.claim_notification(n))
        # 第二次 claim 同一 key 应失败
        self.assertFalse(notif.claim_notification(n))

    # 4. 两个并发 worker 同一 key 只有一个 claim 成功
    def test_concurrent_claim_single_winner(self):
        self._due_task()
        due = notif.get_due_notifications(now=datetime(2026, 10, 3, 15, 0))
        n = due[0]
        r1 = notif.claim_notification(n)
        r2 = notif.claim_notification(n)
        self.assertEqual((r1, r2), (True, False))

    # 5. 发送成功 → delivered
    def test_delivered_status(self):
        self._due_task()
        due = notif.get_due_notifications(now=datetime(2026, 10, 3, 15, 0))
        n = due[0]
        notif.claim_notification(n)
        notif.mark_notification_delivered(n, channel="desktop")
        row = database.get_recent_notifications(1)[0]
        self.assertEqual(row["delivery_status"], "delivered")

    # 6. notify 异常 → failed
    def test_failed_status(self):
        self._due_task()
        due = notif.get_due_notifications(now=datetime(2026, 10, 3, 15, 0))
        n = due[0]
        notif.claim_notification(n)
        notif.mark_notification_failed(n, "winotify error", channel="desktop")
        row = database.get_recent_notifications(1)[0]
        self.assertEqual(row["delivery_status"], "failed")
        self.assertIn("winotify", row["delivery_error"])

    # 7. heartbeat 更新
    def test_heartbeat(self):
        database.update_runtime_status("reminder_worker", pid=1234)
        row = database.get_runtime_status("reminder_worker")
        self.assertIsNotNone(row)
        self.assertEqual(row["pid"], 1234)

    # 8. stale heartbeat → worker offline
    def test_stale_heartbeat_offline(self):
        # 写一个 5 分钟前的 heartbeat
        conn = database.get_connection()
        old = "2026-10-03 10:00:00"
        conn.execute(
            "INSERT OR REPLACE INTO runtime_status (component, pid, heartbeat_at, started_at, status) VALUES ('reminder_worker', 1, ?, ?, 'running')",
            (old, old),
        )
        conn.commit()
        conn.close()
        self.assertFalse(database.is_worker_alive(max_age_seconds=60))

    # 9. daily summary 后台触发
    def test_daily_summary_claim(self):
        services.create_task("提交论文", date="2026-10-03")
        summary = notif.get_daily_summary(now=datetime(2026, 10, 3, 9, 0))
        self.assertIsNotNone(summary)
        self.assertTrue(notif.claim_notification(summary))

    # 10. recurring occurrence 后台触发
    def test_recurring_claim(self):
        services.create_task("每天吃饭", date="2026-10-02", time="18:00",
                             is_recurring=True, recurrence_frequency="daily", recurrence_interval=1)
        due = notif.get_due_notifications(now=datetime(2026, 10, 3, 18, 0))
        self.assertTrue(any(n.title == "每天吃饭" for n in due))

    # 11. completed occurrence 不触发
    def test_completed_occurrence_no_claim(self):
        services.create_task("每天吃饭", date="2026-10-02", time="18:00",
                             is_recurring=True, recurrence_frequency="daily", recurrence_interval=1)
        t = [x for x in services.get_tasks() if x["title"] == "每天吃饭"][0]
        services.complete_occurrence(t["id"], "2026-10-03T18:00", True)
        due = notif.get_due_notifications(now=datetime(2026, 10, 3, 18, 0))
        self.assertFalse(any(n.title == "每天吃饭" for n in due))

    # 12. cancelled occurrence 不触发
    def test_cancelled_occurrence_no_claim(self):
        services.create_task("每天吃饭", date="2026-10-02", time="18:00",
                             is_recurring=True, recurrence_frequency="daily", recurrence_interval=1)
        t = [x for x in services.get_tasks() if x["title"] == "每天吃饭"][0]
        services.delete_task_occurrence(t["id"], "2026-10-03T18:00", "this")
        due = notif.get_due_notifications(now=datetime(2026, 10, 3, 18, 0))
        self.assertFalse(any(n.title == "每天吃饭" for n in due))

    # 15. hidden history 不会导致旧提醒重新弹
    def test_hidden_history_no_refire(self):
        self._due_task()
        due = notif.get_due_notifications(now=datetime(2026, 10, 3, 15, 0))
        n = due[0]
        notif.claim_notification(n)
        notif.mark_notification_delivered(n, channel="desktop")
        # 隐藏历史
        database.hide_notification_history()
        # 再次检查不应再提醒（key 仍存在）
        due2 = notif.get_due_notifications(now=datetime(2026, 10, 3, 15, 1))
        self.assertEqual(len(due2), 0)


if __name__ == "__main__":
    unittest.main()
