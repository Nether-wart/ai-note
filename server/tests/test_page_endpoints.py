"""页资源的四个 HTTP 动作（契约 §10.2、工单 #14）。

spec #2 把页资源的**四个动作**定成一个接缝：「界面的所有读写都落在这个页资源上，
**模型调用与图像统计都在服务内部，界面不碰照片处理**」。

| 动作 | 路由 | 谁实现 |
|---|---|---|
| 建 | `POST /api/page` | 照片 → 存图 + 建页文件 + 跑切分 → 返回块列表（#10 的接缝 + #13 的收件管道） |
| 改 | `PATCH /api/page/<id>` | 边界／合并／拆分／去留／题型／题号（#14，`server/page_edit.py`） |
| 重切 | `POST /api/page/<id>/resegment` | 跑切分 → 逐块**新增／保留**对照，**不写题卡**（#10 的 `classify_resegment`，本工单接线） |
| 入库 | `POST /api/page/<id>/commit` | 按当前块列表生成题卡并回写绑定（#15，本工单只接线到「还没实现」的显式形状） |

这个文件测**外部行为**：HTTP 请求进、信封出、盘上的页文件变/没变。
模型调用一律注入假切分器；测试**不联网、不碰真照片**。
"""

from __future__ import annotations

import json

import pytest

PAGE_ID = "41c86bcfc007"


def build_api(tmp_path, *, pages=(), segmenter=None, semantics=None, cards=(), **kwargs):
    """一个指向临时数据目录的 Api，页文件在它下面（绝不碰真数据）。"""
    from server.http import Api

    root = tmp_path / "data"
    (root / "problems").mkdir(parents=True, exist_ok=True)
    (root / "assets").mkdir(parents=True, exist_ok=True)
    (root / "pages").mkdir(parents=True, exist_ok=True)
    for card in cards:
        (root / "problems" / f"{card['id']}.json").write_text(
            json.dumps(card, ensure_ascii=False), encoding="utf-8")
    for pg in pages:
        (root / "pages" / f"{pg['id']}.json").write_text(
            json.dumps(pg, ensure_ascii=False, indent=2), encoding="utf-8")
    extra = {}
    if segmenter is not None:
        extra["segmenter"] = segmenter
    if semantics is not None:
        extra["semantics"] = semantics
    return Api(root, **extra, **kwargs)


def block(block_id, box, **overrides):
    out = {"id": block_id, "bbox_norm": box, "bbox_px": None, "card_id": None, "keep": None}
    out.update(overrides)
    return out


def page(blocks, page_id=PAGE_ID, **overrides):
    out = {
        "version": 1,
        "id": page_id,
        "image": f"{page_id}.png",
        "created_at": "2026-10-04T14:31:35+08:00",
        "origin": {"original_file": "2.png", "sheet": None, "page_number": None},
        "blocks": blocks,
    }
    out.update(overrides)
    return out


