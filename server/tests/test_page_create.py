"""页资源的「建」动作：照片 → 存图 + 建页文件 + 跑切分 + 红笔统计与建议去留（#15、契约 §10.2.1b）。

打的是**外部行为**：一个 multipart 请求进去、一个信封出来、盘上的照片与页文件变/没变。
切分是注入的接缝（`segmenter`），测试不联网、不碰真照片；合成图用 `server/ink.py` 自己的
PNG 编码器画（与 #11 的测试同一套做法，零依赖）。

三条纪律在这里被钉住：

1. **模型失败 → 502，且 data/ 里一个字节都不留**（D1/D9）：照片先落在系统临时目录里跑切分，
   成功之后才写进数据目录。测试断言「连 pages/ 目录都没有被建出来」。
2. **幂等**：同一张照片再传一次是同一个页 id → 报 `page_already_exists`（hint），
   **不重跑切分、不覆盖块列表**（块可能被人改过，「人动过的部分不许被抹掉」）。
3. **切分不可用 / 解析不出块**都不是「这一页没有题」：页文件**不建**，`blocks` 给 `null`。
"""

from __future__ import annotations

import json
import hashlib

from conftest import multipart_body
from server import ink
from test_ink import RED, white_with_blocks


def data_root(tmp_path, *, make_pages: bool = False):
    root = tmp_path / "data"
    (root / "problems").mkdir(parents=True, exist_ok=True)
    (root / "assets").mkdir(parents=True, exist_ok=True)
    if make_pages:
        (root / "pages").mkdir(parents=True, exist_ok=True)
    return root


def build_api(tmp_path, *, segmenter=None, make_pages: bool = False):
    from server.http import Api

    return Api(data_root(tmp_path, make_pages=make_pages), segmenter=segmenter)


def a_photo(tmp_path, *, w: int = 100, h: int = 60):
    """白底 100×60：左半红笔块、右半黑笔块（与 #11 的合成图同一形状）。"""
    img = white_with_blocks(w, h, [(0, 0, 40, 20, RED), (60, 0, 100, 20, (20, 20, 20))])
    blob = ink.encode_png(img)
    path = tmp_path / "page.png"
    path.write_bytes(blob)
    return blob, path


def static_segmenter(blocks):
    """一个假切分器：记下被调用了几次，回一份**统一形状**的候选块。"""
    calls: list = []

    def run(path):
        calls.append(str(path))
        return {"parsed": True, "blocks": list(blocks), "rejected": [],
                "warnings": [], "message": f"读到 {len(blocks)} 块"}

    run.calls = calls
    return run


def post_page(api, blob: bytes, name: str = "page.png"):
    body, content_type = multipart_body([(name, blob)])
    r = api.handle("POST", "/api/page", body, content_type=content_type)
    return r.status, json.loads(r.body)


TWO_BLOCKS = [
    {"id": "b1", "bbox_norm": [0.0, 0.0, 0.4, 1 / 3], "question_no": 17},
    {"id": "b2", "bbox_norm": [0.6, 0.0, 0.4, 1 / 3], "question_no": 18},
]


# ---------------------------------------------------------------- 建起来了


def test_a_photo_becomes_a_page_with_the_stored_image_and_the_blocks(tmp_path):
    """照片进 → 存图（内容哈希前 12 位，与 #13 同一套命名）+ 建页文件 + 返回块列表。"""
    blob, _ = a_photo(tmp_path)
    api = build_api(tmp_path, segmenter=static_segmenter(TWO_BLOCKS))

    status, envelope = post_page(api, blob)

    assert status == 200, envelope
    page_id = envelope["data"]["created"][0]
    assert page_id == hashlib.sha256(blob).hexdigest()[:12]
    # 照片真的落盘了，且逐字节相同
    assert (api.catalog.pages_dir / f"{page_id}.png").read_bytes() == blob
    saved = json.loads((api.catalog.pages_dir / f"{page_id}.json").read_text(encoding="utf-8"))
    assert saved["id"] == page_id and saved["image"] == f"{page_id}.png"
    assert saved["origin"]["original_file"] == "page.png"
    assert [b["question_no"] for b in saved["blocks"]] == [17, 18]
    assert [b["card_id"] for b in saved["blocks"]] == [None, None]
    # 报告的块就是页文件里那些（含统计与建议去留）
    report = envelope["data"]["pages"][0]
    assert [b["id"] for b in report["blocks"]] == ["b1", "b2"]
    assert report["created"] is True and report["segmentation"] == "ran"


