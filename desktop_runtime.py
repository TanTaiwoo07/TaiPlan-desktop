"""桌面运行主入口。

架构：
    主线程              → pywebview GUI loop（TaiPlan 原生窗口）
    后台 Streamlit 子进程 → http://127.0.0.1:8501
    后台线程            → Reminder Worker
    后台线程            → 系统托盘（pystray，不阻塞 pywebview）
    后台线程            → 单实例 IPC（接收「显示窗口」信号）

启动顺序：
    lock → ipc（控制通道，失败即中止）→ init_db → 注册 App ID → streamlit
        → wait_ready → worker → tray(线程) → 创建窗口 → webview.start()

IPC 是**控制通道**：已持有单实例 lock 却建不起 IPC，等于"实例活着但安装器/第二次
双击都控制不了它"，因此那种情况一律拒绝启动（见 _start_ipc_server 的返回契约）。

命令行：
    python desktop_runtime.py              普通桌面启动（自动显示窗口）
    python desktop_runtime.py --autostart  开机自启（不自动显示窗口）
    python desktop_runtime.py --debug      调试模式（详细日志）
"""

import product_info
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from logging.handlers import RotatingFileHandler
from pathlib import Path

import app_paths
import database
import dynamic_port
import streamlit_runner
from reminder_worker import ReminderWorker
import desktop_window
import tray_app
import windows_app_registration as appreg

PROJECT_DIR = Path(__file__).resolve().parent
LOG_DIR = app_paths.get_logs_dir()
LOCK_PORT = 18721
IPC_PORT = 18722
_selected_port = None
# 本次 Desktop Runtime 的唯一 ownership token（仅用于 runtime / streamlit child /
# port state 的归属判断，不是用户 ID）。每次启动都重新生成。
_runtime_token = None


def get_streamlit_port():
    """当前实际使用的 Streamlit 端口（不再永远硬编码 8501）。"""
    return _selected_port or dynamic_port.DEFAULT_PORT


def streamlit_url():
    return dynamic_port.base_url(get_streamlit_port())


def streamlit_health_url():
    return f"{streamlit_url()}/_stcore/health"


def _choose_streamlit_port():
    """优先 8501；被占用则在 8501~8510 内选择一个可用端口并发布出去。"""
    global _selected_port
    if _selected_port:
        return _selected_port
    try:
        _selected_port = dynamic_port.find_available_port()
    except OSError:
        _selected_port = dynamic_port.DEFAULT_PORT
    # 注意：**不在这里发布 state**。state 必须在 Streamlit child 真正起来、
    # 拿到它的真实 pid 之后才发布（见 start()），否则会出现 pid=None 的假 ownership。
    return _selected_port

_logger = logging.getLogger("desktop_runtime")


def _log(msg, *args):
    print(msg % args if args else msg, flush=True)


def _setup_logging(debug=False):
    # W3：数据目录改名后首次启动时父目录可能还不存在，必须 parents=True
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        LOG_DIR / "runtime.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    _logger.addHandler(handler)
    _logger.setLevel(logging.DEBUG if debug else logging.INFO)
    _logger.propagate = False


def _show_error_box(text):
    """启动失败时弹出简短 Windows MessageBox（正式模式无终端）。

    自动化测试/无界面诊断可用 TODO_APP_NO_GUI_DIALOGS=1 抑制，改为写 stderr。
    """
    import sys as _sys

    _sys.stderr.write((text or "") + "\n")
    if os.environ.get("TODO_APP_NO_GUI_DIALOGS") == "1":
        return
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, text, product_info.APP_DISPLAY_NAME, 0x10)
    except Exception:
        pass


def _acquire_single_instance_lock():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", LOCK_PORT))
        s.listen(1)
        return s
    except OSError:
        s.close()
        return None


