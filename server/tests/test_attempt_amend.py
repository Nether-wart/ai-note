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


def test_the_cooldown_gate_is_taken_before_last_attempt_at_is_written(api_for):
    """定点钉住顺序：重放里每一步都是**先算冷却门、后写 `last_attempt_at`**。

    这一次重做发生在录入后 22 天（早已脱离冷却）→ 改判成「对」必须计入。
    若实现先把 `last_attempt_at` 写成这一刻再算冷却，这一刻就落在冷却里，
    读数会变成「只热身」——那正是 `docs/acceptance-log.md:277` 记的「真错」。
    """
    at = NOW - 8 * DAY
    original = rec(at, "unreadable", confidence=0.2)
    card = card_with([original],
                     mastery={"state": "in_pool", "streak": 0, "last_attempt_at": original["at"]},
                     created_at=NOW - 30 * DAY)
    _, api = api_for_one(api_for, card)

    _, body = post_json(api, f"/api/attempt/{PID}",
                        {"attempt_at": original["at"], "verdict": "correct"})

    mastery = body["data"]["mastery"]
    assert mastery["cooling"] is False, "这一刻尚未进入冷却（基准是录入时间）"
    assert mastery["credited"] is True, "冷却门必须在 last_attempt_at 被改写之前取"
    assert mastery["streak"] == 1
    assert "连续正确 1/2" in mastery["note"]


def test_a_changed_verdict_inside_the_window_is_only_a_warmup(api_for):
    """冷却期内的改判也照旧规则：只热身，`streak` 不动（同一条门，不是另一套规则）。"""
    first = rec(NOW - 2 * DAY, "correct", confidence=0.9)
    second = rec(NOW - 1 * DAY, "unreadable", confidence=0.2)
    card = card_with([first, second],
                     mastery={"state": "in_pool", "streak": 1,
                              "last_attempt_at": second["at"]})
    _, api = api_for_one(api_for, card)

    _, body = post_json(api, f"/api/attempt/{PID}",
                        {"attempt_at": second["at"], "verdict": "correct"})

    mastery = body["data"]["mastery"]
    assert (mastery["cooling"], mastery["credited"], mastery["streak"]) == (True, False, 1)
    assert mastery["gap_days"] == 1
    assert "热身" in mastery["note"]


def test_amending_an_attempt_that_is_not_the_last_one_replays_the_rest(api_for):
    """验收 1 的边界：被改的那次**不是最后一次**时，掌握由整段历史重放得到。

    三次重做各自隔了 8 天、都是「看不清」，只有最后一次是「对」→ 现在 streak=1。
    把**第一次**改成「对」：后面每一次都脱离冷却，于是第 1、3 次计入 → 直接毕业。
    卡级终态是重放的结果；而被改那一次的读数（`credited`/`note`）说的是它自己那一步。
    """
    a1 = rec(NOW - 17 * DAY, "unreadable", confidence=0.2)
    a2 = rec(NOW - 9 * DAY, "unreadable", confidence=0.2)
    a3 = rec(NOW - 1 * DAY, "correct", confidence=0.95)
    card = card_with([a1, a2, a3],
                     mastery={"state": "in_pool", "streak": 1, "last_attempt_at": a3["at"]},
                     created_at=NOW - 31 * DAY)
    _, api = api_for_one(api_for, card)

    _, body = post_json(api, f"/api/attempt/{PID}", {"attempt_at": a1["at"], "verdict": "correct"})

    mastery = body["data"]["mastery"]
    # 被改那一次自己那一步
    assert (mastery["credited"], mastery["cooling"]) == (True, False)
    assert "连续正确 1/2" in mastery["note"]
    # 重放后的卡级终态：第 3 次接着第 1 次的计数 → 毕业
    assert (mastery["state"], mastery["streak"]) == ("graduated", 2)
    assert mastery["last_attempt_at"] == a3["at"], "最后一次的时刻没被改"

    saved = card_on_disk(api)
    assert [a["verdict"] for a in saved["attempts"]] == ["correct", "unreadable", "correct"]
    assert saved["mastery"]["mastered_at"] == a3["at"]
    assert get_json(api, "/api/index")[1]["data"]["problems"][0]["graduated"] is True


