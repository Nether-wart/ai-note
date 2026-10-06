"""写端点 `POST /api/attempt/<pid>` 的验收（#5，契约 §10.1）。

只测外部行为：**一个作答进去，一个判定与状态机读数出来，题卡与索引被改写**。
模型调用一律走注入的 stub——不联网、不花钱（#5 派发简报第 5 条）。

这里钉住 #5 的五条验收：

  1. 三种拒绝理由各自可复现（解答题／没有标准答案／未审核），且**只有一份实现**；
  2. 一次成功作答在卡里留下 `channel=screen` / source / confidence / provider / model；
  3. 判定调用留档到 `runs/`（真实现那一条在 `test_judge_client.py`，这里验 `run_id` 回传）；
  4. 索引重建、该题**当场**进冷却；
  5. 拒绝是明确的错误形状（422），模型失败是 502，**都不是 500**。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from conftest import LONG_AGO, get_json, make_card, post_json, post_raw
from server import judge
from server.autojudge import REASONS
from server.judge_client import JudgeCall, ModelUnavailable

PID = "p-20200101-aaaaaa"
NOW = datetime(2026, 10, 4, 9, 0, tzinfo=timezone.utc)
DAY = timedelta(days=1)
RUN_ID = "20261004-090000-000-judge.json"

EQUIVALENT = '{"equivalent": true, "confidence": 0.95, "reason": "写法不同但数学相同"}'


class StubJudge:
    """一个记账的假判定角色：喂进 `(标准答案, 作答)`，吐出固定的模型原文。"""

    def __init__(self, text: str = EQUIVALENT, *, provider: str = "deepseek",
                 model: str = "deepseek-flash", run_id: str | None = RUN_ID,
                 error: Exception | None = None) -> None:
        self.text = text
        self.provider = provider
        self.model = model
        self.run_id = run_id
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def __call__(self, standard_answer: str, answer: str) -> JudgeCall:
        self.calls.append((standard_answer, answer))
        if self.error is not None:
            raise self.error
        return JudgeCall(text=self.text, provider=self.provider, model=self.model,
                         run_id=self.run_id, usage={"prompt_tokens": 1})


def attempt(api_for, *cards, stub=None, clock=NOW, **build_kwargs):
    """建一个指向临时目录、判定角色被 stub 掉的 Api。"""
    stub = stub or StubJudge()
    return stub, api_for(list(cards), clock=lambda: clock, judge=stub, **build_kwargs)


def plain_card(**overrides):
    """一张能走自动判定的卡（已审核、有标准答案、非解答题），远离冷却。"""
    return make_card(PID, **overrides)


# ------------------------------------------------------------------ 验收 2、3、4

def test_a_screen_answer_is_judged_and_written_back(api_for):
    stub, api = attempt(api_for, plain_card())

    status, body = post_json(api, f"/api/attempt/{PID}", {"channel": "screen", "answer": "A"})

    assert status == 200 and body["ok"] is True, body
    data = body["data"]
    assert stub.calls == [("A", "A")], "标准答案与作答原样送进判定角色"

    rec = data["attempt"]
    assert rec["channel"] == "screen"
    assert rec["source"] == "auto"
    assert rec["confidence"] == 0.95
    assert rec["provider"] == "deepseek"
    assert rec["model"] == "deepseek-flash"
    assert rec["verdict"] == "correct"
    assert rec["at"] == NOW.isoformat(timespec="seconds")
    assert rec["error_causes"] == []
    assert isinstance(rec["note"], str) and rec["note"]

    mastery = data["mastery"]
    assert (mastery["state"], mastery["streak"]) == ("in_pool", 1)
    assert mastery["credited"] is True
    assert mastery["last_attempt_at"] == NOW.isoformat(timespec="seconds")
    assert mastery["note"] == rec["note"], "一次重做只有一句给界面看的原话"
    assert data["run_id"] == RUN_ID, "验收第 3 条：留档标识要回传"
    assert data["index_rebuilt_at"]


def test_the_card_on_disk_carries_the_five_fields(api_for):
    _, api = attempt(api_for, plain_card())

    post_json(api, f"/api/attempt/{PID}", {"channel": "screen", "answer": "A"})

    card = json.loads((api.catalog.problems_dir / f"{PID}.json").read_text(encoding="utf-8"))
    assert len(card["attempts"]) == 1
    saved = card["attempts"][0]
    assert saved["channel"] == "screen"
    assert saved["source"] == "auto"
    assert saved["confidence"] == 0.95
    assert saved["provider"] == "deepseek"
    assert saved["model"] == "deepseek-flash"
    assert card["mastery"]["last_attempt_at"] == NOW.isoformat(timespec="seconds")


def test_the_index_reflects_the_write_immediately(api_for):
    """验收第 4 条：索引重建；判完的题**当场**进冷却、退出默认打印清单。"""
    _, api = attempt(api_for, plain_card())

    post_json(api, f"/api/attempt/{PID}", {"channel": "screen", "answer": "A"})
    status, body = get_json(api, "/api/index")

    assert status == 200
    p = body["data"]["problems"][0]
    assert p["attempts"] == 1
    assert p["last_verdict"] == "correct"
    assert p["cooling"] is True and p["in_default_list"] is False
    assert p["mastery"]["last_attempt_at"] == NOW.isoformat(timespec="seconds")


def test_the_problem_detail_exposes_provider_and_confidence(api_for):
    """spec #1 US 28：日后能回答「这条判定是谁给的、当时有多确定」。"""
    _, api = attempt(api_for, plain_card())

    post_json(api, f"/api/attempt/{PID}", {"channel": "screen", "answer": "A"})
    _, body = get_json(api, f"/api/problem/{PID}")

    detail = body["data"]["attempts_detail"][0]
    assert (detail["provider"], detail["model"]) == ("deepseek", "deepseek-flash")
    assert detail["confidence"] == 0.95
    assert detail["source"] == "auto" and detail["channel"] == "screen"


