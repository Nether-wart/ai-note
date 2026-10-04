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


# ---------------------------------------------------------------- 页级：决定 + 记录 + 报告


def blk(block_id, *, bbox_px=(0, 0, 10, 10), card_id=None, keep=None, decision=None):
    """页文件里的一个块（形状照契约 §10.2.1）。"""
    block = {"id": block_id, "bbox_norm": [0.0, 0.0, 1.0, 1.0],
             "bbox_px": list(bbox_px) if bbox_px is not None else None,
             "card_id": card_id, "keep": keep}
    if decision is not None:
        block["decision"] = decision
    return block


def page_of(blocks, page_id="41c86bcfc007"):
    return {"version": 1, "id": page_id, "image": f"{page_id}.png",
            "created_at": "2026-10-04T14:31:35+08:00",
            "origin": {"original_file": "2.png", "sheet": None, "page_number": None},
            "blocks": blocks}


def stats(colored_px, area=1000):
    """#11 的 `ink_statistics` 形状。"""
    return {"area": area, "colored_px": colored_px,
            "colored_ratio": colored_px / area, "dark_px": 10, "dark_ratio": 0.01}


def reports(*pairs):
    """`ink.page_block_reports` 的形状：逐块的统计（`stats=None` = 读不出来）。"""
    return [{"id": block_id, "stats": s, "reason": None if s else "block_bbox_missing",
             "message": None if s else f"块 {block_id!r} 没有可用的 bbox_px，红笔统计做不了"}
            for block_id, s in pairs]


def answer(semantics, *, parsed=True, confidence=0.9, reason="模型说的一句话",
           run_id="20261004-190000-000-redpen-semantics.json"):
    return {"semantics": semantics, "confidence": confidence, "reason": reason,
            "provider": "deepseek", "model": "deepseek-flash", "run_id": run_id,
            "parsed": parsed}


AT = __import__("datetime").datetime(2026, 10, 4, 19, 0, 0)


def test_every_block_gets_keep_ink_and_an_auditable_decision():
    """验收 3：决策记在页文件里，可审计「为什么收、为什么没收」。"""
    page = page_of([blk("b1"), blk("b2"), blk("b3", bbox_px=None)])

    plan = intake.plan_decisions(
        page,
        ink_reports=reports(("b1", stats(210)), ("b2", stats(3)), ("b3", None)),
        semantics={"b1": answer("correction")},
        at=AT,
    )
    blocks = plan["page"]["blocks"]

    assert [b["keep"] for b in blocks] == [True, False, None]
    for block in blocks:
        decision = block["decision"]
        assert decision["rule"] in intake.RULES
        assert decision["source"] in intake.DECISION_SOURCES
        assert decision["reason"].strip()
        assert decision["at"] == AT.isoformat(timespec="seconds")
        assert "ink" in block
    assert blocks[0]["ink"] == stats(210), "#11 的统计原样写进 ink 键（不改写）"
    assert blocks[0]["decision"]["semantics"] == "correction"
    assert blocks[0]["decision"]["provider"] == "deepseek"
    assert blocks[0]["decision"]["run_id"].endswith("-redpen-semantics.json")
    assert blocks[2]["ink"] is None, "读不出来就是 None，不是 0（没有红笔另有一档）"


def test_the_page_reports_how_many_questions_have_no_red_ink():
    """验收 1：每页都要报「另有 M 道没有红笔痕迹、未入库」，并且**可枚举**。"""
    page = page_of([blk("b1"), blk("b2"), blk("b3"), blk("b4")])

    plan = intake.plan_decisions(
        page,
        ink_reports=reports(("b1", stats(210)), ("b2", stats(1)),
                            ("b3", stats(0)), ("b4", stats(9))),
        semantics={"b1": answer("cross")},
        at=AT,
    )
    not_kept = plan["not_kept"]

    assert not_kept["message"] == "另有 3 道没有红笔痕迹、未入库", \
        "这一句是服务给的原话（spec #2 与工单 #12 验收 1 都写死了这句）"
    no_ink = next(b for b in not_kept["by_rule"] if b["rule"] == intake.RULE_NO_RED_INK)
    assert (no_ink["count"], no_ink["ids"]) == (3, ["b2", "b3", "b4"])
    assert not_kept["block_ids"] == ["b2", "b3", "b4"], "一键补收要的入参要能直接拿去用"
    assert not_kept["one_click"]["entry"] == "server.intake.include_blocks"
    assert plan["counts"]["kept"] == 1 and plan["counts"]["no_red_ink"] == 3


