# -*- coding: utf-8 -*-
"""第 16 阶段打包体积回归测试（文档 BN 节）。

不硬编码「必须小于 123.45 MB」这种脆弱阈值，而是：

1. **禁止已确认无用的大依赖重新进入 dist**（这是真正会回归的东西）；
2. 给一个宽松的总体上限（明显超出优化后基线才失败）；
3. 在没有 dist（例如纯源码环境）时自动跳过，不阻塞开发。
"""
import json
import os
import sys
import unittest
from pathlib import Path

from tests import tests_env  # noqa: F401

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DIST = PROJECT_ROOT / "dist" / "TaiPlan"
INTERNAL = DIST / "_internal"

# --- 已证明的运行时硬依赖：必须存在，禁止再次被 excludes 掉 -------------------
# 依据（Stage 17.3 / W1 实测，不是猜测）：
#   * numpy  : streamlit/time_util.py:69 `import numpy as np`
#              （time_to_seconds 的 isinstance(t, str) 分支）
#   * pandas : streamlit/time_util.py:70 `import pandas as pd`（同一分支）
#   * 触发链 : app.py 的 @st.fragment(run_every="30s")
#              -> streamlit/runtime/fragment.py:742 time_to_seconds(run_every)
#              -> 与真实 Frozen traceback 的帧号完全一致
#   * pyarrow: streamlit/components/v1/custom_component.py:133 create_instance
#              -> Streamlit 组件序列化；page=calendar 时触发
#              （对应 app.log 的真实 traceback）
# 教训：曾在第 16 阶段按「import graph 没走到」把它们瘦身掉，结果 Frozen 版
# run_every="30s" 的 fragment 与 calendar 组件直接 ModuleNotFoundError。
REQUIRED_RUNTIME_PACKAGES = ("numpy", "numpy.libs", "pandas", "pyarrow")

# --- 防回归：当前没有任何运行时需求的重型包 --------------------------------
# 它们一旦重新出现在 dist 里，说明 spec 的 excludes 被改坏或有人加了 blanket collect。
# altair 按用户指示继续禁止：尚无真实 traceback 证明 Streamlit 需要它。
FORBIDDEN_PACKAGES = (
    "altair",        # 尚未证明需要（保留禁止，等真实 traceback）
    "matplotlib",    # 绘图库，本应用不用
    "scipy",         # 科学计算，本应用不用
    "torch",         # 深度学习
    "tensorflow",    # 深度学习
    "sklearn",       # 机器学习
    "cv2",           # 图像处理
    "jupyter",       # notebook 栈
    "notebook",
    "nbformat",
    "IPython",
    "PyQt5",         # 其它 GUI 栈
    "PyQt6",
    "PySide2",
    "PySide6",
    "tkinter",
)

# 宽松总上限：防「突然膨胀」，不精确卡死在当前值。
# 依据：2026-10-06 实测 dist（含 W1 恢复的 numpy+pandas+pyarrow）为
#       194.23 MB / 2059 files，其中 pyarrow 78.94 + numpy.libs 20.18 + pandas 12.37
#       + numpy 6.11 MB。留约 20% 余量到 230 MB：新增一个正常量级的依赖不会误报，
#       而多打包一整套重型栈（往往 +100 MB 以上）会立刻失败。
MAX_TOTAL_MB = 230


def dist_available():
    return INTERNAL.is_dir()


def dir_size(path):
    total = 0
    files = 0
    for p in Path(path).rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
                files += 1
            except OSError:
                pass
    return total, files


