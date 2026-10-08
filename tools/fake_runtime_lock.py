#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""假 Todo 实例（第 17.1 阶段故障注入用）。

它模拟"Todo 已经在运行"，但**故意不响应 SHUTDOWN**：

1. 占住单实例锁端口（18721）与 IPC 端口（18722），让新启动的 TaiPlan.exe
   认为"已有实例"；
2. 接受 IPC 连接、把收到的命令记进日志，然后**什么都不做**（不退出、不显示窗口）；
3. 默认存活 300 秒，收到 stop 文件（或 Ctrl+C）后优雅退出；
4. **绝不接触真实用户数据库**：本脚本只做 socket 操作。

用法：
    .venv\\Scripts\\python.exe tools\\fake_runtime_lock.py \\
        --seconds 300 --log build_logs\\fake_runtime.log --stop-file build_logs\\fake.stop

停止：
    新建 stop 文件（脚本会删除它并退出），或直接结束进程。
"""

from __future__ import annotations

import argparse
import socket
import sys
import threading
import time
from pathlib import Path

LOCK_PORT = 18721
IPC_PORT = 18722


class FakeRuntime:
    def __init__(self, log_path: Path | None, seconds: int, stop_file: Path | None,
                 lock_only: bool = False, ipc_only: bool = False):
        self.log_path = log_path
        self.seconds = seconds
        self.stop_file = stop_file
        self.lock_only = lock_only
        self.ipc_only = ipc_only
        self.stop_event = threading.Event()
        self.lock_sock = None
        self.ipc_sock = None
        self.received: list[bytes] = []
        self.connections = 0

    # ------------------------------------------------------------------ 日志
    def log(self, msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        print(line, flush=True)
        if self.log_path is not None:
            try:
                self.log_path.parent.mkdir(parents=True, exist_ok=True)
                with self.log_path.open("a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except OSError:
                pass

    # ------------------------------------------------------------------ 端口
    def bind(self) -> None:
        """按档位占用端口。

        --lock-only : 只占单实例锁（模拟"有实例但 IPC 不可用"，即残留实例）
        --ipc-only  : 只占 IPC 端口（模拟"IPC 被别的进程占用"，用于验证 Runtime 拒绝启动）
        默认        : 两个都占（模拟"实例在跑但不响应 SHUTDOWN"）
        """
        if not self.ipc_only:
            # 单实例锁端口：只要占着，新的 TaiPlan.exe 就会认为"已有实例"
            self.lock_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.lock_sock.bind(("127.0.0.1", LOCK_PORT))
            self.lock_sock.listen(1)
        if not self.lock_only:
            self.ipc_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.ipc_sock.bind(("127.0.0.1", IPC_PORT))
            self.ipc_sock.listen(5)

        occupied = []
        if self.lock_sock is not None:
            occupied.append(f"{LOCK_PORT}(lock)")
        if self.ipc_sock is not None:
            occupied.append(f"{IPC_PORT}(ipc)")
        self.log("假实例已占用 " + " 与 ".join(occupied))

    # ------------------------------------------------------------------ IPC
    def ipc_loop(self) -> None:
        if self.ipc_sock is None:
            return
        while not self.stop_event.is_set():
            try:
                self.ipc_sock.settimeout(0.5)
                conn, _ = self.ipc_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                data = conn.recv(64)
                self.connections += 1
                self.received.append(data)
                self.log(f"收到命令 {data!r} —— 故意不响应（不退出、不显示窗口）")
            except Exception as exc:  # noqa: BLE001
                self.log(f"读连接出错（忽略）：{exc!r}")
            finally:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass

    # ------------------------------------------------------------------ 生命周期
    def watch_stop_file(self) -> None:
        while not self.stop_event.is_set():
            if self.stop_file is not None and self.stop_file.exists():
                self.log("检测到 stop 文件 → 优雅退出")
                try:
                    self.stop_file.unlink()
                except OSError:
                    pass
                self.stop_event.set()
                return
            time.sleep(0.3)

    def close(self) -> None:
        for sock in (self.ipc_sock, self.lock_sock):
            try:
                if sock is not None:
                    sock.close()
            except OSError:
                pass
        self.log(f"已释放端口（共处理 {self.connections} 次 IPC 连接）")

    def run(self) -> int:
        if self.stop_file is not None and self.stop_file.exists():
            self.stop_file.unlink()
        self.bind()
        threading.Thread(target=self.ipc_loop, daemon=True).start()
        if self.stop_file is not None:
            threading.Thread(target=self.watch_stop_file, daemon=True).start()
        try:
            self.stop_event.wait(timeout=self.seconds)
        except KeyboardInterrupt:
            self.log("收到 Ctrl+C")
        self.close()
        return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="假 Todo 实例（不响应 SHUTDOWN）")
    parser.add_argument("--seconds", type=int, default=300, help="最长存活秒数（默认 300）")
    parser.add_argument("--log", default=None, help="日志文件路径")
    parser.add_argument("--stop-file", default=None, help="出现该文件即优雅退出")
    parser.add_argument("--lock-only", action="store_true",
                        help="只占单实例锁（模拟有实例但 IPC 不可用）")
    parser.add_argument("--ipc-only", action="store_true",
                        help="只占 IPC 端口（模拟 IPC 被占，验证 Runtime 拒绝启动）")
    args = parser.parse_args(argv)

    runtime = FakeRuntime(
        log_path=Path(args.log) if args.log else None,
        seconds=max(1, args.seconds),
        stop_file=Path(args.stop_file) if args.stop_file else None,
        lock_only=args.lock_only,
        ipc_only=args.ipc_only,
    )
    return runtime.run()


if __name__ == "__main__":
    raise SystemExit(main())
