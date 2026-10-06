"""测试夹具：数据一律自造在临时目录里，测完即删。

真实题卡只有两张（`data/problems/*.json`），它们不属于测试：工单 #1 的第 35 条
user story 明写「端到端测试用临时题卡、测完即删，真实的题卡不被测试污染」。
所以这个文件里没有一处指向真实数据目录的路径。
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
from pathlib import Path

import pytest

from server.ink import InkImage, encode_png

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:  # 让 `import server.*` 在 repo 根下可用
    sys.path.insert(0, str(ROOT))

# 一张真的 1×1 PNG：服务按后缀定 Content-Type，但夹具用真字节，
# 免得将来加了魔数校验才发现夹具是假的。
# ⚠ 这必须是**真的** PNG：`ink.read_png` 要能解开它。以前这里是一串手写字节，
# `ink` 读它时会 `zlib.error`（IDAT 解不开）——这个坑已经咬了两次（切分接缝、审核转录），
# 根因是"夹具只要长得像 PNG 就行"这个假设在**任何真去解码它的地方**都不成立。
PNG_1X1 = encode_png(InkImage(1, 1, [(255, 255, 255)]))

# 一个远在过去、远离冷却窗口的时刻，让「是否在冷却」不随运行时刻漂移。
LONG_AGO = "2020-01-01T00:00:00+08:00"


# 代理环境变量：真传输（`model_client.default_transport` → stdlib `urllib`）会读它们。
# 脱网自证用的死代理、公司代理都会让「连 127.0.0.1」的请求被送到代理上去，于是
# 「真 HTTP 客户端能把请求发出去」那几条用例因为**环境**而红——而它们测的不是
# 代理配置对不对（作业单 8）。
PROXY_ENV = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
             "http_proxy", "https_proxy", "all_proxy", "no_proxy")
LOOPBACK_NO_PROXY = "127.0.0.1,localhost,0.0.0.0"


@contextlib.contextmanager
def loopback_transport_env():
    """临时把代理环境变量收干净，让真传输只走本机回环（作业单 8）。

    「连接失败时的形状」另有**注入的** `FakeTransport(OSError(...))` 桩
    （`test_judge_client.py` / `test_intake_client.py` 各一条），不依赖真网络；
    这里解决的是另一半：本机回环那两条真传输用例不许依赖「环境里恰好没设代理」。
    """
    saved = {name: os.environ.get(name) for name in PROXY_ENV}
    for name in PROXY_ENV:
        os.environ.pop(name, None)
    os.environ["NO_PROXY"] = os.environ["no_proxy"] = LOOPBACK_NO_PROXY
    try:
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def make_card(pid: str = "p-20200101-aaaaaa", **overrides) -> dict:
    """造一张形状与真实题卡一致的卡（字段名照 data/problems/*.json）。

    overrides 支持点号路径，比如 `**{"problem.type": "solution"}`，
    用来只改一个深处字段而不用把整棵子树抄一遍。
    """
    card = {
        "id": pid,
        "created_at": LONG_AGO,
        # 科目是导航的根，也是卡上的一等字段。夹具默认给一个**在词表里**的科目，
        # 这样"一张干净的卡"仍然是干净的；要造未归类就显式 `**{"subject": None}`。
        "subject": "数学",
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


def make_data_dir(tmp_path: Path, cards: list[dict], *, images: dict[str, bytes] | None = None,
                  vocab: bool = True) -> Path:
    """在工作区里造一个最小数据目录：problems/ + assets/（+ vocab/）。

    `vocab=False` 用来造「数据目录里根本没有词表」那一档——那是要**喊出来**的一种状态
    （`subjects_vocab_missing`），不是崩溃，但也不能装作科目表是空的。
    """
    root = tmp_path / "data"
    (root / "problems").mkdir(parents=True, exist_ok=True)
    (root / "assets").mkdir(parents=True, exist_ok=True)
    if vocab:
        (root / "vocab").mkdir(parents=True, exist_ok=True)
        (root / "vocab" / "subjects.json").write_text(
            json.dumps({"科目": ["数学", "物理"]}, ensure_ascii=False), encoding="utf-8"
        )
        (root / "vocab" / "topic-outline.seed.json").write_text(
            json.dumps({"大纲": {"数学": {"函数与导数": {"极值与最值": []}}}},
                       ensure_ascii=False), encoding="utf-8"
        )
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
              judge=None, runs_dir=None, config=None, max_upload_bytes=None,
              max_attempt_bytes=None):
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
        return Api(base, clock=clock, judge=judge, runs_dir=runs_dir, config=config,
                   max_upload_bytes=max_upload_bytes, max_attempt_bytes=max_attempt_bytes)

    return build


def get_json(api, target: str):
    """打一个 GET，返回 `(status, 解析后的信封)`。所有测试都从这一个接缝看服务。"""
    response = api.handle("GET", target)
    return response.status, json.loads(response.body)


def multipart_body(files, *, field: str = "file", fields=None,
                   boundary: str = "----AiNoteTestBoundary"):
    """自造一个 multipart/form-data body（形状与浏览器 FormData 一致）。

    `files` 是 `[(文件名, 字节)]` 或 `[(文件名, 字节, 声明的 Content-Type)]`；
    `fields` 是**文本**字段 `[(名, 值)]`——浏览器 `FormData.append("subject", "数学")`
    发出来的就是这一种：有 `name`、**没有** `filename`，服务按「纯文本段」认它。
    返回 `(body, content_type)`——上传测试不许碰真照片，一律自己造字节。
    """
    out = bytearray()
    for name, value in (fields or []):
        out += f"--{boundary}\r\n".encode()
        out += f'Content-Disposition: form-data; name="{name}"\r\n'.encode()
        out += b"\r\n" + str(value).encode("utf-8") + b"\r\n"
    for item in files:
        name, blob = item[0], item[1]
        declared = item[2] if len(item) > 2 else "image/png"
        out += f"--{boundary}\r\n".encode()
        out += f'Content-Disposition: form-data; name="{field}"; filename="{name}"\r\n'.encode()
        if declared:
            out += f"Content-Type: {declared}\r\n".encode()
        out += b"\r\n" + blob + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def post_json(api, target: str, payload):
    """打一个 POST（body 按 JSON 编码），返回 `(status, 解析后的信封)`。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    response = api.handle("POST", target, body)
    return response.status, json.loads(response.body)


def post_raw(api, target: str, body: bytes):
    """打一个 POST，body 原样送（用来测「不是 JSON」那类输入错）。"""
    response = api.handle("POST", target, body)
    return response.status, json.loads(response.body)
