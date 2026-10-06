"""页资源的「改」动作（工单 #14、spec #2 的手动修正最小集合）。

测的是**行为**：一份页 + 一条修正进去，一个改完的页 + 一份「我做了什么」的报告出来。
不测内部函数名、不联网、不碰真照片（夹具全是构造的块列表与临时目录）。

**验收 3** 的核心断言在这里：修正 → 写回页文件 → 重读盘上的那个文件能看到变化
（不是只存在内存里）。所以每条「真改了」的测试都从**重新读盘**验一遍。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from conftest import make_card
from server import intake, page_edit, pages

AT = datetime(2026, 10, 5, 12, 0, 0, tzinfo=timezone.utc)
PAGE_ID = "41c86bcfc007"


def block(block_id, box, **overrides):
    """一个页文件里的块（形状照契约 §10.2.1）。"""
    out = {
        "id": block_id,
        "bbox_norm": box,
        "bbox_px": [0, 0, 10, 10],
        "card_id": None,
        "keep": None,
    }
    out.update(overrides)
    return out


def page(blocks, **overrides):
    out = {
        "version": 1,
        "id": PAGE_ID,
        "image": f"{PAGE_ID}.png",
        "created_at": "2026-10-04T14:31:35+08:00",
        "origin": {"original_file": "2.png", "sheet": None, "page_number": None},
        "blocks": blocks,
    }
    out.update(overrides)
    return out


def three_blocks():
    return page([
        block("b1", [0.02, 0.02, 0.9, 0.2], question_no=1),
        block("b2", [0.02, 0.24, 0.9, 0.2], question_no=2),
        block("b3", [0.02, 0.46, 0.9, 0.2], question_no=3),
    ])


# ---------------------------------------------------------------- 拖边界


def test_moving_a_boundary_writes_the_normalised_box_and_drops_the_stale_pixel_box():
    """拖边界改的是 `bbox_norm`（D5 的规范基准）。

    `bbox_px` 是盘上原值（加了 1.5% pad），拖动之后它**必然过期**——留着它，
    下游就会照着旧位置算红笔统计。所以清成 `None` 并显式报出来，不留「安静的谎」。
    """
    result = page_edit.move_block(three_blocks(), "b2", [0.05, 0.3, 0.8, 0.18], at=AT)

    assert result["changed"] is True
    moved = result["page"]["blocks"][1]
    assert moved["bbox_norm"] == [0.05, 0.3, 0.8, 0.18]
    assert moved["bbox_px"] is None
    assert page_edit.BLOCK_BOX_CHANGED in [w["code"] for w in result["warnings"]]
    # 其余块一个字节都没动
    assert result["page"]["blocks"][0] == three_blocks()["blocks"][0]


def test_moving_a_boundary_we_cannot_read_is_reported_and_changes_nothing():
    """读不出来的边界 → 明确报出来，页文件不动（不是静默改成一个零框）。"""
    before = three_blocks()
    result = page_edit.move_block(before, "b1", [0.1, 0.2, 0.0, 0.3], at=AT)

    assert result["changed"] is False
    assert result["page"] is before
    assert any(w["code"] == page_edit.BOX_NOT_USABLE for w in result["warnings"])


def test_moving_a_block_that_is_not_on_the_page_is_reported():
    """D9：拒绝路径要有测试，且断言形状（状态码/码 + 关键字段）。"""
    result = page_edit.move_block(three_blocks(), "b9", [0.1, 0.1, 0.1, 0.1], at=AT)

    assert result["changed"] is False
    assert result["page"]["blocks"] == three_blocks()["blocks"]
    unknown = [w for w in result["warnings"] if w["code"] == page_edit.BLOCK_UNKNOWN]
    assert unknown and "b9" in unknown[0]["message"]
    assert unknown[0]["level"] == "warning"


def test_moving_a_block_to_where_it_already_is_is_a_no_op_that_says_so():
    """重复提交幂等，而且**说出来**（不许当作一次成功的改动）。"""
    before = three_blocks()
    result = page_edit.move_block(before, "b1", [0.02, 0.02, 0.9, 0.2], at=AT)

    assert result["changed"] is False
    assert any(w["code"] == page_edit.BLOCK_EDIT_NOOP for w in result["warnings"])


# ---------------------------------------------------------------- 合并两块


def test_merging_two_blocks_takes_the_union_of_their_boxes():
    """合并的边界取**并集**（能盖住两块的框），并掉的块从列表里移除。"""
    result = page_edit.merge_blocks(three_blocks(), ["b1", "b2"], at=AT)

    assert result["changed"] is True
    assert [b["id"] for b in result["page"]["blocks"]] == ["b1", "b3"]
    merged = result["page"]["blocks"][0]
    # b1 的 y 区间是 [0.02, 0.22]、b2 的是 [0.24, 0.44] → 并集 y0=0.02、y1=0.44、h=0.42
    # （x 与宽两块相同，所以那两维不变）
    assert merged["bbox_norm"] == pytest.approx([0.02, 0.02, 0.9, 0.42])
    assert merged["merged_from"] == ["b1", "b2"]
    assert result["blocks_removed"] == ["b2"]


def test_merging_fewer_than_two_blocks_is_an_explicit_error():
    """D9：合并要两块，给一块是明确错误形状（不是静默成功）。"""
    before = three_blocks()
    result = page_edit.merge_blocks(before, ["b1"], at=AT)

    assert result["changed"] is False
    assert result["page"] is before
    codes = [w["code"] for w in result["warnings"]]
    assert page_edit.MERGE_REQUIRES_TWO in codes


def test_merging_blocks_with_different_types_takes_the_first_and_says_so():
    """**歧义要报出来**：合并的两块题型不同 → 取第一块，另报一条 hint。"""
    blocks = page([
        block("b1", [0.02, 0.02, 0.9, 0.2], problem_type="choice"),
        block("b2", [0.02, 0.24, 0.9, 0.2], problem_type="solution"),
    ])
    result = page_edit.merge_blocks(blocks, ["b1", "b2"], at=AT)

    assert result["page"]["blocks"][0]["problem_type"] == "choice"
    conflict = [w for w in result["warnings"] if w["code"] == page_edit.MERGE_DIFFERENT_TYPES]
    assert conflict and conflict[0]["level"] == "hint"


def test_merging_a_block_that_carries_a_card_says_the_binding_is_not_carried_over():
    """**丢弃/合并块上已有题卡绑定怎么办**：明确报出来，而不是猜一个。

    两张卡不能同时绑到同一块上（一个块一个 `card_id`），所以被并掉那一块的卡
    会「在这一页上没有块」——`page_binding_lost` 会另外喊，但这里要当场说清。
    """
    blocks = page([
        block("b1", [0.02, 0.02, 0.9, 0.2], card_id="p-20261004-aaaaaa"),
        block("b2", [0.02, 0.24, 0.9, 0.2], card_id="p-20261004-bbbbbb"),
    ])
    result = page_edit.merge_blocks(blocks, ["b1", "b2"], at=AT)

    assert result["page"]["blocks"][0]["card_id"] == "p-20261004-aaaaaa"
    lost = [w for w in result["warnings"] if w["code"] == page_edit.DROP_WITH_CARD_BINDING]
    assert lost and "p-20261004-bbbbbb" in lost[0]["message"]
    # 契约 §2 的警告形状：`id` 是**属于哪张卡**（卡级警告填卡 id，页级为 null）
    assert lost[0]["id"] == "p-20261004-bbbbbb"
    assert lost[0]["level"] == "warning"


def test_merging_points_the_unknown_block_out():
    """点名的块里有一个不在页上 → 报出来，不合并。"""
    before = three_blocks()
    result = page_edit.merge_blocks(before, ["b1", "b7"], at=AT)

    assert result["changed"] is False
    assert any(w["code"] == page_edit.BLOCK_UNKNOWN for w in result["warnings"])


# ---------------------------------------------------------------- 拆分一块


def test_splitting_a_block_keeps_the_binding_and_decision_on_the_first_piece():
    """拆分的歧义（**卡片绑定怎么办**）：绑定只跟第一块，其余块 `None` = 还没入库。

    其余块的 `keep` 是 `None`（**待定**）——不是 `False`（不收）。「待定」会显式报出来，
    静默变成「不收」就是 spec #2 最怕的那种丢题。
    """
    blocks = page([block("b1", [0.02, 0.02, 0.9, 0.4], card_id="p-20261004-aaaaaa",
                         keep=True, question_no=7, problem_type="choice",
                         decision={"keep": True, "rule": "error_trace", "source": "model",
                                   "semantics": "correction", "reason": "红笔是订正"})])
    result = page_edit.split_block(
        blocks, "b1", [[0.02, 0.02, 0.9, 0.19], [0.02, 0.23, 0.9, 0.19]], at=AT)

    assert result["changed"] is True
    first, second = result["page"]["blocks"]
    assert first["id"] == "b1"
    assert first["card_id"] == "p-20261004-aaaaaa"
    assert first["keep"] is True
    assert first["decision"]["rule"] == "error_trace"
    assert second["id"] == "b1s2"
    assert second["card_id"] is None
    assert second["keep"] is None
    carried = [w for w in result["warnings"]
               if w["code"] == page_edit.SPLIT_CARD_BINDING_NOT_CARRIED]
    assert carried and carried[0]["level"] == "hint"


def test_splitting_without_question_numbers_says_so_instead_of_guessing():
    """**歧义要报出来**：拆出来的题号怎么给——第一块继承原块，其余留空并报出来。

    题号连续性检查（spec #2 三条判据里最强的一条）靠题号报得对，所以这里
    **不猜、不自动编号**，只把「你还没给」说出来。
    """
    blocks = page([block("b1", [0.02, 0.02, 0.9, 0.4], question_no=7)])
    result = page_edit.split_block(
        blocks, "b1", [[0.02, 0.02, 0.9, 0.19], [0.02, 0.23, 0.9, 0.19]], at=AT)

    first, second = result["page"]["blocks"]
    assert first["question_no"] == 7      # 第一块继承（它本来就是从那一块来的）
    assert second["question_no"] is None  # 不猜
    unset = [w for w in result["warnings"]
             if w["code"] == page_edit.SPLIT_QUESTION_NUMBERS_UNSET]
    assert unset and unset[0]["level"] == "hint"


def test_splitting_with_given_question_numbers_writes_them():
    """人给了题号就照写——报出来的歧义有了一条出路。"""
    blocks = page([block("b1", [0.02, 0.02, 0.9, 0.4], question_no=7)])
    result = page_edit.split_block(
        blocks, "b1", [[0.02, 0.02, 0.9, 0.19], [0.02, 0.23, 0.9, 0.19]],
        question_numbers=[7, 8], at=AT)

    assert [b["question_no"] for b in result["page"]["blocks"]] == [7, 8]
    assert not [w for w in result["warnings"]
                if w["code"] == page_edit.SPLIT_QUESTION_NUMBERS_UNSET]


def test_splitting_into_one_piece_is_an_explicit_error():
    """D9：拆分至少要两块，给一块是明确错误形状。"""
    before = three_blocks()
    result = page_edit.split_block(before, "b1", [[0.02, 0.02, 0.9, 0.2]], at=AT)

    assert result["changed"] is False
    assert result["page"] is before
    assert page_edit.SPLIT_INTO_ONE in [w["code"] for w in result["warnings"]]


def test_splitting_with_a_bad_question_number_does_not_invent_one():
    """题号不是正整数 → 按「还没定」处理并报出来，不许 `int()` 硬转（那是编一个号）。"""
    blocks = page([block("b1", [0.02, 0.02, 0.9, 0.4], question_no=7)])
    result = page_edit.split_block(
        blocks, "b1", [[0.02, 0.02, 0.9, 0.19], [0.02, 0.23, 0.9, 0.19]],
        question_numbers=[7, 8.5], at=AT)

    assert result["page"]["blocks"][1]["question_no"] is None
    assert page_edit.QUESTION_NO_INVALID in [w["code"] for w in result["warnings"]]


# ---------------------------------------------------------------- 整块丢弃 / 切换收入


def test_dropping_a_block_goes_through_the_intake_decision_entry():
    """**切换收入／丢弃改的是决策，不是另立一套判断**（spec #2 跨单元表）。

    所以丢弃走 `intake.set_keep_by_human`：`rule = human_drop`、`source = human`。
    丢弃**不删块**——块留在页文件里带 `keep: false`，这样「为什么没入库」查得出来。
    """
    blocks = page([block("b1", [0.02, 0.02, 0.9, 0.2], card_id="p-20261004-aaaaaa")])
    result = page_edit.drop_block(blocks, "b1", at=AT)

    dropped = result["page"]["blocks"][0]
    assert dropped["keep"] is False
    assert dropped["decision"]["rule"] == intake.RULE_HUMAN_DROP
    assert dropped["decision"]["source"] == intake.SOURCE_HUMAN
    assert len(result["page"]["blocks"]) == 1, "丢弃不是删块（删掉它就是静默丢题）"


def test_switching_a_block_to_keep_goes_through_the_same_entry():
    """切换收入走同一个入口（`human_include`）——人的判断只有一处写法。"""
    blocks = page([block("b1", [0.02, 0.02, 0.9, 0.2], keep=False,
                         decision={"keep": False, "rule": "no_red_ink",
                                   "source": "statistics", "semantics": None,
                                   "reason": "没有红笔痕迹"})])
    result = page_edit.keep_block(blocks, "b1", at=AT)

    kept = result["page"]["blocks"][0]
    assert kept["keep"] is True
    assert kept["decision"]["rule"] == intake.RULE_HUMAN_INCLUDE
    assert kept["decision"]["source"] == intake.SOURCE_HUMAN


def test_dropping_a_block_that_carries_a_card_says_the_card_is_still_there():
    """**丢弃块上已有题卡绑定怎么办**：明确报出来（卡不会被自动删）。

    「不入库」管的是这一次修正之后的块；已经生成的卡要不要删是另一件事（#15）。
    不许静默地让人以为丢弃 = 卡也没了。
    """
    blocks = page([block("b1", [0.02, 0.02, 0.9, 0.2], card_id="p-20261004-aaaaaa")])
    result = page_edit.drop_block(blocks, "b1", at=AT)

    note = [w for w in result["warnings"] if w["code"] == page_edit.DROP_WITH_CARD_BINDING]
    assert note and "p-20261004-aaaaaa" in note[0]["message"]


def test_toggling_a_decision_that_is_already_that_way_is_a_no_op():
    """幂等：本来就已收，再收一次 = 没动 + 说出来。"""
    blocks = page([block("b1", [0.02, 0.02, 0.9, 0.2], keep=True)])
    result = page_edit.keep_block(blocks, "b1", at=AT)

    assert result["changed"] is False
    assert any(w["code"] == page_edit.BLOCK_EDIT_NOOP for w in result["warnings"])


# ---------------------------------------------------------------- 改题型 / 改题号


def test_setting_the_problem_type_writes_it():
    """改题型：写进块的 `problem_type`（契约 §3 的三取值）。"""
    result = page_edit.set_problem_type(three_blocks(), "b1", "solution", at=AT)

    assert result["page"]["blocks"][0]["problem_type"] == "solution"
    assert result["changed"] is True


def test_a_problem_type_outside_the_enum_is_rejected_and_shouted_about():
    """D9：拼错的题型会**静默**让「解答题没有标准答案属正常」那条判据失效——要拒绝。"""
    before = three_blocks()
    result = page_edit.set_problem_type(before, "b1", "multiple_choice", at=AT)

    assert result["changed"] is False
    assert result["page"] is before
    refused = [w for w in result["warnings"] if w["code"] == page_edit.TYPE_UNKNOWN]
    assert refused and "solution" in refused[0]["message"]   # 把可取值说出来


def test_the_type_can_be_cleared_back_to_undecided():
    """`None`（还没定）与 `"solution"`（解答题）是两件事——要能明确退回未定。"""
    blocks = page([block("b1", [0.02, 0.02, 0.9, 0.2], problem_type="choice")])
    result = page_edit.set_problem_type(blocks, "b1", None, at=AT)

    assert result["page"]["blocks"][0]["problem_type"] is None


def test_setting_the_question_number_writes_it():
    """改题号：写进块的 `question_no`（题号连续性检查的唯一输入）。"""
    result = page_edit.set_question_no(three_blocks(), "b3", 18, at=AT)

    assert result["page"]["blocks"][2]["question_no"] == 18


@pytest.mark.parametrize("bad", ["17", 17.5, 0, -3, True])
def test_a_question_number_that_is_not_a_positive_integer_is_rejected(bad):
    """非正整数一律拒绝——不许 `int()` 硬转，那是**编一个号**（最强的检查会失灵）。"""
    before = three_blocks()
    result = page_edit.set_question_no(before, "b1", bad, at=AT)

    assert result["changed"] is False
    assert result["page"] is before
    assert page_edit.QUESTION_NO_INVALID in [w["code"] for w in result["warnings"]]


def test_a_question_number_can_be_cleared_to_unreadable():
    """看不清就留空——`None` 是允许的（不许猜一个，同 `segmentation` 的口径）。"""
    result = page_edit.set_question_no(three_blocks(), "b1", None, at=AT)

    assert result["page"]["blocks"][0]["question_no"] is None


# ---------------------------------------------------------------- 新增一块（#24/#25/#26）


def test_adding_a_block_gets_a_fresh_id_in_the_same_number_space_as_the_model_blocks():
    """新块的 id 与机器切出来的块**共用一套命名**（`b<序号>`）——不许两套编号空间。

    序号取页上已有的那些 `b<序号>` 里最大的 +1，所以它**不会撞上任何已有的 id**。
    """
    result = page_edit.add_block(three_blocks(), [0.02, 0.68, 0.9, 0.2],
                                 question_no=4, at=AT)

    assert result["changed"] is True
    assert [b["id"] for b in result["page"]["blocks"]] == ["b1", "b2", "b3", "b4"]
    added = result["page"]["blocks"][-1]
    assert added["bbox_norm"] == [0.02, 0.68, 0.9, 0.2]
    assert added["question_no"] == 4
    assert result["blocks_changed"] == ["b4"]

    # 页上有个洞（b2 被删过）也不复用那个号：重复用一个刚删掉的号，会让留痕里那条
    # 「b2 被删过」看起来像在说一个还活着的块。
    holed = page([block("b1", [0.02, 0.02, 0.9, 0.2]),
                  block("b3", [0.02, 0.46, 0.9, 0.2])])
    assert page_edit.add_block(holed, [0.02, 0.68, 0.9, 0.2], at=AT)[
        "page"]["blocks"][-1]["id"] == "b4"


def test_a_hand_drawn_block_defaults_to_keep_and_carries_no_machine_readings():
    """#26：**手工块默认「收」**——人自己画的框就是他要的题。

    这一块的 `bbox_px`／`card_id`／`ink` 都是 `None`：人画的框没有盘上像素原值、
    还没入库（分配在入库那一刻）、红笔统计要等一次 `intake`——**一项都不许编**。

    但 `decision` **不是** `None`：默认「收」是一次**人的判断**，得记成 `source = "human"`。
    不记的话，下一次 `intake --apply` 就会拿像素统计重判它（`plan_decisions` 只保留
    human 的决策）——「不再拿像素统计否决他」那句话当场作废。
    """
    result = page_edit.add_block(three_blocks(), [0.02, 0.68, 0.9, 0.2], at=AT)

    added = result["page"]["blocks"][-1]
    assert added["keep"] is True
    assert added["bbox_px"] is None and added["card_id"] is None
    assert added["ink"] is None
    assert added["problem_type"] is None
    assert added["decision"]["keep"] is True
    assert added["decision"]["source"] == intake.SOURCE_HUMAN
    assert added["decision"]["rule"] == intake.RULE_HUMAN_INCLUDE


@pytest.mark.parametrize("given,expected", [(None, None), (False, False), (True, True)])
def test_an_explicit_keep_on_a_new_block_is_honoured(given, expected):
    """「没给 `keep`」与「明确给了 `null`／`false`」是两件事：后者照给，不被默认值吃掉。

    `null`（待定）**没有**决策记录——「还没有人做决定」与「人说了待定」是同一件事，
    那一条留给 `intake` 判；`false` 则要记成 `human_drop`，否则自动决策会把它翻回去。
    """
    result = page_edit.add_block(three_blocks(), [0.02, 0.68, 0.9, 0.2],
                                 keep=given, at=AT)

    added = result["page"]["blocks"][-1]
    assert added["keep"] is expected
    if expected is None:
        assert added["decision"] is None
    else:
        assert added["decision"]["keep"] is expected
        assert added["decision"]["source"] == intake.SOURCE_HUMAN


def test_adding_a_block_with_an_unusable_box_is_the_same_rejection_shape_as_move():
    """读不出来的框：**一类错误只有一种说法**——与 `move` 同一个码、同一种形状（D9）。"""
    before = three_blocks()
    refused = page_edit.add_block(before, [0.1, 0.2, 0.0, 0.3], at=AT)
    moved = page_edit.move_block(three_blocks(), "b1", [0.1, 0.2, 0.0, 0.3], at=AT)

    assert refused["changed"] is False
    assert refused["page"] is before
    refused_box = [w for w in refused["warnings"] if w["code"] == page_edit.BOX_NOT_USABLE]
    moved_box = [w for w in moved["warnings"] if w["code"] == page_edit.BOX_NOT_USABLE]
    assert refused_box and moved_box
    assert set(refused_box[0]) == set(moved_box[0])
    assert refused_box[0]["level"] == moved_box[0]["level"] == "warning"


def test_adding_a_block_with_a_type_outside_the_enum_is_rejected():
    """拼错的题型会**静默**让「解答题没有标准答案属正常」那条判据失效——照既有形状拒绝。"""
    before = three_blocks()
    result = page_edit.add_block(before, [0.02, 0.68, 0.9, 0.2],
                                 problem_type="multiple_choice", at=AT)

    assert result["changed"] is False
    assert result["page"] is before
    refused = [w for w in result["warnings"] if w["code"] == page_edit.TYPE_UNKNOWN]
    assert refused and "solution" in refused[0]["message"]      # 把可取值说出来


@pytest.mark.parametrize("bad", ["4", 4.5, 0, -1, True])
def test_adding_a_block_with_a_question_number_that_is_not_a_positive_integer_is_rejected(bad):
    """编一个题号会让「题号连续性」这条最强的检查失灵：非正整数一律拒绝。"""
    before = three_blocks()
    result = page_edit.add_block(before, [0.02, 0.68, 0.9, 0.2], question_no=bad, at=AT)

    assert result["changed"] is False
    assert result["page"] is before
    assert page_edit.QUESTION_NO_INVALID in [w["code"] for w in result["warnings"]]


def test_adding_a_block_works_on_a_page_that_had_no_block_list_at_all():
    """切分不可用的页（`blocks: null`）**也能手动画框**——这正是它存在的理由。

    画完之后 `blocks` 是**真的列表**、来源是 `manual`：契约 §10.2.1 那条
    「`mode == "unavailable"` 时 `blocks` 必须是 `null`」仍然成立——来源已经不是它了。
    """
    blank = page(None, segmentation={"mode": "unavailable", "at": None,
                                     "note": "切分不可用：等人手动画框"})
    result = page_edit.add_block(blank, [0.02, 0.02, 0.9, 0.2], question_no=1, at=AT)

    assert result["changed"] is True
    assert [b["id"] for b in result["page"]["blocks"]] == ["b1"]
    assert result["page"]["segmentation"]["mode"] == page_edit.MODE_MANUAL
    assert result["page"]["segmentation"]["note"] == "切分不可用：等人手动画框"


# ---------------------------------------------------------------- 删块（#30）


def test_a_block_that_carries_a_card_can_never_be_deleted():
    """**硬约束 1**：已绑 `card_id` 的块永远不许删。

    那张卡已经存在，删块会造出一条**孤儿绑定**。要「不要它」只能用既有的 `drop`
    （不收，块留在页文件里、带 `decision.rule = "human_drop"`，可审计）。
    """
    from server.errors import ApiError

    blocks = page([block("b1", [0.02, 0.02, 0.9, 0.2], card_id="p-20261004-aaaaaa")])

    with pytest.raises(ApiError) as excinfo:
        page_edit.delete_block(blocks, "b1", at=AT)

    payload = excinfo.value.payload()
    assert excinfo.value.status == 400
    assert payload["code"] == "bad_request"
    assert payload["reason"] == page_edit.DELETE_BOUND_TO_CARD
    assert payload["details"]["param"] == "block_id"
    assert payload["details"]["card_id"] == "p-20261004-aaaaaa"


def test_deleting_a_never_committed_block_leaves_exactly_one_trace(api_for):
    """**硬约束 2**：未入库的块可以删，但删除**必须留痕**——绝不静默消失。

    留痕里那一行是 `{block_id, bbox_norm, question_no, removed_at}`，**永远不带 `card_id`**
    （带了就等于「删块那道闸破了」）。
    """
    api = api_for([])
    api.catalog.pages_dir.mkdir(parents=True, exist_ok=True)
    pages.save_page(api.catalog, three_blocks(), page_id=PAGE_ID, apply=True)

    report = page_edit.apply_edit(api.catalog, PAGE_ID,
                                  [{"action": "delete", "block_id": "b2"}], at=AT, apply=True)

    assert report["changed"] is True
    assert report["blocks_removed"] == 1
    on_disk = json.loads((api.catalog.pages_dir / f"{PAGE_ID}.json").read_text("utf-8"))
    assert [b["id"] for b in on_disk["blocks"]] == ["b1", "b3"]
    assert len(on_disk["removed_blocks"]) == 1
    entry = on_disk["removed_blocks"][0]
    assert set(entry) == {"block_id", "bbox_norm", "question_no", "removed_at"}
    assert entry["block_id"] == "b2"
    assert entry["bbox_norm"] == [0.02, 0.24, 0.9, 0.2]
    assert entry["question_no"] == 2
    assert entry["removed_at"] == AT.isoformat()


def test_deleting_a_block_that_is_not_on_the_page_is_the_same_rejection_as_move():
    """点不到那块 → 与 `move`／`drop` 同一个拒绝形状（`BLOCK_UNKNOWN`、页文件不动）。"""
    before = three_blocks()
    result = page_edit.delete_block(before, "b9", at=AT)

    assert result["changed"] is False
    assert result["page"] is before
    unknown = [w for w in result["warnings"] if w["code"] == page_edit.BLOCK_UNKNOWN]
    assert unknown and unknown[0]["level"] == "warning"


def test_a_refused_delete_writes_nothing_at_all(api_for):
    """拒绝就该**一个字节都不动**（D9）：同一次请求里前半条已经改好的修正也不许落盘。"""
    from server.errors import ApiError

    api = api_for([])
    api.catalog.pages_dir.mkdir(parents=True, exist_ok=True)
    pages.save_page(api.catalog, page([
        block("b1", [0.02, 0.02, 0.9, 0.2], question_no=1),
        block("b2", [0.02, 0.24, 0.9, 0.2], card_id="p-20261004-aaaaaa"),
    ]), page_id=PAGE_ID, apply=True)
    page_file = api.catalog.pages_dir / f"{PAGE_ID}.json"
    before = page_file.read_bytes()

    with pytest.raises(ApiError) as excinfo:
        page_edit.apply_edit(api.catalog, PAGE_ID, [
            {"action": "question_no", "block_id": "b1", "question_no": 17},
            {"action": "delete", "block_id": "b2"},
        ], at=AT, apply=True)

    assert excinfo.value.payload()["reason"] == page_edit.DELETE_BOUND_TO_CARD
    assert page_file.read_bytes() == before


# ---------------------------------------------------------------- 块列表的来源（#17 §4）


def test_any_successful_human_edit_turns_the_block_list_into_a_manual_one():
    """人一改块列表，这份列表的来源就是人（`manual`）——机器预设不再是它的来源。"""
    result = page_edit.move_block(three_blocks(), "b1", [0.0, 0.0, 1.0, 0.3], at=AT)

    assert result["page"]["segmentation"]["mode"] == page_edit.MODE_MANUAL
    assert result["page"]["segmentation"]["at"] == AT.isoformat()


def test_a_no_op_edit_does_not_claim_the_block_list_became_manual():
    """没动的修正（重复提交幂等）不许动来源：它一个字节都没改。"""
    before = three_blocks()
    result = page_edit.move_block(before, "b1", [0.02, 0.02, 0.9, 0.2], at=AT)

    assert result["changed"] is False
    assert result["page"] is before
    assert "segmentation" not in before


def test_both_new_actions_also_mark_the_block_list_as_manual():
    """`add` 与 `delete` 也算人给的列表（同一个来源口径，不许漏一条路）。"""
    added = page_edit.add_block(three_blocks(), [0.02, 0.68, 0.9, 0.2], at=AT)
    assert added["page"]["segmentation"]["mode"] == page_edit.MODE_MANUAL

    deleted = page_edit.delete_block(page([block("b1", [0.02, 0.02, 0.9, 0.2])]),
                                     "b1", at=AT)
    assert deleted["page"]["segmentation"]["mode"] == page_edit.MODE_MANUAL


def test_an_old_page_without_a_segmentation_key_reads_as_a_machine_preset():
    """ADR 0008 时代的页文件**没有** `segmentation` 这个键：按 `model` 读，**不许崩**。

    这就是「旧的页文件不许因为新字段变成脏数据」那条老规矩在新字段上的落点；人一动它，
    这个键就地懒建出来，而不是把旧页判成坏页。
    """
    old = three_blocks()
    assert "segmentation" not in old

    assert page_edit.segmentation_of(old)["mode"] == page_edit.MODE_MODEL
    assert page_edit.has_human_work(old) is False
    assert page_edit.discarded_manual_work(old) == {
        "manual_blocks": 0, "removed_blocks": 0, "mode_before": page_edit.MODE_MODEL}

    edited = page_edit.set_question_no(old, "b1", 5, at=AT)["page"]
    assert edited["segmentation"]["mode"] == page_edit.MODE_MANUAL


def test_a_manual_list_or_a_trace_both_count_as_human_work():
    """「有人工改动」= 块列表是人给的，**或者**删过块（留痕不空）——两条判据都要在。"""
    manual = page([block("b1", [0.02, 0.02, 0.9, 0.2])],
                  segmentation={"mode": "manual", "at": None, "note": None})
    assert page_edit.has_human_work(manual) is True
    assert page_edit.discarded_manual_work(manual) == {
        "manual_blocks": 1, "removed_blocks": 0, "mode_before": "manual"}

    trimmed = page([block("b1", [0.02, 0.02, 0.9, 0.2])],
                   segmentation={"mode": "model", "at": None, "note": None},
                   removed_blocks=[{"block_id": "b9", "bbox_norm": [0.5, 0.5, 0.1, 0.1],
                                    "question_no": 9,
                                    "removed_at": "2026-10-04T15:00:00+08:00"}])
    assert page_edit.has_human_work(trimmed) is True
    assert page_edit.discarded_manual_work(trimmed) == {
        "manual_blocks": 0, "removed_blocks": 1, "mode_before": "model"}


# ---------------------------------------------------------------- 重置为预设（#31）


def test_resetting_to_the_preset_swaps_the_blocks_and_records_the_lost_ones():
    """「重置为预设」：块列表换成新预设，没进新列表的旧块进 `removed_blocks[]` 留痕。

    留痕的形状与 `delete` 那条路**一模一样**（同一本账、同一个形状）。回执报的是
    **真的**丢了几块：409 那份粗估会说 2（`mode == "manual"` 时页上有几块就报几块），
    而这里只丢掉了 1 块——多的那一块被新预设按位置配上了。
    """
    from server import segmentation

    manual = page([
        block("b1", [0.02, 0.02, 0.9, 0.2], question_no=1),
        block("b2", [0.02, 0.24, 0.9, 0.2], question_no=2),
    ], segmentation={"mode": "manual", "at": "2026-10-04T15:00:00+08:00", "note": None})
    report = segmentation.classify_resegment(manual, [
        {"id": "b1", "bbox_norm": [0.02, 0.02, 0.9, 0.1], "question_no": 1}])

    result = page_edit.reset_to_preset(manual, report, at=AT)

    assert page_edit.discarded_manual_work(manual)["manual_blocks"] == 2   # 粗估
    assert result["discarded"] == {"manual_blocks": 1, "removed_blocks": 1,
                                   "mode_before": "manual"}
    reset_page = result["page"]
    assert [b["id"] for b in reset_page["blocks"]] == ["b1"]
    assert reset_page["segmentation"]["mode"] == page_edit.MODE_MODEL
    assert reset_page["segmentation"]["at"] == AT.isoformat()
    entry = reset_page["removed_blocks"][0]
    assert set(entry) == {"block_id", "bbox_norm", "question_no", "removed_at"}
    assert entry["block_id"] == "b2" and entry["question_no"] == 2


def test_a_vanishing_block_that_carries_a_card_never_enters_the_trace():
    """**硬规矩**：留痕里永远不许出现带 `card_id` 的块。

    带卡的旧块在新预设里找不到位置 → 它确实从页上消失了，但**进不了 `removed_blocks[]`**
    （那正是「删块那道闸破了」的直接证据）；它由 `block_removed_with_card` 另外喊。
    """
    from server import segmentation

    manual = page([block("b1", [0.6, 0.6, 0.3, 0.2], card_id="p-20261004-aaaaaa")],
                  segmentation={"mode": "manual", "at": None, "note": None})
    report = segmentation.classify_resegment(manual, [
        {"id": "b1", "bbox_norm": [0.02, 0.02, 0.3, 0.2], "question_no": 1}])

    result = page_edit.reset_to_preset(manual, report, at=AT)

    assert result["page"]["removed_blocks"] == []
    assert result["discarded"]["manual_blocks"] == 0
    assert "block_removed_with_card" in [w["code"] for w in report["warnings"]]


def test_the_report_counts_only_the_explicit_adds_and_deletes(api_for):
    """回执里的 `blocks_removed`／`blocks_added` **只数 `add` / `delete` 两条显式的增删**。

    `merge`（两块并一块）与 `split`（一块拆几块）也会改块数，但那两样的结果在 `edits[]`
    与块列表里报——混进这两个数会让「这一块是谁删的」重新变模糊（契约 §10.2.1b 第 3 条）。
    **0 也报**，两个数也是对称的。
    """
    api = api_for([])
    api.catalog.pages_dir.mkdir(parents=True, exist_ok=True)
    pages.save_page(api.catalog, three_blocks(), page_id=PAGE_ID, apply=True)

    moved = page_edit.apply_edit(api.catalog, PAGE_ID,
                                 [{"action": "move", "block_id": "b1",
                                   "bbox_norm": [0.01, 0.01, 0.9, 0.2]}], at=AT)
    assert moved["blocks_removed"] == 0 and moved["blocks_added"] == 0

    merged = page_edit.apply_edit(api.catalog, PAGE_ID,
                                  [{"action": "merge", "block_ids": ["b1", "b2"]}], at=AT)
    assert merged["blocks_removed"] == 0, "合并改块数，但不是在删块"

    split = page_edit.apply_edit(api.catalog, PAGE_ID,
                                 [{"action": "split", "block_id": "b1",
                                   "boxes": [[0.02, 0.02, 0.9, 0.09],
                                             [0.02, 0.13, 0.9, 0.09]]}], at=AT)
    assert split["blocks_removed"] == 0 and split["blocks_added"] == 0, "拆分也不算增删"

    added = page_edit.apply_edit(api.catalog, PAGE_ID,
                                 [{"action": "add", "bbox_norm": [0.02, 0.68, 0.9, 0.2]}],
                                 at=AT)
    assert added["blocks_added"] == 1 and added["blocks_removed"] == 0

    deleted = page_edit.apply_edit(api.catalog, PAGE_ID,
                                   [{"action": "delete", "block_id": "b1s2"}], at=AT)
    assert deleted["blocks_removed"] == 1 and deleted["blocks_added"] == 0


# ---------------------------------------------------------------- 一份页的修正报告


def test_the_edit_report_says_what_it_did_and_what_it_did_not_do():
    """ADR 0007 第 6 条：每个结果带显式的「我做了什么、没做什么」。"""
    result = page_edit.move_block(three_blocks(), "b1", [0.0, 0.0, 1.0, 0.3], at=AT)

    assert result["action"] == "move"
    assert result["wrote_cards"] is False   # 修正不写题卡（那是「入库」动作，归 #15）
    assert result["wrote_page"] is False    # 写盘由调用方做，save_page 只有一处实现
    assert result["blocks_changed"] == ["b1"]


def test_a_block_list_with_a_non_object_is_shouted_about_not_skipped():
    """列表里混进非对象项 → 点名报出来（`proto/server.py:320-321` 的反面）。"""
    blocks = page([block("b1", [0.02, 0.02, 0.9, 0.2]), "坏掉的一项"])
    result = page_edit.move_block(blocks, "b1", [0.05, 0.05, 0.8, 0.3], at=AT)

    assert result["changed"] is True
    assert page_edit.BLOCK_NOT_AN_OBJECT in [w["code"] for w in result["warnings"]]


def test_the_edit_records_when_and_what_on_the_page_itself():
    """页文件是真相 → 它必须回答「上一次谁在什么时候改了什么」。"""
    result = page_edit.set_question_no(three_blocks(), "b2", 5, at=AT)

    assert result["page"]["updated_at"] == AT.isoformat()
    assert result["page"]["last_edit"]["action"] == "question_no"
    assert "b2" in result["page"]["last_edit"]["note"]


# ---------------------------------------------------------------- 写回页文件（验收 3）


def test_every_edit_is_written_back_to_the_page_file_not_just_the_ui(api_for):
    """**验收 3**：每次修正都写回页文件。所以从**盘上重读**能看到变化。

    这条是本 spec 的立身之本：「切分结果与收入决策一旦只存在于界面的内存里，
    『漏了一题』就永远查不出来」。
    """
    api = api_for([])
    api.catalog.pages_dir.mkdir(parents=True, exist_ok=True)
    pages.save_page(api.catalog, three_blocks(), page_id=PAGE_ID, apply=True)

    report = page_edit.apply_edit(
        api.catalog, PAGE_ID,
        [{"action": "move", "block_id": "b1", "bbox_norm": [0.0, 0.0, 1.0, 0.25]},
         {"action": "question_no", "block_id": "b1", "question_no": 17}],
        at=AT, apply=True)

    assert report["changed"] is True
    on_disk = json.loads((api.catalog.pages_dir / f"{PAGE_ID}.json").read_text("utf-8"))
    assert on_disk["blocks"][0]["bbox_norm"] == [0.0, 0.0, 1.0, 0.25]
    assert on_disk["blocks"][0]["question_no"] == 17
    assert on_disk["last_edit"]["action"] == "question_no"


def test_a_preview_writes_nothing_but_reports_the_same_thing(api_for):
    """预演一个字节都不写，但报告里说得出「真改会改成什么」（同一条代码路径）。"""
    api = api_for([])
    api.catalog.pages_dir.mkdir(parents=True, exist_ok=True)
    pages.save_page(api.catalog, three_blocks(), page_id=PAGE_ID, apply=True)
    before = (api.catalog.pages_dir / f"{PAGE_ID}.json").read_bytes()

    preview = page_edit.apply_edit(
        api.catalog, PAGE_ID,
        [{"action": "move", "block_id": "b1", "bbox_norm": [0.0, 0.0, 1.0, 0.25]}],
        at=AT, apply=False)

    assert (api.catalog.pages_dir / f"{PAGE_ID}.json").read_bytes() == before
    assert preview["changed"] is True
    assert preview["page"]["blocks"][0]["bbox_norm"] == [0.0, 0.0, 1.0, 0.25]
    assert preview["preview"] is True


def test_an_empty_edit_list_is_reported_and_the_page_is_untouched(api_for):
    """D9：空请求是「明确报出来」，不是一次静默的成功。"""
    api = api_for([])
    api.catalog.pages_dir.mkdir(parents=True, exist_ok=True)
    pages.save_page(api.catalog, three_blocks(), page_id=PAGE_ID, apply=True)
    before = (api.catalog.pages_dir / f"{PAGE_ID}.json").read_bytes()

    report = page_edit.apply_edit(api.catalog, PAGE_ID, [], at=AT, apply=True)

    assert report["changed"] is False
    assert (api.catalog.pages_dir / f"{PAGE_ID}.json").read_bytes() == before
    assert page_edit.EDIT_EMPTY in [w["code"] for w in report["warnings"]]


def test_an_unknown_action_is_rejected_inside_the_report():
    """不认识的修正动作 → 明确拒绝，页文件不动（不是静默忽略一项）。"""
    before = three_blocks()
    result = page_edit.apply_one(before, {"action": "rotate", "block_id": "b1"}, at=AT)

    assert result["changed"] is False
    assert result["page"] is before
    refused = [w for w in result["warnings"] if w["code"] == page_edit.EDIT_UNKNOWN_ACTION]
    assert refused and refused[0]["level"] == "warning"
    # 拒绝形状要说清可取值（D9：断言形状，不是「没崩」）
    assert "move" in refused[0]["message"]


def test_an_edit_item_that_is_not_an_object_is_rejected():
    """D9：修正项不是对象 → 明确拒绝那一项（不是静默跳过）。"""
    before = three_blocks()
    result = page_edit.apply_one(before, "把 b1 往右拖", at=AT)

    assert result["changed"] is False
    assert result["page"] is before
    refused = [w for w in result["warnings"] if w["code"] == page_edit.EDIT_NOT_AN_OBJECT]
    assert refused and refused[0]["level"] == "warning"


def test_a_successful_merge_and_split_announce_what_they_did():
    """合并/拆分成了要有一条会喊的记录（审计面：页文件要能回答「谁把这块并了」）。"""
    merged = page_edit.merge_blocks(three_blocks(), ["b1", "b2"], at=AT)
    assert page_edit.BLOCKS_MERGED in [w["code"] for w in merged["warnings"]]

    blocks = page([block("b1", [0.02, 0.02, 0.9, 0.4], question_no=7)])
    split = page_edit.split_block(blocks, "b1", [[0.02, 0.02, 0.9, 0.19],
                                                 [0.02, 0.23, 0.9, 0.19]],
                                  question_numbers=[7, 8], at=AT)
    assert page_edit.BLOCK_SPLIT in [w["code"] for w in split["warnings"]]


def test_every_problem_type_in_the_enum_is_accepted():
    """枚举里的三个取值都要能写进去（只测其中一个会让另外两个成为没人走过的分支）。"""
    for wanted in page_edit.PROBLEM_TYPES:
        blocks = page([block("b1", [0.02, 0.02, 0.9, 0.2], problem_type=None)])
        result = page_edit.set_problem_type(blocks, "b1", wanted, at=AT)
        assert result["page"]["blocks"][0]["problem_type"] == wanted


def test_a_page_id_that_could_escape_the_pages_dir_is_refused(api_for):
    """页 id 会拼进路径 → 校验必须发生在碰盘之前（#12 收的那个穿越缺口同一条）。

    拒绝时**一个字节都不许动**：`pages/` 外面不许出现文件（D9 第 2 条）。
    """
    from server.errors import ApiError

    api = api_for([])
    api.catalog.pages_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(ApiError) as excinfo:
        page_edit.apply_edit(api.catalog, "../../problems/p-20261004-aaaaaa",
                             [{"action": "drop", "block_id": "b1"}], at=AT)

    assert excinfo.value.status == 400
    assert excinfo.value.payload()["details"]["param"] == "page_id"
    assert list(api.catalog.pages_dir.glob("*.json")) == []


def test_a_page_that_is_not_there_is_a_404_not_a_crash(api_for):
    """D9：点名要一页，找不到就得说（404），不是崩。"""
    from server.errors import ApiError

    api = api_for([])
    api.catalog.pages_dir.mkdir(parents=True, exist_ok=True)

    with pytest.raises(ApiError) as excinfo:
        page_edit.apply_edit(api.catalog, "nosuchpage", [{"action": "drop", "block_id": "b1"}],
                             at=AT)

    assert excinfo.value.status == 404
    assert excinfo.value.payload()["code"] == "not_found"


def test_a_page_whose_payload_id_points_outside_the_pages_dir_is_refused(api_for):
    """**页载荷里的 `id` 不许决定写到哪**——写回路径只能由「我加载时用的那个页 id」定。

    这是一条真实的攻击路径（#12 的独立验证发现）：`pages.save_page` 用 `page["id"]`
    拼路径，所以一份 `id: "../problems/p-xxx"` 的页文件会让一次"改页"**覆盖一张真题卡**，
    而且报告里的 `page_path` 还是假的（指向没被碰过的那份页文件）——零警告。

    拒绝时**一个字节都不许动**：题卡与页文件都要原封不动（D9 第 2 条）。
    """
    from server.errors import ApiError

    api = api_for([make_card("p-20261004-aaaaaa")])
    api.catalog.pages_dir.mkdir(parents=True, exist_ok=True)
    # 文件名正常、载荷里 id 穿越。**直接写盘**造这份坏页文件：`save_page` 按载荷 id
    # 拼路径，所以它自己也不该被用来造这种文件（那是另一条已经收口的路）。
    (api.catalog.pages_dir / f"{PAGE_ID}.json").write_text(json.dumps({
        "version": 1, "id": "../problems/p-20261004-aaaaaa", "image": f"{PAGE_ID}.png",
        "blocks": [block("b1", [0.02, 0.02, 0.9, 0.2])],
    }, ensure_ascii=False), encoding="utf-8")
    page_file = api.catalog.pages_dir / f"{PAGE_ID}.json"
    card_file = api.catalog.problems_dir / "p-20261004-aaaaaa.json"
    page_before, card_before = page_file.read_bytes(), card_file.read_bytes()

    with pytest.raises(ApiError) as excinfo:
        page_edit.apply_edit(api.catalog, PAGE_ID,
                             [{"action": "question_no", "block_id": "b1", "question_no": 17}],
                             at=AT)

    assert excinfo.value.status == 400
    assert excinfo.value.payload()["details"]["param"] == "page_id"
    assert card_file.read_bytes() == card_before, "题卡绝不许被页载荷里的 id 带走"
    assert page_file.read_bytes() == page_before


def test_a_page_whose_image_is_not_a_plain_filename_is_refused(api_for):
    """派生症状：`image` 同样可穿越（读得到 `pages/` 外的文件）。

    页文件与照片**同目录并列**（D5），所以 `image` 只能是一个纯文件名——
    带路径分隔符或 `..` 的一律拒绝，一个字节都不写。
    """
    from server.errors import ApiError

    api = api_for([])
    api.catalog.pages_dir.mkdir(parents=True, exist_ok=True)
    # 同上：直接写盘造这份坏页文件（`save_page` 会按载荷 id 写到别处去）
    (api.catalog.pages_dir / f"{PAGE_ID}.json").write_text(json.dumps({
        "version": 1, "id": PAGE_ID, "image": "../../etc/passwd",
        "blocks": [block("b1", [0.02, 0.02, 0.9, 0.2])],
    }, ensure_ascii=False), encoding="utf-8")
    before = (api.catalog.pages_dir / f"{PAGE_ID}.json").read_bytes()

    with pytest.raises(ApiError) as excinfo:
        page_edit.apply_edit(api.catalog, PAGE_ID,
                             [{"action": "drop", "block_id": "b1"}], at=AT)

    assert excinfo.value.status == 400
    assert (api.catalog.pages_dir / f"{PAGE_ID}.json").read_bytes() == before


def test_a_chain_of_edits_is_applied_in_order_to_one_page(api_for):
    """一串修正是**按顺序**累加的（先合并再拖边界 ≠ 先拖再合并）。"""
    api = api_for([])
    api.catalog.pages_dir.mkdir(parents=True, exist_ok=True)
    pages.save_page(api.catalog, three_blocks(), page_id=PAGE_ID, apply=True)

    report = page_edit.apply_edit(
        api.catalog, PAGE_ID,
        [{"action": "merge", "block_ids": ["b1", "b2"]},
         {"action": "split", "block_id": "b1",
          "boxes": [[0.02, 0.02, 0.9, 0.2], [0.02, 0.24, 0.9, 0.2]],
          "question_numbers": [1, 2]},
         {"action": "drop", "block_id": "b3"}],
        at=AT, apply=True)

    assert [r["action"] for r in report["edits"]] == ["merge", "split", "drop"]
    on_disk = json.loads((api.catalog.pages_dir / f"{PAGE_ID}.json").read_text("utf-8"))
    assert [b["id"] for b in on_disk["blocks"]] == ["b1", "b1s2", "b3"]
    assert on_disk["blocks"][2]["keep"] is False


def test_the_edit_does_not_touch_any_card_file(api_for):
    """修正只动页文件：题卡一个字节都不改（题卡只在「入库」动作里生成，归 #15）。"""
    card = make_card("p-20261004-aaaaaa", **{"source.page_image": f"data/pages/{PAGE_ID}.png"})
    api = api_for([card])
    api.catalog.pages_dir.mkdir(parents=True, exist_ok=True)
    pages.save_page(api.catalog, page([
        block("b1", [0.02, 0.02, 0.9, 0.2], card_id="p-20261004-aaaaaa"),
    ]), page_id=PAGE_ID, apply=True)
    card_path = api.catalog.problems_dir / "p-20261004-aaaaaa.json"
    before = card_path.read_bytes()

    page_edit.apply_edit(api.catalog, PAGE_ID,
                         [{"action": "drop", "block_id": "b1"}], at=AT, apply=True)

    assert card_path.read_bytes() == before


