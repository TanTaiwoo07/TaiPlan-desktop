"""第 13.2 阶段：Fluent Navigation Rail 侧栏回归守卫。

守住：
    1. 界面里不出现 Material 图标裸文本
    2. 侧栏只有 **一个** sidebar control（原生折叠/展开控件必须被隐藏）
    3. pinned / rail 两种 CSS 几何：240px ↔ 72px，hover 244px overlay
    4. hover 展开不改变主内容布局（靠 stMain 固定 margin + fixed sidebar）
    5. hover 不 rerun：rail 只用 :hover，没有 hover 回调改 session_state
    6. 只有一个持久状态 sidebar_pinned
"""

import io
import os
import re
import unittest
from pathlib import Path

from tests import tests_env  # noqa: F401  必须早于项目模块导入

import ui.icons as icons
import ui.layout as layout
import ui.theme as theme

from streamlit.testing.v1 import AppTest

APP_PATH = str(Path(__file__).resolve().parent.parent / "app.py")
ROOT = Path(__file__).resolve().parent.parent


class _Base(unittest.TestCase):

    def setUp(self):
        import database
        import services
        import tempfile
        self.tmp = tempfile.mktemp(suffix=".db")
        database.DB_PATH = self.tmp
        database.init_database()
        services.create_task("rail 回归任务", date="2026-10-04", time="14:00",
                             duration_minutes=60)

    def _app(self):
        at = AppTest.from_file(APP_PATH, default_timeout=60)
        at.run()
        return at

    def assert_clean(self, at):
        self.assertEqual([str(e.value) for e in at.exception], [])
        self.assertEqual([str(e.value) for e in at.error], [],
                         "页面渲染出现了被拦截的错误")

    def page_html(self, at):
        return "\n".join(str(m.value) for m in at.markdown
                         if isinstance(m.value, str) and "<style>" not in m.value)

    def injected_css(self, at):
        return "\n".join(str(m.value) for m in at.markdown
                         if isinstance(m.value, str) and "<style>" in m.value)


class MaterialIconLeakTest(_Base):

    def test_no_material_icon_as_plain_text(self):
        at = self._app()
        chunks = []
        for attr in ("markdown", "caption", "text", "warning", "error", "info"):
            for el in getattr(at, attr, []):
                if isinstance(getattr(el, "value", None), str):
                    chunks.append(el.value)
        self.assertNotIn(":material/", "\n".join(chunks))

    def test_no_malformed_material_syntax_in_source(self):
        pattern = re.compile(":" + "material" + r"[_-][a-zA-Z]")
        targets = [p for p in ROOT.glob("*.py")] + [p for p in (ROOT / "ui").glob("*.py")]
        offenders = []
        for path in targets:
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f"{path.name}:{i}")
        self.assertEqual(offenders, [])

    def test_logo_uses_unicode_not_material(self):
        at = self._app()
        logo = [l for l in self.page_html(at).split("\n") if 'class="app-logo' in l]
        self.assertTrue(logo)
        self.assertNotIn("material", logo[0])
        self.assertIn(icons.APP_LOGO_GLYPH, logo[0])


class SingleControlTest(_Base):
    """只能有一个 sidebar control。"""

    def test_only_one_pin_control(self):
        at = self._app()
        pins = [b.key for b in at.button if b.key and "pin" in b.key]
        self.assertEqual(pins, ["sidebar_pin_toggle"])

    def test_native_streamlit_controls_hidden(self):
        for pinned in (True, False):
            css = theme.build_css("light", sidebar_pinned=pinned)
            for testid in ("stSidebarCollapseButton", "stExpandSidebarButton",
                           "stSidebarResizeHandle"):
                self.assertIn(testid, css, f"{testid} 必须被隐藏")
            self.assertRegex(css, r"stSidebarCollapseButton[^}]*display:\s*none")

    def test_no_legacy_arrow_buttons(self):
        at = self._app()
        keys = [b.key for b in at.button if b.key]
        self.assertNotIn("nav_collapse", keys)
        self.assertNotIn("nav_expand", keys)

    def test_control_icon_differs_per_state(self):
        pinned_css = theme.build_css("light", sidebar_pinned=True)
        rail_css = theme.build_css("light", sidebar_pinned=False)
        self.assertIn("width: 240px !important", pinned_css)
        self.assertIn("width: 72px !important", rail_css)
        self.assertIn("width: 244px !important", rail_css)


