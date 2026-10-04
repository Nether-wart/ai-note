"""判定映射：把判定角色的返回映射成三值判定（对／错／看不清）。

**纯逻辑**：不联网、不读盘、不问模型，脱网可测。这套映射只有这一份实现。

规则（spec #1「映射成判定」）：

  · 置信度 ≥ 阈值且模型判等价 → 对
  · 模型判不等价 → 错，**置信度不设闸门**（假「错」只浪费一点时间，而拦住它
    只会让一道错题留在库里；代价不对称的另一半）
  · 置信度 < 阈值、缺置信度、置信度越界（含巨整数）→ 看不清
  · 输出残缺／不可解析（不是对象、没有 `equivalent`、`equivalent` 不是布尔）→ 看不清
  · **低置信度一律落向看不清，绝不落向对**：假「对」会让一道题白拿一次掌握
    计数、甚至悄悄毕业、从默认打印清单里消失，这是整套设计唯一禁止的错
  · 调用失败（网络／超时／缺密钥）**不是一次判定**，见 `judgment_from_call_failure`
  · **坏阈值（NaN／无穷／越出 [0,1]）抛 `ValueError`**，见 `validate_threshold`：
    坏配置必须喊出来，静默地绕过闸门正是上面那条禁止的错

阈值是这里的模块常量（初值 0.9），不埋成散落的字面量；往高调最多多几次人工
确认，往低调则制造这套设计唯一禁止的错。

判定成立时返回的 `Judgment` 就是「判定」这个词的全部内容：取值 + 来源 + 置信度
（CONTEXT.md），并带上留档用的 provider／model 与一句说明这次到底发生什么的
note（ADR 0007「不许静默」）。
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

CONFIDENCE_THRESHOLD = 0.9   # 判定置信度闸门，只作用于「对」

VERDICT_CORRECT = "correct"
VERDICT_WRONG = "wrong"
VERDICT_UNREADABLE = "unreadable"

SOURCE_AUTO = "auto"         # 自动判定；与 proto 的 --source 取值一致（human = 人工确认）


@dataclass(frozen=True)
class Judgment:
    """一次判定的结果。

    `verdict` 为 None 表示**这次没有判定**（调用失败），调用方不得为它写下任何
    重做记录；其余字段见 `judgment_from_output` 的文档。
    """
    verdict: str | None
    confidence: float | None
    reason: str
    source: str | None
    provider: str | None
    model: str | None
    note: str

    @property
    def should_record(self) -> bool:
        """要不要把这次重做写进历史：没有判定就什么都不许写。"""
        return self.verdict is not None


def judgment_from_output(raw, *, threshold: float = CONFIDENCE_THRESHOLD,
                         provider: str | None = None, model: str | None = None) -> Judgment:
    """把判定角色**已解析**的返回映射成判定。

    `raw` 是模型输出解析后的对象；不是对象（None、没解析出来的原文、数组……）
    等同于「残缺」，落向看不清——解析成 JSON 不是这个函数的职责。

    `threshold` 默认取模块常量，只为测试边界留出显式入口。**坏阈值当场抛
    `ValueError`**（见 `validate_threshold`）：NaN 阈值下 `confidence < threshold`
    恒为 False，等于把闸门整个拆掉。

    provider／model 由调用方（知道角色配置的那一层）传入并照抄进结果，用于留档；
    这里绝不猜。
    """
    validate_threshold(threshold)   # 先校验配置：坏阈值在任何输入上都要喊，不许静默
    is_mapping = isinstance(raw, Mapping)
    confidence = _as_confidence(raw.get("confidence")) if is_mapping else None
    reason = _as_reason(raw.get("reason")) if is_mapping else ""

    if not is_mapping:
        return _unreadable(confidence, reason, provider, model,
                           f"模型的输出不是一个 JSON 对象（拿到的是 {type(raw).__name__}）"
                           "——残缺的输出一律落向看不清，不猜")

    equivalent = raw.get("equivalent")
    if not isinstance(equivalent, bool):
        return _unreadable(confidence, reason, provider, model,
                           f"'equivalent' 不是布尔（拿到的是 {equivalent!r}）"
                           "——残缺的输出一律落向看不清，不猜")

    if not equivalent:
        return Judgment(
            VERDICT_WRONG, confidence, reason, SOURCE_AUTO, provider, model,
            "模型判不等价 → 错（判错不看置信度：" + _confidence_words(confidence) + "）")

    if confidence is None:
        return _unreadable(confidence, reason, provider, model,
                           "'equivalent' 为真，但置信度缺失／不是数值／越界"
                           "——拿不到「≥ 阈值」的证据就不给对")

    if confidence < threshold:
        return _unreadable(confidence, reason, provider, model,
                           f"置信度 {confidence} 低于阈值 {threshold}"
                           "——低置信度落向看不清，绝不落向对")

    return Judgment(
        VERDICT_CORRECT, confidence, reason, SOURCE_AUTO, provider, model,
        f"模型判等价且置信度 {confidence} ≥ 阈值 {threshold} → 对")


def judgment_from_call_failure(reason: str, *, provider: str | None = None,
                               model: str | None = None) -> Judgment:
    """模型调用失败（网络／超时／缺密钥）**不是一次判定**。

    返回的判定 `verdict is None`：调用方必须返回错误让人重试，**不留下任何重做
    记录**。把它记成「看不清」会在重做历史里造出一条没发生过的重做，并静默推进
    「上次重做」时刻，于是冷却被凭空重置。
    """
    return Judgment(None, None, "", None, provider, model,
                    f"判定调用失败：{reason}——这次重做不留下任何记录，请重试")


def _unreadable(confidence: float | None, reason: str, provider: str | None,
                model: str | None, note: str) -> Judgment:
    """看不清是一次**判定**（照常记录），只是既不推进也不清零掌握。"""
    return Judgment(VERDICT_UNREADABLE, confidence, reason, SOURCE_AUTO, provider, model,
                    note + " → 看不清（记下这次重做，但不推进也不清零掌握）")


def validate_threshold(value) -> float:
    """校验阈值，坏配置**当场喊出来**（ADR 0007：不许静默）。

    NaN 阈值是本模块最阴的一处：`confidence < NaN` 恒为 False，闸门被整个绕过，
    0.0 的置信度也会判成「对」——正是这套设计唯一禁止的错。±inf 与越界的阈值同理
    （要么全放行、要么把一切都打成看不清），非数值与 bool 则根本不是阈值。

    #11 把阈值收成一份常量／配置时，可以在装载处调用这里先校验，不必等第一次判定。

    合法阈值：数值（bool 不算）、有限、落在 [0,1] 里。0.0 与 1.0 都合法。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"判定阈值必须是数值，拿到的是 {value!r}")
    # 先判范围再转 float：巨整数（json 能给出 10**400）转 float 会 OverflowError
    if not 0.0 <= value <= 1.0:
        raise ValueError(
            f"判定阈值必须落在 [0,1] 里，拿到的是 {value!r}——"
            "NaN／无穷／越界的阈值会把置信度闸门整个绕开，属于坏配置")
    return float(value)


def _as_confidence(value) -> float | None:
    """模型的置信度是否可用。

    bool 不是数值（`True` 会混成 1.0）；越界不可用——模型把 0.95 写成 95 时，
    照收就等于给每道题送一个「对」，正是唯一禁止的那个错。NaN 也在这里出局。
    范围先于 `float()` 判定：巨整数转 float 会 OverflowError，那不该是端点的 500。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if 0.0 <= value <= 1.0 else None


def _as_reason(value) -> str:
    return value if isinstance(value, str) else ""


def _confidence_words(confidence: float | None) -> str:
    return f"照记置信度 {confidence}" if confidence is not None else "模型没给出可用的置信度"
