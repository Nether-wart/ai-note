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

from . import assets, brief, inbox as inbox_mod, subjects
from . import problem_edit as problem_edit_module
from . import settings as settings_module
from .attempt import MAX_BODY_BYTES, AttemptEndpoint
from .brief_client import HttpBrief
from .catalog import Catalog
from .config import BRIEF_ROLE, load_judge_config, load_role_config
from . import errors
from .errors import ApiError, bad_request, method_not_allowed, not_found
from .judge_client import HttpJudge
from .model_client import ModelUnavailable
from .page_api import PageEndpoint
from .paths import default_runs_dir

# 还没实现的写命名空间。给一个含糊的 404，会让人以为是打错了字，而不是
# 「这个端点还没实现」。`/api/attempt/`（#5）、`/api/inbox`（#13）与
# `/api/page*`（#14 落「改」与「重切」；「建」「入库」归 #15，由它给带说明的 404）
# 都已落地，不在这一列。
RESERVED: dict[str, str] = {}

# 手机上传页是后端托管的**静态资源**（ADR 0007 第 2 条：托管文件不是渲染页面）。
UPLOAD_PAGE = Path(__file__).resolve().parent / "static" / "upload.html"

# 留档默认落在**用户数据目录**下的 `runs/`（`paths.default_runs_dir`，测试一律指到临时目录）。


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


def _optional_json_object(body) -> dict:
    """**可选**的 JSON body → 对象。空 body 等价于「一个参数都不给」。

    与 `attempt.py`／`page_api.py` 那两个解析器**故意不同**，理由要说清：那两个资源的
    body 是**必填**的（一次作答必须有 `channel`；改一页必须有 `edits`），所以空 body
    是输入错。简报的每个参数都有默认值（`window_days` 缺省 7），空 body 是**合法**的。
    硬把三处合成一个「有时必填、有时可选」的解析器，只会让「必填」那句悄悄失效——
    而那正是这一类工具最贵的失败（一条看着在、其实不判的检查）。
    """
    if body is None:
        return {}
    raw = body.decode("utf-8", "replace") if isinstance(body, (bytes, bytearray)) else (body or "")
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise bad_request(
            f"body 不是 JSON：{exc}",
            hint='要么不给 body（全部用默认值），要么给 {"window_days": 7}',
            param="body", value=raw[:200]) from exc
    if not isinstance(data, dict):
        raise bad_request(f"body 必须是一个 JSON 对象，拿到的是 {type(data).__name__}",
                          param="body", value=raw[:200], allowed=["object"])
    return data


def _window_days(payload: dict) -> int:
    """`window_days`：不给就是默认天数；给了就必须是正整数。

    拼错的值**不许静默落到默认值上**——`"7"`、`0`、`-3`、`true` 都要当场 400。
    """
    if "window_days" not in payload:
        return brief.DEFAULT_WINDOW_DAYS
    value = payload["window_days"]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise bad_request(
            f"window_days 必须是正整数，拿到的是 {value!r}",
            hint="窗口是「最近几天」，从 1 起数",
            param="window_days", value=value, allowed=">=1")
    return value


