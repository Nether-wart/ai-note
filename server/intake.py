"""收入决策：痕迹语义 → 收 / 不收 / 待定（#12、spec #2「红笔痕迹 ≠ 这道题错了」）。

**两层，不许合并**（spec #2）：

  · **统计**（`server/ink.py`，#11）只回答「这一块有没有红笔、多少」。
  · **语义**（模型，抽取角色，`server/intake_client.py`）回答「这点红笔是勾、是圈、
    是订正，还是只是分数」。

本模块把两者收成**唯一一份决策**（spec #2 的跨单元表点名「收入决策」只有一处）：
`decide_block` 是那张表，别的任何地方都不许再判一遍收不收。

## 规则（逐条照 spec #2 / 工单 #12）

| 情形 | 决策 | 规则码 |
|---|---|---|
| 有表示「错」的痕迹（叉、圈错、改错、订正） | **收** | `error_trace` |
| 有红笔但**只是对勾** | **不收** | `tick_only` |
| **判不准**（分数、圈题号、模型说看不清、模型没给出可用的语义） | **收** | `uncertain_defaults_to_keep` |
| 完全**没有红笔痕迹** | 不入库（每页报「另有 M 道……」，可一键补收） | `no_red_ink` |
| 统计**读不出来** | **待定**（`keep: null`，未入库、显式报出来） | `ink_unknown` |
| 人补收 / 人丢弃 | 收 / 不收 | `human_include` / `human_drop` |

**为什么「判不准 → 收」**（代价不对称，spec 原话）：漏收一道错题，它**永远不在库里**；
误收一道对题，审核时删掉，**几秒**。所以唯一的「不收」例外是**明确的「做对了」**
（纯对勾）——那时红笔是「对」的证据，不是「错」的证据。

分数（`score`）与圈题号（`circle_number`）**落向收**：工单只给了一条「不收」的例外
（纯对勾）。分数与圈题号既不表示对也不表示错 —— 那是「判不准」，按上面那条代价
不对称收下来，而不是发明第三条「不收」的理由。收进来的每一块都在页文件里记着
`semantics: "score"`，审核时一眼能看出「这块是因为分数收的」，几秒就能删。

## 「待定」是**统计**那一档，不是模型那一档

`keep: null` 只用于「红笔统计读不出来」（块没有可用的 `bbox_px`，或整页图不在/读不了）。
`server/ink.py` 的同一条纪律：**「没有红笔」和「读不出来」必须分得开**——把读不出来
当成「没有红笔」就是静默丢题，正是这个项目最怕的一类失败。模型的「判不准」落向**收**，
因为那已经是一个可以落地的决定。

## 已知漏法（spec #2 Further Notes，写在这里免得日后当 bug 查）

- **老师只写分数不写订正** → 分数太小、没到框级阈值（`ink.COLOR_MIN_PIXELS`）时，
  这块被算成「没有红笔痕迹」→ 不入库。**这正是「另有 M 道……未入库 + 一键补收」
  存在的理由**，不是可选装饰。
- **学生用蓝笔或黑笔订正** → 颜色掩膜抓不到（统计层根本看不见它）→ 同上。
- **红笔勾表示做对** → 规则内正确排除（`tick_only`，spec 说这是"规则内正确排除"）。
- **整张卷子本身就是红笔印刷的** → 会**误收**（整页都是彩笔）。这是代价较小的那一侧
  （删掉几秒），但每块的 `decision` 都记着「为什么收」，人能看见并改。

## 决策写进页文件（#12 验收 3）

每个块留下 `keep`（三态）+ `ink`（#11 的统计原样）+ `decision`
（`rule` 命中的规则码、`reason` 一句人话、`source` 是模型判的还是兜底/统计/人给的、
`semantics` 模型的原始语义、以及谁判的与留档 id）。理由：切分结果与收入决策一旦只
存在于界面的内存里，「漏了一题」就永远查不出来（spec #2）——页文件是唯一的审计面。

**人动过的决策不许被抹掉**：`source == "human"` 的块，重跑自动决策时原样保留
（spec #2：「人动过的部分不允许被一次重切抹掉」；同一原则见编排裁决 D10）。

本模块**不联网**：模型调用是注入的接缝（`semantics=`），测试喂假回答；
写盘只有 `save_page` 一处（`server/pages.py`，#9），本模块不另造页结构。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from . import errors, ink, pages
from .config import EXTRACT_ROLE, load_role_config
from .errors import ApiError
from .intake_client import HttpSemantics
from .model_client import ModelUnavailable
from .warnings import _warn

# ---------------------------------------------------------------- 决策的取值（stable）

KEEP = True
DROP = False
PENDING = None      # 待定：契约 §10.2.1 的 keep: null

# 规则码：记进页文件 `decision.rule`，是「为什么收/为什么没收」的机器可读那一半。
# 它们是**审计字段**——改取值等于改历史读数，测试钉住。
RULE_ERROR_TRACE = "error_trace"
RULE_TICK_ONLY = "tick_only"
RULE_UNCERTAIN_KEEP = "uncertain_defaults_to_keep"
RULE_NO_RED_INK = "no_red_ink"
RULE_INK_UNKNOWN = "ink_unknown"
RULE_HUMAN_INCLUDE = "human_include"
RULE_HUMAN_DROP = "human_drop"

RULES = (
    RULE_ERROR_TRACE, RULE_TICK_ONLY, RULE_UNCERTAIN_KEEP,
    RULE_NO_RED_INK, RULE_INK_UNKNOWN, RULE_HUMAN_INCLUDE, RULE_HUMAN_DROP,
)

# 决策的来源：谁判的。验收 3 要求能回答「是模型判的还是兜底落收的」。
SOURCE_MODEL = "model"            # 模型给出了可用的语义，规则据此判定
SOURCE_FALLBACK = "fallback"      # 模型没给出可用的语义 → 按「判不准」兜底落向收
SOURCE_STATISTICS = "statistics"  # 统计层就定了（没有红笔 / 统计读不出来）
SOURCE_HUMAN = "human"            # 人补收 / 人丢弃

DECISION_SOURCES = (SOURCE_MODEL, SOURCE_FALLBACK, SOURCE_STATISTICS, SOURCE_HUMAN)

# ---------------------------------------------------------------- 语义枚举（模型的回答）

# 表示「错」的痕迹 → 收（spec 原话：叉、圈错、改错、订正）
SEMANTICS_CROSS = "cross"                 # 打叉
SEMANTICS_CIRCLE_WRONG = "circle_wrong"   # 圈出错的地方
SEMANTICS_CORRECTION = "correction"       # 写了订正/改错
SEMANTICS_ERROR = (SEMANTICS_CROSS, SEMANTICS_CIRCLE_WRONG, SEMANTICS_CORRECTION)

# 只是对勾 → 不收（唯一的不收例外）
SEMANTICS_TICK = "tick"
SEMANTICS_TICK_ONLY = (SEMANTICS_TICK,)

# 其余：都不表示「对」，也不表示「错」→ 判不准 → 收
SEMANTICS_SCORE = "score"                 # 只是打分数
SEMANTICS_CIRCLE_NUMBER = "circle_number"  # 只是圈了题号
SEMANTICS_NONE = "none"                   # 模型说这块看不到红笔（与统计矛盾时另有警告）
SEMANTICS_OTHER = "other"                 # 别的红笔
SEMANTICS_UNKNOWN = "unknown"             # 模型明说判不准
SEMANTICS_OTHER_VALUES = (
    SEMANTICS_SCORE, SEMANTICS_CIRCLE_NUMBER, SEMANTICS_NONE,
    SEMANTICS_OTHER, SEMANTICS_UNKNOWN,
)

SEMANTICS_VALUES = SEMANTICS_ERROR + SEMANTICS_TICK_ONLY + SEMANTICS_OTHER_VALUES

_SEMANTICS_CN = {
    SEMANTICS_CROSS: "打叉",
    SEMANTICS_CIRCLE_WRONG: "圈出错处",
    SEMANTICS_CORRECTION: "订正（改错）",
    SEMANTICS_TICK: "对勾",
    SEMANTICS_SCORE: "分数",
    SEMANTICS_CIRCLE_NUMBER: "圈题号",
    SEMANTICS_NONE: "看不到红笔",
    SEMANTICS_OTHER: "别的红笔",
    SEMANTICS_UNKNOWN: "看不清",
}


def semantics_text(semantics) -> str:
    """语义取值 → 中文，给人看的那一句里用。认不出来的值原样引用（不许静默）。"""
    if semantics in _SEMANTICS_CN:
        return _SEMANTICS_CN[semantics]
    return f"认不出的取值 {semantics!r}"


# ---------------------------------------------------------------- 框级：这块有没有红笔


def has_red_ink(stats) -> bool | None:
    """#11 的统计 → 「这块有没有红笔」。`None` = **读不出来**（不是「没有」）。

    框级判据只有一处：`ink.COLOR_MIN_PIXELS`（#11 的具名常量，体检与筛选共用它，
    严格大于——原型 `proto/slice.py:888` 的 `in_color > 20`）。这里**不重写那个比较**，
    也不重新数像素：统计由 `ink.ink_statistics` / `ink.page_block_reports` 给。
    """
    if not isinstance(stats, dict):
        return None
    colored_px = stats.get("colored_px")
    if not isinstance(colored_px, (int, float)) or isinstance(colored_px, bool):
        return None
    return colored_px > ink.COLOR_MIN_PIXELS


# ---------------------------------------------------------------- 决策（纯逻辑）


def _decision(keep, rule: str, source: str, reason: str, semantics=None) -> dict:
    """一条决策记录。形状固定：`keep` 三态 + 四个可审计字段（验收 3）。"""
    return {
        "keep": keep,
        "rule": rule,
        "source": source,
        "semantics": semantics,
        "reason": reason,
    }


def decide_block(*, has_red_ink: bool | None, semantics=None) -> dict:
    """**唯一**的收入决策：这块收不收（spec #2 的三条规则 + 无痕迹那一档）。

    `has_red_ink` 由 `has_red_ink(stats)` 给：`True` 有红笔 / `False` 没有 / `None` 读不出来。
    `semantics` 是模型给的语义（`SEMANTICS_VALUES` 之一），没给或认不出就给 `None`。

    判序（先统计、后语义——统计读不出来时没什么可判的）：

    1. 统计读不出来 → **待定**（不是「没有红笔」）。
    2. 没有红笔 → **不收**（但每页要把这些块报出来，可一键补收）。
    3. 有红笔：语义是「错」→ **收**；只是对勾 → **不收**；其余（含判不准）→ **收**。

    返回 `{keep, rule, source, semantics, reason}`：`reason` 是给人看的原话，
    `rule`/`source`/`semantics` 是给审计与测试看的。**不含**时间与模型身份——
    那几样由调用方（`plan_decisions`）补，这个函数因此是纯的、脱网可测的。
    """
    if has_red_ink is None:
        return _decision(
            PENDING, RULE_INK_UNKNOWN, SOURCE_STATISTICS,
            "这块的红笔统计做不出来（块没有可用的 bbox_px，或整页照片不在/读不了）"
            "→ 待定，未入库：把它当成「没有红笔」就是静默丢题",
        )

    if not has_red_ink:
        return _decision(
            DROP, RULE_NO_RED_INK, SOURCE_STATISTICS,
            "这块没有红笔痕迹 → 不入库（这一页还有几道这样的题会一并报出来，可以一键补收）",
        )

    if not isinstance(semantics, str):
        semantics = None
    if semantics in SEMANTICS_TICK_ONLY:
        return _decision(
            DROP, RULE_TICK_ONLY, SOURCE_MODEL,
            "红笔只是一个对勾（表示这道题做对了）→ 不收：勾是「对」的证据，不是「错」的证据",
            semantics,
        )
    if semantics in SEMANTICS_ERROR:
        return _decision(
            KEEP, RULE_ERROR_TRACE, SOURCE_MODEL,
            f"红笔是{semantics_text(semantics)}（表示这道题错了）→ 收：有表示「错」的痕迹",
            semantics,
        )

    # 到这里就是「判不准」那一档：分数、圈题号、看不清、枚举外的值、压根没给语义。
    # 它落向**收**，而且理由要写清代价不对称（工单 #12 验收 2 专门钉住这条）。
    source = SOURCE_MODEL if semantics in SEMANTICS_OTHER_VALUES else SOURCE_FALLBACK
    if source == SOURCE_MODEL:
        why = f"红笔是{semantics_text(semantics)}——既不表示「对」也不表示「错」"
    else:
        why = "模型没给出可用的语义"
    return _decision(
        KEEP, RULE_UNCERTAIN_KEEP, source,
        f"{why} → 判不准，收：漏收一道错题它永远不在库里，误收一道对题审核时删掉几秒",
        semantics,
    )


# ---------------------------------------------------------------- 页级警告码（契约 §8）

# 构造一律走 `server/warnings.py: _warn`（BRIEF 硬规则 7：不许手搓 dict，
# `level` 总是显式发出来）。页级警告的 `id` 为 `null`（契约 §2：它属于哪张卡），
# 块 id 在 `message` 里点名，机器可读的那一份在返回值里。
INTAKE_PAGE_IMAGE_MISSING = "intake_page_image_missing"        # warning
INTAKE_PAGE_IMAGE_UNREADABLE = "intake_page_image_unreadable"  # warning
INTAKE_BLOCK_INK_UNKNOWN = "intake_block_ink_unknown"          # warning
INTAKE_SEMANTICS_FALLBACK = "intake_semantics_fallback"        # hint
INTAKE_SEMANTICS_UNPARSED = "intake_semantics_unparsed"        # warning
INTAKE_INK_SEMANTICS_CONFLICT = "intake_ink_semantics_conflict"  # warning
INTAKE_HUMAN_DECISION_PRESERVED = "intake_human_decision_preserved"  # hint


def _page_warn(code: str, message: str, level: str = "warning") -> dict:
    """页级警告：`id` 为 None（不属于某一张卡），`level` 显式发出来。"""
    return _warn(code, message, None, level)


# ---------------------------------------------------------------- 页级：决定 + 记录 + 报告


def _iso(moment) -> str:
    """时间戳一律 `isoformat(timespec="seconds")`（与 `mastery.py` 的 `attempt.at` 同口径）。"""
    return moment.isoformat(timespec="seconds")


def _reports_by_id(ink_reports) -> dict:
    """#11 的逐块统计报告 → 按块 id 索引。重复的 id 只认第一条（并会被下面的报告喊出来）。"""
    out: dict = {}
    for report in ink_reports or []:
        if isinstance(report, dict):
            out.setdefault(report.get("id"), report)
    return out


