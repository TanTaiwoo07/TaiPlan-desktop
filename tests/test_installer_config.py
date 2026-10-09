# -*- coding: utf-8 -*-
"""第 17 阶段：Inno Setup 安装器配置测试（**不需要安装 Inno Setup**）。

只做配置/契约层面的验证（文档第 79、80 条）：
    fixed AppId / version 同步 / install path / per-user privileges /
    desktop & Start Menu shortcut / startup 参数 / uninstall 不删用户数据 /
    dist source path / 敏感文件排除 / SHA256 / release 文件名 / --shutdown 语义

真正的 Installer 编译属于 integration test（build_installer.py）。
"""

import io
import re
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from taiplan import app_metadata
import build_installer
from taiplan import desktop_runtime
import main as main_module
from taiplan import startup_manager
from taiplan import version

ROOT = Path(__file__).resolve().parent.parent
ISS = ROOT / "installer" / "TaiPlan.iss"
ISS_TEXT = io.open(ISS, encoding="utf-8-sig").read()


def strip_comments(text):
    """去掉 Inno 注释（`;` 起头或行内），只留真正的指令。

    注释里会写“不使用 UPX”“绝不要写 {localappdata}\\TaiPlan”这类反例，
    断言必须只看指令。
    """
    out = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(";"):
            continue
        out.append(line)
    return "\n".join(out)


ISS_CLEAN = strip_comments(ISS_TEXT)


def iss_section(name):
    m = re.search(rf"^\[{name}\]\s*$(.*?)(?=^\[|\Z)", ISS_CLEAN,
                  re.MULTILINE | re.DOTALL)
    return m.group(1) if m else ""


def iss_value(key):
    m = re.search(rf"^{key}\s*=\s*(.+)$", ISS_CLEAN, re.MULTILINE)
    return m.group(1).strip() if m else ""


