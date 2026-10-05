"""配置装载：**坏配置起不来**，而不是每个请求里再验一遍（#5 派发简报第 7 条）。

三件事在这里定：

  · **provider 白名单**（`dashscope` / `deepseek`，都在境内，见 `.env.local.example`）。
    白名单外的 provider 是坏配置 → 装载时 `ValueError`，服务拒绝启动。
  · **角色表**：`extract`（切分与红笔语义）、`judge`（等价比对）与 `brief`（简报）
    各自解析成一份 `RoleConfig`；解析逻辑只有 `load_role_config` 一处，
    别的角色一律走它。
  · **判定阈值**校验一次（复用 `server.judge.validate_threshold`，它是公开的）：
    NaN／无穷／越界会静默绕开置信度闸门，正是这套设计唯一禁止的错。
    「校验过了」与「拿去比的值」必须是同一个东西，所以装载返回的就是校验后的 float。

密钥**不在这里**要求：只读端点没有密钥也能服务，缺密钥是调用那一刻的事
（→ 502 `model_unavailable`，可以重试），不是启动失败。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from . import judge

# provider 白名单。防的不是别人，是自己手滑：一张手写照片的信息一旦出境就收不回来
# （口径继承 proto/slice.py:52-63）。
PROVIDERS = {
    "dashscope": {
        "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "key_env": "DASHSCOPE_API_KEY",
        "default_model": "qwen-vl-max",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "key_env": "DEEPSEEK_API_KEY",
        "default_model": "deepseek-v4-flash-vision-exp",
    },
}

# 角色默认。judge 角色只做**纯文本**等价比对，用正式版即可（proto/slice.py:68-71）；
# extract 角色要看图与模型对账（切分 #10、红笔语义 #12），换模型必须重跑它的考卷。
# brief 角色（#17 §7）默认**沿用抽取角色的默认值**，但它仍是一个**独立**角色：
# 换个模型时两个用途不许互相绑死（CONTEXT「模型角色」），所以是分开的一项配置。
# segmenter 角色（#17 §8）**要看图**（整页照片 → 候选块），与 extract 同样是正式版；
# 它与 extract 也是分开的两项：切分切坏了要单独换、红笔语义读错了也要单独换。
ROLE_DEFAULTS = {
    "extract": {"provider": "deepseek", "model": "deepseek-flash"},
    "judge": {"provider": "deepseek", "model": "deepseek-flash"},
    "brief": {"provider": "deepseek", "model": "deepseek-flash"},
    "segmenter": {"provider": "deepseek", "model": "deepseek-flash"},
}

JUDGE_ROLE = "judge"
EXTRACT_ROLE = "extract"
BRIEF_ROLE = "brief"
SEGMENTER_ROLE = "segmenter"


@dataclass(frozen=True)
class RoleConfig:
    """一个模型角色的**身份**：谁、哪家、哪个地址、哪把钥匙。

    阈值不在里面——置信度闸门是**判定角色**独有的事（`server/judge.py`），
    抽取角色（看红笔痕迹判语义、切分）没有阈值可设。
    """
    role: str
    provider: str
    base_url: str
    model: str
    key_env: str


@dataclass(frozen=True)
class JudgeConfig(RoleConfig):
    """判定角色的配置 = 身份 + 置信度阈值（阈值只作用于「对」，见 `server/judge.py`）。"""
    threshold: float = judge.CONFIDENCE_THRESHOLD


def load_role_config(role: str, env: Mapping | None = None) -> RoleConfig:
    """把**任意**角色名解析成一份配置，坏配置当场喊（`ValueError`）。

    环境变量按角色加前缀：`JUDGE_PROVIDER` / `JUDGE_MODEL`、`EXTRACT_PROVIDER` /
    `EXTRACT_MODEL`（口径继承 proto，那张角色表在 `proto/slice.py:52-96`）。
    """
    env = env if env is not None else os.environ
    if role not in ROLE_DEFAULTS:
        raise ValueError(
            f"没有这个模型角色：{role!r}；可选：{', '.join(sorted(ROLE_DEFAULTS))}"
        )
    default = ROLE_DEFAULTS[role]
    prefix = role.upper()
    provider = (env.get(f"{prefix}_PROVIDER") or default["provider"]).strip()
    if provider not in PROVIDERS:
        raise ValueError(
            f"{prefix}_PROVIDER 指向的 provider 不在白名单里：{provider!r}；"
            f"可选：{', '.join(PROVIDERS)}"
        )
    preset = PROVIDERS[provider]
    # 只换了 provider 而没指定模型时，落到那家 provider 的预设默认，而不是角色原来的模型
    fallback = default["model"] if provider == default["provider"] else preset["default_model"]
    model = (env.get(f"{prefix}_MODEL") or "").strip() or fallback
    return RoleConfig(
        role=role,
        provider=provider,
        base_url=preset["base_url"],
        model=model,
        key_env=preset["key_env"],
    )


def load_judge_config(env: Mapping | None = None) -> JudgeConfig:
    """把 judge 角色解析成一份配置（身份走上面那一份解析，外加一次阈值校验）。"""
    env = env if env is not None else os.environ
    base = load_role_config(JUDGE_ROLE, env)
    return JudgeConfig(
        role=base.role,
        provider=base.provider,
        base_url=base.base_url,
        model=base.model,
        key_env=base.key_env,
        threshold=_load_threshold(env),
    )


def _load_threshold(env: Mapping) -> float:
    raw = (env.get("JUDGE_THRESHOLD") or "").strip()
    if not raw:
        return judge.validate_threshold(judge.CONFIDENCE_THRESHOLD)
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(
            f"JUDGE_THRESHOLD 不是数值：{raw!r}——它必须是 [0,1] 里的一个数"
        ) from exc
    # 校验一次，装载返回的就是拿去比的那个值
    return judge.validate_threshold(value)


def load_env_files(paths, env=None) -> list[Path]:
    """把 `.env.local` 里的键值灌进环境（**已存在的环境变量不覆盖**）。

    口径继承 proto/slice.py:117-144：只认 `KEY=VALUE`，忽略空行与注释；
    只回报用了哪个文件，**绝不打印值**。
    """
    env = env if env is not None else os.environ
    loaded: list[Path] = []
    for path in (Path(p) for p in paths):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env.setdefault(key.strip(), value.strip().strip('"').strip("'"))
        loaded.append(path)
    return loaded
