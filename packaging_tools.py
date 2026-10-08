"""打包期共用的元数据与安全检查（build.py 与 TaiPlan.spec 都用）。

- ``version_info_text`` / ``write_version_file``：生成 Windows 版本资源
- ``scan_sensitive_files``：扫描 dist，确认没有把用户数据 / 密钥打进去

**扫描分级**（重要）：onedir 包里的第三方数据文件（Streamlit 的前端静态资源、
它自带的文档、streamlit_calendar 的 frontend 目录等）天然含有 ``.env``、
``api_key=`` 这类**示例字符串**。一视同仁地报错会把正常第三方文件误判成泄露。
所以分级是**显式**的——只有调用方声明为「有意打包的第三方包」的目录才降级：

- 默认（不传 ``vendor_prefixes``）：严格模式，所有文件都做全套检查 —— 单元测试用这个
- build.py 传入 ``BUNDLED_PACKAGE_PREFIXES``：第三方包目录只做
  「真实凭据原文比对」+ ``todo.db`` 名称检查
  （第三方文档里的示例不可能是我们的凭据；真凭据混进去仍会被抓到）

``probe`` 是可选的真实凭据明文，仅用于逐字节比对，不会被打印或写回。
"""
from __future__ import annotations

import re
from pathlib import Path

APP_DISPLAY_NAME = "TaiPlan"
APP_COMPANY_NAME = "TaiWoo_Chen"          # 公司 / 发布者
APP_COPYRIGHT = "\u00a9 2026 TaiWoo_Chen"  # 版权
APP_INTERNAL_NAME = "TaiPlan"
RELEASE_EXE = "TaiPlan.exe"
DEBUG_EXE = "TaiPlan-Debug.exe"

OWN_TOP_LEVEL_DIRS = {"assets", "tools"}
CONTENTS_DIR = "_internal"

# 我们有意打包进发行版的第三方包（与 TaiPlan.spec 的收集范围一致）。
# 它们自带的数据文件里可能出现 `.env` / `api_key=` 之类的示例，不算泄露。
BUNDLED_PACKAGE_PREFIXES = (
    "streamlit", "streamlit_calendar", "webview", "pystray", "keyring",
    "pil", "pillow", "winotify", "altair", "pandas", "numpy", "pyarrow",
    "pydeck", "packaging", "click", "toml", "typing_extensions", "watchdog",
    "jinja2", "git", "gitdb", "smmap", "blinker", "cachetools", "tenacity",
    "protobuf", "google", "narwhals", "requests", "urllib3", "certifi",
    "charset_normalizer", "idna", "jsonschema", "python_dateutil", "pytz",
    "six", "tzdata", "tornado", "uvicorn", "starlette", "anyio", "httptools",
    "pyinstaller", "setuptools", "pkg_resources", "pyi_", "proxystore",
)

# 绝对不该出现在发行包里的文件名
FORBIDDEN_FILES = {
    "todo.db", "todo.db-wal", "todo.db-shm", "todo.db-journal",
    ".env", ".env.local",
    "appearance.json", "ai_config.json", "calendar_config.json",
    "notification_config.json", "desktop_config.json", "runtime_port.json",
    "first_run.json", "migration_state.json",
}
# 数据库文件任何位置都不允许出现（第三方包也不会带这个文件名）
DB_NAMES = {"todo.db", "todo.db-wal", "todo.db-shm", "todo.db-journal"}
FORBIDDEN_DIRS = {"logs", "backups", "state"}

CONTENT_PATTERNS = [
    ("API Key 形态 (sk-…)", re.compile(rb"sk-[A-Za-z0-9_\-]{16,}")),
    ("api_key 赋值", re.compile(rb"api[_\-]?key\s*[\"']?\s*[:=]\s*[\"'][^\"']{12,}[\"']", re.I)),
    ("bearer token", re.compile(rb"bearer\s+[A-Za-z0-9_\-\.]{24,}", re.I)),
]
SKIP_SUFFIX = {".png", ".ico", ".jpg", ".jpeg", ".gif", ".woff", ".woff2", ".ttf",
               ".exe", ".dll", ".pyd", ".so", ".zip", ".pyc"}


def version_tuple(version: str, fallback=(0, 1, 0, 0)) -> tuple:
    """'0.1.0' → (0, 1, 0, 0)；解析不出来就用 fallback。"""
    parts = []
    for chunk in str(version).split("."):
        digits = re.match(r"\d+", chunk.strip())
        parts.append(int(digits.group(0)) if digits else 0)
        if len(parts) == 4:
            break
    while len(parts) < 4:
        parts.append(0)
    return tuple(parts[:4])


