"""两套坐标基准的**显式换算**（契约 §10.2、§5、编排裁决 D5、工单 #14）。

一页照片上同时住着两套坐标，它们**不是同一个框的两种写法**：

| 基准 | 谁用它 | 形状 |
|---|---|---|
| **整页**（页／块） | `source.bbox_norm`、页文件块的 `bbox_norm`／`bbox_px` | `bbox_norm` 是 xywh，`bbox_px` 是 xyxy |
| **裁剪图**（题面图） | `Problem.clean.boxes_norm`／`clean.manual` | xywh，相对 `<pid>-problem.png` |

界面上「把块框画在整页照片上」与「把掩膜框画在题面裁剪图上」用的是两套坐标；
混用**不会报错，只会静默错位**（ADR 0007 第 6 条最怕的那一类失败）。
所以换算收在本模块唯一一处实现里，并由这份测试钉住——**不许各处现算**。

反例可复算（真实数据 `p-20261004-41c86b`）：
卡的 `source.bbox_norm = [0.02, 0.12, 0.76, 0.28]`、`bbox_px = [3, 20, 541, 79]`；
`clean.boxes_norm = [[0.58, 0.05, 0.08, 0.22]]`。把掩膜框当整页坐标画到页上，
x 会落在 0.58 而不是 0.02 + 0.58*0.76 ≈ 0.46——**差 0.12 个页宽，肉眼看得出来但不报错**。
"""

from __future__ import annotations

import pytest

from server import coords


# 真实数据 `data/problems/p-20261004-41c86b.json` 的那一组数（只读核对过）。
REAL_PAGE_BOX_NORM = [0.02, 0.12, 0.76, 0.28]
REAL_PAGE_BOX_PX = [3, 20, 541, 79]
REAL_MASK_BOX_IN_CROP = [0.58, 0.05, 0.08, 0.22]


# ---------------------------------------------------------------- 形状：xywh vs xyxy


def test_the_two_page_fields_have_different_shapes_not_two_spellings():
    """`bbox_norm` 是 xywh、`bbox_px` 是 xyxy——**形状不同**（契约 §10.2.1 的对照表）。

    把它们当成同一个框的两种写法，是这个坑的根源：`[3, 20, 541, 79]` 当 xywh 读，
    宽高会变成 541×79 而不是 538×59。
    """
    assert coords.xywh_to_xyxy([0.02, 0.12, 0.76, 0.28]) == pytest.approx([0.02, 0.12, 0.78, 0.40])
    assert coords.xyxy_to_xywh([3, 20, 541, 79]) == pytest.approx([3, 20, 538, 59])


def test_the_page_box_px_is_not_a_reencoding_of_the_page_box_norm():
    """两个字段**不是同一个框**：`bbox_px` 加了 1.5% pad 又裁到页边界（D5）。

    所以换算不许拿 `bbox_norm` 重算 `bbox_px`（回填时照抄盘上原值）。
    这里钉住的是「换算函数只做形状转换、不发明 pad」。

    真实页照片是 680×190，纯比例换算得到 `[13.6, 22.8, 516.8, 53.2]`，
    与盘上的 `[3, 20, 541, 79]` **对不上**——差的就是那个 pad 与裁剪。
    """
    page = coords.norm_to_px(REAL_PAGE_BOX_NORM, width=680, height=190)

    assert page == pytest.approx([13.6, 22.8, 516.8, 53.2])
    assert page != pytest.approx(REAL_PAGE_BOX_PX, abs=1.0)


# ---------------------------------------------------------------- 整页 ↔ 裁剪图


def test_a_crop_box_becomes_a_page_box():
    """掩膜框（裁剪图）→ 整页框：先按块尺寸缩放，再平移到块的原点。

    `clean.boxes_norm` 是相对**题面裁剪图**归一化的，所以它落在页上的位置
    由「块在页上的位置 + 块在页上的大小」共同决定。
    """
    box = coords.crop_box_to_page(REAL_MASK_BOX_IN_CROP, REAL_PAGE_BOX_NORM)

    # x = 0.02 + 0.58*0.76 = 0.02 + 0.4408 = 0.4608
    # y = 0.12 + 0.05*0.28 = 0.12 + 0.014  = 0.134
    # w = 0.08 * 0.76 = 0.0608
    # h = 0.22 * 0.28 = 0.0616
    assert box == pytest.approx([0.4608, 0.134, 0.0608, 0.0616])
    # 反例：把掩膜框当整页坐标直接用，x 会是 0.58——差 0.12 个页宽（静默错位）。
    assert box[0] != pytest.approx(REAL_MASK_BOX_IN_CROP[0])


def test_a_page_box_becomes_a_crop_box_and_round_trips():
    """整页框 → 裁剪图框是上面那条的逆运算，一来一回要回到原值。"""
    page_box = coords.crop_box_to_page(REAL_MASK_BOX_IN_CROP, REAL_PAGE_BOX_NORM)
    back = coords.page_box_to_crop(page_box, REAL_PAGE_BOX_NORM)

    assert back == pytest.approx(REAL_MASK_BOX_IN_CROP)
    assert coords.crop_box_to_page(back, REAL_PAGE_BOX_NORM) == pytest.approx(page_box)


