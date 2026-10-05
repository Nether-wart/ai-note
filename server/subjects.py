"""受控词表：科目与考点大纲的**唯一**读取实现（契约 §3、§10.4）。

科目是导航的根（`CONTEXT.md`）。「有哪些科目」只能有一份来源：
`<数据目录>/vocab/subjects.json`——与 `error-causes.json` 同形。受控词表那条纪律在这里
同样成立（`CONTEXT.md`：AI 不得自造标签，找不到合适项只能**提名**，由人审核），
所以卡上的科目不在表里是**一条该喊的检查**，不是悄悄收下。

大纲挂在科目下：`<数据目录>/vocab/topic-outline.seed.json` 的形状是
`{"大纲": {<科目>: {<章>: {<节>: [<点>, …]}}}`。它是导航的第三层，**不是**构建期内容
（ADR 0009：索引栏在运行时，不在构建期）。

读不出来**不是 500**：它是一条该喊的检查——返回空词表 + 一条 `warning`。
一声不响地返回空科目表会让整个侧栏空掉，而那是这个项目最怕的静默。
"""

from __future__ import annotations

import json
from pathlib import Path

# `warnings._warn` 是级别的**唯一**构造函数（契约 §2：级别只有服务能定）。
# 这里 import 它而不是自己拼 dict——手搓 dict 漏了 `level`，下游只能靠猜。
# 反面那条 import 是**函数内**的（`warnings.card_warnings` 才需要本模块），
# 所以模块级没有环。
from .warnings import _warn

SUBJECTS_FILE = "subjects.json"
OUTLINE_FILE = "topic-outline.seed.json"


def vocab_dir(catalog) -> Path:
    return Path(catalog.root) / "vocab"


def _read_json(path: Path) -> tuple[object | None, str | None]:
    """读一个 JSON 文件：`(值, 读不出来的原话)`。读不出来的原话照原话带出去。"""
    try:
        return json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return None, None
    except Exception as exc:  # 读不了也要说清楚，不能装作词表是空的
        return None, f"{exc.__class__.__name__}: {exc}"


