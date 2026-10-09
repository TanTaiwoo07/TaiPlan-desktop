"""配置读写统一入口。

- 原子写入：temp → flush/fsync → os.replace
- 损坏文件不 crash：隔离为 <name>.corrupt.<timestamp>.json，回退默认值
- 读取时 defaults + saved 合并（旧配置缺字段不会报错）
- 路径一律来自 app_paths，不依赖当前工作目录
"""

import json
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

from taiplan import app_paths


class ConfigError(Exception):
    """配置无法读写（非损坏，例如权限问题）。"""


def resolve_path(name_or_path):
    """接受配置文件名或完整 Path。"""
    if isinstance(name_or_path, Path):
        return name_or_path
    text = str(name_or_path)
    if os.path.isabs(text) or os.sep in text or "/" in text:
        return Path(text)
    return app_paths.get_config_path(text)


def _quarantine(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    target = path.with_name(f"{path.stem}.corrupt.{stamp}{path.suffix or '.json'}")
    try:
        shutil.copy2(path, target)
    except OSError:
        pass
    return target


def read_json_config(name_or_path, defaults=None, logger=None):
    """读取 JSON 配置。损坏时隔离原文件并返回 defaults（不抛异常）。"""
    path = resolve_path(name_or_path)
    base = dict(defaults) if defaults else {}
    if not path.is_file():
        return base
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError) as exc:
        target = _quarantine(path)
        if logger:
            logger.warning("配置损坏，已隔离：%s → %s (%s)", path.name,
                           target.name, type(exc).__name__)
        return base
    except OSError as exc:
        if logger:
            logger.warning("配置无法读取：%s (%s)", path.name, type(exc).__name__)
        return base

    if not isinstance(data, dict):
        target = _quarantine(path)
        if logger:
            logger.warning("配置内容不是对象，已隔离：%s → %s", path.name, target.name)
        return base

    merged = dict(base)
    merged.update(data)          # defaults + saved，未知旧字段一并保留
    return merged


def write_json_config_atomic(name_or_path, data, logger=None) -> Path:
    """原子写入 JSON 配置。"""
    path = resolve_path(name_or_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError(f"无法创建配置目录：{path.parent}") from exc

    payload = json.dumps(data, ensure_ascii=False, indent=2)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp",
                                    dir=str(path.parent))
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise ConfigError(f"无法写入配置：{path}") from exc
    if logger:
        logger.debug("配置已写入：%s", path.name)
    return path


def config_exists(name_or_path) -> bool:
    return resolve_path(name_or_path).is_file()


def delete_config(name_or_path) -> bool:
    path = resolve_path(name_or_path)
    try:
        path.unlink()
        return True
    except OSError:
        return False


def list_corrupt_files():
    """列出已隔离的损坏配置文件。"""
    try:
        return sorted(p for p in app_paths.get_config_dir().glob("*.corrupt.*.json"))
    except OSError:
        return []
