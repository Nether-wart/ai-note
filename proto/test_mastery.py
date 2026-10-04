#!/usr/bin/env python3
"""掌握与冷却的状态机验收：不联网、不问模型、只验规则。

这套规则只该有一份实现（`slice.apply_attempt`），而它每一条都有反直觉之处：

  · 冷却起算于「上次重做」，**新录入的题以录入时间起算** → 当天重做不计入掌握；
  · 冷却只挡「计入正确」，**判错永远立刻清零回池**（不对称）；
  · 看不清既不清零也不计入；
  · 掌握 = 连续 2 次计入的正确，而"计入"本身就意味着间隔 ≥7 天。

跑法：python3 proto/test_mastery.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import slice as S  # noqa: E402

CREATED = "2026-10-04T14:31:35+08:00"      # 录入时刻，故意带 +08:00，与重做时刻的 UTC 混用


def d(day: int, hour: int = 9) -> str:
    """以录入当天为 D0，返回第 day 天的 UTC 时刻。"""
    return f"2026-10-{4 + day:02d}T{hour:02d}:00:00+00:00"


def fresh() -> dict:
    return {"id": "t-0001", "created_at": CREATED,
            "mastery": {"state": "in_pool", "streak": 0, "last_attempt_at": None},
            "attempts": []}


CASES: list[tuple[str, list[tuple[str, str]], dict]] = [
    ("当天判对：冷却起算于录入时间，不计入",
     [("correct", d(0))],
     {"credited": [False], "streak": 0, "state": "in_pool"}),
    ("隔 8 天判对 → 计入 1/2",
     [("correct", d(8))],
     {"credited": [True], "streak": 1, "state": "in_pool"}),
    ("隔天再判对：仍在冷却，只热身",
     [("correct", d(8)), ("correct", d(9))],
     {"credited": [True, False], "streak": 1, "state": "in_pool"}),
    ("两次对、各隔 ≥7 天 → 掌握 → 毕业",
     [("correct", d(8)), ("correct", d(16))],
     {"credited": [True, True], "streak": 2, "state": "graduated"}),
    ("毕业后判错 → 立刻回池、毕业取消",
     [("correct", d(8)), ("correct", d(16)), ("wrong", d(17))],
     {"credited": [True, True, False], "streak": 0, "state": "in_pool"}),
    ("判错当天再判对：冷却挡住计入",
     [("correct", d(8)), ("wrong", d(9)), ("correct", d(9))],
     {"credited": [True, False, False], "streak": 0, "state": "in_pool"}),
    ("判错之后隔 11 天判对 → 重新计入 1/2",
     [("wrong", d(9)), ("correct", d(20))],
     {"credited": [False, True], "streak": 1, "state": "in_pool"}),
    ("看不清：既不清零也不计入",
     [("correct", d(8)), ("unreadable", d(20))],
     {"credited": [True, False], "streak": 1, "state": "in_pool"}),
]


def main() -> int:
    bad = 0
    for name, seq, want in CASES:
        rec = fresh()
        got = [S.apply_attempt(rec, v, at=t, source="人工确认")["credited"] for v, t in seq]
        ok = (got == want["credited"]
              and rec["mastery"]["streak"] == want["streak"]
              and rec["mastery"]["state"] == want["state"])
        bad += 0 if ok else 1
        print(f"  {'✓' if ok else '✗'} {name}")
        if not ok:
            print(f"      得到 credited={got} streak={rec['mastery']['streak']} "
                  f"state={rec['mastery']['state']}")
            print(f"      期望 credited={want['credited']} streak={want['streak']} "
                  f"state={want['state']}")
        else:
            print(f"      credited={got}  streak={rec['mastery']['streak']}  "
                  f"state={rec['mastery']['state']}")

    # 冷却天数读数
    rec = fresh()
    S.apply_attempt(rec, "correct", at=d(0))
    checks = [("刚重做完，冷却剩 7 天", d(0), 7),
              ("第 6 天，冷却剩 1 天", d(6), 1),
              ("第 7 天，冷却结束", d(7), 0)]
    for name, at, want in checks:
        got = S.cooldown_days_left(rec, S._as_dt(at))
        ok = got == want
        bad += 0 if ok else 1
        print(f"  {'✓' if ok else '✗'} {name}（得到 {got}）")

    # 错因写成了单个字符串，不该被拆成一个个字
    rec = fresh()
    S.apply_attempt(rec, "wrong", at=d(9), error_causes="概念不清")
    got = rec["attempts"][-1]["error_causes"]
    ok = got == ["概念不清"]
    bad += 0 if ok else 1
    print(f"  {'✓' if ok else '✗'} 单个错因字符串不会被拆成单字（得到 {got}）")

    print()
    print("状态机验收：" + ("全部通过。" if not bad else f"{bad} 项未通过。"))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
