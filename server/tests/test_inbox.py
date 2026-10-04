"""#13：收件目录与手机上传页（ADR 0007 第 4、5 条，契约 §10.3）。

录入的定义只有一句话：**往收件目录放一个文件**。三条路（电脑拖拽／手机上传页／
同步盘目录）之后走**同一条**管道。

这个文件只测外部行为——HTTP 请求进去、信封出来、磁盘上多了个文件出来。
为此两件事必须显式（ADR 0007 第 6 条「不许静默」）：

- **切分还没实现**（#10 在后面）。此时返回的 `pipeline.segmentation` 必须说
  「不可用」，`blocks` 必须是 `null`（**不是空列表**——空列表会被读成「切出来 0 块」），
  `committed` 必须是 `false`。返回一个假块列表／假红笔统计／假「已入库」是最坏的失败。
- **目录监视不实现**（inotify 在同步盘上不可靠）。所以扫描是显式的手动等价入口，
  而且它扫到文件之后必须说清「我找到了但没处理，为什么」。
"""

from __future__ import annotations

import json

from conftest import PNG_1X1, make_card, multipart_body


def build_api(tmp_path, *, files=(), segmenter=None, max_upload_bytes=None, **kwargs):
    """一个指向临时数据目录的 Api，收件目录就在它下面（绝不碰真数据）。"""
    from server.http import Api

    root = tmp_path / "data"
    (root / "problems").mkdir(parents=True, exist_ok=True)
    (root / "assets").mkdir(parents=True, exist_ok=True)
    for card in files:
        (root / "problems" / f"{card['id']}.json").write_text(
            json.dumps(card, ensure_ascii=False), encoding="utf-8"
        )
    extra = {}
    if segmenter is not None:
        extra["segmenter"] = segmenter
    if max_upload_bytes is not None:
        extra["max_upload_bytes"] = max_upload_bytes
    return Api(root, **extra, **kwargs)

def post(api, target, body=b"", content_type="application/octet-stream"):
    r = api.handle("POST", target, body=body, content_type=content_type)
    return r.status, json.loads(r.body)


# --------------------------------------------------------------- 手机上传页


def test_the_upload_page_is_served_by_the_backend_as_one_html_file(tmp_path):
    """ADR 0007 第 2 条：后端**不渲染页面**，但**托管静态资源**。上传页就是那个资源。

    手机上不该为了拍一张照片下载整个前端（ADR 0007 后果），所以：单文件、
    `GET /upload` 直接给 HTML、没有任何外部资源引用（手机在局域网里可能没网）。
    """
    api = build_api(tmp_path)
    r = api.handle("GET", "/upload")
    html = r.body.decode("utf-8")

    assert r.status == 200
    assert r.content_type.startswith("text/html")
    assert r.headers["Cache-Control"] == "no-store"
    # 拍照与「一个文件夹 = 多页」两条路都在页面上
    assert 'capture="environment"' in html
    assert "webkitdirectory" in html
    # 提交到后端自己的收件端点，不是 Docusaurus 的产物
    assert "/api/inbox" in html
    # 无构建步骤、不依赖站点产物：页面里没有任何外链资源（脚本、样式都内联）
    for needle in ("<script src", "<link", "/assets/js/", "docusaurus.config"):
        assert needle not in html.lower(), f"上传页依赖了外部/构建产物：{needle}"
    # 没有外部资源：断网（或手机连不上外网）时页面仍然能用
    for needle in ('src="http', "src='http", 'href="http', "href='http", "//cdn"):
        assert needle not in html, f"上传页引用了外部资源：{needle}"


def test_upload_page_only_answers_get(tmp_path):
    api = build_api(tmp_path)
    r = api.handle("POST", "/upload", body=b"x", content_type="text/plain")

    assert r.status == 405
    assert json.loads(r.body)["error"]["code"] == "method_not_allowed"


# --------------------------------------------------- 往收件目录放一个文件


def test_one_photo_lands_in_the_inbox_dir_and_the_pipeline_says_what_it_did_not_do(tmp_path):
    """录入的定义就是这一条：一个文件 → 收件目录里多一个文件。

    切分（#10）还没实现，所以这次响应必须**显式**说「没有块列表」：
    `blocks` 是 `null`（不是 `[]`——空列表会被读成「切出来 0 块」）、
    `committed` 是 `false`、`warnings[]` 里有一句原话。返回假的块列表／假红笔统计／
    假「已入库」是这一类里最坏的一种失败。
    """
    import hashlib

    api = build_api(tmp_path)
    body, ctype = multipart_body([("IMG_0001.png", PNG_1X1)])
    status, env = post(api, "/api/inbox", body, ctype)

    assert status == 200 and env["ok"] is True
    expected_name = hashlib.sha256(PNG_1X1).hexdigest()[:12] + ".png"
    assert env["data"]["received"] == [{
        "name": "IMG_0001.png",
        "stored_as": expected_name,
        "page_index": 0,
        "bytes": len(PNG_1X1),
        "sha256": hashlib.sha256(PNG_1X1).hexdigest(),
        "content_type": "image/png",
        "already_present": False,
    }]
    # 文件真的躺进了收件目录（收件目录在数据目录下面，是临时目录）
    stored = tmp_path / "data" / "inbox" / expected_name
    assert stored.read_bytes() == PNG_1X1
    assert env["data"]["inbox"]["files"] == 1

    seg = env["data"]["pipeline"]["segmentation"]
    assert seg["available"] is False and seg["reason"] == "not_implemented"
    assert "切分尚未实现" in seg["message"]
    assert env["data"]["pipeline"]["pages"] == [
        {"page_index": 0, "stored_as": expected_name, "blocks": None}
    ]
    assert env["data"]["pipeline"]["committed"] is False
    assert [w["code"] for w in env["warnings"]] == ["segmentation_not_implemented"]
    assert env["warnings"][0]["message"] == seg["message"]
    assert env["skipped"] == []


