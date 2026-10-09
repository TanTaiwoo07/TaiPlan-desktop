# -*- coding: utf-8 -*-
"""数据目录迁移：%LOCALAPPDATA%\\TodoApp → %LOCALAPPDATA%\\TaiPlan（W3）。

安全原则（用户 2026-10-06 明确要求）：
  1. **copy-first**：只读旧目录，从不 move / delete legacy。
  2. **不整目录盲复制**：数据库走 SQLite backup API，配置走白名单。
  3. **不拿"目录存在"当"迁移成功"**：区分 valid / partial / absent，
     全部产物先写进 staging（TaiPlan.migrating-<token>），最后一步才提交。
  4. **幂等**：已有有效新库 → 直接沿用，绝不覆盖/合并。
  5. **fail-safe**：任一步失败都保留旧数据、不建空库、不写成功标记。
  6. 迁移用户不是新用户：不生成 demo 任务，不覆盖既有业务数据。

数据库复制必须用 sqlite3 的 backup()：应用以 WAL 模式运行，
直接复制 todo.db 可能漏掉仍在 WAL 中的数据。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import sqlite3
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from taiplan import app_paths

logger = logging.getLogger(__name__)

# 只迁移这些配置（白名单，不做整目录复制）
CONFIG_WHITELIST = (
    "appearance.json",
    "ai_config.json",
    "notification_config.json",
    "calendar_config.json",
    "desktop_config.json",
    "language_config.json",      # W2 新增
)

# 用户备份档按原样复制（它们不是 active DB）
BACKUP_GLOB = "*.db"

# 业务表：迁移后必须逐一核对 row counts
BUSINESS_TABLES = (
    "tasks",
    "task_occurrence_states",
    "task_occurrence_overrides",
    "notification_log",
    "app_meta",
)
# 运行态表：backup() 会把整库复制过来，必须显式清空
RUNTIME_TABLES = ("runtime_status",)

MARKER_FILENAME = "migration_complete.json"
FIRST_RUN_FILENAME = "first_run.json"

MODE_KEEP_NEW = "keep_new"        # 新库有效 → 沿用，不动
MODE_MIGRATE = "migrate"          # 只有旧数据 → 迁移
MODE_FRESH = "fresh"              # 两边都没有 → 全新用户
MODE_RESUME = "resume"            # 有可验证的 staging 残留 → 提交它
MODE_BLOCKED = "blocked"          # 半成品/损坏且无法安全处理 → 拒绝启动写库
MODE_SKIPPED = "skipped"          # 被环境变量接管（测试/演练）

DISABLE_ENV = "TODO_APP_DISABLE_MIGRATION"


# --------------------------------------------------------------- 结果结构

@dataclass
class Decision:
    mode: str
    reason: str = ""
    legacy_dir: Path | None = None
    legacy_db: Path | None = None
    target_dir: Path | None = None
    target_db: Path | None = None
    target_status: str = "absent"
    staging_dir: Path | None = None

    @property
    def blocked(self) -> bool:
        return self.mode == MODE_BLOCKED


@dataclass
class Result:
    ok: bool
    mode: str
    detail: str = ""
    rows: dict = field(default_factory=dict)
    copied_configs: list = field(default_factory=list)
    copied_backups: list = field(default_factory=list)
    credential: dict = field(default_factory=dict)
    staging: Path | None = None
    errors: list = field(default_factory=list)


# --------------------------------------------------------------- 只读探测

def _db_state(path: Path) -> str:
    """'missing' | 'ok' | 'corrupt'。只读打开，不修改任何字节。"""
    if not path.is_file():
        return "missing"
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5.0)
        try:
            row = conn.execute("PRAGMA quick_check").fetchone()
            return "ok" if row and row[0] == "ok" else "corrupt"
        finally:
            conn.close()
    except sqlite3.Error:
        return "corrupt"


def _table_counts(db_path: Path, tables=BUSINESS_TABLES) -> dict:
    """只读统计业务表行数；表不存在记为 None。"""
    counts = {}
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=5.0)
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for table in tables:
            if table in names:
                counts[table] = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            else:
                counts[table] = None
    finally:
        conn.close()
    return counts


def marker_path(target_dir: Path) -> Path:
    return target_dir / app_paths.STATE_DIRNAME / MARKER_FILENAME


def has_success_marker(target_dir: Path) -> bool:
    path = marker_path(target_dir)
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return False
    return bool(isinstance(data, dict) and data.get("completed") is True)


def has_first_run_marker(target_dir=None) -> bool:
    """state/first_run.json 是否存在且可解析（程序自己写的首次运行痕迹）。

    这是"全新用户已被程序正常初始化过"的凭据，和"迁移成功标记"不同：
    它**只**在目标库为空、且目录里没有其它真实内容时才被采信。
    """
    target_dir = Path(target_dir) if target_dir else app_paths.get_user_data_dir()
    path = target_dir / app_paths.STATE_DIRNAME / FIRST_RUN_FILENAME
    if not path.is_file():
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return False
    return isinstance(data, dict) and bool(data)


def iter_staging_dirs(target_dir=None):
    """列出 staging 残留目录（可能来自上次中断）。

    父目录优先取显式目标目录的父目录；只有未指定时才回落到环境推导出的数据目录，
    这样测试/演练传入临时目录时绝不会去扫描真实目录。
    """
    if target_dir is not None:
        base = Path(target_dir).parent
    else:
        base = app_paths.get_user_data_dir().parent
    prefix = f"{app_paths.APP_DIR_NAME}{app_paths.STAGING_INFIX}"
    if not base.is_dir():
        return []
    return sorted([p for p in base.iterdir() if p.is_dir() and p.name.startswith(prefix)])


def inspect(legacy_dir: Path | None = None, target_dir: Path | None = None) -> dict:
    """只读检查两边状态。"""
    legacy_dir = Path(legacy_dir) if legacy_dir else app_paths.get_legacy_app_dir()
    target_dir = Path(target_dir) if target_dir else app_paths.get_user_data_dir()
    legacy_db = legacy_dir / app_paths.DB_FILENAME
    target_db = target_dir / app_paths.DB_FILENAME

    legacy_state = _db_state(legacy_db)
    target_state = _db_state(target_db)
    if target_state == "ok" and not has_success_marker(target_dir):
        # 库能打开但缺少成功标记：不能被当作"迁移成功"
        target_state = "unmarked"
    return {
        "target_db_empty": _target_db_is_empty(target_dir),
        "legacy_dir": legacy_dir,
        "legacy_db": legacy_db,
        "legacy_state": legacy_state,
        "target_dir": target_dir,
        "target_db": target_db,
        "target_state": target_state,
        "target_dir_exists": target_dir.exists(),
        "marker": has_success_marker(target_dir),
        "staging": iter_staging_dirs(target_dir),
    }


def _valid_staging(staging_dir: Path):
    """staging 是否是一份可验证、可提交的完整迁移产物。"""
    db = staging_dir / app_paths.DB_FILENAME
    if _db_state(db) != "ok" or not has_success_marker(staging_dir):
        return None
    return db


# 程序自己在正常启动过程中会写进数据目录的东西（**不是**用户数据）：
# 只放明确的 allowlist，其余一律当作真实内容 → BLOCKED。
_BOOTSTRAP_STATE_FILES = {
    "runtime_port.json",       # 运行期端口/令牌（每次启动重写）
    "migration_state.json",    # 迁移留痕
    "first_run.json",          # 首次运行留痕
    "migration_complete.json",  # 迁移完成标记
    "ics_import.json",  # 程序自身的状态文件（见 test_state_config_allowlist）
}
_KNOWN_CONFIG_FILES = {
    "appearance.json", "ai_config.json", "notification_config.json",
    "calendar_config.json", "desktop_config.json", "language_config.json",
}


def _is_log_filename(name: str) -> bool:
    """日志文件（含轮转后的 runtime.log.1 这种）。"""
    head = name.split(".")[0]
    return ("." in name and (name.endswith(".log") or ".log." in name)) or head in (
        "runtime", "streamlit", "worker", "launcher", "app", "reminder_worker")


def _dir_is_bootstrap_only(path: Path, allowed_files) -> bool:
    for sub in path.rglob("*"):
        if sub.is_dir():
            return False
        if sub.is_file() and sub.name not in allowed_files:
            return False
    return True


# "空库"只看用户数据表：app_meta / notification_log / runtime_status 都是程序产物
USER_DATA_TABLES = ("tasks", "task_occurrence_states", "task_occurrence_overrides")


def _empty_db_names():
    """空库自身与其 SQLite 侧车文件（程序产物，不是用户数据）。"""
    db = app_paths.DB_FILENAME
    return {db, f"{db}-wal", f"{db}-shm", f"{db}-journal"}


def _db_is_empty(db_path: Path) -> bool:
    """库可打开且所有业务表都是 0 行 / 无表 → 程序刚初始化的空库。"""
    if not db_path.is_file():
        return True
    if _db_state(db_path) != "ok":
        return False
    try:
        counts = _table_counts(db_path, USER_DATA_TABLES)
    except sqlite3.Error:
        return False
    return all(v in (0, None) for v in counts.values())


def _target_db_is_empty(target_dir: Path) -> bool:
    return _db_is_empty(target_dir / app_paths.DB_FILENAME)


def _content_summary(target_dir: Path) -> str:
    """"到底是什么让我们不能放行"——用于错误提示，避免含糊措辞。"""
    if not target_dir.is_dir():
        return "target directory does not exist"
    for entry in sorted(target_dir.iterdir()):
        if entry.is_file():
            if entry.name in _empty_db_names():
                continue
            return f"unexpected file: {entry.name}"
        name = entry.name
        if name == app_paths.LOGS_DIRNAME:
            if not _dir_is_bootstrap_only(entry, set()) and not all(
                    _is_log_filename(f.name) for f in entry.rglob("*") if f.is_file()):
                return f"unexpected content in {name}/"
            continue
        if name == app_paths.STATE_DIRNAME:
            if not _dir_is_bootstrap_only(entry, _BOOTSTRAP_STATE_FILES):
                return f"unexpected content in {name}/"
            continue
        if name == app_paths.CONFIG_DIRNAME:
            if not _dir_is_bootstrap_only(entry, _KNOWN_CONFIG_FILES):
                return f"unexpected content in {name}/"
            continue
        if name == app_paths.BACKUPS_DIRNAME:
            files = [f for f in entry.rglob("*") if f.is_file()]
            main_db_bad = _db_state(target_dir / app_paths.DB_FILENAME) != "ok"
            if not main_db_bad:
                continue
            if all(f.suffix.lower() == ".db" and _db_is_empty(f) for f in files):
                continue
            return "backups/ contains data while todo.db is missing or damaged"
        return f"unexpected directory: {name}/"
    return ""


def _has_real_content(target_dir: Path) -> bool:
    """目标目录里是否存在"真实用户数据迹象"（严格分类）。

    程序自己在启动过程中写的东西（日志、运行期 state、默认配置）**不算**用户数据，
    否则入口的 bootstrap 写入会让全新用户被误判成"半成品目录"而拒绝启动。

    仍然一律视为"内容"（→ BLOCKED）的：
      * 根目录下任何文件，特别是 todo.db（无论 marked/unmarked/corrupt）
      * 未知目录（含 backups/ 里有东西）
      * logs/、state/、config/ 里的非 allowlist 文件
    """
    if not target_dir.is_dir():
        return False
    for entry in target_dir.iterdir():
        if entry.is_file():
            # todo.db 与其 WAL/SHM 侧车都是程序文件：库的可信度由 target_state
            # （ok / unmarked / corrupt）配合迁移标记、首次运行标记来判定，
            # 不由"库里有没有行"判定——否则用户自己建的第一个任务就会把自己锁在门外。
            if entry.name in _empty_db_names():
                continue
            return True
        if not entry.is_dir():
            return True
        name = entry.name
        if name == app_paths.LOGS_DIRNAME:
            if not all(_is_log_filename(f.name) for f in entry.rglob("*") if f.is_file()):
                return True
            continue
        if name == app_paths.STATE_DIRNAME:
            if not _dir_is_bootstrap_only(entry, _BOOTSTRAP_STATE_FILES):
                return True
            continue
        if name == app_paths.CONFIG_DIRNAME:
            if not _dir_is_bootstrap_only(entry, _KNOWN_CONFIG_FILES):
                return True
            continue
        if name == app_paths.BACKUPS_DIRNAME:
            files = [f for f in entry.rglob("*") if f.is_file()]
            if not files:
                continue
            main_db_bad = _db_state(target_dir / app_paths.DB_FILENAME) != "ok"
            if not main_db_bad:
                # 主库好好的，backups/ 纯粹是程序产物（导入前自动备份、手动备份都在这里）
                continue
            # 主库缺失或损坏时，备份里有数据才是值得停下来让人确认的信号
            if all(f.suffix.lower() == ".db" and _db_is_empty(f) for f in files):
                continue
            return True
        return True
    return False


def plan(legacy_dir=None, target_dir=None) -> Decision:
    """决定本次启动该做什么。纯只读。"""
    info = inspect(legacy_dir, target_dir)
    legacy_ok = info["legacy_state"] == "ok"
    target_state = info["target_state"]

    decision = Decision(
        mode=MODE_FRESH,
        legacy_dir=info["legacy_dir"],
        legacy_db=info["legacy_db"],
        target_dir=info["target_dir"],
        target_db=info["target_db"],
        target_status=target_state,
    )

    if target_state == "ok":
        decision.mode = MODE_KEEP_NEW
        decision.reason = "已有有效的 TaiPlan 数据，沿用且绝不覆盖/合并"
        return decision

    # 目标不是有效库：看有没有可验证的 staging 残留可以提交
    for staging in info["staging"]:
        if _valid_staging(staging) is not None:
            decision.mode = MODE_RESUME
            decision.staging_dir = staging
            decision.reason = f"发现可验证的迁移残留 {staging.name}，提交它"
            return decision

    # 目标目录非空但没有可用库 → 半成品/损坏，绝不当成功数据使用（顺序很重要：
    # 必须在"迁移"之前判断，否则会在半成品目录上再初始化一遍）。
    target_dir = info["target_dir"]
    real_content = False
    if info["target_dir_exists"]:
        try:
            real_content = _has_real_content(target_dir)
        except OSError:
            real_content = True
    # 程序自建的空库 + 首次运行标记 = 全新用户已被正常初始化过（不写迁移标记的那种）。
    # 必须同时满足"库为空""有首次运行痕迹""没有其它真实内容"，否则一律走 BLOCKED。
    if (target_state == "unmarked" and has_first_run_marker(target_dir)
            and not real_content):
        # 全新用户由本程序初始化过（有首次运行标记），库里可能已经有他自己建的任务。
        # 迁移标记只有"从旧版本迁过来"才会有，所以这里绝不能要求库为空。
        decision.mode = MODE_KEEP_NEW
        decision.reason = "本程序初始化的数据目录（有首次运行标记）：沿用现有数据，不重建、不覆盖"
        return decision

    if real_content or target_state in ("corrupt", "unmarked"):
        decision.mode = MODE_BLOCKED
        detail = _content_summary(target_dir) if real_content else f"todo.db state={target_state}"
        decision.reason = (f"TaiPlan 数据目录无法安全处理（{detail}），且没有可用的迁移残留；"
                           f"拒绝创建空库以免掩盖数据丢失")
        return decision

    if legacy_ok:
        decision.mode = MODE_MIGRATE
        decision.reason = "只有 legacy 数据，执行迁移"
        return decision

    if info["legacy_state"] == "corrupt":
        decision.mode = MODE_BLOCKED
        decision.reason = "legacy 数据库损坏且没有有效的新数据，拒绝静默创建空库"
        return decision

    decision.mode = MODE_FRESH
    decision.reason = "两边都没有数据，按全新用户初始化"
    return decision


# --------------------------------------------------------------- 迁移执行

def _new_staging_dir(target_dir=None) -> Path:
    """在目标目录的同级创建 staging。显式传入 target_dir 时不依赖环境变量。"""
    token = f"{int(time.time())}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
    if target_dir is not None:
        path = Path(target_dir).parent / f"{app_paths.APP_DIR_NAME}{app_paths.STAGING_INFIX}{token}"
    else:
        path = app_paths.get_staging_dir(token)
    path.mkdir(parents=True, exist_ok=False)
    return path


def _backup_database(src: Path, dst: Path) -> None:
    """用 SQLite backup API 复制（WAL 安全）。源始终只读打开。"""
    src_conn = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True, timeout=10.0)
    dst_conn = sqlite3.connect(str(dst), timeout=10.0)
    try:
        src_conn.backup(dst_conn)          # 原子地把源库内容写入目标连接
        dst_conn.commit()
    finally:
        dst_conn.close()
        src_conn.close()


def _purge_runtime_tables(db_path: Path) -> dict:
    """清空运行态表（backup() 会连它们一起复制，必须显式删）。"""
    removed = {}
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for table in RUNTIME_TABLES:
            if table in names:
                before = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                conn.execute(f'DELETE FROM "{table}"')
                removed[table] = before
        conn.commit()
    finally:
        conn.close()
    return removed


def _pragma_checks(db_path: Path) -> tuple:
    conn = sqlite3.connect(str(db_path), timeout=10.0)
    try:
        quick = conn.execute("PRAGMA quick_check").fetchone()[0]
        full = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()
    return quick, full


def _copy_configs(legacy_dir: Path, staging: Path) -> list:
    copied = []
    src_dir = legacy_dir / app_paths.CONFIG_DIRNAME
    dst_dir = staging / app_paths.CONFIG_DIRNAME
    dst_dir.mkdir(parents=True, exist_ok=True)
    for name in CONFIG_WHITELIST:
        src = src_dir / name
        dst = dst_dir / name
        if src.is_file() and not dst.exists():
            shutil.copy2(src, dst)
            copied.append(name)
    return copied


def _copy_backups(legacy_dir: Path, staging: Path) -> list:
    src_dir = legacy_dir / app_paths.BACKUPS_DIRNAME
    if not src_dir.is_dir():
        return []
    dst_dir = staging / app_paths.BACKUPS_DIRNAME
    dst_dir.mkdir(parents=True, exist_ok=True)
    copied = []
    for src in sorted(src_dir.glob(BACKUP_GLOB)):
        dst = dst_dir / src.name
        if not dst.exists():
            shutil.copy2(src, dst)
            copied.append(src.name)
    return copied


def _write_marker(target_dir: Path, payload: dict) -> None:
    """成功标记：只在全部步骤成功后写。"""
    path = marker_path(target_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(payload, completed=True,
                   finished_at=datetime.now().isoformat(timespec="seconds"))
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _write_first_run_marker(target_dir: Path, payload: dict, fresh: bool = False) -> None:
    """让程序不被当作"从未初始化"（不生成 demo、不重跑新用户初始化）。

    fresh=True 用于**真正全新用户**：如实记录"没有旧数据"，不得谎称从 TodoApp 迁移而来。
    """
    path = target_dir / app_paths.STATE_DIRNAME / FIRST_RUN_FILENAME
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        return
    if fresh:
        data = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "kind": "fresh_user",
            "migrated_from": None,
            "migration_completed": False,
            "source_rows": {},
        }
    else:
        data = {
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "kind": "migrated",
            "migrated_from": app_paths.LEGACY_APP_DIR_NAME,
            "migration_completed": True,
            "source_rows": payload.get("rows", {}),
        }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def migrate(force=False, legacy_dir=None, target_dir=None, logger_=None) -> Result:
    """执行迁移：全部产物先在 staging 内完成并校验，最后一步才提交。

    force=True 时忽略 TODO_APP_DATA_DIR 覆盖（测试/演练用）。
    """
    log = logger_ or logger
    if app_paths.is_data_dir_overridden() and not force:
        return Result(ok=True, mode=MODE_SKIPPED, detail="数据目录被环境变量接管，跳过迁移")
    if os.environ.get(DISABLE_ENV) and not force:
        return Result(ok=True, mode=MODE_SKIPPED, detail="TODO_APP_DISABLE_MIGRATION 已设置")

    decision = plan(legacy_dir, target_dir)
    if decision.mode == MODE_KEEP_NEW:
        return Result(ok=True, mode=MODE_KEEP_NEW, detail=decision.reason)
    if decision.mode == MODE_FRESH:
        return Result(ok=True, mode=MODE_FRESH, detail=decision.reason)
    if decision.mode == MODE_BLOCKED:
        return Result(ok=False, mode=MODE_BLOCKED, detail=decision.reason,
                      errors=[decision.reason])
    if decision.mode == MODE_RESUME:
        return promote(decision.staging_dir, target_dir or app_paths.get_user_data_dir(),
                       logger_=log)

    legacy_dir = decision.legacy_dir
    target_dir = Path(target_dir) if target_dir else app_paths.get_user_data_dir()
    # 目标目录可能只是空脚手架（入口处幂等建的空目录树）：可以安全移除后提交；
    # 一旦发现任何真实数据迹象则绝不触碰（由 promote 再兜一次底）。
    try:
        if target_dir.is_dir() and not _has_real_content(target_dir):
            shutil.rmtree(target_dir)
    except OSError:
        pass
    result = Result(ok=False, mode=MODE_MIGRATE)

    try:
        staging = _new_staging_dir(target_dir)
    except OSError as exc:
        result.errors.append(f"无法创建 staging 目录：{type(exc).__name__}")
        return result
    result.staging = staging

    legacy_db = legacy_dir / app_paths.DB_FILENAME
    staged_db = staging / app_paths.DB_FILENAME
    try:
        # 1) WAL 安全的整库复制（源只读）
        _backup_database(legacy_db, staged_db)
        # 2) 显式清空运行态表
        removed = _purge_runtime_tables(staged_db)
        # 3) 双重完整性校验
        quick, full = _pragma_checks(staged_db)
        if quick != "ok" or full != "ok":
            result.errors.append(f"新库完整性校验未通过：quick={quick} full={full}")
            return result
        # 4) 业务表 row counts 与原库一致
        before = _table_counts(legacy_db)
        after = _table_counts(staged_db)
        result.rows = after
        diff = {k: (before.get(k), after.get(k)) for k in BUSINESS_TABLES
                if before.get(k) != after.get(k)}
        if diff:
            result.errors.append(f"业务表行数不一致：{diff}")
            return result
        # 5) 配置白名单 + 备份档
        result.copied_configs = _copy_configs(legacy_dir, staging)
        result.copied_backups = _copy_backups(legacy_dir, staging)
        # 6) 凭据（复制，不删除旧凭据；失败不影响数据迁移）
        try:
            from taiplan import ai_settings
            result.credential = ai_settings.migrate_credential(
                legacy_config_path=legacy_dir / app_paths.CONFIG_DIRNAME / "ai_config.json",
                logger=log)
        except Exception as exc:  # noqa: BLE001
            result.credential = {"migrated": False, "reason": type(exc).__name__}
        # 7) 全部成功后才写成功标记
        _write_marker(staging, {
            "source_dir": str(legacy_dir),
            "source_db": str(legacy_db),
            "rows": after,
            "runtime_tables_purged": removed,
            "copied_configs": result.copied_configs,
            "copied_backups": result.copied_backups,
            "credential": result.credential,
        })
    except (sqlite3.Error, OSError, shutil.Error) as exc:
        result.errors.append(f"{type(exc).__name__}: {exc}")
        log.warning("数据目录迁移失败（旧数据与 staging 均保留）：%s", type(exc).__name__)
        return result

    # 8) 提交：目标必须仍然不存在，绝不合并/覆盖
    promoted = promote(staging, target_dir, logger_=log, mode=MODE_MIGRATE)
    promoted.rows = result.rows
    promoted.copied_configs = result.copied_configs
    promoted.copied_backups = result.copied_backups
    promoted.credential = result.credential
    if promoted.ok:
        _write_first_run_marker(target_dir, {"rows": result.rows})
        promoted.detail = promoted.detail or "迁移完成并已提交"
        log.info("数据目录迁移完成：%s 行数据已进入 %s", result.rows, target_dir.name)
    return promoted


def promote(staging: Path, target_dir: Path, logger_=None, mode=MODE_RESUME) -> Result:
    """把已验证的 staging 提交为正式目标目录。目标存在则拒绝，绝不合并。"""
    log = logger_ or logger
    result = Result(ok=False, mode=mode, staging=staging)
    staging = Path(staging)
    target_dir = Path(target_dir)
    if _valid_staging(staging) is None:
        result.errors.append("staging 不完整或未通过校验，拒绝提交")
        return result
    if target_dir.exists():
        if _has_real_content(target_dir):
            result.mode = MODE_BLOCKED
            result.errors.append(f"{target_dir.name} 已存在且含数据，拒绝覆盖或合并")
            return result
        # 只含空脚手架：移除后提交（不合并任何既有内容）
        try:
            shutil.rmtree(target_dir)
        except OSError as exc:
            result.mode = MODE_BLOCKED
            result.errors.append(f"无法清理空脚手架目标目录：{type(exc).__name__}")
            return result
    try:
        staging.rename(target_dir)
    except OSError as exc:
        result.errors.append(f"提交失败：{type(exc).__name__}")
        return result
    result.ok = True
    result.detail = f"已从 {staging.name} 提交为 {target_dir.name}"
    log.info("数据目录提交完成：%s", result.detail)
    return result


def ensure_data_dir(logger_=None, force=False) -> Result:
    """应用启动时调用：按决策执行迁移 / 沿用 / 阻断。

    数据目录被 TODO_APP_DATA_DIR 接管时（测试 / 演练 / CI）整体跳过，
    绝不据此推断真实目录状态。force=True 仅供迁移模块自身测试使用。
    """
    log = logger_ or logger
    if not force:
        if app_paths.is_data_dir_overridden():
            return Result(ok=True, mode=MODE_SKIPPED,
                          detail="数据目录被 TODO_APP_DATA_DIR 接管，跳过迁移")
        if os.environ.get(DISABLE_ENV):
            return Result(ok=True, mode=MODE_SKIPPED,
                          detail="TODO_APP_DISABLE_MIGRATION 已设置，跳过迁移")
    # FRESH INSTALL HOTFIX：**判定必须发生在任何 TaiPlan 写入之前**。
    # 以前这里先 ensure_user_directories()，全新用户会被自己的目录树误判成 partial。
    decision = plan()

    # 判定通过（非 BLOCKED）后，才允许创建/写入数据目录树。
    if decision.mode != MODE_BLOCKED:
        try:
            app_paths.ensure_user_directories()
        except OSError as exc:
            log.warning("用户目录创建失败：%s", type(exc).__name__)
    if decision.mode == MODE_KEEP_NEW:
        return Result(ok=True, mode=MODE_KEEP_NEW, detail=decision.reason)
    if decision.mode == MODE_FRESH:
        try:
            app_paths.ensure_user_directories()
        except OSError as exc:
            return Result(ok=False, mode=MODE_FRESH, errors=[f"{type(exc).__name__}"])
        # FRESH INSTALL HOTFIX：全新用户也要留下"被程序正常初始化过"的痕迹，
        # 否则运行期刚建出来的空库会在下一次判定里变成"无标记的库"而被阻断。
        try:
            _write_first_run_marker(decision.target_dir, {}, fresh=True)
        except Exception as exc:  # noqa: BLE001
            log.warning("首次运行标记写入失败：%s", type(exc).__name__)
        return Result(ok=True, mode=MODE_FRESH, detail=decision.reason)
    if decision.mode == MODE_BLOCKED:
        log.error("数据目录状态异常：%s", decision.reason)
        return Result(ok=False, mode=MODE_BLOCKED, detail=decision.reason,
                      errors=[decision.reason])
    if decision.mode == MODE_RESUME:
        return promote(decision.staging_dir, decision.target_dir, logger_=log)
    return migrate(logger_=log)


__all__ = ["Decision", "Result", "plan", "inspect", "migrate", "promote", "ensure_data_dir",
           "has_success_marker", "marker_path", "iter_staging_dirs", "BUSINESS_TABLES",
           "RUNTIME_TABLES", "CONFIG_WHITELIST", "MODE_KEEP_NEW", "MODE_MIGRATE",
           "MODE_FRESH", "MODE_RESUME", "MODE_BLOCKED", "MODE_SKIPPED", "DISABLE_ENV"]
