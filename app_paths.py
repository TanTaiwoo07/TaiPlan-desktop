r"""全项目唯一的路径来源。

严格区分两类目录：

  只读程序资源  resource dir / assets        —— 安装目录，运行时不得写入
  可写用户数据  user data dir (%LOCALAPPDATA%\TodoApp) —— 库 / 配置 / 日志 / 备份 / 状态

任何模块都不要再自己拼 Path("todo.db") / Path("logs") / Path("appearance.json")，
一律通过本模块取得。

路径每次调用都重新计算（不缓存），因此：
  - 环境变量 TODO_APP_DATA_DIR 可以随时覆盖用户数据目录（测试用）
  - 未来 PyInstaller frozen 模式可以直接生效
"""

import os
import sys
from pathlib import Path

ENV_DATA_DIR = "TODO_APP_DATA_DIR"
ENV_LEGACY_DIR = "TODO_APP_LEGACY_DIR"
# W3：正式数据目录改名 TaiPlan；旧目录 TodoApp 只读保留、永不删除
APP_DIR_NAME = "TaiPlan"
LEGACY_APP_DIR_NAME = "TodoApp"
ENV_LEGACY_APP_DIR = "TODO_APP_LEGACY_APP_DIR"
STAGING_INFIX = ".migrating-"

DB_FILENAME = "todo.db"
CONFIG_DIRNAME = "config"
LOGS_DIRNAME = "logs"
BOOTSTRAP_LOG_DIR_ENV = "TODO_APP_BOOTSTRAP_LOG_DIR"
BACKUPS_DIRNAME = "backups"
STATE_DIRNAME = "state"

# 旧版放在项目根目录的配置文件名 → 新配置目录文件名
LEGACY_CONFIG_FILES = {
    "appearance.json": "appearance.json",
    "ai_config.json": "ai_config.json",
    "notification_config.json": "notification_config.json",
    "calendar_config.json": "calendar_config.json",
    "runtime_config.json": "desktop_config.json",
    "desktop_config.json": "desktop_config.json",
}


# ---------------------------------------------------------------
# 程序资源（只读）
# ---------------------------------------------------------------

def is_frozen() -> bool:
    """是否运行在 PyInstaller 打包环境中。"""
    return bool(getattr(sys, "frozen", False))


def get_resource_dir() -> Path:
    """只读程序资源根目录。

    - 源码模式：项目根目录
    - frozen 模式：PyInstaller 解包目录 (_MEIPASS)
    """
    if is_frozen():
        base = getattr(sys, "_MEIPASS", None)
        if base:
            return Path(base)
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def get_project_root() -> Path:
    """源码模式下的项目根目录（frozen 模式下等于可执行文件所在目录）。"""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_path(relative_path) -> Path:
    """资源相对路径 → 绝对路径，兼容源码模式与未来的 PyInstaller。"""
    return get_resource_dir() / str(relative_path)


def get_assets_dir() -> Path:
    return get_resource_dir() / "assets"


# ---------------------------------------------------------------
# 用户数据（可写）
# ---------------------------------------------------------------

def get_user_data_dir() -> Path:
    """用户数据根目录。测试可用 TODO_APP_DATA_DIR 覆盖。"""
    override = os.environ.get(ENV_DATA_DIR)
    if override:
        return Path(override).expanduser().resolve()

    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        if not base:
            base = Path.home() / "AppData" / "Local"
        return Path(base) / APP_DIR_NAME

    # 非 Windows 兜底（开发机 / CI）
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / APP_DIR_NAME


def is_data_dir_overridden() -> bool:
    return bool(os.environ.get(ENV_DATA_DIR))


def get_legacy_app_dir() -> Path:
    """W3 迁移源：旧版用户数据目录 %LOCALAPPDATA%\\TodoApp。

    与 get_legacy_dir()（第 14 阶段之前的项目根目录语义）不同，两者互不影响。
    TODO_APP_LEGACY_APP_DIR 可覆盖（测试 / 演练）。
    """
    override = os.environ.get(ENV_LEGACY_APP_DIR)
    if override:
        return Path(override).expanduser().resolve()
    parent = get_user_data_dir().parent
    return parent / LEGACY_APP_DIR_NAME


