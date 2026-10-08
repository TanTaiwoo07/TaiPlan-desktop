# -*- coding: utf-8 -*-
"""真正全新用户的首次启动（FRESH INSTALL HOTFIX 回归）。

这是 0.1.0 的发布阻断点：迁移判定之前若写入 TaiPlan 数据目录，
全新用户会被误判为 partial 目标而拒绝启动。本文件同时用
  * 结构测试（AST 检查 main.py 的启动顺序）
  * 分类矩阵（plan()）
  * 真实 Frozen 子进程 + 一次性 LOCALAPPDATA
三层守住它。
"""
import ast
import io
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import tests_env  # noqa: F401

ROOT = Path(__file__).resolve().parent
EXE = ROOT / "dist" / "TaiPlan" / "TaiPlan.exe"


# =============================================================== A 结构测试
class StartupOrderTest(unittest.TestCase):
    """迁移/fresh 判定之前，绝不允许对 TaiPlan 数据目录做任何写入。"""

    def setUp(self):
        self.src = io.open(ROOT / "main.py", encoding="utf-8-sig").read()
        self.tree = ast.parse(self.src)
        self.main = next(n for n in self.tree.body
                         if isinstance(n, ast.FunctionDef) and n.name == "main")

    def _calls(self, node):
        out = []
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                fn = sub.func
                if isinstance(fn, ast.Attribute):
                    base = fn.value.id if isinstance(fn.value, ast.Name) else ""
                    out.append((sub.lineno, f"{base}.{fn.attr}"))
                elif isinstance(fn, ast.Name):
                    out.append((sub.lineno, fn.id))
        return sorted(out)

    def test_no_data_dir_write_before_gate(self):
        calls = self._calls(self.main)
        gate = [ln for ln, name in calls if name == "data_dir_migration.ensure_data_dir"]
        self.assertEqual(len(gate), 1, f"应只调用一次闸门，实际 {gate}")
        gate_line = gate[0]
        early = [(ln, name) for ln, name in calls
                 if name == "app_paths.ensure_user_directories" and ln < gate_line]
        self.assertEqual(early, [],
                         f"闸门之前不得创建数据目录（发现 L{early}）")

    def test_bootstrap_log_dir_set_before_gate(self):
        calls = self._calls(self.main)
        gate = [ln for ln, name in calls if name == "data_dir_migration.ensure_data_dir"][0]
        early = [(ln, name) for ln, name in calls
                 if name in ("app_paths.get_bootstrap_logs_dir",
                             "app_paths.get_user_data_dir", "app_paths.get_logs_dir")
                 and ln < gate]
        self.assertTrue(any("bootstrap" in name for _, name in early),
                        f"闸门前必须先启用 bootstrap 日志目录，实际 {early}")

    def test_all_widget_and_config_writes_come_after_gate(self):
        """config/state/日志 相关目录访问在闸门前一律不允许出现。"""
        calls = self._calls(self.main)
        gate = [ln for ln, name in calls if name == "data_dir_migration.ensure_data_dir"][0]
        banned = ("app_paths.get_config_dir", "app_paths.get_state_dir",
                  "app_paths.get_database_path", "first_run.run_first_run",
                  "database.init_database")
        early = [(ln, n) for ln, n in calls if n in banned and ln < gate]
        self.assertEqual(early, [], f"闸门前发生了数据目录访问: {early}")


