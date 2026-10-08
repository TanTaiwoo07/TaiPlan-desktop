"""TaiPlan 正式入口（源码模式与 PyInstaller 打包通用）。

用法::

    TaiPlan.exe                              桌面运行时（pywebview + Tray + Worker）
    TaiPlan.exe --streamlit-child --port N   只运行 Streamlit server（frozen 子进程）
    TaiPlan.exe --smoke-test                 打包自检，不开 UI，成功返回 0
    TaiPlan.exe --benchmark [--samples N]    性能基准（临时数据目录，不开 GUI）
    TaiPlan.exe --shutdown                   让正在运行的实例优雅退出

源码模式同样可用::

    python main.py
    python main.py --streamlit-child --port 8501
    python main.py --smoke-test

设计要点
--------
- **绝不能出现** ``TaiPlan.exe -m streamlit ...``：frozen 后 ``sys.executable``
  是 TaiPlan.exe 而不是 python.exe，``-m streamlit`` 会变成无效调用。
  子进程一律走 ``--streamlit-child``（见 ``streamlit_runner``）。
- 子进程模式只跑 Streamlit server，**不得**再进入桌面运行时（Tray / Worker /
  pywebview / 单实例锁），由模式分发 + ``TODO_APP_STREAMLIT_CHILD`` 双重保证。
"""
from __future__ import annotations

import ctypes
import multiprocessing
import os
from pathlib import Path
import sys

import product_info

CHILD_FLAG = "--streamlit-child"
SMOKE_FLAG = "--smoke-test"
BENCH_FLAG = "--benchmark"
SHUTDOWN_FLAG = "--shutdown"
CHILD_ENV = "TODO_APP_STREAMLIT_CHILD"


def parse_port(argv, default=8501):
    """从 ``--port N`` / ``--port=N`` 里取端口，取不到就用默认值。"""
    for i, item in enumerate(argv):
        if item == "--port" and i + 1 < len(argv):
            try:
                return int(argv[i + 1])
            except (TypeError, ValueError):
                return default
        if item.startswith("--port="):
            try:
                return int(item.split("=", 1)[1])
            except (TypeError, ValueError):
                return default
    return default


def is_child_mode(argv) -> bool:
    return CHILD_FLAG in argv or os.environ.get(CHILD_ENV) == "1"


def _dialogs_suppressed() -> bool:
    """无界面场景（自动化测试/诊断）可用环境变量抑制 Windows 弹窗。"""
    return os.environ.get("TODO_APP_NO_GUI_DIALOGS") == "1"


def _fatal(title_text, body_text) -> None:
    """启动致命错误提示：能弹窗就弹窗，否则写 stderr（绝不静默）。"""
    sys.stderr.write((body_text or "") + "\n")
    if _dialogs_suppressed():
        return
    try:
        ctypes.windll.user32.MessageBoxW(0, body_text, title_text, 0x10)
    except Exception:  # noqa: BLE001
        pass


def _t(key, fallback, **kwargs):
    """尽量走 i18n；任何失败都退回英文，保证错误提示一定出得来。"""
    try:
        from i18n import t as _i18n_t

        text = _i18n_t(key)
        if text and text != key:
            return text.format(**kwargs) if kwargs else text
    except Exception:  # noqa: BLE001
        pass
    return fallback.format(**kwargs) if kwargs else fallback


def _report_blocked(payload) -> None:
    """BLOCKED 提示：只描述**实际**存在的目录，不编造"数据在两个地方"。

    兼容两种入参：
      * Decision（plan() 的返回值，带 target_dir / legacy_dir / reason）
      * Result（ensure_data_dir() 的返回值，只有 mode / detail）
    且只有在 legacy 目录**确实存在数据库**时才提到它——全新用户不该看到
    "你的旧数据仍在某个旧目录里" 这种无中生有的提示。
    """
    import app_paths as _ap

    target = str(getattr(payload, "target_dir", None) or _ap.get_user_data_dir())
    legacy_dir = Path(getattr(payload, "legacy_dir", None) or _ap.get_legacy_app_dir())
    reason = (getattr(payload, "detail", "")
              or getattr(payload, "reason", "")
              or getattr(payload, "mode", ""))
    parts = [
        _t("error.data_blocked_title", "TaiPlan could not verify its data folder."),
        "",
        _t("error.data_blocked_reason", "Reason: {reason}", reason=reason),
        _t("error.data_blocked_keep", "Keep this folder: {target}", target=target),
    ]
    try:
        has_legacy_data = (legacy_dir / _ap.DB_FILENAME).is_file()
    except OSError:
        has_legacy_data = False
    if has_legacy_data:
        parts.append(_t("error.data_blocked_legacy",
                        "Previous version data remains at: {legacy}", legacy=str(legacy_dir)))
    parts += ["", _t("error.data_blocked_no_empty_db",
                     "No empty database was created, so nothing is masked or overwritten.")]
    _fatal(product_info.APP_DISPLAY_NAME, "\n".join(parts))


