"""题卡文件的读写（**唯一实现**）：`<数据目录>/problems/<pid>.json`。

为什么要有这个模块：写盘那一对以前在 `attempt.py` 与 `page_commit.py` 里**各有一份**
（都是「先写临时文件再 `os.replace`」），再给属性编辑写一份就是第三份。
多份实现的代价不是多敲几行字，而是**漂移**——其中一份忘了 `.tmp`、忘了 `ensure_ascii`、
或者拿卡里的 `id` 而不是**传进来的 pid** 去拼路径。最后那条是 §10.2 那条纪律
（**内容不许决定写到哪、读到哪**）的正面违反，而它已经真发生过一次
（一份 `id: "../problems/p-x"` 的页文件会覆盖一张真题卡）。

所以：**路径只由 pid 算，内容永远不参与。**
"""

from __future__ import annotations

import json
import os
from pathlib import Path


def card_path(catalog, pid: str) -> Path:
    """`<数据目录>/problems/<pid>.json`。**只用 pid**。"""
    return Path(catalog.problems_dir) / f"{pid}.json"


def read_card(catalog, pid: str) -> dict | None:
    """读一张卡；文件不在就是 `None`（不是异常）。读不出来**抛**——那是坏数据，不是"没有"。"""
    path = card_path(catalog, pid)
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_card(catalog, pid: str, card: dict) -> Path:
    """原子地写回**按 pid 算出来的那个文件**（不是卡里 `id` 指向的文件）。

    先写临时文件再 `os.replace`：中途失败不留半张卡（`pages.save_page` 同一条纪律）。
    """
    path = card_path(catalog, pid)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():          # 写一半失败时不留残骸；换成了就本来不在了
            tmp.unlink()
    return path
