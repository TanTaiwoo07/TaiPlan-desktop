"""测试环境隔离。

必须在导入任何项目模块之前 import 本模块——它只在导入时设置环境变量，
把「用户数据目录」和「旧数据迁移源」都指到临时目录，
保证测试永远不会碰到真实的 %LOCALAPPDATA%\\TodoApp 与项目根目录里的真实数据。

本模块位于 tests/ 包内（from tests import tests_env）。导入时顺便把项目根
目录插到 sys.path，保证 ``python tests/test_xxx.py`` 直跑时平铺 import 也能命中。
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if not os.environ.get("TODO_APP_DATA_DIR"):
    os.environ["TODO_APP_DATA_DIR"] = tempfile.mkdtemp(prefix="todoapp_test_data_")

if not os.environ.get("TODO_APP_LEGACY_DIR"):
    os.environ["TODO_APP_LEGACY_DIR"] = tempfile.mkdtemp(prefix="todoapp_test_legacy_")
