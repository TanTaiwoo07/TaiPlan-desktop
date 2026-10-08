"""Streamlit server 的统一启动 / 托管，屏蔽 source 与 frozen 的差别。

source 模式
    python -m streamlit run app.py --server.headless true ...

frozen 模式（PyInstaller）
    TaiPlan.exe --streamlit-child --port 8501

**frozen 下绝不使用 ``-m streamlit``**：那时 ``sys.executable`` 是 TaiPlan.exe，
``TaiPlan.exe -m streamlit ...`` 是无意义的调用。改用自身可执行文件的
``--streamlit-child`` 子模式。

子模式内部不使用命令行，直接调用 Streamlit 1.64.0 的进程内 API
（已在当前版本实测）：``streamlit.web.bootstrap.load_config_options()`` +
``bootstrap.run(main_script_path, is_hello, args, flag_options)``，
与 ``streamlit/web/cli.py::_main_run`` 的做法一致。
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
import sys
from pathlib import Path

import app_paths

CHILD_FLAG = "--streamlit-child"
CHILD_ENV = "TODO_APP_STREAMLIT_CHILD"
PORT_ENV = "TODO_APP_STREAMLIT_PORT"

DEFAULT_PORT = 8501


# ----------------------------------------------------------------------
# 模式判断与命令构造
# ----------------------------------------------------------------------

def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包环境中。"""
    return app_paths.is_frozen()


def inject_child_marker(env=None) -> dict:
    """把 ``TODO_APP_STREAMLIT_CHILD=1`` 注入子进程环境。"""
    env = dict(os.environ if env is None else env)
    env[CHILD_ENV] = "1"
    return env


def app_script_path() -> Path:
    """app.py 的位置（源码 = 项目根；frozen = _MEIPASS / 可执行文件目录）。"""
    return app_paths.resource_path("app.py")


def streamlit_command(port) -> list:
    """返回启动 Streamlit server 的命令行参数列表。

    这是整个打包阶段最关键的一处分支：
    frozen 下结果里**不能**出现 ``-m`` 或 ``streamlit``。
    """
    port = int(port)
    if is_frozen():
        return [sys.executable, CHILD_FLAG, "--port", str(port)]
    command = [
        sys.executable,
        "-m", "streamlit", "run", str(app_script_path()),
        "--server.headless", "true",
        "--server.port", str(port),
        "--browser.gatherUsageStats", "false",
    ]
    try:
        import ui.theme as _theme

        for key, value in _theme.streamlit_theme_options().items():
            command += [f"--{key}", str(value)]
    except Exception:  # noqa: BLE001
        pass
    return command


def child_flag_options(port) -> dict:
    """子模式进程内启动 Streamlit 时使用的配置项。

    键名用的是**配置项名**（bootstrap.load_config_options 会把下划线换成点，
    点号形式原样保留）。
    """
    options = {
        # PyInstaller 打包后 streamlit/__file__ 不含 site-packages，
        # Streamlit 据此把 developmentMode 判成 True，而它与 server.port 互斥
        # （config._check_conflicts 会直接抛 RuntimeError）。必须显式关掉。
        "global.developmentMode": False,
        "server.headless": True,
        "server.port": int(port),
        "browser.gatherUsageStats": False,
        # ★ W1.4（17.3）：frozen 正式版隐藏 Streamlit 外壳（Deploy / ⋮ 主菜单 /
        #   framework toolbar）。来源：`streamlit config show` 实测支持的官方配置，
        #   不是猜测；source dev 模式不设这一项，保留开发工具。
        "client.toolbarMode": "minimal",
    }
    # ★ 0.1.2：把用户的外观选择传成 Streamlit 主题基座。
    #   没有这一步，"系统深色 + 应用浅色"时 widget 与 components iframe
    #   （日历）仍按系统 dark 渲染（黑按钮/隐形文字/深色日历）。
    try:
        import ui.theme as _theme

        options.update(_theme.streamlit_theme_options())
    except Exception:  # noqa: BLE001
        # 主题参数只是外观；读不到配置绝不能拦住启动
        pass
    return options