def test_a_second_correct_in_the_cooling_window_is_only_a_warmup(api_for):
    """冷却必须在更新 `last_attempt_at` 之前算——这条在 HTTP 接缝上再钉一次。"""
    _, api = attempt(api_for, plain_card(**{"created_at": (NOW - 8 * DAY).isoformat()}))

    first = post_json(api, f"/api/attempt/{PID}", {"channel": "screen", "answer": "A"})[1]
    second = post_json(api, f"/api/attempt/{PID}", {"channel": "screen", "answer": "A"})[1]

    assert (first["data"]["mastery"]["credited"], first["data"]["mastery"]["streak"]) == (True, 1)
    assert (second["data"]["mastery"]["credited"], second["data"]["mastery"]["streak"]) == (False, 1)
    assert second["data"]["mastery"]["state"] == "in_pool", "一次热身不许推进到毕业"

    # `cooling` 是**这一次重做落在冷却里没有**（proto/slice.py:620 的口径：写之前取的闸门），
    # 不是「写完之后还在不在冷却」——后者恒为真，读数就没有信息了。
    assert first["data"]["mastery"]["cooling"] is False
    assert second["data"]["mastery"]["cooling"] is True

    _, body = get_json(api, "/api/index")
    assert body["data"]["problems"][0]["attempts"] == 2


# ------------------------------------------------------------------ 映射接上端点

def test_low_confidence_equivalence_lands_on_unreadable_and_is_recorded(api_for):
    """低置信度一律落向看不清，绝不落向对——记下这次重做，但不推进掌握。"""
    stub = StubJudge('{"equivalent": true, "confidence": 0.5, "reason": "不确定"}')
    _, api = attempt(api_for, plain_card(), stub=stub)

    status, body = post_json(api, f"/api/attempt/{PID}", {"channel": "screen", "answer": "A"})

    assert status == 200
    assert body["data"]["attempt"]["verdict"] == "unreadable"
    assert body["data"]["mastery"]["credited"] is False
    assert body["data"]["mastery"]["streak"] == 0


