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
import re
from pathlib import Path

from .catalog import ID_PATTERN


class IllegalCardId(Exception):
    """不是合法的题卡 id。**在任何碰文件系统的动作之前**就该被拦住。"""


def check_pid(pid: str) -> str:
    """id 只认 `catalog.ID_PATTERN`（与 §1 的 id 类参数同一条规则）。

    这一道门必须在这里：它是"路径由谁算"的唯一实现，而**写盘穿越**正是绕过它来的。
    §8 那两条（`page_id_mismatch`／`page_image_unsafe`）与「内容不许决定写到哪」
    说的是同一件事：**id 决定了写到哪，所以它必须先过关**。
    """
    if not isinstance(pid, str) or not re.fullmatch(ID_PATTERN, pid or ""):
        raise IllegalCardId(pid)
    return pid


def card_path(catalog, pid: str) -> Path:
    """`<数据目录>/problems/<pid>.json`。**只用 pid**，且 pid 先过 `check_pid`。

    以前这里直接拼字符串，于是 `..%2Fproblems%2F<p-另一个 id>` 这种 pid 能改到**别的文件**
    （实测过：`PATCH` 返回 200、卡真被改了）。`..` 落进路径就是"内容决定写到哪"。
    """
    return Path(catalog.problems_dir) / f"{check_pid(pid)}.json"


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
