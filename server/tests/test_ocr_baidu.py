"""百度 OCR 形状的本地适配器（`server/ocr_baidu.py`）：token 流程、协议、坏回执、配置。

判据全部走注入的 transport——**不联网、不依赖那台机器**。
"""

from __future__ import annotations

import base64
import json
from urllib import parse as urlparse

from server.ocr import UNAVAILABLE_ENGINE
from server.ocr_baidu import DEFAULT_BASE_URL, BaiduOcrEngine, engine_from_config, png_size

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (640).to_bytes(4, "big") + (480).to_bytes(4, "big")


def reply(routes):
    """给人看的假传输：按 url 里的关键字分发，并记下每次调用。"""
    calls = []

    def transport(url, blob, timeout, headers=None):
        calls.append({"url": url, "blob": blob, "timeout": timeout, "headers": headers})
        for key, answer in routes.items():
            if key in url:
                return answer(url, blob)
        raise AssertionError(f"没有为这个 url 准备回执：{url}")

    transport.calls = calls
    return transport


def token_ok(word="tok-1", expires=2592000):
    def answer(url, blob):
        return 200, json.dumps({"access_token": word, "expires_in": expires}).encode()
    return answer


def words(items, *, counted=None):
    return json.dumps({"words_result": items,
                       "words_result_num": len(items) if counted is None else counted},
                      ensure_ascii=False).encode()


def test_拿_token_再_OCR_表单里是图片字节的_base64():
    def ocr_answer(url, blob, headers=None):
        form = urlparse.parse_qs(blob.decode("ascii"))
        assert base64.b64decode(form["image"][0]) == PNG, "送过去的是图片字节本身"
        return 200, words([{"words": "第一行"}, {"words": "第二行"}])

    transport = reply({"oauth/2.0/token": token_ok(), "accurate_basic": ocr_answer,
                       "general": ocr_answer})
    engine = BaiduOcrEngine(client_id="local_api_key", client_secret="local_secret_key",
                            transport=transport)
    result = engine(PNG, "p.png")

    assert result.available is True
    assert [line.text for line in result.lines] == ["第一行", "第二行"]
    assert "access_token=tok-1" in transport.calls[-1]["url"]
    assert any("general" in call["url"] for call in transport.calls), "默认走 general（带位置）"


def test_token_有缓存_两次调用只要一次():
    transport = reply({"oauth/2.0/token": token_ok(),
                       "general": lambda url, blob, headers=None: (200, words([{"words": "x"}]))})
    engine = BaiduOcrEngine(client_id="k", client_secret="s", transport=transport)
    engine(PNG)
    engine(PNG)
    token_calls = [call for call in transport.calls if "oauth" in call["url"]]
    assert len(token_calls) == 1, "30 天的 token 不该每个请求都要一遍"


def test_token_过期时遇_110_自动重取一次_不当成失败():
    seen = {"tokens": []}

    def ocr_answer(url, blob, headers=None):
        token = urlparse.parse_qs(urlparse.urlparse(url).query)["access_token"][0]
        seen["tokens"].append(token)
        if token == "tok-old":
            return 200, json.dumps({"error_code": 110,
                                    "error_msg": "Access token invalid or no longer valid"}).encode()
        return 200, words([{"words": "重取之后拿到了"}])

    transport = reply({"oauth/2.0/token": token_ok("tok-new"), "general": ocr_answer})
    engine = BaiduOcrEngine(client_id="k", client_secret="s", transport=transport)
    engine._cached_token, engine._expires_at = "tok-old", 9e18      # 假装缓存着一个过期的

    result = engine(PNG)
    assert result.available is True
    assert result.lines[0].text == "重取之后拿到了"
    assert seen["tokens"] == ["tok-old", "tok-new"], "该重取一次再打一遍"


def test_直接给_token_时不去要_token():
    transport = reply({"general": lambda url, blob, headers=None: (200, words([{"words": "x"}]))})
    engine = BaiduOcrEngine(token="literal", transport=transport)
    engine(PNG)
    assert all("oauth" not in call["url"] for call in transport.calls)


def test_general_的像素框按_PNG_尺寸归一化():
    items = [{"words": "半宽", "location": {"left": 320, "top": 240, "width": 64, "height": 48}}]
    transport = reply({"general": lambda url, blob, headers=None: (200, words(items))})
    result = BaiduOcrEngine(token="t", transport=transport)(PNG)
    assert result.lines[0].box == [0.5, 0.5, 0.1, 0.1]


def test_不是_PNG_就把框留_None_并说出来():
    items = [{"words": "x", "location": {"left": 10, "top": 10, "width": 5, "height": 5}}]
    transport = reply({"general": lambda url, blob, headers=None: (200, words(items))})
    result = BaiduOcrEngine(token="t", transport=transport)(b"\xff\xd8\xff jpeg")
    assert result.lines[0].box is None
    assert any("不是 PNG" in warning for warning in result.warnings)


