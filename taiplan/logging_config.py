"""统一日志初始化（RotatingFileHandler，写入用户数据目录）。

格式：timestamp level component message

安全约束：不要把 API Key、AI 原始输入全文、任务描述全文写进日志。
需要记录时请使用 _redact() 或只记长度 / 摘要。
"""

import logging
import logging.handlers
import sys

from taiplan import app_paths

COMPONENTS = ("runtime", "streamlit", "worker", "launcher", "app")

_FORMAT = "%(asctime)s [%(levelname)s] %(component)s: %(message)s"
_MAX_BYTES = 512 * 1024
_BACKUPS = 3

_configured = set()

SENSITIVE_KEYS = ("api_key", "apikey", "token", "secret", "password",
                  "authorization", "key")


class _ComponentFilter(logging.Filter):
    def __init__(self, component):
        super().__init__()
        self.component = component

    def filter(self, record):
        if not hasattr(record, "component"):
            record.component = self.component
        return True


def redact(text, keep=0):
    """把可能含敏感内容的长文本变成可安全写入日志的摘要。"""
    if text is None:
        return "<none>"
    text = str(text)
    if keep and len(text) <= keep:
        return text
    return f"<redacted len={len(text)}>"


def sanitize(text):
    """粗略清洗日志行里的疑似密钥。"""
    if not text:
        return text
    out = str(text)
    for key in SENSITIVE_KEYS:
        idx = out.lower().find(key + '"')
        if idx >= 0:
            out = out[:idx] + '<redacted>'
    return out


def setup_logging(component, level=logging.INFO, console=False):
    """为某个组件初始化日志，返回 logger。重复调用只配置一次。"""
    logger = logging.getLogger(f"todoapp.{component}")
    if component in _configured:
        return logger
    _configured.add(component)

    logger.setLevel(level)
    logger.propagate = False
    logger.addFilter(_ComponentFilter(component))

    try:
        log_dir = app_paths.get_logs_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_dir / f"{component}.log",
            maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8")
        handler.setFormatter(logging.Formatter(_FORMAT))
        handler.addFilter(_ComponentFilter(component))
        logger.addHandler(handler)
    except OSError:
        # 日志目录不可写时不能让程序崩掉
        pass

    if console:
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(logging.Formatter(_FORMAT))
        stream.addFilter(_ComponentFilter(component))
        logger.addHandler(stream)

    return logger


def rebind_to_official_logs():
    """迁移/全新判定通过后调用：把已初始化的组件日志切到正式数据目录。

    返回真正切换的组件列表（未处于 bootstrap 阶段时为空）。
    """
    switched = []
    for component in list(_configured):
        logger = logging.getLogger(f"todoapp.{component}")
        file_handlers = [h for h in list(logger.handlers)
                         if isinstance(h, logging.handlers.RotatingFileHandler)]
        if not file_handlers:
            continue
        try:
            log_dir = app_paths.get_logs_dir()
            log_dir.mkdir(parents=True, exist_ok=True)
            for handler in file_handlers:
                logger.removeHandler(handler)
                handler.close()
            new_handler = logging.handlers.RotatingFileHandler(
                log_dir / f"{component}.log",
                maxBytes=_MAX_BYTES, backupCount=_BACKUPS, encoding="utf-8")
            new_handler.setFormatter(logging.Formatter(_FORMAT))
            new_handler.addFilter(_ComponentFilter(component))
            logger.addHandler(new_handler)
            switched.append(component)
        except OSError:
            pass
    return switched


def get_logger(component):
    """取 logger（必要时先初始化）。"""
    logger = logging.getLogger(f"todoapp.{component}")
    if component not in _configured:
        return setup_logging(component)
    return logger


def log_exception(component, exc, context=""):
    """未捕获异常统一入口：写入组件日志，返回可展示给用户的文案。"""
    logger = get_logger(component)
    logger.exception("未捕获异常 %s", context or "")
    return "TaiPlan 遇到错误，请查看日志。"


def install_excepthook(component):
    """安装全局 excepthook：把未捕获异常写进日志，不吞掉 SystemExit。"""
    def _hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        get_logger(component).error("未捕获异常", exc_info=(exc_type, exc_value, exc_tb))
    sys.excepthook = _hook
    return _hook