def test_not_equivalent_is_wrong_even_with_low_confidence(api_for):
    stub = StubJudge('{"equivalent": false, "confidence": 0.1, "reason": "不等价"}')
    _, api = attempt(api_for, plain_card(**{"mastery.streak": 1}), stub=stub)

    _, body = post_json(api, f"/api/attempt/{PID}", {"channel": "screen", "answer": "B"})

    assert body["data"]["attempt"]["verdict"] == "wrong"
    assert body["data"]["mastery"]["streak"] == 0


def test_unparseable_model_output_is_unreadable_and_says_so(api_for):
    """模型答非所问也是一次判定（看不清），但要显式喊出来（不许静默）。"""
    _, api = attempt(api_for, plain_card(), stub=StubJudge("抱歉，我读不懂这道题。"))

    status, body = post_json(api, f"/api/attempt/{PID}", {"channel": "screen", "answer": "A"})

    assert status == 200
    assert body["data"]["attempt"]["verdict"] == "unreadable"
    codes = [w["code"] for w in body["warnings"]]
    assert "judge_output_unparsed" in codes
    # 级别要显式发出来（契约 §2 的 level），界面不靠猜。不能断言「一律是 warning」：
    # 这张卡还没有页文件，会另带一条 **hint** 级 page_binding_missing（#9 验收 2）——
    # 提示与警告并存，正是级别要显式的原因。
    assert all(w["level"] in ("warning", "hint") for w in body["warnings"])
    assert next(w for w in body["warnings"] if w["code"] == "judge_output_unparsed")["level"] == "warning"
    assert body["data"]["mastery"]["streak"] == 0


# ------------------------------------------------------------------ 验收 1、5：拒绝

@pytest.mark.parametrize("overrides,expected", [
    ({"problem.type": "solution"}, "solution_type"),
    ({"standard_answer.value": ""}, "no_standard_answer"),
    ({"review.status": "unreviewed"}, "unreviewed"),
])
def test_the_three_rejections_say_which_reason(api_for, overrides, expected):
    stub, api = attempt(api_for, plain_card(**overrides))

    status, body = post_json(api, f"/api/attempt/{PID}", {"channel": "screen", "answer": "A"})

    assert status == 422, body          # 验收第 5 条：明确的错误形状，不是 500
    assert body["ok"] is False
    error = body["error"]
    assert error["code"] == "not_auto_judgeable"
    assert error["reason"] == expected
    assert error["message"] == REASONS[expected], "文案照 autojudge 的原话，不改写"
    assert error["details"]["id"] == PID
    assert stub.calls == [], "不能判定的题根本不该去问模型（不花钱）"


def test_a_rejection_carries_the_card_warnings(api_for):
    """拒绝时 `warnings[]` = 该卡的自检警告（契约 §10.1）。"""
    _, api = attempt(api_for, plain_card(**{"standard_answer.value": ""}))

    _, body = post_json(api, f"/api/attempt/{PID}", {"channel": "screen", "answer": "A"})

    codes = [w["code"] for w in body["warnings"]]
    assert "standard_answer_missing" in codes
    assert all(w["id"] == PID for w in body["warnings"])


def test_the_rejection_priority_is_the_autojudge_one(api_for):
    """三者同时成立时取 `solution_type`——优先级只有 autojudge 一份实现。"""
    card = plain_card(**{"problem.type": "solution", "standard_answer.value": "",
                         "review.status": "unreviewed"})
    _, api = attempt(api_for, card)

    _, body = post_json(api, f"/api/attempt/{PID}", {"channel": "screen", "answer": "A"})

    assert body["error"]["reason"] == "solution_type"