def _composition(decision: dict, answer, stamp: str) -> dict:
    """规则决策 + 模型身份 → 落进页文件的那一条完整记录（验收 3 的审计面）。

    `confidence`／`provider`／`model`／`run_id` 只在真的问过模型时才有值：
    没有红笔（统计定的）与人补收（人定的）那几档，这几个字段是 `None`——
    「没有模型参与」必须看得出来，不许拿一个假的模型身份充数。
    """
    answer = answer if isinstance(answer, dict) else {}
    return {
        "keep": decision["keep"],
        "rule": decision["rule"],
        "source": decision["source"],
        "semantics": decision["semantics"],
        "reason": decision["reason"],
        "confidence": answer.get("confidence"),
        "provider": answer.get("provider"),
        "model": answer.get("model"),
        "run_id": answer.get("run_id"),
        "at": stamp,
    }


def plan_decisions(page: dict, *, ink_reports, semantics=None, at=None) -> dict:
    """一页的收入决策：逐块判、逐块记，并报出「另有 M 道没有红笔痕迹、未入库」。

    **纯逻辑**：不调模型（`semantics` 是喂进来的答案）、不写盘、不改传进来的 `page`。
    调用方（`run_intake`）负责问模型与 `save_page`，界面（#14）负责显示。

    输入：

    - `ink_reports`：**#11 的 `ink.page_block_reports(image, page)` 原样**（逐块统计，
      `stats=None` = 读不出来）。本模块不数像素、不重算边界。
    - `semantics`：`{块 id: 答案}`，答案是 `server/intake_client` 的形状
      （`{semantics, confidence, reason, provider, model, run_id, parsed}`）。
      有红笔的块才有答案——没有红笔的块不需要问模型（0 与「读不出来」另有两档）。

    返回 `{page, decisions, counts, not_kept, warnings}`：

    - `page`：**新的**页（每块补上 `keep` / `ink` / `decision`），可以直接 `save_page`。
    - `not_kept`：`{count, by_rule:[{rule,count,ids,message}], message, block_ids, one_click}`。
      `message` 就是工单验收 1 那句原话「另有 M 道没有红笔痕迹、未入库」（M=0 也照印，
      与 #7 的 M 同一条口径：零是「我检查过」，不是「可以不说」）；`by_rule` 把每一种
      不入库的理由逐个列出（D3：不能只有一个数字）；`block_ids` 就是 `include_blocks`
      要的入参（一键补收）。
    - `warnings`：一律走 `server/warnings.py: _warn`（`{code,message,id,level}`，页级 `id` 为 null）。

    **人动过的决策原样保留**（`source == "human"`）：一遍重跑不许抹掉人的补收/丢弃
    （spec #2 的同一原则，见编排裁决 D10）。这一档会发一条 `hint`。
    """
    moment = at or datetime.now()
    stamp = _iso(moment)
    answers = semantics if isinstance(semantics, dict) else {}
    reports = _reports_by_id(ink_reports)

    warnings: list[dict] = []
    blocks: list[dict] = []
    decisions: list[dict] = []
    preserved: list[str] = []

    for raw in page.get("blocks") or []:
        if not isinstance(raw, dict):
            # 码沿用 #9/#10 的那一个（同一件事：块列表里混进了不是对象的项）
            warnings.append(_page_warn(
                "block_not_an_object", f"块列表里混进了不是对象的项：{raw!r} → 这一项没有决策"))
            continue
        block = dict(raw)
        block_id = block.get("id")
        report = reports.get(block_id)
        stats = report.get("stats") if isinstance(report, dict) else None
        block["ink"] = stats

        previous = block.get("decision") if isinstance(block.get("decision"), dict) else None
        if previous and previous.get("source") == SOURCE_HUMAN:
            preserved.append(block_id)
            warnings.append(_page_warn(
                INTAKE_HUMAN_DECISION_PRESERVED,
                f"块 {block_id!r} 的去留是人定的（{previous.get('rule')}）→ 重跑自动决策不动它"
                f"（人动过的部分不允许被一次重跑抹掉）",
                "hint"))
            blocks.append(block)
            decisions.append(_decision_row(block_id, previous))
            continue

        has = has_red_ink(stats)
        if has is None:
            if isinstance(report, dict) and report.get("message"):
                warnings.append(_page_warn(INTAKE_BLOCK_INK_UNKNOWN, report["message"]))
            else:
                warnings.append(_page_warn(
                    INTAKE_BLOCK_INK_UNKNOWN,
                    f"块 {block_id!r} 没有红笔统计（整页统计没跑到它）→ 待定，未入库"))

        answer = answers.get(block_id) if has else None
        decision = decide_block(
            has_red_ink=has,
            semantics=(answer or {}).get("semantics") if isinstance(answer, dict) else None,
        )
        warnings.extend(_semantics_warnings(block_id, has, answer, decision, stats))
        block["keep"] = decision["keep"]
        block["decision"] = _composition(decision, answer, stamp)
        blocks.append(block)
        decisions.append(_decision_row(block_id, block["decision"]))

    not_kept = _not_kept_report(blocks)
    kept = sum(1 for b in blocks if b.get("keep") is True)
    return {
        "at": stamp,
        "page": {**page, "blocks": blocks},
        "decisions": decisions,
        "counts": {
            "blocks": len(blocks),
            "kept": kept,
            "dropped": sum(1 for b in blocks if b.get("keep") is False),
            "pending": sum(1 for b in blocks if b.get("keep") is None),
            "no_red_ink": sum(1 for b in blocks
                              if (b.get("decision") or {}).get("rule") == RULE_NO_RED_INK),
            "human": len(preserved) + sum(1 for b in blocks
                                          if (b.get("decision") or {}).get("source") == SOURCE_HUMAN),
        },
        "not_kept": not_kept,
        "warnings": warnings,
    }


