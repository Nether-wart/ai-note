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

    def payload(self) -> dict:
        out: dict = {"code": self.code, "reason": self.reason, "message": self.message}
        if self.hint:
            out["hint"] = self.hint
        if self.details:
            out["details"] = self.details
        return out


def bad_request(message: str, **details) -> ApiError:
    return ApiError(400, "bad_request", message, details=details)


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
