"""数据目录的只读访问：题卡文件 → 派生索引。

索引是派生数据（ADR 0001）：任何时刻都能从题目文件重建，所以 v0 每次请求**现算**，
不落盘、也不会陈旧。写盘是写端点的事（#5 之后）。
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .autojudge import REASONS
from .errors import ApiError, bad_request, not_found
from .records import problem_detail, problem_record
from .warnings import index_warnings

# id 里不许出现 `..`：`/` 已经被挡在外面，所以穿越本来就做不到，这条是纵深防御
# （服务将来要经 Tailscale 暴露给手机，#13）。
ID_PATTERN = r"^(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9._-]*$"
# 页头那句话说「解答题／未审核／无标准答案」，就是这个顺序（契约 §6）。
_REASON_ORDER = ["solution_type", "no_standard_answer", "unreviewed"]


def _skip(code: str, message: str, pid: str | None) -> dict:
    return {"code": code, "message": message, "id": pid}


# 「另有 N 道不能自动判定」与「另有 M 道缺擦除图」是**同一候选总体**上的两个数
# （编排裁决 D3/D4）。候选总体 = 默认打印清单：一张冷却中的解答题本来就不在队列里，
# 它不是「因为不能判定才没进」，算进来会让页头那句话撒谎。
_SCREEN_REDO_BASIS = "in_default_list"


def screen_redo_summary(records: list[dict]) -> dict:
    """`screen_redo` 的索引级汇总。真源永远是每张卡自己的 `screen_redo.blockers`。"""
    candidates = [r for r in records if r["in_default_list"]]
    by_reason: dict[str, list[str]] = {}
    no_clean: list[str] = []
    for rec in candidates:
        for blocker in rec["screen_redo"]["blockers"]:
            if blocker == "no_clean_image":
                no_clean.append(rec["id"])
            else:
                by_reason.setdefault(blocker, []).append(rec["id"])

    ineligible = sorted(by_reason.items(), key=lambda kv: _REASON_ORDER.index(kv[0]))
    blocked = [r for r in candidates if r["screen_redo"]["blockers"]]
    return {
        "basis": _SCREEN_REDO_BASIS,
        "ready": len(candidates) - len(blocked),
        "blocked_total": len(blocked),
        "not_auto_judgeable": {
            "count": sum(len(ids) for _, ids in ineligible),
            "by_reason": [
                {"reason": reason, "reason_text": REASONS[reason], "count": len(ids), "ids": ids}
                for reason, ids in ineligible
            ],
        },
        "no_clean_image": {
            "count": len(no_clean),
            "ids": no_clean,
            "message": f"另有 {len(no_clean)} 道因缺少擦除手写后的题面图不能进屏幕重做",
        },
    }


class Catalog:
    """一个数据目录。它只读——v0 没有任何写路径。"""

    def __init__(self, data_dir: Path | str, clock=None) -> None:
        # 假时钟是一个**测试接缝**，不是内部 mock：冷却与排序的读数取决于「现在」，
        # 真等 7 天没法测（proto/test_mastery.py 是这份做法的先例）。
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.root = Path(data_dir)
        self.problems_dir = self.root / "problems"
        self.assets_dir = self.root / "assets"

    # ---------------------------------------------------------------- 索引

    def index(self) -> tuple[dict, list[dict], list[dict]]:
        """派生索引：`(data, warnings, skipped)`，正好对上契约 §2 的信封。

        形状见契约 §3。`data["warnings"]` 与返回的 `warnings` 是同一份内容，
        两个位置都有，是为了让「按端点取警告」和「按信封统一取警告」两种写法都对。
        """
        at = self.clock()
        warnings: list[dict] = []
        skipped: list[dict] = []
        cards: list[dict] = []

        for path in sorted(self.problems_dir.glob("*.json")):
            try:
                card = json.loads(path.read_text(encoding="utf-8"))
            except Exception as exc:  # 读不了也要说清楚，不能少一张卡还装作没事
                skipped.append(
                    {
                        "code": "problem_file_unreadable",
                        "message": f"{path} 读不了：{exc.__class__.__name__}: {exc}",
                        "id": path.stem,
                    }
                )
                continue
            if not isinstance(card, dict):
                skipped.append(_skip("problem_not_dict", f"{path} 不是一个 JSON 对象", path.stem))
                continue
            if not (card.get("problem") or {}).get("transcript"):
                skipped.append(_skip("problem_missing_field",
                                     f"{path} 缺 problem.transcript，建不出这条记录", path.stem))
                continue
            if path.stem != card.get("id"):
                warnings.append(
                    {
                        "code": "problem_id_mismatch",
                        "message": f"{path.name} 里的 id 是 {card.get('id')!r}，与文件名不一致"
                                   f"→ 改名或复制粘贴事故",
                        "id": card.get("id"),
                    }
                )
            cards.append(card)

        # 默认打印清单的顺序：按上次重做（从未重做过的以录入时间起算）从早到晚。
        # 界面拿这个顺序原样渲染，不重新排——排序规则只有这一份实现（契约 §4）。
        records = [problem_record(card, self, at) for card in cards]
        records.sort(key=lambda rec: rec["sort_key"])

        per_card = [w for rec in records for w in rec["warnings"]]
        warnings += index_warnings(records) + per_card

        stats = {
            "problems": len(records),
            "problems_skipped": len(skipped),
            "in_default_list": sum(1 for r in records if r["in_default_list"]),
            "cooling": sum(1 for r in records if r["cooling"]),
            "graduated": sum(1 for r in records if r["graduated"]),
            "auto_judge_eligible": sum(1 for r in records if r["auto_judge"]["eligible"]),
            "auto_judge_ineligible": sum(1 for r in records if not r["auto_judge"]["eligible"]),
        }
        data = {
            "built_at": at.isoformat(timespec="seconds"),
            "count": len(records),
            "problems": records,
            "stats": stats,
            "screen_redo": screen_redo_summary(records),
            "warnings": warnings,
        }
        return data, warnings, skipped

    # ---------------------------------------------------------------- 一题

    def load_card(self, pid: str) -> dict:
        """按 id 取题卡。id 不合法 → 400（不是 404：这是输入错，不是找不到）。

        id 只接受 `^[A-Za-z0-9][A-Za-z0-9._-]*$`，含 `/`、`..`、空都拒掉——
        服务将来要经 Tailscale 暴露给手机（#13），这条不是形式主义。
        """
        if not re.fullmatch(ID_PATTERN, pid or ""):
            raise bad_request(
                f"题卡 id 非法：{pid!r}",
                param="pid",
                value=pid,
                reason="id 只允许字母、数字、点、下划线与连字符，必须以字母或数字开头，且不许出现 '..'",
            )
        path = self.problems_dir / f"{pid}.json"
        if not path.is_file():
            raise not_found(
                f"没有这道题：{pid}", hint="GET /api/index 看有哪些题", what="problem", id=pid
            )
        try:
            card = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ApiError(
                500,
                "internal_error",
                f"题卡 {path.name} 读不了：{exc.__class__.__name__}: {exc}",
                hint="索引会把它记进 skipped；先修好这个文件",
                details={"what": "problem", "id": pid},
            ) from exc
        return card

    def problem_detail(self, pid: str, at: datetime | None = None) -> dict:
        """契约 §5：索引条目的超集 + 四个详情专属字段。"""
        return problem_detail(self.load_card(pid), self, at or self.clock())
