"""HTTP 层：路由 + 信封（契约 §2）。

路由逻辑本身是一个纯函数 `Api.handle(method, target, body, content_type) -> Response`，
不碰 socket——测试直接调它，不起服务、不占端口；真起 socket 的端到端
另有一条冒烟测试（见 test_server_smoke.py）。
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from . import assets, inbox as inbox_mod
from .attempt import AttemptEndpoint
from .catalog import Catalog
from .config import load_judge_config
from . import errors
from .errors import ApiError, bad_request, method_not_allowed, not_found
from .judge_client import HttpJudge
from .model_client import ModelUnavailable
from .page_api import PageEndpoint

# 还没实现的写命名空间。给一个含糊的 404，会让人以为是打错了字，而不是
# 「这个端点还没实现」。`/api/attempt/`（#5）、`/api/inbox`（#13）与
# `/api/page*`（#14 落「改」与「重切」；「建」「入库」归 #15，由它给带说明的 404）
# 都已落地，不在这一列。
RESERVED: dict[str, str] = {}

# 手机上传页是后端托管的**静态资源**（ADR 0007 第 2 条：托管文件不是渲染页面）。
UPLOAD_PAGE = Path(__file__).resolve().parent / "static" / "upload.html"

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
                 *, inbox: Path | str | None = None, bind_host: str | None = None,
                 max_upload_bytes: int | None = None, segmenter=None,
                 judge=None, runs_dir: Path | str | None = None, config=None) -> None:
        self.catalog = Catalog(data_dir, clock=clock, public_base=public_base,
                               inbox=inbox, bind_host=bind_host)
        # 上传上限是**可注入**的：测试不必真造一个 32MB 的 body 去验 413。
        self.max_upload_bytes = inbox_mod.MAX_UPLOAD_BYTES if max_upload_bytes is None \
            else max_upload_bytes
        # 切分（#10）还没实现。这是一个**接缝**：注入一个 `(path) -> blocks` 就能接上，
        # 不注入就必须显式报「切分不可用」——绝不返回假的块列表。
        self.segmenter = segmenter
        # 坏配置**在这里就起不来**（阈值 NaN／无穷／越界，或 provider 不在白名单里），
        # 而不是每个请求里再验一遍（#5 派发简报第 7 条）。
        self.judge_config = config or load_judge_config()
        judge_call = judge or HttpJudge(self.judge_config, runs_dir or DEFAULT_RUNS_DIR)
        self.attempts = AttemptEndpoint(
            self.catalog, judge_call=judge_call, threshold=self.judge_config.threshold,
            clock=self.catalog.clock, provider=self.judge_config.provider,
            model=self.judge_config.model,
        )
        # 页资源（契约 §10.2）。`segmenter` 是 #10 的模型接缝（与 #13 的收件管道
        # 共用同一个注入对象）；不注入 = 切分不可用，绝不编块列表。
        self.pages = PageEndpoint(self.catalog, segmenter=segmenter)

    def handle(self, method: str, target: str, body: bytes | str | None = b"",
               content_type: str = "", declared_length: int | None = None) -> Response:
        raw_path, _, query = target.partition("?")
        # 先解码再路由：`%2e%2e%2f` 这类编码必须落进 id 校验，而不是绕过它。
        path = urllib.parse.unquote(raw_path)
        try:
            if method == "OPTIONS":
                response = self._options()
            else:
                response = self._route(method, path, urllib.parse.parse_qs(query), body,
                                       content_type, declared_length)
        except ApiError as exc:
            # 失败也可以带警告：拒绝一次作答时那张卡的自检结果要一并带上（§10.1）
            response = json_response(exc.status, error=exc.payload(), warnings=exc.warnings)
        except ModelUnavailable as exc:
            # 兜底：模型调用失败 → **502**（D1），绝不裸 500。具体是哪一页由
            # 抛出的那一层说（`page_api.resegment` 用 `errors.model_unavailable(pid=…)`
            # 把页 id 带进 `details`），这里只保证「502 + 信封」这条底线。
            failure = errors.model_unavailable(str(exc), pid=None)
            response = json_response(502, error=failure.payload(), warnings=failure.warnings)
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

    @staticmethod
    def _require(method: str, *allowed: str) -> None:
        """每条路由自己声明允许哪些方法：`POST` 只属于 `/api/attempt/*`（#5）与
        `/api/inbox*`（#13）。

        `OPTIONS` 永远允许（预检），所以 `allowed` 从必填的方法开始数。
        """
        if method not in allowed:
            raise method_not_allowed(method, [*allowed, "OPTIONS"])

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

    def _route(self, method: str, path: str, query: dict, body: bytes | str | None,
               content_type: str, declared_length: int | None) -> Response:
        # 手机上传页：后端托管的静态资源，不是渲染出来的页面（ADR 0007 第 2 条）。
        if path == "/upload":
            self._require(method, "GET")
            return self._upload_page()

        if path == "/api/index":
            self._require(method, "GET")
            data, warnings, skipped = self.catalog.index()
            return json_response(200, data=data, warnings=warnings, skipped=skipped)

        # 收件目录：录入的唯一入口（ADR 0007 第 4 条）。先判大小，再读 body 的活
        # 由 `app.py` 干（它才知道 Content-Length）。
        if path == "/api/inbox":
            self._require(method, "POST")
            return self._inbox_upload(body, content_type, declared_length)

        # 目录监视失效时的**手动等价入口**（`inotify` 在同步盘上不可靠，ADR 0007 待验证项）。
        if path == "/api/inbox/scan":
            self._require(method, "POST")
            return self._inbox_scan()

        # 写端点（#5）：GET 到它要说清「它收的是 POST」，不是含糊的 404（契约 §9）
        if path.startswith("/api/attempt/"):
            self._require(method, "POST")
            return self._post(path, body)

        # 页资源（契约 §10.2）：四个动作一个接缝。路由在这里显式列出，
        # 好让「用错方法」得到 405（带 allowed）而不是含糊的 404。
        if path == "/api/page":
            self._require(method, "POST")
            return self._page_create(body, content_type)
        if path.startswith("/api/page/"):
            return self._page_route(method, path, body)

        # 其余 POST 交给写端点那一份判断：只读端点上是 405、预留命名空间是带说明的 404。
        if method == "POST":
            return self._post(path, body)

        # 图片路由要排在读一题前面：`/api/problem/<pid>/image/<kind>`
        # `.*`（而不是 `.+`）：空 pid / 空 kind 要落到下面那两条 400 上，
        # 而不是掉进「没有这条路由」的 404——客户端少给一段路径不是路由写错了。
        match = re.fullmatch(r"/api/problem/(?P<pid>.*?)/image/(?P<kind>.*)", path)
        if match:
            self._require(method, "GET")
            return self._image(match.group("pid"), match.group("kind"))

        # pid 用 `.+` 而不是 `[^/]+`：带斜杠的非法 id 要被 **400** 抓住，
        # 而不是掉进一个含糊的路由 404（契约 §5.1）。
        match = re.fullmatch(r"/api/problem/(?P<pid>.*)", path)
        if match:
            self._require(method, "GET")
            return json_response(200, data=self.catalog.problem_detail(match.group("pid")))

        for prefix, note in RESERVED.items():
            if path.startswith(prefix):
                raise not_found(
                    f"这条路由是预留的，还没实现：{path}", hint=note,
                    reserved=True, owner=note,
                )

        raise not_found(f"没有这条路由：{path}", hint="GET /api/index 看看索引")

    # ------------------------------------------------------------ 页资源

    def _page_route(self, method: str, path: str, body: bytes | str | None) -> Response:
        """`/api/page/<id>`（改）与 `/api/page/<id>/resegment`、`/commit`。

        用错方法 → 405 带 `allowed`（契约 §5.1）：不是含糊的 404——路由**在**，
        只是这个动作不收这个方法。
        """
        match = re.fullmatch(r"/api/page/(?P<pid>.*?)/resegment", path)
        if match:
            self._require(method, "POST")
            data, warnings = self.pages.resegment(match.group("pid"))
            return json_response(200, data=data, warnings=warnings)

        match = re.fullmatch(r"/api/page/(?P<pid>.*?)/image", path)
        if match:
            self._require(method, "GET")
            blob, content_type = self.pages.image(match.group("pid"))
            return Response(
                200, blob, content_type,
                headers={"Cache-Control": "no-store", "Content-Length": str(len(blob))},
            )

        match = re.fullmatch(r"/api/page/(?P<pid>.*?)/commit", path)
        if match:
            self._require(method, "POST")
            data, warnings = self.pages.commit(match.group("pid"))
            return json_response(200, data=data, warnings=warnings)

        # `.*`（而不是 `.+`）：空的页 id 要落到 id 校验的 400 上，
        # 而不是掉进「没有这条路由」的 404——客户端少给一段路径不是路由写错了。
        match = re.fullmatch(r"/api/page/(?P<pid>.*)", path)
        if match:
            self._require(method, "PATCH")
            data, warnings = self.pages.edit(match.group("pid"), body)
            return json_response(200, data=data, warnings=warnings)

        raise not_found(f"没有这条路由：{path}", hint="页资源的动作见契约 §10.2")

    def _page_create(self, body: bytes | str | None, content_type: str) -> Response:
        """`POST /api/page`（建）：照片 → 存图 + 建页文件 + 跑切分（#15，`page_create`）。"""
        data, warnings = self.pages.create(body, content_type)
        return json_response(200, data=data, warnings=warnings)

    # ------------------------------------------------------------ 上传页

    def _upload_page(self) -> Response:
        try:
            page = UPLOAD_PAGE.read_bytes()
        except OSError as exc:
            # 页面读不了也不能给一个裸 500——错误仍然是 JSON 信封（契约 §2）。
            raise ApiError(
                500, "internal_error",
                f"上传页文件读不了：{exc.__class__.__name__}: {exc}",
                hint=f"仓库里应该有 {UPLOAD_PAGE}；它随 server/ 一起发布",
                details={"what": "upload_page", "path": str(UPLOAD_PAGE)},
            ) from exc
        return Response(
            200, page, "text/html; charset=utf-8",
            headers={"Cache-Control": "no-store", "Content-Length": str(len(page))},
        )

    # ------------------------------------------------------------ 收件目录

    def _too_large(self, size: int) -> ApiError:
        return ApiError(
            413, "payload_too_large",
            f"这次上传有 {size} 字节，超过上限 {self.max_upload_bytes} 字节",
            hint="一张照片不该这么大；手机原图通常是 2–8MB。要放大上限请改 MAX_UPLOAD_BYTES",
            details={"param": "body", "value": size, "max": self.max_upload_bytes},
        )

    def _inbox_upload(self, body: bytes | str | None, content_type: str,
                      declared_length: int | None) -> Response:
        blob = body.encode("utf-8") if isinstance(body, str) else (body or b"")
        size = declared_length if declared_length is not None else len(blob)
        if size > self.max_upload_bytes:
            raise self._too_large(size)
        data, warnings, skipped = inbox_mod.accept(
            self.catalog.inbox, blob, content_type, self.segmenter
        )
        return json_response(200, data=data, warnings=warnings, skipped=skipped)

    def _inbox_scan(self) -> Response:
        data, warnings, skipped = inbox_mod.scan(self.catalog.inbox, self.segmenter)
        return json_response(200, data=data, warnings=warnings, skipped=skipped)

    # ---------------------------------------------------------------- 写

    def _post(self, path: str, body: bytes | str | None) -> Response:
        """契约 §10.1：写端点一个入口。已落地的是屏幕重做那一种形态。"""
        match = re.fullmatch(r"/api/attempt/(?P<pid>.*)", path)
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
