"""真起一个 socket 的冒烟测试。

前面所有测试都直接调 `Api.handle`（不起服务、不占端口）。这一条专门验**接线**：
路由表挂到 `http.server` 上之后，Content-Type、状态码、CORS 头、图片字节是否还在。
「dev server 起得来」这条验收，靠的就是它先证明了服务这一半。
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from conftest import PNG_1X1, make_card, make_data_dir

PID = "p-20200101-aaaaaa"


def fetch(url: str):
    with urllib.request.urlopen(url) as response:
        return response.status, response.headers, response.read()


def test_real_server_serves_index_problem_and_image(tmp_path):
    from server.app import serve

    root = make_data_dir(
        tmp_path,
        [make_card(PID)],
        images={f"{PID}-problem.png": PNG_1X1, f"{PID}-clean.png": PNG_1X1},
    )
    with serve(root, port=0) as base_url:
        status, headers, body = fetch(f"{base_url}/api/index")
        index = json.loads(body)
        assert status == 200
        assert headers["Content-Type"] == "application/json; charset=utf-8"
        assert headers["Access-Control-Allow-Origin"] == "*"
        assert index["data"]["count"] == 1
        assert index["data"]["problems"][0]["id"] == PID

        status, _, body = fetch(f"{base_url}/api/problem/{PID}")
        assert status == 200
        assert json.loads(body)["data"]["attempts_detail"] == []

        status, headers, body = fetch(f"{base_url}/api/problem/{PID}/image/clean")
        assert status == 200
        assert headers["Content-Type"] == "image/png"
        assert body == PNG_1X1


def test_real_server_answers_even_a_bogus_route_with_json(tmp_path):
    """一个裸 404 或 text/plain 回溯，会让界面拿到一个 parse 不了的响应。"""
    from server.app import serve

    with serve(make_data_dir(tmp_path, []), port=0) as base_url:
        try:
            fetch(f"{base_url}/api/nope")
            raise AssertionError("应该 404")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
            assert exc.headers["Content-Type"] == "application/json; charset=utf-8"
            body = json.loads(exc.read())
            assert body["ok"] is False
            assert body["error"]["reason"] == "not_found"
