"""业务逻辑层。

作为 app.py 与 database.py 之间的业务层：
app.py 不直接执行 SQL，全部通过本模块转发，并由本模块承担基础验证。
任务分组规则也集中在这里（Today / Inbox / Overdue / Upcoming / Completed）。
"""

from datetime import datetime
from datetime import date as _date
from datetime import time as _time
from datetime import timedelta
from urllib.parse import urlparse

from taiplan.database import (
    add_task,
    delete_task,
    get_all_tasks,
    get_cancelled_occurrence_keys_for_tasks,
    get_completed_occurrences,
    get_completed_occurrence_keys_for_tasks,
    get_task_by_id,
    set_occurrence_completed,
    set_occurrence_state,
    set_recurrence_stop_before,
    set_task_completed,
    update_task,
    delete_occurrence_states_for_task,
    set_occurrence_override as db_set_occurrence_override,
    delete_occurrence_override as db_delete_occurrence_override,
    get_occurrence_override as db_get_occurrence_override,
    get_occurrence_overrides_for_tasks,
    get_occurrence_overrides_in_range,
)

from taiplan import recurrence
from taiplan.models import TaskOccurrence

VALID_PRIORITIES = {"low", "normal", "urgent"}

# 时间块持续时间（分钟）
DEFAULT_DURATION_MINUTES = 60
MIN_DURATION_MINUTES = 5
MAX_DURATION_MINUTES = 1440   # 单次时间块最多 24 小时


def _validate_duration(duration_minutes, date_val, time_val):
    """校验持续时间。

    只有在 date + time 均存在时才允许非空 duration；
    范围 5～1440 分钟，超出报错。未提供返回 None。
    """
    if duration_minutes is None or duration_minutes == "":
        return None
    if date_val is None or time_val is None:
        return None
    try:
        minutes = int(duration_minutes)
    except (TypeError, ValueError):
        raise ValueError("持续时间必须是整数分钟")
    if minutes < MIN_DURATION_MINUTES or minutes > MAX_DURATION_MINUTES:
        raise ValueError(
            f"持续时间必须在 {MIN_DURATION_MINUTES}～{MAX_DURATION_MINUTES} 分钟之间")
    return minutes


def _validate_title(title):
    """去除首尾空格，不能为空。"""
    title = (title or "").strip()
    if not title:
        raise ValueError("任务内容不能为空")
    return title


def _validate_priority(priority):
    """priority 只允许 low / normal / urgent。"""
    if priority not in VALID_PRIORITIES:
        raise ValueError("非法的优先级值")
    return priority


def _validate_url(url):
    """URL 可选；非空时至少校验 scheme 为 http/https 且 netloc 非空。"""
    if url is None:
        return None
    url = str(url).strip()
    if not url:
        return None
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("请输入有效的 http:// 或 https:// URL")
    return url


def _validate_and_normalize_recurrence(date_val, time_val, is_recurring,
                                       recurrence_frequency, recurrence_interval,
                                       recurrence_end_type, recurrence_end_date):
    """验证并规范化重复规则。

    返回 (is_recurring, frequency, interval, end_type, end_date) 规范化后的元组。
    关闭重复时清理旧参数。
    """
    if not is_recurring:
        # 关闭重复：规范化清空旧规则
        return 0, None, 1, None, None

    # 启用重复必须有开始日期
    if date_val is None:
        raise ValueError("重复任务必须设置日期。")

    frequency = recurrence_frequency
    if frequency not in recurrence.VALID_FREQUENCIES:
        raise ValueError("非法的重复周期")

    try:
        interval = int(recurrence_interval)
    except (TypeError, ValueError):
        interval = 1
    if interval < 1:
        raise ValueError("重复间隔必须 >= 1")

    # workday / weekend 固定 interval = 1
    if frequency in ("workday", "weekend"):
        interval = 1

    # hourly 必须有具体时间
    if frequency == "hourly" and time_val is None:
        raise ValueError("每小时重复需要设置具体时间。")

    # 结束条件
    end_type = recurrence_end_type
    if end_type not in ("never", "on_date", None):
        raise ValueError("非法的结束条件")

    end_date = None
    if end_type == "on_date":
        if recurrence_end_date is None:
            raise ValueError("请设置结束日期。")
        end_date = _normalize_date(recurrence_end_date)
        if end_date is None:
            raise ValueError("请设置结束日期。")
        # 结束日期不能早于首次日期
        if end_date < _normalize_date(date_val):
            raise ValueError("结束日期不能早于首次日期。")
    else:
        end_type = "never"
        end_date = None

    return 1, frequency, interval, end_type, end_date