# ---------------------------------------------------------------- 验收 2：只改错因

def test_changing_only_the_error_causes_leaves_mastery_untouched(api_for):
    """验收 2：错因是给人看的标注，**不参与状态机**。

    卡里的掌握读数（`streak`/`state`/`last_attempt_at`）与冷却在改前改后必须逐字相同，
    连判定本身的来源、置信度、模型都不动（那是一次「补记错因」，不是改判）。
    """
    at = NOW - 2 * DAY  # 仍在上一次重做的冷却窗口里
    original = rec(at, "wrong", confidence=0.3)
    card = card_with([original],
                     mastery={"state": "in_pool", "streak": 0, "last_attempt_at": original["at"]})
    _, api = api_for_one(api_for, card)
    before = card_on_disk(api)

    status, body = post_json(api, f"/api/attempt/{PID}",
                             {"attempt_at": original["at"], "error_causes": ["计算失误", "审题不清"]})

    assert status == 200, body
    assert body["data"]["attempt"]["error_causes"] == ["计算失误", "审题不清"]
    saved = card_on_disk(api)
    assert saved["mastery"] == before["mastery"], "掌握状态一个字都不许变"
    assert (saved["attempts"][0]["verdict"], saved["attempts"][0]["source"],
            saved["attempts"][0]["confidence"], saved["attempts"][0]["provider"],
            saved["attempts"][0]["model"]) == \
        (before["attempts"][0]["verdict"], before["attempts"][0]["source"],
         before["attempts"][0]["confidence"], before["attempts"][0]["provider"],
         before["attempts"][0]["model"]), "补记错因不是改判"
    assert "overrode" not in saved["attempts"][0], "没改判就不该出现原判定"


def test_a_single_error_cause_string_is_one_cause_not_its_characters(api_for):
    """单个字符串包成单元素列表——`list("计算失误")` 会拆成单字（原型踩过）。"""
    original = rec(NOW - 8 * DAY, "wrong", confidence=0.3)
    card = card_with([original], mastery={"state": "in_pool", "streak": 0,
                                          "last_attempt_at": original["at"]})
    _, api = api_for_one(api_for, card)

    _, body = post_json(api, f"/api/attempt/{PID}",
                        {"attempt_at": original["at"], "error_causes": "计算失误"})

    assert body["data"]["attempt"]["error_causes"] == ["计算失误"]


def test_an_empty_error_cause_list_clears_the_tags(api_for):
    """空列表＝把错因清掉，是合法的修改（不是「什么都没改」）。"""
    original = rec(NOW - 8 * DAY, "wrong", confidence=0.3, error_causes=["概念不清"])
    card = card_with([original], mastery={"state": "in_pool", "streak": 0,
                                          "last_attempt_at": original["at"]})
    _, api = api_for_one(api_for, card)

    _, body = post_json(api, f"/api/attempt/{PID}",
                        {"attempt_at": original["at"], "error_causes": []})

    assert body["data"]["attempt"]["error_causes"] == []


# ---------------------------------------------------------------- 验收 3：幂等

def test_submitting_the_same_amendment_twice_changes_nothing_the_second_time(api_for):
    """验收 3：同一 payload 提交两次，结果与一次一致。

    证据有两层：① 两次响应的 `data` 逐字相同（假时钟下 `index_rebuilt_at` 也相同）；
    ② 第二次之后**题卡文件一个字节都没变**——记录不追加、状态不再动。
    """
    original = rec(NOW - 8 * DAY, "correct", confidence=0.95)
    card = card_with([original],
                     mastery={"state": "in_pool", "streak": 1, "last_attempt_at": original["at"]})
    _, api = api_for_one(api_for, card)
    payload = {"attempt_at": original["at"], "verdict": "wrong", "error_causes": ["计算失误"]}
    path = api.catalog.problems_dir / f"{PID}.json"

    first_status, first = post_json(api, f"/api/attempt/{PID}", payload)
    after_first = path.read_bytes()

    second_status, second = post_json(api, f"/api/attempt/{PID}", payload)

    assert (second_status, second) == (first_status, first), "第二次与第一次结果一致"
    assert path.read_bytes() == after_first, "第二次不该再动题卡"
    saved = card_on_disk(api)
    assert len(saved["attempts"]) == 1, "不追加记录"
    assert saved["attempts"][0]["overrode"]["verdict"] == "correct", \
        "overrode 记的是最初那一次原判定，重复提交不覆盖它"


