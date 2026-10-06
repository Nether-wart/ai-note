"""重做页（#7）的队列与题型分化：**从 pytest 直接断言界面那侧的实现**。

为什么要有这份桥：队列解析住在 `site/src/lib/redo.js`（浏览器与 node 共用**同一份**
实现），而「越界必须明确失败」是 #8 要写不变量测试的那一条。把规则在 Python 里再抄一遍
才是这个项目反复被咬的失败（两处各判各的），所以这里用 node 当探针跑**同一份实现**。

没有 node 就 skip：node 不是后端的依赖，队列解析也不是后端的事。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROBE = ROOT / "site" / "tests" / "probe-redo.mjs"

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="没有 node：队列解析住在 site/ 的 JS 里"
)

# 一张形状与 /api/index 的 Problem 记录一致的卡（只留这份测试要用的字段）。
CHOICE = {
    "id": "p-a",
    "type": "choice",
    "options": [{"label": "A", "text": "甲"}],
    "images": {"original": "/api/problem/p-a/image/original", "clean": "/api/problem/p-a/image/clean"},
    "auto_judge": {"eligible": True, "reason": None, "reason_text": None},
    "screen_redo": {"ready": True, "blockers": [], "blocker_text": []},
    "in_default_list": True,
    "graduated": False,
}
SOLUTION = {
    **CHOICE,
    "id": "p-sol",
    "type": "solution",
    "options": [],
    "auto_judge": {
        "eligible": False,
        "reason": "solution_type",
        "reason_text": "解答题只能人工确认：过程题在屏幕上敲不出过程",
    },
    "screen_redo": {
        "ready": False,
        "blockers": ["solution_type"],
        "blocker_text": ["解答题只能人工确认：过程题在屏幕上敲不出过程"],
    },
}


def probe(fn: str, *args):
    """跑一次 `site/src/lib/redo.js` 里的纯函数，拿回 JSON 结果。"""
    payload = json.dumps([{"fn": fn, "args": list(args)}], ensure_ascii=False)
    done = subprocess.run(
        ["node", str(PROBE)], input=payload, capture_output=True, text=True, cwd=ROOT, timeout=120
    )
    assert done.returncode == 0, f"node 探针失败：{done.stderr}"
    return json.loads(done.stdout)[0]


# ------------------------------------------------ 验收 1：越界必须明确失败


def test_out_of_range_index_fails_explicitly():
    result = probe("parseRedoQuery", "?queue=p-a,p-b&i=9")
    assert result["ok"] is False
    assert result["code"] == "index_out_of_range"
    assert result["details"]["count"] == 2


def test_missing_queue_fails_instead_of_falling_back_to_the_first_problem():
    result = probe("parseRedoQuery", "")
    assert result["ok"] is False
    assert result["code"] == "queue_missing"


def test_empty_queue_fails_explicitly():
    result = probe("parseRedoQuery", "?queue=&i=0")
    assert result["ok"] is False
    assert result["code"] == "queue_empty"


def test_a_queue_item_missing_from_the_index_fails_instead_of_being_skipped():
    result = probe("resolveQueueItem", [CHOICE], ["p-a", "p-gone"], 1)
    assert result["ok"] is False
    assert result["code"] == "problem_not_in_index"
    assert result["details"]["id"] == "p-gone"


def test_a_valid_queue_and_index_resolve_to_that_one_problem():
    result = probe("resolveQueueItem", [CHOICE, SOLUTION], ["p-a", "p-sol"], 1)
    assert result["ok"] is True
    assert result["problem"]["id"] == "p-sol"


def test_judged_problem_leaves_the_queue_without_touching_the_others():
    result = probe("removeAt", ["p-a", "p-b", "p-c"], 1)
    assert result["ok"] is True
    assert result["queue"] == ["p-a", "p-c"]


# --------------------------------------- 验收 3：题型分化（判据来自服务端读数）


def test_solution_gets_no_answer_box():
    result = probe("answerMode", SOLUTION)
    assert result["mode"] == "none"
    assert result["reason"] == "solution_type"
    assert result["reason_text"] == "解答题只能人工确认：过程题在屏幕上敲不出过程"


def test_choice_gets_options_and_fillin_gets_one_blank():
    assert probe("answerMode", CHOICE)["mode"] == "choice"
    fillin = {**CHOICE, "type": "fillin", "options": []}
    assert probe("answerMode", fillin)["mode"] == "fillin"


def test_an_unknown_problem_type_does_not_fall_back_to_choice():
    result = probe("answerMode", {**CHOICE, "type": "proof"})
    assert result["mode"] == "none"
    assert result["reason"] == "unknown_type"


# --------------------------------------- 验收 2：缺擦除图不退回原图


def test_a_card_without_a_clean_image_never_falls_back_to_the_original():
    result = probe("problemImage", {**CHOICE, "images": {"original": "/api/problem/p-a/image/original", "clean": None}})
    assert result["url"] is None
    assert result["missing"] is True
    assert "original" not in json.dumps(result, ensure_ascii=False)


# --------------------------------------- 验收 5：页头数字照服务给的原话


def test_header_reads_the_service_numbers_for_the_selected_basis():
    screen_redo = {
        "default_basis": "in_default_list",
        "bases": {
            "in_default_list": {
                "basis_text": "默认打印清单（未毕业且已脱离冷却）",
                "ready": 1,
                "blocked_total": 2,
                "not_auto_judgeable": {
                    "count": 2,
                    "by_reason": [
                        {"reason": "solution_type", "reason_text": "解答题…", "count": 1, "ids": ["p-sol"]},
                        {"reason": "unreviewed", "reason_text": "未审核…", "count": 1, "ids": ["p-u"]},
                    ],
                },
                "no_clean_image": {"count": 1, "ids": ["p-nc"], "message": "另有 1 道因缺少擦除手写后的题面图不能进屏幕重做"},
            },
            "including_cooling": {"basis_text": "未毕业（含冷却中）", "ready": 2, "blocked_total": 2,
                                  "not_auto_judgeable": {"count": 2, "by_reason": []},
                                  "no_clean_image": {"count": 1, "ids": [], "message": "另有 1 道…"}},
        },
    }
    selected = probe("redoHeader", {"screen_redo": screen_redo}, None)
    assert selected["basis"] == "in_default_list"
    assert selected["not_auto_judgeable"]["count"] == 2
    assert [r["reason"] for r in selected["not_auto_judgeable"]["by_reason"]] == ["solution_type", "unreviewed"]
    assert selected["no_clean_image"]["message"] == "另有 1 道因缺少擦除手写后的题面图不能进屏幕重做"

    switched = probe("redoHeader", {"screen_redo": screen_redo}, "including_cooling")
    assert switched["ready"] == 2
    assert switched["basis_text"] == "未毕业（含冷却中）"

    unknown = probe("redoHeader", {"screen_redo": screen_redo}, "bogus")
    assert unknown["known"] is False


# --------------------------------------- 验收 4：界面只提交作答，判定由服务做


def test_the_interface_submits_only_channel_and_answer():
    result = probe("screenPayload", "A")
    assert sorted(result.keys()) == ["answer", "channel"]
    assert result == {"channel": "screen", "answer": "A"}