def _mtime(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return -1


def _subjects_from(raw: object) -> list[str] | None:
    """`{"科目": [...]}` 或裸数组都收——种子文件两种都出现过，不猜哪个是"对"的。"""
    values = raw.get("科目") if isinstance(raw, dict) else raw
    if not isinstance(values, list):
        return None
    out: list[str] = []
    for item in values:
        if isinstance(item, str) and item.strip() and item.strip() not in out:
            out.append(item.strip())
    return out


def _outline_from(raw: object) -> dict | None:
    """`{"大纲": {科目: {章: {节: [点]}}}}` → 那一棵字典树。形状不对就返回 `None`。"""
    tree = raw.get("大纲") if isinstance(raw, dict) else None
    if not isinstance(tree, dict):
        return None
    out: dict[str, dict] = {}
    for subject, chapters in tree.items():
        if not isinstance(subject, str) or not isinstance(chapters, dict):
            continue
        kept: dict[str, dict] = {}
        for chapter, sections in chapters.items():
            if not isinstance(sections, dict):
                continue
            kept[chapter] = {
                section: [point for point in (points or []) if isinstance(point, str)]
                for section, points in sections.items()
                if isinstance(points, list)
            }
        out[subject] = kept
    return out


def load(catalog) -> tuple[dict, list[dict]]:
    """读两份词表：`(vocabulary, warnings)`。

    `vocabulary` = `{"loaded": bool, "subjects": [...], "outline": {...}}`。
    `loaded` 是**卡级判据的前提**：词表没读出来的时候不许对每张卡喊
    `subject_unknown`——那会把一条真问题和一千条噪声一起发出去，
    而"warning 必须意味着有东西不对"（D3/D4 的口径）。

    **按 mtime 记住上一次的结果**：索引每个请求现算，而词表要随每一张卡的自检读一次
    （`warnings.card_warnings`）。不记的话一千张卡就是两千次读盘；记死的话人改完词表
    要重启服务才生效，那正是「悄悄用着旧值」。所以记的是 mtime：文件一动就重读。
    """
    paths = (vocab_dir(catalog) / SUBJECTS_FILE, vocab_dir(catalog) / OUTLINE_FILE)
    stamps = tuple(_mtime(path) for path in paths)
    memo = getattr(catalog, "_vocabulary_memo", None)
    if memo is not None and memo[0] == stamps:
        return memo[1], memo[2]

    warns: list[dict] = []

    raw_subjects, bad = _read_json(paths[0])
    subjects = None if bad else _subjects_from(raw_subjects)
    if bad:
        warns.append(_warn(
            "subjects_vocab_unreadable",
            f"科目词表读不了（{paths[0]}）：{bad} → 一个科目都没有，侧栏会空掉", None))
    elif subjects is None:
        warns.append(_warn(
            "subjects_vocab_missing",
            f"没有科目词表（{paths[0]}）→ 侧栏没有第一级。"
            f"形状：{{\"科目\": [\"数学\", …]}}", None))
    elif not subjects:
        warns.append(_warn(
            "subjects_vocab_empty",
            f"科目词表是空的（{paths[0]}）→ 侧栏没有第一级", None))
        subjects = []

    raw_outline, bad_outline = _read_json(paths[1])
    outline = None if bad_outline else _outline_from(raw_outline)
    if bad_outline:
        warns.append(_warn(
            "outline_vocab_unreadable",
            f"考点大纲读不了（{paths[1]}）：{bad_outline} → 考点大纲那一层空着", None))
    elif outline is None:
        warns.append(_warn(
            "outline_vocab_missing",
            f"没有考点大纲（{paths[1]}）→ 考点大纲那一层空着；考点仍可打在卡上",
            None, level="hint"))
        outline = {}

    known = set(subjects or [])
    for subject in outline:
        if subject not in known:
            warns.append(_warn(
                "outline_subject_unknown",
                f"大纲里的科目 {subject!r} 不在科目词表里 → 两份词表打架，"
                f"它不会出现在侧栏的科目下", None))

    vocabulary = {
        "loaded": subjects is not None and bool(subjects),
        "subjects": subjects or [],
        "outline": outline,
    }
    catalog._vocabulary_memo = (stamps, vocabulary, warns)
    return vocabulary, warns


def card_subject(card: dict) -> str | None:
    """卡上的科目。空串／空白／缺字段都算**未归类**（`None`），不当成空科目名。"""
    value = card.get("subject")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def card_warnings(card: dict, vocabulary: dict) -> list[dict]:
    """科目那两条（契约 §8）。

    - `subject_missing`：**`hint`**。未归类是**一等状态**（录入中途就是没有科目），
      与 `page_binding_missing` 同级：它必须在侧栏的「未归类」里看得见、道数写得出来，
      但它不是「有东西不对」。把预期状态报成 warning 会把它淹在噪声里。
    - `subject_unknown`：**`warning`**，且**只在词表真的读出来时**才报。级别是
      「有东西不对」：卡上的值不在受控词表里，读数是记录的原值，**不许静默改写成 null**
      ——谁改的谁喊。
    """
    pid = card.get("id")
    value = card_subject(card)
    if value is None:
        return [_warn("subject_missing",
                      "这张卡还没有科目（未归类）→ 它在侧栏的「未归类」下，"
                      "不在任何科目的简报里", pid, level="hint")]
    if vocabulary.get("loaded") and value not in vocabulary.get("subjects", []):
        return [_warn("subject_unknown",
                      f"科目 {value!r} 不在科目词表里 → 先加进词表，或改成表里的一个；"
                      f"读数保持原值，不替你改", pid)]
    return []


def _blank_counts() -> dict:
    return {
        "problems": 0,
        "in_default_list": 0,
        "cooling": 0,
        "graduated": 0,
        "auto_judge_eligible": 0,
        "unreviewed": 0,
    }


def rollup(records: list[dict], vocabulary: dict) -> dict:
    """按科目汇总读数：契约 §3 的 `stats.by_subject` 与 `stats.unclassified`。

    这个函数是**「一道题都不许少」那条不变式的实现处**：
    `sum(by_subject[*].problems) + unclassified == stats.problems`。

    所以桶的来源是**两处并集**，不是只有词表：
    1. 词表里的每个科目（哪怕 0 道——侧栏要能画出空科目，不然人以为它丢了）；
    2. 卡上出现过的每个科目（哪怕词表里没有——它已经带着 `subject_unknown` 喊过了，
       再把它从汇总里丢掉就是第二次静默，而且不变式当场就断）。
    """
    buckets: dict[str, dict] = {}
    for subject in vocabulary.get("subjects", []):
        buckets[subject] = _blank_counts()

    unclassified = 0
    for record in records:
        value = record.get("subject")
        if not value:
            unclassified += 1
            continue
        counts = buckets.setdefault(value, _blank_counts())
        counts["problems"] += 1
        if record.get("in_default_list"):
            counts["in_default_list"] += 1
        if record.get("cooling"):
            counts["cooling"] += 1
        if record.get("graduated"):
            counts["graduated"] += 1
        if (record.get("auto_judge") or {}).get("eligible"):
            counts["auto_judge_eligible"] += 1
        if record.get("review") != "reviewed":
            counts["unreviewed"] += 1

    return {"by_subject": buckets, "unclassified": unclassified}
