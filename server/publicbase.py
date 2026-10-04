"""对外可达地址（ADR 0007 第 5 条，契约 §1）。

页锚点、以及手机上要打开的上传页链接，都必须用**手机真能访问到的地址**。
原型的 `ANCHOR_BASE = f"http://{args.host}:{args.port}"`（`proto/server.py:1447`）
是一处**已查明的债务**：为了手机访问而绑 `0.0.0.0` 时，它给出 `http://0.0.0.0:8765`
——一个手机访问不到的地址。

所以这里有两件事，都是纯逻辑（不联网、不占端口，脱网可测）：

1. `resolve_public_base`：显式给的一律照用；没给才按**绑定之后**的 `host:port` 推导。
2. `public_base_warning`：推导出来的地址如果手机打不开（通配地址／环回），
   就**喊出来**让用户显式配一个（不许静默——ADR 0007 第 6 条）。

这个模块是「对外地址」的唯一实现：`Catalog` 暴露它、启动日志印它、上传页链接拼它，
将来页锚点也拼它（`public_url`）。
"""

from __future__ import annotations

# 通配地址：绑它是为了「谁都连得上」，但把它写进 URL 谁都连不上。
_WILDCARD_HOSTS = frozenset({"0.0.0.0", "::", "[::]", "*", ""})
# 环回：只有这台机器自己能用。默认只监听本机时这是**设计如此**（ADR 0003），
# 不是问题；但绑了通配地址却印环回地址，就是手机打不开的链接。
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

WARNING_CODE = "public_base_not_reachable"


def host_of(base: str) -> str:
    """从 `http://host:port[/path]` 里取出 host（IPv6 的方括号保留）。"""
    rest = base.split("://", 1)[-1]
    for sep in ("/", "?", "#"):
        rest = rest.split(sep, 1)[0]
    if rest.startswith("["):  # [::1]:8765
        return rest.split("]", 1)[0] + "]"
    if rest.count(":") == 1:
        return rest.split(":", 1)[0]
    return rest  # 裸 IPv6 或没有端口


def resolve_public_base(bound_host: str, bound_port: int, explicit: str | None = None) -> str:
    """显式值优先；否则按**绑定之后**的 `host:port` 推导。

    `bound_port` 必须是 socket 真正绑上的那个端口：`--port 0` 时端口是系统给的，
    用命令行的 0 去拼会得到一个打不开的地址。
    """
    if explicit and explicit.strip():
        return explicit.strip().rstrip("/")
    host = bound_host
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"  # IPv6 字面量在 URL 里必须带方括号
    return f"http://{host}:{bound_port}"


def is_reachable_from_other_devices(base: str) -> bool:
    """这个地址是不是「别的设备（手机）也能打开」的。

    通配地址与环回地址都只有本机能用。这是个**便宜的检查**，不是一次真实探测。
    """
    return host_of(base) not in (_WILDCARD_HOSTS | _LOOPBACK_HOSTS)


def public_base_warning(bind_host: str | None, base: str) -> dict | None:
    """绑了通配地址（说明用户想让别的设备连）却印出一个打不开的地址 → 喊一声。

    返回 Warning 形状（契约 §2）或 `None`。`bind_host` 为 `None` 时不判
    （没接线，不乱喊——测试直接构造 `Api` 时就属于这种）。
    """
    if bind_host is None:
        return None
    binds_for_others = bind_host not in _LOOPBACK_HOSTS
    if not binds_for_others or is_reachable_from_other_devices(base):
        return None
    return {
        "code": WARNING_CODE,
        "message": f"对外地址是 {base}，手机打不开这个地址 → "
                   f"用 --public-base（或 AI_NOTE_PUBLIC_BASE）显式指定本机在局域网里的地址，"
                   f"例如 http://192.168.1.x:8765。绑 {bind_host} 是为了让手机连上来，"
                   f"而通配／环回地址从别处连不上",
        "id": None,
        "level": "warning",
    }
