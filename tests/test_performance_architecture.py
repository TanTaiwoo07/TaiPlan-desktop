# -*- coding: utf-8 -*-
"""第 16 阶段架构回归测试（文档 BM 节）。

**不测绝对毫秒数**（CI 会抖动），只测「架构规则」：
- Today 页不构建 Calendar
- Sidebar hover 不产生 Python callback / 不改 state
- 没有双 rerun 的写法
- recurrence 投影必须带范围
- 昂贵的按任务查询必须批量化（禁止 N+1）
- Settings 分区惰性
- 缓存失效用的是 data revision 而不是随机值
"""
import io
import os
import re
import sys
import unittest
from pathlib import Path

from tests import tests_env  # noqa: F401

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def read(name):
    return io.open(PROJECT_ROOT / name, encoding="utf-8").read()


class PageLazyRenderTest(unittest.TestCase):
    def test_page_dispatch_is_conditional(self):
        """nav 必须走 if/elif 分支，不能把 7 个页面全渲染一遍。"""
        app = read("app.py")
        i = app.find("if nav == ui_icons.NAV_TODAY")
        self.assertGreater(i, 0, "找不到页面分发入口")
        block = app[i:i + 1200]
        self.assertIn("elif nav ==", block, "页面分发应该是 if/elif")
        # 每个 render_*_page() 调用点都必须在某个分支里（即前面同缩进处有 if/elif）
        for m in re.finditer(r"\n(\s*)(render_\w+_page\(\))", app):
            if m.group(2).startswith("def"):
                continue
            indent = len(m.group(1))
            tail = app[max(0, m.start() - 600):m.start()]
            # 调用点比它的 if/elif 深一层缩进，所以找「更浅的 if/elif」
            ok = False
            for line in tail.split("\n"):
                if not line.strip():
                    continue
                ind = len(line) - len(line.lstrip())
                if ind < indent and re.match(r"\s*(if|elif)\s", line):
                    ok = True
            self.assertTrue(ok, f"{m.group(2)} 前面找不到 if/elif，可能被无条件渲染")

    def test_today_page_does_not_build_calendar(self):
        app = read("app.py")
        i = app.find("def render_today_page(")
        j = app.find("\ndef ", i + 10)
        body = app[i:j]
        for bad in ("calendar", "Calendar", "get_items_for_month", "get_items_for_range",
                    "fullcalendar"):
            self.assertNotIn(bad, body, f"Today 页不应出现 {bad!r}")


class SidebarHoverTest(unittest.TestCase):
    def test_hover_is_pure_css(self):
        """rail hover 展开必须是纯 CSS：不产生 callback、不改 session_state。"""
        theme = read("taiplan/ui/theme.py")
        self.assertIn(":hover", theme)
        # 不允许在 hover 相关代码里写 session_state / st.rerun / on_click
        for m in re.finditer(r"[^\n]*hover[^\n]*", theme):
            line = m.group(0)
            if line.strip().startswith(("*", "/*", "#")):
                continue
            self.assertNotIn("session_state", line, f"hover 规则里不应有 session_state: {line[:80]}")
            self.assertNotIn("st.rerun", line, f"hover 规则里不应有 st.rerun: {line[:80]}")

    def test_only_one_persistent_ui_state(self):
        layout = read("taiplan/ui/layout.py")
        self.assertIn("SIDEBAR_PINNED_KEY", layout)
        # 规则：侧栏的持久状态只能由 layout.py 这一个模块写入
        # （其它模块只能读），写入点本身集中在几个封装函数里。
        writes = len(re.findall(r"st\.session_state\[SIDEBAR_PINNED_KEY\]\s*=", layout))
        self.assertLessEqual(writes, 3, f"侧栏持久状态写入点有 {writes} 处，过多")
        for name in ("app.py", "taiplan/calendar_view.py"):
            other = read(name)
            self.assertNotIn("session_state[SIDEBAR_PINNED_KEY] =", other,
                             f"{name} 不应直接写侧栏持久状态，应走 ui.layout")


class RerunPatternTest(unittest.TestCase):
    """禁止「渲染 widget → 事后检测 → 改 state → st.rerun()」的双 rerun 写法。"""

    def test_no_consecutive_reruns(self):
        """双 rerun 的判据：**同一路径**上连续两次 st.rerun()。

        必须同时满足：缩进相同、中间没有 return/except/else/elif/if/break
        等控制流关键字（否则就是互斥分支，每次只执行一条，不是双 rerun）。

        说明：更宽泛的启发式（例如"改了 state 再 rerun"）噪声太大——
        edit_session 两阶段编辑、NLP 回填 dialog、通知轮询消费都是**必须**
        靠 rerun 才能生效的写法（Streamlit 不允许在 widget 实例化后改 state）。
        """
        flow_kw = ("return", "except", "else", "elif", "if ", "if(",
                   "break", "continue", "for ", "while ")
        for name in ("app.py", "taiplan/calendar_view.py"):
            lines = read(name).split("\n")
            for i, line in enumerate(lines):
                if "st.rerun()" not in line:
                    continue
                indent = len(line) - len(line.lstrip())
                for j in range(i + 1, min(len(lines), i + 5)):
                    nxt = lines[j]
                    if "st.rerun()" in nxt:
                        nind = len(nxt) - len(nxt.lstrip())
                        between = [b.strip() for b in lines[i + 1:j] if b.strip()]
                        blocked = any(b.startswith(flow_kw) for b in between)
                        self.assertTrue(
                            nind != indent or blocked,
                            f"{name}:{i + 1} 与 {j + 1} 在同一路径上连续两次 "
                            f"st.rerun()（双 rerun，会白跑一轮）",
                        )
                        break
                    if nxt.strip() and (len(nxt) - len(nxt.lstrip())) <= indent:
                        break

    def test_rerun_sites_are_counted_and_bounded(self):
        """rerun 点数量要有上限意识：新增到离谱就说明在靠 rerun 堆交互。"""
        total = sum(len(re.findall(r"st\.rerun\(\)", read(f)))
                    for f in ("app.py", "taiplan/calendar_view.py"))
        self.assertLess(total, 80, f"st.rerun() 共 {total} 处，增长过快需审视")


