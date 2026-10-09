"""重复规则计算引擎（纯计算，不创建数据库记录）。

任务只有一份（tasks 表），重复任务是一条「重复模板」：
- date = 首次发生日期（anchor date）
- time = 发生时间
- 通过 recurrence_frequency / interval / end_type / end_date 描述规则。

本模块只负责：给定任务和日期范围，计算范围内应发生的 occurrence。
调用方传 range_start / range_end，因此不会无限生成。
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta


@dataclass
class Occurrence:
    """一次发生。"""
    task_id: int
    date: str          # YYYY-MM-DD
    time: str          # HH:MM，可能为 None（无具体时间）


VALID_FREQUENCIES = {
    "hourly", "daily", "workday", "weekend",
    "weekly", "monthly", "quarterly", "yearly",
}


def make_occurrence_key(date_value, time_value=None):
    """生成稳定唯一的 occurrence 标识。

    无时间：YYYY-MM-DD
    有时间：YYYY-MM-DDTHH:MM
    例如 2026-10-12、2026-10-12T09:30
    """
    d = _to_date(date_value)
    if d is None:
        raise ValueError("occurrence_key 需要有效日期")
    if time_value:
        return f"{d.isoformat()}T{time_value}"
    return d.isoformat()


def parse_occurrence_key(key):
    """解析 occurrence_key，返回 (date, time) 元组。

    与 make_occurrence_key 互为一致的生成 / 解析逻辑：
    2026-10-05       → ("2026-10-05", None)
    2026-10-05T18:00 → ("2026-10-05", "18:00")
    """
    if key is None:
        return None, None
    key = str(key)
    if "T" in key:
        d, t = key.split("T", 1)
        return d, t
    return key, None


def get_next_occurrence(task, after_date):
    """返回指定日期之后最近的一次 occurrence。

    after_date：YYYY-MM-DD 字符串或 date 对象，表示「从这一天之后」开始找。
    尊重 frequency / interval / end_date；没有未来 occurrence 返回 None。
    非重复任务不适用（返回 None）。
    """
    if not _get(task, "is_recurring"):
        return None
    anchor = _to_date(_get(task, "date"))
    if anchor is None:
        return None

    after = _to_date(after_date)
    if after is None:
        after = anchor

    frequency = _get(task, "recurrence_frequency")
    interval = int(_get(task, "recurrence_interval") or 1)
    if interval < 1:
        interval = 1

    end_type = _get(task, "recurrence_end_type")
    end_limit = _to_date(_get(task, "recurrence_end_date")) if end_type == "on_date" else None
    t = _get(task, "time")

    # 找到第一个 > after 的 occurrence；无上限时用足够大的上限避免死循环
    if end_limit is not None:
        upper = end_limit
    else:
        upper = after + timedelta(days=366 * 5)  # 5 年上限，防御性

    results = get_occurrences_for_range(task, after + timedelta(days=1), upper)
    return results[0] if results else None


def _add_months(d: date, months: int) -> date:
    """在日期 d 上增加 months 个月，月末安全。

    例如 2026-01-31 + 1 个月 = 2026-02-28（不会漂移成 28 日）。
    始终保持「原始目标天数」为 anchor day，再按月安全截断。
    """
    month_index = d.year * 12 + (d.month - 1) + months
    year = month_index // 12
    month = month_index % 12 + 1
    anchor_day = d.day
    # 该月最大天数
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last_day = (next_month - timedelta(days=1)).day
    return date(year, month, min(anchor_day, last_day))


def _clamp_day(year: int, month: int, day: int) -> date:
    """构造日期并把 day 限制到该月最后一天。"""
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    last_day = (next_month - timedelta(days=1)).day
    return date(year, month, min(day, last_day))


def _to_date(value):
    """把 YYYY-MM-DD 字符串或 date 对象转为 date。"""
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        return date.fromisoformat(value)
    return None


def _get(task, key, default=None):
    """从 sqlite3.Row 或 dict 中取值。"""
    try:
        val = task[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if val is None else val


def get_occurrences_for_range(task, range_start, range_end):
    """计算任务在 [range_start, range_end] 闭区间内应发生的所有 occurrence。

    task：sqlite3.Row 或 dict，需含 id/date/time/is_recurring 及 recurrence 字段。
    range_start / range_end：YYYY-MM-DD 字符串或 date 对象。

    返回 Occurrence 列表（按时间升序）。不写数据库。
    """
    start = _to_date(range_start)
    end = _to_date(range_end)
    if start is None or end is None or start > end:
        return []

    task_id = task["id"]

    # 非重复任务：若 date 在范围内，返回单个 occurrence
    if not _get(task, "is_recurring"):
        d = _to_date(_get(task, "date"))
        if d is not None and start <= d <= end:
            return [Occurrence(task_id=task_id, date=d.isoformat(), time=_get(task, "time"))]
        return []

    anchor = _to_date(_get(task, "date"))
    if anchor is None:
        return []  # 重复任务必须有首次日期（业务层已约束，这里防御）

    frequency = _get(task, "recurrence_frequency")
    interval = int(_get(task, "recurrence_interval") or 1)
    if interval < 1:
        interval = 1

    end_type = _get(task, "recurrence_end_type")
    end_limit = _to_date(_get(task, "recurrence_end_date")) if end_type == "on_date" else None
    stop_before = _get(task, "recurrence_stop_before")
    stop_before_dt = _occurrence_key_to_datetime(stop_before) if stop_before else None

    # 有效截止 = min(请求范围末尾, 规则截止, stop_before 前一刻)
    effective_end = end
    if end_limit is not None:
        effective_end = min(effective_end, end_limit)
    if stop_before_dt is not None:
        # stop_before 是第一个不再生成的 occurrence，取它的日期作为截止
        stop_date = stop_before_dt.date()
        effective_end = min(effective_end, stop_date)

    t = _get(task, "time")

    if frequency == "hourly":
        results = _hourly_occurrences(task_id, anchor, t, interval, start, effective_end)
    else:
        results = _date_occurrences(task_id, anchor, frequency, interval, start, effective_end, t)

    # 过滤掉 >= stop_before 的 occurrence（精确到时间）
    if stop_before_dt is not None:
        results = [o for o in results if _occurrence_to_datetime(o) < stop_before_dt]

    results.sort(key=lambda o: (o.date, o.time or ""))
    return results


def _occurrence_to_datetime(occ):
    """把 Occurrence 转成 datetime，用于精确比较（含时间）。"""
    d = _to_date(occ.date)
    if occ.time:
        hh, mm = int(occ.time[:2]), int(occ.time[3:5])
        return datetime.combine(d, time(hh, mm))
    return datetime.combine(d, time(0, 0))


def _occurrence_key_to_datetime(key):
    """把 occurrence_key 转成 datetime。"""
    d_str, t_str = parse_occurrence_key(key)
    d = _to_date(d_str)
    if d is None:
        return None
    if t_str:
        hh, mm = int(t_str[:2]), int(t_str[3:5])
        return datetime.combine(d, time(hh, mm))
    return datetime.combine(d, time(0, 0))


def _date_occurrences(task_id, anchor, frequency, interval, start, end, t):
    out = []
    cur = anchor

    if frequency == "daily":
        while cur <= end:
            if cur >= start:
                out.append(Occurrence(task_id=task_id, date=cur.isoformat(), time=t))
            cur += timedelta(days=interval)

    elif frequency == "weekly":
        # 保持与 anchor 星期几一致
        while cur <= end:
            if cur >= start:
                out.append(Occurrence(task_id=task_id, date=cur.isoformat(), time=t))
            cur += timedelta(weeks=interval)

    elif frequency == "monthly":
        months = 0
        while True:
            cur = _add_months(anchor, months)
            if cur > end:
                break
            if cur >= start:
                out.append(Occurrence(task_id=task_id, date=cur.isoformat(), time=t))
            months += interval

    elif frequency == "quarterly":
        months = 0
        while True:
            cur = _add_months(anchor, months)
            if cur > end:
                break
            if cur >= start:
                out.append(Occurrence(task_id=task_id, date=cur.isoformat(), time=t))
            months += 3 * interval

    elif frequency == "yearly":
        # 闰年 2/29 安全：非闰年落到 2/28
        years = 0
        while True:
            cur = _clamp_day(anchor.year + years, anchor.month, anchor.day)
            if cur > end:
                break
            if cur >= start:
                out.append(Occurrence(task_id=task_id, date=cur.isoformat(), time=t))
            years += interval

    elif frequency == "workday":
        # 规则：从 anchor（含）开始取工作日（周一~周五）。若 anchor 本身是周末，
        # 从 anchor 之后的第一个工作日开始。
        cur = anchor
        while cur.weekday() >= 5:  # 5=周六 6=周日
            cur += timedelta(days=1)
        while cur <= end:
            if cur.weekday() < 5:
                if cur >= start:
                    out.append(Occurrence(task_id=task_id, date=cur.isoformat(), time=t))
            cur += timedelta(days=1)

    elif frequency == "weekend":
        # 规则：从 anchor（含）开始取周六、周日。若 anchor 是工作日，
        # 从 anchor 之后第一个周六开始。
        cur = anchor
        while cur.weekday() not in (5, 6):
            cur += timedelta(days=1)
        while cur <= end:
            if cur.weekday() in (5, 6):
                if cur >= start:
                    out.append(Occurrence(task_id=task_id, date=cur.isoformat(), time=t))
            cur += timedelta(days=1)

    return out


def _hourly_occurrences(task_id, anchor, t, interval, start, end):
    """hourly 必须用 datetime 运算以支持跨午夜。"""
    if t is None:
        return []
    hh, mm = int(t[:2]), int(t[3:5])
    base = datetime.combine(anchor, time(hh, mm))
    out = []
    step = timedelta(hours=interval)
    cur = base
    # 从 anchor 向前找，覆盖 [start, end] 整个区间
    # 先走到 start 当天 00:00 或之前
    start_dt = datetime.combine(start, time(0, 0))
    end_dt = datetime.combine(end, time(23, 59, 59))
    # 回退到不超过 start_dt 的最近一个发生点
    if cur > start_dt:
        diff = (cur - start_dt).total_seconds() // step.total_seconds()
        cur = cur - step * int(diff)
    while cur <= end_dt:
        if start_dt <= cur <= end_dt:
            out.append(Occurrence(
                task_id=task_id,
                date=cur.date().isoformat(),
                time=cur.strftime("%H:%M"),
            ))
        cur += step
    return out
