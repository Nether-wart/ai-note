"""信封、错误形状与方法约束（契约 §2、§9、§10）。

这些测试的存在理由只有一条：**不许静默**。一个含糊的 404、一个 500 冒充的
「输入不对」、一个跨源被浏览器吃掉却没人知道的请求，都是这个项目反复被咬的失败。
"""

from __future__ import annotations

import json

import pytest

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


def test_reserved_write_namespaces_say_they_are_reserved():
    """预留的动作要**明说自己还没实现**：含糊的 404 会让人以为是打错了字。

    `/api/page*`（#14）现在**路由在**了，所以整条前缀不再是「预留命名空间」：
    「改」与「重切」已实现（用错方法是 405，见 `test_page_endpoints.py`），
    而「建」与「入库」两个动作归 #15，走**带说明的 404**——形状由这里钉住。
    """
    from server.errors import ApiError
    from server.page_api import PageEndpoint

    for call, arguments in ((PageEndpoint.create_reserved, ()),
                            (PageEndpoint.commit_reserved, ("p-x",))):
        with pytest.raises(ApiError) as excinfo:
            call(*arguments)
        payload = excinfo.value.payload()
        assert excinfo.value.status == 404
        assert payload["code"] == "not_found"
        assert payload["details"]["reserved"] is True
        assert "#15" in payload["details"]["owner"]


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


def test_an_empty_problem_id_is_a_client_bug_not_a_missing_route(api_for):
    """/api/problem/ 是「你没给 id」，不是「没这条路由」（契约 §5.1）。

    404 会让人去翻路由表；400 + `题卡 id 非法：''` 才能让人当场改对。
    而且 `/`、`..`、超长、空白都已经落在 400，只漏「空」这一档看着就像漏网。
    """
    status, body = get_json(api_for([]), "/api/problem/")

    assert status == 400
    assert body["error"]["code"] == "bad_request"
    assert body["error"]["reason"] == "bad_request"
    assert "id" in body["error"]["message"]
    assert body["error"]["details"]["value"] == ""


def test_an_empty_image_kind_blames_the_kind_not_the_id(api_for):
    """`/api/problem/<pid>/image/` 的错要指向 kind，不能指向 pid。

    不然人会去查题卡 id（是对的），而真正的问题是图片类型没给。
    """
    status, body = get_json(api_for([]), "/api/problem/p-20200101-aaaaaa/image/")

    assert status == 400
    assert body["error"]["code"] == "bad_request"
    assert body["error"]["details"]["param"] == "kind"
    assert body["error"]["details"]["value"] == ""
    assert "图片类型" in body["error"]["message"]