# =============================================================== B 分类矩阵
class ClassificationMatrixTest(unittest.TestCase):
    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="plan_matrix_"))
        import app_paths
        import data_dir_migration

        self.ap = app_paths
        self.ddm = data_dir_migration

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def _target(self, name="TaiPlan"):
        return self.base / name

    def _legacy(self, name="TodoApp"):
        return self.base / name

    def _sqlite(self, path, tables=("tasks", "app_meta")):
        ddl = {
            "tasks": ("CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT,"
                      " description TEXT, url TEXT, date TEXT, time TEXT, priority TEXT,"
                      " is_completed INTEGER DEFAULT 0, created_at TEXT, updated_at TEXT,"
                      " completed_at TEXT, is_recurring INTEGER DEFAULT 0,"
                      " recurrence_frequency TEXT, recurrence_interval INTEGER,"
                      " recurrence_end_type TEXT, recurrence_end_date TEXT,"
                      " recurrence_stop_before TEXT, duration_minutes INTEGER);"),
            "app_meta": "CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT);",
        }
        con = sqlite3.connect(str(path))
        for t in tables:
            con.execute(ddl[t])
        con.commit()
        con.close()

    def _plan(self):
        return self.ddm.plan(legacy_dir=self._legacy(), target_dir=self._target())

    def test_neither_dir_is_fresh(self):
        self.assertEqual(self._plan().mode, self.ddm.MODE_FRESH)

    def test_empty_scaffold_dir_is_fresh(self):
        for sub in ("config", "logs", "backups", "state"):
            (self._target() / sub).mkdir(parents=True, exist_ok=True)
        self.assertEqual(self._plan().mode, self.ddm.MODE_FRESH)

    def test_bootstrap_log_only_is_fresh(self):
        (self._target() / "logs").mkdir(parents=True, exist_ok=True)
        (self._target() / "logs" / "runtime.log").write_text("bootstrap", encoding="utf-8")
        (self._target() / "logs" / "runtime.log.1").write_text("rot", encoding="utf-8")
        self.assertEqual(self._plan().mode, self.ddm.MODE_FRESH)

    def test_bootstrap_state_and_default_config_are_fresh(self):
        (self._target() / "state").mkdir(parents=True, exist_ok=True)
        (self._target() / "state" / "runtime_port.json").write_text("{}", encoding="utf-8")
        (self._target() / "config").mkdir(parents=True, exist_ok=True)
        (self._target() / "config" / "appearance.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self._plan().mode, self.ddm.MODE_FRESH)

    def test_unknown_file_is_blocked(self):
        self._target().mkdir(parents=True, exist_ok=True)
        (self._target() / "notes.txt").write_text("mine", encoding="utf-8")
        self.assertEqual(self._plan().mode, self.ddm.MODE_BLOCKED)

    def test_unknown_config_is_blocked(self):
        (self._target() / "config").mkdir(parents=True, exist_ok=True)
        (self._target() / "config" / "custom.json").write_text("{}", encoding="utf-8")
        self.assertEqual(self._plan().mode, self.ddm.MODE_BLOCKED)

    def test_non_log_file_in_logs_is_blocked(self):
        (self._target() / "logs").mkdir(parents=True, exist_ok=True)
        (self._target() / "logs" / "dump.bin").write_bytes(b"\x00\x01")
        self.assertEqual(self._plan().mode, self.ddm.MODE_BLOCKED)

    def test_backups_with_content_is_blocked(self):
        (self._target() / "backups").mkdir(parents=True, exist_ok=True)
        (self._target() / "backups" / "todo-2026.db").write_bytes(b"SQLite format 3\x00")
        self.assertEqual(self._plan().mode, self.ddm.MODE_BLOCKED)

    def test_unmarked_todo_db_is_blocked(self):
        self._target().mkdir(parents=True, exist_ok=True)
        self._sqlite(self._target() / "todo.db", tables=("tasks",))
        self.assertEqual(self._plan().mode, self.ddm.MODE_BLOCKED)

    def test_corrupt_todo_db_is_blocked(self):
        self._target().mkdir(parents=True, exist_ok=True)
        (self._target() / "todo.db").write_bytes(b"not a database" * 32)
        self.assertEqual(self._plan().mode, self.ddm.MODE_BLOCKED)

    def test_valid_target_is_kept(self):
        self._target().mkdir(parents=True, exist_ok=True)
        self._sqlite(self._target() / "todo.db")
        self.ddm._write_marker(self._target(), {"migrated_from": "none"})
        self.assertEqual(self._plan().mode, self.ddm.MODE_KEEP_NEW)

    def test_legacy_only_migrates(self):
        self._legacy().mkdir(parents=True, exist_ok=True)
        self._sqlite(self._legacy() / "todo.db")
        self.assertEqual(self._plan().mode, self.ddm.MODE_MIGRATE)

    def test_interrupted_staging_resumes(self):
        self._target().mkdir(parents=True, exist_ok=True)
        staging = self.base / f"{self.ap.APP_DIR_NAME}{self.ap.STAGING_INFIX}xyz"
        staging.mkdir(parents=True, exist_ok=True)
        self._sqlite(staging / "todo.db")
        self.ddm._write_marker(staging, {"staged": True})
        self.assertEqual(self._plan().mode, self.ddm.MODE_RESUME)

    def test_blocked_reason_names_the_actual_offender(self):
        self._target().mkdir(parents=True, exist_ok=True)
        (self._target() / "notes.txt").write_text("mine", encoding="utf-8")
        reason = self._plan().reason
        self.assertIn("notes.txt", reason)


# =============================================================== C bootstrap 日志
class BootstrapLoggingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="boot_log_"))
        self._env = {k: os.environ.get(k) for k in
                     ("TODO_APP_DATA_DIR", "TODO_APP_BOOTSTRAP_LOG_DIR")}
        os.environ["TODO_APP_DATA_DIR"] = str(self.tmp / "TaiPlan")
        import logging_config

        self.lc = logging_config

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_logs_go_to_bootstrap_dir_then_official(self):
        import app_paths

        boot = self.tmp / "boot"
        os.environ[app_paths.BOOTSTRAP_LOG_DIR_ENV] = str(boot)
        self.assertEqual(app_paths.get_logs_dir(), boot)
        self.assertFalse((self.tmp / "TaiPlan").exists(),
                         "bootstrap 阶段不得创建正式数据目录")

        logger = self.lc.setup_logging("runtime")
        logger.info("hello bootstrap")
        for h in logger.handlers:
            h.flush()
        self.assertTrue((boot / "runtime.log").is_file(), "bootstrap 日志应写在 %TEMP% 目录")
        self.assertFalse((self.tmp / "TaiPlan" / "logs").exists(),
                         "bootstrap 阶段不得在数据目录里建 logs")

        os.environ.pop(app_paths.BOOTSTRAP_LOG_DIR_ENV, None)
        switched = self.lc.rebind_to_official_logs()
        self.assertIn("runtime", switched)
        logger.info("hello official")
        for h in logger.handlers:
            h.flush()
        self.assertTrue((self.tmp / "TaiPlan" / "logs" / "runtime.log").is_file(),
                        "判定通过后日志应回到正式数据目录")

    def test_no_dialogs_env_flag_is_respected(self):
        sys.path.insert(0, str(ROOT))
        import main as app_main

        os.environ["TODO_APP_NO_GUI_DIALOGS"] = "1"
        try:
            self.assertTrue(app_main._dialogs_suppressed())
        finally:
            os.environ.pop("TODO_APP_NO_GUI_DIALOGS", None)
        self.assertFalse(app_main._dialogs_suppressed())


