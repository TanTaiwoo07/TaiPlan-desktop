"""依赖检查 + 用户数据导出 + 诊断包。

诊断 / 导出都遵循同一条隐私红线：
  绝不包含 API Key、keyring 凭据、AI 原始输入、任务描述全文、日志默认不导出。
"""

import json
import platform
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import app_paths
import app_metadata
import version

# 直接 import 的第三方依赖（缺失会导致对应功能不可用）
REQUIRED_MODULES = (
    ("streamlit", True),
    ("streamlit_calendar", True),
    ("requests", True),
    ("keyring", True),
    ("webview", True),
    ("pystray", True),
    ("PIL", True),
)

OPTIONAL_MODULES = (
    ("winotify", "Windows 通知（可选 provider）"),
    ("toasted", "Windows 通知（可选 provider）"),
    ("win32api", "pywin32（可选）"),
)

SENSITIVE_CONFIG_KEYS = ("api_key", "apikey", "token", "secret", "password")

# Edge WebView2 Runtime 的 Evergreen 产品 GUID（pywebview 依赖它）
WEBVIEW2_CLIENT_GUID = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
WEBVIEW2_DOWNLOAD_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"


def _module_info(name):
    try:
        module = __import__(name)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "version": None, "error": type(exc).__name__}
    ver = getattr(module, "__version__", None)
    return {"ok": True, "version": str(ver) if ver else "unknown", "error": None}


def _webview2_registry_version():
    """从注册表读 WebView2 Runtime 版本（HKLM/HKCU × 32/64 位视图）。"""
    import winreg

    key = rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_CLIENT_GUID}"
    views = []
    for name in ("KEY_WOW64_32KEY", "KEY_WOW64_64KEY", ""):
        views.append(getattr(winreg, name, 0) if name else 0)
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for view in views:
            try:
                with winreg.OpenKey(hive, key, 0, winreg.KEY_READ | view) as k:
                    value, _ = winreg.QueryValueEx(k, "pv")
                    if value:
                        return str(value)
            except OSError:
                continue
    return None


def _webview2_folder_version():
    """从安装目录读 WebView2 Runtime 版本（注册表被清理时的兜底）。"""
    import os as _os
    from pathlib import Path as _Path

    for env in ("ProgramFiles(x86)", "ProgramFiles"):
        root = _os.environ.get(env)
        if not root:
            continue
        base = _Path(root) / "Microsoft" / "EdgeWebView" / "Application"
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir(), reverse=True):
            try:
                if (entry / "msedgewebview2.exe").is_file():
                    return entry.name
            except OSError:
                continue
    return None


def webview2_version():
    """返回检测到的 WebView2 Runtime 版本；未安装返回 None。"""
    try:
        return _webview2_registry_version() or _webview2_folder_version()
    except Exception:  # noqa: BLE001
        return None


def webview2_available() -> bool:
    """pywebview 依赖的 Edge WebView2 Runtime 是否存在。

    Windows 10/11 通常已自带，**只在缺失时**提示，不要对有 Runtime 的机器报噪音。
    """
    return bool(webview2_version())


def webview2_note() -> str:
    version = webview2_version()
    if version:
        return f"WebView2 Runtime：{version}"
    return ("WebView2 Runtime：未检测到（桌面窗口需要 Microsoft Edge WebView2 Runtime，"
            f"下载：{WEBVIEW2_DOWNLOAD_URL}）")


def check_dependencies() -> dict:
    """检查运行依赖。仅供设置页 / 诊断使用，不在正式启动路径上跑。"""
    result = {"required": {}, "optional": {}, "missing_required": []}
    for name, required in REQUIRED_MODULES:
        info = _module_info(name)
        result["required"][name] = info
        if required and not info["ok"]:
            result["missing_required"].append(name)
    for name, note in OPTIONAL_MODULES:
        info = _module_info(name)
        info["note"] = note
        result["optional"][name] = info
    return result