def test_the_no_red_ink_sentence_is_printed_even_at_zero():
    """M=0 时照原话印「另有 0 道…」：它同时是「我检查过」的显式声明。

    与 #7 的 M 同一条口径（REVIEW-BACKLOG 的 UX 观察 4）：零不代表可以不说，
    它代表「这一页没有因为没红笔而少收的题」。
    """
    page = page_of([blk("b1")])

    plan = intake.plan_decisions(page, ink_reports=reports(("b1", stats(210))),
                                semantics={"b1": answer("cross")}, at=AT)

    assert plan["not_kept"]["message"] == "另有 0 道没有红笔痕迹、未入库"
    assert plan["not_kept"]["count"] == 0


def test_the_breakdown_lists_why_each_not_kept_block_was_not_kept():
    """D3 的口径：不只一个数字，逐个理由列出道数（界面才解释得清）。"""
    page = page_of([blk("b1"), blk("b2"), blk("b3")])

    plan = intake.plan_decisions(
        page, ink_reports=reports(("b1", stats(0)), ("b2", stats(80)), ("b3", None)),
        semantics={"b2": answer("tick")}, at=AT,
    )
    by_rule = {item["rule"]: item for item in plan["not_kept"]["by_rule"]}

    assert by_rule[intake.RULE_NO_RED_INK]["ids"] == ["b1"]
    assert by_rule[intake.RULE_TICK_ONLY]["ids"] == ["b2"]
    assert "对勾" in by_rule[intake.RULE_TICK_ONLY]["message"]
    assert by_rule[intake.RULE_INK_UNKNOWN]["ids"] == ["b3"]
    assert plan["not_kept"]["count"] == 3, "三种理由都是「未入库」"


def test_ink_we_could_not_read_is_pending_and_shouted_about():
    """「待定」不是安静的一档：它要有一条警告，原话取自 #11 的统计报告。"""
    page = page_of([blk("b7", bbox_px=None)])

    plan = intake.plan_decisions(page, ink_reports=reports(("b7", None)),
                                semantics={}, at=AT)

    assert plan["page"]["blocks"][0]["keep"] is None
    warning = next(w for w in plan["warnings"]
                   if w["code"] == intake.INTAKE_BLOCK_INK_UNKNOWN)
    assert warning["level"] == "warning"
    assert warning["message"] == "块 'b7' 没有可用的 bbox_px，红笔统计做不了"
    assert warning["id"] is None, "页级警告的 id 为 null（契约 §2）"


def test_statistics_and_the_model_disagreeing_is_a_warning():
    """统计说有红笔、模型说看不到红笔 → 两份证据矛盾，落向收并要人看一眼。"""
    page = page_of([blk("b1")])

    plan = intake.plan_decisions(page, ink_reports=reports(("b1", stats(210))),
                                semantics={"b1": answer("none")}, at=AT)

    assert plan["page"]["blocks"][0]["keep"] is True
    conflict = next(w for w in plan["warnings"]
                    if w["code"] == intake.INTAKE_INK_SEMANTICS_CONFLICT)
    assert conflict["level"] == "warning"
    assert "210" in conflict["message"], "把两边的读数都写出来，人才好判"


