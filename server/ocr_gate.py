"""OCR 文本 →（文本模型修好的）转录：**这一块过不过得去**（纯逻辑）。

背景：数学公式**不能一律**交给文字模型修，因为有两种坏法要分开：

- **字符级噪声**（`∃` 读成 `3`、`∈` 读成 `E`、`≤`／`<` 混）：文字模型**能修**——信息都在，
  它做的是"把一串字符整成 LaTeX"。
- **结构丢失**（分式被拉平、上下标变同行、根号与矩阵没了）：**修不了**——二维结构在文本流里
  根本没被记下来，模型只能**编一个看起来合合理理的**。

所以这里只做**能自动判的那部分**：三道闸，任意一道不过 → 这一块**退回视觉模型**。
这与"简报数字闸门"同源：能自动判的必须自动判，判不了的必须显式路由，**不许含糊过去**。

⚠ **它判不出什么**（写在前面，免得被当成万能）：只保住了符号与数字、结构却被拉平的修补，
它能过去。所以默认阈值取**保守**（宁可退回视觉模型），而下面两颗阈值
**必须拿真实卷子标定**——现在还没有数据，谁都不许拍脑袋改它们。
"""

from __future__ import annotations

import re
from collections import Counter

#: 修补**允许新加**的结构标记（LaTeX 的骨架）。除此之外的符号一律不许凭空出现。
STRUCTURAL_ALLOWED = frozenset({"{", "}", "\\", "$", "^", "_"})

#: 参与"符号守恒"的字符：带数学含义的、**不是普通标点**的那些。
#: `(` `)` `[` `]` `,` `.` `:` `;` `!` 之类故意**不算**——它们在普通句子里太常见，
#: 算进去会把好修补也拒掉（代价是"补了一对括号"这类改动这里抓不到，见文件末尾的已知边界）。
MATH_SYMBOLS = frozenset(
    "∃∀∈∉∋≤≥≠±×÷→←↔⇒⇔∑∏∫∮√∞πθαβγδεζηλμνξρστφχψωΔΩ≈≡∼∝⊥∥∠°′″∪∩⊂⊆⊃⊇∅ℝℕℤℚ"
    "+-*/=<>^_{\\$%"
)

#: 归一化编辑距离上限。**保守**：超过它就算"在重写而不是修补"。
#: ⚠ 这一颗与上面那张白名单**都还没有实测数据**（本地 OCR 还没接上）。
DEFAULT_MAX_DISTANCE = 0.25


#: LaTeX 命令 → 它对应的符号。**比距离之前必须先过这一张表**：
#: `∃x ∈ [1,4]` 修成 `$\exists x \in [1,4]$` 在字符数上暴涨三倍，
#: 拿原始字符串算编辑距离会把**正确的**修补判成"重写"（真踩过）。
LATEX_ALIASES = {
    r"\exists": "∃", r"\forall": "∀", r"\notin": "∉", r"\in": "∈",
    r"\leq": "≤", r"\le": "≤", r"\geq": "≥", r"\ge": "≥",
    r"\neq": "≠", r"\ne": "≠", r"\pm": "±", r"\mp": "∓",
    r"\times": "×", r"\div": "÷", r"\cdot": "·",
    r"\rightarrow": "→", r"\to": "→", r"\leftarrow": "←",
    r"\Leftrightarrow": "⇔", r"\Rightarrow": "⇒", r"\iff": "⇔",
    r"\sum": "∑", r"\prod": "∏", r"\int": "∫", r"\oint": "∮",
    r"\sqrt": "√", r"\infty": "∞", r"\pi": "π", r"\theta": "θ",
    r"\alpha": "α", r"\beta": "β", r"\gamma": "γ", r"\delta": "δ",
    r"\lambda": "λ", r"\mu": "μ", r"\sigma": "σ", r"\phi": "φ", r"\omega": "ω",
    r"\Delta": "Δ", r"\Omega": "Ω", r"\angle": "∠", r"\perp": "⊥",
    r"\parallel": "∥", r"\cup": "∪", r"\cap": "∩",
    r"\subseteq": "⊆", r"\subset": "⊂", r"\supseteq": "⊇", r"\supset": "⊃",
    r"\varnothing": "∅", r"\emptyset": "∅", r"\approx": "≈", r"\equiv": "≡",
    r"\frac": "/", r"\dfrac": "/", r"\tfrac": "/",
}


