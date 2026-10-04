"""定点修正：**改错因 / 当场改判**（#6，契约 §10.1 的第三种形态）。

`POST /api/attempt/<pid>`，body `{"attempt_at": "<那一次重做的时刻>", "error_causes"?, "verdict"?}`。

三条硬规则写死在这里：

  · **只改既有那一次重做**：不新建记录、不重跑判定、不调模型（`run_id` 恒为 `null`）。
    用 `attempt_at` 定位而不是「最近一次」——两次提交之间「最近一次」会漂移。
    定位不到就**明确失败**，绝不落到最近一次（验收 4）。
  · **判定与错因只能由这一个形态改**，别的一律 400：`source`／`confidence`／`provider`／
    `model`／`overrode` 是服务写给人看的审计字段，客户端不许碰（#5 立的那条硬规则）。
  · **掌握只能重算**（`mastery.recompute_mastery`）：改的是历史里某一次时，冷却门
    必须和写路径是同一个（`mastery.step`），先算门、后写 `last_attempt_at`。

`verdict` 与 `error_causes` 都不给＝什么都没得改，门口就 400——静默成功是「不许静默」
最讨厌的那种做法。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable

from . import mastery, warnings as warnings_mod
from .errors import ApiError, bad_request

# 这一种形态**只收这三个键**（契约 §10.1）。多一个键就 400：静默忽略等于没听见。
AMEND_FORM_KEYS = frozenset({"attempt_at", "error_causes", "verdict"})
# 这些字段是**服务**写给审计看的，客户端提交一律 400。`verdict` 与 `error_causes`
# 恰恰是定点修正**允许**改的两个字段，所以不在这张表里。
NOT_CLIENT_WRITABLE = ("source", "confidence", "provider", "model", "overrode",
                       "evidence_image", "channel", "at", "answer", "judge_note")
# 改判后 `overrode` 里保留的原判定。前三个键照契约 §10.1 的例子；`provider`/`model`
# 是超集，否则「原本是哪台机器误判的」会永久丢失（spec #1 US 28）。
_OVERRIDDEN_KEYS = ("verdict", "source", "confidence", "provider", "model")


class AmendEndpoint:
    """一次定点修正的完整处理。时钟是构造函数注入的接缝（与写路径同一个）。"""

    def __init__(self, catalog, *, clock: Callable) -> None:
        self.catalog = catalog
        self.clock = clock

    def handle(self, pid: str, body: dict) -> tuple[dict, list[dict]]:
        """返回 `(data, warnings)`；失败一律抛 `ApiError`（HTTP 层收进信封）。"""
        card = self.catalog.load_card(pid)  # id 非法 → 400；没有这张卡 → 404
        card_warnings = warnings_mod.card_warnings(card, self.catalog)
        fields = _amend_form(body)
        attempts = card.get("attempts") or []
        index = _locate(attempts, fields["attempt_at"], pid, warnings=card_warnings)
        attempt = attempts[index]

        before = _snapshot(card)
        if "verdict" in fields:
            _override(attempt, fields["verdict"])
        if "error_causes" in fields:
            attempt["error_causes"] = fields["error_causes"]

        steps = mastery.recompute_mastery(card)
        # 幂等（验收 3）：一个字都没变就不碰盘。第二次提交同一 payload 时，
        # 重算的读数与 `overrode` 都与第一次相同，于是这里连写都不写。
        if _snapshot(card) != before:
            self._write_card(pid, card)

        # 「索引重建」在 v0 是立刻读一次现算索引（ADR 0001），与写路径同一条路。
        index_data, _, _ = self.catalog.index()
        step = steps[index]
        data = {
            "attempt": attempt,
            # state/streak/last_attempt_at 是**重算后的卡级终态**（与索引一致）；
            # cooling/credited/gap_days/note 是**被改那一次**在重放里的读数。
            # 改的是最后一次时两者重合；不是最后一次时这样分层（见 issue #6 评论）。
            "mastery": {
                "state": card["mastery"].get("state"),
                "streak": card["mastery"].get("streak"),
                "last_attempt_at": card["mastery"].get("last_attempt_at"),
                "cooling": step["cooling"],
                "credited": step["credited"],
                "gap_days": step["gap_days"],
                "note": step["note"],
            },
            "run_id": None,  # 人工修正不调模型、不留档（契约 §10.1）
            "index_rebuilt_at": index_data["built_at"],
        }
        return data, card_warnings

    # ---------------------------------------------------------------- 回写

    def _write_card(self, pid: str, card: dict) -> None:
        """原子地写回**读进来的那个文件**（按 pid，不按卡里的 id）。"""
        path = self.catalog.problems_dir / f"{pid}.json"
        tmp = self.catalog.problems_dir / f"{pid}.json.tmp"
        tmp.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)


# -------------------------------------------------------------------- 改判

def _override(attempt: dict, verdict: str) -> None:
    """把一次重做的判定改成人给的判定，并把**原判定**留在 `overrode` 上。

    `overrode` 只记**最初**那一次原判定：第二次改判不覆盖它，重复提交同一 payload
    也不改动任何东西（幂等，验收 3）。只改错因时不走这里（验收 2）。
    """
    if verdict == attempt.get("verdict"):
        return  # 已经是这个判定：连来源都不动（幂等）
    if "overrode" not in attempt:
        attempt["overrode"] = {key: attempt.get(key) for key in _OVERRIDDEN_KEYS}
    attempt["verdict"] = verdict
    attempt["source"] = mastery.SOURCE_HUMAN
    # 契约：`confidence` 是「模型给的把握」，人工确认时为 null；
    # `provider`/`model` 是「谁判的」，改判后判的是人（原机器身份留在 overrode 里）。
    attempt["confidence"] = None
    attempt["provider"] = None
    attempt["model"] = None


def _snapshot(card: dict) -> str:
    """卡的规范化快照，只用来判「这次提交到底改没改东西」（幂等验收 3）。"""
    return json.dumps(card, ensure_ascii=False, sort_keys=True)


# -------------------------------------------------------------------- 定位

def _locate(attempts: list, raw: str, pid: str, *, warnings: list) -> int:
    """按时刻找到**那一次**重做。0 个匹配 → 404；≥2 个 → 409。**绝不猜。**"""
    moment = mastery.parse_dt(raw)
    if moment is None:
        raise bad_request(
            f"attempt_at 不是一个 ISO 8601 时刻：{raw!r}",
            hint="把那次重做的 attempt.at 原样送回来，例如 2026-10-04T09:39:57+00:00；"
                 "不接受「最近一次」「今天」这类说法",
            param="attempt_at", value=raw, allowed="ISO 8601 时刻字符串",
        )

    available = [a.get("at") for a in attempts if isinstance(a, dict)]
    hits = [i for i, a in enumerate(attempts)
            if isinstance(a, dict) and mastery.parse_dt(a.get("at")) == moment]
    if not hits:
        raise ApiError(
            404, "not_found",
            f"这道题的重做历史里没有 attempt_at = {raw!r} 那一次重做",
            reason="attempt_not_found",
            hint=("这几次重做的时刻：" + " / ".join(map(str, available))) if available
                 else "这道题还没有任何重做记录",
            details={"id": pid, "attempt_at": raw, "available": available},
            warnings=warnings,
        )
    if len(hits) > 1:
        # 同一秒里有两次重做：`attempt_at` 定位不了唯一一次。挑一个就是「猜」。
        raise ApiError(
            409, "ambiguous_attempt_at",
            f"attempt_at = {raw!r} 对应 {len(hits)} 次重做（同一秒里做了两次），"
            "定位不到唯一一次",
            reason="ambiguous_attempt_at",
            hint="这两次重做的时刻逐字相同，定点修正无法区分它们；先修数据或多给一位精度",
            details={"id": pid, "attempt_at": raw, "candidates": hits},
            warnings=warnings,
        )
    return hits[0]


# -------------------------------------------------------------------- 输入校验

def _amend_form(body: dict) -> dict:
    """校验定点修正形态，返回 `{attempt_at, verdict?, error_causes?}`。"""
    unknown = sorted(set(body) - AMEND_FORM_KEYS)
    if unknown:
        leak = [key for key in unknown if key in NOT_CLIENT_WRITABLE]
        if leak:
            raise bad_request(
                "这些字段只有服务能写，客户端不许提交：" + " / ".join(leak),
                hint="定点修正只提交 {attempt_at, error_causes?, verdict?}",
                param="body", forbidden=leak, unknown=unknown,
            )
        raise bad_request(
            "定点修正只收 attempt_at 与 error_causes / verdict，多了：" + " / ".join(unknown),
            hint="定点修正只提交 {attempt_at, error_causes?, verdict?}",
            param="body", unknown=unknown,
        )

    if "verdict" not in body and "error_causes" not in body:
        raise bad_request(
            "定点修正至少要给 error_causes 或 verdict 之一，否则什么都没得改",
            hint="补记错因给 error_causes，改判给 verdict；两个都给也行",
            param="body", allowed=["error_causes", "verdict"],
        )

    fields: dict = {"attempt_at": body.get("attempt_at")}
    if not isinstance(fields["attempt_at"], str) or not fields["attempt_at"].strip():
        raise bad_request(
            "attempt_at 不能为空：定点修正靠它定位那一次重做",
            hint="它的值就是那次重做记录里的 attempt.at",
            param="attempt_at", value=fields["attempt_at"], allowed="ISO 8601 时刻字符串",
        )

    if "verdict" in body:
        verdict = body["verdict"]
        if verdict not in mastery.VERDICTS:
            raise bad_request(
                f"判定取值非法：{verdict!r}",
                hint="判定只有三个取值；改判就是把机器给的那个换成这三个里的一个",
                param="verdict", value=verdict, allowed=list(mastery.VERDICTS),
            )
        fields["verdict"] = verdict

    if "error_causes" in body:
        fields["error_causes"] = _error_causes(body["error_causes"])

    return fields


def _error_causes(value) -> list[str]:
    """错因：单个字符串包成单元素列表（**不许 `list("计算失误")` 拆成单字**），
    列表则逐个校验。空列表合法（＝把错因清掉）。"""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        raise bad_request(
            f"error_causes 要一个字符串或字符串列表，拿到的是 {type(value).__name__}",
            hint='例如 ["计算失误"] 或 "计算失误"',
            param="error_causes", value=value, allowed=["字符串", "字符串列表"],
        )
    for cause in value:
        if not isinstance(cause, str) or not cause.strip():
            raise bad_request(
                f"error_causes 里有一个不是非空字符串：{cause!r}",
                hint="每个错因都是一个非空字符串；要清空就给空列表 []",
                param="error_causes", value=cause, allowed="非空字符串",
            )
    return list(value)
