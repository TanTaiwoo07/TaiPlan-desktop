#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第 17 阶段：一键 Release 构建（可选，文档第 49 条）。

    .venv\\Scripts\\python.exe build_release.py

流程：

    PyInstaller build  →  Smoke Test  →  Security Scan  →  Installer Build  →  SHA256

如果 dist/ 已经是刚构建好的稳定产物，也可以只跑：

    .venv\\Scripts\\python.exe build_installer.py
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable


def _step(title: str, args: list[str]) -> None:
    print(f"\n{'=' * 72}\n>>> {title}\n{'=' * 72}", flush=True)
    start = time.time()
    proc = subprocess.run([PY, "-X", "utf8", *args], cwd=str(ROOT))
    if proc.returncode != 0:
        raise SystemExit(f"步骤失败（exit {proc.returncode}）：{' '.join(args)}")
    print(f"<<< {title} 完成，用时 {time.time() - start:.1f}s", flush=True)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    started = time.time()

    _step("1/3 PyInstaller 构建 dist/TaiPlan（含冒烟自检与敏感扫描）", ["build.py"])
    _step("2/3 生成第三方许可证清单", ["third_party_notices.py"])
    _step("3/3 构建 Windows 安装包（内部会再做一次冒烟自检 + 敏感扫描 + SHA256）",
          ["build_installer.py", *argv])

    print(f"\n全部完成，总用时 {time.time() - started:.1f}s")
    print("release 目录：")
    for path in sorted((ROOT / "release").glob("TaiPlan-Setup-*")):
        print(f"  {path}  ({path.stat().st_size / 1024 / 1024:.2f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
