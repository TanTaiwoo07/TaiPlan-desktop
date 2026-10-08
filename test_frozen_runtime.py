# -*- coding: utf-8 -*-
"""第 15 阶段：frozen 运行时的单元测试。

覆盖文档第 47 节列出的项：source/frozen 判定、两种 Streamlit 命令、
child 不得进入桌面运行时、child 清理、smoke test、frozen 资源路径、
用户数据路径不变、凭据身份不变、AUMID 不变、动态端口传递。
"""
import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tests_env  # noqa: F401  必须最先导入（隔离真实数据目录）

import app_paths
import desktop_runtime
import dynamic_port
import frozen_smoke
import main as app_main
import streamlit_runner
import windows_app_registration


class SourceFrozenDetectionTest(unittest.TestCase):
    def test_source_mode_is_not_frozen(self):
        self.assertFalse(streamlit_runner.is_frozen())
        self.assertFalse(app_paths.is_frozen())

    def test_mocked_frozen_true(self):
        with mock.patch.object(app_paths, "is_frozen", return_value=True):
            self.assertTrue(streamlit_runner.is_frozen())


class StreamlitCommandTest(unittest.TestCase):
    """源码用 `-m streamlit`，frozen 必须换成 --streamlit-child。"""

    def test_source_command_uses_python_m_streamlit(self):
        with mock.patch.object(streamlit_runner, "is_frozen", return_value=False):
            cmd = streamlit_runner.streamlit_command(8501)
        self.assertEqual(cmd[0], sys.executable)
        self.assertEqual(cmd[1:4], ["-m", "streamlit", "run"])
        self.assertIn("--server.port", cmd)
        self.assertIn("8501", cmd)
        self.assertIn("--server.headless", cmd)
        self.assertIn("--browser.gatherUsageStats", cmd)
        self.assertNotIn("--streamlit-child", cmd)

    def test_frozen_command_uses_own_exe_child_mode(self):
        with mock.patch.object(streamlit_runner, "is_frozen", return_value=True):
            cmd = streamlit_runner.streamlit_command(8507)
        self.assertEqual(cmd, [sys.executable, "--streamlit-child", "--port", "8507"])
        # frozen 下绝不能再出现 -m streamlit
        self.assertNotIn("-m", cmd)
        self.assertNotIn("streamlit", cmd)
        self.assertNotIn("run", cmd)

    def test_frozen_command_never_contains_dash_m_streamlit(self):
        for port in (8501, 8502, 8510):
            with mock.patch.object(streamlit_runner, "is_frozen", return_value=True):
                joined = " ".join(streamlit_runner.streamlit_command(port))
            self.assertNotIn("-m streamlit", joined)
            self.assertNotIn("run app.py", joined)

    def test_child_flag_options(self):
        opts = streamlit_runner.child_flag_options(8600)
        self.assertIs(opts["server.headless"], True)
        self.assertEqual(opts["server.port"], 8600)
        self.assertIs(opts["browser.gatherUsageStats"], False)

    def test_child_disables_development_mode(self):
        """打包后 streamlit/__file__ 不含 site-packages，Streamlit 会把
        developmentMode 判成 True，而它与 server.port 互斥 → 必须显式关掉。"""
        for port in (8501, 8507):
            opts = streamlit_runner.child_flag_options(port)
            self.assertIs(opts["global.developmentMode"], False,
                          f"port={port} 未关闭 developmentMode，frozen 下会启动失败")


class ChildEnvMarkerTest(unittest.TestCase):
    def test_child_marker_injected(self):
        env = streamlit_runner.inject_child_marker({"FOO": "bar"})
        self.assertEqual(env[streamlit_runner.CHILD_ENV], "1")
        self.assertEqual(env["FOO"], "bar")

    def test_child_marker_does_not_mutate_os_environ(self):
        before = os.environ.get(streamlit_runner.CHILD_ENV)
        streamlit_runner.inject_child_marker({})
        self.assertEqual(os.environ.get(streamlit_runner.CHILD_ENV), before)