def test_a_folder_of_photos_is_pages_in_upload_order(tmp_path):
    """ADR 0007 第 4 条：电脑上「一个文件夹 = 多页（批量）」。

    页的顺序就是上传的顺序——切分与页文件（#9/#10）拿它当页序。
    """
    api = build_api(tmp_path)
    photos = [(f"page-{i}.jpg", b"\xff\xd8JPEG" + bytes([i]) * 10) for i in range(1, 4)]
    status, env = post(api, "/api/inbox", *multipart_body(photos))

    assert status == 200
    assert env["data"]["grouping"]["kind"] == "multi_page"
    assert env["data"]["grouping"]["count"] == 3
    assert [f["page_index"] for f in env["data"]["received"]] == [0, 1, 2]
    assert [f["name"] for f in env["data"]["received"]] == \
        ["page-1.jpg", "page-2.jpg", "page-3.jpg"]
    assert env["data"]["inbox"]["files"] == 3
    assert [p["page_index"] for p in env["data"]["pipeline"]["pages"]] == [0, 1, 2]
    assert len(list((tmp_path / "data" / "inbox").iterdir())) == 3


def test_the_same_photo_twice_does_not_pile_up_in_the_inbox(tmp_path):
    """同步盘会把同一张照片再同步一遍；文件名取内容哈希，所以重复上传不堆两份。"""
    api = build_api(tmp_path)
    first = post(api, "/api/inbox", *multipart_body([("a.png", PNG_1X1)]))[1]
    second = post(api, "/api/inbox", *multipart_body([("副本.png", PNG_1X1)]))[1]

    assert second["data"]["received"][0]["stored_as"] == \
        first["data"]["received"][0]["stored_as"]
    assert second["data"]["received"][0]["already_present"] is True
    assert second["data"]["inbox"]["files"] == 1
    assert "already_in_inbox" in [w["code"] for w in second["warnings"]]


def test_a_weird_suffix_is_taken_but_announced(tmp_path):
    """同步盘里什么都会掉进来。不收是武断的，不说是不许的（ADR 0007 第 6 条）。"""
    api = build_api(tmp_path)
    status, env = post(api, "/api/inbox", *multipart_body([("scan.txt", b"not a photo")]))

    assert status == 200
    assert env["data"]["received"][0]["stored_as"].endswith(".txt")
    assert env["data"]["received"][0]["content_type"] == "application/octet-stream"
    assert "unexpected_file_type" in [w["code"] for w in env["warnings"]]


def test_a_traversal_filename_cannot_escape_the_inbox(tmp_path):
    """服务将来要经 Tailscale 暴露给手机（契约 §7.3），文件名是别人给的输入。"""
    api = build_api(tmp_path)
    status, env = post(api, "/api/inbox",
                       *multipart_body([("../../../evil.png", PNG_1X1)]))

    assert status == 200
    assert env["data"]["received"][0]["name"] == "evil.png"
    assert not (tmp_path / "evil.png").exists()
    assert not (tmp_path / "data" / "data").exists()


# --------------------------------------------------------------- 失败形状


def test_a_body_without_a_file_part_is_a_400_that_names_the_parameter(tmp_path):
    """输入错不许用 404 或 500 表达，`details` 要点名是哪个参数（契约 §9）。"""
    api = build_api(tmp_path)
    body, ctype = multipart_body([("x.png", PNG_1X1)], field="photo")
    status, env = post(api, "/api/inbox", body, ctype)

    assert status == 400
    assert env["ok"] is False
    assert env["error"]["code"] == "bad_request"
    assert env["error"]["reason"] == "bad_request"
    assert env["error"]["details"]["param"] == "file"
    assert env["error"]["details"]["allowed"] == ["file"]
    assert env["error"]["hint"]


def test_a_non_multipart_body_is_a_400_not_a_500(tmp_path):
    api = build_api(tmp_path)
    status, env = post(api, "/api/inbox", b"raw bytes", "image/png")

    assert status == 400
    assert env["error"]["details"]["allowed"] == ["multipart/form-data"]


