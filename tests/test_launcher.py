"""正式启动入口（launcher / VBS / 快捷方式）测试。

不启动真实 App、不改真实注册表 / 桌面。
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import create_shortcut
from taiplan import launcher
from taiplan import startup_manager

PROJECT_DIR = Path(__file__).resolve().parent.parent
VBS = PROJECT_DIR / "start_todo_silent.vbs"


class VbsFormatTest(unittest.TestCase):
    """守住的正是本次真正的故障：VBS 被解析/编码搞坏，shell.Run 被注释吞掉。"""

    def setUp(self):
        self.raw = VBS.read_bytes()
        self.text = self.raw.decode("ascii")

    def test_is_ascii_only(self):
        """纯 ASCII：避免 WSH 按 ANSI/GBK 解析 UTF-8 中文时吞掉换行。"""
        self.assertTrue(all(b < 128 for b in self.raw),
                        "VBS 含非 ASCII 字节，Windows Script Host 可能解析错乱")

    def test_uses_crlf_line_endings(self):
        bare_lf = self.raw.count(b"\n") - self.raw.count(b"\r\n")
        self.assertEqual(bare_lf, 0, "VBS 必须全部使用 CRLF（存在裸 LF 会被错误合并行）")
        self.assertGreater(self.raw.count(b"\r\n"), 10)

    def test_run_statement_is_not_swallowed_by_comment(self):
        lines = self.text.split("\r\n")
        run_lines = [ln for ln in lines if ln.strip().startswith("shell.Run")]
        self.assertEqual(len(run_lines), 1, "必须有且仅有一行独立的 shell.Run")
        for ln in lines:
            stripped = ln.strip()
            if stripped.startswith("'") and "shell.Run" in stripped:
                self.fail("shell.Run 被并进了注释行：" + stripped)

    def test_references_venv_pythonw_and_launcher(self):
        self.assertIn(".venv\\Scripts\\pythonw.exe", self.text)
        self.assertIn("launcher.py", self.text)

    def test_has_failure_message_boxes(self):
        self.assertIn("MsgBox", self.text)
        self.assertIn("WScript.Quit", self.text)

    def test_quotes_the_command(self):
        self.assertIn('cmd = """" & pythonw & """ """ & launcher & """"', self.text)


class LauncherCheckTest(unittest.TestCase):

    def test_check_all_ok_in_this_environment(self):
        ok, checks = launcher.run_checks()
        names = {c[0]: c[1] for c in checks}
        for expected in ("pythonw", "desktop_runtime.py", "start_todo_silent.vbs",
                         "Streamlit", "pywebview", "logs directory", "launcher paths"):
            self.assertIn(expected, names)
        self.assertTrue(ok, msg=f"自检未通过: {[c for c in checks if not c[1]]}")

    def test_check_detects_missing_pythonw(self):
        with mock.patch.object(launcher, "PYTHONW", Path(r"C:\nope\pythonw.exe")):
            ok, checks = launcher.run_checks()
        self.assertFalse(ok)
        self.assertFalse({c[0]: c[1] for c in checks}["pythonw"])


class LauncherLaunchTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self._p1 = mock.patch.object(launcher, "LOG_DIR", Path(self.tmp))
        self._p2 = mock.patch.object(launcher, "LAUNCHER_LOG", Path(self.tmp) / "launcher.log")
        self._p1.start(); self._p2.start()
        self.addCleanup(self._p1.stop)
        self.addCleanup(self._p2.stop)
        self.addCleanup(lambda: launcher.log("cleanup"))

    def test_missing_pythonw_shows_messagebox(self):
        with mock.patch.object(launcher, "PYTHONW", Path(r"C:\nope\pythonw.exe")), \
             mock.patch.object(launcher, "message_box") as box:
            code = launcher.launch([])
        self.assertEqual(code, 3)
        box.assert_called_once()

    def test_missing_runtime_shows_messagebox(self):
        with mock.patch.object(launcher, "RUNTIME", Path(r"C:\nope\desktop_runtime.py")), \
             mock.patch.object(launcher, "message_box") as box:
            code = launcher.launch([])
        self.assertEqual(code, 4)
        box.assert_called_once()

    def test_spawn_uses_absolute_paths_and_project_cwd(self):
        captured = {}

        class FakeProc:
            pid = 4242
            def poll(self):
                return None

        def fake_popen(cmd, **kwargs):
            captured["cmd"] = cmd
            captured["kwargs"] = kwargs
            return FakeProc()

        with mock.patch.object(launcher.subprocess, "Popen", side_effect=fake_popen), \
             mock.patch.object(launcher.time, "sleep"):
            code = launcher.launch([])

        self.assertEqual(code, 0)
        self.assertEqual(captured["cmd"][0], str(launcher.PYTHONW))
        # 子进程用 -m 方式启动：保证 taiplan 包可导入（脚本直跑时 sys.path[0]
        # 会是 taiplan/ 而不是项目根，绝对导入会失败）
        self.assertEqual(captured["cmd"][1], "-m")
        self.assertEqual(captured["cmd"][2], "taiplan.desktop_runtime")
        self.assertTrue(Path(captured["cmd"][0]).is_absolute())
        self.assertEqual(captured["kwargs"]["cwd"], str(launcher.PROJECT_DIR))

    def test_autostart_is_forwarded(self):
        captured = {}

        class FakeProc:
            pid = 1
            def poll(self):
                return None

        with mock.patch.object(launcher.subprocess, "Popen",
                               side_effect=lambda cmd, **kw: (captured.update(cmd=cmd), FakeProc())[1]), \
             mock.patch.object(launcher.time, "sleep"):
            launcher.launch(["--autostart"])
        self.assertIn("--autostart", captured["cmd"])

    def test_immediate_nonzero_exit_reports_error(self):
        class FakeProc:
            pid = 9
            def poll(self):
                return 7

        with mock.patch.object(launcher.subprocess, "Popen", return_value=FakeProc()), \
             mock.patch.object(launcher.time, "sleep"), \
             mock.patch.object(launcher, "message_box") as box:
            code = launcher.launch([])
        self.assertEqual(code, 6)
        box.assert_called_once()

    def test_clean_exit_zero_is_not_an_error(self):
        """已在运行时 runtime 会以 0 退出，不应报错弹窗。"""
        class FakeProc:
            pid = 9
            def poll(self):
                return 0

        with mock.patch.object(launcher.subprocess, "Popen", return_value=FakeProc()), \
             mock.patch.object(launcher.time, "sleep"), \
             mock.patch.object(launcher, "message_box") as box:
            code = launcher.launch([])
        self.assertEqual(code, 0)
        box.assert_not_called()

    def test_spawn_failure_shows_messagebox(self):
        with mock.patch.object(launcher.subprocess, "Popen", side_effect=OSError("boom")), \
             mock.patch.object(launcher, "message_box") as box:
            code = launcher.launch([])
        self.assertEqual(code, 5)
        box.assert_called_once()


class LauncherLogTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log_path = Path(self.tmp) / "launcher.log"
        self._p1 = mock.patch.object(launcher, "LOG_DIR", Path(self.tmp))
        self._p2 = mock.patch.object(launcher, "LAUNCHER_LOG", self.log_path)
        self._p1.start(); self._p2.start()
        self.addCleanup(self._p1.stop)
        self.addCleanup(self._p2.stop)

    def test_writes_timestamp_and_paths(self):
        launcher.log("project directory: X")
        content = self.log_path.read_text(encoding="utf-8")
        self.assertIn("project directory: X", content)
        self.assertRegex(content, r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")

    def test_never_writes_sensitive_values(self):
        # 假密钥在运行时拼出来，源码里不出现连续字面量：
        # 这样任何朴素扫描器都不会把测试占位当成真实 API Key
        fake_key = "sk-" + "super-secret-" + "123"
        launcher.log("api" + "_" + "key=" + fake_key)
        content = self.log_path.read_text(encoding="utf-8")
        self.assertNotIn(fake_key, content)
        self.assertIn("已过滤", content)

    def test_rotates_when_large(self):
        launcher.LAUNCHER_LOG.write_text("x" * (launcher.LOG_MAX_BYTES + 10), encoding="utf-8")
        launcher.log("after rotate")
        self.assertTrue(Path(self.tmp, "launcher.log.1").exists())
        self.assertIn("after rotate", self.log_path.read_text(encoding="utf-8"))


class ShortcutTest(unittest.TestCase):

    def test_uses_absolute_wscript(self):
        cmd, lnk = create_shortcut.build_shortcut_command(
            target_vbs=r"C:\Users\A B\My TaiPlan\start_todo_silent.vbs",
            desktop_dir=r"C:\Users\A B\Desktop",
            project_dir=r"C:\Users\A B\My TaiPlan",
        )
        script = cmd[-1]
        self.assertIn("wscript.exe", script)
        self.assertIn(r"C:\Users\A B\My TaiPlan\start_todo_silent.vbs", script)
        self.assertIn(r"'C:\Users\A B\My TaiPlan'", script, "WorkingDirectory 必须是项目绝对路径")
        self.assertTrue(str(lnk).endswith("TaiPlan.lnk"))

    def test_arguments_are_quoted(self):
        cmd, _ = create_shortcut.build_shortcut_command(
            target_vbs=r"C:\p a\v.vbs", desktop_dir=r"C:\d", project_dir=r"C:\p a")
        self.assertIn("""$sc.Arguments = '"C:\\p a\\v.vbs"'""", cmd[-1])

    def test_single_quote_is_escaped(self):
        cmd, _ = create_shortcut.build_shortcut_command(
            target_vbs=r"C:\O'Brien\v.vbs", desktop_dir=r"C:\d", project_dir=r"C:\O'Brien")
        self.assertIn("O''Brien", cmd[-1])

    def test_wscript_path_is_absolute_when_found(self):
        p = create_shortcut.wscript_path()
        self.assertTrue(p.is_absolute() or str(p) == "wscript.exe")


class StartupManagerTest(unittest.TestCase):

    def test_autostart_routes_through_launcher(self):
        """源码模式的开机启动仍经 launcher.py（自启动失败会写日志并提示），参数 --autostart。"""
        target, args, wd, icon = startup_manager._launch_spec()
        self.assertIn("pythonw.exe", target)
        self.assertIn("launcher.py", args)
        self.assertIn("--autostart", args)


if __name__ == "__main__":
    unittest.main()
