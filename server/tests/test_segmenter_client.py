"""切分角色的调用接缝（#17 §8）：**不联网、不花钱**地测一遍。

这一层做错两件事才致命，两件都在这里钉住：

1. **提示词不许在别处重写**：system 必须是 `segmentation.SEGMENT_SYSTEM` 那一份。
   提示词与解析它的那份代码**必须一起改**——重写一份，就会让「模型按新形状答、
   解析器按旧形状读」，而那看起来像模型突然变笨了。
2. **它发的是整页照片**（与判定角色相反、与抽取角色同类），并且返回的是**原文**
   ——解析归 `parse_candidate_blocks` 一处，这一层不解析。

切分最坏的失败是「纯文本模型收到图片不报错、照着提示词**凭空编块**」，那由**验收**
（视觉探针）去拦；这一层只管把图真的发出去。第三种失败是**调用失败**：必须原样抛
`ModelUnavailable`，让调用方报 502 并且**一个字节都不写**。
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from server import config
from server.model_client import ModelUnavailable
from server.segmentation import SEGMENT_SYSTEM
from server.segmenter_client import SEGMENT_TAG, HttpSegmenter, segment_messages

NOW = datetime(2026, 10, 4, 9, 39, 57, tzinfo=timezone.utc)

class FakeTransport:
    """把请求记下来、回一个假的模型响应。**一次网络调用都没有。**"""

    def __init__(self, reply, status: int = 200):
        self.reply = reply
        self.status = status
        self.calls: list[dict] = []

    def __call__(self, url, headers, payload, timeout):
        self.calls.append({"url": url, "headers": headers, "payload": payload})
        body = self.reply if isinstance(self.reply, str) \
            else json.dumps(self.reply, ensure_ascii=False)
        return self.status, body


def _photo(tmp_path):
    """一张**真能解码**的小 PNG。

    不能用 conftest 的 `PNG_1X1`：那个夹具够小够假，`ink.read_png` 解不开（IDAT 校验不过），
    而这一层要经 `image_data_url` 真去读它——**夹具解码不了**会把「接缝对不对」测成
    「夹具对不对」。
    """
    from server import ink

    path = tmp_path / "page.png"
    path.write_bytes(ink.encode_png(ink.InkImage(4, 4, [(255, 255, 255)] * 16)))
    return path


def _client(tmp_path, transport):
    return HttpSegmenter(
        config.load_role_config(config.SEGMENTER_ROLE, {}), tmp_path / "runs",
        transport=transport, sleep=lambda _s: None, env={"DEEPSEEK_API_KEY": "test-key"},
        clock=lambda: NOW,
    )


def test_the_segmenter_sends_the_whole_photo_with_the_one_prompt(tmp_path):
    answer = json.dumps({"blocks": [{"question_no": 17, "bbox_norm": [0.06, 0.08, 0.88, 0.14]}]},
                        ensure_ascii=False)
    transport = FakeTransport({"choices": [{"message": {"content": answer}}]})

    text = _client(tmp_path, transport)(_photo(tmp_path))

    # ① 返回的是**原文**：这一层不解析（解析只有一处实现）
    assert json.loads(text)["blocks"][0]["question_no"] == 17

    messages = transport.calls[0]["payload"]["messages"]
    # ② 提示词只有那**一份**：抄一遍就会与解析它的代码分家
    assert messages[0]["content"] == SEGMENT_SYSTEM
    # ③ 图真的发出去了，而且是 data URL（不是路径、不是空）
    image = messages[1]["content"][1]
    assert image["type"] == "image_url"
    assert image["image_url"]["url"].startswith("data:image/png;base64,")
    # ④ 形状提示与铁律在同一段文本里——模型要照着它给 JSON
    assert "blocks" in messages[1]["content"][0]["text"]

    # ⑤ 留档写了（口径同 intake／judge：**图片不入档**）
    runs = sorted((tmp_path / "runs").glob(f"*-{SEGMENT_TAG}.json"))
    assert len(runs) == 1
    assert "base64" not in runs[0].read_text("utf-8")


def test_a_failed_call_is_model_unavailable_not_an_empty_page(tmp_path):
    """调用失败原样抛 `ModelUnavailable`：调用方据此报 **502**、页文件一个字节都不写。

    **不许**把它降级成「这一页没有题」（`blocks: []`）——那是拿静默丢题换一个好看的响应，
    而这个项目最怕的失败正是它。
    """
    transport = FakeTransport("upstream exploded", status=500)

    with pytest.raises(ModelUnavailable):
        _client(tmp_path, transport)(_photo(tmp_path))


def test_the_messages_helper_is_the_single_shape(tmp_path):
    """`segment_messages` 是这一层唯一的消息形状——别处不许再拼一份。"""
    messages = segment_messages(image_url="data:image/png;base64,AAAA")

    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[0]["content"] == SEGMENT_SYSTEM
    assert messages[1]["content"][0]["type"] == "text"
    assert messages[1]["content"][1]["image_url"]["url"].endswith("AAAA")
