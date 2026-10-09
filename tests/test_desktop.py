"""startup_manager / 单实例 / tray 相关单元测试（mock，不碰真实系统）。"""

import os
import socket
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from taiplan import startup_manager
from taiplan import desktop_runtime


class StartupManagerTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        patcher = mock.patch.object(startup_manager, "STARTUP_DIR", Path(self.tmp))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _fake_create(self, path, target, args, wd, icon):
        """替身：不调 PowerShell，只按契约落一个文件。"""
        Path(path).write_text(f"{target}\n{args}", encoding="utf-8")
        return True

    def test_startup_dir_is_absolute_without_appdata(self):
        """APPDATA 为空（宿主 shell 实测为 None）时也必须解析出**绝对**路径。"""
        fake_home = Path(r"C:\Users\X")
        with mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch("pathlib.Path.home", return_value=fake_home):
            resolved = startup_manager._resolve_startup_dir()
        self.assertTrue(resolved.is_absolute(), str(resolved))
        self.assertEqual(resolved, fake_home / "AppData" / "Roaming" / "Microsoft"
                         / "Windows" / "Start Menu" / "Programs" / "Startup")

    def test_startup_dir_uses_appdata_when_present(self):
        with mock.patch.dict("os.environ", {"APPDATA": r"C:\Users\X\AppData\Roaming"},
                             clear=True):
            self.assertEqual(
                startup_manager._resolve_startup_dir(),
                Path(r"C:\Users\X\AppData\Roaming")
                / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup")

    def test_degrades_gracefully_when_home_unknown(self):
        """环境连 home 都确定不了：不得抛异常，也不得回退成相对路径。"""
        with mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch("pathlib.Path.home",
                           side_effect=RuntimeError("Could not determine home directory.")):
            self.assertIsNone(startup_manager._resolve_startup_dir())
        # 模块级 STARTUP_DIR 为 None 时，公开 API 一律安全降级
        with mock.patch.object(startup_manager, "STARTUP_DIR", None):
            self.assertIsNone(startup_manager.startup_dir())
            self.assertIsNone(startup_manager.startup_shortcut_path())
            self.assertFalse(startup_manager.is_startup_enabled())
            self.assertFalse(startup_manager.enable_startup())
            self.assertTrue(startup_manager.disable_startup())

    def test_shortcut_name_matches_installer(self):
        """第 17 阶段：启动项名称/位置必须与安装器 [Icons] 一致（同一个 .lnk）。"""
        from taiplan import app_metadata
        self.assertEqual(
            startup_manager.STARTUP_LNK_NAME, app_metadata.SHORTCUT_NAME + ".lnk")
        self.assertEqual(
            startup_manager.startup_shortcut_path(),
            Path(self.tmp) / startup_manager.STARTUP_LNK_NAME)
        self.assertEqual(
            startup_manager.startup_shortcut_path().parent,
            Path(self.tmp))

    def test_launch_spec_has_autostart_param(self):
        """源码模式：venv pythonw + launcher.py + --autostart（路径已加引号）。"""
        target, args, wd, icon = startup_manager._launch_spec()
        self.assertIn("pythonw.exe", target)
        self.assertIn("launcher.py", args)
        self.assertIn("--autostart", args)
        self.assertIn('"', args)

    def test_frozen_build_launch_spec_uses_installed_exe(self):
        """安装版（frozen）：启动项必须直接指向 TaiPlan.exe + --autostart。"""
        fake_exe = Path(r"C:\Users\X\AppData\Local\Programs\TaiPlan\TaiPlan.exe")
        with mock.patch.object(startup_manager.sys, "frozen", True, create=True), \
                mock.patch.object(startup_manager.sys, "executable", str(fake_exe)):
            target, args, workdir, icon = startup_manager._launch_spec()
        self.assertEqual(target, str(fake_exe))
        self.assertEqual(args, "--autostart")
        self.assertEqual(workdir, str(fake_exe.parent))
        self.assertEqual(icon, str(fake_exe))
        self.assertNotIn("launcher.py", args)

    def test_source_build_launch_spec_is_not_frozen(self):
        self.assertFalse(startup_manager.is_frozen_build())

    def test_installer_created_shortcut_is_recognized_and_removed(self):
        """安装器创建的启动项：应用内应显示已启用，关闭时删掉同一个文件。"""
        lnk = startup_manager.startup_shortcut_path()
        lnk.parent.mkdir(parents=True, exist_ok=True)
        lnk.write_text("installer-created", encoding="utf-8")
        self.assertTrue(startup_manager.is_startup_enabled())
        self.assertTrue(startup_manager.disable_startup())
        self.assertFalse(startup_manager.is_startup_enabled())

    def test_legacy_cmd_is_recognized_and_cleaned(self):
        """历史 TaiPlan.cmd 被识别为已启用，并在关闭时一并清理。"""
        legacy = startup_manager._legacy_cmd_path()
        legacy.parent.mkdir(parents=True, exist_ok=True)
        legacy.write_text("@echo off", encoding="utf-8")
        self.assertTrue(startup_manager.is_startup_enabled())
        with mock.patch.object(startup_manager, "_create_shortcut", side_effect=self._fake_create):
            self.assertTrue(startup_manager.enable_startup())
        self.assertFalse(legacy.exists())
        self.assertTrue(startup_manager.startup_shortcut_path().exists())

    def test_enable_disable(self):
        with mock.patch.object(startup_manager, "_create_shortcut", side_effect=self._fake_create):
            self.assertTrue(startup_manager.enable_startup())
        self.assertTrue(startup_manager.is_startup_enabled())
        self.assertTrue(startup_manager.disable_startup())
        self.assertFalse(startup_manager.is_startup_enabled())

    def test_ps_quote_escapes_single_quote(self):
        self.assertEqual(startup_manager._ps_quote(r"C:\O'Brien"), "'C:\O''Brien'")


class SingleInstanceTest(unittest.TestCase):

    def _free_port(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port

    def test_lock_acquire_then_release(self):
        # 使用临时空闲端口，不依赖真实运行实例
        with mock.patch.object(desktop_runtime, "LOCK_PORT", self._free_port()):
            s1 = desktop_runtime._acquire_single_instance_lock()
            self.assertIsNotNone(s1)
            s2 = desktop_runtime._acquire_single_instance_lock()
            self.assertIsNone(s2)
            s1.close()


if __name__ == "__main__":
    unittest.main()
