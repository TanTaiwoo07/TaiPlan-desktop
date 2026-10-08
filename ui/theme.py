"""主题系统：设计 token + Light / Dark / 跟随系统 + 全局 CSS。

- 颜色 / 圆角 / 阴影 / 字号 / 间距 集中在这里，app.py 不再散落 CSS
- CSS 只在 main() 顶部注入一次，不随每个任务卡重复注入
- 定制只使用稳定钩子：CSS 变量、[data-testid=...]、.st-key-<key>
  （不依赖 Streamlit 自动生成的 hash class）
"""

import json

import streamlit as st

import app_paths
import config_store

PROJECT_DIR = app_paths.get_project_root()
CONFIG_PATH = app_paths.get_config_path("appearance.json")

MODE_SYSTEM = "跟随系统"
MODE_LIGHT = "浅色"
MODE_DARK = "深色"
MODE_OPTIONS = [MODE_SYSTEM, MODE_LIGHT, MODE_DARK]

DENSITY_COMFORT = "舒适"
DENSITY_COMPACT = "紧凑"
DENSITY_OPTIONS = [DENSITY_COMFORT, DENSITY_COMPACT]

# 显示标签（取值不变）：persisted value != display label
MODE_LABEL_KEYS = {
    MODE_SYSTEM: "settings.appearance.system",
    MODE_LIGHT: "settings.appearance.light",
    MODE_DARK: "settings.appearance.dark",
}
DENSITY_LABEL_KEYS = {
    DENSITY_COMFORT: "settings.appearance.comfortable",
    DENSITY_COMPACT: "settings.appearance.compact",
}


def mode_label(value):
    """外观模式：把持久化取值映射为当前语言的显示标签。"""
    from i18n import t

    return t(MODE_LABEL_KEYS.get(value, "settings.appearance.system"))


def density_label(value):
    """密度：把持久化取值映射为当前语言的显示标签。"""
    from i18n import t

    return t(DENSITY_LABEL_KEYS.get(value, "settings.appearance.comfortable"))

DEFAULTS = {
    "mode": MODE_SYSTEM,
    "density": DENSITY_COMFORT,
    "accent": "coral",
}

LIGHT = {
    "bg_primary": "#F6F7F9",
    "bg_secondary": "#EFF1F4",
    "surface": "#FFFFFF",
    "surface_hover": "#F2F4F7",
    "text_primary": "#1F2328",
    "text_secondary": "#57606A",
    "text_tertiary": "#8B949E",
    "border_subtle": "#E4E7EB",
    "accent": "#D9503F",
    "accent_hover": "#C2432F",
    "accent_soft": "#FBEAE6",
    "accent_text": "#FFFFFF",
    "danger": "#D1242F",
    "danger_soft": "#FDECEC",
    "warning": "#9A6700",
    "warning_soft": "#FFF7E0",
    "success": "#1A7F37",
    "success_soft": "#E8F5EC",
    "urgent": "#D1242F",
    "radius_sm": "6px",
    "radius_md": "10px",
    "radius_lg": "14px",
    "radius_xl": "18px",
    "shadow_sm": "0 1px 2px rgba(16,24,40,.05), 0 1px 3px rgba(16,24,40,.05)",
    "shadow_md": "0 6px 18px rgba(16,24,40,.08)",
    "done_text": "#8B949E",
}

DARK = {
    "bg_primary": "#15181D",
    "bg_secondary": "#1A1E24",
    "surface": "#1F242C",
    "surface_hover": "#262C35",
    "text_primary": "#E6E9EE",
    "text_secondary": "#A7B0BD",
    "text_tertiary": "#7C8797",
    "border_subtle": "#2B323C",
    "accent": "#EE7563",
    "accent_hover": "#F38A7A",
    "accent_soft": "#3A2622",
    "accent_text": "#1A1E24",
    "danger": "#F85149",
    "danger_soft": "#3A2124",
    "warning": "#D29922",
    "warning_soft": "#372D17",
    "success": "#3FB950",
    "success_soft": "#1B2E22",
    "urgent": "#F85149",
    "radius_sm": "6px",
    "radius_md": "10px",
    "radius_lg": "14px",
    "radius_xl": "18px",
    "shadow_sm": "0 1px 2px rgba(0,0,0,.35)",
    "shadow_md": "0 8px 24px rgba(0,0,0,.45)",
    "done_text": "#7C8797",
}

