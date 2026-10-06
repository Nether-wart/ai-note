"""**抽取角色**的调用接缝：这一块的红笔是什么意思（#12）。

**为什么这一层要看图**（而判定角色一张图都不发，spec #1 US 38）：语义是**像素的
性质**——勾、圈、叉、订正、分数在文本里没有对应物。spec #2 把两层分得很清楚：
统计（`server/ink.py`）回答「有没有红笔、多少」，**语义由模型回答「这点红笔是勾、
是圈、是订正，还是只是分数」**。所以这一层发**整页照片**（缩放后）＋那个块的
边界与红笔像素数，让模型在上下文里看。

**发整页而不是裁块**：块边界由 `bbox_norm`（整页归一化）给出，裁图要在服务里
重做一遍几何（#9 已经点了「两套坐标基准不许混用」的坑），而整页能让模型看见
「这个勾是打给哪一道题的」这种上下文。代价明说：模型可能把相邻块的痕迹算进来
——所以每块的红笔像素数是**统计层按块算好的**（喂进提示词），而且判出来的语义
只影响「收不收」，人在界面上看得见、能改（#12 的 `decision`）。

**留档**：每次调用写 `runs/<stamp>-redpen-semantics.json`（契约 §10.1 的 `run_id`），
**图片不入档**（`server/model_client.py: save_run`，口径同 `proto/slice.py:157-163`）。
改提示词必须重跑抽取角色的考卷（spec #2 Testing Decisions 第 4 层）。

**失败分两种，别混**（这一层最要紧的一条）：

  · **调用失败**（网络/超时/缺密钥/上游 5xx）→ `ModelUnavailable` 往上抛，
    由调用方变成 502 `model_unavailable`，**页文件一个字节都不动**（D1/D9）。
  · **答了话但答不出语义**（不是 JSON、缺 `semantics`、值不在枚举里）→ 这**是一次
    回答**：返回 `parsed: False`（或原样留着枚举外的值），由决策层按「判不准 → 收」
    处理，并留一条 warning。`proto` 的判定角色也是这么分的（`judge_output_unparsed`）。
"""

from __future__ import annotations

import base64
import time
from pathlib import Path

from . import ink
from .model_client import ModelCall, default_transport, extract_json, save_run
from .model_client import chat as model_chat

INTAKE_TAG = "redpen-semantics"

# 进模型前的最长边（口径继承 proto/slice.py:99-100 的 MAX_SIDE=1600）。
# 本机真实的整页照片是 680×190 与 682×554，远在上限之内，所以正常路径是**原字节直发**；
# 只有手机相册那种大 PNG 才会走缩放（缩放要重新编码，见 `image_data_url`）。
MAX_SIDE = 1600

# ⚠ 改这段提示词就必须重跑抽取角色的考卷（spec #2 Testing Decisions 第 4 层）——
# 与 `SEGMENT_SYSTEM`（#10）同属抽取角色，共用那张「真实照片上的直接可用率」的考卷。
SEMANTICS_SYSTEM = """你在为一页试卷做录入。你会收到一张**整页**试卷照片，以及其中一个块（一道题）的边界与它里面的红笔像素数量。

请判断：**这一块里的红笔是什么意思**？铁律：

1. 只输出一个 JSON 对象，不要解释，不要 markdown 代码块。
2. `semantics` 只能取下面这些值之一（一个字符串）：
   - "cross"：打了叉（表示这道题错了）
   - "circle_wrong"：圈出了错的地方（表示这里错了）
   - "correction"：写了订正/改错（把正确的解法或答案写在旁边）
   - "tick"：**只是一个对勾**（表示这道题做对了）
   - "score"：只是打了个分数（比如 85、-2、8/10）
   - "circle_number"：只是圈了题号
   - "none"：这一块里看不到红笔
   - "other"：别的红笔（不属于上面任何一种）
   - "unknown"：看不清、判不准
3. **判不准就填 "unknown"，不要猜**——猜成"对勾"会让一道错题永远不在库里，猜成"订正"只是多一条要人工删掉的记录。
4. 只看**这一个块边界内**的红笔，别把相邻块的痕迹算进来。
5. `reason` 用一句中文说清你看到的红笔长什么样、写在哪里。"""


def semantics_messages(*, block: dict, stats: dict, image_url: str) -> list[dict]:
    """`[system, user]` 两条消息。user 是**多段 content**：一段文本 + 一张图。

    文本里给的是**整页**归一化边界（`bbox_norm` 是 xywh，与 `source.bbox_norm`
    同一语义，契约 §10.2.1）与#11 统计到的红笔像素数。块 id 是内部编号，不喂给模型。
    """
    box = block.get("bbox_norm")
    if isinstance(box, (list, tuple)) and len(box) == 4:
        where = "、".join(f"{float(v):.3f}" for v in box)
        where_text = f"整页归一化边界 [x, y, w, h] = [{where}]"
    else:
        where_text = "这一块的边界读不出来（没有 bbox_norm）"
    colored_px = (stats or {}).get("colored_px")
    text = (
        f"这一块（一道题）的位置：{where_text}。\n"
        f"统计到这一块里有 {colored_px} 个红笔像素。\n"
        f"这点红笔是什么意思？"
    )
    return [
        {"role": "system", "content": SEMANTICS_SYSTEM},
        {"role": "user", "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": image_url}},
        ]},
    ]


def _mime_of(path: Path) -> str:
    """按后缀定 MIME。认不出的后缀**抛错**，不许拿 octet-stream 去赌上游收不收。"""
    suffix = path.suffix.lower()
    if suffix == ".png":
        return "image/png"
    if suffix in (".jpg", ".jpeg"):
        return "image/jpeg"
    if suffix == ".webp":
        return "image/webp"
    raise ValueError(
        f"认不出这张照片的格式（{path.name}）：只支持 .png / .jpg / .jpeg / .webp"
    )


