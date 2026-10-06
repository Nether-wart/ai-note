"""「这道题能不能走自动判定」——**只有这一份实现**（契约 §6）。

三个拒绝理由，按固定优先级依次判；同时成立时取前面的那个，所以同一张卡
永远给同一个答案。界面照 `reason_text` 原话显示，#5 的写端点拿同一个函数
决定收不收这次作答。

为什么单列一个模块：`proto/slice.py` 里已停用的命令行判定里已经有前两条。
如果新端点另写一遍，两处迟早各判各的——而「题型枚举静默退回默认值」正是
这个项目反复被咬的那类失败。
"""

from __future__ import annotations

REASONS = {
    "solution_type": "解答题只能人工确认：过程题在屏幕上敲不出过程",
    "no_standard_answer": "这道题没有标准答案 → 没有判定的基准，只能人工确认",
    "unreviewed": "这道题还没审核 → 标准答案还不可信，不参与自动判定",
}


def reject_reason(card: dict) -> str | None:
    """不能自动判定的原因；能判定时返回 `None`。"""
    if (card.get("problem") or {}).get("type") == "solution":
        return "solution_type"
    if not ((card.get("standard_answer") or {}).get("value") or "").strip():
        return "no_standard_answer"
    if (card.get("review") or {}).get("status") != "reviewed":
        return "unreviewed"
    return None


def eligibility(card: dict) -> dict:
    """契约 §6 的 `auto_judge` 读数。"""
    reason = reject_reason(card)
    if reason is None:
        return {"eligible": True, "reason": None, "reason_text": None}
    return {"eligible": False, "reason": reason, "reason_text": REASONS[reason]}