@unittest.skipUnless(dist_available(), "尚无 dist 目录，跳过体积检查")
class DistFootprintTest(unittest.TestCase):
    def test_no_forbidden_heavy_packages(self):
        offenders = []
        for name in FORBIDDEN_PACKAGES:
            path = INTERNAL / name
            if path.is_dir():
                size, files = dir_size(path)
                offenders.append(f"{name}: {size / 1024 / 1024:.1f} MB / {files} files")
            elif (INTERNAL / f"{name}.pyd").exists():
                offenders.append(f"{name}.pyd 存在")
        self.assertEqual(offenders, [],
                         "以下重依赖重新进入了 dist（检查 TaiPlan.spec 的 excludes）：\n  "
                         + "\n  ".join(offenders))

    def test_proven_runtime_dependencies_present(self):
        """W1 已证明的硬依赖必须真的打进 dist（防止再次被「瘦身」掉）。

        numpy/pandas/pyarrow 是 Streamlit 运行时硬依赖，见文件头注释里的真实 traceback
        与 streamlit 源码帧号。这个测试与 test_no_forbidden_heavy_packages 方向相反，
        共同锁住「该有的有、不该有的没有」。
        """
        missing = []
        for name in REQUIRED_RUNTIME_PACKAGES:
            path = INTERNAL / name
            if not path.is_dir() and not (INTERNAL / f"{name}.pyd").exists():
                missing.append(name)
        self.assertEqual(missing, [], f"dist 缺少已证明的运行时依赖: {missing}")
        # 真实 import 一次，证明不是只有目录壳子
        import importlib

        for mod in ("numpy", "pandas", "pyarrow"):
            try:
                importlib.import_module(mod)
            except ImportError as exc:  # pragma: no cover - 仅在构建环境缺失时发生
                self.fail(f"运行环境无法导入 {mod}: {exc}")

    def test_total_size_under_loose_cap(self):
        internal_bytes, files = dir_size(INTERNAL)
        exe = DIST / "TaiPlan.exe"
        total = internal_bytes + (exe.stat().st_size if exe.is_file() else 0)
        mb = total / 1024 / 1024
        self.assertLess(mb, MAX_TOTAL_MB,
                        f"dist 共 {mb:.1f} MB，超过宽松上限 {MAX_TOTAL_MB} MB")

    def test_required_runtime_pieces_still_present(self):
        """瘦身不能把真正要用的东西删掉。"""
        for rel in ("app.py", "assets/app_icon.ico", "tools/toast.ps1"):
            self.assertTrue((INTERNAL / rel).exists(), f"dist 缺少 {rel}")
        self.assertTrue((INTERNAL / "streamlit").is_dir(), "dist 缺少 streamlit")
        self.assertTrue((INTERNAL / "streamlit_calendar").is_dir(),
                        "dist 缺少 streamlit_calendar")
        self.assertTrue((INTERNAL / "webview" / "lib").is_dir(),
                        "dist 缺少 pywebview 原生 DLL")
        self.assertTrue((DIST / "TaiPlan.exe").is_file(), "缺少 TaiPlan.exe")

    def test_exe_has_no_console_subsystem(self):
        """Release EXE 必须是 GUI 子系统（Subsystem=2）。"""
        import struct

        exe = DIST / "TaiPlan.exe"
        if not exe.is_file():
            self.skipTest("exe 不存在")
        with open(exe, "rb") as f:
            data = f.read(0x400)
        off = struct.unpack_from("<I", data, 0x3C)[0]
        self.assertEqual(data[off:off + 4], b"PE\0\0", "不是有效 PE")
        subsystem = struct.unpack_from("<H", data, off + 0x5C)[0]
        self.assertEqual(subsystem, 2, f"Subsystem={subsystem}，Release 版应无控制台")

    def test_size_report_matches_reality(self):
        """analyze_dist 的产出（如果跑过）应与实际体积一致。"""
        report = PROJECT_ROOT / "benchmarks" / "dist_after.json"
        if not report.is_file():
            self.skipTest("还没跑过 analyze_dist.py")
        data = json.loads(report.read_text(encoding="utf-8"))
        internal_bytes, _ = dir_size(INTERNAL)
        self.assertAlmostEqual(
            data["internal_bytes"] / internal_bytes, 1.0, delta=0.05,
            msg="benchmarks/dist_after.json 与实际 dist 体积差异过大，请重跑 analyze_dist.py")


if __name__ == "__main__":
    unittest.main(verbosity=2)
