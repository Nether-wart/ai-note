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
from .inbox import Inbox
from .publicbase import is_reachable_from_other_devices, public_base_warning
from .records import problem_detail, problem_record
from .warnings import index_warnings

# id 里不许出现 `..`：`/` 已经被挡在外面，所以穿越本来就做不到，这条是纵深防御
# （服务将来要经 Tailscale 暴露给手机，#13）。
ID_PATTERN = r"^(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9._-]*$"
# 页头那句话说「解答题／未审核／无标准答案」，就是这个顺序（契约 §6）。
_REASON_ORDER = ["solution_type", "no_standard_answer", "unreviewed"]

DEFAULT_PUBLIC_BASE = "http://127.0.0.1:8765"


def _skip(code: str, message: str, pid: str | None) -> dict:
    return {"code": code, "message": message, "id": pid}


# 「另有 N 道不能自动判定」与「另有 M 道缺擦除图」是**同一候选总体**上的两个数
# （编排裁决 D3/D4）。而候选总体取决于界面那个「显示冷却中的题」开关，所以两套
# 都算好、都摆在 `bases` 里，界面按开关**取**，不许自己重算（契约 §6.1 / §4）。
_SCREEN_REDO_BASES = {
    "in_default_list": "默认打印清单（未毕业且已脱离冷却）",
    "including_cooling": "未毕业（含冷却中，「显示冷却中的题」勾上时）",
}


def _population(records: list[dict], basis: str) -> list[dict]:
    if basis == "in_default_list":
        return [r for r in records if r["in_default_list"]]
    return [r for r in records if not r["graduated"]]


def _summarize_population(candidates: list[dict]) -> dict:
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


def screen_redo_summary(records: list[dict]) -> dict:
    """`screen_redo` 的索引级汇总。真源永远是每张卡自己的 `screen_redo.blockers`。

    `bases` 里的每个总体都带 `basis_text`——数字旁边必须有一句话说明它是**在哪一堆题**
    上数的，否则「另有 0 道」会让人以为整库都没问题。
    """
    return {
        "default_basis": "in_default_list",
        "bases": {
            name: {"basis_text": text, **_summarize_population(_population(records, name))}
            for name, text in _SCREEN_REDO_BASES.items()
        },
    }


class Catalog:
    """一个数据目录。**已有数据**只读：题卡／资产／索引都不写（ADR 0001）。

    #13 之后唯一的写入是「往收件目录放一个新文件」，那也不是改已有数据。
    """

    def __init__(self, data_dir: Path | str, clock=None,
                 public_base: str | None = None, *, inbox: Path | str | None = None,
                 bind_host: str | None = None) -> None:
        # 假时钟是一个**测试接缝**，不是内部 mock：冷却与排序的读数取决于「现在」，
        # 真等 7 天没法测（proto/test_mastery.py 是这份做法的先例）。
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        # 对外可达地址：手机要打开的链接、页锚点用它拼。**不许从 --host 推导**
        # （ADR 0007 第 5 条记着这处债），所以它必须能被显式给、且必须被暴露出来。
        self.public_base = public_base or DEFAULT_PUBLIC_BASE
        # 绑在哪个地址上，用来判断推导出来的对外地址手机是否真能打开。
        self.bind_host = bind_host
        self.root = Path(data_dir)
        self.problems_dir = self.root / "problems"
        self.assets_dir = self.root / "assets"
        # 页文件的落点：`<data>/pages/<hash12>.json`，与整页照片同目录并列（D5、契约 §10.2）。
        self.pages_dir = self.root / "pages"
        # 收件目录：录入的唯一入口（ADR 0007 第 4 条）。默认在数据目录下面，
        # 与「往 data/inbox/ 放一个文件」这句话对得上。
        self.inbox = Inbox(inbox if inbox is not None else self.root / "inbox")

    def public_url(self, path: str) -> str:
        """把服务内的路径拼成**手机真能打开**的绝对地址。

        页锚点、上传页链接都走这一个地方——只有一份拼法，就不会有一处忘了用对外地址。
        """
        return self.public_base + (path if path.startswith("/") else "/" + path)

    def config_warnings(self) -> list[dict]:
        """服务配置本身的会喊的检查（不只是题卡）。"""
        warning = public_base_warning(self.bind_host, self.public_base)
        return [warning] if warning else []

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
            if not isinstance(card.get("problem"), dict):
                # 「建不出这条记录」只剩这一档：连 `problem` 对象都没有（结构就是坏的）。
                # 而「有 problem、只是 transcript 还空着」**不再跳过**——那是 #15 入库
                # 建出来的骨架卡（题面要等审核时填），静默跳过它就是「入库了却看不见」。
                # 它由 `warnings.card_warnings` 的 `problem_transcript_missing` 喊出来。
                skipped.append(_skip(
                    "problem_missing_field",
                    f"{path} 缺 problem 对象（题面转录、题型、选项都在里面），建不出这条记录",
                    path.stem))
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
        warnings += self.config_warnings() + index_warnings(records) + per_card

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
            # 服务自述：手机该用哪个地址（ADR 0007 第 5 条）、上传页链接、收件目录在哪，
            # 以及「我没有改任何已有数据」（ADR 0007 第 6 条要的就是这句话）。
            # `upload_url` 与将来的页锚点走同一个 `public_url`：只有一份拼法。
            "server": {
                "public_base": self.public_base,
                "upload_url": self.public_url("/upload"),
                "reachable_from_other_devices": is_reachable_from_other_devices(self.public_base),
                "inbox": str(self.inbox.dir),
                "read_only": True,
                "read_only_note": "题卡／资产／索引只读；唯一的新写入是 POST /api/inbox "
                                  "往收件目录放**新**文件（#13），它不改任何已有数据",
            },
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
                hint="id 只允许字母、数字、点、下划线与连字符，必须以字母或数字开头，"
                     "且不许出现 '..'",
                param="pid",
                value=pid,
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
