"""端到端：合成一页 → 切分 → 收两道丢一道 → 生成两张卡 → 索引 → 审计 → 重切（#15、spec #2 第 5 层）。

spec #2 的端到端验收原话：「合成一页 → 切分 → 收两道丢一道 → 生成两张卡 → 索引 → 审计无报错；
再对同一页重切一次，断言已入库的块变成「保留」而不是「替换」。**测完即删**。」

所以这条链路整条打的是**外部行为**（两次 HTTP + 两个只读入口），夹具全在 pytest 临时目录里：
真数据一张卡都不碰（`data/problems/*.json` 的重做次数至今仍是 0）。切分是注入的假接缝，
**不联网**；合成图用 `server/ink.py` 自己的 PNG 编码器画。

一处口径要如实说清：**骨架卡刚入库时审计一定有 warning**——题面还没转录、没有标准答案、
没有擦除图，那正是「进审核队列」的原因（`problem_transcript_missing` 等）。所以本文件先把
「页↔卡对账没有错误」与「内容缺项」分开断言，再模拟审核把内容填上，然后断言审计
**一条发现都没有**——「审计无报错」那句话在这个语境里才是可验证的。
"""

from __future__ import annotations

import json

from server import audit, ink
from test_ink import RED, WHITE, white_with_blocks
from test_page_create import build_api, static_segmenter

PAGE_H = 60
THREE_BLOCKS = [
    {"id": "b1", "bbox_norm": [0.0, 0.0, 0.4, 0.25], "question_no": 17},
    {"id": "b2", "bbox_norm": [0.0, 1 / 3, 0.4, 0.25], "question_no": 18},
    {"id": "b3", "bbox_norm": [0.0, 2 / 3, 0.4, 0.25], "question_no": 19},
]
# 前两块有红笔（订正）、第三块是空白（没有红笔痕迹 → 按 #12 的规则不入库）
PHOTO = [(0, 0, 40, 15, RED), (0, 20, 40, 35, RED), (0, 40, 40, 55, WHITE)]


def post(api, target, body=None, content_type="application/json"):
    if body is not None and content_type == "application/json":
        body = json.dumps(body).encode("utf-8")
    r = api.handle("POST", target, body or b"", content_type=content_type)
    return r.status, json.loads(r.body)


def get(api, target):
    r = api.handle("GET", target)
    return r.status, json.loads(r.body)


def audit_codes(report, level=None):
    return {f["code"] for f in report["findings"] if level is None or f["level"] == level}