def _normalize_optional(value):
    """空字符串转为 None，统一「未设置」的存储约定。"""
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


def _normalize_date(date_val):
    """date 统一转为 YYYY-MM-DD 字符串；未设置返回 None。"""
    if date_val is None:
        return None
    if isinstance(date_val, _date):
        return date_val.strftime("%Y-%m-%d")
    return _normalize_optional(date_val)


def _normalize_time(time_val):
    """time 统一转为 HH:MM 字符串；未设置返回 None。"""
    if time_val is None:
        return None
    if isinstance(time_val, _time):
        return time_val.strftime("%H:%M")
    return _normalize_optional(time_val)


# ---------------------------------------------------------------
# 基础业务操作
# ---------------------------------------------------------------

def create_task(title, description=None, url=None, date=None, time=None, priority="normal",
                is_recurring=False, recurrence_frequency=None, recurrence_interval=1,
                recurrence_end_type=None, recurrence_end_date=None,
                duration_minutes=None):
    """新增任务（含验证与格式转换，含重复规则与持续时间）。"""
    title = _validate_title(title)
    priority = _validate_priority(priority)
    url = _validate_url(url)
    date_str = _normalize_date(date)
    time_str = _normalize_time(time)
    duration = _validate_duration(duration_minutes, date_str, time_str)

    is_recurring, frequency, interval, end_type, end_date = \
        _validate_and_normalize_recurrence(
            date_str, time_str, is_recurring, recurrence_frequency,
            recurrence_interval, recurrence_end_type, recurrence_end_date)

    add_task(
        title,
        _normalize_optional(description),
        url,
        date_str,
        time_str,
        priority,
        is_recurring=is_recurring,
        recurrence_frequency=frequency,
        recurrence_interval=interval,
        recurrence_end_type=end_type,
        recurrence_end_date=end_date,
        duration_minutes=duration,
    )


def get_tasks():
    """获取全部任务。"""
    return get_all_tasks()


def get_task(task_id):
    """获取单个任务；不存在返回 None。"""
    return get_task_by_id(task_id)


def edit_task(task_id, title, description=None, url=None, date=None, time=None, priority="normal",
              is_recurring=False, recurrence_frequency=None, recurrence_interval=1,
              recurrence_end_type=None, recurrence_end_date=None,
              duration_minutes=None):
    """编辑任务（含验证与格式转换，含重复规则与持续时间）。"""
    title = _validate_title(title)
    priority = _validate_priority(priority)
    url = _validate_url(url)
    date_str = _normalize_date(date)
    time_str = _normalize_time(time)
    duration = _validate_duration(duration_minutes, date_str, time_str)

    is_recurring, frequency, interval, end_type, end_date = \
        _validate_and_normalize_recurrence(
            date_str, time_str, is_recurring, recurrence_frequency,
            recurrence_interval, recurrence_end_type, recurrence_end_date)

    update_task(
        task_id,
        title,
        _normalize_optional(description),
        url,
        date_str,
        time_str,
        priority,
        is_recurring=is_recurring,
        recurrence_frequency=frequency,
        recurrence_interval=interval,
        recurrence_end_type=end_type,
        recurrence_end_date=end_date,
        duration_minutes=duration,
    )


def toggle_task(task_id, completed):
    """完成任务 / 取消完成。"""
    set_task_completed(task_id, completed)


def remove_task(task_id):
    """删除任务。"""
    delete_task(task_id)


# ---------------------------------------------------------------
# 任务分组逻辑
# 所有状态都通过已有字段动态判断，不写入数据库。
# ---------------------------------------------------------------

def _today_str():
    """返回服务器当前本地日期字符串 YYYY-MM-DD。"""
    return datetime.now().date().isoformat()


def _is_completed(task):
    return bool(task["is_completed"])


def _time_key(task):
    """用于排序：无 time 的任务排在有 time 的任务之前。"""
    return task["time"] or ""


def get_today_tasks():
    """今日未完成事项：date == 今天 且未完成。"""
    today = _today_str()
    tasks = [t for t in get_all_tasks() if t["date"] == today and not _is_completed(t)]
    tasks.sort(key=_time_key)
    return tasks


