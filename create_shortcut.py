"""创建 / 更新桌面快捷方式（TaiPlan）。

- 全部使用**绝对路径**，不依赖当前工作目录
- Target = wscript.exe（动态定位 System32）
- Arguments = "<绝对路径的 start_todo_silent.vbs>"（带引号，支持含空格 / 中文的路径）
- WorkingDirectory = 项目根目录
- 已存在时安全覆盖
- 创建后回读校验

不使用额外第三方依赖，通过 PowerShell 的 WScript.Shell COM 创建 .lnk。
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

_logger = logging.getLogger("create_shortcut")

from taiplan import app_paths

PROJECT_DIR = app_paths.get_project_root()
SHORTCUT_NAME = "TaiPlan.lnk"
# 旧身份快捷方式（只用于升级清理；名字固定，不打通配）
LEGACY_SHORTCUT_NAME = "Todo App.lnk"
VBS_NAME = "start_todo_silent.vbs"


def _desktop_dir():
    return Path(os.path.expanduser("~")) / "Desktop"


def wscript_path():
    """定位 wscript.exe 的绝对路径。"""
    root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    candidate = root / "System32" / "wscript.exe"
    if candidate.is_file():
        return candidate
    candidate = root / "SysWOW64" / "wscript.exe"
    if candidate.is_file():
        return candidate
    return Path("wscript.exe")


def _ps_quote(value):
    """PowerShell 单引号字符串转义。"""
    return str(value).replace("'", "''")


def build_shortcut_command(target_vbs=None, name=SHORTCUT_NAME, desktop_dir=None,
                           target_exe=None, project_dir=None):
    """构造创建快捷方式的 PowerShell 命令（可单测）。返回 (cmd, lnk_path)。"""
    project = Path(project_dir) if project_dir else PROJECT_DIR
    vbs = Path(target_vbs) if target_vbs else (project / VBS_NAME)
    folder = Path(desktop_dir) if desktop_dir else _desktop_dir()
    lnk = folder / name
    exe = Path(target_exe) if target_exe else wscript_path()
    _icon_file = app_paths.get_assets_dir() / "app_icon.ico"
    _icon = str(_icon_file) if _icon_file.is_file() else ""

    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$sc = $ws.CreateShortcut('{_ps_quote(lnk)}'); "
        f"$sc.TargetPath = '{_ps_quote(exe)}'; "
        f"$sc.Arguments = '\"{_ps_quote(vbs)}\"'; "
        f"$sc.WorkingDirectory = '{_ps_quote(project)}'; "
        "$sc.Description = 'TaiPlan'; "
        f"$sc.IconLocation = '{_ps_quote(_icon)}'; "
        "$sc.Save()"
    )
    return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], lnk


def _read_back(lnk):
    """回读快捷方式属性，用于校验。返回 dict 或 None。"""
    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"$sc = $ws.CreateShortcut('{_ps_quote(lnk)}'); "
        "Write-Output ('T=' + $sc.TargetPath); "
        "Write-Output ('A=' + $sc.Arguments); "
        "Write-Output ('W=' + $sc.WorkingDirectory)"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            capture_output=True, text=True, timeout=30,
        )
    except Exception as e:  # noqa: BLE001
        _logger.warning("shortcut read-back failed: %s", type(e).__name__)
        return None
    result = {}
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if line.startswith("T="):
            result["target"] = line[2:]
        elif line.startswith("A="):
            result["arguments"] = line[2:]
        elif line.startswith("W="):
            result["working_dir"] = line[2:]
    return result or None


def create_desktop_shortcut(target_vbs=None, name=SHORTCUT_NAME, desktop_dir=None):
    """创建 / 覆盖桌面快捷方式。返回 (是否成功, 快捷方式路径, 说明)。"""
    cmd, lnk = build_shortcut_command(target_vbs, name, desktop_dir)
    try:
        lnk.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception as e:  # noqa: BLE001
        _logger.warning("shortcut error: %s", type(e).__name__)
        return False, str(lnk), f"{type(e).__name__}"

    if proc.returncode != 0:
        detail = (proc.stderr or "").strip().splitlines()
        return False, str(lnk), (detail[-1] if detail else "powershell failed")

    info = _read_back(lnk)
    if info and info.get("working_dir") != str(PROJECT_DIR):
        _logger.warning("shortcut working dir mismatch: %s", info.get("working_dir"))
    return True, str(lnk), info or {}


def legacy_shortcut_path(desktop_dir=None):
    """旧身份桌面快捷方式的精确路径（升级时清理用）。"""
    folder = Path(desktop_dir) if desktop_dir else _desktop_dir()
    return folder / LEGACY_SHORTCUT_NAME


def remove_legacy_shortcut(desktop_dir=None) -> dict:
    """删除我们自己创建的旧身份桌面快捷方式（W4 §7）。

    只删除固定文件名 "Todo App.lnk"，不做任何模糊/通配删除。
    """
    info = {"removed": False, "path": ""}
    target = legacy_shortcut_path(desktop_dir)
    info["path"] = str(target)
    try:
        if target.is_file():
            target.unlink()
            info["removed"] = True
            _logger.info("已清理旧身份桌面快捷方式: %s", target.name)
    except OSError as exc:  # noqa: BLE001
        _logger.warning("清理旧快捷方式失败: %s", type(exc).__name__)
    return info


def main():
    ok, lnk, info = create_desktop_shortcut()
    if ok:
        print("已创建/更新快捷方式：" + lnk)
        if isinstance(info, dict):
            print("  Target     : " + str(info.get("target")))
            print("  Arguments  : " + str(info.get("arguments")))
            print("  Start in   : " + str(info.get("working_dir")))
        return 0
    print("创建失败：" + lnk + "  (" + str(info) + ")")
    return 1


if __name__ == "__main__":
    sys.exit(main())
