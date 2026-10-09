"""应用配置模块。

集中管理数据库路径等配置项。
"""

from taiplan import app_paths

# 项目根目录（本文件所在目录）
BASE_DIR = app_paths.get_project_root()

# SQLite 数据库文件路径
DB_PATH = app_paths.get_database_path()