def get_inbox_tasks():
    """待办中转站：date IS NULL 且未完成（可只有 time 没有 date）。"""
    tasks = [t for t in get_all_tasks() if t["date"] is None and not _is_completed(t)]
    tasks.sort(key=lambda t: (t["created_at"] or "", t["id"]))
    return tasks


def get_overdue_tasks():
    """已逾期：date < 今天 且未完成。"""
    today = _today_str()
    tasks = [t for t in get_all_tasks() if t["date"] and t["date"] < today and not _is_completed(t)]
    tasks.sort(key=lambda t: (t["date"], _time_key(t)))
    return tasks


def get_upcoming_tasks():
    """后续安排：date > 今天 且未完成，按日期升序、时间升序。"""
    today = _today_str()
    tasks = [t for t in get_all_tasks() if t["date"] and t["date"] > today and not _is_completed(t)]
    tasks.sort(key=lambda t: (t["date"], _time_key(t)))
    return tasks


def get_completed_tasks():
    """已完成任务：is_completed == 1，按 completed_at 倒序。"""
    tasks = [t for t in get_all_tasks() if _is_completed(t)]
    tasks.sort(key=lambda t: (t["completed_at"] or ""), reverse=True)
    return tasks


def get_today_summary():
    """今日进度统计。

    total 包括今天已完成和未完成的任务。
    返回 dict：total / completed / remaining / progress。
    """
    today = _today_str()
    all_today = [t for t in get_all_tasks() if t["date"] == today]
    total = len(all_today)
    completed = sum(1 for t in all_today if _is_completed(t))
    remaining = total - completed
    progress = (completed / total) if total else 0.0
    return {
        "total": total,
        "completed": completed,
        "remaining": remaining,
        "progress": progress,
    }


def is_task_time_passed(task):
    """判断「今日」任务是否已过指定时间。

    仅当 date == 今天 且 time < 当前时间时返回 True。
    该状态动态计算，不写入数据库。
    """
    if not task["date"] or not task["time"]:
        return False
    if task["date"] != _today_str():
        return False
    now = datetime.now().strftime("%H:%M")
    return task["time"] < now


# ---------------------------------------------------------------
# 日历查询逻辑（Calendar 只是 tasks 的另一种投影，不复制数据）
# ---------------------------------------------------------------

def get_tasks_for_date(date_value):
    """返回指定日期（YYYY-MM-DD）的所有任务，含已完成和未完成。"""
    target = _normalize_date(date_value)
    if target is None:
        return []
    tasks = [t for t in get_all_tasks() if t["date"] == target]
    # 无具体时间的在前，有时间的按 time 升序
    tasks.sort(key=lambda t: (1 if t["time"] else 0, t["time"] or "", t["id"]))
    return tasks


def get_tasks_for_month(year, month):
    """返回指定月份（date 不为 NULL 且属于该月）的所有任务。"""
    prefix = f"{int(year):04d}-{int(month):02d}-"
    tasks = [t for t in get_all_tasks() if t["date"] and t["date"].startswith(prefix)]
    tasks.sort(key=lambda t: (t["date"], 1 if t["time"] else 0, t["time"] or "", t["id"]))
    return tasks


# ---------------------------------------------------------------
# 重复规则展示（中文文字，不生成未来实例）
# ---------------------------------------------------------------

RECURRENCE_FREQUENCY_LABELS = {
    "hourly": "每小时",
    "daily": "每天",
    "workday": "工作日",
    "weekend": "周末",
    "weekly": "每周",
    "monthly": "每月",
    "quarterly": "每3个月",
    "yearly": "每年",
}


