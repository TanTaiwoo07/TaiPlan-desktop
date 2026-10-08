"""Windows 应用身份（App User Model ID）注册。

Windows 桌面通知需要一个稳定的应用身份，否则通知会显示为 Python，
或在某些情况下完全不显示。本模块负责在当前用户上下文注册 AUMID，
不需要管理员权限。
"""

import logging

_logger = logging.getLogger("windows_app_registration")

APP_USER_MODEL_ID = "TaiWoo.TaiPlan"
APP_DISPLAY_NAME = "TaiPlan"
# 旧 AUMID：仅用于兼容查询，不删除、不新建
LEGACY_APP_USER_MODEL_ID = "TodoApp.Desktop"

_REG_PATH = r"SOFTWARE\Classes\AppUserModelId\\" + APP_USER_MODEL_ID


def _registry_registered():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _REG_PATH) as key:
            winreg.QueryValueEx(key, "DisplayName")
            return True
    except Exception:
        return False


def is_app_registered():
    """判断 App ID 是否已注册。"""
    try:
        from toasted import Toast
        return bool(Toast.is_registered_app_id(APP_USER_MODEL_ID))
    except Exception:
        return _registry_registered()


def _icon_path():
    import app_metadata
    return app_metadata.icon_png_path()


def _register_via_toasted():
    from toasted import Toast
    kwargs = {"display_name": APP_DISPLAY_NAME, "show_in_settings": True}
    icon = _icon_path()
    if icon:
        kwargs["icon_uri"] = icon
    Toast.register_app_id(APP_USER_MODEL_ID, **kwargs)
    return True


def _register_via_registry():
    import winreg
    key = winreg.CreateKey(winreg.HKEY_CURRENT_USER, _REG_PATH)
    winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_EXPAND_SZ, APP_DISPLAY_NAME)
    icon = _icon_path()
    if icon:
        winreg.SetValueEx(key, "IconUri", 0, winreg.REG_EXPAND_SZ, icon)
    winreg.CloseKey(key)
    return True


def ensure_app_registered():
    """确保 App ID 已注册。返回 True/False。"""
    if _registry_registered():
        return True
    # 优先用 toasted 的注册能力
    try:
        if _register_via_toasted():
            return True
    except Exception as e:
        _logger.debug("toasted register unavailable: %s", type(e).__name__)
    # 回退：直接写 HKCU 注册表
    try:
        return _register_via_registry()
    except Exception as e:
        _logger.warning("app id registration failed: %s", type(e).__name__)
        return False
