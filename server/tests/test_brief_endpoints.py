"""`/api/brief/<科目>` 的两个端点（#17 §10.5）：一个 HTTP 请求进去，一个信封出来。

这一层管三件事，与 `brief.py` 里那份（生成与数字闸门）**分开**：

1. **科目走词表逐字校验**——取值不在表里是 400 `subject_unknown` 带 `allowed`。
   它与卡级那条警告**同名同事实**（R2/R9：同一个事实不许两个码），只是层次不同：
   这里发生在「拒绝一次输入」，那里发生在「自检一份已有数据」。
2. 回执那三个**现算**的读数（`is_latest`／`stale`／`new_problems`）由这一层拼，
   **不写进文件**——它们是「此刻」的读数，落盘就会陈旧。
3. 走真 socket 与真模型的那两档不在这一层验：**问不成**（`ModelUnavailable` → 502）
   由 `model_client` 的传输层负责，这里只保证网关那两档形状（400 / 502）说得清。
"""

from __future__ import annotations

import json

from conftest import make_card, make_data_dir


def get(api, target: str):
    r = api.handle("GET", target)
    return r.status, json.loads(r.body)


def post(api, target: str, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    r = api.handle("POST", target, body, content_type="application/json")
    return r.status, json.loads(r.body)


def honest_client():
    """**诚实**的假 client：把候选读数原样抄回去（那是索引里的真值），所以闸门放行。"""
    def call(subject, digest):
        unclassified = next((f["value"] for f in digest["history_facts"]
                             if f["path"] == "stats.unclassified"), 0)
        return {
            "parsed": True,
            "text": f"（夹具）另有 {unclassified} 道未归类未计入。",
            "window_facts": [dict(f) for f in digest["window_facts"]],
            "history_facts": [dict(f) for f in digest["history_facts"]],
            "provider": "fake", "model": "fake-brief", "run_id": "run-fake",
        }
    return call


def fabricating_client():
    """**编数字**的假 client：未归零那条是真话，冷却道数是编的。"""
    def call(subject, digest):
        return {
            "parsed": True,
            "text": "冷却中有 999 道。另有 0 道未归类未计入。",
            "window_facts": [],
            "history_facts": [
                {"label": "未归类", "path": "stats.unclassified", "value": 0},
                {"label": "编出来的冷却道数",
                 "path": "stats.by_subject.数学.cooling", "value": 999},
            ],
        }
    return call


def _api(tmp_path, *, client=None):
    from server.http import Api

    root = make_data_dir(tmp_path, [make_card("p-20200101-aaaaaa")])
    return Api(root, brief_client=client or honest_client()), root


# ------------------------------------------------------------------ 输入错


def test_a_subject_outside_the_vocabulary_is_400_with_the_allowed_list(tmp_path):
    api, _ = _api(tmp_path)

    status, body = get(api, "/api/brief/化学")

    assert status == 400
    assert body["ok"] is False
    assert body["error"]["code"] == "bad_request"
    # **与卡级那条警告同名**：同一个事实、两个层次，名字不许分家
    assert body["error"]["reason"] == "subject_unknown"
    assert set(body["error"]["details"]["allowed"]) == {"数学", "物理"}
    assert body["error"]["hint"], "拒绝要说清怎么办"


def test_a_bad_window_days_is_refused_not_silently_defaulted(tmp_path):
    """拼错的 `window_days` 不许静默落到默认值上——那会让「我设了 30 天」永远查不出来。"""
    api, _ = _api(tmp_path)

    for value in ("7", 0, -3, True, 1.5):
        status, body = post(api, "/api/brief/数学", {"window_days": value})
        assert status == 400, f"{value!r} 应当被拒"
        assert body["error"]["details"]["param"] == "window_days"
        assert body["error"]["details"]["value"] == value


def test_a_non_json_body_is_a_400_not_a_500(tmp_path):
    api, _ = _api(tmp_path)

    r = api.handle("POST", "/api/brief/数学", b"not json", content_type="application/json")

    assert r.status == 400
    assert json.loads(r.body)["error"]["details"]["param"] == "body"


def test_an_empty_body_is_allowed_because_every_parameter_has_a_default(tmp_path):
    """空 body 是**合法**的（与 `attempt`／`PATCH` 那两处故意不同：那两个 body 是必填）。"""
    api, _ = _api(tmp_path)

    r = api.handle("POST", "/api/brief/数学", b"", content_type="application/json")

    assert r.status == 200, r.body


def test_an_oversized_body_is_refused_before_it_is_read(tmp_path):
    api, _ = _api(tmp_path)
    body = b'{"window_days":' + b" " * (api.max_attempt_bytes + 10) + b"7}"

    r = api.handle("POST", "/api/brief/数学", body, content_type="application/json",
                   declared_length=len(body))

    assert r.status == 400
    assert json.loads(r.body)["error"]["reason"] == "body_too_large"


def test_using_the_wrong_method_is_a_405_with_the_allowed_list(tmp_path):
    api, _ = _api(tmp_path)

    r = api.handle("DELETE", "/api/brief/数学")

    assert r.status == 405
    error = json.loads(r.body)["error"]
    assert "POST" in error["details"]["allowed"] and "OPTIONS" in error["details"]["allowed"]


# ------------------------------------------------------------------ 读


def test_no_brief_yet_is_a_404_that_says_which_subject(tmp_path):
    """「还没有」不是「读不到」：404 `brief_missing`，并且说清是哪个科目、有哪几天。"""
    api, _ = _api(tmp_path)

    status, body = get(api, "/api/brief/数学")

    assert status == 404
    assert body["error"]["reason"] == "brief_missing"
    assert body["error"]["details"]["subject"] == "数学"
    assert body["error"]["details"]["available"] == []


def test_reading_a_historical_brief_says_it_is_not_the_latest(tmp_path):
    """`?at=` 取历史那一份时要明说「它已经不是最新」——侧栏照这句话显示。"""
    from server import brief

    api, _ = _api(tmp_path)
    for date in ("2019-01-01", "2019-01-05"):
        brief.save_brief(api.catalog, {
            "subject": "数学", "generated_at": f"{date}T00:00:00+00:00",
            "window_days": 7, "window_from": date, "window_until": date,
            "covers_until": f"{date}T00:00:00+00:00",
            "provider": "fake", "model": "fake-brief", "text": "（夹具）",
            "window_facts": [], "history_facts": [],
        })

    status, body = get(api, "/api/brief/数学?at=2019-01-01")
    assert status == 200
    assert body["data"]["brief"]["is_latest"] is False
    assert body["data"]["brief"]["generated_at"] == "2019-01-01T00:00:00+00:00"

    status, body = get(api, "/api/brief/数学")
    assert status == 200
    assert body["data"]["brief"]["is_latest"] is True
    assert body["data"]["brief"]["window_until"] == "2019-01-05"


# ------------------------------------------------------------------ 生成


def test_generating_a_brief_returns_the_three_computed_readouts(tmp_path):
    api, root = _api(tmp_path)

    status, body = post(api, "/api/brief/数学", {})

    assert status == 200, body
    data = body["data"]
    assert data["brief"]["subject"] == "数学"
    assert data["brief"]["is_latest"] is True
    assert data["brief"]["stale"] is False and data["brief"]["new_problems"] == 0
    assert data["brief"]["provider"] == "fake" and data["brief"]["model"] == "fake-brief"
    assert data["run_id"] == "run-fake"
    assert (root / "briefs").is_dir(), "生成成功就要落盘"
    # 「未归类」那句必须真的在正文里，且它的数字过的是同一道闸门
    assert "未归类未计入" in data["brief"]["text"]


def test_a_generated_brief_shows_up_in_the_index_readout(tmp_path):
    """生成之后索引里的那一栏读数要跟着变——侧栏就是靠它显示「过期了没有」。"""
    api, _ = _api(tmp_path)

    before = json.loads(api.handle("GET", "/api/index").body)["data"]["briefs"]["数学"]
    assert before["latest_date"] is None

    post(api, "/api/brief/数学", {})

    after = json.loads(api.handle("GET", "/api/index").body)["data"]["briefs"]["数学"]
    assert after["latest_date"] is not None
    assert after["stale"] is False and after["new_problems"] == 0


def test_a_fabricated_number_is_refused_and_nothing_is_written(tmp_path):
    """**闸门**：编出来的数字 → 502 `brief_unverifiable`，且**一个字节都不落盘**。

    「不落盘」是判据的一部分：一份数字对不上的简报留在历史上的话，
    「上周最弱的是三角函数」这句话下个月还会被人当真。
    """
    api, root = _api(tmp_path, client=fabricating_client())

    status, body = post(api, "/api/brief/数学", {})

    assert status == 502
    assert body["error"]["reason"] == "brief_unverifiable"
    facts = body["error"]["details"]["facts"]
    assert any(f["reason"] == "value_mismatch" for f in facts), facts
    assert not (root / "briefs").exists(), "闸门没过就不许留下任何东西"


def test_a_brief_with_a_window_of_several_days_is_accepted(tmp_path):
    """`window_days` 给了就照给（含端点），回执里的窗口起止与它一致。"""
    api, _ = _api(tmp_path)

    status, body = post(api, "/api/brief/数学", {"window_days": 30})

    assert status == 200, body
    doc = body["data"]["brief"]
    assert doc["window_days"] == 30
    assert doc["window_from"] < doc["window_until"], "窗口起点要早于终点"
