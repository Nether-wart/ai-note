"""配置装载的验收：**坏配置起不来**，不是每个请求里再验一遍。

阈值那一组照 #4 的加固口径：NaN／无穷／越界会静默绕开置信度闸门（`confidence < NaN`
恒为 False，0.0 的置信度也会判成「对」），所以它必须在装载处就喊。
"""

from __future__ import annotations

import pytest

from server import config, judge

BAD_THRESHOLDS = ["nan", "inf", "-1", "1.5", "abc", "1e400", "0.9.9"]


def test_defaults_come_from_the_role_table():
    cfg = config.load_judge_config({})

    assert cfg.role == "judge"
    assert cfg.provider == "deepseek"
    assert cfg.model == "deepseek-flash"
    assert cfg.base_url == config.PROVIDERS["deepseek"]["base_url"]
    assert cfg.key_env == "DEEPSEEK_API_KEY"
    assert cfg.threshold == judge.CONFIDENCE_THRESHOLD


def test_the_extract_role_resolves_through_the_same_one_loader():
    """角色解析只有一处（`load_role_config`）：抽取角色走它，判定角色也走它。

    抽取角色**没有阈值**——置信度闸门是判定角色独有的事（`server/judge.py`），
    给抽取角色配一个阈值等于发明一个没人用的旋钮。
    """
    extract = config.load_role_config(config.EXTRACT_ROLE, {})

    assert extract.role == "extract"
    assert (extract.provider, extract.model) == ("deepseek", "deepseek-flash")
    assert extract.key_env == "DEEPSEEK_API_KEY"
    assert not hasattr(extract, "threshold")

    judge_cfg = config.load_judge_config({})
    assert (judge_cfg.provider, judge_cfg.model) == (extract.provider, extract.model), \
        "两份配置的身份字段来自同一张角色表——不是两套默认值"


def test_the_extract_role_has_its_own_provider_and_model_env_vars():
    cfg = config.load_role_config("extract", {"EXTRACT_PROVIDER": "dashscope",
                                              "EXTRACT_MODEL": "qwen-vl-max"})

    assert (cfg.provider, cfg.model) == ("dashscope", "qwen-vl-max")
    assert cfg.key_env == "DASHSCOPE_API_KEY"


def test_an_unknown_role_and_an_undefined_provider_error_both_shout():
    with pytest.raises(ValueError) as excinfo:
        config.load_role_config("summarize", {})
    assert "summarize" in str(excinfo.value)
    assert "extract" in str(excinfo.value) and "judge" in str(excinfo.value)

    # 预设之外的名字不再被「白名单」拒绝，但**没定义完整**仍然是装载期错误（ADR 0010）。
    # 报错要同时点出用户设的那个变量与该定义的两个变量，否则人还得自己找另一半。
    with pytest.raises(ValueError) as excinfo:
        config.load_role_config("extract", {"EXTRACT_PROVIDER": "openai"})
    message = str(excinfo.value)
    assert "EXTRACT_PROVIDER" in message
    assert "openai" in message
    assert "AI_NOTE_PROVIDER_OPENAI_BASE_URL" in message
    assert "AI_NOTE_PROVIDER_OPENAI_API_KEY" in message


def test_undefined_provider_is_a_load_time_error():
    with pytest.raises(ValueError) as excinfo:
        config.load_judge_config({"JUDGE_PROVIDER": "openai"})

    assert "openai" in str(excinfo.value)
    assert "dashscope" in str(excinfo.value) and "deepseek" in str(excinfo.value)


def test_switching_provider_without_a_model_falls_back_to_that_provider_default():
    """只换 provider 不换模型时，落到**那家 provider 的** default_model。"""
    cfg = config.load_judge_config({"JUDGE_PROVIDER": "dashscope"})

    assert cfg.model == config.PROVIDERS["dashscope"]["default_model"]
    assert cfg.key_env == "DASHSCOPE_API_KEY"


def test_model_override_wins():
    cfg = config.load_judge_config({"JUDGE_PROVIDER": "dashscope", "JUDGE_MODEL": "qwen-max"})
    assert cfg.model == "qwen-max"


@pytest.mark.parametrize("raw", BAD_THRESHOLDS)
def test_a_bad_threshold_refuses_to_load(raw):
    with pytest.raises(ValueError):
        config.load_judge_config({"JUDGE_THRESHOLD": raw})


