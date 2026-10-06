"""百度 OCR 形状的本地适配器（`server/ocr_baidu.py`）：协议、坏回执、配置。

判据全部走注入的 transport——**不联网、不依赖那台机器**。
"""

from __future__ import annotations

import base64
import json
from urllib import parse as urlparse

from server.ocr import UNAVAILABLE_ENGINE
from server.ocr_baidu import DEFAULT_BASE_URL, BaiduOcrEngine, engine_from_config


def reply(payload, status=200):
    def transport(url, blob, timeout):
        transport.url = url
        transport.blob = blob
        transport.timeout = timeout
        if isinstance(payload, bytes):
            return status, payload
        return status, json.dumps(payload, ensure_ascii=False).encode("utf-8")
    return transport


GOOD = {"words_result": [{"words": "6. 设 $x^2+ax+4$ 的一个条件是（ ）"},
                         {"words": "A. x=1   B. x=2"},
                         {"words": "   "}],
        "words_result_num": 3, "log_id": 123}


def test_好回执_解析成文本行_空行不算行():
    transport = reply(GOOD)
    result = BaiduOcrEngine("http://192.168.0.105:8866/", transport=transport)(b"\x89PNG", "p.png")

    assert result.available is True
    assert result.engine == "paddleocr-local"
    assert [line.text for line in result.lines] == ["6. 设 $x^2+ax+4$ 的一个条件是（ ）",
                                                    "A. x=1   B. x=2"]
    assert result.lines[0].box is None, "accurate_basic 不给框——如实记 None，不编"


def test_请求按百度的形状_表单里是图片字节的_base64():
    transport = reply(GOOD)
    blob = b"\x89PNG\r\n\x1a\nfake"
    BaiduOcrEngine(transport=transport)(blob, "p.png")

    assert transport.url == f"{DEFAULT_BASE_URL}/rest/2.0/ocr/v1/accurate_basic"
    form = urlparse.parse_qs(transport.blob.decode("ascii"))
    assert base64.b64decode(form["image"][0]) == blob, "送过去的是图片字节本身"


def test_带_token_时按百度的查询参数挂上去():
    transport = reply(GOOD)
    BaiduOcrEngine(token="abc", transport=transport)(b"x")
    assert "access_token=abc" in transport.url


def test_服务报_error_code_时明说_不冒充没有字():
    result = BaiduOcrEngine(transport=reply({"error_code": 110, "error_msg": "Access token invalid"}))(b"x")
    assert result.available is False
    assert result.engine == UNAVAILABLE_ENGINE
    assert "110" in result.warnings[0] and "invalid" in result.warnings[0]


def test_非_200_与回执坏掉都是不可用():
    assert BaiduOcrEngine(transport=reply(b'{"error":"boom"}', status=503))(b"x").available is False
    bad = BaiduOcrEngine(transport=reply(b"<html>502</html>"))(b"x")
    assert bad.available is False and "回执读不出来" in bad.warnings[0]


def test_连不上时明说连不上_并把地址带出来():
    def broken(url, blob, timeout):
        raise OSError("Connection refused")

    result = BaiduOcrEngine("http://192.168.0.105:8866", transport=broken)(b"x")
    assert result.available is False and "连不上" in result.warnings[0]


def test_数目对不上要说出来_服务说几条_我们拿到几条():
    result = BaiduOcrEngine(transport=reply({"words_result": [{"words": "只有一条"}],
                                             "words_result_num": 3}))(b"x")
    assert any("3" in warning and "1" in warning for warning in result.warnings)


def test_超时给得宽_第一趟要加载模型():
    transport = reply(GOOD)
    BaiduOcrEngine(transport=transport)(b"x")
    assert transport.timeout >= 60


def test_没配就是不可用_不去试连接():
    assert engine_from_config(None, env={}) is None
    assert engine_from_config(None, env={"AI_NOTE_OCR_URL": "http://x:1"}).base_url == "http://x:1"
    assert engine_from_config(None, env={"AI_NOTE_OCR_URL": "http://x:1",
                                         "AI_NOTE_OCR_PATH": "general"}).path == "general"


def test_设置文件里也能配(tmp_path):
    from server.catalog import Catalog
    from server.tests.conftest import make_data_dir

    root = make_data_dir(tmp_path, [])
    (root / "settings.json").write_text(json.dumps(
        {"roles": {}, "providers": {}, "ocr": {"base_url": "http://192.168.0.105:8866",
                                               "path": "general"}}, ensure_ascii=False),
        encoding="utf-8")
    engine = engine_from_config(Catalog(root), env={})
    assert engine is not None
    assert engine.base_url == "http://192.168.0.105:8866" and engine.path == "general"