class AppIdTest(unittest.TestCase):
    def test_appid_is_locked_exact_guid(self):
        """RC：AppId 锁死为固定 GUID，不只是"格式像 GUID"。"""
        self.assertEqual(build_installer.APP_ID_GUID,
                         "{8E2F4C1B-7A3D-4E62-9B5F-2C7A1D0E4F83}")
        self.assertEqual(build_installer.INNO_APP_ID_LITERAL,
                         "{{8E2F4C1B-7A3D-4E62-9B5F-2C7A1D0E4F83}")
        m = re.search(r'#define MyAppId\s+"([^"]*)"', ISS_TEXT)
        self.assertIsNotNone(m, "未找到固定 AppId")
        self.assertEqual(m.group(1), build_installer.INNO_APP_ID_LITERAL)

    def test_scan_rejects_different_guid(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "Bad.iss"
            bad.write_text(
                '#include "version.iss"\n'
                '#define MyAppId "{{11111111-2222-3333-4444-555555555555}"\n'
                "[Files]\nSource: \"..\\dist\\TaiPlan\\TaiPlan.exe\"; DestDir: \"{app}\"\n"
                "[Code]\nconst UninstallKey = 'x{#MyAppId}_is1';\n",
                encoding="utf-8")
            problems = build_installer.scan_installer_inputs(bad)
            self.assertTrue(any("AppId 与锁定值不一致" in p for p in problems), problems)

    def test_code_section_uses_single_brace_guid(self):
        """★ 实测结论：[Code] 里 {#MyAppId} 不会折叠双花括号，必须用 MyAppIdCode。"""
        self.assertEqual(build_installer.APP_ID_GUID,
                         "{8E2F4C1B-7A3D-4E62-9B5F-2C7A1D0E4F83}")
        self.assertIn(f'#define MyAppIdCode "{build_installer.APP_ID_GUID}"', ISS_TEXT)
        code = ISS_TEXT.split("[Code]")[-1]
        self.assertIn("{#MyAppIdCode}_is1", code)
        self.assertNotIn("{#MyAppId}_is1", code)
        # 展开后必须正好是 {GUID}_is1（单个左花括号）
        expanded = code.replace("{#MyAppIdCode}", build_installer.APP_ID_GUID)
        self.assertIn(build_installer.APP_ID_GUID + "_is1", expanded)
        self.assertNotIn("{{" + build_installer.APP_ID_GUID[1:], expanded)
        self.assertEqual(build_installer.UNINSTALL_KEY,
                         r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
                         r"\{8E2F4C1B-7A3D-4E62-9B5F-2C7A1D0E4F83}_is1")
        self.assertIn("UninstallKeyOk", code)

    def test_uninstall_key_check_present_in_scan(self):
        code = ISS_TEXT.split("[Code]")[-1]
        self.assertIn("Pos('{{', UninstallKey) = 0", code)

    def test_scan_rejects_double_brace_uninstall_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "Bad.iss"
            bad.write_text(
                '#include "version.iss"\n'
                f'#define MyAppId "{build_installer.INNO_APP_ID_LITERAL}"\n'
                "[Files]\nSource: \"..\\dist\\TaiPlan\\TaiPlan.exe\"; DestDir: \"{app}\"\n"
                "[Code]\nconst UninstallKey = 'x{#MyAppId}_is1';\n",
                encoding="utf-8")
            problems = build_installer.scan_installer_inputs(bad)
            self.assertTrue(any("花括号" in p or "失效" in p for p in problems), problems)

    def test_appid_is_literal_not_generated_each_build(self):
        """AppId 必须写死在 .iss 里；build 过程不得每次生成新 GUID。"""
        src = io.open(ROOT / "build_installer.py", encoding="utf-8").read()
        self.assertNotIn("uuid4", src)
        self.assertNotIn("uuid.uuid", src)
        self.assertRegex(ISS_TEXT, r'AppId=\{#[A-Za-z]+\}')

    def test_aumid_is_separate_from_installer_appid(self):
        """Windows 通知 AUMID 与安装器 AppId 是两回事，不能混用。"""
        self.assertEqual(app_metadata.APP_ID, "TaiWoo.TaiPlan")
        self.assertNotIn(app_metadata.APP_ID, ISS_CLEAN)
        self.assertNotIn("AppUserModelID", ISS_CLEAN)


class VersionSyncTest(unittest.TestCase):
    def test_iss_includes_generated_version_file(self):
        self.assertIn('#include "version.iss"', ISS_TEXT)

    def test_iss_does_not_hardcode_version(self):
        self.assertNotRegex(ISS_CLEAN, r'MyAppVersion\s+"')

    def test_version_iss_matches_version_py(self):
        path = build_installer.write_version_iss(version.__version__)
        text = io.open(path, encoding="utf-8-sig").read()
        self.assertIn(f'#define MyAppVersion "{version.__version__}"', text)

    def test_version_iss_is_utf8_bom(self):
        """Inno 靠 BOM 判定编码，没有 BOM 中文会按 ANSI 解析。"""
        raw = (ROOT / "installer" / "version.iss").read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))

    def test_iss_file_has_bom(self):
        self.assertTrue(ISS.read_bytes().startswith(b"\xef\xbb\xbf"))

    def test_setup_filename_and_release_dir(self):
        self.assertEqual(iss_value("OutputDir"), r"..\release")
        self.assertEqual(iss_value("OutputBaseFilename"),
                         "TaiPlan-Setup-{#MyAppVersion}")
        self.assertEqual(build_installer.RELEASE_DIR, ROOT / "release")

    def test_version_info_uses_same_version(self):
        self.assertIn("VersionInfoVersion={#MyAppVersion}", ISS_TEXT)
        self.assertEqual(build_installer.version_tuple("0.1.0"), (0, 1, 0, 0))
        self.assertEqual(build_installer.version_tuple("1.2.3.4"), (1, 2, 3, 4))
        self.assertEqual(build_installer.version_tuple("0.1"), (0, 1, 0, 0))


class InstallModeTest(unittest.TestCase):
    def test_per_user_install_no_admin(self):
        self.assertEqual(iss_value("PrivilegesRequired"), "lowest")

    def test_default_dir_is_localappdata_programs(self):
        self.assertEqual(iss_value("DefaultDirName"),
                         r"{localappdata}\Programs\TaiPlan")

    def test_no_hardcoded_user_paths(self):
        """禁止写死用户名/盘符具体路径。"""
        for bad in ("C:\\Users\\", "Lenovo", "%USERPROFILE%", "C:\\todo"):
            self.assertNotIn(bad, ISS_CLEAN)

    def test_only_x64_and_win10_plus(self):
        self.assertEqual(iss_value("ArchitecturesAllowed"), "x64compatible")
        self.assertEqual(iss_value("MinVersion"), "10.0")

    def test_compression_is_lzma2_max_and_no_upx(self):
        self.assertEqual(iss_value("Compression"), "lzma2/max")
        self.assertEqual(iss_value("SolidCompression"), "yes")
        # 不使用 UPX（不加壳、不为了误报去动运行时）
        self.assertNotIn("upx", ISS_CLEAN.lower())
        self.assertNotIn("packer", ISS_CLEAN.lower())


