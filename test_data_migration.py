"""旧数据迁移测试：复制不移动、不覆盖、一次性、API Key 不进数据目录。"""

import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tests_env  # noqa: F401

import app_paths
import data_migration


def _make_legacy_db(path: Path, titles=("旧任务A", "旧任务B")):
    conn = sqlite3.connect(str(path))
    conn.execute(
        """CREATE TABLE tasks (id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
        is_completed INTEGER NOT NULL DEFAULT 0, created_at TEXT, updated_at TEXT)""")
    for t in titles:
        conn.execute("INSERT INTO tasks(title, created_at, updated_at) VALUES(?,?,?)",
                     (t, "2026-01-01", "2026-01-01"))
    conn.commit()
    conn.close()


class MigrationTest(unittest.TestCase):

    def setUp(self):
        self.legacy = Path(tempfile.mkdtemp(prefix="legacy_src_"))
        self.target = Path(tempfile.mkdtemp(prefix="legacy_dst_"))
        self._state = mock.patch.object(
            data_migration, "get_state_path",
            return_value=self.target / "state" / "migration_state.json")
        self._state.start()
        self._paths = mock.patch.object(data_migration.app_paths, "get_user_data_dir",
                                        return_value=self.target)
        self._paths.start()
        app_paths.ensure_user_directories()
        self.addCleanup(self._state.stop)
        self.addCleanup(self._paths.stop)

    def _target_db(self):
        return self.target / "todo.db"

    def test_legacy_db_copied_and_source_kept(self):
        _make_legacy_db(self.legacy / "todo.db")
        result = data_migration.migrate_legacy_data(legacy_dir=self.legacy)
        self.assertIn("todo.db", result["migrated"])
        self.assertTrue(self._target_db().is_file())
        self.assertTrue((self.legacy / "todo.db").is_file(), "旧库必须保留")

        conn = sqlite3.connect(str(self._target_db()))
        titles = [r[0] for r in conn.execute("SELECT title FROM tasks")]
        conn.close()
        self.assertEqual(sorted(titles), ["旧任务A", "旧任务B"])

    def test_destination_wins_when_both_exist(self):
        _make_legacy_db(self.legacy / "todo.db", titles=("旧任务",))
        _make_legacy_db(self._target_db(), titles=("新任务",))
        with self.assertLogs("todoapp.test", level="WARNING") as captured:
            import logging
            logger = logging.getLogger("todoapp.test")
            result = data_migration.migrate_legacy_data(legacy_dir=self.legacy,
                                                        logger=logger)
        self.assertIn("todo.db", result["skipped"])
        messages = "\n".join(captured.output)
        self.assertIn("Legacy database detected but destination already exists",
                      messages)

        conn = sqlite3.connect(str(self._target_db()))
        titles = [r[0] for r in conn.execute("SELECT title FROM tasks")]
        conn.close()
        self.assertEqual(titles, ["新任务"], "新数据目录优先，绝不能被旧库覆盖")

    def test_legacy_config_migrated_with_rename(self):
        (self.legacy / "runtime_config.json").write_text(
            json.dumps({"close_to_tray": False}), encoding="utf-8")
        (self.legacy / "appearance.json").write_text(
            json.dumps({"mode": "深色"}), encoding="utf-8")
        result = data_migration.migrate_legacy_data(legacy_dir=self.legacy)
        self.assertIn("desktop_config.json", result["migrated"])
        self.assertIn("appearance.json", result["migrated"])
        self.assertTrue(app_paths.get_config_path("desktop_config.json").is_file())
        data = json.loads(app_paths.get_config_path("desktop_config.json")
                          .read_text(encoding="utf-8"))
        self.assertFalse(data["close_to_tray"])

    def test_existing_config_not_overwritten(self):
        (self.legacy / "appearance.json").write_text('{"mode": "旧"}', encoding="utf-8")
        target_cfg = self.target / "config" / "appearance.json"
        target_cfg.parent.mkdir(parents=True, exist_ok=True)
        target_cfg.write_text('{"mode": "新"}', encoding="utf-8")

        result = data_migration.migrate_legacy_data(legacy_dir=self.legacy)
        self.assertIn("appearance.json", result["skipped"])
        self.assertEqual(json.loads(target_cfg.read_text(encoding="utf-8"))["mode"], "新")

    def test_marker_prevents_second_migration(self):
        _make_legacy_db(self.legacy / "todo.db")
        first = data_migration.migrate_legacy_data(legacy_dir=self.legacy)
        self.assertTrue(first["ran"])
        self.assertTrue(data_migration.is_migration_completed())

        self._target_db().unlink()
        second = data_migration.migrate_legacy_data(legacy_dir=self.legacy)
        self.assertFalse(second["ran"], "已迁移过就不应再复制旧数据库")
        self.assertFalse(self._target_db().exists(),
                         "不能因为 marker 存在就偷偷恢复旧的开发数据库")

    def test_missing_legacy_files_are_skipped(self):
        result = data_migration.migrate_legacy_data(legacy_dir=self.legacy)
        self.assertEqual(result["migrated"], [])
        self.assertTrue(any("todo.db" in s for s in result["skipped"]))

    def test_state_file_records_completion(self):
        _make_legacy_db(self.legacy / "todo.db")
        data_migration.migrate_legacy_data(legacy_dir=self.legacy)
        state = json.loads(data_migration.get_state_path().read_text(encoding="utf-8"))
        self.assertTrue(state["legacy_migration_completed"])
        self.assertIn("completed_at", state)

    def test_api_key_never_in_migrated_config(self):
        """AI Key 只存在于 keyring，绝不落到数据目录里的任何 JSON。"""
        (self.legacy / "ai_config.json").write_text(
            json.dumps({"enabled": True, "model_id": "gpt-x"}), encoding="utf-8")
        data_migration.migrate_legacy_data(legacy_dir=self.legacy)

        for path in app_paths.get_config_dir().glob("*.json"):
            text = path.read_text(encoding="utf-8").lower()
            self.assertNotIn("api_key", text, f"{path.name} 不应含 api_key")
            self.assertNotIn("sk-", text, f"{path.name} 不应含明文密钥")

    def test_migration_does_not_touch_program_dir(self):
        (self.legacy / "appearance.json").write_text("{}", encoding="utf-8")
        data_migration.migrate_legacy_data(legacy_dir=self.legacy)
        self.assertTrue((self.legacy / "appearance.json").is_file(),
                        "迁移只复制，不删除源文件")


if __name__ == "__main__":
    unittest.main()
