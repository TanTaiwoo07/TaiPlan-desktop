"""Windows 开机自启动管理（用户 Startup Folder，无需管理员权限）。

第 17 阶段起统一为**一个**启动项：

    %APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\Startup\\Todo App.lnk
        Target  : 安装版 = {app}\\TaiPlan.exe ；源码版 = .venv\\pythonw.exe
        Args    : --autostart
        WorkDir : 程序目录
        Icon    : 应用图标

Inno Setup 安装器创建的就是**同名、同位置、同参数**的快捷方式，
因此：

* 安装器创建的开机启动，在应用内会显示为"已启用"；
* 应用内关闭开机启动，会删掉同一个快捷方式（含安装器创建的）；
* 应用内重新开启，创建完全一致的快捷方式。

（对应文档第 16、17、66、67 条：不出现 TaiPlan / TaiPlan Startup / Todo Startup 三套启动项。）

创建 .lnk 需要 COM，这里一次性调用系统自带的 WScript.Shell；应用的启动与运行
不依赖 PowerShell（那只是一个配置动作）。源码时代遗留的 TodoApp.cmd 仍被识别，
并在关闭时一并清理。
"""

import os
import subprocess
import sys
from pathlib import Path

import app_metadata

PROJECT_DIR = Path(__file__).resolve().parent


def roaming_appdata_dir() -> Path:
    """%APPDATA%（Roaming）目录。

    某些宿主环境里 APPDATA 为空（实测 AutoClaw 宿主 shell 就是 None），
    直接用会得到**相对路径**，于是启动项被写到当前工作目录下的
    Microsoft\\Windows\\... 里 —— 既显示不出"已启用"，还会污染目录。
    因此与 app_paths 同策略回退到 ~/AppData/Roaming，并保证结果是绝对路径。
    """
    value = os.environ.get("APPDATA")
    if value:
        return Path(value)
    try:
        return Path.home() / "AppData" / "Roaming"
    except RuntimeError as exc:
        # 极端环境（环境变量被清空）下无法确定目录：明确报错，由调用方降级，
        # 绝不回退成相对路径去污染当前工作目录。
        raise RuntimeError("无法确定 %APPDATA% 目录") from exc


def _resolve_startup_dir():
    """解析开机启动目录（绝对路径）；环境无法确定时返回 None。

    只在模块加载与测试里调用；运行期请用 ``startup_dir()``。
    """
    try:
        return (roaming_appdata_dir() / "Microsoft" / "Windows" / "Start Menu"
                / "Programs" / "Startup")
    except RuntimeError:
        return None


def startup_dir():
    """当前生效的开机启动目录。

    运行期只读模块级 ``STARTUP_DIR``（测试可整体替换，因此绝不会误写真实
    Startup 目录）；为 None 表示当前环境不支持开机启动。
    """
    return STARTUP_DIR


STARTUP_DIR = _resolve_startup_dir()

# 启动项名称必须与安装器 [Icons] 中的名称完全一致
STARTUP_SHORTCUT_NAME = app_metadata.SHORTCUT_NAME          # "TaiPlan"
STARTUP_LNK_NAME = f"{STARTUP_SHORTCUT_NAME}.lnk"
LEGACY_CMD_NAME = "TodoApp.cmd"                             # 第 13 阶段遗留（源码模式）
LEGACY_LNK_NAME = f"{app_metadata.LEGACY_SHORTCUT_NAME}.lnk"  # 旧身份启动项（只用于迁移清理）
LEGACY_LNK_NAME = f"{app_metadata.LEGACY_SHORTCUT_NAME}.lnk"  # 旧身份启动项（只用于迁移清理）
STARTUP_CMD_NAME = LEGACY_CMD_NAME                          # 向后兼容旧引用


# --------------------------------------------------------------------- 路径
def startup_shortcut_path():
    """统一的启动快捷方式路径（安装版与源码版同一个名字）；环境不支持时 None。"""
    if STARTUP_DIR is None:
        return None
    return STARTUP_DIR / STARTUP_LNK_NAME


def _legacy_cmd_path():
    if STARTUP_DIR is None:
        return None
    return STARTUP_DIR / LEGACY_CMD_NAME


def _startup_cmd_path():
    """兼容旧接口：历史 .cmd 路径。"""
    return _legacy_cmd_path()


def is_frozen_build() -> bool:
    return bool(getattr(sys, "frozen", False))


# --------------------------------------------------------------------- 目标
def _launch_spec():
    """返回 (target, arguments, working_dir, icon)。

    安装版：直接运行 TaiPlan.exe（不经过 cmd / PowerShell / bat / vbs）。
    源码版：用 venv 的 pythonw 跑 launcher.py（失败会写日志并提示，不静默失败）。
    """
    if is_frozen_build():
        exe = Path(sys.executable)
        return str(exe), "--autostart", str(exe.parent), str(exe)

    py = PROJECT_DIR / ".venv" / "Scripts" / "pythonw.exe"
    script = PROJECT_DIR / "launcher.py"
    icon = PROJECT_DIR / "assets" / "app_icon.ico"
    return str(py), f'"{script}" --autostart', str(PROJECT_DIR), str(icon)


def _ps_quote(text: str) -> str:
    """PowerShell 单引号字符串转义：内部的 ' 写成 ''。"""
    return "'" + str(text).replace("'", "''") + "'"


def _create_shortcut(path: Path, target: str, arguments: str, workdir: str, icon: str) -> bool:
    """用 WScript.Shell 创建 .lnk（一次性配置动作）。"""
    script = (
        "$ErrorActionPreference='Stop';"
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut({lnk});"
        "$s.TargetPath={target};"
        "$s.Arguments={args};"
        "$s.WorkingDirectory={wd};"
        "$s.IconLocation={icon};"
        "$s.Description='TaiPlan';"
        "$s.Save()"
    ).format(
        lnk=_ps_quote(str(path)),
        target=_ps_quote(target),
        args=_ps_quote(arguments),
        wd=_ps_quote(workdir),
        icon=_ps_quote(icon),
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, timeout=30,
        )
    except Exception:
        return False
    return proc.returncode == 0 and path.is_file()


