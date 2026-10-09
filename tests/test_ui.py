"""UI 层测试：主题 token、CSS 安全、组件、Sidebar / 设置页渲染。

不启动真实 App、不写真实配置文件（全部指向临时路径）。
"""

import io
import os
import re
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest import mock

from taiplan import database
from taiplan import services
from taiplan.ui import components as components
from taiplan.ui import icons as icons
from taiplan.ui import layout as layout
from taiplan.ui import theme as theme

from streamlit.testing.v1 import AppTest

from tests import tests_env  # noqa: F401  必须早于项目模块导入（隔离数据目录）

APP_PATH = Path(__file__).resolve().parent.parent / "app.py"


class ThemeTokenTest(unittest.TestCase):

    def test_light_and_dark_differ(self):
        light, dark = theme.tokens("light"), theme.tokens("dark")
        self.assertNotEqual(light["bg_primary"], dark["bg_primary"])
        self.assertIn("surface", light)

    def test_dark_is_not_pure_black(self):
        dark = theme.tokens("dark")
        self.assertNotEqual(dark["bg_primary"].upper(), "#000000")
        self.assertNotEqual(dark["surface"].upper(), "#FFFFFF")

    def test_light_bg_is_not_pure_white(self):
        self.assertNotEqual(theme.tokens("light")["bg_primary"].upper(), "#FFFFFF")

    def test_all_required_tokens_present(self):
        for key in ("bg_primary", "bg_secondary", "surface", "surface_hover",
                    "text_primary", "text_secondary", "text_tertiary",
                    "border_subtle", "accent", "danger", "warning", "success",
                    "urgent", "radius_sm", "radius_md", "radius_lg", "radius_xl",
                    "shadow_sm", "shadow_md"):
            self.assertIn(key, theme.LIGHT, key)
            self.assertIn(key, theme.DARK, key)

    def test_resolve_mode(self):
        self.assertEqual(theme.resolve_mode({"mode": theme.MODE_LIGHT}), "light")
        self.assertEqual(theme.resolve_mode({"mode": theme.MODE_DARK}), "dark")
        with mock.patch.object(theme, "system_is_dark", return_value=True):
            self.assertEqual(theme.resolve_mode({"mode": theme.MODE_SYSTEM}), "dark")
        with mock.patch.object(theme, "system_is_dark", return_value=False):
            self.assertEqual(theme.resolve_mode({"mode": theme.MODE_SYSTEM}), "light")


class AppearancePersistenceTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".json")
        p = mock.patch.object(theme, "CONFIG_PATH", Path(self.tmp))
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(lambda: os.path.exists(self.tmp) and os.remove(self.tmp))

    def test_defaults(self):
        cfg = theme.load_appearance()
        self.assertEqual(cfg["mode"], theme.MODE_SYSTEM)
        self.assertEqual(cfg["density"], theme.DENSITY_COMFORT)

    def test_round_trip(self):
        theme.save_appearance({"mode": theme.MODE_DARK,
                               "density": theme.DENSITY_COMPACT})
        cfg = theme.load_appearance()
        self.assertEqual(cfg["mode"], theme.MODE_DARK)
        self.assertEqual(cfg["density"], theme.DENSITY_COMPACT)


class CssSafetyTest(unittest.TestCase):

    def test_css_has_tokens_and_no_hash_classes(self):
        css = theme.build_css("light")
        self.assertIn("--bg-primary:", css)
        self.assertIn("--accent:", css)
        self.assertNotIn('[class*="css-', css)
        self.assertNotRegex(css, r"\.css-[0-9a-z]{6,}")

    def test_css_does_not_override_global_button(self):
        css = theme.build_css("light")
        self.assertNotRegex(css, r"(^|\})\s*button\s*\{",
                            "不要全局覆盖 button，避免影响 Calendar / Dialog")

    def test_css_uses_stable_hooks(self):
        css = theme.build_css("light")
        self.assertIn('[data-testid="stSidebar"]', css)
        self.assertIn(".st-key-appnav", css)
        self.assertIn(".st-key-tasklist", css)

    def test_density_changes_padding(self):
        comfy = theme.build_css("light", theme.DENSITY_COMFORT)
        tight = theme.build_css("light", theme.DENSITY_COMPACT)
        self.assertNotEqual(comfy, tight)
        self.assertIn("--row-pad:", comfy)

    def test_dark_css_uses_dark_tokens(self):
        css = theme.build_css("dark")
        self.assertIn(theme.DARK["bg_primary"], css)