def test_a_fallback_keep_says_it_did_not_really_judge():
    """「判不准 → 收」要留痕：是模型判的还是兜底落收的（验收 3）。"""
    page = page_of([blk("b1"), blk("b2")])

    plan = intake.plan_decisions(
        page, ink_reports=reports(("b1", stats(210)), ("b2", stats(210))),
        semantics={"b1": answer(None, parsed=False, reason="模型答了一段散文"),
                   "b2": answer("可能是勾吧")},
        at=AT,
    )
    decisions = {b["id"]: b["decision"] for b in plan["page"]["blocks"]}

    assert decisions["b1"]["keep"] is True and decisions["b1"]["source"] == "fallback"
    assert decisions["b2"]["keep"] is True and decisions["b2"]["source"] == "fallback"
    codes = {w["code"]: w["level"] for w in plan["warnings"]}
    assert codes[intake.INTAKE_SEMANTICS_UNPARSED] == "warning"
    assert all(w["code"] in codes for w in plan["warnings"])
    assert codes[intake.INTAKE_SEMANTICS_FALLBACK] == "hint", \
        "枚举外的值是「我这一条没查全」，级别比真矛盾轻（#10 的两级纪律）"


def test_a_human_decision_is_never_overwritten_by_a_rerun():
    """spec #2：人动过的部分不允许被一次重跑抹掉（与 D10 同源）。"""
    human = {"rule": intake.RULE_HUMAN_INCLUDE, "source": "human", "semantics": None,
             "reason": "人一键补收", "at": AT.isoformat(timespec="seconds")}
    page = page_of([blk("b1", keep=True, decision=human)])

    plan = intake.plan_decisions(page, ink_reports=reports(("b1", stats(0))),
                                semantics={}, at=AT)

    block = plan["page"]["blocks"][0]
    assert block["keep"] is True and block["decision"] == human
    assert plan["not_kept"]["count"] == 0, "人收进来的不算「未入库」"
    preserved = next(w for w in plan["warnings"]
                     if w["code"] == intake.INTAKE_HUMAN_DECISION_PRESERVED)
    assert preserved["level"] == "hint"


def test_the_plan_does_not_mutate_the_page_it_was_given():
    """纯逻辑：输入原样不动（调用方拿它去写盘或不写，都不会被悄悄改）。"""
    page = page_of([blk("b1")])
    before = json.dumps(page, ensure_ascii=False, sort_keys=True)

    intake.plan_decisions(page, ink_reports=reports(("b1", stats(210))),
                          semantics={"b1": answer("cross")}, at=AT)

    assert json.dumps(page, ensure_ascii=False, sort_keys=True) == before


def test_a_block_that_is_not_an_object_is_shouted_about_not_skipped_silently():
    page = page_of([blk("b1"), "我不是块"])

    plan = intake.plan_decisions(page, ink_reports=reports(("b1", stats(210))),
                                semantics={"b1": answer("cross")}, at=AT)

    codes = [w["code"] for w in plan["warnings"]]
    assert "block_not_an_object" in codes, "码沿用 #9/#10 的那一个，不另发明"
    assert plan["counts"]["blocks"] == 1, "只统计真的块"


def test_the_plan_is_deterministic():
    page = page_of([blk("b1"), blk("b2")])
    args = dict(ink_reports=reports(("b1", stats(210)), ("b2", stats(0))),
                semantics={"b1": answer("cross")}, at=AT)

    first = intake.plan_decisions(page, **args)
    second = intake.plan_decisions(page, **args)

    assert json.dumps(first, ensure_ascii=False, sort_keys=True) == \
        json.dumps(second, ensure_ascii=False, sort_keys=True)


# ---------------------------------------------------------------- 一键补收（验收 1 的入口）


def test_one_click_include_takes_every_block_that_is_not_in_the_library():
    page = page_of([blk("b1", keep=True, card_id="p-20261004-aaa111"),
                    blk("b2", keep=False), blk("b3", keep=None)])

    result = intake.include_blocks(page, at=AT)
    blocks = {b["id"]: b for b in result["page"]["blocks"]}

    assert blocks["b1"]["keep"] is True, "已经在库的不动"
    assert [blocks["b2"]["keep"], blocks["b3"]["keep"]] == [True, True]
    assert blocks["b3"]["decision"]["rule"] == intake.RULE_HUMAN_INCLUDE
    assert blocks["b3"]["decision"]["source"] == "human"
    assert blocks["b3"]["decision"]["at"] == AT.isoformat(timespec="seconds")
    assert (result["summary"]["included"], result["summary"]["already_kept"]) == (2, 1)
    assert [item["block_id"] for item in result["included"]] == ["b2", "b3"]