# =============================================================== D 真实 Frozen 首次启动
class FrozenFirstLaunchTest(unittest.TestCase):
    """用一次性 LOCALAPPDATA 等价"朋友的全新电脑"，跑真实 EXE。"""

    def setUp(self):
        if not EXE.is_file():
            self.fail(f"未找到 {EXE}；这些是发布阻断测试，必须先运行 build.py（不跳过）")
        self.tmp = Path(tempfile.mkdtemp(prefix="freshprofile_"))
        (self.tmp / "Temp").mkdir(parents=True, exist_ok=True)
        self.env = dict(os.environ)
        self.env.update({
            "LOCALAPPDATA": str(self.tmp),
            "USERPROFILE": str(self.tmp),
            "TEMP": str(self.tmp / "Temp"),
            "TMP": str(self.tmp / "Temp"),
            "TODO_APP_NO_GUI_DIALOGS": "1",
        })
        for k in ("TODO_APP_DATA_DIR", "TODO_APP_LEGACY_APP_DIR", "TODO_APP_DISABLE_MIGRATION",
                  "TODO_APP_BOOTSTRAP_LOG_DIR"):
            self.env.pop(k, None)
        self._proc = None

    def tearDown(self):
        subprocess.run([str(EXE), "--shutdown"], cwd=str(EXE.parent), env=self.env,
                       capture_output=True, text=True, errors="replace")
        # 兜底：某些场景（例如弹窗被抑制前的旧进程）可能残留，占住 8501 会影响后续用例
        deadline = time.time() + 25
        while time.time() < deadline and self._port_open(8501):
            time.sleep(1)
        if self._port_open(8501):
            if self._proc is not None and self._proc.poll() is None:
                self._proc.kill()
                try:
                    self._proc.wait(timeout=10)
                except Exception:  # noqa: BLE001
                    pass
        time.sleep(3)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _port_open(self, port):
        s = socket.socket()
        s.settimeout(0.8)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False
        finally:
            s.close()

    def _launch(self, wait_ui=True, timeout=140):
        if self._port_open(8501):
            subprocess.run([str(EXE), "--shutdown"], cwd=str(EXE.parent), env=self.env,
                           capture_output=True, text=True, errors="replace")
            deadline = time.time() + 25
            while time.time() < deadline and self._port_open(8501):
                time.sleep(1)
            self.assertFalse(self._port_open(8501), "8501 被占用，无法进行首次启动测试")
        proc = subprocess.Popen([str(EXE)], cwd=str(EXE.parent), env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace")
        self._proc = proc
        if not wait_ui:
            try:
                out, _ = proc.communicate(timeout=60)
            except subprocess.TimeoutExpired:
                proc.kill()
                out, _ = proc.communicate()
            return proc, out or ""
        end = time.time() + timeout
        while time.time() < end:
            if self._port_open(8501):
                time.sleep(6)
                return proc, ""
            if proc.poll() is not None:
                out, _ = proc.communicate()
                return proc, out or ""
            time.sleep(2)
        proc.kill()
        out, _ = proc.communicate()
        return proc, out or ""

    def _tasks(self):
        db = self.tmp / "TaiPlan" / "todo.db"
        if not db.is_file():
            return None
        con = sqlite3.connect("file:" + db.as_posix() + "?mode=ro", uri=True)
        try:
            return con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        finally:
            con.close()

    def test_01_clean_machine_first_launch_succeeds(self):
        """no TodoApp, no TaiPlan → 必须直接可用，且不得出现 migration error。"""
        proc, out = self._launch()
        self.assertIsNone(proc.poll(), f"全新机器首次启动不应退出: {out[-400:]}")

# =============================================================== E 补充覆盖
class MissingCoverageTest(unittest.TestCase):
    """FRESH INSTALL HOTFIX 补充：判定顺序 + 程序自产的空备份。

    这两条都不是"换个写法的重复用例"：
      * 判定顺序——只有它才能发现"日志/配置在判定之前先建了数据目录"
      * 空备份——真实机器上正是它把全新用户卡在 migration error
    """

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="fresh_extra_"))
        self.target = self.base / "TaiPlan"
        self.legacy = self.base / "TodoApp"
        self._env = os.environ.get("TODO_APP_DATA_DIR")
        os.environ.pop("TODO_APP_DATA_DIR", None)
        self._db_env = None

    def tearDown(self):
        if self._env is not None:
            os.environ["TODO_APP_DATA_DIR"] = self._env
        shutil.rmtree(self.base, ignore_errors=True)

    def _sqlite(self, path, rows=0):
        path.parent.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(str(path))
        con.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    " title TEXT, is_completed INTEGER DEFAULT 0)")
        con.execute("CREATE TABLE app_meta (key TEXT PRIMARY KEY, value TEXT)")
        for i in range(rows):
            con.execute("INSERT INTO tasks (title, is_completed) VALUES (?, 0)", (f"t{i}",))
        con.commit()
        con.close()

    def test_ensure_data_dir_decides_before_creating_anything(self):
        """判定必须发生在任何 TaiPlan 写入之前（顺序回归）。"""
        from unittest import mock

        import app_paths
        import data_dir_migration as ddm

        self.assertFalse(self.target.exists(), "前置：目标目录不应存在")
        with mock.patch.object(app_paths, "get_user_data_dir", lambda: self.target), \
                mock.patch.object(app_paths, "get_legacy_app_dir", lambda: self.legacy):
            result = ddm.ensure_data_dir(force=True)
        self.assertTrue(result.ok)
        self.assertEqual(result.mode, ddm.MODE_FRESH)
        self.assertTrue(self.target.is_dir(), "判定通过后才应创建目录")
        self.assertTrue(ddm.has_first_run_marker(self.target),
                        "全新用户必须留下首次运行标记，下一次判定才不会被判成无标记库")

    def test_fresh_then_second_check_keeps_going(self):
        """全新用户第一次判定之后（运行期已建好空库）再次判定，必须仍然放行。"""
        from unittest import mock

        import app_paths
        import data_dir_migration as ddm

        with mock.patch.object(app_paths, "get_user_data_dir", lambda: self.target), \
                mock.patch.object(app_paths, "get_legacy_app_dir", lambda: self.legacy):
            self.assertTrue(ddm.ensure_data_dir(force=True).ok)
            # 模拟运行期建库 + SQLite 侧车文件
            self._sqlite(self.target / "todo.db")
            (self.target / "todo.db-wal").touch()
            (self.target / "todo.db-shm").touch()
            (self.target / "logs").mkdir(exist_ok=True)
            (self.target / "logs" / "app.log").write_text("run", encoding="utf-8")
            second = ddm.ensure_data_dir(force=True)
        self.assertTrue(second.ok, f"第二次判定不应阻断：{second.detail}")
        self.assertIn(second.mode, (ddm.MODE_KEEP_NEW, ddm.MODE_FRESH))

    def test_program_generated_empty_backup_is_not_user_data(self):
        """程序自己生成的空备份（含 WAL 侧车）不是用户数据，不得阻断全新用户。"""
        import data_dir_migration as ddm

        self.target.mkdir(parents=True, exist_ok=True)
        ddm._write_first_run_marker(self.target, {"kind": "fresh_user"})
        self._sqlite(self.target / "todo.db")
        bk = self.target / "backups"
        bk.mkdir()
        self._sqlite(bk / "todo_auto_20261007.db")
        decision = ddm.plan(self.legacy, self.target)
        self.assertIn(decision.mode, (ddm.MODE_KEEP_NEW, ddm.MODE_FRESH),
                      f"空备份不应阻断：{decision.reason}")

    def test_backup_with_user_rows_is_ignored_while_main_db_is_healthy(self):
        """主库健康时，backups/ 里的备份是程序产物（自动备份/手动备份都落这里），
        不应反过来把用户拦在门外；"主库丢了 + 备份有数据"才是停止信号
        （见 FreshUserWithDataTest.test_missing_db_with_data_backups_is_blocked）。"""
        import data_dir_migration as ddm

        self.target.mkdir(parents=True, exist_ok=True)
        ddm._write_first_run_marker(self.target, {"kind": "fresh_user"})
        self._sqlite(self.target / "todo.db")
        bk = self.target / "backups"
        bk.mkdir()
        self._sqlite(bk / "todo_user_backup.db", rows=3)
        decision = ddm.plan(self.legacy, self.target)
        self.assertIn(decision.mode, (ddm.MODE_KEEP_NEW, ddm.MODE_FRESH),
                      f"主库健康时备份不该阻断：{decision.reason}")


