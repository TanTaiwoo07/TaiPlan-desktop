# -*- coding: utf-8 -*-
"""TaiPlan 运行时包。

运行时模块统一收在此包内；仓库根目录只保留进程/构建入口：

- ``main.py``                PyInstaller 入口（桌面运行时 / 子进程分发）
- ``app.py``                 Streamlit 脚本（必须作为真实文件存在于 bundle 根）
- ``build*.py`` 等构建脚本   根目录执行，通过 ``from taiplan import ...`` 引用包内模块

包内模块之间的引用一律使用绝对导入（``from taiplan import x`` /
``from taiplan.ui import y``），不使用相对导入——因为 ``app.py`` 由 Streamlit
以脚本方式执行，不在包内。
"""
