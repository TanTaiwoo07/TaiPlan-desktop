"""编辑会话状态机。

严格区分三层：

    pending_edit_request   —— 只有意图（task_id / mode / occurrence_key），不碰 widget
    active_edit_session    —— 当前正在编辑的 task_id
    widget_state           —— edit_* widget 的具体值

铁律：**edit widget 一旦在本次 rerun 实例化，就不能再给同名 session_state key 赋值**
（否则 Streamlit 抛 StreamlitWidgetAlreadyInstantiatedError）。
因此所有 widget state 的写入都集中在 prepare()，它必须在任何 edit widget
被创建之前调用；「打开」和「关闭」都通过 pending 请求 + rerun 两阶段完成。
"""

import streamlit as st

import calendar_settings

PENDING_KEY = "pending_edit_request"
ACTIVE_KEY = "active_edit_task_id"
ACTIVE_MODE_KEY = "active_edit_mode"
ACTIVE_OCCURRENCE_KEY = "active_edit_occurrence_key"   # mode=event 时的这一次
CLOSE_KEY = "close_edit_after_rerun"
DIALOG_KEY = "editing_task_id"          # app.py 触发 edit_dialog 的键（非 widget）

RECURRENCE_LABELS = {
    "hourly": "每小时", "daily": "每天", "workday": "工作日", "weekend": "周末",
    "weekly": "每周", "monthly": "每月", "quarterly": "每3个月", "yearly": "每年",
}

# 优先级：数据库值 → 下拉文案（与 app.py 保持一致，避免反向 import）
PRIORITY_REVERSE = {"low": "低", "normal": "普通", "urgent": "紧急"}


def widget_keys(task_id):
    """返回某个 task 对应的全部 edit widget session_state key。"""
    t = task_id
    return [
        f"edit_title_{t}",
        f"edit_description_{t}",
        f"edit_url_{t}",
        f"edit_has_date_{t}",
        f"edit_date_{t}",
        f"edit_has_time_{t}",
        f"edit_time_{t}",
        f"edit_duration_{t}_minutes",
        f"edit_duration_{t}_custom",
        f"edit_priority_{t}",
        f"edit_has_recurring_{t}",
        f"edit_recurrence_frequency_{t}",
        f"edit_recurrence_interval_{t}",
        f"edit_recurrence_end_type_{t}",
        f"edit_recurrence_end_date_{t}",
    ]


# ---------------------------------------------------------------
# 请求层（可以在任意时机调用，只写非 widget key）
# ---------------------------------------------------------------

def request_edit(task_id, mode="task", occurrence_key=None):
    """登记一次编辑请求。不初始化任何 widget state。"""
    st.session_state[PENDING_KEY] = {
        "task_id": int(task_id),
        "mode": mode,                     # task / series / event
        "occurrence_key": occurrence_key,
    }
    st.session_state.pop(CLOSE_KEY, None)


def request_close():
    """请求关闭编辑器（保存 / 取消 / 关闭通用）。真正的清理延后到下一轮。"""
    st.session_state[CLOSE_KEY] = True


def has_pending_request():
    return bool(st.session_state.get(PENDING_KEY))


# ---------------------------------------------------------------
# widget state 清理 / 填充（只允许在 widget 实例化之前调用）
# ---------------------------------------------------------------

def _drop_key(key):
    try:
        if key in st.session_state:
            del st.session_state[key]
    except Exception:
        pass


def clear_widget_state(task_id):
    """删除某个 task 的 edit widget state（widget 实例化之前调用才安全）。"""
    if task_id is None:
        return
    for key in widget_keys(task_id):
        _drop_key(key)


def populate_widget_state(task_id, row):
    """用数据库行的值预填 edit widget state。"""
    from datetime import date, time

    st.session_state[f"edit_title_{task_id}"] = row["title"] or ""
    st.session_state[f"edit_description_{task_id}"] = row["description"] or ""
    st.session_state[f"edit_url_{task_id}"] = row["url"] or ""

    has_date = row["date"] is not None
    st.session_state[f"edit_has_date_{task_id}"] = has_date
    if row["date"]:
        st.session_state[f"edit_date_{task_id}"] = date.fromisoformat(row["date"])

    has_time = row["time"] is not None
    st.session_state[f"edit_has_time_{task_id}"] = has_time
    if row["time"]:
        st.session_state[f"edit_time_{task_id}"] = time.fromisoformat(row["time"])

    try:
        dur = row["duration_minutes"]
    except (KeyError, IndexError):
        dur = None
    st.session_state[f"edit_duration_{task_id}_minutes"] = \
        calendar_settings.DURATION_LABELS.get(
            int(dur or calendar_settings.DEFAULT_DURATION_MINUTES), "自定义")
    if dur and int(dur) not in calendar_settings.DURATION_CHOICES:
        st.session_state[f"edit_duration_{task_id}_custom"] = int(dur)

    st.session_state[f"edit_priority_{task_id}"] = PRIORITY_REVERSE.get(row["priority"], "普通")

    st.session_state[f"edit_has_recurring_{task_id}"] = bool(row["is_recurring"])
    if row["is_recurring"]:
        st.session_state[f"edit_recurrence_frequency_{task_id}"] = \
            RECURRENCE_LABELS.get(row["recurrence_frequency"], "每天")
        st.session_state[f"edit_recurrence_interval_{task_id}"] = int(row["recurrence_interval"] or 1)
        if row["recurrence_end_type"] == "on_date":
            st.session_state[f"edit_recurrence_end_type_{task_id}"] = "指定日期"
            if row["recurrence_end_date"]:
                st.session_state[f"edit_recurrence_end_date_{task_id}"] = \
                    date.fromisoformat(row["recurrence_end_date"])
        else:
            st.session_state[f"edit_recurrence_end_type_{task_id}"] = "永不结束"