class ShortcutTest(unittest.TestCase):
    def test_start_menu_shortcut(self):
        self.assertIn('Name: "{userprograms}\\{#MyAppName}"', iss_section("Icons"))

    def test_desktop_shortcut_task_default_checked(self):
        tasks = iss_section("Tasks")
        self.assertIn('Name: "desktopicon"', tasks)
        self.assertIn("Flags: checkedonce", tasks)
        self.assertIn('Name: "{userdesktop}\\{#MyAppName}"', iss_section("Icons"))
        self.assertIn("Tasks: desktopicon", iss_section("Icons"))

    def test_startup_shortcut_matches_app_startup_manager(self):
        """安装器启动项必须与应用内 StartupManager 同名、同参数（文档第 16 条）。"""
        tasks = iss_section("Tasks")
        self.assertIn('Name: "startupicon"', tasks)
        self.assertIn("Flags: unchecked", tasks)

        m = re.search(r'#define StartupShortcutName "([^"]+)"', ISS_TEXT)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), app_metadata.SHORTCUT_NAME)
        self.assertEqual(m.group(1), startup_manager.STARTUP_SHORTCUT_NAME)
        self.assertEqual(startup_manager.STARTUP_LNK_NAME,
                         f'{m.group(1)}.lnk')

        icons = iss_section("Icons")
        self.assertIn('Name: "{userstartup}\\{#StartupShortcutName}"', icons)
        self.assertIn('Parameters: "--autostart"', icons)
        self.assertIn("Tasks: startupicon", icons)

    def test_shortcuts_point_to_installed_exe_without_cmd(self):
        icons = iss_section("Icons")
        self.assertNotIn("cmd.exe", icons.lower())
        self.assertNotIn("powershell", icons.lower())
        self.assertIn('Filename: "{app}\\{#MyAppExeName}"', icons)

    def test_run_after_install_launches_exe_directly(self):
        run = iss_section("Run")
        self.assertIn('Filename: "{app}\\{#MyAppExeName}"', run)
        self.assertIn("postinstall", run)
        self.assertNotIn("ShellExec", run)
        for bad in ("cmd.exe", "powershell", ".bat", ".vbs", "wscript"):
            self.assertNotIn(bad, run.lower())


class UserDataSafetyTest(unittest.TestCase):
    def test_uninstall_never_deletes_user_data(self):
        uninstall = iss_section("UninstallDelete")
        self.assertNotIn("localappdata", uninstall.lower())
        self.assertNotIn("todo.db", uninstall.lower())
        # 只允许删除安装器自己创建的东西（启动项快捷方式 / install.json）
        for line in uninstall.splitlines():
            if line.strip().lower().startswith("type:"):
                self.assertTrue("{userstartup}" in line.lower() or "{app}" in line.lower(),
                                line)

    def test_iss_never_references_user_data_dir_as_install_source(self):
        for bad in ("todo.db", ".env", "api_key", "api-key"):
            self.assertNotIn(bad, ISS_CLEAN.lower())
        files = iss_section("Files")
        for name in ("logs", "backups", "state", "config"):
            self.assertNotIn(rf"\{name}", files)

    def test_installer_source_is_dist_onedir(self):
        files = iss_section("Files")
        self.assertIn(r'Source: "..\dist\TaiPlan\{#MyAppExeName}"', files)
        self.assertIn(r'Source: "..\dist\TaiPlan\_internal\*"', files)
        self.assertIn("recursesubdirs", files)

    def test_installer_never_installs_dev_scripts(self):
        files = iss_section("Files").lower()
        for bad in ("start_todo.bat", "start_todo_silent.vbs", "requirements.txt",
                    "*.py", "taiplan/launcher.py", "taiplan/desktop_runtime.py"):
            self.assertNotIn(bad, files)

    def test_uninstall_stops_running_app_first(self):
        unrun = iss_section("UninstallRun")
        self.assertIn('Parameters: "--shutdown"', unrun)
        self.assertIn("waituntilterminated", unrun)

    def test_installer_stops_running_app_before_copy(self):
        self.assertIn("PrepareToInstall", ISS_CLEAN)
        self.assertIn("--shutdown", ISS_CLEAN)
        self.assertEqual(iss_value("CloseApplications"), "no")
        self.assertEqual(iss_value("RestartApplications"), "no")

    def test_downgrade_is_not_silent(self):
        """降级必须提示，不能悄悄替换。"""
        code = ISS_CLEAN.split("[Code]")[-1]
        self.assertIn("CompareVer", code)
        self.assertIn("MB_YESNO", code)

    def test_install_marker_contract(self):
        self.assertIn("install.json", ISS_CLEAN)
        self.assertIn('"install_type": "inno"', ISS_CLEAN)


