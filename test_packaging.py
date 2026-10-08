# -*- coding: utf-8 -*-
"""第 15 阶段：打包配置与安全检查的单元测试。

覆盖文档第 47 节剩下的项：敏感文件扫描、EXE 版本资源生成，
以及 spec / build.py 的关键约定（入口、onedir、datas、无 collect_all）。
"""
import sys
import tempfile
import unittest
from pathlib import Path

import tests_env  # noqa: F401

PROJECT_ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(PROJECT_ROOT))

import build  # noqa: E402
import packaging_tools  # noqa: E402
import version as app_version  # noqa: E402


def _tmpdir():
    return Path(tempfile.mkdtemp(prefix="pkgtest_"))


class VersionResourceTest(unittest.TestCase):
    def test_version_tuple(self):
        self.assertEqual(packaging_tools.version_tuple("0.1.0"), (0, 1, 0, 0))
        self.assertEqual(packaging_tools.version_tuple("1.2.3.4"), (1, 2, 3, 4))
        self.assertEqual(packaging_tools.version_tuple(""), (0, 0, 0, 0))
        self.assertEqual(packaging_tools.version_tuple("bad"), (0, 0, 0, 0))

    def test_version_info_has_all_required_fields(self):
        text = packaging_tools.version_info_text("0.1.0")
        for field in ("FileDescription", "ProductName", "FileVersion",
                      "ProductVersion", "InternalName", "OriginalFilename"):
            self.assertIn(field, text, f"版本资源缺少 {field}")
        self.assertIn("'TaiPlan'", text)
        self.assertIn("'TaiPlan'", text)
        self.assertIn("'0.1.0'", text)
        self.assertIn("'TaiPlan.exe'", text)
        self.assertIn("VSVersionInfo(", text)

    def test_debug_version_info_uses_debug_exe_name(self):
        text = packaging_tools.version_info_text("0.1.0", packaging_tools.DEBUG_EXE)
        self.assertIn("'TaiPlan-Debug.exe'", text)

    def test_write_version_file(self):
        path = packaging_tools.write_version_file(_tmpdir() / "v.txt", "0.1.0")
        self.assertTrue(path.is_file())
        self.assertIn("VSVersionInfo(", path.read_text(encoding="utf-8"))

    def test_version_matches_version_py(self):
        text = packaging_tools.version_info_text(app_version.__version__)
        self.assertIn(f"'{app_version.__version__}'", text)


