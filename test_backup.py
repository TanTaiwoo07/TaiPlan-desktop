"""备份 / 恢复 / 轮转 / 迁移前备份 测试。"""

import sqlite3
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

import tests_env  # noqa: F401

import app_paths
import data_backup
import database
import services


class BackupTest(unittest.TestCase):

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="bk_"))
        self.db = self.dir / "todo.db"
        self.bdir = self.dir / "backups"
        self._db = mock.patch.object(app_paths, "get_database_path",
                                     return_value=self.db)
        self._bk = mock.patch.object(app_paths, "get_backup_dir",
                                     return_value=self.bdir)
        self._orm = mock.patch.object(database, "DB_PATH", self.db)
        self._db.start()
        self._bk.start()
        self._orm.start()
        self.addCleanup(self._db.stop)
        self.addCleanup(self._bk.stop)
        self.addCleanup(self._orm.stop)
        database.init_database()
        services.create_task("备份测试任务")

    def test_backup_is_consistent_snapshot(self):
        path = data_backup.backup_database(db_path=self.db)
        self.assertTrue(path.is_file())
        self.assertTrue(data_backup.verify_backup(path))

        conn = sqlite3.connect(str(path))
        titles = [r[0] for r in conn.execute("SELECT title FROM tasks")]
        conn.close()
        self.assertEqual(titles, ["备份测试任务"])

    def test_backup_uses_sqlite_api_not_file_copy(self):
        """备份内容必须是完整 SQLite 库（文件头 + 可查询）。"""
        path = data_backup.backup_database(db_path=self.db)
        head = path.read_bytes()[:15]
        self.assertEqual(head, b"SQLite format 3")

    def test_rotation_keeps_ten(self):
        for _ in range(14):
            data_backup.backup_database(db_path=self.db, keep=10)
        self.assertEqual(data_backup.backup_count(), 10)

    def test_list_backups_newest_first(self):
        for _ in range(3):
            data_backup.backup_database(db_path=self.db)
        names = [p.name for p in data_backup.list_backups()]
        self.assertEqual(names, sorted(names, reverse=True))

    def test_missing_database_raises(self):
        with self.assertRaises(FileNotFoundError):
            data_backup.backup_database(db_path=self.dir / "nope.db")

    def test_verify_rejects_garbage(self):
        bad = self.bdir / "todo_garbage.db"
        self.bdir.mkdir(parents=True, exist_ok=True)
        bad.write_bytes(b"not a database")
        self.assertFalse(data_backup.verify_backup(bad))

    def test_restore_replaces_database_and_keeps_safety_backup(self):
        good = data_backup.backup_database(db_path=self.db)
        services.create_task("备份之后新增的任务")
        before = len(database.get_all_tasks())
        self.assertEqual(before, 2)

        data_backup.restore_backup(good.name, db_path=self.db)
        titles = [t["title"] for t in database.get_all_tasks()]
        self.assertEqual(titles, ["备份测试任务"], "应回到备份时的状态")
        self.assertGreaterEqual(data_backup.backup_count(), 2,
                               "恢复前必须再自动备份一次当前库")

    def test_restore_rejects_invalid_backup(self):
        bad = self.bdir / "todo_broken.db"
        self.bdir.mkdir(parents=True, exist_ok=True)
        bad.write_bytes(b"broken")
        with self.assertRaises(ValueError):
            data_backup.restore_backup(bad.name, db_path=self.db)

    def test_restore_missing_backup_raises(self):
        with self.assertRaises(FileNotFoundError):
            data_backup.restore_backup("todo_19990101_000000.db", db_path=self.db)

    def test_summary_fields(self):
        data_backup.backup_database(db_path=self.db)
        summary = data_backup.backup_summary()
        self.assertEqual(summary["count"], 1)
        self.assertIsNotNone(summary["latest"])
        self.assertGreater(summary["total_bytes"], 0)


class SchemaMigrationBackupTest(unittest.TestCase):
    """Schema 版本落后时，迁移前必须先自动备份。"""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="schemabk_"))
        self.db = self.dir / "todo.db"
        self.bdir = self.dir / "backups"
        self._db = mock.patch.object(app_paths, "get_database_path",
                                     return_value=self.db)
        self._bk = mock.patch.object(app_paths, "get_backup_dir",
                                     return_value=self.bdir)
        self._orm = mock.patch.object(database, "DB_PATH", self.db)
        self._db.start()
        self._bk.start()
        self._orm.start()
        self.addCleanup(self._db.stop)
        self.addCleanup(self._bk.stop)
        self.addCleanup(self._orm.stop)

    def test_fresh_database_does_not_backup(self):
        database.init_database()
        self.assertEqual(database.get_schema_version(), database.SCHEMA_VERSION)
        self.assertEqual(data_backup.backup_count(), 0)

    def test_no_backup_when_version_current(self):
        database.init_database()
        database.init_database()
        self.assertEqual(data_backup.backup_count(), 0)

    def test_backup_before_upgrade_and_version_recorded(self):
        database.init_database()
        conn = sqlite3.connect(str(self.db))
        conn.execute("DROP TABLE app_meta")
        conn.commit()
        conn.close()
        self.assertEqual(database.get_schema_version(), 0)

        database.init_database()
        self.assertGreaterEqual(data_backup.backup_count(), 1,
                                "版本升级前必须自动备份")
        self.assertEqual(database.get_schema_version(), database.SCHEMA_VERSION)
        self.assertTrue(data_backup.verify_backup(data_backup.list_backups()[0]))

    def test_data_survives_migration(self):
        database.init_database()
        services.create_task("迁移前创建的任务")
        conn = sqlite3.connect(str(self.db))
        conn.execute("DROP TABLE app_meta")
        conn.commit()
        conn.close()

        database.init_database()
        self.assertIn("迁移前创建的任务",
                      [t["title"] for t in database.get_all_tasks()])


class IntegrityTest(unittest.TestCase):

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="integ_"))
        self.db = self.dir / "todo.db"
        self._db = mock.patch.object(app_paths, "get_database_path",
                                     return_value=self.db)
        self._orm = mock.patch.object(database, "DB_PATH", self.db)
        self._db.start()
        self._orm.start()
        self.addCleanup(self._db.stop)
        self.addCleanup(self._orm.stop)

    def test_health_check_passes(self):
        database.init_database()
        result = database.check_database_integrity()
        self.assertTrue(result["ok"], result["detail"])

    def test_health_check_reports_corrupt(self):
        self.db.write_bytes(b"definitely not sqlite")
        result = database.check_database_integrity()
        self.assertFalse(result["ok"])
        self.assertTrue(result["detail"])

    def test_missing_database_reported(self):
        result = database.check_database_integrity()
        self.assertFalse(result["exists"])
        self.assertFalse(result["ok"])

    def test_corrupt_database_refuses_to_recreate_empty(self):
        self.db.write_bytes(b"garbage" * 50)
        with self.assertRaises(database.DatabaseUnavailable):
            database.init_database()
        self.assertEqual(self.db.read_bytes(), b"garbage" * 50,
                         "损坏的数据库文件必须原样保留")


if __name__ == "__main__":
    unittest.main()
