"""模型设置（契约 §10.6）：逐字段优先级、密钥不回显、写盘前校验、立刻生效。

这一份测的是**写入口那个接缝**，所以大多数用例走 `Api.handle` 而不是直接调模块——
"界面保存之后到底写了什么、报回去什么"才是要盯住的东西。

密钥那几条是这一节最要紧的：这类设置页最常见的静默数据丢失，就是
"加载一次、保存一次，密钥没了"。
"""

from __future__ import annotations

import json
import os
import stat

from server import settings as settings_module
from server.tests.conftest import get_json


def put_json(api, target: str, payload):
    """打一个 PUT，返回 `(status, 解析后的信封)`（与 `post_json` 同一个接缝）。"""
    response = api.handle("PUT", target, body=json.dumps(payload).encode("utf-8"),
                          content_type="application/json")
    return response.status, json.loads(response.body)


SECRET = "sk-verysecret-1234"


def _write_settings(root, payload):
    path = root / "settings.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


# ------------------------------------------------------------------ 读


def test_没有设置文件不是错误_三层里只用后两层(api_for):
    from server import settings as settings_module

    api = api_for()
    status, env = get_json(api, "/api/settings")
    assert status == 200
    assert env["data"]["exists"] is False
    assert env["data"]["file_ok"] is True
    # 四个角色都在，且每一项都标明来自哪一层
    assert set(env["data"]["roles"]) == {"extract", "segmenter", "judge", "brief"}
    # 这几条断言必须**与环境无关**：开发机上本来就有 DEEPSEEK_API_KEY，
    # 走 HTTP 那条路会把宿主环境读进来（那是对的），断言就会时红时绿。
    clean, warnings = settings_module.public_view(api.catalog, env={})
    assert warnings == []
    assert clean["roles"]["judge"]["provider"]["source"] == "default"
    assert clean["roles"]["judge"]["key"]["set"] is False


def test_逐字段回落_设置文件赢过环境变量_而没设过的字段仍看环境变量(api_for, monkeypatch):
    root = api_for().catalog.root
    _write_settings(root, {
        "roles": {"judge": {"provider": "openai"}},
        "providers": {"openai": {"base_url": "https://api.openai.com/v1", "api_key": SECRET}},
    })
    monkeypatch.setenv("JUDGE_MODEL", "env-model")
    monkeypatch.setenv("EXTRACT_MODEL", "env-extract-model")
    api = api_for(root=root)
    _status, env = get_json(api, "/api/settings")

    judge = env["data"]["roles"]["judge"]
    assert judge["provider"] == {"value": "openai", "source": "settings"}
    # 设置文件里**没设过** model → 环境变量接管（这正是"逐字段"的意义）
    assert judge["model"] == {"value": "env-model", "source": "env"}
    extract = env["data"]["roles"]["extract"]
    assert extract["model"]["value"] == "env-extract-model"


# ------------------------------------------------------------------ 写：密钥


def test_密钥永不回显_只给末四位(api_for):
    api = api_for()
    status, env = put_json(api, "/api/settings", {
        "roles": {"judge": {"provider": "openai", "model": "gpt-4o"}},
        "providers": {"openai": {"base_url": "https://api.openai.com/v1", "api_key": SECRET}},
    })
    assert status == 200
    body = json.dumps(env, ensure_ascii=False)
    assert SECRET not in body, "整把密钥不许进任何响应"
    assert env["data"]["roles"]["judge"]["key"] == {
        "set": True, "hint": "…1234", "source": "settings"}

    # 再 GET 一次也一样
    _status, again = get_json(api, "/api/settings")
    assert SECRET not in json.dumps(again, ensure_ascii=False)


def test_保存时省略或空串的密钥等于不改动(api_for):
    """这类页面最常见的静默数据丢失：加载一次再保存一次，密钥没了。"""
    api = api_for()
    put_json(api, "/api/settings", {
        "roles": {"judge": {"provider": "openai", "model": "gpt-4o"}},
        "providers": {"openai": {"base_url": "https://api.openai.com/v1", "api_key": SECRET}},
    })

    # 第二次保存：**整个 providers 都没带 api_key**（界面就是这样回传的）
    put_json(api, "/api/settings", {
        "roles": {"judge": {"provider": "openai", "model": "gpt-4o"}},
        "providers": {"openai": {"base_url": "https://api.openai.com/v1"}},
    })
    _status, env = get_json(api, "/api/settings")
    assert env["data"]["roles"]["judge"]["key"]["set"] is True, "省略 api_key 不许把密钥抹掉"

    # 空串也一样
    put_json(api, "/api/settings", {
        "roles": {"judge": {"provider": "openai", "model": "gpt-4o"}},
        "providers": {"openai": {"base_url": "https://api.openai.com/v1", "api_key": ""}},
    })
    _status, env = get_json(api, "/api/settings")
    assert env["data"]["roles"]["judge"]["key"]["set"] is True, "空串也只表示不改动"


