"""SQLite 数据访问层。

本模块专门负责 SQLite 相关操作：
- 建立数据库连接
- 初始化数据表（含向后兼容的 Schema Migration）
- 后续所有 SQL 数据操作都放在这里
"""

import sqlite3
from datetime import datetime
from pathlib import Path

from config import DB_PATH

# 当前 Schema 版本。对应下面 5 个逻辑迁移步骤：
#   1 initial          任务表
#   2 recurrence       重复规则 + task_occurrence_states
#   3 notifications    notification_log + runtime_status
#   4 duration         tasks.duration_minutes
#   5 overrides        task_occurrence_overrides
SCHEMA_VERSION = 5

SCHEMA_MIGRATIONS = [
    (1, "initial", "tasks 基础表"),
    (2, "recurrence", "重复规则字段 + task_occurrence_states"),
    (3, "notifications", "notification_log + runtime_status"),
    (4, "duration", "tasks.duration_minutes"),
    (5, "overrides", "task_occurrence_overrides"),
]


class DatabaseUnavailable(Exception):
    """数据库无法打开（损坏 / 权限 / 被占用）。

    绝不在这种情况下自动新建空库，否则用户会以为任务全部丢失。
    """


def _database_path() -> Path:
    return Path(DB_PATH)


def _logger():
    try:
        import logging
        return logging.getLogger("todoapp.app")
    except Exception:  # noqa: BLE001
        return None


def _backup_before_migration() -> None:
    """迁移前自动备份，失败不阻塞启动（只记日志）。"""
    try:
        import data_backup
        path = data_backup.backup_database(db_path=_database_path(), logger=_logger())
        lg = _logger()
        if lg:
            lg.info("Schema 迁移前已备份：%s", path.name)
    except Exception as exc:  # noqa: BLE001
        lg = _logger()
        if lg:
            lg.warning("迁移前备份失败：%s", type(exc).__name__)


def _now() -> str:
    """返回当前本地时间字符串。"""
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _normalize_optional(value):
    """空字符串转为 None，保证整个项目对「未设置」统一存 NULL。"""
    if value is None:
        return None
    s = str(value).strip()
    return s if s else None


# 重复任务相关的新增字段定义（用于 CREATE TABLE 和 Migration 共用）
RECURRENCE_COLUMNS = {
    "is_recurring": "INTEGER NOT NULL DEFAULT 0",
    "recurrence_frequency": "TEXT",
    "recurrence_interval": "INTEGER NOT NULL DEFAULT 1",
    "recurrence_end_type": "TEXT",
    "recurrence_end_date": "TEXT",
    "recurrence_stop_before": "TEXT",
}

# 12 阶段新增：任务持续时间（分钟）
TASK_EXTRA_COLUMNS = {
    "duration_minutes": "INTEGER",
}


ALL_TASK_COLUMNS = {**RECURRENCE_COLUMNS, **TASK_EXTRA_COLUMNS}


def get_connection() -> sqlite3.Connection:
    """建立并返回一个数据库连接。

    每个线程/进程使用自己的 connection，不跨线程共享。
    WAL 模式 + busy_timeout 减少 database is locked。
    """
    conn = sqlite3.connect(DB_PATH, timeout=5.0)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
    except sqlite3.Error:
        # 关键：PRAGMA 失败时不能让连接泄漏，否则文件句柄会一直占着，
        # 之后备份 / 恢复替换 todo.db 会失败（WinError 5）
        conn.close()
        raise
    return conn


