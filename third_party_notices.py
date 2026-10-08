#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""生成 THIRD_PARTY_NOTICES.txt（RC 版：以**实际分发内容**为事实来源）。

生成规则
--------
1. **事实来源 = `dist/TaiPlan/_internal` 的顶层内容** —— 也就是真正打进 bundle、
   会随安装包分发给最终用户的东西。而不是 requirements.txt：那里混着只在开发/构建
   阶段用到的包（pytest 等），把它当 runtime 依赖会虚增清单。
2. 每个顶层包名经 `importlib.metadata.packages_distributions()` 映射到发行版
   （distribution），再读取该发行版 metadata 里的版本与许可证 —— **不猜许可证**。
3. 只列"确实在 bundle 里"的发行版。**纯构建依赖**（PyInstaller、pyinstaller-hooks-contrib）
   不进依赖清单（其引导程序条款另在文末说明）。
4. Python 标准库与打包器内部件（`base_library.zip`、`python3xx.dll`、`encodings/`
   等）不算第三方组件，单独在文末说明（Python 本体许可证）。
5. 映射不到发行版的目录（例如上游 vendored 代码）**不会被静默丢掉**：集中列在
   "未解析"一节里，方便人工复核，保证"不漏也不多"。

用法：
    .venv\\Scripts\\python.exe third_party_notices.py
    .venv\\Scripts\\python.exe third_party_notices.py --dist dist\\TaiPlan --out X.txt
"""

from __future__ import annotations

import argparse
import ast
import sys
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_DIST = ROOT / "dist" / "TaiPlan"
DEFAULT_OUT = ROOT / "docs" / "THIRD_PARTY_NOTICES.txt"

# 纯构建期依赖：**只有它们并不同时出现在 bundle 里**时才排除。
# 注意 setuptools 不在其中：PyInstaller 会把 pkg_resources 打进 onedir，
# 那就是实际分发的内容，必须列入（否则属于"遗漏传递依赖"）。
BUILD_ONLY = {"pyinstaller", "pyinstaller-hooks-contrib", "pytest", "pip", "wheel"}

# 项目自有资源目录（不是第三方组件）
OWN_RESOURCE_DIRS = {"assets", "tools"}

# 打包器内部件（不是第三方发行版）
PACKAGER_INTERNAL_PREFIXES = ("base_library.zip", "python3", "_internal", "api-ms-", "vcruntime")

HEADER = """THIRD PARTY NOTICES
===================

TaiPlan 分发了以下第三方组件。本清单以 **dist/TaiPlan/_internal 的实际内容**为准
（即真正打进安装包、随程序分发的东西），版本与许可证全部读取自本机这些发行版的
metadata（importlib.metadata / *.dist-info/METADATA），未做任何推测。

生成规则：
  1. 顶层内容取自 dist/TaiPlan/_internal（真实 bundle 事实）；
  2. 顶层包名经 importlib.metadata.packages_distributions() 映射到发行版；
  3. 只列确实在 bundle 里的发行版；纯构建依赖（PyInstaller 等）不列为运行时依赖；
  4. Python 标准库与打包器内部件不算第三方组件（见文末说明）；
  5. 映射不到发行版的内容集中列在"未解析"一节，不静默丢弃。

若与上游项目声明不一致，以上游为准。

"""

FOOTER = """
------------------------------------------------------------
Python 运行时
------------------------------------------------------------
本程序内嵌 Python 运行时。Python 由 Python Software Foundation 提供，
许可证为 PSF License Agreement。
https://docs.python.org/3/license.html

------------------------------------------------------------
打包工具（非运行时依赖）
------------------------------------------------------------
本程序使用 PyInstaller 打包。PyInstaller 的引导程序（bootloader）按 GPL 2.0 提供，
并附有允许分发打包产物的例外条款（Bootloader Exception）。
PyInstaller 本身不随安装包作为可导入的库分发，因此不计入上面的依赖清单。
https://pyinstaller.org/en/stable/license.html

