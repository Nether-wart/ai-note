"""对外可达地址：解析与「手机能不能打开」的检查（ADR 0007 第 5 条）。

这条是一处**已查明的债务**：原型 `ANCHOR_BASE = f"http://{args.host}:{args.port}"`
（`proto/server.py:1447`）在为了手机访问而绑 `0.0.0.0` 时，印出来的是
`http://0.0.0.0:8765`——手机**访问不到**。所以这里的判据不是「有个配置项」，
而是「绑 0.0.0.0 时印出来的地址仍然能用，且推不出来的地方会喊」。
"""

from __future__ import annotations

from server.publicbase import (
    is_reachable_from_other_devices,
    public_base_warning,
    resolve_public_base,
)


def test_explicit_public_base_wins_over_the_bound_host():
    """显式给的一律照用——这是那条债务的正面判据（不许从 host 推导了事）。"""
    assert resolve_public_base("0.0.0.0", 8765, "http://192.168.1.50:8765") == \
        "http://192.168.1.50:8765"
    # 域名、带路径的基址、末尾斜杠都要能活下来
    assert resolve_public_base("0.0.0.0", 8765, "https://note.example.com/") == \
        "https://note.example.com"
    assert resolve_public_base("0.0.0.0", 8765, " http://192.168.1.50:8765/ ") == \
        "http://192.168.1.50:8765"


def test_derived_public_base_uses_the_port_the_os_actually_gave():
    """`--port 0` 时端口是绑好之后才有的，先算会算错（契约 §1）。"""
    assert resolve_public_base("127.0.0.1", 51234, None) == "http://127.0.0.1:51234"
    assert resolve_public_base("0.0.0.0", 8765, "") == "http://0.0.0.0:8765"


def test_derived_public_base_brackets_an_ipv6_literal():
    """IPv6 字面量在 URL 里必须带方括号，否则那个地址打不开。"""
    assert resolve_public_base("::1", 8765, None) == "http://[::1]:8765"


def test_wildcard_bind_without_an_explicit_base_shouts():
    """绑 0.0.0.0 说明想让手机连上来；此时印 0.0.0.0 就是那个已查明的债。"""
    base = resolve_public_base("0.0.0.0", 8765, None)
    warning = public_base_warning("0.0.0.0", base)

    assert warning["code"] == "public_base_not_reachable"
    assert "0.0.0.0" in warning["message"]
    assert "--public-base" in warning["message"]
    assert warning["id"] is None
    # 手机打不开的两种地址：通配与环回
    assert is_reachable_from_other_devices(base) is False
    assert is_reachable_from_other_devices("http://127.0.0.1:8765") is False
    assert is_reachable_from_other_devices("http://192.168.1.50:8765") is True
    assert is_reachable_from_other_devices("http://[::1]:8765") is False


def test_the_two_cases_that_must_not_shout():
    """不乱喊：本机自用（默认只监听本机，ADR 0003）不喊；显式配好了更不喊。"""
    # 绑 0.0.0.0 + 显式给了局域网地址 → 没有警告
    assert public_base_warning("0.0.0.0", "http://192.168.1.50:8765") is None
    # 默认：只监听本机、地址也是本机 → 设计如此，不是问题
    assert public_base_warning("127.0.0.1", resolve_public_base("127.0.0.1", 8765, None)) is None
    # 没接线（测试直接构造 Api）时不判
    assert public_base_warning(None, "http://127.0.0.1:8765") is None
    # 绑通配地址却显式配了个环回地址 → 那是用户自己写的，也要喊（手机真打不开）
    assert public_base_warning("0.0.0.0", "http://127.0.0.1:8765")["code"] == \
        "public_base_not_reachable"