class MainDispatchTest(unittest.TestCase):
    """main.py 的模式分发与防呆。"""

    def tearDown(self):
        os.environ.pop(app_main.CHILD_ENV, None)

    def test_parse_port(self):
        self.assertEqual(app_main.parse_port(["--port", "8599"]), 8599)
        self.assertEqual(app_main.parse_port(["--port=8600"]), 8600)
        self.assertEqual(app_main.parse_port(["--port", "x"], default=8501), 8501)
        self.assertEqual(app_main.parse_port([], default=8501), 8501)

    def test_child_mode_does_not_start_desktop_runtime(self):
        with mock.patch.object(streamlit_runner, "run_streamlit_child",
                               return_value=0) as child, \
             mock.patch.object(desktop_runtime, "main",
                               side_effect=AssertionError("child 不得启动桌面运行时")):
            rc = app_main.main(["--streamlit-child", "--port", "8601"])
        self.assertEqual(rc, 0)
        child.assert_called_once_with(8601)

    def test_smoke_test_mode_does_not_start_anything(self):
        with mock.patch.object(frozen_smoke, "run_smoke_test", return_value=0) as smoke, \
             mock.patch.object(desktop_runtime, "main",
                               side_effect=AssertionError("smoke-test 不得启动桌面运行时")), \
             mock.patch.object(streamlit_runner, "run_streamlit_child",
                               side_effect=AssertionError("smoke-test 不得启动 Streamlit")):
            rc = app_main.main(["--smoke-test"])
        self.assertEqual(rc, 0)
        smoke.assert_called_once()

    def test_default_mode_starts_desktop_runtime(self):
        with mock.patch.object(desktop_runtime, "main") as runtime:
            rc = app_main.main([])
        self.assertEqual(rc, 0)
        runtime.assert_called_once()

    def test_child_env_refuses_desktop_runtime(self):
        os.environ[app_main.CHILD_ENV] = "1"
        with mock.patch.object(desktop_runtime, "main",
                               side_effect=AssertionError("带 child 标记时不得启动运行时")):
            rc = app_main.main([])
        self.assertEqual(rc, 2)

    def test_desktop_runtime_main_refuses_when_child_marked(self):
        os.environ[app_main.CHILD_ENV] = "1"
        try:
            with mock.patch.object(desktop_runtime, "DesktopRuntime",
                                   side_effect=AssertionError("不得构造 DesktopRuntime")):
                desktop_runtime.main()
        finally:
            os.environ.pop(app_main.CHILD_ENV, None)


class ChildProcessLifecycleTest(unittest.TestCase):
    def test_start_streamlit_subprocess_uses_child_command(self):
        proc = None
        try:
            with mock.patch.object(streamlit_runner, "is_frozen", return_value=True):
                proc = streamlit_runner.start_streamlit_subprocess(
                    8610, log_path=Path(tempfile.mkdtemp()) / "streamlit.log")
            # 用一个必然快速失败的命令来验证环境变量与参数，而不是真的起服务
            self.assertEqual(proc.args[0], sys.executable)
            self.assertIn("--streamlit-child", proc.args)
            self.assertIn("8610", proc.args)
            self.assertEqual(proc.args[proc.args.index("--port") + 1], "8610")
        finally:
            if proc is not None:
                streamlit_runner.stop_streamlit_subprocess(proc)

    def test_stop_streamlit_subprocess_is_idempotent(self):
        log_dir = Path(tempfile.mkdtemp())
        with mock.patch.object(streamlit_runner, "is_frozen", return_value=False):
            with mock.patch.object(streamlit_runner.subprocess, "Popen") as popen:
                fake = popen.return_value
                fake.poll.return_value = None
                fake.args = ["x"]
                proc = streamlit_runner.start_streamlit_subprocess(
                    8611, log_path=log_dir / "s.log")
        streamlit_runner.stop_streamlit_subprocess(proc)
        streamlit_runner.stop_streamlit_subprocess(proc)
        fake.terminate.assert_called_once()

    def test_child_cleanup_terminates_real_process(self):
        # 用一个真实但无害的子进程验证 terminate + 句柄关闭
        dummy = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log_dir = Path(tempfile.mkdtemp())
        log_path = log_dir / "child.log"
        dummy._todo_log_file = open(log_path, "ab", buffering=0)  # noqa: SLF001
        streamlit_runner.stop_streamlit_subprocess(dummy)
        self.assertIsNotNone(dummy.poll())
        self.assertTrue(dummy._todo_log_file.closed)  # noqa: SLF001