def test_a_rejection_writes_nothing(api_for):
    _, api = attempt(api_for, plain_card(**{"review.status": "unreviewed"}))

    post_json(api, f"/api/attempt/{PID}", {"channel": "screen", "answer": "A"})

    card = json.loads((api.catalog.problems_dir / f"{PID}.json").read_text(encoding="utf-8"))
    assert card["attempts"] == []
    assert card["mastery"]["last_attempt_at"] is None


# ------------------------------------------------------------------ 验收 5：模型失败 502

def test_a_failed_model_call_is_502_and_leaves_no_record(api_for):
    stub = StubJudge(error=ModelUnavailable("网络不通，重试 3 次：OSError: boom"))
    _, api = attempt(api_for, plain_card(**{"mastery.streak": 1}), stub=stub)

    status, body = post_json(api, f"/api/attempt/{PID}", {"channel": "screen", "answer": "A"})

    assert status == 502, body
    assert body["ok"] is False
    assert body["error"]["code"] == "model_unavailable"
    assert body["error"]["reason"] == "model_unavailable"
    assert "重试" in body["error"]["hint"], "要告诉人可以直接重试"

    card = json.loads((api.catalog.problems_dir / f"{PID}.json").read_text(encoding="utf-8"))
    assert card["attempts"] == [], "验收第 14 条：这一次重做不留下任何记录"
    assert card["mastery"]["last_attempt_at"] is None, "冷却不许被凭空重置"
    assert card["mastery"]["streak"] == 1


def test_a_failed_model_call_does_not_touch_the_index(api_for):
    _, api = attempt(api_for, plain_card(**{"mastery.streak": 1}),
                     stub=StubJudge(error=ModelUnavailable("boom")))

    post_json(api, f"/api/attempt/{PID}", {"channel": "screen", "answer": "A"})
    _, body = get_json(api, "/api/index")

    p = body["data"]["problems"][0]
    assert p["attempts"] == 0 and p["last_verdict"] is None
    assert p["mastery"]["last_attempt_at"] is None


def test_never_a_bare_500_on_any_input(api_for):
    _, api = attempt(api_for, plain_card())
    bad_bodies = [
        b"{not json", b"[]", b"", b'"a string"', b"null",
        b'{"channel": "screen"}',
        b'{"channel": "screen", "answer": 3}',
        b'{"channel": "paper", "answer": "A"}',
        b'{"answer": "A"}',
    ]
    for raw in bad_bodies:
        status, body = post_raw(api, f"/api/attempt/{PID}", raw)
        assert status == 400, (raw, status, body)
        assert body["ok"] is False and body["error"]["code"] == "bad_request"


# ------------------------------------------------------------------ 输入错 400

def test_the_client_cannot_touch_the_judgment(api_for):
    """spec：客户端绝不能自己声称「模型说等价」——判定只能由服务自己做。"""
    _, api = attempt(api_for, plain_card())
    for field, value in (("verdict", "correct"), ("source", "auto"), ("confidence", 1.0),
                         ("provider", "deepseek"), ("model", "deepseek-flash"),
                         ("overrode", {})):
        status, body = post_json(api, f"/api/attempt/{PID}",
                                 {"channel": "screen", "answer": "A", field: value})
        assert status == 400, (field, status, body)
        assert body["error"]["details"]["param"] == "body"
        assert field in json.dumps(body["error"]["details"], ensure_ascii=False)

    card = json.loads((api.catalog.problems_dir / f"{PID}.json").read_text(encoding="utf-8"))
    assert card["attempts"] == []


def test_an_empty_answer_is_rejected_instead_of_silently_clearing_the_streak(api_for):
    """空白作答不是「错」：它会静默清零一次掌握计数，宁可在门口喊。"""
    stub, api = attempt(api_for, plain_card())

    for empty in ("", "   ", "\n"):
        status, body = post_json(api, f"/api/attempt/{PID}", {"channel": "screen", "answer": empty})
        assert status == 400, (empty, body)
        assert body["error"]["details"]["param"] == "answer"
    assert stub.calls == []