def test_include_can_name_the_blocks_it_should_take():
    page = page_of([blk("b1", keep=False), blk("b2", keep=False)])

    result = intake.include_blocks(page, ["b2"], at=AT)
    blocks = {b["id"]: b for b in result["page"]["blocks"]}

    assert (blocks["b1"]["keep"], blocks["b2"]["keep"]) == (False, True)
    assert result["summary"]["included"] == 1
    assert result["summary"]["already_kept"] == 0


def test_naming_a_block_that_does_not_exist_is_reported_not_ignored():
    page = page_of([blk("b1", keep=False)])

    result = intake.include_blocks(page, ["b1", "b9"], at=AT)

    assert result["unknown"] == ["b9"]
    assert result["summary"]["unknown"] == 1
    assert result["summary"]["included"] == 1


def test_include_keeps_the_red_pen_readout_so_the_audit_does_not_lose_why():
    """补收只改**去留**，不改「这块的红笔当初是什么」那条读数。"""
    old = {"rule": intake.RULE_TICK_ONLY, "source": "model", "semantics": "tick",
           "confidence": 0.8, "provider": "deepseek", "model": "deepseek-flash",
           "run_id": "run-1.json", "reason": "只是一个对勾", "at": "2026-10-04T19:00:00"}
    page = page_of([blk("b1", keep=False, decision=old)])

    result = intake.include_blocks(page, ["b1"], at=AT)
    decision = result["page"]["blocks"][0]["decision"]

    assert decision["rule"] == intake.RULE_HUMAN_INCLUDE
    assert decision["semantics"] == "tick", "当初的语义要留着，审核时看得见"
    assert decision["run_id"] == "run-1.json"
    assert intake.RULE_TICK_ONLY in decision["reason"], "理由里要写清原来为什么没收"


def test_include_records_a_human_drop_the_same_way():
    page = page_of([blk("b1", keep=True)])

    result = intake.set_keep_by_human(page, ["b1"], keep=False, at=AT)
    block = result["page"]["blocks"][0]

    assert block["keep"] is False
    assert block["decision"]["rule"] == intake.RULE_HUMAN_DROP
    assert block["decision"]["source"] == "human"


def test_a_block_without_a_decision_can_still_be_included_by_name():
    """还没跑过决策的块也能补收（界面上一键补收不该要求先跑一遍自动决策）。"""
    page = page_of([blk("b1", keep=None)])

    result = intake.include_blocks(page, ["b1"], at=AT)
    decision = result["page"]["blocks"][0]["decision"]

    assert decision["rule"] == intake.RULE_HUMAN_INCLUDE
    assert decision["semantics"] is None
    assert "没判过" in decision["reason"] or "未判过" in decision["reason"]


# ---------------------------------------------------------------- 驱动：读页 → 统计 → 问模型 → 写回


def make_pages_dir(tmp_path, page_id="41c86bcfc007", *, blocks, photo="red", image_name=None):
    """造一个数据目录：`<data>/pages/<id>.json` + 与它并列的整页照片。"""
    from server.catalog import Catalog

    data = tmp_path / "data"
    (data / "pages").mkdir(parents=True, exist_ok=True)
    (data / "problems").mkdir(parents=True, exist_ok=True)
    page = page_of(blocks, page_id)
    if image_name:
        page["image"] = image_name
    path = data / "pages" / f"{page_id}.json"
    path.write_text(json.dumps(page, ensure_ascii=False, indent=2), encoding="utf-8")
    if photo == "red":
        width, height = 40, 20
        pixels = [(255, 255, 255)] * (width * height)
        for y in range(2, 8):          # 上半：红笔块
            for x in range(2, 18):
                pixels[y * width + x] = (220, 30, 30)
        for y in range(12, 18):        # 下半：只有印刷体/黑笔
            for x in range(2, 38):
                pixels[y * width + x] = (20, 20, 20)
        ink.write_png(data / "pages" / page["image"], ink.InkImage(width, height, pixels))
    elif photo == "junk":
        (data / "pages" / page["image"]).write_bytes(b"not a png at all")
    return Catalog(data), path


