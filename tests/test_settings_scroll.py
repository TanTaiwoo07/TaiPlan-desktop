# -*- coding: utf-8 -*-
"""W3.5 Settings 连续滚动页测试。

覆盖要求：
  * 一次渲染全部 8 个 Section（数据管理随 0.1.1 归位）
  * zh-CN / en-US 八个标题都正确
  * language 切换仍持久化
  * AI / Reminder / Calendar / Desktop 原有控件仍存在
  * About 内容正确 + TaiWoo_Chen 精确拼写
  * 没有 st.exception / st.error
  * 不新增未国际化硬编码中文 UI
  * anchor / sticky 目录 / 响应式实现的结构性断言
"""
import io
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

from tests import tests_env  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from taiplan import version  # noqa: E402
from taiplan.ui import icons as icons  # noqa: E402
from taiplan.ui import layout as layout  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

APP_PATH = str(ROOT / "app.py")
ORDER_KEYS = ("appearance", "language", "ai", "reminder", "calendar", "desktop",
              "data", "about")
ANCHORS = {
    "appearance": "settings-appearance",
    "language": "settings-language",
    "ai": "settings-ai",
    "reminder": "settings-reminders",
    "calendar": "settings-calendar",
    "desktop": "settings-desktop",
    "data": "settings-data",
    "about": "settings-about",
}
ZH_TITLES = ("外观", "语言", "AI", "提醒", "日历", "桌面", "数据管理", "关于")
EN_TITLES = ("Appearance", "Language", "AI", "Reminders", "Calendar", "Desktop",
             "Data", "About")


class _Base(unittest.TestCase):
    def setUp(self):
        from taiplan import database
        from taiplan import services

        self.tmp = tempfile.mktemp(suffix=".db")
        database.DB_PATH = self.tmp
        database.init_database()
        services.create_task("settings probe", date="2026-10-06", time="14:00",
                             duration_minutes=60)
        from taiplan.i18n import set_language

        set_language("system")
        self.addCleanup(set_language, "system")

    def settings_app(self):
        at = AppTest.from_file(APP_PATH, default_timeout=90)
        at.run()
        at.session_state[layout.NAV_KEY] = icons.NAV_SETTINGS
        at.run()
        return at

    def markdown_text(self, at):
        """收集页面上所有可见文本元素。

        注意：About 的 Version / 版权 / 许可证是用 st.caption 渲染的，
        只扫 at.markdown 会漏掉它们，所以这里把 caption/title/header/subheader 一起收。
        """
        chunks = []
        for attr in ("markdown", "caption", "title", "header", "subheader", "text"):
            for element in getattr(at, attr, []) or []:
                value = getattr(element, "value", None)
                if isinstance(value, str):
                    chunks.append(value)
        return "\n".join(chunks)

    def assert_clean(self, at):
        self.assertEqual([str(e.value) for e in at.exception], [], "不应有 st.exception")
        self.assertEqual([str(e.value) for e in at.error], [], "不应有 st.error")


