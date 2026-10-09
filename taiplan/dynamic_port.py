"""动态端口分配。

优先 8501；被未知程序占用时在 8501~8510 内寻找可用端口。
选定结果通过 runtime_status（数据库）与内存 IPC 共享，
pywebview / 托盘 / 通知动作 / 单实例 IPC 都读同一个值，不再各自硬编码 8501。
"""

import socket

DEFAULT_PORT = 8501
PORT_SPAN = 10


def candidate_ports(preferred=DEFAULT_PORT, span=PORT_SPAN):
    return list(range(int(preferred), int(preferred) + max(1, int(span))))


def is_port_in_use(port, host="127.0.0.1", timeout=0.3) -> bool:
    """能否连上（有人正在监听）。"""
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return True
    except OSError:
        return False


def is_port_free(port, host="127.0.0.1") -> bool:
    """端口是否可用。

    注意：Windows 上 SO_REUSEADDR 的语义接近 SO_REUSEPORT，
    加了它会让 bind 在「已有进程监听同一端口」时也成功，从而误判为空闲。
    因此这里先做连接探测，再不带 SO_REUSEADDR 地尝试 bind。
    """
    if is_port_in_use(port, host=host):
        return False
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, int(port)))
        return True
    except OSError:
        return False


def find_available_port(preferred=DEFAULT_PORT, span=PORT_SPAN,
                        host="127.0.0.1") -> int:
    """返回第一个可用端口；范围内全被占用则抛 OSError。"""
    for port in candidate_ports(preferred, span):
        if is_port_free(port, host=host):
            return port
    raise OSError(f"{preferred}~{preferred + span - 1} 内没有可用端口")


def base_url(port, host="127.0.0.1") -> str:
    return f"http://{host}:{int(port)}"


# ---------------------------------------------------------------
# 端口发布（跨进程共享：runtime → tray / worker）
# ---------------------------------------------------------------

STATE_NAME = "runtime_port.json"


def publish_port(port, component="streamlit", pid=None,
                 parent_pid=None, owner_token=None):
    """把实际选定的端口写到 state/，供托盘 / Worker 等其他进程读取。

    ownership 字段是**给 orphan 判断用的**：
      * pid          —— 真正监听该端口的 Streamlit child PID（必须真实，不能是 None）
      * parent_pid   —— 拉起它的 Desktop Runtime PID
      * owner_token  —— 该 Runtime 本次启动的唯一 token
    旧 child 退出时靠 pid + owner_token 做 compare-and-delete，避免删掉新 Runtime 的 state。
    """
    from taiplan import config_store
    from datetime import datetime
    payload = {
        "port": int(port),
        "component": component,
        "pid": _as_int(pid),
        "parent_pid": _as_int(parent_pid),
        "owner_token": str(owner_token) if owner_token else None,
        "published_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        config_store.write_json_config_atomic(_state_path(), payload)
    except Exception:  # noqa: BLE001
        # 发布 state 是 best-effort：**绝不能让 Runtime 因为写 state 失败/参数异常而崩**
        # （真实路径下 pid 来自 Popen.pid，一定是 int；这里只是兜底）。
        pass
    return payload


def _as_int(value):
    """尽力转成 int；转不了返回 None（宁可 state 里是 None，也不能让写盘炸）。"""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


# 认为 state 已过期的默认时长（超过它且没人认领 → 视为 stale）
STATE_TTL_SECONDS = 6 * 3600


def _state_path():
    from taiplan import app_paths
    return app_paths.get_state_path(STATE_NAME)


def read_published_state():
    """读取已发布的端口 state（含 ownership）。

    返回 dict 或 None；字段：port / component / pid / parent_pid /
    owner_token / published_at。port 非法时返回 None。
    """
    from taiplan import config_store
    try:
        data = config_store.read_json_config(_state_path(), defaults={}) or {}
    except Exception:  # noqa: BLE001
        return None
    try:
        port = int(data.get("port"))
    except (TypeError, ValueError):
        return None
    if not (DEFAULT_PORT - 1 < port < DEFAULT_PORT + 100):
        return None
    return {
        "port": port,
        "component": data.get("component"),
        "pid": data.get("pid"),
        "parent_pid": data.get("parent_pid"),
        "owner_token": data.get("owner_token"),
        "published_at": data.get("published_at"),
    }


def state_age_seconds(state):
    """state 发布至今的秒数；无法解析返回 None。"""
    from datetime import datetime
    stamp = (state or {}).get("published_at")
    if not stamp:
        return None
    try:
        return max(0.0, (datetime.now() - datetime.fromisoformat(stamp)).total_seconds())
    except (TypeError, ValueError):
        return None


def state_is_expired(state, ttl_seconds=STATE_TTL_SECONDS):
    """state 是否已超过 TTL（这是 read_published_port 文档里承诺的"已过期"语义）。"""
    age = state_age_seconds(state)
    return age is not None and age > float(ttl_seconds)


def state_owner_matches(state, pid=None, owner_token=None):
    """state 是否仍属于给定 pid / token（compare-and-delete 的依据）。"""
    if not state:
        return False
    if pid is not None and str(state.get("pid")) != str(pid):
        return False
    if owner_token is not None and str(state.get("owner_token")) != str(owner_token):
        return False
    return True


def read_published_port():
    """兼容 wrapper：只要端口号。

    真正的"是否过期/是否属于我"请用 read_published_state() + state_is_expired() +
    state_owner_matches()。注意本函数**不做 TTL 判断**，只做端口合法性检查。
    """
    state = read_published_state()
    return state["port"] if state else None


def clear_published_port():
    """无条件清除 state（正常 Runtime 退出时用）。"""
    from taiplan import config_store
    return config_store.delete_config(_state_path())


def clear_published_state(pid=None, owner_token=None):
    """**compare-and-delete**：只有 state 仍属于给定 pid / token 时才删。

    旧 Streamlit child 在父进程死后清 state 必须走这里：否则它可能删掉新 Runtime
    刚发布的新 state（ownership race）。pid/token 不匹配 → 不删，返回 False。
    """
    from taiplan import config_store
    state = read_published_state()
    if state is None:
        return False
    if not state_owner_matches(state, pid=pid, owner_token=owner_token):
        return False
    return bool(config_store.delete_config(_state_path()))


def published_url():
    port = read_published_port() or DEFAULT_PORT
    return base_url(port)