def recurrence_description(task):
    """返回任务重复规则的中文简要描述。

    例如：🔁 每天 / 🔁 每2周 / 🔁 每周 · 至 2026-12-31
    非重复任务返回 None。
    task 可以是 sqlite3.Row 或 dict。
    """
    def _get(key, default=None):
        try:
            val = task[key]
        except (KeyError, IndexError, TypeError):
            return default
        return default if val is None else val

    if not _get("is_recurring"):
        return None
    frequency = _get("recurrence_frequency")
    if frequency not in RECURRENCE_FREQUENCY_LABELS:
        return None

    base = RECURRENCE_FREQUENCY_LABELS[frequency]
    interval = int(_get("recurrence_interval") or 1)

    if frequency == "hourly":
        if interval == 1:
            label = "每小时"
        else:
            label = f"每{interval}小时"
    elif frequency == "daily":
        label = "每天" if interval == 1 else f"每{interval}天"
    elif frequency == "weekly":
        label = "每周" if interval == 1 else f"每{interval}周"
    elif frequency == "monthly":
        label = "每月" if interval == 1 else f"每{interval}个月"
    elif frequency == "quarterly":
        label = "每3个月" if interval == 1 else f"每{3 * interval}个月"
    elif frequency == "yearly":
        label = "每年" if interval == 1 else f"每{interval}年"
    else:
        label = base

    end_type = _get("recurrence_end_type")
    end_date = _get("recurrence_end_date")
    if end_type == "on_date" and end_date:
        return f"🔁 {label} · 至 {end_date}"
    return f"🔁 {label}"


# ---------------------------------------------------------------
# 统一投影：普通任务 + 重复 occurrence → TaskOccurrence
# ---------------------------------------------------------------

def _task_duration(task):
    """读取 task 的 duration_minutes（无时间任务一律 None）。"""
    try:
        val = task["duration_minutes"]
    except (KeyError, IndexError, TypeError):
        return None
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _task_to_occurrence(task, completed_keys=None):
    """把一条非重复 task 转成 TaskOccurrence。"""
    return TaskOccurrence(
        task_id=task["id"],
        occurrence_key=None,
        title=task["title"],
        date=task["date"],
        time=task["time"],
        description=task["description"],
        url=task["url"],
        priority=task["priority"],
        is_recurring=False,
        is_completed=bool(task["is_completed"]),
        completed_at=task["completed_at"],
        duration_minutes=_task_duration(task),
    )


def _occurrence_to_task_occurrence(task, occ, completed_keys=None, override=None):
    """把一次重复 occurrence 转成 TaskOccurrence，并应用 override。

    occurrence_key 始终取 **原始** base occurrence（稳定 identity，不随移动改变）；
    date / time / duration 则可能被 override 替换。
    """
    occurrence_key = recurrence.make_occurrence_key(occ.date, occ.time)
    done = completed_keys.get(task["id"], set()) if completed_keys else set()

    date_val = occ.date
    time_val = occ.time
    duration = _task_duration(task)
    override_date = None
    override_time = None

    if override is not None:
        if override["override_date"]:
            date_val = override["override_date"]
            override_date = override["override_date"]
        if override["override_time"]:
            time_val = override["override_time"]
            override_time = override["override_time"]
        if override["override_duration_minutes"] is not None:
            duration = int(override["override_duration_minutes"])

    # 全天（无时间）不应带 duration
    if time_val is None:
        duration = None

    return TaskOccurrence(
        task_id=task["id"],
        occurrence_key=occurrence_key,
        title=task["title"],
        date=date_val,
        time=time_val,
        description=task["description"],
        url=task["url"],
        priority=task["priority"],
        is_recurring=True,
        is_completed=occurrence_key in done,
        recurrence_frequency=task["recurrence_frequency"],
        recurrence_interval=task["recurrence_interval"] or 1,
        recurrence_end_type=task["recurrence_end_type"],
        recurrence_end_date=task["recurrence_end_date"],
        duration_minutes=duration,
        override_date=override_date,
        override_time=override_time,
    )


def get_items_for_date(date_value):
    """返回指定日期的所有投影项（含被 override 移动到该日的 occurrence）。"""
    target = _normalize_date(date_value)
    if target is None:
        return []
    return get_items_for_range(target, target)


def get_items_for_month(year, month):
    """返回指定月份所有投影项（含被 override 移动到该月的 occurrence）。"""
    first = _date(int(year), int(month), 1)
    if int(month) == 12:
        last = _date(int(year), 12, 31)
    else:
        last = _date(int(year), int(month) + 1, 1) - timedelta(days=1)
    return get_items_for_range(first.isoformat(), last.isoformat())