def get_staging_dir(token) -> Path:
    """迁移 staging 目录（与正式目录同级）。"""
    return get_user_data_dir().parent / f"{APP_DIR_NAME}{STAGING_INFIX}{token}"


def get_config_dir() -> Path:
    return get_user_data_dir() / CONFIG_DIRNAME


def get_logs_dir() -> Path:
    """正式日志目录；处于迁移判定前的 bootstrap 阶段时临时指向 %TEMP%。"""
    if os.environ.get(BOOTSTRAP_LOG_DIR_ENV):
        return get_bootstrap_logs_dir()
    return get_user_data_dir() / LOGS_DIRNAME


RESTART_FLAG_FILENAME = "ui_restart.flag"


def get_restart_flag_path() -> Path:
    """UI 重启标记：设置保存"需要重启"时写入，runtime 退出前消费。"""
    return get_state_path(RESTART_FLAG_FILENAME)


def get_bootstrap_logs_dir() -> Path:
    """迁移/全新判定**之前**的日志目录：放在 %TEMP%，绝不触碰正式数据目录。

    这样"程序自己写日志"不会让迁移判定把全新用户误判成 partial。
    """
    import tempfile

    override = os.environ.get("TODO_APP_BOOTSTRAP_LOG_DIR")
    if override:
        return Path(override)
    return Path(tempfile.gettempdir()) / f"{APP_DIR_NAME}-bootstrap"


def get_backup_dir() -> Path:
    return get_user_data_dir() / BACKUPS_DIRNAME


def get_state_dir() -> Path:
    return get_user_data_dir() / STATE_DIRNAME


def get_database_path() -> Path:
    return get_user_data_dir() / DB_FILENAME


def get_config_path(name) -> Path:
    return get_config_dir() / str(name)


def get_state_path(name) -> Path:
    return get_state_dir() / str(name)


def get_log_path(component) -> Path:
    return get_logs_dir() / f"{component}.log"


# ---------------------------------------------------------------
# 旧版（项目根目录）路径，仅用于一次性迁移
# ---------------------------------------------------------------

def get_legacy_dir() -> Path:
    """第 14 阶段之前用户数据所在的位置：项目根目录。

    TODO_APP_LEGACY_DIR 可覆盖迁移源（测试 / 手工迁移用）。
    """
    override = os.environ.get(ENV_LEGACY_DIR)
    if override:
        return Path(override).expanduser().resolve()
    return get_project_root()


def get_legacy_database_path() -> Path:
    return get_legacy_dir() / DB_FILENAME


def get_legacy_config_path(name) -> Path:
    return get_legacy_dir() / str(name)


def get_legacy_logs_dir() -> Path:
    return get_legacy_dir() / LOGS_DIRNAME


# ---------------------------------------------------------------
# 目录创建 / 权限
# ---------------------------------------------------------------

def iter_user_dirs():
    return (get_user_data_dir(), get_config_dir(), get_logs_dir(),
            get_backup_dir(), get_state_dir())


def ensure_user_directories() -> Path:
    """创建全部用户数据子目录，返回用户数据根目录。已存在则不动。"""
    root = get_user_data_dir()
    for path in iter_user_dirs():
        path.mkdir(parents=True, exist_ok=True)
    return root


def is_program_dir_writable() -> bool:
    """程序资源目录是否可写（安装到 Program Files 后为 False）。"""
    try:
        probe = get_resource_dir() / ".write_probe"
        probe.write_text("x", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def describe_paths() -> dict:
    """给 About / 诊断包用的路径摘要。"""
    return {
        "resource_dir": str(get_resource_dir()),
        "project_root": str(get_project_root()),
        "user_data_dir": str(get_user_data_dir()),
        "config_dir": str(get_config_dir()),
        "logs_dir": str(get_logs_dir()),
        "backup_dir": str(get_backup_dir()),
        "state_dir": str(get_state_dir()),
        "database": str(get_database_path()),
        "legacy_dir": str(get_legacy_dir()),
        "legacy_app_dir": str(get_legacy_app_dir()),
        "frozen": is_frozen(),
        "data_dir_overridden": is_data_dir_overridden(),
        "legacy_dir_overridden": bool(os.environ.get(ENV_LEGACY_DIR)),
        "program_dir_writable": is_program_dir_writable(),
    }