class IconsTest(unittest.TestCase):

    def test_nav_keys_unique(self):
        self.assertEqual(len(icons.NAV_KEYS), len(set(icons.NAV_KEYS)))
        self.assertEqual(len(icons.SETTINGS_KEYS), len(set(icons.SETTINGS_KEYS)))

    def test_nav_icons_are_valid_material(self):
        for entries in (icons.NAV_ENTRIES, icons.SETTINGS_ENTRIES):
            for key, icon, label in entries:
                self.assertTrue(icons.is_material_icon(icon), icon)
                self.assertFalse(icon.endswith("_"), icon)
                self.assertNotIn("material_", icon)
                self.assertNotIn("material-", icon)
                self.assertTrue(label, key)

    def test_nav_labels_resolve_in_both_languages(self):
        """i18n 改造后：标签是 i18n key，必须两种语言都能解析出非空文案。

        比原来只检查字面量更强的断言（原来第三项是中文标签，现在必须走 t()）。
        """
        from taiplan.i18n import LANGUAGE_EN_US, LANGUAGE_ZH_CN, set_language

        try:
            for lang in (LANGUAGE_ZH_CN, LANGUAGE_EN_US):
                set_language(lang)
                for key in icons.NAV_KEYS:
                    label = icons.nav_label(key)
                    self.assertTrue(label and label != icons.NAV_LABELS[key],
                                    f"{lang} 下导航标签未解析: {key}")
                for key in icons.SETTINGS_KEYS:
                    label = icons.settings_label(key)
                    self.assertTrue(label and label != icons.SETTINGS_LABELS[key],
                                    f"{lang} 下设置标签未解析: {key}")
        finally:
            set_language("system")

    def test_nav_labels_resolve_in_both_languages(self):
        """i18n 改造后：标签是 i18n key，必须两种语言都能解析出非空文案。

        比原来只检查字面量更强的断言（原来第三项是中文标签，现在必须走 t()）。
        """
        from taiplan.i18n import LANGUAGE_EN_US, LANGUAGE_ZH_CN, set_language

        try:
            for lang in (LANGUAGE_ZH_CN, LANGUAGE_EN_US):
                set_language(lang)
                for key in icons.NAV_KEYS:
                    label = icons.nav_label(key)
                    self.assertTrue(label and label != icons.NAV_LABELS[key],
                                    f"{lang} 下导航标签未解析: {key}")
                for key in icons.SETTINGS_KEYS:
                    label = icons.settings_label(key)
                    self.assertTrue(label and label != icons.SETTINGS_LABELS[key],
                                    f"{lang} 下设置标签未解析: {key}")
        finally:
            set_language("system")

    def test_no_emoji_in_nav_labels(self):
        for key, icon, label in icons.NAV_ENTRIES + icons.SETTINGS_ENTRIES:
            self.assertTrue(all(ord(ch) < 0x1F000 for ch in label), label)

    def test_aux_nav_included(self):
        self.assertIn(icons.NAV_REMINDER, icons.NAV_ITEMS)
        self.assertIn(icons.NAV_SETTINGS, icons.NAV_ITEMS)


