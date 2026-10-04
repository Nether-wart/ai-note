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
from .attempt import AttemptEndpoint
from .catalog import Catalog
from .config import load_judge_config
from .errors import ApiError, bad_request, method_not_allowed, not_found
from .judge_client import HttpJudge

# 还没实现的写命名空间。给一个含糊的 404，会让人以为是打错了字，
# 而不是「这个端点还没实现」。`/api/attempt/` 已由 #5 落地，不在这一列。
RESERVED = {
    "/api/page": "页资源（建 / 改 / 重切 / 入库）归 #9 #10 #12 #14",
    "/api/inbox": "往收件目录放一个文件归 #13",
}

# 留档默认落在仓库根的 `runs/`（.gitignore 里已有）。测试一律指到临时目录。
DEFAULT_RUNS_DIR = Path(__file__).resolve().parent.parent / "runs"


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
    def __init__(self, data_dir: Path | str, clock=None, public_base: str | None = None,
                 *, judge=None, runs_dir: Path | str | None = None, config=None) -> None:
        self.catalog = Catalog(data_dir, clock=clock, public_base=public_base)
        # 坏配置**在这里就起不来**（阈值 NaN／无穷／越界，或 provider 不在白名单里），
        # 而不是每个请求里再验一遍（#5 派发简报第 7 条）。
        self.judge_config = config or load_judge_config()
        judge_call = judge or HttpJudge(self.judge_config, runs_dir or DEFAULT_RUNS_DIR)
        self.attempts = AttemptEndpoint(
            self.catalog, judge_call=judge_call, threshold=self.judge_config.threshold,
            clock=self.catalog.clock, provider=self.judge_config.provider,
            model=self.judge_config.model,
        )

    def handle(self, method: str, target: str, body: bytes | str | None = None) -> Response:
        raw_path, _, query = target.partition("?")
        # 先解码再路由：`%2e%2e%2f` 这类编码必须落进 id 校验，而不是绕过它。
        path = urllib.parse.unquote(raw_path)
        try:
            if method == "OPTIONS":
                response = self._options()
            elif method == "GET":
                response = self._get(path, urllib.parse.parse_qs(query))
            elif method == "POST":
                response = self._post(path, body)
            else:
                raise method_not_allowed(method, ["GET", "POST", "OPTIONS"])
        except ApiError as exc:
            # 失败也可以带警告：拒绝一次作答时那张卡的自检结果要一并带上（§10.1）
            response = json_response(exc.status, error=exc.payload(), warnings=exc.warnings)
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
                "Allow": "GET, POST, OPTIONS",
                "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type",
                "Access-Control-Max-Age": "600",
            },
        )

    def _get(self, path: str, query: dict) -> Response:
        if path == "/api/index":
            data, warnings, skipped = self.catalog.index()
            return json_response(200, data=data, warnings=warnings, skipped=skipped)

        # 写端点收到 GET：说清它收什么，而不是一个含糊的 404（契约 §9）
        if path.startswith("/api/attempt/"):
            raise method_not_allowed("GET", ["POST", "OPTIONS"])

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

    # ---------------------------------------------------------------- 写

    def _post(self, path: str, body: bytes | str | None) -> Response:
        """契约 §10.1：写端点一个入口。已落地的是屏幕重做那一种形态。"""
        match = re.fullmatch(r"/api/attempt/(?P<pid>.+)", path)
        if match:
            data, warnings = self.attempts.handle(match.group("pid"), body)
            return json_response(200, data=data, warnings=warnings)

        # 只读端点上用错方法 → 405（契约 §5.1），不是含糊的 404
        if path == "/api/index" or path.startswith("/api/problem/"):
            raise method_not_allowed("POST", ["GET", "OPTIONS"])

        for prefix, note in RESERVED.items():
            if path.startswith(prefix):
                raise not_found(
                    f"这条路由是预留的，还没实现：{path}", hint=note,
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