FONT_STACK = ('"Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", '
              '-apple-system, BlinkMacSystemFont, sans-serif')


# ---------------------------------------------------------------
# 外观配置（持久化）
# ---------------------------------------------------------------

_startup_mode = None       # 本次启动生效的 mode（浅色/深色/跟随系统）；进程内不变


def _init_startup_mode():
    """记录本次启动时的 mode；保存外观时用它判断"主题基座是否变了"。"""
    global _startup_mode
    if _startup_mode is None:
        try:
            _startup_mode = load_appearance().get("mode", MODE_SYSTEM)
        except Exception:  # noqa: BLE001
            _startup_mode = MODE_SYSTEM
    return _startup_mode


def load_appearance():
    return config_store.read_json_config(CONFIG_PATH, defaults=DEFAULTS)


def save_appearance(cfg):
    data = {k: cfg.get(k, DEFAULTS[k]) for k in DEFAULTS}
    config_store.write_json_config_atomic(CONFIG_PATH, data)
    return data


def streamlit_theme_options(cfg=None) -> dict:
    """给 Streamlit server 的 theme.* 配置（决定 widget / iframe 的主题基座）。

    用户显式选择浅色/深色时照传；"跟随系统"时不传 base，让 Streamlit 继续
    按系统偏好决定（保持默认行为，同时避免 server 端追不上系统切换的时差）。
    返回 {} 表示没有需要覆写的配置。
    """
    cfg = cfg or load_appearance()
    mode = cfg.get("mode", MODE_SYSTEM)
    resolved = resolve_mode(cfg)
    options: dict[str, str] = {}
    if mode in (MODE_LIGHT, MODE_DARK):
        options["theme.base"] = resolved
    accent = cfg.get("accent", "coral")
    if accent == "coral":
        options["theme.primaryColor"] = "#D9503F" if resolved == "light" else "#F38A7A"
    if resolved == "dark":
        options["theme.backgroundColor"] = DARK["bg_primary"]
        options["theme.secondaryBackgroundColor"] = DARK["surface"]
        options["theme.textColor"] = DARK["text_primary"]
    else:
        options["theme.backgroundColor"] = LIGHT["bg_primary"]
        options["theme.secondaryBackgroundColor"] = LIGHT["surface"]
        options["theme.textColor"] = LIGHT["text_primary"]
    return options


def system_is_dark():
    """读取 Streamlit 的宿主主题（跟随系统 / 浏览器偏好）。"""
    try:
        theme = st.context.theme
        value = theme.get("type") if isinstance(theme, dict) else getattr(theme, "type", None)
        return str(value).lower() == "dark"
    except Exception:
        return False


def resolve_mode(cfg=None):
    """把外观设置解析成实际使用的 'light' / 'dark'。"""
    cfg = cfg or load_appearance()
    mode = cfg.get("mode", MODE_SYSTEM)
    if mode == MODE_DARK:
        return "dark"
    if mode == MODE_LIGHT:
        return "light"
    return "dark" if system_is_dark() else "light"


def tokens(mode):
    return DARK if mode == "dark" else LIGHT


# ---------------------------------------------------------------
# CSS
# ---------------------------------------------------------------

