"""掌握与冷却的**只读读数**（契约 §4）。

口径继承 `proto/slice.py` 与 `proto/test_mastery.py`（冻结的实测证据），代码不继承。
这里只有读数，没有写：`apply_attempt` 那套状态机归写端点（#5）。

两条最容易写错、原型里各踩过一次的地方：
  · 冷却的基准是「上次重做」，**从未重做过的以录入时间起算**（CONTEXT「默认打印清单」）；
  · 比较时刻必须**先归一化到 UTC**再比——卡里的 created_at 是 +08:00，重做时刻是 UTC，
    直接比字符串会把 06:31Z 排在 09:00Z 后面。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

COOLDOWN_DAYS = 7  # 距上次重做不满 7 天为冷却
MASTERY_STREAK = 2  # 掌握 = 连续 2 次判对，且两次都已脱离冷却

TYPE_CN = {"choice": "选择", "fillin": "填空", "solution": "解答"}
MASTERY_CN = {"in_pool": "在池", "graduated": "毕业"}
VERDICT_CN = {"correct": "对", "wrong": "错", "unreadable": "看不清"}
CHANNEL_CN = {"paper": "纸上重做", "screen": "屏幕重做"}
# 「这条判定是谁给的」。**机器可以读的取值只有这两个**（编排裁决 D2：沿用 proto 的
# auto/human）；中文渲染另给一份，界面照它显示，不要把中文写回 source 字段。
SOURCE = ("auto", "human")
SOURCE_CN = {"auto": "自动判定", "human": "人工确认"}
# 版面格初值。键必须用题卡里的实际枚举（choice/fillin/solution）——
# 写中文键会静默退回默认值，这个坑真踩过。
DEFAULT_CELLS = {"choice": 1, "fillin": 2, "solution": 4}


def parse_dt(value) -> datetime | None:
    """ISO 8601 → 带时区的时刻。裸时刻按 UTC 认（卡里两种写法都出现过）。"""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def last_attempt_at(card: dict) -> datetime | None:
    return parse_dt((card.get("mastery") or {}).get("last_attempt_at"))


def cooldown_base(card: dict) -> datetime | None:
    """冷却的基准时刻：上次重做；从未重做过的以**录入时间**起算。"""
    return last_attempt_at(card) or parse_dt(card.get("created_at"))


def cooldown_until(card: dict) -> datetime | None:
    base = cooldown_base(card)
    return base + timedelta(days=COOLDOWN_DAYS) if base else None


def is_cooling(card: dict, at: datetime | None = None) -> bool:
    until = cooldown_until(card)
    return bool(until and (at or datetime.now(timezone.utc)) < until)


def cooldown_days_left(card: dict, at: datetime | None = None) -> int:
    until = cooldown_until(card)
    at = at or datetime.now(timezone.utc)
    if not until or at >= until:
        return 0
    secs = (until - at).total_seconds()
    return max(1, int(secs // 86400) + (1 if secs % 86400 else 0))


def sort_key(card: dict) -> str:
    """默认打印清单按「上次重做的先后、从早到晚」排，归一化到 UTC 再比。"""
    base = cooldown_base(card)
    return base.astimezone(timezone.utc).isoformat() if base else ""


def temperature(card: dict, at: datetime | None = None) -> tuple[bool, bool, int]:
    """`(graduated, cooling, cooldown_days_left)`。"""
    graduated = (card.get("mastery") or {}).get("state") == "graduated"
    return graduated, is_cooling(card, at), cooldown_days_left(card, at)


def default_list_status(card: dict, at: datetime | None = None) -> tuple[bool, str | None]:
    """`(in_default_list, excluded_because)`。被排除时必须给出**原因**（不许静默）。

    未毕业、且已脱离冷却，才在默认打印清单里。两个原因同时成立时给 "graduated"：
    毕业是更强的陈述，且毕业的卡本来就不会因为冷却结束而回来。
    """
    graduated, cooling, _ = temperature(card, at)
    if graduated:
        return False, "graduated"
    if cooling:
        return False, "cooling"
    return True, None
