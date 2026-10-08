"""布局：Fluent 风格 Navigation Rail 侧栏、设置页骨架、内容容器。

侧栏状态模型只有一个持久状态：``sidebar_pinned: bool``
    True  → PINNED_EXPANDED，240px，占据布局宽度
    False → RAIL，72px 固定定位；鼠标 hover 由 **纯 CSS** 临时展开成 244px overlay

hover 不写 session_state、不 st.rerun、不触发页面导航。
"""

import contextlib
from datetime import date, datetime

import streamlit as st

from i18n import dates, t

from i18n import dates, t

from ui import icons

NAV_KEY = "nav"
SIDEBAR_PINNED_KEY = "sidebar_pinned"
LEGACY_COLLAPSE_KEY = "nav_collapsed"      # 13.1 的旧键，读一次后迁移
SETTINGS_NAV_KEY = "settings_section"      # W3.5：8 个 Section（0.1.1 起含数据管理）

RAIL_WIDTH = 72
RAIL_HOVER_WIDTH = 244
PINNED_WIDTH = 240


def _default_pinned() -> bool:
    return True


def is_pinned() -> bool:
    """侧栏是否固定展开。只保留这一个持久 UI 状态。"""
    if SIDEBAR_PINNED_KEY in st.session_state:
        return bool(st.session_state[SIDEBAR_PINNED_KEY])
    if LEGACY_COLLAPSE_KEY in st.session_state:      # 13.1 → 13.2 兼容迁移
        pinned = not bool(st.session_state[LEGACY_COLLAPSE_KEY])
        st.session_state[SIDEBAR_PINNED_KEY] = pinned
        return pinned
    pinned = _default_pinned()
    st.session_state[SIDEBAR_PINNED_KEY] = pinned
    return pinned


def _toggle_pinned():
    """唯一控制：固定展开 ↔ rail。只改 sidebar_pinned，然后 rerun。"""
    st.session_state[SIDEBAR_PINNED_KEY] = not is_pinned()
    st.rerun()


def _worker_status():
    try:
        import database
        return database.is_worker_alive(max_age_seconds=60)
    except Exception:
        return False


def _nav_button(key, icon, label, current):
    """导航项。标签始终渲染，rail 状态下由 CSS 隐藏（hover 恢复）。

    这里**故意不传 help**：Streamlit 的提示气泡渲染在 body 层的 portal 上，
    鼠标移出应用窗口（尤其 rail 宽度动画期间）时 WebView2 收不到 mouseleave，
    气泡不会自己消失，连着悬停几个导航项就会叠成一片。
    标签本身始终渲染，rail 悬停时也由 CSS 显示，气泡只是多余的一层。
    """
    selected = key == current
    if st.button(label, key=f"nav_item_{key}", icon=icon,
                 type="primary" if selected else "secondary",
                 use_container_width=True):
        st.session_state[NAV_KEY] = key
        st.rerun()


def render_sidebar():
    """渲染侧栏，返回当前页面 key。"""
    pinned = is_pinned()
    current = st.session_state.get(NAV_KEY) or icons.NAV_TODAY
    if current not in icons.NAV_KEYS:
        current = icons.NAV_TODAY
    st.session_state[NAV_KEY] = current

    with st.sidebar:
        # ---- 头部：Logo 独占整行 ----
        # 不能把 Logo 放进 st.columns：rail 下侧栏内容区只有约 32px 宽
        # （72px 减去 Streamlit 两侧各 ~20px 内边距），分成两列后 Logo 那列只剩
        # ~12px，22px 的图标会向左溢出，与导航图标错位。
        # 所以 Logo 直接占满整行，pin 按钮改为绝对定位到侧栏右上角。
        st.markdown(
            f'<div class="app-logo">'
            f'<span class="app-logo-icon">{icons.APP_LOGO_GLYPH}</span>'
            f'<span class="app-logo-text">{icons.APP_TITLE}</span></div>',
            unsafe_allow_html=True,
        )
        # ---- 唯一的控制：侧栏右上角同一个槽位（rail 下 hover 才显示）----
        with st.container(key="sidebarhead"):
            # 同理：pin 按钮也不加提示气泡，否则会留下同样不消失的气泡
            if st.button("", key="sidebar_pin_toggle",
                         icon=icons.PIN if pinned else icons.UNPIN,
                         use_container_width=True):
                _toggle_pinned()
        st.divider()

        # ---- 主导航 ----
        with st.container(key="appnav"):
            for key, icon, label in icons.NAV_MAIN_ENTRIES:
                _nav_button(key, icon, icons.nav_label(key), current)
            st.divider()
            for key, icon, label in icons.NAV_AUX_ENTRIES:
                _nav_button(key, icon, icons.nav_label(key), current)

        st.divider()
        _render_footer()

    return st.session_state[NAV_KEY]


