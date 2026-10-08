# -*- coding: utf-8 -*-
"""W3 数据目录迁移测试。

覆盖（用户要求 14 项 + 新增 5 项重点）：
  old only / new only / both / neither / 二次启动幂等 / corrupt DB /
  interrupted(半成品) / integrity 失败 / partial target /
  legacy 凭据 only / 新凭据 only / 两边都有凭据 / keyring 不可用 /
  runtime state 不迁移 / 新用户无 demo 任务 /
  ← 新增：partial target 不被当成功 / 中断后再次启动 / 迁移用户无 demo /
          runtime_status 最终为空 / 凭据写新身份失败时 legacy fallback 可用

全部使用 tempfile 与 fake keyring；绝不读写真实的 %LOCALAPPDATA%\\TaiPlan。
"""
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import tests_env  # noqa: F401  必须早于项目模块导入

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import app_paths  # noqa: E402
import data_dir_migration as ddm  # noqa: E402


# --------------------------------------------------------------- fake keyring

class FakeKeyring:
    """内存凭据库。可配置 set_password 失败，用于验证 fallback。"""

    def __init__(self):
        self.store = {}
        self.fail_write = False
        self.calls = []

    def get_password(self, service, account):
        self.calls.append(("get", service, account))
        return self.store.get((service, account))

    def set_password(self, service, account, value):
        if self.fail_write:
            raise RuntimeError("fake keyring write failure")
        self.calls.append(("set", service, account))
        self.store[(service, account)] = value

    def delete_password(self, service, account):
        self.calls.append(("delete", service, account))
        self.store.pop((service, account), None)


