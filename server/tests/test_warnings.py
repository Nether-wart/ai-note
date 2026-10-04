"""警告是契约里最有牙齿的一部分（ADR 0007 第 6 条：每个由人填写的字段都要有一个会喊的检查）。

测的是**行为**：一张卡进去，一句带 `code` 的原话出来。不测文案措辞，
只断言「该响的响了、不该响的没响」。
"""

from __future__ import annotations

import json

from conftest import PNG_1X1, make_card
from server import pages


def index_of(api):
    r = api.handle("GET", "/api/index")
    assert r.status == 200, r.body
    return json.loads(r.body)


def codes_of(problem: dict) -> set[str]:
    return {w["code"] for w in problem["warnings"]}


def card_codes(api_for, card, *, images=None):
    body = index_of(api_for([card], images=images))
    return codes_of(body["data"]["problems"][0])


def warnings_of(api, code: str) -> list[dict]:
    return [w for w in index_of(api)["warnings"] if w["code"] == code]


def test_a_clean_card_warns_about_nothing(api_for):
    """字段自检干净、页绑定也在 → 一条都没有（连提示也不该有）。"""
    pid = "p-20200101-aaaaaa"
    page_id = "aaaaaa"
    images = {f"{pid}-problem.png": PNG_1X1, f"{pid}-clean.png": PNG_1X1}
    api = api_for([make_card(pid, **{"source.page_image": f"data/pages/{page_id}.png"})],
                  images=images)
    # 让这张卡变成「有页绑定」：走真的回填，而不是在夹具里手搓一个页文件
    pages.backfill_pages(api.catalog, apply=True)

    assert codes_of(index_of(api)["data"]["problems"][0]) == set()


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


# ------------------------------------------- 页绑定：提示不是错误（#9 验收 2）


def test_a_legacy_card_without_a_page_file_is_a_hint_not_an_error(api_for):
    """#9 验收 2：旧卡缺页绑定报**提示**——旧数据不该因为新结构变成脏数据。

    原型里同族的先例是解答题的「标准答案为空」（`proto/server.py:1044-1051`）：
    把按设计如此的事报成问题，会训练人忽略体检——那比漏报更糟。
    """
    pid = "p-20200101-aaaaaa"
    api = api_for([make_card(pid, **{"source.page_image": "data/pages/aaaaaa.png"})])

    body = index_of(api)

    assert body["ok"] is True, "提示不是错误：索引照建、卡照出现"
    assert "error" not in body
    assert body["data"]["count"] == 1

    (hint,) = warnings_of(api, "page_binding_missing")
    assert hint["level"] == "hint"
    assert hint["id"] == pid
    assert "aaaaaa.json" in hint["message"], "提示里要能定位到缺的是哪个页文件"
    assert "回填" in hint["message"], "提示要说出下一步怎么办"


def test_backfilling_a_legacy_card_clears_the_hint(api_for):
    """回填之后就这条提示就消失——提示是「还没回填」，不是永久的脏标记。"""
    pid = "p-20200101-aaaaaa"
    api = api_for([make_card(pid, **{"source.page_image": "data/pages/aaaaaa.png"})])
    assert warnings_of(api, "page_binding_missing"), "先确认它本来会响"

    pages.backfill_pages(api.catalog, apply=True)

    assert not warnings_of(api, "page_binding_missing")


def test_a_page_file_that_does_not_bind_the_card_is_a_warning(api_for):
    """本该有却缺失 = 矛盾，必须喊（提示与错误的级别要分开）。"""
    pid = "p-20200101-aaaaaa"
    page_id = "aaaaaa"
    api = api_for([make_card(pid, **{"source.page_image": f"data/pages/{page_id}.png"})])
    pages.save_page(api.catalog, {
        "version": 1, "id": page_id, "image": f"{page_id}.png",
        "created_at": None, "origin": {"original_file": None, "sheet": None, "page_number": None},
        "blocks": [],   # 页文件在，却一个块都没绑定这张卡
    })

    (warn,) = warnings_of(api, "page_binding_lost")
    assert warn["level"] == "warning"
    assert warn["id"] == pid
    assert not warnings_of(api, "page_binding_missing"), "两种缺绑定不许混成一个"


def test_an_unreadable_page_file_is_a_warning_not_a_silent_hint(api_for):
    """页文件读不了 = 对不上账，不能装作「旧卡没有页」。"""
    pid = "p-20200101-aaaaaa"
    page_id = "aaaaaa"
    api = api_for([make_card(pid, **{"source.page_image": f"data/pages/{page_id}.png"})])
    api.catalog.pages_dir.mkdir(parents=True, exist_ok=True)
    (api.catalog.pages_dir / f"{page_id}.json").write_text("{ 这不是 JSON", encoding="utf-8")

    (warn,) = warnings_of(api, "page_binding_lost")
    assert warn["level"] == "warning"
    assert "读不了" in warn["message"]


def test_every_warning_carries_an_explicit_level(api_for):
    """契约 §2 的 Warning 有 `level`：不许靠「省略即默认」——级别只有服务能定。"""
    a = make_card("p-20200101-aaaaaa", **{
        "source.page_image": "data/pages/aaaaaa.png",
        "standard_answer.value": None,
        "problem.clean_image": None,
    })
    b = make_card("p-20200101-bbbbbb", **{"problem.transcript": "1. 一道题"})
    body = index_of(api_for([a, b]))

    assert body["warnings"], "这组夹具本来就该有警告，否则这条测试是空转"
    assert all(w.get("level") in ("warning", "hint") for w in body["warnings"])
    assert "page_binding_missing" in {w["code"] for w in body["warnings"]}
