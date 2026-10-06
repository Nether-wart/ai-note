"""判定映射的验收：不联网、不问模型、不花钱，只验规则。

判定只有三种取值（对／错／看不清），映射表里每一条都有反直觉之处：

  · 置信度只给「对」设闸门；「错」不看置信度——假「错」只浪费一点时间，
    假「对」会让一道题白拿一次掌握计数、甚至悄悄毕业、从默认打印清单里消失；
  · 低置信度、缺置信度、越界置信度一律落向「看不清」，**绝不落向「对」**；
  · 模型调用失败不是判定——不产生任何可记录的结果（否则重做历史里会凭空多出
    一条没发生过的重做，冷却也被静默重置）。

阈值是具名常量（`judge.CONFIDENCE_THRESHOLD`，初值 0.9），不是散落的字面量。

跑法：python3 -m pytest server/tests/test_judge.py
"""
from __future__ import annotations

import json

import pytest

from server import judge as J

# 巨整数：json 真能给出这种数（`float()` 转它会直接 OverflowError，而不是落向看不清）
HUGE_INT = json.loads("1" + "0" * 400)

# 模型返回、阈值、期望判定、期望「该不该记这次重做」
CASES: list[tuple[str, object, float, str, bool]] = [
    # —— 判等价：置信度过闸门才给「对」
    ("置信度 0.95、判等价 → 对",
     {"equivalent": True, "confidence": 0.95}, 0.9, J.VERDICT_CORRECT, True),
    ("置信度恰好等于阈值 → 对（取 ≥ 一侧）",
     {"equivalent": True, "confidence": 0.9}, 0.9, J.VERDICT_CORRECT, True),
    ("置信度 0.8、判等价 → 看不清",
     {"equivalent": True, "confidence": 0.8}, 0.9, J.VERDICT_UNREADABLE, True),
    ("差一点不到阈值 → 看不清",
     {"equivalent": True, "confidence": 0.8999999}, 0.9, J.VERDICT_UNREADABLE, True),
    ("阈值可覆盖：阈值 0.5 下置信度 0.6 的等价 → 对",
     {"equivalent": True, "confidence": 0.6}, 0.5, J.VERDICT_CORRECT, True),
    ("阈值可覆盖：阈值 0.95 下置信度 0.9 的等价 → 看不清",
     {"equivalent": True, "confidence": 0.9}, 0.95, J.VERDICT_UNREADABLE, True),

    # —— 判不等价：置信度不设闸门，一律「错」
    ("判不等价、置信度低 → 错（置信度不设闸门）",
     {"equivalent": False, "confidence": 0.1}, 0.9, J.VERDICT_WRONG, True),
    ("判不等价、根本没给置信度 → 错",
     {"equivalent": False}, 0.9, J.VERDICT_WRONG, True),
    ("判不等价、置信度越界（95 当成百分数）→ 错",
     {"equivalent": False, "confidence": 95}, 0.9, J.VERDICT_WRONG, True),
    ("判不等价、置信度不是数值 → 错",
     {"equivalent": False, "confidence": "低"}, 0.9, J.VERDICT_WRONG, True),

    # —— 输出残缺／不可解析：看不清（记下这次重做，但不推进也不清零）
    ("没有 equivalent 字段 → 看不清",
     {"confidence": 0.99}, 0.9, J.VERDICT_UNREADABLE, True),
    ("equivalent 是 1（不是布尔）→ 看不清",
     {"equivalent": 1, "confidence": 0.99}, 0.9, J.VERDICT_UNREADABLE, True),
    ("equivalent 是字符串 'true' → 看不清",
     {"equivalent": "true", "confidence": 0.99}, 0.9, J.VERDICT_UNREADABLE, True),
    ("equivalent 是 null → 看不清",
     {"equivalent": None, "confidence": 0.99}, 0.9, J.VERDICT_UNREADABLE, True),
    ("输出不是 JSON 对象（没解析出来）→ 看不清",
     "抱歉，我无法判断。", 0.9, J.VERDICT_UNREADABLE, True),
    ("输出为空 → 看不清",
     None, 0.9, J.VERDICT_UNREADABLE, True),

    # —— 判等价但置信度不可用：拿不到「≥阈值」的证据就不给「对」
    ("判等价但没给置信度 → 看不清",
     {"equivalent": True}, 0.9, J.VERDICT_UNREADABLE, True),
    ("判等价但置信度是字符串 → 看不清",
     {"equivalent": True, "confidence": "0.99"}, 0.9, J.VERDICT_UNREADABLE, True),
    ("判等价但置信度越界（95 当成百分数）→ 看不清",
     {"equivalent": True, "confidence": 95}, 0.9, J.VERDICT_UNREADABLE, True),
    ("判等价但置信度是 NaN → 看不清",
     {"equivalent": True, "confidence": float("nan")}, 0.9, J.VERDICT_UNREADABLE, True),
    ("判等价但置信度是 true（布尔不是数值）→ 看不清",
     {"equivalent": True, "confidence": True}, 0.9, J.VERDICT_UNREADABLE, True),
]


