# -*- coding: utf-8 -*-
"""17.2 ownership / orphan 单测（文档第 12 条 a-j）。

全部用临时 state 文件，绝不触碰真实 %LOCALAPPDATA%\\TaiPlan\\state。
"""

import io
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import desktop_runtime
import desktop_window
import dynamic_port

ROOT = Path(__file__).resolve().parent.parent


class PublishedStateTest(unittest.TestCase):
    """a/b/c：state 写真实 child pid、read_published_state、ownership token。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "runtime_port.json"
        patcher = mock.patch.object(dynamic_port, "_state_path", return_value=self.state)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_publish_writes_real_child_pid_and_ownership(self):
        payload = dynamic_port.publish_port(8501, component="streamlit", pid=4321,
                                            parent_pid=1111, owner_token="tok-A")
        self.assertEqual(payload["pid"], 4321)
        self.assertEqual(payload["parent_pid"], 1111)
        self.assertEqual(payload["owner_token"], "tok-A")
        self.assertTrue(dynamic_port.publish_port.__doc__)

    def test_read_published_state_returns_all_fields(self):
        dynamic_port.publish_port(8502, component="streamlit", pid=4321,
                                  parent_pid=1111, owner_token="tok-A")
        state = dynamic_port.read_published_state()
        for key in ("port", "component", "pid", "parent_pid", "owner_token", "published_at"):
            self.assertIn(key, state)
        self.assertEqual(state["port"], 8502)
        self.assertEqual(state["pid"], 4321)
        self.assertEqual(state["owner_token"], "tok-A")
        self.assertIsNotNone(state["published_at"])

    def test_read_published_port_is_compat_wrapper(self):
        dynamic_port.publish_port(8503, pid=1, owner_token="t")
        self.assertEqual(dynamic_port.read_published_port(), 8503)
        dynamic_port.clear_published_port()
        self.assertIsNone(dynamic_port.read_published_port())

    def test_pid_is_real_not_none(self):
        """不能继续 pid=None（文档第 2 条）。"""
        dynamic_port.publish_port(8501, pid=999, parent_pid=1, owner_token="t")
        self.assertEqual(dynamic_port.read_published_state()["pid"], 999)

    def test_invalid_port_returns_none(self):
        self.state.write_text('{"port": "abc"}', encoding="utf-8")
        self.assertIsNone(dynamic_port.read_published_state())

    def test_owner_token_matching(self):
        state = {"pid": 10, "owner_token": "tok-A"}
        self.assertTrue(dynamic_port.state_owner_matches(state, pid=10, owner_token="tok-A"))
        self.assertTrue(dynamic_port.state_owner_matches(state, pid="10"))
        self.assertFalse(dynamic_port.state_owner_matches(state, pid=11))
        self.assertFalse(dynamic_port.state_owner_matches(state, owner_token="tok-B"))
        self.assertFalse(dynamic_port.state_owner_matches(None, pid=10))

    def test_state_expiry_is_real(self):
        """文档第 7 条：要么真做 stale 检查，要么删掉承诺 —— 这里真的做了。"""
        self.state.write_text(
            '{"port": 8501, "published_at": "2000-01-01T00:00:00"}', encoding="utf-8")
        state = dynamic_port.read_published_state()
        self.assertTrue(dynamic_port.state_is_expired(state))
        dynamic_port.publish_port(8501, pid=1, owner_token="t")
        self.assertFalse(dynamic_port.state_is_expired(dynamic_port.read_published_state()))


class CompareAndDeleteTest(unittest.TestCase):
    """d / ownership race：旧 child 绝不能删掉新 owner 的 state。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = Path(self.tmp.name) / "runtime_port.json"
        patcher = mock.patch.object(dynamic_port, "_state_path", return_value=self.state)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_old_child_cannot_delete_new_owner_state(self):
        # 新 Runtime 已经发布了新 state
        dynamic_port.publish_port(8501, pid=2222, parent_pid=999,
                                  owner_token="new-token")
        # 旧 child（pid=1111, token=old-token）想清 state
        removed = dynamic_port.clear_published_state(pid=1111, owner_token="old-token")
        self.assertFalse(removed)
        self.assertIsNotNone(dynamic_port.read_published_state())
        self.assertEqual(dynamic_port.read_published_state()["pid"], 2222)

    def test_owner_can_delete_own_state(self):
        dynamic_port.publish_port(8501, pid=1111, parent_pid=999, owner_token="old-token")
        self.assertTrue(dynamic_port.clear_published_state(pid=1111,
                                                           owner_token="old-token"))
        self.assertIsNone(dynamic_port.read_published_state())

    def test_missing_state_delete_is_false(self):
        self.assertFalse(dynamic_port.clear_published_state(pid=1, owner_token="x"))


