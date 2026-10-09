"""Calendar Adapter：Task / Occurrence ←→ FullCalendar Event。

把「项目投影」与「FullCalendar 事件」互相转换，并把 FullCalendar 回调
翻译成中立的「变更描述」，由 app / calendar_view 决定如何写库。

本模块只做纯转换（不写数据库），便于单测。
"""

from datetime import date as _date
from datetime import datetime, timedelta

from taiplan import calendar_settings

EVENT_TASK_PREFIX = "task"
EVENT_OCCURRENCE_PREFIX = "occurrence"


# ---------------------------------------------------------------
# Event ID
# ---------------------------------------------------------------

def make_event_id(item):
    """普通任务 → task:{id}；重复 occurrence → occurrence:{id}:{occurrence_key}。"""
    if item.is_recurring and item.occurrence_key:
        return f"{EVENT_OCCURRENCE_PREFIX}:{item.task_id}:{item.occurrence_key}"
    return f"{EVENT_TASK_PREFIX}:{item.task_id}"


def parse_event_id(event_id):
    """解析 event id → dict(kind, task_id, occurrence_key)。非法返回 None。"""
    if not event_id:
        return None
    parts = str(event_id).split(":", 2)
    if len(parts) < 2:
        return None
    kind = parts[0]
    try:
        task_id = int(parts[1])
    except (TypeError, ValueError):
        return None
    if kind == EVENT_TASK_PREFIX:
        return {"kind": "task", "task_id": task_id, "occurrence_key": None}
    if kind == EVENT_OCCURRENCE_PREFIX and len(parts) == 3:
        return {"kind": "occurrence", "task_id": task_id, "occurrence_key": parts[2]}
    return None


# ---------------------------------------------------------------
# 时间工具
# ---------------------------------------------------------------

def _add_minutes(hhmm, minutes):
    """HH:MM + 分钟 → HH:MM（自动跨午夜回绕）。"""
    if hhmm is None:
        return None
    hh = int(hhmm[:2])
    mm = int(hhmm[3:5])
    total = (hh * 60 + mm + int(minutes)) % (24 * 60)
    return f"{total // 60:02d}:{total % 60:02d}"


def event_start_end(item, default_duration=None):
    """返回 (start_str, end_str)；全天返回 (YYYY-MM-DD, None)。

    跨午夜时 end 自动滚到次日（例如 23:00 + 120min → 次日 01:00）。
    """
    if not item.date:
        return None, None
    if not item.time:
        return item.date, None
    dur = item.duration_minutes
    if dur is None:
        dur = default_duration if default_duration is not None \
            else calendar_settings.DEFAULT_DURATION_MINUTES

    start_dt = datetime.fromisoformat(f"{item.date}T{item.time}")
    end_dt = start_dt + timedelta(minutes=int(dur))
    return start_dt.strftime("%Y-%m-%dT%H:%M"), end_dt.strftime("%Y-%m-%dT%H:%M")


# ---------------------------------------------------------------
# Item → Event
# ---------------------------------------------------------------

def item_to_event(item, cfg=None, conflict=False, default_duration=None):
    """把一个投影项转成 FullCalendar event dict。"""
    if cfg is None:
        cfg = calendar_settings.load_calendar_config()
    if default_duration is None:
        default_duration = int(cfg.get("default_duration_minutes",
                                       calendar_settings.DEFAULT_DURATION_MINUTES))

    start, end = event_start_end(item, default_duration)
    all_day = item.time is None

    event = {
        "id": make_event_id(item),
        "title": _title_for(item),
        "start": start,
        "allDay": all_day,
        "backgroundColor": _bg_for(item, conflict),
        "borderColor": _border_for(item, conflict),
        "textColor": "#ffffff" if item.priority == "urgent" and not item.is_completed else "#1f2328",
        "extendedProps": {
            "taskId": item.task_id,
            "occurrenceKey": item.occurrence_key,
            "isRecurring": bool(item.is_recurring),
            "isCompleted": bool(item.is_completed),
            "priority": item.priority,
            "hasTime": item.time is not None,
            "durationMinutes": item.duration_minutes,
            "hasConflict": bool(conflict),
        },
    }
    if end:
        event["end"] = end
    return event


def _title_for(item):
    parts = []
    if item.is_completed:
        parts.append("✅")
    if item.is_recurring:
        parts.append("🔁")
    if item.time:
        parts.append(item.time)
    parts.append(item.title or "")
    title = " ".join(p for p in parts if p)
    if item.duration_minutes:
        title += f" ({_human_duration(item.duration_minutes)})"
    return title


def _human_duration(minutes):
    minutes = int(minutes)
    if minutes % 60 == 0:
        return f"{minutes // 60}h"
    if minutes > 60:
        return f"{minutes // 60}h{minutes % 60}m"
    return f"{minutes}m"


def _bg_for(item, conflict):
    if item.is_completed:
        return "#d8dee4"
    if item.priority == "urgent":
        return "#d1242f"
    if conflict:
        return "#fb8500"
    if item.is_recurring:
        return "#8250df"
    return "#0969da"


def _border_for(item, conflict):
    if item.is_completed:
        return "#b8c0c8"
    if item.priority == "urgent":
        return "#a40e26"
    if conflict:
        return "#e07b00"
    if item.is_recurring:
        return "#6639ba"
    return "#0550ae"