@pytest.mark.parametrize("channel", ["paper", "telepathy", None, 1])
def test_only_the_screen_channel_is_implemented_here(api_for, channel):
    _, api = attempt(api_for, plain_card())

    status, body = post_json(api, f"/api/attempt/{PID}", {"channel": channel, "answer": "A"})

    assert status == 400, body
    assert body["error"]["code"] == "bad_request"
    assert body["error"]["details"]["param"] == "channel"
    assert body["error"]["details"]["allowed"] == ["screen"]


def test_a_missing_answer_is_a_400_naming_the_parameter(api_for):
    _, api = attempt(api_for, plain_card())

    status, body = post_json(api, f"/api/attempt/{PID}", {"channel": "screen"})

    assert status == 400
    assert body["error"]["details"]["param"] == "answer"


def test_a_path_traversal_id_is_a_400_not_a_404(api_for):
    _, api = attempt(api_for, plain_card())

    status, body = post_json(api, "/api/attempt/..%2fetc%2fpasswd",
                             {"channel": "screen", "answer": "A"})

    assert status == 400
    assert body["error"]["reason"] == "bad_request"


def test_an_empty_pid_is_a_400_not_a_404(api_for):
    """少给一段路径不是路由写错了——与 /api/problem 那两条 400 同一口径。"""
    _, api = attempt(api_for, plain_card())

    status, body = post_json(api, "/api/attempt/", {"channel": "screen", "answer": "A"})

    assert status == 400
    assert body["error"]["code"] == "bad_request"
    assert body["error"]["details"]["param"] == "pid"


def test_an_unknown_problem_is_a_404(api_for):
    _, api = attempt(api_for, plain_card())

    status, body = post_json(api, "/api/attempt/p-nope", {"channel": "screen", "answer": "A"})

    assert status == 404
    assert body["error"]["code"] == "not_found"


def test_get_on_the_write_endpoint_says_which_methods_it_takes(api_for):
    _, api = attempt(api_for, plain_card())

    status, body = get_json(api, f"/api/attempt/{PID}")

    assert status == 405
    assert body["error"]["code"] == "method_not_allowed"
    assert set(body["error"]["details"]["allowed"]) == {"POST", "OPTIONS"}


def test_the_threshold_config_is_used_for_this_request(api_for):
    """阈值是装载时校验过的那个值，端点上用它参与比较（0.5 下 0.6 的等价 → 对）。"""
    from server import config as config_mod

    cfg = config_mod.load_judge_config({"JUDGE_THRESHOLD": "0.5"})
    stub = StubJudge('{"equivalent": true, "confidence": 0.6, "reason": "勉强算对"}')
    _, api = attempt(api_for, plain_card(), stub=stub, config=cfg)

    _, body = post_json(api, f"/api/attempt/{PID}", {"channel": "screen", "answer": "A"})

    assert body["data"]["attempt"]["verdict"] == "correct"


def test_a_low_threshold_default_would_call_the_same_answer_unreadable(api_for):
    """对照：默认阈值 0.9 下同一个 0.6 的等价落向看不清（证明阈值真的被用了）。"""
    stub = StubJudge('{"equivalent": true, "confidence": 0.6, "reason": "勉强"}')
    _, api = attempt(api_for, plain_card(), stub=stub)

    _, body = post_json(api, f"/api/attempt/{PID}", {"channel": "screen", "answer": "A"})

    assert body["data"]["attempt"]["verdict"] == judge.VERDICT_UNREADABLE


def test_the_write_survives_a_card_whose_created_at_is_long_ago(api_for):
    """存量数据的冷启动事实：真实卡 created_at 是 +08:00、attempts 为空。"""
    card = plain_card(**{"created_at": LONG_AGO})
    _, api = attempt(api_for, card)

    status, body = post_json(api, f"/api/attempt/{PID}", {"channel": "screen", "answer": "A"})

    assert status == 200
    assert body["data"]["mastery"]["gap_days"] > 0