class NoUnownedReuseTest(unittest.TestCase):
    """e：拿到 runtime LOCK 后绝不复用仍然健康的旧 Streamlit。"""

    def test_source_has_no_reuse_path(self):
        src = io.open(ROOT / "desktop_runtime.py", encoding="utf-8").read()
        code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
        self.assertNotIn("Reusing existing Streamlit", code)
        self.assertNotIn("reused = True", code)
        self.assertIn("禁止无所有权复用", src)

    def test_start_fails_closed_when_stale_streamlit_stays_healthy(self):
        rt = desktop_runtime.DesktopRuntime()
        with mock.patch.object(desktop_runtime, "_acquire_single_instance_lock",
                               return_value=mock.Mock()), \
                mock.patch.object(rt, "_start_ipc_server", return_value=True), \
                mock.patch.object(dynamic_port, "read_published_state",
                                  return_value={"port": 8501, "pid": 4242,
                                                "parent_pid": 1, "owner_token": "x",
                                                "component": "streamlit",
                                                "published_at": None}), \
                mock.patch.object(desktop_runtime, "_streamlit_is_healthy",
                                  return_value=True), \
                mock.patch.object(rt, "cleanup") as cleanup, \
                mock.patch.object(desktop_runtime, "_start_streamlit") as starter, \
                mock.patch.object(dynamic_port, "clear_published_state") as clearer, \
                mock.patch("database.init_database"), \
                mock.patch("first_run.run_first_run"), \
                mock.patch.object(desktop_runtime, "appreg"):
            ok = rt.start()
        self.assertFalse(ok)
        starter.assert_not_called()            # 不复用，也不启新的
        clearer.assert_not_called()            # 没退出就不清 state
        cleanup.assert_called_once()
        self.assertIn("残留的后台服务", rt.start_error or "")


class ShutdownSecondGuardTest(unittest.TestCase):
    """f/g：LOCK 空闲时仍要检查 published state。"""

    def _run(self, state, healthy):
        with mock.patch.object(desktop_runtime, "is_runtime_lock_held", return_value=False), \
                mock.patch.object(dynamic_port, "read_published_state", return_value=state), \
                mock.patch.object(desktop_runtime, "_streamlit_is_healthy",
                                  return_value=healthy), \
                mock.patch.object(dynamic_port, "clear_published_state") as clearer:
            rc = desktop_runtime.request_shutdown(timeout=1)
        return rc, clearer

    def test_lock_free_no_state_returns_zero(self):
        rc, _ = self._run(None, healthy=False)
        self.assertEqual(rc, 0)

    def test_lock_free_with_healthy_owned_streamlit_returns_one(self):
        rc, _ = self._run({"port": 8501, "pid": 4242, "owner_token": "tok",
                           "component": "streamlit"}, healthy=True)
        self.assertEqual(rc, 1)

    def test_lock_free_with_dead_streamlit_clears_state_and_returns_zero(self):
        rc, clearer = self._run({"port": 8501, "pid": 4242, "owner_token": "tok",
                                 "component": "streamlit"}, healthy=False)
        self.assertEqual(rc, 0)
        clearer.assert_called_once()

    def test_never_kills_unknown_pid(self):
        """只做判断，绝不杀未知进程（剥掉注释后检查代码本体）。"""
        src = io.open(ROOT / "desktop_runtime.py", encoding="utf-8").read()
        code = "\n".join(l for l in src.splitlines() if not l.strip().startswith("#"))
        code = re.sub(r"#[^\n]*", "", code)
        for bad in ("taskkill", "TerminateProcess", "os.kill"):
            self.assertNotIn(bad, code, f"出现强杀手段：{bad}")


