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


def data_root(tmp_path, *, make_pages: bool = False, vocab: bool = True):
    root = tmp_path / "data"
    (root / "problems").mkdir(parents=True, exist_ok=True)
    (root / "assets").mkdir(parents=True, exist_ok=True)
    if make_pages:
        (root / "pages").mkdir(parents=True, exist_ok=True)
    if vocab:
        # 科目词表：科目在**录入时**由人指定，而取值必须来自这份表。
        # 默认给一份，这些测试才跑在「真实的目录形状」上；`vocab=False` 专门造
        # 「词表本身不在」那一档（那时是**先收下再喊**，不是 400）。
        (root / "vocab").mkdir(parents=True, exist_ok=True)
        (root / "vocab" / "subjects.json").write_text(
            json.dumps({"科目": ["数学", "物理"]}, ensure_ascii=False), encoding="utf-8")
    return root


def build_api(tmp_path, *, segmenter=None, make_pages: bool = False, vocab: bool = True):
    from server.http import Api

    return Api(data_root(tmp_path, make_pages=make_pages, vocab=vocab), segmenter=segmenter)


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


def post_page(api, blob: bytes, name: str = "page.png", fields=None):
    body, content_type = multipart_body([(name, blob)], fields=fields)
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


def test_without_a_segmenter_the_page_is_still_created_so_a_human_can_draw(tmp_path):
    """切分不可用 → 页**照建**、`blocks` 给 `null`（不是 `[]`），人可以在照片上自己画框。

    这是一次**有意的反转**（#23）：以前这里是不建页、照片也不落盘，理由是
    「`[]` 会被读成『这一页没有题』」——那条理由今天仍然成立，所以 `blocks` 仍是 `null`。
    变的是后半句：切分不可用时最该发生的事就是让人自己画，而页文件不建、照片不落盘，
    人就没有东西可画。那条纪律于是从「防误读」变成了「挡住唯一的出路」。
    """
    blob, _ = a_photo(tmp_path)
    api = build_api(tmp_path)

    status, envelope = post_page(api, blob)

    assert status == 200
    row = envelope["data"]["pages"][0]
    assert row["created"] is True and row["blocks"] is None
    assert row["segmentation"] == "unavailable"
    assert envelope["data"]["segmentation"]["available"] is False
    assert "segmentation_not_implemented" in {w["code"] for w in envelope["warnings"]}

    # 照片与页文件都真的在盘上——人要有东西可画
    assert len(list(api.catalog.pages_dir.glob("*.png"))) == 1
    on_disk = json.loads((api.catalog.pages_dir / f"{row['page_id']}.json").read_text("utf-8"))
    assert on_disk["blocks"] is None
    assert on_disk["segmentation"]["mode"] == "unavailable"
    assert on_disk["segmentation"]["note"]


def test_a_segmentation_that_cannot_be_parsed_is_not_an_empty_page(tmp_path):
    """模型答了话但抠不出块 → 这是「切分没跑成」，**不是**「这一页没有题」。

    判据是 `blocks is None`（不是 `[]`）与 `segmentation.mode == "unavailable"`；
    页**照建**（#23），因为模型切坏时人的出路正是自己画框。
    """
    blob, _ = a_photo(tmp_path)
    api = build_api(tmp_path, segmenter=lambda path: "这一页我看不清，就不给 JSON 了")

    status, envelope = post_page(api, blob)

    assert status == 200
    row = envelope["data"]["pages"][0]
    assert row["blocks"] is None and row["segmentation"] == "unparsed"
    assert row["message"]
    assert "page_segmentation_unparsed" in {w["code"] for w in envelope["warnings"]}
    on_disk = json.loads((api.catalog.pages_dir / f"{row['page_id']}.json").read_text("utf-8"))
    assert on_disk["blocks"] is None, "不许拿 `[]` 冒充「这一页没有题」"
    assert on_disk["segmentation"]["mode"] == "unavailable"
    # 那一句「为什么没有块」要留在页文件上：下一个人看页文件时才知道机器试过了
    assert "抠不出块" in on_disk["segmentation"]["note"]


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



def test_create_says_which_ink_it_excluded_as_a_full_page_draft(tmp_path):
    """R3/D9：`page_ink_draft_excluded`（hint）——按启发式排除了「整页草稿式手写」。

    排除**必须看得见**：`checks.coverage.excluded` 里带 `reason` 与像素数——
    「排除了什么」是覆盖率这条判据可信度的一部分（spec #2 点名这一步是启发式）。
    """
    img = white_with_blocks(100, 60, [(1, 1, 99, 59, (20, 20, 20))])
    blocks = [{"id": "b1", "bbox_norm": [0.0, 0.0, 1.0, 0.5], "question_no": 1}]
    api = build_api(tmp_path, segmenter=static_segmenter(blocks))

    _, envelope = post_page(api, ink.encode_png(img))

    hits = [w for w in envelope["warnings"] if w["code"] == "page_ink_draft_excluded"]
    assert len(hits) == 1, envelope["warnings"]
    assert hits[0]["level"] == "hint", "排除是启发式，不是真矛盾"
    assert hits[0]["id"] is None
    assert "排除" in hits[0]["message"] and "启发式" in hits[0]["message"]
    coverage = envelope["data"]["pages"][0]["checks"]["coverage"]
    assert coverage["checked"] is True
    (excluded,) = coverage["excluded"]
    assert excluded["reason"] == "page_span"
    assert excluded["px"] == 98 * 58
    assert coverage["ok"] is True, "排除之后没有可对账的墨迹——这不是「没覆盖」"


