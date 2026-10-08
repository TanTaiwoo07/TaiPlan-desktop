# -*- coding: utf-8 -*-
"""日期/星期本地化（Stage 17.3 §9）。

要求：
  * 中文 "10月5日 · 周一"；英文 "Oct 5 · Monday"
  * 使用应用自己的格式函数，不修改系统全局 locale
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from i18n import get_language

WEEKDAYS_ZH = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")
WEEKDAYS_EN = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
WEEKDAYS_EN_SHORT = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MONTHS_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
             "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")

# 日历组件的星期标题（单字/短名）
WEEKDAY_HEADS_ZH = ("一", "二", "三", "四", "五", "六", "日")
WEEKDAY_HEADS_EN = ("Mo", "Tu", "We", "Th", "Fr", "Sa", "Su")


def _is_zh() -> bool:
    return get_language() == "zh-CN"


def weekday_name(d, short=False):
    idx = d.weekday()
    if _is_zh():
        return WEEKDAYS_ZH[idx]
    return WEEKDAYS_EN_SHORT[idx] if short else WEEKDAYS_EN[idx]


def weekday_heads():
    return WEEKDAY_HEADS_ZH if _is_zh() else WEEKDAY_HEADS_EN


def month_day(d, with_weekday=True):
    """"10月5日 · 周一" / "Oct 5 · Monday"（当年省略年份，跨年补上）。"""
    today = date.today()
    year_part = "" if d.year == today.year else (
        f"{d.year}年" if _is_zh() else f"{d.year} ")
    month_name = (f"{d.month}月" if _is_zh() else f"{MONTHS_EN[d.month - 1]} ")
    day_part = (f"{d.day}日" if _is_zh() else f"{d.day}")
    text = f"{year_part}{month_name}{day_part}"
    if with_weekday:
        text = f"{text} \u00b7 {weekday_name(d)}"
    return text


def full_date(d):
    """"2026年10月5日" / "Oct 5, 2026"。"""
    if _is_zh():
        return f"{d.year}年{d.month}月{d.day}日"
    return f"{MONTHS_EN[d.month - 1]} {d.day}, {d.year}"


def month_year(d):
    return f"{d.year} 年 {d.month} 月" if _is_zh() else f"{MONTHS_EN[d.month - 1]} {d.year}"


def relative_day(d, today=None):
    """今天 / 明天 / 昨天 / None（其余交给 month_day）。"""
    today = today or date.today()
    delta = (d - today).days if isinstance(d, date) else None
    if delta == 0:
        return "今天" if _is_zh() else "Today"
    if delta == 1:
        return "明天" if _is_zh() else "Tomorrow"
    if delta == -1:
        return "昨天" if _is_zh() else "Yesterday"
    return None


def human_date(d, today=None):
    """优先相对说法，其次月日 + 星期。"""
    rel = relative_day(d, today=today)
    return rel if rel else month_day(d)


def datetime_short(dt):
    """"10月5日 14:30" / "Oct 5, 14:30"。"""
    hhmm = dt.strftime("%H:%M")
    return f"{month_day(dt.date(), with_weekday=False)} {hhmm}"


def minutes_label(minutes):
    """分钟显示（与 catalog 的 duration.* 分别处理；此处用于日历里的时长）。"""
    minutes = int(minutes)
    if _is_zh():
        if minutes % 60 == 0 and minutes >= 60:
            hours = minutes // 60
            return f"{hours} 小时"
        return f"{minutes} 分钟"
    if minutes % 60 == 0 and minutes >= 60:
        hours = minutes // 60
        return f"{hours} hour" + ("s" if hours > 1 else "")
    return f"{minutes} min"


def to_iso(d):
    return d.isoformat() if isinstance(d, date) else d


__all__ = ["weekday_name", "weekday_heads", "month_day", "full_date", "month_year",
           "relative_day", "human_date", "datetime_short", "minutes_label", "to_iso",
           "WEEKDAYS_ZH", "WEEKDAYS_EN", "MONTHS_EN"]
