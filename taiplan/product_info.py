# -*- coding: utf-8 -*-
"""统一产品身份（Stage 17.3 §1）。

单一来源约束：
  * 版本号只在 version.py 定义，本模块只做转发，避免第二处版本号。
  * 品牌/作者/版权/标语集中在此，供 app / tray / notifications / About / build 复用。

注意：这里只定义"显示层"身份。
exe 名、data path、credential identity、AppUserModelID、installer identity
属于 W3/W4，不在此模块决定。
"""
from __future__ import annotations

from taiplan.version import __version__ as _VERSION

APP_NAME = "TaiPlan"
APP_DISPLAY_NAME = "TaiPlan"
APP_VERSION = _VERSION
APP_AUTHOR = "TaiWoo_Chen"
APP_COPYRIGHT = "\u00a9 2026 TaiWoo_Chen"
APP_TAGLINE = "Tasks into time."
APP_LICENSE = "MIT License"
APP_DESCRIPTION_ZH = "\u4e00\u4e2a\u672c\u5730\u4f18\u5148\u7684\u4efb\u52a1\u4e0e\u65f6\u95f4\u89c4\u5212\u684c\u9762\u5e94\u7528\u3002"
APP_DESCRIPTION_EN = "A local-first task and time planning desktop app."

# 尚未有真实仓库地址：不写假 URL（§11）
APP_REPO_URL = "https://github.com/TanTaiwoo07/TaiPlan-desktop"

__all__ = ["APP_NAME", "APP_DISPLAY_NAME", "APP_VERSION", "APP_AUTHOR", "APP_COPYRIGHT",
           "APP_TAGLINE", "APP_LICENSE", "APP_DESCRIPTION_ZH", "APP_DESCRIPTION_EN",
           "APP_REPO_URL"]
