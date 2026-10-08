"""AI 设置持久化与 API Key 安全存储。

- 非敏感配置 → ai_config.json（原子写入）
- API Key → 操作系统安全凭据库（keyring）
- credential_id 基于 api_type + base_url + model_id 生成，避免不同 provider 串 Key
"""

import hashlib
import json
import os
from pathlib import Path
from pathlib import Path

import app_paths
import config_store

try:
    import keyring
except ImportError:  # pragma: no cover
    keyring = None

# W3：新凭据身份；旧身份只读兼容，永不删除
SERVICE_NAME = "TaiPlan-AI"
LEGACY_SERVICE_NAME = "TodoApp-AI"

# 配置文件位于项目根目录
_CONFIG_PATH = app_paths.get_config_path("ai_config.json")

DEFAULT_CONFIG = {
    "enabled": False,
    "api_type": "openai_responses",
    "base_url": "https://api.openai.com/v1",
    "model_id": "",
    "display_name": "",
    "context_budget": 200000,
    "max_output_tokens": 2048,
    "timeout": 60,
    "token_parameter_mode": "auto",
}


def make_credential_id(api_type, base_url, model_id):
    """生成稳定的 credential_id，基于 provider + endpoint + model。"""
    raw = f"{api_type}|{base_url}|{model_id}".strip().lower()
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"{api_type}|{base_url}|{model_id}|{digest}"


# ---------- 非敏感配置（JSON） ----------

def _atomic_write_json(path, data):
    """原子写入：先写临时文件，成功后替换。"""
    tmp = path.with_suffix(".json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def load_config():
    """读取 ai_config.json。文件损坏时返回默认配置（不删除原文件）。"""
    if not _CONFIG_PATH.exists():
        return dict(DEFAULT_CONFIG), "default"
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("not a dict")
        cfg = dict(DEFAULT_CONFIG)
        cfg.update(data)
        # 清理可能残留的敏感字段
        cfg.pop("api" + "_" + "key", None)
        return cfg, "ok"
    except (ValueError, json.JSONDecodeError):
        return dict(DEFAULT_CONFIG), "corrupt"


def save_config(cfg):
    """只保存非敏感字段到 ai_config.json（原子写入）。"""
    data = {
        "enabled": bool(cfg.get("enabled", False)),
        "api_type": cfg.get("api_type", "openai_responses"),
        "base_url": cfg.get("base_url", ""),
        "model_id": cfg.get("model_id") or cfg.get("model", ""),
        "display_name": cfg.get("display_name", ""),
        "context_budget": int(cfg.get("context_budget", 200000)),
        "max_output_tokens": int(cfg.get("max_output_tokens", 2048)),
        "timeout": int(cfg.get("timeout", 60)),
        "token_parameter_mode": cfg.get("token_param", "auto"),
    }
    config_store.write_json_config_atomic(_CONFIG_PATH, data)


# ---------- API Key（keyring） ----------

def keyring_available():
    return keyring is not None


def get_api_key(credential_id):
    """读取 API Key：新身份优先，新身份没有时回退旧身份；失败返回 None。

    keyring 不可用时返回 None（绝不降级为明文）。
    """
    if keyring is None:
        return None
    try:
        value = keyring.get_password(SERVICE_NAME, credential_id)
    except Exception:
        value = None
    if value:
        return value
    try:
        return keyring.get_password(LEGACY_SERVICE_NAME, credential_id)
    except Exception:
        return None


def legacy_credential_id(config_path=None):
    """从 legacy ai_config.json 推导 credential_id（不硬编码账户名）。"""
    path = Path(config_path) if config_path else _CONFIG_PATH
    if not path.is_file():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return ""
    return make_credential_id(data.get("api_type", ""), data.get("base_url", ""),
                              data.get("model_id", "") or data.get("model", ""))


def migrate_credential(legacy_config_path=None, logger=None):
    """把 legacy 凭据**复制**到新身份（绝不删除旧凭据、绝不落明文）。

    规则：
      * 新身份已有 → 直接沿用（新优先），不动 legacy
      * 新身份没有且 legacy 有 → 读取并写入新身份
      * 写入失败 → 不视为致命：读取时仍会回退 legacy
    """
    info = {"migrated": False, "reason": "", "account": "", "legacy_available": False}
    if keyring is None:
        info["reason"] = "keyring_unavailable"
        return info
    account = legacy_credential_id(legacy_config_path)
    if not account:
        info["reason"] = "no_legacy_config"
        return info
    info["account"] = account
    try:
        if keyring.get_password(SERVICE_NAME, account):
            info["reason"] = "new_identity_already_has_key"
            return info
    except Exception:  # noqa: BLE001
        pass
    try:
        legacy_value = keyring.get_password(LEGACY_SERVICE_NAME, account)
    except Exception:  # noqa: BLE001
        legacy_value = None
    if not legacy_value:
        info["reason"] = "legacy_missing"
        return info
    info["legacy_available"] = True
    try:
        keyring.set_password(SERVICE_NAME, account, legacy_value)
        info["migrated"] = True
        info["reason"] = "copied"
        if logger:
            logger.info("凭据已复制到新身份（长度 %d，内容不记录）", len(legacy_value))
    except Exception as exc:  # noqa: BLE001
        info["reason"] = f"write_failed:{type(exc).__name__}"
        if logger:
            logger.warning("凭据复制到新身份失败，将回退读取旧身份：%s", type(exc).__name__)
    return info


def set_api_key(credential_id, api_key):
    """保存 API Key 到系统凭据库。失败抛异常（不降级明文）。"""
    if keyring is None:
        raise RuntimeError("keyring 不可用")
    keyring.set_password(SERVICE_NAME, credential_id, api_key)


def delete_api_key(credential_id):
    """删除指定 credential 的 API Key。"""
    if keyring is None:
        return
    try:
        keyring.delete_password(SERVICE_NAME, credential_id)
    except Exception:
        pass


# ---------- 旧版明文 Key 迁移 ----------

def migrate_legacy_plaintext_key(cfg):
    """若 JSON 存在旧 api_key 字段，尝试迁移到 keyring。

    成功则从 JSON 删除该字段并写回；失败保留旧值并提示。
    返回 (migrated: bool, message: str)。
    """
    if not _CONFIG_PATH.exists():
        return False, ""
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (ValueError, json.JSONDecodeError):
        return False, ""

    legacy = data.get("api" + "_" + "key")
    if not legacy:
        return False, ""

    credential_id = make_credential_id(
        cfg.get("api_type", "openai_responses"),
        cfg.get("base_url", ""),
        cfg.get("model", ""),
    )
    try:
        set_api_key(credential_id, legacy)
        data.pop("api" + "_" + "key", None)
        _atomic_write_json(_CONFIG_PATH, data)
        return True, "旧版明文 API Key 已迁移到系统安全凭据库。"
    except Exception:
        return False, "存在旧版明文 API Key，但系统凭据库不可用，请立即处理。"
