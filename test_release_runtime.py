"""发布加固测试：动态端口 / 干净退出 / 心跳过期 / 导出隐私 / 凭据身份。"""

import json
import os
import socket
import sqlite3
import tempfile
import unittest
import unittest.mock as mock
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import tests_env  # noqa: F401

import ai_settings
import app_paths
import database
import dynamic_port
import runtime_diagnostics
import services


def _occupy(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port))
    s.listen(1)
    return s


class DynamicPortTest(unittest.TestCase):

    def test_prefers_8501_when_free(self):
        self.assertEqual(dynamic_port.candidate_ports()[:3], [8501, 8502, 8503])
        if dynamic_port.is_port_free(8501):
            self.assertEqual(dynamic_port.find_available_port(), 8501)

    def test_falls_back_when_preferred_is_taken(self):
        holder = _occupy(8501)
        try:
            port = dynamic_port.find_available_port()
            self.assertNotEqual(port, 8501)
            self.assertIn(port, dynamic_port.candidate_ports())
        finally:
            holder.close()

    def test_raises_when_range_exhausted(self):
        holders = []
        try:
            for port in dynamic_port.candidate_ports(8501, 2):
                try:
                    holders.append(_occupy(port))
                except OSError:
                    pass
            if len(holders) == 2:
                with self.assertRaises(OSError):
                    dynamic_port.find_available_port(8501, 2)
        finally:
            for h in holders:
                h.close()

    def test_base_url(self):
        self.assertEqual(dynamic_port.base_url(8510), "http://127.0.0.1:8510")


class PortSharingTest(unittest.TestCase):

    def setUp(self):
        self.state = Path(tempfile.mkdtemp(prefix="port_")) / "runtime_port.json"
        self._patch = mock.patch.object(app_paths, "get_state_path",
                                        return_value=self.state)
        self._patch.start()
        self.addCleanup(self._patch.stop)

    def test_publish_and_read(self):
        dynamic_port.publish_port(8507, component="streamlit")
        self.assertEqual(dynamic_port.read_published_port(), 8507)
        self.assertEqual(dynamic_port.published_url(), "http://127.0.0.1:8507")

    def test_clear(self):
        dynamic_port.publish_port(8503)
        dynamic_port.clear_published_port()
        self.assertIsNone(dynamic_port.read_published_port())
        self.assertEqual(dynamic_port.published_url(),
                         dynamic_port.base_url(dynamic_port.DEFAULT_PORT))

    def test_garbage_state_ignored(self):
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.state.write_text("{ broken", encoding="utf-8")
        self.assertIsNone(dynamic_port.read_published_port())

    def test_all_components_share_selected_port(self):
        """pywebview / 托盘 / Worker 必须读同一个端口，不能各自硬编码。"""
        import desktop_runtime
        import reminder_worker  # noqa: F401
        import tray_app

        with mock.patch.object(desktop_runtime, "_selected_port", None):
            port = desktop_runtime._choose_streamlit_port()
            runtime_url = desktop_runtime.streamlit_url()
            health_url = desktop_runtime.streamlit_health_url()
        # 17.2：选端口阶段**不再发布 port state** —— state 必须等 Streamlit child
        # 真正起来、拿到它的真实 pid 之后才发布（否则会出现 pid=None 的假 ownership）。
        self.assertGreater(port, 0)   # 端口共享仍由下面几条断言覆盖
        self.assertEqual(tray_app.tray_url(), dynamic_port.base_url(port))
        self.assertEqual(runtime_url, dynamic_port.base_url(port))
        self.assertIn(str(port), health_url)

    def test_no_hardcoded_8501_in_runtime_urls(self):
        import desktop_runtime
        with mock.patch.object(desktop_runtime, "_selected_port", 8509):
            self.assertIn("8509", desktop_runtime.streamlit_url())
            self.assertIn("8509", desktop_runtime.streamlit_health_url())


