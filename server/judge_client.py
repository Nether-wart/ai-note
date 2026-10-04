"""判定角色的调用接缝：问模型 → 拿回原文 → 留档（#5 验收第 3 条）。

**这一层只负责「把话问出去、把回答带回来」**，不做任何判定映射——映射在
`server/judge.py`（#4 的唯一实现）。也不写题卡——回写在 `server/attempt.py`。

三件事写死在这里：

  · **纯文本**：输入只有标准答案与这次作答，**不发送任何图片**（spec #1 US 38；
    等价性判断不需要看题）。提示词逐字继承 `proto/slice.py:325-333` 的
    `EQUIV_JUDGE_SYSTEM`（口径继承，代码不继承——`proto/` 是冻结的只读证据）。
  · **可注入/stub**：HTTP 走 `transport` 接缝，`sleep` 也可注入；
    测试不联网、不花钱（`server/tests/test_judge_client.py`）。
  · **失败不 `sys.exit`**：原型的失败路径是 `die()` = `sys.exit(2)`
    （`proto/slice.py:214`）——HTTP 处理器里用它会杀掉请求。这里抛
    `ModelUnavailable`，由端点映射成 502（编排裁决 D1），且**不留任何重做记录**。

留档写 `runs/<stamp>-judge.json`，内容 `{payload, response}`（口径同 `proto/slice.py:151-167`）。
`runs/` 已在 `.gitignore` 里，绝不进仓库。
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

from .config import JudgeConfig

JUDGE_TAG = "judge"
# 上游抖动：这些码重试，其余 4xx（比如模型名写错）立刻失败，重试没意义
RETRY_STATUSES = (429, 500, 502, 503, 504)
TIMEOUT = 180
RETRIES = 3
BACKOFF_SECONDS = 2
MAX_RESPONSE_CHARS = 500

# 逐字继承 proto/slice.py:325-333 的 EQUIV_JUDGE_SYSTEM。改这里必须重跑判定角色的考卷
# （spec #1「换任何模型之后都要重跑验收」，提示词改了同理）。
JUDGE_SYSTEM = """你要判断两个数学答案是否**等价**（数学上相同，写法可以不同）。

输出一个 JSON：{"equivalent": true, "confidence": 0.0, "reason": "一句话"}

