import sys
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import unittest
import tempfile, os
import database

# 导入 app 的辅助函数（不触发 streamlit 运行）
import app as app_module

tmp = tempfile.mktemp(suffix=".db")
database.DB_PATH = tmp
database.init_database()


class ParseQualityTest(unittest.TestCase):

    def test_plain_text_no_hint(self):
        self.assertFalse(app_module._text_has_datetime_hint("买书"))

    def test_hint_detected(self):
        self.assertTrue(app_module._text_has_datetime_hint("下个礼拜五下午五点三刻去上高数"))
        self.assertTrue(app_module._text_has_datetime_hint("明天下午3点"))
        self.assertTrue(app_module._text_has_datetime_hint("10月15号晚上八点半"))

    def test_iso_date_no_hint_false(self):
        # 纯数字日期时间，本地能解析，不算 partial hint
        # 这里只测 hint 函数：ISO 日期含年月日标记，会被识别为 hint
        self.assertTrue(app_module._text_has_datetime_hint("2026-10-20 14:30 开会"))


if __name__ == "__main__":
    unittest.main()
