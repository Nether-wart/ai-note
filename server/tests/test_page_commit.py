"""页资源的「入库」动作：按块列表生成题卡 + 回写绑定（工单 #15、spec #2 的第四个动作）。

测试打的是**外部行为**：一个 HTTP 请求进去、一个信封出来、盘上的卡与页文件变/没变。
模型调用一律注入 stub，测试不联网、不碰真数据（夹具全在 pytest 临时目录里）。

三条验收在这里各有落点：

1. **一页收 2 道 → 生成 2 张卡（各带页绑定）**：`test_...two_kept_blocks...`
2. 幂等：已经生成过的块**不重复生成**（`test_committing_twice...`）——这是「入库」动作
   的原话，也是「重切不会给已审核的卡改名」在数据上的落点。
3. **新卡一律未审核 → 不参与自动判定、不进重做纸**：`test_new_cards_are_gated_by_the_one_implementation`
   用的是 `server/autojudge.py` 与 `server/records.py: screen_redo_gate`（#4/#7 的那一份实现，
   本工单不另写判断）。
"""

from __future__ import annotations

import json

from test_page_endpoints import PAGE_ID, block, build_api, page


def commit(api, page_id: str = PAGE_ID):
    """打 `POST /api/page/<id>/commit`，返回 `(状态码, 信封)`。"""
    r = api.handle("POST", f"/api/page/{page_id}/commit")
    return r.status, json.loads(r.body)


def codes(warnings):
    return [w["code"] for w in warnings]


def a_page(blocks, **overrides):
    """一页（照片名与页 id 并列，D5）。"""
    return page(blocks, **overrides)


# ---------------------------------------------------------------- 验收 1：一页两卡


def test_two_kept_blocks_become_two_cards_each_bound_to_the_page(tmp_path):
    """一页收 2 道 → 生成 2 张卡，各带页绑定（页文件的块上有 card_id，卡上不加新字段）。"""
    from server import pages

    api = build_api(tmp_path, pages=[a_page([
        block("b1", [0.02, 0.02, 0.9, 0.2], keep=True, bbox_px=[10, 4, 600, 40]),
        block("b2", [0.02, 0.40, 0.9, 0.2], keep=True, bbox_px=[10, 76, 600, 112]),
    ])])

    status, envelope = commit(api)

    assert status == 200, envelope
    data = envelope["data"]
    assert data["wrote_cards"] is True and data["wrote_page"] is True
    assert [row["block_id"] for row in data["created"]] == ["b1", "b2"]
    created_ids = [row["card_id"] for row in data["created"]]
    assert len(set(created_ids)) == 2, "一页多块各得一个互不相同的 id（#9 验收 3）"

    # 盘上真的有两张卡，而且每张卡都能从页文件推回绑定
    on_disk = sorted(p.stem for p in api.catalog.problems_dir.glob("*.json"))
    assert on_disk == sorted(created_ids)
    for card_id in created_ids:
        card = json.loads((api.catalog.problems_dir / f"{card_id}.json").read_text(encoding="utf-8"))
        assert pages.page_binding(api.catalog, card)["bound"] is True

    # 页文件写回了绑定：块上有 card_id，且 id 与这次分配的一致
    saved = json.loads((api.catalog.pages_dir / f"{PAGE_ID}.json").read_text(encoding="utf-8"))
    assert [b["card_id"] for b in saved["blocks"]] == created_ids


def test_the_card_records_which_page_and_which_block_box_it_came_from(tmp_path):
    """卡上的 `source` 记着整页照片与**块**的边界（两套坐标基准：这里是整页坐标）。"""
    api = build_api(tmp_path, pages=[a_page([
        block("b1", [0.02, 0.12, 0.76, 0.28], keep=True, bbox_px=[3, 20, 541, 79]),
    ])])

    status, envelope = commit(api)
    card_id = envelope["data"]["created"][0]["card_id"]
    card = json.loads((api.catalog.problems_dir / f"{card_id}.json").read_text(encoding="utf-8"))

    assert status == 200
    assert card["source"]["page_image"].endswith(f"{PAGE_ID}.png")
    assert card["source"]["bbox_norm"] == [0.02, 0.12, 0.76, 0.28]
    assert card["source"]["bbox_px"] == [3, 20, 541, 79]
    # 卡上**不加**新字段：绑定只记在页文件里（契约 §10.2.1）
    assert "page_id" not in card and "page_binding" not in card


# ---------------------------------------------------------------- 幂等：不重复生成


def test_committing_twice_creates_nothing_the_second_time_and_changes_no_byte(tmp_path):
    """已经生成过的块**不重复生成**：第二次入库一个字节都不动（入库必须是幂等的）。"""
    api = build_api(tmp_path, pages=[a_page([
        block("b1", [0.02, 0.02, 0.9, 0.2], keep=True),
        block("b2", [0.02, 0.40, 0.9, 0.2], keep=True),
    ])])
    first_status, first = commit(api)
    card_bytes = {p.name: p.read_bytes() for p in api.catalog.problems_dir.glob("*.json")}
    page_bytes = (api.catalog.pages_dir / f"{PAGE_ID}.json").read_bytes()

    second_status, second = commit(api)

    assert first_status == 200 and second_status == 200
    data = second["data"]
    assert data["created"] == [], "第二次不许再建卡"
    assert [row["card_id"] for row in data["reused"]] == [row["card_id"] for row in first["data"]["created"]]
    assert data["wrote_cards"] is False
    assert data["wrote_page"] is False, "没有任何变化就不该重写页文件"
    assert {p.name: p.read_bytes() for p in api.catalog.problems_dir.glob("*.json")} == card_bytes
    assert (api.catalog.pages_dir / f"{PAGE_ID}.json").read_bytes() == page_bytes


