# -*- coding: utf-8 -*-
"""i18n 层测试（Stage 17.3 §6/§7/§9/§10/§11）。"""
import unittest
from datetime import date, datetime

import product_info
from i18n import (LANGUAGE_EN_US, LANGUAGE_SYSTEM, LANGUAGE_ZH_CN, catalog,
                  detect_system_language, get_language, get_setting,
                  resolve_language, set_language, t)
from i18n import dates
from i18n.en_US import STRINGS as EN
from i18n.zh_CN import STRINGS as ZH


class CatalogTest(unittest.TestCase):
    def test_key_parity(self):
        """两种语言的 key 必须完全一致（§7：禁止散落翻译）。"""
        missing_in_en = sorted(set(ZH) - set(EN))
        missing_in_zh = sorted(set(EN) - set(ZH))
        self.assertEqual(missing_in_en, [], f"en_US 缺 key: {missing_in_en}")
        self.assertEqual(missing_in_zh, [], f"zh_CN 缺 key: {missing_in_zh}")

    def test_keys_are_dotted_semantic(self):
        """key 必须是点号语义化，且禁止空值。"""
        for key, value in ZH.items():
            self.assertIn(".", key, f"key 不是点号语义化: {key}")
            self.assertTrue(value.strip(), f"zh_CN 空值: {key}")
        for key, value in EN.items():
            self.assertIn(".", key, f"key 不是点号语义化: {key}")
            self.assertTrue(value.strip(), f"en_US 空值: {key}")

    def test_required_coverage_from_spec(self):
        """§8 要求的覆盖面。"""
        required = [
            "nav.today", "nav.inbox", "nav.calendar", "nav.upcoming", "nav.completed",
            "nav.reminder", "nav.settings",
            "settings.appearance", "settings.ai", "settings.reminder", "settings.calendar",
            "settings.desktop", "settings.about", "settings.language",
            "task.add", "task.edit", "task.delete",
            "dialog.delete_title", "dialog.recurrence_title", "dialog.recurrence_this_only",
            "conflict.title", "status.overdue", "priority.urgent", "priority.low",
            "nlp.confirm", "ai.confirm",
            "empty.today", "empty.calendar",
            "error.generic", "error.save_failed",
            "notify.reminder_title", "notify.overdue_title",
            "tray.show", "tray.quit",
            "about.product", "about.tagline", "about.version", "about.author_label",
            "about.copyright", "about.license_label", "about.description",
            "settings.language.system", "settings.language.zh", "settings.language.en",
        ]
        for key in required:
            self.assertIn(key, ZH, f"缺少规范要求的 key: {key}")
            self.assertIn(key, EN, f"缺少规范要求的 key(EN): {key}")

    def test_placeholders_match_across_languages(self):
        import re

        pattern = re.compile(r"\{(\w+)\}")
        for key in ZH:
            self.assertEqual(sorted(pattern.findall(ZH[key])), sorted(pattern.findall(EN[key])),
                             f"占位符不一致: {key}")


class TranslationTest(unittest.TestCase):
    def tearDown(self):
        set_language(LANGUAGE_SYSTEM)

    def test_zh_and_en(self):
        set_language(LANGUAGE_ZH_CN)
        self.assertEqual(t("nav.today"), "今日")
        self.assertEqual(get_language(), LANGUAGE_ZH_CN)
        set_language(LANGUAGE_EN_US)
        self.assertEqual(t("nav.today"), "Today")
        self.assertEqual(get_language(), LANGUAGE_EN_US)

    def test_placeholder_formatting(self):
        set_language(LANGUAGE_ZH_CN)
        self.assertIn("3", t("conflict.badge", count=3))
        set_language(LANGUAGE_EN_US)
        self.assertIn("3", t("conflict.badge", count=3))

    def test_missing_key_returns_key(self):
        set_language(LANGUAGE_EN_US)
        self.assertEqual(t("nope.not.exists"), "nope.not.exists")

    def test_invalid_setting_falls_back_to_system(self):
        set_language("klingon")
        self.assertEqual(get_setting(), LANGUAGE_SYSTEM)
        self.assertIn(get_language(), (LANGUAGE_ZH_CN, LANGUAGE_EN_US))

    def test_manual_choice_beats_system(self):
        """§10：手动选择优先于系统。"""
        set_language(LANGUAGE_ZH_CN)
        self.assertEqual(resolve_language(LANGUAGE_ZH_CN), LANGUAGE_ZH_CN)
        set_language(LANGUAGE_EN_US)
        self.assertEqual(resolve_language(LANGUAGE_EN_US), LANGUAGE_EN_US)

    def test_system_resolution_is_one_of_two(self):
        self.assertIn(resolve_language(LANGUAGE_SYSTEM), (LANGUAGE_ZH_CN, LANGUAGE_EN_US))

    def test_detect_fallback_is_definite(self):
        """§10：探测失败必须有明确 fallback（en-US）。"""
        self.assertIn(detect_system_language(), (LANGUAGE_ZH_CN, LANGUAGE_EN_US))
        from i18n import FALLBACK_LANGUAGE

        self.assertEqual(FALLBACK_LANGUAGE, LANGUAGE_EN_US)

    def test_no_inline_language_branching_in_ui(self):
        """§7：不允许散落 if lang == ... / if language == ...。"""
        import io
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parent
        files = [root / "app.py", root / "calendar_view.py", root / "tray_app.py"]
        files += sorted((root / "ui").glob("*.py"))
        bad = re.compile(r"if\s+(lang|language|locale)\s*==")
        hits = []
        for f in files:
            if not f.is_file():
                continue
            with io.open(f, encoding="utf-8-sig", errors="replace") as fh:
                text = fh.read()
            for i, line in enumerate(text.split("\n"), 1):
                if bad.search(line):
                    hits.append(f"{f.name}:{i}")
        self.assertEqual(hits, [], f"发现散落的语言判断: {hits}")


