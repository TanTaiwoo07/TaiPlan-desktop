"""Calendar 日历视图。

两种实现：
1. 交互式日历（streamlit-calendar / FullCalendar）—— 默认
2. 基础日历（原有月视图 / 日视图）—— 组件不可用时的 fallback

Calendar 不是第二套任务数据库，只是把 tasks 表和重复 occurrence
投影成事件。所有任务数据仍只存于 SQLite tasks 表；
occurrence 完成/取消状态存于 task_occurrence_states，
单次移动/时长覆盖存于 task_occurrence_overrides。
"""

import calendar as _pycalendar
import hashlib
import json
from datetime import date, datetime, time as _time, timedelta

import streamlit as st

from i18n import dates, t

import calendar_adapter
import calendar_settings
import edit_session
import services

PRIORITY_LABEL = {"low": "低", "normal": "普通", "urgent": "🔴 紧急"}
PRIORITY_MAP = {"低": "low", "普通": "normal", "紧急": "urgent"}
PRIORITY_REVERSE = {"low": "低", "normal": "普通", "urgent": "紧急"}
PRIORITY_OPTIONS = ["低", "普通", "紧急"]

WEEKDAY_HEADERS = ["一", "二", "三", "四", "五", "六", "日"]

CALENDAR_COMPONENT_KEY = "todo_interactive_calendar"
_PENDING_CHANGE_KEY = "_pending_cal_change"
_LAST_CHANGE_FP_KEY = "_cal_last_change_fp"
_CREATE_PREFILL_KEY = "_calendar_create_prefill"
_CLEAR_CALLBACK_KEY = "_clear_calendar_callback"
CONSUMED_CLICK_KEY = "_cal_consumed_click"
LAST_CAL_KEY = "_cal_last_component_key"
LAST_CALLBACK_KEY = "_cal_last_callback"


def _interactive_available():
    """streamlit-calendar 是否可用。"""
    try:
        from streamlit_calendar import calendar  # noqa: F401
        return True
    except Exception:
        return False


# ---------------------------------------------------------------
# 旧状态键（基础视图使用）
# ---------------------------------------------------------------

def _selected_date():
    if "calendar_selected_date" not in st.session_state:
        st.session_state["calendar_selected_date"] = date.today()
    return st.session_state["calendar_selected_date"]


def _set_selected_date(d):
    st.session_state["calendar_selected_date"] = d


def _calendar_view_mode():
    if "calendar_view_mode" not in st.session_state:
        st.session_state["calendar_view_mode"] = "month"
    return st.session_state["calendar_view_mode"]


def _set_calendar_view_mode(mode):
    st.session_state["calendar_view_mode"] = mode


def _safe_key(s):
    return str(s).replace(":", "-").replace("T", "-")


def _start_edit_series(task_id, occurrence_key=None):
    """请求打开编辑弹窗（两阶段：只登记请求，下一轮再初始化 widget state）。

    绝不在 calendar 回调里直接写 edit_* widget state，
    否则一旦该 widget 已在本次 rerun 实例化，Streamlit 会抛
    StreamlitWidgetAlreadyInstantiatedError。

    带 occurrence_key（在日历里点了某一次）→ mode="event"，
    弹窗里再让用户选「仅修改这一次 / 修改整个系列」；
    不带 → mode="series"（改整个系列）。
    """
    edit_session.request_edit(
        task_id,
        mode="event" if occurrence_key is not None else "series",
        occurrence_key=occurrence_key,
    )


def _item_label(item):
    prefix = "✅ " if item.is_completed else ""
    time_part = f"{item.time} " if item.time else ""
    urgent = "🔴 " if (item.priority == "urgent" and not item.is_completed) else ""
    return f"{prefix}{urgent}{time_part}{item.title}"


# ===============================================================
# 交互式日历（默认）
# ===============================================================

def _interactive_state():
    """初始化并返回 (cfg, view, current_date_iso)。"""
    cfg = calendar_settings.load_calendar_config()
    if "calendar_view_mode_full" not in st.session_state:
        st.session_state["calendar_view_mode_full"] = cfg.get(
            "default_view", calendar_settings.VIEW_MONTH)
    if "calendar_current_date" not in st.session_state:
        st.session_state["calendar_current_date"] = date.today().isoformat()
    return cfg, st.session_state["calendar_view_mode_full"], \
        st.session_state["calendar_current_date"]


