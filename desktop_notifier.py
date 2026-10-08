"""桌面系统通知。

统一接口 DesktopNotifier，内部按优先级尝试多个 provider：
    1. ToastedNotifier         —— toasted（需 winsdk），可显式注册 AUMID
    2. PowerShellToastNotifier —— 注册 AUMID + PowerShell WinRT，零额外依赖
    3. WinotifyNotifier        —— winotify 兜底

业务代码只依赖 DesktopNotifier，不直接 import toasted / winotify。
notify() 返回 NotificationResult(success, provider, error)。
"""

import logging
import os
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

from windows_app_registration import APP_USER_MODEL_ID, APP_DISPLAY_NAME, ensure_app_registered

_logger = logging.getLogger("desktop_notifier")

import app_paths

PROJECT_DIR = app_paths.get_project_root()
TOAST_PS1 = app_paths.resource_path(Path("tools") / "toast.ps1")

HEADING_NORMAL = "任务到期"
HEADING_URGENT = "紧急任务"


@dataclass
class NotificationResult:
    success: bool
    provider: str
    error: str = None


def _decorate(heading, urgent):
    return ("🚨 " if urgent else "⏰ ") + heading


class BaseNotifier:
    """provider 基类：接收最终标题（已装饰），不做业务判断。"""

    name = "base"

    def send(self, title, message, urgent=False, url=None):
        raise NotImplementedError


class ToastedNotifier(BaseNotifier):
    """toasted（Windows WinRT toast，支持显式注册 App ID）。"""

    name = "toasted"

    def __init__(self, app_id=APP_USER_MODEL_ID):
        self.app_id = app_id

    def send(self, title, message, urgent=False, url=None):
        try:
            import asyncio
            from toasted import Toast, Text, ToastDuration
        except Exception as e:
            return NotificationResult(False, self.name, f"unavailable: {type(e).__name__}")

        outcome = {"err": None}

        def _run():
            try:
                toast = Toast(
                    app_id=self.app_id,
                    duration=ToastDuration.LONG if urgent else ToastDuration.SHORT,
                )
                toast.elements.append(Text(title))
                toast.elements.append(Text(message))
                asyncio.run(toast.show())
            except Exception as e:  # noqa: BLE001
                outcome["err"] = f"{type(e).__name__}: {e}"

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout=3.0)
        if t.is_alive():
            # show() 仍在等待用户交互/自动消失 = 已成功显示
            return NotificationResult(True, self.name)
        if outcome["err"]:
            return NotificationResult(False, self.name, outcome["err"][:200])
        return NotificationResult(True, self.name)


class PowerShellToastNotifier(BaseNotifier):
    """注册 AUMID + PowerShell WinRT 发送通知（无第三方 Python 依赖）。"""

    name = "powershell"

    def __init__(self, app_id=APP_USER_MODEL_ID):
        self.app_id = app_id

    def send(self, title, message, urgent=False, url=None):
        if not TOAST_PS1.exists():
            return NotificationResult(False, self.name, "toast.ps1 missing")
        ensure_app_registered()
        env = dict(os.environ)
        env["TODO_TOAST_APPID"] = self.app_id
        env["TODO_TOAST_TITLE"] = title
        env["TODO_TOAST_MSG"] = message
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", str(TOAST_PS1)],
                env=env, capture_output=True, text=True, timeout=15,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except Exception as e:  # noqa: BLE001
            return NotificationResult(False, self.name, f"{type(e).__name__}: {e}"[:200])
        if proc.returncode == 0 and "TOAST_SENT" in (proc.stdout or ""):
            return NotificationResult(True, self.name)
        err = (proc.stderr or proc.stdout or "").strip().splitlines()
        return NotificationResult(False, self.name, (err[-1] if err else "unknown")[:200])


class WinotifyNotifier(BaseNotifier):
    """winotify 兜底实现。"""

    name = "winotify"

    def __init__(self, app_id=APP_USER_MODEL_ID):
        self.app_id = app_id

    def send(self, title, message, urgent=False, url=None):
        try:
            from winotify import Notification as WinNotify
        except Exception as e:
            return NotificationResult(False, self.name, f"unavailable: {type(e).__name__}")
        try:
            n = WinNotify(app_id=self.app_id, title=title, msg=message)
            if url:
                try:
                    n.add_actions(label=t('tray.show'), launch=url)
                except Exception:
                    pass
            n.show()
            return NotificationResult(True, self.name)
        except Exception as e:  # noqa: BLE001
            return NotificationResult(False, self.name, f"{type(e).__name__}: {e}"[:200])


class DesktopNotifier:
    """统一桌面通知入口。"""

    def __init__(self, app_id=APP_USER_MODEL_ID, app_name=APP_DISPLAY_NAME, providers=None):
        self.app_id = app_id
        self.app_name = app_name
        self._providers = providers if providers is not None else [
            ToastedNotifier(app_id),
            PowerShellToastNotifier(app_id),
            WinotifyNotifier(app_id),
        ]

    def _dispatch(self, title, message, urgent=False, url=None):
        last = None
        for p in self._providers:
            try:
                result = p.send(title, message, urgent=urgent, url=url)
            except Exception as e:  # noqa: BLE001
                result = NotificationResult(False, p.name, f"{type(e).__name__}: {e}"[:200])
            _logger.info("notify provider=%s success=%s app_id=%s",
                         result.provider, result.success, self.app_id)
            if result.success:
                return result
            _logger.warning("notify provider=%s failed: %s", result.provider, result.error)
            last = result
        return last or NotificationResult(False, "none", "no provider available")

    def notify(self, heading, message, urgent=False, url=None):
        """业务通知：标题加 ⏰ / 🚨 前缀。"""
        return self._dispatch(_decorate(heading, urgent), message, urgent=urgent, url=url)

    def notify_raw(self, title, message, urgent=False, url=None):
        """原样标题通知（用于测试通知）。"""
        return self._dispatch(title, message, urgent=urgent, url=url)

    def notify_task_due(self, message, urgent=False, url=None):
        heading = HEADING_URGENT if urgent else HEADING_NORMAL
        return self.notify(heading, message, urgent=urgent, url=url)

    def notify_daily_summary(self, title, message, url=None):
        return self.notify(title, message, urgent=False, url=url)
