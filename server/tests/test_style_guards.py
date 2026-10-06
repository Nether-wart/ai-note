"""写作纪律的判据：**能红的那种**。

这一份不是风格洁癖，是一条真实事故的固化：同一个会话里 **6 次**把中文散文写进
标识符（`def` 名字里的空格、名字里的引号、以及最阴的**全角括号**），症状一律是
`SyntaxError: invalid character '（' (U+FF08)`，而要等到 pytest **收集**阶段才发现——
那时整份文件已经写完了。写成判据，它就在 `pytest` 里当场红，而不是等我写完。

为什么不能靠"注意点"：那 6 次里后 3 次是**在写完 `docs/agents/pitfalls.md` 那条之后**发生的。
文档拦不住的，让判据来拦。
"""

from __future__ import annotations

import re
from pathlib import Path

#: 标识符：Unicode 的"词字符"（含中文）＋ 下划线。**空格、引号、全角括号都不是**
#: ——半角括号在参数表里合法，所以下面只取 `def`/`class` 与参数表之间的那一段。
IDENTIFIER = re.compile(r"\w+")
# 名字那一段一直取到 `(` 或 `:` 之前——**中间的空格也要取进来**，
# 否则 `def test_a b():` 只会被取到 `test_a`（一个合法标识符），那一档就漏了。
DEFINITION = re.compile(r"\s*(?:async\s+)?(?:def|class)\s+(.*?)\s*(?:\(|:)")


def offenders_in(text: str) -> list[str]:
    """扫一份源码，返回**名字那一段不合法**的定义（行内容）。"""
    found = []
    for line in text.splitlines():
        match = DEFINITION.match(line)
        if match and not IDENTIFIER.fullmatch(match.group(1).strip()):
            found.append(line.strip())
    return found


def test_这条判据对坏写法会响_它不是永远绿的():
    assert offenders_in("def test_数字变了或丢了就退回（最坏的一种）():")
    assert offenders_in('def test_自检所以"什么时候探过"查得到():')
    assert offenders_in("def test_a b():")
    # 对合法写法要放行：中文名字、下划线、参数表里的括号与逗号都不算错
    assert offenders_in("def test_数字变了或丢了就退回_这是最坏的一种():") == []
    assert offenders_in("def test_x(a, b=1) -> None:") == []
    assert offenders_in("class RoleConfig(RoleConfig):") == []


def test_server_下没有定义名里带空格或全角标点的():
    offenders = []
    for path in sorted(Path("server").rglob("*.py")):
        for line in offenders_in(path.read_text(encoding="utf-8")):
            offenders.append(f"{path}: {line}")
    assert offenders == [], "标识符里只能有字母数字下划线（中文也算字符），别的都是事故：\n  " + "\n  ".join(offenders)