def test_a_second_different_verdict_keeps_the_original_original(api_for):
    """连着改两次：`overrode` 保留的始终是**最初**（机器给的）那次判定。"""
    original = rec(NOW - 8 * DAY, "correct", confidence=0.95)
    card = card_with([original],
                     mastery={"state": "in_pool", "streak": 1, "last_attempt_at": original["at"]})
    _, api = api_for_one(api_for, card)

    post_json(api, f"/api/attempt/{PID}", {"attempt_at": original["at"], "verdict": "wrong"})
    _, body = post_json(api, f"/api/attempt/{PID}",
                        {"attempt_at": original["at"], "verdict": "unreadable"})

    attempt = body["data"]["attempt"]
    assert attempt["verdict"] == "unreadable"
    assert attempt["overrode"]["verdict"] == "correct"
    assert attempt["overrode"]["confidence"] == 0.95


# ---------------------------------------------------------------- 验收 4：定位失败

def test_an_unknown_attempt_at_fails_loudly_instead_of_touching_the_latest(api_for):
    """验收 4：`attempt_at` 不存在 → 明确失败（结构化信封），**不落到最近一次**。

    卡一个字节都不许改：拿最近一次顶上就是「静默落到别的题上」那类事故的同一族。
    """
    latest = rec(NOW - 1 * DAY, "correct", confidence=0.95)
    card = card_with([rec(NOW - 8 * DAY, "wrong", confidence=0.2), latest],
                     mastery={"state": "in_pool", "streak": 1, "last_attempt_at": latest["at"]})
    _, api = api_for_one(api_for, card)
    before = (api.catalog.problems_dir / f"{PID}.json").read_bytes()

    status, body = post_json(api, f"/api/attempt/{PID}",
                             {"attempt_at": "2026-01-01T00:00:00+00:00", "verdict": "wrong"})

    assert status == 404, body
    assert body["ok"] is False
    error = body["error"]
    assert error["code"] == "not_found"
    assert error["reason"] == "attempt_not_found"
    assert "没有" in error["message"], "要有一句给人看的中文原话"
    assert error["details"]["id"] == PID
    assert error["details"]["attempt_at"] == "2026-01-01T00:00:00+00:00"
    assert error["details"]["available"] == [rec(NOW - 8 * DAY, "wrong")["at"], latest["at"]], \
        "把这道题**实际有哪些**重做时刻说出来（不许静默）"
    assert isinstance(body["warnings"], list) and body["warnings"], \
        "失败也带该卡的自检警告（契约 §10.1）"
    # 级别只有服务能定，界面不许自行降级（契约 §2）。警告通道有两级（warning 与 hint），
    # 所以这里断言「每项都有级别、取值合法」，而不是「一律 warning」。
    assert all(w["level"] in ("warning", "hint") for w in body["warnings"])
    assert (api.catalog.problems_dir / f"{PID}.json").read_bytes() == before, "卡一个字节都不改"


def test_a_near_miss_timestamp_is_not_snapped_to_the_nearest_attempt(api_for):
    """差一秒也是「没有那一次」——绝不按「最接近」匹配。"""
    only = rec(NOW - 8 * DAY, "unreadable", confidence=0.2)
    card = card_with([only], mastery={"state": "in_pool", "streak": 0,
                                      "last_attempt_at": only["at"]})
    _, api = api_for_one(api_for, card)
    near_miss = (NOW - 8 * DAY + timedelta(seconds=1)).isoformat(timespec="seconds")

    status, body = post_json(api, f"/api/attempt/{PID}",
                             {"attempt_at": near_miss, "verdict": "correct"})

    assert status == 404
    assert body["error"]["reason"] == "attempt_not_found"
    assert card_on_disk(api)["attempts"][0]["verdict"] == "unreadable"