def _decision_row(block_id, decision: dict) -> dict:
    """报告里的逐块一行：够界面列出「这一页每一块为什么收/为什么没收」。"""
    return {
        "block_id": block_id,
        "keep": decision.get("keep"),
        "rule": decision.get("rule"),
        "source": decision.get("source"),
        "semantics": decision.get("semantics"),
        "reason": decision.get("reason"),
    }


def _semantics_warnings(block_id, has, answer, decision: dict, stats=None) -> list[dict]:
    """语义这一层的响声：没查到（hint）、与统计矛盾（warning）。

    级别按 #10 的两级纪律：`warning` = 真矛盾；`hint` = 「我这一条没查全」。
    把「没查到」报成 warning 会训练人忽略体检（`proto/server.py:1046-1047`）。
    """
    if not has or decision["rule"] not in (RULE_ERROR_TRACE, RULE_TICK_ONLY,
                                           RULE_UNCERTAIN_KEEP):
        return []
    out: list[dict] = []
    if isinstance(answer, dict) and answer.get("semantics") == SEMANTICS_NONE:
        colored_px = (stats or {}).get("colored_px")
        out.append(_page_warn(
            INTAKE_INK_SEMANTICS_CONFLICT,
            f"块 {block_id!r}：统计说这块有红笔 {colored_px}px，模型却说看不到红笔"
            f"（两份证据矛盾）→ 判不准，落向收，请人看一眼",
        ))
    if answer is None:
        out.append(_page_warn(
            INTAKE_SEMANTICS_FALLBACK,
            f"块 {block_id!r} 有红笔，但这次没有拿到它的语义（没问模型，或答案丢了）"
            f"→ 判不准，落向收", "hint"))
        return out
    if not isinstance(answer, dict):
        out.append(_page_warn(
            INTAKE_SEMANTICS_FALLBACK,
            f"块 {block_id!r} 的语义不是一个可读的对象（{answer!r}）→ 判不准，落向收", "hint"))
        return out
    if not answer.get("parsed", True):
        out.append(_page_warn(
            INTAKE_SEMANTICS_UNPARSED,
            f"块 {block_id!r}：模型的输出解析不出语义（{str(answer.get('reason'))[:200]}）"
            f"→ 判不准，落向收"))
    elif answer.get("semantics") not in SEMANTICS_VALUES:
        out.append(_page_warn(
            INTAKE_SEMANTICS_FALLBACK,
            f"块 {block_id!r}：模型给的语义不在枚举里（{answer.get('semantics')!r}）"
            f"→ 判不准，落向收", "hint"))
    return out