def _shift_current(view, current_iso, direction):
    """按视图粒度移动当前日期。"""
    try:
        cur = date.fromisoformat(current_iso)
    except (TypeError, ValueError):
        cur = date.today()
    if view == calendar_settings.VIEW_WEEK:
        return (cur + timedelta(days=7 * direction)).isoformat()
    if view == calendar_settings.VIEW_DAY:
        return (cur + timedelta(days=direction)).isoformat()
    # month
    month_index = cur.year * 12 + (cur.month - 1) + direction
    year, month = month_index // 12, month_index % 12 + 1
    day = min(cur.day, _pycalendar.monthrange(year, month)[1])
    return date(year, month, day).isoformat()


def _conflict_ids(items, cfg):
    """返回处于时间冲突状态的 (task_id, occurrence_key) 集合。"""
    default = int(cfg.get("default_duration_minutes", calendar_settings.DEFAULT_DURATION_MINUTES))
    conflicts = set()
    for item in items:
        if item.is_completed or not item.date or not item.time:
            continue
        dur = services.resolve_duration(item, default)
        if services.detect_time_conflicts(
                item.date, item.time, dur,
                exclude_task_id=item.task_id,
                exclude_occurrence_key=item.occurrence_key,
                default=default):
            conflicts.add((item.task_id, item.occurrence_key))
    return conflicts


def render_calendar_view():
    """日历入口：优先交互式，组件不可用时回退到基础视图。"""
    if _interactive_available():
        render_interactive_calendar()
    else:
        st.error(t('cal.fallback_warning'))
        render_basic_calendar()


def render_interactive_calendar():
    from streamlit_calendar import calendar as cal_component

    cfg, view, current_iso = _interactive_state()
    slot = int(cfg.get("slot_duration_minutes", calendar_settings.SLOT_DURATION_MINUTES))

    # 上一轮消费过的回调 → 在本轮创建组件之前清空组件状态，
    # 避免 eventClick / eventChange 在后续 rerun 中被重复投递。
    if st.session_state.get(_CLEAR_CALLBACK_KEY):
        st.session_state[_CLEAR_CALLBACK_KEY] = False
        _reset_component_state(st.session_state.get(LAST_CAL_KEY))
        st.session_state.pop(CONSUMED_CLICK_KEY, None)
        st.session_state.pop(_LAST_CHANGE_FP_KEY, None)

    # ---------- 顶部工具栏 ----------
    col_today, col_prev, col_next, col_view, col_more = st.columns([1, 0.8, 0.8, 4, 1.5])
    with col_today:
        if st.button(t('common.today'), key="cal_today"):
            st.session_state["calendar_current_date"] = date.today().isoformat()
            st.rerun()
    with col_prev:
        if st.button("←", key="cal_prev"):
            st.session_state["calendar_current_date"] = _shift_current(view, current_iso, -1)
            st.rerun()
    with col_next:
        if st.button("→", key="cal_next"):
            st.session_state["calendar_current_date"] = _shift_current(view, current_iso, 1)
            st.rerun()
    with col_view:
        labels = ["月", "周", "日"]
        current_label = calendar_settings.VIEW_LABELS.get(view, "月")
        picked = st.segmented_control(
            t('cal.view_label'), labels, default=current_label,
            format_func=_view_label,
            key="cal_view_seg", label_visibility="collapsed")
        if picked and calendar_settings.LABEL_TO_VIEW[picked] != view:
            st.session_state["calendar_view_mode_full"] = calendar_settings.LABEL_TO_VIEW[picked]
            st.rerun()
    with col_more:
        with st.popover(t('cal.settings'), key="cal_settings_pop", use_container_width=True):
            render_calendar_settings(key_prefix="cal_cfg_pop")

    # ---------- 当前范围事件 ----------
    range_start, range_end = calendar_adapter.range_for_view(view, current_iso)
    items = services.get_items_for_range(range_start, range_end)
    conflict_ids = _conflict_ids(items, cfg)
    events = calendar_adapter.items_to_events(items, cfg, conflict_ids)

    st.caption(t("cal.current", current=current_iso, count=len(items))
               + (t("cal.conflicts_suffix", count=len(conflict_ids)) if conflict_ids else ""))

    options = {
        "initialView": view,
        "initialDate": current_iso,
        "locale": _fc_locale(),
        "timeZone": "local",
        "firstDay": 1,
        "nowIndicator": bool(cfg.get("show_now_indicator", True)),
        "slotDuration": f"00:{slot:02d}:00",
        "slotLabelInterval": f"00:{slot:02d}:00",
        "slotMinTime": "00:00:00",
        "slotMaxTime": "24:00:00",
        "allDaySlot": True,
        "editable": True,
        "selectable": True,
        "dayMaxEvents": False,
        "weekends": bool(cfg.get("show_weekends", True)),
        "headerToolbar": False,
        "height": "auto",
        "buttonText": {"today": t('common.today'), "month": "月", "week": "周", "day": "日"},
    }

    component_key = _component_key(events, view, current_iso)
    st.session_state[LAST_CAL_KEY] = component_key

    state = cal_component(
        events=events,
        options=options,
        callbacks=["dateClick", "eventClick", "eventChange", "select"],
        key=component_key,
    )

    _handle_calendar_state(state, cfg, view)