def _rail_common_css():
    """两个状态都要：藏掉 Streamlit 原生折叠/展开控件，只留我们自己的一个控制。"""
    return """
/* ==== 只保留一个 sidebar control：隐藏 Streamlit 原生控件 ==== */
[data-testid="stSidebarCollapseButton"],
[data-testid="stExpandSidebarButton"],
[data-testid="stSidebarResizeHandle"] { display: none !important; }
[data-testid="stSidebarHeader"] { height: 0 !important; min-height: 0 !important; padding: 0 !important; }
"""


def _sidebar_labels_css(rail):
    """rail 时文字/pin 控制收起的规则（pinned 时不需要）。"""
    if not rail:
        return ""
    return """
[data-testid="stSidebar"]:not(:hover) .app-logo-text,
[data-testid="stSidebar"]:not(:hover) .nav-caption,
[data-testid="stSidebar"]:not(:hover) .st-key-appnav [data-testid="stButton"] button p,
[data-testid="stSidebar"]:not(:hover) .st-key-settingsnav [data-testid="stButton"] button p {
    opacity: 0 !important;
    max-width: 0 !important;
    overflow: hidden !important;
}
[data-testid="stSidebar"] .app-logo-text,
[data-testid="stSidebar"] .nav-caption,
[data-testid="stSidebar"] .st-key-appnav [data-testid="stButton"] button p,
[data-testid="stSidebar"] .st-key-settingsnav [data-testid="stButton"] button p {
    transition: opacity .18s ease;
}
[data-testid="stSidebar"]:not(:hover) .st-key-appnav [data-testid="stButton"] button,
[data-testid="stSidebar"]:not(:hover) .st-key-settingsnav [data-testid="stButton"] button {
    justify-content: center;
    padding-left: 0;
    padding-right: 0;
}
[data-testid="stSidebar"]:not(:hover) .st-key-sidebarhead [data-testid="stButton"] {
    opacity: 0;
    pointer-events: none;
}
[data-testid="stSidebar"]:not(:hover) .app-logo {
    justify-content: center;
    padding-right: 0;
    margin-left: 0;
    gap: 0;              /* 隐藏的文字仍会占 8px gap，去掉它 logo 才会真正居中 */
}
/* rail 下未 hover 时按钮不可见，别让它拦截指针 */
[data-testid="stSidebar"]:not(:hover) .st-key-sidebarhead { pointer-events: none; }
[data-testid="stSidebar"]:hover .st-key-sidebarhead { pointer-events: auto; }
/* 没有 hover 能力的环境：pin 控制必须可点，否则会卡在 rail */
@media (hover: none) {
    [data-testid="stSidebar"]:not(:hover) .st-key-sidebarhead [data-testid="stButton"] {
        opacity: 1;
        pointer-events: auto;
    }
}
"""


def _sidebar_state_css(pinned):
    """PINNED_EXPANDED 与 RAIL 两种布局。"""
    if pinned:
        return """
[data-testid="stSidebar"] {
    position: relative;
    width: 240px !important;
    min-width: 240px !important;
    max-width: 240px !important;
    overflow-x: hidden !important;
}
"""
    return """
[data-testid="stSidebar"] {
    position: fixed !important;
    top: 0;
    left: 0;
    height: 100vh;
    width: 72px !important;
    min-width: 72px !important;
    max-width: 72px !important;
    /* 必须高于 Streamlit header 的 999990：它是一条全宽 60px 高的层，
       用 1000 会让侧栏顶部条带被盖住 → hover 不触发、pin 按钮点不到 */
        z-index: 999999;
    overflow-x: hidden !important;
    box-shadow: none;
    transition: width .18s ease, box-shadow .18s ease;
}
/* hover：只做宽度动画，主内容完全不动（overlay 在 Main Content 之上） */
[data-testid="stSidebar"]:hover {
    width: 244px !important;
    min-width: 244px !important;
    max-width: 244px !important;
    box-shadow: var(--shadow-md);
}
/* 给 rail 预留位置；hover 不改变这个值 */
[data-testid="stMain"] { margin-left: 72px !important; }
"""