class FakeSemantics:
    """假的抽取角色：记下被问了什么，按块 id 吐预先排好的语义。"""

    def __init__(self, answers=None, *, raises=None):
        self.answers = answers or {}
        self.raises = raises or {}
        self.calls: list[dict] = []

    def __call__(self, block, stats, image_path):
        self.calls.append({"block_id": block.get("id"), "colored_px": stats.get("colored_px"),
                           "image": str(image_path)})
        if block.get("id") in self.raises:
            from server.model_client import ModelUnavailable
            raise ModelUnavailable(self.raises[block["id"]])
        return answer(self.answers.get(block.get("id")))


def red_block(block_id="b1", bbox_px=(2, 2, 18, 8)):
    return blk(block_id, bbox_px=bbox_px)


def plain_block(block_id="b2", bbox_px=(2, 12, 38, 18)):
    return blk(block_id, bbox_px=bbox_px)


def test_intake_asks_the_model_only_about_blocks_that_have_red_ink(tmp_path):
    catalog, path = make_pages_dir(tmp_path, blocks=[red_block(), plain_block()])
    fake = FakeSemantics({"b1": "correction"})

    report = intake.run_intake(catalog, "41c86bcfc007", semantics=fake, at=AT, apply=True)

    assert [call["block_id"] for call in fake.calls] == ["b1"], \
        "没有红笔的块不问模型（0 与「读不出来」另有两档，问模型是浪费）"
    assert fake.calls[0]["colored_px"] > ink.COLOR_MIN_PIXELS, "问之前统计已经算好了"
    assert [b["keep"] for b in report["page"]["blocks"]] == [True, False]


def test_the_decision_is_written_into_the_page_file_so_it_can_be_audited(tmp_path):
    """spec #2：决策只存在界面的内存里 = 「漏了一题」永远查不出来。"""
    from server import pages

    catalog, path = make_pages_dir(tmp_path, blocks=[red_block(), plain_block()])
    intake.run_intake(catalog, "41c86bcfc007", semantics=FakeSemantics({"b1": "cross"}),
                      at=AT, apply=True)

    on_disk, error = pages.read_page(catalog, "41c86bcfc007")
    assert error is None
    red, plain = on_disk["blocks"]
    assert red["keep"] is True and red["decision"]["rule"] == intake.RULE_ERROR_TRACE
    assert red["decision"]["semantics"] == "cross"
    assert red["decision"]["at"] == AT.isoformat(timespec="seconds")
    assert red["ink"]["colored_px"] > 0
    assert plain["keep"] is False and plain["decision"]["rule"] == intake.RULE_NO_RED_INK


def test_preview_writes_nothing_and_asks_nobody(tmp_path):
    """预演（默认）：算统计、报事实，**不问模型、不写盘**。"""
    catalog, path = make_pages_dir(tmp_path, blocks=[red_block(), plain_block()])
    before = path.read_bytes()
    fake = FakeSemantics({"b1": "correction"})

    report = intake.run_intake(catalog, "41c86bcfc007", semantics=fake, at=AT, apply=False)

    assert fake.calls == [], "预演不该花钱调模型"
    assert path.read_bytes() == before, "预演一个字节都不写"
    assert report["apply"] is False and report["preview"] is True
    assert report["not_kept"]["message"] == "另有 1 道没有红笔痕迹、未入库"


def test_a_model_failure_leaves_the_page_file_byte_identical(tmp_path):
    """D1/D9：调用失败 → 明确失败，**不留半截决策**。"""
    from server.model_client import ModelUnavailable

    catalog, path = make_pages_dir(tmp_path, blocks=[red_block("b1"),
                                                     red_block("b2", (2, 2, 18, 8))])
    before = path.read_bytes()
    fake = FakeSemantics({"b1": "cross"}, raises={"b2": "HTTP 502（redpen-semantics）：上游挂了"})

    with pytest.raises(ModelUnavailable) as excinfo:
        intake.run_intake(catalog, "41c86bcfc007", semantics=fake, at=AT, apply=True)

    assert "上游挂了" in str(excinfo.value)
    assert path.read_bytes() == before, "拒绝路径不得留下痕迹：第一个块判出来了也不许写"
    assert [call["block_id"] for call in fake.calls] == ["b1", "b2"]


