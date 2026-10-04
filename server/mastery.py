"""掌握与冷却：**读数 + 状态机的唯一一份实现**（契约 §4、§12）。

口径继承 `proto/slice.py` 与 `proto/test_mastery.py`（冻结的实测证据），代码不继承。
读（`cooldown_until` / `is_cooling` / `default_list_status` …）与写（`apply_attempt`）
都在这一处：两套规则分开写，迟早会各判各的。

两条最容易写错、原型里各踩过一次的地方：
  · 冷却的基准是「上次重做」，**从未重做过的以录入时间起算**（CONTEXT「默认打印清单」）；
  · 比较时刻必须**先归一化到 UTC**再比——卡里的 created_at 是 +08:00，重做时刻是 UTC，
    直接比字符串会把 06:31Z 排在 09:00Z 后面。

写那一半还多一条顺序硬规则：**冷却必须在更新 `last_attempt_at` 之前算**。顺序反了，
当天判对就会被算成脱离冷却、白拿一次掌握计数（`apply_attempt` 的 docstring 有详述）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

COOLDOWN_DAYS = 7  # 距上次重做不满 7 天为冷却
MASTERY_STREAK = 2  # 掌握 = 连续 2 次判对，且两次都已脱离冷却

TYPE_CN = {"choice": "选择", "fillin": "填空", "solution": "解答"}
MASTERY_CN = {"in_pool": "在池", "graduated": "毕业"}
VERDICT_CN = {"correct": "对", "wrong": "错", "unreadable": "看不清"}
VERDICTS = ("correct", "wrong", "unreadable")
CHANNEL_SCREEN = "screen"  # 屏幕重做
CHANNEL_PAPER = "paper"    # 纸上重做
CHANNELS = (CHANNEL_SCREEN, CHANNEL_PAPER)
SOURCE_AUTO = "auto"
SOURCE_HUMAN = "human"
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


# ------------------------------------------------------------------ 状态机的写

def apply_attempt(card: dict, verdict: str, *, confidence=None, source: str = SOURCE_AUTO,
                  error_causes=None, at=None, channel: str = CHANNEL_SCREEN,
                  judge_note: str | None = None, provider: str | None = None,
                  model: str | None = None) -> dict:
    """把一次重做写进题卡，并按既定规则更新掌握与冷却。**这套规则只有这一份实现。**

      · 判错 → 无条件清零并立刻回池（毕业取消）；冷却只挡「计入正确」，不挡这一条。
      · 判对 → 只有距上次重做 ≥ 冷却期（7 天）才计入连续正确；冷却期内的重做只是热身。
      · 看不清 → 记下这次重做，但既不推进也不清零（低置信度一律落向这里，绝不落向对）。
      · 连续 2 次**计入**的正确 → 掌握 → 毕业（退出默认打印清单）。

    三条容易写错的地方，前两条各有一条测试钉住：

      · **冷却必须在更新 `last_attempt_at` 之前算**。冷却基准是「上次重做／录入时间」，
        一旦先把 `last_attempt_at` 写成「现在」，再算冷却就永远落在冷却期里——
        于是当天判对也被算成热身/（反过来）白拿一次掌握计数。原型踩过这个坑。
      · `at` 必须可以外部给，且**先归一化到 UTC**：纸上重做是几天里做的，
        标记可能晚几天才做；卡里 `created_at` 是 `+08:00`、重做时刻是 UTC。
      · `error_causes` 单个字符串要包成单元素列表——`list("计算失误")` 会拆成单字。

    `judge_note` 是**判定那一步**的说明（为什么落成这个三值），与 `note`（掌握这一步的
    解释）分开记：界面读 `note`，审计读 `judge_note`（ADR 0007：不许静默）。

    返回本次重做之后的掌握读数（契约 §10.1 的 `mastery` 主体）。
    """
    if verdict not in VERDICTS:
        raise ValueError(f"判定取值非法：{verdict!r}（只能是 {' / '.join(VERDICTS)}）")
    if source not in SOURCE:
        raise ValueError(f"来源取值非法：{source!r}（只能是 {' / '.join(SOURCE)}）")
    if channel not in CHANNELS:
        raise ValueError(f"通道取值非法：{channel!r}（只能是 {' / '.join(CHANNELS)}）")

    m = card.setdefault("mastery", {"state": "in_pool", "streak": 0, "last_attempt_at": None})
    if at is None:
        moment = datetime.now(timezone.utc)
    elif isinstance(at, datetime):
        moment = at
    else:
        moment = parse_dt(at)
    if moment is None:
        raise ValueError(f"重做时刻解析不了：{at!r}（要 ISO 8601 或 datetime）")
    moment = moment.astimezone(timezone.utc)

    causes = [error_causes] if isinstance(error_causes, str) else list(error_causes or [])
    attempt = {
        "at": moment.isoformat(timespec="seconds"),   # 定点修正靠它定位，不靠「最近一次」
        "channel": channel,
        "verdict": verdict,
        "source": source,
        "confidence": confidence,
        "provider": provider,
        "model": model,
        "error_causes": causes,
        "note": None,
    }
    if judge_note:
        attempt["judge_note"] = judge_note
    card.setdefault("attempts", []).append(attempt)
    return step(card, m, attempt)


def step(card: dict, m: dict, attempt: dict) -> dict:
    """把**一次**重做按既定规则作用到掌握状态 `m` 上，返回这一次的读数。

    状态机的规则**只有这一处实现**：`apply_attempt`（追加一次新重做）与
    `recompute_mastery`（重放整段历史）都从它走。定点修正（#6）因此复用的是
    同一个冷却门，不可能和写路径各判各的。

    ⚠ 冷却的基准取的是**写之前**的状态：`m["last_attempt_at"]` 必须排在
    `cooling` 算完之后才更新（否则这一刻永远落在冷却里，见模块 docstring）。
    """
    moment = parse_dt(attempt.get("at"))
    if moment is None:
        raise ValueError(f"重做时刻解析不了：{attempt.get('at')!r}（要 ISO 8601）")
    moment = moment.astimezone(timezone.utc)
    verdict = attempt.get("verdict")
    if verdict not in VERDICTS:
        raise ValueError(f"重做记录里的判定取值非法：{verdict!r}")

    # ⚠ 这两行必须排在 `m["last_attempt_at"] = …` 前面（见 docstring）。
    prev_base = parse_dt(m.get("last_attempt_at")) or parse_dt(card.get("created_at"))
    cooling = bool(prev_base and moment < prev_base + timedelta(days=COOLDOWN_DAYS))
    # 同一天里「录入在下午、标记在当天」会让差值为负——读数该是 0 天，不是 -1 天
    gap_days = max(0, int((moment - prev_base).total_seconds() // 86400)) if prev_base else None

    # 时刻一律归一化到 UTC 再落库：定点修正靠它定位，写法必须唯一
    attempt["at"] = moment.isoformat(timespec="seconds")
    m["last_attempt_at"] = attempt["at"]

    base = {
        "verdict": verdict, "credited": False, "streak": int(m.get("streak") or 0),
        "state": m.get("state") or "in_pool", "cooling": cooling, "gap_days": gap_days,
        "confidence": attempt.get("confidence"), "source": attempt.get("source"), "note": None,
    }

    if verdict == "wrong":
        m["streak"] = 0
        m["state"] = "in_pool"
        m.pop("mastered_at", None)
        base.update(streak=0, state="in_pool",
                    note="判错：清零回池（毕业若存在则取消）")
    elif verdict == "correct":
        if cooling:
            base["note"] = (f"判对但仍在冷却期（距上次重做 {gap_days} 天）："
                            "只热身，不计入掌握")
        else:
            m["streak"] = int(m.get("streak") or 0) + 1
            base.update(credited=True, streak=m["streak"])
            if m["streak"] >= MASTERY_STREAK:
                m["state"] = "graduated"
                m["mastered_at"] = attempt["at"]
                base.update(state="graduated",
                            note="判对且脱离冷却：连续正确达标 → 掌握 → 毕业，"
                                 "退出默认打印清单")
            else:
                base["note"] = f"判对且脱离冷却：连续正确 {m['streak']}/{MASTERY_STREAK}"
    else:
        base["note"] = "看不清：已记录这次重做，但既不推进也不清零"

    # 一次重做只有一句给界面看的原话（契约 §10.1：attempt.note 与 mastery.note 是同一句）
    attempt["note"] = base["note"]
    return base


def recompute_mastery(card: dict) -> list[dict]:
    """把卡里的 `attempts` **从头重放一遍**重算掌握，返回每一步的读数。

    定点修正（#6）改的是历史里**某一次**，甚至可能不是最后一次，于是掌握与冷却
    只能重算：状态机对「`created_at` + 一串 (at, verdict)」是一个确定的纯函数
    （冷却基准是上一次重做／录入时间），所以重放得到的读数**就是**「这条判定
    从一开始就是这样」时该有的读数。就地打补丁在「改的不是最后一次」时会得出
    自相矛盾的读数——后面几次的 `credited` 还是按旧判定算的。

    重放会覆盖 `mastery` 与各次记录的 `note`：两者都是派生数据（ADR 0001）。
    """
    card["mastery"] = {"state": "in_pool", "streak": 0, "last_attempt_at": None}
    m = card["mastery"]
    return [step(card, m, attempt) for attempt in (card.get("attempts") or [])]
