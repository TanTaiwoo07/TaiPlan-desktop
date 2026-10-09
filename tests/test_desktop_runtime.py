"""桌面化（10D）单元测试。

全部 mock：不启动真实 Streamlit / pywebview / 托盘 / Windows 通知，
不修改真实注册表、Startup Folder 或 todo.db。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from taiplan import desktop_notifier as dn
from taiplan import desktop_runtime as dr
from taiplan import desktop_window as dw
from taiplan import runtime_config
import create_shortcut
from taiplan import notification_service as notif
from taiplan.reminder_worker import ReminderWorker
from taiplan.desktop_notifier import DesktopNotifier, NotificationResult


# ---------------------------------------------------------------
# Runtime 启动流程
# ---------------------------------------------------------------

class StartFlowTest(unittest.TestCase):

    def _common(self):
        return [
            mock.patch.object(dr, "_acquire_single_instance_lock", return_value=mock.Mock()),
            mock.patch.object(dr.database, "init_database"),
            mock.patch.object(dr.appreg, "ensure_app_registered", return_value=True),
            mock.patch.object(dr, "_is_port_in_use", return_value=True),
            mock.patch.object(dr, "_streamlit_is_healthy", return_value=True),
            mock.patch.object(dr.DesktopRuntime, "_start_ipc_server"),
            mock.patch.object(dr.tray_app, "build_tray", return_value=None),
        ]

    def test_normal_start_creates_window_and_runs_webview(self):
        rt = dr.DesktopRuntime(autostart=False, debug=True)
        p = self._common()
        with p[0], p[1], p[2], p[3], p[4], p[5], p[6], \
             mock.patch.object(dr, "ReminderWorker") as MW, \
             mock.patch.object(dr.desktop_window, "create_main_window") as mock_create, \
             mock.patch("webview.start") as mock_start, \
             mock.patch("webbrowser.open") as mock_browser:
            MW.return_value.run = lambda: None
            ok = rt.start()
        self.assertTrue(ok)
        mock_create.assert_called_once()
        self.assertTrue(mock_create.call_args.kwargs.get("show") is True)
        mock_start.assert_called_once()
        # 桌面模式不再自动打开系统浏览器
        mock_browser.assert_not_called()

    def test_autostart_creates_hidden_window(self):
        rt = dr.DesktopRuntime(autostart=True, debug=True)
        p = self._common()
        with p[0], p[1], p[2], p[3], p[4], p[5], p[6], \
             mock.patch.object(dr, "ReminderWorker") as MW, \
             mock.patch.object(dr.desktop_window, "create_main_window") as mock_create, \
             mock.patch("webview.start"), \
             mock.patch("webbrowser.open") as mock_browser:
            MW.return_value.run = lambda: None
            rt.start()
        self.assertFalse(mock_create.call_args.kwargs.get("show"))
        mock_browser.assert_not_called()

    def test_second_instance_signals_and_exits(self):
        rt = dr.DesktopRuntime()
        with mock.patch.object(dr, "_acquire_single_instance_lock", return_value=None), \
             mock.patch.object(dr, "_signal_existing_instance", return_value=True) as mock_sig, \
             mock.patch.object(dr.database, "init_database") as mock_init, \
             mock.patch.object(dr, "ReminderWorker") as MW:
            ok = rt.start()
        self.assertFalse(ok)
        mock_sig.assert_called_once()
        mock_init.assert_not_called()
        MW.assert_not_called()

    def test_streamlit_failure_cleans_up(self):
        rt = dr.DesktopRuntime(autostart=False, debug=True)
        with mock.patch.object(dr, "_acquire_single_instance_lock", return_value=mock.Mock()), \
             mock.patch.object(dr.database, "init_database"), \
             mock.patch.object(dr.appreg, "ensure_app_registered", return_value=True), \
             mock.patch.object(dr, "_is_port_in_use", return_value=False), \
             mock.patch.object(dr, "_streamlit_is_healthy", return_value=False), \
             mock.patch.object(dr.DesktopRuntime, "_start_ipc_server", return_value=True), \
             mock.patch.object(dr, "_start_streamlit", return_value=mock.Mock()), \
             mock.patch.object(dr, "_wait_for_streamlit", return_value=(False, "exit_code=1")), \
             mock.patch.object(dr.DesktopRuntime, "cleanup") as mock_cleanup, \
             mock.patch.object(dr, "_show_error_box") as mock_box, \
             mock.patch.object(dr, "ReminderWorker") as MW:
            ok = rt.start()
        self.assertFalse(ok)
        mock_cleanup.assert_called_once()
        mock_box.assert_called_once()
        MW.assert_not_called()

    def test_wait_for_streamlit_timeout(self):
        fake = mock.Mock()
        fake.poll.return_value = None
        with mock.patch.object(dr, "_streamlit_is_healthy", return_value=False):
            with mock.patch.object(dr.time, "time", side_effect=[0, 0, 31]):
                ok, err = dr._wait_for_streamlit(fake, timeout=30)
        self.assertFalse(ok)
        self.assertEqual(err, "timeout")

    def test_single_instance_lock(self):
        # 使用临时空闲端口，不依赖真实运行实例
        import socket as _socket
        s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        free_port = s.getsockname()[1]
        s.close()
        with mock.patch.object(dr, "LOCK_PORT", free_port):
            s1 = dr._acquire_single_instance_lock()
            self.assertIsNotNone(s1)
            s2 = dr._acquire_single_instance_lock()
            self.assertIsNone(s2)
            s1.close()


# ---------------------------------------------------------------
# 窗口关闭行为
# ---------------------------------------------------------------

class WindowCloseTest(unittest.TestCase):

    def test_close_to_tray_hides_window(self):
        wc = dw.WindowController("http://x")
        wc.window = mock.Mock()
        with mock.patch.object(dw.runtime_config, "load_runtime_config",
                               return_value={"close_to_tray": True}):
            result = wc._on_closing()
        self.assertIs(result, False)   # 阻止关闭
        wc.window.hide.assert_called_once()

    def test_close_fully_allows_close(self):
        wc = dw.WindowController("http://x")
        wc.window = mock.Mock()
        with mock.patch.object(dw.runtime_config, "load_runtime_config",
                               return_value={"close_to_tray": False}):
            result = wc._on_closing()
        self.assertIs(result, True)    # 允许关闭
        wc.window.hide.assert_not_called()

    def test_force_close_allows_close(self):
        wc = dw.WindowController("http://x")
        wc.window = mock.Mock()
        wc._force_close = True
        with mock.patch.object(dw.runtime_config, "load_runtime_config",
                               return_value={"close_to_tray": True}):
            result = wc._on_closing()
        self.assertIs(result, True)

    def test_show_hide_destroy_calls_through(self):
        wc = dw.WindowController("http://x")
        wc.window = mock.Mock()
        wc.show(); wc.window.show.assert_called_once()
        wc.hide(); wc.window.hide.assert_called_once()
        wc.destroy()
        wc.window.destroy.assert_called_once()
        self.assertTrue(wc._force_close)


# ---------------------------------------------------------------
# 通知 provider 链
# ---------------------------------------------------------------

class NotifierChainTest(unittest.TestCase):

    def test_fallback_to_second_provider(self):
        p1 = mock.Mock(); p1.name = "toasted"
        p1.send.return_value = NotificationResult(False, "toasted", "unavailable")
        p2 = mock.Mock(); p2.name = "powershell"
        p2.send.return_value = NotificationResult(True, "powershell")
        n = DesktopNotifier(providers=[p1, p2])
        r = n.notify_task_due("13:30 上高数")
        self.assertTrue(r.success)
        self.assertEqual(r.provider, "powershell")

    def test_all_providers_fail(self):
        p1 = mock.Mock(); p1.name = "a"
        p1.send.return_value = NotificationResult(False, "a", "boom")
        n = DesktopNotifier(providers=[p1])
        r = n.notify("任务到期", "msg")
        self.assertFalse(r.success)
        self.assertEqual(r.error, "boom")

    def test_urgent_heading_decorated(self):
        p = mock.Mock(); p.name = "p"
        p.send.return_value = NotificationResult(True, "p")
        n = DesktopNotifier(providers=[p])
        n.notify_task_due("13:30 提交报告", urgent=True)
        title = p.send.call_args[0][0]
        self.assertIn("🚨", title)
        self.assertIn("紧急任务", title)

    def test_normal_heading_decorated(self):
        p = mock.Mock(); p.name = "p"
        p.send.return_value = NotificationResult(True, "p")
        n = DesktopNotifier(providers=[p])
        n.notify_task_due("13:30 上高数", urgent=False)
        title = p.send.call_args[0][0]
        self.assertIn("⏰", title)
        self.assertIn("任务到期", title)

    def test_raw_title_not_decorated(self):
        p = mock.Mock(); p.name = "p"
        p.send.return_value = NotificationResult(True, "p")
        n = DesktopNotifier(providers=[p])
        n.notify_raw("🔔 Windows 系统提醒测试成功", "body")
        title = p.send.call_args[0][0]
        self.assertEqual(title, "🔔 Windows 系统提醒测试成功")

    def test_provider_exception_is_isolated(self):
        p1 = mock.Mock(); p1.name = "bad"
        p1.send.side_effect = RuntimeError("kaboom")
        p2 = mock.Mock(); p2.name = "good"
        p2.send.return_value = NotificationResult(True, "good")
        n = DesktopNotifier(providers=[p1, p2])
        r = n.notify("h", "m")
        self.assertTrue(r.success)
        self.assertEqual(r.provider, "good")


# ---------------------------------------------------------------
# Worker 投递状态
# ---------------------------------------------------------------

class WorkerDeliveryTest(unittest.TestCase):

    def test_success_marks_delivered(self):
        fake = mock.Mock()
        fake.notify_task_due.return_value = NotificationResult(True, "powershell")
        w = ReminderWorker(notifier=fake)
        n = notif.Notification(key="k1", kind="due", title="t", message="m")
        with mock.patch.object(notif, "mark_notification_delivered") as md, \
             mock.patch.object(notif, "mark_notification_failed") as mf:
            w._send_notification(n)
        md.assert_called_once()
        mf.assert_not_called()

    def test_failure_marks_failed(self):
        fake = mock.Mock()
        fake.notify_task_due.return_value = NotificationResult(False, "powershell", "err")
        w = ReminderWorker(notifier=fake)
        n = notif.Notification(key="k2", kind="due", title="t", message="m")
        with mock.patch.object(notif, "mark_notification_delivered") as md, \
             mock.patch.object(notif, "mark_notification_failed") as mf:
            w._send_notification(n)
        mf.assert_called_once()
        md.assert_not_called()

    def test_daily_summary_uses_summary_api(self):
        fake = mock.Mock()
        fake.notify_daily_summary.return_value = NotificationResult(True, "powershell")
        w = ReminderWorker(notifier=fake)
        n = notif.Notification(key="k3", kind="daily-summary", title="今天有 3 项全天任务", message="x")
        with mock.patch.object(notif, "mark_notification_delivered"):
            w._send_notification(n)
        fake.notify_daily_summary.assert_called_once()


# ---------------------------------------------------------------
# runtime_config / 快捷方式 / 托盘
# ---------------------------------------------------------------

class RuntimeConfigTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.patcher = mock.patch.object(runtime_config, "CONFIG_PATH", Path(self.tmp) / "rc.json")
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_defaults(self):
        cfg = runtime_config.load_runtime_config()
        self.assertTrue(cfg["close_to_tray"])

    def test_round_trip(self):
        runtime_config.save_runtime_config({"close_to_tray": False})
        cfg = runtime_config.load_runtime_config()
        self.assertFalse(cfg["close_to_tray"])


class ShortcutTest(unittest.TestCase):

    def test_command_quotes_paths(self):
        cmd, lnk = create_shortcut.build_shortcut_command(
            target_vbs=r"C:\a b\待办\start_todo_silent.vbs",
            desktop_dir=r"C:\Users\X\Desktop",
        )
        script = cmd[-1]
        self.assertIn("wscript.exe", script)
        self.assertIn(r"C:\a b\待办\start_todo_silent.vbs", script)
        self.assertTrue(str(lnk).endswith("TaiPlan.lnk"))


class TrayTest(unittest.TestCase):

    def test_build_tray_returns_icon(self):
        from taiplan import tray_app
        icon = tray_app.build_tray(on_quit=lambda: None, on_show_window=lambda: None)
        self.assertIsNotNone(icon)

    def test_reminder_menu_label(self):
        from taiplan import tray_app
        with mock.patch.object(tray_app.notif, "load_notification_config",
                               return_value={"enabled": True}):
            self.assertEqual(tray_app._reminder_menu_label(None), "暂停提醒")
        with mock.patch.object(tray_app.notif, "load_notification_config",
                               return_value={"enabled": False}):
            self.assertEqual(tray_app._reminder_menu_label(None), "恢复提醒")


if __name__ == "__main__":
    unittest.main()
