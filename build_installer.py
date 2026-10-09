#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""第 17 阶段：把 dist/TaiPlan（PyInstaller onedir）打成 release/TaiPlan-Setup-<version>.exe

流程（文档第 47 条）：
    1 读 version.py          → 版本唯一来源
    2 校验 dist 产物          → TaiPlan.exe + _internal
    3 dist 冒烟自检           → TaiPlan.exe --smoke-test（临时数据目录）
    4 dist 敏感文件扫描        → 不含 todo.db / 日志 / 备份 / API Key
    5 生成 installer/version.iss
    6 定位 Inno Setup ISCC.exe
    7 编译 installer/TaiPlan.iss
    8 输出 release/TaiPlan-Setup-<version>.exe
    9 写 SHA256
   10 打印摘要

用法：
    .venv\\Scripts\\python.exe build_installer.py
    .venv\\Scripts\\python.exe build_installer.py --skip-smoke --skip-scan
    .venv\\Scripts\\python.exe build_installer.py --iscc "D:\\Inno Setup 6\\ISCC.exe"
"""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import hashlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST_DIR = ROOT / "dist" / "TaiPlan"
INSTALLER_DIR = ROOT / "installer"
ISS_FILE = INSTALLER_DIR / "TaiPlan.iss"
VERSION_ISS = INSTALLER_DIR / "version.iss"
RELEASE_DIR = ROOT / "release"
EXE_NAME = "TaiPlan.exe"

# 安装器输入中禁止出现的用户数据/密钥文件名（文档第 3、52 条）
FORBIDDEN_INPUT_NAMES = {
    "todo.db", "todo.db-wal", "todo.db-shm", "todo.db-journal",
    ".env", "api_key.txt", "credentials.json",
}
FORBIDDEN_INPUT_DIRS = {"logs", "backups", "state", "config"}

# 常见安装位置（含 winget 默认的**按用户**安装位置）
ISCC_CANDIDATES = (
    r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
    r"C:\Program Files\Inno Setup 6\ISCC.exe",
)

# 只支持 Inno Setup 6（RC 起移除 Inno Setup 5 fallback：两代编译器行为不同，
# 允许 5 会产出不可预期的安装包）。
ISCC_RELATIVE = (r"Inno Setup 6\ISCC.exe",)
ISCC_REQUIRED_MAJOR = 6

# ---- 安装器 identity：AppId 必须锁死，不能只校验"是个 GUID" ----
# [Setup] 里的写法是 "{{GUID}"（Inno 用 {{ 转义出一个字面 {）；
# 注册表卸载键则是 {GUID}_is1 —— 两者都由这里派生，避免各写一份而漂移。
APP_ID_GUID = "{8E2F4C1B-7A3D-4E62-9B5F-2C7A1D0E4F83}"
INNO_APP_ID_LITERAL = "{{" + APP_ID_GUID[1:]          # 即 {{8E2F...E4F83}
UNINSTALL_KEY = (
    "Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\"
    + APP_ID_GUID + "_is1"
)


def local_appdata_dir() -> Path:
    """%LOCALAPPDATA% 目录。

    某些 shell（例如 AutoClaw 宿主）里 LOCALAPPDATA 为空，必须回退到
    ~/AppData/Local —— 与 app_paths 的处理保持一致，否则会漏掉 winget 的
    **按用户**安装位置（这正是本机实际的位置）。
    """
    value = os.environ.get("LOCALAPPDATA")
    if value:
        return Path(value)
    try:
        return Path.home() / "AppData" / "Local"
    except RuntimeError:
        # 环境被清空（例如测试里 clear=True）时无法确定 home：返回 None，
        # 调用方跳过这一档候选，绝不抛异常。
        return None


def _dynamic_candidates():
    """按用户安装（winget / 免管理员安装）与环境变量目录下的候选路径。"""
    out = []
    local = local_appdata_dir()
    if local is not None:
        for rel in ISCC_RELATIVE:
            out.append(local / "Programs" / rel)
    for env in ("ProgramFiles", "ProgramFiles(x86)"):
        root = os.environ.get(env)
        if root:
            for rel in ISCC_RELATIVE:
                out.append(Path(root) / rel)
    return out


class BuildError(RuntimeError):
    """安装包构建失败。"""


# ---------------------------------------------------------------- 步骤 1：版本
def read_version() -> tuple[str, str]:
    """从 version.py 读版本（唯一来源，不在 .iss / 脚本里硬编码）。"""
    sys.path.insert(0, str(ROOT))
    from taiplan import version as version_module  # noqa: PLC0415

    return version_module.__version__, getattr(version_module, "BUILD_CHANNEL", "dev")


def version_tuple(version: str) -> tuple[int, ...]:
    parts = [int(x) for x in re.findall(r"\d+", version)[:4]]
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts)


# ---------------------------------------------------------------- 步骤 2：dist
def check_dist(dist_dir: Path = DIST_DIR) -> Path:
    """校验 onedir 产物存在且看起来是完整的。"""
    exe = dist_dir / EXE_NAME
    internal = dist_dir / "_internal"
    if not dist_dir.is_dir():
        raise BuildError(f"找不到 dist 目录：{dist_dir}\n请先运行：.venv\\Scripts\\python.exe build.py")
    if not exe.is_file():
        raise BuildError(f"找不到 {exe}\n请先运行：.venv\\Scripts\\python.exe build.py")
    if not internal.is_dir():
        raise BuildError(f"缺少 _internal 目录：{internal}（onedir 产物不完整）")
    count = sum(1 for _ in internal.rglob("*") if _.is_file())
    if count < 100:
        raise BuildError(f"_internal 里只有 {count} 个文件，产物不完整，拒绝打包")
    return exe


def dist_size_report(dist_dir: Path = DIST_DIR) -> dict:
    files = [p for p in dist_dir.rglob("*") if p.is_file()]
    return {
        "files": len(files),
        "bytes": sum(p.stat().st_size for p in files),
    }


def human_size(num_bytes: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num_bytes < 1024 or unit == "GB":
            return f"{num_bytes:.2f} {unit}"
        num_bytes /= 1024
    return f"{num_bytes:.2f} GB"


# ---------------------------------------------------------------- 步骤 3：冒烟
def run_smoke(exe: Path, timeout: int = 300) -> None:
    """在临时数据目录里跑 dist 的自检，不允许碰真实用户数据。"""
    tmp = Path(tempfile.mkdtemp(prefix="todoapp-installer-smoke-"))
    env = dict(os.environ)
    env["TODO_APP_DATA_DIR"] = str(tmp)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run(
            [str(exe), "--smoke-test"],
            cwd=str(exe.parent), env=env, capture_output=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise BuildError(f"冒烟自检超时（{timeout}s）") from exc
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # 与 build.py 一致：只看退出码（输出是给人看的，编码随控制台变化，不能用来判定）
    tail = _decode_tail(proc.stdout) + _decode_tail(proc.stderr)
    if proc.returncode != 0:
        raise BuildError(
            f"dist 冒烟自检未通过（exit {proc.returncode}）：\n  " + "\n  ".join(tail))


# ---------------------------------------------------------------- 步骤 4：扫描
def scan_dist(dist_dir: Path = DIST_DIR):
    """复用 packaging_tools 的敏感文件扫描（与 build.py 同一口径）。

    vendor_prefixes：第三方包目录里自带文档/示例出现的 "api_key = ..." 属于上游
    内容，不算泄漏 —— 不传它会把 streamlit 自带文档误报成 error。
    """
    import packaging_tools  # noqa: PLC0415

    return packaging_tools.scan_sensitive_files(
        dist_dir, vendor_prefixes=packaging_tools.BUNDLED_PACKAGE_PREFIXES
    )


def _decode_tail(blob, lines=12):
    """尽力解析子进程输出（UTF-8 / GBK 都可能），只取末尾若干行。"""
    if not blob:
        return []
    for encoding in ("utf-8", "gbk", "mbcs"):
        try:
            text = blob.decode(encoding)
            break
        except (UnicodeDecodeError, LookupError):
            continue
    else:
        text = blob.decode("utf-8", "replace")
    return text.strip().splitlines()[-lines:]


def strip_iss_comments(text: str) -> str:
    """去掉整行注释（Inno 里只有行首 `;` 才是注释，行内 `;` 是参数分隔符）。"""
    return "\n".join(
        line for line in text.splitlines() if not line.strip().startswith(";")
    )


def scan_installer_inputs(iss_path: Path = ISS_FILE, dist_dir: Path = DIST_DIR):
    """安装器输入检查（文档第 3、22、52 条）。

    返回问题列表；空列表表示通过。
    """
    problems: list[str] = []
    text = strip_iss_comments(iss_path.read_text(encoding="utf-8-sig"))

    # 1) [Files] 只允许来自 dist\TaiPlan 与两个说明文档
    files_section = _section(text, "Files")
    for line in files_section.splitlines():
        line = line.strip()
        if not line.lower().startswith("source:"):
            continue
        source = line.split(":", 1)[1].split(";")[0].strip().strip('"')
        allowed = (
            source.startswith("..\\dist\\TaiPlan")
            or source in ("..\\docs\\RELEASE_NOTES.md", "..\\docs\\THIRD_PARTY_NOTICES.txt")
        )
        if not allowed:
            problems.append(f"[Files] 含有非 dist 来源：{source}")
        name = Path(source.replace("\\", "/")).name.lower()
        if name in FORBIDDEN_INPUT_NAMES:
            problems.append(f"[Files] 含有禁止文件：{source}")
        if name in FORBIDDEN_INPUT_DIRS:
            problems.append(f"[Files] 含有禁止目录：{source}")

    # 2) 卸载规则绝不能碰用户数据目录
    # 用户数据目录名（当前身份 TaiPlan + legacy TodoApp）：任何卸载/安装步骤都不得
    # 直接删除 %LOCALAPPDATA%\<数据目录> 本身（那是用户数据，不是安装目录）。
    data_names = (app_paths.APP_DIR_NAME, app_paths.LEGACY_APP_DIR_NAME) \
        if "app_paths" in dir() else ("TaiPlan", "TaiPlan")
    for section_name in ("UninstallDelete", "InstallDelete"):
        section_text = _section(text, section_name)
        if not section_text:
            continue
        for name in data_names:
            if re.search(rf"localappdata[^\n]*\\{name}(?![\\\w])", section_text,
                         re.IGNORECASE):
                problems.append(
                    f"[{section_name}] 出现了 {{localappdata}}\\{name} —— 高风险数据删除，禁止")

    # 3) 安装版不得依赖 cmd / powershell / bat / vbs 启动
    for section in ("Run", "Icons"):
        body = _section(text, section)
        for line in body.splitlines():
            low = line.strip().lower()
            if not low.startswith(("filename:", "name:")):
                continue
            for bad in ("cmd.exe", "powershell", ".bat", ".vbs", "wscript"):
                if bad in low:
                    problems.append(f"[{section}] 依赖了 {bad}：{line.strip()}")

    # 4) AppId 必须**等于**锁定的固定 GUID（不是"像 GUID 就行"）
    app_id = re.search(r'MyAppId\s+"([^"]*)"', text)
    if not app_id:
        problems.append("未找到固定 AppId")
    elif app_id.group(1) != INNO_APP_ID_LITERAL:
        problems.append(
            f"AppId 与锁定值不一致：期望 {INNO_APP_ID_LITERAL!r}，实际 {app_id.group(1)!r}")

    # 4b) Code 段的注册表卸载键必须是 {GUID}_is1（单个左花括号）
    code = _section(text, "Code")
    # ★ 实测结论：{#MyAppId}（{{GUID}} 转义写法）在 [Code] 段里**不会**折叠成单花括号，
    # 用它组装注册表键会让 InstalledVersion() 永远读不到版本 → 降级检测静默失效。
    # 因此 [Code] 必须用单花括号常量 {#MyAppIdCode}。
    if "{#MyAppIdCode}_is1" not in code:
        problems.append("[Code] 未用 {#MyAppIdCode}_is1 组装卸载注册表键")
    if "{#MyAppId}_is1" in code:
        problems.append("[Code] 用了 {#MyAppId}_is1（会展开成双左花括号），降级检测会失效")
    if f'#define MyAppIdCode "{APP_ID_GUID}"' not in text:
        problems.append("缺少单花括号的 MyAppIdCode 定义，或与锁定的 GUID 不一致")
    if "Pos('{{', UninstallKey) = 0" not in code:
        problems.append("[Code] 缺少 UninstallKey 单花括号校验（UninstallKeyOk）")

    # 5) 版本不得硬编码（必须来自 version.iss）
    if re.search(r'MyAppVersion\s+"', text):
        problems.append("TaiPlan.iss 里硬编码了 MyAppVersion，应 #include version.iss")
    if '#include "version.iss"' not in text:
        problems.append("TaiPlan.iss 未 #include version.iss")

    # 6) 每次 build 生成新 GUID 的写法（时间戳/随机）不允许
    if "GetDateTimeString" in app_id.group(1) if app_id else False:
        problems.append("AppId 使用了动态值")

    # 7) dist 里不得混入用户数据
    for path in dist_dir.rglob("*"):
        if path.is_file():
            if path.name.lower() in FORBIDDEN_INPUT_NAMES:
                problems.append(f"dist 里出现禁止文件：{path}")
    return problems


def _section(text: str, name: str) -> str:
    m = re.search(rf"^\[{name}\]\s*$(.*?)(?=^\[|\Z)", text, re.MULTILINE | re.DOTALL)
    return m.group(1) if m else ""


# ---------------------------------------------------------------- 步骤 5：version.iss
def write_version_iss(version: str, path: Path = VERSION_ISS) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Inno 用 BOM 判定脚本编码，务必写 UTF-8 BOM
    path.write_text(
        "; 本文件由 build_installer.py 自动生成，请勿手改（版本唯一来源是 version.py）\n"
        f'#define MyAppVersion "{version}"\n',
        encoding="utf-8-sig",
    )
    return path


# ---------------------------------------------------------------- 步骤 6：定位 ISCC
class _VS_FIXEDFILEINFO(ctypes.Structure):  # noqa: N801
    _fields_ = [
        ("dwSignature", ctypes.wintypes.DWORD),
        ("dwStrucVersion", ctypes.wintypes.DWORD),
        ("dwFileVersionMS", ctypes.wintypes.DWORD),
        ("dwFileVersionLS", ctypes.wintypes.DWORD),
    ]


def file_version(path: Path) -> str | None:
    """读取 PE 版本资源里的 FileVersion（纯 ctypes，无第三方依赖）。

    只有 Inno Setup 6 被支持，因此需要能识别编译器真实版本。
    读不到返回 None。
    """
    try:
        size = ctypes.windll.version.GetFileVersionInfoSizeW(str(path), None)
        if not size:
            return None
        buf = ctypes.create_string_buffer(size)
        if not ctypes.windll.version.GetFileVersionInfoW(str(path), 0, size, buf):
            return None
        value = ctypes.c_void_p()
        length = ctypes.wintypes.UINT()
        if not ctypes.windll.version.VerQueryValueW(
                buf, "\\", ctypes.byref(value), ctypes.byref(length)):
            return None
        info = ctypes.cast(value, ctypes.POINTER(_VS_FIXEDFILEINFO)).contents
        ms, ls = info.dwFileVersionMS, info.dwFileVersionLS
        return f"{ms >> 16}.{ms & 0xFFFF}.{ls >> 16}.{ls & 0xFFFF}"
    except Exception:  # noqa: BLE001
        return None


def iscc_version(path: Path) -> str | None:
    """识别编译器版本。

    首选：运行 ISCC（不给参数）读它自己的版本横幅
    （例如 "Inno Setup 6 Command-Line Compiler" / "Compiler engine version: Inno Setup 6.7.3"），
    实测比 PE 版本资源可靠（ISCC 的固定版本字段为 0）。
    兜底：PE 版本资源里 FileVersion 的字符串部分。
    """
    try:
        proc = subprocess.run([str(path)], capture_output=True, timeout=60)
        text = _decode_tail(proc.stdout, lines=200) + _decode_tail(proc.stderr, lines=200)
        blob = "\n".join(text)
        match = re.search(r"Inno Setup (\d+\.\d+(?:\.\d+)*)", blob)
        if match:
            return match.group(1)
        match = re.search(r"Inno Setup (\d+)\b", blob)
        if match:
            return match.group(1) + ".0"
    except Exception:  # noqa: BLE001
        pass
    return file_version(path)


def _check_iscc_version(path: Path) -> Path:
    """只允许 Inno Setup 6。"""
    ver = iscc_version(path)
    if ver is None:
        raise BuildError(
            f"无法识别编译器版本：{path}\n"
            f"本流程只支持 Inno Setup {ISCC_REQUIRED_MAJOR}。"
            "请安装 Inno Setup 6：https://jrsoftware.org/isdl.php")
    if ver.split(".")[0] != str(ISCC_REQUIRED_MAJOR):
        raise BuildError(
            f"不支持的编译器版本：{path} 是 {ver}，"
            f"只支持 Inno Setup {ISCC_REQUIRED_MAJOR}（已移除 Inno Setup 5 支持）。")
    return path


def find_iscc(explicit: str | None = None) -> Path:
    """依次查：命令行 → 环境变量 INNO_SETUP_COMPILER → 常见安装位置。"""
    tried: list[str] = []
    if explicit:
        p = Path(explicit)
        if p.is_file():
            return _check_iscc_version(p)
        tried.append(str(p))

    env = os.environ.get("INNO_SETUP_COMPILER")
    if env:
        p = Path(env)
        if p.is_file():
            return _check_iscc_version(p)
        tried.append(f"$INNO_SETUP_COMPILER={env}")

    for cand in ISCC_CANDIDATES:
        p = Path(cand)
        if p.is_file():
            return _check_iscc_version(p)
        tried.append(cand)

    for cand in _dynamic_candidates():
        if cand.is_file():
            return _check_iscc_version(cand)
        tried.append(str(cand))

    raise BuildError(
        "Inno Setup 6 未安装。\n"
        "请安装 Inno Setup 6 后重试（免费）：https://jrsoftware.org/isdl.php\n"
        "或设置环境变量 INNO_SETUP_COMPILER 指向 ISCC.exe，例如：\n"
        '  $env:INNO_SETUP_COMPILER = "C:\\Program Files (x86)\\Inno Setup 6\\ISCC.exe"\n'
        "或使用 --iscc 指定路径。\n"
        "已尝试：\n  " + "\n  ".join(tried)
    )


# ---------------------------------------------------------------- 步骤 7：编译
def compile_installer(iscc: Path, iss: Path = ISS_FILE, release_dir: Path = RELEASE_DIR) -> Path:
    release_dir.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [str(iscc), f"/O{release_dir}", str(iss)],
        cwd=str(iss.parent), capture_output=True, timeout=1800,
    )
    out = (proc.stdout or b"").decode("utf-8", "replace")
    err = (proc.stderr or b"").decode("utf-8", "replace")
    if proc.returncode != 0:
        raise BuildError(f"ISCC 编译失败（exit {proc.returncode}）：\n{out[-2000:]}\n{err[-2000:]}")
    sys.stdout.write(out[-800:])
    setups = sorted(release_dir.glob("TaiPlan-Setup-*.exe"), key=lambda p: p.stat().st_mtime)
    if not setups:
        raise BuildError("编译返回 0，但 release/ 里没有 TaiPlan-Setup-*.exe")
    return setups[-1]


# ---------------------------------------------------------------- 步骤 9：SHA256
def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_sha256(setup: Path) -> tuple[Path, str]:
    digest = sha256_of(setup)
    side = setup.with_suffix(setup.suffix + ".sha256")
    side.write_text(f"{digest}  {setup.name}\n", encoding="utf-8")
    return side, digest


# ---------------------------------------------------------------- main
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="构建 TaiPlan Windows 安装包")
    parser.add_argument("--skip-smoke", action="store_true", help="跳过 dist 冒烟自检")
    parser.add_argument("--skip-scan", action="store_true", help="跳过敏感文件扫描")
    parser.add_argument("--iscc", default=None, help="ISCC.exe 路径")
    parser.add_argument("--only-version-iss", action="store_true",
                        help="只生成 installer/version.iss（无 Inno 也能跑，供测试用）")
    args = parser.parse_args(argv)

    version, channel = read_version()
    print(f"[1/10] 版本：{version}（channel={channel}）")

    if args.only_version_iss:
        path = write_version_iss(version)
        print(f"[5/10] 已生成 {path}")
        return 0

    exe = check_dist()
    size = dist_size_report()
    print(f"[2/10] dist 产物：{exe}（{size['files']} 个文件，{human_size(size['bytes'])}）")

    if args.skip_smoke:
        print("[3/10] 已跳过冒烟自检")
    else:
        run_smoke(exe)
        print("[3/10] dist 冒烟自检通过（临时数据目录）")

    if args.skip_scan:
        print("[4/10] 已跳过敏感文件扫描")
    else:
        result = scan_dist()
        findings = getattr(result, "findings", result) or []
        if findings:
            raise BuildError(f"dist 敏感文件扫描发现问题：{findings[:5]}")
        print("[4/10] dist 敏感文件扫描通过（0 命中）")

    problems = scan_installer_inputs()
    if problems:
        raise BuildError("安装器输入检查未通过：\n  " + "\n  ".join(problems))
    print("       安装器输入检查通过（无用户数据 / 无 cmd|powershell 依赖 / AppId 固定）")

    path = write_version_iss(version)
    print(f"[5/10] 已生成 {path}")

    iscc = find_iscc(args.iscc)
    print(f"[6/10] Inno Setup 编译器：{iscc}")

    setup = compile_installer(iscc)
    print(f"[7/10] 编译完成：[8/10] 输出 {setup}")

    side, digest = write_sha256(setup)
    print(f"[9/10] SHA256：{digest}")
    print(f"       已写 {side}")

    setup_bytes = setup.stat().st_size
    raw = size["bytes"]
    print("[10/10] 摘要")
    print(f"  ONEDIR 原始体积 : {human_size(raw)}（{size['files']} 个文件）")
    print(f"  Setup.exe 体积  : {human_size(setup_bytes)}")
    print(f"  压缩后的比例    : {setup_bytes / raw * 100:.1f}%")
    print(f"  发布文件        : {setup}")
    print(f"                  : {side}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except BuildError as exc:
        print(f"\n构建失败：{exc}\n", file=sys.stderr)
        raise SystemExit(2) from exc
