"""性能基准（第 16 阶段 BO / BP 节）。

用法::

    .venv\\Scripts\\python.exe benchmark.py                 # 默认 7 次采样
    .venv\\Scripts\\python.exe benchmark.py --samples 11
    .venv\\Scripts\\python.exe benchmark.py --json benchmarks/latest.json

    dist\\TaiPlan\\TaiPlan.exe --benchmark                  # frozen 模式（不开 GUI）

**只使用临时数据库**（TODO_APP_DATA_DIR / TODO_APP_LEGACY_DIR 指向临时目录），
绝不触碰真实用户数据。测的是 Python 侧耗时（取数 / 分类 / 投影 / 转换），
GUI 感知延迟仍需人工测。
"""
from __future__ import annotations

import argparse
import contextlib
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_SAMPLES = 7
DEFAULT_JSON = PROJECT_ROOT / "benchmarks" / "latest.json"


# ----------------------------------------------------------------------
# 环境准备（必须在 import 项目模块之前设置数据目录）
# ----------------------------------------------------------------------

def prepare_isolated_env():
    tmp = tempfile.mkdtemp(prefix="todo_bench_")
    os.environ["TODO_APP_DATA_DIR"] = tmp
    os.environ["TODO_APP_LEGACY_DIR"] = os.path.join(tmp, "legacy")
    return tmp


@contextlib.contextmanager
def count_sql():
    """用 SQLite 的 trace callback 统计一次操作里执行了多少条 SQL。"""
    import database

    counter = {"n": 0}
    original = database.get_connection

    def patched():
        conn = original()
        try:
            conn.set_trace_callback(lambda _s: counter.__setitem__("n", counter["n"] + 1))
        except Exception:  # noqa: BLE001
            pass
        return conn

    database.get_connection = patched
    try:
        yield counter
    finally:
        database.get_connection = original


def seed_database(task_count=120, recurring_count=12):
    """造一份有代表性的数据：普通任务 + 重复任务 + 若干历史完成。"""
    import services
    from datetime import date, timedelta

    today = date.today()
    # 注意：services.create_task() 不返回任务对象，只能按标题回查 id
    to_complete = []
    for i in range(task_count - recurring_count):
        offset = (i % 21) - 10          # 前后各 10 天铺开
        d = today + timedelta(days=offset)
        hour = 8 + (i % 12)
        title = f"基准任务 {i:03d}"
        services.create_task(
            title,
            date=d.isoformat(),
            time=f"{hour:02d}:{(i % 4) * 15:02d}",
            duration_minutes=30 + (i % 4) * 15,
            priority=("urgent" if i % 7 == 0 else "normal"),
        )
        if i % 5 == 0:
            to_complete.append(title)

    for i in range(recurring_count):
        services.create_task(
            f"基准重复 {i:02d}",
            date=(today - timedelta(days=i)).isoformat(),
            time=f"{9 + (i % 6):02d}:00",
            duration_minutes=45,
            is_recurring=True,
            recurrence_frequency="daily",
            recurrence_interval=1,
        )

    id_by_title = {t["title"]: t["id"] for t in services.get_tasks()}
    for title in to_complete:
        tid = id_by_title.get(title)
        if tid is not None:
            services.toggle_task(tid, True)


# ----------------------------------------------------------------------
# 被测操作
# ----------------------------------------------------------------------

