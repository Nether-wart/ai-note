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


def post_multipart(base_url: str, files):
    """真的从 socket 上传一次：容器里的 curl 与手机的 FormData 走的就是这条路。"""
    import urllib.request

    from conftest import multipart_body

    body, ctype = multipart_body(files)
    request = urllib.request.Request(
        f"{base_url}/api/inbox", data=body, method="POST",
        headers={"Content-Type": ctype, "Content-Length": str(len(body))},
    )
    with urllib.request.urlopen(request) as response:
        return response.status, json.loads(response.read())


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


# ------------------------------------------------- #13：手机上传这条路真的通


def test_a_real_socket_takes_the_upload_and_serves_the_upload_page(tmp_path):
    """验收 1 的服务端那一半：起真服务 → 一次 HTTP 上传 → 收件目录里多了那个文件。

    剩下那一半是「手机浏览器能不能打开」——那需要一个真的手机与局域网，
    在沙箱里验不到，所以这里只验到「页面能被 HTTP 取到 + 上传真的落盘」。
    """
    from server.app import serve

    root = make_data_dir(tmp_path, [])
    with serve(root, port=0) as base_url:
        status, headers, body = fetch(f"{base_url}/upload")
        assert status == 200
        assert headers["Content-Type"] == "text/html; charset=utf-8"
        assert b"capture=\"environment\"" in body
        assert b"/api/inbox" in body

        status, env = post_multipart(base_url, [("phone.jpg", PNG_1X1)])
        assert status == 200 and env["ok"] is True
        assert env["data"]["received"][0]["stored_as"].endswith(".jpg")
        assert env["data"]["received"][0]["bytes"] == len(PNG_1X1)
        assert list((root / "inbox").iterdir())[0].read_bytes() == PNG_1X1

        # 手动入口也真的在 socket 上活着
        request = urllib.request.Request(f"{base_url}/api/inbox/scan", data=b"", method="POST")
        with urllib.request.urlopen(request) as response:
            scan = json.loads(response.read())
        assert [f["name"] for f in scan["data"]["found"]] == \
            [env["data"]["received"][0]["stored_as"]]


def test_an_oversized_body_is_refused_with_json_without_reading_it(tmp_path):
    """上限要在**读 body 之前**判。判据是可观察的：声明 5000 字节却只发几个字节，
    服务必须在没有读到那 5000 字节的情况下就把 413 发回来（先读再拒会一直等下去）。
    """
    import http.client
    import urllib.parse

    from server.app import serve

    with serve(make_data_dir(tmp_path, []), port=0, max_upload_bytes=32) as base_url:
        parsed = urllib.parse.urlsplit(base_url)
        conn = http.client.HTTPConnection(parsed.hostname, parsed.port, timeout=5)
        conn.putrequest("POST", "/api/inbox")
        conn.putheader("Content-Type", "multipart/form-data; boundary=x")
        conn.putheader("Content-Length", "5000")
        conn.endheaders()
        conn.send(b"only a few bytes")  # 远少于声明的 5000
        response = conn.getresponse()
        body = json.loads(response.read())
        conn.close()

    assert response.status == 413
    assert response.getheader("Content-Type") == "application/json; charset=utf-8"
    assert body["error"]["code"] == "payload_too_large"
    assert not (tmp_path / "data" / "inbox").exists()


# ------------------------------------- 验收 2：绑 0.0.0.0 时印出来的地址


def test_binding_a_wildcard_host_but_printing_the_explicit_phone_address(tmp_path):
    """ADR 0007 第 5 条那处债的正面判据：绑 `0.0.0.0` 是为了让手机连上来，
    此时上传页链接与页锚点必须用**显式给的对外地址**，不是 `--host`。"""
    from server.app import serve

    explicit = "http://192.168.1.50:8765"
    # 客户端连的是 127.0.0.1（服务真绑在 0.0.0.0 上），响应里给的却是给人手机用的地址
    with serve(make_data_dir(tmp_path, []), host="0.0.0.0", port=0,
               public_base=explicit) as base_url:
        status, _, body = fetch(f"{base_url}/api/index")
    server = json.loads(body)["data"]["server"]

    assert status == 200
    assert server["public_base"] == explicit
    assert server["upload_url"] == f"{explicit}/upload"
    assert "0.0.0.0" not in server["upload_url"]
    assert server["reachable_from_other_devices"] is True


def test_a_wildcard_bind_without_an_explicit_address_shouts_in_the_index(tmp_path):
    """不许静默：推导出来的地址手机打不开时，索引里必须有一句会喊的警告。"""
    from server.app import serve

    with serve(make_data_dir(tmp_path, []), host="0.0.0.0", port=0) as base_url:
        status, _, body = fetch(f"{base_url}/api/index")

    server = json.loads(body)["data"]["server"]
    assert server["public_base"].startswith("http://0.0.0.0:")
    assert server["reachable_from_other_devices"] is False
    assert "public_base_not_reachable" in [w["code"] for w in json.loads(body)["warnings"]]


def test_the_cli_takes_the_inbox_and_the_public_base_from_flags_and_env(monkeypatch, tmp_path):
    """配置项必须有**两个**入口（命令行＋环境变量）：手机那条路上，
    服务往往是被脚本或桌面图标拉起来的，改不了命令行。"""
    from server.app import build_parser

    monkeypatch.setenv("AI_NOTE_INBOX", str(tmp_path / "box"))
    monkeypatch.setenv("AI_NOTE_PUBLIC_BASE", "http://10.0.0.2:9000")
    args = build_parser().parse_args([])
    assert args.inbox == str(tmp_path / "box")
    assert args.public_base == "http://10.0.0.2:9000"

    args = build_parser().parse_args(["--inbox", "x", "--public-base", "http://1.2.3.4"])
    assert (args.inbox, args.public_base) == ("x", "http://1.2.3.4")


