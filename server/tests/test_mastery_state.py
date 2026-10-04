"""掌握 + 冷却**状态机的写**（`mastery.apply_attempt`）——不联网、不花钱、只验规则。

口径继承 `proto/slice.py:604-661` 与 `proto/test_mastery.py`（冻结的实测证据，
代码不继承）。四条最容易写错的规则各有一条测试钉住：

  · **冷却必须在更新 `last_attempt_at` 之前算**——顺序反了，当天判对就会计入掌握；
  · 判错 → 无条件清零回池，冷却只挡「计入正确」，不挡这一条；
  · 看不清 → 记下这次重做，既不推进也不清零；
  · 连续 2 次**计入**的正确 → 毕业；两次之间必须各隔一次冷却结束。

时刻在这一层是**外部给的**（纸上重做是几天里做的，标记可能晚几天），
所以测试用固定时刻，不依赖运行时刻。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from conftest import make_card
from server import mastery as M

NOW = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)
DAY = timedelta(days=1)


def card(**overrides):
    return make_card("p-20200101-aaaaaa", **overrides)


def test_correct_after_cooldown_is_credited():
    """脱离冷却（录入 8 天前）的判对 → 计入连续正确 1/2。"""
    c = card(**{"created_at": (NOW - 8 * DAY).isoformat()})

    out = M.apply_attempt(c, "correct", confidence=0.95, at=NOW,
                          provider="deepseek", model="deepseek-flash")

    assert (out["credited"], out["streak"], out["state"]) == (True, 1, "in_pool")
    assert c["mastery"]["streak"] == 1
    assert c["mastery"]["last_attempt_at"] == NOW.isoformat(timespec="seconds")


def test_cooling_must_be_computed_before_last_attempt_at_is_updated():
    """定点钉住顺序：先算冷却，再写 `last_attempt_at`。

    从未重做过的题以**录入时间**起算。录入在 8 天前 → 已脱离冷却 → 计入。
    若实现先把 `last_attempt_at` 写成「现在」再算冷却，这一刻就落在冷却里，
    结果会变成「只热身」——那正是原型踩过的「当天判对计入掌握」的错。
    """
    c = card(**{"created_at": (NOW - 8 * DAY).isoformat()})

    out = M.apply_attempt(c, "correct", confidence=0.95, at=NOW)

    assert out["credited"] is True, "冷却基准必须在 last_attempt_at 被改写之前取"
    assert out["cooling"] is False, "这一刻尚未进入冷却"
    assert c["mastery"]["last_attempt_at"] == NOW.isoformat(timespec="seconds"), \
        "记录写下了，但冷却读数取的是写之前的基准"


def test_correct_within_cooldown_is_only_a_warmup():
    """判对但距上次重做不满 7 天 → 只热身，streak 不动。"""
    c = card(**{"created_at": (NOW - 100 * DAY).isoformat(),
                "mastery.streak": 1,
                "mastery.last_attempt_at": (NOW - 1 * DAY).isoformat()})

    out = M.apply_attempt(c, "correct", confidence=0.99, at=NOW)

    assert (out["credited"], out["streak"]) == (False, 1)
    assert c["mastery"]["streak"] == 1
    assert "热身" in out["note"]


def test_two_correct_in_a_row_second_one_is_warmup_not_graduation():
    """连着两次判对（第二次仍在冷却里）→ 第二次只热身，绝不毕业。"""
    c = card(**{"created_at": (NOW - 8 * DAY).isoformat()})

    M.apply_attempt(c, "correct", confidence=0.95, at=NOW)
    out = M.apply_attempt(c, "correct", confidence=0.95, at=NOW + timedelta(minutes=1))

    assert (out["credited"], out["streak"], out["state"]) == (False, 1, "in_pool")
    assert c["mastery"]["state"] == "in_pool"


def test_two_credited_corrects_a_cooldown_apart_graduate():
    """连续 2 次**计入**的正确（各隔一次冷却）→ 掌握 → 毕业。"""
    c = card(**{"created_at": (NOW - 30 * DAY).isoformat()})

    first = M.apply_attempt(c, "correct", confidence=0.95, at=NOW)
    second = M.apply_attempt(c, "correct", confidence=0.95, at=NOW + 8 * DAY)

    assert (first["credited"], first["streak"]) == (True, 1)
    assert (second["credited"], second["streak"], second["state"]) == (True, 2, "graduated")
    assert c["mastery"]["state"] == "graduated"
    assert c["mastery"]["mastered_at"] == (NOW + 8 * DAY).isoformat(timespec="seconds")


def test_wrong_clears_streak_and_returns_to_pool_unconditionally():
    """判错 → 无条件清零并立刻回池；冷却**不挡**这一条。"""
    c = card(**{"created_at": (NOW - 100 * DAY).isoformat(),
                "mastery.state": "graduated", "mastery.streak": 2,
                "mastery.last_attempt_at": (NOW - 1 * DAY).isoformat(),
                "mastery.mastered_at": (NOW - 1 * DAY).isoformat()})

    out = M.apply_attempt(c, "wrong", confidence=0.1, at=NOW)

    assert (out["credited"], out["streak"], out["state"]) == (False, 0, "in_pool")
    assert c["mastery"]["state"] == "in_pool"
    assert "mastered_at" not in c["mastery"], "毕业必须被取消"
    assert c["mastery"]["last_attempt_at"] == NOW.isoformat(timespec="seconds"), \
        "判错也进冷却（这是设计，不是 bug）"


def test_wrong_after_graduation_cancels_graduation_and_immediately_unpool():
    """毕业后判错 → 立刻回池、毕业取消（proto/test_mastery.py 的同名用例）。"""
    c = card(**{"created_at": (NOW - 100 * DAY).isoformat(),
                "mastery.state": "graduated", "mastery.streak": 2,
                "mastery.last_attempt_at": (NOW - 30 * DAY).isoformat()})

    out = M.apply_attempt(c, "wrong", at=NOW)

    assert (out["state"], c["mastery"]["state"]) == ("in_pool", "in_pool")
    assert out["credited"] is False


def test_unreadable_records_but_neither_advances_nor_clears():
    """看不清 → 记下这次重做，既不推进也不清零。"""
    c = card(**{"created_at": (NOW - 100 * DAY).isoformat(), "mastery.streak": 1})

    out = M.apply_attempt(c, "unreadable", confidence=0.2, at=NOW)

    assert (out["credited"], out["streak"], out["state"]) == (False, 1, "in_pool")
    assert len(c["attempts"]) == 1
    assert c["attempts"][0]["verdict"] == "unreadable"


def test_created_at_offset_is_normalised_before_comparing():
    """卡里 created_at 是 +08:00、重做时刻是 UTC：归一化后才能比。

    录入 `2026-10-04T00:30:00+08:00` = `2026-10-03T16:30:00Z`，距 NOW 不到 7 天
    → 冷却中 → 判对只热身。直接比字符串会得出相反的结论。
    """
    c = card(**{"created_at": "2026-10-04T00:30:00+08:00"})

    out = M.apply_attempt(c, "correct", confidence=0.99, at=NOW)

    assert (out["cooling"], out["credited"], out["gap_days"]) == (True, False, 0)


def test_gap_days_is_never_negative():
    """同一天里「录入在下午、标记在当天」会让差值为负——读数该是 0 天。"""
    c = card(**{"created_at": "2026-10-04T18:00:00+08:00"})  # = 10:00Z，比 NOW 晚

    out = M.apply_attempt(c, "correct", confidence=0.99, at=NOW)

    assert out["gap_days"] == 0


def test_error_causes_string_is_wrapped_not_split_into_characters():
    """单个错因字符串要包成单元素列表——`list("计算失误")` 会拆成单字。"""
    c = card(**{"created_at": (NOW - 100 * DAY).isoformat()})

    M.apply_attempt(c, "wrong", error_causes="计算失误", at=NOW)

    assert c["attempts"][-1]["error_causes"] == ["计算失误"]


def test_attempt_records_the_five_fields_the_ticket_names():
    """#5 验收第 2 条：channel / source / confidence / provider / model 都落进卡里。"""
    c = card(**{"created_at": (NOW - 100 * DAY).isoformat()})

    M.apply_attempt(c, "correct", confidence=0.93, source=M.SOURCE_AUTO, at=NOW,
                    channel="screen", provider="deepseek", model="deepseek-flash",
                    judge_note="模型判等价且置信度 0.93 ≥ 阈值 0.9 → 对")

    attempt = c["attempts"][-1]
    assert attempt["channel"] == "screen"
    assert attempt["source"] == "auto"
    assert attempt["confidence"] == 0.93
    assert attempt["provider"] == "deepseek"
    assert attempt["model"] == "deepseek-flash"
    assert attempt["at"] == NOW.isoformat(timespec="seconds")
    assert "judge_note" in attempt


def test_low_confidence_correct_is_not_credited():
    """低置信度在映射层已落向看不清；状态机这一层收到「看不清」就两不相干。"""
    c = card(**{"created_at": (NOW - 100 * DAY).isoformat(), "mastery.streak": 1})

    out = M.apply_attempt(c, "unreadable", confidence=0.3, at=NOW)

    assert out["streak"] == 1 and out["credited"] is False


def test_bad_verdict_shouts_instead_of_inventing_a_state():
    with pytest.raises(ValueError):
        M.apply_attempt(card(), "maybe", at=NOW)


def test_bad_source_shouts():
    with pytest.raises(ValueError):
        M.apply_attempt(card(), "correct", source="模型说的", at=NOW)


def test_bad_channel_shouts():
    with pytest.raises(ValueError):
        M.apply_attempt(card(), "correct", channel="telepathy", at=NOW)


def test_unparseable_at_shouts():
    with pytest.raises(ValueError):
        M.apply_attempt(card(), "correct", at="昨天下午")