@pytest.mark.parametrize("name,raw,threshold,verdict,should_record", CASES,
                         ids=[c[0] for c in CASES])
def test_mapping_table(name, raw, threshold, verdict, should_record):
    got = J.judgment_from_output(raw, threshold=threshold)
    assert got.verdict == verdict
    assert got.should_record is should_record
    assert got.note.strip(), "不许静默：每个结果都要有一句「我做了什么／跳过了什么」"
    assert got.source == J.SOURCE_AUTO


def test_low_confidence_never_reaches_correct():
    """低置信度永不到「对」——这是整套设计唯一禁止的错。"""
    for conf in [0.0, 0.1, 0.5, 0.89, 0.899999, 0.9 - 1e-9]:
        assert J.judgment_from_output(
            {"equivalent": True, "confidence": conf}).verdict != J.VERDICT_CORRECT


def test_threshold_is_a_named_constant():
    """阈值收在模块常量里，不埋成字面量；默认阈值就是它。"""
    assert J.CONFIDENCE_THRESHOLD == 0.9
    default = J.judgment_from_output({"equivalent": True, "confidence": 0.95})
    assert default.verdict == J.VERDICT_CORRECT


def test_confidence_is_echoed_and_reason_kept():
    """置信度与理由照抄进判定里：日后要能回答「这条判定当时有多确定」。"""
    got = J.judgment_from_output(
        {"equivalent": True, "confidence": 0.95, "reason": "2/√3 与 2√3/3 等价"})
    assert got.confidence == 0.95
    assert got.reason == "2/√3 与 2√3/3 等价"


def test_provenance_fields_have_a_place():
    """来源与模型留档的位置：判定要能回答「这条判定是谁给的」。"""
    got = J.judgment_from_output({"equivalent": True, "confidence": 0.95},
                                 provider="dashscope", model="qwen3-vl-plus")
    assert (got.source, got.provider, got.model) == (J.SOURCE_AUTO, "dashscope", "qwen3-vl-plus")


@pytest.mark.parametrize("reason", ["网络超时", "缺密钥 DASHSCOPE_API_KEY"])
def test_call_failure_is_not_a_judgment(reason):
    """模型调用失败 ≠ 看不清：失败不产生任何判定，因此不留任何重做记录。"""
    got = J.judgment_from_call_failure(reason, provider="dashscope", model="qwen3-vl-plus")
    assert got.verdict is None
    assert got.should_record is False
    assert got.confidence is None
    assert got.source is None
    assert reason in got.note and got.note.strip()


# —— 加固：独立证伪钉住的两个反例（崩溃、以及静默绕过闸门）

