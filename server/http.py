"""HTTP 层：路由 + 信封（契约 §2）。

路由逻辑本身是一个纯函数 `Api.handle(method, target) -> Response`，
不碰 socket——测试直接调它，不起服务、不占端口；真起 socket 的端到端
另有一条冒烟测试（见 test_server_smoke.py）。
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from . import assets
from .catalog import Catalog
from .errors import ApiError, bad_request, method_not_allowed, not_found

# v0 只有只读端点。这些命名空间是**预留**的：给一个含糊的 404，
# 会让人以为是打错了字，而不是「这个端点还没实现」。
RESERVED = {
    "/api/attempt/": "POST /api/attempt/<pid>（作答进 → 判定出 → 回写）归 #5",
    "/api/page": "页资源（建 / 改 / 重切 / 入库）归 #9 #10 #12 #14",
    "/api/inbox": "往收件目录放一个文件归 #13",
}


@dataclass
class Response:
    status: int
    body: bytes
    content_type: str
    headers: dict[str, str] = field(default_factory=dict)


def json_response(
    status: int,
    *,
    data: dict | None = None,
    error: dict | None = None,
    warnings: list | None = None,
    skipped: list | None = None,
) -> Response:
    """信封只有两个取值不同的形状：成功有 data，失败有 error。契约 §2。"""
    envelope: dict = {"ok": error is None}
    if error is None:
        envelope["data"] = data if data is not None else {}
    else:
        envelope["error"] = error
    envelope["warnings"] = warnings or []
    envelope["skipped"] = skipped or []
    body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
    return Response(
        status=status,
        body=body,
        content_type="application/json; charset=utf-8",
        headers={"Content-Length": str(len(body))},
    )


class Api:
    def __init__(self, data_dir: Path | str, clock=None,
                 public_base: str | None = None) -> None:
        self.catalog = Catalog(data_dir, clock=clock, public_base=public_base)

    def handle(self, method: str, target: str) -> Response:
        raw_path, _, query = target.partition("?")
        # 先解码再路由：`%2e%2e%2f` 这类编码必须落进 id 校验，而不是绕过它。
        path = urllib.parse.unquote(raw_path)
        try:
            if method == "OPTIONS":
                response = self._options()
            elif method == "GET":
                response = self._get(path, urllib.parse.parse_qs(query))
            else:
                raise method_not_allowed(method, ["GET", "OPTIONS"])
        except ApiError as exc:
            response = json_response(exc.status, error=exc.payload())
        except Exception as exc:  # 不允许用 500 表达「输入不对」，但真出错要说清
            response = json_response(
                500,
                error={
                    "code": "internal_error",
                    "reason": "internal_error",
                    "message": f"{exc.__class__.__name__}: {exc}",
                    "hint": "看服务日志；输入不合法应该是 400 而不是 500",
                },
            )
        # 站点在 :3000、服务在 :8765，跨源是常态。服务只监听本机（ADR 0003），
        # 所以 `*` 的暴露面就是本机。
        response.headers.setdefault("Access-Control-Allow-Origin", "*")
        return response

    def _options(self) -> Response:
        return Response(
            204,
            b"",
            "text/plain; charset=utf-8",
            headers={
                "Allow": "GET, OPTIONS",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
                "Access-Control-Max-Age": "600",
            },
        )

    def _get(self, path: str, query: dict) -> Response:
        if path == "/api/index":
            data, warnings, skipped = self.catalog.index()
            return json_response(200, data=data, warnings=warnings, skipped=skipped)

        # 图片路由要排在读一题前面：`/api/problem/<pid>/image/<kind>`
        match = re.fullmatch(r"/api/problem/(?P<pid>.+?)/image/(?P<kind>.+)", path)
        if match:
            return self._image(match.group("pid"), match.group("kind"))

        # pid 用 `.+` 而不是 `[^/]+`：带斜杠的非法 id 要被 **400** 抓住，
        # 而不是掉进一个含糊的路由 404（契约 §5.1）。
        match = re.fullmatch(r"/api/problem/(?P<pid>.+)", path)
        if match:
            return json_response(200, data=self.catalog.problem_detail(match.group("pid")))

        for prefix, note in RESERVED.items():
            if path.startswith(prefix):
                raise not_found(
                    f"这条路由是预留的，v0 还没实现：{path}", hint=note,
                    reserved=True, owner=note,
                )

        raise not_found(f"没有这条路由：{path}", hint="GET /api/index 看看索引")

    # ---------------------------------------------------------------- 图片

    def _image(self, pid: str, kind: str) -> Response:
        """契约 §7：成功是图片字节，失败仍然是 JSON 信封。"""
        if kind not in assets.KINDS:
            raise bad_request(
                f"图片类型非法：{kind!r}", param="kind", value=kind, allowed=list(assets.KINDS)
            )
        card = self.catalog.load_card(pid)
        path = assets.asset_file(self.catalog, card, kind)
        if path is None:
            available = assets.available_kinds(self.catalog, card)
            recorded = assets.recorded_path(card, kind)
            message = f"这张卡取不到 {kind} 图"
            if recorded:
                message += f"：卡里记了 {recorded!r}，但文件不在"
            raise not_found(
                message,
                hint="这张卡现在能取到的：" + (" / ".join(available) or "一张都没有"),
                what="image", id=pid, kind=kind, available_kinds=available,
            )
        body = path.read_bytes()
        return Response(
            200,
            body,
            assets.content_type_for(path),
            headers={
                "Cache-Control": "no-store",
                "X-Ai-Note-Image-Kind": kind,
                "Content-Length": str(len(body)),
            },
        )