def _component_key(events, view, current_iso):
    """由「事件内容 + 视图 + 当前日期」派生组件 key。

    必须这样做，因为 FullCalendar 的 React 包装层在 props 变化时只调用
    resetOptions()，内部派发 {type:"NOTHING"}，而 reduceEventSources /
    reduceEventStore 对 NOTHING 直接返回旧值 —— 也就是说：
      * 只改 events 不会刷新日历；
      * initialView / initialDate 只在初始化时生效（自建工具栏导航同样不生效）。
    因此当数据/视图/日期变化时更换 key，让组件重新初始化；
    三者都不变时 key 不变，避免无关 rerun 重置视图与滚动位置。
    """
    payload = json.dumps(events, sort_keys=True, ensure_ascii=False, default=str)
    raw = "|".join([view, current_iso, payload])
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]
    return f"{CALENDAR_COMPONENT_KEY}_{digest}"


def _reset_component_state(component_key=None):
    """清空 streamlit-calendar 组件状态（widget 实例化之前调用）。

    注意：删除（__delitem__）widget key 是 Streamlit 允许的，
    设置（__setitem__）才会抛 StreamlitWidgetAlreadyInstantiatedError。
    """
    for key in (component_key, CALENDAR_COMPONENT_KEY):
        if not key:
            continue
        try:
            if key in st.session_state:
                del st.session_state[key]
        except Exception:
            pass


def _note_callback(text):
    """记录最近一次日历回调，便于用户分辨点了什么。"""
    st.session_state[LAST_CALLBACK_KEY] = text


def _mark_consumed():
    """标记本轮已消费组件回调，下一轮 rerun 开始时清空组件状态。"""
    st.session_state[_CLEAR_CALLBACK_KEY] = True


def _handle_calendar_state(state, cfg, view):
    """处理 FullCalendar 回调。

    组件返回值会在后续 rerun 中持续存在，因此：
    1. 用 fingerprint 保证同一 payload 只处理一次；
    2. 处理完立即标记消费，下一轮 rerun 清空组件状态，
       从而允许用户以后再次点击同一任务。
    """
    if not state:
        # 本轮没有回调（组件状态已被清空）→ 重置 fingerprint，允许未来重新点击
        st.session_state.pop(CONSUMED_CLICK_KEY, None)
        st.session_state.pop(_LAST_CHANGE_FP_KEY, None)
        return

    # ---- eventChange（拖动 / 拉伸）----
    if state.get("eventChange"):
        payload = state["eventChange"]
        fp = calendar_adapter.change_fingerprint(payload)
        if fp is not None and st.session_state.get(_LAST_CHANGE_FP_KEY) == fp:
            return
        st.session_state[_LAST_CHANGE_FP_KEY] = fp
        _mark_consumed()
        _apply_event_change(payload, cfg)
        return

    # ---- dateClick（单击空白时间）----
    if state.get("dateClick"):
        info = (calendar_adapter.compute_date_click(state["dateClick"], cfg)
                if cfg.get("blank_click_action", "ignore") == "create" else None)
        _note_callback(f"dateClick {info['date']} {info['time'] or '(全天)'}" if info
                       else "dateClick（已按设置忽略）")
        _mark_consumed()
        if info:
            st.session_state[_CREATE_PREFILL_KEY] = info
            st.rerun()
        return

    # ---- select（拖选时间范围）----
    if state.get("select"):
        info = (calendar_adapter.compute_select(state["select"], cfg)
                if cfg.get("drag_select_action", "create") == "create" else None)
        _note_callback(f"select {info['date']} {info['time']}（{info['duration_minutes']} 分钟）"
                       if info else "select（已按设置忽略）")
        _mark_consumed()
        if info:
            st.session_state[_CREATE_PREFILL_KEY] = info
            st.rerun()
        return

    # ---- eventClick（点击已有任务）----
    if state.get("eventClick"):
        parsed = calendar_adapter.compute_event_click(state["eventClick"])
        fp = None
        if parsed:
            fp = f"eventClick|{parsed['task_id']}|{parsed['occurrence_key']}"
        if fp is not None and st.session_state.get(CONSUMED_CLICK_KEY) == fp:
            return  # 同一次点击的重复投递，忽略
        st.session_state[CONSUMED_CLICK_KEY] = fp
        _mark_consumed()
        if parsed:
            _note_callback(f"eventClick 已有任务 #{parsed['task_id']}"
                           + (f" [{parsed['occurrence_key']}]" if parsed["occurrence_key"] else ""))
            _start_edit_series(parsed["task_id"], parsed["occurrence_key"])
            st.rerun()
        return