def get_items_for_range(range_start, range_end):
    """返回 [range_start, range_end] 内的所有投影项。

    包含：
    1. base occurrence 落在 range 内（应用 override 后的日期仍在 range）
    2. override_date 落在 range 内（即使 base occurrence 在 range 外，因为被移入）

    cancelled 的 occurrence 不返回。
    """
    start = _normalize_date(range_start)
    end = _normalize_date(range_end)
    if start is None or end is None or start > end:
        return []

    tasks = get_all_tasks()
    recurring = [t for t in tasks if t["is_recurring"]]
    recurring_ids = [t["id"] for t in recurring]
    completed_by_task = get_completed_occurrence_keys_for_tasks(recurring_ids)
    cancelled_by_task = get_cancelled_occurrence_keys_for_tasks(recurring_ids)
    overrides_by_task = get_occurrence_overrides_for_tasks(recurring_ids)

    items = []
    emitted = set()   # (task_id, occurrence_key)

    # --- 1) base projection ---
    for t in recurring:
        cancelled = cancelled_by_task.get(t["id"], set())
        overrides = overrides_by_task.get(t["id"], {})
        occs = recurrence.get_occurrences_for_range(t, start, end)
        for occ in occs:
            key = recurrence.make_occurrence_key(occ.date, occ.time)
            if key in cancelled:
                continue
            override = overrides.get(key)
            # override 把这次移到 range 外 → 原位置不再显示
            if override is not None and override["override_date"]:
                od = override["override_date"]
                if od < start or od > end:
                    continue
            items.append(_occurrence_to_task_occurrence(t, occ, completed_by_task, override))
            emitted.add((t["id"], key))

    # --- 2) 从 range 外移入的 override（含原 occurrence 时间/时长变化）---
    tasks_by_id = {t["id"]: t for t in recurring}
    for row in get_occurrence_overrides_in_range(start, end):
        t = tasks_by_id.get(row["task_id"])
        if t is None:
            continue
        key = row["occurrence_key"]
        if (t["id"], key) in emitted:
            continue
        if key in cancelled_by_task.get(t["id"], set()):
            continue
        base_date, base_time = recurrence.parse_occurrence_key(key)
        occ = recurrence.Occurrence(task_id=t["id"], date=base_date, time=base_time)
        items.append(_occurrence_to_task_occurrence(t, occ, completed_by_task, row))
        emitted.add((t["id"], key))

    # --- 3) 普通任务 ---
    for t in tasks:
        if t["is_recurring"]:
            continue
        if t["date"] and start <= t["date"] <= end:
            items.append(_task_to_occurrence(t))

    items.sort(key=lambda x: (x.date or "", 1 if x.time else 0, x.time or "", x.task_id))
    return items


def get_today_items():
    """今日未完成项：今天的非重复任务 + 今天重复 occurrence（排除已完成的 occurrence）。"""
    today = _today_str()
    items = get_items_for_date(today)
    return [x for x in items if not x.is_completed]


def get_upcoming_items():
    """后续安排：非重复未来任务全部显示；重复系列只显示下一次 occurrence。"""
    today = _today_str()
    tasks = get_all_tasks()

    # 批量取一次：以前是循环里对每个重复任务单独查 2 次（N+1；
    # 实测 12 个重复任务 → 25 条 SQL、183ms）。语义不变，只是把
    # 「每个任务一次查询」改成「一次拿全部 task_ids」。
    recurring_ids = [t["id"] for t in tasks if t["is_recurring"] and not t["is_completed"]]
    done_map = get_completed_occurrence_keys_for_tasks(recurring_ids) if recurring_ids else {}
    cancelled_map = get_cancelled_occurrence_keys_for_tasks(recurring_ids) if recurring_ids else {}

    items = []

    for t in tasks:
        if t["is_completed"]:
            continue
        if t["is_recurring"]:
            done = done_map.get(t["id"], set())
            cancelled = cancelled_map.get(t["id"], set())
            # 找到第一个未被取消的下一次 occurrence
            after = today
            chosen = None
            for _ in range(10):  # 最多尝试 10 次跳过 cancelled，防死循环
                next_occ = recurrence.get_next_occurrence(t, after)
                if next_occ is None:
                    break
                key = recurrence.make_occurrence_key(next_occ.date, next_occ.time)
                if key in cancelled:
                    after = next_occ.date  # 从这次日期之后继续找
                    continue
                chosen = next_occ
                break
            if chosen is not None:
                items.append(_occurrence_to_task_occurrence(t, chosen, {t["id"]: done}))
        else:
            if t["date"] and t["date"] > today:
                items.append(_task_to_occurrence(t))

    items.sort(key=lambda x: (x.date, 1 if x.time else 0, x.time or "", x.task_id))
    return items