def patch_json(api, target, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    r = api.handle("PATCH", target, body, content_type="application/json")
    return r.status, json.loads(r.body)


def post_json(api, target, payload=None):
    body = b"" if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    r = api.handle("POST", target, body, content_type="application/json")
    return r.status, json.loads(r.body)


# ---------------------------------------------------------------- 改（PATCH）


def test_patching_a_page_applies_the_edits_and_writes_the_file(tmp_path):
    """**验收 3 的 HTTP 面**：修正一次 → 200 + 信封 → 盘上的页文件真的变了。"""
    api = build_api(tmp_path, pages=[page([block("b1", [0.02, 0.02, 0.9, 0.2], question_no=1)])])

    status, envelope = patch_json(api, f"/api/page/{PAGE_ID}", {
        "edits": [
            {"action": "move", "block_id": "b1", "bbox_norm": [0.0, 0.0, 1.0, 0.25]},
            {"action": "question_no", "block_id": "b1", "question_no": 17},
        ]})

    assert status == 200
    assert envelope["ok"] is True
    assert envelope["data"]["changed"] is True
    on_disk = json.loads((api.catalog.pages_dir / f"{PAGE_ID}.json").read_text("utf-8"))
    assert on_disk["blocks"][0]["bbox_norm"] == [0.0, 0.0, 1.0, 0.25]
    assert on_disk["blocks"][0]["question_no"] == 17


def test_patching_with_dry_run_writes_nothing(tmp_path):
    """预演（`dry_run`）：报告说得出会改成什么，盘上一个字节都不动。"""
    api = build_api(tmp_path, pages=[page([block("b1", [0.02, 0.02, 0.9, 0.2])])])
    before = (api.catalog.pages_dir / f"{PAGE_ID}.json").read_bytes()

    status, envelope = patch_json(api, f"/api/page/{PAGE_ID}", {
        "dry_run": True,
        "edits": [{"action": "move", "block_id": "b1", "bbox_norm": [0.0, 0.0, 1.0, 0.3]}]})

    assert status == 200
    assert (api.catalog.pages_dir / f"{PAGE_ID}.json").read_bytes() == before
    assert envelope["data"]["preview"] is True
    assert envelope["data"]["page"]["blocks"][0]["bbox_norm"] == [0.0, 0.0, 1.0, 0.3]


def test_an_unknown_edit_action_is_a_400_that_lists_the_allowed_ones(tmp_path):
    """D9：不认识的修正动作 → **400** 带 `reason`/`details.allowed`，不是裸 500，也不是静默忽略。"""
    api = build_api(tmp_path, pages=[page([block("b1", [0.02, 0.02, 0.9, 0.2])])])
    before = (api.catalog.pages_dir / f"{PAGE_ID}.json").read_bytes()

    status, envelope = patch_json(api, f"/api/page/{PAGE_ID}", {
        "edits": [{"action": "rotate", "block_id": "b1"}]})

    assert status == 400
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "bad_request"
    assert envelope["error"]["reason"] == "bad_request"
    assert "move" in envelope["error"]["details"]["allowed"]
    assert (api.catalog.pages_dir / f"{PAGE_ID}.json").read_bytes() == before


def test_a_page_id_that_could_escape_the_pages_dir_is_a_400(tmp_path):
    """页 id 会拼进路径 → 校验必须在碰盘**之前**（#12 收的穿越缺口同一份 `is_page_id`）。

    拒绝时 `pages/` 外面不许出现任何文件（D9 第 2 条：拒绝就该一个字节都不动）。
    """
    api = build_api(tmp_path)

    status, envelope = patch_json(api, "/api/page/../../problems/p-x", {
        "edits": [{"action": "drop", "block_id": "b1"}]})

    assert status == 400
    assert envelope["error"]["details"]["param"] == "page_id"
    assert list(api.catalog.pages_dir.glob("*.json")) == []


def test_patching_a_page_that_is_not_there_is_a_404(tmp_path):
    """D9：点名要一页，找不到就得说（404），形状与 #12 的 `_load_page` 一致。"""
    api = build_api(tmp_path, pages=[page([block("b1", [0.02, 0.02, 0.9, 0.2])])])

    status, envelope = patch_json(api, "/api/page/nosuchpage", {
        "edits": [{"action": "drop", "block_id": "b1"}]})

    assert status == 404
    assert envelope["error"]["code"] == "not_found"


def test_a_body_without_an_edits_list_is_a_400_naming_the_parameter(tmp_path):
    """D9：`edits` 不是一个列表 → 400 并点名参数（不是静默当作「0 条修正」）。"""
    api = build_api(tmp_path, pages=[page([block("b1", [0.02, 0.02, 0.9, 0.2])])])

    status, envelope = patch_json(api, f"/api/page/{PAGE_ID}", {"edits": "把 b1 拖一下"})

    assert status == 400
    assert envelope["error"]["details"]["param"] == "edits"


def test_a_body_that_is_not_json_at_all_is_a_400_not_a_500(tmp_path):
    """D9：不是 JSON 的 body 是 400（输入不对），绝不是裸 500。"""
    api = build_api(tmp_path, pages=[page([block("b1", [0.02, 0.02, 0.9, 0.2])])])

    r = api.handle("PATCH", f"/api/page/{PAGE_ID}", b"not json at all",
                   content_type="application/json")
    envelope = json.loads(r.body)

    assert r.status == 400
    assert envelope["error"]["code"] == "bad_request"
    assert envelope["error"]["reason"] == "bad_request"


def test_the_page_route_only_answers_the_methods_it_declares(tmp_path):
    """用错方法 → 405 带 `details.allowed`（契约 §5.1），不是含糊的 404。"""
    api = build_api(tmp_path, pages=[page([block("b1", [0.02, 0.02, 0.9, 0.2])])])

    r = api.handle("DELETE", f"/api/page/{PAGE_ID}")
    envelope = json.loads(r.body)

    assert r.status == 405
    assert envelope["error"]["code"] == "method_not_allowed"
    assert "PATCH" in envelope["error"]["details"]["allowed"]


# ---------------------------------------------------------------- 重切（resegment）


def test_resegment_returns_the_three_state_reconciliation_without_writing_cards(tmp_path):
    """**验收 1**：重切返回**逐块对照**（新增／保留），且**不写题卡、不写页文件**。

    界面要能显示这三态，而不是只显示「重切完成」——所以对照在 `data.matches` 里，
    `summary.replaced` 恒为 0（#10 已裁决是结构性的，不是「还没做」）。
    """
    existing = page([block("b1", [0.02, 0.02, 0.9, 0.2], card_id="p-20261004-aaaaaa",
                           keep=True, question_no=1)])
    def segmenter(image_path):
        return {
            "blocks": [
                {"id": "b1", "bbox_norm": [0.02, 0.02, 0.9, 0.2], "question_no": 1},
                {"id": "b2", "bbox_norm": [0.02, 0.24, 0.9, 0.2], "question_no": 2},
            ],
            "parsed": True, "rejected": [], "warnings": [],
        }

    api = build_api(tmp_path, pages=[existing], segmenter=segmenter)
    before = (api.catalog.pages_dir / f"{PAGE_ID}.json").read_bytes()

    status, envelope = post_json(api, f"/api/page/{PAGE_ID}/resegment")

    assert status == 200
    data = envelope["data"]
    assert [m["state"] for m in data["matches"]] == ["kept", "new"]
    assert data["summary"] == {"kept": 1, "new": 1, "replaced": 0, "removed": 0,
                               "needs_human": False, "human_work_checked": False}
    assert data["wrote_cards"] is False and data["wrote_page"] is False
    # 已入库的那一块**卡片 id 不变**（验收 1 的后半）
    assert data["blocks"][0]["card_id"] == "p-20261004-aaaaaa"
    assert data["blocks"][1]["card_id"] is None
    assert (api.catalog.pages_dir / f"{PAGE_ID}.json").read_bytes() == before


def test_resegment_shouts_when_a_block_with_a_card_would_disappear(tmp_path):
    """D9：旧块带着卡却在新切分里找不到位置 → 必须喊（卡片不会被自动抹掉）。"""
    existing = page([block("b1", [0.02, 0.02, 0.9, 0.2], card_id="p-20261004-aaaaaa")])
    def segmenter(image_path):
        return {"blocks": [{"id": "b1", "bbox_norm": [0.6, 0.7, 0.3, 0.2],
                            "question_no": 9}],
                "parsed": True, "rejected": [], "warnings": []}

    api = build_api(tmp_path, pages=[existing], segmenter=segmenter)

    status, envelope = post_json(api, f"/api/page/{PAGE_ID}/resegment")

    assert status == 200
    assert envelope["data"]["summary"]["removed"] == 1
    assert envelope["data"]["summary"]["needs_human"] is True
    codes = [w["code"] for w in envelope["warnings"]]
    assert "block_removed_with_card" in codes
    assert all(w["level"] in ("hint", "warning") for w in envelope["warnings"])


def test_resegment_when_segmentation_is_unavailable_says_so_and_writes_nothing(tmp_path):
    """**不注入切分器 = 切分不可用**（#10 的接缝口径）：显式报出来，绝不编一个块列表。

    这也不是 404——路由在、页在，是**这一趟没跑成**。
    """
    existing = page([block("b1", [0.02, 0.02, 0.9, 0.2], card_id="p-20261004-aaaaaa")])
    api = build_api(tmp_path, pages=[existing])          # segmenter=None
    before = (api.catalog.pages_dir / f"{PAGE_ID}.json").read_bytes()

    status, envelope = post_json(api, f"/api/page/{PAGE_ID}/resegment")

    assert status == 200
    assert envelope["data"]["segmentation"] == "unavailable"
    assert envelope["data"]["ran"] is False
    assert envelope["data"]["blocks"] is None          # **不是 `[]`**：没切 ≠ 切出 0 块
    assert "segmentation_not_implemented" in [w["code"] for w in envelope["warnings"]]
    assert (api.catalog.pages_dir / f"{PAGE_ID}.json").read_bytes() == before


def test_resegment_of_a_page_that_is_not_there_is_a_404(tmp_path):
    """D9：重切一个不存在的页 → 404。"""
    api = build_api(tmp_path)

    status, envelope = post_json(api, "/api/page/nosuchpage/resegment")

    assert status == 404
    assert envelope["error"]["code"] == "not_found"


def test_a_segmenter_that_fails_is_a_502_and_leaves_the_page_untouched(tmp_path):
    """D1：模型没问成 → **502 `model_unavailable`**，页文件一个字节都不动。

    「没能问成」与「一次回答」必须分开（#12 的口径）：上游失败可以重试，
    而且它不许留下任何半截结果。
    """
    from server.model_client import ModelUnavailable

    existing = page([block("b1", [0.02, 0.02, 0.9, 0.2], card_id="p-20261004-aaaaaa")])
    def broken(image_path):
        raise ModelUnavailable("上游 402：余额不足")

    api = build_api(tmp_path, pages=[existing], segmenter=broken)
    before = (api.catalog.pages_dir / f"{PAGE_ID}.json").read_bytes()

    status, envelope = post_json(api, f"/api/page/{PAGE_ID}/resegment")

    assert status == 502
    assert envelope["error"]["code"] == "model_unavailable"
    assert envelope["error"]["reason"] == "model_unavailable"
    # 「哪一页的切分没跑成」要在形状里（不许含糊）
    assert envelope["error"]["details"]["id"] == PAGE_ID
    assert "重试" in envelope["error"]["hint"]
    assert (api.catalog.pages_dir / f"{PAGE_ID}.json").read_bytes() == before


# ------------------------------------------------ 对账接进生产路径（R1）
#
# 重切这条路上也要跑 `segmentation.reconcile` 的三条判据（题号连续性／块重叠／覆盖率），
# 结论结构化放进响应。覆盖率要读**页文件旁边那张整页照片**（D5：照片与页文件并列）。

UPPER = {"id": "b1", "bbox_norm": [0.0, 0.0, 1.0, 0.5], "question_no": 1}


def a_black_patch_page(api, patches):
    """把一张合成整页照片写到页文件旁边（与页文件同名不同后缀，D5）。"""
    from server import ink
    from test_ink import white_with_blocks

    api.catalog.pages_dir.mkdir(parents=True, exist_ok=True)
    (api.catalog.pages_dir / f"{PAGE_ID}.png").write_bytes(
        ink.encode_png(white_with_blocks(100, 60, patches)))


def test_resegment_reconciles_the_new_blocks_and_is_quiet_when_all_is_well(tmp_path):
    """**阴性对照**：题号连续、块不重叠、墨迹都被框住 → 重切一条响声都没有。"""
    existing = page([block("b1", [0.0, 0.0, 0.4, 1 / 3], card_id="p-20261004-aaaaaa",
                           keep=True, question_no=17)])
    def segmenter(image_path):
        return {"blocks": [
            {"id": "b1", "bbox_norm": [0.0, 0.0, 0.4, 1 / 3], "question_no": 17},
            {"id": "b2", "bbox_norm": [0.6, 0.0, 0.4, 1 / 3], "question_no": 18},
        ], "parsed": True, "rejected": [], "warnings": []}

    api = build_api(tmp_path, pages=[existing], segmenter=segmenter)
    a_black_patch_page(api, [(0, 0, 40, 20, (20, 20, 20)), (60, 0, 100, 20, (20, 20, 20))])

    status, envelope = post_json(api, f"/api/page/{PAGE_ID}/resegment")

    assert status == 200, envelope
    data = envelope["data"]
    assert envelope["warnings"] == []
    assert data["checks"]["coverage"]["checked"] is True
    assert data["checks"]["coverage"]["ok"] is True
    assert data["reconciliation"]["ok"] is True
    assert data["reconciliation"]["complete"] is True


def test_resegment_shouts_about_ink_that_no_new_block_covers(tmp_path):
    """新切分的块只框住上半页，下半页那片墨迹没人框 → 覆盖率当场报警（只报不改）。"""
    existing = page([block("b1", [0.0, 0.0, 1.0, 0.5], card_id="p-20261004-aaaaaa",
                           keep=True, question_no=1)])
    api = build_api(tmp_path, pages=[existing],
                    segmenter=lambda path: {"blocks": [dict(UPPER)], "parsed": True,
                                            "rejected": [], "warnings": []})
    a_black_patch_page(api, [(0, 40, 100, 60, (20, 20, 20))])
    before = (api.catalog.pages_dir / f"{PAGE_ID}.json").read_bytes()

    status, envelope = post_json(api, f"/api/page/{PAGE_ID}/resegment")

    assert status == 200, envelope
    assert "page_ink_uncovered" in [w["code"] for w in envelope["warnings"]]
    assert envelope["data"]["checks"]["coverage"]["ok"] is False
    assert envelope["data"]["reconciliation"]["alarms"] == ["page_ink_uncovered"]
    # 对账只报不改：页文件一个字节都没动
    assert (api.catalog.pages_dir / f"{PAGE_ID}.json").read_bytes() == before


def test_resegment_shouts_when_the_new_cut_skips_a_question_number(tmp_path):
    """新切分报出 17、19 却没有 18 → 重切当场报警（缺口在 `checks` 里点名）。"""
    existing = page([block("b1", [0.0, 0.0, 1.0, 0.5], card_id="p-20261004-aaaaaa",
                           keep=True, question_no=17)])
    def segmenter(image_path):
        return {"blocks": [
            {"id": "b1", "bbox_norm": [0.0, 0.0, 1.0, 0.5], "question_no": 17},
            {"id": "b2", "bbox_norm": [0.0, 0.5, 1.0, 0.5], "question_no": 19},
        ], "parsed": True, "rejected": [], "warnings": []}

    api = build_api(tmp_path, pages=[existing], segmenter=segmenter)

    status, envelope = post_json(api, f"/api/page/{PAGE_ID}/resegment")

    assert status == 200, envelope
    assert "question_number_gap" in [w["code"] for w in envelope["warnings"]]
    assert envelope["data"]["checks"]["question_numbers"]["gaps"] == [18]


def test_resegment_shouts_when_two_new_blocks_overlap(tmp_path):
    """两块新边界相交（切重了）→ 重切当场报警。"""
    existing = page([block("b1", [0.0, 0.0, 0.5, 0.5], card_id="p-20261004-aaaaaa",
                           keep=True, question_no=1)])
    def segmenter(image_path):
        return {"blocks": [
            {"id": "b1", "bbox_norm": [0.0, 0.0, 0.5, 0.5], "question_no": 1},
            {"id": "b2", "bbox_norm": [0.4, 0.0, 0.5, 0.5], "question_no": 2},
        ], "parsed": True, "rejected": [], "warnings": []}

    api = build_api(tmp_path, pages=[existing], segmenter=segmenter)

    status, envelope = post_json(api, f"/api/page/{PAGE_ID}/resegment")

    assert status == 200, envelope
    assert "block_overlap" in [w["code"] for w in envelope["warnings"]]
    assert envelope["data"]["checks"]["overlaps"]["pairs"]


def test_resegment_names_a_card_file_it_could_not_read(tmp_path):
    """R4/D9：盘上有一张读不了的卡 → 重切**点名报出来**（§8 `card_file_unreadable`）。

    修前 `_cards()` 里 `except Exception: continue` 把它丢掉，于是「人动过的卡不许被
    重切改写」这条对账**查不全却不记账**——读不了的那张可能正是人动过的那张。
    """
    existing = page([block("b1", [0.0, 0.0, 1.0, 0.5], card_id="p-20261004-aaaaaa",
                           keep=True, question_no=1)])
    api = build_api(tmp_path, pages=[existing],
                    segmenter=lambda path: {"blocks": [dict(UPPER)], "parsed": True,
                                            "rejected": [], "warnings": []})
    (api.catalog.problems_dir / "p-20261004-zzzzzz.json").write_bytes(b"{ not json")

    status, envelope = post_json(api, f"/api/page/{PAGE_ID}/resegment")

    assert status == 200, envelope
    hits = [w for w in envelope["warnings"] if w["code"] == "card_file_unreadable"]
    assert len(hits) == 1, envelope["warnings"]
    # 形状：warning + 文件指针（`id` 是文件名主干，`message` 里带全路径）
    assert hits[0]["level"] == "warning"
    assert hits[0]["id"] == "p-20261004-zzzzzz"
    assert "p-20261004-zzzzzz.json" in hits[0]["message"]
    # 明说这张卡**没进对账**（D1：不许安静地少一张）
    assert "没有进" in hits[0]["message"] and "对账" in hits[0]["message"]


def test_resegment_is_not_confused_by_a_readable_card_next_to_an_unreadable_one(tmp_path):
    """正常卡不受影响：能读的卡照旧进对账、照旧按「人动过」喊。"""
    existing = page([block("b1", [0.0, 0.0, 1.0, 0.5], card_id="p-20261004-aaaaaa",
                           keep=True, question_no=1)])
    reviewed = {"id": "p-20261004-aaaaaa", "review": {"status": "reviewed"}, "attempts": []}
    api = build_api(tmp_path, pages=[existing], cards=[reviewed],
                    segmenter=lambda path: {"blocks": [dict(UPPER)], "parsed": True,
                                            "rejected": [], "warnings": []})
    (api.catalog.problems_dir / "p-20261004-zzzzzz.json").write_bytes(b"{ not json")

    status, envelope = post_json(api, f"/api/page/{PAGE_ID}/resegment")

    assert status == 200, envelope
    codes = [w["code"] for w in envelope["warnings"]]
    assert codes.count("card_file_unreadable") == 1
    assert "resegment_card_human_work" in codes, "能读的那张照样进对账"


# ---------------------------------------------------------------- 入库（commit）与建（POST /api/page）


def test_commit_is_implemented_and_reports_what_it_wrote(tmp_path):
    """**入库已在 #15 落地**：这条路不再给「预留」的 404，而是一份入库报告。

    这一页的块**还没判过去留**（`keep: null`）→ 一块都不入库，而且要说出来
    （`page_commit_nothing_kept`）：「还没判」与「不收」不是一回事，更不许悄悄建卡。
    """
    from server import page_commit

    api = build_api(tmp_path, pages=[page([block("b1", [0.02, 0.02, 0.9, 0.2])])])

    status, envelope = post_json(api, f"/api/page/{PAGE_ID}/commit")

    assert status == 200, envelope
    assert envelope["data"]["created"] == [] and envelope["data"]["reused"] == []
    assert envelope["data"]["wrote_cards"] is False
    assert [row["block_id"] for row in envelope["data"]["skipped"]] == ["b1"]
    assert page_commit.COMMIT_NOTHING_KEPT in {w["code"] for w in envelope["warnings"]}
    assert list(api.catalog.problems_dir.glob("*.json")) == [], "不许悄悄建题卡"


def test_creating_a_page_needs_a_multipart_photo_and_writes_nothing_otherwise(tmp_path):
    """**建也已在 #15 落地**（`server/page_create.py`）：这条路不再是「预留」的 404。

    它在**任何写盘之前**先要一张能读的照片（形状与 `POST /api/inbox` 一致：
    multipart + 字段名 `file`），所以这里给 JSON 得到的是 **400 带 `allowed`**，
    而不是含糊的 404，更不是「其实什么也没做」的 200。
    建页的正路（存图、块列表、幂等、模型失败不留痕迹）在 `test_page_create.py`。
    """
    api = build_api(tmp_path)

    status, envelope = post_json(api, "/api/page", {"image": "x.png"})

    assert status == 400
    assert envelope["error"]["code"] == "bad_request"
    assert envelope["error"]["details"]["allowed"] == ["multipart/form-data"]
    assert list(api.catalog.pages_dir.glob("*.json")) == [], "拒绝路径不许留下页文件"


# ---------------------------------------------------------------- 其他方法


def test_resegment_only_answers_post(tmp_path):
    """重切只收 POST：GET 到它要说清「它收的是 POST」（405 带 allowed）。"""
    api = build_api(tmp_path, pages=[page([block("b1", [0.02, 0.02, 0.9, 0.2])])])

    r = api.handle("GET", f"/api/page/{PAGE_ID}/resegment")
    envelope = json.loads(r.body)

    assert r.status == 405
    assert "POST" in envelope["error"]["details"]["allowed"]


@pytest.mark.parametrize("target,method", [
    (f"/api/page/{PAGE_ID}", "PUT"),
    (f"/api/page/{PAGE_ID}/commit", "GET"),
])
def test_wrong_methods_are_405_with_the_allowed_list(tmp_path, target, method):
    """每一条路由自己声明允许哪些方法（契约 §5.1）。"""
    api = build_api(tmp_path, pages=[page([block("b1", [0.02, 0.02, 0.9, 0.2])])])

    r = api.handle(method, target)
    envelope = json.loads(r.body)

    assert r.status == 405
    assert envelope["error"]["code"] == "method_not_allowed"
    assert envelope["error"]["details"]["allowed"]


# ---------------------------------------------------------------- 整页照片


def test_the_page_image_route_serves_the_photo_next_to_the_page_file(tmp_path):
    """界面要「把块框画在整页照片上」，所以它要取得到那张照片（D5：与页文件并列）。"""
    import base64

    png = base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
    )
    api = build_api(tmp_path, pages=[page([block("b1", [0.02, 0.02, 0.9, 0.2])])])
    (api.catalog.pages_dir / f"{PAGE_ID}.png").write_bytes(png)

    r = api.handle("GET", f"/api/page/{PAGE_ID}/image")

    assert r.status == 200
    assert r.content_type.startswith("image/png")
    assert r.body == png


