# -*- coding: utf-8 -*-
"""W4：产品身份一致性测试（§12）。

要求：
  * TaiPlan.exe / TaiPlan / TaiWoo.TaiPlan / TaiWoo_Chen / TaiPlan-AI 之间不得错误混搭
  * 允许 legacy 兼容代码里出现旧名称；不写「整个 repo 不能出现 TodoApp」这种错误测试
"""
import io
import re
import sys
import unittest
from pathlib import Path

from tests import tests_env  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from taiplan import version  # noqa: E402

LEGACY_ALLOWED_FILES = {
    "taiplan/app_paths.py",            # legacy 数据目录名（迁移源）
    "taiplan/data_dir_migration.py",   # W3 迁移
    "taiplan/data_migration.py",       # 第 14 阶段旧迁移
    "taiplan/ai_settings.py",          # legacy 凭据身份回退
    "taiplan/windows_app_registration.py",   # legacy AUMID 常量
    "taiplan/startup_manager.py",      # 旧启动项迁移
    "create_shortcut.py",      # 旧快捷方式清理
    "taiplan/first_run.py",            # 升级兼容接入
    "README.md",               # W5 处理
    "RELEASE_NOTES.md",
    "INSTALLER_FAILURE_TEST.md",
    "PACKAGING_NOTES.md",
    "installer/TaiPlan.iss",   # 旧快捷方式/启动项精确清理
    "tests_env.py",            # 测试环境变量助手（只提到 legacy 目录路径）
}
SELF = "test_product_identity.py"
LEGACY_ALLOWED_PATTERNS = (
    r"LEGACY", r"legacy", r"TodoApp-AI", r"TodoApp\.Desktop", r"TodoApp\.cmd",
    r"Todo App\.lnk", r"migrated_from", r"Todo App", r"LEGACY_APP_DIR_NAME", r"%LOCALAPPDATA%\\\\TodoApp", r"localappdata\\\\todoapp",
)


class IdentityConstantsTest(unittest.TestCase):
    def test_app_metadata_identity(self):
        from taiplan import app_metadata

        self.assertEqual(app_metadata.APP_NAME, "TaiPlan")
        self.assertEqual(app_metadata.APP_ID, "TaiWoo.TaiPlan")
        self.assertEqual(app_metadata.SHORTCUT_NAME, "TaiPlan")
        self.assertEqual(app_metadata.PUBLISHER, "TaiWoo_Chen")
        self.assertEqual(app_metadata.EXE_NAME, "TaiPlan.exe")
        self.assertEqual(app_metadata.CREDENTIAL_SERVICE, "TaiPlan-AI")

    def test_legacy_constants_kept_for_compat(self):
        from taiplan import app_metadata

        self.assertEqual(app_metadata.LEGACY_APP_NAME, "Todo App")
        self.assertEqual(app_metadata.LEGACY_APP_ID, "TodoApp.Desktop")
        self.assertEqual(app_metadata.LEGACY_SHORTCUT_NAME, "Todo App")
        self.assertEqual(app_metadata.LEGACY_EXE_NAME, "TodoApp.exe")

    def test_product_info_identity(self):
        from taiplan import product_info

        self.assertEqual(product_info.APP_DISPLAY_NAME, "TaiPlan")
        self.assertEqual(product_info.APP_AUTHOR, "TaiWoo_Chen")
        self.assertEqual(product_info.APP_AUTHOR.count("_"), 1)
        self.assertEqual(product_info.APP_COPYRIGHT, "\u00a9 2026 TaiWoo_Chen")
        self.assertEqual(product_info.APP_VERSION, version.__version__)
        self.assertEqual(product_info.APP_TAGLINE, "Tasks into time.")
        self.assertEqual(product_info.APP_LICENSE, "MIT License")

    def test_packaging_identity(self):
        import packaging_tools as pt

        self.assertEqual(pt.APP_DISPLAY_NAME, "TaiPlan")
        self.assertEqual(pt.APP_INTERNAL_NAME, "TaiPlan")
        self.assertEqual(pt.APP_COMPANY_NAME, "TaiWoo_Chen")
        self.assertEqual(pt.APP_COPYRIGHT, "\u00a9 2026 TaiWoo_Chen")
        self.assertEqual(pt.RELEASE_EXE, "TaiPlan.exe")
        self.assertEqual(pt.DEBUG_EXE, "TaiPlan-Debug.exe")

    def test_windows_identity(self):
        from taiplan import windows_app_registration as reg

        self.assertEqual(reg.APP_USER_MODEL_ID, "TaiWoo.TaiPlan")
        self.assertEqual(reg.APP_DISPLAY_NAME, "TaiPlan")
        self.assertEqual(reg.LEGACY_APP_USER_MODEL_ID, "TodoApp.Desktop")

    def test_credential_identity(self):
        from taiplan import ai_settings

        self.assertEqual(ai_settings.SERVICE_NAME, "TaiPlan-AI")
        self.assertEqual(ai_settings.LEGACY_SERVICE_NAME, "TodoApp-AI")

    def test_data_dir_identity(self):
        from taiplan import app_paths

        self.assertEqual(app_paths.APP_DIR_NAME, "TaiPlan")
        self.assertEqual(app_paths.LEGACY_APP_DIR_NAME, "TodoApp")

    def test_runtime_ports_unchanged(self):
        """W4 不得因为改名换端口。"""
        from taiplan import desktop_runtime

        self.assertEqual(desktop_runtime.LOCK_PORT, 18721)
        self.assertEqual(desktop_runtime.IPC_PORT, 18722)