# ---------------------------------------------------------------
# 主入口：必须在任何 edit widget 之前调用
# ---------------------------------------------------------------

def prepare(services_module):
    """处理待关闭 / 待初始化的编辑会话。

    必须在 app 主流程最前面调用（任何 edit widget 实例化之前）。
    返回当前活跃的 task_id 或 None。
    """
    # 1) 关闭请求
    if st.session_state.get(CLOSE_KEY):
        active = st.session_state.get(ACTIVE_KEY)
        clear_widget_state(active)
        for key in (ACTIVE_KEY, ACTIVE_MODE_KEY, DIALOG_KEY, CLOSE_KEY,
                    ACTIVE_OCCURRENCE_KEY):
            st.session_state.pop(key, None)
        st.session_state.pop(PENDING_KEY, None)

    # 2) 初始化请求
    pending = st.session_state.pop(PENDING_KEY, None)
    if not pending:
        return st.session_state.get(ACTIVE_KEY)

    task_id = pending.get("task_id")
    if task_id is None:
        return st.session_state.get(ACTIVE_KEY)

    row = services_module.get_task(task_id)
    if row is None:
        return st.session_state.get(ACTIVE_KEY)

    # 切换任务时先清掉上一个任务的 widget state
    previous = st.session_state.get(ACTIVE_KEY)
    if previous is not None and previous != task_id:
        clear_widget_state(previous)

    populate_widget_state(task_id, row)

    mode = pending.get("mode", "task")
    occurrence_key = pending.get("occurrence_key")
    if mode == "event" and occurrence_key:
        # 「仅修改这一次」：把这一次已有的 override 叠加到日期/时间/时长控件上，
        # 这样弹窗里看到的是**这一次**的实际值，而不是系列值。
        apply_occurrence_override(task_id, occurrence_key, services_module)
    else:
        occurrence_key = None

    st.session_state[DIALOG_KEY] = task_id
    st.session_state[ACTIVE_KEY] = task_id
    st.session_state[ACTIVE_MODE_KEY] = mode
    st.session_state[ACTIVE_OCCURRENCE_KEY] = occurrence_key
    return task_id


def apply_occurrence_override(task_id, occurrence_key, services_module):
    """把某一次 occurrence 的 override 叠加到 edit widget state 上。"""
    try:
        ov = services_module.get_occurrence_override(task_id, occurrence_key)
    except Exception:
        ov = None
    if not ov:
        return
    from datetime import date as _date
    from datetime import time as _time

    try:
        od = ov["override_date"]
    except (KeyError, IndexError, TypeError):
        od = None
    if od:
        st.session_state[f"edit_has_date_{task_id}"] = True
        st.session_state[f"edit_date_{task_id}"] = _date.fromisoformat(str(od))

    try:
        ot = ov["override_time"]
    except (KeyError, IndexError, TypeError):
        ot = None
    if ot:
        st.session_state[f"edit_has_time_{task_id}"] = True
        st.session_state[f"edit_time_{task_id}"] = _time.fromisoformat(str(ot))

    try:
        odur = ov["override_duration_minutes"]
    except (KeyError, IndexError, TypeError):
        odur = None
    if odur is not None:
        dur = int(odur)
        st.session_state[f"edit_duration_{task_id}_minutes"] = \
            calendar_settings.DURATION_LABELS.get(dur, "自定义")
        if dur not in calendar_settings.DURATION_CHOICES:
            st.session_state[f"edit_duration_{task_id}_custom"] = dur


def active_mode():
    return st.session_state.get(ACTIVE_MODE_KEY)


def active_occurrence_key():
    """mode=event 时返回这一次的 occurrence_key，否则 None。"""
    return st.session_state.get(ACTIVE_OCCURRENCE_KEY)
