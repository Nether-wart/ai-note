"""判定角色调用的接缝：提示词、HTTP、留档。

**测试不联网、不花钱**：HTTP 走一个假的 `transport`（`(url, headers, payload, timeout)
→ (status, text)`），重试用的 `sleep` 也是假的（不然 3 次退避要等 12 秒）。
真路径只多一层 `urllib` 的默认 transport，形状完全一样。

这里钉住 #5 验收第 3 条（留档到 `runs/`）与 spec #1 US 38（**只发文本，不发图片**），
以及编排裁决 D1：调用失败是 `ModelUnavailable` 异常，**绝不许 `sys.exit`**
（原型的失败路径是 `die()` = `sys.exit(2)`，HTTP 处理器里用它会杀掉请求）。
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from server import config
from server.judge_client import (
    JUDGE_TAG,
    ModelUnavailable,
    HttpJudge,
    extract_json,
    judge_messages,
)

KEY = "test-key-not-real"
OK_BODY = json.dumps({
    "choices": [{"message": {"content": '{"equivalent": true, "confidence": 0.97, '
                                          '"reason": "写法不同但数学相同"}'}}],
    "usage": {"prompt_tokens": 42, "completion_tokens": 7},
})


class FakeTransport:
    """按顺序吐预先排好的 `(status, text)`；元素是异常就抛出来（模拟网络错）。"""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, url, headers, payload, timeout):
        self.calls.append({"url": url, "headers": headers, "payload": payload,
                           "timeout": timeout})
        item = self.responses.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture
def cfg():
    return config.load_judge_config({"JUDGE_PROVIDER": "deepseek", "JUDGE_MODEL": "deepseek-flash"})


@pytest.fixture
def sleeper():
    delays: list[float] = []
    return delays, delays.append


def make_judge(cfg, runs_dir, transport, sleeps=None, env=None):
    return HttpJudge(cfg, runs_dir, transport=transport,
                     sleep=(sleeps.append if sleeps is not None else (lambda _s: None)),
                     env={"DEEPSEEK_API_KEY": KEY} if env is None else env)


def test_a_successful_call_returns_the_text_and_archives_it(cfg, tmp_path):
    transport = FakeTransport((200, OK_BODY))
    judge = make_judge(cfg, tmp_path / "runs", transport)

    call = judge("A", "A")

    assert json.loads(call.text)["equivalent"] is True
    assert (call.provider, call.model) == ("deepseek", "deepseek-flash")
    assert call.run_id and call.run_id.endswith(f"-{JUDGE_TAG}.json")
    assert call.usage == {"prompt_tokens": 42, "completion_tokens": 7}

    run_file = tmp_path / "runs" / call.run_id
    assert run_file.is_file(), "验收第 3 条：每次自动判定留一份调用档"
    run = json.loads(run_file.read_text(encoding="utf-8"))
    assert run["payload"]["model"] == "deepseek-flash"
    assert run["payload"]["messages"] == transport.calls[0]["payload"]["messages"]
    assert run["response"]["choices"][0]["message"]["content"] == call.text


def test_the_prompt_is_text_only_no_images_anywhere(cfg, tmp_path):
    """spec #1 US 38：判定只把文本（标准答案与作答）发给模型，不发送任何图片。"""
    transport = FakeTransport((200, OK_BODY))
    judge = make_judge(cfg, tmp_path / "runs", transport)

    judge("A", "B")
    payload = transport.calls[0]["payload"]

    assert payload["model"] == "deepseek-flash"
    assert payload["temperature"] == 0.0
    assert [m["role"] for m in payload["messages"]] == ["system", "user"]
    assert all(isinstance(m["content"], str) for m in payload["messages"])
    blob = json.dumps(payload, ensure_ascii=False)
    assert "image_url" not in blob and "base64" not in blob and "data:" not in blob
    user = payload["messages"][1]["content"]
    assert "A" in user and "B" in user, "标准答案与作答都要进提示词"
    assert "等价" in payload["messages"][0]["content"]

    run = json.dumps(judge_messages("A", "B"), ensure_ascii=False)
    assert "image" not in run


def test_missing_key_says_which_variable_and_never_touches_the_network(cfg, tmp_path):
    transport = FakeTransport()
    judge = make_judge(cfg, tmp_path / "runs", transport, env={})

    with pytest.raises(ModelUnavailable) as excinfo:
        judge("A", "A")

    assert "DEEPSEEK_API_KEY" in str(excinfo.value)
    assert KEY not in str(excinfo.value), "报错里不许出现密钥值"
    assert transport.calls == []


def test_a_network_error_is_retried_then_reported_as_model_unavailable(cfg, tmp_path):
    transport = FakeTransport(*[OSError("connection refused")] * 4)
    sleeps: list[float] = []
    judge = make_judge(cfg, tmp_path / "runs", transport, sleeps=sleeps)

    with pytest.raises(ModelUnavailable) as excinfo:
        judge("A", "A")

    assert "connection refused" in str(excinfo.value)
    assert len(transport.calls) == 4  # 首次 + 3 次重试
    assert len(sleeps) == 3, "重试之间要退避"
    assert not (tmp_path / "runs").exists() or not list((tmp_path / "runs").iterdir())


