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


def test_provider_outside_the_whitelist_is_a_load_time_error():
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
