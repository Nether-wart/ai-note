"""简报：生成、**数字闸门**、落盘（契约 §10.5）。

`CONTEXT.md` 的「简报」：就某个科目近期状态写出的一份文字总结（错因分布、最薄弱的考点），
**由模型写**，因此要过验收才上岗（#17 §7）。这个模块管三件事：

  1. **汇总候选读数 → 问 `brief` 角色**（调用接缝在 `server/brief_client.py`，一律**注入**）；
  2. **数字闸门**：正文用到的每一个数字都要能在**本次索引**里逐字找回——解不出来或对不上
     就**一个字节都不落盘**，报 **502 `brief_unverifiable`**（`verify_facts` 是纯函数，
     没有模型、没有网络也能测）；
  3. **落盘** `<数据目录>/briefs/<科目>-<YYYY-MM-DD>.json`（一天一份，保留历史）。

## 为什么必须是一道硬闸门

这个项目最怕的失败是一段读起来很顺、**数字却是编的**总结。能**自动判**的只有「数字对不对」
——「这段总结说得好不好」只由人读，当线索。所以闸门只管数字，而且**管死**：`path` 解不出来
是**对不上**、不是跳过；值对上了但**种类**不对（字符串 `"2"` 冒充数字 `2`）也是**对不上**。

**它与「模型没问成」处置完全不同**（这一条最容易被写混）：

  · **模型没问成**（网络／超时／缺密钥／上游 5xx）→ `ModelUnavailable` → 502
    `model_unavailable`，**可以重试**，因为下一次可能就通了；
  · **答了话但数字是编的** → 502 `brief_unverifiable`，**重试无用**——同一份提示词、
    同一个模型，再问一次不会让编出来的数字变真。要么改提示词，要么换模型
    （换模型就要重跑简报角色的验收）。

把两者混成一句「可以重试」会把人引去重试一个不会变好的东西。所以这条界线在代码里也分开：
前者的异常**原样往上抛**，后者只在 `generate` 里由闸门造出来
（`errors.brief_unverifiable` 的 docstring 记着同一条理由）。

## `facts[].path` 的路径语法——只有这一份实现

    path     := segment ("." segment)*
    segment  := key index*
    index    := "[" 十进制非负整数 "]"
    key      := 不含 "."、"["、"]" 的字符串（中文键如「数学」**逐字**匹配，不做转义）

例：`stats.by_subject.数学.cooling`、`stats.unclassified`、`problems[3].mastery_cn`、
`problems[0].error_causes`。

  · 每一段先按 `key` 在**对象**里取成员，再按 `[n]` 依次在**数组**里取下标；
    类型不对（对数组取键、对对象取下标）与下标越界都算**解不出来**。
  · **解不出来不是跳过**：那一条照样是「对不上」，`actual` 记 `null`、`reason` 记
    `path_unresolved`——「解不出来」与「解出来正好是 `null`」是两档，必须分得开，
    所以每条对不上除了契约那四个键（`label`/`path`/`claimed`/`actual`）还带一个 `reason`。
  · 键里含 `.`／`[`／`]` 的字段**寻址不了**——这是这套语法的诚实边界（本索引没有这样的键）。
  · 本语法**没有**任何过滤／聚合（没有「最近 N 天的平均数」这种东西）：它只能在索引里
    **取一个已经存在的值**。所以正文里能过闸门的数字，只能是索引里现成的读数。

## 未归类必须**说出口**

没有科目的题（`subject` 是 `null`）不属于任何科目的简报，但它也**不许被静默排除**：
正文必须写「另有 N 道未归类未计入」，而且 N 必须在 `history_facts` 里、过**同一道**闸门。
一个安静地把一堆题排除在外的汇总，正是这个项目反复被咬的那类失败。

## 过期

`stale` 的判据只有一条：**该科目**里 `created_at` 晚于 `brief["covers_until"]` 的道数
（索引每个请求现算，`covers_until` 就是它读的那份快照的 `built_at`——没有这个固定时刻，
「过期」无从判起）。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import errors
from .mastery import parse_dt

BRIEFS_DIR = "briefs"
DEFAULT_WINDOW_DAYS = 7

UNCLASSIFIED_PATH = "stats.unclassified"
UNCLASSIFIED_LABEL = "未归类未计入"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 一段：键（不含 . [ ]）+ 若干 [n]
_SEGMENT_RE = re.compile(r"^([^.\[\]]+)((?:\[\d+\])*)$")
_INDEX_RE = re.compile(r"\[(\d+)\]")

# 科目级候选读数：全部历史（`stats.by_subject.<科目>.*`）。
_SUBJECT_FIELDS = (
    ("problems", "全部题数"),
    ("in_default_list", "默认打印清单里的道数"),
    ("cooling", "冷却中的道数"),
    ("graduated", "已毕业的道数"),
    ("auto_judge_eligible", "可自动判定的道数"),
    ("unreviewed", "未审核的道数"),
)
# 窗口里每道题各自的候选读数。**必须逐个都是索引里真有的键**——
# 候选里给一条解不出来的 path，等于递一把注定对不上的枪给模型。
_PROBLEM_FIELDS = (
    ("attempts", "重做次数"),
    ("cooling", "是否在冷却"),
    ("cooldown_days", "冷却还剩几天"),
    ("mastery_cn", "掌握读数"),
    ("last_verdict_cn", "最近一次判定"),
    ("error_causes", "错因"),
    ("topics", "考点"),
)


# ------------------------------------------------------------------ 路径语法


def resolve_path(path, index) -> tuple[bool, object]:
    """把 `path` 按**模块 docstring 里的那套语法**从索引里解出来：`(解出来了吗, 值)`。

    解不出来一律 `(False, None)`——调用方**不许**把它当成「值是 null」（两档必须分开）。
    """
    if not isinstance(path, str) or not path:
        return False, None
    node: object = index
    for segment in path.split("."):
        match = _SEGMENT_RE.match(segment)
        if match is None:
            return False, None
        key, indices = match.group(1), match.group(2)
        if not isinstance(node, dict) or key not in node:
            return False, None
        node = node[key]
        for raw in _INDEX_RE.findall(indices):
            position = int(raw)
            if not isinstance(node, list) or position >= len(node):
                return False, None
            node = node[position]
    return True, node


def _kind(value) -> str:
    """JSON 值的**种类**。布尔**不**算数字（Python 里 `True == 1`，不先分种类就会漏一档）。"""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _same_value(claimed, actual) -> bool:
    """**种类**先相等，再看值：数字按数值比（`2` 与 `2.0` 相等），字符串逐字比。

    数组／对象按 JSON 深度相等比。先分种类的必要性见 `_kind`。
    """
    kind = _kind(claimed)
    if kind != _kind(actual):
        return False
    if kind == "number":
        # NaN 与任何值都不相等（包括它自己）：它永远是一条「对不上」，正合期望
        return float(claimed) == float(actual)
    return claimed == actual


def _check_fact(fact, index) -> dict | None:
    """核对一条：对不上就给一条 `{label, path, claimed, actual, reason}`，对上了返回 `None`。"""
    if not isinstance(fact, dict):
        return {"label": None, "path": None, "claimed": fact, "actual": None,
                "reason": "bad_fact"}
    label, path = fact.get("label"), fact.get("path")
    claimed = fact.get("value")
    if "value" not in fact:
        # 没有「声称的值」就没有可比的东西——那不是一条可核对的事实
        return {"label": label, "path": path, "claimed": None, "actual": None,
                "reason": "missing_value"}
    found, actual = resolve_path(path, index)
    if not found:
        return {"label": label, "path": path, "claimed": claimed, "actual": None,
                "reason": "path_unresolved"}
    if not _same_value(claimed, actual):
        return {"label": label, "path": path, "claimed": claimed, "actual": actual,
                "reason": "kind_mismatch" if _kind(claimed) != _kind(actual)
                          else "value_mismatch"}
    return None


def verify_facts(facts, index) -> dict:
    """数字闸门（**纯函数**：不碰模型、不碰网络、不碰盘）：`{"ok", "mismatches"}`。

    每一条对不上写成 `{label, path, claimed, actual, reason}`：

    | `reason` | 什么时候 |
    |---|---|
    | `bad_fact` | 这一条根本不是 `{label, path, value}` 形状的对象 |
    | `missing_value` | 没有 `value` 键——没有「声称的值」可比 |
    | `path_unresolved` | `path` 在本次索引里**解不出来**（`actual` 恒为 `null`） |
    | `kind_mismatch` | 解出来了，但**种类**不同（字符串 `"1"` vs 数字 `1`） |
    | `value_mismatch` | 解出来了、种类相同，值不相等 |
    """
    mismatches: list[dict] = []
    for fact in facts or []:
        mismatch = _check_fact(fact, index)
        if mismatch is not None:
            mismatches.append(mismatch)
    return {"ok": not mismatches, "mismatches": mismatches}


def _unclassified_missing(history_facts, index) -> dict | None:
    """未归类那条**必须在场**：没有它就拒（「另有 N 道未归类未计入」是硬要求）。

    只认**路径逐字等于** `stats.unclassified` 的那一条；它的值照样要过 `verify_facts`，
    所以「N」不可能是一个编出来的数。少了它，这份简报就是在把一堆题**安静地**排除在外。
    """
    for fact in history_facts or []:
        if isinstance(fact, dict) and fact.get("path") == UNCLASSIFIED_PATH:
            return None
    _, actual = resolve_path(UNCLASSIFIED_PATH, index)
    return {"label": UNCLASSIFIED_LABEL, "path": UNCLASSIFIED_PATH, "claimed": None,
            "actual": actual, "reason": "missing_fact"}


# ------------------------------------------------------------------ 候选读数


def _window(built: datetime, window_days: int) -> tuple[str, str]:
    """`(window_until, window_from)`，两个**日期**。

    `window_until` 是索引快照那一天；`window_from` 是**含端点**的 `window_days` 天起点
    （7 天 = 从 09-28 到 10-04，契约 §10.5 的例子就是这个数法）。一律先归一化到 UTC
    再取日期——卡里的 `created_at` 常是 `+08:00`（契约 §1）。
    """
    until = built.astimezone(timezone.utc).date()
    return until.isoformat(), (until - timedelta(days=window_days - 1)).isoformat()


def _history_candidates(index, subject) -> list[dict]:
    """全部历史的候选读数：科目级六个读数 + 未归类那一个。"""
    stats = index.get("stats") if isinstance(index, dict) else None
    stats = stats if isinstance(stats, dict) else {}
    by_subject = stats.get("by_subject")
    bucket = by_subject.get(subject) if isinstance(by_subject, dict) else None
    bucket = bucket if isinstance(bucket, dict) else {}
    facts = [
        {"label": f"{subject}{label}", "path": f"stats.by_subject.{subject}.{field}",
         "value": bucket.get(field)}
        for field, label in _SUBJECT_FIELDS
    ]
    facts.append({"label": UNCLASSIFIED_LABEL, "path": UNCLASSIFIED_PATH,
                  "value": stats.get("unclassified")})
    return facts


def _window_candidates(problems: list, subject, window_from: str) -> list[dict]:
    """最近 `window_days` 天里**录入**的那几道题，各自可摘引的读数。

    窗口按**时刻**判、归一化到 UTC 后取日期（不是字符串前缀比）——`window_from` 是日期，
    `created_at` 是带时区的时刻。
    """
    facts: list[dict] = []
    for position, record in enumerate(problems):
        if not isinstance(record, dict) or record.get("subject") != subject:
            continue
        created = parse_dt(record.get("created_at"))
        if created is None:
            continue
        if created.astimezone(timezone.utc).date().isoformat() < window_from:
            continue
        name = record.get("id") or f"第 {position} 条"
        for field, label in _PROBLEM_FIELDS:
            facts.append({"label": f"{name} 的{label}",
                          "path": f"problems[{position}].{field}",
                          "value": record.get(field)})
    return facts


def brief_digest(index, subject, *, window_days: int = DEFAULT_WINDOW_DAYS) -> dict:
    """本次索引 → 喂给模型的**候选读数**（两种窗口并列，路径都指向本次索引）。

    它只是候选：模型可以从里面挑、抄进 `window_facts`／`history_facts`。真正把关的是
    `verify_facts`——候选之外的一条路径，只要解得出、值对得上，也照收（闸门只管数字真不真，
    不管它是从哪儿挑的）。
    """
    built = parse_dt(index.get("built_at")) if isinstance(index, dict) else None
    if built is None:
        raise ValueError("索引里没有可解析的 built_at——简报的 covers_until 必须指着一次索引快照")
    window_until, window_from = _window(built, window_days)
    problems = index.get("problems") if isinstance(index, dict) else None
    problems = problems if isinstance(problems, list) else []
    return {
        "subject": subject,
        "window_days": window_days,
        "window_from": window_from,
        "window_until": window_until,
        "window_facts": _window_candidates(problems, subject, window_from),
        "history_facts": _history_candidates(index, subject),
    }


# ------------------------------------------------------------------ 生成


def _read_answer(answer) -> tuple[str, list, list, str | None]:
    """一次回答 → `(正文, window_facts, history_facts, 不可用的原话)`。

    形状不对（不是对象／没有 text／facts 不是数组）时，前三项退化成空、第四项给**原话**：
    不许只说「解析失败」（口径同 `intake_client.parse_semantics`）。
    """
    if not isinstance(answer, dict):
        return "", [], [], f"这一次的回答不是一个对象：{type(answer).__name__}"
    raw = answer.get("reason")
    text = answer.get("text")
    if not isinstance(text, str) or not text.strip():
        return "", [], [], raw or "模型没给出可用的 text（简报正文），没得核对"
    for name in ("window_facts", "history_facts"):
        if not isinstance(answer.get(name), list):
            return "", [], [], raw or f"模型给的 {name} 不是数组，没得核对"
    return text.strip(), answer["window_facts"], answer["history_facts"], None


def _fact_out(fact: dict) -> dict:
    """落盘只留契约那三个键（`label`/`path`/`value`）。"""
    return {"label": fact.get("label"), "path": fact.get("path"), "value": fact.get("value")}


def generate(catalog, subject, *, client, window_days: int = DEFAULT_WINDOW_DAYS) -> dict:
    """生成一份简报：问模型 → 过数字闸门 → 落盘。返回 `{brief, path, run_id}`（契约 §10.5）。

    `client` 必须**注入**，形状 `(科目, 候选读数) -> 回答`。真实现是
    `brief_client.HttpBrief`（走 `model_client` 那条与角色无关的管道 + `runs/` 留档），
    测试给一个假的——仓库的规矩：模型调用一律走注入的 stub，不联网、不花钱。

    三种失败，处置不同：

      · **没问成** → `ModelUnavailable` **原样往上抛**（调用方报 502 `model_unavailable`，
        可以重试）。这里不吞、不包装。
      · **答了话但没有一条能核对**（不是 JSON／没有 `text`／facts 不是数组）→
        502 `brief_unverifiable`，`details.facts` 是空的、`details.note` 记模型的原话。
      · **答了话但数字对不上** → 502 `brief_unverifiable`，`details.facts` 逐条列出对不上的。

    后两种都**一个字节都不落盘**——连 `briefs/` 目录都不建（契约 §12.1）。

    **前提**：`subject` 已由 HTTP 层按受控词表逐字校验过（契约 §10.5 的 400
    `subject_not_in_vocabulary`）；这里只做路径安全那一层（`_check_subject`）。
    """
    if isinstance(window_days, bool) or not isinstance(window_days, int) or window_days < 1:
        raise errors.bad_request(
            f"window_days 必须是正整数，拿到 {window_days!r}",
            hint="给「最近多少天」，默认 7",
            param="window_days", value=window_days,
        )
    subject = _check_subject(subject)

    index, _warnings, _skipped = catalog.index()
    digest = brief_digest(index, subject, window_days=window_days)
    answer = client(subject, digest)

    text, window_facts, history_facts, note = _read_answer(answer)
    if note is not None:
        raise errors.brief_unverifiable(subject, [], note=note)

    mismatches = list(verify_facts([*window_facts, *history_facts], index)["mismatches"])
    gap = _unclassified_missing(history_facts, index)
    if gap is not None:
        mismatches.append(gap)
    if mismatches:
        raise errors.brief_unverifiable(subject, mismatches)

    brief = {
        "subject": subject,
        "generated_at": catalog.clock().isoformat(timespec="seconds"),
        "window_days": window_days,
        "window_from": digest["window_from"],
        "window_until": digest["window_until"],
        "covers_until": index["built_at"],
        "provider": answer.get("provider"),
        "model": answer.get("model"),
        "text": text,
        "window_facts": [_fact_out(fact) for fact in window_facts],
        "history_facts": [_fact_out(fact) for fact in history_facts],
    }
    path = save_brief(catalog, brief)
    return {"brief": brief, "path": str(path), "run_id": answer.get("run_id")}


# ------------------------------------------------------------------ 落盘


def briefs_dir(catalog) -> Path:
    """简报的家：`<数据目录>/briefs/`。"""
    return Path(catalog.root) / BRIEFS_DIR


def _check_subject(subject) -> str:
    """科目名不许带任何**路径成分**（纵深防御；主闸是 HTTP 层按受控词表逐字校验）。

    与 `page_id_mismatch` 同一条纪律：**内容不许决定写到哪、读到哪**。一个含 `..` 或 `/`
    的科目拼出来的文件名会指到 `briefs/` 外面去。
    """
    if (not isinstance(subject, str) or not subject or subject != subject.strip()
            or any(ch in subject for ch in "/\\\x00") or ".." in subject):
        raise errors.bad_request(
            f"科目非法：{subject!r}",
            hint="科目取自受控词表，不许含 / \\ 或 '..'，也不许有首尾空白",
            param="subject", value=subject,
        )
    return subject


def _check_date(date) -> str:
    """日期只认 `YYYY-MM-DD`：文件名里的日期要拿去**逐字**匹配 `?at=`（契约 §10.5）。"""
    if not isinstance(date, str) or not _DATE_RE.match(date):
        raise errors.bad_request(
            f"日期必须是 YYYY-MM-DD，拿到 {date!r}",
            hint="简报一天一份，文件名里的日期就是这个形状",
            param="at", value=date,
        )
    return date


def brief_path(catalog, subject, date) -> Path:
    """`<数据目录>/briefs/<科目>-<YYYY-MM-DD>.json`。拼路径前先过两道闸（见上面两个函数）。"""
    return briefs_dir(catalog) / f"{_check_subject(subject)}-{_check_date(date)}.json"


def save_brief(catalog, brief: dict, *, date: str | None = None) -> Path:
    """落盘，**先写临时文件再原子替换**（契约 §10.5）。

    文件名里的日期取 `window_until`（= 索引快照那一天），不取 `generated_at`：两者跨零点
    时可能不是同一天，而文件名的日期是要拿去逐字匹配 `?at=` 的、也是「一天一份」的口径。
    """
    date = date or brief.get("window_until")
    path = brief_path(catalog, brief.get("subject"), date)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(json.dumps(brief, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        if tmp.exists():  # 写一半失败时不留残骸；换成了就本来不在了
            tmp.unlink()
    return path


def _read_brief(path: Path) -> dict:
    """读一份简报文件。**在盘上但读不了**是 500 `filesystem_error`，**不是** 404。"""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise errors.filesystem_error(
            exc, hint=f"简报文件 {path.name} 在盘上、但读不出来；修好它或重新生成一份"
        ) from exc


def list_briefs(catalog, subject) -> list[str]:
    """这个科目**实际有哪几天**：升序的 `YYYY-MM-DD`（404 的 `details.available` 用它）。

    只认 `<科目>-<YYYY-MM-DD>.json`：临时文件、别的科目的文件都不算。
    """
    subject = _check_subject(subject)
    directory = briefs_dir(catalog)
    if not directory.is_dir():
        return []
    prefix, suffix = f"{subject}-", ".json"
    dates: list[str] = []
    for path in directory.iterdir():
        name = path.name
        if not path.is_file() or not name.startswith(prefix) or not name.endswith(suffix):
            continue
        date = name[len(prefix):-len(suffix)]
        if _DATE_RE.match(date):
            dates.append(date)
    return sorted(dates)


def load_at(catalog, subject, date) -> tuple[dict, Path]:
    """读**历史上某一天**那一份：`(brief, path)`。那天没有 → **404 `brief_missing`**。

    `?at=` 按文件名里的日期**逐字**匹配，绝不落到「最接近的一天」（契约 §10.5）。
    """
    path = brief_path(catalog, subject, date)
    if not path.is_file():
        raise errors.brief_missing(subject, at=date, available=list_briefs(catalog, subject))
    return _read_brief(path), path


def load_latest(catalog, subject) -> tuple[dict, Path]:
    """读**日期最新**的那一份：`(brief, path)`。

    一份都没有 → **404 `brief_missing`**：那是「还没有」，不是「读不到」
    （文件在但读不了是 500，见 `_read_brief`）。
    """
    dates = list_briefs(catalog, subject)
    if not dates:
        raise errors.brief_missing(subject, available=[])
    return load_at(catalog, subject, dates[-1])


# ------------------------------------------------------------------ 过期


def stale(catalog, subject, brief, records=None) -> dict:
    """`{stale, new_problems}` =「这份简报生成之后又有题录进来了」。

    判据只有一条：该科目的题里 `created_at` **晚于** `brief["covers_until"]` 的道数 > 0。
    索引是每个请求现算的，`covers_until` 是它读的那份快照的 `built_at`——没有这个固定时刻，
    「过期」就无从判起（契约 §10.5）。

    比较的是**时刻**不是字符串：`created_at` 常是 `+08:00`、`covers_until` 是 `+00:00`，
    直接比字符串会把时区差当成先后（契约 §1）。

    `records` 不给就现算一次索引。两条诚实边界：

      · 读不出 `created_at` 的记录**无法比较**，不算新——那种卡本来就由「细则」的
        `undated` 单独报出来（不许静默丢的是它，不是这里）；
      · `covers_until` 读不出来的简报**按过期报**（`stale: True`、`new_problems: 0`）：
        宁可让人重新生成一次，也不要端着一份不知道依据哪一刻的总结说「它是新的」。
    """
    if records is None:
        data, _warnings, _skipped = catalog.index()
        records = data.get("problems") or []
    cutoff = parse_dt((brief or {}).get("covers_until"))
    if cutoff is None:
        return {"stale": True, "new_problems": 0}
    cutoff = cutoff.astimezone(timezone.utc)
    count = 0
    for record in records or []:
        if not isinstance(record, dict) or record.get("subject") != subject:
            continue
        created = parse_dt(record.get("created_at"))
        if created is not None and created.astimezone(timezone.utc) > cutoff:
            count += 1
    return {"stale": count > 0, "new_problems": count}


__all__ = [
    "BRIEFS_DIR", "DEFAULT_WINDOW_DAYS", "UNCLASSIFIED_LABEL", "UNCLASSIFIED_PATH",
    "brief_digest", "brief_path", "briefs_dir", "generate", "list_briefs", "load_at",
    "load_latest", "resolve_path", "save_brief", "stale", "verify_facts",
]