@pytest.mark.parametrize("name,raw,verdict", [
    ("巨整数置信度 10**400 → 看不清，不崩",
     {"equivalent": True, "confidence": HUGE_INT}, J.VERDICT_UNREADABLE),
    ("巨负整数置信度 → 看不清，不崩",
     {"equivalent": True, "confidence": -HUGE_INT}, J.VERDICT_UNREADABLE),
    ("巨整数置信度 + 判不等价 → 错，不崩",
     {"equivalent": False, "confidence": HUGE_INT}, J.VERDICT_WRONG),
], ids=lambda v: v if isinstance(v, str) else "")
def test_huge_integer_confidence_is_unusable_not_a_crash(name, raw, verdict):
    """越界的数就是不可用的置信度——先判范围再转 float，巨整数不该把端点打成 500。"""
    got = J.judgment_from_output(raw)
    assert got.verdict == verdict
    assert got.should_record is True


BAD_THRESHOLDS: list[tuple[str, object]] = [
    ("NaN", float("nan")),
    ("正无穷", float("inf")),
    ("负无穷", float("-inf")),
    ("越界 1.5", 1.5),
    ("越界 -0.1", -0.1),
    ("字符串", "0.9"),
    ("None", None),
    ("布尔 True", True),
    ("巨整数 10**400", HUGE_INT),
]


@pytest.mark.parametrize("name,threshold", BAD_THRESHOLDS, ids=[b[0] for b in BAD_THRESHOLDS])
@pytest.mark.parametrize("raw", [{"equivalent": True, "confidence": 0.95},
                                 {"equivalent": False}],
                         ids=["判等价", "判不等价"])
def test_bad_threshold_screams_instead_of_silently_passing(name, threshold, raw):
    """坏阈值是配置错，必须当场喊出来。

    NaN 阈值下 `confidence < threshold` 恒为 False，等于把闸门整个拆掉：
    0.0 的置信度也会变成「对」——这是这套设计唯一禁止的错，宁可抛异常。
    校验对任何输入都先发生（判不等价那条也一样），坏配置不许只在某些路径上发作。
    """
    with pytest.raises(ValueError) as err:
        J.judgment_from_output(raw, threshold=threshold)
    assert str(err.value).strip()


def test_threshold_bounds_are_legal():
    """0.0 与 1.0 是合法阈值：前者全放行，后者只认满置信度。"""
    assert J.judgment_from_output({"equivalent": True, "confidence": 0.0},
                                  threshold=0.0).verdict == J.VERDICT_CORRECT
    assert J.judgment_from_output({"equivalent": True, "confidence": 1.0},
                                  threshold=1.0).verdict == J.VERDICT_CORRECT
    assert J.judgment_from_output({"equivalent": True, "confidence": 0.99},
                                  threshold=1.0).verdict == J.VERDICT_UNREADABLE


def test_validate_threshold_is_reusable_at_config_load():
    """#11 把阈值收成一份常量／配置时，能在装载处先校验，而不是等第一次判定。"""
    assert J.validate_threshold(0.9) == 0.9
    with pytest.raises(ValueError, match="阈值"):
        J.validate_threshold(float("nan"))


class NeverGreater(float):
    """重载比较使其恒假：校验若被丢掉、比较仍拿原值，闸门就被绕开了。"""

    def __gt__(self, other):
        return False

    def __lt__(self, other):
        return False


def test_validated_threshold_is_the_one_compared():
    """校验的返回值必须真的参与比较——「校验过了」与「拿去比的值」要是同一个东西。

    这是本模块存在的那个失败模式的另一种形态：闸门被绕开。校验通过后若继续拿调用方
    给的原值去比，一个比较运算被重载的对象就能让 0.5 的置信度越过 0.9 的阈值判成「对」。
    """
    threshold = NeverGreater(0.9)
    assert J.validate_threshold(threshold) == 0.9
    assert J.judgment_from_output({"equivalent": True, "confidence": 0.5},
                                  threshold=threshold).verdict == J.VERDICT_UNREADABLE
    assert J.judgment_from_output({"equivalent": True, "confidence": 0.95},
                                  threshold=threshold).verdict == J.VERDICT_CORRECT