def _apply_event_change(payload, cfg):
    """执行拖动 / 拉伸产生的变更。"""
    from datetime import datetime as _dt

    event = payload.get("event") if isinstance(payload, dict) else None
    event = event or payload
    change = calendar_adapter.compute_event_change(event, cfg)
    if change is None:
        return

    if change["kind"] == "task":
        # 普通任务：即时保存
        try:
            services.move_plain_task(
                change["task_id"],
                new_date=change["date"],
                new_time=change["time"],
                new_duration_minutes=change["duration_minutes"],
                clear_time=change["all_day"],
            )
        except ValueError as e:
            st.error(t("cal.save_failed", error=e))
            st.rerun()
            return
        _notify_conflicts_after_save(change, cfg)
        st.rerun()
        return

    # 重复 occurrence：先不写库，弹窗让用户选择范围
    st.session_state[_PENDING_CHANGE_KEY] = change
    st.rerun()


def _notify_conflicts_after_save(change, cfg):
    if not change.get("date") or not change.get("time"):
        return
    dur = change.get("duration_minutes") or int(
        cfg.get("default_duration_minutes", calendar_settings.DEFAULT_DURATION_MINUTES))
    conflicts = services.detect_time_conflicts(
        change["date"], change["time"], dur,
        exclude_task_id=change["task_id"],
        exclude_occurrence_key=None,
        default=int(cfg.get("default_duration_minutes", calendar_settings.DEFAULT_DURATION_MINUTES)),
    )
    if conflicts:
        names = "、".join(f"“{c.title}”" for c in conflicts[:3])
        st.warning(t("conflict.detail", count=len(conflicts), names=names))


# ---------------------------------------------------------------
# 重复任务改动的确认弹窗
# ---------------------------------------------------------------

@st.dialog(t('dialog.recurrence_title'))
def recurring_change_dialog():
    change = st.session_state.get(_PENDING_CHANGE_KEY)
    if not change:
        st.write(t('cal.no_pending_changes'))
        if st.button(t('common.close'), key="pending_close"):
            st.rerun()
        return

    task = services.get_task(change["task_id"])
    if task is None:
        st.session_state.pop(_PENDING_CHANGE_KEY, None)
        st.rerun()
        return

    when = change["date"] or ""
    if change.get("time"):
        when += f" {change['time']}"
    if change.get("duration_minutes"):
        when += f"（{change['duration_minutes']} 分钟）"

    st.markdown(f"**{task['title']}**")
    st.caption(t("dev.raw_occurrence", key=change['occurrence_key']))
    st.info(t('cal.new_time', time=when)
            + (t('cal.become_all_day') if change.get("all_day") else ""))

    scope = st.radio(
        t('dialog.recurrence_scope'),
        [t('dialog.recurrence_this_only'), t('dialog.recurrence_whole_series')],
        index=0, key="recurring_change_scope",
    )

    col_ok, col_cancel = st.columns(2)
    with col_ok:
        if st.button(t('common.ok'), key="recurring_change_ok"):
            _apply_recurring_change(change, scope)
            st.session_state.pop(_PENDING_CHANGE_KEY, None)
            st.rerun()
    with col_cancel:
        if st.button(t('common.cancel'), key="recurring_change_cancel"):
            # 不写数据库；下一次 rerun 日历从数据库重建，回到原位置
            st.session_state.pop(_PENDING_CHANGE_KEY, None)
            st.rerun()


