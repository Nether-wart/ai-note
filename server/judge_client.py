"""**判定角色**的调用接缝：提示词与消息形状（#5 验收第 3 条）。

**管道在 `server/model_client.py`**（#12 抽出去的）：HTTP、JSON 抽取、`ModelUnavailable`、
`runs/` 留档四样与角色无关的东西都在那里，判定与抽取（#12）共用同一条管道。
这里**只留判定角色独有的东西**：

  · `JUDGE_SYSTEM` / `judge_messages`：**纯文本**提示词——输入只有标准答案与这次作答，
    **不发送任何图片**（spec #1 US 38；等价性判断不需要看题）。提示词逐字继承
    `proto/slice.py:325-333` 的 `EQUIV_JUDGE_SYSTEM`（口径继承，代码不继承——
    `proto/` 是冻结的只读证据）。
  · `JUDGE_TAG`：留档文件名里的角色标记。
  · `HttpJudge`：把角色配置 + 消息形状接到管道上。

**这一层不做任何判定映射**——映射在 `server/judge.py`（#4 的唯一实现），
也不写题卡——回写在 `server/attempt.py`。
"""

from __future__ import annotations

import time
from pathlib import Path

from .config import JudgeConfig
from .model_client import (  # noqa: F401  —— 转出去给 #5 的调用方（形状没变）
    BACKOFF_SECONDS,
    MAX_RESPONSE_CHARS,
    RETRIES,
    RETRY_STATUSES,
    TIMEOUT,
    ModelCall,
    ModelUnavailable,
    chat,
    content_of,
    default_transport,
    extract_json,
    save_run,
)

JUDGE_TAG = "judge"

# 逐字继承 proto/slice.py:325-333 的 EQUIV_JUDGE_SYSTEM。改这里必须重跑判定角色的考卷
# （spec #1「换任何模型之后都要重跑验收」，提示词改了同理）。
JUDGE_SYSTEM = """你要判断两个数学答案是否**等价**（数学上相同，写法可以不同）。

输出一个 JSON：{"equivalent": true, "confidence": 0.0, "reason": "一句话"}

只有数学上完全相同才算等价。举例：
- "2/√3" 与 "2√3/3" → 等价
- "x=2" 与 "x=±2" → 不等价
- "(1,2)" 与 "[1,2]" → 不等价
- "1/2" 与 "0.5" → 等价"""

# 判定角色返回的形状在 #5 就叫这个名字（`server/attempt.py` 与测试都读 `JudgeCall`）。
# 形状与管道里那个 `ModelCall` 一模一样，所以是同一个类——不是两份定义。
JudgeCall = ModelCall


def judge_messages(standard_answer: str, answer: str) -> tuple[str, str]:
    """`(system, user)` 两段**纯文本**。口径继承 `proto/slice.py:767-771`。"""
    return JUDGE_SYSTEM, f'标准答案："{standard_answer}"\n待判答案："{answer}"\n两者是否等价？'


class HttpJudge:
    """判定角色的真实现。`transport` / `sleep` / `env` / `clock` 都是测试接缝。"""

    def __init__(self, config: JudgeConfig, runs_dir: Path | str, *, transport=None,
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

    # 让 `Api(judge=…)` 与真实现同一个调用形状
    def __call__(self, standard_answer: str, answer: str) -> JudgeCall:
        system, user = judge_messages(standard_answer, answer)
        return chat(
            self.config,
            [{"role": "system", "content": system},
             {"role": "user", "content": user}],
            tag=JUDGE_TAG,
            runs_dir=self.runs_dir,
            transport=self.transport,
            sleep=self.sleep,
            env=self.env,
            clock=self.clock,
            timeout=self.timeout,
            retries=self.retries,
        )