def _read_shortcut(path: Path):
    """读回 .lnk 的属性（诊断/测试用）；失败返回 {}。"""
    script = (
        "$ErrorActionPreference='Stop';"
        "$s=(New-Object -ComObject WScript.Shell).CreateShortcut({lnk});"
        "Write-Output ($s.TargetPath + \"`n\" + $s.Arguments + \"`n\" + $s.WorkingDirectory)"
    ).format(lnk=_ps_quote(str(path)))
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, timeout=30,
        )
    except Exception:
        return {}
    if proc.returncode != 0:
        return {}
    lines = (proc.stdout or b"").decode("utf-8", "replace").replace("\r", "").split("\n")
    while len(lines) < 3:
        lines.append("")
    return {"target": lines[0].strip(), "arguments": lines[1].strip(), "working_dir": lines[2].strip()}


# --------------------------------------------------------------------- 公开 API
def enable_startup():
    """开启开机自启动（统一创建 Todo App.lnk）。返回 True/False。"""
    try:
        path = startup_shortcut_path()
        if path is None:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        target, arguments, workdir, icon = _launch_spec()
        ok = _create_shortcut(path, target, arguments, workdir, icon)
        if ok:
            # 历史遗留的 .cmd 一并清掉，避免出现两套启动项
            _remove_quietly(_legacy_cmd_path())
        return bool(ok)
    except Exception:
        return False


def disable_startup():
    """关闭开机自启动（含安装器创建的启动项与历史 .cmd）。返回 True/False。"""
    try:
        _remove_quietly(startup_shortcut_path())
        _remove_quietly(_legacy_cmd_path())
        return True
    except Exception:
        return False


def is_startup_enabled():
    """是否已开启开机自启动。

    安装器（Inno）创建的启动项、应用创建的启动项、历史 .cmd 都算"已启用"，
    保证设置页与真实状态一致。
    """
    path = startup_shortcut_path()
    if path is not None and path.exists():
        return True
    legacy = _legacy_cmd_path()
    return legacy is not None and legacy.exists()


def legacy_startup_paths():
    """旧身份在我们自己固定位置上留下的启动项（精确名单，绝不通配）。"""
    if STARTUP_DIR is None:
        return []
    return [p for p in (STARTUP_DIR / LEGACY_LNK_NAME, STARTUP_DIR / LEGACY_CMD_NAME)
            if p.exists()]


def migrate_legacy_startup() -> dict:
    """把旧 Todo App 启动项迁移为 TaiPlan 启动项（W4 §6）。

    规则：
      * 旧启动项不存在 → 什么都不做（绝不擅自替用户开启）
      * 旧项存在 → 先创建新的 TaiPlan 启动项并**验证成功**，再删除旧项
      * 新项创建失败 → 保留旧项（不让用户升级后丢掉开机启动）
    """
    info = {"migrated": False, "reason": "", "removed": []}
    legacy = legacy_startup_paths()
    if not legacy:
        info["reason"] = "no_legacy_entry"
        return info
    # 新项已经在位：只清理旧项
    if startup_shortcut_path() is not None and startup_shortcut_path().exists():
        for p in legacy:
            _remove_quietly(p)
            info["removed"].append(p.name)
        info["reason"] = "already_migrated"
        return info
    if not enable_startup():
        info["reason"] = "enable_failed_kept_legacy"
        return info
    target = startup_shortcut_path()
    if target is None or not target.exists():
        info["reason"] = "verify_failed_kept_legacy"
        return info
    for p in legacy:
        _remove_quietly(p)
        info["removed"].append(p.name)
    info["migrated"] = True
    info["reason"] = "migrated"
    return info


def legacy_startup_paths():
    """旧身份在我们自己固定位置上留下的启动项（精确名单，绝不通配）。"""
    if STARTUP_DIR is None:
        return []
    return [p for p in (STARTUP_DIR / LEGACY_LNK_NAME, STARTUP_DIR / LEGACY_CMD_NAME)
            if p.exists()]


def migrate_legacy_startup() -> dict:
    """把旧 Todo App 启动项迁移为 TaiPlan 启动项（W4 §6）。

    规则：
      * 旧启动项不存在 → 什么都不做（绝不擅自替用户开启）
      * 旧项存在 → 先创建新的 TaiPlan 启动项并**验证成功**，再删除旧项
      * 新项创建失败 → 保留旧项（不让用户升级后丢掉开机启动）
    """
    info = {"migrated": False, "reason": "", "removed": []}
    legacy = legacy_startup_paths()
    if not legacy:
        info["reason"] = "no_legacy_entry"
        return info
    # 新项已经在位：只清理旧项
    if startup_shortcut_path() is not None and startup_shortcut_path().exists():
        for p in legacy:
            _remove_quietly(p)
            info["removed"].append(p.name)
        info["reason"] = "already_migrated"
        return info
    if not enable_startup():
        info["reason"] = "enable_failed_kept_legacy"
        return info
    target = startup_shortcut_path()
    if target is None or not target.exists():
        info["reason"] = "verify_failed_kept_legacy"
        return info
    for p in legacy:
        _remove_quietly(p)
        info["removed"].append(p.name)
    info["migrated"] = True
    info["reason"] = "migrated"
    return info


def _remove_quietly(path) -> None:
    if path is None:
        return
    try:
        if path.exists():
            path.unlink()
    except Exception:
        pass
