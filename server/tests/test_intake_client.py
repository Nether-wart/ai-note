"""**抽取角色**的调用接缝：红笔痕迹的语义（#12）。

这一层只做三件事：拼提示词（**要看图**）、把模型的原文解析成语义、留档。
决策不在这里（`server/intake.py`），管道不在这里（`server/model_client.py`）。

**测试不联网、不花钱**：HTTP 走假 `transport`，重试用的 `sleep` 也是假的。
真路径只多一层 `urllib` 的默认 transport（形状一样），另有一条**本机回环**的假
endpoint 把「数据 URL 真的发出去了」验一遍——那不是联网，是不出机器的自证。
"""

from __future__ import annotations

import base64
import json
import re
from datetime import datetime

import pytest

from server import config, ink, intake, intake_client
from server.model_client import ModelUnavailable

KEY = "test-key-not-real"
OK_BODY = json.dumps({
    "choices": [{"message": {"content": json.dumps({
        "semantics": "correction", "confidence": 0.93,
        "reason": "红笔写了一个新的答案，把原来的圈掉了",
    }, ensure_ascii=False)}}],
    "usage": {"prompt_tokens": 900, "completion_tokens": 30},
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
    return config.load_role_config("extract", {"EXTRACT_PROVIDER": "deepseek",
                                               "EXTRACT_MODEL": "deepseek-flash"})


@pytest.fixture
def page_png(tmp_path):
    """一页合成假卷：白底 + 一个红笔块（自造，不碰任何真照片）。"""
    width, height = 60, 40
    pixels = [(255, 255, 255)] * (width * height)
    for y in range(5, 15):
        for x in range(5, 35):
            pixels[y * width + x] = (220, 30, 30)   # 红笔（高饱和）
    for y in range(25, 30):
        for x in range(5, 55):
            pixels[y * width + x] = (20, 20, 20)    # 印刷体/黑笔
    path = tmp_path / "41c86bcfc007.png"
    ink.write_png(path, ink.InkImage(width, height, pixels))
    return path


def make_semantics(cfg, runs_dir, transport, *, env=None, clock=None):
    return intake_client.HttpSemantics(
        cfg, runs_dir, transport=transport, sleep=lambda _s: None,
        env={"DEEPSEEK_API_KEY": KEY} if env is None else env, clock=clock)


BLOCK = {"id": "b2", "bbox_norm": [0.1, 0.2, 0.5, 0.1], "bbox_px": [5, 5, 35, 15],
         "card_id": None, "keep": None}
STATS = {"area": 300, "colored_px": 300, "colored_ratio": 1.0, "dark_px": 0, "dark_ratio": 0.0}


# ---------------------------------------------------------------- 提示词与消息形状


def test_the_prompt_lists_exactly_the_enum_the_decision_table_knows():
    """提示词里的枚举与决策表里的枚举是同一份——两边各写一套就会各判各的。"""
    for value in intake.SEMANTICS_VALUES:
        assert value in intake_client.SEMANTICS_SYSTEM, value
    assert "判不准" in intake_client.SEMANTICS_SYSTEM
    assert "只输出一个 JSON" in intake_client.SEMANTICS_SYSTEM


def test_the_message_carries_the_page_image_and_the_block_boundary(page_png):
    """抽取角色**要看图**（与判定角色的纯文本相反，spec #1 US 38 只管判定）。"""
    messages = intake_client.semantics_messages(
        block=BLOCK, stats=STATS, image_url="data:image/png;base64,AAAA")

    assert [m["role"] for m in messages] == ["system", "user"]
    system, user = messages
    assert isinstance(system["content"], str)
    parts = user["content"]
    assert [p["type"] for p in parts] == ["text", "image_url"]
    assert parts[1]["image_url"]["url"] == "data:image/png;base64,AAAA"
    text = parts[0]["text"]
    assert "0.1" in text and "0.2" in text, "块的整页归一化边界要写进提示词（两套坐标别混）"
    assert "300" in text, "统计到的红笔像素数也要写进去（统计与语义两层都要有）"
    assert "b2" not in text, "块 id 是内部编号，对模型没有意义（不喂它）"


def test_the_image_data_url_is_a_real_png_of_the_page(page_png, tmp_path):
    url = intake_client.image_data_url(page_png)

    assert url.startswith("data:image/png;base64,")
    blob = base64.b64decode(url.split(",", 1)[1])
    assert blob == page_png.read_bytes(), "没超上限就是原图字节，不是重新编码过的东西"

    probe = tmp_path / "probe.png"
    probe.write_bytes(blob)
    assert ink.read_png(probe).size == (60, 40)


def test_an_oversized_page_is_downscaled_to_the_inherited_max_side(tmp_path):
    """口径继承 proto：进模型前最长边压到 1600（`proto/slice.py:99-100`）。"""
    width, height = 2000, 100
    pixels = [(255, 255, 255)] * (width * height)
    path = tmp_path / "big.png"
    ink.write_png(path, ink.InkImage(width, height, pixels))

    url = intake_client.image_data_url(path)
    blob = base64.b64decode(url.split(",", 1)[1])
    probe = tmp_path / "probe.png"
    probe.write_bytes(blob)
    w, h = ink.read_png(probe).size

    assert max(w, h) == intake_client.MAX_SIDE
    assert h == 80, "按比例缩，不是硬裁"


def test_a_page_photo_in_jpeg_is_sent_as_jpeg(tmp_path):
    """#13 的收件目录会把手机照片按原后缀存下来（.jpg），别按 PNG 发出去。"""
    path = tmp_path / "phone.jpg"
    path.write_bytes(b"\xff\xd8\xff\xe0" + b"not-really-a-jpeg" * 4)

    assert intake_client.image_data_url(path).startswith("data:image/jpeg;base64,")


# ---------------------------------------------------------------- 解析（模型原文 → 语义）


def test_a_clean_answer_is_parsed_into_the_semantics_shape():
    answer = intake_client.parse_semantics(json.dumps({
        "semantics": "tick", "confidence": 0.8, "reason": "只有一个对勾",
    }, ensure_ascii=False))

    assert answer["parsed"] is True
    assert answer["semantics"] == "tick"
    assert answer["confidence"] == 0.8
    assert answer["reason"] == "只有一个对勾"


def test_a_fenced_answer_is_parsed_like_everywhere_else():
    answer = intake_client.parse_semantics(
        '好的：\n```json\n{"semantics": "cross", "confidence": 0.9, "reason": "打了叉"}\n```')

    assert answer["semantics"] == "cross" and answer["parsed"] is True


def test_an_answer_that_is_not_json_is_a_parsed_false_not_an_exception():
    """模型答了话但不是 JSON：算「判不准」（落向收 + 一条 warning），不是调用失败。

    这一档与「问都没问成」是两件事：后者是 `ModelUnavailable`（502、什么都不写），
    前者是一次回答——`server/attempt.py` 对判定角色也是这么分的
    （`judge_output_unparsed` 那条 warning）。
    """
    answer = intake_client.parse_semantics("我觉得这点红笔是订正吧")

    assert answer["parsed"] is False
    assert answer["semantics"] is None
    assert "订正" in answer["reason"], "把模型的原话留着，别只说「解析失败」"


def test_a_missing_or_bogus_semantics_field_is_parsed_false():
    for raw in ["{}", '{"reason": "看不清"}', '{"semantics": 17}', '{"semantics": null}']:
        answer = intake_client.parse_semantics(raw)
        assert answer["parsed"] is False, raw
        assert answer["semantics"] is None, raw


def test_a_label_outside_the_enum_is_kept_as_written():
    """枚举外的取值照样记下来（判不准那一档），原文要留在页文件里给人看。"""
    answer = intake_client.parse_semantics('{"semantics": "maybe-tick", "confidence": 0.4}')

    assert answer["parsed"] is True
    assert answer["semantics"] == "maybe-tick"


def test_a_bogus_confidence_is_recorded_as_none_never_gated():
    """置信度只记录、不设闸门（与 #4 的口径一致：判错不设闸门）。"""
    for raw in ['{"semantics": "tick", "confidence": "很高"}',
                '{"semantics": "tick", "confidence": 5}',
                '{"semantics": "tick", "confidence": true}']:
        answer = intake_client.parse_semantics(raw)
        assert answer["confidence"] is None, raw
        assert answer["semantics"] == "tick", raw


# ---------------------------------------------------------------- 真实现（假 transport）


def test_a_successful_call_returns_the_semantics_and_archives_the_run(cfg, tmp_path, page_png):
    transport = FakeTransport((200, OK_BODY))
    semantics = make_semantics(cfg, tmp_path / "runs", transport)

    answer = semantics(BLOCK, STATS, page_png)

    assert answer["semantics"] == "correction" and answer["parsed"] is True
    assert (answer["provider"], answer["model"]) == ("deepseek", "deepseek-flash")
    assert answer["run_id"].endswith(f"-{intake_client.INTAKE_TAG}.json")

    archived = json.loads((tmp_path / "runs" / answer["run_id"]).read_text(encoding="utf-8"))
    sent = transport.calls[0]["payload"]
    assert archived["payload"]["model"] == "deepseek-flash"
    assert archived["payload"]["messages"][-1]["content"][0] == sent["messages"][-1]["content"][0], \
        "留档里的提示词就是发出去的那一份（图另算）"
    assert "correction" in archived["response"]["choices"][0]["message"]["content"]


def test_the_archive_never_keeps_the_page_photo(cfg, tmp_path, page_png):
    """私人手写照片不进 `runs/`（口径同 proto/slice.py:157-163）。"""
    semantics = make_semantics(cfg, tmp_path / "runs", FakeTransport((200, OK_BODY)))

    answer = semantics(BLOCK, STATS, page_png)
    blob = (tmp_path / "runs" / answer["run_id"]).read_text(encoding="utf-8")

    assert "<image omitted>" in blob
    assert "base64" not in blob, "留档里不许留图（连数据 URL 的前缀都不许）"


def test_a_missing_key_fails_as_model_unavailable_without_touching_the_network(cfg, tmp_path, page_png):
    transport = FakeTransport()
    semantics = make_semantics(cfg, tmp_path / "runs", transport, env={})

    with pytest.raises(ModelUnavailable) as excinfo:
        semantics(BLOCK, STATS, page_png)

    assert "DEEPSEEK_API_KEY" in str(excinfo.value)
    assert KEY not in str(excinfo.value), "报错里不许出现密钥值"
    assert transport.calls == []


def test_a_network_error_is_retried_then_reported_as_model_unavailable(cfg, tmp_path, page_png):
    transport = FakeTransport(*[OSError("connection refused")] * 4)
    sleeps: list[float] = []
    semantics = intake_client.HttpSemantics(
        cfg, tmp_path / "runs", transport=transport, sleep=sleeps.append,
        env={"DEEPSEEK_API_KEY": KEY})

    with pytest.raises(ModelUnavailable) as excinfo:
        semantics(BLOCK, STATS, page_png)

    assert "connection refused" in str(excinfo.value)
    assert len(transport.calls) == 4 and len(sleeps) == 3
    assert not (tmp_path / "runs").exists() or not list((tmp_path / "runs").iterdir())


def test_an_unparseable_answer_still_archives_the_evidence(cfg, tmp_path, page_png):
    body = json.dumps({"choices": [{"message": {"content": "这点红笔是订正"}}]})
    semantics = make_semantics(cfg, tmp_path / "runs", FakeTransport((200, body)))

    answer = semantics(BLOCK, STATS, page_png)

    assert answer["parsed"] is False
    assert answer["reason"] == "这点红笔是订正"
    assert len(list((tmp_path / "runs").iterdir())) == 1, "答了话就要留证据（不许静默）"


def test_two_calls_in_the_same_millisecond_do_not_overwrite_each_other(cfg, tmp_path, page_png):
    frozen = datetime(2026, 10, 4, 19, 0, 0, 123000)
    semantics = make_semantics(cfg, tmp_path / "runs",
                               FakeTransport((200, OK_BODY), (200, OK_BODY)), clock=lambda: frozen)

    first, second = semantics(BLOCK, STATS, page_png), semantics(BLOCK, STATS, page_png)

    assert first["run_id"] != second["run_id"]
    assert len(list((tmp_path / "runs").iterdir())) == 2


def test_the_real_transport_really_sends_the_image(cfg, tmp_path, page_png):
    """真的走一遍默认 transport（stdlib urllib），上游换成本机一个假 endpoint。

    这是「接缝后面那一层」唯一没有假 transport 覆盖的地方：Authorization 头、
    多段 content、以及**图真的发出去了**。仍然不联网、不花钱（本机回环）。
    """
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    seen: dict = {}

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            seen["auth"] = self.headers.get("Authorization")
            seen["payload"] = json.loads(self.rfile.read(length))
            blob = OK_BODY.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(blob)))
            self.end_headers()
            self.wfile.write(blob)

        def log_message(self, *args):  # 别把测试的访问日志打到 stderr
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{httpd.server_address[1]}/v1"
        cfg = config.RoleConfig(role="extract", provider="deepseek", base_url=base,
                                model="deepseek-flash", key_env="DEEPSEEK_API_KEY")
        semantics = intake_client.HttpSemantics(cfg, tmp_path / "runs",
                                                env={"DEEPSEEK_API_KEY": KEY})

        answer = semantics(BLOCK, STATS, page_png)
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)

    assert seen["auth"] == f"Bearer {KEY}"
    parts = seen["payload"]["messages"][-1]["content"]
    data_url = parts[1]["image_url"]["url"]
    assert data_url.startswith("data:image/png;base64,")
    decoded = base64.b64decode(data_url.split(",", 1)[1])
    assert decoded == page_png.read_bytes(), "发的就是那一页的原字节"
    assert answer["semantics"] == "correction"
    assert (tmp_path / "runs" / answer["run_id"]).is_file()


def test_the_tag_is_stable_and_says_which_role_it_was(cfg, tmp_path, page_png):
    """留档的 tag 要能一眼认出是抽取角色（改提示词之后对账靠它）。"""
    assert intake_client.INTAKE_TAG == "redpen-semantics"
    assert re.fullmatch(r"[a-z0-9-]+", intake_client.INTAKE_TAG)