class ErrorBoxPolicyTest(unittest.TestCase):
    """h/i：autostart 等无人值守模式绝不弹模态框；人工启动仍可弹。"""

    def test_unattended_modes_never_show_box(self):
        for flag in ("--autostart", "--shutdown", "--streamlit-child",
                     "--smoke-test", "--benchmark"):
            self.assertFalse(desktop_runtime.should_show_error_box([flag]),
                             f"{flag} 不该弹错误框")
            self.assertFalse(
                desktop_runtime.should_show_error_box(["--debug", flag]),
                f"--debug {flag} 不该弹错误框")

    def test_manual_launch_may_show_box(self):
        self.assertTrue(desktop_runtime.should_show_error_box([]))
        self.assertTrue(desktop_runtime.should_show_error_box(["--debug"]))

    def test_unattended_failure_writes_runtime_log(self):
        """无人值守失败必须写 runtime logger（Release EXE 无 console，stdout 不可见）。"""
        src = io.open(ROOT / "desktop_runtime.py", encoding="utf-8").read()
        start = src.find("if should_show_error_box(args):")
        self.assertGreater(start, 0)
        first = src[start:src.find("raise SystemExit(2)")]
        self.assertIn("_logger.error", first, "启动失败分支必须写 logger")
        self.assertIn("无人值守模式", first)
        second_start = src.find("if should_show_error_box(args):", start + 10)
        second = src[second_start:src.find("raise SystemExit(1) from exc")]
        self.assertIn("_logger.error", second, "异常分支必须写 logger")
        # 不重复刷屏：每个分支只写一次 logger.error
        self.assertEqual(first.count("_logger.error"), 1)
        self.assertEqual(second.count("_logger.error"), 1)

    def test_main_guards_both_failure_paths(self):
        src = io.open(ROOT / "desktop_runtime.py", encoding="utf-8").read()
        self.assertEqual(src.count("should_show_error_box(args)"), 2)


class WindowDestroyTest(unittest.TestCase):
    """j：destroy 异常必须返回 False 并记录，不得假装成功。"""

    def test_destroy_without_window_is_true(self):
        ctrl = desktop_window.WindowController.__new__(desktop_window.WindowController)
        ctrl.window = None
        ctrl._force_close = False
        self.assertTrue(ctrl.destroy())

    def test_destroy_exception_returns_false(self):
        class Boom:
            def destroy(self):
                raise RuntimeError("bing boom")

        ctrl = desktop_window.WindowController.__new__(desktop_window.WindowController)
        ctrl.window = Boom()
        ctrl._force_close = False
        with self.assertLogs("todo.window", level="ERROR") as captured:
            result = ctrl.destroy()
        self.assertFalse(result)
        self.assertIn("destroy 失败", "\n".join(captured.output))

    def test_destroy_success_returns_true(self):
        class Ok:
            def destroy(self):
                return None

        ctrl = desktop_window.WindowController.__new__(desktop_window.WindowController)
        ctrl.window = Ok()
        ctrl._force_close = False
        self.assertTrue(ctrl.destroy())

    def test_close_main_window_returns_bool(self):
        self.assertTrue(desktop_window.close_main_window())   # 没有 controller
        with mock.patch.object(desktop_window, "_controller", None):
            self.assertTrue(desktop_window.close_main_window())

    def test_shutdown_does_not_claim_success_when_close_fails(self):
        rt = desktop_runtime.DesktopRuntime()
        rt._webview_started = False
        with mock.patch.object(desktop_window, "close_main_window", return_value=False), \
                mock.patch.object(rt, "cleanup") as cleanup:
            rt.shutdown()
        cleanup.assert_not_called()          # 不收尾 → LOCK 不释放 → --shutdown 超时 exit 1
        self.assertFalse(rt._shutting_down)  # 允许用户重试关闭


if __name__ == "__main__":
    unittest.main()
