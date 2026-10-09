# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec：TaiPlan 发行版（--onedir）。

一般由 ``build.py`` 调用；也可以手写命令：

    .venv\\Scripts\\python.exe -m PyInstaller TaiPlan.spec --noconfirm

    TODO_APP_DEBUG=1 时生成带控制台的 Debug 版（dist/TaiPlan-Debug/）。

约定
----
- 只读资源（app.py / assets / tools）通过 ``datas`` 放到 bundle 根，
  与 ``app_paths.resource_path()`` 的语义一致（onedir 下 = ``_internal/``）。
- 自家模块全部显式进 ``hiddenimports``：``app.py`` 是交给 Streamlit 当脚本
  执行的，静态分析未必能从 main.py 的依赖图里推到它 import 的那些模块。
- 动态选择后端的包（keyring / pywebview / pystray）必须显式列后端模块。
- 不做 ``collect_all`` 全量收集，只按包名精确收集。
"""
import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import (collect_data_files, collect_submodules,
                                     copy_metadata)

PROJECT_ROOT = Path(SPECPATH).resolve()  # noqa: F821 - PyInstaller 注入
sys.path.insert(0, str(PROJECT_ROOT))

import packaging_tools  # noqa: E402
from taiplan import version as app_version  # noqa: E402

DEBUG = os.environ.get("TODO_APP_DEBUG") == "1"
EXE_NAME = packaging_tools.DEBUG_EXE if DEBUG else packaging_tools.RELEASE_EXE
BASE_NAME = EXE_NAME[:-4] if EXE_NAME.lower().endswith(".exe") else EXE_NAME
ICON = PROJECT_ROOT / "assets" / "app_icon.ico"

# ----------------------------------------------------------------------
# datas：只读资源
# ----------------------------------------------------------------------
datas = [
    # Streamlit 会把 app.py 当脚本执行，所以它必须是真实文件
    (str(PROJECT_ROOT / "app.py"), "."),
]
for sub in ("assets", "tools"):
    src = PROJECT_ROOT / sub
    if src.is_dir():
        datas.append((str(src), sub))

# Streamlit 前端静态资源 + streamlit_calendar 的前端 build（打包高风险点）
for pkg in ("streamlit", "streamlit_calendar"):
    try:
        datas += collect_data_files(pkg)
    except Exception as exc:  # noqa: BLE001
        print(f"[spec] collect_data_files({pkg}) 失败: {exc}")

# 需要 importlib.metadata 才能读到版本/入口点的包
for dist in ("streamlit", "streamlit_calendar", "pywebview", "pystray",
             "pillow", "keyring", "winotify"):
    try:
        datas += copy_metadata(dist)
    except Exception as exc:  # noqa: BLE001
        print(f"[spec] copy_metadata({dist}) 跳过: {exc}")

# ----------------------------------------------------------------------
# hiddenimports
# ----------------------------------------------------------------------
hiddenimports = []

# Streamlit 内部大量动态导入，按包精确收集子模块
try:
    hiddenimports += collect_submodules("streamlit")
except Exception as exc:  # noqa: BLE001
    print(f"[spec] collect_submodules(streamlit) 失败: {exc}")

hiddenimports += [
    # 前端组件
    "streamlit_calendar",
    # Windows 通知兜底（无官方 hook）
    "winotify",
    # 动态选择的后端，静态分析看不到
    "keyring.backends.Windows",
    "win32ctypes.core",
    "webview.platforms.winforms",
    "pystray._win32",
    "PIL.Image",
    # 自家模块：app.py 由 Streamlit 当脚本跑，必须全部可 import
    "taiplan.ui", "taiplan.ui.theme", "taiplan.ui.layout",
    "taiplan.ui.components", "taiplan.ui.icons",
]
# 自家运行时模块全部在 taiplan/ 包内（根目录只留进程/构建入口 main.py、app.py
# 与构建脚本，两者都不进这个 glob）。
hiddenimports += sorted(
    "taiplan." + str(p.relative_to(PROJECT_ROOT / "taiplan").with_suffix(""))
                     .replace("\\", ".").replace("/", ".")
    for p in (PROJECT_ROOT / "taiplan").rglob("*.py")
    if p.stem != "__init__" and not p.stem.startswith("test_")
)

# ----------------------------------------------------------------------
# 版本资源
# ----------------------------------------------------------------------
version_file = packaging_tools.write_version_file(
    PROJECT_ROOT / "build_logs" / f"version_info_{BASE_NAME}.txt",
    app_version.__version__,
    exe_name=EXE_NAME,
)

# 第 16 阶段体积优化：这里是**有证据**的排除，不是"为了瘦身随手删"。
#
# 证据 1：import graph 实测——`import streamlit` 之后 sys.modules 里
#         没有 pyarrow / pandas / numpy / altair（全部为 False）；
#         在 streamlit 自身代码里，这四个包**没有任何模块级 import**
#         （pyarrow 0 处、altair 0 处；pandas/numpy 的模块级 import 只出现在
#          hello/ 与 .agents/ 的示例 app 里，不在运行路径上）。
# 证据 2：全项目检索——自家代码不使用 pandas/numpy/pyarrow/altair，
#         也没有 st.dataframe / st.data_editor / st.table / st.altair_chart
#         等任何 dataframe / 图表组件。
# 证据 3：体积基线（analyze_dist.py）——pyarrow 78.94 MB(42%)、
#         numpy+numpy.libs 26.29 MB、pandas 12.37 MB、altair 1.84 MB。
#
# 这些包只有在真正调用 dataframe / 图表组件时才会被延迟导入；
# 本应用不会走到那些分支。排除后仍必须通过 EXE 全回归（smoke test +
# 真机 Today/Calendar/AI 检查）才算数。
# ★ W1.2（17.3）：numpy 与 pyarrow **不得**再排除 ——
#   真实 traceback 证明它们是运行时硬依赖：
#     * numpy   ← streamlit/time_util.py:69  `import numpy as np`  ← st.fragment(run_every=…)
#     * pandas  ← streamlit/time_util.py:70  `import pandas as pd`（同一 str 分支）
#                 证据：streamlit/runtime/fragment.py:742 调用 time_to_seconds(run_every)，
#                 与真实 traceback 的帧号完全一致；run_every="30s" 是字符串，必走该分支。
#     * pyarrow ← streamlit/components/v1/custom_component.py ← streamlit-calendar
#   正确性 > 安装包体积。若要重新优化，必须先有可复现的 render 证据。
excludes = [
    "altair", "matplotlib", "scipy",
    "PIL._avif",          # 只用于 AVIF 解码；本应用资源是 .ico/.png
    "tkinter", "IPython", "jupyter", "notebook", "nbformat",
    "pytest", "PyQt5", "PyQt6", "PySide2", "PySide6",
]

a = Analysis(  # noqa: F821
    [str(PROJECT_ROOT / "main.py")],
    pathex=[str(PROJECT_ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

# 敏感文件卫生（第 17 阶段）：streamlit_calendar 自带的前端 dev 配置
#   _internal/streamlit_calendar/frontend/.env（内容只有 PORT/BROWSER，npm 开发用）
# 运行时毫无用处，且安装器输入里禁止出现 .env（文档第 3、52 条）→ 从 datas 剔除。
a.datas = [  # noqa: F821
    d for d in a.datas  # noqa: F821
    if not str(d[0]).replace('\\', '/').endswith('frontend/.env')
]

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=BASE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=DEBUG,           # Release：无控制台；Debug：带控制台
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON) if ICON.is_file() else None,
    version=str(version_file),
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name=BASE_NAME,
)