def build_operations():
    import calendar_adapter
    import recurrence
    import services
    from datetime import date, timedelta

    today = date.today()
    month_start = today.replace(day=1)
    month_end = (month_start + timedelta(days=31)).replace(day=1)
    week_start = today - timedelta(days=today.weekday())
    week_end = week_start + timedelta(days=6)

    recurring = [t for t in services.get_tasks() if t["is_recurring"]]
    master = recurring[0] if recurring else None

    ops = []

    def op(name, fn, count_sql_too=True):
        ops.append((name, fn, count_sql_too))

    op("page_today_fetch", lambda: (services.get_today_items(),
                                    services.get_overdue_tasks()))
    op("page_inbox_fetch", lambda: services.get_inbox_tasks())
    op("page_completed_fetch", lambda: services.get_completed_items())
    op("page_upcoming_fetch", lambda: services.get_upcoming_items())
    op("page_month_items", lambda: services.get_items_for_month(today.year, today.month))
    op("page_week_items", lambda: services.get_items_for_range(
        week_start.isoformat(), week_end.isoformat()))

    if master is not None:
        op("recurrence_project_month",
           lambda: recurrence.get_occurrences_for_range(
               master, month_start.isoformat(), month_end.isoformat()))
        op("calendar_events_month", lambda: [
            calendar_adapter.to_event(i) for i in
            services.get_items_for_range(month_start.isoformat(), month_end.isoformat())
        ] if hasattr(calendar_adapter, "to_event") else
            services.get_items_for_range(month_start.isoformat(), month_end.isoformat()))

    def _create():
        services.create_task("基准临时任务", date=today.isoformat(), time="07:30")

    op("create_task", _create)

    def _complete():
        items = services.get_today_items()
        if items:
            services.toggle_task(items[0].task_id, True)

    op("complete_task", _complete)

    def _edit():
        items = services.get_today_items()
        if items:
            services.edit_task(items[0].task_id, "基准改后标题")

    op("edit_task", _edit)

    return ops


def run(samples=DEFAULT_SAMPLES, out_path=None):
    tmp = prepare_isolated_env()
    import database
    import performance

    database.init_database()
    t0 = time.perf_counter()
    seed_database()
    seed_s = time.perf_counter() - t0

    ops = build_operations()
    results = []
    print("=" * 74)
    print(f"TaiPlan 性能基准（临时数据目录: {tmp}）")
    print(f"造数据耗时: {seed_s:.2f}s  任务数: {len(__import__('services').get_tasks())}")
    print("=" * 74)
    print(f"{'operation':<26}{'median':>10}{'p95':>10}{'min':>10}{'sql':>8}  n")
    print("-" * 74)

    for name, fn, sql_too in ops:
        times = []
        sql_counts = []
        for i in range(samples):
            with count_sql() as counter:
                t0 = time.perf_counter()
                try:
                    fn()
                except Exception as exc:  # noqa: BLE001
                    print(f"  !! {name} 执行失败: {type(exc).__name__}: {exc}")
                    break
                times.append((time.perf_counter() - t0) * 1000.0)
            if sql_too:
                sql_counts.append(counter["n"] if "counter" in dir() else 0)
        if not times:
            continue
        med = statistics.median(times)
        p95 = sorted(times)[max(0, int(round(0.95 * (len(times) - 1))))]
        entry = {
            "operation": name,
            "median_ms": round(med, 2),
            "p95_ms": round(p95, 2),
            "min_ms": round(min(times), 2),
            "sample_count": len(times),
            "sql_median": int(statistics.median(sql_counts)) if sql_counts else None,
        }
        results.append(entry)
        print(f"{name:<26}{med:>9.2f}ms{p95:>9.2f}ms{min(times):>9.2f}ms"
              f"{(entry['sql_median'] if entry['sql_median'] is not None else '-'):>8}  {len(times)}")

    payload = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "frozen": bool(getattr(sys, "frozen", False)),
        "samples": samples,
        "seed_seconds": round(seed_s, 2),
        "operations": results,
    }
    out = Path(out_path or DEFAULT_JSON)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print("-" * 74)
    print(f"已写出: {out}")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="TaiPlan 性能基准（临时数据库）")
    ap.add_argument("--samples", type=int, default=DEFAULT_SAMPLES)
    ap.add_argument("--json", default=None)
    args = ap.parse_args(argv)
    return run(samples=max(1, args.samples), out_path=args.json)


if __name__ == "__main__":
    raise SystemExit(main())
