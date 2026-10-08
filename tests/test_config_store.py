"""config_store 测试：原子写、损坏回退、默认值合并。"""

import json
import os
import tempfile
import unittest
from pathlib import Path

from tests import tests_env  # noqa: F401

import app_paths
import config_store


class ConfigStoreTest(unittest.TestCase):

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="cfg_"))
        self.path = self.dir / "cfg.json"

    def test_write_then_read_round_trip(self):
        config_store.write_json_config_atomic(self.path, {"a": 1, "b": "x"})
        self.assertEqual(config_store.read_json_config(self.path), {"a": 1, "b": "x"})

    def test_write_creates_parent_dir(self):
        target = self.dir / "deep" / "nested" / "c.json"
        config_store.write_json_config_atomic(target, {"k": 1})
        self.assertTrue(target.is_file())

    def test_atomic_write_leaves_no_temp_files(self):
        config_store.write_json_config_atomic(self.path, {"a": 1})
        leftovers = [p.name for p in self.dir.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_missing_file_returns_defaults(self):
        out = config_store.read_json_config(self.dir / "nope.json",
                                           defaults={"z": 9})
        self.assertEqual(out, {"z": 9})

    def test_defaults_merged_with_saved(self):
        config_store.write_json_config_atomic(self.path, {"saved": 1})
        out = config_store.read_json_config(self.path, defaults={"saved": 0, "new": 2})
        self.assertEqual(out["saved"], 1)
        self.assertEqual(out["new"], 2)

    def test_unknown_old_fields_preserved(self):
        config_store.write_json_config_atomic(self.path, {"old_removed_field": "keep"})
        out = config_store.read_json_config(self.path, defaults={"a": 1})
        self.assertEqual(out["old_removed_field"], "keep")

    def test_corrupt_json_falls_back_and_quarantines(self):
        self.path.write_text("{ not json at all", encoding="utf-8")
        out = config_store.read_json_config(self.path, defaults={"d": 1})
        self.assertEqual(out, {"d": 1})
        corrupt = list(self.dir.glob("cfg.corrupt.*.json"))
        self.assertEqual(len(corrupt), 1, "损坏文件应被隔离留存")
        self.assertTrue(corrupt[0].read_text(encoding="utf-8").startswith("{ not json"))

    def test_non_object_json_treated_as_corrupt(self):
        self.path.write_text("[1, 2, 3]", encoding="utf-8")
        out = config_store.read_json_config(self.path, defaults={"d": 1})
        self.assertEqual(out, {"d": 1})
        self.assertTrue(list(self.dir.glob("cfg.corrupt.*.json")))

    def test_write_is_overwriting_not_appending(self):
        config_store.write_json_config_atomic(self.path, {"a": 1})
        config_store.write_json_config_atomic(self.path, {"b": 2})
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"b": 2})

    def test_damaged_config_does_not_raise(self):
        self.path.write_text("", encoding="utf-8")
        try:
            config_store.read_json_config(self.path, defaults={})
        except Exception as exc:  # noqa: BLE001
            self.fail(f"损坏配置不应抛异常：{exc}")

    def test_name_resolves_into_user_config_dir(self):
        resolved = config_store.resolve_path("appearance.json")
        self.assertEqual(resolved, app_paths.get_config_path("appearance.json"))

    def test_delete_config(self):
        config_store.write_json_config_atomic(self.path, {"a": 1})
        self.assertTrue(config_store.delete_config(self.path))
        self.assertFalse(self.path.exists())
        self.assertFalse(config_store.delete_config(self.path))

    def test_existing_module_configs_are_writable_in_isolation(self):
        """隔离环境下写真实模块的配置路径，也不应落到项目目录。"""
        import calendar_settings
        calendar_settings.save_calendar_config(calendar_settings.load_calendar_config())
        self.assertTrue(calendar_settings.CONFIG_PATH.is_file())
        self.assertFalse(str(calendar_settings.CONFIG_PATH)
                         .startswith(str(app_paths.get_project_root())))


if __name__ == "__main__":
    unittest.main()
