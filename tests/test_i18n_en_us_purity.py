# -*- coding: utf-8 -*-
"""en-US 用户可见中文清零（0.1.0 hotfix 回归）。

判定口径（三层，互补）：
  1) 静态：渲染调用里的中文字符串常量，若未走 t(...) 且该调用没有 format_func → 判为残留
  2) 显示映射：en-US 下所有"取值 → 标签"映射函数必须只输出 ASCII；zh-CN 下必须复现原中文
  3) 运行时不变量：真实渲染各页面，正文/按钮/提示类文本（不含 CSS 与控件取值）不得出现中文
另含"持久化取值不得改变"的兼容断言。
"""
import ast
import io
import os
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

from tests import tests_env  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
CJK = re.compile(r"[\u4e00-\u9fff]")

WIDGETS = {
    "write", "markdown", "caption", "button", "radio", "checkbox", "selectbox", "multiselect",
    "text_input", "text_area", "number_input", "slider", "toggle", "select_slider",
    "segmented_control", "pills", "header", "subheader", "title", "warning", "error", "info",
    "success", "toast", "expander", "tabs", "metric", "status", "date_input", "time_input",
    "form", "form_submit_button", "download_button", "link_button", "page_link", "progress",
    "spinner", "help", "dialog", "popover", "badge", "chat_input", "chat_message", "code",
    "json", "table", "dataframe", "data_editor", "file_uploader", "color_picker", "html",
    "latex", "exception", "echo", "image", "video", "audio", "divider", "container", "columns",
    "empty", "graphviz_chart", "plotly_chart", "altair_chart", "vega_lite_chart", "map",
    "navigation", "switch_page", "logo", "feedback", "text",
}
COMPONENTS = {"toast_ok", "toast_err", "friendly_error", "status_row", "empty_state",
              "section_title", "page_header", "group_label", "duration_label", "callout",
              "badge", "list_row", "metric_row", "banner", "chip", "tag"}


def _scan_unmapped_cjk():
    offenders = []
    for p in sorted(ROOT.glob("*.py")) + sorted((ROOT / "taiplan" / "ui").glob("*.py")):
        if p.name.startswith("test_"):
            continue
        tree = ast.parse(io.open(p, encoding="utf-8-sig", errors="replace").read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            target = None
            if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name) \
                    and fn.value.id == "st" and fn.attr in WIDGETS:
                target = f"st.{fn.attr}"
            elif isinstance(fn, ast.Attribute) and fn.attr in COMPONENTS:
                target = fn.attr
            elif isinstance(fn, ast.Name) and fn.id in COMPONENTS:
                target = fn.id
            if not target:
                continue
            has_ff = any(k.arg == "format_func" for k in node.keywords)
            if has_ff:
                continue  # 取值由 format_func 映射为本地化标签

            def walk(n, inside_t=False):
                if isinstance(n, ast.Call):
                    name = n.func.id if isinstance(n.func, ast.Name) else (
                        n.func.attr if isinstance(n.func, ast.Attribute) else "")
                    inside = inside_t or name == "t"
                    for a in n.args:
                        walk(a, inside)
                    for k in n.keywords:
                        walk(k.value, inside)
                    return
                if isinstance(n, ast.Constant) and isinstance(n.value, str) and not inside_t:
                    if CJK.search(n.value):
                        offenders.append(
                            f"{p.relative_to(ROOT).as_posix()}:{n.lineno} [{target}] "
                            f"{n.value[:40]!r}")
                for c in ast.iter_child_nodes(n):
                    walk(c, inside_t)
            walk(node)
    return offenders


class StaticPurityTest(unittest.TestCase):
    def test_no_unmapped_chinese_in_render_calls(self):
        offenders = _scan_unmapped_cjk()
        self.assertEqual(offenders, [],
                         "仍有未本地化的用户可见中文（应走 t(...) 或用 format_func 映射）:\n"
                         + "\n".join(offenders))


