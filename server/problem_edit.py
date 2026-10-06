"""题卡属性的编辑（**唯一实现**，契约 §10.7）。

只改**属性**：`subject`／`topics`／`error_causes`／`review`。别的字段一律拒绝——
题面与答案是**审核**那条路的活，掌握与重做历史只能由重做/判定那条路改（它们带冷却与重放的
不变量）。属性编辑不许成为绕过它们的后门。

四条纪律：

1. **闭集**：不在可改闭集里的字段 → 拒绝并**点名**（`problem_field_not_editable` ＋ `allowed`）。
2. **幂等且明说**：值没变也 200，但 `changed: []`——"什么都没改"要说出来，不是含糊成功。
   而且**不写盘**（没变就没必要动那个文件，也就没有"改坏了"的机会）。
3. **词表两条口径**（与 §10.2.1b「建」同源）：词表**在**而取值不在 → 400 带 `allowed`；
   词表**本身不在** → 先收下并带回警告。录入摩擦是这类工具的头号死因。
4. **路径只由 pid 算**（`cardstore`）：内容永远不参与决定写到哪。
"""

from __future__ import annotations

import json
from pathlib import Path

from .cardstore import read_card, write_card

#: 可改闭集，**顺序就是回执里 `changed` 的顺序**（界面按它比对与重画）。
EDITABLE = ("subject", "topics", "error_causes", "review")

REVIEW_STATES = ("reviewed", "unreviewed")


class ProblemEditError(Exception):
    """一次编辑不收：`code` 走 §9 的 400 细因（`reason`）。"""

    def __init__(self, code: str, message: str, hint: str | None = None,
                 details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        self.details = details


def _vocab_error_causes(catalog) -> tuple[list | None, list]:
    """读 `<数据目录>/vocab/error-causes.json`。**不在**或读不出来 → `(None, [警告])`。

    口径与科目词表逐字一致：词表不在**不是**拒绝的理由（那是环境缺失，不是输入错），
    但一定要喊出来——悄悄按"没有词表"放行，等于把一个受控词表降级成自由文本而没人知道。
    """
    path = Path(catalog.root) / "vocab" / "error-causes.json"
    if not path.is_file():
        return None, [{
            "code": "error_causes_vocab_missing", "level": "warning", "id": None,
            "message": f"错因词表不在：{path}——这一次按自由文本收下，没有校验取值",
        }]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        values = data["错因"]
        if not isinstance(values, list):
            raise ValueError("「错因」不是列表")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return None, [{
            "code": "error_causes_vocab_unreadable", "level": "warning", "id": None,
            "message": f"错因词表读不出来：{path}（{exc}）——这一次按自由文本收下",
        }]
    return [str(value) for value in values], []


def _check_string_list(field: str, value) -> list:
    if not isinstance(value, list):
        raise ProblemEditError(
            "problem_field_not_editable",
            f"{field} 得是一个字符串列表，拿到的是 {type(value).__name__}",
            hint=f"给 `{field}: [\"…\"]`；要清空就给空列表",
            details={"field": field, "allowed": list(EDITABLE)},
        )
    cleaned = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ProblemEditError(
                "problem_field_not_editable",
                f"{field} 里有一项不是非空字符串：{item!r}",
                details={"field": field},
            )
        cleaned.append(item.strip())
    # 去重但**保序**：顺序是用户给的语义（先想到的排前面），不是随便排的集合
    return list(dict.fromkeys(cleaned))


def apply_edit(catalog, pid: str, payload: dict, *, clock):
    """改一张卡的属性 → `({"card": …, "changed": [...]}, warnings)`。

    卡的 id 不存在**不在这里判**：那由路由层给 404（这个模块只负责"这次编辑收不收"）。
    """
    if not isinstance(payload, dict) or not payload:
        raise ProblemEditError(
            "problem_field_not_editable",
            "这一次请求没有要改的字段",
            hint=f"至少要给一项：{', '.join(EDITABLE)}",
            details={"allowed": list(EDITABLE)},
        )

    for field in payload:
        if field not in EDITABLE:
            raise ProblemEditError(
                "problem_field_not_editable",
                f"{field} 不是可以改的属性",
                hint=("题面与答案是审核那条路的活，掌握与重做历史只能由重做/判定改；"
                      f"属性编辑只认：{', '.join(EDITABLE)}"),
                details={"field": field, "allowed": list(EDITABLE)},
            )

    card = read_card(catalog, pid)
    if card is None:
        return None, []

    warnings: list = []
    wanted: dict = {}

    if "subject" in payload:
        subject = payload["subject"]
        if subject is not None and not isinstance(subject, str):
            raise ProblemEditError("subject_unknown",
                                   f"subject 得是字符串或 null，拿到的是 {type(subject).__name__}",
                                   details={"allowed": []})
        if isinstance(subject, str):
            subject = subject.strip()
            from . import subjects as subjects_module
            vocabulary, vocab_warnings = subjects_module.load(catalog)
            known = list(vocabulary.get("subjects") or [])
            if vocabulary.get("loaded") and known and subject not in known:
                raise ProblemEditError(
                    "subject_unknown", f"科目 {subject!r} 不在受控词表里",
                    hint="先把它加进 <数据目录>/vocab/subjects.json；或给 null 表示未归类",
                    details={"value": subject, "allowed": known},
                )
            if not vocabulary.get("loaded"):
                warnings.extend(vocab_warnings)
        wanted["subject"] = subject or None

    if "topics" in payload:
        wanted["topics"] = _check_string_list("topics", payload["topics"])

    if "error_causes" in payload:
        causes = _check_string_list("error_causes", payload["error_causes"])
        allowed, vocab_warnings = _vocab_error_causes(catalog)
        warnings.extend(vocab_warnings)
        if allowed is not None:
            unknown = [cause for cause in causes if cause not in allowed]
            if unknown:
                raise ProblemEditError(
                    "error_cause_unknown",
                    f"错因不在受控词表里：{', '.join(unknown)}",
                    hint=f"可选：{'、'.join(allowed)}",
                    details={"unknown": unknown, "allowed": allowed},
                )
        wanted["error_causes"] = causes

    if "review" in payload:
        review = payload["review"]
        if review not in REVIEW_STATES:
            raise ProblemEditError(
                "problem_field_not_editable",
                f"review 只认 {', '.join(REVIEW_STATES)}，拿到的是 {review!r}",
                hint="卡上的 review 是一个对象；界面只给字符串，落地由服务负责",
                details={"field": "review", "allowed": list(REVIEW_STATES)},
            )
        wanted["review"] = review

    # 逐字段比：**没变就不算改**（幂等的那一半，也让"什么都没改"能被说出来）
    changed = []
    for field in EDITABLE:
        if field not in wanted:
            continue
        if field == "review":
            if (card.get("review") or {}).get("status") != wanted[field]:
                changed.append(field)
        elif (card.get(field) if card.get(field) is not None else None) != wanted[field]:
            changed.append(field)

    if changed:
        for field in changed:
            if field == "review":
                status = wanted[field]
                card["review"] = {
                    "status": status,
                    # 置回未审核时**不留**上一个时刻：留着会让人以为它这一刻被审过
                    "reviewed_at": clock().isoformat() if status == "reviewed" else None,
                }
            else:
                card[field] = wanted[field]
        write_card(catalog, pid, card)

    return {"card": card, "changed": changed}, warnings