# 「不收」的三种理由各自的句子。**`no_red_ink` 那一句是 spec/工单写死的原话**
# （「另有 M 道没有红笔痕迹、未入库」），M=0 也照印：零是「我检查过」。
# 另外两句由服务给，界面照原话显示、不再自己拼（契约 §6.1 第 5 条的同一条口径）。
_NO_RED_INK_SENTENCE = "另有 {n} 道没有红笔痕迹、未入库"
_NOT_KEPT_SENTENCES = {
    RULE_TICK_ONLY: "另有 {n} 道只有红笔对勾（表示做对了）、未入库",
    RULE_INK_UNKNOWN: "另有 {n} 道因为红笔统计读不出来、未入库（待定，等人看一眼）",
    RULE_HUMAN_DROP: "另有 {n} 道被人标成不入库",
}
# `by_rule` 的顺序：头条那句在最前，其余按规则表的顺序（确定性、可断言）。
_NOT_KEPT_ORDER = (RULE_NO_RED_INK, RULE_TICK_ONLY, RULE_INK_UNKNOWN, RULE_HUMAN_DROP)


def _not_kept_report(blocks: list[dict]) -> dict:
    """「没入库的题」的枚举报告（验收 1 的前半：列出未入库的题）。

    `count` 是**没入库**的块数（含待定——待定也没入库，只是原因不同）。
    """
    buckets: dict[str, list] = {}
    order: list[str] = []
    for block in blocks:
        if block.get("keep") is True:
            continue
        rule = (block.get("decision") or {}).get("rule")
        if rule not in buckets:
            buckets[rule] = []
            order.append(rule)
        buckets[rule].append(block.get("id"))

    by_rule = []
    for rule in list(_NOT_KEPT_ORDER) + [r for r in order if r not in _NOT_KEPT_ORDER]:
        ids = buckets.get(rule)
        if not ids and rule != RULE_NO_RED_INK:
            continue
        ids = ids or []
        sentence = _NOT_KEPT_SENTENCES.get(rule, _NO_RED_INK_SENTENCE)
        by_rule.append({"rule": rule, "count": len(ids), "ids": ids,
                        "message": sentence.format(n=len(ids))})

    block_ids = [b.get("id") for b in blocks if b.get("keep") is not True]
    return {
        "count": len(block_ids),
        "by_rule": by_rule,
        "message": _NO_RED_INK_SENTENCE.format(n=len(buckets.get(RULE_NO_RED_INK) or [])),
        "block_ids": block_ids,
        "one_click": {
            "entry": "server.intake.include_blocks",
            "how": "include_blocks(page, block_ids) —— 不传 block_ids 就是把上面这些全收进来",
            "block_ids": block_ids,
            "cli": "python3 -m server.intake --page <页 id> --include --apply",
        },
    }