def _shrink(image: ink.InkImage, max_side: int) -> ink.InkImage:
    """最近邻等比缩到最长边 ≤ `max_side`（纯标准库，够用：只为了少传几 MB）。

    缩小后**要重新编码**——最近的采样点，不做插值：这一层的目的是让上游看得清
    红笔的形状，不是做图像处理。
    """
    scale = max_side / max(image.width, image.height)
    width = max(1, int(image.width * scale))
    height = max(1, int(image.height * scale))
    pixels = []
    for y in range(height):
        src_y = min(image.height - 1, int(y / scale))
        row = src_y * image.width
        for x in range(width):
            pixels.append(image.pixels[row + min(image.width - 1, int(x / scale))])
    return ink.InkImage(width, height, pixels)


def image_data_url(path: str | Path, *, max_side: int = MAX_SIDE) -> str:
    """整页照片 → `data:<mime>;base64,…`（模型 API 认的形状）。

    - 最长边已经 ≤ `max_side`：**原字节直发**（不重新编码，不损失任何像素）。
    - 超了：解码 → 等比缩 → 重新编码成 PNG（只有 PNG 能解，`ink.read_png` 的
      支持范围就是它的诚实边界；别的格式超限会抛 `UnsupportedImage`，**绝不静默
      发一张 12MB 的图出去**）。
    """
    path = Path(path)
    mime = _mime_of(path)
    blob = path.read_bytes()
    if mime == "image/png":
        image = ink.read_png(path)
        if max(image.width, image.height) > max_side:
            mime = "image/png"
            blob = ink.encode_png(_shrink(image, max_side))
    return f"data:{mime};base64," + base64.b64encode(blob).decode("ascii")


def parse_semantics(text: str) -> dict:
    """模型原文 → 语义答案（**从不抛异常**：答不出来是「判不准」，不是调用失败）。

    返回 `{semantics, confidence, reason, parsed}`：

    - `parsed=True` 且 `semantics` 是字符串：模型给了一个可读的语义（**枚举外的值
      也照原样留着**——决策层据此落向「判不准」，人能在页文件里看到它写了什么）。
    - `parsed=False`：抠不出 JSON，或 `semantics` 缺失/不是字符串。`reason` 里留下
      模型的原话（截断），不许只说「解析失败」。
    - `confidence`：只**记录**，不设闸门（与 #4 的口径一致：判错不设闸门）。
      不是 [0,1] 里的真数就记 `None`——记一个假数字比记 None 更坏。
    """
    raw = text if isinstance(text, str) else ""
    try:
        data = extract_json(raw)
    except ValueError:
        return {"semantics": None, "confidence": None, "parsed": False,
                "reason": raw.strip()[:300] or "（模型返回了空响应）"}
    if not isinstance(data, dict):
        return {"semantics": None, "confidence": None, "parsed": False,
                "reason": f"模型返回的不是一个 JSON 对象：{str(data)[:200]}"}
    semantics = data.get("semantics")
    if not isinstance(semantics, str) or not semantics.strip():
        return {"semantics": None, "confidence": _confidence(data.get("confidence")),
                "parsed": False,
                "reason": f"模型没给出可用的 semantics 字段：{str(data)[:200]}"}
    return {"semantics": semantics.strip(), "confidence": _confidence(data.get("confidence")),
            "parsed": True, "reason": _reason_text(data.get("reason"))}


def _confidence(value):
    """[0,1] 里的真数 → float；别的（字符串、布尔、越界、NaN）→ None。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if value != value or value < 0.0 or value > 1.0:   # NaN 与越界都不记
        return None
    return value


def _reason_text(value) -> str:
    return value.strip()[:300] if isinstance(value, str) else ""


class HttpSemantics:
    """抽取角色的真实现：`(块, 统计, 照片路径) → 语义答案`。接缝都可注入。"""

    def __init__(self, config, runs_dir: Path | str, *, transport=None,
                 sleep=time.sleep, env=None, clock=None, timeout: float = 180,
                 retries: int = 3) -> None:
        self.config = config
        self.runs_dir = Path(runs_dir)
        self.transport = transport
        self.sleep = sleep
        self.env = env
        self.clock = clock
        self.timeout = timeout
        self.retries = retries

    def __call__(self, block: dict, stats: dict, image_path) -> dict:
        """问一次模型。**调用失败抛 `ModelUnavailable`**（调用方据此报 502、什么都不写）。"""
        messages = semantics_messages(block=block, stats=stats,
                                      image_url=image_data_url(image_path))
        call = model_chat(
            self.config, messages, tag=INTAKE_TAG, runs_dir=self.runs_dir,
            transport=self.transport, sleep=self.sleep, env=self.env, clock=self.clock,
            timeout=self.timeout, retries=self.retries,
        )
        answer = _with_identity(parse_semantics(call.text), call)
        return answer


def _with_identity(answer: dict, call: ModelCall) -> dict:
    """把「谁判的、留档是哪一份」补进答案——页文件的审计面要它（验收 3）。"""
    return {**answer, "provider": call.provider, "model": call.model, "run_id": call.run_id}


# `default_transport` / `save_run` 是给测试与将来的角色用的显式再导出
# （与 `judge_client.py` 同一形状）：这一层自己不实现 HTTP。
__all__ = [
    "INTAKE_TAG", "MAX_SIDE", "SEMANTICS_SYSTEM", "HttpSemantics",
    "image_data_url", "parse_semantics", "semantics_messages",
    "default_transport", "save_run",
]
