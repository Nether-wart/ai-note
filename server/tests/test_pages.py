"""页：一等实体（spec #2、CONTEXT「页」、契约 §10.2、编排裁决 D5）。

测的是**行为**：一张存量卡进去，一个页文件出来；一份新旧块列表进去，
一条「谁保留、谁新增、谁消失」的对照出来。不测内部函数名、不联网、不画图。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from conftest import make_card
from server import pages

# 真实存量数据（只读核对过）：一页一题，页照片 12 位哈希。
REAL_PAGE_HASH = "41c86bcfc007"
REAL_PID = "p-20261004-41c86b"


def real_shaped_card(pid: str = REAL_PID, page_hash: str = REAL_PAGE_HASH, **overrides) -> dict:
    """一张与 `data/problems/p-20261004-41c86b.json` 同形状的存量卡。

    回填所需的料全在 `source`：整页照片 + 单块边界（`bbox_norm`；`bbox_px` 读盘上原值）。
    """
    fields = {
        "created_at": "2026-10-04T14:31:35+08:00",
        "source.page_image": f"data/pages/{page_hash}.png",
        "source.bbox_norm": [0.02, 0.12, 0.76, 0.28],
        "source.bbox_px": [3, 20, 541, 79],
        "source.original_file": "2.png",
    }
    fields.update(overrides)
    return make_card(pid, **fields)


def read_page(api, page_id: str = REAL_PAGE_HASH) -> dict:
    path = api.catalog.pages_dir / f"{page_id}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_page_id_comes_from_the_photo_filename_not_the_card_id():
    """真实数据的坑：页照片是 12 位哈希、题卡 id 是 6 位——长度都不一样（D5）。

    `data/problems/p-20261004-41c86b.json` → `data/pages/41c86bcfc007.png`
    所以页文件叫 `41c86bcfc007.json`，**不是** `41c86b.json`。
    """
    assert pages.page_hash_from_image("data/pages/41c86bcfc007.png") == "41c86bcfc007"
    assert pages.page_hash_from_image("41c86bcfc007.png") == "41c86bcfc007"
    assert pages.page_hash_from_image("/abs/data/pages/ef7c47156392.jpeg") == "ef7c47156392"


def test_page_id_is_none_when_there_is_no_usable_photo_name():
    """没有照片就没有页——不猜、不从题卡 id 兜底。"""
    assert pages.page_hash_from_image(None) is None
    assert pages.page_hash_from_image("") is None
    assert pages.page_hash_from_image("data/pages/") is None


def test_page_id_is_none_for_a_name_that_is_not_a_single_safe_component():
    """页 id 会变成文件名，所以它必须是一个安全的文件名片段（纵深防御，§7.3 同款）。"""
    assert pages.page_hash_from_image("data/pages/../../etc/passwd") is None
    assert pages.page_hash_from_image("data/pages/..png") is None


# ---------------------------------------------------------------- 验收 1：存量回填


def test_backfill_turns_a_legacy_card_into_a_page_file(api_for):
    """验收 1：存量卡反推成页文件——照片 + 单个块 = 那张卡的边界。

    真实数据里两张卡各是「一页一题」，所以回填出两个页文件、各一个块。
    """
    api = api_for([real_shaped_card()])

    report = pages.backfill_pages(api.catalog, apply=True)

    assert report["apply"] is True
    assert [c["action"] for c in report["cards"]] == ["created"]
    assert report["summary"]["created"] == 1

    page = read_page(api)
    assert page["version"] == 1
    assert page["id"] == REAL_PAGE_HASH, "页 id 是照片文件名主干，不是题卡 id 的 6 位"
    assert page["image"] == f"{REAL_PAGE_HASH}.png", "页文件与照片同目录并列（D5）"
    assert page["created_at"] == "2026-10-04T14:31:35+08:00"
    assert page["origin"] == {
        "original_file": "2.png",
        "sheet": None,        # 来源信息：第几页、哪张卷子（spec #2），存量数据没有
        "page_number": None,
    }

    (block,) = page["blocks"]
    assert block["bbox_norm"] == [0.02, 0.12, 0.76, 0.28], "规范基准是整页归一化坐标"
    assert block["bbox_px"] == [3, 20, 541, 79], "读盘上的原值，不用 bbox_norm 重算"
    assert block["card_id"] == REAL_PID
    assert block["keep"] is True, "卡片在库里就是在「收」——存量卡的去留不是待定"


def test_backfill_is_idempotent_and_never_touches_the_card(api_for):
    """简报点名的幂等三条：不产生第二份页文件、不覆盖已有绑定、不改动卡上别的字段。"""
    api = api_for([real_shaped_card()])
    card_file = api.catalog.problems_dir / f"{REAL_PID}.json"
    card_bytes = card_file.read_bytes()

    pages.backfill_pages(api.catalog, apply=True)
    after_first = read_page(api)
    second = pages.backfill_pages(api.catalog, apply=True)

    assert [c["action"] for c in second["cards"]] == ["unchanged"]
    assert second["summary"]["pages_changed"] == 0
    assert read_page(api) == after_first, "第二次跑必须一个字节都不改"
    assert sorted(p.name for p in api.catalog.pages_dir.glob("*.json")) == [f"{REAL_PAGE_HASH}.json"]
    assert card_file.read_bytes() == card_bytes, "回填只读题卡，题卡上的字段一个都不许动"


def test_dry_run_reports_but_writes_nothing(api_for):
    """审计只报告、回填是另一条显式命令（spec-2 笔记 §I）：预演不落盘。"""
    api = api_for([real_shaped_card()])

    report = pages.backfill_pages(api.catalog, apply=False)

    assert [c["action"] for c in report["cards"]] == ["created"]
    assert report["summary"]["pages_changed"] == 1, "预演也要说清「本来会改什么」"
    assert not list(api.catalog.pages_dir.glob("*.json")), "预演不许写盘"


def test_two_cards_from_one_photo_share_a_page_and_get_distinct_blocks(api_for):
    """一页多题：两块汇进同一个页文件，各块绑定各自的题卡 id（验收 3 的回填侧）。"""
    api = api_for([
        real_shaped_card("p-20261004-aaaaaa"),
        real_shaped_card("p-20261004-bbbbbb", **{
            "source.bbox_norm": [0.02, 0.45, 0.76, 0.18],
            "source.bbox_px": [3, 84, 541, 119],
        }),
    ])

    report = pages.backfill_pages(api.catalog, apply=True)

    assert report["summary"]["pages"] == 1, "两张卡指向同一张照片 = 一页"
    assert {b["card_id"] for b in read_page(api)["blocks"]} == {
        "p-20261004-aaaaaa", "p-20261004-bbbbbb"}
    assert len(list(api.catalog.pages_dir.glob("*.json"))) == 1


def test_backfill_skips_a_card_without_a_whole_page_photo_without_crashing(api_for):
    """没有整页照片可回填的卡要被**明确列出来**，不是安静地少一个页文件。"""
    api = api_for([real_shaped_card(**{"source.page_image": None})])

    report = pages.backfill_pages(api.catalog, apply=True)

    (entry,) = report["cards"]
    assert entry["action"] == "skipped"
    assert "page_image" in entry["message"]
    assert not list(api.catalog.pages_dir.glob("*.json"))


# ------------------------------------- 重切：位置重合保留原有绑定（#10/#14 的地基）


def blk(block_id: str, bbox_norm, card_id=None, keep=None) -> dict:
    return {"id": block_id, "bbox_norm": list(bbox_norm), "bbox_px": None,
            "card_id": card_id, "keep": keep}


def test_rebind_keeps_the_binding_of_a_block_that_stayed_put():
    """目标句：重切不会把已审核、已重做过的卡片改名（spec #2 的核心）。"""
    old = [blk("b1", [0, 0, 1, 1], card_id="p-20261004-aaa111", keep=True)]
    new = [blk("n1", [0.005, 0.005, 0.99, 0.99])]   # 几乎没动

    report = pages.rebind(old, new)

    (block,) = report["blocks"]
    assert block["card_id"] == "p-20261004-aaa111", "绑定照旧"
    assert block["keep"] is True
    # 对照事实在 matches 里、不写进块：块会长成页文件的一行，而 matched_from 下次就过期
    (match,) = report["matches"]
    assert (match["block_id"], match["matched_from"]) == ("n1", "b1")
    assert match["iou"] > 0.9
    assert "matched_from" not in block and "iou" not in block
    assert report["summary"] == {"matched": 1, "new": 0, "removed": 0, "removed_with_card": 0}