def test_every_warning_the_create_path_emits_carries_an_explicit_level(tmp_path):
    """R1：新接的对账警告也要带显式 `level`（契约 §2）——整封信封逐条走一遍。

    逐条断言只有「我想到的那几条」；这条**遍历信封**，所以将来新加的码漏了 `level`
    会当场红（`level` 有默认值却不出现在响应里，界面只能靠猜）。
    """
    blob, _ = a_photo(tmp_path)
    blocks = [{"id": "b1", "bbox_norm": [0.0, 0.0, 0.4, 1 / 3], "question_no": 17},
              {"id": "b2", "bbox_norm": [0.6, 0.0, 0.4, 1 / 3], "question_no": 19}]
    api = build_api(tmp_path, segmenter=static_segmenter(blocks))

    _, envelope = post_page(api, blob)

    assert envelope["warnings"], "这组夹具本来就该有警告，否则这条测试空转"
    assert all(w.get("level") in ("warning", "hint") for w in envelope["warnings"]), \
        envelope["warnings"]
    assert {w["level"] for w in envelope["warnings"]} == {"warning", "hint"}, \
        "两档都要出现过（题号缺口是 warning、没问模型是 hint），这条断言才不空转"


# ------------------------------------------------- 科目在录入时由人指定（#17）


def test_the_create_response_echoes_the_subject_it_accepted(tmp_path):
    """回执要**回显**科目。不回显，「我填的科目到底进没进去」只能靠再打一次索引去猜。

    没给这个字段时回显 `null`：那是**未归类**（一等状态），不是缺字段。
    """
    api = build_api(tmp_path, segmenter=static_segmenter(TWO_BLOCKS))

    blob, _ = a_photo(tmp_path)
    status, envelope = post_page(api, blob, fields=[("subject", "数学")])
    assert status == 200
    assert envelope["data"]["pages"][0]["subject"] == "数学"

    # 换一张尺寸不同的图（内容哈希不同 → 是另一页），这次不给科目
    other, _ = a_photo(tmp_path, w=120)
    status, envelope = post_page(api, other, name="other.png")
    assert status == 200
    assert envelope["data"]["pages"][0]["subject"] is None


def test_a_subject_outside_the_vocabulary_is_refused_with_the_allowed_list(tmp_path):
    """词表在而取值不在 → **400 带可选值**，而且**一个字节都不写**（拒绝路径不动盘）。

    受控词表那条纪律（`CONTEXT.md`：AI 不得自造标签，找不到只能提名）在这里落在**人**身上：
    让一个表外的科目在侧栏里长出一根孤立分支，比当场拒掉更难收拾。
    """
    api = build_api(tmp_path, segmenter=static_segmenter(TWO_BLOCKS))
    blob, _ = a_photo(tmp_path)

    status, envelope = post_page(api, blob, fields=[("subject", "化学")])

    assert status == 400
    assert envelope["ok"] is False
    details = envelope["error"]["details"]
    assert details["param"] == "subject"
    assert details["value"] == "化学"
    assert set(details["allowed"]) == {"数学", "物理"}
    assert envelope["error"]["hint"], "拒绝要告诉人怎么办（改成表里的一个，或者不给）"
    # 拒绝路径一个字节都不动：连 pages/ 都不该有东西
    assert list(api.catalog.pages_dir.glob("*")) == [] if api.catalog.pages_dir.exists() else True


def test_a_missing_vocabulary_does_not_block_entry_but_shouts(tmp_path):
    """**词表本身不在**时：收下这个科目，但把词表级警告一并带回。

    不许因为词表缺席就把录入整个挡住——录入摩擦是这类工具的头号死因
    （ADR 0006 决定第 5 条）。代价是拼错的科目可能积下来，那正是索引级
    `subjects_vocab_missing` 要喊的事。
    """
    api = build_api(tmp_path, segmenter=static_segmenter(TWO_BLOCKS), vocab=False)
    blob, _ = a_photo(tmp_path)

    status, envelope = post_page(api, blob, fields=[("subject", "数学")])

    assert status == 200
    assert envelope["data"]["pages"][0]["subject"] == "数学"
    assert "subjects_vocab_missing" in {w["code"] for w in envelope["warnings"]}, \
        "词表不在必须喊出来——否则侧栏会空掉而没人知道为什么"
