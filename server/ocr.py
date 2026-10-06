"""本地 OCR 这条路的**接缝**（第一步：先把形状钉住——**还没有接线**）。

语义照抄 `segmenter=None` 那条既有的约定：**不注入引擎 ＝ OCR 不可用**，调用方必须明说，
绝不假装、绝不编一段文本来顶上。

三条边界，写清楚免得被误读：

1. **它是本地引擎，不是模型端点**。所以它**不进** `ROLE_DEFAULTS`（那四个是 HTTP 模型角色），
   而是像 `segmenter` 那样**注入**。这样"没装 PaddleOCR"就落在"这个能力不可用"上，
   而不是落在"配置写错了"上——两者的处置完全不同。
2. **输入是 `images.clean`（擦除手写后的图）**：印刷体是这类引擎的强项，而它最弱的手写
   已经被管道擦掉了。这不是巧合，是这份数据结构刚好合拍。
3. **输出是"文本行 ＋ 框"，不是这个仓库要的转录**。转录要的是结构化的（题号／题型／选项／
   正解），OCR 给的是一串行。中间那一步"整结构"交给**纯文本模型**（比视觉调用便宜一个量级）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: 引擎名进 `provenance`，所以它是**读数的一部分**，不许写"unknown"糊过去。
UNAVAILABLE_ENGINE = "unavailable"


@dataclass(frozen=True)
class OcrLine:
    """一行识别结果。

    `box` 用**归一化的 xywh**（与页文件的块框同一套口径）——不同引擎给的框不一样，
    但在这个仓库里它们最终都要能跟页坐标对上，所以进接缝之前先归一。
    `confidence` 允许是 `None`：**"这家不报置信度"与"置信度是 0"是两件事**。
    """
    text: str
    box: list[float] | None = None
    confidence: float | None = None


@dataclass(frozen=True)
class OcrResult:
    """一次识别的结果 ＋ **是谁给的**。

    `engine` 与 `model` 一定要有：转录最终会写进题卡的 `provenance`，
    而"这段文字是谁读出来的"是那份记录里最要紧的一格。
    """
    lines: list[OcrLine] = field(default_factory=list)
    engine: str = UNAVAILABLE_ENGINE
    model: str | None = None
    #: 引擎自己报的告警（照原话带出去，不翻译）
    warnings: list[str] = field(default_factory=list)

    @property
    def available(self) -> bool:
        return self.engine != UNAVAILABLE_ENGINE

    def text(self) -> str:
        """把所有行拼成一段文本（给"整结构"那一步用）。"""
        return "\n".join(line.text for line in self.lines if line.text.strip())


def unavailable(reason: str | None = None) -> OcrResult:
    """**OCR 不可用**这一档的唯一构造入口。

    `reason` 是给日志/读数看的一句话（例如"没有注入引擎"或"PaddleOCR 没装"）；
    它与"识别出来了但一行都没有"是**两件不同的事**，所以不走同一个形状。
    """
    warnings = [reason] if reason else []
    return OcrResult(lines=[], engine=UNAVAILABLE_ENGINE, model=None, warnings=warnings)


def is_available(engine) -> bool:
    """注入进来的东西是不是一个**能用**的引擎。

    判据只有一条：可调用。**不试探、不预热**——试探会在这里花掉一次真实调用，
    而这条路的调用是要花钱/花时间的。
    """
    return callable(engine)


def read(engine, image: bytes, *, name: str = "") -> OcrResult:
    """跑一次 OCR。`engine` 的协议是 `(image_bytes, name) -> OcrResult`。

    不注入 ＝ **明确不可用**（返回 `unavailable()`），而不是抛异常：调用方要能照原样
    把"这条路现在走不通"报给用户，而不是让一个异常冒到 HTTP 层变成 500。
    """
    if not is_available(engine):
        return unavailable("没有注入 OCR 引擎（本地 OCR 这条路还没接上）")
    result = engine(image, name)
    if not isinstance(result, OcrResult):
        # 自造引擎返回了别的东西：这是**接线错误**，要说出来，不能当成空结果
        raise TypeError(
            f"OCR 引擎要返回 OcrResult，拿到的是 {type(result).__name__}——"
            "这不是'没识别出来'，是接错了"
        )
    return result