def _create_app_meta_table(conn) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS app_meta (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        )
        """
    )


def get_schema_version(conn=None) -> int:
    """返回数据库记录的 Schema 版本；旧库（无 app_meta）返回 0。"""
    own = conn is None
    if own:
        conn = get_connection()
    try:
        try:
            row = conn.execute(
                "SELECT value FROM app_meta WHERE key = 'schema_version'").fetchone()
        except sqlite3.Error:
            return 0
        if not row:
            return 0
        try:
            return int(row[0])
        except (TypeError, ValueError):
            return 0
    finally:
        if own:
            conn.close()


def set_schema_version(conn, version=SCHEMA_VERSION) -> None:
    _create_app_meta_table(conn)
    conn.execute(
        "INSERT INTO app_meta(key, value, updated_at) VALUES('schema_version', ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
        "updated_at = excluded.updated_at",
        (str(int(version)), _now()),
    )


def get_meta(key, default=None):
    conn = get_connection()
    try:
        try:
            row = conn.execute("SELECT value FROM app_meta WHERE key = ?", (key,)).fetchone()
        except sqlite3.Error:
            return default
        return row[0] if row else default
    finally:
        conn.close()


def set_meta(key, value) -> None:
    conn = get_connection()
    try:
        _create_app_meta_table(conn)
        conn.execute(
            "INSERT INTO app_meta(key, value, updated_at) VALUES(?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (str(key), str(value), _now()),
        )
        conn.commit()
    finally:
        conn.close()


def check_database_integrity(db_path=None, quick=True) -> dict:
    """PRAGMA quick_check 完整性检查。不要每次启动都跑。"""
    path = Path(db_path) if db_path else _database_path()
    result = {"path": str(path), "exists": path.is_file(), "ok": False, "detail": ""}
    if not result["exists"]:
        result["detail"] = "数据库文件不存在"
        return result
    try:
        conn = sqlite3.connect(str(path), timeout=5.0)
    except sqlite3.Error as exc:
        result["detail"] = f"{type(exc).__name__}: {exc}"
        return result
    try:
        pragma = "quick_check" if quick else "integrity_check"
        row = conn.execute(f"PRAGMA {pragma}").fetchone()
        detail = str(row[0]) if row else ""
        result["ok"] = detail.lower() == "ok"
        result["detail"] = detail
    except sqlite3.Error as exc:
        result["detail"] = f"{type(exc).__name__}: {exc}"
    finally:
        conn.close()
    return result


def _migrate_tasks(conn) -> None:
    """向后兼容的 Schema Migration（幂等：先查 PRAGMA table_info）。"""
    existing = {row[1] for row in conn.execute("PRAGMA table_info(tasks)")}
    for name, ddl in ALL_TASK_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE tasks ADD COLUMN {name} {ddl}")


def _create_occurrence_overrides_table(conn) -> None:
    """创建单个 occurrence 覆盖表（仅修改这一次）。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS task_occurrence_overrides (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            occurrence_key TEXT NOT NULL,
            override_date TEXT,
            override_time TEXT,
            override_duration_minutes INTEGER,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(task_id, occurrence_key)
        )
        """
    )


def _create_occurrence_state_table(conn) -> None:
    """创建 occurrence 状态表（如果不存在）。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS task_occurrence_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id INTEGER NOT NULL,
            occurrence_key TEXT NOT NULL,
            status TEXT NOT NULL,
            completed_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(task_id, occurrence_key)
        )
        """
    )


def _create_notification_log_table(conn) -> None:
    """创建通知日志表（如果不存在），并幂等补充新列。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS notification_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            notification_key TEXT NOT NULL UNIQUE,
            task_id INTEGER,
            occurrence_key TEXT,
            notification_type TEXT NOT NULL,
            fired_at TEXT NOT NULL,
            delivery_status TEXT,
            delivery_channel TEXT,
            delivery_error TEXT,
            delivered_at TEXT,
            retry_count INTEGER DEFAULT 0,
            title_snapshot TEXT,
            due_at TEXT,
            priority_snapshot TEXT,
            hidden_from_history INTEGER DEFAULT 0
        )
        """
    )
    existing = {row[1] for row in conn.execute("PRAGMA table_info(notification_log)")}
    extra_cols = {
        "delivery_status": "TEXT",
        "delivery_channel": "TEXT",
        "delivery_error": "TEXT",
        "delivered_at": "TEXT",
        "retry_count": "INTEGER DEFAULT 0",
        "title_snapshot": "TEXT",
        "due_at": "TEXT",
        "priority_snapshot": "TEXT",
        "hidden_from_history": "INTEGER DEFAULT 0",
    }
    for name, ddl in extra_cols.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE notification_log ADD COLUMN {name} {ddl}")


def _create_runtime_status_table(conn) -> None:
    """创建后台服务状态表（如果不存在）。"""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_status (
            component TEXT PRIMARY KEY,
            pid INTEGER,
            heartbeat_at TEXT,
            started_at TEXT,
            status TEXT
        )
        """
    )