def test_the_blocks_carry_the_page_pixel_box_derived_from_the_photo_size(tmp_path):
    """块边界的两套基准：`bbox_norm` 用模型给的（规范基准），`bbox_px` 按照片尺寸推出来。

    #11 的红笔统计读的是 `bbox_px`（整页像素 xyxy），所以这一栏必须有值——它是**新增页**
    的初值（回填那条路照抄盘上原值，D5；新页没有原值可抄）。
    """
    blob, _ = a_photo(tmp_path)
    api = build_api(tmp_path, segmenter=static_segmenter(TWO_BLOCKS))

    _, envelope = post_page(api, blob)
    blocks = envelope["data"]["pages"][0]["blocks"]

    assert blocks[0]["bbox_px"] == [0, 0, 40, 20]
    assert blocks[1]["bbox_px"] == [60, 0, 100, 20]


def test_blocks_get_ink_statistics_and_a_suggested_keep_from_the_one_implementation(tmp_path):
    """建的产出含**每块的红笔统计与建议去留**——统计归 #11、去留归 #12，本动作不另判。"""
    from server import intake

    blob, _ = a_photo(tmp_path)
    api = build_api(tmp_path, segmenter=static_segmenter(TWO_BLOCKS))

    _, envelope = post_page(api, blob)
    blocks = {b["id"]: b for b in envelope["data"]["pages"][0]["blocks"]}

    # 左块是红笔 → 有红笔、没有语义可问 → 判不准 → 收（#12 的规则，兜底那一档）
    assert blocks["b1"]["ink"]["colored_px"] > ink.COLOR_MIN_PIXELS
    assert blocks["b1"]["keep"] is True
    assert blocks["b1"]["decision"]["source"] == intake.SOURCE_FALLBACK
    # 右块只有黑笔 → 没有红笔痕迹 → 不入库（唯一那个「不收」的例外之外的一档）
    assert blocks["b2"]["ink"]["colored_px"] == 0
    assert blocks["b2"]["keep"] is False
    assert blocks["b2"]["decision"]["rule"] == intake.RULE_NO_RED_INK
    # 「没问模型」这件事必须说出来，不许安静兜底
    assert intake.INTAKE_SEMANTICS_FALLBACK in {w["code"] for w in envelope["warnings"]}


# ---------------------------------------------------------------- 幂等


def test_uploading_the_same_photo_twice_keeps_the_first_page_and_does_not_cut_again(tmp_path):
    """同一张照片再传一次 = 同一个页 id：报「已经有了」，**不重跑切分、不覆盖块列表**。"""
    blob, _ = a_photo(tmp_path)
    segmenter = static_segmenter(TWO_BLOCKS)
    api = build_api(tmp_path, segmenter=segmenter)
    _, first = post_page(api, blob)
    page_id = first["data"]["created"][0]
    before = (api.catalog.pages_dir / f"{page_id}.json").read_bytes()

    status, second = post_page(api, blob)

    assert status == 200
    assert second["data"]["created"] == []
    assert second["data"]["existing"] == [page_id]
    assert len(segmenter.calls) == 1, "第二次不许再调切分（那是花钱的那一步）"
    assert (api.catalog.pages_dir / f"{page_id}.json").read_bytes() == before
    assert "page_already_exists" in {w["code"] for w in second["warnings"]}


# ---------------------------------------------------------------- 拒绝/跳过路径（D9）