def _apply_recurring_change(change, scope):
    try:
        if scope == t('dialog.recurrence_this_only'):
            if change.get("all_day"):
                # 变为全天：仅记录日期（时间保持原样，override_time 留空）
                services.set_occurrence_override(
                    change["task_id"], change["occurrence_key"],
                    date=change["date"], time=None, duration_minutes=None)
            else:
                services.set_occurrence_override(
                    change["task_id"], change["occurrence_key"],
                    date=change["date"], time=change["time"],
                    duration_minutes=change.get("duration_minutes"))
            st.success(t('dialog.recurrence_done_once'))
        else:
            services.move_series_by_occurrence(
                change["task_id"], change["occurrence_key"],
                change["date"], change.get("time"),
                change.get("duration_minutes"))
            st.success(t('dialog.recurrence_done_series'))
    except ValueError as e:
        st.error(t("cal.save_failed", error=e))


def render_pending_recurring_dialog():
    """在 app 主流程中调用：有待处理改动时弹出确认框。"""
    if st.session_state.get(_PENDING_CHANGE_KEY):
        recurring_change_dialog()


# ---------------------------------------------------------------
# 日历设置
# ---------------------------------------------------------------

# ---------------------------------------------------------------
# 显示映射（取值不变）：中文选项值 → 当前语言的显示标签
# ---------------------------------------------------------------
def _fc_locale():
    """FullCalendar 自身的月份/星期名跟随界面语言。"""
    try:
        from i18n import LANGUAGE_EN_US, get_language

        return "en" if get_language() == LANGUAGE_EN_US else "zh-cn"
    except Exception:  # noqa: BLE001
        return "zh-cn"


def _duration_display(minutes):
    if minutes < 60:
        return t("calendar.duration_minutes", n=minutes)
    hours = minutes / 60
    hours = int(hours) if float(hours).is_integer() else hours
    return t("calendar.duration_hours", n=hours)


_DURATION_VALUE_TO_MINUTES = {
    calendar_settings.DURATION_LABELS[m]: m for m in calendar_settings.DURATION_CHOICES
}
_SLOT_LABEL_TO_MINUTES = {f"{m} 分钟": m for m in calendar_settings.SLOT_CHOICES}
_VIEW_VALUE_KEYS = {"月": "cal.view_month", "周": "cal.view_week", "日": "cal.view_day"}


def _duration_label(value):
    minutes = _DURATION_VALUE_TO_MINUTES.get(value)
    return _duration_display(minutes) if minutes else str(value)


def _slot_label(value):
    minutes = _SLOT_LABEL_TO_MINUTES.get(value)
    return f"{minutes} " + t("cal.minutes_unit") if minutes else str(value)


def _view_label(value):
    key = _VIEW_VALUE_KEYS.get(value)
    return t(key) if key else str(value)