# ---------------------------------------------------------------- 一键补收（验收 1 的后半）


def include_blocks(page: dict, block_ids=None, *, at=None) -> dict:
    """**一键补收**：把没入库的块收进来（工单 #12 验收 1 的那个可调用入口）。

    `block_ids=None` = 把这一页所有没入库的块（`keep is not True`，含待定）收进来；
    给一串块 id 就只收它们。返回 `{page, included, already_kept, unknown, summary, warnings}`：
    **点不到的 id 要说出来**（`unknown`），不许静默忽略。

    补收记成 `decision.rule = "human_include"`、`source = "human"`——所以
    **重跑自动决策不会把它翻回去**（`plan_decisions` 保留人做的决策）。这就是
    spec 说的「补收入口」在数据上的落点：漏收的那几道一旦被人收进来，就一直是收的。
    """
    return _human_keep(page, block_ids, keep=True, at=at)


def set_keep_by_human(page: dict, block_ids, *, keep: bool, at=None) -> dict:
    """人改去留（补收 / 丢弃）：`keep=True` 是补收（走 `include_blocks`），`False` 是丢弃。

    「切换收入／丢弃」是 spec #2 的手动修正最小集合之一（#14 的界面调它）；
    #12 只需要补收那一条，但两条共用同一份实现——**人的决策只有一处写法**。
    """
    if not isinstance(keep, bool):
        raise ValueError(f"keep 必须是布尔（收／不收），收到 {keep!r}")
    return _human_keep(page, block_ids, keep=keep, at=at)