def test_没有凭据时明说没有凭据_不冒充没有字():
    transport = reply({})
    result = BaiduOcrEngine(transport=transport)(PNG)
    assert result.available is False and result.engine == UNAVAILABLE_ENGINE
    assert "没有凭据" in result.warnings[0]


def test_服务报错_非_200_坏回执_连不上_都是不可用():
    transport = reply({"oauth/2.0/token": token_ok(),
                       "general": lambda url, blob, headers=None: (200, json.dumps(
                           {"error_code": 17, "error_msg": "Open api daily request limit reached"}).encode())})
    result = BaiduOcrEngine(client_id="k", client_secret="s", transport=transport)(PNG)
    assert result.available is False and "17" in result.warnings[0]

    down = BaiduOcrEngine(token="t", transport=lambda url, blob, timeout, headers=None: (503, b"boom"))
    assert down(PNG).available is False

    def broken(url, blob, timeout, headers=None):
        raise OSError("Connection refused")
    assert "连不上" in BaiduOcrEngine(token="t", transport=broken)(PNG).warnings[0]


def test_数目对不上要说出来():
    transport = reply({"general": lambda url, blob, headers=None: (200, words([{"words": "只有一条"}], counted=3))})
    result = BaiduOcrEngine(token="t", transport=transport)(PNG)
    assert any("3" in warning and "1" in warning for warning in result.warnings)


def test_png_size_只读头部_不是_PNG_就_None():
    assert png_size(PNG) == (640, 480)
    assert png_size(b"GIF89a" + b"\x00" * 40) is None
    assert png_size(b"") is None


def test_没配就是不可用_不去试连接_配了就按配置():
    assert engine_from_config(None, env={}) is None
    engine = engine_from_config(None, env={"AI_NOTE_OCR_URL": DEFAULT_BASE_URL,
                                           "AI_NOTE_OCR_CLIENT_ID": "local_api_key",
                                           "AI_NOTE_OCR_CLIENT_SECRET": "local_secret_key"})
    assert engine.base_url == DEFAULT_BASE_URL and engine.path == "general"
    assert engine.client_id == "local_api_key" and engine.token is None


def test_设置文件里也能配(tmp_path):
    from server.catalog import Catalog
    from server.tests.conftest import make_data_dir

    root = make_data_dir(tmp_path, [])
    (root / "settings.json").write_text(json.dumps(
        {"roles": {}, "providers": {},
         "ocr": {"base_url": "http://192.168.0.105:8866", "path": "accurate_basic",
                 "client_id": "k", "client_secret": "s"}}, ensure_ascii=False), encoding="utf-8")
    engine = engine_from_config(Catalog(root), env={})
    assert engine is not None and engine.path == "accurate_basic" and engine.client_id == "k"


def test_真百度的_token_错误是_oauth_形状_要读成人话():
    """实测：真 aip 端点用假凭据会回 `{"error":"invalid_client",
    "error_description":"unknown client id"}` ＋ **401**，不是 `error_code`。"""
    def oauth_error(url, blob, headers=None):
        return 401, json.dumps({"error": "invalid_client",
                                "error_description": "unknown client id"}).encode()

    result = BaiduOcrEngine(client_id="fake", client_secret="fake",
                            transport=oauth_error)(PNG)
    assert result.available is False
    assert "invalid_client" in result.warnings[0]
    assert "unknown client id" in result.warnings[0], "百度那句原话要带出来"


def test_bearer_模式_不取_token_改挂请求头():
    """千帆 v2 那一套：`Authorization: Bearer <密钥>`，没有 access_token 那一步。"""
    transport = reply({"general": lambda url, blob, headers=None: (200, words([{"words": "x"}]))})
    engine = BaiduOcrEngine(base_url="https://qianfan.baidubce.com", bearer="bce-v3/ALTAK-xxx",
                            transport=transport)
    result = engine(PNG)
    assert result.available is True
    assert all("oauth" not in call["url"] for call in transport.calls), "bearer 模式不该去要 token"
    assert transport.calls[-1]["headers"] == {"Authorization": "Bearer bce-v3/ALTAK-xxx"}
    assert "access_token" not in transport.calls[-1]["url"]


def test_可配的_token_路径_与_没凭据时的三种说法():
    transport = reply({"/custom/token": token_ok(), "general": lambda url, blob, headers=None:
                       (200, words([{"words": "x"}]))})
    engine = BaiduOcrEngine(client_id="k", client_secret="s", token_path="/custom/token",
                            transport=transport)
    assert engine(PNG).available is True
    assert any("/custom/token" in call["url"] for call in transport.calls)

    no_creds = BaiduOcrEngine(transport=reply({}))(PNG)
    assert "没有凭据" in no_creds.warnings[0]
