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


def test_no_write_namespace_is_reserved_any_more(api_for):
    """预留机制现在**一个动作都不剩**：#14 落了「改」「重切」，#15 落了「建」「入库」。

    所以 `/api/page*` 的坏输入给的是 **400 带 `allowed`**（输入错），而不是
    「预留、还没实现」的 404——两者混起来会让人分不清「我发错了」与「还没做」。
    `RESERVED` 这张表本身留着：它是**将来**新增预留端点时的形状，不是死代码。
    """
    from server.http import RESERVED

    assert RESERVED == {}
    r = api_for([]).handle("POST", "/api/page", b"{}", content_type="application/json")
    body = json.loads(r.body)

    assert r.status == 400
    assert body["error"]["code"] == "bad_request"
    assert "reserved" not in body["error"].get("details", {})


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


# ---------------------------------------------------------------- 兜底 500 的 reason（R5）


def test_the_last_resort_500_is_an_envelope_whose_reason_is_registered(api_for):
    """R5/D9：`Api.handle` 最后那一档（没预料到的异常）也必须是 §2 的信封。

    它的输入是「谁都不该这么调」——这一档存在的意义正是：真出了没预料到的异常时，
    出口仍是一个带 `reason` 的 JSON 信封（D1：绝不许裸回溯、绝不许空响应体），
    而 `reason` 还得是 §9 登记过的取值（否则下游照码表写分支就会漏掉真正的兜底）。
    """
    api = api_for([])

    r = api.handle("POST", "/api/page", 12345,
                   content_type="multipart/form-data; boundary=----x")
    body = json.loads(r.body)

    assert r.status == 500
    assert body["error"]["code"] == "internal_error"
    assert body["error"]["reason"] == "internal_error"
    assert body["error"]["message"], "message 要带异常类名（不许空响应体）"
    assert ":" in body["error"]["message"], "形状是 `<异常类名>: <说明>`"
    assert body["error"]["hint"], "hint 指向服务日志"
    assert "Traceback" not in r.body.decode("utf-8")


def test_the_500_reason_values_in_the_contract_match_the_code():
    """R5：§9 为 `internal_error` 登记的 `reason` 取值，与代码里真会发的那些一致。

    两个方向都要红：契约里多一个没人发的取值 = 文档撒谎；代码里多一个没登记的取值 =
    下游照码表写分支时漏掉真兜底（这次漏的就是兜底那一档）。
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]                     # server/
    contract = (Path(__file__).resolve().parents[2]
                / "docs" / "contracts" / "http-api-v0.md").read_text(encoding="utf-8")
    section = contract.split("`internal_error` 下的 `reason` 取值")[1]
    block = section.split("`bad_request` 下的")[0]
    registered = set(re.findall(r"^- `([a-z_]+)`", block, re.M))
    sources = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))

    assert registered == {"page_file_unreadable", "filesystem_error", "internal_error"}, block
    assert {name for name in registered if f'"{name}"' in sources} == registered, \
        f"契约登记了但代码里没人发的取值：{ {n for n in registered if f'\"{n}\"' not in sources} }"