def test_an_oversized_upload_is_refused_with_413_and_is_not_written(tmp_path):
    """不能把「一张 2GB 的 body」读进内存再说对不起；上限是可注入的，测试不造大文件。"""
    api = build_api(tmp_path, max_upload_bytes=16)
    status, env = post(api, "/api/inbox", *multipart_body([("big.png", b"x" * 64)]))

    assert status == 413
    assert env["error"]["code"] == "payload_too_large"
    assert env["error"]["details"]["max"] == 16
    assert not (tmp_path / "data" / "inbox").exists()


def test_an_empty_part_is_skipped_and_said_so(tmp_path):
    """`skipped` 比警告重：那一张**没有**收进收件目录，不能只靠一句警告带过（契约 §2）。"""
    api = build_api(tmp_path)
    status, env = post(api, "/api/inbox",
                       *multipart_body([("empty.png", b""), ("ok.png", PNG_1X1)]))

    assert status == 200
    assert [f["name"] for f in env["data"]["received"]] == ["ok.png"]
    assert [s["code"] for s in env["skipped"]] == ["inbox_part_empty"]
    assert "empty.png" in env["skipped"][0]["message"]
    assert env["data"]["inbox"]["files"] == 1


def test_the_inbox_endpoints_only_answer_post(tmp_path):
    api = build_api(tmp_path)
    for target in ("/api/inbox", "/api/inbox/scan"):
        r = api.handle("GET", target)
        assert r.status == 405
        assert json.loads(r.body)["error"]["details"]["allowed"] == ["POST", "OPTIONS"]


# ------------------------------------------- 目录监视失效时的手动等价入口


def test_scan_finds_photos_dropped_into_the_directory_and_says_what_it_did_not_do(tmp_path):
    """第三路录入：同步盘把照片落进收件目录，而 **inotify 在某些挂载上不灵**
    （ADR 0007 待验证项）。所以「扫一遍」必须是显式入口，且它不许假装处理过。
    """
    api = build_api(tmp_path)
    inbox_dir = tmp_path / "data" / "inbox"
    inbox_dir.mkdir(parents=True)
    (inbox_dir / "synced-1.png").write_bytes(PNG_1X1)
    (inbox_dir / "synced-2.jpg").write_bytes(b"\xff\xd8JPEGDATA")

    status, env = post(api, "/api/inbox/scan")

    assert status == 200 and env["ok"] is True
    assert env["data"]["inbox"] == {"dir": str(inbox_dir), "exists": True,
                                    "created": False, "files": 2}
    assert env["data"]["watch"]["implemented"] is False
    assert "手动等价入口" in env["data"]["watch"]["message"]
    found = env["data"]["found"]
    assert [f["name"] for f in found] == ["synced-1.png", "synced-2.jpg"]
    assert [f["status"] for f in found] == ["unprocessed", "unprocessed"]
    assert {f["reason"] for f in found} == {"segmentation_not_implemented"}
    # 「找到了」不等于「处理了」：没走完管道要说出来，且没有假块列表
    assert [p["blocks"] for p in env["data"]["pipeline"]["pages"]] == [None, None]
    assert env["data"]["pipeline"]["committed"] is False
    assert [w["code"] for w in env["warnings"]] == ["segmentation_not_implemented"]


def test_scan_on_a_missing_directory_creates_it_and_says_so(tmp_path):
    """「收件目录不存在」不该是一个含糊的空响应，也不该是一个错误。"""
    api = build_api(tmp_path)
    status, env = post(api, "/api/inbox/scan")

    assert status == 200
    assert env["data"]["inbox"]["created"] is True
    assert env["data"]["found"] == []
    assert [w["code"] for w in env["warnings"]] == ["inbox_created"]


def test_an_injected_segmenter_is_the_seam_the_blocks_come_through(tmp_path):
    """切分（#10）接上来的口子就在这里。这个测试用假切分器证明接缝是真的、
    不是装饰——接上之后块列表**真的**从管道里出来，而入库仍然明说没做。
    """
    blocks = [{"index": 1, "bbox_norm": [0.0, 0.1, 1.0, 0.3], "ink_px": 12}]
    api = build_api(tmp_path, segmenter=lambda path: [dict(b, page=path.name) for b in blocks])
    body, ctype = multipart_body([("p.png", PNG_1X1)])
    status, env = post(api, "/api/inbox", body, ctype)

    assert status == 200
    page = env["data"]["pipeline"]["pages"][0]
    assert page["blocks"][0]["index"] == 1
    assert env["data"]["pipeline"]["segmentation"]["available"] is True
    assert "segmentation_not_implemented" not in [w["code"] for w in env["warnings"]]
    # 块出来了，但**没有**入库——`committed` 与 commit 那段话仍然说实话
    assert env["data"]["pipeline"]["committed"] is False
    assert env["data"]["pipeline"]["commit"]["reason"] == "not_implemented"