def test_a_mask_box_on_the_whole_page_is_the_question_the_ui_actually_asks():
    """界面的两个问题分别吃两种坐标，函数名必须让用错的那个显眼：

    - 「把块框画在整页照片上」→ 框本来就是整页坐标，**不换算**；
    - 「把掩膜框画在题面裁剪图上」→ 掩膜框本来就是裁剪图坐标，**不换算**；
    - 「把掩膜框画在整页照片上」（审核时要看掩膜在页上的哪儿）→ **要换算**。
    """
    # 掩膜落在块内：整页坐标下它必须仍在块的框里（这是换算对不对的硬判据）
    on_page = coords.crop_box_to_page(REAL_MASK_BOX_IN_CROP, REAL_PAGE_BOX_NORM)
    assert coords.contains_box(REAL_PAGE_BOX_NORM, on_page)

    # 不换算的话它会跑到块外面去——正是静默错位的样子
    assert not coords.contains_box(REAL_PAGE_BOX_NORM, REAL_MASK_BOX_IN_CROP)


def test_boxes_from_the_crop_are_read_the_way_the_contract_writes_them():
    """`clean.boxes_norm` / `manual.add` 是**一串**框，两种坐标的批量换算走同一个函数。"""
    boxes = [REAL_MASK_BOX_IN_CROP, [0.0, 0.0, 1.0, 1.0]]

    on_page = coords.crop_boxes_to_page(boxes, REAL_PAGE_BOX_NORM)

    assert on_page[0] == pytest.approx([0.4608, 0.134, 0.0608, 0.0616])
    # 铺满裁剪图的掩膜 = 恰好铺满这一块（换算是线性映射，两个端点必须对上）
    assert on_page[1] == pytest.approx(REAL_PAGE_BOX_NORM)


# ---------------------------------------------------------------- 读不出来的框


def test_a_box_that_cannot_be_read_is_none_and_never_a_zero_box():
    """「读不出来」与「在左上角」是两件事——`(0,0,0,0)` 会静默画在角上。

    `pages.usable_box` 是形状判据的唯一实现（#10 定的），本模块**消费它**，不重写。
    """
    assert coords.crop_box_to_page(None, REAL_PAGE_BOX_NORM) is None
    assert coords.crop_box_to_page([0.1, 0.2, 0.3], REAL_PAGE_BOX_NORM) is None
    assert coords.crop_box_to_page([0.1, 0.2, 0.0, 0.3], REAL_PAGE_BOX_NORM) is None
    assert coords.crop_box_to_page(REAL_MASK_BOX_IN_CROP, None) is None
    assert coords.crop_box_to_page(REAL_MASK_BOX_IN_CROP, [0.0, 0.0, 0.0, 1.0]) is None
    assert coords.crop_boxes_to_page([None, [1.0, 0.0, 0.1, 0.1]], REAL_PAGE_BOX_NORM) == [None, None]


def test_a_box_that_sticks_out_of_its_parent_is_clamped_and_said_out_loud():
    """越出父框的框要**裁到父框**并显式报出来，不许静默画到页外（ADR 0007 第 6 条）。

    这不是理论情形：手工掩膜框是人在裁剪图上拖出来的，拖到边界外是常事。
    这里那一项从块内 `[0.8, 0.9]` 起步、往右下拖出块外，所以裁完还剩一小块
    （`[0.628, 0.372, 0.152, 0.028]`，右下角正好压在块的角上）。
    """
    result = coords.crop_boxes_to_page([[0.8, 0.9, 0.5, 0.5]], REAL_PAGE_BOX_NORM, report=True)

    assert result["clamped"] == 1
    assert result["boxes"][0] == pytest.approx([0.628, 0.372, 0.152, 0.028])
    assert result["warnings"], "裁过的框必须有一条会喊的警告"


def test_a_box_that_ends_up_with_no_area_is_none_and_counted_as_clamped():
    """整个落在块外面的手工掩膜：画不出来，但**不许当成「这一项没问题」**。

    裁剪图上拖一个框到右下角之外，投到页上就在块的右边/下边——它已经不属于这一块，
    所以给 `None` 并计入 `clamped`，让调用方能说出「第几个掩膜我没画出来」。
    """
    result = coords.crop_boxes_to_page([[2.0, 2.0, 0.2, 0.2]], REAL_PAGE_BOX_NORM, report=True)

    assert result["boxes"] == [None]
    assert result["clamped"] == 1
    assert result["clamped_indexes"] == [0]
    assert result["warnings"]
    # 一个位置没错、形状也对的框不能被误报
    clean = coords.crop_boxes_to_page([REAL_MASK_BOX_IN_CROP], REAL_PAGE_BOX_NORM, report=True)
    assert clean["clamped"] == 0 and clean["warnings"] == []


# ---------------------------------------------------------------- 像素与归一化


def test_pixel_and_normalised_page_boxes_convert_both_ways():
    """整页归一化 ↔ 整页像素：界面要按照片的**显示尺寸**缩放画框，这里给换算。"""
    px = coords.norm_to_px([0.5, 0.25, 0.25, 0.5], width=800, height=400)
    assert px == pytest.approx([400.0, 100.0, 200.0, 200.0])
    assert coords.px_to_norm(px, width=800, height=400) == pytest.approx([0.5, 0.25, 0.25, 0.5])
    assert coords.px_to_norm(None, width=800, height=400) is None
    assert coords.norm_to_px([0.5, 0.25, 0.25, 0.5], width=0, height=400) is None


def test_a_display_scale_is_applied_to_page_boxes_for_drawing():
    """照片在界面上通常不是原尺寸：画框要把整页归一化坐标投到**显示出来的**尺寸上。

    这是「块框画在整页照片上」那句话在坐标上的全部内容——一处实现。
    """
    assert coords.norm_to_px([0.1, 0.2, 0.3, 0.4], width=1000, height=500) == \
        pytest.approx([100.0, 100.0, 300.0, 200.0])