# 父进程所有权（child 端 watchdog 用；不硬编码任何值）
PARENT_PID_ENV = "TODO_APP_PARENT_PID"
PARENT_TOKEN_ENV = "TODO_APP_PARENT_START_TOKEN"
RUNTIME_TOKEN_ENV = "TODO_APP_RUNTIME_TOKEN"
WATCHDOG_INTERVAL = 0.5      # 秒：检查父进程的频率
WATCHDOG_GRACE = 2.0         # 秒：启动后先宽限一会儿，避免与父进程启动竞争


def process_start_token(pid):
    """进程创建时间（100ns 单位），用于防 PID reuse 的 ownership 校验。

    纯 ctypes，不引入 psutil。取不到返回 None。
    """
    try:
        import ctypes
        import ctypes.wintypes as wt

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            created = wt.FILETIME()
            exited = wt.FILETIME()
            kernel = wt.FILETIME()
            user = wt.FILETIME()
            if not kernel32.GetProcessTimes(handle, ctypes.byref(created),
                                            ctypes.byref(exited),
                                            ctypes.byref(kernel), ctypes.byref(user)):
                return None
            return (created.dwHighDateTime << 32) | created.dwLowDateTime
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return None


def is_process_alive(pid):
    """PID 是否仍存活（Windows 用进程句柄，最可靠）。

    保守原则：**只有明确判定"进程不存在"才返回 False**，其余情况（权限不足、
    探测异常、非法输入）一律返回 True —— watchdog 绝不能因为探测异常而杀 child。
    """
    try:
        pid = int(pid)
        if pid <= 0:
            return True
        if os.name != "nt":
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                return False
        import ctypes

        SYNCHRONIZE = 0x00100000
        QUERY_LIMITED = 0x1000
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(SYNCHRONIZE | QUERY_LIMITED, False, pid)
        if not handle:
            err = kernel32.GetLastError()
            # 87 = ERROR_INVALID_PARAMETER（PID 不存在）→ 明确已死
            return err != 87
        try:
            # 0x102 = WAIT_TIMEOUT（还活着）；0 = WAIT_OBJECT_0（已退出）
            return kernel32.WaitForSingleObject(handle, 0) == 0x102
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return True


def _clear_state_if_owner(owner_token=None):
    """父进程已死时 best-effort 清 state —— 必须 compare-and-delete。"""
    try:
        import dynamic_port

        removed = dynamic_port.clear_published_state(pid=os.getpid(),
                                                     owner_token=owner_token)
        print(f"[streamlit-child] state 清理(compare-and-delete)={removed}", flush=True)
    except Exception:  # noqa: BLE001
        pass


def _parent_watchdog(parent_pid, parent_token=None, on_parent_gone=None):
    """父 Runtime 消失后，让整个 child 进程退出（crash fallback）。

    bootstrap.run() 阻塞主线程，所以在 worker thread 里不能只 sys.exit()
    （那只结束线程），必须用 os._exit() —— 且仅在**已确认父进程死亡**的分支使用。
    """
    import sys as _sys

    time.sleep(WATCHDOG_GRACE)
    while True:
        try:
            alive = is_process_alive(parent_pid)
            if alive and parent_token:
                # PID 复用防护：PID 活着但创建时间变了 → 原父进程其实已经死了
                current = process_start_token(parent_pid)
                if current is not None and current != parent_token:
                    alive = False
            if not alive:
                msg = (f"[streamlit-child] 父 Runtime (pid={parent_pid}) 已消失，"
                       f"child 自行退出以避免成为 orphan。")
                try:
                    print(msg, file=_sys.stderr, flush=True)
                except Exception:  # noqa: BLE001
                    pass
                if on_parent_gone is None:
                    on_parent_gone = _clear_state_if_owner
                try:
                    on_parent_gone(os.environ.get(RUNTIME_TOKEN_ENV))
                except Exception:  # noqa: BLE001
                    pass
                os._exit(0)
        except Exception:  # noqa: BLE001
            pass   # 任何异常都不干预，继续观察
        time.sleep(WATCHDOG_INTERVAL)


