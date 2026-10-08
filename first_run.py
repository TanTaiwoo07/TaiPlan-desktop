"""首次启动流程。

- 创建用户数据目录树
- 一次性迁移旧数据（不覆盖、不移动）
- 初始化缺失的默认配置（不覆盖已有值）
- 注册 Windows App User Model ID（本地注册表操作，不联网）
- 写 state/first_run.json 记录 app_version / migration_completed / created_at

首次启动绝不主动联网（不检查更新、不探测 AI）。
"""

from datetime import datetime

import app_paths
import config_store
import data_migration
import version

STATE_FILENAME = "first_run.json"


def get_state_path():
    return app_paths.get_state_path(STATE_FILENAME)


def load_state() -> dict:
    return config_store.read_json_config(get_state_path(), defaults={})


def is_first_run() -> bool:
    return not get_state_path().is_file()


def _default_configs() -> dict:
    """缺省配置文件内容（只在文件不存在时写入）。"""
    import calendar_settings
    return {
        "appearance.json": {"mode": "跟随系统", "density": "舒适", "accent": "coral"},
        "ai_config.json": {"provider": "openai_responses", "model": "",
                           "base_url": "", "enabled": False},
        "notification_config.json": {"enabled": True, "advance_minutes": 10},
        "calendar_config.json": dict(calendar_settings.DEFAULTS),
        "desktop_config.json": {"close_to_tray": True},
    }


def init_missing_configs(logger=None) -> list:
    written = []
    for name, defaults in _default_configs().items():
        path = app_paths.get_config_path(name)
        if path.is_file():
            continue
        try:
            config_store.write_json_config_atomic(path, defaults)
            written.append(name)
        except config_store.ConfigError:
            if logger:
                logger.warning("默认配置写入失败：%s", name)
    return written


def register_windows_app_id(logger=None) -> bool:
    """注册 Windows App ID（注册表 / 可选库，本地操作）。"""
    try:
        import windows_app_registration
        # W4：旧代码调用不存在的 register_app_id()，导致一直有 AttributeError warning。
        # 真实 API 是 ensure_app_registered / is_app_registered。
        ok = windows_app_registration.ensure_app_registered(logger=logger)
        if logger:
            logger.info("Windows App ID 注册：%s", "成功" if ok else "跳过")
        return bool(ok)
    except Exception as exc:  # noqa: BLE001
        if logger:
            logger.warning("Windows App ID 注册失败：%s", type(exc).__name__)
        return False


def run_first_run(logger=None, register_app_id=True) -> dict:
    """执行首次启动流程，幂等，可以每次启动都调用。"""
    first = is_first_run()
    summary = {"first_run": first, "migrated": [], "configs_written": [],
               "app_id_registered": False, "errors": []}

    try:
        app_paths.ensure_user_directories()
    except OSError as exc:
        summary["errors"].append(f"数据目录创建失败：{type(exc).__name__}")

    if data_migration.is_migration_completed():
        summary["migration"] = "skipped"
    else:
        result = data_migration.migrate_legacy_data(logger=logger)
        summary["migrated"] = result.get("migrated", [])
        summary["migration"] = "ran" if result.get("ran") else "skipped"
        summary["migration_errors"] = result.get("errors", [])

    summary["configs_written"] = init_missing_configs(logger=logger)

    # W4 升级兼容：旧身份启动项 / 桌面快捷方式迁移（幂等；未启用则绝不擅自开启）
    try:
        import startup_manager
        summary["startup_migration"] = startup_manager.migrate_legacy_startup()
    except Exception as exc:  # noqa: BLE001
        summary["errors"].append(f"启动项迁移失败：{type(exc).__name__}")
    try:
        import create_shortcut
        summary["shortcut_migration"] = create_shortcut.remove_legacy_shortcut()
    except Exception as exc:  # noqa: BLE001
        summary["errors"].append(f"旧快捷方式清理失败：{type(exc).__name__}")

    if register_app_id:
        summary["app_id_registered"] = register_windows_app_id(logger=logger)

    state = load_state()
    state.setdefault("created_at", datetime.now().isoformat(timespec="seconds"))
    state["app_version"] = version.__version__
    state["build_channel"] = version.BUILD_CHANNEL
    state["migration_completed"] = data_migration.is_migration_completed()
    state["last_started_at"] = datetime.now().isoformat(timespec="seconds")
    try:
        config_store.write_json_config_atomic(get_state_path(), state)
    except config_store.ConfigError as exc:
        summary["errors"].append(str(exc))

    if logger:
        logger.info("首次启动流程完成：first_run=%s migrated=%s configs=%s",
                    first, summary["migrated"], summary["configs_written"])
    return summary