def test_a_missing_page_photo_is_a_404_that_names_the_file(tmp_path):
    """D9：页在、照片不在是**真矛盾** → 404 说清缺的是哪一张（不给替身图）。"""
    api = build_api(tmp_path, pages=[page([block("b1", [0.02, 0.02, 0.9, 0.2])])])

    r = api.handle("GET", f"/api/page/{PAGE_ID}/image")
    envelope = json.loads(r.body)

    assert r.status == 404
    assert envelope["error"]["code"] == "not_found"
    assert envelope["error"]["details"]["what"] == "page_image"
    assert envelope["error"]["details"]["image"] == f"{PAGE_ID}.png"


def test_the_page_image_route_refuses_a_traversing_image_field(tmp_path):
    """D9：`image` 可穿越 → 必须拒（这是 #12 那条漏洞的派生症状）。"""
    api = build_api(tmp_path, pages=[page([block("b1", [0.02, 0.02, 0.9, 0.2])])])
    (api.catalog.pages_dir / f"{PAGE_ID}.json").write_text(json.dumps({
        "version": 1, "id": PAGE_ID, "image": "../../problems/p-x.json",
        "blocks": [block("b1", [0.02, 0.02, 0.9, 0.2])],
    }, ensure_ascii=False), encoding="utf-8")

    r = api.handle("GET", f"/api/page/{PAGE_ID}/image")
    envelope = json.loads(r.body)

    assert r.status == 400
    assert envelope["error"]["details"]["param"] == "page_id"


def test_the_page_image_route_only_answers_get(tmp_path):
    """用错方法 → 405 带 allowed（契约 §5.1）。"""
    api = build_api(tmp_path, pages=[page([block("b1", [0.02, 0.02, 0.9, 0.2])])])

    r = api.handle("POST", f"/api/page/{PAGE_ID}/image")
    envelope = json.loads(r.body)

    assert r.status == 405
    assert "GET" in envelope["error"]["details"]["allowed"]