class StructureTest(_Base):
    def test_all_sections_render_at_once(self):
        at = self.settings_app()
        self.assert_clean(at)
        md = self.markdown_text(at)
        for key, anchor in ANCHORS.items():
            self.assertIn(f'id="{anchor}"', md, f"缺少 anchor: {anchor}")
        # 八个 Section 的标题必须同时出现在同一页面（说明是连续页而非按分区分页）
        for title in ZH_TITLES:
            self.assertIn(title, md, f"同一页缺少 Section 标题: {title}")

    def test_section_order_is_fixed(self):
        at = self.settings_app()
        md = self.markdown_text(at)
        positions = [md.index(f'id="{ANCHORS[k]}"') for k in ORDER_KEYS]
        self.assertEqual(positions, sorted(positions), "Section 顺序不符合规范")

    def test_toc_links_point_to_real_anchors(self):
        at = self.settings_app()
        md = self.markdown_text(at)
        hrefs = re.findall(r'href="#(settings-[a-z]+)"', md)
        self.assertEqual(hrefs, [ANCHORS[k] for k in ORDER_KEYS])
        ids = set(re.findall(r'id="(settings-[a-z]+)"', md))
        for h in hrefs:
            self.assertIn(h, ids, f"目录链接 {h} 没有对应 anchor")

    def test_no_single_section_switching_left(self):
        """不允许再出现「选择分类 → rerun → 只显示该分类」的组织方式。"""
        src = io.open(ROOT / "app.py", encoding="utf-8").read()
        start = src.index("def render_settings_page():")
        body = src[start:start + 2000]
        self.assertNotIn("elif section ==", body)
        self.assertNotIn("settings_shell(", body)

    def test_sticky_toc_and_responsive_rules_present(self):
        css = layout._SETTINGS_CSS
        self.assertIn("position: sticky", css)
        self.assertIn("scroll-behavior: smooth", css)
        self.assertIn("min-width: 0", css)
        self.assertIn("box-sizing: border-box", css)
        self.assertIn("max-width: 100%", css)
        self.assertIn("@media (max-width: 1099px)", css)
        self.assertIn("@media (max-width: 759px)", css)
        # 窄屏隐藏目录（含用 :has() 折叠空列，避免残留空白栏）
        self.assertIn("display: none !important", css)
        self.assertIn(":has(.st-key-settings_toc)", css)

    def test_anchor_helper_is_stable(self):
        for key, anchor in ANCHORS.items():
            self.assertEqual(icons.settings_anchor(key), anchor)
        self.assertEqual(icons.settings_anchor("unknown"), "settings-unknown")

    def test_settings_entries_order_matches_spec(self):
        self.assertEqual([k for k, _, _ in icons.SETTINGS_ENTRIES], list(ORDER_KEYS))


class TitlesTest(_Base):
    def _set_lang(self, lang):
        """把语言写进 config（AppTest 会重新执行 app.py，读到的是持久化设置）。"""
        from taiplan import language_store
        from taiplan.i18n import set_language

        path = language_store.config_path()
        backup = path.read_bytes() if path.is_file() else None

        def restore():
            if backup is not None:
                path.write_bytes(backup)
            else:
                path.unlink(missing_ok=True)
            set_language("system")

        self.addCleanup(restore)
        language_store.save_setting(lang)
        self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["language"], lang)
        return path

    def test_zh_titles(self):
        self._set_lang("zh-CN")
        at = self.settings_app()
        self.assert_clean(at)
        md = self.markdown_text(at)
        for title in ZH_TITLES:
            self.assertIn(title, md, f"zh-CN 缺少标题: {title}")

    def test_en_titles(self):
        self._set_lang("en-US")
        at = self.settings_app()
        self.assert_clean(at)
        md = self.markdown_text(at)
        for title in EN_TITLES:
            self.assertIn(title, md, f"en-US 缺少标题: {title}")

    def test_toc_labels_both_languages(self):
        for lang, titles in (("zh-CN", ZH_TITLES), ("en-US", EN_TITLES)):
            self._set_lang(lang)
            at = self.settings_app()
            self.assert_clean(at)
            md = self.markdown_text(at)
            for title in titles:
                self.assertIn(title, md, f"{lang} 目录/标题缺少 {title}")


class ControlsTest(_Base):
    def test_existing_controls_still_present(self):
        at = self.settings_app()
        self.assert_clean(at)
        keys = [str(getattr(e, "key", "") or "") for e in at.button]
        toggles = [str(getattr(t, "key", "") or "") for t in at.toggle]
        radios = [str(getattr(r, "key", "") or "") for r in at.radio]
        # Language 三分支仍存在
        self.assertTrue(any("language_choice" == k for k in radios), f"radios={radios}")
        # AI / 提醒 / 外观 / 桌面 的保存类控件仍在
        joined = " ".join(keys + toggles)
        self.assertIn("save_language", joined)
        self.assertTrue(len(at.text_input) >= 3, f"AI 输入控件数={len(at.text_input)}")
        self.assertTrue(len([t for t in at.toggle]) >= 1, "提醒/桌面 toggle 应存在")
        self.assertTrue(len(at.checkbox) >= 1 or len(at.toggle) >= 1)

    def test_language_switch_still_persists(self):
        from taiplan import language_store

        old = None
        p = language_store.config_path()
        if p.is_file():
            old = p.read_bytes()
        try:
            language_store.save_setting("en-US")
            data = json.loads(p.read_text(encoding="utf-8"))
            self.assertEqual(data["language"], "en-US")
            from taiplan.i18n import get_language

            self.assertEqual(get_language(), "en-US")
        finally:
            if old is not None:
                p.write_bytes(old)
            else:
                p.unlink(missing_ok=True)
            from taiplan.i18n import set_language

            set_language("system")