class RailGeometryTest(_Base):

    def test_pinned_geometry(self):
        css = theme.build_css("light", sidebar_pinned=True)
        self.assertIn("width: 240px !important", css)
        self.assertNotIn("margin-left: 72px", css.split("@media")[0],
                         "pinned 时不需要给主内容留 rail 位置")

    def test_rail_geometry_and_overlay(self):
        css = theme.build_css("light", sidebar_pinned=False)
        self.assertIn("width: 72px !important", css)
        self.assertIn("position: fixed", css)
        # z-index 必须高于 Streamlit header（实测 999990）：它是一条全宽 60px 高、
        # pointer-events:auto 的层，压住侧栏顶部会让 hover 不触发、pin 按钮点不到
        mz = re.search(r'\[data-testid="stSidebar"\]\s*\{[^}]*z-index:\s*(\d+)', css)
        self.assertIsNotNone(mz, "rail 规则里没有 z-index")
        self.assertGreater(int(mz.group(1)), 999990,
                           f"rail z-index={mz.group(1)} 会被 header(999990) 覆盖")
        self.assertIn('stMain"] { margin-left: 72px !important; }', css)

    def test_hover_expands_to_overlay_width(self):
        css = theme.build_css("light", sidebar_pinned=False)
        hover_block = css.split("[data-testid=\"stSidebar\"]:hover {")[1].split("}")[0]
        self.assertIn("width: 244px", hover_block)
        self.assertIn("box-shadow", hover_block)

    def test_transition_is_short_and_limited(self):
        css = theme.build_css("light", sidebar_pinned=False)
        self.assertIn(".18s ease", css)
        self.assertNotIn("animation:", css)

    def test_labels_hidden_only_when_not_hovered(self):
        css = theme.build_css("light", sidebar_pinned=False)
        self.assertIn(':not(:hover) .app-logo-text', css)
        self.assertIn(':not(:hover) .nav-caption', css)
        self.assertIn("opacity: 0 !important", css)

    def test_hover_none_fallback_keeps_control_usable(self):
        css = theme.build_css("light", sidebar_pinned=False)
        self.assertIn("@media (hover: none)", css)
        self.assertIn("pointer-events: auto", css)

    def test_narrow_window_falls_back_to_rail(self):
        for pinned in (True, False):
            css = theme.build_css("light", sidebar_pinned=pinned)
            self.assertIn("@media (max-width: 1050px)", css)

    def test_no_has_selector_in_rail_rules(self):
        css = theme.build_css("light", sidebar_pinned=False)
        block = css.split("只保留一个 sidebar control")[-1]
        stripped = re.sub(r"/\*.*?\*/", "", block, flags=re.S)
        self.assertNotIn(":has(", stripped)


class StateModelTest(_Base):

    def test_single_persistent_state(self):
        self.assertEqual(layout.SIDEBAR_PINNED_KEY, "sidebar_pinned")
        src = (ROOT / "ui" / "layout.py").read_text(encoding="utf-8")
        for banned in ("nav_hover", "sidebar_hovered", "sidebar_expanded",
                       "sidebar_temp_expanded", "nav_hovered"):
            self.assertNotIn(banned, src)

    def test_default_is_pinned(self):
        at = self._app()
        self.assertTrue(at.session_state.get(layout.SIDEBAR_PINNED_KEY))

    def test_legacy_nav_collapsed_migrates(self):
        at = self._app()
        del at.session_state[layout.SIDEBAR_PINNED_KEY]
        at.session_state[layout.LEGACY_COLLAPSE_KEY] = True
        at.run()
        self.assertFalse(at.session_state.get(layout.SIDEBAR_PINNED_KEY))

    def test_toggle_flips_pinned_and_keeps_nav(self):
        at = self._app()
        at.session_state[layout.NAV_KEY] = icons.NAV_CALENDAR
        at.run()
        at.button(key="sidebar_pin_toggle").click().run()
        self.assertFalse(at.session_state.get(layout.SIDEBAR_PINNED_KEY))
        self.assertEqual(at.session_state.get(layout.NAV_KEY), icons.NAV_CALENDAR)
        at.button(key="sidebar_pin_toggle").click().run()
        self.assertTrue(at.session_state.get(layout.SIDEBAR_PINNED_KEY))
        self.assertEqual(at.session_state.get(layout.NAV_KEY), icons.NAV_CALENDAR)

    def test_hover_has_no_callback_in_source(self):
        """hover 必须是纯 CSS：源码里不能有 hover 回调去改 state。"""
        for rel in ("ui/layout.py", "ui/theme.py", "app.py"):
            src = (ROOT / rel).read_text(encoding="utf-8")
            for banned in ("on_hover", "on_mouseover", "on_mouse_enter"):
                self.assertNotIn(banned, src, f"{rel} 不应有 {banned}")


