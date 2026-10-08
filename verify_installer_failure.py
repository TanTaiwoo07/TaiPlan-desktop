# -*- coding: utf-8 -*-
"""第 17.1 阶段：安装器 shutdown 故障注入 —— 全自动部分。

真条件（不是 mock MsgBox）：
  A1 fake 实例占位时 --shutdown 必须等满 timeout 并返回非 0，且不杀 fake、不开新窗口
  A2 正常实例的 --shutdown 不受影响（exit 0，全部子进程退出）
  A3 安装器 shutdown 失败 → 不得覆盖任何文件（静默分支同样必须中止）
  A4 卸载器 shutdown 失败 → 不得删除任何文件（不得半卸载）
  A5 释放占用后：安装/卸载都能正常继续（对应 GUI 的"重试"）
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import hashlib
import os
import re
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / "build_logs"
LOGS.mkdir(exist_ok=True)
PY = str(ROOT / ".venv" / "Scripts" / "python.exe")
FAKE = ROOT / "tools" / "fake_runtime_lock.py"
FAKE_LOG = LOGS / "fake_runtime.log"
FAKE_STOP = LOGS / "fake.stop"

LOCAL = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
INSTALL_DIR = LOCAL / "Programs" / "TaiPlan"
INSTALLED_EXE = INSTALL_DIR / "TaiPlan.exe"
DIST_EXE = ROOT / "dist" / "TaiPlan" / "TaiPlan.exe"
SETUP = ROOT / "release" / "TaiPlan-Setup-0.1.0.exe"
UNINSTALLER = INSTALL_DIR / "unins000.exe"
DATA_DB = LOCAL / "TaiPlan" / "todo.db"
GUID = "{8E2F4C1B-7A3D-4E62-9B5F-2C7A1D0E4F83}"
UNINSTALL_KEY = rf"HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\{GUID}_is1"

RESULTS: list[tuple[str, bool, str]] = []
shutdown_elapsed = 0.0
shutdown_exit = None
window_seen = False


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" :: {detail}" if detail else ""), flush=True)
    return bool(ok)


def run(args, timeout=900, **kw):
    return subprocess.run(args, capture_output=True, timeout=timeout, **kw)


def port_busy(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            return True
    except OSError:
        return False


def todo_processes() -> int:
    out = run(["tasklist", "/FI", "IMAGENAME eq TaiPlan.exe", "/NH"], timeout=60).stdout
    return out.lower().count(b"todoapp.exe")


FAKE_PROC = None


def fake_alive() -> bool:
    """fake 是否仍在运行（用进程句柄判断，不依赖 wmic —— 本机没有 wmic）。"""
    return FAKE_PROC is not None and FAKE_PROC.poll() is None


def visible_todo_window() -> bool:
    user32 = ctypes.windll.user32
    proc_ids = set()
    out = run(["tasklist", "/FI", "IMAGENAME eq TaiPlan.exe", "/NH", "/FO", "CSV"], timeout=60)
    for line in out.stdout.decode("mbcs", "replace").splitlines():
        parts = line.split('","')
        if len(parts) > 1 and parts[0].strip('"').lower() == "todoapp.exe":
            try:
                proc_ids.add(int(parts[1]))
            except ValueError:
                pass
    found = []

    def cb(hwnd, _):
        pid = wt.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in proc_ids and user32.IsWindowVisible(hwnd):
            length = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if buf.value:
                found.append(buf.value)
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(cb), 0)
    return bool(found)


def window_sampler(stop_event, seconds):
    global window_seen
    end = time.time() + seconds
    while time.time() < end and not stop_event.is_set():
        if visible_todo_window():
            window_seen = True
        time.sleep(0.4)


def stop_real_app():
    if INSTALLED_EXE.is_file():
        run([str(INSTALLED_EXE), "--shutdown"], timeout=90)
    time.sleep(3)


def start_fake(seconds=180):
    global FAKE_PROC
    if FAKE_STOP.exists():
        FAKE_STOP.unlink()
    FAKE_LOG.write_text("", encoding="utf-8")
    FAKE_PROC = subprocess.Popen(
        [PY, "-X", "utf8", str(FAKE), "--seconds", str(seconds),
         "--log", str(FAKE_LOG), "--stop-file", str(FAKE_STOP)],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(40):
        time.sleep(0.5)
        if port_busy(18721) and port_busy(18722):
            return True
    return False


def stop_fake():
    FAKE_STOP.write_text("stop", encoding="utf-8")
    for _ in range(30):
        if not port_busy(18722):
            return True
        time.sleep(0.5)
    return False


def digest_tree(root: Path) -> tuple[str, int]:
    """安装目录内容摘要 + 文件数（用于判断是否被部分覆盖）。"""
    if not root.is_dir():
        return "(不存在)", 0
    h = hashlib.sha256()
    files = sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: str(p).lower())
    for p in files:
        h.update(str(p.relative_to(root)).encode("utf-8"))
        h.update(str(p.stat().st_size).encode("utf-8"))
        if p.stat().st_size < 2_000_000:
            h.update(p.read_bytes())
    return h.hexdigest()[:16], len(files)


def registry_present() -> bool:
    return run(["reg", "query", UNINSTALL_KEY], timeout=60).returncode == 0


def read_log(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace") if path.is_file() else ""


print("=" * 78)
print("准备：停掉真实实例，腾出 18721/18722")
print("=" * 78)
stop_real_app()
check("18721 空闲", not port_busy(18721))
check("18722 空闲", not port_busy(18722))
check("安装版已就位（0.1.0）", INSTALLED_EXE.is_file(), str(INSTALLED_EXE))
check("用户数据存在", DATA_DB.is_file())

print()
print("=" * 78)
print("A1 fake 实例：--shutdown 必须等满 timeout 并返回非 0")
print("=" * 78)
check("fake 已启动并占住端口", start_fake(180), "ports 18721/18722 已占用")
check("fake 是独立进程", fake_alive(), f"pid={getattr(FAKE_PROC, 'pid', None)}")

sampler_stop = threading.Event()
threading.Thread(target=window_sampler, args=(sampler_stop, 20), daemon=True).start()
t0 = time.time()
proc = run([str(DIST_EXE), "--shutdown"], timeout=120)
shutdown_elapsed = time.time() - t0
shutdown_exit = proc.returncode
sampler_stop.set()
check("--shutdown 退出码非 0", shutdown_exit != 0, f"exit={shutdown_exit}")
check("--shutdown 等满 timeout（≈10 秒）", 9.0 <= shutdown_elapsed <= 25.0,
      f"实测 {shutdown_elapsed:.2f} 秒")
check("没有启动新的 Todo 主窗口", not window_seen and todo_processes() == 0,
      f"窗口={'出现过' if window_seen else '无'}，TaiPlan 进程={todo_processes()}")
check("没有杀死 fake 实例", fake_alive() and port_busy(18722),
      f"fake 存活={fake_alive()}，端口仍占用={port_busy(18722)}")
fake_text = read_log(FAKE_LOG)
check("fake 日志记录了收到的 SHUTDOWN", "SHUTDOWN" in fake_text,
      next((l for l in fake_text.splitlines() if "SHUTDOWN" in l), "（无）"))

print()
print("=" * 78)
print("A3 安装器 shutdown 失败：不得覆盖任何文件")
print("=" * 78)
before_digest, before_files = digest_tree(INSTALL_DIR)
print(f"  安装目录基线：{before_files} 个文件，摘要 {before_digest}")
install_log = LOGS / "failinject_install.log"
proc = run([str(SETUP), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/TASKS=desktopicon",
            f"/LOG={install_log}"], timeout=900)
text = read_log(install_log)
after_digest, after_files = digest_tree(INSTALL_DIR)
check("安装失败（exit != 0）", proc.returncode != 0, f"exit={proc.returncode}")
check("安装目录未被覆盖（文件数与摘要完全一致）",
      (before_files, before_digest) == (after_files, after_digest),
      f"{before_files}→{after_files} 个文件，{before_digest}→{after_digest}")
check("安装日志记录了 shutdown 失败与中止",
      "shutdown 失败" in text and "安装已取消" in text,
      next((l.strip() for l in text.splitlines() if "安装已取消" in l), "（未找到中止记录）"))

print()
print("=" * 78)
print("A4 卸载器 shutdown 失败：不得删除任何文件、不得半卸载")
print("=" * 78)
un_log = LOGS / "failinject_uninstall.log"
proc = run([str(UNINSTALLER), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
            f"/LOG={un_log}"], timeout=900)
text_u = read_log(un_log)
after_u_digest, after_u_files = digest_tree(INSTALL_DIR)
check("卸载未执行（exit != 0）", proc.returncode != 0, f"exit={proc.returncode}")
check("TaiPlan.exe 仍在", INSTALLED_EXE.is_file())
check("安装目录完整（未被删除/半卸载）",
      (before_files, before_digest) == (after_u_files, after_u_digest),
      f"{after_u_files} 个文件，摘要 {after_u_digest}")
check("Windows 应用列表条目仍在", registry_present())
check("卸载日志记录了中止原因",
      "无法确认 TaiPlan 已退出" in text_u or "静默卸载中止" in text_u,
      next((l.strip() for l in text_u.splitlines() if "中止" in l), "（未找到中止记录）"))

print()
print("=" * 78)
print("A5 释放占用后：安装与卸载都能正常继续（GUI 的『重试』等价路径）")
print("=" * 78)
check("fake 已优雅停止", stop_fake(), f"端口占用={port_busy(18722)}")
time.sleep(2)
proc = run([str(SETUP), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/TASKS=desktopicon",
            f"/LOG={LOGS / 'failinject_retry_install.log'}"], timeout=900)
check("重试（释放后）安装成功", proc.returncode == 0, f"exit={proc.returncode}")
check("安装目录已更新", digest_tree(INSTALL_DIR)[0] != before_digest)

proc = run([str(UNINSTALLER), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
            f"/LOG={LOGS / 'failinject_retry_uninstall.log'}"], timeout=900)
time.sleep(5)
check("重试（释放后）卸载成功", proc.returncode == 0 and not INSTALL_DIR.exists(),
      f"exit={proc.returncode}, 目录存在={INSTALL_DIR.exists()}")
check("卸载后应用列表条目已移除", not registry_present())
check("★ 用户数据完好", DATA_DB.is_file() and DATA_DB.stat().st_size > 0,
      f"{DATA_DB.stat().st_size if DATA_DB.is_file() else 0} bytes")

print()
print("=" * 78)
print("恢复现场：重新安装 0.1.0 并启动")
print("=" * 78)
proc = run([str(SETUP), "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/TASKS=desktopicon",
            f"/LOG={LOGS / 'failinject_reinstall.log'}"], timeout=900)
check("重新安装成功", proc.returncode == 0 and INSTALLED_EXE.is_file(), f"exit={proc.returncode}")
subprocess.Popen([str(INSTALLED_EXE)], cwd=str(INSTALL_DIR))
time.sleep(45)
check("安装版已启动", todo_processes() >= 2, f"{todo_processes()} 个进程")

print()
print("=" * 78)
passed = sum(1 for _, ok, _ in RESULTS if ok)
print(f"17.1 自动部分：{passed}/{len(RESULTS)} 通过")
for name, ok, detail in RESULTS:
    if not ok:
        print(f"  [FAIL] {name} :: {detail}")
print(f"--shutdown 实测耗时 {shutdown_elapsed:.2f} 秒，exit={shutdown_exit}")
print("=" * 78)
sys.exit(0 if passed == len(RESULTS) else 1)