def _sidebar_narrow_css():
    """900~1050px：无论是否 pinned，都退化成 rail（仍然支持 hover 展开）。"""
    return """
@media (max-width: 1050px) {
    [data-testid="stSidebar"] {
        position: fixed !important;
        top: 0;
        left: 0;
        height: 100vh;
        width: 72px !important;
        min-width: 72px !important;
        max-width: 72px !important;
        /* 必须高于 Streamlit header 的 999990：它是一条全宽 60px 高的层，
       用 1000 会让侧栏顶部条带被盖住 → hover 不触发、pin 按钮点不到 */
        z-index: 999999;
        box-shadow: none !important;
        overflow-x: hidden !important;
        transition: width .18s ease, box-shadow .18s ease;
    }
    [data-testid="stSidebar"]:hover {
        width: 244px !important;
        min-width: 244px !important;
        max-width: 244px !important;
        box-shadow: var(--shadow-md);
    }
    [data-testid="stMain"] { margin-left: 72px !important; }
}
"""


def _streamlit_widget_override_css(mode):
    """按 resolved mode 覆写 Streamlit 内置 widget 的具体色值（CSS 兜底层）。

    场景：Streamlit 的主题基座在页面加载时按**系统**主题生成；用户随后在应用内
    切换主题时，基座不变。下面的规则把高频部件（radio/checkbox/按钮/输入框/
    expander/下载按钮/提示气泡）的关键前景/背景色显式钉住，保证
    "系统深色 + 应用浅色"（或反向）在**不重启**的当场也不会出现黑底黑字。
    完全正确的切换由 server 端 theme.base + 重启保证（见 streamlit_runner）。
    """
    t = tokens(mode)
    bg = t["bg_primary"]
    surface = t["surface"]
    hover = t["surface_hover"]
    text = t["text_primary"]
    text2 = t["text_secondary"]
    border = t["border_subtle"]
    return f"""
/* ==== 0.1.2 CSS 兜底：按应用主题钉住内置 widget 色值 ==== */
.stApp {{
    --primary-color: var(--accent) !important;
    --text-color: {text} !important;
    --background-color: {bg} !important;
    --secondary-background-color: {surface} !important;
}}
/* radio / checkbox 文字（"隐形文字"的主因） */
.stApp label, .stApp p, .stApp span {{
    color: inherit;
}}
.stApp [data-testid="stRadio"] label,
.stApp [data-testid="stCheckbox"] label,
.stApp [data-testid="stToggle"] label {{
    color: {text} !important;
}}
.stApp [data-testid="stRadio"] label p,
.stApp [data-testid="stCheckbox"] label p {{
    color: {text} !important;
}}
/* 原生按钮：避免 dark 基座下的黑底黑字 */
.stApp button[kind="secondary"] {{
    background: {surface} !important;
    border-color: {border} !important;
    color: {text} !important;
}}
.stApp button[kind="secondary"]:hover {{
    background: {hover} !important;
}}
.stApp button[kind="primary"] {{
    background: var(--accent) !important;
    color: var(--accent-text) !important;
}}
/* 输入框 / 下拉 / 文本域 */
.stApp [data-testid="stTextInput"] input,
.stApp [data-testid="stTextArea"] textarea,
.stApp [data-testid="stSelectbox"] > div,
.stApp [data-testid="stNumberInput"] input {{
    background: {surface} !important;
    color: {text} !important;
    border-color: {border} !important;
}}
.stApp [data-testid="stSelectbox"] svg {{
    fill: {text2} !important;
}}
/* expander 与分隔线 */
.stApp [data-testid="stExpander"] details {{
    background: {surface} !important;
    border-color: {border} !important;
}}
.stApp hr {{
    border-color: {border} !important;
}}
/* Markdown 正文 / caption */
.stApp [data-testid="stMarkdownContainer"] {{
    color: {text};
}}
/* 下载/上传按钮区域 */
.stApp [data-testid="stDownloadButton"] button,
.stApp [data-testid="stFileUploader"] {{
    color: {text};
}}
"""
def build_css(mode, density=DENSITY_COMFORT, sidebar_pinned=True):
    t = tokens(mode)
    compact = density == DENSITY_COMPACT
    sidebar_css = _sidebar_state_css(sidebar_pinned)
    rail_extra = _sidebar_labels_css(not sidebar_pinned)
    common_css = _rail_common_css()
    narrow_css = _sidebar_narrow_css()
    widget_override = _streamlit_widget_override_css(mode)
    row_pad = "6px 10px" if compact else "10px 12px"
    card_pad = "10px 12px" if compact else "14px 16px"
    gap = "6px" if compact else "10px"

    vars_css = "\n".join(f"    --{k.replace('_', '-')}: {v};" for k, v in t.items())

    return f"""
<style>
:root {{
{vars_css}
    --font-ui: {FONT_STACK};
    --fs-page: 22px;
    --fs-section: 15px;
    --fs-task: 14.5px;
    --fs-body: 14px;
    --fs-meta: 12.5px;
    --fs-caption: 12px;
    --row-pad: {row_pad};
    --card-pad: {card_pad};
    --stack-gap: {gap};
}}

/* ---- 让 Streamlit 自身也跟随 token ---- */
:root, .stApp {{
    --background-color: var(--bg-primary) !important;
    --secondary-background-color: var(--surface) !important;
    --text-color: var(--text-primary) !important;
    --primary-color: var(--accent) !important;
    --font: var(--font-ui) !important;
}}
html, body, .stApp, [data-testid="stAppViewContainer"] {{
    background: var(--bg-primary) !important;
    color: var(--text-primary);
    font-family: var(--font-ui);
}}
[data-testid="stHeader"] {{ background: transparent !important; }}
[data-testid="stToolbar"] {{ right: 8px; }}
[data-testid="stDecoration"] {{ display: none; }}

/* 主内容宽度上限（Todo 列表 1000-1200px；日历更宽） */
[data-testid="stMainBlockContainer"] {{
    max-width: 1180px;
    margin: 0 auto;
    padding-top: 1.4rem;
    padding-bottom: 3rem;
}}
.st-key-widearea [data-testid="stMainBlockContainer"],
.st-key-widearea + div [data-testid="stMainBlockContainer"] {{ max-width: 1560px; }}

/* ---- Sidebar ---- */
[data-testid="stSidebar"] {{
    background: var(--surface) !important;
    border-right: 1px solid var(--border-subtle);
    
}}
[data-testid="stSidebar"] > div:first-child {{ padding-top: 1.1rem; }}
[data-testid="stSidebar"] hr {{ margin: 10px 0; border-color: var(--border-subtle); }}

.st-key-appnav [role="radiogroup"] {{ gap: 2px; }}
.st-key-appnav [role="radiogroup"] > label {{
    display: flex; align-items: center;
    padding: 8px 10px;
    border-radius: var(--radius-md);
    color: var(--text-secondary);
    font-size: var(--fs-body);
    transition: background .12s ease, color .12s ease;
    cursor: pointer;
}}
.st-key-appnav [role="radiogroup"] > label:hover {{ background: var(--surface-hover); }}
.st-key-appnav [role="radiogroup"] > label:has(input:checked) {{
    background: var(--accent-soft);
    color: var(--accent);
    font-weight: 600;
}}
.st-key-appnav [role="radiogroup"] input[type="radio"] {{ display: none; }}
.st-key-appnav [role="radiogroup"] > label > div:first-child {{ display: none; }}

/* ---- 页面头部 ---- */
.st-key-pagehead h1, .page-title {{
    font-size: var(--fs-page) !important;
    font-weight: 650;
    letter-spacing: -.01em;
    margin: 0 0 2px 0;
    color: var(--text-primary);
}}
.page-sub {{ color: var(--text-secondary); font-size: var(--fs-meta); margin: 0 0 14px 0; }}
.st-key-pagesection {{ margin-top: 10px; }}
.section-title {{
    font-size: var(--fs-section); font-weight: 620;
    color: var(--text-primary); margin: 6px 0 8px 0;
}}
.section-count {{ color: var(--text-tertiary); font-weight: 500; font-size: var(--fs-meta); }}

/* ---- 任务卡（只在 .st-key-tasklist 内生效） ---- */
.st-key-tasklist [data-testid="stVerticalBlockBorderWrapper"] {{
    background: var(--surface);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-lg);
    box-shadow: var(--shadow-sm);
    padding: var(--card-pad);
    margin-bottom: 8px;
    transition: box-shadow .12s ease, border-color .12s ease;
}}
.st-key-tasklist [data-testid="stVerticalBlockBorderWrapper"]:hover {{
    box-shadow: var(--shadow-md);
    border-color: var(--text-tertiary);
}}
.st-key-tasklist [data-testid="stVerticalBlock"] {{ gap: var(--stack-gap); }}

/* 任务卡内的文本 */
.task-title {{ font-size: var(--fs-task); font-weight: 560; color: var(--text-primary); }}
.task-title.done {{ color: var(--done-text); text-decoration: line-through; }}
.task-desc {{ font-size: var(--fs-meta); color: var(--text-secondary); margin-top: 2px; }}
.task-meta {{ font-size: var(--fs-meta); color: var(--text-secondary); margin-top: 4px; }}
.task-meta .sep {{ color: var(--text-tertiary); margin: 0 6px; }}
.chip {{
    display: inline-block;
    padding: 1px 7px;
    border-radius: 999px;
    font-size: var(--fs-caption);
    background: var(--bg-secondary);
    color: var(--text-secondary);
    margin-right: 6px;
}}
.chip.urgent {{ background: var(--danger-soft); color: var(--urgent); font-weight: 600; }}
.chip.low {{ background: var(--bg-secondary); color: var(--text-tertiary); }}
.chip.overdue {{ background: var(--warning-soft); color: var(--warning); }}
.chip.conflict {{ background: var(--warning-soft); color: var(--warning); }}
.chip.done {{ background: var(--success-soft); color: var(--success); }}
.card-accent {{ border-left: 3px solid var(--urgent); margin: -2px 0 0 -1px; }}

/* ---- 圆形完成按钮（仅任务卡内） ---- */
.st-key-tasklist [data-testid="stCheckbox"] {{ margin-top: 2px; }}
.st-key-tasklist [data-testid="stCheckbox"] > label > div:first-of-type {{
    border-radius: 50% !important;
    width: 18px !important; height: 18px !important;
    border: 1.5px solid var(--text-tertiary) !important;
    background: transparent !important;
}}
.st-key-tasklist [data-testid="stCheckbox"] > label:hover > div:first-of-type {{
    border-color: var(--accent) !important;
}}

/* ---- 细进度条 ---- */
[data-testid="stProgress"] > div > div {{ background: var(--bg-secondary); border-radius: 999px; }}
[data-testid="stProgress"] > div > div > div {{
    background: var(--accent); border-radius: 999px;
}}
[data-testid="stProgress"] div[role="progressbar"] {{ height: 6px; }}

/* ---- 折叠 / 容器 ---- */
[data-testid="stExpander"] details {{
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-md);
    background: var(--surface);
}}
[data-testid="stExpander"] summary {{ font-size: var(--fs-body); color: var(--text-secondary); }}

/* ---- 日历容器 ---- */
.st-key-calwrap [data-testid="stVerticalBlockBorderWrapper"] {{
    background: var(--surface);
    border: 1px solid var(--border-subtle);
    border-radius: var(--radius-xl);
    box-shadow: var(--shadow-sm);
    padding: 12px 14px 6px 14px;
}}
.fc .fc-toolbar-title {{ font-size: 1rem; }}
.fc .fc-timegrid-slot-label-cushion, .fc .fc-col-header-cell-cushion {{
    font-size: var(--fs-caption); color: var(--text-tertiary); font-weight: 500;
}}
.fc .fc-timegrid-slot {{ border-color: var(--border-subtle); }}
.fc .fc-timegrid-slot-lane {{ border-color: var(--border-subtle); }}
.fc .fc-timegrid-slot-minor {{ border-style: dotted; }}
.fc .fc-timegrid-now-indicator-line {{ border-color: var(--accent); }}
.fc .fc-timegrid-now-indicator-arrow {{ border-color: var(--accent); }}
.fc .fc-event {{ border-radius: var(--radius-sm); font-size: var(--fs-caption); padding: 1px 3px; }}
.fc .fc-daygrid-event {{ border-radius: var(--radius-sm); }}
.fc-theme-standard td, .fc-theme-standard th {{ border-color: var(--border-subtle); }}

/* ---- 弹窗 ---- */
[data-testid="stDialog"] > div {{
    border-radius: var(--radius-xl) !important;
    border: 1px solid var(--border-subtle);
    box-shadow: var(--shadow-md);
}}
[data-testid="stDialog"] h2 {{ font-size: 18px !important; font-weight: 640; }}

/* ---- 按钮（限定范围，不触碰全局 button） ---- */
.st-key-primaryaction button {{
    background: var(--accent) !important;
    border-color: var(--accent) !important;
    color: var(--accent-text) !important;
    border-radius: var(--radius-md) !important;
    font-weight: 600;
    padding: .38rem .9rem;
}}
.st-key-primaryaction button:hover {{
    background: var(--accent-hover) !important;
    border-color: var(--accent-hover) !important;
}}
.st-key-dangerzone button {{
    color: var(--danger) !important;
    border-color: var(--danger) !important;
    border-radius: var(--radius-md) !important;
}}
.st-key-menuarea button, .st-key-menuarea [data-testid="stPopover"] button {{
    border-radius: var(--radius-sm) !important;
    color: var(--text-tertiary) !important;
    border-color: transparent !important;
    background: transparent !important;
    padding: 2px 6px;
}}
.st-key-menuarea button:hover {{ background: var(--surface-hover) !important; color: var(--text-primary) !important; }}

/* 任务卡内的⋯菜单（作用域限定在 tasklist，不依赖每张卡的 key） */
.st-key-tasklist [data-testid="stPopover"] > button {{
    border: 1px solid transparent !important;
    background: transparent !important;
    color: var(--text-tertiary) !important;
    border-radius: var(--radius-sm) !important;
    padding: 0 .35rem;
    min-height: 1.7rem;
}}
.st-key-tasklist [data-testid="stPopover"] > button:hover {{
    background: var(--surface-hover) !important;
    color: var(--text-primary) !important;
}}

/* 空状态 / 信息块 */
.empty-state {{
    text-align: center;
    padding: 34px 16px;
    border: 1px dashed var(--border-subtle);
    border-radius: var(--radius-lg);
    background: var(--surface);
}}
.empty-title {{ font-size: var(--fs-section); color: var(--text-primary); margin-bottom: 4px; }}
.empty-hint {{ font-size: var(--fs-meta); color: var(--text-tertiary); }}
.overdue-banner {{
    background: var(--warning-soft);
    border: 1px solid var(--border-subtle);
    color: var(--warning);
    border-radius: var(--radius-md);
    padding: 8px 12px;
    font-size: var(--fs-meta);
    font-weight: 560;
}}
.group-label {{
    font-size: var(--fs-meta); font-weight: 640;
    color: var(--text-secondary);
    margin: 14px 0 6px 0;
}}
.window-status {{ font-size: var(--fs-caption); color: var(--text-tertiary); line-height: 1.7; }}

/* 控件外观微调 */
.stButton button, .stPopover button {{ font-family: var(--font-ui); }}
[data-testid="stSidebar"] .stButton button {{
    justify-content: flex-start;
    border-color: transparent;
    background: transparent;
    color: var(--text-secondary);
}}
[data-testid="stSidebar"] .stButton button:hover {{
    background: var(--surface-hover); color: var(--text-primary);
}}
[data-testid="stTextInput"] input, [data-testid="stTextArea"] textarea {{
    background: var(--surface); color: var(--text-primary);
    border-radius: var(--radius-md);
}}

@media (max-width: 1100px) {{
    [data-testid="stMainBlockContainer"] {{ max-width: 100%; padding-left: 1rem; padding-right: 1rem; }}
}}
/* ================= 13.1 Sidebar 稳定化 ================= */

{common_css}{sidebar_css}{rail_extra}
[data-testid="stSidebar"] > div:first-child {{
    padding: .85rem .6rem 1rem .6rem;
}}
[data-testid="stSidebar"] hr {{ margin: 8px 0; }}

/* 导航项：按钮式（不再有 radio 原生圆点） */
.st-key-appnav [data-testid="stButton"],
.st-key-settingsnav [data-testid="stButton"] {{ margin-bottom: 2px; }}
.st-key-appnav [data-testid="stButton"] button,
.st-key-settingsnav [data-testid="stButton"] button {{
    justify-content: flex-start;
    gap: .55rem;
    padding: .34rem .55rem;
    min-height: 2.1rem;
    border-radius: var(--radius-md) !important;
}}
.st-key-appnav [data-testid="stButton"] button p,
.st-key-settingsnav [data-testid="stButton"] button p {{
    margin: 0;
    font-size: var(--fs-body);
    white-space: nowrap;
    overflow: hidden;
}}
/* 选中项：accent 浅底 + accent 文字（不依赖 :has()） */
.st-key-appnav [data-testid="stButton"] button[kind="primary"],
.st-key-appnav [data-testid="stBaseButton-primary"],
.st-key-settingsnav [data-testid="stButton"] button[kind="primary"],
.st-key-settingsnav [data-testid="stBaseButton-primary"] {{
    background: var(--accent-soft) !important;
    color: var(--accent) !important;
    border-color: transparent !important;
    font-weight: 620;
}}

/* Logo：图标 + 文字，强制一行 */
.app-logo {{
    display: flex;
    align-items: center;
    justify-content: flex-start;
    gap: 8px;
    width: 100%;
    padding-right: 32px;   /* 给右上角的 pin 按钮留位置 */
    box-sizing: border-box;
    white-space: nowrap;
    overflow: hidden;
    font-size: 17px;
    font-weight: 640;
    color: var(--text-primary);
    margin: 1px 0 6px 0;
}}
/* 唯一的 sidebar control：固定在侧栏右上角同一个槽位 */
.st-key-sidebarhead {{
    position: absolute !important;
    top: 6px !important;
    right: 8px !important;
    width: auto !important;
    z-index: 3;
}}
.st-key-sidebarhead [data-testid="stButton"] button {{
    width: 28px !important;
    min-width: 28px !important;
    padding: 0 !important;
    justify-content: center;
}}
.app-logo-icon {{
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 22px;
    height: 22px;
    border-radius: 7px;
    background: var(--accent);
    color: var(--accent-text);
    font-size: 13px;
    font-weight: 700;
    flex: 0 0 auto;
}}
.nav-caption {{
    font-size: var(--fs-caption);
    color: var(--text-tertiary);
    white-space: nowrap;
    overflow: hidden;
    padding: 0 .2rem;
}}

{narrow_css}
{widget_override}{rail_extra}
</style>
"""


def inject_theme(mode, density=DENSITY_COMFORT, sidebar_pinned=True):
    """在 main() 顶部调用一次，注入整站 CSS。"""
    st.markdown(build_css(mode, density, sidebar_pinned), unsafe_allow_html=True)