def render_calendar_settings(key_prefix="cal_cfg"):
    """日历设置（持久化）。key_prefix 用于避免多处渲染时 key 冲突。"""
    with st.expander(t('cal.settings'), expanded=False):
        cfg = calendar_settings.load_calendar_config()

        dur_labels = [calendar_settings.DURATION_LABELS[m] for m in calendar_settings.DURATION_CHOICES]
        cur_dur_label = calendar_settings.DURATION_LABELS.get(
            int(cfg.get("default_duration_minutes", 60)), "1 小时")
        dur_label = st.selectbox(
            t('cal.default_duration'), dur_labels,
            index=dur_labels.index(cur_dur_label) if cur_dur_label in dur_labels else 3,
            key=f"{key_prefix}_duration",

            format_func=_duration_label,
        )
        slot_label = st.selectbox(
            t('cal.time_grid'),
            [f"{m} 分钟" for m in calendar_settings.SLOT_CHOICES],  # 取值不变
            index=calendar_settings.SLOT_CHOICES.index(
                int(cfg.get("slot_duration_minutes", 30))),
            key=f"{key_prefix}_slot",

            format_func=_slot_label,
        )
        view_pick = st.selectbox(
            t('cal.default_view'),
            ["月", "周", "日"],  # 取值不变
            index=["月", "周", "日"].index(
                calendar_settings.VIEW_LABELS.get(cfg.get("default_view"), "月")),
            key=f"{key_prefix}_view",

            format_func=_view_label,
        )
        click_labels = [t('task.add'), t('cal.action_ignore')]
        click_pick = st.selectbox(
            t('cal.click_blank_action'),
            click_labels,
            index=0 if cfg.get("blank_click_action", "ignore") == "create" else 1,
            key=f"{key_prefix}_click",
        )
        select_pick = st.selectbox(
            t('cal.drag_select_action'),
            click_labels,
            index=0 if cfg.get("drag_select_action", "create") == "create" else 1,
            key=f"{key_prefix}_select",
        )
        last_cb = st.session_state.get(LAST_CALLBACK_KEY)
        st.caption(t("dev.last_cal_action", action=last_cb) if last_cb else t("dev.last_cal_action_none"))
        show_weekends = st.checkbox(t('cal.show_weekends'),
                                    value=bool(cfg.get("show_weekends", True)),
                                    key=f"{key_prefix}_weekends")
        now_indicator = st.checkbox(t('cal.show_now_indicator'),
                                    value=bool(cfg.get("show_now_indicator", True)),
                                    key=f"{key_prefix}_now")

        if st.button(t('cal.save_settings'), key=f"{key_prefix}_save"):
            minutes = None
            for m, lbl in calendar_settings.DURATION_LABELS.items():
                if lbl == dur_label:
                    minutes = m
                    break
            if minutes is None:
                minutes = calendar_settings.DEFAULT_DURATION_MINUTES
            new_cfg = {
                "blank_click_action": "create" if click_pick == t('task.add') else "ignore",
                "drag_select_action": "create" if select_pick == t('task.add') else "ignore",
                "default_duration_minutes": int(minutes),
                "slot_duration_minutes": int(slot_label.split(" ")[0]),
                "default_view": calendar_settings.LABEL_TO_VIEW[view_pick],
                "show_weekends": show_weekends,
                "show_now_indicator": now_indicator,
            }
            calendar_settings.save_calendar_config(new_cfg)
            st.session_state["calendar_view_mode_full"] = new_cfg["default_view"]
            st.success(t('cal.settings_saved'))
            st.rerun()


# ===============================================================
# 基础日历（fallback，保持原有行为）
# ===============================================================

def render_basic_calendar():
    selected = _selected_date()
    mode = st.radio(
        t('cal.view_label'),
        ["月", "日"],  # 取值不变
        index=0 if _calendar_view_mode() == "month" else 1,
        horizontal=True, key="calendar_view_mode_radio",

        format_func=_view_label,
    )
    if (mode == "月") != (_calendar_view_mode() == "month"):
        _set_calendar_view_mode("month" if mode == "月" else "day")
        st.rerun()

    if _calendar_view_mode() == "month":
        render_month_view(selected)
    else:
        render_day_view(selected)


def render_month_view(selected):
    year = selected.year
    month = selected.month

    col_prev, col_title, col_today, col_next = st.columns([1, 4, 1, 1])
    with col_prev:
        if st.button("←", key="month_prev"):
            first = date(year, month, 1)
            _set_selected_date((first - timedelta(days=1)).replace(day=1))
            st.rerun()
    with col_title:
        st.markdown(f"**{dates.month_year(date(int(year), int(month), 1))}**")
    with col_today:
        if st.button(t('common.today'), key="month_today"):
            _set_selected_date(date.today())
            st.rerun()
    with col_next:
        if st.button("→", key="month_next"):
            if month == 12:
                _set_selected_date(date(year + 1, 1, 1))
            else:
                _set_selected_date(date(year, month + 1, 1))
            st.rerun()

    items = services.get_items_for_month(year, month)
    by_day = {}
    for it in items:
        by_day.setdefault(it.date, []).append(it)

    header_cols = st.columns(7)
    for i, wd in enumerate(WEEKDAY_HEADERS):
        header_cols[i].markdown(f"**{wd}**")

    today = date.today()
    for week in _pycalendar.monthcalendar(year, month):
        cols = st.columns(7)
        for i, day_num in enumerate(week):
            with cols[i]:
                if day_num == 0:
                    st.write("")
                    continue
                this_date = date(year, month, day_num)
                day_str = this_date.isoformat()

                label = str(day_num)
                if this_date == today:
                    label = f"{day_num} 今天"
                if st.button(label, key=f"month_date_{day_str}"):
                    _set_selected_date(this_date)
                    _set_calendar_view_mode("day")
                    st.rerun()

                day_items = by_day.get(day_str, [])
                shown = day_items[:3]
                for it in shown:
                    st.caption(_item_label(it))
                extra = len(day_items) - len(shown)
                if extra > 0:
                    st.caption(t("cal.more_items", extra=extra))


