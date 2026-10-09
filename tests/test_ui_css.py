# -*- coding: utf-8 -*-
"""CSS 生成质量回归测试。

背景：ui/theme.py 里同时存在 f-string（需要把 CSS 花括号写成 {{ }}）和
普通字符串（必须写单花括号）两种模板。13.2 的一个补丁在普通字符串里写了
双花括号，导致浏览器收到 `{{ ... }}` 后**整条规则的声明块被丢弃**——
规则看着存在、其实完全没生效（Logo 居中等三条规则就这样静默失效）。
本测试锁死这类问题。
"""
import os
import re
import sys
import tempfile
import unittest

os.environ.setdefault("TODO_APP_DATA_DIR", tempfile.mkdtemp(prefix="testcss_"))
os.environ.setdefault("TODO_APP_LEGACY_DIR", tempfile.mkdtemp(prefix="testcssl_"))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from taiplan.ui import theme as theme  # noqa: E402

STATES = [(True, "dark"), (True, "light"), (False, "dark"), (False, "light")]


def css_body(pinned, mode):
    css = theme.build_css(mode, sidebar_pinned=pinned)
    assert "<style>" in css and "</style>" in css, "build_css 未返回 <style> 包裹的 CSS"
    return css.split("<style>", 1)[1].split("</style>", 1)[0]


class TestGeneratedCss(unittest.TestCase):
    def test_no_escaped_brace_leak(self):
        """生成的 CSS 里不允许出现 {{ 或 }}——它们会让浏览器丢弃整条规则。"""
        for pinned, mode in STATES:
            body = css_body(pinned, mode)
            self.assertEqual(
                body.count("{{") + body.count("}}"), 0,
                f"pinned={pinned} {mode} 存在泄漏的双花括号",
            )

    def test_no_rule_with_empty_declaration_block(self):
        """不允许出现空声明块的规则（说明该规则被解析器丢掉了）。"""
        for pinned, mode in STATES:
            body = css_body(pinned, mode)
            for m in re.finditer(r"([^{}@]+)\{\s*\}", body):
                sel = " ".join(m.group(1).split())
                self.fail(f"pinned={pinned} {mode} 空声明块规则: {sel[:120]}")

    def test_braces_are_balanced(self):
        for pinned, mode in STATES:
            body = css_body(pinned, mode)
            self.assertEqual(body.count("{"), body.count("}"),
                             f"pinned={pinned} {mode} 花括号不平衡")

    def test_rail_rules_present(self):
        """rail 态关键规则必须在生成的 CSS 里（且带声明）。"""
        body = css_body(False, "dark")
        for needle in (
            'position: fixed',
            'width: 72px',
            'width: 244px',
            '.app-logo',
            'justify-content: center',
            'pointer-events: none',
        ):
            self.assertIn(needle, body, f"rail CSS 缺少: {needle}")

    def test_rail_not_hover_rule_has_declarations(self):
        """定位到那条曾失效的规则，断言它有真正的声明。"""
        body = css_body(False, "dark")
        m = re.search(r'\[data-testid="stSidebar"\]:not\(:hover\)\s*\.app-logo\s*\{([^{}]*)\}', body)
        self.assertIsNotNone(m, "未找到 rail 未 hover 的 .app-logo 规则")
        decl = m.group(1)
        self.assertIn("justify-content: center", decl)
        self.assertIn("gap: 0", decl)

    def test_rail_zindex_above_streamlit_header(self):
        """rail 的 z-index 必须高于 Streamlit header（实测其 z-index=999990）。

        header 是一条全宽（0..1270）、高 60px、pointer-events:auto 的层。
        13.2 里 rail 用了 z-index:1000，结果侧栏顶部 60px 被 header 盖住：
        hover 不触发、绝对定位在 top:6px 的 pin 按钮点不到 —— 表现为
        「收起后无法恢复」。测到 999991 以上才算安全（Streamlit 弹窗层是 9999999）。
        """
        for pinned, mode in [(False, "dark"), (False, "light")]:
            body = css_body(pinned, mode)
            zs = [int(m.group(1)) for m in re.finditer(r"z-index:\s*(\d+)", body)]
            self.assertTrue(zs, f"pinned={pinned} {mode} 的 CSS 里没有 z-index")
            self.assertGreater(max(zs), 999990,
                               f"rail z-index 最大只有 {max(zs)}，会被 z-index=999990 "
                               f"的 Streamlit header 覆盖")

    def test_rail_hover_reveal_rules_have_declarations(self):
        """hover 展开规则必须有真实声明（曾经被双花括号泄漏吃掉）。"""
        body = css_body(False, "dark")
        m = re.search(r'\[data-testid="stSidebar"\]:hover\s*\{([^{}]*)\}', body)
        self.assertIsNotNone(m, "未找到 rail 的 :hover 规则")
        self.assertIn("244px", m.group(1))


if __name__ == "__main__":
    unittest.main(verbosity=2)