def test_the_whole_page_pipeline_from_a_photo_to_an_audited_library(tmp_path):
    """一条链走完：照片 → 块 → 收两道丢一道 → 两张卡 → 索引 → 审计 → 重切「保留」。"""
    from conftest import multipart_body

    blob = ink.encode_png(white_with_blocks(100, PAGE_H, PHOTO))
    segmenter = static_segmenter(THREE_BLOCKS)
    api = build_api(tmp_path, segmenter=segmenter)

    # ---- 1) 建：照片进来，切出三块，其中两块有红笔 → 建议收，第三块没有红笔 → 不入库
    body, content_type = multipart_body([("page.png", blob)], fields=[("subject", "数学")])
    status, envelope = post(api, "/api/page", body, content_type)
    assert status == 200, envelope
    page_id = envelope["data"]["created"][0]
    blocks = {b["id"]: b for b in envelope["data"]["pages"][0]["blocks"]}
    assert [blocks[key]["keep"] for key in ("b1", "b2", "b3")] == [True, True, False]
    assert envelope["data"]["pages"][0]["not_kept"]["message"] == "另有 1 道没有红笔痕迹、未入库"

    # ---- 2) 入库：只有两块「收」的块 → 两张卡，各带页绑定；索引重建后两张都在
    status, envelope = post(api, f"/api/page/{page_id}/commit")
    assert status == 200, envelope
    created = [row["card_id"] for row in envelope["data"]["created"]]
    assert len(created) == 2 and len(set(created)) == 2
    assert [row["block_id"] for row in envelope["data"]["skipped"]] == ["b3"]
    assert envelope["data"]["index"]["count"] == 2

    saved = json.loads((api.catalog.pages_dir / f"{page_id}.json").read_text(encoding="utf-8"))
    bound = {b["id"]: b["card_id"] for b in saved["blocks"]}
    assert bound["b3"] is None, "没入库的块不许拿到卡号"
    assert sorted(bound[key] for key in ("b1", "b2")) == sorted(created)

    status, index = get(api, "/api/index")
    assert status == 200 and index["data"]["count"] == 2
    from server import pages as pages_mod
    for pid in created:
        card = api.catalog.load_card(pid)
        assert pages_mod.page_binding(api.catalog, card)["bound"] is True

    # ---- 3) 审计：页↔卡这一侧**没有错误**；警告只有「内容还没填」（审核队列的活）
    report = audit.audit(api.catalog)
    reconciliation_errors = {
        "page_card_missing", "page_binding_missing", "page_binding_lost",
        "card_bound_to_other_page", "duplicate_transcript_on_page", "page_photo_missing",
        "page_file_unreadable", "card_file_unreadable", "page_id_mismatch",
        "problem_id_mismatch", "page_image_unsafe", "block_not_an_object",
    }
    assert audit_codes(report, "warning") & reconciliation_errors == set(), report["findings"]
    assert audit_codes(report, "warning") <= {
        "problem_transcript_missing", "standard_answer_missing", "topics_empty",
        "no_clean_image"}
    # 页↔卡这一侧连提示都没有：丢弃的那一块**从来没有拿到卡号**（不是「卡绑在丢弃的块上」），
    # 所以审计没有它的话可说——这正是「不给丢弃的块发卡号」那条纪律在审计上的读数。
    assert audit_codes(report, "hint") == set(), report["findings"]

    # ---- 4) 模拟审核把内容填上（这一步不在 #15 的范围里）：审计从此一条发现都没有
    for pid in created:
        card = api.catalog.load_card(pid)
        card["problem"]["transcript"] = f"{card['id']} 的题面（审核时填的）"
        card["problem"]["type"] = "choice"       # 块上没定题型 → 审核时定（#14 的「还没定」）
        card["problem"]["image"] = f"data/assets/{pid}-problem.png"
        card["problem"]["clean_image"] = f"data/assets/{pid}-clean.png"
        card["standard_answer"] = {"value": "A", "confidence": 1.0}
        card["topics"] = ["函数与导数/极值与最值"]
        (api.catalog.problems_dir / f"{pid}.json").write_text(
            json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
        (api.catalog.assets_dir / f"{pid}-problem.png").write_bytes(ink.encode_png(
            white_with_blocks(40, 15, [(0, 0, 40, 15, WHITE)])))
        (api.catalog.assets_dir / f"{pid}-clean.png").write_bytes(ink.encode_png(
            white_with_blocks(40, 15, [(0, 0, 40, 15, WHITE)])))

    report = audit.audit(api.catalog)
    assert report["findings"] == [], report["findings"]
    assert report["ok"] is True

    # ---- 5) 重切：同一页同样的候选块 → 已入库的块是「保留」，不是「替换」
    #
    # **本版（#17 / #31）改动**：重切不再是「只给对照、一个字节都不写」，它的语义改成
    # 「**重置为预设**」——预设落进页文件（`blocks` ＋ `segmentation.mode = "model"`），
    # 没进新列表的旧块进 `removed_blocks[]`。这一页三块都按位置配上了，所以留痕是空的。
    # **题卡仍然一个字节都不写**（`wrote_cards` 恒为 False）：重置动的是页，不是库。
    cards_before = {p.name: p.read_bytes() for p in api.catalog.problems_dir.glob("*.json")}
    status, envelope = post(api, f"/api/page/{page_id}/resegment")

    assert status == 200, envelope
    states = [m["state"] for m in envelope["data"]["matches"]]
    assert states == ["kept", "kept", "kept"], envelope["data"]["matches"]
    assert envelope["data"]["summary"]["replaced"] == 0
    assert envelope["data"]["wrote_cards"] is False
    assert envelope["data"]["wrote_page"] is True, "重置为预设是真的重置，落盘"
    after = {b["id"]: b["card_id"] for b in envelope["data"]["blocks"]}
    assert [after["b1"], after["b2"]] == created, "重切不许给已入库的卡改名"
    on_disk = json.loads((api.catalog.pages_dir / f"{page_id}.json").read_text("utf-8"))
    assert on_disk["segmentation"]["mode"] == "model"
    assert [b["id"] for b in on_disk["blocks"]] == ["b1", "b2", "b3"]
    assert on_disk["removed_blocks"] == [], "三块都还在，没有东西被丢掉"
    assert {p.name: p.read_bytes() for p in api.catalog.problems_dir.glob("*.json")} == cards_before
