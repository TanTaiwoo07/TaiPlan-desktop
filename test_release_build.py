# -*- coding: utf-8 -*-
"""第 17 阶段：发布流程与发布物测试（不需要 Inno Setup）。

覆盖文档第 79 条里 install 配置之外的发布侧要求：
    SHA256 生成 / release 文件名 / 一键构建顺序 / RELEASE_NOTES 面向用户 /
    THIRD_PARTY_NOTICES 来自真实 metadata / WebView2 检测 / 安装版识别
"""

import io
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import app_metadata
import build_installer
import build_release
import runtime_diagnostics
import third_party_notices
import version

ROOT = Path(__file__).resolve().parent


class ReleaseArtifactTest(unittest.TestCase):
    def test_setup_name_contains_version(self):
        iss = io.open(ROOT / "installer" / "TaiPlan.iss", encoding="utf-8-sig").read()
        self.assertIn("OutputBaseFilename=TaiPlan-Setup-{#MyAppVersion}", iss)
        self.assertRegex(version.__version__, r"^\d+\.\d+\.\d+$",
                         "版本号必须是 x.y.z（升版时这里不该写死）")

    def test_sha256_sidecar_name_and_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            setup = Path(tmp) / f"TaiPlan-Setup-{version.__version__}.exe"
            setup.write_bytes(b"setup-bytes")
            side, digest = build_installer.write_sha256(setup)
            self.assertEqual(side.name, f"TaiPlan-Setup-{version.__version__}.exe.sha256")
            text = io.open(side, encoding="utf-8").read()
            self.assertRegex(text, rf"^[0-9a-f]{{64}}  {re.escape(setup.name)}\n$")

    def test_sha256_matches_hashlib(self):
        import hashlib

        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "x.bin"
            f.write_bytes(b"abc" * 1000)
            self.assertEqual(build_installer.sha256_of(f),
                             hashlib.sha256(b"abc" * 1000).hexdigest())

    def test_release_dir_is_project_release(self):
        self.assertEqual(build_installer.RELEASE_DIR, ROOT / "release")


class ReleaseNotesTest(unittest.TestCase):
    def _notes(self):
        return io.open(ROOT / "RELEASE_NOTES.md", encoding="utf-8").read()

    def test_exists_and_has_version(self):
        """版本号必须与 version.py 一致（升版时这里不该再写死旧版本）。"""
        import version

        text = self._notes()
        self.assertIn(version.__version__, text)
        self.assertIn(f"TaiPlan-Setup-{version.__version__}.exe", text)

    def test_lists_user_facing_features(self):
        text = self._notes().lower()
        for token in ("todo", "calendar", "time blocking", "recurrence",
                      "ai parsing", "reminder", "windows notifications", "fluent ui"):
            self.assertIn(token, text)

    def test_no_developer_internals(self):
        """发布说明面向用户，不写开发内部细节。"""
        text = self._notes()
        for bad in ("PyInstaller", "Streamlit", "_internal", "localhost",
                    "sqlite", "venv", "pip install"):
            self.assertNotIn(bad, text)

    def test_mentions_data_location_and_uninstall_keeps_data(self):
        text = self._notes()
        self.assertIn("%LOCALAPPDATA%", text)
        self.assertIn("凭据管理器", text)
        self.assertIn("不会删除", text)


