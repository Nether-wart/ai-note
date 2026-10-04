"""契约里的错误形状（docs/contracts/http-api-v0.md §9）。

错误必须是**数据**，不是一段 HTML 或一句裸文本：界面照 `message` 原话显示，
按 `code` 决定怎么处理，`details` 里点名是哪个参数、值是什么、允许什么
（ADR 0007 第 6 条：不许静默）。
"""

from __future__ import annotations


class ApiError(Exception):
    """一个已知的、能说清楚的失败。`status` 是它该出的 HTTP 状态码。"""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        reason: str | None = None,
        hint: str | None = None,
        details: dict | None = None,
        warnings: list | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        # `code` 是信封级类别，`reason` 是机器可读的细因（编排裁决 D1）。
        # 没有更细的原因时两者相同——但**字段永远在**，界面不必写两种分支。
        self.reason = reason or code
        self.message = message
        self.hint = hint
        self.details = details or {}
        # 失败也可以带警告：拒绝一次作答时，那张卡自己的自检结果要一并带上
        # （契约 §10.1：`warnings[]` = 该卡的自检警告）。绝不静默。
        self.warnings = warnings or []

    def payload(self) -> dict:
        out: dict = {"code": self.code, "reason": self.reason, "message": self.message}
        if self.hint:
            out["hint"] = self.hint
        if self.details:
            out["details"] = self.details
        return out


def bad_request(message: str, *, hint: str | None = None, **details) -> ApiError:
    return ApiError(400, "bad_request", message, hint=hint, details=details)


def not_found(message: str, hint: str | None = None, **details) -> ApiError:
    return ApiError(404, "not_found", message, hint=hint, details=details)


def method_not_allowed(method: str, allowed: list[str]) -> ApiError:
    return ApiError(
        405,
        "method_not_allowed",
        f"这个端点不接受 {method}",
        hint="只支持 " + " / ".join(allowed),
        details={"method": method, "allowed": allowed},
    )


def not_auto_judgeable(reason: str, message: str, *, pid: str, warnings: list | None = None) -> ApiError:
    """契约 §9：请求本身合法，只是这道题**不能**自动判定 → **422，不是 400/500**。

    `reason` 与 `message` 必须来自 `server/autojudge.py` 的唯一那份实现
    （编排裁决 D1、D3）：界面照 `message` 原话显示，测试按 `reason` 断言。
    """
    return ApiError(
        422,
        "not_auto_judgeable",
        message,
        reason=reason,
        hint="这道题只能在纸上重做（人工确认）；屏幕重做只收字符串答案",
        details={"id": pid},
        warnings=warnings,
    )


def model_unavailable(message: str, *, pid: str, provider: str | None = None,
                      model: str | None = None) -> ApiError:
    """契约 §9：模型调用失败 → **502**（上游失败），且这一次重做**不留任何记录**。

    把它记成「看不清」会在重做历史里造出一条没发生过的重做，并静默推进
    「上次重做」时刻、凭空重置冷却（编排裁决 D1、spec #1 US 14）。
    """
    details = {"id": pid}
    if provider:
        details["provider"] = provider
    if model:
        details["model"] = model
    return ApiError(
        502,
        "model_unavailable",
        message,
        reason="model_unavailable",
        hint="可以直接重试；这一次没有留下任何记录",
        details=details,
    )
