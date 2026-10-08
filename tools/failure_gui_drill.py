#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第 17.1 阶段：GUI 故障注入演练驱动（配合人工点击 Retry / Cancel）。

按文档第十一条：对话框允许人工点击，但**故障条件必须真实** —— 本脚本负责
把真实条件摆好（假实例占住端口、不响应 SHUTDOWN），你只需要按提示点按钮。
不 mock 任何 MsgBox。

用法（在项目根目录）：

    .venv\\Scripts\\python.exe tools\\failure_gui_drill.py install-cancel
    .venv\\Scripts\\python.exe tools\\failure_gui_drill.py install-retry
    .venv\\Scripts\\python.exe tools\\failure_gui_drill.py uninstall-cancel
    .venv\\Scripts\\python.exe tools\\failure_gui_drill.py uninstall-retry

四种演练：
  * install-cancel   : 起假实例 → 打开安装向导 → 出现「当前仍在运行」时点【取消】
  * install-retry    : 起假实例 → 打开安装向导 → 脚本在 60 秒后自动停掉假实例，
                       此时点【重试】→ 安装应继续
  * uninstall-cancel : 起假实例 → 打开卸载程序 → 点【取消】→ 应完整保留
  * uninstall-retry  : 起假实例 → 打开卸载程序 → 脚本在 60 秒后停掉假实例 →
                       点【重试】→ 卸载应继续

每个演练都会把安装/卸载日志写到 build_logs/drill_<动作>.log，结束后打印结论。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
FAKE = ROOT / "tools" / "fake_runtime_lock.py"
LOGS = ROOT / "build_logs"
FAKE_LOG = LOGS / "drill_fake_runtime.log"
FAKE_STOP = LOGS / "drill_fake.stop"

LOCAL = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
INSTALL_DIR = LOCAL / "Programs" / "TodoApp"
INSTALLED_EXE = INSTALL_DIR / "TaiPlan.exe"
UNINSTALLER = INSTALL_DIR / "unins000.exe"
SETUP = next(iter(sorted((ROOT / "release").glob("TaiPlan-Setup-*.exe"))), None)
DATA_DB = LOCAL / "TodoApp" / "todo.db"

ACTIONS = {
    "install-cancel": ("install", "cancel"),
    "install-retry": ("install", "retry"),
    "uninstall-cancel": ("uninstall", "cancel"),
    "uninstall-retry": ("uninstall", "retry"),
}


def file_count(root: Path) -> int:
    return sum(1 for p in root.rglob("*") if p.is_file()) if root.is_dir() else 0


def start_fake(seconds: int = 240):
    FAKE_STOP.unlink(missing_ok=True)
    proc = subprocess.Popen(
        [PY, "-X", "utf8", str(FAKE), "--seconds", str(seconds),
         "--log", str(FAKE_LOG), "--stop-file", str(FAKE_STOP)],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        time.sleep(0.5)
        if proc.poll() is not None:
            raise SystemExit("假实例启动失败（端口可能被真实 Todo 占用，请先退出 TaiPlan）")
        import socket
        try:
            with socket.create_connection(("127.0.0.1", 18722), timeout=1):
                return proc
        except OSError:
            continue
    raise SystemExit("假实例未能在 20 秒内占住 18722")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="17.1 GUI 故障注入演练")
    parser.add_argument("action", choices=sorted(ACTIONS))
    parser.add_argument("--release-after", type=int, default=60,
                        help="retry 演练中，多少秒后自动停掉假实例（默认 60）")
    args = parser.parse_args(argv)
    target, mode = ACTIONS[args.action]

    if not INSTALLED_EXE.is_file():
        raise SystemExit("安装版不存在，请先安装 release/TaiPlan-Setup-<version>.exe")
    if target == "uninstall" and not UNINSTALLER.is_file():
        raise SystemExit(f"找不到卸载器 {UNINSTALLER}")

    logs_before = file_count(INSTALL_DIR)
    db_size_before = DATA_DB.stat().st_size if DATA_DB.is_file() else 0
    log_file = LOGS / f"drill_{args.action}.log"
    log_file.unlink(missing_ok=True)

    print("=" * 72)
    print(f"演练：{args.action}（{target} / {mode}）")
    print("=" * 72)
    print(f"安装目录：{INSTALL_DIR}（{logs_before} 个文件）")
    fake = start_fake()
    print(f"假实例已占住 18721/18722（pid={fake.pid}）")

    program = SETUP if target == "install" else UNINSTALLER
    flags = [f"/LOG={log_file}"]
    if target == "install":
        flags += ["/TASKS=desktopicon"]      # 不加 /VERYSILENT：要走可视向导
    print(f"启动：{program.name} {' '.join(flags)}")
    proc = subprocess.Popen([str(program), *flags], cwd=str(program.parent))

    print()
    if mode == "cancel":
        print(">>> 请在对话框出现后点【取消】。预期：程序中止，不覆盖/不删除任何文件。")
    else:
        print(f">>> 对话框出现后先不要点。脚本将在 {args.release_after} 秒后停掉假实例，")
        print("    看到假实例停止后，请点【重试】。预期：安装/卸载继续。")
        time.sleep(args.release_after)
        FAKE_STOP.write_text("stop", encoding="utf-8")
        for _ in range(30):
            if fake.poll() is not None:
                break
            time.sleep(0.5)
        print(">>> 假实例已停止（端口已释放）。现在请点【重试】。")

    print(">>> 等待程序结束……（想在界面里改主意随时可以点另一个按钮）")
    rc = proc.wait(timeout=1800)
    time.sleep(2)

    logs_after = file_count(INSTALL_DIR)
    db_size_after = DATA_DB.stat().st_size if DATA_DB.is_file() else 0
    print()
    print("=" * 72)
    print(f"结果：{'安装' if target == 'install' else '卸载'}程序退出码 = {rc}")
    print(f"安装目录文件数：{logs_before} → {logs_after}")
    print(f"TaiPlan.exe 存在：{INSTALLED_EXE.is_file()}")
    print(f"用户数据 todo.db：{db_size_before} → {db_size_after} 字节")
    print(f"日志：{log_file}")
    if target == "uninstall":
        print(f"注册表卸载项仍在："
              f"{subprocess.run(['reg', 'query', 'HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{8E2F4C1B-7A3D-4E62-9B5F-2C7A1D0E4F83}_is1'], capture_output=True).returncode == 0}")
    print("=" * 72)
    if fake.poll() is None:
        FAKE_STOP.write_text("stop", encoding="utf-8")
        print("（假实例已一并停止）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