def canonical(text: str, *, keep_space: bool = False) -> str:
    """把一段文本归一成**可比**的形状：LaTeX 命令还原成符号、去掉 `$` 与花括号。

    `keep_space=False`（默认）连空白一起去掉——**比编辑距离时用这个**：
    换行与空格怎么变都不该算成"重写"。

    `keep_space=True` 留着空白——**比数字时必须用这个**：`答 1 2` 与 `答 12` 不是一回事
    （前者可能是两个数，后者是一个数），把空白吃掉就再也分不出来了。这个区别真踩过：
    一开始两处都用去空白的形式，于是"把 1 2 粘成 12"这种改动会被放行。
    """
    out = text or ""
    for command, glyph in sorted(LATEX_ALIASES.items(), key=lambda item: -len(item[0])):
        out = out.replace(command, glyph)
    out = out.replace("$", "").replace("{", "").replace("}", "")
    return out if keep_space else re.sub(r"\s+", "", out)


def _symbols(text: str) -> Counter:
    return Counter(char for char in canonical(text) if char in MATH_SYMBOLS)


def _numbers(text: str) -> Counter:
    # ⚠ 这里要**留着空白**：`1 2` 与 `12` 对数字守恒来说是两件事（见 `canonical` 的注释）
    return Counter(re.findall(r"\d+", canonical(text, keep_space=True)))


def _squeeze(text: str) -> str:
    """比距离之前的那一步：归一 ＋ 去掉空白（换行与空格怎么变都不算"重写"）。"""
    return canonical(text)


def edit_distance(left: str, right: str) -> int:
    """Levenshtein（两行滚动，纯标准库）。"""
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for i, char_left in enumerate(left, start=1):
        current = [i]
        for j, char_right in enumerate(right, start=1):
            current.append(min(
                previous[j] + 1,                       # 删
                current[j - 1] + 1,                    # 增
                previous[j - 1] + (char_left != char_right),   # 换
            ))
        previous = current
    return previous[-1]


def check(source: str, repaired: str, *, max_distance: float = DEFAULT_MAX_DISTANCE) -> dict:
    """三道闸。返回**结构化**结论（`{"ok", "code", "detail"}`），不打日志、不抛异常。

    `code` 取值：`accept`／`empty_source`／`symbol_invented`／`number_changed`／`too_far_rewritten`。
    """
    source = source or ""
    repaired = repaired or ""

    if not _squeeze(source):
        # 原文都没有，就无从"修补"——这一块只能走视觉模型
        return {"ok": False, "code": "empty_source", "detail": {}}

    if not _squeeze(repaired):
        return {"ok": False, "code": "empty_source",
                "detail": {"why": "修补结果是空的"}}

    # 闸一：符号守恒（只许加白名单里的结构标记，别的符号必须在原文里找得到）
    source_symbols = _symbols(source)
    invented = _symbols(repaired) - source_symbols - Counter(STRUCTURAL_ALLOWED)
    if invented:
        return {"ok": False, "code": "symbol_invented",
                "detail": {"symbols": sorted(invented),
                           "why": "修补版里出现了原文没有的数学符号——那是编的，不是修的"}}

    # 闸二：数字守恒（既不许凭空多，也不许悄悄少）
    source_numbers = _numbers(source)
    repaired_numbers = _numbers(repaired)
    if source_numbers != repaired_numbers:
        return {"ok": False, "code": "number_changed",
                "detail": {"lost": sorted((source_numbers - repaired_numbers).elements()),
                           "added": sorted((repaired_numbers - source_numbers).elements()),
                           "why": "数字变了或丢了——这是最坏的一种修补"}}

    # 闸三：不许重写（归一化编辑距离）
    left, right = _squeeze(source), _squeeze(repaired)
    distance = edit_distance(left, right)
    ratio = distance / max(len(left), len(right))
    if ratio > max_distance:
        return {"ok": False, "code": "too_far_rewritten",
                "detail": {"ratio": round(ratio, 3), "limit": max_distance,
                           "why": "改得太多，已经是在重写而不是修补"}}

    return {"ok": True, "code": "accept",
            "detail": {"ratio": round(ratio, 3), "limit": max_distance}}

# 已知边界（写给下一个人，免得以为它没漏）：
# 1. 一对括号／一个逗号被补上，这里抓不到（它们不在 `MATH_SYMBOLS` 里，理由见那张表的注释）。
# 2. 结构被拉平但符号与数字恰好守恒的修补，**能过去**——所以阈值要保守，
#    并且这一块最终应当由"同一张图再问一次视觉模型"来兜底，而不是靠这道闸兜底。
