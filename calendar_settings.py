"""Calendar 设置（持久化 JSON）。

- 默认任务时长
- 时间网格粒度
- 默认视图
"""

import json
import os
from pathlib import Path

import app_paths
import config_store

PROJECT_DIR = app_paths.get_project_root()
CONFIG_PATH = app_paths.get_config_path("calendar_config.json")

DEFAULT_DURATION_MINUTES = 60
SLOT_DURATION_MINUTES = 30

# FullCalendar 视图名
VIEW_MONTH = "dayGridMonth"
VIEW_WEEK = "timeGridWeek"
VIEW_DAY = "timeGridDay"

VIEW_LABELS = {
    VIEW_MONTH: "月",
    VIEW_WEEK: "周",
    VIEW_DAY: "日",
}
LABEL_TO_VIEW = {v: k for k, v in VIEW_LABELS.items()}

DURATION_CHOICES = [15, 30, 45, 60, 90, 120, 180]
DURATION_LABELS = {
    15: "15 分钟", 30: "30 分钟", 45: "45 分钟",
    60: "1 小时", 90: "1.5 小时", 120: "2 小时", 180: "3 小时",
}

SLOT_CHOICES = [15, 30, 60]

DEFAULTS = {
    "default_duration_minutes": DEFAULT_DURATION_MINUTES,
    "slot_duration_minutes": SLOT_DURATION_MINUTES,
    "default_view": VIEW_MONTH,
    "show_weekends": True,
    "show_now_indicator": True,
    # 单击空白时间：create = 打开新建任务；ignore = 不响应（默认安静，避免误触）
    "blank_click_action": "ignore",
    # 拖选时间范围：create = 打开新建任务并带入时长；ignore = 不响应
    "drag_select_action": "create",
}


def load_calendar_config():
    """读取 calendar 配置，缺失字段用默认值。"""
    return config_store.read_json_config(CONFIG_PATH, defaults=DEFAULTS)


def save_calendar_config(cfg):
    """原子写入 calendar 配置（只保存已知字段）。"""
    data = {k: cfg.get(k, DEFAULTS[k]) for k in DEFAULTS}
    config_store.write_json_config_atomic(CONFIG_PATH, data)
    return data


def default_duration(cfg=None):
    cfg = cfg or load_calendar_config()
    return int(cfg.get("default_duration_minutes", DEFAULT_DURATION_MINUTES))


def slot_duration(cfg=None):
    cfg = cfg or load_calendar_config()
    return int(cfg.get("slot_duration_minutes", SLOT_DURATION_MINUTES))
