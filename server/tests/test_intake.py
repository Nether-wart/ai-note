"""收入决策：痕迹语义 → 收 / 不收 / 待定（#12、spec #2「红笔痕迹 ≠ 这道题错了」）。

**这一层只回答一个问题**：这块（红笔痕迹的语义 + 有没有红笔）该不该入库。
统计归 `server/ink.py`（#11，只回答「有没有红笔、多少」），语义归模型（抽取角色，
`server/intake_client.py`）——本模块是**唯一的决策实现**（spec #2 的跨单元表：
「收入决策（痕迹语义 → 收/不收/待定）」只有一处）。

**规则逐条照 spec #2 / 工单 #12**：

| 情形 | 决策 | 规则码 |
|---|---|---|
| 有表示「错」的痕迹（叉、圈错、改错、订正） | **收** | `error_trace` |
| 有红笔但**只是对勾** | **不收** | `tick_only` |
| **判不准**（分数、圈题号、模型说看不清、模型没给出可用的语义） | **收** | `uncertain_defaults_to_keep` |
| 完全**没有红笔痕迹** | 不入库（每页报「另有 M 道……」，可一键补收） | `no_red_ink` |
| 统计**读不出来**（没有 `bbox_px`／整页图不在或读不了） | **待定**（`keep: null`，未入库、显式报出来） | `ink_unknown` |
| 人补收 / 人丢弃 | 收 / 不收 | `human_include` / `human_drop` |

## 为什么「判不准 → 收」

代价不对称：**漏收一道错题，它永远不在库里**（没人会再去看那一页）；
**误收一道对题，审核时删掉，几秒**。所以唯一的「不收」例外是**明确的「做对了」**
（纯对勾）——那时红笔是「对」的证据，不是「错」的证据。

## 已知漏法（spec #2 Further Notes，宁可写在前面，免得日后当 bug 查）

- **老师只写分数不写订正** → 分数太小、没到框级阈值（`ink.COLOR_MIN_PIXELS`）时，
  这块会被算成「没有红笔痕迹」→ 不入库。**这正是「另有 M 道……未入库 + 一键补收」
  存在的理由**，不是可选装饰。
- **学生用蓝笔或黑笔订正** → 颜色掩膜抓不到，同上（统计层看不见它）。
- **红笔勾表示做对** → 规则内正确排除（`tick_only`）。
- **整张卷子本身就是红笔印刷的** → 会**误收**（整页都是彩笔）。这是代价较小的那一侧，
  但人要在界面上看得见——每块的 `decision` 都记着「为什么收」。

**本模块不联网、不碰盘。** 模型调用在 `server/intake_client.py`（可注入的接缝），
写盘在 `run_intake` / `include_blocks` 的调用方（`save_page` 只有一处实现）。
"""

from __future__ import annotations

import json

import pytest

from server import ink, intake

# ---------------------------------------------------------------- 决策规则（纯逻辑）


@pytest.mark.parametrize("semantics", ["cross", "circle_wrong", "correction"])
def test_a_trace_that_means_wrong_is_kept(semantics):
    """叉、圈错、改错/订正 → 收（spec #2 第一条规则）。"""
    decision = intake.decide_block(has_red_ink=True, semantics=semantics)

    assert decision["keep"] is True
    assert decision["rule"] == intake.RULE_ERROR_TRACE
    assert decision["source"] == "model"


def test_a_plain_tick_is_the_only_thing_that_is_dropped():
    """「红笔只是对勾」是**唯一**的不收例外——勾表示做对了。"""
    decision = intake.decide_block(has_red_ink=True, semantics="tick")

    assert decision["keep"] is False
    assert decision["rule"] == intake.RULE_TICK_ONLY
    assert decision["source"] == "model"


@pytest.mark.parametrize("semantics,source", [
    ("score", "model"),          # 只是打分数：既不表示对也不表示错
    ("circle_number", "model"),  # 只是圈了题号
    ("none", "model"),           # 模型说这块看不到红笔（与统计矛盾，见下）
    ("other", "model"),          # 别的红笔
    ("unknown", "model"),        # 模型明说判不准
    (None, "fallback"),          # 模型没给出语义
    ("可能是勾，也可能是订正吧", "fallback"),   # 枚举外的值
    (17, "fallback"),
])
def test_anything_short_of_a_definite_tick_falls_to_keep(semantics, source):
    """**判不准 → 收**（工单 #12 验收 2：分不清勾还是订正 → 收）。

    这一条是**决策**，不是实现细节：它一退化（分不清就丢），漏收的错题就永远
    不在库里了。所以分数、圈题号、看不清、枚举外的值、压根没给语义——全部落向收。
    """
    decision = intake.decide_block(has_red_ink=True, semantics=semantics)

    assert decision["keep"] is True
    assert decision["rule"] == intake.RULE_UNCERTAIN_KEEP
    assert decision["source"] == source
    assert "漏收" in decision["reason"], "理由要写清代价不对称，别只写「默认收」"


