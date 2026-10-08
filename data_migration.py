"""旧版数据一次性迁移：项目根目录 → %LOCALAPPDATA%\\TodoApp

规则（第 14 阶段要求）：

- 只在目标文件不存在时 COPY，绝不覆盖
- 复制而不是移动，旧文件保留在项目根目录作为安全备份
- 新旧同时存在：新数据目录优先，只记日志
  `Legacy database detected but destination already exists; migration skipped.`
- 迁移完成后写 state/migration_state.json，避免每次启动重复复制
"""

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

import app_paths
import config_store

STATE_FILENAME = "migration_state.json"
STATE_VERSION = 1


def get_state_path() -> Path:
    return app_paths.get_state_path(STATE_FILENAME)


def load_migration_state() -> dict:
    return config_store.read_json_config(get_state_path(), defaults={})


def save_migration_state(state: dict) -> Path:
    payload = dict(state)
    payload["state_version"] = STATE_VERSION
    payload["updated_at"] = datetime.now().isoformat(timespec="seconds")
    return config_store.write_json_config_atomic(get_state_path(), payload)


def is_migration_completed() -> bool:
    return bool(load_migration_state().get("legacy_migration_completed"))


def mark_migration_completed(**extra) -> Path:
    state = load_migration_state()
    state["legacy_migration_completed"] = True
    state["completed_at"] = datetime.now().isoformat(timespec="seconds")
    state.update(extra)
    return save_migration_state(state)


def _copy_database(src: Path, dest: Path, logger=None) -> None:
    """优先用 SQLite 备份 API；失败退回普通复制。"""
    try:
        source = sqlite3.connect(str(src))
        try:
            target = sqlite3.connect(str(dest))
            try:
                source.backup(target)
                target.commit()
            finally:
                target.close()
        finally:
            source.close()
    except sqlite3.Error:
        shutil.copy2(src, dest)
    if logger:
        logger.info("旧数据库已迁移：%s → %s", src, dest)


def migrate_legacy_data(legacy_dir=None, target_dir=None, force=False,
                        logger=None) -> dict:
    """执行一次性迁移，返回摘要 dict。"""

    legacy = Path(legacy_dir) if legacy_dir else app_paths.get_legacy_dir()
    result = {"ran": False, "migrated": [], "skipped": [], "errors": [],
              "legacy_dir": str(legacy), "target_dir": str(target_dir or app_paths.get_user_data_dir())}

    if is_migration_completed() and not force:
        result["skipped"].append("legacy_migration_completed 已存在")
        if logger:
            logger.info("旧数据迁移已完成过，跳过。")
        return result

    result["ran"] = True
    app_paths.ensure_user_directories()

    db_dest = Path(target_dir) / app_paths.DB_FILENAME if target_dir \
        else app_paths.get_database_path()
    db_src = legacy / app_paths.DB_FILENAME

    if db_src.is_file():
        if db_dest.exists():
            message = ("Legacy database detected but destination already exists; "
                       "migration skipped.")
            result["skipped"].append("todo.db")
            if logger:
                logger.warning(message)
        else:
            try:
                db_dest.parent.mkdir(parents=True, exist_ok=True)
                _copy_database(db_src, db_dest, logger)
                result["migrated"].append("todo.db")
            except OSError as exc:
                result["errors"].append(f"todo.db: {type(exc).__name__}")
                if logger:
                    logger.error("旧数据库迁移失败：%s", type(exc).__name__)
    else:
        result["skipped"].append("todo.db（旧文件不存在）")

    for legacy_name, new_name in app_paths.LEGACY_CONFIG_FILES.items():
        src = legacy / legacy_name
        if not src.is_file():
            continue
        dest = (Path(target_dir) / app_paths.CONFIG_DIRNAME / new_name
                if target_dir else app_paths.get_config_path(new_name))
        if dest.exists():
            result["skipped"].append(new_name)
            if logger:
                logger.info("配置已存在，跳过迁移：%s", new_name)
            continue
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            result["migrated"].append(new_name)
            if logger:
                logger.info("旧配置已迁移：%s → %s", legacy_name, new_name)
        except OSError as exc:
            result["errors"].append(f"{new_name}: {type(exc).__name__}")

    mark_migration_completed(migrated=result["migrated"], skipped=result["skipped"])
    return result
