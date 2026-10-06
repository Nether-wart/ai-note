"""红笔痕迹的像素统计与裁剪体检：**阈值在这里，只有一份**（#11）。

**这个模块只回答两件事：这一块有没有红笔、有多少。** 它**不做语义判断**——
这点红笔是勾、是圈、是订正、还是只是分数，由模型回答（CONTEXT「红笔痕迹」、
spec #2 第 24 条），归 #12（B4）。所以这里不许长出 `verdict`/`kind`/`is_correction`
这类字段：那是第二份语义实现，而模型才是判语义的那一个。

**红笔痕迹是两个彼此独立的用途，共用同一份颜色掩膜**（CONTEXT「红笔痕迹」）：
它是「这道题错过」的证据（筛选错题的判据），也是擦除必须擦净的对象
（订正里含正确答案，印在重做纸上就毁掉重做）。所以**筛选与体检必须读同一份掩膜**，
否则两处会各判各的——这正是本工单要收掉的东西。

## 两个层次、两颗常量（不是一颗）

原型里有两颗各自回答**不同问题**的阈值（`proto/slice.py`）：

| 层次 | 常量 | 原型 | 问题 |
|---|---|---|---|
| 像素级 | `COLORED_SATURATION_MIN = 60` | `slice.py:849`（体检）、`:921`（擦除） | **一个像素**算不算红笔痕迹 |
| 框级 | `COLOR_MIN_PIXELS = 20` | `slice.py:888`（体检判据） | **一个框**里超过多少像素才算「这题有红笔」 |

把 `60` 与 `20` 收成同一颗常量是**错的**——它们是两个不同问题的答案。原型真正
重复实现的是 `60`：它在体检（`_ink_masks`）与擦除（`_np_masks`）里**各写了一遍**。

**收成了什么、谁共用**：`ink_masks`（像素级掩膜）、`colored_pixels`（按框统计，
筛选侧）、`ink_statistics`（统计）、`cropcheck`（框级判据的输入）四条读数**都读
同一个** `COLORED_SATURATION_MIN`；`cropcheck` 的「框里有红笔」再叠上框级的
`COLOR_MIN_PIXELS`。深色那两颗（`DARK_MAX_LIGHTNESS`/`DARK_MAX_SATURATION`）
同样收掉了原型的三份副本。

**诚实边界**：擦除路径（原型的 `_np_masks`）**还没被搬进 `server/`**（`#11` 不管
擦除），所以「擦除与筛选读同一颗」这句话现在是**结构上不再可能分叉**，不是
「已经接好线」——将来搬擦除时，`ink_masks` 就是它该调的那一个函数，不许再写第二份
`sat >= 60`。

## 深色掩膜：为什么它与红笔掩膜共用同一个派生量

深色（印刷体候选，**黑笔手写也在内**）是「暗且低饱和」：`mx < DARK_MAX_LIGHTNESS`
且 `sat < DARK_MAX_SATURATION`（`proto/slice.py:847-848`、`:921`，`clean_health`
里还有第三份，`:1060`）。这三份一起收进 `DARK_MAX_LIGHTNESS`/`DARK_MAX_SATURATION`。

## 依赖：**只用标准库**

契约 §11 记着「`server/` 零第三方依赖」。所以这里不用 PIL、不用 numpy：PNG 的读写
是这个模块自己的 `read_png`/`write_png`（`zlib` + 结构化字节解析），与 `proto/` 里
那套 PIL/numpy 实现**没关系**——`proto/` 是冻结的只读证据，口径继承、代码不继承。
代价明说：只支持 **8 位**非交错 PNG（灰度 / RGB / RGBA / 调色板）；16 位、亚字节位深
（1/2/4 位）、交错图**抛错喊出来**，绝不静默当成空图或读出一张假图——
「没红笔」和「读不懂」必须分得开。
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

# ---------------------------------------------------------------- 阈值（唯一一份）

# 像素级：一个像素算不算红笔痕迹。彩笔＝高饱和（`sat = max(RGB) - min(RGB)`）。
# 用途：ink_masks（掩膜）、colored_pixels（按框统计）、cropcheck（框级判据）。
# 口径来自 proto/slice.py:849 与 :921 —— 那两处各写了一遍 60，这里只有一份。
COLORED_SATURATION_MIN = 60

# 框级：一个框里有多少红笔像素才算「这块有红笔」。**绝对像素数，不是比例**
# （proto/slice.py:888-890 的 `in_color > 20`）。它与像素级阈值回答的是两个问题。
COLOR_MIN_PIXELS = 20

# 深色墨迹（印刷体候选，黑笔手写也在内）：暗且低饱和（proto/slice.py:847-848、:921）。
DARK_MAX_LIGHTNESS = 120
DARK_MAX_SATURATION = 40

# 体检的另外两条条带判据（proto/slice.py:886-896）。它们只用于 cropcheck 的报警，
# 不是红笔统计的一部分；放在这里是为了不再散落字面量。
BLANK_MAX_DARK_RATIO = 0.001
STRIP_MIN_DARK_RATIO = 0.005

# `ink_statistics` 能返回的字段全集。**这是统计的边界**：只有计数与占比，没有判定。
STAT_KEYS = (
    "area",
    "colored_px",
    "colored_ratio",
    "dark_px",
    "dark_ratio",
)


class UnsupportedImage(ValueError):
    """这张图本模块读不了（形状不在支持范围内）。调用方必须喊出来，不许当空图。"""


class InkImage:
    """一张已解码的整页照片：`(w, h)` 与 RGB 像素行优先排列。"""

    __slots__ = ("width", "height", "pixels")

    def __init__(self, width: int, height: int, pixels: list[tuple[int, int, int]]) -> None:
        self.width = width
        self.height = height
        self.pixels = pixels

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    def __repr__(self) -> str:  # pragma: no cover - 只为调试可读
        return f"InkImage({self.width}x{self.height}, {len(self.pixels)}px)"


_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def read_png(path: str | Path) -> InkImage:
    """读一张 PNG 成 `InkImage`。形状不支持时抛 `UnsupportedImage`。"""
    raw = Path(path).read_bytes()
    if not raw.startswith(_PNG_MAGIC):
        raise UnsupportedImage(f"{path} 不是 PNG（开头不是 PNG 魔数）")

    width = height = depth = color_type = None
    interlace = 0
    idat = bytearray()
    palette = b""
    pos = len(_PNG_MAGIC)
    while pos + 8 <= len(raw):
        (length,) = struct.unpack_from(">I", raw, pos)
        kind = raw[pos + 4:pos + 8]
        chunk = raw[pos + 8:pos + 8 + length]
        if len(chunk) != length:
            raise UnsupportedImage(f"{path} 的 {kind!r} 块被截断")
        if kind == b"IHDR":
            width, height, depth, color_type, _, _, interlace = struct.unpack(">IIBBBBB", chunk)
        elif kind == b"IDAT":
            idat += chunk
        elif kind == b"PLTE":
            palette = chunk
        elif kind == b"IEND":
            break
        pos += 12 + length  # length + type + data + crc

    if width is None or height is None:
        raise UnsupportedImage(f"{path} 里没有 IHDR")
    if width < 1 or height < 1:
        raise UnsupportedImage(f"{path} 的尺寸不合法：{width}x{height}")
    if interlace:
        raise UnsupportedImage(f"{path} 是交错（Adam7）PNG，本模块不读交错图")
    # 别的位深一律抛错：4 位索引当 8 位样本读会静默给出一张看起来正常、其实全错的图，
    # 而 16 位要丢弃低位才能凑成 8 位样本——两件都是本模块不该悄悄做的事。
    if depth != 8:
        raise UnsupportedImage(f"{path} 的位深是 {depth}，本模块只读 8 位（16 位与亚字节位深都抛错）")
    if color_type == 3:  # 调色板：一个样本是一个 PLTE 索引
        channels = 1
        if not palette:
            raise UnsupportedImage(f"{path} 是调色板 PNG 但没有 PLTE 块")
    else:
        channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color_type)
        if channels is None:
            raise UnsupportedImage(f"{path} 的 color_type={color_type} 不是本模块认得的形状")

    try:
        flat = zlib.decompress(bytes(idat))
    except zlib.error as exc:
        raise UnsupportedImage(f"{path} 的 IDAT 解不开：{exc}") from exc

    stride = width * channels
    if len(flat) < (stride + 1) * height:
        raise UnsupportedImage(f"{path} 的像素数据比 {width}x{height} 少")
    rows = _unfilter(flat, width, height, channels)
    if color_type == 3:
        return InkImage(width, height, _expand_palette(rows, palette))
    return InkImage(width, height, _to_rgb(rows, channels))


def _expand_palette(rows: list[bytearray], palette: bytes) -> list[tuple[int, int, int]]:
    """PLTE 索引 → RGB。一个索引越界就是文件坏了，喊出来。"""
    table = [
        (palette[i], palette[i + 1], palette[i + 2])
        for i in range(0, len(palette) - 2, 3)
    ]
    out: list[tuple[int, int, int]] = []
    for row in rows:
        for index in row:
            if index >= len(table):
                raise UnsupportedImage(f"调色板索引 {index} 超出 PLTE 的 {len(table)} 色")
            out.append(table[index])
    return out


def _unfilter(flat: bytes, width: int, height: int, channels: int) -> list[bytearray]:
    """逐行反滤波（PNG 的 0-4 号滤波器）。每行前面那个字节是滤波器类型。"""
    stride = width * channels
    rows: list[bytearray] = []
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        ftype = flat[pos]
        line = bytearray(flat[pos + 1:pos + 1 + stride])
        pos += stride + 1
        if ftype == 0:
            pass
        elif ftype == 1:  # Sub：加上左边一个像素
            for i in range(channels, stride):
                line[i] = (line[i] + line[i - channels]) & 0xFF
        elif ftype == 2:  # Up：加上上面一个像素
            for i in range(stride):
                line[i] = (line[i] + prev[i]) & 0xFF
        elif ftype == 3:  # Average：加上左与上的平均
            for i in range(stride):
                left = line[i - channels] if i >= channels else 0
                line[i] = (line[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:  # Paeth
            for i in range(stride):
                a = line[i - channels] if i >= channels else 0
                b = prev[i]
                c = prev[i - channels] if i >= channels else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[i] = (line[i] + pred) & 0xFF
        else:
            raise UnsupportedImage(f"未知的 PNG 滤波器类型 {ftype}")
        rows.append(line)
        prev = line
    return rows


def _to_rgb(rows: list[bytearray], channels: int) -> list[tuple[int, int, int]]:
    if channels == 3:
        return [
            (row[i], row[i + 1], row[i + 2])
            for row in rows
            for i in range(0, len(row), 3)
        ]
    if channels == 4:
        return [
            (row[i], row[i + 1], row[i + 2])
            for row in rows
            for i in range(0, len(row), 4)
        ]
    if channels == 1:  # 灰度：三通道同值
        return [(v, v, v) for row in rows for v in row]
    return [(row[i], row[i], row[i]) for row in rows for i in range(0, len(row), 2)]  # 灰度+alpha


def write_png(path: str | Path, image: InkImage) -> None:
    """把 `InkImage` 写成一个 PNG（8 位 RGB、非交错）。

    生产代码走 `encode_png` 那一半（#12 的抽取角色要把块图发给模型）；
    `write_png` 是给**合成图夹具**用的，这样测试造图不必引入图片库。
    """
    Path(path).write_bytes(encode_png(image))


def encode_png(image: InkImage) -> bytes:
    """`InkImage` → PNG 字节（8 位 RGB、非交错、滤波器 0）。

    PNG 的写出只有这一处：夹具落盘（`write_png`）与发给模型的数据 URL
    （#12，缩放后要重新编码）都从这里走——两处各写一份编码器迟早写出两种图。
    """
    if len(image.pixels) != image.width * image.height:
        raise ValueError(
            f"像素数与尺寸对不上：{image.width}x{image.height} 要有 "
            f"{image.width * image.height} 个像素，实际 {len(image.pixels)}"
        )
    raw = bytearray()
    for y in range(image.height):
        raw.append(0)  # 滤波器 0（None）：夹具的图小，不值得为压缩率换取代码
        for x in range(image.width):
            r, g, b = image.pixels[y * image.width + x]
            raw += bytes((r, g, b))
    ihdr = struct.pack(">IIBBBBB", image.width, image.height, 8, 2, 0, 0, 0)
    return (
        _PNG_MAGIC
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + _chunk(b"IEND", b"")
    )


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def _clip_box(box, width: int, height: int) -> tuple[int, int, int, int]:
    """把 xyxy 框裁到图边界。越界是常态（块边界由人标），所以不报错。

    越界就收窄（`x0 = min(x0, width)` 这种），所以裁完永远 `x0 <= x1`——不需要再交换。
    """
    if len(box) != 4:
        raise ValueError(f"框要是四个数（xyxy），收到 {box!r}")
    x0, y0, x1, y1 = (int(v) for v in box)
    return (
        max(0, min(x0, width)),
        max(0, min(y0, height)),
        max(0, min(x1, width)),
        max(0, min(y1, height)),
    )


def ink_masks(image: InkImage) -> tuple[list[bool], list[bool]]:
    """`(红笔掩膜, 深色掩膜)`，与像素一一对应。

    - 红笔掩膜：`饱和 >= COLORED_SATURATION_MIN`
    - 深色掩膜：`最亮通道 < DARK_MAX_LIGHTNESS` 且 `饱和 < DARK_MAX_SATURATION`

    黑笔与印刷体在灰度上同色（`proto/slice.py:860-862`），所以深色掩膜分不开它们——
    它只用来挡「红笔统计把黑笔算进去」，不是「这是印刷体」的判据。

    **这是 `COLORED_SATURATION_MIN` 唯一的消费点**：筛选、体检、统计都经它拿掩膜，
    所以几方不可能对同一张图给出不同的红笔像素数（原型的 `60` 在体检与擦除里
    各写了一遍，这里只有一份）。
    """
    colored: list[bool] = []
    dark: list[bool] = []
    for r, g, b in image.pixels:
        mx, mn = max(r, g, b), min(r, g, b)
        sat = mx - mn
        colored.append(sat >= COLORED_SATURATION_MIN)
        dark.append(mx < DARK_MAX_LIGHTNESS and sat < DARK_MAX_SATURATION)
    return colored, dark


def _count(mask: list[bool], width: int, y0: int, y1: int, x0: int, x1: int) -> int:
    """半开区间 `[y0,y1) × [x0,x1)` 里为真的位数。调用方已经裁过框。

    只看行内一段（切片 + `sum`），不逐像素走 Python 循环——整页照片是百万像素级。
    """
    return sum(sum(mask[y * width + x0: y * width + x1]) for y in range(y0, y1))


def _mask_pixels(mask: list[bool], image: InkImage, box) -> int:
    x0, y0, x1, y1 = _clip_box(box, image.width, image.height)
    return _count(mask, image.width, y0, y1, x0, x1)


def colored_pixels(image: InkImage, box: tuple[int, int, int, int]) -> int:
    """框内红笔像素数（`box` 是整页像素 xyxy）。框会被裁到图边界，不报错。

    **筛选路径读的就是这一颗常量**：它先把 `ink_masks` 造出来，再按框统计，
    所以「改一处，两处都变」不是靠约定，是靠只有一处可改。
    """
    colored, _ = ink_masks(image)
    return _mask_pixels(colored, image, box)


def dark_pixels(image: InkImage, box: tuple[int, int, int, int]) -> int:
    """框内深色像素数。与 `colored_pixels` 同一套掩膜、同一套裁剪。"""
    _, dark = ink_masks(image)
    return _mask_pixels(dark, image, box)


def ink_statistics(image: InkImage, box: tuple[int, int, int, int]) -> dict:
    """一块的墨迹统计：**只有计数与占比，没有判定**（字段全集见 `STAT_KEYS`）。

    `area` 是**裁剪后**的框面积（框越界时它小于框的原始面积）——两个占比的分母。
    这里不出 `has_red_ink` 这类结论：框级判据是 `cropcheck` 的事，语义是模型的事。

    掩膜只造一次，两次计数共用它：这是**同一份掩膜同时服务筛选与保密**的落点
    （CONTEXT「红笔痕迹」）——不是「两次调用恰好结果一样」。
    """
    x0, y0, x1, y1 = _clip_box(box, image.width, image.height)
    colored, dark = ink_masks(image)
    area = (x1 - x0) * (y1 - y0)
    colored_px = _count(colored, image.width, y0, y1, x0, x1)
    dark_px = _count(dark, image.width, y0, y1, x0, x1)
    return {
        "area": area,
        "colored_px": colored_px,
        "colored_ratio": (colored_px / area) if area else 0.0,
        "dark_px": dark_px,
        "dark_ratio": (dark_px / area) if area else 0.0,
    }


def cropcheck(image: InkImage, box: tuple[int, int, int, int],
              *, problem_type: str | None = None) -> dict:
    """裁剪体检：不用眼睛也能查出「框切掉了内容」与「框里混进了红笔」。

    **它只能报警两件事**（`proto/slice.py:910`）：「框小了」与「框里混进彩笔」。
    黑笔手写与印刷体在灰度上同色，机器分不开，所以「解答题的框下方有一大片墨迹」
    是正常的——那正是该被排除的手写解答，所以解答题不做下方条带检查。

    框级红笔判据读的是 `COLOR_MIN_PIXELS`（**绝对像素数**），像素级读的是
    `COLORED_SATURATION_MIN`——两颗常量、两个问题，都在本模块。
    """
    width, height = image.width, image.height
    x0, y0, x1, y1 = _clip_box(box, width, height)
    colored, dark = ink_masks(image)  # 整张图只走一遍：四条读数共用这一份掩膜
    area = (x1 - x0) * (y1 - y0)
    colored_px = _count(colored, width, y0, y1, x0, x1)
    dark_px = _count(dark, width, y0, y1, x0, x1)
    stats = {
        "area": area,
        "colored_px": colored_px,
        "colored_ratio": (colored_px / area) if area else 0.0,
        "dark_px": dark_px,
        "dark_ratio": (dark_px / area) if area else 0.0,
    }
    flags: list[dict] = []

    if area and stats["dark_ratio"] < BLANK_MAX_DARK_RATIO:
        flags.append({
            "kind": "blank",
            "level": "warning",
            "message": "框内几乎空白——框错了",
        })

    if colored_px > COLOR_MIN_PIXELS:
        flags.append({
            "kind": "colored_ink",
            "level": "warning",
            "px": colored_px,
            "message": f"框内有红笔痕迹 {colored_px}px——重做纸会把它一起印出来"
                       f"（订正常常是红笔，正是最不该印上重做纸的东西）",
        })

    # 框右外同高条带（proto/slice.py:891-893）
    right_area = (width - x1) * (y1 - y0)
    if right_area:
        right_px = _count(dark, width, y0, y1, x1, width)
        if right_px / right_area > STRIP_MIN_DARK_RATIO:
            flags.append({
                "kind": "ink_right_of_box",
                "level": "warning",
                "px": right_px,
                "ratio": right_px / right_area,
                "message": f"右边界外还有成片墨迹 {right_px}px"
                           f"（占该条带 {100 * right_px / right_area:.1f}%）"
                           f"——可能切掉了题干或选项",
            })

    # 框下外条带。解答题免检：下方本就是该被排除的手写解答（proto/slice.py:894-896）。
    below_area = (x1 - x0) * (height - y1)
    if below_area and problem_type != "solution":
        below_px = _count(dark, width, y1, height, x0, x1)
        if below_px / below_area > STRIP_MIN_DARK_RATIO:
            flags.append({
                "kind": "ink_below_box",
                "level": "warning",
                "px": below_px,
                "ratio": below_px / below_area,
                "message": f"下边界外有成片墨迹 {below_px}px"
                           f"（占该条带 {100 * below_px / below_area:.1f}%）"
                           f"——正文可能被切掉了",
            })

    return {"box": [x0, y0, x1, y1], "stats": stats, "flags": flags}


def page_block_reports(image: InkImage, page: dict) -> list[dict]:
    """一页的每块红笔统计（契约 §10.2.1 预留的页文件 `ink` 键）。

    块边界是**整页**坐标：`bbox_px` 是 xyxy（`pages.py` 的 D5 基准对照表），
    正好是本模块 `box` 的形状。块没有可用边界时给 `None` 并**明说原因**——
    绝不静默当成「零红笔」（0 与「不知道」是两件事）。
    """
    reports: list[dict] = []
    for block in page.get("blocks") or []:
        box = block.get("bbox_px")
        report: dict = {"id": block.get("id")}
        if not isinstance(box, (list, tuple)) or len(box) != 4:
            report["stats"] = None
            report["reason"] = "block_bbox_missing"
            report["message"] = f"块 {block.get('id')!r} 没有可用的 bbox_px，红笔统计做不了"
        else:
            report["stats"] = ink_statistics(image, box)
            report["reason"] = None
            report["message"] = None
        reports.append(report)
    return reports


def page_ink_regions(image: InkImage) -> list[dict]:
    """整页墨迹 → **连通区域**列表（`{"bbox_norm": [x, y, w, h], "px": N}`）。

    这是覆盖率对账（`segmentation.check_coverage`）唯一的输入来源：它问的是
    「**一片墨迹**有没有被框住」，所以要把散落的墨迹像素聚回成片。判据是
    **八连通**（对角贴着算同一片）——笔画是连着画的，四连通会把一笔拆成几片。

    **红笔与深色都算墨迹**（两个掩膜的并集）：墨迹不只是黑笔——红笔订正同样是
    页面上该被框住的内容；只数深色会把一整片红笔订正漏出覆盖率之外。
    这**不是**判定：本函数只回答「哪一片、多少像素」，语义归 #12。

    输出按**行优先**（左上角先）——同一张图永远给同一个列表（确定性；
    测试与界面都按这个顺序读）。`bbox_norm` 是整页归一化 xywh（D5 的规范基准），
    `px` 是这一片里的墨迹像素数（与 `check_coverage` 的 `px` 同一口径）。
    """
    width, height = image.width, image.height
    colored, dark = ink_masks(image)      # 整张图只走一遍：两个掩膜一起造
    ink_px = [c or d for c, d in zip(colored, dark)]
    seen = bytearray(width * height)
    regions: list[dict] = []
    for start in range(width * height):
        if not ink_px[start] or seen[start]:
            continue
        # 显式栈的洪泛填充：百万像素级的整页照片不许用递归（栈会爆）。
        seen[start] = 1
        stack = [start]
        count = 0
        min_x = max_x = start % width
        min_y = max_y = start // width
        while stack:
            index = stack.pop()
            count += 1
            x, y = index % width, index // width
            min_x, max_x = min(min_x, x), max(max_x, x)
            min_y, max_y = min(min_y, y), max(max_y, y)
            for ny in (y - 1, y, y + 1):
                if ny < 0 or ny >= height:
                    continue
                base = ny * width
                for nx in (x - 1, x, x + 1):
                    if nx < 0 or nx >= width:
                        continue
                    neighbour = base + nx
                    if ink_px[neighbour] and not seen[neighbour]:
                        seen[neighbour] = 1
                        stack.append(neighbour)
        regions.append({
            "bbox_norm": [min_x / width, min_y / height,
                          (max_x - min_x + 1) / width, (max_y - min_y + 1) / height],
            "px": count,
        })
    return regions

