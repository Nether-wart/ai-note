"""警告是契约里最有牙齿的一部分（ADR 0007 第 6 条：每个由人填写的字段都要有一个会喊的检查）。

测的是**行为**：一张卡进去，一句带 `code` 的原话出来。不测文案措辞，
只断言「该响的响了、不该响的没响」。
"""

from __future__ import annotations

import json

from conftest import PNG_1X1, make_card


def index_of(api):
    r = api.handle("GET", "/api/index")
    assert r.status == 200, r.body
    return json.loads(r.body)


def codes_of(problem: dict) -> set[str]:
    return {w["code"] for w in problem["warnings"]}


def card_codes(api_for, card, *, images=None):
    body = index_of(api_for([card], images=images))
    return codes_of(body["data"]["problems"][0])


def test_a_clean_card_warns_about_nothing(api_for):
    pid = "p-20200101-aaaaaa"
    images = {f"{pid}-problem.png": PNG_1X1, f"{pid}-clean.png": PNG_1X1}
    assert card_codes(api_for, make_card(pid), images=images) == set()


def test_missing_standard_answer_warns(api_for):
    codes = card_codes(api_for, make_card(**{"standard_answer.value": None}))
    assert "standard_answer_missing" in codes


def test_choice_card_with_a_non_letter_answer_warns(api_for):
    codes = card_codes(api_for, make_card(**{"standard_answer.value": "A、C"}))
    assert "standard_answer_not_choice_letter" in codes


def test_choice_letter_on_a_solution_card_warns(api_for):
    """串题那条老事故的形状：解答题身上带着选项字母。"""
    codes = card_codes(
        api_for,
        make_card(**{"problem.type": "solution", "standard_answer.value": "A",
                     "original_solution.original_answer": "B"}),
    )
    assert {"standard_answer_choice_letter_on_non_choice", "original_answer_choice_letter_on_non_choice"} <= codes


def test_original_answer_equal_to_standard_answer_warns(api_for):
    """错题本里「原答 == 标准答案」通常意味着订正被当成了原答。"""
    codes = card_codes(api_for, make_card(**{"original_solution.original_answer": "A"}))
    assert "original_answer_equals_standard_answer" in codes


def test_empty_topics_warn(api_for):
    codes = card_codes(api_for, make_card(**{"topics": []}))
    assert "topics_empty" in codes


def test_missing_clean_image_warns_and_kills_the_url(api_for):
    """没有擦除手写后的题面图 → 不进屏幕重做、也不进重做纸。"""
    pid = "p-20200101-aaaaaa"
    body = index_of(api_for([make_card(pid, **{"problem.clean_image": None})],
                            images={f"{pid}-problem.png": PNG_1X1}))
    problem = body["data"]["problems"][0]

    assert "no_clean_image" in codes_of(problem)
    assert problem["has_clean"] is False
    assert problem["images"]["clean"] is None


def test_recorded_but_absent_image_file_warns(api_for):
    """卡里记了擦除图、文件却不在——最阴的一种：线上会拿到一个 404。"""
    pid = "p-20200101-aaaaaa"
    body = index_of(api_for([make_card(pid)], images={f"{pid}-problem.png": PNG_1X1}))
    problem = body["data"]["problems"][0]

    assert {"clean_image_file_missing", "no_clean_image", "original_image_file_missing"} & codes_of(problem)
    assert "clean_image_file_missing" in codes_of(problem)
    assert problem["images"]["clean"] is None


def test_reviewed_but_incomplete_warns(api_for):
    codes = card_codes(api_for, make_card(**{"topics": [], "standard_answer.value": None}))
    assert "reviewed_but_incomplete" in codes


def test_two_cards_with_identical_transcript_warn(api_for):
    """CONTEXT「串题」：两道不同的题不可能有同一段题干，这条没有例外。"""
    a = make_card("p-20200101-aaaaaa")
    b = make_card("p-20200101-bbbbbb", **{"problem.transcript": "1. 一道题"})
    body = index_of(api_for([a, b]))

    warnings = [w for w in body["warnings"] if w["code"] == "duplicate_transcript"]
    assert len(warnings) == 1, body["warnings"]
    assert all(pid in warnings[0]["message"] for pid in ("p-20200101-aaaaaa", "p-20200101-bbbbbb"))


def test_filename_not_matching_card_id_warns(api_for):
    """改名或复制粘贴事故：文件名与卡内 id 不一致。索引照建，但必须响一声。"""
    card = make_card("p-20200101-aaaaaa")
    body = index_of(api_for(files={"p-20200101-zzzzzz": card}))
    assert body["data"]["problems"][0]["id"] == "p-20200101-aaaaaa"
    assert "problem_id_mismatch" in {w["code"] for w in body["warnings"]}


def test_per_card_warnings_show_up_in_both_places(api_for):
    """列表页按卡渲染不必筛，审计按列表取不必翻卡——同一份内容，两处放。"""
    body = index_of(api_for([make_card(**{"topics": []})]))
    per_card = body["data"]["problems"][0]["warnings"]
    flat = body["warnings"]
    assert per_card and flat == per_card
    assert body["data"]["warnings"] == flat
