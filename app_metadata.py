"""应用元数据集中定义（名称 / App ID / 发布者 / 图标资源）。"""

from pathlib import Path

import app_paths
import version

APP_NAME = "TaiPlan"
APP_SUBTITLE = "Desktop"
APP_ID = "TaiWoo.TaiPlan"           # W4：Windows App User Model ID（稳定，不再变化）
SHORTCUT_NAME = "TaiPlan"
PUBLISHER = "TaiWoo_Chen"           # 公司 / 发布者 / 作者

EXE_NAME = "TaiPlan.exe"

# ---- 旧身份（W4 仅用于升级兼容：清理旧快捷方式/启动项、注册表兼容查询）----
LEGACY_APP_NAME = "Todo App"
LEGACY_APP_ID = "TodoApp.Desktop"
LEGACY_SHORTCUT_NAME = "Todo App"
LEGACY_EXE_NAME = "TodoApp.exe"
VERSION = version.__version__
BUILD_CHANNEL = version.BUILD_CHANNEL

ICON_PNG_NAME = "app_icon.png"
ICON_ICO_NAME = "app_icon.ico"

# 数据与凭据命名空间
# W3：凭据身份迁移到 TaiPlan-AI（旧 TodoApp-AI 由 ai_settings 只读兼容）
CREDENTIAL_SERVICE = "TaiPlan-AI"


def icon_png() -> Path:
    return app_paths.get_assets_dir() / ICON_PNG_NAME


def icon_ico() -> Path:
    return app_paths.get_assets_dir() / ICON_ICO_NAME


def icon_png_path():
    p = icon_png()
    return str(p) if p.is_file() else None


def icon_ico_path():
    p = icon_ico()
    return str(p) if p.is_file() else None


INSTALL_MARKER_NAME = "install.json"


def install_marker_path() -> Path:
    """安装器写的标记文件（frozen 下 = 可执行文件所在目录）。"""
    return app_paths.get_project_root() / INSTALL_MARKER_NAME


def is_installed_build() -> bool:
    """是否为安装版（Inno Setup 装出来的）。

    **只用于 About 页面与诊断显示，业务逻辑不得依赖它。**
    """
    try:
        return install_marker_path().is_file()
    except Exception:  # noqa: BLE001
        return False


def install_info() -> dict:
    """读取 install.json（version / install_type / installed_at）；读不到返回 {}。"""
    import json

    try:
        # 安装器用 SaveStringsToUTF8File 写，带 BOM → utf-8-sig 读
        return json.loads(install_marker_path().read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return {}


def about_dict() -> dict:
    return {
        "name": APP_NAME,
        "app_id": APP_ID,
        "publisher": PUBLISHER,
        "version": VERSION,
        "build_channel": BUILD_CHANNEL,
        "full_version": version.full_version(),
    }