def _migrate_legacy_recurring_completed(conn) -> None:
    """迁移 7A 阶段旧数据：is_recurring=1 且 is_completed=1 的 master task。"""
    rows = conn.execute(
        "SELECT id, date, time, completed_at FROM tasks WHERE is_recurring = 1 AND is_completed = 1"
    ).fetchall()
    for row in rows:
        if row["date"] is None:
            continue
        key = row["date"]
        if row["time"]:
            key = f"{row['date']}T{row['time']}"
        now = _now()
        conn.execute(
            """
            INSERT OR IGNORE INTO task_occurrence_states
                (task_id, occurrence_key, status, completed_at, created_at, updated_at)
            VALUES (?, ?, 'completed', ?, ?, ?)
            """,
            (row["id"], key, row["completed_at"] or now, now, now),
        )
        conn.execute(
            "UPDATE tasks SET is_completed = 0, completed_at = NULL WHERE id = ?",
            (row["id"],),
        )


def init_database() -> None:
    """自动创建数据库以及所有表，并执行必要的字段迁移。

    - 已存在的库：先确认可打开；版本落后时先备份再迁移
    - 全新库：直接建表并登记 schema_version
    - 打开失败：抛 DatabaseUnavailable，绝不新建空库覆盖
    """
    db_path = _database_path()
    was_missing = not db_path.exists()

    try:
        conn = get_connection()
    except sqlite3.Error as exc:
        raise DatabaseUnavailable(
            f"数据库无法打开：{db_path}") from exc

    try:
        existing_version = 0
        if not was_missing:
            try:
                # 廉价探针：能读出 PRAGMA 说明文件头是有效的 SQLite
                conn.execute("PRAGMA schema_version").fetchone()
            except sqlite3.DatabaseError as exc:
                raise DatabaseUnavailable(
                    f"数据库文件已损坏或不是有效的 SQLite 文件：{db_path}") from exc
            existing_version = get_schema_version(conn)

        needs_migration = (not was_missing) and existing_version < SCHEMA_VERSION
        if needs_migration:
            _backup_before_migration()

        conn.isolation_level = None       # 手动控制事务
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    description TEXT,
                    url TEXT,
                    date TEXT,
                    time TEXT,
                    priority TEXT NOT NULL DEFAULT 'normal'
                        CHECK (priority IN ('low', 'normal', 'urgent')),
                    is_completed INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT,
                    is_recurring INTEGER NOT NULL DEFAULT 0,
                    recurrence_frequency TEXT,
                    recurrence_interval INTEGER NOT NULL DEFAULT 1,
                    recurrence_end_type TEXT,
                    recurrence_end_date TEXT,
                    recurrence_stop_before TEXT,
                    duration_minutes INTEGER
                )
                """
            )
            _create_app_meta_table(conn)
            _migrate_tasks(conn)
            _create_occurrence_state_table(conn)
            _create_occurrence_overrides_table(conn)
            _create_notification_log_table(conn)
            _create_runtime_status_table(conn)
            _migrate_legacy_recurring_completed(conn)
            set_schema_version(conn, SCHEMA_VERSION)
            conn.execute("COMMIT")
        except Exception:
            try:
                conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
            raise
    finally:
        conn.close()


def add_task(title, description=None, url=None, date=None, time=None, priority="normal",
             is_recurring=0, recurrence_frequency=None, recurrence_interval=1,
             recurrence_end_type=None, recurrence_end_date=None,
             duration_minutes=None) -> None:
    """新增完整任务（含重复规则与持续时间）。

    duration_minutes 仅在 date + time 均存在时才有意义；无时间时强制存 NULL。
    """
    title = (title or "").strip()
    if not title:
        return

    description = _normalize_optional(description)
    url = _normalize_optional(url)
    date = _normalize_optional(date)
    time = _normalize_optional(time)
    recurrence_frequency = _normalize_optional(recurrence_frequency)
    recurrence_end_type = _normalize_optional(recurrence_end_type)
    recurrence_end_date = _normalize_optional(recurrence_end_date)
    if time is None or date is None:
        duration_minutes = None
    elif duration_minutes is not None:
        duration_minutes = int(duration_minutes)

    now = _now()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO tasks
                (title, description, url, date, time, priority,
                 is_completed, created_at, updated_at,
                 is_recurring, recurrence_frequency, recurrence_interval,
                 recurrence_end_type, recurrence_end_date, duration_minutes)
            VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (title, description, url, date, time, priority, now, now,
             int(is_recurring), recurrence_frequency, int(recurrence_interval),
             recurrence_end_type, recurrence_end_date, duration_minutes),
        )
        conn.commit()
    finally:
        conn.close()


def get_all_tasks():
    """返回数据库中的所有任务。"""
    conn = get_connection()
    try:
        return conn.execute(
            """
            SELECT * FROM tasks
            ORDER BY is_completed ASC, created_at ASC, id ASC
            """
        ).fetchall()
    finally:
        conn.close()


def get_task_by_id(task_id):
    """根据数据库 ID 查询单个任务；不存在返回 None。"""
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT * FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
    finally:
        conn.close()


def update_task(task_id, title, description=None, url=None, date=None, time=None,
                priority="normal", is_recurring=0, recurrence_frequency=None,
                recurrence_interval=1, recurrence_end_type=None,
                recurrence_end_date=None, duration_minutes=None) -> None:
    """编辑现有任务（含重复规则与持续时间）。"""
    title = (title or "").strip()
    if not title:
        return

    description = _normalize_optional(description)
    url = _normalize_optional(url)
    date = _normalize_optional(date)
    time = _normalize_optional(time)
    recurrence_frequency = _normalize_optional(recurrence_frequency)
    recurrence_end_type = _normalize_optional(recurrence_end_type)
    recurrence_end_date = _normalize_optional(recurrence_end_date)
    if time is None or date is None:
        duration_minutes = None
    elif duration_minutes is not None:
        duration_minutes = int(duration_minutes)

    now = _now()
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE tasks
            SET title = ?, description = ?, url = ?, date = ?, time = ?,
                priority = ?, updated_at = ?,
                is_recurring = ?, recurrence_frequency = ?, recurrence_interval = ?,
                recurrence_end_type = ?, recurrence_end_date = ?, duration_minutes = ?
            WHERE id = ?
            """,
            (title, description, url, date, time, priority, now,
             int(is_recurring), recurrence_frequency, int(recurrence_interval),
             recurrence_end_type, recurrence_end_date, duration_minutes, task_id),
        )
        conn.commit()
    finally:
        conn.close()