class ThirdPartyNoticesTest(unittest.TestCase):
    def _text(self):
        return (ROOT / "THIRD_PARTY_NOTICES.txt").read_text(encoding="utf-8")

    def test_file_exists(self):
        self.assertTrue((ROOT / "THIRD_PARTY_NOTICES.txt").is_file())

    def test_licenses_come_from_real_metadata(self):
        """许可证必须来自真实 metadata（不是手写也不是猜的）。"""
        text = self._text()
        self.assertIn("THIRD PARTY NOTICES", text)
        self.assertGreaterEqual(text.count("许可证 :"), 20)
        self.assertIn("未做任何推测", text)

    def test_documented_generation_rules(self):
        """生成规则要写在文件里，便于人工复核。"""
        text = self._text()
        for token in ("生成规则", "dist/TaiPlan/_internal", "packages_distributions",
                      "纯构建依赖", "未解析"):
            self.assertIn(token, text)

    def test_mentions_python_and_pyinstaller(self):
        text = self._text()
        self.assertIn("Python Software Foundation", text)
        self.assertIn("PyInstaller", text)

    def test_pure_python_packages_are_not_missed(self):
        """纯 Python 包冻在 PYZ 里，只扫 _internal 会漏 —— 必须从 PYZ-00.toc 补上。"""
        text = self._text()
        for name in ("keyring", "pystray", "winotify", "streamlit-calendar"):
            self.assertIn(name, text, f"缺少 {name} 的许可证声明")
        self.assertIn("PYZ-00.toc", text)

    def test_build_tooling_not_listed_as_runtime_dependency(self):
        """PyInstaller 不能作为运行时依赖出现在依赖表里（只在打包器组件一节说明）。"""
        text = self._text()
        stats = third_party_notices.build_notices(ROOT / "dist" / "TaiPlan", verbose=False)[1]
        self.assertIn("pyinstaller", [n.lower() for n in stats["packager_components"]])
        dep_entries = [line for line in text.splitlines()
                       if re.match(r"^pyinstaller\s+\d", line, re.IGNORECASE)]
        self.assertEqual(dep_entries, [], f"pyinstaller 被当成运行时依赖：{dep_entries}")

    def test_own_modules_are_not_reported_as_unresolved(self):
        stats = third_party_notices.build_notices(ROOT / "dist" / "TaiPlan", verbose=False)[1]
        unresolved = [x.lower() for x in stats["unresolved"]]
        for own in ("app", "database", "services", "models"):
            self.assertNotIn(own, unresolved, f"{own} 被误报为未解析第三方")

    def test_bundle_source_includes_pyz_and_internal(self):
        stats = third_party_notices.build_notices(ROOT / "dist" / "TaiPlan", verbose=False)[1]
        self.assertIn("PYZ-00.toc", stats["source"])
        self.assertGreaterEqual(stats["distributions"], 30)

    def test_generator_accepts_out_argument(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "n.txt"
            third_party_notices.main(["--out", str(out), "--quiet"])
            text = out.read_text(encoding="utf-8")
            self.assertIn("THIRD PARTY NOTICES", text)
            self.assertGreaterEqual(text.count("许可证 :"), 20)

    def test_missing_dist_is_reported_clearly(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                third_party_notices.build_notices(Path(tmp), verbose=False)


class OneShotReleaseBuildTest(unittest.TestCase):
    def test_steps_run_in_documented_order(self):
        calls = []

        def fake_step(title, args):
            calls.append(args)

        with mock.patch.object(build_release, "_step", side_effect=fake_step), \
                mock.patch.object(build_release, "ROOT", ROOT):
            build_release.main(["--skip-smoke"])
        self.assertEqual(len(calls), 3)
        self.assertIn("build.py", calls[0])
        self.assertIn("third_party_notices.py", calls[1])
        self.assertIn("build_installer.py", calls[2])
        self.assertIn("--skip-smoke", calls[2])

    def test_step_failure_aborts_release(self):
        with mock.patch.object(build_release, "_step",
                               side_effect=SystemExit("boom")) as step:
            with self.assertRaises(SystemExit):
                build_release.main([])
            self.assertEqual(step.call_count, 1)

    def test_build_release_uses_same_interpreter(self):
        self.assertEqual(build_release.PY, sys.executable)


class WebView2Test(unittest.TestCase):
    def test_available_returns_bool(self):
        self.assertIsInstance(runtime_diagnostics.webview2_available(), bool)

    def test_note_mentions_runtime_or_version(self):
        note = runtime_diagnostics.webview2_note()
        self.assertIn("WebView2 Runtime", note)

    def test_missing_runtime_note_has_download_url(self):
        with mock.patch.object(runtime_diagnostics, "webview2_version", return_value=None):
            note = runtime_diagnostics.webview2_note()
            self.assertIn("未检测到", note)
            self.assertIn(runtime_diagnostics.WEBVIEW2_DOWNLOAD_URL, note)

    def test_present_runtime_does_not_nag(self):
        with mock.patch.object(runtime_diagnostics, "webview2_version",
                               return_value="154.0.4258.53"):
            note = runtime_diagnostics.webview2_note()
            self.assertIn("154.0.4258.53", note)
            self.assertNotIn("未检测到", note)

    def test_guid_and_url_are_stable(self):
        self.assertEqual(runtime_diagnostics.WEBVIEW2_CLIENT_GUID,
                         "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}")
        self.assertTrue(runtime_diagnostics.WEBVIEW2_DOWNLOAD_URL.startswith("https://"))

    def test_registry_probe_never_raises(self):
        with mock.patch("winreg.OpenKey", side_effect=OSError("no key")):
            self.assertIsNone(runtime_diagnostics._webview2_registry_version())


class InstallModeDiagnosticsTest(unittest.TestCase):
    def test_collect_diagnostics_includes_webview2(self):
        data = runtime_diagnostics.collect_diagnostics(log_tail_lines=1)
        blob = repr(data)
        self.assertIn("webview2", blob.lower())

    def test_source_build_is_not_installed_build(self):
        self.assertFalse(app_metadata.is_installed_build())

    def test_about_dict_has_version_and_publisher(self):
        info = app_metadata.about_dict()
        self.assertEqual(info["version"], version.__version__)
        self.assertEqual(info["name"], "TaiPlan")


if __name__ == "__main__":
    unittest.main()