def test_a_page_that_is_not_there_is_a_404_not_a_crash(tmp_path):
    from server.catalog import Catalog
    from server.errors import ApiError

    data = tmp_path / "data"
    (data / "pages").mkdir(parents=True)

    with pytest.raises(ApiError) as excinfo:
        intake.run_intake(Catalog(data), "41c86bcfc007", semantics=None, at=AT, apply=True)

    assert (excinfo.value.status, excinfo.value.code) == (404, "not_found")
    assert "41c86bcfc007.json" in excinfo.value.message


def test_an_unreadable_page_file_is_a_500_that_names_the_file(tmp_path):
    """页文件读不了 ≠ 页不存在：前者是矛盾，要说清是哪个文件（口径同 catalog.load_card）。"""
    from server.catalog import Catalog
    from server.errors import ApiError

    data = tmp_path / "data"
    (data / "pages").mkdir(parents=True)
    (data / "pages" / "41c86bcfc007.json").write_text("{ 这不是 JSON", encoding="utf-8")

    with pytest.raises(ApiError) as excinfo:
        intake.run_intake(Catalog(data), "41c86bcfc007", semantics=None, at=AT, apply=True)

    assert (excinfo.value.status, excinfo.value.code) == (500, "internal_error")
    assert "41c86bcfc007.json" in excinfo.value.message


def test_a_page_id_that_could_escape_the_pages_dir_is_rejected(tmp_path):
    """页 id 会变成文件名，所以它必须先过校验——否则写回会打到 `pages/` 外面去。"""
    from server.catalog import Catalog
    from server.errors import ApiError

    data = tmp_path / "data"
    (data / "pages").mkdir(parents=True)
    (data / "problems").mkdir(parents=True)
    victim = data / "problems" / "p-20261004-41c86b.json"
    victim.write_text('{"id": "p-20261004-41c86b"}', encoding="utf-8")

    with pytest.raises(ApiError) as excinfo:
        intake.run_intake(Catalog(data), "../problems/p-20261004-41c86b",
                          semantics=None, at=AT, apply=True)

    assert (excinfo.value.status, excinfo.value.code) == (400, "bad_request")
    assert excinfo.value.details.get("param") == "page_id"
    assert victim.read_text(encoding="utf-8") == '{"id": "p-20261004-41c86b"}', \
        "拒绝路径不得留下痕迹（这里差点是一次真正的数据破坏）"


def test_a_missing_page_photo_makes_every_block_pending_not_dropped(tmp_path):
    """整页图不在 → 统计做不了 → 每块**待定**，不是「没有红笔」。"""
    from server.catalog import Catalog

    data = tmp_path / "data"
    (data / "pages").mkdir(parents=True)
    page = page_of([red_block("b1"), plain_block("b2")])
    (data / "pages" / "41c86bcfc007.json").write_text(
        json.dumps(page, ensure_ascii=False), encoding="utf-8")
    fake = FakeSemantics({"b1": "cross"})

    report = intake.run_intake(Catalog(data), "41c86bcfc007", semantics=fake, at=AT, apply=True)

    assert [b["keep"] for b in report["page"]["blocks"]] == [None, None]
    assert fake.calls == []
    warning = next(w for w in report["warnings"]
                   if w["code"] == intake.INTAKE_PAGE_IMAGE_MISSING)
    assert warning["level"] == "warning"
    assert report["not_kept"]["count"] == 2
    assert all(item["rule"] == intake.RULE_INK_UNKNOWN
               for item in report["not_kept"]["by_rule"] if item["count"])


def test_an_unreadable_photo_is_shouted_about_and_nothing_is_guessed(tmp_path):
    catalog, _ = make_pages_dir(tmp_path, blocks=[red_block()], photo="junk")
    fake = FakeSemantics({"b1": "cross"})

    report = intake.run_intake(catalog, "41c86bcfc007", semantics=fake, at=AT, apply=True)

    assert report["page"]["blocks"][0]["keep"] is None
    assert fake.calls == []
    warning = next(w for w in report["warnings"]
                   if w["code"] == intake.INTAKE_PAGE_IMAGE_UNREADABLE)
    assert warning["level"] == "warning" and "not a png" not in warning["message"]