def set_task_completed(task_id, completed) -> None:
    """标记任务完成或取消完成。"""
    now = _now()
    conn = get_connection()
    try:
        if completed:
            conn.execute(
                """
                UPDATE tasks
                SET is_completed = 1, completed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                (now, now, task_id),
            )
        else:
            conn.execute(
                """
                UPDATE tasks
                SET is_completed = 0, completed_at = NULL, updated_at = ?
                WHERE id = ?
                """,
                (now, task_id),
            )
        conn.commit()
    finally:
        conn.close()


def delete_task(task_id) -> None:
    """根据任务 id 永久删除任务，并清理 occurrence 状态与 override。"""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        conn.execute("DELETE FROM task_occurrence_states WHERE task_id = ?", (task_id,))
        conn.execute("DELETE FROM task_occurrence_overrides WHERE task_id = ?", (task_id,))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------
# occurrence 状态表操作（完成某一次重复 occurrence）
# ---------------------------------------------------------------

def set_occurrence_state(task_id, occurrence_key, status) -> None:
    """UPSERT 一次 occurrence 的状态（completed / cancelled 等）。"""
    now = _now()
    completed_at = now if status == "completed" else None
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO task_occurrence_states
                (task_id, occurrence_key, status, completed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id, occurrence_key)
            DO UPDATE SET status = excluded.status, completed_at = excluded.completed_at,
                          updated_at = excluded.updated_at
            """,
            (task_id, occurrence_key, status, completed_at, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def set_occurrence_completed(task_id, occurrence_key, completed) -> None:
    """标记某一次 occurrence 的完成状态。"""
    if completed:
        set_occurrence_state(task_id, occurrence_key, "completed")
    else:
        conn = get_connection()
        try:
            conn.execute(
                "DELETE FROM task_occurrence_states WHERE task_id = ? AND occurrence_key = ?",
                (task_id, occurrence_key),
            )
            conn.commit()
        finally:
            conn.close()


def get_occurrence_states_for_task(task_id):
    """返回指定 task 的所有 occurrence 状态记录。"""
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT * FROM task_occurrence_states WHERE task_id = ?",
            (task_id,),
        ).fetchall()
    finally:
        conn.close()


def get_cancelled_occurrence_keys_for_tasks(task_ids):
    """批量返回多个 task 的所有 cancelled occurrence_key 集合。"""
    if not task_ids:
        return {}
    placeholders = ",".join("?" for _ in task_ids)
    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT task_id, occurrence_key FROM task_occurrence_states "
            f"WHERE status = 'cancelled' AND task_id IN ({placeholders})",
            tuple(task_ids),
        ).fetchall()
        result = {}
        for r in rows:
            result.setdefault(r["task_id"], set()).add(r["occurrence_key"])
        return result
    finally:
        conn.close()