def test_a_5xx_is_retried_and_can_succeed_on_the_second_try(cfg, tmp_path):
    transport = FakeTransport((503, "upstream busy"), (200, OK_BODY))
    judge = make_judge(cfg, tmp_path / "runs", transport)

    call = judge("A", "A")

    assert json.loads(call.text)["equivalent"] is True
    assert len(transport.calls) == 2


def test_a_4xx_is_not_retried(cfg, tmp_path):
    transport = FakeTransport((400, "bad request: model unknown"))
    judge = make_judge(cfg, tmp_path / "runs", transport)

    with pytest.raises(ModelUnavailable) as excinfo:
        judge("A", "A")

    assert "400" in str(excinfo.value) and "model unknown" in str(excinfo.value)
    assert len(transport.calls) == 1


def test_retries_exhausted_on_5xx_raise_instead_of_looping_forever(cfg, tmp_path):
    transport = FakeTransport(*[(500, "boom")] * 4)
    judge = make_judge(cfg, tmp_path / "runs", transport)

    with pytest.raises(ModelUnavailable) as excinfo:
        judge("A", "A")

    assert "500" in str(excinfo.value)
    assert len(transport.calls) == 4


def test_a_200_that_is_not_json_is_a_call_failure_not_a_judgment(cfg, tmp_path):
    transport = FakeTransport((200, "<html>gateway timeout</html>"))
    judge = make_judge(cfg, tmp_path / "runs", transport)

    with pytest.raises(ModelUnavailable):
        judge("A", "A")

    runs = tmp_path / "runs"
    assert not runs.exists() or not list(runs.iterdir()), "解析不出 JSON 就没建成留档"


def test_a_json_body_without_content_keeps_the_evidence_but_still_fails(cfg, tmp_path):
    transport = FakeTransport((200, json.dumps({"error": "content filter"})))
    judge = make_judge(cfg, tmp_path / "runs", transport)

    with pytest.raises(ModelUnavailable):
        judge("A", "A")

    files = list((tmp_path / "runs").iterdir())
    assert len(files) == 1, "上游返回了什么要留证据（不许静默）"
    assert "content filter" in files[0].read_text(encoding="utf-8")


def test_content_returned_as_parts_is_joined(cfg, tmp_path):
    body = json.dumps({"choices": [{"message": {"content": [
        {"type": "text", "text": '{"equivalent": false,'},
        {"type": "text", "text": ' "confidence": 0.9, "reason": "不等"}'},
    ]}}]})
    judge = make_judge(cfg, tmp_path / "runs", FakeTransport((200, body)))

    call = judge("A", "B")

    assert json.loads(call.text)["equivalent"] is False


def test_two_calls_in_the_same_millisecond_do_not_overwrite_each_other(cfg, tmp_path):
    """留档名带毫秒戳；同一毫秒里的两次调用也必须各留一份，不许互相覆盖。"""
    frozen = datetime(2026, 10, 4, 9, 0, 0, 123000)
    judge = HttpJudge(cfg, tmp_path / "runs", transport=FakeTransport((200, OK_BODY), (200, OK_BODY)),
                      sleep=lambda _s: None, env={"DEEPSEEK_API_KEY": KEY}, clock=lambda: frozen)

    first, second = judge("A", "A"), judge("A", "B")

    assert first.run_id != second.run_id
    assert len(list((tmp_path / "runs").iterdir())) == 2


def test_run_archival_never_writes_outside_the_runs_dir(cfg, tmp_path):
    judge = make_judge(cfg, tmp_path / "runs", FakeTransport((200, OK_BODY)))
    call = judge("A", "A")
    assert "/" not in (call.run_id or "") and ".." not in (call.run_id or "")


# ------------------------------------------------------------------ 纯逻辑

def test_extract_json_strips_fences_and_prose():
    assert extract_json('```json\n{"equivalent": true}\n```') == {"equivalent": True}
    assert extract_json('好的，结果是：{"equivalent": false} 以上。') == {"equivalent": False}


def test_extract_json_handles_nested_objects_and_braces_inside_strings():
    raw = '{"equivalent": true, "reason": "值 {a} 与 \\"}\\" 都在字符串里", "meta": {"n": 1}}'
    assert extract_json(raw)["meta"] == {"n": 1}


@pytest.mark.parametrize("raw", ["没有 JSON", "```json\n{,}\n```", ""])
def test_extract_json_shouts_when_there_is_nothing_to_parse(raw):
    with pytest.raises(ValueError):
        extract_json(raw)


def test_judge_messages_pins_the_equivalence_shape():
    system, user = judge_messages("2/√3", "2√3/3")
    assert "equivalent" in system and "confidence" in system
    assert "2/√3" in user and "2√3/3" in user