def test_the_inbox_follows_the_data_dir_when_nothing_says_otherwise(monkeypatch):
    """换了 `--data` 却还往仓库里的 `data/inbox` 写，就是往真数据里写
    ——这是本工单最硬的一条禁令，所以默认值是「没给」而不是一个绝对路径。"""
    from server.app import build_parser

    monkeypatch.delenv("AI_NOTE_INBOX", raising=False)
    assert build_parser().parse_args(["--data", "/tmp/somewhere"]).inbox is None

    # 没给收件目录时，Catalog 把它落在数据目录下面（upload 的端到端测试验的就是这条）
    from server.http import Api

    api = Api("/tmp/somewhere-else")
    assert str(api.catalog.inbox.dir) == "/tmp/somewhere-else/inbox"


def test_env_local_can_set_the_public_base_and_the_inbox(tmp_path, monkeypatch):
    """`.env.local` 里的 `AI_NOTE_*` 必须真的生效。

    这条不是形式主义：`--public-base`/`--inbox` 的默认值是**建 parser 时**从环境里现算的，
    所以 `main()` 必须**先**把 `.env.local` 灌进环境再建 parser。顺序反了的话，
    写进文件里的对外地址会**静默失效**——正是 ADR 0007 第 6 条要挡的那类失败。
    """
    from server.app import build_parser, load_local_env

    (tmp_path / ".env.local").write_text(
        "AI_NOTE_PUBLIC_BASE=http://10.1.2.3:8765\nAI_NOTE_INBOX=/tmp/from-env-file/inbox\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("AI_NOTE_PUBLIC_BASE", raising=False)
    monkeypatch.delenv("AI_NOTE_INBOX", raising=False)

    # 仓库根那份先灌（`~/.env.local` 可能有，但 setdefault 让先来的赢）
    assert (tmp_path / ".env.local") in load_local_env(tmp_path)
    args = build_parser().parse_args([])
    assert args.public_base == "http://10.1.2.3:8765"
    assert args.inbox == "/tmp/from-env-file/inbox"


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


# --------------------- #14「改」在**真 socket** 上必须通（曾经是 501 + text/html）

PAGE_ID = "41c86bcfc007"


def _write_page(root, blocks, page_id: str = PAGE_ID):
    (root / "pages").mkdir(parents=True, exist_ok=True)
    page = {
        "version": 1,
        "id": page_id,
        "image": f"{page_id}.png",
        "created_at": "2026-10-04T14:31:35+08:00",
        "origin": {"original_file": "2.png", "sheet": None, "page_number": None},
        "blocks": blocks,
    }
    (root / "pages" / f"{page_id}.json").write_text(
        json.dumps(page, ensure_ascii=False), encoding="utf-8"
    )
    return page


def patch(url: str, payload: dict):
    request = urllib.request.Request(
        url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="PATCH",
    )
    with urllib.request.urlopen(request) as response:
        return response.status, response.headers, response.read()


def test_a_real_socket_takes_the_page_patch_it_advertises(tmp_path):
    """`PATCH /api/page/<id>` 在**真 socket** 上必须通，而且预检必须先答应它。

    这条曾经是坏的：路由表（`Api._route`）与预检都答应 PATCH，但 `http.server` 那一层
    没有 `do_PATCH`，于是真实浏览器拿到框架自带的 **501 + text/html**——不是契约 §2
    要求的信封。它活了很久，因为唯一吃 PATCH 的测试直接调 `api.handle("PATCH", …)`，
    **绕过了 socket**：判据断言的层级与坏掉的那一层错开一格。

    所以这条测试有两半，缺一不可：① **走真 socket**；② 顺手钉住**预检里列了 PATCH**
    ——跨源预检不通过时，浏览器根本不会把这条请求发出去，那时候后一半永远看不到。
    """
    from server.app import serve

    root = make_data_dir(tmp_path, [])
    _write_page(root, [{
        "id": "b1", "bbox_norm": [0.02, 0.02, 0.9, 0.2], "bbox_px": None,
        "card_id": None, "keep": None, "ink": None, "decision": None,
        "question_no": 1, "problem_type": None,
    }])

    with serve(root, port=0) as base_url:
        # ① 预检：站点在 :3000、服务在 :8765，跨源是常态
        request = urllib.request.Request(f"{base_url}/api/page/{PAGE_ID}", method="OPTIONS")
        with urllib.request.urlopen(request) as response:
            assert response.status == 204
            allowed = response.headers["Access-Control-Allow-Methods"]
        assert "PATCH" in [m.strip() for m in allowed.split(",")], (
            f"预检没答应 PATCH（{allowed}）→ 浏览器不会发出这条请求，切分修正页等于没有写路径"
        )

        # ② 真发一次 PATCH
        status, headers, body = patch(f"{base_url}/api/page/{PAGE_ID}", {
            "edits": [{"action": "move", "block_id": "b1", "bbox_norm": [0.0, 0.0, 1.0, 0.25]}],
        })

        assert status == 200
        assert headers["Content-Type"] == "application/json; charset=utf-8"
        envelope = json.loads(body)
        assert envelope["ok"] is True, envelope
        assert envelope["data"]["changed"] is True

        # ③ 盘上的页文件真的变了（不是只回了一封信封）
        on_disk = json.loads((root / "pages" / f"{PAGE_ID}.json").read_text("utf-8"))
        assert on_disk["blocks"][0]["bbox_norm"] == [0.0, 0.0, 1.0, 0.25]
