"""版本信息（唯一来源）。About / Runtime / 日志 / 诊断包都读这里。"""

__version__ = "0.1.2"

# dev / release —— 打包阶段会改成 release，UI 不应在多处写死
BUILD_CHANNEL = "dev"


def version_string() -> str:
    return __version__


def full_version() -> str:
    return f"{__version__}-{BUILD_CHANNEL}" if BUILD_CHANNEL else __version__
