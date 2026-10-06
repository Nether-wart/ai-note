"""**百度 OCR 形状**的本地服务：把整页照片送过去，取回文本行。

那台机器（`192.168.0.105`）把 PaddleOCR 包成了**百度 OCR 的 REST 形状**：

    POST {base}/rest/2.0/ocr/v1/{general_basic,accurate_basic,general,accurate}
    body: application/x-www-form-urlencoded，`image=<图片字节的 base64（再 URL 编码）>`
    → {"words_result": [{"words": "..."}], "words_result_num": N, "log_id": ...}

所以这里按**那个协议**说话，不自造第二套（自造的那套脚手架——原始字节协议 ＋ 远端脚本——
已经扔掉了：多一套协议就是多一处漂移）。

四条纪律：

1. **不问成就不假装**：连不上、非 200、`error_code`、body 形状不对，一律变成
   `ocr.unavailable(原因)`——绝不回一个"零行"冒充"这一页没有字"。
2. **超时给足**：那一侧要加载模型、跑满一张整页图；超时短了会把"慢"报成"坏"。
3. **`accurate_basic` 不给框**，所以 `box` 是 `None`——这是**如实**，不是缺省值。
   （要按块切草稿就得走 `general`／`accurate` 那两条带 `location` 的路，那需要把图片像素尺寸
   一起给进来；现在没有，所以草稿是**整页**的，提示词里也这么写。）
4. **token 可选**：那条服务测试期不校验；真要校验时用 `access_token=` 查询参数（百度那套）。
"""

from __future__ import annotations

import base64
import json
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from .ocr import OcrLine, OcrResult, unavailable

#: 那台机器上的默认地址；路径按用途分（`accurate_basic`＝高精度、`general`＝带位置）。
DEFAULT_BASE_URL = "http://192.168.0.105:8866"
DEFAULT_PATH = "accurate_basic"
PATHS = ("general_basic", "accurate_basic", "general", "accurate")

#: 第一趟要加载模型（几十秒的量级），给宽。
DEFAULT_TIMEOUT = 120.0


def _parse(body: bytes) -> tuple[list[OcrLine], list[str]]:
    data = json.loads(body.decode("utf-8", "replace"))
    if not isinstance(data, dict):
        raise ValueError("远端返回的不是一个 JSON 对象")
    if data.get("error_code"):
        raise ValueError(f"OCR 服务报错 {data['error_code']}：{data.get('error_msg')}")
    lines = []
    for item in data.get("words_result") or []:
        if not isinstance(item, dict):
            continue
        words = item.get("words")
        if not isinstance(words, str) or not words.strip():
            continue
        lines.append(OcrLine(text=words, box=None, confidence=None))
    counted = data.get("words_result_num")
    warnings = []
    if isinstance(counted, int) and counted != len(lines):
        # 数目对不上要说出来：这是"服务说它认了 N 条、我们只拿到 M 条"
        warnings.append(f"服务说有 {counted} 条，解析出 {len(lines)} 条")
    return lines, warnings


class BaiduOcrEngine:
    """包成 `server/ocr.py` 要的形状：`(image_bytes, name) -> OcrResult`。

    `transport` 是可注入的接缝（与模型客户端同一条纪律）：测试注入桩，**不联网**。
    """

    def __init__(self, base_url: str = DEFAULT_BASE_URL, *, path: str = DEFAULT_PATH,
                 token: str | None = None, transport=None,
                 timeout: float = DEFAULT_TIMEOUT) -> None:
        if path not in PATHS:
            raise ValueError(f"认不出的 OCR 路径：{path!r}；可选：{', '.join(PATHS)}")
        self.base_url = base_url.rstrip("/")
        self.path = path
        self.token = token
        self.transport = transport or self._default_transport
        self.timeout = timeout

    @property
    def url(self) -> str:
        url = f"{self.base_url}/rest/2.0/ocr/v1/{self.path}"
        if self.token:
            url += "?" + urlparse.urlencode({"access_token": self.token})
        return url

    def _default_transport(self, url: str, blob: bytes, timeout: float):
        """标准库 POST（表单形状，与百度一致）；返回 `(status, body_bytes)`。"""
        request = urlrequest.Request(
            url, data=blob, method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Content-Length": str(len(blob))},
        )
        try:
            with urlrequest.urlopen(request, timeout=timeout) as response:
                return response.status, response.read()
        except urlerror.HTTPError as exc:
            return exc.code, exc.read()

    def __call__(self, image: bytes, name: str = "") -> OcrResult:
        # 百度的形状：`image` 是**图片字节的 base64**，整个表单再 URL 编码一次
        form = urlparse.urlencode({"image": base64.b64encode(image).decode("ascii")}).encode("ascii")
        try:
            status, body = self.transport(self.url, form, self.timeout)
        except OSError as exc:
            return unavailable(f"连不上 OCR 服务（{self.base_url}）：{type(exc).__name__}: {exc}")
        if status != 200:
            return unavailable(f"OCR 服务回了 HTTP {status}："
                               f"{body[:200].decode('utf-8', 'replace')}")
        try:
            lines, warnings = _parse(body)
        except (ValueError, TypeError) as exc:
            return unavailable(f"OCR 服务的回执读不出来：{exc}（前 200 字节：{body[:200]!r}）")
        warnings.append(f"走的是 {self.path}")
        return OcrResult(lines=lines, engine="paddleocr-local", model=self.path, warnings=warnings)


def engine_from_config(catalog=None, *, env=None, transport=None) -> BaiduOcrEngine | None:
    """按配置给一个引擎；**没配就是 `None`（＝ OCR 不可用）**，不去试连接。

    配置两处（前者优先）：环境变量 `AI_NOTE_OCR_URL`、设置文件里的 `ocr.base_url`。
    `AI_NOTE_OCR_PATH` / `ocr.path` 可以换那条路（默认 `accurate_basic`）；
    `AI_NOTE_OCR_TOKEN` / `ocr.token` 是给"真要校验"时用的。
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
    path = (env.get("AI_NOTE_OCR_PATH") or section.get("path") or DEFAULT_PATH).strip()
    token = (env.get("AI_NOTE_OCR_TOKEN") or section.get("token") or "").strip() or None
    return BaiduOcrEngine(base_url, path=path, token=token, transport=transport)
