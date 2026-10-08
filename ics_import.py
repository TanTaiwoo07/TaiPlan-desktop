# -*- coding: utf-8 -*-
"""iCalendar（.ics）导入：RFC 5545 子集解析 + 去重导入。

设计约束（与项目既有约定一致）
------------------------------
* **零新依赖**：不引入 icalendar / vobject / dateutil，自带解析与日期运算。
* **不写 SQL**：任务一律通过 ``services.create_task`` 创建，验证与规范化沿用既有实现。
* **不新增数据库列、不改 schema 版本**：已导入的 UID 记在 ``state/ics_import.json``。
* **导入前自动备份**：调用既有的 ``data_backup.backup_database()``。
* **不静默丢弃**：任何"看不懂就降级"的条目都进 ``Report.notes``，由界面逐条展示。

支持范围（子集）
----------------
* 折行展开、``\\n`` / ``\\,`` / ``\\;`` / ``\\\\`` 转义、UTF-8 优先（GBK 兜底）
* 组件：VEVENT、VTODO；``STATUS:CANCELLED`` 跳过；VALARM / VTIMEZONE 内部属性忽略
* 时间：``VALUE=DATE``（全天）、floating、``TZID``（按本机本地时间）、``Z``（UTC→本地）
* 重复：FREQ / INTERVAL / COUNT / UNTIL / BYDAY（工作日、周末、单个星期）
* 其余 RRULE 关键字与 RDATE/EXDATE 不参与计算，降级为单次任务并记 note
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import date as _date
from datetime import datetime, time as _time, timedelta, timezone
from pathlib import Path

import app_paths

MAX_BYTES = 5 * 1024 * 1024
SUPPORTED_COMPONENTS = ("VEVENT", "VTODO")
SKIPPED_COMPONENTS = ("VALARM", "VTIMEZONE", "STANDARD", "DAYLIGHT")
STATE_FILENAME = "ics_import.json"
WORKDAYS = ("MO", "TU", "WE", "TH", "FR")
WEEKEND = ("SA", "SU")


# --------------------------------------------------------------- 数据结构

@dataclass
class Event:
    """从 .ics 里解析出来的一个事件（尽量接近原始语义）。"""

    uid: str | None = None
    summary: str | None = None
    description: str | None = None
    location: str | None = None
    url: str | None = None
    priority: int | None = None
    status: str | None = None
    start_date: _date | None = None
    start_time: _time | None = None
    all_day: bool = False
    end_date: _date | None = None
    end_time: _time | None = None
    rrule: str | None = None
    component: str = "VEVENT"

    @property
    def cancelled(self) -> bool:
        return (self.status or "").upper() == "CANCELLED"


@dataclass
class Report:
    """一次导入的结果。"""

    created: int = 0
    duplicates: int = 0
    downgraded: int = 0
    errors: int = 0
    total: int = 0
    backup: str | None = None
    notes: list[str] = field(default_factory=list)

    def summary_line(self) -> str:
        return (f"created={self.created} duplicates={self.duplicates} "
                f"downgraded={self.downgraded} errors={self.errors} total={self.total}")


# --------------------------------------------------------------- 解析：文本层

def decode_bytes(raw: bytes) -> str:
    """UTF-8 优先，失败再试 GBK（国内不少日历导出的 .ics 其实是 GBK）。"""
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


def unfold_lines(text: str) -> list[str]:
    """RFC 5545 折行展开：以空格或 TAB 开头的行是上一行的续行。"""
    out: list[str] = []
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if raw_line[:1] in (" ", "\t") and out:
            out[-1] += raw_line[1:]
        else:
            out.append(raw_line)
    return out


_ESCAPES = {"n": "\n", "N": "\n", ",": ",", ";": ";", "\\": "\\"}


def unescape(value: str) -> str:
    """还原 TEXT 值里的转义序列。"""
    out: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value) and value[i + 1] in _ESCAPES:
            out.append(_ESCAPES[value[i + 1]])
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


def split_property(line: str):
    """把 ``NAME;PARAM=1:VALUE`` 拆成 ``(NAME, params, VALUE)``。

    冒号取**第一个不在引号内**的：参数值里可能带冒号（例如 DTSTART;TZID=...）。
    """
    in_quote = False
    head = value = ""
    for idx, ch in enumerate(line):
        if ch == '"':
            in_quote = not in_quote
        elif ch == ":" and not in_quote:
            head, value = line[:idx], line[idx + 1:]
            break
    else:
        return None, {}, ""
    parts = head.split(";")
    name = parts[0].strip().upper()
    params: dict[str, str] = {}
    for chunk in parts[1:]:
        if "=" in chunk:
            key, _, val = chunk.partition("=")
            params[key.strip().upper()] = val.strip().strip('"')
    return name, params, value


# --------------------------------------------------------------- 解析：值层

_DT_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})(?:T(\d{2})(\d{2})(\d{2})?(Z)?)?$")


def parse_datetime(value: str, params: dict | None = None):
    """返回 ``(date, time, all_day)``；``time`` 为 None 表示全天。

    时间口径（写在这里，避免以后靠猜）：
      * ``VALUE=DATE`` 或只有日期 → 全天
      * 以 ``Z`` 结尾 → UTC，换算成本机本地时间
      * 带 ``TZID`` 或不带 → 按本机本地时间原样使用（不做时区库换算）
    """
    params = params or {}
    m = _DT_RE.match((value or "").strip())
    if not m:
        return None, None, False
    try:
        base = _date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None, None, False

    if params.get("VALUE", "").upper() == "DATE" or m.group(4) is None:
        return base, None, True

    t = _time(int(m.group(4)), int(m.group(5)), int(m.group(6) or 0))
    if m.group(7):                                  # UTC → 本地
        local_dt = datetime.combine(base, t, tzinfo=timezone.utc).astimezone()
        t = local_dt.time()
        base = local_dt.date()
    return base, t.replace(second=0, microsecond=0), False


def parse_rrule(value: str) -> dict:
    """把 RRULE 拆成 ``{FREQ: ..., INTERVAL: ...}``（键统一大写）。"""
    out: dict[str, str] = {}
    for chunk in (value or "").split(";"):
        if "=" in chunk:
            key, _, val = chunk.partition("=")
            out[key.strip().upper()] = val.strip()
    return out


# --------------------------------------------------------------- 解析：组件层

def parse_ics(text: str):
    """解析出 ``(events, warnings)``；只认识 VEVENT / VTODO。"""
    events: list[Event] = []
    warnings: list[str] = []
    stack: list[str] = []
    current: Event | None = None

    for line in unfold_lines(text):
        if not line.strip():
            continue
        name, params, value = split_property(line)
        if name is None:
            continue
        if name == "BEGIN":
            comp = value.strip().upper()
            stack.append(comp)
            if comp in SUPPORTED_COMPONENTS:
                current = Event(component=comp)
            elif comp not in ("VCALENDAR",) + SKIPPED_COMPONENTS:
                warnings.append(f"ignored component: {comp}")
            continue
        if name == "END":
            comp = value.strip().upper()
            if stack and stack[-1] == comp:
                stack.pop()
            elif comp in stack:
                stack = stack[:stack.index(comp)]
            if comp in SUPPORTED_COMPONENTS and current is not None:
                # VTODO 常常只有 DUE 没有 DTSTART：用 DUE 作为日期，避免整条丢掉
                if current.start_date is None and current.end_date is not None:
                    current.start_date = current.end_date
                    current.start_time = None if current.all_day else current.end_time
                    current.all_day = current.end_time is None
                    current.end_date, current.end_time = None, None
                    warnings.append("VTODO without DTSTART: used DUE as the date")
                if current.start_date is not None:
                    events.append(current)
                else:
                    warnings.append(f"skipped {comp} without DTSTART")
                current = None
            continue
        # VALARM / VTIMEZONE 内部属性不得污染事件
        if stack and stack[-1] in SKIPPED_COMPONENTS:
            continue
        if current is None:
            continue

        if name == "UID":
            current.uid = value.strip() or None
        elif name == "SUMMARY":
            current.summary = unescape(value).strip()
        elif name == "DESCRIPTION":
            current.description = unescape(value).strip()
        elif name == "LOCATION":
            current.location = unescape(value).strip()
        elif name == "URL":
            current.url = value.strip()
        elif name == "STATUS":
            current.status = value.strip()
        elif name == "PRIORITY":
            try:
                current.priority = int(value.strip())
            except ValueError:
                current.priority = None
        elif name == "DTSTART":
            d, t, all_day = parse_datetime(value, params)
            current.start_date, current.start_time, current.all_day = d, t, all_day
        elif name in ("DTEND", "DUE"):
            d, t, _ = parse_datetime(value, params)
            current.end_date, current.end_time = d, t
        elif name == "RRULE":
            current.rrule = value.strip()
        elif name in ("EXDATE", "RDATE"):
            warnings.append(f"{name} ignored")
    return events, warnings


# --------------------------------------------------------------- 映射：→ 任务参数

def _add_months(base: _date, months: int) -> _date:
    """加月份（跨年、月末自动收敛到合法日期）。"""
    total = base.month - 1 + months
    year = base.year + total // 12
    month = total % 12 + 1
    day = base.day
    while day > 28:
        try:
            return _date(year, month, day)
        except ValueError:
            day -= 1
    return _date(year, month, day)


def _priority_text(priority: int | None) -> str:
    """ICS PRIORITY(1..9) → 应用的三档优先级。"""
    if priority is None:
        return "normal"
    if priority <= 4:
        return "urgent"
    if priority >= 6:
        return "low"
    return "normal"


def map_recurrence(rrule: dict, start: _date):
    """RRULE → ``(frequency, interval, end_type, end_date, note)``。

    表达不了的组合返回 ``frequency=None``：调用方按单次任务导入并记 note。
    """
    freq = (rrule.get("FREQ") or "").upper()
    try:
        interval = int(rrule.get("INTERVAL") or 1)
    except ValueError:
        interval = 1
    interval = max(1, interval)

    bad = [k for k in ("BYMONTHDAY", "BYSETPOS", "BYWEEKNO", "BYYEARDAY", "BYMONTH",
                       "BYHOUR", "BYMINUTE") if k in rrule]
    if bad:
        return None, 1, None, None, "unsupported rrule keys: " + ",".join(bad)

    byday = [d.strip().upper() for d in (rrule.get("BYDAY") or "").split(",") if d.strip()]
    codes = [d[-2:] for d in byday if len(d) >= 2]

    if freq == "HOURLY":
        frequency = "hourly"
    elif freq == "DAILY":
        if codes and set(codes) == set(WORKDAYS):
            frequency = "workday"
        elif codes and set(codes) == set(WEEKEND):
            frequency = "weekend"
        elif codes:
            return None, 1, None, None, "BYDAY not representable: " + ",".join(codes)
        else:
            frequency = "daily"
    elif freq == "WEEKLY":
        if codes and start.strftime("%a").upper()[:2] not in codes:
            return None, 1, None, None, "weekly BYDAY != DTSTART weekday"
        frequency = "weekly"
    elif freq == "MONTHLY":
        if codes:
            return None, 1, None, None, "monthly BYDAY not representable"
        if interval == 3:
            frequency, interval = "quarterly", 1
        else:
            frequency = "monthly"
    elif freq == "YEARLY":
        frequency = "yearly"
    else:
        return None, 1, None, None, f"unsupported FREQ: {freq or '?'}"

    if frequency in ("workday", "weekend"):
        interval = 1

    until_raw = rrule.get("UNTIL")
    if until_raw:
        d, _t, _a = parse_datetime(until_raw, {})
        if d is not None:
            return frequency, interval, "on_date", d, None

    count_raw = rrule.get("COUNT")
    if count_raw:
        try:
            count = int(count_raw)
        except ValueError:
            count = 0
        if count <= 1:
            return None, 1, None, None, "COUNT<=1 treated as single"
        span = {"daily": lambda n: timedelta(days=n * interval),
                "weekly": lambda n: timedelta(weeks=n * interval),
                "monthly": lambda n: None,
                "quarterly": lambda n: None,
                "yearly": lambda n: None}.get(frequency)
        if frequency in ("monthly", "quarterly", "yearly"):
            months = {"monthly": interval, "quarterly": 3, "yearly": 12}[frequency]
            return frequency, interval, "on_date", _add_months(start, (count - 1) * months), None
        if span is not None:
            return frequency, interval, "on_date", start + span(count - 1), None
        return frequency, interval, "never", None, "COUNT ignored for hourly recurrence"

    return frequency, interval, "never", None, None


def build_task_args(event: Event, labels: dict):
    """Event → ``services.create_task`` 参数字典 + notes。"""
    notes: list[str] = []
    title = (event.summary or labels.get("untitled", "Untitled")).strip()

    parts = []
    if event.location:
        parts.append(f"{labels.get('location', 'Location')}: {event.location}")
    if event.description:
        parts.append(event.description)
    description = "\n".join(parts) or None

    url = event.url
    if url and not url.lower().startswith(("http://", "https://")):
        notes.append(labels.get("invalid_url", "Invalid URL skipped"))
        url = None

    duration = None
    if not event.all_day and event.end_date is not None:
        try:
            start_dt = datetime.combine(event.start_date, event.start_time or _time(0, 0))
            end_dt = datetime.combine(event.end_date, event.end_time or _time(0, 0))
            minutes = int((end_dt - start_dt).total_seconds() // 60)
            if 5 <= minutes <= 1440:
                duration = minutes
            elif minutes > 0:
                notes.append(labels.get("duration_ignored", "Duration ignored"))
        except (TypeError, ValueError):
            notes.append(labels.get("duration_ignored", "Duration ignored"))
    elif event.all_day and event.end_date and event.end_date > event.start_date:
        notes.append(labels.get("multiday_allday", "Multi-day all-day event"))

    is_recurring, frequency, interval, end_type, end_date = False, None, 1, None, None
    if event.rrule:
        frequency, interval, end_type, end_date, note = map_recurrence(
            parse_rrule(event.rrule), event.start_date)
        if frequency is None:
            notes.append(note or "recurrence unsupported")
            notes.append(labels.get("recurrence_simplified", "Imported as a single task"))
        else:
            is_recurring = True
            if note:
                notes.append(note)

    return {
        "title": title,
        "description": description,
        "url": url,
        "date": event.start_date,
        "time": None if event.all_day else event.start_time,
        "priority": _priority_text(event.priority),
        "is_recurring": is_recurring,
        "recurrence_frequency": frequency,
        "recurrence_interval": interval,
        "recurrence_end_type": end_type,
        "recurrence_end_date": end_date,
        "duration_minutes": duration,
    }, notes


# --------------------------------------------------------------- 去重记录

def state_path() -> Path:
    return app_paths.get_state_path(STATE_FILENAME)


def load_state() -> dict:
    path = state_path()
    if not path.is_file():
        return {"imported": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"imported": {}}
    if not isinstance(data, dict) or not isinstance(data.get("imported"), dict):
        return {"imported": {}}
    return data


def save_state(state: dict) -> None:
    path = state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        pass


def _content_key(title, date_val, time_val) -> str:
    """内容去重键：把 date/time 归一成库里存的形式（YYYY-MM-DD / HH:MM）。

    事件里拿到的是 datetime/date/time 对象，而库里存的是字符串；
    不归一化的话 "09:30:00" 与 "09:30" 会被当成两条不同的内容。
    """
    if isinstance(date_val, _date):
        date_val = date_val.strftime("%Y-%m-%d")
    if isinstance(time_val, _time):
        time_val = time_val.strftime("%H:%M")
    return f"{(title or '').strip()}|{date_val or ''}|{time_val or ''}"


# --------------------------------------------------------------- 预览与导入

def preview(events, labels, limit=8):
    """返回 ``(将导入条数, 前若干条预览文本)``；已取消的事件不计入。"""
    rows: list[str] = []
    count = 0
    for event in events:
        if event.cancelled:
            continue
        count += 1
        if len(rows) < limit:
            if event.all_day:
                when = str(event.start_date)
            else:
                hhmm = event.start_time.strftime("%H:%M") if event.start_time else ""
                when = f"{event.start_date} {hhmm}".strip()
            rows.append(f"{event.summary or labels.get('untitled', 'Untitled')} · {when}")
    return count, rows


def _existing_keys(services):
    keys = set()
    try:
        for task in services.get_tasks():
            keys.add(_content_key(task["title"], task["date"], task["time"]))
    except Exception:  # noqa: BLE001
        pass
    return keys


def import_events(events, labels, *, dry_run=False, do_backup=True) -> Report:
    """把解析结果写进数据库；所有写入都经过 ``services.create_task``。"""
    import data_backup
    import services

    report = Report()
    state = load_state()
    imported = state.setdefault("imported", {})

    if do_backup and not dry_run:
        try:
            report.backup = data_backup.backup_database().name
        except Exception as exc:  # noqa: BLE001
            report.notes.append(f"backup failed: {type(exc).__name__}")

    existing = _existing_keys(services)

    for event in events:
        report.total += 1
        if event.cancelled:
            report.notes.append(labels.get("cancelled", "Cancelled event skipped"))
            continue
        if event.uid and event.uid in imported:
            report.duplicates += 1
            continue

        args, notes = build_task_args(event, labels)
        for note in dict.fromkeys(notes):
            report.notes.append(note)
        if event.rrule and args["recurrence_frequency"] is None:
            report.downgraded += 1

        key = _content_key(args["title"], args["date"], args["time"])
        if key in existing:
            report.duplicates += 1
            if event.uid:
                imported[event.uid] = {"skipped": "duplicate"}
            continue

        if dry_run:
            report.created += 1
            continue
        try:
            services.create_task(**args)
        except (ValueError, TypeError) as exc:
            report.errors += 1
            report.notes.append(f"{args['title']}: {exc}")
            continue
        existing.add(key)
        report.created += 1
        if event.uid:
            imported[event.uid] = {"content": key}

    if not dry_run:
        save_state(state)
    return report


def import_ics_bytes(raw: bytes, labels, **kwargs) -> Report:
    """便捷入口：字节 → 解析 → 导入。"""
    events, warnings = parse_ics(decode_bytes(raw))
    report = import_events(events, labels, **kwargs)
    report.notes.extend(warnings)
    return report


def import_ics_file(path, labels, **kwargs) -> Report:
    return import_ics_bytes(Path(path).read_bytes(), labels, **kwargs)


__all__ = ["Event", "Report", "MAX_BYTES", "decode_bytes", "unfold_lines", "unescape",
           "split_property", "parse_datetime", "parse_rrule", "parse_ics",
           "map_recurrence", "build_task_args", "preview", "import_events",
           "import_ics_bytes", "import_ics_file", "state_path", "load_state",
           "save_state"]
