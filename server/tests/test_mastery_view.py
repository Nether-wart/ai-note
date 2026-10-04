"""掌握、冷却与默认打印清单的**读数**（契约 §4）。

规则的中文口径在 CONTEXT.md，边界用例的口径继承 proto/test_mastery.py：
假时钟、不联网、只验规则。这里只验读，不验写（状态机归 #5）。
"""

from __future__ import annotations

from datetime import datetime, timezone

from conftest import get_json, make_card

NOW = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)


def index_with(api_for, *cards):
    status, body = get_json(api_for(list(cards), clock=lambda: NOW), "/api/index")
    assert status == 200, body
    return body["data"]


def one_card(**overrides):
    return make_card("p-20200101-aaaaaa", **overrides)


def test_never_redone_card_cools_down_from_its_created_at(api_for):
    """从未重做过的题以**录入时间**起算——这条最容易被漏掉。"""
    data = index_with(api_for, one_card(**{"created_at": "2026-10-01T09:00:00+00:00"}))
    p = data["problems"][0]

    assert (p["cooling"], p["cooldown_days"]) == (True, 4)
    assert (p["in_default_list"], p["excluded_from_default_because"]) == (False, "cooling")


def test_a_card_past_its_cooldown_is_in_the_default_list(api_for):
    data = index_with(api_for, one_card(**{"created_at": "2026-09-27T09:00:00+00:00"}))
    p = data["problems"][0]

    assert (p["cooling"], p["cooldown_days"]) == (False, 0)
    assert (p["in_default_list"], p["excluded_from_default_because"]) == (True, None)


def test_exactly_seven_days_is_no_longer_cooling(api_for):
    """边界取哪一侧：满 7 天即脱离冷却（`at >= until`）。"""
    data = index_with(api_for, one_card(**{"created_at": "2026-09-27T09:00:00+00:00"}))
    assert data["problems"][0]["cooling"] is False


def test_cooldown_is_measured_from_the_last_attempt_not_creation(api_for):
    card = one_card(
        **{"created_at": "2026-01-01T00:00:00+00:00",
           "mastery.state": "in_pool", "mastery.streak": 1,
           "mastery.last_attempt_at": "2026-10-02T09:00:00+00:00"}
    )
    p = index_with(api_for, card)["problems"][0]

    assert (p["cooling"], p["cooldown_days"]) == (True, 5)
    assert p["in_default_list"] is False


def test_graduated_cards_are_excluded_and_say_so(api_for):
    """毕业的卡退出默认打印清单——被排除时**必须**给出原因。"""
    card = one_card(
        **{"mastery.state": "graduated", "mastery.streak": 2,
           "mastery.last_attempt_at": "2026-10-03T09:00:00+00:00"}
    )
    p = index_with(api_for, card)["problems"][0]

    assert (p["graduated"], p["mastery_cn"]) == (True, "毕业")
    assert p["in_default_list"] is False
    # 同时也还在冷却里，但更强的原因是毕业
    assert p["excluded_from_default_because"] == "graduated"


def test_the_default_list_is_ordered_by_last_redo_earliest_first(api_for):
    """最早该做（最久没做）的排最前；从未重做过的以录入时间起算。"""
    cards = [
        make_card("p-cccccc-newest", **{"mastery.last_attempt_at": "2026-09-01T00:00:00+00:00",
                                        "mastery.streak": 1}),
        make_card("p-bbbbbb-middle", **{"mastery.last_attempt_at": "2026-06-01T00:00:00+00:00",
                                        "mastery.streak": 1}),
        make_card("p-aaaaaa-oldest", **{"created_at": "2026-01-01T00:00:00+00:00"}),
    ]
    data = index_with(api_for, *cards)

    assert [p["id"] for p in data["problems"]] == [
        "p-aaaaaa-oldest", "p-bbbbbb-middle", "p-cccccc-newest"
    ]
    assert [p["in_default_list"] for p in data["problems"]] == [True, True, True]


def test_sort_key_normalises_offsets_before_comparing(api_for):
    """卡里是 +08:00、重做时刻是 UTC；比字符串会把 06:31Z 排到 09:00Z 后面。"""
    local = make_card("p-local-0800", **{"created_at": "2026-01-01T14:00:00+08:00",
                                         "mastery.streak": 0})
    utc = make_card("p-utc-0000", **{"created_at": "2026-01-01T07:00:00+00:00"})
    data = index_with(api_for, local, utc)

    # 14:00+08:00 == 06:00Z，比 07:00Z 早 → 排前面
    assert [p["id"] for p in data["problems"]] == ["p-local-0800", "p-utc-0000"]


def test_cooling_cards_still_appear_they_are_only_flagged(api_for):
    """「显示冷却中的题」开关是界面的事：服务把冷却的题也列出来，只打标记。"""
    data = index_with(api_for, one_card(**{"created_at": "2026-10-04T01:00:00+00:00"}))

    assert data["count"] == 1
    assert data["problems"][0]["cooling"] is True
    assert data["stats"]["cooling"] == 1
    assert data["stats"]["in_default_list"] == 0