# ------------------------------------------------- body 的显式上限（最终修复 pass 作业单 2）

def test_an_oversized_body_is_rejected_before_anything_happens(api_for):
    """body 有**显式上限**（64 KiB）：超限 → 结构化 400，一个字节都不写、不问模型。

    没有上限时，2MB 的**合法 JSON** 会被整段读进内存、作答还会发给模型（#13 之后
    服务要经 Tailscale 给手机用）。这条拒绝必须在**进模型之前**发生（D1 / D9）。
    """
    stub, api = attempt(api_for, plain_card())
    before = (api.catalog.problems_dir / f"{PID}.json").read_bytes()
    # 合法 JSON、远小于上传上限（32MB），只是作答长了——正是「能过 JSON 校验」的那一类
    oversized = {"channel": "screen", "answer": "A" * (70 * 1024)}

    status, body = post_json(api, f"/api/attempt/{PID}", oversized)

    assert status == 400, body
    assert body["ok"] is False
    assert body["error"]["code"] == "bad_request"
    assert body["error"]["reason"] == "body_too_large"
    assert body["error"]["details"]["param"] == "body"
    assert body["error"]["details"]["max"] == 64 * 1024
    assert stub.calls == [], "超限的 body 根本不该去问模型（不花钱）"
    assert (api.catalog.problems_dir / f"{PID}.json").read_bytes() == before, \
        "拒绝就该一个字节都不动"


def test_a_declared_length_over_the_limit_is_rejected_without_reading_the_body(api_for):
    """`app.py` 只按 `Content-Length` 判大小、**不读** body；HTTP 层仍要给出同一个 400。

    这是真起服务时走的那条路（声明 2MB、body 根本没读进内存），不能因为
    「body 是空的」就掉进 `_parse_body` 的空 body 400——那样 reason 会说错、
    而且 `details` 里看不到真实大小。
    """
    stub, api = attempt(api_for, plain_card())

    response = api.handle("POST", f"/api/attempt/{PID}", body=b"", declared_length=2 * 1024 * 1024)
    body = json.loads(response.body)

    assert response.status == 400, body
    assert body["error"]["reason"] == "body_too_large"
    assert body["error"]["details"]["value"] == 2 * 1024 * 1024
    assert stub.calls == []


def test_a_normal_request_is_unaffected_by_the_limit(api_for):
    """正常请求不受影响：**恰好在上限内**的合法 body 照旧请求-判定-回写。"""
    stub, api = attempt(api_for, plain_card(), max_attempt_bytes=4096)
    payload = {"channel": "screen", "answer": "A"}
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    response = api.handle("POST", f"/api/attempt/{PID}", body=raw, declared_length=len(raw))
    body = json.loads(response.body)

    assert response.status == 200, body
    assert stub.calls == [("A", "A")]


def test_write_endpoints_get_the_tight_limit_and_uploads_do_not(api_for):
    """上限是**按路由**的：写端点（几个键的 JSON）64 KiB，上传（多部分、照片本来就有几 MB）32 MiB。

    这条判据**曾经编码了错的行为**：以前 `/api/page/<id>`（改页那几个动作，收的是 JSON）
    也落在上传档，于是那些请求会被**先读进内存、再按 64 KiB 拒绝**——而 §9 恰恰要求
    "在读 body 之前挡住"。独立验证者对着契约挑出这一条时它才红。**它红得正是时候**：
    分档写错的时候，只有这种判据能发现。
    """
    _, api = attempt(api_for, plain_card())

    for path in (f"/api/attempt/{PID}", "/api/settings", f"/api/problem/{PID}",
                 f"/api/page/{PID}", f"/api/page/{PID}/commit"):
        assert api.body_limit(path) == 64 * 1024, path
    # 收照片的两条留在上传档（**只有** `/api/page` 这个精确路径是"POST 上传新页"）
    for path in ("/api/inbox", "/api/page"):
        assert api.body_limit(path) == api.max_upload_bytes, path
