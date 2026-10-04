"""与**角色无关**的模型调用底座：问出去、拿回来、留档、失败分类。

这一层是从 `server/judge_client.py`（#5 的判定角色）里抽出来的第二个使用者出现时
才抽的（#12 的抽取角色要对红笔痕迹判语义）。**抽的是四样与角色无关的东西**：

| 抽出来的 | 为什么与角色无关 |
|---|---|
| `default_transport` | 标准库 HTTP（`server/` 零第三方依赖），谁调都是这一个 POST 形状 |
| `extract_json` | 从自由文本里抠第一个完整 JSON，是所有角色共用的容错 |
| `ModelUnavailable` | 「我们没能问成」≠「一次回答」——这条界线对每个角色都成立 |
| `save_run` | `runs/<stamp>-<tag>.json` 留档（契约 §10.1），代价只有 tag 不同 |

**留在 `judge_client.py` 的**：`JUDGE_SYSTEM` 与 `judge_messages`（判定角色的提示词与
消息形状：纯文本、只发标准答案与作答、**一张图都不发**——spec #1 US 38 是判定角色
独有的约束）。同理，`server/intake_client.py` 的提示词与消息形状（**要看图的抽取角色**）
留在它自己那里。**提示词按角色分家，管道共用一条**——这是这一层的全部要点。

三件事写死在这里：

  · **失败不 `sys.exit`**：原型的失败路径是 `die()` = `sys.exit(2)`（`proto/slice.py:214`），
    HTTP 处理器里用它会杀掉请求。这里抛 `ModelUnavailable`，由调用方映射成 502
    （编排裁决 D1）。
  · **可注入/stub**：`transport` / `sleep` / `env` / `clock` 都是接缝，测试不联网、不花钱。
  · **图片不入档**：留档时把 `image_url` 换成 `<image omitted>`（口径同
    `proto/slice.py:157-163`）——将来任何角色往提示词里加图，证据链也不会把私人照片写进 `runs/`。
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# 上游抖动：这些码重试，其余 4xx（比如模型名写错）立刻失败，重试没意义
RETRY_STATUSES = (429, 500, 502, 503, 504)
TIMEOUT = 180
RETRIES = 3
BACKOFF_SECONDS = 2
MAX_RESPONSE_CHARS = 500


class ModelUnavailable(Exception):
    """模型调用失败（网络／超时／缺密钥／上游 5xx）：**不是一次回答**。

    调用方必须把它变成「这一次不留下任何记录」的错误（502），让人可以直接重试。
    「我们没能问成」与「问成了但答得不好」是两件事：后者是一次回答（可以落向
    保守的默认值并喊出来），前者什么都不该写。
    """


@dataclass(frozen=True)
class ModelCall:
    """一次**成功**的模型调用。原文的解析与映射都不在这里。"""
    text: str
    provider: str
    model: str
    run_id: str | None = None
    usage: dict = field(default_factory=dict)


def extract_json(text: str) -> object:
    """从模型的自由文本里抠出第一个完整的 JSON 对象。

    口径继承 `proto/slice.py:217-241`：剥 ```json 围栏、花括号配对、字符串里不数括号。
    """
    if not isinstance(text, str):
        raise ValueError(f"响应不是一个字符串：{type(text).__name__}")
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.S).strip()
    start = t.find("{")
    if start < 0:
        raise ValueError(f"响应里没有 JSON：{text[:300]}")
    depth, in_str, esc = 0, False, False
    for i in range(start, len(t)):
        ch = t[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(t[start : i + 1])
    raise ValueError(f"JSON 不完整：{text[:300]}")


def default_transport(url: str, headers: dict, payload: dict, timeout: float):
    """真的打一次 HTTP（标准库，`server/` 零第三方依赖）。返回 `(status, text)`。

    网络层异常原样抛 `OSError`（`URLError` 就是它的子类），由调用方决定重试。
    """
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def content_of(response: dict) -> str | None:
    """从上游响应里取回答原文。少数实现会返回分片，拼起来。"""
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return None


def save_run(runs_dir: Path | str, tag: str, payload: dict, response: dict,
             *, clock=None) -> str:
    """写一份调用档，返回它的文件名（契约 §10.1 的 `run_id`）。

    文件名 `runs/<YYYYmmdd-HHMMSS-mmm>-<tag>.json`，内容 `{payload, response}`
    （口径同 `proto/slice.py:151-167`）。`runs/` 在 `.gitignore` 里，绝不进仓库。
    同一毫秒里的第二次调用**不覆盖**第一份：证据链不能被悄悄抹掉。
    """
    runs_dir = Path(runs_dir)
    runs_dir.mkdir(parents=True, exist_ok=True)
    stamp = (clock or datetime.now)().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    name = f"{stamp}-{tag}.json"
    collision = 2
    while (runs_dir / name).exists():
        name = f"{stamp}-{tag}-{collision}.json"
        collision += 1
    slim = json.loads(json.dumps(payload, ensure_ascii=False))
    # 图片一律不入档：私人手写照片不该进 runs/ 的留档（口径同 proto/slice.py:157-163）。
    for message in slim.get("messages", []):
        content = message.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    part["image_url"] = {"url": "<image omitted>"}
    (runs_dir / name).write_text(
        json.dumps({"payload": slim, "response": response}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return name


def chat(config, messages, *, tag: str, runs_dir: Path | str,
         transport=None, sleep=time.sleep, env=None, clock=None,
         timeout: float = TIMEOUT, retries: int = RETRIES) -> ModelCall:
    """问一次上游：`config` 给角色身份，`messages` 给这一次的内容。

    `config` 只要有 `provider` / `base_url` / `model` / `key_env` 四个属性即可
    （`config.RoleConfig` 与它带阈值的子类 `JudgeConfig` 都满足）——这一层不认识
    「判定」「抽取」这些角色，也不认识阈值。

    返回的 `ModelCall.text` 是**原文**：解析与映射归各自的角色模块（`judge.py` /
    `intake_client.parse_semantics`），这里绝不替它们猜。
    """
    env = env if env is not None else os.environ
    key = (env.get(config.key_env) or "").strip()
    if not key:
        # 缺密钥是「我们没能问成」，不是「看不清」：不留记录，可以重试。
        raise ModelUnavailable(
            f"没有找到 {config.key_env}——把密钥写进 .env.local（已在 .gitignore 里）"
            "或导出成环境变量；这一次没有留下任何记录"
        )

    payload = {
        "model": config.model,
        "messages": list(messages),
        "temperature": 0.0,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    url = f"{config.base_url}/chat/completions"

    last = ""
    text = ""
    for attempt in range(retries + 1):
        try:
            status, text = (transport or default_transport)(url, headers, payload, timeout)
        except OSError as exc:
            last = f"{type(exc).__name__}: {exc}"
            if attempt < retries:
                sleep(BACKOFF_SECONDS * (attempt + 1))
                continue
            raise ModelUnavailable(
                f"调用失败（{tag}），重试 {retries} 次：{last}"
                "——这一次没有留下任何记录，可以直接重试"
            ) from exc
        if status in RETRY_STATUSES and attempt < retries:
            last = f"HTTP {status}: {text[:300]}"
            sleep(BACKOFF_SECONDS * (attempt + 1))
            continue
        if status >= 400:
            raise ModelUnavailable(
                f"HTTP {status}（{tag}）：{text[:MAX_RESPONSE_CHARS]}"
                "——这一次没有留下任何记录，可以直接重试"
            )
        break

    try:
        response = json.loads(text)
    except ValueError as exc:
        raise ModelUnavailable(
            f"上游返回的不是 JSON（{tag}）：{exc}"
            "——这一次没有留下任何记录，可以直接重试"
        ) from exc
    if not isinstance(response, dict):
        raise ModelUnavailable(
            f"上游返回的 JSON 不是一个对象（{tag}，拿到 {type(response).__name__}）"
            "——这一次没有留下任何记录，可以直接重试"
        )

    run_id = save_run(runs_dir, tag, payload, response, clock=clock)
    content = content_of(response)
    if content is None:
        # 留档先写：上游返回了什么必须留证据（不许静默）。但这次仍不算一次回答。
        raise ModelUnavailable(
            f"上游响应里没有回答内容（{tag}）："
            f"{json.dumps(response, ensure_ascii=False)[:MAX_RESPONSE_CHARS]}"
            "——这一次没有留下任何记录，可以直接重试"
        )
    return ModelCall(text=content, provider=config.provider, model=config.model,
                     run_id=run_id, usage=response.get("usage") or {})