def is_runtime_lock_held():
    """单实例 runtime lock 是否被占用（**权威判据**）。

    用 bind 探测，不用 connect：
      * bind 成功 → 没有 runtime lock（探测 socket 只 bind、不 listen，用完立即关闭，
        不会留下 backlog 污染，也不会产生 TIME_WAIT）；
      * bind 失败 → 端口仍被占用 → lock 存在。
    **任何不确定情况一律 fail closed**（当作"有实例在跑"），绝不当成"没有实例"。
    """
    # sock 必须先置 None：连 socket() 本身失败时也要能安全走到 finally，
    # 否则 finally 会对未绑定变量再抛一次异常，fail closed 就失效了。
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sock.bind(("127.0.0.1", LOCK_PORT))
        except OSError:
            return True
        return False
    except Exception:  # noqa: BLE001
        return True
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:  # noqa: BLE001
                pass


def _send_ipc_command(command: bytes, timeout: float = 3.0) -> bool:
    """向运行中的实例发送一条控制命令；连不上返回 False。

    注意：False 只表示"控制通道不可用"，**不代表"没有实例"**。
    """
    try:
        sock = socket.create_connection(("127.0.0.1", IPC_PORT), timeout=timeout)
    except OSError:
        return False
    try:
        sock.sendall(command)
        return True
    except OSError:
        return False
    finally:
        try:
            sock.close()
        except Exception:  # noqa: BLE001
            pass


def _is_port_in_use(port):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.connect(("127.0.0.1", port))
        s.close()
        return True
    except OSError:
        return False


def _streamlit_is_healthy(port=None):
    try:
        url = f"{dynamic_port.base_url(port or get_streamlit_port())}/_stcore/health"
        urllib.request.urlopen(url, timeout=2)
        return True
    except Exception:
        return False


def _start_streamlit():
    """启动 Streamlit server 子进程。

    实际命令行由 streamlit_runner 生成，按运行形态分支：
    - 源码模式：python -m streamlit run app.py ...
    - frozen  ：TaiPlan.exe --streamlit-child --port N
      （frozen 下 sys.executable 是 TaiPlan.exe，绝不能再用 `-m streamlit`）
    """
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return streamlit_runner.start_streamlit_subprocess(
        get_streamlit_port(),
        log_path=LOG_DIR / "streamlit.log",
        cwd=PROJECT_DIR,
    )