# ======================================================= F 严格版首次启动（反假绿）
class FrozenFreshUserWithDataTest(unittest.TestCase):
    """冻结版回归：全新用户库里有了自己的任务、目录里有了导入前备份之后，
    第二次启动**不得**被自己的数据判定拦住（这是 0.1.0 一直存在的真实故障）。"""

    def setUp(self):
        if not EXE.is_file():
            self.fail(f"未找到 {EXE}；请先运行 build.py（不跳过）")
        self.tmp = Path(tempfile.mkdtemp(prefix="freshdata_"))
        (self.tmp / "Temp").mkdir(parents=True, exist_ok=True)
        self.env = dict(os.environ)
        self.env.update({
            "LOCALAPPDATA": str(self.tmp),
            "APPDATA": str(self.tmp),
            "USERPROFILE": str(self.tmp),
            "TEMP": str(self.tmp / "Temp"),
            "TMP": str(self.tmp / "Temp"),
            "TODO_APP_NO_GUI_DIALOGS": "1",
        })
        for k in ("TODO_APP_DATA_DIR", "TODO_APP_LEGACY_APP_DIR", "TODO_APP_DISABLE_MIGRATION",
                  "TODO_APP_BOOTSTRAP_LOG_DIR"):
            self.env.pop(k, None)
        self._proc = None

    def tearDown(self):
        subprocess.run([str(EXE), "--shutdown"], cwd=str(EXE.parent), env=self.env,
                       capture_output=True, text=True, errors="replace")
        deadline = time.time() + 25
        while time.time() < deadline and self._port_open(8501):
            time.sleep(1)
        if self._port_open(8501) and self._proc is not None and self._proc.poll() is None:
            self._proc.kill()
            try:
                self._proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                pass
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        "Get-Process TaiPlan -ErrorAction SilentlyContinue | "
                        "Where-Object { $_.Path -like '*freshdata_*' } | Stop-Process -Force"],
                       capture_output=True, text=True, errors="replace")
        time.sleep(2)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _port_open(self, port):
        s = socket.socket()
        s.settimeout(0.8)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False
        finally:
            s.close()

    def _launch(self, settle=20, timeout=150):
        proc = subprocess.Popen([str(EXE)], cwd=str(EXE.parent), env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace")
        self._proc = proc
        end = time.time() + timeout
        ui = False
        while time.time() < end:
            if self._port_open(8501):
                ui = True
                break
            if proc.poll() is not None:
                break
            time.sleep(2)
        if ui:
            time.sleep(settle)
        return proc, ui

    def _logs(self):
        text = ""
        for name in ("runtime.log", "streamlit.log", "app.log"):
            f = self.tmp / "TaiPlan" / "logs" / name
            if f.is_file():
                text += io.open(f, encoding="utf-8", errors="replace").read()
        return text

    def _stop(self, proc):
        subprocess.run([str(EXE), "--shutdown"], cwd=str(EXE.parent), env=self.env,
                       capture_output=True, text=True, errors="replace")
        end = time.time() + 30
        while time.time() < end and proc.poll() is None:
            time.sleep(1)
        if proc.poll() is None:
            proc.kill()

    def test_second_launch_after_user_data_exists(self):
        db = self.tmp / "TaiPlan" / "todo.db"
        proc, ui = self._launch(settle=12)
        self.assertTrue(ui, "首次启动应先正常工作")
        self._stop(proc)
        self.assertTrue(db.is_file(), "首次启动应创建空库")

        # 模拟用户自己建了任务 + 导入/手动备份（备份里含数据）
        con = sqlite3.connect(str(db))
        con.execute("INSERT INTO tasks (title, date, is_completed, created_at, updated_at)"
                    " VALUES (?, ?, 0, ?, ?)",
                    ("我自己建的任务", "2026-10-07", "2026-10-07T09:00:00",
                     "2026-10-07T09:00:00"))
        con.commit()
        con.close()
        bk = self.tmp / "TaiPlan" / "backups"
        bk.mkdir(parents=True, exist_ok=True)
        shutil.copy2(db, bk / "todo_auto_with_data.db")

        proc2, ui2 = self._launch(settle=18)
        try:
            logs = self._logs()
            self.assertTrue(ui2, "有数据之后第二次启动仍应正常\n" + logs[-1200:])
            self.assertNotIn("数据目录状态异常", logs,
                             "用户自己的任务/备份不该把他拦在门外\n" + logs[-1200:])
            self.assertNotIn("migration_blocked", logs)
            con = sqlite3.connect("file:" + db.as_posix() + "?mode=ro", uri=True)
            try:
                self.assertEqual(con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 1,
                                 "既有任务不得被改动或删除")
            finally:
                con.close()
        finally:
            self._stop(proc2)


class FrozenFirstLaunchStrictTest(unittest.TestCase):
    """比 D 组更严：UI 起来之后还要确认**页面没有报迁移阻断**。

    D 组只看"进程没退出 + 端口通"，页面上渲染出 migration error 也可能混过；
    这正是 0.1.0 在真实新电脑上漏掉的点。
    """

    def setUp(self):
        if not EXE.is_file():
            self.fail(f"未找到 {EXE}；请先运行 build.py（不跳过）")
        self.tmp = Path(tempfile.mkdtemp(prefix="freshstrict_"))
        (self.tmp / "Temp").mkdir(parents=True, exist_ok=True)
        self.env = dict(os.environ)
        self.env.update({
            "LOCALAPPDATA": str(self.tmp),
            "APPDATA": str(self.tmp),
            "USERPROFILE": str(self.tmp),
            "TEMP": str(self.tmp / "Temp"),
            "TMP": str(self.tmp / "Temp"),
            "TODO_APP_NO_GUI_DIALOGS": "1",
        })
        for k in ("TODO_APP_DATA_DIR", "TODO_APP_LEGACY_APP_DIR", "TODO_APP_DISABLE_MIGRATION",
                  "TODO_APP_BOOTSTRAP_LOG_DIR"):
            self.env.pop(k, None)
        self._proc = None

    def tearDown(self):
        subprocess.run([str(EXE), "--shutdown"], cwd=str(EXE.parent), env=self.env,
                       capture_output=True, text=True, errors="replace")
        deadline = time.time() + 25
        while time.time() < deadline and self._port_open(8501):
            time.sleep(1)
        if self._port_open(8501) and self._proc is not None and self._proc.poll() is None:
            self._proc.kill()
            try:
                self._proc.wait(timeout=10)
            except Exception:  # noqa: BLE001
                pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _port_open(self, port):
        s = socket.socket()
        s.settimeout(0.8)
        try:
            s.connect(("127.0.0.1", port))
            return True
        except OSError:
            return False
        finally:
            s.close()

    def _launch_and_settle(self):
        if self._port_open(8501):
            subprocess.run([str(EXE), "--shutdown"], cwd=str(EXE.parent), env=self.env,
                           capture_output=True, text=True, errors="replace")
            deadline = time.time() + 25
            while time.time() < deadline and self._port_open(8501):
                time.sleep(1)
            self.assertFalse(self._port_open(8501), "8501 被占用，无法进行首次启动测试")
        proc = subprocess.Popen([str(EXE)], cwd=str(EXE.parent), env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, errors="replace")
        self._proc = proc
        end = time.time() + 150
        while time.time() < end and not self._port_open(8501):
            if proc.poll() is not None:
                break
            time.sleep(2)
        self.assertTrue(self._port_open(8501), "全新用户首次启动没有起来 UI")
        # 等 Streamlit 页面真正跑完一轮脚本（迁移阻断就是在这里渲染出来的）
        time.sleep(20)
        return proc

    def _app_logs(self):
        text = ""
        for name in ("runtime.log", "streamlit.log", "app.log", "reminder_worker.log"):
            f = self.tmp / "TaiPlan" / "logs" / name
            if f.is_file():
                text += io.open(f, encoding="utf-8", errors="replace").read()
        return text

    def test_clean_profile_shows_no_migration_error_on_page(self):
        proc = self._launch_and_settle()
        logs = self._app_logs()
        self.assertIsNone(proc.poll(), f"首次启动进程不应退出：{logs[-400:]}")
        self.assertNotIn("数据目录状态异常", logs,
                         "全新机器首次启动不得在页面上出现迁移阻断；日志：\n" + logs[-1200:])
        self.assertNotIn("migration_blocked", logs)
        self.assertFalse((self.tmp / "TodoApp").exists(), "全新机器不该凭空出现旧目录")
        db = self.tmp / "TaiPlan" / "todo.db"
        self.assertTrue(db.is_file(), "全新用户应正常创建空库")
        con = sqlite3.connect("file:" + db.as_posix() + "?mode=ro", uri=True)
        try:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0,
                             "空库不得含 demo 任务")
        finally:
            con.close()
        for bad in ("Traceback", "ModuleNotFoundError", "AttributeError"):
            self.assertNotIn(bad, logs, f"日志出现 {bad}")

