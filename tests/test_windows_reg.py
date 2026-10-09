"""Windows App ID 注册测试（mock toasted / winreg，不碰真实注册表）。"""

import unittest
from unittest import mock

from taiplan import windows_app_registration as reg


class AppRegistrationTest(unittest.TestCase):

    @mock.patch.object(reg, "_registry_registered", return_value=True)
    def test_already_registered(self, _):
        self.assertTrue(reg.ensure_app_registered())

    @mock.patch.object(reg, "_registry_registered", return_value=False)
    @mock.patch.object(reg, "_register_via_toasted", return_value=True)
    def test_register_via_toasted(self, mtoasted, _):
        self.assertTrue(reg.ensure_app_registered())
        mtoasted.assert_called_once()

    @mock.patch.object(reg, "_registry_registered", return_value=False)
    @mock.patch.object(reg, "_register_via_registry", return_value=True)
    @mock.patch.object(reg, "_register_via_toasted", side_effect=RuntimeError("no winsdk"))
    def test_fallback_to_registry(self, _toasted, mreg, _):
        self.assertTrue(reg.ensure_app_registered())
        mreg.assert_called_once()

    def test_app_id_constants(self):
        self.assertEqual(reg.APP_USER_MODEL_ID, "TaiWoo.TaiPlan")
        self.assertEqual(reg.LEGACY_APP_USER_MODEL_ID, "TodoApp.Desktop")
        self.assertEqual(reg.APP_DISPLAY_NAME, "TaiPlan")
        self.assertEqual(reg.APP_DISPLAY_NAME, "TaiPlan")


if __name__ == "__main__":
    unittest.main()