def test_a_model_failure_leaves_not_one_byte_behind(tmp_path):
    """模型失败 → **502**，而且连 `pages/` 目录都不该被建出来（D1/D9：拒绝不留痕迹）。"""
    from server.model_client import ModelUnavailable

    def broken(path):
        raise ModelUnavailable("连接超时（假的）")

    blob, _ = a_photo(tmp_path)
    api = build_api(tmp_path, segmenter=broken)

    status, envelope = post_page(api, blob)

    assert status == 502
    assert envelope["error"]["code"] == "model_unavailable"
    assert envelope["error"]["reason"] == "model_unavailable"
    assert envelope["error"]["details"]["id"] == hashlib.sha256(blob).hexdigest()[:12]
    assert not api.catalog.pages_dir.exists(), "拒绝路径不许把目录建出来"
    assert list(api.catalog.problems_dir.glob("*.json")) == []


def test_without_a_segmenter_the_page_is_not_created(tmp_path):
    """切分不可用 → 页**不建**、`blocks` 给 `null`（不是 `[]`），并说清是哪一项没做。"""
    blob, _ = a_photo(tmp_path)
    api = build_api(tmp_path)

    status, envelope = post_page(api, blob)

    assert status == 200
    assert envelope["data"]["created"] == []
    row = envelope["data"]["pages"][0]
    assert row["blocks"] is None and row["created"] is False
    assert envelope["data"]["segmentation"]["available"] is False
    assert "segmentation_not_implemented" in {w["code"] for w in envelope["warnings"]}
    assert not api.catalog.pages_dir.exists()
    assert list(api.catalog.pages_dir.glob("*.png")) == []


def test_a_segmentation_that_cannot_be_parsed_is_not_an_empty_page(tmp_path):
    """模型答了话但抠不出块 → 这是「切分没跑成」，**不是**「这一页没有题」：页不建。"""
    blob, _ = a_photo(tmp_path)
    api = build_api(tmp_path, segmenter=lambda path: "这一页我看不清，就不给 JSON 了")

    status, envelope = post_page(api, blob)

    assert status == 200
    assert envelope["data"]["created"] == []
    row = envelope["data"]["pages"][0]
    assert row["blocks"] is None and row["segmentation"] == "unparsed"
    assert row["message"]
    assert "page_segmentation_unparsed" in {w["code"] for w in envelope["warnings"]}
    assert not api.catalog.pages_dir.exists()


def test_a_request_without_a_photo_is_400_and_creates_nothing(tmp_path):
    """不是 multipart / 没有 file 段 / 全是空文件 → **400**，且一个字节都不写。"""
    api = build_api(tmp_path, segmenter=static_segmenter(TWO_BLOCKS))

    raw = api.handle("POST", "/api/page", json.dumps({"image": "x.png"}).encode(),
                     content_type="application/json")
    json_envelope = json.loads(raw.body)

    assert raw.status == 400
    assert json_envelope["error"]["code"] == "bad_request"
    assert json_envelope["error"]["details"]["allowed"] == ["multipart/form-data"]

    body, content_type = multipart_body([("empty.png", b"")])
    response = api.handle("POST", "/api/page", body, content_type=content_type)
    envelope = json.loads(response.body)

    assert response.status == 400
    assert envelope["error"]["details"]["param"] == "file"
    assert not api.catalog.pages_dir.exists()


# ---------------------------------------------------------------- 其余拒绝/跳过分支（D9：每条都要有自己的测试）


def test_two_photos_in_one_upload_become_two_pages(tmp_path):
    """一个文件 = 一页；一次传多个（一个文件夹）= 多页（与 `POST /api/inbox` 同一分组口径）。"""
    first, _ = a_photo(tmp_path)
    second = ink.encode_png(white_with_blocks(80, 40, [(0, 0, 30, 10, RED)]))
    segmenter = static_segmenter([{"id": "b1", "bbox_norm": [0.0, 0.0, 1.0, 1.0], "question_no": 1}])
    api = build_api(tmp_path, segmenter=segmenter)

    body, content_type = multipart_body([("a.png", first), ("b.png", second)])
    response = api.handle("POST", "/api/page", body, content_type=content_type)
    envelope = json.loads(response.body)

    assert response.status == 200
    assert len(envelope["data"]["created"]) == 2
    assert len(envelope["data"]["pages"]) == 2
    assert len(segmenter.calls) == 2
    for page_id in envelope["data"]["created"]:
        assert (api.catalog.pages_dir / f"{page_id}.json").is_file()


