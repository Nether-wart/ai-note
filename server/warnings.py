"""会喊的检查（契约 §8，ADR 0007 第 6 条）。

这套检查是被一次真事故逼出来的：界面一个静默的 bug 把第一题的原答/标准答案
写进了第二题，于是那张卡带着另一道题的标准答案被标成了「已审核」。
所以判据不测措辞、只测「该响的响了」。

每个警告是 `{code, message, id, level}`：`code` 稳定（机器/测试读它），
`message` 是给人看的原话（界面照原话显示，不改写、不吞掉），
`level` **显式发出来**（`"warning"`｜`"hint"`）——有默认值却不出现在响应里，
#9 加 `hint` 级码时就得改判定逻辑、界面也只能靠猜。
"""

from __future__ import annotations

import re

from . import assets, pages
from .mastery import TYPE_CN

_CHOICE_LETTER = re.compile(r"[A-Da-d]")


def _warn(code: str, message: str, pid: str | None, level: str = "warning") -> dict:
    """一条警告。`level` 由**服务**给（契约 §2：级别只有服务能定，界面不许自行升降级）。"""
    return {"code": code, "message": message, "id": pid, "level": level}


def card_warnings(card: dict, catalog) -> list[dict]:
    """一张卡的字段自检。既在列表页暴露存量脏数据，也是写端点保存前的同一份判据。"""
    pid = card.get("id")
    problem = card.get("problem") or {}
    t = problem.get("type")
    cn = TYPE_CN.get(t, t)
    std = ((card.get("standard_answer") or {}).get("value") or "").strip()
    orig = ((card.get("original_solution") or {}).get("original_answer") or "").strip()
    topics = card.get("topics") or []
    reviewed = (card.get("review") or {}).get("status") == "reviewed"

    warns: list[dict] = []
    if not std:
        warns.append(_warn("standard_answer_missing",
                           "标准答案为空 → 不能走自动判定，只能人工确认", pid))
    if t == "choice" and std and not _CHOICE_LETTER.fullmatch(std):
        warns.append(_warn("standard_answer_not_choice_letter",
                           f"选择题的标准答案不是选项字母：{std!r}", pid))
    if t != "choice" and std and _CHOICE_LETTER.fullmatch(std):
        warns.append(_warn("standard_answer_choice_letter_on_non_choice",
                           f"题型是{cn}，标准答案却只有一个字母 {std!r} ——像是把选择题的答案填到这道题上了", pid))
    if t != "choice" and orig and _CHOICE_LETTER.fullmatch(orig):
        warns.append(_warn("original_answer_choice_letter_on_non_choice",
                           f"题型是{cn}，原答却是一个选项字母 {orig!r}", pid))
    if orig and std and orig.upper() == std.upper():
        warns.append(_warn("original_answer_equals_standard_answer",
                           f"原答与标准答案相同（都是 {std!r}）→ 这是错题本，录进来的是做错的题；"
                           f"两者相同通常意味着订正被当成了原答", pid))
    if not topics:
        warns.append(_warn("topics_empty", "考点为空 → 考点是检索入口", pid))
    if reviewed and (not std or not topics):
        warns.append(_warn("reviewed_but_incomplete",
                           "已标为已审核，但标准答案或考点是空的", pid))

    if not assets.asset_file(catalog, card, "clean"):
        if assets.recorded_path(card, "clean"):
            warns.append(_warn("clean_image_file_missing",
                               f"卡里记了擦除图 {assets.recorded_path(card, 'clean')!r}，"
                               f"但文件不在 → 屏幕重做会拿到一个 404", pid))
        warns.append(_warn("no_clean_image",
                           "没有擦除手写后的题面图 → 不进屏幕重做，也不进重做纸", pid))
    if assets.recorded_path(card, "original") and not assets.asset_file(catalog, card, "original"):
        warns.append(_warn("original_image_file_missing",
                           f"卡里记了题面图 {assets.recorded_path(card, 'original')!r}，但文件不在", pid))

    # 页绑定（契约 §8、#9 验收 2、编排裁决 D5）。级别由 `page_binding` 一处定：
    # 旧卡还没回填 = 提示；页实体在场却对不上账 = 警告。这里不重新判一遍。
    binding = pages.page_binding(catalog, card)
    if not binding["bound"]:
        warns.append(_warn(binding["code"], binding["message"], pid, level=binding["level"]))
    return warns


def index_warnings(records: list[dict]) -> list[dict]:
    """索引级检查：跨卡才看得出来的那类错。"""
    warns: list[dict] = []

    by_transcript: dict[str, list[str]] = {}
    for rec in records:
        text = (rec.get("transcript") or "").strip()
        if text:
            by_transcript.setdefault(text, []).append(rec["id"])
    for text, ids in by_transcript.items():
        if len(ids) > 1:
            warns.append(_warn(
                "duplicate_transcript",
                f"两张卡的题干逐字相同（{' '.join(ids)}）→ 两道不同的题不可能有同一段题干，"
                f"检查是不是串题了。题干：{text[:40]}",
                None,
            ))
    return warns
