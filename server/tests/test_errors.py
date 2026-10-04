"""信封、错误形状与方法约束（契约 §2、§9、§10）。

这些测试的存在理由只有一条：**不许静默**。一个含糊的 404、一个 500 冒充的
「输入不对」、一个跨源被浏览器吃掉却没人知道的请求，都是这个项目反复被咬的失败。
"""

from __future__ import annotations

import json

from conftest import get_json


def test_unknown_route_is_a_json_404_with_a_hint(api_for):
    status, body = get_json(api_for([]), "/api/nope")

    assert status == 404
    assert body["ok"] is False
    assert body["error"]["code"] == "not_found"
    assert "hint" in body["error"]
    assert body["warnings"] == [] and body["skipped"] == []


def test_write_methods_on_read_only_endpoints_are_405(api_for):
    r = api_for([]).handle("POST", "/api/index")
    body = json.loads(r.body)

    assert r.status == 405
    assert body["error"]["code"] == "method_not_allowed"
    assert body["error"]["details"]["allowed"] == ["GET", "OPTIONS"]


def test_reserved_write_namespaces_say_they_are_reserved(api_for):
    """#9 #13 要落在这里。含糊的 404 会让人以为是打错了字。

    `/api/attempt/` 已由 #5 落地，不在这一列：它收到 GET 是 405（说清收 POST），
    见 `test_attempt_endpoint.py`。
    """
    for target in ("/api/page/p-x", "/api/inbox", "/api/inbox/scan"):
        status, body = get_json(api_for([]), target)

        assert status == 404, target
        assert body["error"]["code"] == "not_found"
        assert "预留" in body["error"]["message"] or "预留" in body["error"].get("hint", "")


def test_every_response_carries_cors_headers(api_for):
    """站点在 :3000、服务在 :8765，是跨源。没有它列表页一行数据都拿不到。"""
    api = api_for([])
    for target in ("/api/index", "/api/nope"):
        r = api.handle("GET", target)
        assert r.headers["Access-Control-Allow-Origin"] == "*"


def test_options_preflight_is_answered(api_for):
    r = api_for([]).handle("OPTIONS", "/api/index")
    assert r.status == 204
    assert "POST" in r.headers["Access-Control-Allow-Methods"]
    assert "Content-Type" in r.headers["Access-Control-Allow-Headers"]


def test_every_error_carries_a_machine_readable_reason(api_for):
    """`code` 是信封级类别，`reason` 是细因。没有更细的原因时 `reason == code`。"""
    api = api_for([])
    cases = [
        api.handle("GET", "/api/nope"),                        # 路由 404
        api.handle("GET", "/api/problem/p-nope"),              # 题 404
        api.handle("GET", "/api/problem/../x"),                # 400
        api.handle("GET", "/api/problem/p-x/image/thumb"),     # 400
        api.handle("POST", "/api/index"),                      # 405
    ]
    for response in cases:
        error = json.loads(response.body)["error"]
        assert isinstance(error.get("reason"), str) and error["reason"], error
        assert error["reason"] == error.get("reason", error["code"])
    assert cases[2] and json.loads(cases[2].body)["error"]["reason"] == "bad_request"
