# -*- coding: utf-8 -*-
"""语言设置的持久化与应用（Stage 17.3 §6）。

  * 存储值固定为 system / zh-CN / en-US
  * 手动选择持久化，并优先于系统语言
  * 只读写配置目录里的 language_config.json，不碰数据/凭据
"""
from __future__ import annotations

from taiplan import app_paths
from taiplan import config_store

from taiplan.i18n import (LANGUAGE_CHOICES, LANGUAGE_SYSTEM, get_language, get_setting,
                  set_language)

FILENAME = "language_config.json"
DEFAULTS = {"language": LANGUAGE_SYSTEM}


def config_path():
    return app_paths.get_config_path(FILENAME)


def load_setting():
    """读取用户选择；非法或缺省时回落到 system。"""
    try:
        data = config_store.read_json_config(config_path(), defaults=dict(DEFAULTS))
    except Exception:  # noqa: BLE001 - 配置损坏不应阻塞启动
        return LANGUAGE_SYSTEM
    value = (data or {}).get("language", LANGUAGE_SYSTEM)
    return value if value in LANGUAGE_CHOICES else LANGUAGE_SYSTEM


def save_setting(setting):
    """持久化选择并立即生效。"""
    if setting not in LANGUAGE_CHOICES:
        setting = LANGUAGE_SYSTEM
    config_store.write_json_config_atomic(config_path(), {"language": setting})
    return set_language(setting)


def apply():
    """按已保存的设置生效（启动时调用）。"""
    return set_language(load_setting())


__all__ = ["FILENAME", "DEFAULTS", "config_path", "load_setting", "save_setting",
           "apply", "get_language", "get_setting"]