def render_day_view(selected):
    today = date.today()

    col_prev, col_title, col_today, col_next = st.columns([1, 4, 1, 1])
    with col_prev:
        if st.button("←", key="day_prev"):
            _set_selected_date(selected - timedelta(days=1))
            st.rerun()
    with col_title:
        title = f"{selected.year} 年 {selected.month} 月 {selected.day} 日"
        if selected == today:
            title += " · 今天"
        st.markdown(f"**{title}**")
    with col_today:
        if st.button(t('common.today'), key="day_today"):
            _set_selected_date(today)
            st.rerun()
    with col_next:
        if st.button("→", key="day_next"):
            _set_selected_date(selected + timedelta(days=1))
            st.rerun()

    items = services.get_items_for_date(selected.isoformat())

    all_day = [it for it in items if not it.time]
    timed = [it for it in items if it.time]
    timed.sort(key=lambda it: it.time)

    st.subheader(t('cal.all_day_items'))
    if not all_day:
        st.caption(t('empty.calendar_all_day'))
    else:
        for it in all_day:
            _render_day_item(it)

    st.subheader(t('cal.timeline'))
    if not timed:
        st.caption(t('empty.calendar'))
    else:
        by_hour = {}
        for it in timed:
            hour = int(it.time[:2])
            by_hour.setdefault(hour, []).append(it)

        for hour in range(24):
            label = f"{hour:02d}:00"
            with st.container():
                st.markdown(f"**{label}**")
                st.markdown("─" * 20)
                for it in by_hour.get(hour, []):
                    _render_day_item(it)


def _render_day_item(item):
    task_id = item.task_id
    is_completed = item.is_completed

    col_check, col_body, col_actions = st.columns([0.5, 7, 2.5])

    with col_check:
        checked = st.checkbox(
            t('common.done'), value=is_completed,
            key=f"calendar_complete_{task_id}_{_safe_key(item.occurrence_key)}",
            label_visibility="collapsed",
        )
        if checked != is_completed:
            if item.is_recurring:
                services.complete_occurrence(task_id, item.occurrence_key, checked)
            else:
                services.toggle_task(task_id, checked)
            st.rerun()

    with col_body:
        label = _item_label(item)
        if item.duration_minutes:
            label += f"（{item.duration_minutes} 分钟）"
        if is_completed:
            st.markdown(f"~~{label}~~")
        else:
            st.markdown(f"**{label}**")
        if item.description:
            st.caption(t("hint.note", text=item.description))
        if item.url:
            st.link_button(f"🔗 {item.url}", item.url)
        if item.is_recurring:
            st.caption(f"🔁 {_recurrence_short(item)}")

    with col_actions:
        col_edit, col_del = st.columns(2)
        with col_edit:
            edit_label = "编辑"
            if st.button(edit_label, key=f"calendar_edit_{task_id}_{_safe_key(item.occurrence_key)}"):
                # 把 occurrence_key 一起带上：重复任务才能选「仅修改这一次」
                _start_edit_series(task_id, item.occurrence_key)
                st.rerun()
        with col_del:
            del_label = "删除系列" if item.is_recurring else "删除"
            if st.button(del_label, key=f"calendar_delete_{task_id}_{_safe_key(item.occurrence_key)}"):
                if item.is_recurring:
                    st.session_state["_delete_occurrence"] = (task_id, item.occurrence_key)
                else:
                    services.remove_task(task_id)
                st.rerun()


def _recurrence_short(item):
    from services import RECURRENCE_FREQUENCY_LABELS
    freq = item.recurrence_frequency
    if freq not in RECURRENCE_FREQUENCY_LABELS:
        return freq or ""
    base = RECURRENCE_FREQUENCY_LABELS[freq]
    interval = int(item.recurrence_interval or 1)
    if freq == "daily":
        return "每天" if interval == 1 else f"每{interval}天"
    if freq == "weekly":
        return "每周" if interval == 1 else f"每{interval}周"
    if freq == "monthly":
        return "每月" if interval == 1 else f"每{interval}个月"
    if freq == "yearly":
        return "每年" if interval == 1 else f"每{interval}年"
    return base