def _human_keep(page: dict, block_ids, *, keep: bool, at=None) -> dict:
    moment = at or datetime.now()
    stamp = _iso(moment)
    wanted = None if block_ids is None else list(block_ids)
    remaining = list(wanted) if wanted is not None else None

    warnings: list[dict] = []
    blocks: list[dict] = []
    changed: list[dict] = []
    already: list = []
    for raw in page.get("blocks") or []:
        if not isinstance(raw, dict):
            warnings.append(_page_warn(
                "block_not_an_object", f"块列表里混进了不是对象的项：{raw!r} → 这一项没动"))
            continue
        block = dict(raw)
        block_id = block.get("id")
        target = True if wanted is None else (block_id in wanted)
        if target and remaining is not None and block_id in remaining:
            remaining.remove(block_id)
        if not target:
            blocks.append(block)
            continue
        if block.get("keep") is keep:
            already.append(block_id)
            blocks.append(block)
            continue
        previous = block.get("decision") if isinstance(block.get("decision"), dict) else {}
        rule = RULE_HUMAN_INCLUDE if keep else RULE_HUMAN_DROP
        was = previous.get("rule") or "没判过"
        block["keep"] = keep
        block["decision"] = {
            "keep": keep,
            "rule": rule,
            "source": SOURCE_HUMAN,
            "semantics": previous.get("semantics"),
            "reason": (f"人{'一键补收' if keep else '改判为不入库'}（原来：{was}）"
                       f"→ {'收' if keep else '不收'}：人的判断优先于自动决策"),
            "confidence": previous.get("confidence"),
            "provider": previous.get("provider"),
            "model": previous.get("model"),
            "run_id": previous.get("run_id"),
            "at": stamp,
        }
        changed.append({"block_id": block_id, "rule": rule,
                        "reason": block["decision"]["reason"]})
        blocks.append(block)

    unknown = list(remaining or [])
    if unknown:
        warnings.append(_page_warn(
            "intake_block_unknown",
            f"点名要动的块在这一页上找不到：{unknown}（页 {page.get('id')!r} 上没有这些块 id）"))
    return {
        "at": stamp,
        "page": {**page, "blocks": blocks},
        "included": changed if keep else [],
        "dropped": [] if keep else changed,
        "already_kept": already,
        "unknown": unknown,
        "summary": {
            "blocks": len(blocks),
            "included": len(changed) if keep else 0,
            "dropped": 0 if keep else len(changed),
            "already_kept": len(already),
            "unknown": len(unknown),
        },
        "warnings": warnings,
    }


# ---------------------------------------------------------------- 驱动：读页 → 统计 → 问模型 → 写回


def _load_page(catalog, page_id) -> dict:
    """点名要一页：id 非法 → 400；不在 → 404；读不了 → 500；**页里的 id 与点名的 id
    不一致 → 400 `page_id_mismatch`**（`details.param == "page_id"`）。**拒绝路径只有这一处**。

    页 id 会拼进路径（`<data>/pages/<id>.json`），所以校验必须发生在碰盘之前——
    `../../problems/p-xxx` 这种 id 会让写回打到 `pages/` 外面，可能覆盖一张真题卡。
    同理，**页文件里**那个 `id` 字段也必须与点名的 id 一致：写盘路径只认点名这个 id，
    内容里的 id 只用于对账（`pages.require_page_identity`，`save_page` 里还有一道）。
    """
    if not pages.is_page_id(page_id):
        raise errors.bad_request(
            f"页 id 非法：{page_id!r}",
            hint="页 id 就是页文件名的主干，只允许字母、数字、点、下划线与连字符，"
                 "必须以字母或数字开头，且不许出现 '..'（它会变成路径穿越）",
            param="page_id",
            value=page_id,
        )
    page, read_error = pages.read_page(catalog, page_id)
    if read_error:
        raise ApiError(
            500, "internal_error", read_error,
            reason="page_file_unreadable",
            hint="页文件读不了就没法谈收入决策；先修好这个文件（或从照片重建这一页）",
            details={"what": "page", "id": page_id},
        )
    if page is None:
        raise errors.not_found(
            f"没有这一页：{page_id}（页文件 {pages.page_path(catalog, page_id)} 不在）",
            hint="先建页：存量卡用 `python3 -m server.backfill --apply`，"
                 "新照片走收件目录（#13）",
            what="page", id=page_id,
        )
    pages.require_page_identity(catalog, page, page_id)
    return page