def test_rebind_geometry_boundaries():
    """三条边界：完全重合 / 部分重叠但没重合到算同一块 / 完全不相交（spec-2 §C）。

    重合度的期望值是**手算的几何**，不是拿实现算一遍再跟自己比。
    """
    old_box = [0, 0, 0.5, 0.5]
    assert pages.iou(old_box, [0, 0, 0.5, 0.5]) == 1.0                  # 完全重合
    assert round(pages.iou(old_box, [0.3, 0, 0.4, 0.5]), 4) == 0.2857   # 0.1/(0.25+0.2-0.1)
    assert pages.iou(old_box, [0.6, 0.6, 0.3, 0.3]) == 0.0              # 完全不相交

    old = [blk("b1", old_box, card_id="p-20261004-aaa111")]

    same = pages.rebind(old, [blk("n1", [0, 0, 0.5, 0.5])])
    assert same["matches"][0]["matched_from"] == "b1"

    partial = pages.rebind(old, [blk("n1", [0.3, 0, 0.4, 0.5])])
    assert partial["matches"][0]["matched_from"] is None, "IoU≈0.29 < 0.5：这不是同一块"
    assert partial["summary"] == {"matched": 0, "new": 1, "removed": 1, "removed_with_card": 1}

    disjoint = pages.rebind(old, [blk("n1", [0.6, 0.6, 0.3, 0.3])])
    assert disjoint["matches"][0]["matched_from"] is None
    assert disjoint["matches"][0]["iou"] is None, "没配上就没有重合度可言"