def _report_check_failed(exc_name, exc) -> None:
    """启动前数据检查自身出错：明确说明"未改动任何数据"。"""
    import app_paths as _ap

    body = "\n".join([
        _t("error.data_check_failed_title", "TaiPlan could not check its data folder."),
        "",
        _t("error.data_check_failed_reason", "Reason: {error}", error=f"{exc_name}: {exc}"),
        _t("error.data_blocked_keep", "Keep this folder: {target}",
           target=str(_ap.get_user_data_dir())),
        "",
        _t("error.data_check_failed_untouched", "No data was modified."),
    ])
    _fatal(product_info.APP_DISPLAY_NAME, body)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    # W3：数据目录改名为 TaiPlan 后，首次启动时目标目录还不存在，
    # 运行时/日志/state 的写入会因父目录缺失而失败（而且日志本身也写不出来）。
    # 这里最早、幂等地建好目录树；空目录不等于迁移成功——DB 仍只可能来自
    # 校验通过的迁移或全新用户初始化。
    # FRESH INSTALL HOTFIX：迁移/全新判定**之前**绝不写 TaiPlan 数据目录。
    # 判定前的日志改写 %TEMP%（app_paths.BOOTSTRAP_LOG_DIR_ENV），
    # 这样程序自己写的 bootstrap 文件不会让全新用户被误判成 partial 目标。
    try:
        import app_paths
    except Exception:  # noqa: BLE001
        app_paths = None

    if CHILD_FLAG in argv:
        # 标记住，防止任何路径又回到桌面运行时
        os.environ[CHILD_ENV] = "1"
        import streamlit_runner
        return streamlit_runner.run_streamlit_child(parse_port(argv))

    if SMOKE_FLAG in argv:
        import frozen_smoke
        return frozen_smoke.run_smoke_test()

    if SHUTDOWN_FLAG in argv:
        # 第 17 阶段：让运行中的实例优雅退出；没有实例则直接 exit 0（不起新实例）
        import desktop_runtime
        return desktop_runtime.request_shutdown()

    if BENCH_FLAG in argv:
        # 第 16 阶段 BP 节：frozen 下也能跑基准，不开 GUI、只用临时数据目录
        import benchmark
        return benchmark.main([a for a in argv if a != BENCH_FLAG])

    if os.environ.get(CHILD_ENV) == "1":
        sys.stderr.write(
            "refusing to start the desktop runtime inside a streamlit child process\n")
        return 2

    # W3：数据目录迁移门（fail-closed）。
    # 必须在这里——runtime/reminder_worker 也会碰数据目录，如果只在 Streamlit app 里把关，
    # worker 会抢先 init_database() 建出一个空库，让用户误以为数据丢了。
    # 判定前的日志一律写 %TEMP%：程序自己写的 bootstrap 文件不得污染数据目录判定。
    if app_paths is not None:
        os.environ.setdefault(app_paths.BOOTSTRAP_LOG_DIR_ENV,
                              str(app_paths.get_bootstrap_logs_dir()))

    try:
        import data_dir_migration
        decision = data_dir_migration.ensure_data_dir()
    except Exception as exc:  # noqa: BLE001
        _report_check_failed(type(exc).__name__, exc)
        return 3
    if not decision.ok:
        _report_blocked(decision)
        return 3

    import desktop_runtime
    # 判定通过：切回正式日志目录；只有此时才允许创建/写入 TaiPlan 数据目录。
    try:
        os.environ.pop(app_paths.BOOTSTRAP_LOG_DIR_ENV, None)
        import logging_config
        logging_config.rebind_to_official_logs()
        app_paths.ensure_user_directories()
    except Exception:  # noqa: BLE001
        pass

    desktop_runtime.main()
    return 0


if __name__ == "__main__":
    # PyInstaller 打包后必须调用，否则任何 multiprocessing 用法会反复自我派生
    multiprocessing.freeze_support()
    raise SystemExit(main())
