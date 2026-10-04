"""测试夹具：数据一律自造在临时目录里，测完即删。

真实题卡只有两张（`data/problems/*.json`），它们不属于测试：工单 #1 的第 35 条
user story 明写「端到端测试用临时题卡、测完即删，真实的题卡不被测试污染」。
所以这个文件里没有一处指向真实数据目录的路径。
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # 让 `import server.*` 在 repo 根下可用
    sys.path.insert(0, str(ROOT))

# 一张真的 1×1 PNG：服务按后缀定 Content-Type，但夹具用真字节，
# 免得将来加了魔数校验才发现夹具是假的。
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="
)

# 一个远在过去、远离冷却窗口的时刻，让「是否在冷却」不随运行时刻漂移。
LONG_AGO = "2020-01-01T00:00:00+08:00"


def make_card(pid: str = "p-20200101-aaaaaa", **overrides) -> dict:
    """造一张形状与真实题卡一致的卡（字段名照 data/problems/*.json）。

    overrides 支持点号路径，比如 `**{"problem.type": "solution"}`，
    用来只改一个深处字段而不用把整棵子树抄一遍。
    """
    card = {
        "id": pid,
        "created_at": LONG_AGO,
        "source": {
            "page_image": f"data/pages/{pid[2:8]}.png",
            "bbox_norm": [0.0, 0.0, 1.0, 1.0],
            "bbox_px": [0, 0, 100, 100],
            "original_file": "1.png",
        },
        "problem": {
            "image": f"data/assets/{pid}-problem.png",
            "type": "choice",
            "transcript": "1. 一道题",
            "options": [{"label": "A", "text": "甲"}, {"label": "B", "text": "乙"}],
            "clean_image": f"data/assets/{pid}-clean.png",
            "clean": {
                "method": "erase_ink",
                "boxes_norm": [[0.1, 0.1, 0.2, 0.2]],
                "mask_px": 100,
                "colored_px": 10,
                "residual_px": 0,
                "dropped_px": 5,
                "health": {"residual_color_px": 0, "print_holes_px": 0},
            },
        },
        "original_solution": {
            "present": True,
            "original_answer": "B",
            "transcript": "（黑笔）推导",
            "correction_transcript": "（红笔）订正",
        },
        "correct_solution": {"text": "正解正文", "source": "ai"},
        "standard_answer": {"value": "A", "confidence": 0.9},
        "topics": ["函数与导数/极值与最值"],
        "error_causes": ["概念不清"],
        "new_tag_proposals": [],
        "review": {"status": "reviewed", "reviewed_at": "2020-01-01T00:00:00+00:00"},
        "attempts": [],
        "mastery": {"state": "in_pool", "streak": 0, "last_attempt_at": None},
        "provenance": {
            "role": "extract",
            "provider": "deepseek",
            "model": "deepseek-flash",
            "extract_warnings": ["夹具"],
            "confidence": {"standard_answer": 0.9},
        },
        "print": {"cells": 1, "cells_source": "manual"},
    }
    for path, value in overrides.items():
        node = card
        *parents, leaf = path.split(".")
        for key in parents:
            node = node[key]
        node[leaf] = value
    return card


def make_data_dir(tmp_path: Path, cards: list[dict], *, images: dict[str, bytes] | None = None) -> Path:
    """在工作区里造一个最小数据目录：problems/ + assets/。"""
    root = tmp_path / "data"
    (root / "problems").mkdir(parents=True, exist_ok=True)
    (root / "assets").mkdir(parents=True, exist_ok=True)
    for card in cards:
        (root / "problems" / f"{card['id']}.json").write_text(
            json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    for name, blob in (images or {}).items():
        (root / "assets" / name).write_bytes(blob)
    return root


@pytest.fixture
def api_for(tmp_path):
    """返回一个工厂：给几张口卡，拿一个指向临时目录的 Api。

    `files={"文件名": card}` 用来自造「文件名与卡内 id 不一致」那类事故。
    """
    from server.http import Api

    def build(cards=None, *, files=None, extra_files=None, images=None, root=None, clock=None,
              judge=None, runs_dir=None, config=None):
        base = root or make_data_dir(tmp_path, cards or [])
        for name, card in (files or {}).items():
            (base / "problems" / f"{name}.json").write_text(
                json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        if extra_files:
            for name, blob in extra_files.items():
                (base / "problems" / name).write_bytes(blob)
        if images:
            for name, blob in images.items():
                (base / "assets" / name).write_bytes(blob)
        return Api(base, clock=clock, judge=judge, runs_dir=runs_dir, config=config)

    return build


def get_json(api, target: str):
    """打一个 GET，返回 `(status, 解析后的信封)`。所有测试都从这一个接缝看服务。"""
    response = api.handle("GET", target)
    return response.status, json.loads(response.body)


def post_json(api, target: str, payload):
    """打一个 POST（body 按 JSON 编码），返回 `(status, 解析后的信封)`。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    response = api.handle("POST", target, body)
    return response.status, json.loads(response.body)


def post_raw(api, target: str, body: bytes):
    """打一个 POST，body 原样送（用来测「不是 JSON」那类输入错）。"""
    response = api.handle("POST", target, body)
    return response.status, json.loads(response.body)
