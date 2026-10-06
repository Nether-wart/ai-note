"""模型端点的**自检**（`server/probe.py`）：一次最小调用，回答"这条线现在通不通"。

判据全部走注入的 transport——**不联网、不花钱**（与项目里所有模型调用同一条纪律）。
这一份同时钉住一件容易被混淆的事：自检**不是**上岗验收（test_acceptance_models.py 那份才是）。
"""

from __future__ import annotations

import json

from server import config, probe


def ok_transport(reply="好"):
    def transport(url, headers, payload, timeout):
        assert url.endswith("/chat/completions"), url
        assert headers["Authorization"].startswith("Bearer "), headers
        return 200, json.dumps({
            "choices": [{"message": {"content": reply}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 1},
        }, ensure_ascii=False)
    return transport


def test_端点通了_读数里有是谁在回答_以及延迟(tmp_path):
    env = {"DEEPSEEK_API_KEY": "sk-x"}
    readout = probe.probe("judge", env=env, transport=ok_transport(), runs_dir=tmp_path)

    assert readout["ok"] is True, readout
    assert readout["provider"] == config.ROLE_DEFAULTS["judge"]["provider"]
    assert readout["model"], "要能看见实际用的是哪个模型"
    assert readout["base_url"].startswith("https://")
    assert readout["reply"] == "好"
    assert isinstance(readout["latency_ms"], int)
    assert readout["error"] is None


def test_自检照常留一条_run_所以什么时候探过查得到(tmp_path):
    probe.probe("judge", env={"DEEPSEEK_API_KEY": "sk-x"}, transport=ok_transport(),
                runs_dir=tmp_path)
    runs = list(tmp_path.glob("*.json"))
    assert len(runs) == 1, runs
    saved = json.loads(runs[0].read_text(encoding="utf-8"))
    assert saved["payload"]["model"], "留档里有模型名"


def test_缺密钥时说清该往哪填_而且不是一句含糊的失败(tmp_path):
    readout = probe.probe("judge", env={}, transport=ok_transport(), runs_dir=tmp_path)

    assert readout["ok"] is False
    assert readout["error"]["code"] == "model_unavailable"
    assert "密钥" in readout["error"]["message"]
    assert readout["run_id"] is None


def test_上游拒绝时把它的原话带出来(tmp_path):
    def refusing(url, headers, payload, timeout):
        return 404, json.dumps({"error": {"message": "Model is unavailable"}})

    readout = probe.probe("judge", env={"DEEPSEEK_API_KEY": "sk-x"},
                          transport=refusing, runs_dir=tmp_path)
    assert readout["ok"] is False
    assert readout["error"]["code"] == "model_unavailable"


def test_自检用的是与真调用同一条解析_设置文件那层也算(tmp_path):
    settings = {"roles": {"judge": {"provider": "openai", "model": "gpt-4o"}},
                "providers": {"openai": {"base_url": "https://api.openai.com/v1",
                                         "api_key": "sk-from-settings"}}}
    readout = probe.probe("judge", env={}, transport=ok_transport(),
                          runs_dir=tmp_path, settings=settings)
    assert readout["ok"] is True
    assert readout["provider"] == "openai"
    assert readout["model"] == "gpt-4o"
    assert readout["key_env"] is None, "密钥来自设置文件时不该再让人去填环境变量"


def test_装不起来的配置_自检也要给出那句原话(tmp_path):
    readout = probe.probe("judge", env={"JUDGE_PROVIDER": "openai"},
                          transport=ok_transport(), runs_dir=tmp_path)
    assert readout["ok"] is False
    assert "openai" in readout["error"]["message"]


def test_角色不认识时退出码是_2():
    assert probe.main(["nope"]) == 2
    assert probe.main([]) == 2


def test_通了退出码_0_没通退出码_1(capsys):
    """两条都**注入 transport 与 env**：CLI 的判据不许依赖宿主环境，
    更不许真的打网络（那会花钱，而且宿主上恰好有密钥时结果就不确定了）。"""
    env = {"DEEPSEEK_API_KEY": "sk-x"}

    def refusing(url, headers, payload, timeout):
        return 401, json.dumps({"error": {"message": "invalid api key"}})

    assert probe.main(["judge"], transport=ok_transport(), env=env) == 0
    assert probe.main(["judge"], transport=refusing, env=env) == 1
