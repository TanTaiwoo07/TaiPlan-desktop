"""分析 dist/TaiPlan/_internal 的体积构成（第 16 阶段 AG 节）。

用法::

    .venv\\Scripts\\python.exe analyze_dist.py
    .venv\\Scripts\\python.exe analyze_dist.py --json benchmarks/dist_size.json
"""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_DIST = PROJECT_ROOT / "dist" / "TaiPlan"


def _human(mb):
    return f"{mb:8.2f} MB"


def scan(dist_dir):
    dist_dir = Path(dist_dir)
    internal = dist_dir / "_internal"
    root = internal if internal.is_dir() else dist_dir
    if not root.is_dir():
        raise SystemExit(f"找不到 dist 目录: {dist_dir}")

    pkg = defaultdict(lambda: {"size": 0, "files": 0})
    dll, pyd, dirs = [], [], defaultdict(lambda: {"size": 0, "files": 0})
    total = 0
    nfiles = 0

    for p in root.rglob("*"):
        if not p.is_file():
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        total += size
        nfiles += 1

        rel = p.relative_to(root)
        parts = rel.parts
        top = parts[0] if len(parts) > 1 else "(顶层文件)"
        pkg[top]["size"] += size
        pkg[top]["files"] += 1
        dirs[str(rel.parent)]["size"] += size
        dirs[str(rel.parent)]["files"] += 1

        low = p.name.lower()
        if low.endswith(".dll"):
            dll.append((size, str(rel)))
        elif low.endswith(".pyd"):
            pyd.append((size, str(rel)))

    exe = dist_dir / "TaiPlan.exe"
    exe_size = exe.stat().st_size if exe.is_file() else 0
    return {
        # 相对仓库路径：快照会进版本库，绝不写入本机绝对路径
        "dist": dist_dir.relative_to(PROJECT_ROOT).as_posix()
        if dist_dir.is_relative_to(PROJECT_ROOT) else dist_dir.name,
        "internal": root.relative_to(PROJECT_ROOT).as_posix()
        if root.is_relative_to(PROJECT_ROOT) else root.name,
        "exe_bytes": exe_size,
        "internal_bytes": total,
        "total_bytes": total + exe_size,
        "file_count": nfiles + (1 if exe_size else 0),
        "packages": sorted(((v["size"], k, v["files"]) for k, v in pkg.items()),
                           reverse=True),
        "dirs": sorted(((v["size"], k, v["files"]) for k, v in dirs.items()), reverse=True),
        "dll": sorted(dll, reverse=True),
        "pyd": sorted(pyd, reverse=True),
    }


def report(data, top=15):
    mb = 1024 * 1024
    print("=" * 74)
    print(f"dist: {data['dist']}")
    print(f"exe : {data['exe_bytes'] / mb:.2f} MB")
    print(f"内部: {data['internal_bytes'] / mb:.2f} MB   文件数(不含 exe): {data['file_count'] - 1}")
    print(f"合计: {data['total_bytes'] / mb:.2f} MB   文件数: {data['file_count']}")
    print("=" * 74)

    print(f"\n--- 最大的 {top} 个顶层组件（package / 目录）---")
    for size, name, files in data["packages"][:top]:
        pct = 100.0 * size / max(1, data["internal_bytes"])
        print(f"  {_human(size / mb)}  {pct:5.1f}%  {files:6d} files  {name}")

    print(f"\n--- 最大的 {top} 个目录 ---")
    for size, name, files in data["dirs"][:top]:
        print(f"  {_human(size / mb)}  {files:6d} files  {name[:58]}")

    print(f"\n--- 最大的 {top} 个 DLL ---")
    for size, name in data["dll"][:top]:
        print(f"  {_human(size / mb)}  {name}")

    print(f"\n--- 最大的 {top} 个 .pyd ---")
    for size, name in data["pyd"][:top]:
        print(f"  {_human(size / mb)}  {name}")


def main(argv=None):
    ap = argparse.ArgumentParser(description="分析 dist 体积构成")
    ap.add_argument("--dist", default=str(DEFAULT_DIST))
    ap.add_argument("--json", default=None, help="把结果写到指定 json")
    ap.add_argument("--top", type=int, default=15)
    args = ap.parse_args(argv)

    data = scan(args.dist)
    report(data, top=args.top)
    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        payload = dict(data)
        payload["packages"] = [{"name": n, "bytes": s, "files": f}
                               for s, n, f in data["packages"]]
        payload["dirs"] = [{"name": n, "bytes": s, "files": f} for s, n, f in data["dirs"]]
        payload["dll"] = [{"name": n, "bytes": s} for s, n in data["dll"]]
        payload["pyd"] = [{"name": n, "bytes": s} for s, n in data["pyd"]]
        payload.pop("dll", None)
        payload["dll"] = [{"name": n, "bytes": s} for s, n in data["dll"]]
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"\n已写出: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
