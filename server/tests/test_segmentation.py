"""切分与对账（#10 B2、spec #2「模型出候选块，确定性统计做对账」、契约 §10.2）。

测的是**行为**：一份构造的块列表进去，一条明确的对账结论出来（spec #2 Testing
Decisions 第 1 层）。不联网、不画图、不碰真数据、不写题卡。

三条判据各自的「响声」都必须**结构化、可枚举**（ADR 0007 第 6 条不许静默）：
原型的两个静默丢题样本在 `proto/server.py:320-321`（`if pid in by_id` 直接不渲染）
与 `:933-936`（`continue` 跳过取不到的题）——同一类写法不许带到新代码里。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from server import pages, segmentation


def blk(block_id: str = "b1", box=(0.0, 0.0, 0.5, 0.5), **extra) -> dict:
    """一个页文件形状的块（契约 §10.2.1）。`bbox_norm` 是整页归一化 xywh（D5）。"""
    block = {"id": block_id, "bbox_norm": None if box is None else list(box),
             "bbox_px": None, "card_id": None, "keep": None}
    block.update(extra)
    return block


# ------------------------------------------------- 判据 1：题号连续性（最便宜也最强）


def test_question_number_gap_is_reported_with_the_missing_number():
    """验收 1：有 17、19 却没有 18 → 报警，并**点名**缺的是 18。

    题号来自模型报出的每块题号（spec #2）。缺号是「漏了一题」最便宜的探测器。
    """
    blocks = [blk("b1", question_no=17), blk("b2", question_no=19)]

    result = segmentation.check_question_numbers(blocks)

    assert result["checked"] is True
    assert result["ok"] is False
    assert result["numbers"] == [17, 19]
    assert result["gaps"] == [18], "缺号要说清是哪一号，不是一句「不连续」"
    assert result["duplicates"] == []
    assert result["blocks_without_number"] == []


def test_consecutive_question_numbers_pass():
    """连续（1、2、3）不报警——判据不能因为「太少」或「不整齐」就喊。"""
    blocks = [blk("b1", question_no=1), blk("b2", question_no=2), blk("b3", question_no=3)]

    result = segmentation.check_question_numbers(blocks)

    assert (result["checked"], result["ok"], result["gaps"]) == (True, True, [])


def test_a_single_block_cannot_have_a_gap():
    """一道题无所谓连续——别把「只有一个题号」报成缺口。"""
    assert segmentation.check_question_numbers([blk("b1", question_no=12)])["ok"] is True


def test_a_duplicated_question_number_is_reported():
    """两块报同一个题号也是错：它不是缺口，是切重了，同样要喊。"""
    blocks = [blk("b1", question_no=17), blk("b2", question_no=17), blk("b3", question_no=18)]

    result = segmentation.check_question_numbers(blocks)

    assert result["ok"] is False
    assert result["duplicates"] == [17]
    assert result["gaps"] == []


def test_blocks_that_do_not_report_a_number_are_named_not_ignored():
    """已知弱点：这条最强的检查依赖模型报出的题号。

    模型没报题号的块必须被**点名**（检查在这里是瞎的），不许当成「没有缺口」——
    否则检查的失败会伪装成检查的通过。非整数题号（`"17"`、`17.5`、`True`）同理。
    """
    blocks = [blk("b1", question_no=17), blk("b2"), blk("b3", question_no="19")]

    result = segmentation.check_question_numbers(blocks)

    assert result["blocks_without_number"] == ["b2", "b3"]
    assert result["numbers"] == [17]
    assert result["ok"] is True, "没报题号的块不是「题号不连续」，所以不升级成缺口警报"
    assert result["complete"] is False, "但结论里必须说清这一条没查全（检查的失败不许伪装成通过）"
    assert result["gaps"] == [], "只剩一个可读题号，推不出一条缺口——不许编"


# ------------------------------------------------------ 判据 2：块之间不重叠（同页两块）


def test_two_blocks_whose_bounds_intersect_are_reported():
    """验收 2：两个块的边界相交 → 报警，并点名是**哪两块**。

    边界用整页归一化 `bbox_norm`（xywh，D5）；重合度只有一份实现
    （`pages.iou`，#9），这里不另算一套几何。
    """
    blocks = [blk("b1", (0.0, 0.0, 0.5, 0.5)), blk("b2", (0.4, 0.4, 0.5, 0.5))]

    result = segmentation.check_overlaps(blocks)

    assert result["checked"] is True
    assert result["ok"] is False
    (pair,) = result["pairs"]
    assert (pair["a"], pair["b"]) == ("b1", "b2")
    assert pair["iou"] == pytest.approx(0.01 / 0.49), "手算：交 0.01，并 0.25+0.25-0.01"
    assert result["invalid"] == []


def test_blocks_that_merely_touch_at_the_edge_do_not_overlap():
    """贴着边不算相交：交集的面积是 0。判据不能把「相邻」喊成「重叠」。"""
    blocks = [blk("b1", (0.0, 0.0, 0.4, 0.5)), blk("b2", (0.4, 0.0, 0.6, 0.5))]

    result = segmentation.check_overlaps(blocks)

    assert (result["ok"], result["pairs"]) == (True, [])


def test_a_block_with_an_unreadable_box_is_named_not_silently_skipped():
    """边界读不出来的块，这一条对它**没查**——要说出来，不是安静地跳过它。

    原型的 `/mark`（`proto/server.py:933-936`）就是用一个 `continue` 把取不到的题
    安静地漏掉；对账里同样形状的 `continue` 会让「两块重叠」在坏框上失灵。
    """
    blocks = [blk("b1", (0.0, 0.0, 0.5, 0.5)), blk("b2", None), {"id": "b3"}]

    result = segmentation.check_overlaps(blocks)

    assert result["invalid"] == ["b2", "b3"]
    assert result["complete"] is False
    assert result["ok"] is True, "读不出边界 ≠ 重叠，但结论里必须说清没查全"


# --------------------------------------------------------------- 判据 3：覆盖率（墨迹）


def ink(box=(0.0, 0.5, 1.0, 0.4), px: int = 6000) -> dict:
    """一块墨迹（契约：由图像统计层给出，本模块不碰图）。

    `bbox_norm` 与块同一个基准（整页归一化 xywh）；`px` 是这块里的墨迹像素数。
    """
    return {"bbox_norm": list(box), "px": px}


def test_a_large_patch_of_ink_covered_by_no_block_is_reported():
    """验收 3：大片墨迹未被任何块覆盖 → 报警。

    块只盖住页面上部（y<0.3），下半页那片 6000px 的墨迹谁也没框住——
    真实数据里 `1-0000.png` 框下方 6158 像素墨迹全是手写解答
    （`docs/acceptance-log.md:75-83`），这条判据就是冲它去的。
    """
    blocks = [blk("b1", (0.0, 0.0, 1.0, 0.3))]

    result = segmentation.check_coverage(blocks, [ink()])

    assert result["checked"] is True
    assert result["ok"] is False
    assert result["covered_ratio"] == 0.0
    (miss,) = result["uncovered"]
    assert miss["px"] == 6000
    assert miss["alarm"] is True, "大片 → 报警"


def test_a_small_uncovered_patch_is_still_listed_even_though_it_does_not_alarm():
    """小片没被覆盖不算「大片」——但也不许消失（ADR 0007 第 6 条不许静默）。

    只有 `alarm` 的才升级成警报；其余仍在 `uncovered` 里可枚举、可求和。
    """
    result = segmentation.check_coverage([blk("b1", (0.0, 0.0, 1.0, 0.3))], [ink(px=40)])

    assert result["ok"] is True, "40px 不是「大片」"
    (miss,) = result["uncovered"]
    assert miss["px"] == 40 and miss["alarm"] is False
    assert result["uncovered_px"] == 40, "没报警不等于没发生——数字要在"


def test_ink_inside_a_block_is_covered():
    """框住的就是覆盖了：整块墨迹落在块里 → 覆盖率 1.0、没有未覆盖项。"""
    result = segmentation.check_coverage([blk("b1", (0.0, 0.0, 1.0, 1.0))], [ink()])

    assert (result["ok"], result["uncovered"], result["covered_ratio"]) == (True, [], 1.0)


def test_coverage_of_a_half_covered_patch_is_hand_computable():
    """覆盖比例是**手算的几何**，不是拿实现算一遍再跟自己比。

    墨迹 [0,0,0.4,0.4]，块只盖住它的左半边（x<0.2）→ 覆盖 0.5。
    阈值 `COVERED_MIN` 是「基本覆盖」：恰好一半算覆盖（不小于阈值），
    提到 0.6 就必须报出来。
    """
    blocks = [blk("b1", (0.0, 0.0, 0.2, 0.4))]
    trail = [ink(box=(0.0, 0.0, 0.4, 0.4), px=1000)]

    half = segmentation.check_coverage(blocks, trail)
    assert half["covered_ratio"] == pytest.approx(0.5)
    assert (half["ok"], half["uncovered"]) == (True, [])

    strict = segmentation.check_coverage(blocks, trail, covered_min=0.6)
    (miss,) = strict["uncovered"]
    assert miss["covered"] == pytest.approx(0.5)
    assert miss["alarm"] is False, "1000px 还没到「大片」的门槛——报警要留给大片，"
    assert strict["ok"] is True, "否则真实卷子上每一小片手写都会喊，人就不看警报了"
    assert strict["uncovered_px"] == 1000, "但没覆盖的像素数照样报出来"


def test_page_wide_scratch_ink_is_excluded_explicitly_not_silently():
    """覆盖率依赖「排除整页草稿式手写」这一步（spec #2 点名是启发式）。

    判据：单个墨迹区域几乎铺满整页（宽高都 ≥ `DRAFT_PAGE_SPAN`）= 整页草稿。
    被排除的区域**显式列出来**（`excluded`），不进覆盖率分母、也不当成未覆盖——
    否则一整页草稿会制造一片假警报，而「排除了什么」必须看得见。
    """
    scratch = [ink(box=(0.02, 0.02, 0.96, 0.96), px=50000)]

    result = segmentation.check_coverage([blk("b1", (0.0, 0.0, 1.0, 0.3))], scratch)

    assert (result["ok"], result["uncovered"]) == (True, [])
    assert result["ink_px"] == 0, "排除之后没有可对账的墨迹"
    (excluded,) = result["excluded"]
    assert excluded["px"] == 50000
    assert excluded["reason"] == "page_span"


def test_coverage_says_it_did_not_run_when_there_is_no_ink_to_check():
    """没有墨迹统计 = 这一条**没查**。结论必须说「没查」，不许当成「查过且通过」。"""
    result = segmentation.check_coverage([blk("b1")], None)

    assert result["checked"] is False
    assert result["ok"] is None
    assert "没查" in result["message"]


# ------------------------------------------------- 三条判据汇总成一条对账结论（可枚举）


def codes(warnings, level=None) -> list[str]:
    return [w["code"] for w in warnings if level is None or w["level"] == level]


def test_all_three_judgements_shout_in_one_structured_conclusion():
    """验收 1/2/3 合起来：三条反例各自的响声都在，且**可枚举、可断言**。

    这不是 `print`，是返回值：调用方（界面／审计）能逐条读 `code`、`level`、
    `message`，也能在 `checks` 里看到每条判据的明细。原型的静默丢题样本
    （`proto/server.py:320-321`、`:933-936`）在这里没有对应的写法。
    """
    blocks = [
        blk("b1", (0.0, 0.0, 1.0, 0.3), question_no=17),
        blk("b2", (0.7, 0.4, 0.5, 0.5), question_no=19),   # 与 b1 无关，但……
        blk("b3", (0.0, 0.2, 0.5, 0.3), question_no=19),   # 与 b1 相交、且与 b2 重号
    ]
    ink_regions = [
        ink(box=(0.0, 0.0, 1.0, 0.2)),        # 被 b1 盖住
        ink(box=(0.0, 0.6, 1.0, 0.3), px=8000),  # 谁也没盖住的大片墨迹
    ]

    report = segmentation.reconcile(blocks, ink_regions)

    assert report["summary"]["blocks"] == 3
    assert set(report["summary"]["alarms"]) == {
        segmentation.QUESTION_NUMBER_GAP,
        segmentation.QUESTION_NUMBER_DUPLICATE,
        segmentation.BLOCK_OVERLAP,
        segmentation.PAGE_INK_UNCOVERED,
    }
    # 逐条：级别只有服务能定（契约 §2）。这三条是真矛盾 → warning。
    assert codes(report["warnings"], "warning") == [
        segmentation.QUESTION_NUMBER_GAP,
        segmentation.QUESTION_NUMBER_DUPLICATE,
        segmentation.BLOCK_OVERLAP,
        segmentation.PAGE_INK_UNCOVERED,
    ], "顺序按判据走，读起来和体检报告一样"
    assert report["summary"]["ok"] is False
    assert report["checks"]["question_numbers"]["gaps"] == [18]
    assert report["checks"]["overlaps"]["pairs"][0]["b"] == "b3"
    assert report["checks"]["coverage"]["uncovered"][0]["px"] == 8000
    assert "缺 18" in next(w for w in report["warnings"]
                          if w["code"] == segmentation.QUESTION_NUMBER_GAP)["message"]


def test_reconcile_declares_the_judgement_it_did_not_run():
    """ADR 0007 第 6 条：说清「我做了什么、没做什么」。

    没给墨迹 → 覆盖率**没查**（`checks.coverage.checked is False`、
    `summary.checks_skipped` 列出它、发一条 `hint`）。它不冒充警报，
    也不冒充通过——`ok` 只覆盖「查过的」那部分，`complete` 说清有缺口。
    """
    report = segmentation.reconcile([blk("b1", (0.0, 0.0, 1.0, 0.3), question_no=1)])

    coverage = report["checks"]["coverage"]
    assert coverage["checked"] is False and coverage["ok"] is None
    assert report["summary"]["checks_run"] == ["question_numbers", "overlaps"]
    assert report["summary"]["checks_skipped"] == ["coverage"]
    assert codes(report["warnings"]) == [segmentation.COVERAGE_NOT_CHECKED]
    assert report["warnings"][0]["level"] == "hint", "「没查」不是矛盾，不该训练人忽略警报"
    assert report["summary"]["ok"] is True, "没有查出来的问题"
    assert report["summary"]["complete"] is False, "但这一页并没有被查全"


def test_a_blind_question_number_check_is_a_hint_not_a_false_alarm():
    """已知弱点不允许伪装成通过，也不允许伪装成警报。

    模型漏报题号的块 → `question_number_missing`（hint、点名是哪几块、`complete=False`）；
    它**不是** warning，因为「没报题号」不是「缺号」——把它报成同一级会让
    `question_number_gap` 这条最便宜的探测器被噪声淹掉。
    """
    report = segmentation.reconcile([blk("b1", (0.0, 0.0, 0.5, 0.5), question_no=1),
                                     blk("b2", (0.6, 0.6, 0.3, 0.3))],
                                    [ink(box=(0.0, 0.0, 0.5, 0.5))])

    assert codes(report["warnings"]) == [segmentation.QUESTION_NUMBER_MISSING]
    assert report["warnings"][0]["level"] == "hint"
    assert "b2" in report["warnings"][0]["message"]
    assert report["summary"]["alarms"] == []
    assert report["checks"]["question_numbers"]["complete"] is False


def test_reconcile_points_at_a_block_whose_box_cannot_be_read():
    """坏框要让对账说得出是**哪一块**没查，而不是安静地跳过它。"""
    report = segmentation.reconcile([
        blk("b1", (0.0, 0.0, 0.5, 0.5), question_no=1),
        {"id": "b2", "bbox_norm": None, "question_no": 2},
    ], [ink(box=(0.0, 0.0, 0.4, 0.4))])

    bad = [w for w in report["warnings"] if w["code"] == segmentation.BLOCK_WITHOUT_BOX]
    assert [w["level"] for w in bad] == ["warning"], "块数据有毛病是真矛盾，不是提示"
    assert len(bad) == 1 and "b2" in bad[0]["message"]
    assert report["checks"]["overlaps"]["complete"] is False
    assert report["checks"]["coverage"]["complete"] is False


def test_reconcile_points_at_an_ink_region_it_cannot_read():
    """读不出来的墨迹区域要用**自己的码**报出来，不许混进「块读不出来」那条。

    混成一条会让人去查块，而毛病其实在墨迹统计那一侧——错的消息比没有消息更坏。
    """
    report = segmentation.reconcile([blk("b1", (0.0, 0.0, 1.0, 1.0), question_no=1)],
                                    [{"bbox_norm": [0.1, 0.1, 0.2, 0.2], "px": -5}])

    assert segmentation.BLOCK_WITHOUT_BOX not in [w["code"] for w in report["warnings"]]
    (bad,) = [w for w in report["warnings"] if w["code"] == segmentation.PAGE_INK_INVALID]
    assert bad["level"] == "warning"
    assert report["checks"]["coverage"]["complete"] is False


def test_a_non_object_entry_in_the_block_list_is_named():
    """块列表里混进一个字符串：它不是一个块，但也不许安静地消失。"""
    report = segmentation.reconcile([blk("b1", (0.0, 0.0, 1.0, 1.0), question_no=1), "oops"])

    (bad,) = [w for w in report["warnings"] if w["code"] == segmentation.BLOCK_NOT_AN_OBJECT]
    assert bad["level"] == "warning"
    assert "oops" in bad["message"]
    assert report["summary"]["complete"] is False


# ------------------------------- MATCH_IOU 的裁决（#9 把口径的最终裁决留给本工单）
#
# 裁决理由写在 `server/pages.py: MATCH_IOU / MATCH_CONTAIN` 旁边。这里只钉行为：
# 「同一个块、边界被重切细化了」必须**保留**绑定，而「只是部分重叠的两个不同块」
# 仍然不许配上（#9 已经钉住的那条边界不许被我松掉）。


def test_a_refined_boundary_keeps_the_binding_even_though_iou_is_low():
    """同一个块被重切细化了边界（缩小到四分之一）→ 必须还是它，绑定不许丢。

    IoU = 交 0.1 / 并 0.4 = 0.25 < 0.5——纯 IoU 会判成「旧的消失 + 新的出现」，
    于是一张可能已审核的卡被孤立（正是 spec #2 最怕的那类失败）。
    重叠系数 = 交 0.1 / min(0.4, 0.1) = 1.0：「新框整个落在旧框里」= 同一个块。
    """
    old = [blk("b1", (0.0, 0.0, 1.0, 0.4), card_id="p-20261004-aaa111", keep=True)]
    new = [blk("n1", (0.0, 0.0, 1.0, 0.1))]

    report = pages.rebind(old, new)

    assert pages.iou(old[0]["bbox_norm"], new[0]["bbox_norm"]) == pytest.approx(0.25)
    assert report["matches"][0]["matched_from"] == "b1", "边界细化过的同一个块要认出它"
    assert report["blocks"][0]["card_id"] == "p-20261004-aaa111"
    assert report["summary"]["removed_with_card"] == 0, "卡片不许被孤立"


def test_partial_overlap_of_two_different_blocks_still_does_not_match():
    """裁决不许把 #9 钉住的边界松掉：部分重叠但谁也不包含谁 → 不是同一个块。

    旧 [0,0,0.5,0.5] vs 新 [0.3,0,0.4,0.5]：IoU≈0.29、重叠系数=0.5，两条都不够。
    """
    old = [blk("b1", (0.0, 0.0, 0.5, 0.5), card_id="p-20261004-aaa111")]
    new = [blk("n1", (0.3, 0.0, 0.4, 0.5))]

    report = pages.rebind(old, new)

    assert report["matches"][0]["matched_from"] is None
    assert report["summary"] == {"matched": 0, "new": 1, "removed": 1, "removed_with_card": 1}


def test_a_genuinely_new_block_at_another_place_never_inherits_a_binding():
    """位置完全不相交仍然不配——裁决只放宽了「包含」，没有放宽「位置」。"""
    old = [blk("b1", (0.0, 0.0, 0.3, 0.3), card_id="p-20261004-aaa111")]

    report = pages.rebind(old, [blk("n1", (0.6, 0.6, 0.3, 0.3))])

    assert report["blocks"][0]["card_id"] is None
    assert report["summary"]["new"] == 1


# ------------------------------------------- 重切对账：新增／替换／保留三态（不写题卡）


def page(blocks, page_id: str = "41c86bcfc007") -> dict:
    return {"version": 1, "id": page_id, "image": f"{page_id}.png",
            "created_at": "2026-10-04T14:31:35+08:00",
            "origin": {"original_file": "2.png", "sheet": None, "page_number": None},
            "blocks": blocks}


def card(pid: str, *, reviewed: bool = False, attempts=None, manual=None) -> dict:
    return {"id": pid,
            "review": {"status": "reviewed" if reviewed else "unreviewed"},
            "attempts": list(attempts or []),
            "problem": {"clean": {"manual": manual or []}}}


def test_resegment_maps_each_block_to_new_kept_or_replaced_and_writes_no_card():
    """验收 4：重切同一页时，位置重合的块**保留**原有绑定，不重新分配 id。

    逐块的对照就是「新增／替换／保留」三态。重切**只返回对照**：不写题卡、不写页文件
    （spec #2 的「重切」动作），所以报告里显式带 `wrote_cards=False`。
    """
    old = page([
        blk("b1", (0.0, 0.0, 1.0, 0.3), card_id="p-20261004-aaa111", keep=True),
        blk("b2", (0.0, 0.7, 1.0, 0.3), card_id="p-20261004-bbb222", keep=True),
    ])
    snapshot = json.dumps(old, ensure_ascii=False, sort_keys=True)
    new = [blk("n1", (0.0, 0.01, 1.0, 0.3)),        # 原来那一块，几乎没动
           blk("n2", (0.0, 0.4, 1.0, 0.2))]         # 新出现的一块

    report = segmentation.classify_resegment(old, new)

    assert [(m["block_id"], m["state"], m["matched_from"]) for m in report["matches"]] == [
        ("n1", "kept", "b1"),
        ("n2", "new", None),
    ]
    assert [b["card_id"] for b in report["blocks"]] == ["p-20261004-aaa111", None]
    assert "state" not in report["blocks"][0], "对照事实不许落进块（过期事实不进存档文件）"
    assert "state" not in report["blocks"][0] and "matched_from" not in report["blocks"][0]
    assert report["summary"] == {"kept": 1, "new": 1, "replaced": 0, "removed": 1,
                                 "needs_human": True,
                                 "human_work_checked": False}, \
        "没给卡片就没法判断「人动过没有」——这一条要自述没查"
    (gone,) = report["removed"]
    assert gone["card_id"] == "p-20261004-bbb222"
    assert report["wrote_cards"] is False and report["wrote_page"] is False
    assert json.dumps(old, ensure_ascii=False, sort_keys=True) == snapshot, "纯函数：不改传进来的页"


def test_replacement_is_never_an_automatic_result_of_resegmenting():
    """「替换」在新规则下**不该**作为自动结果出现——这条要能核。

    自动重切的候选块不带绑定（模型只给位置与题号）。配上的块继承旧绑定（保留）、
    配不上的块没有绑定（新增），所以 `replaced` 恒为 0：不存在「同一个位置换了
    一张卡」这种自动结果。候选块上就算混进一个 card_id，也不被认，并且要说出来。
    """
    old = page([blk("b1", (0.0, 0.0, 1.0, 0.3), card_id="p-20261004-aaa111")])
    new = [blk("n1", (0.0, 0.0, 1.0, 0.3)),                  # 正常候选：没有绑定
           blk("n2", (0.0, 0.5, 1.0, 0.3), card_id="p-99999999-ffffff")]  # 混进来的绑定

    report = segmentation.classify_resegment(old, new)

    assert report["summary"]["replaced"] == 0
    assert [m["state"] for m in report["matches"]] == ["kept", "new"]
    assert report["blocks"][0]["card_id"] == "p-20261004-aaa111"
    hint = next(w for w in report["warnings"]
                if w["code"] == segmentation.RESEGMENT_CANDIDATE_BOUND)
    assert hint["level"] == "hint"
    assert "p-99999999-ffffff" in hint["message"], "被忽略的绑定要点名，不能安静地丢掉"


def test_resegment_then_commit_never_renames_an_existing_card():
    """验收 4 的完整含义：重切 → 入库分配，位置重合的块**保留原 id**，只有新块拿新 id。

    对账给出的 `blocks` 是「可以落进页文件」的那一份，所以它直接喂给
    `pages.assign_card_ids`（#9 的分配，唯一实现）就能验证「重切不会把已审核的卡片改名」。
    """
    old = page([blk("b1", (0.0, 0.0, 1.0, 0.3), card_id="p-20261004-aaa111", keep=True)])
    new = [blk("n1", (0.0, 0.0, 1.0, 0.3)), blk("n2", (0.0, 0.5, 1.0, 0.3))]

    report = segmentation.classify_resegment(old, new)
    at = datetime(2026, 10, 4, 12, 0, 0, tzinfo=timezone.utc)
    committed = pages.assign_card_ids(
        {"id": report["page_id"], "blocks": report["blocks"]},
        taken=["p-20261004-aaa111"], at=at)

    ids = [b["card_id"] for b in committed["page"]["blocks"]]
    assert ids[0] == "p-20261004-aaa111", "重切不给已入库的块改名"
    assert ids[1] != "p-20261004-aaa111" and ids[1].startswith("p-20261004-"), "新块才分配新 id"
    assert [a["block_id"] for a in committed["assigned"]] == ["n2"]


def test_resegment_warns_about_a_card_a_human_has_already_worked_on():
    """「这一块对应的卡片已经审核过或已有重做历史」→ 警告（spec #2 原文）。

    理由（也是 #14 验收 2 的量化依据）：两题一共 12 笔人工掩膜修正，9 笔是「减」
    （`docs/acceptance-log.md:259-263`）——人动过的部分一次重切抹掉就是真实损失。
    """
    old = page([blk("b1", (0.0, 0.0, 1.0, 0.3), card_id="p-20261004-aaa111", keep=True)])
    new = [blk("n1", (0.0, 0.0, 1.0, 0.3))]

    report = segmentation.classify_resegment(old, new, cards=[
        card("p-20261004-aaa111", reviewed=True, attempts=[{"at": "2026-01-01T00:00:00+00:00"}],
             manual=[[0.1, 0.1, 0.2, 0.2]]),
    ])

    (loud,) = [w for w in report["warnings"]
               if w["code"] == segmentation.RESEGMENT_CARD_HUMAN_WORK]
    assert loud["level"] == "warning"
    assert "p-20261004-aaa111" in loud["message"]
    assert "审核" in loud["message"] and "重做" in loud["message"] and "掩膜" in loud["message"]
    assert report["summary"]["needs_human"] is True


def test_resegment_is_quiet_about_cards_nobody_has_touched():
    """新卡（未审核、没有重做、没有人工掩膜）不触发警告——否则警报会被噪声淹掉。"""
    old = page([blk("b1", (0.0, 0.0, 1.0, 0.3), card_id="p-20261004-aaa111")])
    new = [blk("n1", (0.0, 0.0, 1.0, 0.3))]

    report = segmentation.classify_resegment(old, new, cards=[card("p-20261004-aaa111")])

    assert segmentation.RESEGMENT_CARD_HUMAN_WORK not in [
        w["code"] for w in report["warnings"]]
    assert report["summary"]["needs_human"] is False


# ------------------------------------------- 切分那一半：模型出候选块（proto 里没有可抄的）


def test_candidate_blocks_come_out_numbered_in_the_order_the_model_reported_them():
    """模型给位置与题号，模块给块 id 与统一形状（`bbox_norm` = 整页 xywh，D5）。

    提示词里写死 `[x, y, w, h]`（口径继承 `proto/slice.py:281`）——两个坐标字段
    形状不同（`bbox_norm` xywh vs `bbox_px` xyxy），解析这一步只产 `bbox_norm`。
    """
    text = json.dumps({"blocks": [
        {"question_no": 17, "bbox_norm": [0.1, 0.1, 0.8, 0.2]},
        {"question_no": 18, "bbox_norm": [0.1, 0.35, 0.8, 0.2]},
    ]}, ensure_ascii=False)

    result = segmentation.parse_candidate_blocks(text)

    assert result["parsed"] is True
    assert [b["id"] for b in result["blocks"]] == ["b1", "b2"]
    assert [b["question_no"] for b in result["blocks"]] == [17, 18]
    assert result["blocks"][0]["bbox_norm"] == [0.1, 0.1, 0.8, 0.2]
    assert result["rejected"] == [] and result["warnings"] == []


def test_a_candidate_with_an_unusable_box_is_rejected_with_a_reason_not_dropped():
    """模型给了一块读不出来的边界 → **拒绝它并说明理由**，不是安静地少一块。

    「少了一块」正是本 spec 最怕的失败（`proto/server.py:320-321` 的同款写法），
    所以拒绝也是要喊的结果：`rejected` 里逐条给理由，`warnings` 里也有码。
    """
    text = json.dumps({"blocks": [
        {"question_no": 17, "bbox_norm": [0.1, 0.1, 0.8, 0.2]},
        {"question_no": 18, "bbox_norm": "0.1,0.35,0.8,0.2"},
        {"question_no": 19, "bbox_norm": [0.1, 0.6, 0.0, 0.2]},
    ]}, ensure_ascii=False)

    result = segmentation.parse_candidate_blocks(text)

    assert [b["question_no"] for b in result["blocks"]] == [17], "读得出边界的才留下"
    assert [(r["index"], r["question_no"]) for r in result["rejected"]] == [(1, 18), (2, 19)]
    assert all(r["reason"] for r in result["rejected"])
    loud = [w for w in result["warnings"]
            if w["code"] == segmentation.BLOCK_CANDIDATE_REJECTED]
    assert [w["level"] for w in loud] == ["warning", "warning"]
    assert "18" in loud[0]["message"], "拒绝了哪一块要在消息里点名"


def test_a_box_that_sticks_out_of_the_page_is_clamped_and_shouted():
    """模型把框划出页面（归一化坐标 > 1）→ 裁到页边界并**显式报警**。

    裁不是替模型猜：原始值写进消息里，人看得到「它本来想框到哪」。
    形状基准是归一化坐标（D5），越界一定是模型的错，不是坐标系的错。
    """
    text = json.dumps({"blocks": [
        {"question_no": 17, "bbox_norm": [0.1, 0.8, 0.9, 0.4]},
    ]}, ensure_ascii=False)

    result = segmentation.parse_candidate_blocks(text)

    (block,) = result["blocks"]
    assert block["bbox_norm"] == pytest.approx([0.1, 0.8, 0.9, 0.2]), "裁到页内"
    (loud,) = [w for w in result["warnings"] if w["code"] == segmentation.BLOCK_BOX_CLAMPED]
    assert loud["level"] == "warning"
    assert "[0.1, 0.8, 0.9, 0.4]" in loud["message"], "原始值要留证据"


def test_a_question_number_that_is_not_an_integer_stays_unreported_and_the_check_says_so():
    """`question_no` 是 `"17"` 这种非整数 → 不当成题号（不猜），留给题号判据说「没查全」。"""
    text = json.dumps({"blocks": [{"question_no": "17", "bbox_norm": [0.1, 0.1, 0.8, 0.2]}]},
                      ensure_ascii=False)

    result = segmentation.parse_candidate_blocks(text)

    assert result["blocks"][0]["question_no"] is None
    report = segmentation.reconcile(result["blocks"])
    assert segmentation.QUESTION_NUMBER_MISSING in [w["code"] for w in report["warnings"]]


def test_garbage_from_the_model_is_a_structured_failure_not_a_crash():
    """模型答非所问（没有 JSON / 没有 blocks）→ 结构化失败，不抛异常、不假装切出 0 块。

    「解析失败」与「这一页真的没有题」是两件事，必须分得开——否则失败会伪装成空页。
    """
    for text in ["抱歉，我看不清这张图", json.dumps({"foo": 1}), json.dumps({"blocks": "17,18"})]:
        result = segmentation.parse_candidate_blocks(text)
        assert result["parsed"] is False
        assert result["blocks"] == []
        assert result["message"], "要说清为什么没解析出东西"


# ------------------------------------------- 出口形状：契约 §2 的 Warning 一个键都不许少
#
# 起因（本人接手时查出来的缺口，不是推测）：`pages.rebind` 交回来的**事实**是它自己
# 手搓的字典（没有 `level`），`classify_resegment` 原样 `list(...)` 收下，于是页级对账的
# 出口会漏出「没有 level 的警告」——契约 §2 明说 `level` **总是显式发出来**（下游只能靠猜
# 就是这条要防的），§8 的页级对账码表也逐条给了级别。`id` 同理（索引级为 null）。
# 出口只有三个：`reconcile` / `classify_resegment` / `parse_candidate_blocks`。


def _assert_warning_shape(warnings, where: str):
    for w in warnings:
        assert set(w) >= {"code", "level", "message", "id"}, f"{where}：少键 {w}"
        assert w["level"] in {"warning", "hint"}, f"{where}：级别只有两级 {w}"
        assert isinstance(w["message"], str) and w["message"].strip(), f"{where}：空消息 {w}"


def test_reconcile_warnings_all_carry_the_contract_shape():
    """三条判据全响 + 没给墨迹：出口每一条都必须是 `{code, message, id, level}`。"""
    blocks = [blk("b1", (0.0, 0.0, 1.0, 0.3), question_no=17),
              blk("b2", (0.0, 0.2, 0.5, 0.3), question_no=19),
              blk("b3", None), "oops"]

    report = segmentation.reconcile(blocks, [ink(px=9000), {"bbox_norm": None, "px": 1}])

    _assert_warning_shape(report["warnings"], "reconcile")
    assert {w["level"] for w in report["warnings"]} == {"warning", "hint"}, \
        "「真矛盾」与「我这一条没查全」两级都要在——级别不是清一色的默认值"


def test_classify_resegment_passes_rebind_facts_through_as_full_warnings():
    """重切对账的出口也要有自己的 `level`：`rebind` 给的事实不是契约形状。

    这一条是缺口本身：坏框（`block_without_box`）与「消失的块带着卡片」
    （`block_removed_with_card`）都由 `pages.rebind` 产生，原样透传时**没有 level**。
    """
    old = page([blk("b1", (0.0, 0.0, 1.0, 0.3), card_id="p-20261004-aaa111", keep=True),
                blk("b2", None, card_id="p-20261004-bbb222")])
    new = [blk("n1", (0.0, 0.0, 1.0, 0.3)), blk("n2", None), "oops"]

    report = segmentation.classify_resegment(old, new, cards=[
        card("p-20261004-aaa111", reviewed=True)])

    _assert_warning_shape(report["warnings"], "classify_resegment")
    levels = {w["code"]: w["level"] for w in report["warnings"]}
    assert levels[segmentation.RESEGMENT_CARD_HUMAN_WORK] == "warning"
    assert {segmentation.BLOCK_WITHOUT_BOX, segmentation.BLOCK_NOT_AN_OBJECT,
            segmentation.BLOCK_REMOVED_WITH_CARD} <= set(levels), \
        "rebind 的三个码都要到出口上，一个都不许在路上丢"
    assert all(levels[c] == "warning" for c in (
        segmentation.BLOCK_WITHOUT_BOX, segmentation.BLOCK_NOT_AN_OBJECT,
        segmentation.BLOCK_REMOVED_WITH_CARD)), "块数据有毛病是真矛盾，不是提示"


def test_the_card_a_warning_is_about_is_named_in_id_not_only_in_the_message():
    """`id` 是机器可读的那一栏：警告挂在哪张卡上，界面不该去 grep 消息。

    页级（没有具体卡片）的警告 `id` 为 `None`——契约 §2 的「索引级为 null」同款。
    """
    old = page([blk("b1", (0.0, 0.0, 1.0, 0.3), card_id="p-20261004-aaa111", keep=True)])
    new = [blk("n1", (0.0, 0.0, 1.0, 0.3))]

    report = segmentation.classify_resegment(old, new, cards=[
        card("p-20261004-aaa111", attempts=[{"at": "2026-01-01T00:00:00+00:00"}])])

    (loud,) = [w for w in report["warnings"]
               if w["code"] == segmentation.RESEGMENT_CARD_HUMAN_WORK]
    assert loud["id"] == "p-20261004-aaa111"

    page_level = segmentation.reconcile([blk("b1", (0.0, 0.0, 0.5, 0.5), question_no=1)])
    assert [w["id"] for w in page_level["warnings"]] == [None], \
        "页级的警告没有具体卡片：id 显式为 null，不是缺键"


def test_parse_candidate_blocks_warnings_all_carry_the_contract_shape():
    """解析那一半的出口同理：拒块、裁框、解析失败，每条都有 code/level/message/id。"""
    text = json.dumps({"blocks": [
        {"question_no": 17, "bbox_norm": [0.1, 0.8, 0.9, 0.4]},
        {"question_no": 18, "bbox_norm": "读不出来"},
    ]}, ensure_ascii=False)

    result = segmentation.parse_candidate_blocks(text)

    _assert_warning_shape(result["warnings"], "parse_candidate_blocks")
    assert [w["code"] for w in result["warnings"]] == [
        segmentation.BLOCK_CANDIDATE_REJECTED, segmentation.BLOCK_BOX_CLAMPED] or \
        [w["code"] for w in result["warnings"]] == [
        segmentation.BLOCK_BOX_CLAMPED, segmentation.BLOCK_CANDIDATE_REJECTED]

    garbage = segmentation.parse_candidate_blocks("抱歉，我看不清")
    _assert_warning_shape(garbage["warnings"], "parse_candidate_blocks(失败)")
    assert garbage["warnings"][0]["level"] == "warning", "「切分没跑成」是真矛盾"


def test_an_unknown_rebind_code_is_reported_at_the_loudest_level():
    """`rebind` 将来加一条新码、级别表却没跟上时：按**最响**的那一级报，不许安静降级。

    这条兜底分支也要有自己的测试（编排裁决 D9：每个跳过／兜底分支至少一条测试）——
    安静地把一条真矛盾降成 `hint`，正是这套检查要防的失败。
    """
    fact = {"code": "some_brand_new_code", "message": "页上出了点新花样"}

    warning = segmentation._reemit(fact)

    assert warning == {"code": "some_brand_new_code", "message": "页上出了点新花样",
                       "id": None, "level": "warning"}