def test_rebind_never_invents_an_id_for_a_genuinely_new_block():
    """只有真正新增的块才分配新 id——分配发生在入库那一刻，不在重切里。"""
    report = pages.rebind([], [blk("n1", [0.1, 0.1, 0.2, 0.2])])

    assert report["blocks"][0]["card_id"] is None
    assert report["summary"]["new"] == 1


def test_rebind_preserves_human_decisions_on_a_matched_block():
    """人动过的部分不被一次重切抹掉：绑定与去留都跟着块走（#14 验收 2 的地基）。"""
    old = [blk("b1", [0, 0, 1, 1], card_id="p-20261004-aaa111", keep=False)]
    new = [blk("n1", [0, 0, 1, 1])]

    (block,) = pages.rebind(old, new)["blocks"]

    assert (block["card_id"], block["keep"]) == ("p-20261004-aaa111", False)


def test_rebind_matches_one_to_one_greedily_by_overlap():
    """一对一：一个旧块只喂给重合度最高的那个新块，不许一夫多妻。"""
    old = [blk("b1", [0, 0, 1, 1], card_id="p-20261004-aaa111")]
    new = [blk("n1", [0, 0, 0.9, 1]), blk("n2", [0.2, 0, 0.8, 1])]

    report = pages.rebind(old, new)

    assert [m["matched_from"] for m in report["matches"]] == ["b1", None]
    assert report["blocks"][1]["card_id"] is None
    assert report["summary"] == {"matched": 1, "new": 1, "removed": 0, "removed_with_card": 0}


def test_rebind_shouts_when_a_removed_block_carried_a_card():
    """「人动过的卡片不允许被一次重切抹掉」——消失的块如果带着卡，必须显式喊。"""
    old = [blk("b1", [0, 0, 0.3, 0.3], card_id="p-20261004-aaa111")]

    report = pages.rebind(old, [blk("n1", [0.6, 0.6, 0.3, 0.3])])

    assert report["summary"]["removed_with_card"] == 1
    (removed,) = report["removed"]
    assert removed["card_id"] == "p-20261004-aaa111"
    assert any(w["code"] == "block_removed_with_card" for w in report["warnings"])