def test_a_human_decided_block_is_not_asked_about_again(tmp_path):
    human = {"rule": intake.RULE_HUMAN_INCLUDE, "source": "human", "semantics": None,
             "reason": "人一键补收", "at": AT.isoformat(timespec="seconds")}
    catalog, _ = make_pages_dir(tmp_path, blocks=[red_block("b1"), red_block("b2")])
    page_path = catalog.pages_dir / "41c86bcfc007.json"
    page = json.loads(page_path.read_text(encoding="utf-8"))
    page["blocks"][0]["keep"] = True
    page["blocks"][0]["decision"] = human
    page_path.write_text(json.dumps(page, ensure_ascii=False), encoding="utf-8")
    fake = FakeSemantics({"b2": "cross"})

    report = intake.run_intake(catalog, "41c86bcfc007", semantics=fake, at=AT, apply=True)

    assert [call["block_id"] for call in fake.calls] == ["b2"]
    assert report["page"]["blocks"][0]["decision"] == human


# ---------------------------------------------------------------- CLI 入口（一键补收的命令形态）


def cli(*args, **kwargs):
    """跑一次 CLI，返回 `(退出码, 解析后的信封, stderr 文本)`。"""
    import contextlib
    import io

    from server.intake import main

    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(list(args), **kwargs)
    text = out.getvalue().strip()
    return code, (json.loads(text) if text else None), err.getvalue()


def test_the_cli_defaults_to_a_preview_that_writes_nothing(tmp_path):
    catalog, path = make_pages_dir(tmp_path, blocks=[red_block(), plain_block()])
    before = path.read_bytes()
    fake = FakeSemantics({"b1": "correction"})

    code, envelope, _ = cli("--data", str(catalog.root), "--page", "41c86bcfc007",
                            semantics=fake, at=AT)

    assert code == 0
    assert envelope["ok"] is True
    assert envelope["data"]["preview"] is True
    assert envelope["skipped"] == []
    assert path.read_bytes() == before
    assert fake.calls == []


def test_the_cli_apply_writes_the_decisions_and_prints_the_envelope(tmp_path):
    catalog, path = make_pages_dir(tmp_path, blocks=[red_block(), plain_block()])

    code, envelope, _ = cli("--data", str(catalog.root), "--page", "41c86bcfc007",
                            "--apply", semantics=FakeSemantics({"b1": "cross"}), at=AT)

    assert code == 0 and envelope["ok"] is True
    data = envelope["data"]
    assert data["preview"] is False and data["asked"] == ["b1"]
    assert data["not_kept"]["message"] == "另有 1 道没有红笔痕迹、未入库"
    assert data["not_kept"]["one_click"]["entry"] == "server.intake.include_blocks"
    for warning in envelope["warnings"]:
        assert warning["level"] in ("warning", "hint"), "level 总是显式发出来（契约 §2）"
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["blocks"][0]["keep"] is True


def test_the_cli_reports_a_model_failure_as_the_d1_envelope_and_writes_nothing(tmp_path):
    """D1：模型失败 → 明确失败（`model_unavailable`），不留半截决策。"""
    catalog, path = make_pages_dir(tmp_path, blocks=[red_block("b1"), red_block("b2")])
    before = path.read_bytes()
    fake = FakeSemantics({"b1": "cross"}, raises={"b2": "HTTP 502（redpen-semantics）：上游挂了"})

    code, envelope, err = cli("--data", str(catalog.root), "--page", "41c86bcfc007",
                              "--apply", semantics=fake, at=AT)

    assert code == 2
    assert envelope["ok"] is False
    assert envelope["error"]["code"] == "model_unavailable"
    assert envelope["error"]["reason"] == "model_unavailable"
    assert "上游挂了" in envelope["error"]["message"]
    assert envelope["error"]["details"]["id"] == "41c86bcfc007"
    assert "没有留下任何记录" in envelope["error"]["hint"], \
        "与 HTTP 端点共用 errors.model_unavailable 那一份信封（两处不许各说各的）"
    assert path.read_bytes() == before, "字节不变才是「没有留下任何记录」的证据"
    assert "model_unavailable" in err