def run_intake(catalog, page_id, *, semantics=None, at=None, apply: bool = False) -> dict:
    """跑一页的收入决策：**唯一的入口**（#14 的界面与 CLI 都调它）。

    步骤：读页文件 → 读整页照片 → 用 #11 的 `ink.page_block_reports` 逐块算红笔统计
    → 对**有红笔**的块问模型要语义 → `plan_decisions` 成篇 → `apply` 时 `save_page` 写回。

    - `apply=False`（默认）是**预演**：算统计、报事实、看已有的决策，**不问模型、不写盘**
      （调模型要花钱，预演不该悄悄花）。
    - `apply=True` 才问模型并写回。**模型调用失败（`ModelUnavailable`）往上抛，
      页文件一个字节都不动**（D1/D9：不留半截决策）——写回只发生在全部块都判完之后。
    - 只问**有红笔**且**尚未被人定过去留**的块：没有红笔的块不需要语义，
      人补收/丢弃过的块不许被自动决策覆盖（`plan_decisions` 保留它们）。

    失败形状（D1：拒绝一律 JSON 信封，带 `reason`）：

    - 页 id 非法 → **400** `bad_request`（`details.param == "page_id"`）——
      页 id 会变成文件名，不校验就可能写到 `pages/` 外面去。
    - 页文件不在 → **404** `not_found`（与 #9 的「旧卡还没回填」不是一回事：
      这里是人点名要一页，找不到就得说）。
    - 页文件读不了/不是 JSON 对象 → **500** `internal_error`（口径同 `catalog.load_card`），
      `message` 里点名是哪个文件。
    - **页里的 `id` 与点名的 id 不一致** → **400** `page_id_mismatch`
      （`details.param == "page_id"`）：写盘路径只认点名的那个 id，内容里的 id 只用于对账。
    - **页里的 `image` 不是纯文件名**（含 `/`、`\\`、`..`）→ **400** `page_image_unsafe`
      （`details.param == "image"`）：不拿它拼 `pages/` 外面的路径去读。

    返回值在 `plan_decisions` 的报告之上再加 `page_id` / `page_path` / `apply` / `preview` /
    `asked`（这次问了哪些块）——「我做了什么、没做什么」都要看得见（ADR 0007 第 6 条）。
    """
    page = _load_page(catalog, page_id)

    # `image` 拼路径之前先保证它是**纯文件名**（不含 '/'、'\'、'..'）：坏页文件里的
    # `../` 会让服务去 `pages/` 外面读文件。拒绝在算路径之前，一个字节都没碰。
    image_name = page.get("image")
    if not pages.is_page_image_name(image_name):
        raise errors.page_image_unsafe(page_id=page_id, image=image_name)

    moment = at or datetime.now()
    image_path = catalog.pages_dir / str(image_name or "")
    warnings: list[dict] = []
    reports: list[dict] = []
    if not image_path.is_file():
        warnings.append(_page_warn(
            INTAKE_PAGE_IMAGE_MISSING,
            f"整页照片不在（{image_path}）→ 这一页每一块的红笔统计都做不了，"
            f"全部待定（未入库）：把它当成「没有红笔」就是静默丢题",
        ))
    else:
        try:
            image = ink.read_png(image_path)
        except ink.UnsupportedImage as exc:
            warnings.append(_page_warn(
                INTAKE_PAGE_IMAGE_UNREADABLE,
                f"整页照片读不了（{image_path}）：{exc} → 这一页的红笔统计做不了，全部待定（未入库）",
            ))
        else:
            reports = ink.page_block_reports(image, page)

    answers: dict = {}
    asked: list = []
    if apply and semantics is not None:
        stats_by_id = {r.get("id"): r.get("stats") for r in reports if isinstance(r, dict)}
        for block in page.get("blocks") or []:
            if not isinstance(block, dict):
                continue
            previous = block.get("decision")
            if isinstance(previous, dict) and previous.get("source") == SOURCE_HUMAN:
                continue    # 人定过的去留不许被自动决策重问/覆盖
            stats = stats_by_id.get(block.get("id"))
            if has_red_ink(stats) is not True:
                continue
            answers[block["id"]] = semantics(block, stats, image_path)
            asked.append(block["id"])

    plan = plan_decisions(page, ink_reports=reports, semantics=answers, at=moment)
    if apply:
        pages.save_page(catalog, plan["page"], page_id=page_id, apply=True)
    return {
        **plan,
        "page_id": page_id,
        "page_path": str(pages.page_path(catalog, page_id)),
        "apply": bool(apply),
        "preview": not apply,
        "asked": asked,
        "warnings": warnings + plan["warnings"],
    }


# ---------------------------------------------------------------- CLI 入口（一键补收的命令形态）


def _default_runs_dir() -> Path:
    """留档目录的唯一定义在 `server/paths.py`（所有角色的调用档都落在那儿）。"""
    from .paths import default_runs_dir
    return default_runs_dir()


