# -*- coding: utf-8 -*-
"""i18n 层（Stage 17.3 §6/§7/§10）。

对外 API：
    t("nav.today")                    -> 当前语言的字符串
    t("cal.conflict", count=2)        -> 支持 {count} 形式的占位符
    set_language("system"|"zh-CN"|"en-US")
    get_language()                    -> 生效中的语言（zh-CN / en-US）
    get_setting()                     -> 用户存的值（system / zh-CN / en-US）
    resolve_language(setting)         -> 把设置解析成生效语言
    detect_system_language()          -> Windows/Python 探测，失败 fallback en-US

约束：
  * 不在别处写 if lang == ...，一律 t("key")。
  * 缺失 key：返回 fallback 语言的值；两者都缺则返回 key 本身（便于测试发现）。
"""
from __future__ import annotations

import logging
import os

from taiplan.i18n.en_US import STRINGS as _EN
from taiplan.i18n.zh_CN import STRINGS as _ZH

logger = logging.getLogger(__name__)

LANGUAGE_SYSTEM = "system"
LANGUAGE_ZH_CN = "zh-CN"
LANGUAGE_EN_US = "en-US"

DEFAULT_LANGUAGE = LANGUAGE_ZH_CN
FALLBACK_LANGUAGE = LANGUAGE_EN_US

# 供设置界面渲染：显示名由 t() 提供，避免在 UI 里硬编码语言名
LANGUAGE_CHOICES = (LANGUAGE_SYSTEM, LANGUAGE_ZH_CN, LANGUAGE_EN_US)

_CATALOGS = {LANGUAGE_ZH_CN: _ZH, LANGUAGE_EN_US: _EN}

_setting = LANGUAGE_SYSTEM
_active = DEFAULT_LANGUAGE


def available_languages():
    return tuple(_CATALOGS.keys())


def catalog(lang=None):
    return _CATALOGS.get(lang or _active, _ZH)


def detect_system_language():
    """可靠探测系统语言；失败返回 FALLBACK_LANGUAGE（§10，必须有确定 fallback）。

    顺序：Windows GetUserDefaultUILanguage -> LANG/LC_ALL -> locale -> fallback。
    """
    # 1) Windows 原生：主语言 ID 0x04 = 中文
    try:
        import ctypes

        langid = ctypes.windll.kernel32.GetUserDefaultUILanguage()
        primary = langid & 0x03FF
        if primary == 0x04:
            return LANGUAGE_ZH_CN
        if primary:
            return LANGUAGE_EN_US
    except Exception:  # noqa: BLE001
        pass

    # 2) 环境变量
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var) or ""
        if value.lower().startswith(("zh", "chinese")):
            return LANGUAGE_ZH_CN
        if value.strip():
            return LANGUAGE_EN_US

    # 3) Python locale
    try:
        import locale

        code = (locale.getlocale()[0] or "")
        if code.lower().startswith("zh"):
            return LANGUAGE_ZH_CN
        if code:
            return LANGUAGE_EN_US
    except Exception:  # noqa: BLE001
        pass

    return FALLBACK_LANGUAGE


def resolve_language(setting=None):
    """把存储值解析成生效语言。手动选择优先于系统（§10）。"""
    value = _setting if setting is None else setting
    if value == LANGUAGE_SYSTEM:
        return detect_system_language()
    if value in _CATALOGS:
        return value
    return FALLBACK_LANGUAGE


def set_language(setting):
    """设置语言（不负责持久化；持久化由 config_store 负责）。"""
    global _setting, _active
    if setting not in LANGUAGE_CHOICES:
        setting = LANGUAGE_SYSTEM
    _setting = setting
    _active = resolve_language(setting)
    return _active


def get_language():
    return _active


def get_setting():
    return _setting


def t(message_key, **kwargs):
    """取翻译。缺失时按 fallback 语言 → key 自身降级。

    参数名故意不叫 key，以便调用方可以传 key=... 这类占位符
    （例如 t("dev.raw_occurrence", key=occurrence_key)）。
    """
    for lang in (get_language(), FALLBACK_LANGUAGE, DEFAULT_LANGUAGE):
        table = _CATALOGS.get(lang)
        if table and message_key in table:
            text = table[message_key]
            break
    else:
        logger.warning("i18n missing key: %s", message_key)
        return message_key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text
    return text


def is_rtl(lang=None):
    return False


# 模块导入时按系统判定一次，随后由 app 依据 config 覆写
set_language(LANGUAGE_SYSTEM)

# dates 依赖本模块的 get_language，必须放在函数定义之后导入（避免循环导入）
from taiplan.i18n import dates  # noqa: E402,F401