def _wait_for_streamlit(proc, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            return False, f"exit_code={proc.returncode}"
        if _streamlit_is_healthy():
            return True, None
        time.sleep(0.5)
    return False, "timeout"


def _signal_existing_instance():
    """通知已运行的实例显示主窗口。"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(3)
        s.connect(("127.0.0.1", IPC_PORT))
        s.sendall(b"SHOW")
        s.close()
        return True
    except Exception:
        return False


class DesktopRuntime:
    def __init__(self, autostart=False, debug=False):
        self.autostart = autostart
        self.debug = debug
        # 启动失败原因（None = 正常；第二实例退出不算失败）
        self.start_error = None
        # 本次启动的唯一 ownership token
        self.runtime_token = uuid.uuid4().hex
        self.lock_socket = None
        self.ipc_socket = None
        self.streamlit_proc = None
        self.worker = None
        self.worker_thread = None
        self.tray = None
        self.tray_thread = None
        self._exit_event = threading.Event()
        self._pending_show = False
        self._webview_started = False
        self._cleaned = False
        self._shutting_down = False

    # ---- 主流程 ----
    def start(self):
        _log("[Todo Runtime] Starting...")
        _log("[Todo Runtime] Acquiring single-instance lock...")
        self.lock_socket = _acquire_single_instance_lock()
        if self.lock_socket is None:
            _log("[Todo Runtime] 已有实例运行，发送显示窗口信号。")
            if _signal_existing_instance():
                _log("[Todo Runtime] 已通知现有实例显示主窗口，本进程退出。")
            else:
                # ★ 不再 webbrowser.open(STREAMLIT_URL)：
                #   该常量根本不存在，而且桌面版在"有实例但控制通道不可用"时突然
                #   弹出浏览器既突兀又可能让人以为开了两个实例。
                #   正确做法：记录 + 明确提示，然后安全退出第二实例。
                msg = ("检测到已有 TaiPlan 实例存在，但 IPC 控制通道不可用，"
                       "无法请求它显示窗口。\n"
                       "为避免出现两个实例，本次启动已安全退出。\n\n"
                       "请从系统托盘打开 TaiPlan；若界面异常，请先退出 TaiPlan 再重新启动。")
                _logger.error(
                    "已有实例存在（runtime lock 被占用）但 IPC 控制通道不可用，"
                    "第二实例安全退出。")
                _log("[Todo Runtime] 已有实例存在但 IPC 控制通道不可用 → 安全退出第二实例。")
                _show_error_box(msg)
            return False

        # ★ IPC 控制通道：拿到 lock 之后**立刻**建立，失败就干净退出。
        #   理由：已持有单实例 lock 却建不起 IPC = "实例活着，但安装器和第二次双击
        #   都控制不了它"，属于危险状态；而且此时还没起 Streamlit / 没建 Tray /
        #   没建窗口，失败回滚最干净（不创建 Tray / pywebview，不进入正常运行态）。
        if not self._start_ipc_server():
            self.start_error = (
                f"IPC 控制通道无法建立（127.0.0.1:{IPC_PORT} 被占用）。"
                "为避免出现『实例活着但无法被控制』的状态，启动已中止。")
            _log("[Todo Runtime] ERROR: 已持有单实例锁但 IPC 不可用 → 拒绝启动并释放资源。")
            try:
                self.cleanup()
            except Exception:  # noqa: BLE001
                pass
            return False
        _log("[Todo Runtime] 控制通道已就绪，继续启动。")

        # 顺序很重要：先迁移旧数据，再 init_database。
        # 反过来的话会先建出一个空库，旧库迁移因「目标已存在」被跳过，
        # 用户会以为任务全部丢失。
        try:
            import first_run
            first_run.run_first_run(logger=_logger, register_app_id=False)
        except Exception:  # noqa: BLE001
            _logger.warning("first run / legacy migration failed", exc_info=True)

        database.init_database()
        _log("[Todo Runtime] Database ready.")

        appreg.ensure_app_registered()
        _log(f"[Todo Runtime] Windows app id ready: {appreg.APP_USER_MODEL_ID}")

        # Streamlit（先尝试复用已发布的健康实例，否则动态选端口）
        # ★ 17.2：**禁止无所有权复用**。
        #   已经拿到 runtime LOCK 就说明当前不存在合法的 owner Runtime；
        #   此时若还健康，只可能是上一次 crash 留下的 orphan —— 一律不复用。
        published = dynamic_port.read_published_state()
        if published:
            published_port = published.get("port")
            published_pid = published.get("pid")
            if published_port and _streamlit_is_healthy(published_port):
                _log(f"[Todo Runtime] 发现上一次遗留的 Streamlit（port={published_port}, "
                     f"pid={published_pid}）→ 不复用，等待 parent watchdog 自行退出……")
                deadline = time.time() + 3.0
                while time.time() < deadline and _streamlit_is_healthy(published_port):
                    time.sleep(0.3)
                if _streamlit_is_healthy(published_port):
                    self.start_error = (
                        "检测到上一次异常退出后残留的后台服务"
                        f"（端口 {published_port}，pid={published_pid}）。\n"
                        "为避免出现两个实例，启动已中止。请稍后重试，或重新启动 Windows。")
                    _log("[Todo Runtime] ERROR: 残留 Streamlit 仍未退出 → fail closed"
                         "（不复用、不强杀未知进程）。")
                    try:
                        self.cleanup()
                    except Exception:  # noqa: BLE001
                        pass
                    return False
                _log("[Todo Runtime] 残留 Streamlit 已自行退出。")
            # 不健康 / 已自行退出 → 清 stale state（compare-and-delete，绝不误删新 owner）
            dynamic_port.clear_published_state(pid=published_pid,
                                               owner_token=published.get("owner_token"))

        port = _choose_streamlit_port()
        _log(f"[Todo Runtime] Selected Streamlit port {port}.")
        global _runtime_token
        _runtime_token = self.runtime_token
        os.environ[streamlit_runner.RUNTIME_TOKEN_ENV] = self.runtime_token
        self.streamlit_proc = _start_streamlit()
        if self.streamlit_proc is not None:
            state = dynamic_port.publish_port(
                port, component="streamlit", pid=self.streamlit_proc.pid,
                parent_pid=os.getpid(), owner_token=self.runtime_token)
            _log(f"[Todo Runtime] 已发布 streamlit state: port={state['port']} "
                 f"pid={state['pid']} token={self.runtime_token[:8]}…")
            _log("[Todo Runtime] Waiting for Streamlit...")
            ok, err = _wait_for_streamlit(self.streamlit_proc, timeout=30)
            if not ok:
                _log(f"[Todo Runtime] Streamlit failed to start within 30 seconds ({err}).")
                _logger.error("streamlit start failed: %s", err)
                self.cleanup()
                _show_error_box("TaiPlan 启动失败，请查看 logs/runtime.log")
                return False
            _log("[Todo Runtime] Streamlit ready.")

        # Reminder Worker（后台线程）
        _log("[Todo Runtime] Starting reminder worker...")
        self.worker = ReminderWorker()
        self.worker_thread = threading.Thread(target=self.worker.run, daemon=True)
        self.worker_thread.start()
        _log("[Todo Runtime] Reminder worker started.")

        # 托盘（后台线程，不阻塞 pywebview）
        _log("[Todo Runtime] Starting system tray...")
        self.tray = tray_app.build_tray(
            on_quit=self.shutdown, on_show_window=desktop_window.show_main_window)
        if self.tray is not None:
            self.tray_thread = threading.Thread(target=self.tray.run, daemon=True)
            self.tray_thread.start()
            _log("[Todo Runtime] System tray ready.")
        else:
            _log("[Todo Runtime] System tray unavailable (pystray/Pillow missing).")

        # 主窗口 + GUI loop（主线程）
        _log("[Todo Runtime] Creating desktop window...")
        desktop_window.create_main_window(
            streamlit_url(), show=not self.autostart,
            on_fully_closed=self._on_window_closed)

        # 窗口创建前若已收到「显示窗口」信号（第二次双击快捷方式），此时补上
        if self._pending_show:
            self._pending_show = False
            _log("[Todo Runtime] 补处理启动期间收到的显示窗口请求。")
            desktop_window.focus_main_window()

        _log("[Todo Runtime] Ready.")
        _log("Todo is running. Exit from the system tray to stop it.")

        try:
            import webview
            self._webview_started = True
            _icon = None
            try:
                # 窗口图标必须是 .ico，PNG 会让 WinForms 后端抛异常
                _icon = desktop_window.window_icon_path()
            except Exception:
                _icon = None
            try:
                if _icon:
                    webview.start(debug=self.debug, icon=_icon)
                else:
                    webview.start(debug=self.debug)
            except Exception as e:  # noqa: BLE001
                _logger.exception("webview icon start failed, retry without icon")
                _log(f"[Todo Runtime] 窗口图标加载失败（{type(e).__name__}），改为无图标启动。")
                webview.start(debug=self.debug)
        except Exception as e:  # noqa: BLE001
            _logger.exception("webview error")
            _log(f"[Todo Runtime] Desktop window failed: {type(e).__name__}: {e}")
            _log("[Todo Runtime] 保持 Streamlit + Worker 运行，按 Ctrl+C 退出。")
            self._wait_forever()

        self.cleanup()
        return True

    def _on_window_closed(self):
        """窗口被真正关闭（非隐藏）时，若设置为完全退出则关闭 Runtime。"""
        pass

    def show_window(self):
        """显示 / 置前主窗口（单实例第二次启动时由 IPC 触发）。"""
        if desktop_window.get_controller() is None:
            # 窗口还没创建好，先记下来，创建后立刻补显示
            self._pending_show = True
            _log("[Todo Runtime] 收到显示窗口请求（窗口尚未就绪，已排队）。")
            return
        _log("[Todo Runtime] 收到显示窗口请求，置前主窗口。")
        desktop_window.focus_main_window()

    # ---- IPC（单实例第二进程 → 显示窗口）----
    def _start_ipc_server(self) -> bool:
        """建立 IPC 控制通道；**失败必须让整个 Runtime 拒绝进入运行态**。

        返回 False 的情形：端口被占 / 绑定失败。绝不能像以前那样"静默继续运行" ——
        那会造成"Runtime 活着，但安装器无法控制它"的危险状态。
        """
        self.ipc_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.ipc_socket.bind(("127.0.0.1", IPC_PORT))
            self.ipc_socket.listen(5)
        except OSError as exc:
            _logger.error("IPC server 绑定 127.0.0.1:%s 失败：%s", IPC_PORT, exc)
            _log(f"[Todo Runtime] ERROR: IPC 控制通道无法建立"
                 f"（127.0.0.1:{IPC_PORT} 被占用）：{exc}")
            try:
                self.ipc_socket.close()
            except Exception:  # noqa: BLE001
                pass
            self.ipc_socket = None
            return False
        threading.Thread(target=self._ipc_loop, daemon=True).start()
        _log(f"[Todo Runtime] IPC control channel ready on 127.0.0.1:{IPC_PORT}.")
        return True

    def _ipc_loop(self):
        while not self._exit_event.is_set():
            try:
                self.ipc_socket.settimeout(1.0)
                conn, _ = self.ipc_socket.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                data = conn.recv(64)
                cmd = data.strip()
                if cmd == b"SHOW":
                    self.show_window()
                elif cmd == b"SHUTDOWN":
                    # 第 17 阶段：安装器/卸载器用 --shutdown 让运行中的实例优雅退出
                    # （停 worker → 关 tray → 关 pywebview → 停 Streamlit child
                    #   → 释放锁与端口 → 退出），不直接 taskkill。
                    _log("[Todo Runtime] 收到 SHUTDOWN 请求。")
                    threading.Thread(target=self.shutdown, daemon=True).start()
            except Exception:
                pass
            finally:
                try:
                    conn.close()
                except Exception:
                    pass

    # ---- 退出 ----
    def shutdown(self):
        if self._shutting_down:
            return
        self._shutting_down = True
        _log("[Todo Runtime] Shutting down...")
        closed = True
        try:
            closed = desktop_window.close_main_window()
        except Exception:  # noqa: BLE001
            closed = False
            _logger.exception("close_main_window raised")
        if not closed:
            # ★ 窗口关不掉就**不宣告成功**：不 set exit_event、不 cleanup，
            #   于是 LOCK 不释放，--shutdown 会以超时(fail closed)=1 收场。
            #   绝不能为了"确保 exit 0"去 os._exit(0) 强行退出。
            _logger.error("主窗口 destroy 失败 → 不宣告退出成功（LOCK 保持占用）")
            _log("[Todo Runtime] 主窗口关闭失败 → 保持运行，不释放 LOCK。")
            self._shutting_down = False
            return
        if not self._webview_started:
            # 没有 pywebview 时由这里完成收尾；pywebview 起来后由 start() 末尾
            # 在 GUI loop 结束后调用 cleanup()。两条路径都以 cleanup 结束，
            # 所以"lock 释放"永远是最后一步。
            self.cleanup()
        self._exit_event.set()

    def cleanup(self):
        if self._cleaned:
            return
        self._cleaned = True
        _logger.info("cleaning up")
        if self.worker:
            try:
                self.worker.stop()
            except Exception:
                pass
        if self.tray:
            try:
                self.tray.stop()
            except Exception:
                pass
        if self.streamlit_proc:
            # terminate → 必要时 kill → 关闭日志句柄，统一在 streamlit_runner 里
            streamlit_runner.stop_streamlit_subprocess(self.streamlit_proc)
        # 退出顺序：worker → tray → Streamlit → 锁 → 心跳/端口 → 收尾
        try:
            database.update_runtime_status("desktop_runtime", pid=os.getpid(),
                                           status="stopped")
        except Exception:
            pass
        try:
            database.delete_runtime_status("reminder_worker")
        except Exception:
            pass
        try:
            # 干净退出时把 WAL 合并回主库（失败不影响退出）
            conn = database.get_connection()
            try:
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass
        try:
            dynamic_port.clear_published_port()
        except Exception:
            pass
        # ★ 顺序契约（安装器依赖它）：worker → tray → Streamlit child → IPC socket
        #   → **单实例 lock 最后释放**。因此"runtime lock 已释放"就等价于
        #   "全部子资源都已收尾"，request_shutdown 才能用 lock 作为权威判据。
        for sock in (self.ipc_socket, self.lock_socket):
            try:
                if sock:
                    sock.close()
            except Exception:
                pass
        self.ipc_socket = None
        self.lock_socket = None
        _logger.info("cleanup done")
        _log("[Todo Runtime] Stopped.")

    def _wait_forever(self):
        try:
            self._exit_event.wait()
        except KeyboardInterrupt:
            self.shutdown()
        self.cleanup()


def request_shutdown(timeout=10.0):
    """让正在运行的实例优雅退出。

    返回（安装器就靠这个退出码决定"能不能覆盖文件"）：
        0 —— 已确认退出：authoritative runtime lock 已释放（= 全部子资源收尾完成），
             或者本来就没有运行中的实例
        1 —— 仍有实例占用 runtime lock（IPC 不可用、或超时未退）→ 调用方**不得**覆盖文件

    三条硬规则：
        * "IPC 连不上"**绝不**等于"没有实例"；
        * 判据是 **single-instance lock 是否释放**，不是 IPC 端口是否消失；
        * 拿不准一律 fail closed（返回 1）。
    """
    # 1) 先看权威判据：runtime lock 在不在
    if not is_runtime_lock_held():
        # ★ 第二层守卫：LOCK 空闲也可能存在 orphan Streamlit child
        #   （父 Runtime 被强杀、watchdog 极端失效）。此时**不能**告诉安装器"已完全退出"。
        state = dynamic_port.read_published_state()
        if state is None:
            _log("[Todo Runtime] --shutdown: 没有运行中的实例（lock 空闲、无 port state），直接退出。")
            return 0
        port = state.get("port")
        if port and _streamlit_is_healthy(port):
            _log(f"[Todo Runtime] --shutdown: LOCK 空闲，但 state 指向的 Streamlit "
                 f"(port={port}, pid={state.get('pid')}) 仍然健康 → 可能存在 orphan child，"
                 f"判为未完全退出（fail closed）。")
            return 1
        # state 存在但服务已不可用 → 清 stale state（compare-and-delete，绝不误删新 owner）
        removed = dynamic_port.clear_published_state(pid=state.get("pid"),
                                                     owner_token=state.get("owner_token"))
        _log(f"[Todo Runtime] --shutdown: lock 空闲、port {port} 已不健康 → "
             f"清理 stale state(removed={removed})，判为已退出。")
        return 0

    # 2) lock 在 → 必须有可用的控制通道
    if not _send_ipc_command(b"SHUTDOWN"):
        _log("[Todo Runtime] --shutdown: runtime lock 仍被占用，但 IPC 控制通道不可用 "
             "→ 判为未退出（fail closed，绝不当成没有实例）。")
        return 1

    # 3) 等 lock 真正释放（而不是等 IPC 端口消失）
    _log("[Todo Runtime] --shutdown: 已发送 SHUTDOWN，等待 runtime lock 释放……")
    deadline = time.time() + max(1.0, float(timeout))
    while time.time() < deadline:
        if not is_runtime_lock_held():
            _log("[Todo Runtime] --shutdown: runtime lock 已释放，实例完成收尾。")
            return 0
        time.sleep(0.25)
    _log("[Todo Runtime] --shutdown: 超时，runtime lock 仍被占用 → 判为未退出。")
    return 1


UNATTENDED_FLAGS = ("--shutdown", "--streamlit-child", "--smoke-test",
                    "--benchmark", "--autostart")


def should_show_error_box(argv=None) -> bool:
    """启动失败时是否允许弹模态错误框。

    无人值守 / 非交互模式**绝不能弹**：Windows 登录时挂一个没人处理的模态窗口，
    会把 --autostart 变成卡住的进程。这些模式只写 runtime.log。
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    return not any(flag in argv for flag in UNATTENDED_FLAGS)


def _maybe_relaunch_after_exit():
    """UI 重启机制（0.1.2）：state/ui_restart.flag 存在 → 优雅退出完成后拉起新实例。

    标记由设置页"立即重启"写入；这里只负责"退出后拉起"，不判断原因。
    拉起失败绝不弹窗、不重试（避免重启风暴）——下次用户双击图标照常可用。
    """
    try:
        flag = app_paths.get_restart_flag_path()
        if not flag.is_file():
            return
        payload = flag.read_text(encoding="utf-8", errors="replace")[:256]
        flag.unlink(missing_ok=True)
        _log("[Todo Runtime] 检测到 UI 重启标记，重新拉起应用……")
        exe = Path(sys.executable)
        if exe.name.lower() == "python.exe" or exe.name.lower() == "pythonw.exe":
            command = [str(exe), "-X", "utf8", str(PROJECT_DIR / "desktop_runtime.py")]
        else:
            command = [str(exe)]
        subprocess.Popen(command, cwd=str(PROJECT_DIR),
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _log("[Todo Runtime] UI 重启：新进程已拉起（%s）", payload.strip() or "no-detail")
    except Exception as exc:  # noqa: BLE001
        _log("[Todo Runtime] UI 重启失败（不影响本次退出）：%s", type(exc).__name__)


def main():
    args = sys.argv[1:]
    # 防呆：子进程模式绝不能又起一套桌面运行时（Tray/Worker/pywebview/单实例锁）
    if os.environ.get("TODO_APP_STREAMLIT_CHILD") == "1":
        _log("[Todo Runtime] 检测到 TODO_APP_STREAMLIT_CHILD=1，拒绝启动桌面运行时。")
        return
    autostart = "--autostart" in args
    debug = "--debug" in args
    _setup_logging(debug=debug)
    try:
        import logging_config
        logging_config.setup_logging("runtime", level=logging.DEBUG if debug else logging.INFO)
        logging_config.install_excepthook("runtime")
    except Exception:
        pass
    runtime = DesktopRuntime(autostart=autostart, debug=debug)
    try:
        started = runtime.start()
        if not started and runtime.start_error:
            # 已持锁但 IPC 不可用 = 启动失败，必须非 0 退出（不能悄悄"起来了又不受控"）
            _logger.error("desktop runtime 启动失败：%s", runtime.start_error)
            if should_show_error_box(args):
                _show_error_box("TaiPlan 启动失败。\n" + runtime.start_error)
            else:
                # Release EXE 没有 console，_log() 的 stdout 内容实际不可见，
                # 因此无人值守路径必须**同时写 runtime logger**，让 runtime.log
                # 明确留下启动失败原因（不弹框、不改退出码、不重复刷屏）。
                _logger.error("无人值守模式（%s）：启动失败只写日志，不弹错误框：%s",
                              " ".join(args) or "(无参数)", runtime.start_error)
                _log("[Todo Runtime] 无人值守模式：启动失败只写日志，不弹错误框。")
            raise SystemExit(2)
    except KeyboardInterrupt:
        runtime.shutdown()
    except Exception as exc:  # noqa: BLE001
        _logger.exception("desktop runtime failed")
        _maybe_relaunch_after_exit()
    else:
        _maybe_relaunch_after_exit()
        if should_show_error_box(args):
            _show_error_box("TaiPlan 启动失败。\n详细原因已写入日志目录。")
        else:
            _logger.error("无人值守模式（%s）：启动异常只写日志，不弹错误框。",
                          " ".join(args) or "(无参数)", exc_info=True)
            _log("[Todo Runtime] 无人值守模式：异常只写日志，不弹错误框。")
        try:
            runtime.cleanup()
        except Exception:
            pass
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