def test_a_block_without_red_ink_is_not_kept_but_is_not_an_error():
    decision = intake.decide_block(has_red_ink=False)

    assert decision["keep"] is False
    assert decision["rule"] == intake.RULE_NO_RED_INK
    assert decision["source"] == "statistics"
    assert "没有红笔痕迹" in decision["reason"]


def test_ink_we_cannot_read_is_pending_and_never_dropped():
    """「没有红笔」与「读不出来」必须分得开（`server/ink.py` 的同一条纪律）。

    统计读不出来时既不许收（那是编一个不存在的痕迹）也不许丢（那是静默丢题）：
    `keep: null` = 待定，并且调用方**必须**把它报出来。
    """
    decision = intake.decide_block(has_red_ink=None)

    assert decision["keep"] is None, "待定就是 keep: null（契约 §10.2.1 的三态）"
    assert decision["rule"] == intake.RULE_INK_UNKNOWN
    assert decision["source"] == "statistics"


def test_every_decision_carries_a_rule_a_source_and_a_sentence():
    """不许静默（ADR 0007 第 6 条）：每个决策都要能回答「为什么收、为什么没收」。"""
    cases = [
        {"has_red_ink": True, "semantics": s} for s in intake.SEMANTICS_VALUES
    ] + [{"has_red_ink": False}, {"has_red_ink": None}]

    for case in cases:
        decision = intake.decide_block(**case)
        assert decision["rule"] in intake.RULES, case
        assert decision["source"] in intake.DECISION_SOURCES, case
        assert decision["reason"].strip(), case
        assert decision["keep"] in (True, False, None), case


def test_has_red_ink_reads_the_one_box_level_constant_from_ink():
    """框级阈值只有一份（`ink.COLOR_MIN_PIXELS`，#11）：边界就在它上面。

    这条测试是 #11 验收 2 在筛选侧的落点：改那颗常量，这里跟着变——
    两处不可能各判各的，因为压根只有一处可改。
    """
    assert intake.has_red_ink({"colored_px": ink.COLOR_MIN_PIXELS}) is False, \
        "原型判据是严格大于（proto/slice.py:888 的 in_color > 20）"
    assert intake.has_red_ink({"colored_px": ink.COLOR_MIN_PIXELS + 1}) is True

    assert intake.has_red_ink(None) is None, "没有统计 ≠ 没有红笔"
    assert intake.has_red_ink({}) is None, "统计里没有 colored_px 也是读不出来"


# ---------------------------------------------------------------- 语义解析（模型原文 → 语义）


def test_the_semantics_labels_are_exactly_the_documented_enum():
    assert set(intake.SEMANTICS_VALUES) == {
        "cross", "circle_wrong", "correction", "tick",
        "score", "circle_number", "none", "other", "unknown",
    }
    assert set(intake.SEMANTICS_ERROR) | set(intake.SEMANTICS_TICK_ONLY) | \
        set(intake.SEMANTICS_OTHER_VALUES) == set(intake.SEMANTICS_VALUES), "三档必须覆盖整个枚举"


def test_the_rule_codes_are_stable_strings():
    """规则码是记进页文件的审计字段，改它就是改历史读数——钉住取值。"""
    assert intake.RULES == (
        "error_trace", "tick_only", "uncertain_defaults_to_keep",
        "no_red_ink", "ink_unknown", "human_include", "human_drop",
    )


def test_a_page_file_block_round_trips_json_with_the_decision():
    """决策必须能原样进 JSON 页文件（审计靠读盘，不靠内存）。"""
    decision = intake.decide_block(has_red_ink=True, semantics="correction")
    blob = json.dumps({"keep": decision["keep"], "decision": decision}, ensure_ascii=False)

    assert json.loads(blob)["decision"]["rule"] == "error_trace"