完整的分发依赖清单亦见安装目录下的 _internal 文件夹中各 *.dist-info/METADATA。
"""


def pyz_top_levels(toc: Path) -> list[str]:
    """从 PyInstaller 的 PYZ-00.toc 读出冻进归档的**纯 Python** 模块顶层名。

    只扫 _internal 是不够的：keyring / pystray / winotify 这类纯 Python 包会被
    冻进 exe 内部的 PYZ，不会以目录形式出现，漏掉就等于漏掉它们的许可证。
    """
    try:
        data = ast.literal_eval(toc.read_text(encoding="utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return []
    if not isinstance(data, (list, tuple)) or len(data) < 2:
        return []
    entries = data[1]
    names = set()
    for item in entries or []:
        try:
            name = str(item[0])
        except (TypeError, IndexError):
            continue
        top = name.split(".")[0].strip()
        if top:
            names.add(top)
    return sorted(names)


def bundle_top_levels(dist_dir: Path, build_dir: Path | None = None):
    """返回 (顶层名, 被跳过的内部件名, 数据来源说明)。

    事实来源 = PYZ 归档里的模块（纯 Python） ∪ _internal 顶层目录（C 扩展 / 数据）。
    """
    internal = dist_dir / "_internal"
    if not internal.is_dir():
        raise FileNotFoundError(
            f"找不到 {internal}。请先构建 dist（.venv\\Scripts\\python.exe build.py），"
            "或显式传入 --dist 指向 onedir 产物目录。"
        )
    build_dir = build_dir or (ROOT / "build" / dist_dir.name)
    toc = build_dir / "PYZ-00.toc"

    candidates: set[str] = set()
    skipped: list[str] = []
    for entry in sorted(internal.iterdir(), key=lambda q: q.name.lower()):
        if not entry.is_dir():
            name = entry.name
            if name.startswith(PACKAGER_INTERNAL_PREFIXES) or name.endswith(".dll"):
                skipped.append(name)
            continue
        name = entry.name
        if name.endswith(".dist-info") or name.endswith(".data"):
            skipped.append(name)
            continue
        candidates.add(name)

    pyz_names = pyz_top_levels(toc)
    candidates |= set(pyz_names)

    packages = []
    for name in sorted(candidates, key=str.lower):
        if name in getattr(sys, "stdlib_module_names", frozenset()):
            skipped.append(name)
            continue
        if name.startswith("_"):
            skipped.append(name)
            continue
        packages.append(name)

    source = (f"PYZ-00.toc（{len(pyz_names)} 个顶层模块） ∪ _internal 顶层目录"
              if pyz_names else
              "仅 _internal 顶层目录（⚠ 未找到 build/PYZ-00.toc，纯 Python 包可能漏列）")
    return packages, skipped, source


def own_resource_names() -> set[str]:
    """项目自有内容：自己的模块（*.py）与资源目录（assets/tools/ui 等）。"""
    names = set(OWN_RESOURCE_DIRS)
    try:
        for entry in ROOT.iterdir():
            if entry.is_dir():
                names.add(entry.name)
            elif entry.suffix == ".py":
                names.add(entry.stem)          # 自己的模块，不是第三方
    except OSError:
        pass
    return names


def resolve_distributions(import_names: list[str]) -> tuple[dict, list[str]]:
    """import 名 → 发行版；返回 ({发行版名: 版本}, 未解析的 import 名)。"""
    try:
        mapping = metadata.packages_distributions()
    except Exception:  # noqa: BLE001
        mapping = {}
    resolved: dict[str, str] = {}
    unresolved: list[str] = []
    for name in import_names:
        dists = mapping.get(name) or []
        if not dists:
            # 少数包名与导入名直接同名（大小写/连字符差异）
            try:
                dists = [metadata.distribution(name).metadata["Name"]]
            except metadata.PackageNotFoundError:
                unresolved.append(name)
                continue
        for dist_name in dists:
            try:
                dist = metadata.distribution(dist_name)
            except metadata.PackageNotFoundError:
                unresolved.append(name)
                continue
            resolved[dist.metadata["Name"] or dist_name] = dist.version
    return resolved, unresolved


def transitive_requirements(dist_names: list[str]) -> list[str]:
    """传递运行时依赖（沿 Requires-Dist，忽略 extra 标记）。

    仅用于**报告**：bundle 里没有的传递依赖不会被列进清单，避免虚增。
    """
    seen: set[str] = set()
    queue = list(dist_names)
    while queue:
        name = queue.pop()
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue
        for raw in dist.requires or []:
            if "extra ==" in raw:  # 可选依赖，不属于默认分发
                continue
            req = raw.split(";")[0].split("[")[0].strip()
            req = req.split("(")[0].split(" ")[0].split("<")[0].split(">")[0]
            req = req.split("=")[0].split("!")[0].split("~")[0].strip()
            if req and req.lower() not in seen:
                queue.append(req)
    return sorted(n for n in seen if n)


# License 字段里这些值等于"没写"（只是指向仓库里的 LICENSE 文件）
_LICENSE_FILE_VALUES = {"license", "license.txt", "license.md", "licence", "copying",
                        "copying.txt", "unknown", "none", "see license", ""}


def _license_of(dist) -> str:
    meta = dist.metadata
    lic = (meta.get("License") or "").strip()
    if lic and len(lic) < 120 and "\n" not in lic \
            and lic.lower() not in _LICENSE_FILE_VALUES:
        return lic
    classifiers = [c for c in meta.get_all("Classifier") or [] if c.startswith("License")]
    if classifiers:
        return "; ".join(c.split("::")[-1].strip() for c in classifiers)
    for field in ("License-Expression",):
        value = (meta.get(field) or "").strip()
        if value:
            return value
    if (meta.get("License-File") or "").strip():
        return "见上游仓库中的 LICENSE 文件（metadata 未给 SPDX 标识）"
    return "见上游项目（metadata 未声明）"


def _homepage(dist) -> str:
    meta = dist.metadata
    for field in ("Home-page", "Project-URL"):
        value = (meta.get(field) or "").strip()
        if not value:
            continue
        if "Project-URL" in field and "," in value:
            return value.split(",", 1)[1].strip()
        return value
    return ""


def build_notices(dist_dir: Path = DEFAULT_DIST, verbose: bool = False) -> tuple[str, dict]:
    packages, skipped, source = bundle_top_levels(dist_dir)
    own = own_resource_names()
    own_present = [n for n in packages if n in own]
    third_party_names = [n for n in packages if n not in own]
    resolved, unresolved = resolve_distributions(third_party_names)

    rows = []
    for name, version in sorted(resolved.items(), key=lambda kv: kv[0].lower()):
        dist = metadata.distribution(name)
        rows.append((name, version, _license_of(dist), _homepage(dist)))

    # 打包器运行时组件（PyInstaller 自己的 runtime hook）确实随包分发，
    # 但它是**打包器**组件而非用户的运行时依赖：单独透明列出，许可证见文末。
    packager_present = [r for r in rows if r[0].lower() in BUILD_ONLY]
    rows = [r for r in rows if r[0].lower() not in BUILD_ONLY]

    all_deps = transitive_requirements(list(resolved.keys()))
    missing_from_bundle = [d for d in all_deps
                           if d.lower() not in {r[0].lower() for r in rows}
                           and d.lower() not in {n.lower() for n in resolved}]

    out = [HEADER]
    width = max((len(r[0]) for r in rows), default=10)
    for name, ver, lic, home in rows:
        out.append(f"{name.ljust(width)}  {ver}")
        out.append(f"{' ' * width}  许可证 : {lic}")
        if home:
            out.append(f"{' ' * width}  主页   : {home}")
        out.append("")

    out.append("\n".join([
        "------------------------------------------------------------",
        "未解析 / 未列入（保证不漏也不多）",
        "------------------------------------------------------------",
        f"bundle 顶层名：{len(packages)} 个，映射到发行版：{len(resolved)} 个",
        f"事实来源：{source}",
    ]))
    if own_present:
        out.append("项目自有资源（非第三方，不计入）：" + ", ".join(sorted(own_present)))
    if unresolved:
        out.append("映射不到发行版的第三方目录（人工复核）：" + ", ".join(sorted(unresolved)))
    if packager_present:
        out.append("随包分发的**打包器**运行时组件（不是运行时依赖，许可证见文末）："
                   + ", ".join(r[0] for r in packager_present))
    if missing_from_bundle:
        out.append("传递依赖中**未打进 bundle** 的包（因此未列清单，不计为分发内容）："
                   + ", ".join(missing_from_bundle))
    out.append("打包器内部件/标准库（不算第三方）："
               + ", ".join(sorted(set(skipped))[:12])
               + ("…" if len(skipped) > 12 else ""))
    out.append(FOOTER)

    stats = {
        "source": source,
        "bundle_dirs": len(packages),
        "own_resources": sorted(own_present),
        "distributions": len(rows),
        "unresolved": sorted(unresolved),
        "packager_components": [r[0] for r in packager_present],
        "transitive": all_deps,
        "skipped": sorted(set(skipped)),
    }
    if verbose:
        print(f"事实来源：{source}")
        print(f"bundle 顶层名 {stats['bundle_dirs']} 个 → 发行版 {stats['distributions']} 个")
        print("未解析：", stats["unresolved"] or "（无）")
        print("随包分发的打包器组件：", stats["packager_components"] or "（无）")
        print("传递依赖（含未打进 bundle 的）：", len(all_deps), "个")
    return "\n".join(out), stats


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="生成 THIRD_PARTY_NOTICES.txt")
    parser.add_argument("--dist", default=str(DEFAULT_DIST), help="onedir 产物目录")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    text, stats = build_notices(Path(args.dist), verbose=not args.quiet)
    out = Path(args.out)
    out.write_text(text, encoding="utf-8")
    print(f"已写 {out}（{len(text.splitlines())} 行，{stats['distributions']} 个发行版）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