def set_recurrence_stop_before(task_id, stop_before) -> None:
    """设置任务的 recurrence_stop_before。"""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE tasks SET recurrence_stop_before = ?, updated_at = ? WHERE id = ?",
            (stop_before, _now(), task_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_occurrence_state(task_id, occurrence_key):
    """查询某次 occurrence 的状态记录；无记录返回 None。"""
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT * FROM task_occurrence_states WHERE task_id = ? AND occurrence_key = ?",
            (task_id, occurrence_key),
        ).fetchone()
    finally:
        conn.close()


def get_occurrence_states():
    """返回所有 occurrence 状态记录。"""
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT * FROM task_occurrence_states ORDER BY completed_at DESC"
        ).fetchall()
    finally:
        conn.close()


def get_completed_occurrences():
    """返回所有 status='completed' 的 occurrence 状态记录。"""
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT * FROM task_occurrence_states WHERE status = 'completed' ORDER BY completed_at DESC"
        ).fetchall()
    finally:
        conn.close()


def get_completed_occurrence_keys_for_task(task_id):
    """返回指定 task 的所有已完成 occurrence_key 集合。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT occurrence_key FROM task_occurrence_states WHERE task_id = ? AND status = 'completed'",
            (task_id,),
        ).fetchall()
        return {r["occurrence_key"] for r in rows}
    finally:
        conn.close()


def get_completed_occurrence_keys_for_tasks(task_ids):
    """批量返回多个 task 的所有已完成 occurrence_key 集合。"""
    if not task_ids:
        return {}
    placeholders = ",".join("?" for _ in task_ids)
    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT task_id, occurrence_key FROM task_occurrence_states "
            f"WHERE status = 'completed' AND task_id IN ({placeholders})",
            tuple(task_ids),
        ).fetchall()
        result = {}
        for r in rows:
            result.setdefault(r["task_id"], set()).add(r["occurrence_key"])
        return result
    finally:
        conn.close()


def delete_occurrence_states_for_task(task_id) -> None:
    """删除某 task 的所有 occurrence 状态记录。"""
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM task_occurrence_states WHERE task_id = ?", (task_id,)
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------
# task_occurrence_overrides（仅修改这一次的 occurrence 覆盖）
# ---------------------------------------------------------------

def set_occurrence_override(task_id, occurrence_key, override_date=None,
                            override_time=None, override_duration_minutes=None) -> None:
    """UPSERT 一次 occurrence 的覆盖值（仅在修改这一次时使用）。"""
    now = _now()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO task_occurrence_overrides
                (task_id, occurrence_key, override_date, override_time,
                 override_duration_minutes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(task_id, occurrence_key)
            DO UPDATE SET override_date = excluded.override_date,
                          override_time = excluded.override_time,
                          override_duration_minutes = excluded.override_duration_minutes,
                          updated_at = excluded.updated_at
            """,
            (task_id, occurrence_key, override_date, override_time,
             override_duration_minutes, now, now),
        )
        conn.commit()
    finally:
        conn.close()