def test_a_good_threshold_is_validated_once_and_handed_back():
    assert config.load_judge_config({"JUDGE_THRESHOLD": "0.5"}).threshold == 0.5
    # 边界：0 与 1 都合法
    assert config.load_judge_config({"JUDGE_THRESHOLD": "0"}).threshold == 0.0
    assert config.load_judge_config({"JUDGE_THRESHOLD": "1"}).threshold == 1.0


def test_env_files_are_read_without_overwriting_real_env(tmp_path):
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        "# 注释\nDEEPSEEK_API_KEY=from-file\nJUDGE_THRESHOLD=0.7\n\nBROKEN LINE\n",
        encoding="utf-8",
    )
    env = {"DEEPSEEK_API_KEY": "from-real-env"}

    loaded = config.load_env_files([env_file, tmp_path / "missing.local"], env=env)

    assert loaded == [env_file]
    assert env["DEEPSEEK_API_KEY"] == "from-real-env", "已存在的环境变量不覆盖"
    assert env["JUDGE_THRESHOLD"] == "0.7"


def test_missing_key_is_not_a_load_time_error():
    """只读端点没有密钥也能服务；缺密钥是调用那一刻的 502，不是启动失败。"""
    cfg = config.load_judge_config({})
    assert cfg.key_env not in ("", None)


def test_a_bad_threshold_stops_the_service_from_booting(api_for, monkeypatch):
    """装载处校验一次：坏阈值**起不来**，而不是每个请求里再验一遍。"""
    monkeypatch.setenv("JUDGE_THRESHOLD", "nan")

    with pytest.raises(ValueError):
        api_for([])


def test_a_bad_provider_stops_the_cli_from_booting(tmp_path, monkeypatch):
    from conftest import make_data_dir
    from server.app import main

    monkeypatch.setenv("JUDGE_PROVIDER", "openai")

    assert main(["--data", str(make_data_dir(tmp_path, []))]) == 2


# ---------------------------------------------------------------- 自定义 provider（ADR 0010）


def test_自定义provider的端点与密钥从环境里取():
    env = {
        "AI_NOTE_PROVIDER_OPENAI_BASE_URL": "https://api.openai.com/v1",
        "AI_NOTE_PROVIDER_OPENAI_API_KEY": "sk-x",
        "AI_NOTE_PROVIDER_OPENAI_MODEL": "gpt-4o",
        "EXTRACT_PROVIDER": "openai",
    }
    cfg = config.load_role_config("extract", env)
    assert cfg.provider == "openai"
    assert cfg.base_url == "https://api.openai.com/v1"
    assert cfg.key_env == "AI_NOTE_PROVIDER_OPENAI_API_KEY"
    assert cfg.model == "gpt-4o"


def test_自定义provider的名字归一成环境变量名():
    env = {
        "AI_NOTE_PROVIDER_MY_PROXY_BASE_URL": "https://proxy.example/v1",
        "AI_NOTE_PROVIDER_MY_PROXY_API_KEY": "k",
        "JUDGE_PROVIDER": "my-proxy",
        "JUDGE_MODEL": "some-model",
    }
    # `my-proxy` → `AI_NOTE_PROVIDER_MY_PROXY_*`
    assert config.load_role_config("judge", env).base_url == "https://proxy.example/v1"


def test_自定义provider定义不全时装载就失败():
    # 只给键不给端点
    no_url = {"AI_NOTE_PROVIDER_OPENAI_API_KEY": "sk-x", "EXTRACT_PROVIDER": "openai"}
    with pytest.raises(ValueError, match="AI_NOTE_PROVIDER_OPENAI_BASE_URL"):
        config.load_role_config("extract", no_url)
    # 只给端点不给键（本机端点也要填占位值：留空与忘了填长得一样）
    no_key = {"AI_NOTE_PROVIDER_OPENAI_BASE_URL": "https://x/v1", "EXTRACT_PROVIDER": "openai"}
    with pytest.raises(ValueError, match="AI_NOTE_PROVIDER_OPENAI_API_KEY"):
        config.load_role_config("extract", no_key)


def test_自定义provider没给模型且没有默认模型时装载就失败():
    env = {
        "AI_NOTE_PROVIDER_OPENAI_BASE_URL": "https://x/v1",
        "AI_NOTE_PROVIDER_OPENAI_API_KEY": "k",
        "EXTRACT_PROVIDER": "openai",
    }
    with pytest.raises(ValueError, match="EXTRACT_MODEL"):
        config.load_role_config("extract", env)


def test_预设provider不受影响():
    cfg = config.load_role_config("judge", {})
    assert cfg.provider == config.ROLE_DEFAULTS["judge"]["provider"]
    assert cfg.base_url == config.PROVIDERS[cfg.provider]["base_url"]