def test_an_unreadable_page_file_is_not_overwritten_and_no_photo_is_written(tmp_path):
    """页文件在却读不了 = 真矛盾：**不覆盖**、也不写照片（先留证据，同回填那条纪律）。"""
    blob, _ = a_photo(tmp_path)
    page_id = hashlib.sha256(blob).hexdigest()[:12]
    api = build_api(tmp_path, segmenter=static_segmenter(TWO_BLOCKS), make_pages=True)
    (api.catalog.pages_dir / f"{page_id}.json").write_bytes(b"{ not json")

    status, envelope = post_page(api, blob)

    assert status == 200
    assert envelope["data"]["created"] == []
    row = envelope["data"]["pages"][0]
    assert row["created"] is False and row["segmentation"] == "unreadable_page_file"
    assert "page_file_unreadable" in {w["code"] for w in envelope["warnings"]}
    assert (api.catalog.pages_dir / f"{page_id}.json").read_bytes() == b"{ not json"
    assert not (api.catalog.pages_dir / f"{page_id}.png").exists(), "坏页文件旁边不许写照片"


def test_a_non_png_photo_is_stored_but_the_ink_statistics_say_they_could_not_run(tmp_path):
    """不是 PNG（手机原图可能是 JPEG）→ 收得下、页建得起，但红笔统计**做不了**。

    这一档必须是「待定」（`keep: null`）而不是「没有红笔」（`keep: false`）——
    「不知道」与「没有」是两件事（#11/#12 同一条纪律）。
    """
    from server import intake

    api = build_api(tmp_path, segmenter=static_segmenter(TWO_BLOCKS))

    status, envelope = post_page(api, b"\xff\xd8\xff\xe0-not-a-png", name="page.jpg")

    assert status == 200
    page_id = envelope["data"]["created"][0]
    saved = json.loads((api.catalog.pages_dir / f"{page_id}.json").read_text(encoding="utf-8"))
    assert saved["image"] == f"{page_id}.jpg"
    assert all(b["keep"] is None for b in saved["blocks"])
    assert all(b["bbox_px"] is None for b in saved["blocks"])
    assert all(b["decision"]["rule"] == intake.RULE_INK_UNKNOWN for b in saved["blocks"])
    assert {w["code"] for w in envelope["warnings"]} >= {
        "intake_page_image_unreadable", "intake_block_ink_unknown"}


def test_a_str_body_is_a_400_not_a_500(tmp_path):
    """body 是 str（不是 bytes）也要走 400：D1 不许用兜底 500 表达输入不对。"""
    api = build_api(tmp_path, segmenter=static_segmenter(TWO_BLOCKS))

    response = api.handle("POST", "/api/page", "这不是字节",
                          content_type="multipart/form-data; boundary=----x")
    envelope = json.loads(response.body)

    assert response.status == 400
    assert envelope["error"]["code"] == "bad_request"
    assert not api.catalog.pages_dir.exists()


# ------------------------------------------------ 对账接进生产路径（R1：不许静默丢题）
#
# spec #2：「对账必须是确定性的，这是把『静默丢题』变成响声的唯一办法。」
# 所以「建」这条路上必须真的跑 `segmentation.reconcile` 的三条判据，并把结论
# 结构化放进响应（`checks` + `reconciliation`，警告走 `warnings`）。

RECONCILE_CODES = {
    "question_number_gap", "question_number_duplicate", "question_number_missing",
    "block_overlap", "block_without_box", "block_not_an_object",
    "page_ink_uncovered", "page_ink_draft_excluded", "page_ink_invalid",
    "coverage_not_checked",
}


def reconcile_warnings(envelope):
    return [w for w in envelope["warnings"] if w["code"] in RECONCILE_CODES]