def test_a_page_that_says_a_card_exists_recreates_it_under_the_same_id(tmp_path):
    """页里已经有绑定、卡却不在（删了/没写完）：重跑**补建**，且**不改 id**。

    绑定是页文件的真相；给它换一个新 id 就是「重切给已审核的卡改名」那一类事故。
    """
    bound = "p-20200101-abcdef"
    api = build_api(tmp_path, pages=[a_page([
        block("b1", [0.02, 0.02, 0.9, 0.2], keep=True, card_id=bound),
    ])])

    status, envelope = commit(api)

    assert status == 200
    assert [row["card_id"] for row in envelope["data"]["created"]] == [bound]
    assert (api.catalog.problems_dir / f"{bound}.json").is_file()


# ---------------------------------------------------------------- 验收 3：新卡被两把闸门挡住


def test_new_cards_are_gated_by_the_one_implementation(tmp_path):
    """新卡一律未审核：**同一份实现**（`autojudge` + `screen_redo_gate` 的 D4 硬闸门）挡住它。

    「不参与自动判定」与「不进重做纸」不是两处巧合：它们读**同一个** `review.status`，
    而新卡只有 `commit` 会写、写出来的永远是 `unreviewed`。骨架卡还同时踩着另外两把
    闸门（没有标准答案、没有擦除图），所以这里逐把验：

    - 现状（没标准答案）→ `autojudge` 按**固定优先级**给 `no_standard_answer`；
    - 只补上标准答案（模拟审核填了标准答案、但还没标已审核）→ 同一个函数给 `unreviewed`；
    - 即使标成已审核，`screen_redo_gate` 仍因**缺擦除图**挡住它，且**不许退化成原图**
      （D4：原图印着订正，会把答案摆在做题的人面前）。
    """
    from server import autojudge, records

    api = build_api(tmp_path, pages=[a_page([
        block("b1", [0.02, 0.02, 0.9, 0.2], keep=True),
    ])])
    commit(api)
    card_id = json.loads((api.catalog.problems_dir.iterdir().__next__().read_text(encoding="utf-8")))["id"]
    card = api.catalog.load_card(card_id)

    assert card["review"]["status"] == "unreviewed"
    assert card["standard_answer"]["value"] in (None, "")
    assert not card["problem"].get("clean_image") and not card["problem"].get("image")

    # 1) 不参与自动判定：唯一的判断实现说不合格（没标准答案优先级更高）
    assert autojudge.eligibility(card)["eligible"] is False
    assert autojudge.reject_reason(card) == "no_standard_answer"
    # 补上标准答案之后，「未审核」这条自己就浮出来——它确实是被审的那一条
    card["standard_answer"]["value"] = "A"
    assert autojudge.reject_reason(card) == "unreviewed"
    assert autojudge.eligibility(card)["reason_text"] == autojudge.REASONS["unreviewed"]

    # 2) 不进重做纸：D4 的硬闸门（缺擦除图也不许退化成原图）
    card["review"]["status"] = "reviewed"
    card["topics"] = ["函数与导数/极值与最值"]
    gate = records.screen_redo_gate(card, api.catalog)
    assert gate["ready"] is False
    assert gate["blockers"] == ["no_clean_image"]
    assert records.screen_redo_gate(api.catalog.load_card(card_id), api.catalog)["ready"] is False


# ---------------------------------------------------------------- 索引重建（验收 1 的后半）


def test_the_rebuilt_index_shows_the_two_new_cards(tmp_path):
    """「生成 2 张卡（各带页绑定），**索引重建**」：入库报告与索引读到的数字必须一致。

    骨架卡没有题面转录，所以索引**收录它并报 `problem_transcript_missing`**（warning），
    而不是像以前那样静默跳过——静默跳过等于「入库了却看不见」。
    """
    api = build_api(tmp_path, pages=[a_page([
        block("b1", [0.02, 0.02, 0.9, 0.2], keep=True),
        block("b2", [0.02, 0.40, 0.9, 0.2], keep=True),
    ])])
    _, envelope = commit(api)
    created = [row["card_id"] for row in envelope["data"]["created"]]

    r = api.handle("GET", "/api/index")
    body = json.loads(r.body)

    assert r.status == 200
    assert body["data"]["count"] == 2
    assert sorted(rec["id"] for rec in body["data"]["problems"]) == sorted(created)
    assert sorted(envelope["data"]["index"]["card_ids"]) == sorted(created)
    assert body["skipped"] == []
    for rec in body["data"]["problems"]:
        codes = {w["code"] for w in rec["warnings"]}
        assert "problem_transcript_missing" in codes
        assert "page_binding_missing" not in codes and "page_binding_lost" not in codes
        # 两把闸门都关着（同一份实现给的两个读数）
        assert rec["auto_judge"]["eligible"] is False
        assert rec["screen_redo"]["ready"] is False
