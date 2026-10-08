"""轻量性能计时工具（第 16 阶段 B 节）。

设计约束
--------
- **不写磁盘**：只用一个内存 ring buffer，避免"为了量性能反而拖慢"。
- **开销极小**：只做 ``perf_counter`` 差值与一次 deque append。
- **Release 下也记录**（微秒级开销），但只有 Developer Mode 才把结果显示出来。

用法::

    from performance import perf_timer, start_run, get_perf_snapshot

    start_run()                      # 每次 rerun 开头调用一次
    with perf_timer("database_queries"):
        items = services.get_today_items()
    snack = get_perf_snapshot()      # 最近一次 + 平均 + P95
"""

from __future__ import annotations

import time
from collections import deque

MAX_RECORDS = 400          # ring buffer 容量（够看最近几十次 rerun）
MAX_STAGES_PER_RUN = 60    # 单次 rerun 内最多记多少个阶段

_records = deque(maxlen=MAX_RECORDS)   # [{"run": int, "name": str, "ms": float}]
_current_run = 0
_current_start = 0.0


def start_run() -> int:
    """标记一次新的 rerun 开始。返回 run 序号。"""
    global _current_run, _current_start
    _current_run += 1
    _current_start = time.perf_counter()
    return _current_run


def current_run_id() -> int:
    return _current_run


def record_timing(name, ms, run_id=None):
    """记录一个阶段的耗时（毫秒）。"""
    try:
        _records.append({
            "run": int(_current_run if run_id is None else run_id),
            "name": str(name),
            "ms": round(float(ms), 3),
        })
    except Exception:  # noqa: BLE001  性能工具绝不能拖垮主流程
        pass


class perf_timer:
    """上下文管理器 / 装饰器两用。

        with perf_timer("page_fetch"):
            ...

        @perf_timer("calendar_event_build")
        def build(...): ...
    """

    def __init__(self, name):
        self.name = name
        self._t0 = None

    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._t0 is not None:
            record_timing(self.name, (time.perf_counter() - self._t0) * 1000.0)
        return False

    def __call__(self, func):
        name = self.name

        def _wrapped(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                record_timing(name, (time.perf_counter() - t0) * 1000.0)

        _wrapped.__name__ = getattr(func, "__name__", "wrapped")
        _wrapped.__doc__ = getattr(func, "__doc__", None)
        return _wrapped


def finish_run():
    """本次 rerun 结束：把总时长记为 app_total。"""
    if _current_start:
        record_timing("app_total", (time.perf_counter() - _current_start) * 1000.0)


def run_total_ms(run_id=None):
    """取某次 rerun 的 app_total。"""
    rid = _current_run if run_id is None else run_id
    for rec in reversed(_records):
        if rec["run"] == rid and rec["name"] == "app_total":
            return rec["ms"]
    return None


def _percentile(values, pct):
    if not values:
        return 0.0
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, int(round((pct / 100.0) * (len(ordered) - 1)))))
    return ordered[k]


def get_perf_snapshot(last_n=20):
    """返回最近 last_n 次 rerun 的汇总。

    {
      "runs": 20,
      "last_run": {阶段名: ms},
      "stages": {阶段名: {"last": ms, "avg": ms, "p95": ms, "count": n}},
      "app_total": {"last": ms, "avg": ms, "p95": ms},
      "sql_queries": {"last": n, "avg": .., "p95": ..},
    }
    """
    runs = max(1, int(last_n))
    run_ids = []
    for rec in reversed(_records):
        if rec["run"] not in run_ids:
            run_ids.append(rec["run"])
        if len(run_ids) >= runs:
            break

    stages = {}
    last_run = {}
    for rec in _records:
        if rec["run"] not in run_ids:
            continue
        cur = stages.setdefault(rec["name"], [])
        cur.append(rec["ms"])
        if rec["run"] == _current_run:
            last_run[rec["name"]] = rec["ms"]

    summary = {}
    for name, values in stages.items():
        summary[name] = {
            "last": values[-1],
            "avg": round(sum(values) / len(values), 2),
            "p95": round(_percentile(values, 95), 2),
            "count": len(values),
        }

    out = {
        "runs": len(run_ids),
        "last_run": last_run,
        "stages": summary,
        "app_total": summary.get("app_total", {}),
    }
    out["sql_queries"] = summary.get("sql_queries", {})
    return out


def format_perf_lines(snapshot=None, keys=None):
    """给 Developer Mode 面板用的可读文本行。"""
    snap = snapshot or get_perf_snapshot()
    order = keys or ["app_total", "database_queries", "page_fetch", "classification",
                     "recurrence_projection", "calendar_event_build", "task_list_render",
                     "settings_render", "sidebar", "sql_queries"]
    lines = []
    for key in order:
        info = snap["stages"].get(key)
        if not info:
            continue
        unit = " 条" if key == "sql_queries" else " ms"
        lines.append(
            f"{key}: 最近 {info['last']:.1f}{unit} · "
            f"平均 {info['avg']:.1f}{unit} · P95 {info['p95']:.1f}{unit}"
            f"（{info['count']} 次）"
        )
    for key, info in snap["stages"].items():
        if key in order:
            continue
        unit = " 条" if key == "sql_queries" else " ms"
        lines.append(f"{key}: 最近 {info['last']:.1f}{unit} · 平均 {info['avg']:.1f}{unit}")
    return lines


def reset():
    _records.clear()
    global _current_run, _current_start
    _current_run = 0
    _current_start = 0.0


def record_count(name, count, run_id=None):
    """记录一个计数量（例如 SQL 语句条数）。"""
    record_timing(name, count, run_id=run_id)