def version_info_text(version: str, exe_name: str = RELEASE_EXE) -> str:
    """生成 PyInstaller ``version=`` 需要的 VSVersionInfo 文本。

    FileVersion / ProductVersion 用 version.py 里的**原字符串**（例如 ``0.1.0``），
    ``filevers`` / ``prodvers`` 才用 Windows 要求的四段数字元组。
    """
    v = version_tuple(version)
    raw = str(version).strip() or ".".join(str(x) for x in v)
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(
    filevers={v},
    prodvers={v},
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', '{APP_COMPANY_NAME}'),
        StringStruct('FileDescription', '{APP_DISPLAY_NAME}'),
        StringStruct('FileVersion', '{raw}'),
        StringStruct('InternalName', '{APP_INTERNAL_NAME}'),
        StringStruct('LegalCopyright', '{APP_COPYRIGHT}'),
        StringStruct('OriginalFilename', '{exe_name}'),
        StringStruct('ProductName', '{APP_DISPLAY_NAME}'),
        StringStruct('ProductVersion', '{raw}'),
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""


def write_version_file(path, version: str, exe_name: str = RELEASE_EXE) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(version_info_text(version, exe_name), encoding="utf-8")
    return path


def _strip_contents_dir(parts):
    parts = list(parts)
    if parts and parts[0].lower() == CONTENTS_DIR:
        parts = parts[1:]
    return parts


def is_vendor_path(rel, vendor_prefixes=()) -> bool:
    """该路径是否落在「有意打包的第三方包」目录下。"""
    parts = _strip_contents_dir(Path(rel).parts)
    if len(parts) <= 1:
        return False
    top = parts[0].lower()
    for raw in vendor_prefixes:
        v = str(raw).lower()
        if top == v or top.startswith(v + "-") or top.startswith(v + "."):
            return True
    return False


def classify_path(rel, vendor_prefixes=()) -> str:
    """把 dist 内的相对路径分成 root / own / vendor 三档。"""
    if is_vendor_path(rel, vendor_prefixes):
        return "vendor"
    parts = _strip_contents_dir(Path(rel).parts)
    if len(parts) <= 1:
        return "root"
    return "own" if parts[0].lower() in OWN_TOP_LEVEL_DIRS else "root"


def scan_sensitive_files(root, probe=None, vendor_prefixes=(), max_files=30000):
    """扫描 dist 目录，返回发现列表。

    每项：``{"kind": ..., "path": 相对路径, "severity": "error"|"warning"}``。
    默认严格模式；``vendor_prefixes`` 里的第三方包目录只做强检查。
    """
    root = Path(root)
    findings = []
    if not root.is_dir():
        return [{"kind": "dist 目录不存在", "path": str(root), "severity": "error"}]

    needle = None
    if probe:
        needle = probe.encode("utf-8") if isinstance(probe, str) else bytes(probe)

    files = 0
    for p in root.rglob("*"):
        rel = p.relative_to(root)
        parts = [s.lower() for s in rel.parts]
        depth_under_root = len(_strip_contents_dir(parts))

        if p.is_dir():
            if p.name.lower() in FORBIDDEN_DIRS and depth_under_root <= 1:
                findings.append({"kind": f"顶层目录 {p.name}/", "path": str(rel),
                                 "severity": "error"})
            continue

        files += 1
        if files > max_files:
            break

        # 1) 用户数据库文件：任何位置都不允许
        if p.name.lower() in DB_NAMES:
            findings.append({"kind": f"用户数据文件 {p.name}", "path": str(rel),
                             "severity": "error"})
            continue

        try:
            blob = p.read_bytes()
        except OSError:
            continue

        # 2) 真实凭据原文比对：所有位置都查（含第三方包数据）
        if needle and needle in blob:
            findings.append({"kind": "真实 API Key 明文", "path": str(rel),
                             "severity": "error"})
            continue

        # 3) 声明过的第三方包目录：只做上面的强检查
        if is_vendor_path(rel, vendor_prefixes):
            continue

        # 4) 自家文件与根层级：名称 + 内容形态
        if p.name.lower() in FORBIDDEN_FILES:
            findings.append({"kind": f"用户数据文件 {p.name}", "path": str(rel),
                             "severity": "error"})
            continue
        if set(parts) & FORBIDDEN_DIRS:
            findings.append({"kind": "用户数据目录内文件", "path": str(rel),
                             "severity": "error"})
            continue
        if p.suffix.lower() in SKIP_SUFFIX:
            continue

        for name, pat in CONTENT_PATTERNS:
            if pat.search(blob):
                findings.append({"kind": name, "path": str(rel), "severity": "error"})
                break

    return findings