def dependency_lines() -> list:
    deps = check_dependencies()
    lines = []
    for name, info in deps["required"].items():
        mark = "OK" if info["ok"] else "缺失"
        lines.append(f"{name}: {mark} ({info['version'] or info['error']})")
    for name, info in deps["optional"].items():
        mark = "OK" if info["ok"] else "缺失"
        lines.append(f"{name}（可选）: {mark}")
    lines.append(webview2_note())
    return lines


def collect_diagnostics(log_tail_lines=40) -> dict:
    """生成可安全分享的诊断信息（不含任务内容与密钥）。"""
    return {
        "app": app_metadata.about_dict(),
        "python": sys.version.split(" ")[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "paths": app_paths.describe_paths(),
        "dependencies": {k: v for k, v in check_dependencies()["required"].items()},
        "optional_dependencies": {k: v for k, v in check_dependencies()["optional"].items()},
        "webview2": {"ok": webview2_available(), "version": webview2_version()},
        "install_type": "installed" if app_metadata.is_installed_build() else "source",
        "logs": _sanitized_logs(log_tail_lines),
    }


def _sanitized_logs(tail_lines=40) -> dict:
    """只取日志尾部若干行，并做敏感词清洗。"""
    import logging_config
    out = {}
    try:
        log_dir = app_paths.get_logs_dir()
    except OSError:
        return out
    if not log_dir.is_dir():
        return out
    for path in sorted(log_dir.glob("*.log")):
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        out[path.name] = [logging_config.sanitize(line) for line in lines[-tail_lines:]]
    return out


# ---------------------------------------------------------------
# 导出用户数据（不含密钥）
# ---------------------------------------------------------------

def non_sensitive_config_files():
    """返回可导出的配置文件名（过滤掉含敏感字段的配置）。"""
    safe = []
    try:
        config_dir = app_paths.get_config_dir()
    except OSError:
        return safe
    if not config_dir.is_dir():
        return safe
    for path in sorted(config_dir.glob("*.json")):
        if ".corrupt." in path.name:
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        lowered = {str(k).lower() for k in data}
        if any(bad in key.lower() for bad in SENSITIVE_CONFIG_KEYS for key in lowered):
            continue
        safe.append(path)
    return safe


def export_user_data_zip(dest=None, logger=None) -> Path:
    """导出 todo.db + 非敏感配置。不含 API Key，默认不含 logs。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if dest is None:
        dest = app_paths.get_user_data_dir() / f"TaiPlan-export-{stamp}.zip"
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("README.txt", _export_readme())
        db = app_paths.get_database_path()
        if db.is_file():
            zf.write(db, "todo.db")
        for path in non_sensitive_config_files():
            zf.write(path, f"config/{path.name}")
    if logger:
        logger.info("用户数据已导出：%s", dest.name)
    return dest


def _export_readme() -> str:
    return (
        f"{app_metadata.APP_NAME} 数据导出\n"
        f"版本：{version.full_version()}\n"
        f"导出时间：{datetime.now().isoformat(timespec='seconds')}\n"
        "\n"
        "包含：todo.db、config/ 下的非敏感配置。\n"
        "不包含：AI API Key（保存在 Windows 凭据管理器）、日志、备份文件。\n"
        "AI API Key 永远不会写入本导出包中的任何文件。\n"
    )


def export_diagnostics_zip(dest=None, logger=None) -> Path:
    """Developer Mode 用的诊断包。"""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if dest is None:
        dest = app_paths.get_user_data_dir() / f"TaiPlan-diagnostics-{stamp}.zip"
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)

    payload = collect_diagnostics()
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("diagnostics.json",
                    json.dumps(payload, ensure_ascii=False, indent=2))
        zf.writestr("dependencies.txt", "\n".join(dependency_lines()))
    if logger:
        logger.info("诊断包已导出：%s", dest.name)
    return dest