class FreshUserWithDataTest(unittest.TestCase):
    """全新用户**建了任务/导入过数据之后**不能被自己的应用锁在门外。

    真实故障（0.1.0 起一直存在，0.1.1 冻结端到端测试抓到）：
    全新用户的库没有"迁移完成"标记，一旦库里有了任务、或目录里出现了
    导入前自动生成的备份，下一次启动的页面判定就判 BLOCKED。
    """

    def setUp(self):
        self.base = Path(tempfile.mkdtemp(prefix="fresh_data_"))
        self.target = self.base / "TaiPlan"
        self.legacy = self.base / "TodoApp"
        self._env = os.environ.get("TODO_APP_DATA_DIR")
        # 必须指向临时目录：data_backup.backup_database() 会按它决定备份落到哪里，
        # 否则测试会往真实用户目录写备份（曾经发生过，已改）。
        os.environ["TODO_APP_DATA_DIR"] = str(self.target)
        import data_dir_migration as ddm

        self.ddm = ddm

    def tearDown(self):
        if self._env is None:
            os.environ.pop("TODO_APP_DATA_DIR", None)
        else:
            os.environ["TODO_APP_DATA_DIR"] = self._env
        if hasattr(self, "_db_path_before"):
            import database

            database.DB_PATH = self._db_path_before
        shutil.rmtree(self.base, ignore_errors=True)

    def _fresh_init(self):
        """模拟程序自己初始化过的目录：首次运行标记 + 空库。"""
        import database

        if not hasattr(self, "_db_path_before"):
            self._db_path_before = database.DB_PATH      # 结束后必须还原，避免污染其它用例
        self.target.mkdir(parents=True, exist_ok=True)
        (self.target / "state").mkdir(exist_ok=True)
        self.ddm._write_first_run_marker(self.target, {}, fresh=True)
        database.DB_PATH = str(self.target / "todo.db")
        database.init_database()

    def _add_task(self, title="我自己建的任务"):
        import services

        services.create_task(title, date="2026-10-07")

    def _make_backup_with_data(self):
        import data_backup

        return data_backup.backup_database()

    def test_fresh_user_with_own_tasks_is_not_blocked(self):
        self._fresh_init()
        self._add_task()
        decision = self.ddm.plan(self.legacy, self.target)
        self.assertIn(decision.mode, (self.ddm.MODE_KEEP_NEW, self.ddm.MODE_FRESH),
                      f"自己建的任务不该把自己挡住：{decision.reason}")

    def test_fresh_user_after_import_backup_is_not_blocked(self):
        """导入前会自动备份；备份里含数据也不能反过来把用户拦住。"""
        self._fresh_init()
        self._add_task("导入进来的任务")
        path = self._make_backup_with_data()
        self.assertTrue(Path(path).is_file(), "应生成备份")
        decision = self.ddm.plan(self.legacy, self.target)
        self.assertIn(decision.mode, (self.ddm.MODE_KEEP_NEW, self.ddm.MODE_FRESH),
                      f"有备份不该被拦住：{decision.reason}")

    def test_missing_db_with_data_backups_is_blocked(self):
        """主库丢了、备份里有数据：必须停下来（防止静默建空库）。"""
        self._fresh_init()
        self._add_task()
        self._make_backup_with_data()
        (self.target / "todo.db").unlink()
        decision = self.ddm.plan(self.legacy, self.target)
        self.assertEqual(decision.mode, self.ddm.MODE_BLOCKED,
                         "主库缺失 + 备份有数据时必须 BLOCKED")

    def test_unmarked_db_without_first_run_is_still_blocked(self):
        """没有首次运行痕迹的无标记库仍然不被信任（W3 保护语义不削弱）。"""
        import sqlite3 as _sq

        self.target.mkdir(parents=True, exist_ok=True)
        con = _sq.connect(str(self.target / "todo.db"))
        con.execute("CREATE TABLE tasks (id INTEGER PRIMARY KEY, title TEXT)")
        con.execute("INSERT INTO tasks (title) VALUES ('unknown')")
        con.commit()
        con.close()
        self.assertEqual(self.ddm.plan(self.legacy, self.target).mode, self.ddm.MODE_BLOCKED)

    def test_blocked_hint_does_not_claim_legacy_without_legacy(self):
        """全新用户不该看到"旧数据仍在旧目录"这种无中生有的提示。"""
        zh = io.open(ROOT / "i18n" / "zh_CN.py", encoding="utf-8-sig").read()
        en = io.open(ROOT / "i18n" / "en_US.py", encoding="utf-8-sig").read()
        self.assertNotIn("TodoApp", zh.split('"error.migration_blocked_hint"')[1][:160])
        self.assertNotIn("TodoApp", en.split('"error.migration_blocked_hint"')[1][:200])
        src = io.open(ROOT / "app.py", encoding="utf-8-sig").read()
        self.assertIn("has_legacy", src, "旧目录提示必须按实际存在与否显示")