def get_occurrence_override(task_id, occurrence_key):
    """查询单条 override；不存在返回 None。"""
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT * FROM task_occurrence_overrides WHERE task_id = ? AND occurrence_key = ?",
            (task_id, occurrence_key),
        ).fetchone()
    finally:
        conn.close()


def get_occurrence_overrides_for_task(task_id):
    """返回某 task 的所有 override（以 occurrence_key 为键的 dict）。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM task_occurrence_overrides WHERE task_id = ?", (task_id,)
        ).fetchall()
        return {r["occurrence_key"]: r for r in rows}
    finally:
        conn.close()


def get_occurrence_overrides_for_tasks(task_ids):
    """批量返回多个 task 的 override：{task_id: {occurrence_key: row}}。"""
    if not task_ids:
        return {}
    placeholders = ",".join("?" for _ in task_ids)
    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT * FROM task_occurrence_overrides WHERE task_id IN ({placeholders})",
            tuple(task_ids),
        ).fetchall()
        result = {}
        for r in rows:
            result.setdefault(r["task_id"], {})[r["occurrence_key"]] = r
        return result
    finally:
        conn.close()


def get_occurrence_overrides_in_range(range_start, range_end):
    """返回 override_date 落在 [range_start, range_end] 的所有 override。

    用于「移出原日期后仍能在新日期显示」：即使 base occurrence 不在当前 range，
    只要 override_date 在 range 内就必须展示。
    """
    conn = get_connection()
    try:
        return conn.execute(
            """
            SELECT * FROM task_occurrence_overrides
            WHERE override_date IS NOT NULL
              AND override_date >= ? AND override_date <= ?
            """,
            (range_start, range_end),
        ).fetchall()
    finally:
        conn.close()


def delete_occurrence_override(task_id, occurrence_key) -> None:
    """删除单条 override。"""
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM task_occurrence_overrides WHERE task_id = ? AND occurrence_key = ?",
            (task_id, occurrence_key),
        )
        conn.commit()
    finally:
        conn.close()


def delete_occurrence_overrides_for_task(task_id) -> None:
    """删除某 task 的所有 override。"""
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM task_occurrence_overrides WHERE task_id = ?", (task_id,)
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------
# notification_log 表操作（提醒去重 + claim + 投递状态）
# ---------------------------------------------------------------

def has_notification_fired(notification_key) -> bool:
    """检查某个提醒 key 是否已经触发过。"""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM notification_log WHERE notification_key = ?",
            (notification_key,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def mark_notification_fired(notification_key, task_id=None, occurrence_key=None,
                            notification_type="due") -> None:
    """写入一条提醒日志。UNIQUE 约束保证不重复。"""
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR IGNORE INTO notification_log
                (notification_key, task_id, occurrence_key, notification_type, fired_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (notification_key, task_id, occurrence_key, notification_type, _now()),
        )
        conn.commit()
    finally:
        conn.close()


def claim_notification(notification_key, task_id=None, occurrence_key=None,
                       notification_type="due", title_snapshot=None,
                       due_at=None, priority_snapshot=None) -> bool:
    """原子 claim 一条提醒。

    返回 True 表示本进程获得发送权；False 表示已被其他进程 claim。
    依赖 UNIQUE 约束，先 claim 再发送。
    """
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO notification_log
                (notification_key, task_id, occurrence_key, notification_type,
                 fired_at, delivery_status, title_snapshot, due_at, priority_snapshot)
            VALUES (?, ?, ?, ?, ?, 'claimed', ?, ?, ?)
            """,
            (notification_key, task_id, occurrence_key, notification_type,
             _now(), title_snapshot, due_at, priority_snapshot),
        )
        conn.commit()
        return cur.rowcount == 1
    finally:
        conn.close()


