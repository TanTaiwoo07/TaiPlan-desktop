"""可复用 UI 组件（只渲染与回调，不含业务逻辑 / 不写 SQL）。"""

import html

import streamlit as st

from taiplan.i18n import dates, t

from taiplan.ui import icons, theme

DURATION_KEYS = {15: "duration.15", 30: "duration.30", 45: "duration.45",
                 60: "duration.60", 90: "duration.90", 120: "duration.120",
                 180: "duration.180"}


def duration_label(minutes):
    """时长文案（按当前语言解析）。未知时长回落到 {count} 分钟/分钟。"""
    try:
        minutes = int(minutes)
    except (TypeError, ValueError):
        return str(minutes)
    key = DURATION_KEYS.get(minutes)
    return t(key) if key else t("duration.minutes", count=minutes)


def _esc(text):
    return html.escape(str(text or ""))


# ---------------------------------------------------------------
# 页面头部 / 分组
# ---------------------------------------------------------------

def page_header(title, subtitle="", action_label=None, action_key=None,
                action_icon=None):
    """统一页面头部：左标题+副标题，右主操作按钮。返回按钮是否被点击。"""
    left, right = st.columns([6, 1.7], vertical_alignment="center")
    with left:
        with st.container(key="pagehead"):
            st.markdown(
                f'<div class="page-title">{_esc(title)}</div>'
                f'<div class="page-sub">{_esc(subtitle)}</div>',
                unsafe_allow_html=True,
            )
    clicked = False
    with right:
        if action_label:
            with st.container(key="primaryaction"):
                clicked = st.button(action_label, key=action_key, icon=action_icon,
                                    use_container_width=True)
    return clicked


def section_title(title, count=None):
    extra = f' <span class="section-count">{count}</span>' if count is not None else ""
    st.markdown(f'<div class="section-title">{_esc(title)}{extra}</div>',
                unsafe_allow_html=True)


def group_label(title, count=None):
    extra = f" · {count}" if count is not None else ""
    st.markdown(f'<div class="group-label">{_esc(title)}{_esc(extra)}</div>',
                unsafe_allow_html=True)


def empty_state(title, hint="", action_label=None, action_key=None):
    """友好空状态。返回按钮是否被点击。"""
    st.markdown(
        f'<div class="empty-state"><div class="empty-title">{_esc(title)}</div>'
        f'<div class="empty-hint">{_esc(hint)}</div></div>',
        unsafe_allow_html=True,
    )
    if action_label:
        cols = st.columns([3, 2, 3])
        with cols[1]:
            return st.button(action_label, key=action_key, use_container_width=True)
    return False


def overdue_banner(count, key=None):
    """逾期提示条（淡橙，不大面积鲜红）。返回是否展开。"""
    st.markdown(f'<div class="overdue-banner">{_esc(t("overdue.banner", count=int(count)))}</div>',
                unsafe_allow_html=True)
    return st.toggle(t("common.expand"), key=key or "overdue_toggle") if count else False


def keyboard_shortcuts_js():
    """全局快捷键（第一组）：Ctrl+N / Cmd+N → 点击「新建任务」按钮。

    一次性注入零依赖 JS；幂等（重复渲染不叠加监听器）。
    Esc 关闭对话框由 Streamlit 原生支持，不在此处理。
    """
    st.markdown(
        """<script id="taiplan-hotkeys">
        (function () {
          if (window.__taiplanHotkeys) return;
          window.__taiplanHotkeys = true;
          document.addEventListener('keydown', function (ev) {
            if ((ev.ctrlKey || ev.metaKey) && !ev.shiftKey && !ev.altKey
                && (ev.key === 'n' || ev.key === 'N')) {
              const btn = [...document.querySelectorAll('.st-key-primaryaction button')]
                .find(b => !b.disabled);
              if (btn) { ev.preventDefault(); btn.click(); }
            }
          });
        })();
        </script>""",
        unsafe_allow_html=True,
    )