def test_an_unknown_attempt_at_on_a_card_with_no_history_says_so(api_for):
    """从来没有重做过的卡：`available` 是空的，hint 也要说清「还没有任何重做记录」。"""
    card = card_with([], mastery={"state": "in_pool", "streak": 0, "last_attempt_at": None})
    _, api = api_for_one(api_for, card)

    status, body = post_json(api, f"/api/attempt/{PID}",
                             {"attempt_at": NOW.isoformat(timespec="seconds"), "verdict": "wrong"})

    assert status == 404
    assert body["error"]["details"]["available"] == []
    assert "还没有任何重做记录" in body["error"]["hint"]


def test_a_malformed_attempt_at_is_a_400_naming_the_parameter(api_for):
    only = rec(NOW - 8 * DAY, "unreadable", confidence=0.2)
    card = card_with([only], mastery={"state": "in_pool", "streak": 0,
                                      "last_attempt_at": only["at"]})
    _, api = api_for_one(api_for, card)

    for bad in ("昨天下午", "", "   ", 3, None):
        status, body = post_json(api, f"/api/attempt/{PID}",
                                 {"attempt_at": bad, "verdict": "wrong"})
        assert status == 400, (bad, body)
        assert body["error"]["code"] == "bad_request"
        assert body["error"]["details"]["param"] == "attempt_at"


def test_two_attempts_in_the_same_second_are_ambiguous_not_silently_picked(api_for):
    """`attempt_at` 定位不到唯一一次时也要明确失败（409），不许静默挑一个。"""
    same = rec(NOW - 8 * DAY, "wrong", confidence=0.2)
    duplicate = rec(NOW - 8 * DAY, "correct", confidence=0.9)
    card = card_with([same, duplicate],
                     mastery={"state": "in_pool", "streak": 0, "last_attempt_at": same["at"]})
    _, api = api_for_one(api_for, card)

    status, body = post_json(api, f"/api/attempt/{PID}",
                             {"attempt_at": same["at"], "verdict": "correct"})

    assert status == 409, body
    assert body["error"]["code"] == "ambiguous_attempt_at"
    assert body["error"]["reason"] == "ambiguous_attempt_at"
    assert body["error"]["details"]["candidates"] == [0, 1]
    assert card_on_disk(api)["attempts"][0]["verdict"] == "wrong", "歧义时什么都不改"


# ---------------------------------------------------------------- 形态与取值校验

def test_the_client_still_cannot_write_the_audit_fields(api_for):
    """`source`/`confidence`/`provider`/`model`/`overrode` 只有服务能写（400）。"""
    only = rec(NOW - 8 * DAY, "wrong", confidence=0.2)
    card = card_with([only], mastery={"state": "in_pool", "streak": 0,
                                      "last_attempt_at": only["at"]})
    _, api = api_for_one(api_for, card)

    for field, value in (("source", "human"), ("confidence", 1.0), ("provider", "deepseek"),
                         ("model", "deepseek-flash"), ("overrode", {}), ("channel", "paper"),
                         ("at", only["at"]), ("answer", "A")):
        status, body = post_json(api, f"/api/attempt/{PID}",
                                 {"attempt_at": only["at"], "verdict": "correct", field: value})
        assert status == 400, (field, status, body)
        assert body["error"]["details"]["param"] == "body"
        assert field in json.dumps(body["error"]["details"], ensure_ascii=False)

    assert card_on_disk(api)["attempts"][0]["verdict"] == "wrong", "一个字都不许写进去"


def test_a_bad_verdict_names_the_parameter_and_the_allowed_values(api_for):
    only = rec(NOW - 8 * DAY, "wrong", confidence=0.2)
    card = card_with([only], mastery={"state": "in_pool", "streak": 0,
                                      "last_attempt_at": only["at"]})
    _, api = api_for_one(api_for, card)

    status, body = post_json(api, f"/api/attempt/{PID}",
                             {"attempt_at": only["at"], "verdict": "maybe"})

    assert status == 400
    details = body["error"]["details"]
    assert details["param"] == "verdict"
    assert details["value"] == "maybe"
    assert details["allowed"] == ["correct", "wrong", "unreadable"]