class DisplayMappingTest(unittest.TestCase):
    """en-US 只输出 ASCII；zh-CN 复现原中文（证明显示保真、取值未动）。"""

    def setUp(self):
        from taiplan import i18n

        self.i18n = i18n
        self._orig = i18n.get_language()

    def tearDown(self):
        self.i18n.set_language(self._orig)

    def test_mappings_are_english_and_chinese(self):
        import app
        from taiplan.ui import theme as theme
        from taiplan.i18n import LANGUAGE_EN_US, LANGUAGE_ZH_CN
        from taiplan import calendar_view

        pairs = [
            ("mode_label", theme.mode_label, theme.MODE_OPTIONS),
            ("density_label", theme.density_label, theme.DENSITY_OPTIONS),
            ("_priority_label", app._priority_label, ["低", "普通", "紧急"]),
            ("_recurrence_label", app._recurrence_label,
             ["每小时", "每天", "工作日", "周末", "每周", "每月", "每3个月", "每年"]),
            ("_end_label", app._end_label, ["永不结束", "指定日期"]),
            ("_delete_scope_label", app._delete_scope_label,
             ["仅删除这一次", "从这一次起全部删除", "删除整个重复系列"]),
            ("_close_label", app._close_label, ["最小化到系统托盘", "完全退出"]),
            ("_create_mode_label", app._create_mode_label,
             [app.CREATE_MODE_QUICK, app.CREATE_MODE_DETAIL]),
            ("_duration_value_label", app._duration_value_label,
             ["15 分钟", "1 小时", "自定义"]),
            ("_view_label", calendar_view._view_label, ["月", "周", "日"]),
            ("_duration_label", calendar_view._duration_label, ["45 分钟", "1.5 小时"]),
            ("_slot_label", calendar_view._slot_label, ["15 分钟", "60 分钟"]),
        ]
        for name, fn, values in pairs:
            self.i18n.set_language(LANGUAGE_EN_US)
            for v in values:
                out = fn(v)
                self.assertFalse(CJK.search(str(out)),
                                 f"{name}({v!r}) 在 en-US 下仍含中文: {out!r}")
            self.i18n.set_language(LANGUAGE_ZH_CN)
            for v in values:
                self.assertTrue(CJK.search(str(fn(v))),
                                f"{name}({v!r}) 在 zh-CN 下应是中文: {fn(v)!r}")

    def test_appearance_modes_keep_chinese_values(self):
        from taiplan.ui import theme as theme

        self.assertEqual(theme.MODE_OPTIONS, ["跟随系统", "浅色", "深色"])
        self.assertEqual(theme.DENSITY_OPTIONS, ["舒适", "紧凑"])
        self.assertEqual(theme.MODE_DARK, "深色")
        self.assertEqual(theme.DENSITY_COMPACT, "紧凑")