class ShutdownSemanticsTest(unittest.TestCase):
    """Runtime / IPC shutdown 契约（RC 二审）：

        * "IPC 连不上"≠"没有实例"
        * 判据是 single-instance lock 是否释放（不是 IPC 端口消失）
        * 拿不准一律 fail closed
    """

    def setUp(self):
        # 用空闲端口，避免碰真实 runtime 的 18721/18722
        self.lock_port = self._free_port()
        self.ipc_port = self._free_port()
        for name, value in (("LOCK_PORT", self.lock_port), ("IPC_PORT", self.ipc_port)):
            patcher = mock.patch.object(desktop_runtime, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self._holders = []

    def tearDown(self):
        for sock in self._holders:
            try:
                sock.close()
            except OSError:
                pass

    def _free_port(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        port = srv.getsockname()[1]
        srv.close()
        return port

    def _hold(self, port):
        """占用端口（模拟 runtime lock / IPC server）。"""
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", port))
        sock.listen(1)
        self._holders.append(sock)
        return sock

    # ---------------------------------------------------------- lock 探测
    def test_lock_probe_detects_held_lock(self):
        self._hold(self.lock_port)
        self.assertTrue(desktop_runtime.is_runtime_lock_held())

    def test_lock_probe_detects_free_lock(self):
        self.assertFalse(desktop_runtime.is_runtime_lock_held())

    def test_lock_probe_fails_closed_on_error(self):
        with mock.patch("socket.socket", side_effect=RuntimeError("boom")):
            self.assertTrue(desktop_runtime.is_runtime_lock_held())

    def test_lock_probe_does_not_listen(self):
        """bind 探测不能 listen（不得污染 backlog、不得影响真实 runtime 行为）。"""
        src = io.open(ROOT / "taiplan" / "desktop_runtime.py", encoding="utf-8").read()
        block = src.split("def is_runtime_lock_held")[1].split("\ndef ")[0]
        self.assertNotIn("listen(", block)
        self.assertIn("bind(", block)

    # ---------------------------------------------------------- request_shutdown
    def test_no_lock_returns_zero_without_touching_ipc(self):
        with mock.patch.object(desktop_runtime, "_send_ipc_command") as sender:
            self.assertEqual(desktop_runtime.request_shutdown(timeout=1), 0)
        sender.assert_not_called()

    def test_lock_held_but_ipc_unavailable_returns_one(self):
        """★ 核心修复：lock 在、IPC 连不上 → 必须是 1，绝不能是 0。"""
        self._hold(self.lock_port)          # lock 被占，但没人监听 IPC 端口
        self.assertEqual(desktop_runtime.request_shutdown(timeout=1), 1)

    def test_lock_held_and_released_returns_zero(self):
        lock = self._hold(self.lock_port)
        srv = self._hold(self.ipc_port)

        def serve():
            conn, _ = srv.accept()
            conn.recv(64)
            conn.close()
            srv.close()                     # 收到 SHUTDOWN 后释放 IPC
            lock.close()                    # 紧接着释放 lock（= 收尾完成）

        threading.Thread(target=serve, daemon=True).start()
        self.assertEqual(desktop_runtime.request_shutdown(timeout=5), 0)

    def test_timeout_with_lock_still_held_returns_one(self):
        self._hold(self.lock_port)
        srv = self._hold(self.ipc_port)

        def serve():
            conn, _ = srv.accept()
            conn.recv(64)
            conn.close()

        threading.Thread(target=serve, daemon=True).start()
        self.assertEqual(desktop_runtime.request_shutdown(timeout=1), 1)

    def test_waits_for_lock_not_for_ipc_port(self):
        """只断开 IPC、但 lock 仍在 → 必须继续等，最后返回 1。"""
        self._hold(self.lock_port)
        srv = self._hold(self.ipc_port)

        def serve():
            conn, _ = srv.accept()
            conn.recv(64)
            conn.close()
            srv.close()                     # IPC 没了，lock 还在

        threading.Thread(target=serve, daemon=True).start()
        self.assertEqual(desktop_runtime.request_shutdown(timeout=1), 1)

    def test_sends_shutdown_command(self):
        self._hold(self.lock_port)
        srv = self._hold(self.ipc_port)
        received = []

        def serve():
            conn, _ = srv.accept()
            received.append(conn.recv(64))
            conn.close()
            srv.close()

        threading.Thread(target=serve, daemon=True).start()
        desktop_runtime.request_shutdown(timeout=2)
        self.assertEqual(received[0].strip(), b"SHUTDOWN")

    # ---------------------------------------------------------- IPC server 契约
    def test_start_ipc_server_returns_false_on_conflict(self):
        self._hold(self.ipc_port)
        runtime = desktop_runtime.DesktopRuntime()
        self.assertFalse(runtime._start_ipc_server())
        self.assertIsNone(runtime.ipc_socket)

    def test_start_ipc_server_returns_true_when_free(self):
        runtime = desktop_runtime.DesktopRuntime()
        self.assertTrue(runtime._start_ipc_server())
        self.assertIsNotNone(runtime.ipc_socket)
        runtime.cleanup()

    def test_runtime_refuses_to_start_when_ipc_unavailable(self):
        """已持锁但 IPC 建不起来 → 拒绝启动、清理、不碰数据库/Streamlit/Tray。"""
        runtime = desktop_runtime.DesktopRuntime()
        fake_lock = object()
        with mock.patch.object(desktop_runtime, "_acquire_single_instance_lock",
                               return_value=fake_lock), \
                mock.patch.object(runtime, "_start_ipc_server", return_value=False), \
                mock.patch.object(runtime, "cleanup") as cleanup, \
                mock.patch("taiplan.database.init_database") as init_db, \
                mock.patch("taiplan.first_run.run_first_run") as first_run:
            self.assertFalse(runtime.start())
        self.assertIsNotNone(runtime.start_error)
        cleanup.assert_called_once()
        init_db.assert_not_called()
        first_run.assert_not_called()

    def test_second_instance_exit_is_not_a_start_failure(self):
        """第二实例（lock 被占）安全退出，start_error 必须仍为 None（不是启动失败）。"""
        runtime = desktop_runtime.DesktopRuntime()
        with mock.patch.object(desktop_runtime, "_acquire_single_instance_lock",
                               return_value=None), \
                mock.patch.object(desktop_runtime, "_signal_existing_instance",
                                  return_value=True), \
                mock.patch.object(runtime, "_wait_forever"):
            self.assertFalse(runtime.start())
        self.assertIsNone(runtime.start_error)

    def test_no_webbrowser_fallback_code(self):
        """第二实例在控制通道不可用时绝不能打开浏览器。"""
        src = io.open(ROOT / "taiplan" / "desktop_runtime.py", encoding="utf-8").read()
        code = "\n".join(line for line in src.splitlines()
                         if not line.strip().startswith("#"))
        self.assertNotIn("webbrowser.open", code)
        self.assertNotIn("import webbrowser", code)

    def test_cleanup_releases_lock_after_children(self):
        """顺序契约：lock/IPC socket 必须在 worker / tray / Streamlit 之后释放。"""
        src = io.open(ROOT / "taiplan" / "desktop_runtime.py", encoding="utf-8").read()
        block = src.split("def cleanup(self)")[1]
        idx_worker = block.find("self.worker.stop()")
        idx_tray = block.find("self.tray.stop()")
        idx_streamlit = block.find("stop_streamlit_subprocess")
        idx_lock = block.find("self.lock_socket")
        for name, idx in (("worker", idx_worker), ("tray", idx_tray),
                          ("streamlit", idx_streamlit), ("lock", idx_lock)):
            self.assertGreater(idx, 0, f"cleanup 里找不到 {name} 的收尾")
        self.assertGreater(idx_lock, idx_worker)
        self.assertGreater(idx_lock, idx_tray)
        self.assertGreater(idx_lock, idx_streamlit)

    def test_main_exits_nonzero_on_start_error(self):
        src = io.open(ROOT / "main.py", encoding="utf-8").read() + \
            io.open(ROOT / "taiplan" / "desktop_runtime.py", encoding="utf-8").read()
        self.assertIn("runtime.start_error", src)
        self.assertIn("SystemExit(2)", src)


class BuildScriptTest(unittest.TestCase):
    def test_flow_steps_present(self):
        src = io.open(ROOT / "build_installer.py", encoding="utf-8").read()
        for token in ("--smoke-test", "scan_sensitive_files", "write_version_iss",
                      "find_iscc", "compile_installer", "sha256_of", "write_sha256"):
            self.assertIn(token, src)

    def test_iscc_detection_uses_env_then_common_paths(self):
        src = io.open(ROOT / "build_installer.py", encoding="utf-8").read()
        self.assertIn("INNO_SETUP_COMPILER", src)
        self.assertIn(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe", src)
        self.assertIn(r"C:\Program Files\Inno Setup 6\ISCC.exe", src)

    def test_inno_setup_5_is_fully_removed(self):
        """RC：只允许 Inno Setup 6，Inno 5 的**候选路径**必须消失。

        注意：脚本注释里会写"已移除 Inno Setup 5 支持"，所以只检查代码里
        有没有出现 Inno 5 的路径字面量，而不是整份源码的文本。
        """
        src = io.open(ROOT / "build_installer.py", encoding="utf-8").read()
        self.assertNotIn(r"Inno Setup 5\ISCC.exe", src)
        self.assertNotIn("Inno Setup 5/ISCC.exe", src)
        self.assertEqual(build_installer.ISCC_CANDIDATES, (
            r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
            r"C:\Program Files\Inno Setup 6\ISCC.exe",
        ))
        self.assertEqual(build_installer.ISCC_RELATIVE, (r"Inno Setup 6\ISCC.exe",))
        self.assertEqual(build_installer.ISCC_REQUIRED_MAJOR, 6)
        readme = io.open(ROOT / "installer" / "README.md", encoding="utf-8").read()
        self.assertNotIn("Inno 5", readme)
        self.assertIn("只支持 6", readme)

    def test_iscc_version_rejects_non_six(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "ISCC.exe"
            fake.write_bytes(b"MZ")
            with mock.patch.object(build_installer, "iscc_version", return_value="5.6.1"):
                with self.assertRaises(build_installer.BuildError) as ctx:
                    build_installer._check_iscc_version(fake)
            self.assertIn("只支持 Inno Setup 6", str(ctx.exception))
            with mock.patch.object(build_installer, "iscc_version", return_value=None):
                with self.assertRaises(build_installer.BuildError) as ctx2:
                    build_installer._check_iscc_version(fake)
            self.assertIn("无法识别编译器版本", str(ctx2.exception))
            with mock.patch.object(build_installer, "iscc_version", return_value="6.7.3"):
                self.assertEqual(build_installer._check_iscc_version(fake), fake)

    def test_real_iscc_is_inno_6(self):
        """本机真实编译器必须是 Inno Setup 6（找不到则跳过）。"""
        try:
            iscc = build_installer.find_iscc()
        except build_installer.BuildError:
            self.skipTest("本机没有 Inno Setup 6")
        self.assertEqual(build_installer.iscc_version(iscc).split(".")[0], "6")

    def test_iscc_detection_covers_per_user_install(self):
        r"""winget 默认按用户安装到 %LOCALAPPDATA%\Programs\Inno Setup 6。"""
        with tempfile.TemporaryDirectory() as tmp:
            iscc = Path(tmp) / "Programs" / "Inno Setup 6" / "ISCC.exe"
            iscc.parent.mkdir(parents=True)
            iscc.write_bytes(b"MZ")
            with mock.patch.dict("os.environ", {"LOCALAPPDATA": tmp}, clear=True), \
                    mock.patch.object(build_installer, "ISCC_CANDIDATES", ()), \
                    mock.patch.object(build_installer, "iscc_version", return_value="6.7.3"):
                self.assertEqual(build_installer.find_iscc(), iscc)

    def test_dynamic_candidates_include_localappdata(self):
        with mock.patch.dict("os.environ", {"LOCALAPPDATA": r"C:\Users\X\AppData\Local"},
                             clear=True):
            cands = [str(c) for c in build_installer._dynamic_candidates()]
            self.assertTrue(any("Inno Setup 6" in c and "Programs" in c for c in cands), cands)

    def test_dynamic_candidates_fall_back_when_env_missing(self):
        """宿主 shell 里 LOCALAPPDATA 可能为空，必须回退到 ~/AppData/Local。"""
        fake_home = Path(r"C:\Users\X")
        with mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch("pathlib.Path.home", return_value=fake_home):
            self.assertEqual(build_installer.local_appdata_dir(),
                             fake_home / "AppData" / "Local")
            cands = [str(c) for c in build_installer._dynamic_candidates()]
        self.assertTrue(any("Inno Setup 6" in c for c in cands), cands)

    def test_local_appdata_dir_returns_none_when_home_unknown(self):
        """环境完全为空时不得抛异常（否则 find_iscc 的报错路径会崩）。"""
        with mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch("pathlib.Path.home",
                           side_effect=RuntimeError("Could not determine home directory.")):
            self.assertIsNone(build_installer.local_appdata_dir())
            self.assertIsInstance(build_installer._dynamic_candidates(), list)

    def test_find_iscc_error_message_is_clear(self):
        with mock.patch.dict("os.environ", {}, clear=True), \
                mock.patch.object(build_installer, "ISCC_CANDIDATES", ()):
            with self.assertRaises(build_installer.BuildError) as ctx:
                build_installer.find_iscc()
        self.assertIn("Inno Setup 6 未安装", str(ctx.exception))
        self.assertIn("jrsoftware.org", str(ctx.exception))

    def test_find_iscc_honours_env_var(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "ISCC.exe"
            fake.write_bytes(b"MZ")
            with mock.patch.dict("os.environ", {"INNO_SETUP_COMPILER": str(fake)}), \
                    mock.patch.object(build_installer, "iscc_version", return_value="6.7.3"):
                self.assertEqual(build_installer.find_iscc(), fake)

    def test_explicit_iscc_argument_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake = Path(tmp) / "ISCC.exe"
            fake.write_bytes(b"MZ")
            with mock.patch.object(build_installer, "iscc_version", return_value="6.7.3"):
                self.assertEqual(build_installer.find_iscc(str(fake)), fake)

    def test_smoke_and_scan_can_be_skipped(self):
        with mock.patch.object(build_installer, "run_smoke") as smoke, \
                mock.patch.object(build_installer, "scan_dist") as scan, \
                mock.patch.object(build_installer, "compile_installer") as comp:
            setup = Path(tempfile.mkdtemp()) / "TaiPlan-Setup-0.1.0.exe"
            setup.write_bytes(b"MZ")
            comp.return_value = setup
            with mock.patch.object(build_installer, "find_iscc", return_value=Path("ISCC.exe")):
                rc = build_installer.main(["--skip-smoke", "--skip-scan", "--iscc", "x"])
            self.assertEqual(rc, 0)
            smoke.assert_not_called()
            scan.assert_not_called()

    def test_sha256_generation(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "TaiPlan-Setup-0.1.0.exe"
            f.write_bytes(b"todo app setup")
            side, digest = build_installer.write_sha256(f)
            self.assertEqual(len(digest), 64)
            self.assertRegex(digest, r"^[0-9a-f]{64}$")
            self.assertEqual(side.name, "TaiPlan-Setup-0.1.0.exe.sha256")
            self.assertEqual(io.open(side, encoding="utf-8").read(),
                             f"{digest}  TaiPlan-Setup-0.1.0.exe\n")

    def test_dist_check_rejects_missing_dist(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(build_installer.BuildError):
                build_installer.check_dist(Path(tmp))

    def test_installer_input_scan_passes_on_real_iss(self):
        self.assertEqual(build_installer.scan_installer_inputs(), [])

    def test_installer_input_scan_catches_user_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "Bad.iss"
            bad.write_text(
                '#include "version.iss"\n#define MyAppId "{{8E2F4C1B-7A3D-4E62-9B5F-2C7A1D0E4F83}"\n'
                "[Files]\nSource: \"..\\todo.db\"; DestDir: \"{app}\"\n"
                '[Run]\nFilename: "{app}\\run.bat"\n',
                encoding="utf-8")
            problems = build_installer.scan_installer_inputs(bad)
            self.assertTrue(any("todo.db" in p for p in problems))
            self.assertTrue(any(".bat" in p for p in problems))

    def test_installer_input_scan_catches_localappdata_uninstall_delete(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "Bad.iss"
            bad.write_text(
                '#include "version.iss"\n#define MyAppId "{{8E2F4C1B-7A3D-4E62-9B5F-2C7A1D0E4F83}"\n'
                "[Files]\nSource: \"..\\dist\\TaiPlan\\TaiPlan.exe\"; DestDir: \"{app}\"\n"
                "[UninstallDelete]\nType: filesandordirs; Name: \"{localappdata}\\TaiPlan\"\n",
                encoding="utf-8")
            problems = build_installer.scan_installer_inputs(bad)
            self.assertTrue(any("高风险数据删除" in p for p in problems), problems)

    def test_installer_input_scan_catches_hardcoded_version(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "Bad.iss"
            bad.write_text(
                '#define MyAppVersion "9.9.9"\n'
                '#define MyAppId "{{8E2F4C1B-7A3D-4E62-9B5F-2C7A1D0E4F83}"\n',
                encoding="utf-8")
            problems = build_installer.scan_installer_inputs(bad)
            self.assertTrue(any("version.iss" in p for p in problems), problems)
            self.assertTrue(any("硬编码" in p for p in problems), problems)


class InstalledBuildDetectionTest(unittest.TestCase):
    def test_marker_path_is_next_to_executable(self):
        self.assertEqual(app_metadata.install_marker_path().name, "install.json")

    def test_is_installed_build_false_without_marker(self):
        with mock.patch.object(app_metadata, "install_marker_path",
                               return_value=Path(tempfile.gettempdir()) / "no-such-install.json"):
            self.assertFalse(app_metadata.is_installed_build())
            self.assertEqual(app_metadata.install_info(), {})

    def test_is_installed_build_true_with_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / "install.json"
            marker.write_text('{"version": "0.1.0", "install_type": "inno"}',
                              encoding="utf-8")
            with mock.patch.object(app_metadata, "install_marker_path", return_value=marker):
                self.assertTrue(app_metadata.is_installed_build())
                self.assertEqual(app_metadata.install_info()["install_type"], "inno")


if __name__ == "__main__":
    unittest.main()

class PascalContractTest(unittest.TestCase):
    """RC 修复的静态契约（需要在编译前就守住）。"""

    def _code(self):
        return ISS_TEXT.split("[Code]")[-1]

    def test_stop_running_todo_fails_closed_on_exec_failure(self):
        """Exec 失败时 Result 必须为 False（否则会覆盖运行中的文件）。"""
        code = self._code()
        block = code.split("function StopRunningTodo")[1].split("function ")[0]
        self.assertIn("else", block, "缺少 Exec 失败的 else 分支")
        self.assertIn("Result := False", block.split("else")[1])
        self.assertNotIn("Result := True", block.split("else")[1])

    def test_uninstall_calls_graceful_shutdown_before_deleting(self):
        """卸载必须有自己的 graceful shutdown 检查，不能只靠 [UninstallRun]。"""
        code = self._code()
        self.assertIn("function InitializeUninstall(): Boolean;", code)
        init = code.split("function InitializeUninstall")[1].split("\nfunction ")[0]
        self.assertIn("StopRunningTodo()", init)
        self.assertIn("UninstallSilent()", init)
        self.assertIn("MB_RETRYCANCEL", init)
        self.assertIn("Result := False", init)
        # 不再"只依赖 [UninstallRun]"，但保留它作为第二道保险
        self.assertIn('Parameters: "--shutdown"', ISS_TEXT)

    def _pascal_code(self):
        """[Code] 段的纯代码：扒掉 { } 块注释与 // 行注释。"""
        code = ISS_TEXT.split("[Code]")[-1]
        code = re.sub(r"\{[^}]*\}", "", code, flags=re.DOTALL)
        return re.sub(r"//[^\n]*", "", code)

    def test_no_force_kill_anywhere(self):
        """绝不允许 taskkill /F 之类强杀（剥掉 Pascal 注释后检查）。"""
        body = self._pascal_code()
        for bad in ("taskkill", "TerminateProcess", "WM_CLOSE", "/F /IM",
                    "Stop-Process", "Kill("):
            self.assertNotIn(bad, body, f"[Code] 出现了强杀手段：{bad}")
        # 必须只通过应用自己的 --shutdown 退出
        self.assertIn("'--shutdown'", body)

    def test_install_side_silent_mode_does_not_loop_forever(self):
        """静默安装下 MsgBox 回 Retry 会死循环，必须直接中止。"""
        code = self._code()
        prep = code.split("function PrepareToInstall")[1].split("function ")[0]
        self.assertIn("WizardSilent()", prep)
        self.assertIn("Exit", prep)

    def test_downgrade_warning_logs_and_blocks_silent(self):
        code = self._code()
        init = code.split("function InitializeSetup")[1]
        self.assertIn("检测到降级", init)
        self.assertIn("WizardSilent()", init)
        self.assertIn("MB_YESNO", init)
        self.assertIn("CompareVer(installed", init)

    def test_webview2_warning_only_when_missing(self):
        code = self._code()
        self.assertIn("WebView2Installed()", code)
        self.assertIn("if not WebView2Installed() then", code)

    def test_brace_comments_do_not_contain_braces(self):
        """Pascal 的 { } 注释遇第一个 } 就结束——注释文本里不能有花括号。"""
        for idx, line in enumerate(self._code().splitlines()):
            stripped = line.strip()
            if not stripped.startswith("{") or stripped.startswith("{$"):
                continue
            body = stripped[1:]
            if body.startswith(" "):
                body = body.strip()
            if "}" in body.rstrip("}").rstrip() and not body.endswith("}"):
                continue
            self.assertNotIn("{", body.replace("}", ""),
                             f"[Code] 第 {idx} 行花括号注释里含花括号：{stripped[:60]}")



if __name__ == "__main__":
    unittest.main()