def test_a_normal_page_reconciles_completely_and_quietly(tmp_path):
    """**阴性对照**：正常页（题号连续、块不重叠、墨迹都被框住）一条响声都不许有。

    这三条新接线最容易的失败模式是给每一页加一句「我没查」——那比不接还坏：
    它会训练人忽略体检（`proto/server.py:1046-1047`）。
    """
    blob, _ = a_photo(tmp_path)
    api = build_api(tmp_path, segmenter=static_segmenter(TWO_BLOCKS))

    _, envelope = post_page(api, blob)

    row = envelope["data"]["pages"][0]
    assert reconcile_warnings(envelope) == []
    assert row["checks"]["coverage"]["checked"] is True, "覆盖率这一条真的跑了"
    assert row["checks"]["coverage"]["ok"] is True
    assert row["checks"]["question_numbers"]["gaps"] == []
    assert row["checks"]["overlaps"]["pairs"] == []
    assert row["reconciliation"] == {
        "blocks": 2, "checks_run": ["question_numbers", "overlaps", "coverage"],
        "checks_skipped": [], "alarms": [], "ok": True, "complete": True,
    }


def test_create_shouts_when_the_model_skips_a_question_number(tmp_path):
    """模型报出 17、19 却没有 18 → 建页当场报警（「漏了一题」最便宜的探测器）。"""
    blob, _ = a_photo(tmp_path)
    blocks = [
        {"id": "b1", "bbox_norm": [0.0, 0.0, 0.4, 1 / 3], "question_no": 17},
        {"id": "b2", "bbox_norm": [0.6, 0.0, 0.4, 1 / 3], "question_no": 19},
    ]
    api = build_api(tmp_path, segmenter=static_segmenter(blocks))

    _, envelope = post_page(api, blob)

    assert "question_number_gap" in {w["code"] for w in envelope["warnings"]}
    row = envelope["data"]["pages"][0]
    assert row["checks"]["question_numbers"]["gaps"] == [18]
    assert row["reconciliation"]["ok"] is False
    assert "question_number_gap" in row["reconciliation"]["alarms"]


def test_create_shouts_when_two_cut_blocks_overlap(tmp_path):
    """两块边界相交（切重了）→ 建页当场报警。"""
    blob, _ = a_photo(tmp_path)
    blocks = [
        {"id": "b1", "bbox_norm": [0.0, 0.0, 0.5, 1 / 3], "question_no": 1},
        {"id": "b2", "bbox_norm": [0.4, 0.0, 0.5, 1 / 3], "question_no": 2},
    ]
    api = build_api(tmp_path, segmenter=static_segmenter(blocks))

    _, envelope = post_page(api, blob)

    assert "block_overlap" in {w["code"] for w in envelope["warnings"]}
    assert envelope["data"]["pages"][0]["checks"]["overlaps"]["pairs"]


def test_create_shouts_about_a_large_patch_of_ink_no_block_covers(tmp_path):
    """下半页一大片墨迹（2000px）没被任何块框住 → 覆盖率这一条当场报警。

    正是 spec #2 那条实测锚点：`1-0000.png` 框下方 6158 像素墨迹全是手写解答
    （`docs/acceptance-log.md:75-83`）。
    """
    img = white_with_blocks(100, 60, [(0, 40, 100, 60, (20, 20, 20))])
    blob = ink.encode_png(img)
    blocks = [{"id": "b1", "bbox_norm": [0.0, 0.0, 1.0, 0.5], "question_no": 1}]
    api = build_api(tmp_path, segmenter=static_segmenter(blocks))

    _, envelope = post_page(api, blob)

    codes = {w["code"] for w in envelope["warnings"]}
    assert "page_ink_uncovered" in codes
    row = envelope["data"]["pages"][0]
    assert row["checks"]["coverage"]["ok"] is False
    (miss,) = row["checks"]["coverage"]["uncovered"]
    assert miss["px"] == 2000 and miss["alarm"] is True
    # 页还是建起来了——对账只报不改（切分仍由人确认）
    assert row["created"] is True
    assert (api.catalog.pages_dir / f"{row['page_id']}.json").is_file()

