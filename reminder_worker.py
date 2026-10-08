"""独立后台 Reminder Worker（不依赖 Streamlit）。

循环检查到期任务，调用 notification_service，
通过 DesktopNotifier 发送 Windows 系统通知。

通过 threading.Event 支持 graceful shutdown。
"""

import logging
import os
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

import database
import dynamic_port
import notification_service as notif
from desktop_notifier import DesktopNotifier

import app_paths

PROJECT_DIR = app_paths.get_project_root()
LOG_DIR = app_paths.get_logs_dir()

CHECK_INTERVAL = 30  # 秒
HEARTBEAT_INTERVAL = 15  # 秒
MAX_RETRY = 3

_logger = logging.getLogger("reminder_worker")


def _setup_logging():
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_DIR / "reminder_worker.log", maxBytes=1_000_000, backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False


class ReminderWorker:
    def __init__(self, notifier=None, check_interval=CHECK_INTERVAL):
        self.stop_event = threading.Event()
        self.notifier = notifier or DesktopNotifier()
        self.check_interval = check_interval
        self.pid = os.getpid()

    def _heartbeat(self):
        database.update_runtime_status(
            "reminder_worker", pid=self.pid, status="running")

    def _send_notification(self, n):
        """发送一条通知，成功/失败都更新投递状态。"""
        url = dynamic_port.published_url()
        try:
            if n.kind == "daily-summary":
                res = self.notifier.notify_daily_summary(n.title, n.message, url=url)
            else:
                res = self.notifier.notify_task_due(n.message, urgent=n.urgent, url=url)
            if res.success:
                notif.mark_notification_delivered(n, channel=res.provider)
                _logger.info("delivered key=%s provider=%s", n.key, res.provider)
            else:
                notif.mark_notification_failed(n, res.error or "unknown", channel=res.provider)
                _logger.warning("notification failed key=%s provider=%s err=%s",
                                n.key, res.provider, (res.error or "")[:200])
        except Exception as e:
            notif.mark_notification_failed(n, str(e), channel="desktop")
            _logger.warning("notification error key=%s err=%s", n.key, str(e)[:200])

    def _check_once(self):
        cfg = notif.load_notification_config()
        if not cfg.get("enabled", True):
            return

        due = notif.get_due_notifications(config=cfg)
        for n in due:
            # 先原子 claim，只有成功才发送
            if not notif.claim_notification(n):
                continue
            self._send_notification(n)

        summary = notif.get_daily_summary(config=cfg)
        if summary is not None and notif.claim_notification(summary):
            self._send_notification(summary)

    def _retry_failed(self):
        """有限重试发送失败的提醒。"""
        for row in database.get_claimable_failed_notifications(max_retry=MAX_RETRY):
            key = row["notification_key"]
            n = notif.Notification(
                key=key,
                kind=row["notification_type"],
                title=row["title_snapshot"] or "",
                task_id=row["task_id"],
                occurrence_key=row["occurrence_key"],
                priority=row["priority_snapshot"] or "normal",
                urgent=(row["priority_snapshot"] == "urgent"),
                message=f"{row.get('due_at') or ''} {row['title_snapshot'] or ''}".strip(),
            )
            self._send_notification(n)

    def run(self):
        _setup_logging()
        _logger.info("worker started pid=%s", self.pid)
        database.init_database()
        self._heartbeat()

        next_heartbeat = datetime.now()

        while not self.stop_event.wait(self.check_interval):
            try:
                self._check_once()
                self._retry_failed()
            except Exception as e:
                _logger.warning("check error: %s", str(e)[:200])

            # heartbeat 每 15~30 秒更新
            if (datetime.now() - next_heartbeat).total_seconds() >= HEARTBEAT_INTERVAL:
                try:
                    self._heartbeat()
                except Exception:
                    pass
                next_heartbeat = datetime.now()

        # 退出时清理状态
        try:
            database.delete_runtime_status("reminder_worker")
        except Exception:
            pass
        _logger.info("worker stopped")

    def stop(self):
        self.stop_event.set()


def main():
    worker = ReminderWorker()
    try:
        worker.run()
    except KeyboardInterrupt:
        worker.stop()


if __name__ == "__main__":
    main()
