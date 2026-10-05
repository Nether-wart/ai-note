"""**切分角色**的调用接缝：一整页照片 → 模型原文（#10 那一半的模型侧）。

`server/segmentation.py` 只做**确定性**的事：解析模型给的位置、逐块校验、三条对账判据。
这一层只负责把整页照片送出去、把模型的**原文**带回来——**不解析**
（解析在 `segmentation.parse_candidate_blocks`，一处实现，不在这里抄第二份）。

**为什么这一角色要过视觉探针**（契约 §1 的角色表、issue #17 §8）：切分最坏的失败是
「纯文本模型收到图片不报错、照着提示词**凭空编块**」——那会让一页题凭空多出几个位置
离谱的块，把「静默丢题」换成一个更贵的问题：**看起来切过了**。视觉探针（给一张写着
随机数字的图，看它读不读得出来）就是为这个形状设的第一关；第二关是固定照片集上的人眼；
第三关是三条确定性对账判据。三关**都**要过（不过考不许上岗）。

**留档**：每次调用写 `runs/<stamp>-segment.json`，图片不入档（口径同 `intake_client`）。
**一处已知的审计缺口**：页文件上的 `segmentation` 只有 `{mode, at, note}`，**没有**
provider/model/run_id——所以「这一页是哪个模型切的」目前只在留档里查得到，不在页上。
要把它记进页文件就得同时改契约与 `page_create`，那一笔**没有**在这一版做（见 issue #17）。

**失败分两种，别混**（与抽取角色同一条纪律）：

  · **调用失败**（网络／超时／缺密钥／上游 5xx）→ `ModelUnavailable` 往上抛，
    由调用方变成 **502**，页文件一个字节都不动。收件目录里已经落下的照片**不回滚**
    ——那件事发生在这次请求之前。
  · **答了话但抠不出块** → 那是**一次回答**：原文照带回去，
    `parse_candidate_blocks` 给 `parsed: False`，页**照建**、`blocks` 是 `null`（#23），
    人在照片上自己画框。**不许把「切分没跑成」伪装成「这一页没有题」。**

配置：`SEGMENTER_PROVIDER` / `SEGMENTER_MODEL`（默认沿用抽取角色，理由见契约 §1——
切分与「读红笔语义」同属看照片的活，共用一套默认最省事；要分开配也随时能分开）。
**前提**：`ROLE_DEFAULTS` 里得有 `"segmenter"` 这一项，`load_role_config` 才认这个角色。
"""

from __future__ import annotations

import time
from pathlib import Path

from .model_client import ModelCall, default_transport, save_run
from .model_client import chat as model_chat
from .segmentation import SEGMENT_SHAPE, SEGMENT_SYSTEM

# `data:<mime>;base64,…` 只有**一份**实现（在 `intake_client` 里）。
# 这一层不写第二份：两份拼法迟早会在某一条路径上漏掉缩放或漏掉重新编码。
from .intake_client import image_data_url

SEGMENT_TAG = "segment"


def segment_messages(*, image_url: str) -> list[dict]:
    """`[system, user]` 两条消息。

    system 是 `segmentation.SEGMENT_SYSTEM`——**不在这里重写**：提示词与解析它的
    那份代码必须一起改（改一处忘一处就会让「模型按新形状答、解析器按旧形状读」，
    而那看起来像模型突然变笨了）。
    """
    return [
        {"role": "system", "content": SEGMENT_SYSTEM},
        {"role": "user", "content": [
            {"type": "text", "text": "这一页的块列表，请按这个形状给：\n" + SEGMENT_SHAPE},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]},
    ]


class HttpSegmenter:
    """切分角色的真实现：`(照片路径) → 模型原文`。传输、时钟、睡眠都可注入。

    返回的是**原文**（`str`），不是解析好的块：解析归 `segmentation.parse_candidate_blocks`。
    `ModelCall` 的身份（provider/model/run_id）留在 `runs/` 的留档里，不往上传——
    见模块 docstring 里那处已知的审计缺口。
    """

    def __init__(self, config, runs_dir: Path | str, *, transport=None,
                 sleep=time.sleep, env=None, clock=None, timeout: float = 180,
                 retries: int = 3) -> None:
        self.config = config
        self.runs_dir = Path(runs_dir)
        self.transport = transport
        self.sleep = sleep
        self.env = env
        self.clock = clock
        self.timeout = timeout
        self.retries = retries

    def __call__(self, image_path) -> str:
        """问一次模型。**调用失败抛 `ModelUnavailable`**（调用方据此报 502、什么都不写）。"""
        messages = segment_messages(image_url=image_data_url(image_path))
        call: ModelCall = model_chat(
            self.config, messages, tag=SEGMENT_TAG, runs_dir=self.runs_dir,
            transport=self.transport, sleep=self.sleep, env=self.env, clock=self.clock,
            timeout=self.timeout, retries=self.retries,
        )
        return call.text


__all__ = [
    "SEGMENT_TAG", "HttpSegmenter", "segment_messages",
    "default_transport", "save_run",
]