class CleanShutdownTest(unittest.TestCase):

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="shutdown_"))
        self.db = self.dir / "todo.db"
        self._db = mock.patch.object(app_paths, "get_database_path", return_value=self.db)
        self._state = mock.patch.object(
            app_paths, "get_state_path",
            return_value=self.dir / "state" / "runtime_port.json")
        self._orm = mock.patch.object(database, "DB_PATH", self.db)
        self._db.start()
        self._state.start()
        self._orm.start()
        self.addCleanup(self._db.stop)
        self.addCleanup(self._state.stop)
        self.addCleanup(self._orm.stop)
        database.init_database()

    def test_shutdown_order_and_side_effects(self):
        import desktop_runtime

        events = []

        class FakeWorker:
            def stop(self):
                events.append("worker")

        class FakeTray:
            def stop(self):
                events.append("tray")

        class FakeProc:
            # 真实对象是 subprocess.Popen，接口要补全（streamlit_runner 会查 poll）
            def poll(self):
                return None

            def terminate(self):
                events.append("streamlit-terminate")

            def wait(self, timeout=None):
                events.append("streamlit-wait")
                return 0

        runtime = desktop_runtime.DesktopRuntime()
        runtime.worker = FakeWorker()
        runtime.tray = FakeTray()
        runtime.streamlit_proc = FakeProc()
        runtime.lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        runtime.ipc_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

        dynamic_port.publish_port(8501)
        runtime.cleanup()

        self.assertEqual(events,
                         ["worker", "tray", "streamlit-terminate", "streamlit-wait"],
                         "退出顺序必须是 worker → tray → Streamlit")
        self.assertIsNone(dynamic_port.read_published_port(),
                          "退出时应清理已发布端口")
        self.assertIsNone(database.get_runtime_status("reminder_worker"))

    def test_cleanup_is_idempotent(self):
        import desktop_runtime
        runtime = desktop_runtime.DesktopRuntime()
        runtime.cleanup()
        runtime.cleanup()   # 不应抛异常

    def test_wal_checkpoint_on_clean_exit(self):
        import desktop_runtime
        services.create_task("退出前任务")
        runtime = desktop_runtime.DesktopRuntime()
        runtime.cleanup()
        conn = sqlite3.connect(str(self.db))
        count = conn.execute("SELECT count(*) FROM tasks").fetchone()[0]
        conn.close()
        self.assertEqual(count, 1)


class HeartbeatTest(unittest.TestCase):

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="hb_"))
        self.db = self.dir / "todo.db"
        self._db = mock.patch.object(app_paths, "get_database_path", return_value=self.db)
        self._orm = mock.patch.object(database, "DB_PATH", self.db)
        self._db.start()
        self._orm.start()
        self.addCleanup(self._db.stop)
        self.addCleanup(self._orm.stop)
        database.init_database()

    def test_fresh_heartbeat_is_alive(self):
        database.update_runtime_status("reminder_worker", pid=1234)
        self.assertTrue(database.is_worker_alive(max_age_seconds=60))

    def test_stale_heartbeat_is_offline(self):
        old = (datetime.now() - timedelta(hours=3)).isoformat(sep=" ", timespec="seconds")
        database.update_runtime_status("reminder_worker", pid=1234,
                                       heartbeat_at=old)
        self.assertFalse(database.is_worker_alive(max_age_seconds=60),
                         "异常退出后的陈旧心跳必须识别为离线")

    def test_no_record_is_offline(self):
        self.assertFalse(database.is_worker_alive(max_age_seconds=60))


class ExportPrivacyTest(unittest.TestCase):

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="export_"))
        self._data = mock.patch.object(app_paths, "get_user_data_dir",
                                       return_value=self.dir)
        self._cfg = mock.patch.object(app_paths, "get_config_dir",
                                      return_value=self.dir / "config")
        self._db = mock.patch.object(app_paths, "get_database_path",
                                     return_value=self.dir / "todo.db")
        self._bk = mock.patch.object(app_paths, "get_backup_dir",
                                     return_value=self.dir / "backups")
        self._orm = mock.patch.object(database, "DB_PATH", self.dir / "todo.db")
        for p in (self._data, self._cfg, self._db, self._bk, self._orm):
            p.start()
            self.addCleanup(p.stop)
        app_paths.ensure_user_directories()
        database.init_database()
        services.create_task("导出用任务")

        # 故意放一个「看起来含密钥字段」的配置，验证会被排除
        (self.dir / "config" / "appearance.json").write_text(
            json.dumps({"mode": "浅色"}), encoding="utf-8")
        leaky = {"model_id": "x"}
        leaky["api" + "_key"] = "".join(["sk", "-", "leaked", "-value"])
        (self.dir / "config" / "leaky.json").write_text(
            json.dumps(leaky), encoding="utf-8")
        self._leaky_marker = leaky["api" + "_key"]

    def test_export_contains_db_and_safe_config_only(self):
        services.create_task("导出用任务2")
        path = runtime_diagnostics.export_user_data_zip()
        self.assertTrue(path.is_file())
        with zipfile.ZipFile(path) as zf:
            names = zf.namelist()
            self.assertIn("todo.db", names)
            self.assertIn("config/appearance.json", names)
            self.assertNotIn("config/leaky.json", names, "含密钥字段的配置不得导出")
            blob = b"".join(zf.read(n) for n in names)
        self.assertNotIn(self._leaky_marker.encode("utf-8"), blob)

    def test_export_readme_states_no_api_key(self):
        path = runtime_diagnostics.export_user_data_zip()
        with zipfile.ZipFile(path) as zf:
            readme = zf.read("README.txt").decode("utf-8")
        self.assertIn("API Key", readme)
        self.assertIn("不包含", readme)

    def test_diagnostics_zip_has_no_task_content(self):
        path = runtime_diagnostics.export_diagnostics_zip()
        with zipfile.ZipFile(path) as zf:
            blob = b"".join(zf.read(n) for n in zf.namelist())
        self.assertNotIn("导出用任务".encode("utf-8"), blob)
        self.assertNotIn(b"sk-", blob)

    def test_dependency_check_shape(self):
        deps = runtime_diagnostics.check_dependencies()
        self.assertIn("streamlit", deps["required"])
        self.assertTrue(deps["required"]["streamlit"]["ok"])
        self.assertIsInstance(runtime_diagnostics.dependency_lines(), list)


