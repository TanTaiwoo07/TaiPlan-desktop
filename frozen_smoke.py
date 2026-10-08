"""打包自检：``TaiPlan.exe --smoke-test``

不打开 UI、不启动 Streamlit server、不进入桌面运行时。
逐项检查打包后最容易缺东西的地方，全部通过返回 0，否则返回 1。

**不碰真实用户数据**：
- build.py 调用时会把 TODO_APP_DATA_DIR 指到临时目录（文档第 37 条）；
- 即使没被覆盖，本模块也不会在真实目录里建库，而是换一个一次性临时目录验证
  SQLite 可用（第 3 项仍然照实报告真实数据路径）。
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

RESULTS = []


def _run(name, fn):
    try:
        detail = fn()
        RESULTS.append((True, name, "" if detail is None else str(detail)))
    except Exception as exc:  # noqa: BLE001
        RESULTS.append((False, name, f"{type(exc).__name__}: {exc}"))


def _check_streamlit_render_paths():
    """★ W1.3（17.3）：真实 render 路径回归 —— 旧 smoke 只 import，抓不到这两条链。

    链 A：st.fragment(run_every=…) → streamlit/runtime/fragment.py → time_util.time_to_seconds
          → import numpy      （原因：numpy 曾被 spec 排除）
    链 B：streamlit-calendar 自定义组件 → components/v1/custom_component.create_instance
          → import pyarrow    （原因：pyarrow 曾被 spec 排除）
    """
    import streamlit as st

    # 链 A：直接调用触发 numpy 的那个函数，再真实调用一次 fragment 包装函数
    from streamlit.time_util import time_to_seconds

    assert time_to_seconds("30s") in (30, 30.0), "time_to_seconds 结果异常"

    @st.fragment(run_every="30s")
    def _probe():
        return 1

    try:
        _probe()
    except (ModuleNotFoundError, ImportError):
        raise                      # ← 这正是必须暴露的失败
    except Exception:
        pass                       # 没有 Streamlit 运行上下文等异常不算失败

    # 链 B：自定义组件 marshalling 依赖
    import pyarrow  # noqa: F401
    import pyarrow as pa

    assert pa.table({"a": [1]}).num_rows == 1
    import streamlit.components.v1 as components  # noqa: F401
    from streamlit.components.v1.custom_component import CustomComponent  # noqa: F401

    import streamlit_calendar  # noqa: F401
    from streamlit_calendar import calendar as _calendar_component  # noqa: F401
    return "fragment/numpy + components/pyarrow + streamlit-calendar"


def _check_ui_import_paths():
    """把 UI 真正会 import 的模块全部走一遍（页面/设置各分区的导入层）。"""
    import importlib

    names = [
        "app",                       # app.py 模块级导入（页面函数都在这里）
        "ui.theme", "ui.layout", "ui.components", "ui.icons",
        "calendar_view", "calendar_adapter", "calendar_settings",
        "services", "models", "database", "recurrence", "edit_session",
        "nlp_parser", "datetime_parser", "reminder_worker", "notification_service",
        "ai_client", "ai_settings", "desktop_window", "tray_app",
        "config_store", "app_paths", "runtime_diagnostics", "dynamic_port",
    ]
    loaded = []
    for name in names:
        importlib.import_module(name)
        loaded.append(name)
    return f"{len(loaded)} 个 UI/服务模块"


def run_smoke_test() -> int:
    import app_paths

    data_dir = Path(app_paths.get_user_data_dir())
    resource_dir = Path(app_paths.get_resource_dir())
    overridden = app_paths.is_data_dir_overridden()

    print("=" * 62)
    print("TaiPlan frozen smoke test")
    print("=" * 62)
    print(f"executable   : {sys.executable}")
    print(f"frozen       : {app_paths.is_frozen()}")
    print(f"resource dir : {resource_dir}")
    print(f"data dir     : {data_dir}" + ("  (已被 TODO_APP_DATA_DIR 覆盖)" if overridden else ""))
    print("-" * 62)

    # 1) frozen 模式
    _run("frozen mode", lambda: f"is_frozen={app_paths.is_frozen()}")

    # 2) resource_path：app.py 与两个只读资源都必须定位到
    def _resource_path():
        need = {
            "app.py": Path(app_paths.resource_path("app.py")),
            "assets/app_icon.ico": Path(app_paths.resource_path("assets/app_icon.ico")),
            "tools/toast.ps1": Path(app_paths.resource_path(Path("tools") / "toast.ps1")),
        }
        missing = [k for k, v in need.items() if not v.is_file()]
        assert not missing, f"缺失只读资源: {missing}"
        return "app.py + assets/app_icon.ico + tools/toast.ps1 就位"

    _run("resource_path", _resource_path)


    _run("streamlit render paths", _check_streamlit_render_paths)

    _run("ui import paths", _check_ui_import_paths)
    # 3) 用户数据路径必须仍在 LOCALAPPDATA，不能落在程序目录里
    def _data_path():
        assert os.path.isabs(str(data_dir)), "数据目录不是绝对路径"
        for bad in (resource_dir, Path(app_paths.get_project_root())):
            try:
                data_dir.relative_to(bad)
            except ValueError:
                continue
            assert overridden, f"数据目录落在了程序目录内: {data_dir}"
        return str(data_dir)

    _run("user data path", _data_path)

    # 4) SQLite 初始化（绝不写真实用户数据）
    _cleanup = []

    def _sqlite():
        import database

        target = data_dir
        if not overridden:
            target = Path(tempfile.mkdtemp(prefix="todoapp_smoke_"))
            _cleanup.append(target)
            os.environ["TODO_APP_DATA_DIR"] = str(target)
        else:
            target.mkdir(parents=True, exist_ok=True)

        database.init_database()
        version = database.get_schema_version()
        integrity = database.check_database_integrity()
        ok = integrity.get("ok", integrity.get("status"))
        assert ok in (True, "ok", None), f"完整性检查未通过: {integrity}"
        note = "" if overridden else "（临时目录，未触碰真实数据）"
        return f"schema_version={version}  db={database._database_path().name}{note}"

    _run("SQLite init", _sqlite)

    # 5) Streamlit
    def _streamlit():
        import streamlit

        from streamlit.web import bootstrap  # noqa: F401

        return f"streamlit {streamlit.__version__}"

    _run("Streamlit import", _streamlit)

    # 6) streamlit-calendar（打包高风险点）
    def _calendar():
        import streamlit_calendar

        pkg = Path(streamlit_calendar.__file__).parent
        assets = list(pkg.rglob("*.js")) + list(pkg.rglob("*.html"))
        assert assets, f"streamlit_calendar 包内找不到前端资源: {pkg}"
        return f"{pkg.name}  前端资源 {len(assets)} 个"

    _run("streamlit-calendar import", _calendar)

    # 7) pywebview（Windows 后端 + 原生 DLL）
    def _webview():
        import webview

        from webview import guilib  # noqa: F401

        try:
            import webview.platforms.winforms  # noqa: F401
            backend = "winforms 可用"
        except Exception as exc:  # noqa: BLE001
            backend = f"winforms 导入失败({type(exc).__name__})"
        lib = Path(webview.__file__).parent / "lib"
        dlls = sorted(p.name for p in lib.glob("*.dll")) if lib.is_dir() else []
        assert dlls, f"webview/lib 下没有 DLL: {lib}"
        return f"webview {getattr(webview, '__version__', '?')}  {backend}  DLL {len(dlls)} 个"

    _run("pywebview import", _webview)

    # 8) pystray + Pillow
    def _tray():
        import pystray
        import pystray._win32  # noqa: F401
        import PIL

        return f"pystray {getattr(pystray, '__version__', '?')}  Pillow {PIL.__version__}"

    _run("pystray import", _tray)

    # 9) keyring（Windows 凭据后端）——只查可用性，不读也不写任何凭据
    def _keyring():
        import keyring

        import ai_settings

        backend = keyring.get_keyring()
        name = f"{type(backend).__module__}.{type(backend).__name__}"
        assert ai_settings.keyring_available(), f"keyring 后端不可用: {name}"
        return name

    _run("keyring import", _keyring)

    # 10) 通知提供方（按优先级列出可用 provider，并确认 toast.ps1 在包内）
    def _notify():
        import desktop_notifier

        notifier = desktop_notifier.DesktopNotifier()
        names = [getattr(p, "name", type(p).__name__) for p in notifier._providers]  # noqa: SLF001
        ps1 = Path(desktop_notifier.TOAST_PS1)
        assert ps1.is_file(), f"toast.ps1 不在资源目录: {ps1}"
        usable = []
        for mod, label in (("toasted", "toasted"), ("winotify", "winotify")):
            try:
                __import__(mod)
                usable.append(label)
            except Exception:  # noqa: BLE001
                pass
        return (f"providers={names}  可用依赖={usable or ['powershell']}  "
                f"app_id={notifier.app_id}")

    _run("notification provider", _notify)

    # 11) 应用图标资源
    def _icon():
        from PIL import Image

        icon = Path(app_paths.resource_path("assets/app_icon.ico"))
        with Image.open(icon) as im:
            sizes = sorted(getattr(im, "info", {}).get("sizes", []) or [im.size])
        return f"{icon.name}  sizes={sizes[:5]}"

    _run("app icon resource", _icon)

    print("-" * 62)
    failed = 0
    for ok, name, detail in RESULTS:
        tag = "[OK]  " if ok else "[FAIL]"
        print(f"{tag} {name}" + (f"  —  {detail}" if detail else ""))
        if not ok:
            failed += 1
    print("-" * 62)
    print(f"共 {len(RESULTS)} 项，通过 {len(RESULTS) - failed} 项，失败 {failed} 项")
    print("=" * 62)

    for tmp in _cleanup:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0 if failed == 0 else 1