def test_显式null才是清掉密钥(api_for):
    api = api_for()
    put_json(api, "/api/settings", {
        "roles": {"judge": {"provider": "openai"}},
        "providers": {"openai": {"base_url": "https://api.openai.com/v1", "api_key": SECRET}},
    })
    put_json(api, "/api/settings", {
        "roles": {"judge": {"provider": "openai", "model": "gpt-4o"}},
        "providers": {"openai": {"base_url": "https://api.openai.com/v1", "api_key": None}},
    })
    from server import settings as settings_module
    clean, _warnings = settings_module.public_view(api.catalog, env={})
    assert clean["roles"]["judge"]["key"]["set"] is False


# ------------------------------------------------------------------ 写：校验


def test_装不起来的设置_一个字节都不写(api_for):
    api = api_for()
    path = api.catalog.root / "settings.json"
    put_json(api, "/api/settings", {
        "roles": {"judge": {"provider": "openai", "model": "gpt-4o"}},
        "providers": {"openai": {"base_url": "https://api.openai.com/v1", "api_key": SECRET}},
    })
    before = path.read_bytes()

    # 自定义 provider 缺 base_url → 装不起来（"坏配置起不来"在写入口也要成立）
    status, env = put_json(api, "/api/settings", {
        "roles": {"judge": {"provider": "openai"}},
        "providers": {"openai": {"api_key": "k"}},
    })
    assert status == 400
    assert env["error"]["code"] == "bad_request"
    assert env["error"]["reason"] == "settings_invalid"
    assert path.read_bytes() == before, "拒绝时盘上那份必须原封不动"


def test_认不出的字段明确拒绝(api_for):
    api = api_for()
    status, env = put_json(api, "/api/settings", {"roles": {"nope": {}}})
    assert status == 400
    assert env["error"]["reason"] == "settings_unknown_field"
    assert "nope" in env["error"]["message"]

    status, env = put_json(api, "/api/settings", {"top_level_nope": 1})
    assert status == 400
    assert env["error"]["reason"] == "settings_unknown_field"


def test_坏文件明说_且不拿环境变量悄悄兜底(api_for):
    root = api_for().catalog.root
    (root / "settings.json").write_text("{ 这不是 JSON", encoding="utf-8")
    api = api_for(root=root)
    status, env = get_json(api, "/api/settings")
    assert status == 200, "读不出来也要能画出页面，好告诉人哪里坏了"
    assert env["data"]["file_ok"] is False
    assert [w["code"] for w in env["warnings"]] == ["settings_malformed"]
    assert env["warnings"][0]["level"] == "warning"


# ------------------------------------------------------------------ 立刻生效


def test_保存之后不用重启就生效(api_for):
    api = api_for()
    before = api.judge_config
    put_json(api, "/api/settings", {
        "roles": {"judge": {"provider": "openai", "model": "gpt-4o"}},
        "providers": {"openai": {"base_url": "https://api.openai.com/v1", "api_key": SECRET}},
    })
    assert api.judge_config is not before, "保存要重建模型配置"
    assert api.judge_config.provider == "openai"
    assert api.judge_config.model == "gpt-4o"
    assert api.judge_config.api_key == SECRET
    # judge 角色那几份读数也跟着换了（判定记在卡上的就是这几个值）
    assert api.attempts.provider == "openai"
    assert api.attempts.model == "gpt-4o"


def test_role_config每次调用都重读文件(api_for):
    """`role_config` 是"立刻生效"的另一半：手改了文件（或别的进程改的）也认。"""
    root = api_for().catalog.root
    catalog = api_for(root=root).catalog
    _write_settings(root, {
        "roles": {"judge": {"provider": "openai", "model": "one"}},
        "providers": {"openai": {"base_url": "https://api.openai.com/v1", "api_key": SECRET}},
    })
    assert settings_module.role_config("judge", catalog, env={}).model == "one"

    _write_settings(root, {
        "roles": {"judge": {"provider": "openai", "model": "two"}},
        "providers": {"openai": {"base_url": "https://api.openai.com/v1", "api_key": SECRET}},
    })
    assert settings_module.role_config("judge", catalog, env={}).model == "two"


def test_写出来的设置文件只有本人可读(api_for):
    api = api_for()
    put_json(api, "/api/settings", {
        "roles": {"judge": {"provider": "openai", "model": "gpt-4o"}},
        "providers": {"openai": {"base_url": "https://api.openai.com/v1", "api_key": SECRET}},
    })
    path = api.catalog.root / "settings.json"
    mode = stat.S_IMODE(os.stat(path).st_mode)
    assert mode == 0o600, f"设置文件里有密钥，权限要是 0600，实际是 {oct(mode)}"


def test_坏设置文件在每一个响应里现身(api_for):
    """回落可以，**静默**不行：文件坏着的时候每个响应都要说清现在的配置不是它给的。"""
    root = api_for().catalog.root
    (root / "settings.json").write_text("{ 坏", encoding="utf-8")
    api = api_for(root=root)
    for target in ("/api/index", "/api/settings"):
        status, env = get_json(api, target)
        assert status == 200, target
        codes = [w["code"] for w in env["warnings"]]
        assert "settings_malformed" in codes, f"{target} 没带上那条原因：{codes}"
        # §2 的四键形状：不许自创第五个键
        warning = next(w for w in env["warnings"] if w["code"] == "settings_malformed")
        assert set(warning) == {"code", "message", "id", "level"}
        assert warning["id"] is None
