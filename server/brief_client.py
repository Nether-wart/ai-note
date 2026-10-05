"""**简报角色**的调用接缝：按索引读数写一段给人读的中文总结（契约 §10.5）。

它与另外三个角色**各自独立**（#17 §7：**不复用抽取角色**——复用会让换模型时两个用途
互相绑死）：提示词与消息形状只在这里，HTTP／JSON 抽取／`ModelUnavailable`／`runs/` 留档
四样照旧走 `server/model_client.py` 那条与角色无关的底座，留档 tag `brief`。

**纯文本角色，一张图都不发**（与切分／抽取角色相反）：输入是 `brief.brief_digest` 算好的
**候选读数**（都是索引里现成的数字，连 `path` 都一并给出），输出是一段话 + 它用到的数字。

**闸门不在这里**：核对是 `server/brief.py` 的 `verify_facts`（纯函数，一处实现）。
这一层只负责把 JSON 抠出来；抠不出来是**一次回答**（`parsed: False` + 原话），
最终由 `brief.generate` 落成 502 `brief_unverifiable`——**不是** `ModelUnavailable`。
「模型没问成」（可以重试）与「答了话但数字是编的」（重试无用）是两件事，不许混
（理由见 `errors.brief_unverifiable`）。

改这段提示词必须重跑简报角色的验收（`CONTEXT.md`：换模型就要重跑验收，不过考不许上岗）。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .model_client import (
    RETRIES,
    TIMEOUT,
    ModelCall,
    default_transport,
    extract_json,
    save_run,
)
from .model_client import chat as model_chat

BRIEF_TAG = "brief"

# ⚠ 改这段提示词就必须重跑简报角色的验收（`CONTEXT.md`「验收」）。
# 它唯一要保证的事情是：正文里的每一个数字都能在候选读数里逐字找回，且未归类那句在场。
# 「这段总结说得好不好」不归它管——那由人读。
BRIEF_SYSTEM = """你在为一个错题本写某个科目的**简报**：一段简短的中文总结，说清这个科目近期的状态（例如错因分布、最薄弱的考点）。读者是这本错题本的主人。

铁律：

1. 只输出一个 JSON 对象，不要解释，不要 markdown 代码块。
2. 形状：{"text": "……", "window_facts": [...], "history_facts": [...]}。
3. `text` 是**给人读的那段话**：简短、直说。不要复述这张提示词，不要加标题。
4. **正文里的每一个数字都必须来自候选读数**，并把用到的数字原样抄进对应的数组：
   - `window_facts`：最近 window_days 天那一段用到的数字（候选在 window_facts 里）；
   - `history_facts`：全部历史那一段用到的数字（候选在 history_facts 里）。
   每一条写成 {"label": "这个数字是说什么的", "path": "候选里那条 path，逐字抄", "value": 候选里的那个值}。
5. **不许编数字，也不许自己算**：「平均」「占比」「比上周多了」这类算出来的数，候选里没有就不许写进正文。
6. 正文**必须**有一句「另有 N 道未归类未计入」（N 取候选里 stats.unclassified 那条的值），并把这一条**放进 `history_facts`**。
   未归类的题不属于任何科目；漏掉它，就是把一堆题安静地排除在外。
7. 候选里没有的东西可以**定性**地说，但不许给它配一个数字。"""


def brief_messages(subject: str, digest: dict) -> list[dict]:
    """`[system, user]` 两条**纯文本**消息（判定角色那样，不是抽取／切分角色那样）。

    user 里把候选读数整份给出去：path 与 value 都摆在同一行，模型要做的只是**抄**。
    """
    user = (
        f"科目：{subject}\n"
        f"窗口：最近 {digest.get('window_days')} 天"
        f"（{digest.get('window_from')} 至 {digest.get('window_until')}）\n\n"
        "候选读数（path 与 value 一律逐字抄；这些数字的出处是本次索引）：\n"
        + json.dumps(digest, ensure_ascii=False, indent=2)
    )
    return [
        {"role": "system", "content": BRIEF_SYSTEM},
        {"role": "user", "content": user},
    ]


def parse_brief(text) -> dict:
    """模型原文 → `{parsed, text, window_facts, history_facts, reason}`。**从不抛异常**。

    抠不出 JSON 是「答了话但答不出一份简报」，**不是**调用失败。`parsed=False` 时
    `reason` 留模型的原话（截断）——不许只说「解析失败」。
    """
    raw = text if isinstance(text, str) else ""
    blank = {"parsed": False, "text": None, "window_facts": None, "history_facts": None}
    try:
        data = extract_json(raw)
    except ValueError:
        return {**blank, "reason": raw.strip()[:300] or "（模型返回了空响应）"}
    if not isinstance(data, dict):
        return {**blank, "reason": f"模型返回的不是一个 JSON 对象：{str(data)[:200]}"}
    return {
        "parsed": True,
        "text": data.get("text"),
        "window_facts": data.get("window_facts"),
        "history_facts": data.get("history_facts"),
        "reason": None,
    }


class HttpBrief:
    """简报角色的真实现：`(科目, 候选读数) -> {text, window_facts, history_facts, …}`。

    `transport` / `sleep` / `env` / `clock` 都是测试接缝（不联网、不花钱）。
    **调用失败抛 `ModelUnavailable`**（调用方报 502 `model_unavailable`、什么都不落盘）；
    答了话就照原样带回去，核不核对是 `brief.generate` 的事。
    """

    def __init__(self, config, runs_dir: Path | str, *, transport=None,
                 sleep=time.sleep, env=None, clock=None, timeout: float = TIMEOUT,
                 retries: int = RETRIES) -> None:
        self.config = config
        self.runs_dir = Path(runs_dir)
        self.transport = transport
        self.sleep = sleep
        self.env = env
        self.clock = clock
        self.timeout = timeout
        self.retries = retries

    def __call__(self, subject: str, digest: dict) -> dict:
        call: ModelCall = model_chat(
            self.config, brief_messages(subject, digest), tag=BRIEF_TAG,
            runs_dir=self.runs_dir, transport=self.transport, sleep=self.sleep,
            env=self.env, clock=self.clock, timeout=self.timeout, retries=self.retries,
        )
        # provider／model／run_id 三项照 `intake_client._with_identity` 的口径补齐：
        # 落盘的简报要能回答「这是哪个模型、哪一次调用写的」（契约 §10.5 的两个字段）
        return {**parse_brief(call.text), "provider": call.provider,
                "model": call.model, "run_id": call.run_id}


# `default_transport` / `save_run` 是给测试与将来的角色用的显式再导出，
# 与 `intake_client.py` / `judge_client.py` 同一形状：这一层自己不实现 HTTP。
__all__ = [
    "BRIEF_TAG", "BRIEF_SYSTEM", "HttpBrief", "brief_messages", "parse_brief",
    "default_transport", "save_run",
]
