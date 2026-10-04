"""屏幕重做的硬闸门（契约 §6.1，编排裁决 D3/D4）。

原型在这里有一个坑：入选条件是 `has_clean or review == "reviewed"`（**是 `or`**），
于是「已审核但没有擦除图」的卡会过关、渲染成 `<img src="None">`。
新规则两条都是 `and`，而且**缺擦除图不许回退到原图**——原图印着订正。
更不许静默丢掉：丢了几道要说出来（这句是 ADR 0007 第 6 条的唯一要求）。
"""

from __future__ import annotations

from conftest import PNG_1X1, get_json, make_card

PID = "p-20200101-aaaaaa"


def index_with(api_for, *cards, images=None):
    status, body = get_json(api_for(list(cards), images=images), "/api/index")
    assert status == 200, body
    return body["data"]


def ready_images(pid: str) -> dict:
    return {f"{pid}-problem.png": PNG_1X1, f"{pid}-clean.png": PNG_1X1}


def test_a_card_with_a_clean_image_and_a_verdict_rule_is_ready(api_for):
    data = index_with(api_for, make_card(PID), images=ready_images(PID))
    p = data["problems"][0]

    assert p["screen_redo"] == {"ready": True, "blockers": [], "blocker_text": []}


def test_a_reviewed_card_without_a_clean_image_is_blocked(api_for):
    """原型那条 `or` 就死在这里：已审核 ≠ 有擦除图。"""
    pid = PID
    data = index_with(api_for, make_card(pid), images={f"{pid}-problem.png": PNG_1X1})
    p = data["problems"][0]

    assert p["review"] == "reviewed"          # 已审核
    assert p["images"]["original"] is not None  # 原图在
    assert p["screen_redo"]["ready"] is False
    assert p["screen_redo"]["blockers"] == ["no_clean_image"]
    assert "擦除" in p["screen_redo"]["blocker_text"][0]


def test_blockers_list_every_reason_not_just_the_first(api_for):
    """一张题可以同时踩两个坑，界面要能一次说清。"""
    card = make_card(PID, **{"problem.type": "solution"})
    data = index_with(api_for, card)
    p = data["problems"][0]

    assert p["screen_redo"]["ready"] is False
    assert p["screen_redo"]["blockers"] == ["solution_type", "no_clean_image"]
    assert len(p["screen_redo"]["blocker_text"]) == 2


def test_index_reports_the_n_and_m_numbers_separately(api_for):
    """「另有 N 道不能自动判定」与「另有 M 道缺擦除图」是两个数，都要能列出题号。"""
    solution = make_card("p-solution", **{"problem.type": "solution"})
    unreviewed = make_card("p-unreviewed", **{"review.status": "unreviewed"})
    no_clean = make_card("p-noclean", **{"problem.clean_image": None})
    ok_card = make_card("p-ok")
    data = index_with(
        api_for, solution, unreviewed, no_clean, ok_card,
        images={**ready_images("p-ok"), **ready_images("p-unreviewed"),
                "p-noclean-problem.png": PNG_1X1},
    )

    block = data["screen_redo"]
    assert block["default_basis"] == "in_default_list"
    block = block["bases"]["in_default_list"]
    assert block["basis_text"] == "默认打印清单（未毕业且已脱离冷却）"
    assert block["ready"] == 1
    assert block["blocked_total"] == 3

    ineligible = block["not_auto_judgeable"]
    assert ineligible["count"] == 2
    assert {r["reason"] for r in ineligible["by_reason"]} == {"solution_type", "unreviewed"}
    assert {pid for r in ineligible["by_reason"] for pid in r["ids"]} == {"p-solution", "p-unreviewed"}
    for entry in ineligible["by_reason"]:
        assert entry["reason_text"] and entry["count"] == len(entry["ids"])

    assert block["no_clean_image"] == {
        "count": 2,
        "ids": ["p-noclean", "p-solution"],
        "message": "另有 2 道因缺少擦除手写后的题面图不能进屏幕重做",
    }


def test_both_bases_are_computed_so_the_switch_never_recomputes_it(api_for):
    """「显示冷却中的题」勾上/不勾上是**两套数字**，两套都由服务算好。

    冷却中的解答题：不勾时它根本不在候选总体里，勾上时它在——所以它算进
    `including_cooling` 的 N，不算进 `in_default_list` 的 N。
    """
    from datetime import datetime, timezone

    now = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)
    cooling_solution = make_card(
        "p-cooling", **{"problem.type": "solution", "created_at": "2026-10-02T09:00:00+00:00"}
    )
    status, body = get_json(api_for([cooling_solution], clock=lambda: now), "/api/index")
    data = body["data"]

    assert data["problems"][0]["in_default_list"] is False
    assert set(data["screen_redo"]["bases"]) == {"in_default_list", "including_cooling"}

    off = data["screen_redo"]["bases"]["in_default_list"]
    on = data["screen_redo"]["bases"]["including_cooling"]
    assert (off["blocked_total"], off["not_auto_judgeable"]["count"]) == (0, 0)
    assert on["basis_text"] == "未毕业（含冷却中，「显示冷却中的题」勾上时）"
    assert on["not_auto_judgeable"]["count"] == 1
    assert on["not_auto_judgeable"]["by_reason"][0]["reason"] == "solution_type"
    assert on["not_auto_judgeable"]["by_reason"][0]["ids"] == ["p-cooling"]
    # 毕业的卡两套里都不算：开关管的是冷却，不是毕业
    assert on["ready"] == 0 and on["blocked_total"] == 1


def test_the_counts_stay_consistent_with_the_per_card_flags(api_for):
    """stats 与 screen_redo 都是便利读数，真源是 problems 自己。"""
    data = index_with(api_for, make_card(PID))
    from collections import Counter

    per_card = Counter(
        blocker for p in data["problems"] for blocker in p["screen_redo"]["blockers"]
    )
    block = data["screen_redo"]["bases"]["in_default_list"]
    assert block["no_clean_image"]["count"] == per_card["no_clean_image"]
    assert block["not_auto_judgeable"]["count"] == sum(
        per_card[reason] for reason in ("solution_type", "no_standard_answer", "unreviewed")
    )
