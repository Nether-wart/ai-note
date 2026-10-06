"""**百度 OCR 形状**的本地服务：拿 token、把图送过去、取回文本行。

那台机器（`192.168.0.105:8866`）把 PaddleOCR 包成了百度 OCR 的 REST 形状，实测的配方是：

    POST /oauth/2.0/token     form: grant_type=client_credentials&client_id=…&client_secret=…
                              → {"access_token": …, "expires_in": 2592000}
    POST /rest/2.0/ocr/v1/{general_basic,accurate_basic,general,accurate}?access_token=…
                              form: image=<图片字节的 base64（整个表单再 URL 编码一次）>
                              → {"words_result": [...], "words_result_num": N, "log_id": …}

实测三件事（都进了默认值）：一次调用**约 17 秒**（超时给宽，别把"慢"报成"坏"）；
`accurate_basic` **不给框**、`general` 给 `location`（像素框）——所以默认走 `general`，
它让"按块切草稿"以后做得到；token 有效期 30 天，但**过期要能自己重取**。

四条纪律：

1. **不问成就不假装**：连不上、非 200、`error_code`、body 形状不对，一律变成
   `ocr.unavailable(原因)`——绝不回一个"零行"冒充"这一页没有字"。
2. **凭据不进代码**：`client_id`／`client_secret` 只从配置来，这里一个都不写死。
3. **token 有缓存、过期重取、遇 110 重试一次**：30 天的 token 不该每个请求都要一遍；
   "过期了"这种瞬时故障不该以一次失败的 OCR 出现在人面前。
4. **框归一化**：`general` 给的是**像素**框，这里按 PNG 头里的宽高换算成归一化 xywh
   （与页文件的块框同一套）。**只读 PNG 头部，不解码整幅图**；不是 PNG 就把框留 `None`。
"""

from __future__ import annotations

import base64
import json
import time
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from .ocr import OcrLine, OcrResult, unavailable

#: 那台机器上的默认地址与默认路径（`general` 带位置）。
DEFAULT_BASE_URL = "http://192.168.0.105:8866"
DEFAULT_PATH = "general"
PATHS = ("general_basic", "accurate_basic", "general", "accurate")

#: 实测一次约 17 秒（含模型加载）；超时给宽。
DEFAULT_TIMEOUT = 120.0

#: 提前多久认为 token 该换了（秒）。太贴边会在边界上失败一次。
TOKEN_SLACK = 300.0


def png_size(blob: bytes) -> tuple[int, int] | None:
    """从 PNG 头部读像素尺寸（IHDR 在固定位置）。**不解码**整幅图，只要宽高。"""
    if len(blob) < 24 or blob[:8] != b"\x89PNG\r\n\x1a\n" or blob[12:16] != b"IHDR":
        return None
    width = int.from_bytes(blob[16:20], "big")
    height = int.from_bytes(blob[20:24], "big")
    return (width, height) if width > 0 and height > 0 else None


def _norm_box(location, size) -> list[float] | None:
    """百度给的像素 `location` → 归一化 xywh。"""
    if not isinstance(location, dict) or size is None:
        return None
    try:
        left = float(location["left"])
        top = float(location["top"])
        width = float(location["width"])
        height = float(location["height"])
    except (KeyError, TypeError, ValueError):
        return None
    image_w, image_h = size
    return [round(left / image_w, 6), round(top / image_h, 6),
            round(width / image_w, 6), round(height / image_h, 6)]


#: token 的路径（百度 aip 与千帆都用它；留着可配是为了别家形状）。
DEFAULT_TOKEN_PATH = "/oauth/2.0/token"


def _oauth_reason(text: str) -> str:
    """把 OAuth 形状的错误读成人话：`invalid_client` / `unknown client id` 这种。"""
    try:
        data = json.loads(text)
    except ValueError:
        return text
    if not isinstance(data, dict):
        return text
    error = data.get("error") or data.get("error_code")
    description = data.get("error_description") or data.get("error_msg") or ""
    return f"{error}：{description}".strip("：") if error else text


class _ServiceError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"OCR 服务报错 {code}：{message}")
        self.code = code


