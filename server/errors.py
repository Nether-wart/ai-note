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


def attempt_not_found(raw: str, *, pid: str, available: list, warnings: list | None = None) -> ApiError:
    """定点修正（#6）：`attempt_at` 在重做历史里**没有**那一次 → **404**。

    不是 400：请求本身合法，只是它指的那一次不存在。也不是「落到最近一次」——
    那是原型最讨厌的静默降级。`available` 把**实际有哪些**时刻说出来（ADR 0007 第 6 条）。
    """
    return ApiError(
        404,
        "not_found",
        f"这道题的重做历史里没有 attempt_at = {raw!r} 那一次重做",
        reason="attempt_not_found",
        hint=("这几次重做的时刻：" + " / ".join(map(str, available))) if available
             else "这道题还没有任何重做记录",
        details={"id": pid, "attempt_at": raw, "available": available},
        warnings=warnings,
    )


def ambiguous_attempt_at(raw: str, *, pid: str, candidates: list,
                         warnings: list | None = None) -> ApiError:
    """定点修正（#6）：`attempt_at` 定位到**不止一次**重做（同一秒里做了两次）→ **409**。

    那几次确实存在，只是这个参数区分不了它们；挑一个就是「猜」。
    """
    return ApiError(
        409,
        "ambiguous_attempt_at",
        f"attempt_at = {raw!r} 对应 {len(candidates)} 次重做（同一秒里做了两次），"
        "定位不到唯一一次",
        reason="ambiguous_attempt_at",
        hint="这几次重做的时刻逐字相同，定点修正无法区分它们；先修数据或多给一位精度",
        details={"id": pid, "attempt_at": raw, "candidates": candidates},
        warnings=warnings,
    )


def page_id_mismatch(*, page_id: str, found, path) -> ApiError:
    """页里的 `id` 与**目标页 id** 不一致 → **400**，且一个字节都不写。

    页 id 是**文件名**（写盘只看调用方给的那个 id）；页里那个 `id` 字段是**内容**，
    只用于对账。按内容里的 id 拼路径就是「静默写到别的文件上」：`../problems/p-xxx`
    会整份覆盖一张真题卡，`someotherpage` 会写错文件却报另一个 `page_path`。

    `details.param == "page_id"`，HTTP 层可以把这个 400 原样透出去——**两层各守一次**
    是刻意的纵深防御（#12 的 `run_intake` 与 #14 的改页写入路径）。
    """
    return ApiError(
        400,
        "bad_request",
        f"页里的 id 与目标页 id 不一致：页里是 {found!r}，目标是 {page_id!r}"
        f"（页 id 就是文件名；内容里的 id 只用于对账）",
        reason="page_id_mismatch",
        hint="页 id 只允许字母、数字、点、下划线与连字符，且必须与目标页 id 逐字相同；"
             "拒绝写入是为了不把这份页 JSON 覆盖到别的文件上",
        details={"param": "page_id", "value": page_id, "found": found, "path": str(path)},
    )


def page_image_unsafe(*, page_id: str, image) -> ApiError:
    """页里的 `image` 不是纯文件名 → **400**，拒绝拿它拼路径。

    照片必须与页文件**同目录并列**（D5）。`../` 或绝对路径拼出来的 `image_path`
    会指到 `pages/` 外面去——坏页文件因此能变成一个读任意路径的入口。
    `details.param == "image"`。
    """
    return ApiError(
        400,
        "bad_request",
        f"页 {page_id} 的 image 不是纯文件名：{image!r}"
        f"（它只能是与页文件并列的照片名，不许含 '/'、'\\' 或 '..'）",
        reason="page_image_unsafe",
        hint="照片与页文件同目录并列（D5）；image 只填文件名，不带任何目录成分",
        details={"param": "image", "value": image, "id": page_id},
    )


def filesystem_error(exc: OSError) -> ApiError:
    """盘上的失败（文件不在／无权限／盘满）→ **500**，`message` 带异常类名。

    D1：CLI 也不许裸回溯。写盘是「先写临时文件再原子替换」，所以失败不会留下半个文件。
    """
    return ApiError(
        500,
        "internal_error",
        f"盘上操作失败：{exc.__class__.__name__}: {exc}",
        reason="filesystem_error",
        hint="这是盘上的问题（路径不存在、无权限、盘满），不是收入决策本身；"
             "页文件没有写到一半（先写临时文件再原子替换）",
    )
