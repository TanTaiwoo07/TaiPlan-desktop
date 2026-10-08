# -*- coding: utf-8 -*-
"""测试包。

运行方式（均需在项目根目录执行）：

    .venv\\Scripts\\python.exe -m unittest discover -s tests -p "test_*.py"
    .venv\\Scripts\\python.exe -m unittest tests.test_services

测试文件里对项目模块（database / services / app_paths 等）的平铺 import
依赖项目根目录在 sys.path 上；unittest discovery 遇到 tests 是包时会把
项目根作为 top_level_dir 自动加入。个别测试文件另有一行
``sys.path.insert(0, <项目根>)`` 引导，保证 ``python tests/test_xxx.py``
这种单文件直跑方式也能工作。
"""