class StateConfigAllowlistTest(unittest.TestCase):
    """程序自己会写进 state/ 与 config/ 的文件，必须都在 planner 的白名单里。

    真实故障：0.1.1 的 .ics 导入把 state/ics_import.json 写进数据目录，
    而白名单里没有它 → 用户导入成功后页面判定 "unexpected content in state/"，
    直接把人锁在自己的应用外面（冻结端到端测试抓到的第二个同类问题）。
    这条守卫让"新增状态文件却没进白名单"立刻变红。
    """

    SKIP = (".venv" + os.sep, "build" + os.sep, "dist" + os.sep, "release" + os.sep)

    def _sources(self):
        root = Path(__file__).resolve().parent
        for path in root.rglob("*.py"):
            text = str(path)
            if any(k in text for k in self.SKIP) or path.name.startswith("test_"):
                continue
            yield path, io.open(path, encoding="utf-8-sig", errors="replace").read()

    def _literals(self, call_name, const_name=None):
        """收集 call_name("x.json") 与 const_name = "x.json" 里的文件名。"""
        import re as _re

        call_re = _re.compile(call_name + r"\(\s*[\"']([^\"']+)[\"']")
        const_re = (_re.compile("(?:" + const_name + r")\s*=\s*[\"']([^\"']+)[\"']")
                    if const_name else None)
        found = set()
        for _path, src in self._sources():
            for item in call_re.findall(src):
                found.add(item if isinstance(item, str) else item[0])
            if const_re is not None:
                for item in const_re.findall(src):
                    found.add(item if isinstance(item, str) else item[0])
        return found

    def test_every_state_file_is_allowlisted(self):
        import data_dir_migration as ddm

        used = self._literals("get_state_path",
                              "[A-Z_]*STATE_FILENAME|FIRST_RUN_FILENAME")
        missing = sorted(used - set(ddm._BOOTSTRAP_STATE_FILES))
        self.assertEqual(missing, [], f"state/ 白名单缺少：{missing}")

    def test_every_config_file_is_allowlisted(self):
        import data_dir_migration as ddm

        used = self._literals("get_config_path")
        missing = sorted(used - set(ddm._KNOWN_CONFIG_FILES))
        self.assertEqual(missing, [], f"config/ 白名单缺少：{missing}")