class Base(unittest.TestCase):
    """搭建 legacy / target 临时目录，并把环境变量指向它们。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="w3mig_"))
        self.legacy = self.tmp / "TodoApp"
        self.target = self.tmp / "TaiPlan"
        (self.legacy / "config").mkdir(parents=True)
        (self.legacy / "state").mkdir(parents=True)
        (self.legacy / "logs").mkdir(parents=True)
        (self.legacy / "backups").mkdir(parents=True)
        # 记录原值：清理时必须"恢复"，绝不能删除——删掉会让后续测试回落到真实目录
        self._prev_env = {k: os.environ.get(k)
                          for k in ("TODO_APP_DATA_DIR", "TODO_APP_LEGACY_APP_DIR",
                                    ddm.DISABLE_ENV)}
        os.environ["TODO_APP_DATA_DIR"] = str(self.target)
        os.environ["TODO_APP_LEGACY_APP_DIR"] = str(self.legacy)
        self.addCleanup(self._cleanup_env)

        import ai_settings

        self.fake = FakeKeyring()
        self._orig_keyring = ai_settings.keyring
        ai_settings.keyring = self.fake
        self.addCleanup(self._restore_keyring)

    def _cleanup_env(self):
        """恢复（而非删除）环境变量：删除会让后续测试回落到真实数据目录。"""
        for key, value in getattr(self, "_prev_env", {}).items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        # 断言：清理后若原本就有覆盖值，必须仍然存在
        prev = getattr(self, "_prev_env", {}).get("TODO_APP_DATA_DIR")
        if prev is not None:
            assert os.environ.get("TODO_APP_DATA_DIR") == prev, "环境变量未被正确恢复"

    def test_00_env_isolation_guard(self):
        """自检：迁移测试期间 TODO_APP_DATA_DIR 必须指向临时目录。"""
        data_dir = Path(os.environ["TODO_APP_DATA_DIR"])
        real = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        self.assertNotEqual(data_dir, real / "TaiPlan")
        self.assertTrue(str(data_dir).startswith(str(self.tmp)))

    def _restore_keyring(self):
        import ai_settings

        ai_settings.keyring = self._orig_keyring

    # ---------- fixture ----------

    def make_legacy_db(self, tasks=5, with_runtime_state=True, corrupt=False):
        db = self.legacy / "todo.db"
        if corrupt:
            db.write_bytes(b"this is not a sqlite database at all" * 10)
            return db
        import database

        database.DB_PATH = str(db)
        database.init_database()
        import services

        for i in range(tasks):
            services.create_task(f"任务 {i}", date="2026-10-06", time="09:00",
                                 duration_minutes=30)
        conn = sqlite3.connect(str(db))
        try:
            # 业务表：occurrence states / overrides / notification_log
            conn.execute("INSERT OR REPLACE INTO task_occurrence_states"
                         "(task_id, occurrence_key, status, created_at, updated_at)"
                         " VALUES (1, '2026-10-06T09:00', 'completed', '2026-10-06', '2026-10-06')")
            conn.execute("INSERT OR REPLACE INTO task_occurrence_overrides"
                         "(task_id, occurrence_key, override_date, override_time,"
                         " override_duration_minutes, created_at, updated_at)"
                         " VALUES (1, '2026-10-06T09:00', '2026-10-07', '10:00', 30,"
                         " '2026-10-06', '2026-10-06')")
            conn.execute("INSERT OR REPLACE INTO notification_log"
                         "(notification_key, task_id, occurrence_key, notification_type,"
                         " fired_at, delivery_status, retry_count, title_snapshot, due_at,"
                         " priority_snapshot)"
                         " VALUES ('task:1:due:2026-10-06T09:00', 1, NULL, 'due', '2026-10-06',"
                         " 'delivered', 0, '任务 0', '2026-10-06T09:00', 'normal')")
            if with_runtime_state:
                conn.execute("INSERT OR REPLACE INTO runtime_status"
                             "(component, pid, heartbeat_at, started_at, status)"
                             " VALUES ('streamlit', 12345, '2026-10-06', '2026-10-06', 'running')")
            conn.commit()
        finally:
            conn.close()
        return db

    def make_legacy_configs(self, language="en-US"):
        cfg = self.legacy / "config"
        (cfg / "appearance.json").write_text('{"mode": "深色", "density": "紧凑"}', encoding="utf-8")
        (cfg / "ai_config.json").write_text(json.dumps({
            "enabled": True, "api_type": "openai_responses",
            "base_url": "https://api.deepseek.com", "model_id": "deepseek-flash",
        }), encoding="utf-8")
        (cfg / "notification_config.json").write_text('{"enabled": false}', encoding="utf-8")
        (cfg / "calendar_config.json").write_text('{"show_weekend": true}', encoding="utf-8")
        (cfg / "desktop_config.json").write_text('{"close_to_tray": true}', encoding="utf-8")
        (cfg / "language_config.json").write_text(json.dumps({"language": language}),
                                                  encoding="utf-8")
        # 不应迁移的
        (self.legacy / "state" / "runtime_port.json").write_text('{"port": 8501}', encoding="utf-8")
        (self.legacy / "state" / "first_run.json").write_text('{"app_version": "0.1.0"}',
                                                              encoding="utf-8")
        (self.legacy / "state" / "migration_state.json").write_text('{"completed": true}',
                                                                    encoding="utf-8")
        (self.legacy / "logs" / "app.log").write_text("log line", encoding="utf-8")
        (self.legacy / "backups" / "todo_20261004_163713.db").write_bytes(b"backup-bytes")
        (self.legacy / "todo.db-wal").write_bytes(b"stale wal")
        (self.legacy / "todo.db-shm").write_bytes(b"stale shm")

    def make_new_valid(self, tasks=3):
        """构造一个"有效的新库"（库 + 成功标记）。"""
        import database

        self.target.mkdir(parents=True, exist_ok=True)
        database.DB_PATH = str(self.target / "todo.db")
        database.init_database()
        import services

        for i in range(tasks):
            services.create_task(f"新库任务 {i}", date="2026-10-07", time="10:00")
        ddm._write_marker(self.target, {"rows": {}, "source_dir": "test"})

    def counts(self, db_path):
        return ddm._table_counts(Path(db_path))

    def legacy_snapshot(self):
        """legacy 目录内容快照（排除 SQLite 正常产生的 -wal/-shm 边车）。"""
        snap = {}
        for p in sorted(self.legacy.rglob("*")):
            if p.is_file() and not p.name.endswith(("-wal", "-shm")):
                snap[str(p.relative_to(self.legacy))] = p.read_bytes()
        return snap


# --------------------------------------------------------------- 场景

class OldOnlyTest(Base):
    def test_old_only_migrates(self):
        self.make_legacy_db(tasks=5)
        self.make_legacy_configs()
        before = self.legacy_snapshot()
        legacy_counts = self.counts(self.legacy / "todo.db")

        result = ddm.migrate(force=True, legacy_dir=self.legacy, target_dir=self.target)

        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.mode, ddm.MODE_MIGRATE)
        self.assertTrue((self.target / "todo.db").is_file())
        self.assertTrue(ddm.has_success_marker(self.target))
        # 业务表行数一致
        self.assertEqual(self.counts(self.target / "todo.db"), legacy_counts)
        # runtime_status 必须被清空
        self.assertEqual(self.counts(self.target / "todo.db")["tasks"], 5)
        conn = sqlite3.connect(str(self.target / "todo.db"))
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM runtime_status").fetchone()[0], 0)
        finally:
            conn.close()
        # legacy 一字节未改
        self.assertEqual(self.legacy_snapshot(), before)
        # 配置白名单都复制了
        for name in ddm.CONFIG_WHITELIST:
            self.assertTrue((self.target / "config" / name).is_file(), name)
        # 不迁移：logs / state 旧文件 / wal / shm
        self.assertFalse((self.target / "logs").exists())
        self.assertFalse((self.target / "state" / "runtime_port.json").exists())
        self.assertFalse((self.target / "state" / "first_run.json").read_text(
            encoding="utf-8").count("app_version"))
        self.assertFalse((self.target / "todo.db-wal").exists())
        self.assertFalse((self.target / "todo.db-shm").exists())
        # 备份档复制
        self.assertTrue((self.target / "backups" / "todo_20261004_163713.db").is_file())
        # 迁移用户不被当作新用户
        fr = json.loads((self.target / "state" / "first_run.json").read_text(encoding="utf-8"))
        self.assertEqual(fr.get("migrated_from"), "TodoApp")
        self.assertTrue(fr.get("migration_completed"))

    def test_migrated_user_has_no_demo_tasks(self):
        self.make_legacy_db(tasks=4)
        ddm.migrate(force=True, legacy_dir=self.legacy, target_dir=self.target)
        import database

        database.DB_PATH = str(self.target / "todo.db")
        database.init_database()
        conn = sqlite3.connect(str(self.target / "todo.db"))
        try:
            n = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(n, 4, "迁移用户不应出现 demo/默认任务")

    def test_runtime_status_empty_after_migration(self):
        self.make_legacy_db(tasks=2, with_runtime_state=True)
        ddm.migrate(force=True, legacy_dir=self.legacy, target_dir=self.target)
        conn = sqlite3.connect(f"file:{(self.target / 'todo.db').as_posix()}?mode=ro", uri=True)
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM runtime_status").fetchone()[0], 0)
        finally:
            conn.close()


class NewOnlyTest(Base):
    def fingerprint(self, db_path):
        """业务内容指纹（忽略 SQLite 正常产生的 -wal/-shm 边车文件）。"""
        conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
        try:
            counts = {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                      for t in ddm.BUSINESS_TABLES}
            titles = [r[0] for r in conn.execute("SELECT title FROM tasks ORDER BY id")]
        finally:
            conn.close()
        return counts, titles

    def test_new_only_is_kept(self):
        self.make_new_valid(tasks=3)
        before = self.fingerprint(self.target / "todo.db")
        files_before = {str(p.relative_to(self.target)) for p in self.target.rglob("*")
                        if p.is_file() and not p.name.endswith(("-wal", "-shm"))}

        decision = ddm.plan(self.legacy, self.target)
        self.assertEqual(decision.mode, ddm.MODE_KEEP_NEW)
        result = ddm.migrate(force=True, legacy_dir=self.legacy, target_dir=self.target)
        self.assertTrue(result.ok)
        self.assertEqual(result.mode, ddm.MODE_KEEP_NEW)
        self.assertEqual(self.fingerprint(self.target / "todo.db"), before,
                         "有效新库内容不得被改动")
        files_after = {str(p.relative_to(self.target)) for p in self.target.rglob("*")
                       if p.is_file() and not p.name.endswith(("-wal", "-shm"))}
        self.assertEqual(files_after, files_before, "不得新增/覆盖任何文件")

    def test_new_dir_without_marker_is_not_treated_as_success(self):
        """库能打开但没有成功标记 → 不能被当成迁移成功。"""
        import database

        self.target.mkdir(parents=True, exist_ok=True)
        database.DB_PATH = str(self.target / "todo.db")
        database.init_database()
        decision = ddm.plan(self.legacy, self.target)
        self.assertNotEqual(decision.mode, ddm.MODE_KEEP_NEW)
        self.assertEqual(decision.target_status, "unmarked")


class BothTest(Base):
    def test_both_new_wins_no_merge(self):
        self.make_legacy_db(tasks=5)
        self.make_new_valid(tasks=3)
        legacy_before = self.legacy_snapshot()
        new_counts_before = self.counts(self.target / "todo.db")
        titles_before = self._titles(self.target / "todo.db")

        result = ddm.migrate(force=True, legacy_dir=self.legacy, target_dir=self.target)

        self.assertEqual(result.mode, ddm.MODE_KEEP_NEW)
        self.assertEqual(self.counts(self.target / "todo.db"), new_counts_before)
        self.assertEqual(self.counts(self.target / "todo.db")["tasks"], 3)
        self.assertEqual(self._titles(self.target / "todo.db"), titles_before)
        self.assertEqual(self.legacy_snapshot(), legacy_before)

    def _titles(self, db_path):
        conn = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
        try:
            return [r[0] for r in conn.execute("SELECT title FROM tasks ORDER BY id")]
        finally:
            conn.close()


class NeitherTest(Base):
    def test_neither_is_fresh_without_data(self):
        decision = ddm.plan(self.legacy, self.target)
        self.assertEqual(decision.mode, ddm.MODE_FRESH)
        result = ddm.migrate(force=True, legacy_dir=self.legacy, target_dir=self.target)
        self.assertTrue(result.ok)
        self.assertEqual(result.mode, ddm.MODE_FRESH)
        self.assertFalse((self.target / "todo.db").exists(), "全新用户不应被预先塞入空库")

    def test_fresh_user_gets_no_demo_tasks(self):
        import database

        self.target.mkdir(parents=True, exist_ok=True)
        database.DB_PATH = str(self.target / "todo.db")
        database.init_database()
        conn = sqlite3.connect(str(self.target / "todo.db"))
        try:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0], 0)
        finally:
            conn.close()


class IdempotencyTest(Base):
    def test_second_launch_does_not_migrate_again(self):
        self.make_legacy_db(tasks=5)
        self.make_legacy_configs()
        first = ddm.migrate(force=True, legacy_dir=self.legacy, target_dir=self.target)
        self.assertTrue(first.ok)
        snapshot = {p.name: p.read_bytes() for p in self.target.rglob("*") if p.is_file()}

        second = ddm.migrate(force=True, legacy_dir=self.legacy, target_dir=self.target)
        self.assertEqual(second.mode, ddm.MODE_KEEP_NEW)
        self.assertEqual({p.name: p.read_bytes() for p in self.target.rglob("*") if p.is_file()},
                         snapshot)
        self.assertEqual(ddm.plan(self.legacy, self.target).mode, ddm.MODE_KEEP_NEW)

    def test_no_staging_left_after_success(self):
        self.make_legacy_db(tasks=2)
        ddm.migrate(force=True, legacy_dir=self.legacy, target_dir=self.target)
        self.assertEqual(ddm.iter_staging_dirs(), [])


class CorruptTest(Base):
    def test_corrupt_legacy_blocks_and_creates_nothing(self):
        self.make_legacy_db(corrupt=True)
        decision = ddm.plan(self.legacy, self.target)
        self.assertEqual(decision.mode, ddm.MODE_BLOCKED)
        result = ddm.migrate(force=True, legacy_dir=self.legacy, target_dir=self.target)
        self.assertFalse(result.ok)
        self.assertEqual(result.mode, ddm.MODE_BLOCKED)
        self.assertFalse((self.target / "todo.db").exists(), "不得静默创建空库")
        self.assertTrue((self.legacy / "todo.db").is_file(), "旧数据必须保留")

    def test_integrity_failure_in_staging_is_not_promoted(self):
        staging = app_paths.get_staging_dir("test-bad")
        staging.mkdir(parents=True)
        (staging / "todo.db").write_bytes(b"garbage" * 100)
        ddm._write_marker(staging, {"rows": {}})
        self.assertIsNone(ddm._valid_staging(staging))
        result = ddm.promote(staging, self.target)
        self.assertFalse(result.ok)
        self.assertFalse(self.target.exists())


class PartialTest(Base):
    def test_bootstrap_only_target_with_valid_legacy_migrates(self):
        """FRESH INSTALL HOTFIX：目标目录里只有程序自动生成的 bootstrap 项时，
        不得被当成"半成品"——有效的 legacy 数据仍应正常迁移。"""
        self.make_legacy_db(tasks=3)
        self.target.mkdir(parents=True)
        (self.target / "config").mkdir()
        (self.target / "config" / "appearance.json").write_text("{}", encoding="utf-8")
        (self.target / "logs").mkdir()
        (self.target / "logs" / "runtime.log").write_text("bootstrap", encoding="utf-8")
        (self.target / "state").mkdir()
        (self.target / "state" / "runtime_port.json").write_text("{}", encoding="utf-8")
        decision = ddm.plan(self.legacy, self.target)
        self.assertEqual(decision.mode, ddm.MODE_MIGRATE)
        result = ddm.migrate(force=True, legacy_dir=self.legacy, target_dir=self.target)
        self.assertTrue(result.ok, "bootstrap 项不应阻断 legacy 迁移")
        self.assertTrue((self.target / "todo.db").exists())

    def test_partial_target_without_staging_is_blocked(self):
        """真正的"半成品"（未知文件）仍必须 BLOCKED，且 migrate 不得落库。"""
        self.make_legacy_db(tasks=3)
        self.target.mkdir(parents=True)
        (self.target / "leftover.txt").write_text("x", encoding="utf-8")
        decision = ddm.plan(self.legacy, self.target)
        self.assertEqual(decision.mode, ddm.MODE_BLOCKED)
        result = ddm.migrate(force=True, legacy_dir=self.legacy, target_dir=self.target)
        self.assertFalse(result.ok)
        self.assertFalse((self.target / "todo.db").exists())

    def test_partial_target_with_broken_staging_is_blocked(self):
        self.make_legacy_db(tasks=3)
        self.target.mkdir(parents=True)
        (self.target / "leftover.txt").write_text("x", encoding="utf-8")
        staging = app_paths.get_staging_dir("broken")
        staging.mkdir(parents=True)
        (staging / "todo.db").write_bytes(b"not sqlite")
        decision = ddm.plan(self.legacy, self.target)
        self.assertEqual(decision.mode, ddm.MODE_BLOCKED)

    def test_interrupted_migration_resumes_and_promotes(self):
        """中断（staging 已完成但未提交）→ 下次启动提交它，不重复迁移。"""
        self.make_legacy_db(tasks=6)
        self.make_legacy_configs()
        # 手工构造"已完成校验但未提交"的 staging
        staging = app_paths.get_staging_dir("interrupted")
        staging.mkdir(parents=True)
        ddm._backup_database(self.legacy / "todo.db", staging / "todo.db")
        ddm._purge_runtime_tables(staging / "todo.db")
        ddm._write_marker(staging, {"rows": self.counts(staging / "todo.db")})

        decision = ddm.plan(self.legacy, self.target)
        self.assertEqual(decision.mode, ddm.MODE_RESUME)
        result = ddm.ensure_data_dir(force=True)   # 走真实入口（测试需绕过环境接管）
        self.assertTrue(result.ok, result.errors)
        self.assertTrue((self.target / "todo.db").is_file())
        self.assertFalse(staging.exists(), "提交后 staging 不应残留")
        self.assertEqual(self.counts(self.target / "todo.db")["tasks"], 6)

    def test_empty_target_dir_is_not_treated_as_partial(self):
        """完全空的目录（上次只建了目录）不算半成品：可以正常迁移或全新初始化。"""
        self.make_legacy_db(tasks=3)
        self.target.mkdir(parents=True)
        decision = ddm.plan(self.legacy, self.target)
        self.assertEqual(decision.mode, ddm.MODE_MIGRATE)
        result = ddm.migrate(force=True, legacy_dir=self.legacy, target_dir=self.target)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(self.counts(self.target / "todo.db")["tasks"], 3)

    def test_incomplete_staging_is_not_used(self):
        """staging 缺成功标记 → 不可提交，也不能被当成有效数据。"""
        self.make_legacy_db(tasks=3)
        staging = app_paths.get_staging_dir("nope")
        staging.mkdir(parents=True)
        (staging / "todo.db").write_bytes(b"whatever")
        self.assertIsNone(ddm._valid_staging(staging))


class CredentialTest(Base):
    def _account(self):
        import ai_settings

        return ai_settings.legacy_credential_id(self.legacy / "config" / "ai_config.json")

    def _seed_legacy_cred(self, value="sk-" + "x" * 32):
        self.make_legacy_configs()
        self.fake.store[("TodoApp-AI", self._account())] = value
        return value

    def test_legacy_credential_only_is_copied(self):
        value = self._seed_legacy_cred()
        import ai_settings

        info = ai_settings.migrate_credential(
            legacy_config_path=self.legacy / "config" / "ai_config.json")
        self.assertTrue(info["migrated"])
        self.assertEqual(self.fake.store[("TaiPlan-AI", self._account())], value)
        self.assertIn(("TodoApp-AI", self._account()), self.fake.store)   # 旧凭据保留
        self.assertEqual(ai_settings.get_api_key(self._account()), value)

    def test_new_credential_only_is_used(self):
        self.make_legacy_configs()
        import ai_settings

        self.fake.store[("TaiPlan-AI", self._account())] = "sk-" + "n" * 32
        info = ai_settings.migrate_credential(
            legacy_config_path=self.legacy / "config" / "ai_config.json")
        self.assertFalse(info["migrated"])
        self.assertEqual(info["reason"], "new_identity_already_has_key")

    def test_both_new_wins(self):
        self._seed_legacy_cred("sk-" + "l" * 32)
        import ai_settings

        new_value = "sk-" + "w" * 32
        self.fake.store[("TaiPlan-AI", self._account())] = new_value
        ai_settings.migrate_credential(
            legacy_config_path=self.legacy / "config" / "ai_config.json")
        self.assertEqual(ai_settings.get_api_key(self._account()), new_value)

    def test_write_failure_falls_back_to_legacy(self):
        """写新身份失败 → 不允许明文降级，但必须仍能读到旧凭据。"""
        value = self._seed_legacy_cred("sk-" + "f" * 32)
        import ai_settings

        self.fake.fail_write = True
        info = ai_settings.migrate_credential(
            legacy_config_path=self.legacy / "config" / "ai_config.json")
        self.assertFalse(info["migrated"])
        self.assertTrue(info["reason"].startswith("write_failed"))
        self.assertTrue(info["legacy_available"])
        self.assertNotIn(("TaiPlan-AI", self._account()), self.fake.store)
        self.assertEqual(ai_settings.get_api_key(self._account()), value, "应回退读取旧凭据")
        self.assertNotIn(("delete", "TodoApp-AI", self._account()), self.fake.calls)

    def test_keyring_unavailable_is_not_plaintext(self):
        self._seed_legacy_cred()
        import ai_settings

        ai_settings.keyring = None
        info = ai_settings.migrate_credential(
            legacy_config_path=self.legacy / "config" / "ai_config.json")
        self.assertFalse(info["migrated"])
        self.assertEqual(info["reason"], "keyring_unavailable")
        self.assertIsNone(ai_settings.get_api_key(self._account()))
        raw = (self.legacy / "config" / "ai_config.json").read_text(encoding="utf-8")
        self.assertNotIn("sk-", raw, "任何情况下 keyring 不可用都不得落明文")

    def test_never_deletes_legacy_credential(self):
        self._seed_legacy_cred()
        import ai_settings

        ai_settings.migrate_credential(
            legacy_config_path=self.legacy / "config" / "ai_config.json")
        self.assertFalse([c for c in self.fake.calls if c[0] == "delete"])


class SafetyTest(Base):
    def test_migration_skipped_when_data_dir_overridden(self):
        """TODO_APP_DATA_DIR 接管时（测试/演练）不得自动迁移真实目录。"""
        self.make_legacy_db(tasks=3)
        result = ddm.migrate(force=False)
        self.assertEqual(result.mode, ddm.MODE_SKIPPED)

    def test_disable_env_blocks_migration(self):
        self.make_legacy_db(tasks=3)
        os.environ[ddm.DISABLE_ENV] = "1"
        result = ddm.migrate(force=False)
        self.assertEqual(result.mode, ddm.MODE_SKIPPED)

    def test_legacy_dir_points_to_temp_not_real(self):
        self.assertEqual(Path(os.environ["TODO_APP_LEGACY_APP_DIR"]), self.legacy)
        real = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
        self.assertNotEqual(app_paths.get_legacy_app_dir(),
                            real / app_paths.LEGACY_APP_DIR_NAME)


if __name__ == "__main__":
    unittest.main(verbosity=2)