def test_rebind_tolerates_a_block_without_a_usable_box():
    """边界读不出来就没法按位置匹配——不静默、不猜，明说匹配不上。"""
    old = [{"id": "b1", "bbox_norm": None, "card_id": "p-20261004-aaa111"}]

    report = pages.rebind(old, [blk("n1", [0, 0, 1, 1])])

    assert report["matches"][0]["matched_from"] is None
    assert any(w["code"] == "block_without_box" for w in report["warnings"])


# ------------------------------- 验收 3：一页多块时各块的题卡 id 互不相同


def unbound_page(block_ids=("b1", "b2", "b3"), page_id: str = REAL_PAGE_HASH) -> dict:
    return {
        "version": 1,
        "id": page_id,
        "image": f"{page_id}.png",
        "created_at": "2026-10-04T14:31:35+08:00",
        "origin": {"original_file": "2.png", "sheet": None, "page_number": None},
        "blocks": [blk(i, [0.0, 0.1 * n, 1.0, 0.08]) for n, i in enumerate(block_ids)],
    }


def test_three_blocks_on_one_page_get_three_distinct_card_ids():
    """验收 3：一页多块时，各块的题卡 id 互不相同（照片哈希不再等于题卡 id）。"""
    page = unbound_page()
    at = datetime(2026, 10, 4, 14, 31, 35, tzinfo=timezone.utc)

    result = pages.assign_card_ids(page, taken=[], at=at)

    ids = [b["card_id"] for b in result["page"]["blocks"]]
    assert len(ids) == 3
    assert len(set(ids)) == 3, "互不相同"
    assert all(pid.startswith("p-20261004-") for pid in ids), "日期是入库日"
    assert [a["block_id"] for a in result["assigned"]] == ["b1", "b2", "b3"]
    assert result["kept"] == []


def test_assign_card_ids_never_reuses_an_id_that_is_already_taken():
    """id 的唯一性由 `taken` 保证：撞上盘上已有的卡就换下一个候选。"""
    at = datetime(2026, 10, 4, tzinfo=timezone.utc)
    seed = f"{REAL_PAGE_HASH}#b1"

    first = pages.allocate_card_id([], at=at, seed=seed)
    second = pages.allocate_card_id([first], at=at, seed=seed)

    assert first != second
    assert len(first) == len("p-20261004-41c86b"), "形状沿用存量习惯"


def test_assign_card_ids_leaves_an_already_bound_block_alone():
    """已经入库过的块**不重复生成**——#15 的入库幂等靠这一条。"""
    page = unbound_page(("b1", "b2"))
    page["blocks"][0]["card_id"] = "p-20261004-41c86b"
    at = datetime(2026, 10, 4, tzinfo=timezone.utc)

    result = pages.assign_card_ids(page, taken=["p-20261004-41c86b"], at=at)

    assert [b["card_id"] for b in result["page"]["blocks"]][0] == "p-20261004-41c86b"
    assert [k["card_id"] for k in result["kept"]] == ["p-20261004-41c86b"]
    assert len(result["assigned"]) == 1
    # 纯函数：不写盘，也不改传进来的那一份
    assert page["blocks"][1]["card_id"] is None


def test_assign_card_ids_is_idempotent_when_run_twice():
    """跑两次不换名：第二次看到的块都已经有绑定了（重切不该给卡改名）。"""
    at = datetime(2026, 10, 4, tzinfo=timezone.utc)

    once = pages.assign_card_ids(unbound_page(), taken=[], at=at)
    twice = pages.assign_card_ids(once["page"], taken=[], at=at)

    assert [b["card_id"] for b in twice["page"]["blocks"]] == \
           [b["card_id"] for b in once["page"]["blocks"]]
    assert twice["assigned"] == []
    assert len(twice["kept"]) == 3


def test_ids_allocated_on_one_page_do_not_collide_with_other_pages():
    """两张页各自分配，互不撞——id 是「块」的函数，不是「照片」的函数。"""
    at = datetime(2026, 10, 4, tzinfo=timezone.utc)

    a = pages.assign_card_ids(unbound_page(page_id="41c86bcfc007"), taken=[], at=at)
    b = pages.assign_card_ids(unbound_page(page_id="ef7c47156392"), taken=[], at=at)

    ids = [x["card_id"] for x in a["page"]["blocks"] + b["page"]["blocks"]]
    assert len(set(ids)) == len(ids) == 6
