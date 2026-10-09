"""正式启动入口（无控制台）。

由 start_todo_silent.vbs 用 pythonw.exe 调用。

职责：
- 校验项目路径 / pythonw / desktop_runtime.py
- 创建 logs 目录
- 把启动过程写入 logs/launcher.log
- 启动 desktop_runtime.py（detached）
- 启动失败时弹 Windows MessageBox，绝不静默退出

用法：
    .venv\\Scripts\\pythonw.exe launcher.py            # 正式启动（显示窗口）
    .venv\\Scripts\\pythonw.exe launcher.py --autostart # 开机自启（不显示窗口）
    .venv\\Scripts\\python.exe  launcher.py --check     # 自检，不启动 App
"""

import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from taiplan import app_paths

PROJECT_DIR = app_paths.get_project_root()
LOG_DIR = app_paths.get_logs_dir()
LAUNCHER_LOG = LOG_DIR / "launcher.log"
PYTHONW = PROJECT_DIR / ".venv" / "Scripts" / "pythonw.exe"
RUNTIME = PROJECT_DIR / "taiplan" / "desktop_runtime.py"
VBS = PROJECT_DIR / "start_todo_silent.vbs"

LOG_MAX_BYTES = 1_000_000          # 约 1 MB 后轮转
_SENSITIVE = ("api_key", "apikey", "token", "secret", "password")


def _ensure_log_dir():
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        return True
    except Exception:
        return False


def _rotate_if_needed():
    try:
        if LAUNCHER_LOG.exists() and LAUNCHER_LOG.stat().st_size > LOG_MAX_BYTES:
            rotated = LAUNCHER_LOG.with_name("launcher.log.1")
            try:
                rotated.unlink()
            except OSError:
                pass
            LAUNCHER_LOG.replace(rotated)
    except Exception:
        pass


def log(message):
    """写入 launcher.log（自动过滤疑似敏感内容）。"""
    low = str(message).lower()
    if any(word in low for word in _SENSITIVE):
        message = "(已过滤可能的敏感内容)"
    _ensure_log_dir()
    _rotate_if_needed()
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} [launcher] {message}\n"
    try:
        with open(LAUNCHER_LOG, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        pass


def message_box(text, title="TaiPlan"):
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, str(text), title, 0x10)  # MB_ICONERROR
    except Exception:
        pass


# ---------------------------------------------------------------
# 自检
# ---------------------------------------------------------------

def run_checks():
    """返回 (全部通过?, [(名称, 是否通过, 说明)...])。"""
    checks = []

    checks.append(("project dir", PROJECT_DIR.is_dir(), str(PROJECT_DIR)))
    checks.append(("pythonw", PYTHONW.is_file(), str(PYTHONW)))
    checks.append(("desktop_runtime.py", RUNTIME.is_file(), str(RUNTIME)))
    checks.append(("start_todo_silent.vbs", VBS.is_file(), str(VBS)))
    checks.append(("launcher paths", True, "launcher.py / logs / 项目根目录"))

    ok_logs = _ensure_log_dir() and LOG_DIR.is_dir()
    checks.append(("logs directory", ok_logs, str(LOG_DIR)))

    for module, label in (("streamlit", "Streamlit"),
                          ("webview", "pywebview"),
                          ("pystray", "pystray"),
                          ("winotify", "winotify")):
        try:
            __import__(module)
            checks.append((label, True, module))
        except Exception as e:
            checks.append((label, False, f"{module}: {type(e).__name__}"))

    return all(c[1] for c in checks), checks


def cmd_check():
    ok, checks = run_checks()
    print("TaiPlan 自检 (launcher --check)")
    for name, passed, detail in checks:
        print(f"[{'OK' if passed else 'FAIL'}] {name}" + ("" if passed else f"  <- {detail}"))
    print("结果：" + ("全部通过" if ok else "存在失败项"))
    log(f"--check result: {'OK' if ok else 'FAIL'}")
    return 0 if ok else 1


# ---------------------------------------------------------------
# 启动
# ---------------------------------------------------------------

def launch(extra_args=None):
    extra_args = list(extra_args or [])
    log("---- launch attempt ----")
    log(f"timestamp: {datetime.now().isoformat(timespec='seconds')}")
    log(f"project directory: {PROJECT_DIR}")
    log(f"pythonw path: {PYTHONW}")
    log(f"desktop_runtime path: {RUNTIME}")
    log(f"args: {extra_args}")

    if not PROJECT_DIR.is_dir():
        log("ERROR: project directory missing")
        message_box("TaiPlan 启动失败：\n未找到项目目录。\n" + str(PROJECT_DIR))
        return 2

    if not PYTHONW.is_file():
        log("ERROR: pythonw not found")
        message_box("TaiPlan 启动失败：\n未找到虚拟环境 Python。\n请先运行开发环境安装。\n\n"
                    + str(PYTHONW))
        return 3

    if not RUNTIME.is_file():
        log("ERROR: desktop_runtime.py not found")
        message_box("TaiPlan 启动失败：\n未找到 desktop_runtime.py。\n\n" + str(RUNTIME))
        return 4

    if not _ensure_log_dir():
        log("WARNING: cannot create logs directory")

    cmd = [str(PYTHONW), "-m", "taiplan.desktop_runtime"] + extra_args
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(PROJECT_DIR),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        log(f"ERROR: spawn failed: {type(e).__name__}: {e}")
        message_box("TaiPlan 启动失败：\n无法启动后台进程。\n请查看 logs\\launcher.log")
        return 5

    log(f"spawned desktop_runtime pid={proc.pid}")

    # 短暂观察：若立即以非 0 退出，说明启动真的失败（已运行时会以 0 正常退出）
    time.sleep(3)
    code = proc.poll()
    if code is not None and code != 0:
        log(f"ERROR: desktop_runtime exited immediately, code={code}")
        message_box("TaiPlan 启动失败：\n后台进程立即退出（code "
                    + str(code) + "）。\n请查看 logs\\runtime.log")
        return 6

    log("launch ok")
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--check" in argv:
        return cmd_check()
    return launch([a for a in argv if a in ("--autostart", "--debug")])


if __name__ == "__main__":
    sys.exit(main())
