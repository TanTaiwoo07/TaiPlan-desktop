"""Runtime 级配置（Streamlit 与 desktop_runtime 共享的小型 JSON）。

用于「关闭主窗口时」等桌面行为设置，两个进程都能读写。
"""

import json
import os
from pathlib import Path

from taiplan import app_paths
from taiplan import config_store

PROJECT_DIR = app_paths.get_project_root()
CONFIG_PATH = app_paths.get_config_path("desktop_config.json")

DEFAULTS = {
    "close_to_tray": True,   # 关闭窗口时最小化到托盘（False = 完全退出）
}


def load_runtime_config():
    """读取 runtime 配置，缺失时用默认值。"""
    return config_store.read_json_config(CONFIG_PATH, defaults=DEFAULTS)


def save_runtime_config(cfg):
    """原子写入 runtime 配置（仅保存已知字段）。"""
    data = {k: cfg.get(k, DEFAULTS[k]) for k in DEFAULTS}
    config_store.write_json_config_atomic(CONFIG_PATH, data)
    return data