class FrozenPathTest(unittest.TestCase):
    def test_frozen_resource_path_uses_meipass(self):
        fake_bundle = Path(tempfile.mkdtemp(prefix="fake_bundle_"))
        (fake_bundle / "assets").mkdir(exist_ok=True)
        with mock.patch.object(app_paths, "is_frozen", return_value=True), \
             mock.patch.object(sys, "_MEIPASS", str(fake_bundle), create=True):
            self.assertEqual(app_paths.get_resource_dir(), fake_bundle)
            self.assertEqual(app_paths.resource_path("app.py"),
                             fake_bundle / "app.py")
            self.assertEqual(app_paths.get_assets_dir(), fake_bundle / "assets")

    def test_frozen_project_root_is_exe_dir(self):
        exe_dir = Path(tempfile.mkdtemp(prefix="fake_exe_"))
        with mock.patch.object(app_paths, "is_frozen", return_value=True), \
             mock.patch.object(sys, "executable", str(exe_dir / "TaiPlan.exe")):
            self.assertEqual(app_paths.get_project_root(), exe_dir)

    def test_user_data_path_unchanged_under_frozen(self):
        with mock.patch.object(app_paths, "is_frozen", return_value=True), \
             mock.patch.object(sys, "_MEIPASS", tempfile.mkdtemp(), create=True):
            data_dir = Path(app_paths.get_user_data_dir())
        self.assertTrue(data_dir.is_absolute())
        # 必须仍在隔离出来的测试数据目录里，绝不落在程序目录
        self.assertEqual(data_dir, Path(os.environ["TODO_APP_DATA_DIR"]))

    def test_data_dir_override_flag(self):
        self.assertTrue(app_paths.is_data_dir_overridden())


class StableIdentityTest(unittest.TestCase):
    """frozen 不得改变凭据身份与 AUMID（否则用户要重新输 Key、通知失效）。"""

    def test_app_user_model_id_unchanged(self):
        self.assertEqual(windows_app_registration.APP_USER_MODEL_ID, "TaiWoo.TaiPlan",
                         "W4 AUMID")
        self.assertEqual(windows_app_registration.LEGACY_APP_USER_MODEL_ID,
                         "TodoApp.Desktop")

    def test_credential_id_stable_across_frozen(self):
        import ai_settings

        args = ("openai_responses", "https://api.deepseek.com", "deepseek-flash")
        normal = ai_settings.make_credential_id(*args)
        with mock.patch.object(app_paths, "is_frozen", return_value=True):
            frozen = ai_settings.make_credential_id(*args)
        self.assertEqual(normal, frozen)
        self.assertTrue(normal.startswith("openai_responses|"))

    def test_credential_id_differs_per_provider(self):
        import ai_settings

        a = ai_settings.make_credential_id("openai_responses", "https://api.deepseek.com", "m")
        b = ai_settings.make_credential_id("anthropic", "https://api.anthropic.com", "m")
        self.assertNotEqual(a, b)


class DynamicPortPropagationTest(unittest.TestCase):
    def test_port_reaches_child_command(self):
        port = dynamic_port.find_available_port()
        self.assertTrue(dynamic_port.DEFAULT_PORT <= port <= 8510)
        with mock.patch.object(streamlit_runner, "is_frozen", return_value=True):
            cmd = streamlit_runner.streamlit_command(port)
        self.assertEqual(cmd[cmd.index("--port") + 1], str(port))

    def test_url_and_health_url_follow_port(self):
        runtime_port = desktop_runtime.get_streamlit_port()
        self.assertIn(str(runtime_port), desktop_runtime.streamlit_url())
        self.assertTrue(desktop_runtime.streamlit_health_url().endswith("/_stcore/health"))


class SmokeTestTest(unittest.TestCase):
    def test_smoke_test_returns_zero_in_source_mode(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = frozen_smoke.run_smoke_test()
        out = buf.getvalue()
        self.assertEqual(rc, 0, "smoke test 未全通过：\n" + out)
        for needle in ("[OK]   frozen mode", "[OK]   resource_path",
                       "[OK]   user data path", "[OK]   SQLite init",
                       "[OK]   Streamlit import", "[OK]   streamlit-calendar import",
                       "[OK]   pywebview import", "[OK]   pystray import",
                       "[OK]   keyring import", "[OK]   notification provider",
                       "[OK]   app icon resource"):
            self.assertIn(needle, out)
        self.assertNotIn("[FAIL]", out)

    def test_smoke_test_never_uses_real_data_dir_when_not_overridden(self):
        """没被覆盖时也不能在真实目录建库（会换临时目录）。"""
        buf = io.StringIO()
        saved = os.environ.pop("TODO_APP_DATA_DIR", None)
        try:
            with contextlib.redirect_stdout(buf):
                rc = frozen_smoke.run_smoke_test()
        finally:
            if saved is not None:
                os.environ["TODO_APP_DATA_DIR"] = saved
            else:
                os.environ["TODO_APP_DATA_DIR"] = tempfile.mkdtemp(prefix="restore_")
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertIn("未触碰真实数据", buf.getvalue())


if __name__ == "__main__":
    unittest.main(verbosity=2)
