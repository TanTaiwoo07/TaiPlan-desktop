"""任务相关的数据结构定义。

包含数据库实体 Task 和用于投影的虚拟 occurrence 结构。
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class Task:
    """任务数据结构，对应数据库 tasks 表中的一条记录。"""

    title: str
    description: Optional[str] = None
    url: Optional[str] = None
    date: Optional[str] = None       # 格式 YYYY-MM-DD
    time: Optional[str] = None       # 格式 HH:MM
    priority: str = "normal"         # low / normal / urgent
    is_completed: int = 0
    id: Optional[int] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_minutes: Optional[int] = None   # 仅 date+time 均存在时有意义


@dataclass
class TaskOccurrence:
    """一次任务「发生」的虚拟投影结构。

    用于把普通任务和重复任务的 occurrence 统一成 UI 可渲染的结构。
    不创建数据库记录。
    """

    task_id: int
    occurrence_key: Optional[str] = None   # 普通任务可为 None；重复 occurrence 有稳定唯一标识
    title: str = ""
    date: Optional[str] = None
    time: Optional[str] = None
    description: Optional[str] = None
    url: Optional[str] = None
    priority: str = "normal"
    is_recurring: bool = False
    is_completed: bool = False
    completed_at: Optional[str] = None
    recurrence_frequency: Optional[str] = None
    recurrence_interval: int = 1
    recurrence_end_type: Optional[str] = None
    recurrence_end_date: Optional[str] = None
    duration_minutes: Optional[int] = None   # 时间块持续分钟数；全天/无时间为 None
    override_date: Optional[str] = None      # 本 occurrence 被单独移动到的日期
    override_time: Optional[str] = None