class Api:
    def __init__(self, data_dir: Path | str, clock=None, public_base: str | None = None,
                 *, inbox: Path | str | None = None, bind_host: str | None = None,
                 max_upload_bytes: int | None = None, max_attempt_bytes: int | None = None,
                 segmenter=None,
                 judge=None, runs_dir: Path | str | None = None, config=None,
                 brief_client=None) -> None:
        self.catalog = Catalog(data_dir, clock=clock, public_base=public_base,
                               inbox=inbox, bind_host=bind_host)
        # 上传上限是**可注入**的：测试不必真造一个 32MB 的 body 去验 413。
        self.max_upload_bytes = inbox_mod.MAX_UPLOAD_BYTES if max_upload_bytes is None \
            else max_upload_bytes
        # 写端点的 body 上限也是可注入的（作业单 2），但默认就是那个紧上限：
        # 合法请求只有几个键，64 KiB 没有理由放宽。
        self.max_attempt_bytes = MAX_BODY_BYTES if max_attempt_bytes is None \
            else max_attempt_bytes
        # 切分（#10）还没实现。这是一个**接缝**：注入一个 `(path) -> blocks` 就能接上，
        # 不注入就必须显式报「切分不可用」——绝不返回假的块列表。
        self.segmenter = segmenter
        # 坏配置**在这里就起不来**（阈值 NaN／无穷／越界，或 provider 没定义），
        # 而不是每个请求里再验一遍（#5 派发简报第 7 条）。
        # 设置文件坏掉时**不用它兜底**（§10.6 规矩 4）：这里保持启动时的那份，
        # 而 `/api/settings` 会把 `file_ok: false` 与原因明说。
        self._runs_dir = runs_dir or default_runs_dir()
        self._settings_doc = self._load_settings_doc()
        self.judge_config = config or load_judge_config(settings=self._settings_doc)
        judge_call = judge or HttpJudge(self.judge_config, runs_dir or default_runs_dir())
        self.attempts = AttemptEndpoint(
            self.catalog, judge_call=judge_call, threshold=self.judge_config.threshold,
            clock=self.catalog.clock, provider=self.judge_config.provider,
            model=self.judge_config.model,
        )
        # 页资源（契约 §10.2）。`segmenter` 是 #10 的模型接缝（与 #13 的收件管道
        # 共用同一个注入对象）；不注入 = 切分不可用，绝不编块列表。
        self.pages = PageEndpoint(self.catalog, segmenter=segmenter)
        # 简报角色（#17 §10.5）：与判定角色同一个形状——**默认接上**（它不是「故障态」，
        # 而 `segmenter=None` 那种「不注入 = 不可用」的语义是切分独有的）。
        # 坏配置在这里就起不来（provider 不在白名单 → ValueError → 退出码 2）。
        self.brief = brief_client or HttpBrief(
            load_role_config(BRIEF_ROLE, settings=self._settings_doc), self._runs_dir)

    def _load_settings_doc(self) -> dict:
        """读一次设置文件；**坏文件不让整个 API 起不来**（读题卡不该被一份坏设置拖死）。

        代价是模型调用会退回环境变量／预设——所以这件事**必须每一次都被看见**：
        文件坏着的时候，**每一个响应**的 `warnings[]` 里都带上原因
        （`_with_settings_notice`）。静默回落会让人以为在用自己配的那一家，其实不是。
        """
        try:
            self._settings_error = None
            return settings_module.load_settings(self.catalog)
        except settings_module.SettingsError as exc:
            self._settings_error = {
                "code": exc.code, "level": "warning", "id": None,
                "message": f"{exc.message}（{settings_module.settings_path(self.catalog)}）",
            }
            return {}

    def _with_settings_notice(self, response: Response) -> Response:
        """设置文件坏着的时候，把原因挂到**每一个** JSON 响应的 `warnings[]` 上。

        熔断与"静默"之间还有第三条路：**照常服务，但每一次都说明现在的模型配置
        不是从设置文件来的**。读题卡不该被一份坏设置拖死，模型调用也不该假装没事。
        """
        notice = getattr(self, "_settings_error", None)
        if not notice:
            return response
        try:
            envelope = json.loads(response.body)
        except (ValueError, TypeError):
            return response
        if not isinstance(envelope, dict) or "warnings" not in envelope:
            return response
        if any(w.get("code") == notice["code"] for w in envelope["warnings"]):
            return response
        envelope["warnings"] = list(envelope["warnings"]) + [notice]
        body = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        return Response(status=response.status, body=body, content_type=response.content_type,
                        headers={"Content-Length": str(len(body))})

    def _problem_edit(self, pid: str, body, declared_length) -> Response:
        """`PATCH /api/problem/<id>`（契约 §10.7）：改一张**已有**卡的属性。

        可改的是一个闭集（`subject`／`topics`／`error_causes`／`review`）——题面与答案是
        审核那条路的活，掌握与重做历史只能由重做/判定改。这次编辑**不许**成为绕过它们的后门。
        """
        self._reject_oversized_write(body, declared_length)
        payload = _optional_json_object(body)
        try:
            result, warnings = problem_edit_module.apply_edit(
                self.catalog, pid, payload, clock=self.catalog.clock)
        except problem_edit_module.ProblemEditError as exc:
            raise bad_request(exc.message, reason=exc.code, hint=exc.hint,
                              **(exc.details or {})) from exc
        if result is None:
            raise not_found(f"没有这张题卡：{pid}",
                            hint="属性编辑只对**已经有**的卡有效；id 取自索引")
        return json_response(200, data=result, warnings=warnings)

    def _settings_route(self, method: str, body, declared_length) -> Response:
        """`GET`／`PUT /api/settings`（契约 §10.6）。

        `PUT` 是整体替换（幂等、好写测试）：先校验形状、再拿候选跑一遍三个角色的解析，
        **一个字节都还没写**就已经知道它装不起来；写盘走临时文件 + `os.replace`。
        成功之后**重载**模型配置——这就是"改完立刻生效"。
        """
        if method == "GET":
            data, warnings = settings_module.public_view(self.catalog)
            return json_response(200, data=data, warnings=warnings)
        self._require(method, "PUT")
        self._reject_oversized_write(body, declared_length)
        payload = _optional_json_object(body)
        try:
            settings_module.save_settings(self.catalog, payload)
        except settings_module.SettingsError as exc:
            # 按 §9 的约定：这一类错误的 `code` 恒为 `bad_request`，**细因走 `reason`**
            # （`settings_invalid`／`settings_unknown_field` 就是两个 `reason` 取值）。
            raise bad_request(exc.message, reason=exc.code, hint=exc.hint,
                              param="body", **(exc.details or {})) from exc
        self.reload_model_configs()
        data, warnings = settings_module.public_view(self.catalog)
        return json_response(200, data=data, warnings=warnings)

    def reload_model_configs(self) -> None:
        """重读设置并重建模型配置（§10.6「改完立刻生效」）。

        `Api` 是**启动时建一次**的（`app.py` 里那句 `httpd.api = Api(...)`），
        所以不重载就得重启服务——而桌面程序（Tauri）里"重启 sidecar"是额外的一整套生命周期。
        重载只换模型配置，不动任何数据。
        """
        self._settings_doc = self._load_settings_doc()
        self.judge_config = load_judge_config(settings=self._settings_doc)
        self.attempts.judge_call = HttpJudge(self.judge_config, self._runs_dir)
        self.attempts.threshold = self.judge_config.threshold
        self.attempts.provider = self.judge_config.provider
        self.attempts.model = self.judge_config.model
        self.brief = HttpBrief(
            load_role_config(BRIEF_ROLE, settings=self._settings_doc), self._runs_dir)

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
        return self._with_settings_notice(response)

    @staticmethod
    def _require(method: str, *allowed: str) -> None:
        """每条路由自己声明允许哪些方法：`POST` 只属于 `/api/attempt/*`（#5）与
        `/api/inbox*`（#13）。

        `OPTIONS` 永远允许（预检），所以 `allowed` 从必填的方法开始数。
        """
        if method not in allowed:
            raise method_not_allowed(method, [*allowed, "OPTIONS"])

    def _options(self) -> Response:
        """预检。**PATCH 必须在这里列出来**：跨源预检不通过时，浏览器根本不会把
        `PATCH /api/page/<id>` 发出去（`Access-Control-Allow-Methods` 里没有它），
        而这条请求恰恰是切分修正页唯一的写路径。站点在 :3000、服务在 :8765，
        跨源是常态，所以这不是理论问题。

        这份清单是**全局**的（预检不带路由上下文），比逐路由精确更松一档：
        在只读端点上答应 PATCH，真发过来仍然是 405 带 `allowed`（契约 §5.1）。
        松一档的代价是「预检过了、真请求 405」；紧一档的代价是「改不了」。
        """
        return Response(
            204,
            b"",
            "text/plain; charset=utf-8",
            headers={
                "Allow": "GET, POST, PATCH, OPTIONS",
                "Access-Control-Allow-Methods": "GET, POST, PATCH, OPTIONS",
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
            self._reject_oversized_write(body, declared_length)
            return self._post(path, body)

        # 页资源（契约 §10.2）：四个动作一个接缝。路由在这里显式列出，
        # 好让「用错方法」得到 405（带 allowed）而不是含糊的 404。
        if path == "/api/page":
            self._require(method, "POST")
            return self._page_create(body, content_type)
        if path.startswith("/api/page/"):
            return self._page_route(method, path, body)

        # 简报（#17 §10.5）：读最新那一份／生成一份。显式列出来，好让「用错方法」得到
        # 405（带 `allowed`）而不是一个含糊的 404——路由**在**，只是不收这个方法。
        if path.startswith("/api/brief/"):
            return self._brief_route(method, path, query, body, declared_length)

        # 设置（§10.6）：读生效值、整体替换。它是**唯一**会改到"服务怎么调模型"的端点，
        # 所以保存成功后要**重载**那几份模型配置——口径是"改完立刻生效"。
        if path == "/api/settings":
            return self._settings_route(method, body, declared_length)

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
            # `PATCH /api/problem/<id>`（§10.7）：改一张**已有**卡的属性。与 `GET` 同一条路径、
            # 不同方法——所以 `allowed` 要把两个都报出来（用错方法要说清能用什么）。
            if method == "PATCH":
                return self._problem_edit(match.group("pid"), body, declared_length)
            self._require(method, "GET", "PATCH")
            return json_response(200, data=self.catalog.problem_detail(match.group("pid")))

        for prefix, note in RESERVED.items():
            if path.startswith(prefix):
                raise not_found(
                    f"这条路由是预留的，还没实现：{path}", hint=note,
                    reserved=True, owner=note,
                )

        raise not_found(f"没有这条路由：{path}", hint="GET /api/index 看看索引")

    # ------------------------------------------------------------ 简报（#17 §10.5）

    def _brief_route(self, method: str, path: str, query: dict, body,
                     declared_length: int | None) -> Response:
        """`GET /api/brief/<科目>`（读）与 `POST /api/brief/<科目>`（生成）。

        科目是**另一类路径参数**（契约 §1）：它不在 id 的字符集里（中文），所以只做
        URL 解码（`handle` 已经解过）＋**词表逐字校验**。取值不在词表里 → 400
        `subject_unknown` 带 `allowed`——与卡级那条警告**同名同事实**，层次不同：
        这里发生在「拒绝一次输入」，那里发生在「自检一份已有数据」。名字同一个，
        是因为「同一个事实两个码会让界面出现两种说法、让按码统计永远对不上」（R2/R9）。
        """
        subject = path[len("/api/brief/"):].strip()
        vocabulary, _vocab_warnings = subjects.load(self.catalog)
        known = list(vocabulary["subjects"])
        if subject not in known:
            raise bad_request(
                f"科目 {subject!r} 不在受控词表里",
                reason="subject_unknown",
                hint="先把它加进 <数据目录>/vocab/subjects.json；简报只对有科目的题生成",
                param="subject", value=subject, allowed=known,
            )

        if method == "GET":
            at = (query.get("at") or [None])[0]
            document, where = (brief.load_at(self.catalog, subject, at) if at
                               else brief.load_latest(self.catalog, subject))
            return json_response(200, data={
                "brief": self._brief_readout(subject, document),
                # `str()`：这两处回来的是 `Path`，直接塞进信封就是一个
                # 「Object of type PosixPath is not JSON serializable」的 500——
                # 契约要的是路径字符串，不是一个 Python 对象。
                "path": str(where),
            })

        self._require(method, "POST")
        self._reject_oversized_write(body, declared_length)
        payload = _optional_json_object(body)
        generated = brief.generate(self.catalog, subject, client=self.brief,
                                  window_days=_window_days(payload))
        return json_response(200, data={
            "brief": self._brief_readout(subject, generated["brief"]),
            "path": str(generated["path"]),
            "run_id": generated.get("run_id"),
        })

    def _brief_readout(self, subject: str, document: dict) -> dict:
        """落盘形状 ＋ 三个**现算**的读数（契约 §10.5）：`is_latest`／`stale`／`new_problems`。

        为什么不把这三个键写进文件：它们是「此刻」的读数（今天最新的是哪一份、之后又录进来
        几道题），落盘就会陈旧——`is_latest` 更是下一份生成出来的那一刻就变了。
        判据只有 `brief.stale` 一处，这里只**取**。
        """
        dates = brief.list_briefs(self.catalog, subject)
        latest = dates[-1] if dates else None
        return {
            **(document or {}),
            "is_latest": latest is not None and (document or {}).get("window_until") == latest,
            **brief.stale(self.catalog, subject, document),
        }

    # ------------------------------------------------------------ 页资源

    def _page_route(self, method: str, path: str, body: bytes | str | None) -> Response:
        """`/api/page/<id>`（改）与 `/api/page/<id>/resegment`、`/commit`。

        用错方法 → 405 带 `allowed`（契约 §5.1）：不是含糊的 404——路由**在**，
        只是这个动作不收这个方法。
        """
        match = re.fullmatch(r"/api/page/(?P<pid>.*?)/resegment", path)
        if match:
            self._require(method, "POST")
            # body 一路带进去：`{"confirm_discard_manual": true}` 是「重置为预设」的确认
            # （#31 的破坏性动作）。空 body／不是 JSON 不算确认，由 `PageEndpoint` 判。
            data, warnings = self.pages.resegment(match.group("pid"), body)
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
            # 读与写分两条路：`GET` 读整份页（界面打开一页来改的第一步），
            # `PATCH` 才改。以前这里只有 `PATCH`，于是「读一页」只能拿一次
            # **空修正的预演**去凑——让读依赖一条写形状的路由，下一个读代码的人
            # 得先确认它到底写不写。
            # `_require` 收**两个**方法：用错方法时的 405 要把 `GET` 也列进去，
            # 否则那条 `allowed` 会漏掉一个真允许的方法（那就是一句误导）。
            self._require(method, "GET", "PATCH")
            if method == "GET":
                data, warnings = self.pages.read(match.group("pid"))
                return json_response(200, data=data, warnings=warnings)
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

    # ------------------------------------------------------------ 输入错

    def body_limit(self, path: str) -> int:
        """这条路由的 body 上限。`app.py` 靠它在**读 body 之前**决定读不读。

        按路由分档：**写端点**收的是几个键的 JSON（`MAX_BODY_BYTES` = 64 KiB），
        上传收的是多部分照片（`max_upload_bytes`）。上限只有这一处判据，`app.py` 与
        `_reject_oversized_write` 都从这里取——两处各写一份迟早分叉。

        写端点那一档**不分资源**：作答与简报的 body 都只有几个键，没有理由放宽
        （放宽了就是又一条「读一个巨大的 body 再拒绝」的路）。
        """
        raw_path = path.partition("?")[0]
        if raw_path.startswith("/api/attempt/") or raw_path.startswith("/api/brief/"):
            return self.max_attempt_bytes
        return self.max_upload_bytes

    def _reject_oversized_write(self, body: bytes | str | None,
                                declared_length: int | None) -> None:
        """写端点的 body 超限 → 400 `body_too_large`，**在进模型之前**（作业单 2）。

        声明长度与真读到的字节数取大的那个：`app.py` 只按 `Content-Length` 判、
        超限时**不读** body（`body` 是空的），所以不能只看 `len(body)`，否则
        真起服务时这条会退化成「空 body 400」而把真实大小丢掉。
        """
        if isinstance(body, str):
            read = len(body.encode("utf-8"))
        else:
            read = len(body or b"")
        size = max(declared_length or 0, read)
        if size > self.max_attempt_bytes:
            raise errors.body_too_large(size, limit=self.max_attempt_bytes)

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