只有数学上完全相同才算等价。举例：
- "2/√3" 与 "2√3/3" → 等价
- "x=2" 与 "x=±2" → 不等价
- "(1,2)" 与 "[1,2]" → 不等价
- "1/2" 与 "0.5" → 等价"""


class ModelUnavailable(Exception):
    """模型调用失败（网络／超时／缺密钥／上游 5xx）：**不是一次判定**。

    调用方必须把它变成「这一次重做不留下任何记录」的错误（502），让人可以直接重试。
    """


@dataclass(frozen=True)
class JudgeCall:
    """一次**成功**的模型调用。原文的解析与映射都不在这里。"""
    text: str
    provider: str
    model: str
    run_id: str | None = None
    usage: dict = field(default_factory=dict)


def judge_messages(standard_answer: str, answer: str) -> tuple[str, str]:
    """`(system, user)` 两段**纯文本**。口径继承 `proto/slice.py:767-771`。"""
    return JUDGE_SYSTEM, f'标准答案："{standard_answer}"\n待判答案："{answer}"\n两者是否等价？'


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


def _default_transport(url: str, headers: dict, payload: dict, timeout: float):
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


class HttpJudge:
    """判定角色的真实现。`transport` / `sleep` / `env` / `clock` 都是测试接缝。"""

    def __init__(self, config: JudgeConfig, runs_dir: Path | str, *, transport=None,
                 sleep=time.sleep, env=None, clock=None, timeout: float = TIMEOUT,
                 retries: int = RETRIES) -> None:
        self.config = config
        self.runs_dir = Path(runs_dir)
        self.transport = transport or _default_transport
        self.sleep = sleep
        self.env = env
        self.clock = clock or datetime.now
        self.timeout = timeout
        self.retries = retries

    # 让 `Api(judge=…)` 与真实现同一个调用形状
    def __call__(self, standard_answer: str, answer: str) -> JudgeCall:
        env = self.env if self.env is not None else os.environ
        key = (env.get(self.config.key_env) or "").strip()
        if not key:
            # 缺密钥是「我们没能问成」，不是「看不清」：不留记录，可以重试。
            raise ModelUnavailable(
                f"没有找到 {self.config.key_env}——把密钥写进 .env.local（已在 .gitignore 里）"
                "或导出成环境变量；这一次没有留下任何记录"
            )

        system, user = judge_messages(standard_answer, answer)
        payload = {
            "model": self.config.model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": 0.0,
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        url = f"{self.config.base_url}/chat/completions"

        last = ""
        text = ""
        for attempt in range(self.retries + 1):
            try:
                status, text = self.transport(url, headers, payload, self.timeout)
            except OSError as exc:
                last = f"{type(exc).__name__}: {exc}"
                if attempt < self.retries:
                    self.sleep(BACKOFF_SECONDS * (attempt + 1))
                    continue
                raise ModelUnavailable(
                    f"调用失败（{JUDGE_TAG}），重试 {self.retries} 次：{last}"
                    "——这一次没有留下任何记录，可以直接重试"
                ) from exc
            if status in RETRY_STATUSES and attempt < self.retries:
                last = f"HTTP {status}: {text[:300]}"
                self.sleep(BACKOFF_SECONDS * (attempt + 1))
                continue
            if status >= 400:
                raise ModelUnavailable(
                    f"HTTP {status}（{JUDGE_TAG}）：{text[:MAX_RESPONSE_CHARS]}"
                    "——这一次没有留下任何记录，可以直接重试"
                )
            break

        try:
            response = json.loads(text)
        except ValueError as exc:
            raise ModelUnavailable(
                f"上游返回的不是 JSON（{JUDGE_TAG}）：{exc}"
                "——这一次没有留下任何记录，可以直接重试"
            ) from exc
        if not isinstance(response, dict):
            raise ModelUnavailable(
                f"上游返回的 JSON 不是一个对象（{JUDGE_TAG}，拿到 {type(response).__name__}）"
                "——这一次没有留下任何记录，可以直接重试"
            )

        run_id = self._save_run(payload, response)
        content = _content_of(response)
        if content is None:
            # 留档先写：上游返回了什么必须留证据（不许静默）。但这次仍不算判定。
            raise ModelUnavailable(
                f"上游响应里没有判定内容（{JUDGE_TAG}）：{json.dumps(response, ensure_ascii=False)[:MAX_RESPONSE_CHARS]}"
                "——这一次没有留下任何记录，可以直接重试"
            )
        return JudgeCall(text=content, provider=self.config.provider, model=self.config.model,
                         run_id=run_id, usage=response.get("usage") or {})

    # ---------------------------------------------------------------- 留档

    def _save_run(self, payload: dict, response: dict) -> str:
        """写一份调用档，返回它的文件名（契约 §10.1 的 `run_id`）。"""
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        stamp = self.clock().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        name = f"{stamp}-{JUDGE_TAG}.json"
        # 同一毫秒里的第二次调用不许覆盖第一份留档（证据链不能被悄悄抹掉）
        collision = 2
        while (self.runs_dir / name).exists():
            name = f"{stamp}-{JUDGE_TAG}-{collision}.json"
            collision += 1
        slim = json.loads(json.dumps(payload, ensure_ascii=False))
        # 图片一律不入档（口径同 proto/slice.py:157-163）。判定是纯文本，这里只是把
        # 这条规矩留在代码里：将来谁把图加进提示词，留档也不会把图写出去。
        for message in slim.get("messages", []):
            content = message.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        part["image_url"] = {"url": "<image omitted>"}
        (self.runs_dir / name).write_text(
            json.dumps({"payload": slim, "response": response}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return name


def _content_of(response: dict) -> str | None:
    """从上游响应里取判定原文。少数实现会返回分片，拼起来。"""
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p.get("text", "") for p in content if isinstance(p, dict))
    return None