class ComponentsTest(unittest.TestCase):

    def test_format_date_relative(self):
        today = date.today()
        self.assertEqual(components._format_date(today.isoformat()), "今天")
        self.assertEqual(components._format_date((today + timedelta(days=1)).isoformat()), "明天")
        self.assertEqual(components._format_date((today - timedelta(days=1)).isoformat()), "昨天")

    def test_format_date_other_year(self):
        out = components._format_date("2020-03-04")
        self.assertIn("2020年", out)

    def test_end_time(self):
        self.assertEqual(components._end_time("14:30", 90), "16:00")
        self.assertEqual(components._end_time("23:00", 120), "01:00")
        self.assertEqual(components._end_time(None, 60), "")

    def test_meta_html_urgent_chip(self):
        from taiplan import models
        item = models.TaskOccurrence(task_id=1, title="x", date="2026-10-05",
                                     time="14:00", duration_minutes=60, priority="urgent")
        html_out = components.meta_html(item)
        self.assertIn("紧急", html_out)
        self.assertIn("14:00", html_out)

    def test_meta_html_normal_has_no_priority_chip(self):
        from taiplan import models
        item = models.TaskOccurrence(task_id=1, title="x", date="2026-10-05", priority="normal")
        self.assertNotIn("chip urgent", components.meta_html(item))

    def test_card_html_escapes_html(self):
        from taiplan import models
        item = models.TaskOccurrence(task_id=1, title="<script>alert(1)</script>",
                                     date="2026-10-05")
        out = components.card_html(item)
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_card_desc_truncated(self):
        from taiplan import models
        item = models.TaskOccurrence(task_id=1, title="t", date="2026-10-05",
                                     description="长" * 300)
        out = components.card_html(item)
        self.assertIn("…", out)

    def test_overdue_flag_chip(self):
        from taiplan import models
        item = models.TaskOccurrence(task_id=1, title="t", date="2026-10-05", time="10:00")
        self.assertIn("逾期", components.meta_html(item, is_overdue=True))

    def test_conflict_label_rendered(self):
        from taiplan import models
        item = models.TaskOccurrence(task_id=1, title="t", date="2026-10-05", time="10:00")
        self.assertIn("冲突", components.meta_html(item, conflict_label="项目会议"))


class LayoutTest(unittest.TestCase):

    def test_nav_and_settings_keys(self):
        self.assertEqual(layout.NAV_KEY, "nav")
        self.assertEqual(layout.SETTINGS_NAV_KEY, "settings_section")

    def test_worker_status_bool(self):
        self.assertIsInstance(layout._worker_status(), bool)


