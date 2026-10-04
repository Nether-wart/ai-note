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

from . import ink
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
