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
