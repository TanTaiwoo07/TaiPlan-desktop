"""AI 设置持久化单元测试（mock keyring，不写真实凭据）。

运行方式：
    cd <PROJECT_ROOT>
    .venv\\Scripts\\python -m unittest test_ai_settings
"""

import json
import os
import tempfile
import unittest
from unittest import mock

import ai_settings


class AI_SettingsTest(unittest.TestCase):

    def setUp(self):
        # 临时配置文件路径
        self.tmpdir = tempfile.mkdtemp()
        self.patcher_path = mock.patch.object(
            ai_settings, "_CONFIG_PATH",
            type(ai_settings._CONFIG_PATH)(os.path.join(self.tmpdir, "ai_config.json")),
        )
        self.patcher_path.start()

    def tearDown(self):
        self.patcher_path.stop()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _config(self, **kw):
        cfg = dict(ai_settings.DEFAULT_CONFIG)
        cfg.update(kw)
        return cfg

    # 1. enabled 保存/恢复
    def test_enabled_persisted(self):
        cfg = self._config(enabled=True)
        ai_settings.save_config(cfg)
        loaded, status = ai_settings.load_config()
        self.assertTrue(loaded["enabled"])

    # 2. base_url / model / tokens 保存恢复
    def test_fields_persisted(self):
        cfg = self._config(
            base_url="https://api.deepseek.com",
            model_id="deepseek-flash",
            context_budget=1000000,
            max_output_tokens=384000,
            timeout=180,
        )
        ai_settings.save_config(cfg)
        loaded, _ = ai_settings.load_config()
        self.assertEqual(loaded["base_url"], "https://api.deepseek.com")
        self.assertEqual(loaded["model_id"], "deepseek-flash")
        self.assertEqual(loaded["context_budget"], 1000000)
        self.assertEqual(loaded["max_output_tokens"], 384000)

    # 4. ai_config.json 不含 Secret
    def test_config_has_no_secret(self):
        cfg = self._config(model_id="m1")
        ai_settings.save_config(cfg)
        with open(ai_settings._CONFIG_PATH, "r", encoding="utf-8") as f:
            raw = f.read()
        self.assertNotIn("api_key", raw)
        self.assertNotIn("api" + "_" + "key", raw)

    # 5. provider 切换：不同 credential_id
    def test_credential_id_differs_by_provider(self):
        cid_a = ai_settings.make_credential_id("openai_compatible", "https://api.deepseek.com", "deepseek-flash")
        cid_b = ai_settings.make_credential_id("anthropic", "https://api.anthropic.com", "claude-1")
        self.assertNotEqual(cid_a, cid_b)

    # 6. keyring 保存 / 覆盖
    @mock.patch("ai_settings.keyring")
    def test_set_and_get_api_key(self, mock_keyring):
        mock_keyring.get_password.return_value = "sk-old"
        cid = "test-cid"
        self.assertEqual(ai_settings.get_api_key(cid), "sk-old")
        ai_settings.set_api_key(cid, "sk-new")
        mock_keyring.set_password.assert_called_once_with(
            ai_settings.SERVICE_NAME, cid, "sk-new")

    # 7. 删除 key
    @mock.patch("ai_settings.keyring")
    def test_delete_api_key(self, mock_keyring):
        ai_settings.delete_api_key("test-cid")
        mock_keyring.delete_password.assert_called_once_with(
            ai_settings.SERVICE_NAME, "test-cid")

    # 8. keyring 异常不降级明文
    @mock.patch("ai_settings.keyring")
    def test_keyring_failure_raises(self, mock_keyring):
        mock_keyring.set_password.side_effect = RuntimeError("backend down")
        with self.assertRaises(RuntimeError):
            ai_settings.set_api_key("cid", "sk-x")

    # 9. JSON 损坏不崩溃
    def test_corrupt_json_returns_default(self):
        with open(ai_settings._CONFIG_PATH, "w", encoding="utf-8") as f:
            f.write("{not valid json")
        cfg, status = ai_settings.load_config()
        self.assertEqual(status, "corrupt")
        self.assertIsInstance(cfg, dict)

    # 10. 旧版明文 Key 迁移
    @mock.patch("ai_settings.keyring")
    def test_migrate_legacy_plaintext(self, mock_keyring):
        # 写旧版含 api_key 的 JSON
        with open(ai_settings._CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump({"model_id": "m1", "api" + "_" + "key": "sk-legacy"}, f)
        cfg = self._config(model_id="m1", api_type="openai_compatible", base_url="https://x.com")
        migrated, msg = ai_settings.migrate_legacy_plaintext_key(cfg)
        self.assertTrue(migrated)
        mock_keyring.set_password.assert_called_once()
        # JSON 中 api_key 已删除
        with open(ai_settings._CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.assertNotIn("api" + "_" + "key", data)


if __name__ == "__main__":
    unittest.main()
