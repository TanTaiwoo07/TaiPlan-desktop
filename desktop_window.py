"""pywebview 桌面主窗口封装。

把 Streamlit（http://127.0.0.1:8501）作为本地 URL 载入一个原生窗口，
不重写任何 Streamlit UI。

窗口关闭行为由 runtime_config.close_to_tray 决定：
    True （默认）→ 隐藏到系统托盘，Runtime / Worker / 通知继续运行
    False        → 完全退出（destroy 窗口，使 webview.start() 返回）
"""

import logging
import time
from pathlib import Path

import product_info

import product_info

import app_paths

import runtime_config

_logger = logging.getLogger("desktop_window")

WINDOW_TITLE = product_info.APP_DISPLAY_NAME   # 显示层品牌（exe 名留 W4）
DEFAULT_WIDTH = 1200
DEFAULT_HEIGHT = 820
MIN_WIDTH = 900
MIN_HEIGHT = 650
ASSETS_DIR = app_paths.get_assets_dir()


def _centered_position(width, height):
    """把窗口放在屏幕水平居中、垂直略偏上的位置。"""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        sw = user32.GetSystemMetrics(0)
        sh = user32.GetSystemMetrics(1)
        return max(0, (sw - width) // 2), max(0, (sh - height) // 3)
    except Exception:
        return None, None


def _background_color():
    """与当前主题一致的初始背景色，避免启动瞬间刺眼白屏。"""
    try:
        import ui.theme as _theme
        mode = _theme.resolve_mode()
        return _theme.tokens(mode)["bg_primary"]
    except Exception:
        return "#F6F7F9"


def icon_path():
    """托盘 / 通知用（PNG）。"""
    candidate = ASSETS_DIR / "app_icon.png"
    return str(candidate) if candidate.is_file() else None


def window_icon_path():
    """pywebview 窗口图标必须是 .ico（.NET Icon 不接受 PNG）。"""
    candidate = ASSETS_DIR / "app_icon.ico"
    if candidate.is_file():
        return str(candidate)
    return None


class WindowController:
    def __init__(self, url, on_fully_closed=None):
        self.url = url
        self.on_fully_closed = on_fully_closed
        self.window = None
        self._force_close = False

    def create(self, show=True):
        """创建主窗口。show=False 时隐藏创建（--autostart）。"""
        import webview
        x, y = _centered_position(DEFAULT_WIDTH, DEFAULT_HEIGHT)
        self.window = webview.create_window(
            WINDOW_TITLE,
            self.url,
            width=DEFAULT_WIDTH,
            height=DEFAULT_HEIGHT,
            min_size=(MIN_WIDTH, MIN_HEIGHT),
            resizable=True,
            hidden=not show,
            background_color=_background_color(),
            x=x,
            y=y,
        )
        self.window.events.closing += self._on_closing
        return self.window

    # ---- 关闭行为 ----
    def _on_closing(self):
        cfg = runtime_config.load_runtime_config()
        if self._force_close or not cfg.get("close_to_tray", True):
            return True  # 允许关闭
        try:
            self.window.hide()
        except Exception:
            pass
        _logger.info("window hidden to tray")
        return False  # 阻止关闭

    # ---- 窗口操作 ----
    def show(self):
        if not self.window:
            return
        try:
            self.window.show()
            self.window.restore()
        except Exception:
            pass

    def hide(self):
        if not self.window:
            return
        try:
            self.window.hide()
        except Exception:
            pass

    def focus(self):
        """显示并尽量把窗口带到最前（已运行时再次双击快捷方式走这里）。"""
        if not self.window:
            return
        self.show()
        # pywebview 没有 bring_to_front；用 on_top 短暂置顶再取消，
        # 让 Windows 把窗口激活到前台。
        try:
            self.window.on_top = True
            time.sleep(0.08)
            self.window.on_top = False
        except Exception:
            pass
        try:
            self.window.restore()
        except Exception:
            pass

    def destroy(self) -> bool:
        """真正关闭窗口（使 webview.start() 返回）。

        成功 / 本来就没有窗口 → True；destroy 抛异常 → 记录 exception 并返回 False
        （调用方据此**不得**宣告退出成功，见 desktop_runtime.shutdown 的契约）。
        """
        self._force_close = True
        if not self.window:
            return True
        try:
            self.window.destroy()
            return True
        except Exception:  # noqa: BLE001
            import logging

            logging.getLogger("todo.window").exception("close_main_window: destroy 失败")
            return False


# ---------------------------------------------------------------
# 模块级单例（供 tray / runtime 调用）
# ---------------------------------------------------------------

_controller = None


def create_main_window(url, show=True, on_fully_closed=None):
    """创建并注册主窗口 controller。"""
    global _controller
    _controller = WindowController(url, on_fully_closed=on_fully_closed)
    return _controller.create(show=show)


def get_controller():
    return _controller


def show_main_window():
    if _controller:
        _controller.show()


def hide_main_window():
    if _controller:
        _controller.hide()


def focus_main_window():
    if _controller:
        _controller.focus()


def close_main_window() -> bool:
    """关闭主窗口；成功或本来没有窗口返回 True，destroy 失败返回 False。"""
    if not _controller:
        return True
    return bool(_controller.destroy())