class NoWrongMixingTest(unittest.TestCase):
    def _sources(self):
        """非测试源码 + spec/iss；跳过本测试文件自身（它按设计列出错误写法）。"""
        out = {}
        for p in sorted(ROOT.glob("*.py")) + sorted((ROOT / "taiplan" / "ui").glob("*.py")):
            rel = p.relative_to(ROOT).as_posix()
            if rel.startswith("test_") or rel == SELF:
                continue
            out[rel] = io.open(p, encoding="utf-8-sig", errors="replace").read()
        out["TaiPlan.spec"] = io.open(ROOT / "TaiPlan.spec", encoding="utf-8-sig").read()
        out["installer/TaiPlan.iss"] = io.open(ROOT / "installer" / "TaiPlan.iss",
                                               encoding="utf-8-sig").read()
        return out

    def test_no_wrong_brand_mixes(self):
        bad = ("TaiPlan.Desktop", "Taiplan", "TAIPLAN", "TaiWooChen", "Taiwoo_Chen",
               "TaiwooChen", "TaiWoo Chen", "TaiPlan-Chen", "TaiPlanChen")
        offenders = []
        for rel, src in self._sources().items():
            for needle in bad:
                if needle in src:
                    offenders.append(f"{rel}: {needle}")
        self.assertEqual(offenders, [], f"身份混搭: {offenders}")

    def test_author_underscore_preserved(self):
        for rel, src in self._sources().items():
            if "TaiPlan" in src or "TaiWoo" in src:
                self.assertNotIn("TaiWooChen", src, rel)

    def test_legacy_names_only_in_compat_files(self):
        offenders = []
        for rel, src in self._sources().items():
            if rel in LEGACY_ALLOWED_FILES or rel.startswith("test_") or rel == SELF:
                continue
            for i, line in enumerate(src.split("\n"), 1):
                if "TodoApp" not in line and "Todo App" not in line:
                    continue
                if any(re.search(pat, line) for pat in LEGACY_ALLOWED_PATTERNS):
                    continue
                offenders.append(f"{rel}:{i}: {line.strip()[:80]}")
        self.assertEqual(offenders, [],
                         "旧身份出现在非兼容位置（应改为 TaiPlan）:\n" + "\n".join(offenders))

    def test_no_legacy_spec_or_iss_files(self):
        self.assertFalse((ROOT / "TodoApp.spec").exists(), "TodoApp.spec 应已改名为 TaiPlan.spec")
        self.assertFalse((ROOT / "installer" / "TodoApp.iss").exists(),
                         "TodoApp.iss 应已改名为 TaiPlan.iss")
        self.assertTrue((ROOT / "TaiPlan.spec").is_file())
        self.assertTrue((ROOT / "installer" / "TaiPlan.iss").is_file())

    def test_spec_builds_taiplan_exe(self):
        """spec 通过 packaging_tools 常量取 EXE 名（不写死字面量），产物必须是 TaiPlan。"""
        import packaging_tools as pt

        spec = io.open(ROOT / "TaiPlan.spec", encoding="utf-8-sig").read()
        self.assertIn("packaging_tools.RELEASE_EXE", spec)
        self.assertIn("name=BASE_NAME", spec)
        self.assertEqual(pt.RELEASE_EXE, "TaiPlan.exe")
        self.assertEqual(Path(pt.RELEASE_EXE).stem, "TaiPlan")


