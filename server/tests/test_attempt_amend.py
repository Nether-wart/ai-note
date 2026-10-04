"""定点修正 `POST /api/attempt/<pid>` 的第三种形态（#6，契约 §10.1）。

`{attempt_at, error_causes?, verdict?}` —— **只改既有那一次重做**：不新建、不重跑判定、
不调模型。这里钉住 #6 的四条验收，加上两条契约边界（定位失败 / 定位歧义）：

  1. 改判会**重算**掌握状态（判错清零；冷却按既有规则重算，且冷却门取在写
     `last_attempt_at` **之前**——这一条单独一条测试钉住）；
  2. 改错因**不改变**掌握状态（`streak`/`state`/冷却读数一律不动）；
  3. 同一次修改重复提交**幂等**（不追加记录、不改状态）；
  4. `attempt_at` 不存在时**明确失败**，绝不落到最近一次；
  5. 改判保留原判定（`overrode`），来源变 `human`（spec #1 US 17、US 28）；
  6. 定位歧义（同一秒两次重做）也**明确失败**，不静默挑一个。

只测外部行为（HTTP 接缝）：一个修正进去，卡与索引的读数出来。模型一次都不调。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from conftest import get_json, make_card, post_json
from test_attempt_endpoint import StubJudge

PID = "p-20200101-aaaaaa"
NOW = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)
DAY = timedelta(days=1)


def rec(at, verdict, *, source="auto", confidence=0.9, provider="deepseek",
        model="deepseek-flash", error_causes=(), note=None):
    """卡里**已有**的那一次重做（形状照契约 §10.1 的 attempt）。"""
    return {
        "at": at.isoformat(timespec="seconds"),
        "channel": "screen",
        "verdict": verdict,
        "source": source,
        "confidence": confidence,
        "provider": provider,
        "model": model,
        "error_causes": list(error_causes),
        "note": note,
    }


def card_with(attempts, *, mastery, created_at=(NOW - 30 * DAY), **overrides):
    """一张已重做过若干次的卡：定点修正改的正是这些既有记录。"""
    return make_card(PID, **{
        "created_at": created_at.isoformat(),
        "attempts": attempts,
        "mastery": mastery,
        **overrides,
    })


def api_for_one(api_for, card, *, judge=None):
    """判定角色一律是 stub：定点修正不该调模型，这个接缝用来证明它没调。"""
    stub = judge or StubJudge()
    return stub, api_for([card], clock=lambda: NOW, judge=stub)


def card_on_disk(api):
    return json.loads((api.catalog.problems_dir / f"{PID}.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------- 验收 1：改判重算

def test_changing_the_verdict_to_wrong_clears_the_streak(api_for):
    """判错清零：把唯一那次重做改成「错」→ 掌握回到 0/在池。"""
    at = NOW - 8 * DAY
    card = card_with([rec(at, "correct", confidence=0.95)],
                     mastery={"state": "in_pool", "streak": 1,
                              "last_attempt_at": rec(at, "correct")["at"]})
    _, api = api_for_one(api_for, card)

    status, body = post_json(api, f"/api/attempt/{PID}",
                             {"attempt_at": rec(at, "correct")["at"], "verdict": "wrong"})

    assert status == 200 and body["ok"] is True, body
    data = body["data"]
    assert data["attempt"]["verdict"] == "wrong"
    assert data["mastery"]["streak"] == 0
    assert data["mastery"]["state"] == "in_pool"
    assert "判错" in data["mastery"]["note"]
    assert data["run_id"] is None, "定点修正不调模型、不留档"

    saved = card_on_disk(api)
    assert len(saved["attempts"]) == 1, "只改既有那一次：不新建记录"
    assert saved["attempts"][0]["verdict"] == "wrong"
    assert saved["mastery"]["streak"] == 0
    assert saved["mastery"]["last_attempt_at"] == rec(at, "correct")["at"], \
        "重做时刻不变——定点修正定位的就是它"


def test_a_changed_verdict_keeps_the_original_judgment_visible(api_for):
    """spec #1 US 17：改判之后仍能看出原本的判定是什么、是谁给的。"""
    at = NOW - 8 * DAY
    original = rec(at, "correct", confidence=0.95)
    card = card_with([original],
                     mastery={"state": "in_pool", "streak": 1,
                              "last_attempt_at": original["at"]})
    _, api = api_for_one(api_for, card)

    _, body = post_json(api, f"/api/attempt/{PID}",
                        {"attempt_at": original["at"], "verdict": "wrong"})

    attempt = body["data"]["attempt"]
    assert attempt["source"] == "human", "改判后来源变成人给的来源"
    assert attempt["confidence"] is None, "人判的没有模型置信度"
    assert (attempt["provider"], attempt["model"]) == (None, None), \
        "provider/model 是「谁判的」；改判后判的是人"
    assert attempt["overrode"] == {"verdict": "correct", "source": "auto", "confidence": 0.95,
                                   "provider": "deepseek", "model": "deepseek-flash"}, \
        "原判定（含是哪台机器给的）不许被抹掉"

    detail = get_json(api, f"/api/problem/{PID}")[1]["data"]["attempts_detail"]
    assert detail[0]["overrode"]["verdict"] == "correct", "详情页也看得到原判定"
    assert detail[0]["source"] == "human"