def update_notification_delivery(notification_key, status, channel=None,
                                 error=None, delivered_at=None) -> None:
    """更新提醒投递状态。"""
    conn = get_connection()
    try:
        if delivered_at is None and status == "delivered":
            delivered_at = _now()
        conn.execute(
            """
            UPDATE notification_log
            SET delivery_status = ?, delivery_channel = ?,
                delivery_error = ?, delivered_at = ?
            WHERE notification_key = ?
            """,
            (status, channel, error, delivered_at, notification_key),
        )
        conn.commit()
    finally:
        conn.close()


def increment_notification_retry(notification_key) -> None:
    """投递失败时增加重试计数。"""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE notification_log SET retry_count = retry_count + 1 WHERE notification_key = ?",
            (notification_key,),
        )
        conn.commit()
    finally:
        conn.close()


def get_claimable_failed_notifications(max_retry=3):
    """返回投递失败且未超过重试次数的提醒。"""
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT * FROM notification_log WHERE delivery_status = 'failed' AND retry_count < ?",
            (max_retry,),
        ).fetchall()
    finally:
        conn.close()


def get_recent_notifications(limit=20):
    """返回最近提醒历史（不含 hidden）。"""
    conn = get_connection()
    try:
        return conn.execute(
            """
            SELECT * FROM notification_log
            WHERE hidden_from_history = 0
            ORDER BY fired_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()


def hide_notification_history() -> None:
    """清除提醒历史：标记 hidden（不 DELETE，保留防重复）。"""
    conn = get_connection()
    try:
        conn.execute("UPDATE notification_log SET hidden_from_history = 1")
        conn.commit()
    finally:
        conn.close()


def delete_notification_logs_for_task(task_id) -> None:
    """删除某 task 的所有提醒日志。"""
    conn = get_connection()
    try:
        conn.execute(
            "DELETE FROM notification_log WHERE task_id = ?", (task_id,)
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------
# 后台服务状态（heartbeat）
# ---------------------------------------------------------------

def update_runtime_status(component, pid=None, status="running", heartbeat_at=None) -> None:
    """更新后台组件状态。"""
    if heartbeat_at is None:
        heartbeat_at = _now()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO runtime_status (component, pid, heartbeat_at, started_at, status)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(component)
            DO UPDATE SET pid = excluded.pid, heartbeat_at = excluded.heartbeat_at,
                          status = excluded.status
            """,
            (component, pid, heartbeat_at, _now(), status),
        )
        conn.commit()
    finally:
        conn.close()


def get_runtime_status(component):
    """获取某组件状态。"""
    conn = get_connection()
    try:
        return conn.execute(
            "SELECT * FROM runtime_status WHERE component = ?", (component,)
        ).fetchone()
    finally:
        conn.close()


def delete_runtime_status(component) -> None:
    """删除组件状态（退出时）。"""
    conn = get_connection()
    try:
        conn.execute("DELETE FROM runtime_status WHERE component = ?", (component,))
        conn.commit()
    finally:
        conn.close()


def is_worker_alive(max_age_seconds=60) -> bool:
    """根据 heartbeat 判断 worker 是否存活。"""
    row = get_runtime_status("reminder_worker")
    if row is None or row["heartbeat_at"] is None:
        return False
    try:
        hb = datetime.strptime(row["heartbeat_at"], "%Y-%m-%d %H:%M:%S")
        return (datetime.now() - hb).total_seconds() <= max_age_seconds
    except ValueError:
        return False
