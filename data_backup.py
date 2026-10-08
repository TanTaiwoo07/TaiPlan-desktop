"""数据库备份 / 恢复 / 轮转。

- 备份使用 SQLite 在线备份 API（connection.backup），保证一致性快照
  （不直接用 shutil.copy，避免 WAL 写入过程中产生损坏文件）
- 默认只保留最近 10 个自动备份
- 恢复前一定会先把当前数据库再备份一次
"""

import os
import shutil
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import app_paths

BACKUP_PREFIX = "todo_"
BACKUP_SUFFIX = ".db"
DEFAULT_KEEP = 10


def _backup_dir() -> Path:
    d = app_paths.get_backup_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def backup_name(when=None) -> str:
    stamp = (when or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return f"{BACKUP_PREFIX}{stamp}{BACKUP_SUFFIX}"


def _unique_backup_path() -> Path:
    """同一秒内多次备份也要有不同文件名。"""
    directory = _backup_dir()
    base = backup_name()
    dest = directory / base
    counter = 2
    while dest.exists():
        dest = directory / f"{base[:-len(BACKUP_SUFFIX)]}_{counter}{BACKUP_SUFFIX}"
        counter += 1
    return dest


def backup_database(db_path=None, keep=DEFAULT_KEEP, logger=None) -> Path:
    """用 SQLite backup API 生成一致快照，返回备份文件路径。"""
    src = Path(db_path) if db_path else app_paths.get_database_path()
    if not src.is_file():
        raise FileNotFoundError(f"数据库不存在：{src}")

    dest = _unique_backup_path()
    source = sqlite3.connect(str(src))
    try:
        target = sqlite3.connect(str(dest))
        try:
            source.backup(target)          # 一致性在线备份
            target.commit()
        finally:
            target.close()
    except sqlite3.Error:
        try:
            dest.unlink()
        except OSError:
            pass
        raise
    finally:
        source.close()

    if logger:
        logger.info("数据库已备份：%s", dest.name)
    prune_backups(keep=keep, logger=logger)
    return dest


def list_backups():
    """按时间倒序列出备份文件。"""
    try:
        files = [p for p in _backup_dir().glob(f"{BACKUP_PREFIX}*{BACKUP_SUFFIX}")
                 if p.is_file()]
    except OSError:
        return []
    return sorted(files, key=lambda p: p.name, reverse=True)


def backup_count() -> int:
    return len(list_backups())


def total_backup_bytes() -> int:
    total = 0
    for p in list_backups():
        try:
            total += p.stat().st_size
        except OSError:
            pass
    return total


def prune_backups(keep=DEFAULT_KEEP, logger=None) -> int:
    """只保留最近 keep 个，返回删除数量。"""
    backups = list_backups()
    removed = 0
    for old in backups[keep:]:
        try:
            old.unlink()
            removed += 1
        except OSError:
            pass
    if removed and logger:
        logger.info("清理旧备份 %d 个（保留最近 %d 个）", removed, keep)
    return removed


def verify_backup(path) -> bool:
    """备份文件可打开且通过 quick_check 才算有效。"""
    path = Path(path)
    if not path.is_file():
        return False
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
            return bool(row) and str(row[0]).lower() == "ok"
        finally:
            conn.close()
    except sqlite3.Error:
        return False


def restore_backup(name_or_path, db_path=None, logger=None) -> Path:
    """从备份恢复数据库。

    恢复前会先把当前数据库备份一次；当前数据库不存在时跳过该步。
    返回实际写入的数据库路径。
    """
    src = Path(name_or_path)
    if not src.is_absolute():
        src = _backup_dir() / src.name
    if not src.is_file():
        raise FileNotFoundError(f"备份不存在：{src}")
    if not verify_backup(src):
        raise ValueError(f"备份文件不可用（quick_check 未通过）：{src.name}")

    target = Path(db_path) if db_path else app_paths.get_database_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    safety = None
    if target.is_file():
        try:
            safety = backup_database(db_path=target, logger=logger)
        except sqlite3.Error:
            # 当前库已损坏时无法在线备份，改为原样留档，保证恢复仍可进行
            safety = target.with_name(
                f"todo.corrupt.{datetime.now().strftime('%Y%m%d_%H%M%S')}.db")
            try:
                shutil.copy2(target, safety)
            except OSError:
                safety = None
            if logger:
                logger.warning("当前数据库无法备份（已损坏），已原样留档：%s",
                               safety.name if safety else "失败")

    tmp = target.with_suffix(target.suffix + ".restoring")
    shutil.copy2(src, tmp)
    # 旧 WAL / SHM 必须清掉，否则会和新主库不一致
    for side in ("-wal", "-shm"):
        sidecar = Path(str(target) + side)
        if sidecar.exists():
            try:
                sidecar.unlink()
            except OSError:
                pass

    # Windows 上目标文件可能仍被短暂占用，做有限重试并允许强制替换
    last_error = None
    for attempt in range(5):
        try:
            os.replace(tmp, target)
            last_error = None
            break
        except PermissionError as exc:
            last_error = exc
            time.sleep(0.2 * (attempt + 1))
            try:
                target.unlink()
            except OSError:
                pass
    if last_error is not None:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise last_error

    if logger:
        logger.info("已从备份恢复：%s → %s（恢复前备份：%s）",
                    src.name, target.name, safety.name if safety else "无")
    return target


def backup_summary() -> dict:
    backups = list_backups()
    return {
        "count": len(backups),
        "latest": backups[0].name if backups else None,
        "total_bytes": total_backup_bytes(),
        "dir": str(app_paths.get_backup_dir()),
        "files": [p.name for p in backups],
    }
