"""提醒系统业务层。

负责：
- 判断哪些任务需要提醒
- 生成 notification key
- 检查是否已经提醒
- 写入提醒历史
- 日期任务汇总
- recurring occurrence 提醒判断

时间可注入（now 参数），便于测试。
"""

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional

from taiplan import app_paths
from taiplan import config_store

from taiplan.i18n import t as _t

from taiplan import database
from taiplan import recurrence
from taiplan import services

# 默认提醒设置
DEFAULT_NOTIFICATION_CONFIG = {
    "enabled": True,
    "all_day_summary_time": "09:00",
    "grace_minutes": 60,
}

_CONFIG_PATH = app_paths.get_config_path("notification_config.json")


def load_notification_config():
    """读取提醒配置；文件损坏时返回默认。"""
    if not _CONFIG_PATH.exists():
        return dict(DEFAULT_NOTIFICATION_CONFIG)
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return dict(DEFAULT_NOTIFICATION_CONFIG)
        cfg = dict(DEFAULT_NOTIFICATION_CONFIG)
        cfg.update(data)
        return cfg
    except (ValueError, json.JSONDecodeError):
        return dict(DEFAULT_NOTIFICATION_CONFIG)


def save_notification_config(cfg):
    """原子写入提醒配置。"""
    data = {
        "enabled": bool(cfg.get("enabled", True)),
        "all_day_summary_time": cfg.get("all_day_summary_time", "09:00"),
        "grace_minutes": int(cfg.get("grace_minutes", 60)),
    }
    config_store.write_json_config_atomic(_CONFIG_PATH, data)


@dataclass
class Notification:
    key: str
    kind: str                     # due / daily-summary
    title: str
    task_id: Optional[int] = None
    occurrence_key: Optional[str] = None
    date: Optional[str] = None
    time: Optional[str] = None
    priority: str = "normal"
    urgent: bool = False
    message: str = ""


def make_task_notification_key(task_id, date, time):
    """普通有时间任务的提醒 key。"""
    return f"task:{task_id}:due:{date}T{time}"


def make_occurrence_notification_key(task_id, occurrence_key):
    """重复 occurrence 的提醒 key。"""
    return f"occurrence:{task_id}:{occurrence_key}:due"


def make_daily_summary_key(date_str):
    """全天任务汇总提醒 key。"""
    return f"daily-summary:{date_str}"


def _parse_time(hhmm):
    try:
        h, m = hhmm.split(":")
        return int(h), int(m)
    except (ValueError, AttributeError):
        return 0, 0


def _row_get(row, key, default=None):
    """从 sqlite3.Row 或 dict 中取值。"""
    try:
        val = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if val is None else val


def _task_due_datetime(task, now):
    """返回任务的 due datetime（仅当 date 和 time 都存在）。"""
    d = _row_get(task, "date")
    t = _row_get(task, "time")
    if not d or not t:
        return None
    try:
        h, m = _parse_time(t)
        return datetime(int(d[:4]), int(d[5:7]), int(d[8:10]), h, m)
    except (ValueError, IndexError, TypeError):
        return None


def get_due_notifications(now=None, config=None):
    """返回当前应触发的时间提醒列表。

    now：可注入的当前时间（用于测试）。
    config：提醒设置 dict（enabled / grace_minutes）。
    """
    now = now or datetime.now()
    cfg = config or dict(DEFAULT_NOTIFICATION_CONFIG)
    if not cfg.get("enabled", True):
        return []

    grace_minutes = int(cfg.get("grace_minutes", 60))
    today_str = now.date().isoformat()
    results = []

    tasks = database.get_all_tasks()

    # 普通非重复任务：date+time，未完成
    for t in tasks:
        if t["is_recurring"]:
            continue
        if t["is_completed"]:
            continue
        due = _task_due_datetime(t, now)
        if due is None:
            continue
        # 只考虑今天到期 + grace window
        if due.date().isoformat() != today_str:
            continue
        if not (due <= now <= due + timedelta(minutes=grace_minutes)):
            continue
        key = make_task_notification_key(t["id"], t["date"], t["time"])
        if database.has_notification_fired(key):
            continue
        results.append(Notification(
            key=key,
            kind="due",
            title=t["title"],
            task_id=t["id"],
            date=t["date"],
            time=t["time"],
            priority=t["priority"],
            urgent=(t["priority"] == "urgent"),
            message=f"{t['time']} {t['title']}",
        ))

    # 重复任务：今天范围内 occurrence
    recurring_ids = [t["id"] for t in tasks if t["is_recurring"]]
    completed_by_task = database.get_completed_occurrence_keys_for_tasks(recurring_ids)
    cancelled_by_task = database.get_cancelled_occurrence_keys_for_tasks(recurring_ids)

    for t in tasks:
        if not t["is_recurring"]:
            continue
        occs = recurrence.get_occurrences_for_range(t, today_str, today_str)
        for occ in occs:
            occ_key = recurrence.make_occurrence_key(occ.date, occ.time)
            # 跳过 completed / cancelled
            if occ_key in completed_by_task.get(t["id"], set()):
                continue
            if occ_key in cancelled_by_task.get(t["id"], set()):
                continue
            if not occ.time:
                continue  # 全天 occurrence 走汇总，不即时提醒
            h, m = _parse_time(occ.time)
            due = datetime(now.year, now.month, now.day, h, m)
            if not (due <= now <= due + timedelta(minutes=grace_minutes)):
                continue
            nkey = make_occurrence_notification_key(t["id"], occ_key)
            if database.has_notification_fired(nkey):
                continue
            results.append(Notification(
                key=nkey,
                kind="due",
                title=t["title"],
                task_id=t["id"],
                occurrence_key=occ_key,
                date=occ.date,
                time=occ.time,
                priority=t["priority"],
                urgent=(t["priority"] == "urgent"),
                message=f"{occ.time} {t['title']}",
            ))

    return results