class PeMetadataTest(unittest.TestCase):
    def test_version_info_has_identity_fields(self):
        import packaging_tools as pt

        text = pt.version_info_text("0.1.0", "TaiPlan.exe")
        for needle in ("CompanyName", "FileDescription", "LegalCopyright",
                       "InternalName", "OriginalFilename", "ProductName", "ProductVersion"):
            self.assertIn(needle, text, f"PE 元数据缺少 {needle}")
        self.assertIn("'TaiWoo_Chen'", text)
        self.assertIn("'\u00a9 2026 TaiWoo_Chen'", text)
        self.assertIn("'TaiPlan'", text)
        self.assertNotIn("'TodoApp'", text)
        self.assertIn("'TaiPlan.exe'", text)

    def test_file_and_product_version(self):
        import packaging_tools as pt

        text = pt.version_info_text("0.1.0", "TaiPlan.exe")
        self.assertIn("filevers=(0, 1, 0, 0)", text)
        self.assertIn("prodvers=(0, 1, 0, 0)", text)
        self.assertIn("StringStruct('FileVersion', '0.1.0')", text)
        self.assertIn("StringStruct('ProductVersion', '0.1.0')", text)


class InstallerIdentityTest(unittest.TestCase):
    def setUp(self):
        self.iss = io.open(ROOT / "installer" / "TaiPlan.iss", encoding="utf-8-sig").read()

    def test_app_id_guid_unchanged(self):
        self.assertIn("8E2F4C1B-7A3D-4E62-9B5F-2C7A1D0E4F83", self.iss,
                      "Installer AppId GUID 必须保持不变（升级兼容）")

    def test_new_names(self):
        self.assertIn('MyAppName "TaiPlan"', self.iss)
        self.assertIn('MyAppExeName "TaiPlan.exe"', self.iss)
        self.assertIn('MyAppPublisher "TaiWoo_Chen"', self.iss)
        self.assertIn('StartupShortcutName "TaiPlan"', self.iss)

    def test_default_dir_is_taiplan(self):
        self.assertIn("DefaultDirName={localappdata}\\Programs\\TaiPlan", self.iss)

    def test_previous_app_dir_disabled_for_upgrade(self):
        """Inno 默认会沿用上一次安装目录；必须显式关闭，否则升级仍装进 Programs\\TodoApp。"""
        self.assertIn("UsePreviousAppDir=no", self.iss)

    def test_legacy_shortcut_cleanup_is_exact(self):
        self.assertIn("[InstallDelete]", self.iss)
        self.assertIn('{#LegacyAppName}.lnk', self.iss)
        self.assertNotIn('Type: filesandordirs; Name: "{userdesktop}\\*', self.iss)

    def test_output_name(self):
        self.assertIn("OutputBaseFilename=TaiPlan-Setup-", self.iss)


class UpgradeMigrationTest(unittest.TestCase):
    def test_startup_migration_does_not_enable_when_absent(self):
        """原本没启用开机启动时，升级不得擅自开启。"""
        import os
        import tempfile

        from taiplan import startup_manager

        tmp = Path(tempfile.mkdtemp())
        old_dir = startup_manager.STARTUP_DIR
        startup_manager.STARTUP_DIR = tmp
        try:
            info = startup_manager.migrate_legacy_startup()
            self.assertFalse(info["migrated"])
            self.assertEqual(info["reason"], "no_legacy_entry")
            self.assertFalse((tmp / f"{startup_manager.STARTUP_LNK_NAME}").exists())
        finally:
            startup_manager.STARTUP_DIR = old_dir

    def test_shortcut_cleanup_only_exact_name(self):
        import tempfile

        import create_shortcut

        tmp = Path(tempfile.mkdtemp())
        other = tmp / "Not Ours.lnk"
        other.write_bytes(b"x")
        legacy = tmp / create_shortcut.LEGACY_SHORTCUT_NAME
        legacy.write_bytes(b"x")
        info = create_shortcut.remove_legacy_shortcut(desktop_dir=tmp)
        self.assertTrue(info["removed"])
        self.assertFalse(legacy.exists(), "旧快捷方式应被清理")
        self.assertTrue(other.exists(), "不属于我们的快捷方式绝不能删")


if __name__ == "__main__":
    unittest.main(verbosity=2)