class SensitiveFileScanTest(unittest.TestCase):
    def test_clean_dist_has_no_findings(self):
        d = _tmpdir()
        (d / "_internal").mkdir()
        (d / "TaiPlan.exe").write_bytes(b"MZ fake exe")
        (d / "_internal" / "base_library.zip").write_bytes(b"PK\x03\x04")
        (d / "_internal" / "app.py").write_text("print('hi')\n", encoding="utf-8")
        self.assertEqual(packaging_tools.scan_sensitive_files(d), [])

    def test_detects_user_database(self):
        d = _tmpdir()
        (d / "todo.db").write_bytes(b"SQLite format 3")
        kinds = [f["kind"] for f in packaging_tools.scan_sensitive_files(d)]
        self.assertTrue(any("todo.db" in k for k in kinds), kinds)

    def test_detects_dot_env(self):
        d = _tmpdir()
        (d / ".env").write_text("X=1\n", encoding="utf-8")
        kinds = [f["kind"] for f in packaging_tools.scan_sensitive_files(d)]
        self.assertTrue(any(".env" in k for k in kinds), kinds)

    def test_detects_config_files(self):
        d = _tmpdir()
        (d / "_internal" / "config").mkdir(parents=True)
        (d / "_internal" / "config" / "ai_config.json").write_text("{}", encoding="utf-8")
        kinds = [f["kind"] for f in packaging_tools.scan_sensitive_files(d)]
        self.assertTrue(any("ai_config.json" in k for k in kinds), kinds)

    def test_detects_user_data_dirs(self):
        for name in ("logs", "backups", "state"):
            d = _tmpdir()
            (d / name).mkdir()
            (d / name / "x.log").write_text("hello", encoding="utf-8")
            findings = packaging_tools.scan_sensitive_files(d)
            self.assertTrue(findings, f"{name}/ 未被识别")
            self.assertEqual(findings[0]["severity"], "error")

    def test_detects_key_shaped_content(self):
        d = _tmpdir()
        (d / "leak.txt").write_text("token=" + "sk-" + "a" * 24 + "\n", encoding="utf-8")
        findings = packaging_tools.scan_sensitive_files(d)
        self.assertTrue(any("API Key" in f["kind"] for f in findings), findings)

    def test_detects_exact_real_secret_when_provided(self):
        probe_value = "zzz-not-a-real-key-" + "0" * 12
        d = _tmpdir()
        (d / "some.log").write_text(f"export KEY={probe_value}\n", encoding="utf-8")
        self.assertEqual(packaging_tools.scan_sensitive_files(d), [])
        findings = packaging_tools.scan_sensitive_files(d, probe=probe_value)
        self.assertTrue(any("真实 API Key 明文" == f["kind"] for f in findings), findings)

    def test_vendor_prefix_relaxes_only_for_declared_packages(self):
        """声明过的第三方包目录里，示例字符串放行；真实凭据仍然要抓到。"""
        d = _tmpdir()
        vendor_file = d / "_internal" / "streamlit" / "docs.md"
        vendor_file.parent.mkdir(parents=True)
        leak = "sk-" + "a" * 20          # 22 字符，命中严格模式的 sk- 形态
        vendor_file.write_text("token: " + leak + "\n", encoding="utf-8")
        prefixes = ("streamlit",)
        strict = packaging_tools.scan_sensitive_files(d)
        relaxed = packaging_tools.scan_sensitive_files(d, vendor_prefixes=prefixes)
        self.assertTrue(strict, "严格模式应报出第三方文档里的示例")
        self.assertEqual(relaxed, [], "声明为第三方包后应放行")

        probe_value = "zzz-not-a-real-key-" + "0" * 12
        (d / "_internal" / "streamlit" / "leak.txt").write_text(
            probe_value, encoding="utf-8")
        still = packaging_tools.scan_sensitive_files(
            d, probe=probe_value, vendor_prefixes=prefixes)
        self.assertTrue(any("真实 API Key 明文" == f["kind"] for f in still),
                        "第三方目录里的真实凭据也必须报出来")

    def test_db_file_flagged_even_in_vendor_tree(self):
        d = _tmpdir()
        (d / "_internal" / "streamlit").mkdir(parents=True)
        (d / "_internal" / "streamlit" / "todo.db").write_bytes(b"SQLite format 3")
        findings = packaging_tools.scan_sensitive_files(
            d, vendor_prefixes=("streamlit",))
        self.assertTrue(findings, "todo.db 在任何位置都必须报出")

    def test_missing_dist_is_error(self):
        findings = packaging_tools.scan_sensitive_files(_tmpdir() / "nope")
        self.assertEqual(findings[0]["severity"], "error")