class AboutTest(_Base):
    def test_about_content(self):
        from taiplan.i18n import set_language

        set_language("zh-CN")
        at = self.settings_app()
        md = self.markdown_text(at)
        for needle in ("TaiPlan", "Tasks into time.", f"Version {version.__version__}",
                       "Designed & Developed by", "TaiWoo_Chen", "© 2026 TaiWoo_Chen",
                       "MIT License", "https://github.com/TanTaiwoo07/TaiPlan-desktop"):
            self.assertIn(needle, md, f"About 缺少: {needle}")

    def test_taiwoo_chen_exact_spelling(self):
        for rel in ("taiplan/product_info.py", "taiplan/i18n/zh_CN.py", "taiplan/i18n/en_US.py"):
            src = io.open(ROOT / rel, encoding="utf-8-sig").read()
            self.assertIn("TaiWoo_Chen", src, f"{rel} 缺少 TaiWoo_Chen")
        for rel in ("app.py", "taiplan/product_info.py", "taiplan/i18n/zh_CN.py", "taiplan/i18n/en_US.py",
                    "taiplan/ui/layout.py", "taiplan/ui/icons.py"):
            src = io.open(ROOT / rel, encoding="utf-8-sig").read()
            for wrong in ("Taiwoo_Chen", "TaiWooChen", "TaiwooChen", "taiwoo_chen",
                          "Taiwoo_chen"):
                self.assertNotIn(wrong, src, f"{rel} 出现错误拼写 {wrong}")

    def test_author_comes_from_product_info(self):
        src = io.open(ROOT / "app.py", encoding="utf-8-sig").read()
        self.assertIn("product_info.APP_AUTHOR", src, "作者应来自 product_info 单一来源")

    def test_repo_url_is_real(self):
        """0.1.2 起：仓库真实存在，About 页必须给出可点击的真实 GitHub 链接。

        历史语义（0.1.0–0.1.1）：仓库未建，禁止任何假 URL。
        现在反转：必须是 TanTaiwoo07/TaiPlan-desktop，且 About 渲染走链接分支。
        """
        from taiplan import product_info

        self.assertEqual(product_info.APP_REPO_URL,
                         "https://github.com/TanTaiwoo07/TaiPlan-desktop")
        # About 页的分支逻辑：有 URL → 链接；无 URL 才显示 coming soon
        src = io.open(ROOT / "app.py", encoding="utf-8-sig").read()
        self.assertIn('if product_info.APP_REPO_URL:', src)
        self.assertIn('[GitHub]({product_info.APP_REPO_URL})', src)


class HardcodedTextTest(_Base):
    def test_no_new_hardcoded_zh_in_settings_ui(self):
        """Settings 相关渲染代码里，UI 调用行不得再有未走 t() 的中文。"""
        cjk = re.compile(r"[\u4e00-\u9fff]")
        strlit = re.compile(r"""(['"])((?:(?!\1).)*[\u4e00-\u9fff](?:(?!\1).)*)\1""")
        ui_call = re.compile(r"(section_title|group_label|empty_state|st\.(markdown|button|"
                             r"toggle|checkbox|radio|caption|info|warning|error|success|"
                             r"text_input|date_input|popover|expander)\b|label=|help=)")
        offenders = []
        for rel in ("app.py", "taiplan/ui/layout.py", "taiplan/ui/components.py", "taiplan/ui/icons.py",
                    "taiplan/calendar_view.py"):
            for i, line in enumerate(io.open(ROOT / rel, encoding="utf-8-sig",
                                             errors="replace").read().split("\n"), 1):
                s = line.strip()
                if s.startswith("#") or "t(" in line or not cjk.search(line):
                    continue
                if not ui_call.search(line):
                    continue
                for m in strlit.finditer(line):
                    offenders.append(f"{rel}:{i}: {m.group(2)[:40]}")
        self.assertEqual(offenders, [], f"发现未国际化的硬编码中文 UI: {offenders}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