# ----------------------------------------------------------------------
# 父进程侧：启动子进程
# ----------------------------------------------------------------------

def start_streamlit_subprocess(port, log_path=None, cwd=None,
                               owner_token=None) -> subprocess.Popen:
    """启动 Streamlit server 子进程，返回 Popen。stdout/stderr 合并进日志。"""
    port = int(port)
    cmd = streamlit_command(port)
    env = inject_child_marker()
    env[PORT_ENV] = str(port)
    # 父进程所有权：child 侧 watchdog 靠它判断父 Runtime 是否已消失（不硬编码）
    parent_pid = os.getpid()
    env[PARENT_PID_ENV] = str(parent_pid)
    token = process_start_token(parent_pid)
    if token is not None:
        env[PARENT_TOKEN_ENV] = str(token)
    # 本次 Runtime 的唯一 token：child 退出时靠它确认 state 是否还属于自己。
    # 参数优先；没传就回退读父进程环境变量（调用方不必改动）。
    owner_token = owner_token or os.environ.get(RUNTIME_TOKEN_ENV)
    if owner_token:
        env[RUNTIME_TOKEN_ENV] = str(owner_token)

    stdout = subprocess.DEVNULL
    log_file = None
    if log_path is not None:
        log_path = Path(log_path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = open(log_path, "ab", buffering=0)
        stdout = log_file

    workdir = str(cwd) if cwd else (str(app_paths.get_project_root()) if is_frozen() else None)
    proc = subprocess.Popen(
        cmd,
        cwd=workdir,
        env=env,
        stdout=stdout,
        stderr=subprocess.STDOUT if log_file else subprocess.DEVNULL,
    )
    # 供 stop 时关闭文件句柄
    proc._todo_log_file = log_file  # noqa: SLF001
    return proc


def stop_streamlit_subprocess(proc, timeout=8.0) -> None:
    """终止子进程并关闭它的日志句柄（幂等）。"""
    if proc is None:
        return
    if getattr(proc, "_todo_stopped", False) is True:
        return
    try:
        proc._todo_stopped = True  # noqa: SLF001
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    pass
    finally:
        log_file = getattr(proc, "_todo_log_file", None)
        if log_file is not None:
            try:
                log_file.close()
            except OSError:
                pass


# ----------------------------------------------------------------------
# 子进程侧：进程内跑 Streamlit server
# ----------------------------------------------------------------------

def run_streamlit_child(port=None) -> int:
    """在当前进程内启动 Streamlit server（frozen 子进程模式）。

    **只运行 Streamlit**：不碰桌面运行时、不建单实例锁、不起 Tray/Worker。
    """
    os.environ[CHILD_ENV] = "1"
    if port is None:
        port = int(os.environ.get(PORT_ENV, DEFAULT_PORT))
    port = int(port)

    script = app_script_path()
    if not script.is_file():
        sys.stderr.write(f"app.py not found: {script}\n")
        return 3

    # 必须在 load_config_options 之前设置，Streamlit 用它定位 config.toml / secrets
    from streamlit import config as st_config

    st_config._main_script_path = os.path.abspath(str(script))

    from streamlit.web import bootstrap

    flag_options = child_flag_options(port)
    bootstrap.load_config_options(flag_options=flag_options)
    # 父进程 watchdog（crash fallback）：父 Runtime 异常消失时 child 自行退出，
    # 避免出现『Runtime lock 已释放、Streamlit 仍占着 exe/DLL』的 orphan 状态。
    parent_pid = os.environ.get(PARENT_PID_ENV)
    if parent_pid and str(parent_pid).isdigit():
        _token = os.environ.get(PARENT_TOKEN_ENV) or ''
        threading.Thread(
            target=_parent_watchdog,
            args=(int(parent_pid), int(_token) if _token.isdigit() else None),
            name='parent-watchdog', daemon=True).start()
        print(f"[streamlit-child] 已监听父进程 pid={parent_pid}", flush=True)
    else:
        print("[streamlit-child] 未收到父进程 PID，watchdog 未启用。", flush=True)
    bootstrap.run(str(script), False, [], flag_options)
    return 0