def test_an_amendment_that_changes_nothing_is_a_400_not_a_silent_success(api_for):
    """只给 `attempt_at` ＝什么都没得改：门口就喊，不假装成功。"""
    only = rec(NOW - 8 * DAY, "wrong", confidence=0.2)
    card = card_with([only], mastery={"state": "in_pool", "streak": 0,
                                      "last_attempt_at": only["at"]})
    _, api = api_for_one(api_for, card)

    status, body = post_json(api, f"/api/attempt/{PID}", {"attempt_at": only["at"]})

    assert status == 400
    assert body["error"]["details"]["param"] == "body"


def test_an_unknown_key_in_the_amendment_form_is_a_400(api_for):
    only = rec(NOW - 8 * DAY, "wrong", confidence=0.2)
    card = card_with([only], mastery={"state": "in_pool", "streak": 0,
                                      "last_attempt_at": only["at"]})
    _, api = api_for_one(api_for, card)

    status, body = post_json(api, f"/api/attempt/{PID}",
                             {"attempt_at": only["at"], "error_causes": [], "note": "我自己写的"})

    assert status == 400
    assert body["error"]["details"]["param"] == "body"
    assert "note" in json.dumps(body["error"]["details"], ensure_ascii=False)


def test_a_bad_error_causes_shape_is_a_400_naming_the_parameter(api_for):
    only = rec(NOW - 8 * DAY, "wrong", confidence=0.2)
    card = card_with([only], mastery={"state": "in_pool", "streak": 0,
                                      "last_attempt_at": only["at"]})
    _, api = api_for_one(api_for, card)

    for bad in (3, {"a": 1}, [""], ["计算失误", 7], None):
        status, body = post_json(api, f"/api/attempt/{PID}",
                                 {"attempt_at": only["at"], "error_causes": bad})
        assert status == 400, (bad, status, body)
        assert body["error"]["details"]["param"] == "error_causes"


# ---------------------------------------------------------------- 定点修正不碰判定

def test_an_amendment_never_calls_the_judge_and_never_needs_autojudge(api_for):
    """改判是**人**给的判定：不问模型，也不看这道题能不能自动判定（#6 落点接缝）。"""
    only = rec(NOW - 8 * DAY, "wrong", confidence=0.2)
    card = card_with([only],
                     mastery={"state": "in_pool", "streak": 0, "last_attempt_at": only["at"]},
                     **{"problem.type": "solution", "standard_answer.value": "",
                        "review.status": "unreviewed"})
    stub, api = api_for_one(api_for, card)

    status, body = post_json(api, f"/api/attempt/{PID}",
                             {"attempt_at": only["at"], "verdict": "correct"})

    assert status == 200, body
    assert stub.calls == [], "定点修正一次模型都不该问（不联网、不花钱）"
    assert body["data"]["run_id"] is None
    assert body["data"]["attempt"]["verdict"] == "correct"


def test_the_index_is_rebuilt_after_an_amendment(api_for):
    """契约 §10.1：定点修正之后照旧重建派生索引（v0 = 立刻现算）。"""
    original = rec(NOW - 8 * DAY, "correct", confidence=0.95)
    card = card_with([original],
                     mastery={"state": "in_pool", "streak": 1, "last_attempt_at": original["at"]})
    _, api = api_for_one(api_for, card)

    _, body = post_json(api, f"/api/attempt/{PID}",
                        {"attempt_at": original["at"], "verdict": "wrong"})
    assert body["data"]["index_rebuilt_at"] == NOW.isoformat(timespec="seconds")

    problem = get_json(api, "/api/index")[1]["data"]["problems"][0]
    assert problem["attempts"] == 1, "不追加记录"
    assert problem["last_verdict"] == "wrong"
    assert problem["streak"] == 0 and problem["graduated"] is False