def _parse(body: bytes, size=None) -> tuple[list[OcrLine], list[str]]:
    data = json.loads(body.decode("utf-8", "replace"))
    if not isinstance(data, dict):
        raise ValueError("远端返回的不是一个 JSON 对象")
    if data.get("error_code"):
        raise _ServiceError(int(data["error_code"]), str(data.get("error_msg") or ""))
    lines = []
    for item in data.get("words_result") or []:
        if not isinstance(item, dict):
            continue
        words = item.get("words")
        if not isinstance(words, str) or not words.strip():
            continue
        lines.append(OcrLine(text=words, box=_norm_box(item.get("location"), size)))
    counted = data.get("words_result_num")
    warnings = []
    if isinstance(counted, int) and counted != len(lines):
        warnings.append(f"服务说有 {counted} 条，解析出 {len(lines)} 条")
    return lines, warnings


class BaiduOcrEngine:
    """包成 `server/ocr.py` 要的形状：`(image_bytes, name) -> OcrResult`。

    `transport` 是可注入的接缝：测试注入桩，**不联网**。凭据三选一：直接给 `token`；
    或给 `client_id`+`client_secret`（拿到手再缓存）；都没有就是**不可用**（调用时明说）。
    """

    def __init__(self, base_url: str = DEFAULT_BASE_URL, *, path: str = DEFAULT_PATH,
                 token: str | None = None, client_id: str | None = None,
                 client_secret: str | None = None, bearer: str | None = None,
                 token_path: str = DEFAULT_TOKEN_PATH, transport=None,
                 timeout: float = DEFAULT_TIMEOUT) -> None:
        if path not in PATHS:
            raise ValueError(f"认不出的 OCR 路径：{path!r}；可选：{', '.join(PATHS)}")
        self.base_url = base_url.rstrip("/")
        self.path = path
        self.token = (token or "").strip() or None
        self.client_id = (client_id or "").strip() or None
        self.client_secret = (client_secret or "").strip() or None
        # 千帆 v2 那一套用 `Authorization: Bearer <密钥>`，不取 access_token。
        # 给哪一套就按哪一套说：两套混着猜是最糟的。
        self.bearer = (bearer or "").strip() or None
        self.token_path = token_path if token_path.startswith("/") else "/" + token_path
        self.transport = transport or self._default_transport
        self.timeout = timeout
        self._cached_token: str | None = None
        self._expires_at = 0.0

    def _default_transport(self, url: str, blob: bytes, timeout: float, headers=None):
        merged = {"Content-Type": "application/x-www-form-urlencoded",
                  "Content-Length": str(len(blob))}
        merged.update(headers or {})
        request = urlrequest.Request(url, data=blob, method="POST", headers=merged)
        try:
            with urlrequest.urlopen(request, timeout=timeout) as response:
                return response.status, response.read()
        except urlerror.HTTPError as exc:
            return exc.code, exc.read()

    def _fetch_token(self) -> str:
        """去要一个新 token。失败抛 `_ServiceError`（由 `__call__` 变成不可用）。"""
        if not (self.client_id and self.client_secret):
            raise _ServiceError(0, "没有凭据：要给 token／client_id＋client_secret／bearer")
        form = urlparse.urlencode({
            "grant_type": "client_credentials",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
        }).encode("ascii")
        status, body = self.transport(f"{self.base_url}{self.token_path}", form, self.timeout)
        text = body[:200].decode("utf-8", "replace")
        if status != 200:
            # **真百度的 token 错误是 OAuth 形状**（`{"error":"invalid_client",
            # "error_description":"unknown client id"}` ＋ 401），不是 `error_code`——
            # 实测过。把它读成人话，别只丢一串 JSON 给人。
            raise _ServiceError(status, _oauth_reason(text))
        try:
            data = json.loads(body.decode("utf-8", "replace"))
        except ValueError as exc:
            raise _ServiceError(0, f"token 回执读不出来：{exc}") from exc
        if data.get("error_code"):
            raise _ServiceError(int(data["error_code"]), str(data.get("error_msg") or ""))
        if data.get("error"):
            raise _ServiceError(0, _oauth_reason(text))
        value = data.get("access_token")
        if not isinstance(value, str) or not value:
            raise _ServiceError(0, f"token 回执里没有 access_token：{body[:120]!r}")
        expires_in = data.get("expires_in")
        seconds = float(expires_in) if isinstance(expires_in, (int, float)) else 0.0
        self._cached_token = value
        self._expires_at = time.time() + seconds
        return value

    def access_token(self) -> str:
        """给这一次调用用的 token。**有缓存就用缓存**（30 天的不该每次都要一遍）。"""
        if self.token:
            return self.token
        if self._cached_token and time.time() < self._expires_at - TOKEN_SLACK:
            return self._cached_token
        return self._fetch_token()

    def _once(self, access_token: str | None, form: bytes, size):
        url = f"{self.base_url}/rest/2.0/ocr/v1/{self.path}"
        headers = None
        if access_token:
            url += "?" + urlparse.urlencode({"access_token": access_token})
        else:
            headers = {"Authorization": f"Bearer {self.bearer}"}
        status, body = self.transport(url, form, self.timeout, headers)
        if status != 200:
            raise _ServiceError(status, body[:200].decode("utf-8", "replace"))
        return _parse(body, size)

    def __call__(self, image: bytes, name: str = "") -> OcrResult:
        # 百度的形状：`image` 是图片字节的 base64，整个表单再 URL 编码一次
        form = urlparse.urlencode({"image": base64.b64encode(image).decode("ascii")}).encode("ascii")
        size = png_size(image)
        try:
            try:
                lines, warnings = self._once(None if self.bearer else self.access_token(),
                                             form, size)
            except _ServiceError as exc:
                if exc.code != 110 or self.token:
                    raise
                # token 过期（110）：清掉缓存重取一次——瞬时故障不该变成一次失败的 OCR
                self._cached_token, self._expires_at = None, 0.0
                lines, warnings = self._once(self._fetch_token(), form, size)
        except _ServiceError as exc:
            return unavailable(str(exc))
        except OSError as exc:
            return unavailable(f"连不上 OCR 服务（{self.base_url}）：{type(exc).__name__}: {exc}")
        except (ValueError, TypeError) as exc:
            return unavailable(f"OCR 服务的回执读不出来：{exc}")

        if size is None:
            warnings.append("这不是 PNG（读不出像素尺寸），所以这一趟没有框")
        warnings.append(f"走的是 {self.path}")
        return OcrResult(lines=lines, engine="paddleocr-local", model=self.path, warnings=warnings)