class ShellRenderTest(unittest.TestCase):
    """整壳渲染：所有导航页与设置分区都必须无异常。"""

    def setUp(self):
        self.tmp = tempfile.mktemp(suffix=".db")
        database.DB_PATH = self.tmp
        database.init_database()
        self.cfg_tmp = tempfile.mktemp(suffix=".json")
        self._p = mock.patch.object(theme, "CONFIG_PATH", Path(self.cfg_tmp))
        self._p.start()
        today = date.today().isoformat()
        tomorrow = (date.today() + timedelta(days=1)).isoformat()
        services.create_task("高数", date=today, time="14:00", duration_minutes=90,
                             priority="urgent")
        services.create_task("交作业", date=tomorrow)
        services.create_task("每天吃药", date=today, time="09:00", duration_minutes=30,
                             is_recurring=True, recurrence_frequency="daily",
                             recurrence_interval=1)
        services.create_task("收件箱任务")
        services.create_task("逾期任务", date=(date.today() - timedelta(days=3)).isoformat(),
                             time="10:00")

    def tearDown(self):
        self._p.stop()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.tmp + suffix)
            except OSError:
                pass
        try:
            os.remove(self.cfg_tmp)
        except OSError:
            pass

    def _app(self):
        at = AppTest.from_file(str(APP_PATH), default_timeout=60)
        at.run()
        return at

    def test_shell_renders_without_exception(self):
        at = self._app()
        self.assertEqual([str(e.value) for e in at.exception], [])
        nav_keys = [b.key for b in at.button if b.key.startswith("nav_item_")]
        self.assertEqual(len(nav_keys), len(icons.NAV_KEYS))
        self.assertEqual([r.key for r in at.radio], [], "侧栏不应再有 radio")

    def test_all_nav_pages_render(self):
        at = self._app()
        for nav in icons.NAV_ITEMS:
            at.session_state[layout.NAV_KEY] = nav
            at.run()
            self.assertEqual([str(e.value) for e in at.exception], [],
                             msg=f"页面 {nav} 渲染异常")

    def test_all_settings_sections_render(self):
        at = self._app()
        at.session_state[layout.NAV_KEY] = icons.NAV_SETTINGS
        at.run()
        for sec in icons.SETTINGS_KEYS:
            at.session_state[layout.SETTINGS_NAV_KEY] = sec
            at.run()
            self.assertEqual([str(e.value) for e in at.exception], [],
                             msg=f"设置分区 {sec} 渲染异常")

    def test_today_uses_unified_cards(self):
        at = self._app()
        menus = [b.key for b in at.button
                 if b.key and (b.key.startswith("m_edit_") or b.key.startswith("m_del_"))]
        self.assertTrue(menus, "今日页应有统一任务卡（⋯ 菜单项）")
        self.assertTrue(len(at.checkbox) >= 1)

    def test_sidebar_pin_toggle(self):
        """13.2：只有一个 sidebar control（sidebar_pin_toggle）。"""
        at = self._app()
        self.assertTrue(at.session_state.get(layout.SIDEBAR_PINNED_KEY))
        at.button(key="sidebar_pin_toggle").click().run()
        self.assertFalse(at.session_state.get(layout.SIDEBAR_PINNED_KEY))
        at.button(key="sidebar_pin_toggle").click().run()
        self.assertTrue(at.session_state.get(layout.SIDEBAR_PINNED_KEY))

    def test_dark_mode_renders(self):
        theme.save_appearance({"mode": theme.MODE_DARK, "density": theme.DENSITY_COMFORT})
        at = self._app()
        self.assertEqual([str(e.value) for e in at.exception], [])
        body = "\n".join(m.value for m in at.markdown if isinstance(m.value, str))
        self.assertIn(theme.DARK["bg_primary"], body)

    def test_light_mode_renders(self):
        theme.save_appearance({"mode": theme.MODE_LIGHT, "density": theme.DENSITY_COMFORT})
        at = self._app()
        self.assertEqual([str(e.value) for e in at.exception], [])
        body = "\n".join(m.value for m in at.markdown if isinstance(m.value, str))
        self.assertIn(theme.LIGHT["bg_primary"], body)

    def test_debug_info_hidden_by_default(self):
        at = self._app()
        at.session_state[layout.NAV_KEY] = icons.NAV_SETTINGS
        at.run()
        at.session_state[layout.SETTINGS_NAV_KEY] = icons.SET_REMINDER
        at.run()
        self.assertFalse(at.session_state.get("developer_mode"))
        self.assertNotIn("notif_debug", [b.key for b in at.button])

    def test_debug_info_visible_in_developer_mode(self):
        at = self._app()
        at.session_state[layout.NAV_KEY] = icons.NAV_SETTINGS
        at.run()
        at.session_state[layout.SETTINGS_NAV_KEY] = icons.SET_REMINDER
        at.run()
        at.session_state["developer_mode"] = True
        at.run()
        self.assertIn("notif_debug", [b.key for b in at.button])

    def test_appearance_save_persists(self):
        at = self._app()
        at.session_state[layout.NAV_KEY] = icons.NAV_SETTINGS
        at.run()
        at.session_state[layout.SETTINGS_NAV_KEY] = icons.SET_APPEARANCE
        at.run()
        at.radio(key="appearance_mode").set_value(theme.MODE_DARK)
        at.button(key="appearance_save").click().run()
        self.assertEqual([str(e.value) for e in at.exception], [])
        self.assertEqual(theme.load_appearance()["mode"], theme.MODE_DARK)


if __name__ == "__main__":
    unittest.main()

class KeyboardShortcutTest(unittest.TestCase):
    """Ctrl+N 快捷键：JS 注入存在且幂等、挂载点在主入口。"""

    BASE = Path(__file__).resolve().parent.parent

    def test_helper_exists_and_idempotent(self):
        src = io.open(self.BASE / "taiplan" / "ui" / "components.py",
                      encoding="utf-8-sig").read()
        self.assertIn("def keyboard_shortcuts_js", src)
        self.assertIn("__taiplanHotkeys", src, "必须幂等（防重复监听）")

    def test_mounted_in_main_flow(self):
        src = io.open(self.BASE / "app.py", encoding="utf-8-sig").read()
        self.assertIn("ui_components.keyboard_shortcuts_js()", src)