# ---------------------------------------------------------------- 与重切的接续（验收 1）


def test_edited_blocks_still_reconcile_as_kept_after_a_resegment(api_for):
    """**验收 1** 的服务层那一半：手动修正之后重切同一页，位置重合的块仍然「保留」。

    人在界面上拖动过的边界本身就是重切要拿来做对照的位置——所以「修正过」与
    「重切保留」不冲突：只要边界还在原处，`rebind` 就认得出是同一个块。
    """
    from server import segmentation

    api = api_for([])
    api.catalog.pages_dir.mkdir(parents=True, exist_ok=True)
    blocks = page([
        block("b1", [0.02, 0.02, 0.9, 0.2], card_id="p-20261004-aaaaaa", keep=True,
              question_no=1),
    ])
    pages.save_page(api.catalog, blocks, page_id=PAGE_ID, apply=True)

    # 人改题号（不动边界）
    page_edit.apply_edit(api.catalog, PAGE_ID,
                         [{"action": "question_no", "block_id": "b1", "question_no": 17}],
                         at=AT, apply=True)
    after_edit = json.loads((api.catalog.pages_dir / f"{PAGE_ID}.json").read_text("utf-8"))

    # 重切返回同一个位置的块（模型重跑给出同样的框）
    report = segmentation.classify_resegment(
        after_edit, [{"id": "b1", "bbox_norm": [0.02, 0.02, 0.9, 0.2], "question_no": 17}])

    assert report["matches"][0]["state"] == "kept"
    assert report["summary"]["replaced"] == 0
    assert report["blocks"][0]["card_id"] == "p-20261004-aaaaaa"
    assert report["wrote_cards"] is False and report["wrote_page"] is False