class PersistedValueCompatTest(unittest.TestCase):
    """不改持久化取值/语义：appearance 既有中文取值必须原样读写。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="taiplan_i18n_")
        self._env = os.environ.get("TODO_APP_DATA_DIR")
        os.environ["TODO_APP_DATA_DIR"] = self.tmp
        import importlib

        from taiplan import app_paths
        from taiplan.ui import theme as theme

        self.theme = importlib.reload(theme)
        importlib.reload(app_paths)

    def tearDown(self):
        import shutil as _sh

        if self._env is None:
            os.environ.pop("TODO_APP_DATA_DIR", None)
        else:
            os.environ["TODO_APP_DATA_DIR"] = self._env
        _sh.rmtree(self.tmp, ignore_errors=True)

    def test_roundtrip_writes_chinese_values(self):
        import json

        self.theme.save_appearance({"mode": self.theme.MODE_DARK,
                                    "density": self.theme.DENSITY_COMPACT,
                                    "accent": "coral"})
        cfg = self.theme.load_appearance()
        self.assertEqual(cfg["mode"], "深色")
        self.assertEqual(cfg["density"], "紧凑")
        from taiplan import app_paths

        raw = json.loads(io.open(app_paths.get_config_path("appearance.json"),
                                 encoding="utf-8").read())
        self.assertEqual(raw["mode"], "深色")
        self.assertEqual(raw["density"], "紧凑")

    def test_legacy_chinese_config_still_loads(self):
        from taiplan import app_paths

        p = app_paths.get_config_path("appearance.json")
        Path(p).parent.mkdir(parents=True, exist_ok=True)
        io.open(p, "w", encoding="utf-8").write('{"mode": "浅色", "density": "舒适"}')
        cfg = self.theme.load_appearance()
        self.assertEqual(cfg["mode"], "浅色")
        self.assertEqual(cfg["density"], "舒适")


class RuntimePurityTest(unittest.TestCase):
    """真实渲染页面：正文/按钮/提示类文本不得出现中文（排除 CSS 与控件取值）。"""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="taiplan_rt_")
        cls._env = os.environ.get("TODO_APP_DATA_DIR")
        os.environ["TODO_APP_DATA_DIR"] = cls.tmp
        os.environ["TODO_APP_DISABLE_MIGRATION"] = "1"
        os.environ.pop("TODO_APP_LEGACY_APP_DIR", None)
        from taiplan import database

        cls._db_path = database.DB_PATH
        database.DB_PATH = str(Path(cls.tmp) / "todo.db")
        database.init_database()
        from taiplan import services

        services.create_task("Sample task", date="2026-10-07", time="09:00",
                             duration_minutes=60, priority="urgent")
        cfg = Path(cls.tmp) / "config"
        cfg.mkdir(parents=True, exist_ok=True)
        (cfg / "language_config.json").write_text('{"language": "en-US"}', encoding="utf-8")

    @classmethod
    def tearDownClass(cls):
        import shutil as _sh

        from taiplan import database

        database.DB_PATH = cls._db_path
        if cls._env is None:
            os.environ.pop("TODO_APP_DATA_DIR", None)
        else:
            os.environ["TODO_APP_DATA_DIR"] = cls._env
        os.environ.pop("TODO_APP_DISABLE_MIGRATION", None)
        _sh.rmtree(cls.tmp, ignore_errors=True)

    def _texts(self, nav):
        from streamlit.testing.v1 import AppTest

        from taiplan.ui import icons as icons

        at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120)
        at.run()
        at.session_state["nav"] = nav
        at.run()
        self.assertEqual([str(e.value) for e in at.exception], [])
        chunks = []
        for coll in ("markdown", "caption", "warning", "error", "info", "success", "header",
                     "subheader", "title"):
            for el in getattr(at, coll):
                chunks.append(str(getattr(el, "value", "")))
        for el in at.button:
            chunks.append(str(getattr(el, "label", "")))
        return chunks

    @staticmethod
    def _strip_css(chunks):
        out = []
        for c in chunks:
            out.append(re.sub(r"(?is)<style.*?</style>", " ", c))
        return out

    def test_en_us_pages_have_no_chinese_text(self):
        from taiplan.ui import icons as icons

        bad = {}
        for nav in (icons.NAV_TODAY, icons.NAV_INBOX, icons.NAV_CALENDAR, icons.NAV_UPCOMING,
                    icons.NAV_COMPLETED, icons.NAV_REMINDER, icons.NAV_SETTINGS):
            hits = sorted({c.strip()[:60] for c in self._strip_css(self._texts(nav))
                           if CJK.search(c)})
            if hits:
                bad[nav] = hits[:6]
        self.assertEqual(bad, {}, f"en-US 下仍有中文正文: {bad}")

    def test_zh_cn_pages_are_chinese(self):
        """非空验证：切回 zh-CN 必须出现中文，否则上面的断言可能是空测。"""
        import json

        from taiplan.ui import icons as icons
        from taiplan.i18n import LANGUAGE_ZH_CN
        from taiplan import i18n

        cfg = Path(self.tmp) / "config" / "language_config.json"
        cfg.write_text(json.dumps({"language": LANGUAGE_ZH_CN}), encoding="utf-8")
        i18n.set_language(LANGUAGE_ZH_CN)
        try:
            hits = [c for c in self._strip_css(self._texts(icons.NAV_TODAY)) if CJK.search(c)]
            self.assertTrue(hits, "zh-CN 下没有任何中文，说明语言切换未生效")
        finally:
            cfg.write_text('{"language": "en-US"}', encoding="utf-8")


if __name__ == "__main__":
    unittest.main(verbosity=2)