class StabilityTest(_Base):

    def test_twenty_toggles_stable(self):
        at = self._app()
        for i in range(20):
            at.session_state[layout.SIDEBAR_PINNED_KEY] = bool(i % 2 == 0)
            at.run()
            self.assert_clean(at)

    def test_all_pages_render_in_both_states(self):
        at = self._app()
        for pinned in (True, False):
            at.session_state[layout.SIDEBAR_PINNED_KEY] = pinned
            for key in icons.NAV_KEYS:
                at.session_state[layout.NAV_KEY] = key
                at.run()
                self.assert_clean(at)

    def test_settings_sections_render_in_both_states(self):
        at = self._app()
        at.session_state[layout.NAV_KEY] = icons.NAV_SETTINGS
        for pinned in (True, False):
            at.session_state[layout.SIDEBAR_PINNED_KEY] = pinned
            for sec in icons.SETTINGS_KEYS:
                at.session_state[layout.SETTINGS_NAV_KEY] = sec
                at.run()
                self.assert_clean(at)

    def test_nav_buttons_present_in_both_states(self):
        at = self._app()
        for pinned in (True, False):
            at.session_state[layout.SIDEBAR_PINNED_KEY] = pinned
            at.run()
            keys = [b.key for b in at.button if (b.key or "").startswith("nav_item_")]
            self.assertEqual(keys, [f"nav_item_{k}" for k in icons.NAV_KEYS])


class OptionalLibraryTest(unittest.TestCase):
    """streamlit-rail-nav 是可选的第三方增强，缺失也必须能跑。"""

    def test_app_does_not_require_rail_nav(self):
        for rel in ("app.py", "ui/layout.py", "ui/theme.py"):
            src = (ROOT / rel).read_text(encoding="utf-8")
            self.assertNotIn("import streamlit_rail_nav", src)
            self.assertNotIn("from streamlit_rail_nav", src)


if __name__ == "__main__":
    unittest.main()

class TooltipResidueTest(unittest.TestCase):
    """侧栏不得使用 Streamlit 提示气泡（help=）。

    真实故障：rail 宽度动画 + 鼠标移出窗口时 WebView2 收不到 mouseleave，
    气泡不消失、连续悬停多个导航项会叠成一片（用户截图可见）。
    标签本身始终渲染，因此 help= 纯属多余的残留风险。
    """

    def _help_calls(self, path):
        import ast as _ast

        tree = _ast.parse(io.open(path, encoding="utf-8-sig").read())
        rows = []
        for node in _ast.walk(tree):
            if isinstance(node, _ast.Call):
                for kw in node.keywords:
                    if kw.arg == "help":
                        rows.append(node.lineno)
        return rows

    def test_no_help_tooltips_in_sidebar(self):
        path = Path(__file__).resolve().parent.parent / "ui" / "layout.py"
        self.assertEqual(self._help_calls(path), [],
                         "侧栏不允许再出现 help=（会留下不消失的提示气泡）")

    def test_nav_labels_are_still_rendered_and_hover_revealed(self):
        """去掉气泡不等于去掉标签：标签仍渲染，rail 悬停由 CSS 恢复。"""
        src = io.open(Path(__file__).resolve().parent.parent / "ui" / "layout.py",
                      encoding="utf-8-sig").read()
        self.assertIn('st.button(label, key=f"nav_item_{key}"', src)
        theme = io.open(Path(__file__).resolve().parent.parent / "ui" / "theme.py",
                        encoding="utf-8-sig").read()
        self.assertIn('.st-key-appnav [data-testid="stButton"] button p', theme)
        self.assertIn('[data-testid="stSidebar"]:not(:hover)', theme)