def _render_footer():
    """底部说明：日期/星期一律走 i18n.dates（§9，不依赖系统 locale）。"""
    if st.session_state.get("developer_mode"):
        alive = _worker_status()
        state = t("sidebar.backend_running") if alive else t("sidebar.backend_stopped")
        text = t("sidebar.backend_label", state=state)
    else:
        text = dates.month_day(date.today())
    st.markdown(f'<div class="nav-caption">{text}</div>', unsafe_allow_html=True)


# ---------------------------------------------------------------
# 设置页骨架
# ---------------------------------------------------------------

SETTINGS_TOC_KEY = "settings_toc"          # 稳定 CSS 钩子（不依赖 Streamlit 随机类名）
SETTINGS_SECTION_PREFIX = "settings_section_"
SETTINGS_CSS_KEY = "settings_scroll_css"

_SETTINGS_CSS = """
/* ---- W3.5 Settings：左侧 sticky 目录 + 右侧连续滚动页 ---- */
html { scroll-behavior: smooth; }
.st-key-settings_toc,
.st-key-settings_toc * { box-sizing: border-box; max-width: 100%; }
.st-key-settings_toc {
    position: sticky; top: 1.2rem;
    min-width: 0;
    padding-right: .2rem;
}
.st-key-settings_toc .settings-toc-title {
    font-size: .74rem; letter-spacing: .09em; text-transform: uppercase;
    opacity: .55; margin: 0 0 .5rem .15rem;
}
a.settings-toc-link {
    display: block; text-decoration: none !important;
    color: inherit !important;
    padding: .34rem .6rem; margin: 0 0 .12rem 0;
    border-radius: 8px; font-size: .9rem; line-height: 1.25;
    overflow-wrap: anywhere; word-break: break-word;
    transition: background-color .12s ease;
}
a.settings-toc-link:hover { background: rgba(217, 80, 63, .10); }
.settings-anchor { scroll-margin-top: 1rem; }
/* 滚动到某 Section 时给它一点视觉提示（纯 CSS，无需 JS / scroll-spy） */
[class*="st-key-settings_section_"] {
    box-sizing: border-box; min-width: 0; max-width: 100%;
    overflow-wrap: anywhere;
    scroll-margin-top: 1rem;
}
[class*="st-key-settings_section_"] + [class*="st-key-settings_section_"] {
    margin-top: 1.1rem; padding-top: 1.1rem;
    border-top: 1px solid rgba(128, 128, 128, .18);
}
/* 中等宽度：目录收窄，但仍然两栏 */
@media (max-width: 1099px) {
    a.settings-toc-link { font-size: .84rem; padding: .3rem .45rem; }
    .st-key-settings_toc { top: 1rem; }
}
/* 窄屏 / Windows 分屏：隐藏目录，设置内容保持单一连续滚动页 */
@media (max-width: 759px) {
    .st-key-settings_toc { display: none !important; }
    [data-testid="stColumn"]:has(.st-key-settings_toc),
    [data-testid="column"]:has(.st-key-settings_toc) { display: none !important; }
}
"""


def _inject_settings_css():
    st.markdown(f'<style id="{SETTINGS_CSS_KEY}">{_SETTINGS_CSS}</style>',
                unsafe_allow_html=True)


def settings_scaffold():
    """Settings 页脚手架：左侧 sticky 目录（真 anchor 链接）+ 右侧连续内容容器。

    返回 (toc_column, right_column)。目录项是普通 <a href="#settings-xxx"> 锚点：
    不触发 Streamlit rerun、不动 session_state、不依赖随机类名；
    平滑滚动由 CSS scroll-behavior 提供，窄屏由 CSS 隐藏目录。
    """
    _inject_settings_css()
    left, right = st.columns([1.0, 3.5], gap="large")
    with left:
        with st.container(key=SETTINGS_TOC_KEY):
            st.markdown('<div class="settings-toc-title">{}</div>'.format(
                t("settings.toc_title")), unsafe_allow_html=True)
            for key, _icon, _i18n in icons.SETTINGS_ENTRIES:
                st.markdown(
                    '<a class="settings-toc-link" href="#{}">{}</a>'.format(
                        icons.settings_anchor(key), icons.settings_label(key)),
                    unsafe_allow_html=True)
    return left, right


@contextlib.contextmanager
def settings_section(sec_key):
    """一个设置 Section：先落稳定 anchor，再进带稳定 key 的容器。"""
    st.markdown('<div id="{}" class="settings-anchor"></div>'.format(
        icons.settings_anchor(sec_key)), unsafe_allow_html=True)
    with st.container(key=f"{SETTINGS_SECTION_PREFIX}{sec_key}"):
        yield


# ---------------------------------------------------------------
# 内容容器
# ---------------------------------------------------------------

def page_container(key=None):
    return st.container(key=key) if key else st.container()


def wide_area():
    return st.container(key="widearea")


def calendar_wrap():
    return st.container(key="calwrap")


def task_list():
    return st.container(key="tasklist")


def now_label():
    return datetime.now().strftime("%H:%M")