def get_completed_items():
    """已完成项：普通完成任务 + 已完成重复 occurrence。"""
    tasks = get_all_tasks()
    items = []

    # 普通已完成任务
    for t in tasks:
        if not t["is_recurring"] and t["is_completed"]:
            items.append(_task_to_occurrence(t))

    # 已完成 recurring occurrence
    completed_states = get_completed_occurrences()
    tasks_by_id = {t["id"]: t for t in tasks if t["is_recurring"]}
    for st in completed_states:
        t = tasks_by_id.get(st["task_id"])
        if t is None:
            continue
        occ_date, occ_time = recurrence.parse_occurrence_key(st["occurrence_key"])
        items.append(TaskOccurrence(
            task_id=t["id"],
            occurrence_key=st["occurrence_key"],
            title=t["title"],
            date=occ_date,
            time=occ_time,
            description=t["description"],
            url=t["url"],
            priority=t["priority"],
            is_recurring=True,
            is_completed=True,
            completed_at=st["completed_at"],
            recurrence_frequency=t["recurrence_frequency"],
            recurrence_interval=t["recurrence_interval"] or 1,
            recurrence_end_type=t["recurrence_end_type"],
            recurrence_end_date=t["recurrence_end_date"],
        ))

    items.sort(key=lambda x: x.completed_at or "", reverse=True)
    return items


def _to_date(value):
    if isinstance(value, _date):
        return value
    if isinstance(value, str):
        try:
            return _date.fromisoformat(value)
        except ValueError:
            return None
    return None


def complete_occurrence(task_id, occurrence_key, completed):
    """完成 / 取消完成某一次 occurrence。不修改 master task。"""
    set_occurrence_completed(task_id, occurrence_key, completed)


# ---------------------------------------------------------------
# 重复任务删除语义
# ---------------------------------------------------------------

def cancel_occurrence(task_id, occurrence_key):
    """仅删除这一次：UPSERT status='cancelled'。不删除 master。"""
    set_occurrence_state(task_id, occurrence_key, "cancelled")


def cancel_from_occurrence(task_id, occurrence_key):
    """从这一次起全部删除：设置 recurrence_stop_before。"""
    set_recurrence_stop_before(task_id, occurrence_key)


def delete_recurring_series(task_id):
    """删除整个重复系列：删除 master + 清理所有 occurrence states。"""
    delete_task(task_id)


def delete_task_occurrence(task_id, occurrence_key, mode):
    """根据模式删除 recurring occurrence。

    mode:
      'this'   - 仅删除这一次（cancelled）
      'after'  - 从这一次起全部删除（recurrence_stop_before）
      'series' - 删除整个重复系列
    """
    if mode == "this":
        cancel_occurrence(task_id, occurrence_key)
    elif mode == "after":
        cancel_from_occurrence(task_id, occurrence_key)
    elif mode == "series":
        delete_recurring_series(task_id)
    else:
        raise ValueError("未知的删除模式")


def recurrence_description_for_occurrence(item):
    """返回 TaskOccurrence 的重复规则中文描述。"""
    freq = item.recurrence_frequency
    if freq not in RECURRENCE_FREQUENCY_LABELS:
        return None
    base = RECURRENCE_FREQUENCY_LABELS[freq]
    interval = int(item.recurrence_interval or 1)
    if freq == "hourly":
        label = "每小时" if interval == 1 else f"每{interval}小时"
    elif freq == "daily":
        label = "每天" if interval == 1 else f"每{interval}天"
    elif freq == "weekly":
        label = "每周" if interval == 1 else f"每{interval}周"
    elif freq == "monthly":
        label = "每月" if interval == 1 else f"每{interval}个月"
    elif freq == "quarterly":
        label = "每3个月" if interval == 1 else f"每{3 * interval}个月"
    elif freq == "yearly":
        label = "每年" if interval == 1 else f"每{interval}年"
    else:
        label = base
    if item.recurrence_end_type == "on_date" and item.recurrence_end_date:
        return f"🔁 {label} · 至 {item.recurrence_end_date}"
    return f"🔁 {label}"


# ---------------------------------------------------------------
# 时间块（Time Blocking）
# ---------------------------------------------------------------