def test_the_cli_one_click_include_takes_every_block_that_is_not_kept(tmp_path):
    catalog, path = make_pages_dir(tmp_path, blocks=[red_block("b1"), plain_block("b2")])
    cli("--data", str(catalog.root), "--page", "41c86bcfc007", "--apply",
        semantics=FakeSemantics({"b1": "cross"}), at=AT)

    code, envelope, _ = cli("--data", str(catalog.root), "--page", "41c86bcfc007",
                            "--include", "--apply", at=AT)

    assert code == 0
    assert envelope["data"]["summary"]["included"] == 1
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert [b["keep"] for b in on_disk["blocks"]] == [True, True]
    assert on_disk["blocks"][1]["decision"]["rule"] == intake.RULE_HUMAN_INCLUDE


def test_the_cli_can_include_named_blocks(tmp_path):
    catalog, path = make_pages_dir(tmp_path, blocks=[red_block("b1"), plain_block("b2")])
    cli("--data", str(catalog.root), "--page", "41c86bcfc007", "--apply",
        semantics=FakeSemantics({"b1": "cross"}), at=AT)

    code, envelope, _ = cli("--data", str(catalog.root), "--page", "41c86bcfc007",
                            "--include-block", "b2", "--apply", at=AT)

    assert code == 0
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert [b["keep"] for b in on_disk["blocks"]] == [True, True]
    assert envelope["data"]["summary"]["included"] == 1


def test_the_cli_reports_a_bad_page_id_as_a_400_envelope(tmp_path):
    catalog, _ = make_pages_dir(tmp_path, blocks=[red_block()])

    code, envelope, _ = cli("--data", str(catalog.root), "--page", "../problems/p-x", at=AT)

    assert code == 2
    assert envelope["error"]["code"] == "bad_request"
    assert envelope["error"]["reason"] == "bad_request"
    assert envelope["error"]["details"]["param"] == "page_id"


def test_the_cli_refuses_a_data_dir_that_is_not_there(tmp_path):
    code, _, err = cli("--data", str(tmp_path / "nope"), "--page", "41c86bcfc007")

    assert code == 2
    assert "数据目录不存在" in err


def test_the_cli_builds_the_real_extractor_when_nothing_is_injected(tmp_path, monkeypatch):
    """不注入 `semantics` 时 CLI 自己建抽取角色（`--apply` 的那条路）。

    这条分支原先**没有测试**，是 `ruff check server/` 用 `F821`（`HttpSemantics`
    未导入）抓出来的——那种写法在真跑时是 `NameError` → 裸回溯，而测试全绿
    （因为测试都注入了假角色）。这正是「测试全绿永不覆盖没写的测试」的样本。

    这条用例仍然不联网、不花钱：把真实现换成一个**被调用就报错**的替身，
    页上又只有「没有红笔」的块（压根不会问到它），于是构造那一半被覆盖、
    调用那一半不可能发生。
    """
    catalog, _ = make_pages_dir(tmp_path, blocks=[plain_block()])
    built: dict = {}

    def spy(config, runs_dir, **kwargs):
        built["config"], built["runs_dir"] = config, str(runs_dir)

        class NeverCalled:
            def __call__(self, *args, **kwargs):
                raise AssertionError("这一页没有红笔块，不该问模型")

        return NeverCalled()

    monkeypatch.setattr(intake, "HttpSemantics", spy)
    runs = tmp_path / "runs"

    code, envelope, _ = cli("--data", str(catalog.root), "--page", "41c86bcfc007",
                            "--apply", "--runs-dir", str(runs), at=AT)

    assert code == 0 and envelope["ok"] is True
    assert envelope["data"]["asked"] == []
    assert built["config"].role == "extract", "抽取角色的身份走同一张角色表"
    assert built["runs_dir"] == str(runs), "留档目录用 --runs-dir 给的那个"