def items_to_events(items, cfg=None, conflict_ids=None):
    """批量转换。conflict_ids 为处于冲突状态的 (task_id, occurrence_key) 集合。"""
    if cfg is None:
        cfg = calendar_settings.load_calendar_config()
    conflict_ids = conflict_ids or set()
    default_duration = int(cfg.get("default_duration_minutes",
                                   calendar_settings.DEFAULT_DURATION_MINUTES))
    out = []
    for item in items:
        conflict = (item.task_id, item.occurrence_key) in conflict_ids
        out.append(item_to_event(item, cfg, conflict, default_duration))
    return out


# ---------------------------------------------------------------
# FullCalendar 回调 → 中立变更描述
# ---------------------------------------------------------------

def _split_iso(value):
    """把 'YYYY-MM-DDTHH:MM:SS' 或 'YYYY-MM-DD' 拆成 (date, time)。"""
    if not value:
        return None, None
    value = str(value)
    if "T" in value:
        d, t = value.split("T", 1)
        return d, t[:5]
    return value, None


def _minutes_between(start_hm, end_hm):
    """两个 HH:MM 之间的分钟数（允许跨午夜）。"""
    if not start_hm or not end_hm:
        return 60
    s = int(start_hm[:2]) * 60 + int(start_hm[3:5])
    e = int(end_hm[:2]) * 60 + int(end_hm[3:5])
    if e <= s:
        e += 24 * 60
    return e - s


def compute_event_change(event, cfg=None):
    """把 eventChange 的 event payload 转成中立变更描述。

    返回 dict：
        kind, task_id, occurrence_key,
        date, time, duration_minutes, all_day
    无法解析返回 None。
    """
    if not event:
        return None
    parsed = parse_event_id(event.get("id"))
    if parsed is None:
        return None

    all_day = bool(event.get("allDay"))
    start_date, start_time = _split_iso(event.get("start"))
    end_date, end_time = _split_iso(event.get("end"))

    result = dict(parsed)
    result["all_day"] = all_day
    result["date"] = start_date

    if all_day or start_time is None:
        result["time"] = None
        result["duration_minutes"] = None
        return result

    result["time"] = start_time
    if end_time is not None:
        result["duration_minutes"] = _minutes_between(start_time, end_time)
    else:
        result["duration_minutes"] = None
    return result


def compute_date_click(payload, cfg=None):
    """dateClick → 预填信息 dict(date, time, duration_minutes, all_day)。"""
    if not payload:
        return None
    all_day = bool(payload.get("allDay"))
    d, t = _split_iso(payload.get("date") or payload.get("dateStr"))
    if d is None:
        return None
    if all_day or t is None:
        return {"date": d, "time": None, "duration_minutes": None, "all_day": True}
    default_duration = int((cfg or {}).get("default_duration_minutes",
                                           calendar_settings.DEFAULT_DURATION_MINUTES))
    return {"date": d, "time": t, "duration_minutes": default_duration, "all_day": False}


def compute_select(payload, cfg=None):
    """select（拖选时间范围）→ 预填信息 dict。"""
    if not payload:
        return None
    all_day = bool(payload.get("allDay"))
    start_date, start_time = _split_iso(payload.get("start"))
    end_date, end_time = _split_iso(payload.get("end"))
    if start_date is None:
        return None
    if all_day or start_time is None:
        return {"date": start_date, "time": None, "duration_minutes": None, "all_day": True}

    duration = _minutes_between(start_time, end_time)
    # 拖选跨到第二天：仅用开始日 + 时长（不保存 end_date）
    if end_date and end_date != start_date:
        duration = _minutes_between(start_time, end_time)
    duration = max(5, min(1440, duration)) if duration else 60
    return {"date": start_date, "time": start_time,
            "duration_minutes": duration, "all_day": False}


def compute_event_click(payload):
    """eventClick → event id 解析结果。"""
    if not payload:
        return None
    event = payload.get("event") or payload
    return parse_event_id(event.get("id"))


def change_fingerprint(payload):
    """为回调生成 transient fingerprint，避免同一次拖动被重复提交。"""
    if not payload:
        return None
    event = payload.get("event") or payload
    return "|".join(str(event.get(k)) for k in ("id", "start", "end", "allDay"))


# ---------------------------------------------------------------
# 范围计算
# ---------------------------------------------------------------

def range_for_view(view, current_date_iso):
    """根据视图与当前日期返回 (range_start, range_end)（含首尾）。"""
    try:
        cur = _date.fromisoformat(current_date_iso)
    except (TypeError, ValueError):
        cur = _date.today()

    if view == calendar_settings.VIEW_MONTH:
        first = cur.replace(day=1)
        if first.month == 12:
            last = _date(first.year, 12, 31)
        else:
            last = _date(first.year, first.month + 1, 1) - timedelta(days=1)
        # 向前后各扩展一周，覆盖日历网格边缘
        return (first - timedelta(days=7)).isoformat(), (last + timedelta(days=7)).isoformat()

    if view == calendar_settings.VIEW_WEEK:
        start = cur - timedelta(days=cur.weekday())
        end = start + timedelta(days=6)
        return start.isoformat(), end.isoformat()

    return cur.isoformat(), cur.isoformat()