def engine_from_config(catalog=None, *, env=None, transport=None) -> BaiduOcrEngine | None:
    """按配置给一个引擎；**没配就是 `None`（＝ OCR 不可用）**，不去试连接。

    配置（环境变量优先）：`AI_NOTE_OCR_URL`（不给就没有 OCR）、`AI_NOTE_OCR_PATH`、
    `AI_NOTE_OCR_TOKEN`、`AI_NOTE_OCR_CLIENT_ID`／`AI_NOTE_OCR_CLIENT_SECRET`；
    也可以写进设置文件的 `ocr` 段（`base_url`／`path`／`token`／`client_id`／`client_secret`）。
    """
    import os

    env = os.environ if env is None else env
    document = {}
    if catalog is not None:
        try:
            from . import settings as settings_module
            document = settings_module.load_settings(catalog)
        except Exception:                       # noqa: BLE001 —— 设置文件坏了不该挡住 OCR 的默认值
            document = {}
    section = document.get("ocr") or {}

    base_url = (env.get("AI_NOTE_OCR_URL") or section.get("base_url") or "").strip()
    if not base_url:
        return None

    def pick(key: str) -> str | None:
        return ((env.get(f"AI_NOTE_OCR_{key.upper()}") or section.get(key) or "").strip()) or None

    return BaiduOcrEngine(
        base_url,
        path=(pick("path") or DEFAULT_PATH),
        token=pick("token"),
        client_id=pick("client_id"),
        client_secret=pick("client_secret"),
        bearer=pick("bearer"),
        token_path=(pick("token_path") or DEFAULT_TOKEN_PATH),
        transport=transport,
    )