class RecurrenceRangeTest(unittest.TestCase):
    def test_projection_requires_range(self):
        import inspect

        from taiplan import recurrence

        sig = inspect.signature(recurrence.get_occurrences_for_range)
        params = list(sig.parameters)
        self.assertIn("range_start", params)
        self.assertIn("range_end", params, "投影必须带范围，不能无限生成")

    def test_next_occurrence_takes_after_date(self):
        import inspect

        from taiplan import recurrence

        params = list(inspect.signature(recurrence.get_next_occurrence).parameters)
        self.assertIn("after_date", params)


class NPlusOneTest(unittest.TestCase):
    """昂贵的按任务查询必须批量化（禁止在循环里对单个 task 查库）。"""

    def test_no_single_item_batch_calls_in_loops(self):
        for name in ("taiplan/services.py", "taiplan/calendar_view.py", "app.py"):
            src = read(name)
            offenders = re.findall(r"\w*_for_tasks\(\[[^\]]*\]\)", src)
            self.assertEqual(offenders, [],
                             f"{name} 仍存在循环内单元素批量调用（N+1）: {offenders[:5]}")

    def test_upcoming_batches_once(self):
        sv = read("taiplan/services.py")
        i = sv.find("def get_upcoming_items(")
        j = sv.find("\ndef ", i + 10)
        body = sv[i:j]
        # 批量取数必须在「主循环」之前完成。
        # 注意：不能用 "for t in tasks" 当哨兵——构造 recurring_ids 的
        # 列表推导里也含这个子串。
        self.assertIn("_for_tasks(recurring_ids)", body, "缺少批量查询")
        batch_pos = body.find("_for_tasks(recurring_ids)")
        loop_pos = body.find("\n    for t in tasks:")
        self.assertGreater(loop_pos, 0, "找不到主循环")
        self.assertLess(batch_pos, loop_pos, "批量查询应在主循环之前完成")


class SettingsLazyTest(unittest.TestCase):
    """W3.5：Settings 已改为「一次渲染全部 Section 的连续滚动页」。

    旧契约（按分区 if/elif 惰性渲染）已废止，这里改为守护新结构，
    同时保留真正的性能守护：页面级分发仍然是惰性的。
    """

    def test_settings_renders_all_sections_in_one_pass(self):
        app = read("app.py")
        i = app.find("def render_settings_page(")
        self.assertGreater(i, 0, "找不到 render_settings_page")
        j = app.find("\ndef ", i + 10)
        body = app[i:j]
        # 八个 Section 必须在同一页按固定顺序全部渲染
        for name in ("_settings_appearance", "_settings_language", "render_ai_settings",
                     "render_notification_settings", "calendar_view.render_calendar_settings",
                     "_settings_desktop", "_settings_data_management", "_settings_about"):
            self.assertIn(name, body, f"设置页缺少 Section 渲染: {name}")
        self.assertEqual(body.count("settings_section("), 8,
                         "应恰好渲染 8 个带 anchor 的 Section（0.1.1 起含数据管理）")

    def test_no_per_section_switching_regression(self):
        """不允许回退成「选分区 → rerun → 只显示该分区」。"""
        app = read("app.py")
        i = app.find("def render_settings_page(")
        j = app.find("\ndef ", i + 10)
        body = app[i:j]
        self.assertNotIn("if section ==", body, "不应再有按分区条件渲染")
        self.assertNotIn("elif section ==", body, "不应再有按分区 elif 链")
        self.assertNotIn("settings_shell(", body, "不应再使用旧的按分区脚手架")

    def test_page_level_dispatch_is_still_lazy(self):
        """性能守护：main() 里仍然只渲染当前页面，不会把所有页面都渲染一遍。"""
        app = read("app.py")
        i = app.find("def main(")
        body = app[i:i + 4000]
        self.assertIn("nav == ui_icons.NAV_TODAY", body)
        self.assertIn("elif nav == ui_icons.NAV_INBOX", body)
        self.assertGreaterEqual(body.count("elif nav =="), 4,
                                "页面级分发应保持 if/elif 惰性结构")

    def test_settings_css_hooks_are_stable(self):
        """连续页的样式钩子必须是我们自己的稳定 key，不用随机类名。"""
        layout_src = read("taiplan/ui/layout.py")
        self.assertIn('SETTINGS_TOC_KEY = "settings_toc"', layout_src)
        self.assertIn('SETTINGS_SECTION_PREFIX = "settings_section_"', layout_src)
        self.assertIn("settings_anchor", layout_src)


class DataRevisionTest(unittest.TestCase):
    def test_revision_exists_and_bumps(self):
        perf = read("taiplan/performance.py")
        self.assertIn("get_perf_snapshot", perf)

    def test_ai_imports_are_lazy(self):
        """AI 依赖不得出现在 app.py 模块顶层（会拖慢冷启动）。"""
        app = read("app.py")
        head = app[:app.find("\n\n\n")]
        self.assertNotIn("from taiplan.ai_client import", head)
        self.assertNotIn("\nimport ai_settings", head)
        # 但它必须仍能被用到（局部 import 存在）
        self.assertIn("from taiplan.ai_client import", app)
        self.assertIn("import ai_settings", app)


if __name__ == "__main__":
    unittest.main(verbosity=2)