def _minutes_from_hhmm(hhmm):
    """HH:MM → 自 00:00 起的分钟数。"""
    if not hhmm:
        return None
    try:
        return int(hhmm[:2]) * 60 + int(hhmm[3:5])
    except (ValueError, TypeError):
        return None


def _hhmm_from_minutes(total):
    """分钟数 → HH:MM（允许 >= 1440，表示跨午夜）。"""
    total = int(total)
    hh = (total // 60) % 24
    mm = total % 60
    return f"{hh:02d}:{mm:02d}"


def resolve_duration(item, default=DEFAULT_DURATION_MINUTES):
    """返回项的展示时长（分钟）。

    有明确 duration_minutes 用它；否则旧任务默认 60 分钟；
    全日/无时间任务返回 None。不写数据库。
    """
    if item.time is None:
        return None
    dur = getattr(item, "duration_minutes", None)
    if dur is None:
        return default
    return int(dur)


def item_end_time(item, default=DEFAULT_DURATION_MINUTES):
    """返回 (start_minutes, end_minutes)，已含跨午夜。无时间返回 None。"""
    start = _minutes_from_hhmm(item.time)
    if start is None:
        return None
    dur = resolve_duration(item, default)
    return start, start + dur


def _intervals_overlap(start1, end1, start2, end2):
    """半开区间 [start, end) 是否重叠。

    14:00-15:00 与 15:00-16:00 → 不重叠（首尾相接不算冲突）。
    """
    return start1 < end2 and start2 < end1


def detect_time_conflicts(date_value, time_value, duration_minutes,
                         exclude_task_id=None, exclude_occurrence_key=None,
                         default=DEFAULT_DURATION_MINUTES):
    """检测同一天内与该时间块重叠的项。

    返回冲突项列表（TaskOccurrence）。
    只考虑：
      - 普通 timed task（未完成）
      - 未取消、未完成的 recurring occurrence / override
    completed 不参与冲突（仍可显示）。
    """
    if not date_value or not time_value or duration_minutes is None:
        return []
    start = _minutes_from_hhmm(time_value)
    if start is None:
        return []
    end = start + int(duration_minutes)

    conflicts = []
    for item in get_items_for_range(date_value, date_value):
        if item.is_completed:
            continue
        if item.time is None:
            continue
        if exclude_task_id is not None and item.task_id == exclude_task_id \
                and item.occurrence_key == exclude_occurrence_key:
            continue
        span = item_end_time(item, default)
        if span is None:
            continue
        s2, e2 = span
        if _intervals_overlap(start, end, s2, e2):
            conflicts.append(item)
    return conflicts


def conflict_labels(item, default=DEFAULT_DURATION_MINUTES):
    """返回某时间块冲突的其他项标题列表。"""
    if not item.date or not item.time:
        return []
    dur = resolve_duration(item, default)
    return [c.title for c in detect_time_conflicts(
        item.date, item.time, dur,
        exclude_task_id=item.task_id,
        exclude_occurrence_key=item.occurrence_key,
        default=default,
    )]


def total_conflict_count(items, default=DEFAULT_DURATION_MINUTES):
    """统计一组投影项中处于冲突状态的项数（去重按 task+key）。"""
    seen = set()
    count = 0
    for item in items:
        if item.is_completed or not item.date or not item.time:
            continue
        key = (item.task_id, item.occurrence_key, item.date, item.time)
        if key in seen:
            continue
        seen.add(key)
        dur = resolve_duration(item, default)
        if detect_time_conflicts(item.date, item.time, dur,
                                 exclude_task_id=item.task_id,
                                 exclude_occurrence_key=item.occurrence_key,
                                 default=default):
            count += 1
    return count


# ---------------------------------------------------------------
# 仅修改这一次：occurrence override
# ---------------------------------------------------------------

def set_occurrence_override(task_id, occurrence_key, date=None, time=None,
                            duration_minutes=None):
    """写入一条 occurrence override（仅修改这一次）。

    occurrence_key 始终是原始 identity，不随移动改变。
    """
    date_str = _normalize_date(date) if date is not None else None
    time_str = _normalize_time(time) if time is not None else None
    dur = None
    if duration_minutes is not None:
        dur = _validate_duration(duration_minutes, date_str or "x", time_str or "x")
    if time_str is None and date_str is None and dur is None:
        db_delete_occurrence_override(task_id, occurrence_key)
        return
    db_set_occurrence_override(task_id, occurrence_key, date_str, time_str, dur)


def get_occurrence_override(task_id, occurrence_key):
    """查询一次 occurrence 的 override。"""
    return db_get_occurrence_override(task_id, occurrence_key)


def clear_occurrence_override(task_id, occurrence_key):
    """删除一次 occurrence 的 override。"""
    db_delete_occurrence_override(task_id, occurrence_key)


# ---------------------------------------------------------------
# 修改整个系列
# ---------------------------------------------------------------

def move_series(task_id, new_date, new_time=None, new_duration_minutes=None):
    """调整整个重复系列：更新 master 的 anchor date / time / duration。

    不删除已有单次 override（override 继续优先）。
    """
    task = get_task_by_id(task_id)
    if task is None:
        raise ValueError("任务不存在")
    return _apply_series(task, new_date, new_time, new_duration_minutes)


def _apply_series(task, new_date, new_time, new_duration_minutes):
    date_str = _normalize_date(new_date)
    time_str = _normalize_time(new_time) if new_time is not None else task["time"]
    if new_duration_minutes is not None:
        duration = _validate_duration(new_duration_minutes, date_str, time_str)
    else:
        duration = _task_duration(task)

    edit_task(
        task["id"],
        task["title"],
        description=task["description"],
        url=task["url"],
        date=date_str,
        time=time_str,
        priority=task["priority"],
        is_recurring=True,
        recurrence_frequency=task["recurrence_frequency"],
        recurrence_interval=task["recurrence_interval"] or 1,
        recurrence_end_type=task["recurrence_end_type"],
        recurrence_end_date=task["recurrence_end_date"],
        duration_minutes=duration,
    )


def resize_series(task_id, new_duration_minutes):
    """整个系列 resize：只改 master 的 duration。"""
    task = get_task_by_id(task_id)
    if task is None:
        raise ValueError("任务不存在")
    return _apply_series(task, task["date"], task["time"], new_duration_minutes)


def move_series_by_occurrence(task_id, occurrence_key, new_date, new_time=None,
                              new_duration_minutes=None):
    """把某次 occurrence 的位移应用到整个系列。

    计算「该 occurrence 的日期差」并平移 anchor date，
    这样整个 recurrence 相位保持，但整体移到新星期几 / 新日期。
    """
    task = get_task_by_id(task_id)
    if task is None:
        raise ValueError("任务不存在")
    base_date_str, _ = recurrence.parse_occurrence_key(occurrence_key)
    try:
        base_d = _date.fromisoformat(base_date_str)
        new_d = _date.fromisoformat(_normalize_date(new_date))
    except (TypeError, ValueError):
        raise ValueError("日期格式无效")
    anchor = _date.fromisoformat(task["date"]) if task["date"] else new_d
    shifted_anchor = anchor + timedelta(days=(new_d - base_d).days)
    time_val = new_time if new_time is not None else task["time"]
    duration = new_duration_minutes if new_duration_minutes is not None \
        else _task_duration(task)
    return _apply_series(task, shifted_anchor.isoformat(), time_val, duration)


# ---------------------------------------------------------------
# 普通任务拖动 / resize（即时保存）
# ---------------------------------------------------------------

def move_plain_task(task_id, new_date=None, new_time=None,
                    new_duration_minutes=None, clear_time=False):
    """移动 / 拉伸普通任务（直接写库）。

    clear_time=True 时把任务变为全天事项（time/duration 置 NULL）。
    """
    task = get_task_by_id(task_id)
    if task is None:
        raise ValueError("任务不存在")

    date_str = _normalize_date(new_date) if new_date is not None else task["date"]
    if clear_time:
        time_str = None
        duration = None
    elif new_time is not None:
        time_str = _normalize_time(new_time)
        duration = (_validate_duration(new_duration_minutes, date_str, time_str)
                    if new_duration_minutes is not None
                    else (_task_duration(task) or DEFAULT_DURATION_MINUTES))
    else:
        time_str = task["time"]
        duration = (_validate_duration(new_duration_minutes, date_str, time_str)
                    if new_duration_minutes is not None else _task_duration(task))

    edit_task(
        task_id,
        task["title"],
        description=task["description"],
        url=task["url"],
        date=date_str,
        time=time_str,
        priority=task["priority"],
        is_recurring=False,
        duration_minutes=duration,
    )

