"""统一图标与导航定义。

两条硬规则（13.1 真机回归后定的）：

1. Streamlit Material 图标只在 **markdown 文本** 或 **原生组件的 icon= 参数** 里使用，
   格式必须是 ``:material/icon_name:``。
   绝不能把 ``:material/xxx:`` 放进 ``unsafe_allow_html=True`` 的原始 HTML —— 
   HTML 块不走 markdown 解析，图标会原样显示成文本。

2. 导航用「button + icon=」，不用 st.radio。
   radio 的原生圆点是 BaseWeb 内部 DOM，靠 CSS 隐藏很脆弱，
   这也是真机上圆点重新出现的原因。
"""

from taiplan.product_info import APP_DISPLAY_NAME  # noqa: F401

APP_LOGO_GLYPH = "✓"          # 纯标题区域用 Unicode，不用 Material 图标
APP_TITLE = APP_DISPLAY_NAME   # 显示层品牌；身份（exe/AUMID）留 W4

# ---------------------------------------------------------------
# 主导航
# ---------------------------------------------------------------

NAV_TODAY = "today"
NAV_INBOX = "inbox"
NAV_CALENDAR = "calendar"
NAV_UPCOMING = "upcoming"
NAV_COMPLETED = "completed"
NAV_REMINDER = "reminder"
NAV_SETTINGS = "settings"

# (key, material icon, i18n key)  —— 标签在渲染时用 t() 解析（Stage 17.3 §7）
NAV_MAIN_ENTRIES = [
    (NAV_TODAY, ":material/today:", "nav.today"),
    (NAV_INBOX, ":material/inbox:", "nav.inbox"),
    (NAV_CALENDAR, ":material/calendar_month:", "nav.calendar"),
    (NAV_UPCOMING, ":material/event_upcoming:", "nav.upcoming"),
    (NAV_COMPLETED, ":material/task_alt:", "nav.completed"),
]
NAV_AUX_ENTRIES = [
    (NAV_REMINDER, ":material/notifications:", "nav.reminder"),
    (NAV_SETTINGS, ":material/settings:", "nav.settings"),
]
NAV_ENTRIES = NAV_MAIN_ENTRIES + NAV_AUX_ENTRIES
NAV_KEYS = [key for key, _, _ in NAV_ENTRIES]
NAV_LABELS = {key: key_i18n for key, _, key_i18n in NAV_ENTRIES}  # 值是 i18n key
NAV_ICONS = {key: icon for key, icon, _ in NAV_ENTRIES}

# 兼容旧引用
NAV_ITEMS = NAV_KEYS

# ---------------------------------------------------------------
# 设置二级导航
# ---------------------------------------------------------------

SET_APPEARANCE = "appearance"
SET_AI = "ai"
SET_REMINDER = "reminder"
SET_CALENDAR = "calendar"
SET_DESKTOP = "desktop"
SET_DATA = "data"
SET_ABOUT = "about"
SET_LANGUAGE = "language"

# W3.5：Settings 连续页的 Section 顺序（外观 → 语言 → AI → 提醒 → 日历 → 桌面 → 关于）
SETTINGS_ENTRIES = [
    (SET_APPEARANCE, ":material/palette:", "settings.appearance"),
    (SET_LANGUAGE, ":material/translate:", "settings.language"),
    (SET_AI, ":material/auto_awesome:", "settings.ai"),
    (SET_REMINDER, ":material/notifications_active:", "settings.reminder"),
    (SET_CALENDAR, ":material/calendar_today:", "settings.calendar"),
    (SET_DESKTOP, ":material/desktop_windows:", "settings.desktop"),
    (SET_DATA, ":material/event_available:", "settings.data"),
    (SET_ABOUT, ":material/info:", "settings.about"),
]

# 稳定 anchor（DOM id）：settings-appearance / -language / -ai / -reminders / ...
SETTINGS_ANCHORS = {
    SET_APPEARANCE: "settings-appearance",
    SET_LANGUAGE: "settings-language",
    SET_AI: "settings-ai",
    SET_REMINDER: "settings-reminders",
    SET_CALENDAR: "settings-calendar",
    SET_DESKTOP: "settings-desktop",
    SET_DATA: "settings-data",
    SET_ABOUT: "settings-about",
}


def settings_anchor(sec_key) -> str:
    """Section 的稳定 DOM id（供 #hash 跳转使用，不依赖 Streamlit 类名）。"""
    return SETTINGS_ANCHORS.get(sec_key, f"settings-{sec_key}")
SETTINGS_KEYS = [key for key, _, _ in SETTINGS_ENTRIES]
SETTINGS_LABELS = {key: key_i18n for key, _, key_i18n in SETTINGS_ENTRIES}
SETTINGS_ICONS = {key: icon for key, icon, _ in SETTINGS_ENTRIES}

SETTINGS_ITEMS = SETTINGS_KEYS

# ---------------------------------------------------------------
# 其它图标（只用于原生组件的 icon= 参数）
# ---------------------------------------------------------------

TIME = ":material/schedule:"
DATE = ":material/calendar_today:"
RECUR = ":material/autorenew:"
URGENT = ":material/priority_high:"
DONE = ":material/check_circle:"
LINK = ":material/link:"
CONFLICT = ":material/warning:"
OVERDUE = ":material/history:"
ADD = ":material/add:"
MORE = ":material/more_horiz:"
EDIT = ":material/edit:"
DELETE = ":material/delete:"
DURATION = ":material/timelapse:"
BACKEND = ":material/dns:"
NOTIFY = ":material/notifications:"
COLLAPSE = ":material/chevron_left:"
EXPAND = ":material/chevron_right:"
PIN = ":material/left_panel_close:"      # 固定展开时：点击收起为 rail
UNPIN = ":material/left_panel_open:"     # rail 时：点击固定展开


def nav_label(key) -> str:
    """导航标签（按当前语言解析）。"""
    from taiplan.i18n import t
    return t(NAV_LABELS[key])


def settings_label(key) -> str:
    """设置二级导航标签（按当前语言解析）。"""
    from taiplan.i18n import t
    return t(SETTINGS_LABELS[key])


def nav_entries():
    """(key, icon, 已解析标签) 列表，供侧栏渲染。"""
    return [(key, icon, nav_label(key)) for key, icon, _ in NAV_ENTRIES]


def nav_label(key) -> str:
    """导航标签（按当前语言解析）。"""
    from taiplan.i18n import t
    return t(NAV_LABELS[key])


def settings_label(key) -> str:
    """设置二级导航标签（按当前语言解析）。"""
    from taiplan.i18n import t
    return t(SETTINGS_LABELS[key])


def nav_entries():
    """(key, icon, 已解析标签) 列表，供侧栏渲染。"""
    return [(key, icon, nav_label(key)) for key, icon, _ in NAV_ENTRIES]


def is_material_icon(text) -> bool:
    """是否是合法的 Material 图标字符串。"""
    return (isinstance(text, str) and text.startswith(":material/")
            and text.endswith(":") and " " not in text)
