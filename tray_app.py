"""Windows 系统托盘（pystray）。

菜单：打开 Todo / 暂停-恢复提醒 / 退出 Todo。
「打开 Todo」显示并 focus 已存在的 pywebview 主窗口（不新开窗口）。
"""

import webbrowser
from pathlib import Path

import app_metadata
import app_paths
import dynamic_port
import language_store
import product_info

from i18n import t

import notification_service as notif

def tray_url():
    """实际端口由 runtime 动态发布，托盘不再硬编码 8501。"""
    try:
        return dynamic_port.published_url()
    except Exception:
        return dynamic_port.base_url(dynamic_port.DEFAULT_PORT)


def _toggle_reminders(icon, item):
    cfg = notif.load_notification_config()
    cfg["enabled"] = not cfg.get("enabled", True)
    notif.save_notification_config(cfg)


def _reminder_menu_label(item):
    cfg = notif.load_notification_config()
    return t("tray.pause_reminders") if cfg.get("enabled", True) else t("tray.resume_reminders")


def _quit(icon, item, on_quit=None):
    icon.stop()
    if on_quit:
        on_quit()


def build_tray(on_quit=None, on_open=None, on_show_window=None):
    """构建托盘 icon。

    on_quit: 退出回调（真正关闭 Runtime）。
    on_show_window: 显示主窗口回调；缺省时回退到打开浏览器。
    """
    language_store.apply()          # 托盘文案跟随用户语言设置
    try:
        import pystray
        from PIL import Image, ImageDraw
    except ImportError:
        return None

    icon_file = app_metadata.icon_png()
    if icon_file.is_file():
        try:
            image = Image.open(icon_file).convert("RGBA").resize((64, 64))
        except Exception:
            image = None
    else:
        image = None
    if image is None:
        image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle((8, 8, 56, 56), radius=14, fill=(217, 80, 63, 255))
        draw.line([(20, 34), (29, 43)], fill=(255, 255, 255, 255), width=5)
        draw.line([(29, 43), (45, 23)], fill=(255, 255, 255, 255), width=5)

    handler = on_show_window or on_open

    def _on_open(icon, item):
        if handler:
            handler()
        else:
            webbrowser.open(tray_url())

    menu = pystray.Menu(
        pystray.MenuItem(t("tray.show"), _on_open, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(_reminder_menu_label, _toggle_reminders),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(t("tray.quit"), lambda icon, item: _quit(icon, item, on_quit)),
    )
    # 第一个参数是托盘内部标识（身份，留 W4）；第三个是显示名（显示层 → TaiPlan）
    icon = pystray.Icon("TaiPlan", image, product_info.APP_DISPLAY_NAME, menu)
    return icon


def run_tray(on_quit=None, on_open=None, on_show_window=None):
    """阻塞运行托盘（通常放在后台线程）。"""
    icon = build_tray(on_quit=on_quit, on_open=on_open, on_show_window=on_show_window)
    if icon is None:
        raise RuntimeError("pystray/Pillow 未安装，无法启动系统托盘")
    icon.run()
