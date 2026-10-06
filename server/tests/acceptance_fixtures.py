"""模型上岗验收用的**合成**夹具（`CONTEXT.md`「验收」「视觉探针」）。

两件东西，两处来源，理由不一样：

1. **视觉探针**用这里合成的数字图——它**不需要任何私人内容**，所以在任何机器上
   都跑得起来，包括刚 clone 下来、`data/` 是空的机器。探针防的是一个很具体的形状：
   **纯文本模型收到图片不会报错**，它会忽略图片、照着提示词把内容编出来。
2. **真实整页照片**用本机的 `data/pages/*.png`（**不是**这里合成的）。
   仓库里的 `data/` 是私人内容的副本、被 `.gitignore` 拦住，所以**不进仓库**：
   「种子（词表）进仓库，私人内容一律不进」。照片不在的机器上，那一条验收
   **明说跳过**，不冒充通过。

七段数码管画出来的数字**不是手写数字**：它测的是「模型真的打开了这张图」这一件事，
不是手写识别能力。把那句话说清，免得一条通过的验收被读成一件它没证明的事。
"""

from __future__ import annotations

from pathlib import Path

from server import ink

# 七段数码管的段名与每个数字点亮的段（教科书口径）。
SEGMENTS = {
    "0": "abcdef",
    "1": "bc",
    "2": "abged",
    "3": "abgcd",
    "4": "fgbc",
    "5": "afgcd",
    "6": "afgecd",
    "7": "abc",
    "8": "abcdefg",
    "9": "abcfgd",
}

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)


def _blank(width: int, height: int) -> list[tuple[int, int, int]]:
    return [WHITE] * (width * height)


def _fill(pixels, width, height, box) -> None:
    x, y, w, h = (int(v) for v in box)
    for row in range(max(0, y), min(height, y + h)):
        base = row * width
        for col in range(max(0, x), min(width, x + w)):
            pixels[base + col] = BLACK


def draw_digits(text: str, *, digit_width: int = 36, digit_height: int = 64,
                thickness: int = 6, gap: int = 14, margin: int = 24) -> ink.InkImage:
    """一串数字 → 一张白底黑字的图（七段数码管画法，纯标准库）。"""
    count = max(1, len(text))
    width = margin * 2 + count * digit_width + (count - 1) * gap
    height = margin * 2 + digit_height
    pixels = _blank(width, height)
    half = (digit_height - thickness) // 2

    for index, char in enumerate(text):
        on = SEGMENTS.get(char)
        if not on:
            raise ValueError(f"七段数码管画不出这个字符：{char!r}（只支持 0-9）")
        left = margin + index * (digit_width + gap)
        top = margin
        right = left + digit_width - thickness
        bottom = top + digit_height - thickness
        mid = top + half
        lower_top = top + (digit_height + thickness) // 2
        lower_h = bottom - lower_top

        segments = {
            "a": (left + thickness, top, digit_width - 2 * thickness, thickness),
            "g": (left + thickness, mid, digit_width - 2 * thickness, thickness),
            "d": (left + thickness, bottom, digit_width - 2 * thickness, thickness),
            "f": (left, top + thickness, thickness, half - thickness),
            "b": (right, top + thickness, thickness, half - thickness),
            "e": (left, lower_top, thickness, lower_h),
            "c": (right, lower_top, thickness, lower_h),
        }
        for name in on:
            _fill(pixels, width, height, segments[name])

    return ink.InkImage(width, height, pixels)


def write_png(image: ink.InkImage, path: str | Path) -> Path:
    """写一张 PNG，**返回绝对路径**——验收报告里要能直接点开它看。"""
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(ink.encode_png(image))
    return path