class BuildScriptSafetyTest(unittest.TestCase):
    def test_build_artifacts_are_inside_project(self):
        for path in (build.BUILD_DIR, build.DIST_DIR, build.BUILD_LOGS):
            self.assertTrue(str(path.resolve()).startswith(str(PROJECT_ROOT.resolve())),
                            f"{path} 不在项目目录内")

    def test_refuses_to_delete_outside_project(self):
        outside = Path(tempfile.gettempdir()).resolve()
        with self.assertRaises(SystemExit):
            build._assert_inside_project(outside)

    def test_refuses_to_delete_user_data_dir(self):
        import app_paths

        data_dir = Path(app_paths.get_user_data_dir()).resolve()
        # 只有在测试隔离生效时才安全地断言（此时数据目录在临时区）
        if data_dir.exists():
            with self.assertRaises(SystemExit):
                build._assert_inside_project(data_dir)

    def test_build_py_has_ten_steps_documented(self):
        text = (PROJECT_ROOT / "build.py").read_text(encoding="utf-8")
        self.assertIn("STEP_TOTAL = 10", text)
        for step in ("step1_check_pyinstaller", "step2_clean_build", "step3_clean_dist",
                     "step4_guard_userdata", "step5_run_pyinstaller", "step7_check_exe",
                     "step8_scan_dist", "step9_smoke_test"):
            self.assertIn(f"def {step}", text)

    def test_build_py_smoke_test_uses_temp_data_dir(self):
        text = (PROJECT_ROOT / "build.py").read_text(encoding="utf-8")
        self.assertIn("TODO_APP_DATA_DIR", text)
        self.assertIn("--smoke-test", text)

    def test_pyinstaller_log_path(self):
        text = (PROJECT_ROOT / "build.py").read_text(encoding="utf-8")
        self.assertIn('"pyinstaller.log"', text)


class SpecTest(unittest.TestCase):
    def setUp(self):
        self.spec = (PROJECT_ROOT / "TaiPlan.spec").read_text(encoding="utf-8")

    def test_entry_is_main_py(self):
        self.assertIn('main.py', self.spec)
        self.assertNotIn("['app.py']", self.spec)

    def test_onedir_layout(self):
        self.assertIn("COLLECT(", self.spec)
        self.assertIn("exclude_binaries=True", self.spec)

    def test_console_switches_with_debug_flag(self):
        self.assertIn('TODO_APP_DEBUG', self.spec)
        self.assertIn("console=DEBUG", self.spec)

    def test_icon_and_version_resource(self):
        self.assertIn("app_icon.ico", self.spec)
        self.assertIn("write_version_file", self.spec)
        self.assertIn("version=str(version_file)", self.spec)

    def test_no_collect_all(self):
        # 允许注释里提到，但不允许真的调用 collect_all()
        self.assertNotIn("collect_all(", self.spec)

    def test_collects_streamlit_and_calendar_data(self):
        # 按包名精确收集，不 collect_all 整个 site-packages
        self.assertIn("collect_data_files(pkg)", self.spec)
        self.assertIn('for pkg in ("streamlit", "streamlit_calendar")', self.spec)

    def test_metadata_for_key_packages(self):
        for dist in ("streamlit", "streamlit_calendar", "pywebview",
                     "pystray", "keyring"):
            self.assertIn(f'"{dist}"', self.spec, f"spec 未 copy_metadata({dist})")

    def test_dynamic_backends_are_hidden_imports(self):
        for imp in ('"keyring.backends.Windows"', '"win32ctypes.core"',
                    '"webview.platforms.winforms"', '"pystray._win32"',
                    '"winotify"'):
            self.assertIn(imp, self.spec, f"spec 缺少 {imp}")

    def test_bundles_app_py_and_readonly_resources(self):
        self.assertIn('PROJECT_ROOT / "app.py"', self.spec)
        self.assertIn('for sub in ("assets", "tools")', self.spec)


class RuntimeIntegrationTest(unittest.TestCase):
    def test_desktop_runtime_no_longer_builds_dash_m_streamlit(self):
        text = (PROJECT_ROOT / "desktop_runtime.py").read_text(encoding="utf-8")
        self.assertNotIn('"-m", "streamlit"', text)
        self.assertIn("streamlit_runner.start_streamlit_subprocess", text)
        self.assertIn("streamlit_runner.stop_streamlit_subprocess", text)

    def test_required_packaging_files_exist(self):
        for name in ("main.py", "streamlit_runner.py", "frozen_smoke.py",
                     "packaging_tools.py", "TaiPlan.spec", "build.py",
                     "assets/app_icon.ico", "tools/toast.ps1"):
            self.assertTrue((PROJECT_ROOT / name).exists(), f"缺少 {name}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
