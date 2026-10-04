"""真起一个 socket 的冒烟测试。

前面所有测试都直接调 `Api.handle`（不起服务、不占端口）。这一条专门验**接线**：
路由表挂到 `http.server` 上之后，Content-Type、状态码、CORS 头、图片字节是否还在，
以及 **POST 的 body 有没有被读到并转发给写端点**。
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


def post(url: str, payload: dict):
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(request) as response:
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


def test_real_server_writes_a_screen_attempt_over_a_socket(tmp_path):
    """POST 的 body 要真的被 `http.server` 读到并转发给写端点（接线，不联网模型）。"""
    from server.app import serve
    from server.judge_client import JudgeCall

    class StubJudge:
        def __call__(self, standard_answer, answer):
            return JudgeCall(text='{"equivalent": true, "confidence": 0.95, "reason": "ok"}',
                             provider="deepseek", model="deepseek-flash",
                             run_id="20261004-090000-000-judge.json", usage={})

    root = make_data_dir(
        tmp_path,
        [make_card(PID)],
        images={f"{PID}-problem.png": PNG_1X1, f"{PID}-clean.png": PNG_1X1},
    )
    with serve(root, port=0, judge=StubJudge(), runs_dir=tmp_path / "runs") as base_url:
        status, headers, body = post(f"{base_url}/api/attempt/{PID}",
                                     {"channel": "screen", "answer": "A"})

        assert status == 200
        assert headers["Content-Type"] == "application/json; charset=utf-8"
        assert headers["Access-Control-Allow-Origin"] == "*"
        data = json.loads(body)["data"]
        assert data["attempt"]["channel"] == "screen"
        assert data["attempt"]["provider"] == "deepseek"
        assert data["run_id"] == "20261004-090000-000-judge.json"

        # 坏输入也必须是 JSON 信封，不是 socket 层的裸错误
        request = urllib.request.Request(
            f"{base_url}/api/attempt/{PID}", data=b"{not json",
            headers={"Content-Type": "application/json"}, method="POST",
        )
        try:
            urllib.request.urlopen(request)
            raise AssertionError("应该 400")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400
            assert exc.headers["Content-Type"] == "application/json; charset=utf-8"
            assert json.loads(exc.read())["error"]["code"] == "bad_request"


def test_a_taken_port_says_so_instead_of_a_traceback(tmp_path, capsys):
    """起不来也要说人话：`Errno 98` 加一段回溯等于让人自己去猜哪一步错了。

    这条不是洁癖——作者自己在里程碑二里就撞上了（8765 被别的进程占着），
    当时服务吐了一段回溯，人得读完才知道是端口的问题。
    """
    from server.app import main, serve

    root = make_data_dir(tmp_path, [make_card(PID)])
    with serve(root, port=0) as base_url:
        taken = int(base_url.rsplit(":", 1)[1])
        assert main(["--data", str(root), "--port", str(taken)]) == 2

    err = capsys.readouterr().err
    assert "已被占用" in err
    assert "Errno" not in err and "Traceback" not in err
