"""Problem 记录的**唯一**构造函数（契约 §3.1）。

列表页与详情页对同一道题说不同的话，是这个项目已经出过的那类事故
（界面把第一题的文字写进了第二题），所以两者由这里同一个函数产出，
详情只是它的**超集**——`test_problem.py` 逐字段断言这条承诺。
"""

from __future__ import annotations

from datetime import datetime, timezone

from . import assets, autojudge, warnings as warnings_mod
from .autojudge import REASONS
from .mastery import (
    DEFAULT_CELLS,
    MASTERY_CN,
    SOURCE_CN,
    TYPE_CN,
    VERDICT_CN,
    default_list_status,
    sort_key,
    temperature,
)


def attempt_brief(attempt: dict) -> dict:
    """最近几次重做的摘要形态（契约 §3.2）。

    `provider` 与 `model` 是**两个**字段（编排裁决 D2）：工单 #5 的验收点名了这两个，
    原型那个 `"provider/model"` 合体字段不许沿用——「换模型必须重跑考卷」这句话要求
    模型身份可查、可比。
    """
    from .mastery import CHANNEL_CN

    source = attempt.get("source")
    return {
        "at": (attempt.get("at") or "")[:10],
        "channel": attempt.get("channel"),
        "channel_cn": CHANNEL_CN.get(attempt.get("channel"), attempt.get("channel")),
        "verdict": attempt.get("verdict"),
        "verdict_cn": VERDICT_CN.get(attempt.get("verdict"), attempt.get("verdict")),
        "source": source,
        "source_cn": SOURCE_CN.get(source, source),
        "confidence": attempt.get("confidence"),
        "provider": attempt.get("provider"),
        "model": attempt.get("model"),
        "error_causes": attempt.get("error_causes") or [],
        "note": attempt.get("note"),
    }


def screen_redo_gate(card: dict, catalog) -> dict:
    """能不能进屏幕重做——两条硬闸门都必须是 `and`（编排裁决 D3/D4）。

    1. **有擦除手写后的题面图**（缺了不许回退到原图：原图印着订正，会把答案摆在做题的人面前）。
    2. **通过 `autojudge`**（已审核 + 有标准答案 + 非解答题）。

    两条都不满足时也只给一个布尔——`blockers` 是**全部**原因，不是第一个：
    一张题可以同时踩两个坑，界面要能一次说清。缺擦除图的**道数**由索引级汇总报出来
    （「另有 M 道……」），绝不静默丢掉。
    """
    blockers: list[str] = []
    texts: list[str] = []
    reason = autojudge.reject_reason(card)
    if reason:
        blockers.append(reason)
        texts.append(REASONS[reason])
    if not assets.asset_file(catalog, card, "clean"):
        blockers.append("no_clean_image")
        texts.append("缺少擦除手写后的题面图 → 不能进屏幕重做（也不许退回原图：原图印着订正）")
    return {"ready": not blockers, "blockers": blockers, "blocker_text": texts}


def problem_record(card: dict, catalog, at: datetime | None = None) -> dict:
    """题卡 → 契约 §3.1 的 Problem 记录。"""
    at = at or datetime.now(timezone.utc)
    pid = card.get("id")
    problem = card.get("problem") or {}
    mastery = card.get("mastery") or {}
    attempts = card.get("attempts") or []
    last = attempts[-1] if attempts else None
    graduated, cooling, days_left = temperature(card, at)
    in_default, excluded = default_list_status(card, at)
    printable = card.get("print") or {}

    return {
        "id": pid,
        "created_at": card.get("created_at"),
        "type": problem.get("type"),
        "type_cn": TYPE_CN.get(problem.get("type"), problem.get("type")),
        "transcript": problem.get("transcript"),
        "options": problem.get("options") or [],
        "original_answer": (card.get("original_solution") or {}).get("original_answer"),
        "correction": (card.get("original_solution") or {}).get("correction_transcript"),
        "original_transcript": (card.get("original_solution") or {}).get("transcript"),
        "present": (card.get("original_solution") or {}).get("present"),
        "standard_answer": (card.get("standard_answer") or {}).get("value"),
        "correct_solution": (card.get("correct_solution") or {}).get("text"),
        "topics": card.get("topics") or [],
        "error_causes": card.get("error_causes") or [],
        "new_tag_proposals": card.get("new_tag_proposals") or [],
        "review": (card.get("review") or {}).get("status"),
        "reviewed_at": (card.get("review") or {}).get("reviewed_at"),
        "review_reopened_because": (card.get("review") or {}).get("reopened_because"),
        "mastery": {
            "state": mastery.get("state"),
            "streak": mastery.get("streak"),
            "last_attempt_at": mastery.get("last_attempt_at"),
        },
        "mastery_cn": MASTERY_CN.get(mastery.get("state"), mastery.get("state")),
        "streak": int(mastery.get("streak") or 0),
        "graduated": graduated,
        "cooling": cooling,
        "cooldown_days": days_left,
        "in_default_list": in_default,
        "excluded_from_default_because": excluded,
        "last_verdict": (last or {}).get("verdict"),
        "last_verdict_cn": VERDICT_CN.get((last or {}).get("verdict"), (last or {}).get("verdict")),
        "attempts": len(attempts),
        "attempt_log": [attempt_brief(a) for a in attempts[-6:]],
        "sort_key": sort_key(card),
        "cells": printable.get("cells") or DEFAULT_CELLS.get(problem.get("type"), 2),
        "cells_source": printable.get("cells_source") or "default",
        "images": assets.image_urls(catalog, card),
        "has_clean": bool(assets.asset_file(catalog, card, "clean")),
        "auto_judge": autojudge.eligibility(card),
        "screen_redo": screen_redo_gate(card, catalog),
        "warnings": warnings_mod.card_warnings(card, catalog),
    }


def problem_detail(card: dict, catalog, at: datetime | None = None) -> dict:
    """契约 §5：Problem 记录的超集 + 四个详情专属字段。

    「超集」是硬承诺——列表页与详情页由同一个构造函数产出（就是上面那个），
    所以两处永远说同一套话。detail 只**增加**字段，从不改写列表条目里已有的字段。
    """
    record = problem_record(card, catalog, at)
    clean = (card.get("problem") or {}).get("clean") or {}
    if clean and "manual" not in clean:
        clean = {**clean, "manual": {"add": [], "drop": []}}
    record.update(
        attempts_detail=card.get("attempts") or [],
        source=card.get("source"),
        # 擦除那一趟的读数。`boxes_norm` 与 `manual` 是**裁剪图坐标**，
        # 与 `source.bbox_*` 的整页坐标是两套基准，跨用必须换算（契约 §5、§10.2）。
        clean={
            key: clean.get(key)
            for key in ("method", "boxes_norm", "manual", "mask_px", "colored_px",
                        "residual_px", "dropped_px", "health")
        }
        if clean
        else None,
        provenance=card.get("provenance"),
    )
    return record