def get_daily_summary(now=None, config=None):
    """返回当天汇总提醒（date-only 任务）。每天只触发一次。"""
    now = now or datetime.now()
    cfg = config or dict(DEFAULT_NOTIFICATION_CONFIG)
    if not cfg.get("enabled", True):
        return None

    today_str = now.date().isoformat()
    summary_key = make_daily_summary_key(today_str)
    if database.has_notification_fired(summary_key):
        return None

    summary_time = cfg.get("all_day_summary_time", "09:00")
    h, m = _parse_time(summary_time)
    summary_dt = datetime(now.year, now.month, now.day, h, m)

    # 汇总时间到了才提醒
    if now < summary_dt:
        return None

    tasks = database.get_all_tasks()
    items = []

    # 非重复 date-only 未完成任务
    for t in tasks:
        if t["is_recurring"]:
            continue
        if t["is_completed"]:
            continue
        if t["date"] == today_str and not t["time"]:
            items.append(t["title"])

    # 重复 occurrence 中全天（无 time）的
    recurring_ids = [t["id"] for t in tasks if t["is_recurring"]]
    completed_by_task = database.get_completed_occurrence_keys_for_tasks(recurring_ids)
    cancelled_by_task = database.get_cancelled_occurrence_keys_for_tasks(recurring_ids)
    for t in tasks:
        if not t["is_recurring"]:
            continue
        occs = recurrence.get_occurrences_for_range(t, today_str, today_str)
        for occ in occs:
            if occ.time:
                continue
            occ_key = recurrence.make_occurrence_key(occ.date, occ.time)
            if occ_key in completed_by_task.get(t["id"], set()):
                continue
            if occ_key in cancelled_by_task.get(t["id"], set()):
                continue
            items.append(t["title"])

    if not items:
        return None

    return Notification(
        key=summary_key,
        kind="daily-summary",
        title=_t("notify.daily_summary_title", count=len(items)),
        date=today_str,
        message="\n".join(f"• {it}" for it in items[:5]),
    )


def mark_notification_fired(notification):
    """写入提醒日志（防重复）。"""
    database.mark_notification_fired(
        notification.key,
        task_id=notification.task_id,
        occurrence_key=notification.occurrence_key,
        notification_type=notification.kind,
    )


def claim_notification(notification):
    """原子 claim 一条提醒（后台 Worker 与 Streamlit 共用的去重入口）。

    返回 True 表示本进程获得发送权，False 表示已被其他进程处理。
    """
    due_at = f"{notification.date}T{notification.time}" if notification.time else notification.date
    return database.claim_notification(
        notification.key,
        task_id=notification.task_id,
        occurrence_key=notification.occurrence_key,
        notification_type=notification.kind,
        title_snapshot=notification.title,
        due_at=due_at,
        priority_snapshot=notification.priority,
    )


def mark_notification_delivered(notification, channel="desktop"):
    """标记提醒已成功发送。"""
    database.update_notification_delivery(notification.key, "delivered", channel=channel)


def mark_notification_failed(notification, error, channel="desktop"):
    """标记提醒发送失败（保存简短错误，不含敏感信息）。"""
    err = (error or "")[:200]
    database.update_notification_delivery(notification.key, "failed", channel=channel, error=err)
    database.increment_notification_retry(notification.key)