def progress_line(done, total):
    ratio = (done / total) if total else 0.0
    st.progress(ratio)
    st.markdown(
        f'<div class="task-meta">'
        f'{_esc(t("task.meta_progress", done=done, total=total, percent=int(round(ratio * 100))))}'
        f'</div>',
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------
# 任务卡
# ---------------------------------------------------------------

def _format_date(date_str):
    """ISO 日期 → 本地化文案（今天/明天/昨天，或 10月5日 · 周一 / Oct 5 · Monday）。"""
    if not date_str:
        return ""
    try:
        from datetime import date as _d
        d = _d.fromisoformat(date_str)
    except (TypeError, ValueError):
        return str(date_str)
    return dates.human_date(d)


def _end_time(time_str, duration):
    if not time_str:
        return ""
    try:
        hh, mm = int(time_str[:2]), int(time_str[3:5])
        total = hh * 60 + mm + int(duration or 0)
        return f"{(total // 60) % 24:02d}:{total % 60:02d}"
    except (TypeError, ValueError):
        return ""


def meta_html(item, show_date=True, conflict_label=None, is_overdue=False):
    """构建任务卡的元信息行（纯文本 + Unicode 符号，保持安静）。"""
    chips = []
    if item.is_completed:
        chips.append(f'<span class="chip done">{_esc(t("status.done"))}</span>')
    if item.priority == "urgent" and not item.is_completed:
        chips.append(f'<span class="chip urgent">{_esc(t("priority.urgent"))}</span>')
    elif item.priority == "low":
        chips.append(f'<span class="chip low">{_esc(t("priority.low"))}</span>')

    bits = []
    if show_date and item.date:
        bits.append(_esc(_format_date(item.date)))
    if item.time:
        end = _end_time(item.time, item.duration_minutes)
        span = f"{item.time}–{end}" if end else item.time
        dur = item.duration_minutes
        if dur and dur not in (60,) and not end:
            span += f" ({duration_label(dur)})"
        bits.append(_esc(span))
    elif item.date and not show_date:
        pass
    if is_overdue:
        bits.append(f'<span class="chip overdue">{_esc(t("status.overdue"))}</span>')
    if conflict_label:
        bits.append(f'<span class="chip conflict">'
                    f'{_esc(t("conflict.chip", name=conflict_label))}</span>')

    meta = " · ".join(bits)
    recur = ""
    if item.is_recurring:
        recur = f' · {_esc(t("task.repeat_badge"))}'
    line = f'<div class="task-meta">{"".join(chips)}{meta}{recur}</div>' if (chips or meta or recur) else ""
    return line


def card_html(item, show_date=True, conflict_label=None, is_overdue=False):
    title_cls = "task-title done" if item.is_completed else "task-title"
    out = f'<div class="{title_cls}">{_esc(item.title)}</div>'
    out += meta_html(item, show_date, conflict_label, is_overdue)
    if item.description:
        desc = str(item.description).strip().replace("\n", " ")
        if len(desc) > 90:
            desc = desc[:90] + "…"
        out += f'<div class="task-desc">{_esc(desc)}</div>'
    return out


def task_card(item, key_prefix, *, on_toggle=None, on_edit=None, on_delete=None,
              on_move=None, show_date=True, conflict_label=None, is_overdue=False,
              move_default=None, danger_confirm=False):
    """统一任务卡。

    - 正常状态只显示 ○ 与 ⋯，操作收进 popover（编辑 / 完成 / 移动日期 / 删除）
    - 紧急：淡红「紧急」标签，不整卡变红
    """
    with st.container(border=True):
        cols = st.columns([0.5, 7.4, 1.1], vertical_alignment="top")
        with cols[0]:
            done = st.checkbox(t("task.complete"), value=bool(item.is_completed),
                               key=f"chk_{key_prefix}", label_visibility="collapsed")
            if on_toggle is not None and done != bool(item.is_completed):
                on_toggle(item, done)
                st.rerun()
        with cols[1]:
            st.markdown(card_html(item, show_date, conflict_label, is_overdue),
                        unsafe_allow_html=True)
            if item.url:
                st.markdown(f"[{t('task.open_link')}]({item.url})")
        with cols[2]:
            with st.popover("⋯", key=f"menu_{key_prefix}", use_container_width=False):
                if on_edit is not None and st.button(t("task.edit"), key=f"m_edit_{key_prefix}",
                                                     icon=icons.EDIT,
                                                     use_container_width=True):
                    on_edit(item)
                    st.rerun()
                if on_toggle is not None:
                    label = t("task.uncomplete") if item.is_completed else t("task.complete")
                    if st.button(label, key=f"m_done_{key_prefix}",
                                 icon=icons.DONE, use_container_width=True):
                        on_toggle(item, not item.is_completed)
                        st.rerun()
                if on_move is not None and item.date is not None:
                    from datetime import date as _d
                    try:
                        current = _d.fromisoformat(item.date)
                    except (TypeError, ValueError):
                        current = move_default or _d.today()
                    new_date = st.date_input(
                        t("task.move"), value=move_default or current,
                        key=f"m_date_{key_prefix}")
                    if st.button(t("task.move_date"), key=f"m_move_{key_prefix}",
                                 use_container_width=True):
                        on_move(item, new_date)
                        st.rerun()
                if on_delete is not None:
                    st.divider()
                    if st.button(t("task.delete"), key=f"m_del_{key_prefix}",
                                 icon=icons.DELETE, use_container_width=True):
                        on_delete(item)
                        st.rerun()


# ---------------------------------------------------------------
# 反馈
# ---------------------------------------------------------------

def toast_ok(message):
    st.toast(message, icon=icons.DONE)


def toast_info(message):
    st.toast(message)


def friendly_error(message=None, detail=None):
    """正式模式只显示友好文案；detail 仅用于开发者模式。"""
    st.error(message if message is not None else t("error.generic"))
    if detail and st.session_state.get("developer_mode"):
        with st.expander(t("common.developer_detail")):
            st.code(str(detail))


def status_row(label, value, ok=None):
    dot = "🟢" if ok is True else ("🔴" if ok is False else "")
    st.markdown(f'<div class="window-status">{_esc(label)}：{dot} {_esc(value)}</div>',
                unsafe_allow_html=True)


__all__ = [
    "page_header", "section_title", "group_label", "empty_state", "overdue_banner", "duration_label",
    "progress_line", "task_card", "card_html", "meta_html",
    "toast_ok", "toast_info", "friendly_error", "status_row",
    "theme", "icons",
]