def build_parser() -> argparse.ArgumentParser:
    from .paths import default_data_dir, default_runs_dir

    parser = argparse.ArgumentParser(
        description="收入决策（#12）：红笔痕迹的语义 → 收 / 不收 / 待定")
    parser.add_argument("--data", default=str(default_data_dir()),
                        help="数据目录（默认**用户数据目录**，见 server/paths.py；"
                             "仓库里的 data/ 只做测试语料）")
    parser.add_argument("--page", required=True,
                        help="页 id（页文件名主干，例如 41c86bcfc007）")
    parser.add_argument("--apply", action="store_true",
                        help="真的问模型并写回页文件；不传就是预演（只报告，一个字节都不写，"
                             "也不问模型——调模型要花钱，预演不该悄悄花）")
    parser.add_argument("--include", action="store_true",
                        help="一键补收：把这一页没入库的块全收进来（写回要加 --apply）")
    parser.add_argument("--include-block", action="append", default=None, metavar="块ID",
                        help="只补收点名的块（可重复：--include-block b2 --include-block b5）")
    parser.add_argument("--runs-dir", default=str(default_runs_dir()),
                        help="模型调用留档目录（契约 §10.1；默认 <数据目录>/runs，"
                             "也就是用户数据目录下的 runs/）")
    return parser


def main(argv: list[str] | None = None, *, semantics=None, at=None) -> int:
    """CLI：`python3 -m server.intake --page <页 id> [--apply] [--include]`。

    输出是**契约 §2 的信封**（`{ok, data, warnings, skipped}` 或 `{ok, error, …}`）——
    与 HTTP 端点同一形状，所以 #14 把界面接到这里时不必要另学一套。
    失败一律退出码 2 并把 `code`/`reason`/`message` 打出来（D1：绝不许裸回溯），
    **盘上的 `OSError`（含 `FileNotFoundError`）也一样**（`reason = "filesystem_error"`）。

    `semantics` 是**测试接缝**：注入假的抽取角色，测试就不联网、不花钱。
    """
    # 环境先灌、parser 后建（与 `server/app.py: main` 同一个顺序讲究：默认值现算）。
    from .app import load_local_env

    for env_file in load_local_env():
        print(f"密钥文件：{env_file}", file=sys.stderr)

    parser = build_parser()
    args = parser.parse_args(argv)

    data_dir = Path(args.data)
    if not data_dir.is_dir():
        print(f"数据目录不存在：{data_dir}", file=sys.stderr)
        return 2

    from .catalog import Catalog

    catalog = Catalog(data_dir)
    moment = at or datetime.now()
    want_include = bool(args.include or args.include_block)
    block_ids = None if args.include else args.include_block

    try:
        if want_include:
            result = include_blocks(_load_page(catalog, args.page), block_ids, at=moment)
            if args.apply:
                pages.save_page(catalog, result["page"], page_id=args.page, apply=True)
            report = result
        else:
            extractor = semantics
            if args.apply and extractor is None:
                # 抽取角色的身份走同一张角色表（EXTRACT_PROVIDER / EXTRACT_MODEL）
                extractor = HttpSemantics(load_role_config(EXTRACT_ROLE),
                                          args.runs_dir or _default_runs_dir())
            report = run_intake(catalog, args.page, semantics=extractor,
                                at=moment, apply=args.apply)
    except ApiError as exc:
        print(json.dumps({"ok": False, "error": exc.payload(),
                          "warnings": exc.warnings, "skipped": []},
                         ensure_ascii=False, indent=2))
        print(f"[{exc.code}] {exc.message}", file=sys.stderr)
        return 2
    except ModelUnavailable as exc:
        # D1：模型失败 → 502 那一个码，页文件一个字节都没动（写回只在全部块判完之后）
        err = errors.model_unavailable(str(exc), pid=args.page)
        print(json.dumps({"ok": False, "error": err.payload(),
                          "warnings": err.warnings, "skipped": []},
                         ensure_ascii=False, indent=2))
        print(f"[model_unavailable] {err.message}", file=sys.stderr)
        return 2
    except OSError as exc:
        # D1：盘上的失败（文件不在／无权限／盘满）也是**结构化信封**，不许裸回溯。
        # 写盘是「先写临时文件再原子替换」，所以到这儿的失败不会留下半个页文件。
        err = errors.filesystem_error(exc)
        print(json.dumps({"ok": False, "error": err.payload(),
                          "warnings": err.warnings, "skipped": []},
                         ensure_ascii=False, indent=2))
        print(f"[internal_error] {err.message}", file=sys.stderr)
        return 2
    except ValueError as exc:
        # 坏配置（provider 不在白名单、阈值 NaN…）——**起不来**，不许静默降级
        print(f"配置有问题：{exc}", file=sys.stderr)
        return 2

    warnings = report.get("warnings") or []
    data = {key: value for key, value in report.items() if key != "warnings"}
    print(json.dumps({"ok": True, "data": data, "warnings": warnings, "skipped": []},
                     ensure_ascii=False, indent=2))

    verdict = "已写入" if args.apply else "预演（页文件一个字节都没写；要写加 --apply）"
    if want_include:
        summary = report["summary"]
        print(f"{verdict}：补收 {summary['included']} 块，"
              f"{summary['already_kept']} 块本来就在库里，"
              f"{summary['unknown']} 个块 id 在页上找不到", file=sys.stderr)
    else:
        counts = report["counts"]
        print(f"{verdict}：这一页 {counts['blocks']} 块 —— 收 {counts['kept']} / "
              f"不收 {counts['dropped']} / 待定 {counts['pending']}；"
              f"{report['not_kept']['message']}", file=sys.stderr)
    for warning in warnings:
        print(f"[{warning['level']}] {warning['code']}：{warning['message']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