class DateLocalizationTest(unittest.TestCase):
    def tearDown(self):
        set_language(LANGUAGE_SYSTEM)

    def test_month_day_zh(self):
        set_language(LANGUAGE_ZH_CN)
        d = date(2026, 10, 5)          # 周一
        self.assertEqual(dates.month_day(d), "10月5日 \u00b7 周一")

    def test_month_day_en(self):
        set_language(LANGUAGE_EN_US)
        d = date(2026, 10, 5)
        self.assertEqual(dates.month_day(d), "Oct 5 \u00b7 Monday")

    def test_full_date_both(self):
        set_language(LANGUAGE_ZH_CN)
        self.assertEqual(dates.full_date(date(2026, 10, 5)), "2026年10月5日")
        set_language(LANGUAGE_EN_US)
        self.assertEqual(dates.full_date(date(2026, 10, 5)), "Oct 5, 2026")

    def test_relative_days(self):
        today = date(2026, 10, 5)
        set_language(LANGUAGE_ZH_CN)
        self.assertEqual(dates.relative_day(today, today=today), "今天")
        self.assertEqual(dates.relative_day(date(2026, 10, 6), today=today), "明天")
        self.assertEqual(dates.relative_day(date(2026, 10, 4), today=today), "昨天")
        self.assertIsNone(dates.relative_day(date(2026, 10, 20), today=today))
        set_language(LANGUAGE_EN_US)
        self.assertEqual(dates.relative_day(today, today=today), "Today")
        self.assertEqual(dates.relative_day(date(2026, 10, 6), today=today), "Tomorrow")

    def test_weekday_heads(self):
        set_language(LANGUAGE_ZH_CN)
        self.assertEqual(dates.weekday_heads(), ("一", "二", "三", "四", "五", "六", "日"))
        set_language(LANGUAGE_EN_US)
        self.assertEqual(dates.weekday_heads(), ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"))

    def test_minutes_label(self):
        set_language(LANGUAGE_ZH_CN)
        self.assertEqual(dates.minutes_label(30), "30 分钟")
        self.assertEqual(dates.minutes_label(120), "2 小时")
        set_language(LANGUAGE_EN_US)
        self.assertEqual(dates.minutes_label(30), "30 min")
        self.assertEqual(dates.minutes_label(120), "2 hours")

    def test_no_system_locale_mutation(self):
        """§9：不得修改系统全局 locale。"""
        import io
        from pathlib import Path

        with io.open(Path(__file__).resolve().parent / "i18n" / "dates.py",
                     encoding="utf-8") as fh:
            src = fh.read()
        self.assertNotIn("setlocale", src)


class ProductIdentityTest(unittest.TestCase):
    def test_identity_values(self):
        self.assertEqual(product_info.APP_NAME, "TaiPlan")
        self.assertEqual(product_info.APP_DISPLAY_NAME, "TaiPlan")
        self.assertEqual(product_info.APP_AUTHOR, "TaiWoo_Chen")
        self.assertEqual(product_info.APP_TAGLINE, "Tasks into time.")
        self.assertEqual(product_info.APP_LICENSE, "MIT License")
        self.assertIn("2026 TaiWoo_Chen", product_info.APP_COPYRIGHT)

    def test_version_single_source(self):
        import version

        self.assertEqual(product_info.APP_VERSION, version.__version__)

    def test_no_fake_repo_url(self):
        """§11：没有真实仓库地址就不要写假的。"""
        self.assertEqual(product_info.APP_REPO_URL, "")


if __name__ == "__main__":
    unittest.main()
