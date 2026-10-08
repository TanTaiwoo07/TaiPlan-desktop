# -*- coding: utf-8 -*-
"""0.1.2：主题切换回归（系统深色 + 应用浅色的四类泄漏）。

覆盖：
  * streamlit_theme_options：跟随系统不传 base；显式浅/深传 base 与四色
  * streamlit_runner：child_flag_options 会注入 theme.*（frozen 行为）
  * CSS 兜底层：build_css 按模式钉住 widget 色值，无伪代码残留
  * 重启机制：保存主题 → 标记文件 → runtime 退出后消费并拉起（契约层）
  * 启动模式记录：_startup_mode 在进程内稳定
"""
import io
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import tests_env  # noqa: F401

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


class ThemeOptionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="themeopt_"))
        self._env = os.environ.get("TODO_APP_DATA_DIR")
        os.environ["TODO_APP_DATA_DIR"] = str(self.tmp)
        import ui.theme as theme

        self.theme = theme

    def tearDown(self):
        if self._env is None:
            os.environ.pop("TODO_APP_DATA_DIR", None)
        else:
            os.environ["TODO_APP_DATA_DIR"] = self._env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_system_mode_does_not_force_base(self):
        options = self.theme.streamlit_theme_options({"mode": "跟随系统"})
        self.assertNotIn("theme.base", options,
                         "跟随系统必须让 Streamlit 自己决定基座")
        self.assertIn("theme.backgroundColor", options)

    def test_explicit_modes_pass_base_and_colors(self):
        light = self.theme.streamlit_theme_options({"mode": "浅色"})
        dark = self.theme.streamlit_theme_options({"mode": "深色"})
        self.assertEqual(light["theme.base"], "light")
        self.assertEqual(dark["theme.base"], "dark")
        self.assertNotEqual(light["theme.backgroundColor"],
                            dark["theme.backgroundColor"])
        self.assertNotEqual(light["theme.textColor"], dark["theme.textColor"])

    def test_runner_injects_theme_options(self):
        """child_flag_options 必须带上 theme.*（frozen 的 server 参数）。"""
        import streamlit_runner

        options = streamlit_runner.child_flag_options(8501)
        self.assertIn("client.toolbarMode", options)
        self.assertTrue(any(k.startswith("theme.") for k in options),
                        f"server options 缺 theme.*: {sorted(options)}")


class CssOverrideTest(unittest.TestCase):
    def test_build_css_pins_widget_colors_both_modes(self):
        import ui.theme as theme

        for mode in ("light", "dark"):
            css = theme.build_css(mode)
            self.assertIn("0.1.2 CSS 兜底", css, f"{mode} 缺兜底层")
            self.assertNotIn("if False", css, "伪代码不得进入 CSS")
            self.assertIn('button[kind="secondary"]', css)

    def test_light_and_dark_pin_different_values(self):
        import ui.theme as theme

        self.assertIn("#F6F7F9", theme.build_css("light"))
        self.assertIn("#15181D", theme.build_css("dark"))


class RestartMechanismTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="themerestart_"))
        self._env = os.environ.get("TODO_APP_DATA_DIR")
        os.environ["TODO_APP_DATA_DIR"] = str(self.tmp)

    def tearDown(self):
        if self._env is None:
            os.environ.pop("TODO_APP_DATA_DIR", None)
        else:
            os.environ["TODO_APP_DATA_DIR"] = self._env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_flag_roundtrip(self):
        import app_paths

        flag = app_paths.get_restart_flag_path()
        self.assertIn("state", str(flag), "标记必须放在 state/（程序自己的目录）")
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("theme-base-changed", encoding="utf-8")
        self.assertTrue(flag.is_file())
        flag.unlink()
        self.assertFalse(flag.exists())

    def test_runtime_consumes_flag_and_relaunches(self):
        """desktop_runtime._maybe_relaunch_after_exit：有标记 → Popen 自身并删标记。"""
        import app_paths
        import desktop_runtime

        flag = app_paths.get_restart_flag_path()
        flag.parent.mkdir(parents=True, exist_ok=True)
        flag.write_text("theme-base-changed", encoding="utf-8")

        launched = []
        with mock.patch.object(desktop_runtime.subprocess, "Popen",
                               lambda cmd, **kw: launched.append(cmd)):
            desktop_runtime._maybe_relaunch_after_exit()
        self.assertEqual(len(launched), 1, "应拉起一次新进程")
        self.assertFalse(flag.exists(), "标记必须被消费")
        # 无标记时不动作
        with mock.patch.object(desktop_runtime.subprocess, "Popen",
                               lambda cmd, **kw: launched.append(cmd)):
            desktop_runtime._maybe_relaunch_after_exit()
        self.assertEqual(len(launched), 1, "无标记不得拉起")


class StartupModeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="thememode_"))
        self._env = os.environ.get("TODO_APP_DATA_DIR")
        os.environ["TODO_APP_DATA_DIR"] = str(self.tmp)
        import ui.theme as theme

        theme._startup_mode = None          # 重置进程内缓存
        self.theme = theme

    def tearDown(self):
        self.theme._startup_mode = None
        if self._env is None:
            os.environ.pop("TODO_APP_DATA_DIR", None)
        else:
            os.environ["TODO_APP_DATA_DIR"] = self._env
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_startup_mode_is_stable_within_process(self):
        self.theme.save_appearance({"mode": "浅色", "density": "舒适", "accent": "coral"})
        first = self.theme._init_startup_mode()
        self.theme.save_appearance({"mode": "深色", "density": "舒适", "accent": "coral"})
        again = self.theme._init_startup_mode()
        self.assertEqual(first, again, "启动模式在进程内不得被后续保存改变")
        self.assertEqual(first, "浅色")


if __name__ == "__main__":
    unittest.main(verbosity=2)