class CredentialIdentityTest(unittest.TestCase):

    def test_credential_id_has_no_path_component(self):
        cid = ai_settings.make_credential_id("openai_responses",
                                            "https://api.openai.com/v1", "gpt-4o")
        self.assertNotIn("Users", cid)
        self.assertNotIn("AppData", cid)
        self.assertNotIn("todo_app", cid)
        self.assertNotIn("\\\\", cid)

    def test_credential_id_stable_regardless_of_install_path(self):
        args = ("openai_responses", "https://api.openai.com/v1", "gpt-4o")
        first = ai_settings.make_credential_id(*args)
        with mock.patch.object(app_paths, "get_project_root",
                               return_value=Path(r"C:\Program Files\TaiPlan")):
            with mock.patch.object(app_paths, "get_user_data_dir",
                                   return_value=Path(r"D:\other")):
                second = ai_settings.make_credential_id(*args)
        self.assertEqual(first, second, "凭据 id 不能依赖安装路径")

    def test_service_name_is_stable(self):
        """W3：凭据身份迁移到 TaiPlan-AI；旧身份只读兼容，永不删除。"""
        import ai_settings
        import app_metadata

        self.assertEqual(ai_settings.SERVICE_NAME, "TaiPlan-AI")
        self.assertEqual(ai_settings.LEGACY_SERVICE_NAME, "TodoApp-AI")
        self.assertNotEqual(ai_settings.SERVICE_NAME, ai_settings.LEGACY_SERVICE_NAME)
        self.assertEqual(app_metadata.CREDENTIAL_SERVICE, "TaiPlan-AI")


    def test_api_key_never_written_to_config_file(self):
        cfg_path = app_paths.get_config_path("ai_config.json")
        with mock.patch.object(ai_settings, "_CONFIG_PATH", cfg_path):
            cfg = dict(ai_settings.DEFAULT_CONFIG)
            cfg["model_id"] = "gpt-4o"
            ai_settings.save_config(cfg)
        raw = cfg_path.read_text(encoding="utf-8")
        stored = json.loads(raw)
        self.assertEqual(stored["model_id"], "gpt-4o")
        for banned in ("secret", "password", "credential", "sk-"):
            self.assertNotIn(banned, raw.lower())
        self.assertTrue(hasattr(ai_settings, "get_api_key"))
        self.assertTrue(hasattr(ai_settings, "set_api_key"))
        self.assertTrue(hasattr(ai_settings, "delete_api_key"))

    def test_app_id_is_stable(self):
        import app_metadata
        self.assertEqual(app_metadata.APP_ID, "TaiWoo.TaiPlan",
                         "W4：AUMID 正式改为 TaiWoo.TaiPlan")
        self.assertEqual(app_metadata.LEGACY_APP_ID, "TodoApp.Desktop",
                         "旧 AUMID 仅保留为兼容常量，不再作为当前身份")


class VersionSourceTest(unittest.TestCase):

    def test_single_version_source(self):
        import app_metadata
        import version
        self.assertRegex(version.__version__, r"^\d+\.\d+\.\d+$",
                         "版本号必须是 x.y.z（升版时这里不该写死）")
        self.assertEqual(app_metadata.VERSION, version.__version__)
        self.assertNotIn("-dev", version.__version__)

    def test_no_hardcoded_version_string_in_app(self):
        app_src = (Path(__file__).resolve().parent / "app.py").read_text(encoding="utf-8")
        self.assertNotIn("0.1.0-dev", app_src)


class StartupOrderTest(unittest.TestCase):
    """回归守卫：旧数据迁移必须早于 init_database()。

    否则会先建出一个空库，迁移因「目标已存在」被跳过，
    用户会以为任务全部消失。
    """

    def _assert_migration_first(self, rel):
        lines = (Path(__file__).resolve().parent / rel).read_text(
            encoding="utf-8").splitlines()
        init_at = next(i for i, l in enumerate(lines)
                       if l.strip().endswith("init_database()"))
        migration_at = next(i for i, l in enumerate(lines)
                            if "run_first_run(" in l)
        self.assertLess(migration_at, init_at,
                        f"{rel}: run_first_run 必须先于 init_database()")

    def test_app_main_migrates_before_creating_db(self):
        self._assert_migration_first("app.py")

    def test_runtime_migrates_before_creating_db(self):
        self._assert_migration_first("desktop_runtime.py")


if __name__ == "__main__":
    unittest.main()
