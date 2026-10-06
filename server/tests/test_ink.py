"""红笔统计与阈值（#11 B3）。

测的是**行为**，不是常量本身：

1. **合成图能区分红笔与黑笔**——白底 + 红笔块 + 黑笔块，红块统计 > 0、黑块 = 0
   （黑笔走深色掩膜 `mx < 120 & sat < 40`，不算红笔）。
2. **三条路径对同一张图给出同一个彩笔像素数**——掩膜（体检侧）、按框统计（筛选侧）、
   体检判据。这是「改一处，两处都变」的正确验证方式：断言两条路径的**输出一致**，
   而不是断言两个常量相等（那是自我欺骗式的测试，常量改成一样也照样绿）。
3. **统计不做语义判断**——返回的字段里不许出现「这是订正／这是勾」这类结论，
   语义归 #12（B4）的模型。

造图用**标准库**（本模块自己的 `write_png`），不联网、不引入图片库——
与契约 §11「`server/` 零第三方依赖」一致。
"""

from __future__ import annotations

import pathlib
import struct
import zlib

import pytest

from server import ink

WHITE = (255, 255, 255)
RED = (220, 30, 40)
BLACK = (20, 20, 20)


def color_image(w: int, h: int, color: tuple[int, int, int]) -> ink.InkImage:
    """纯色图。"""
    return ink.InkImage(w, h, [color] * (w * h))


def white_with_blocks(w: int, h: int, blocks) -> ink.InkImage:
    """白底 + 若干 `(x0, y0, x1, y1, color)` 实心块。"""
    px = [WHITE] * (w * h)
    for x0, y0, x1, y1, color in blocks:
        for y in range(y0, y1):
            for x in range(x0, x1):
                px[y * w + x] = color
    return ink.InkImage(w, h, px)


@pytest.fixture
def page(tmp_path):
    """白底 100×60，左半红笔块 (0,0,40,20)、右半黑笔块 (60,0,100,20)。"""
    img = white_with_blocks(100, 60, [(0, 0, 40, 20, RED), (60, 0, 100, 20, BLACK)])
    path = tmp_path / "page.png"
    ink.write_png(path, img)
    return path


# ---------------------------------------------------------------- 切片 1：PNG 读写

def test_write_then_read_png_returns_the_same_pixels(tmp_path):
    """造图与读图是本模块自己的活，用标准库——夹具不许引入图片库。"""
    img = ink.InkImage(3, 2, [RED, WHITE, BLACK, WHITE, RED, BLACK])
    path = tmp_path / "tiny.png"
    ink.write_png(path, img)

    back = ink.read_png(path)

    assert back.size == (3, 2)
    assert back.pixels == [RED, WHITE, BLACK, WHITE, RED, BLACK]


def test_encode_png_is_the_one_encoder(tmp_path):
    """PNG 的写出只有一处：`write_png` 就是 `encode_png` 落盘（#12 复用它重编码缩放后的图）。

    两处各写一份编码器，迟早会写出两种图（滤波器、位深、色彩类型各一套），
    而下游（模型、擦除、统计）读的是同一批字节。
    """
    img = ink.InkImage(2, 1, [RED, BLACK])
    path = tmp_path / "tiny.png"
    ink.write_png(path, img)

    assert path.read_bytes() == ink.encode_png(img)
    assert ink.read_png(path).pixels == [RED, BLACK]


def test_reading_something_that_is_not_a_png_says_so(tmp_path):
    """读不懂必须喊出来。静默当成空图会把「没红笔」和「读不了」混成一个答案。"""
    path = tmp_path / "not-a-png.png"
    path.write_bytes(b"this is not a png at all")

    with pytest.raises(ink.UnsupportedImage):
        ink.read_png(path)


# ------------------------------------------------- 切片 2：掩膜与按框统计（验收 1）

def test_page_photo_is_read_back_pixel_for_pixel(page):
    assert ink.read_png(page).size == (100, 60)


def test_red_ink_block_is_counted_and_black_ink_block_is_not(page):
    """验收 1：合成图上红笔与黑笔分得开。

    红块 40×20 = 800 像素；黑笔走深色掩膜（`mx < 120 & sat < 40`），
    所以黑块的**红笔**统计必须是 0——否则筛选会把整页黑笔的卷子全当成错题。
    """
    img = ink.read_png(page)

    assert ink.colored_pixels(img, (0, 0, 40, 20)) == 800   # 红块整块
    assert ink.colored_pixels(img, (60, 0, 100, 20)) == 0   # 黑块一个也不算红笔
    assert ink.dark_pixels(img, (60, 0, 100, 20)) == 800    # 但它是深色墨迹
    assert ink.dark_pixels(img, (0, 0, 40, 20)) == 0        # 红块不是深色


def test_the_two_masks_partition_pixels_the_way_the_prototype_says(page):
    """掩膜的口径照 `proto/slice.py:847-848`、`:921`，逐像素核对。"""
    img = ink.read_png(page)
    colored, dark = ink.ink_masks(img)

    def at(x, y):
        return y * img.width + x

    assert colored[at(10, 10)] and not dark[at(10, 10)]     # 红笔
    assert dark[at(70, 10)] and not colored[at(70, 10)]     # 黑笔
    assert not colored[at(50, 40)] and not dark[at(50, 40)]  # 白纸


def test_boxes_are_clipped_to_the_image_instead_of_raising(page):
    """框越界不报错：块边界来自人标的位置，越界是常态，不是异常。"""
    img = ink.read_png(page)

    assert ink.colored_pixels(img, (-100, -100, 20, 20)) == 20 * 20
    assert ink.colored_pixels(img, (90, 50, 500, 500)) == 0
    assert ink.colored_pixels(img, (10, 10, 10, 10)) == 0


# ------------------------------------------------- 切片 3：框级判据与统计字段

@pytest.fixture
def twenty_red_pixels(tmp_path):
    """一块**恰好 20 个**红笔像素：20 是框级阈值的边界值（`COLOR_MIN_PIXELS`）。"""
    img = white_with_blocks(40, 40, [(0, 0, 20, 1, RED)])
    path = tmp_path / "block.png"
    ink.write_png(path, img)
    return ink.read_png(path)


def test_ink_statistics_counts_and_ratios_but_nothing_else(page):
    """统计字段的**边界**：只有计数与占比，`area` 是裁剪后的框面积（占比的分母）。"""
    stats = ink.ink_statistics(ink.read_png(page), (0, 0, 40, 20))

    assert set(stats) == set(ink.STAT_KEYS)
    assert stats["colored_px"] == 800
    assert stats["dark_px"] == 0
    assert stats["area"] == 800
    assert stats["colored_ratio"] == 1.0
    assert stats["dark_ratio"] == 0.0


def test_ink_statistics_area_follows_the_clipped_box(page):
    """框越界时 `area` 是**裁剪后**的面积——否则占比会算出小于真实值的假读数。"""
    stats = ink.ink_statistics(ink.read_png(page), (0, 0, 40, 999))

    assert stats["area"] == 40 * 60
    assert stats["colored_px"] == 800
    assert stats["colored_ratio"] == pytest.approx(800 / 2400)


def test_cropcheck_flags_a_box_that_has_nearly_no_dark_ink(page):
    """框几乎空白 = 框错了（`proto/slice.py:886-887` 的 `in_dark/area < 0.001`）。"""
    result = ink.cropcheck(ink.read_png(page), (40, 40, 100, 60))

    assert [f["kind"] for f in result["flags"]] == ["blank"]
    assert result["stats"]["dark_px"] == 0


def test_cropcheck_flags_red_ink_inside_the_box(page):
    """框里混进红笔：重做纸会把它一起印出来——这是体检要抓的第二件事。"""
    result = ink.cropcheck(ink.read_png(page), (0, 0, 40, 20))

    assert "colored_ink" in [f["kind"] for f in result["flags"]]
    flag = next(f for f in result["flags"] if f["kind"] == "colored_ink")
    assert flag["px"] == 800
    assert flag["level"] == "warning"


def test_cropcheck_flags_ink_just_outside_the_right_edge(page):
    """框右边还有成片墨迹 = 框小了，可能切掉题干或选项（`proto/slice.py:891-893`）。

    条带是 (30,0,100,60)：红块的后 10×20 不是深色，所以只数黑块 40×20 = 800。
    """
    result = ink.cropcheck(ink.read_png(page), (0, 0, 30, 60))

    kinds = [f["kind"] for f in result["flags"]]
    assert "ink_right_of_box" in kinds
    flag = next(f for f in result["flags"] if f["kind"] == "ink_right_of_box")
    assert flag["px"] == 800


def test_cropcheck_does_not_check_below_a_solution_box(page):
    """解答题框下方本就是手写解答，检查它只会制造假警报（`proto/slice.py:894-896`）。"""
    img = white_with_blocks(100, 60, [(0, 0, 40, 20, RED), (0, 30, 40, 60, BLACK)])

    checked = ink.cropcheck(img, (0, 0, 40, 20))
    solution = ink.cropcheck(img, (0, 0, 40, 20), problem_type="solution")

    assert "ink_below_box" in [f["kind"] for f in checked["flags"]]
    assert "ink_below_box" not in [f["kind"] for f in solution["flags"]]


def test_cropcheck_says_it_is_clean_when_it_is_clean(page):
    """白底连着白边 → 一个警报都不该响（只在框内和框外都干净时才成立）。"""
    img = white_with_blocks(60, 40, [(10, 10, 50, 30, BLACK)])

    result = ink.cropcheck(img, (10, 10, 50, 30))

    assert result["flags"] == []


def test_cropcheck_reads_the_box_level_constant(twenty_red_pixels, monkeypatch):
    """验收 2：`COLOR_MIN_PIXELS` 是**唯一**决定「框里有红笔」的那个数。

    20 个像素是边界：默认阈值下不算（`> 20` 不成立），把它调小一处，判据就变。
    """
    default = ink.cropcheck(twenty_red_pixels, (0, 0, 20, 1))
    assert "colored_ink" not in [f["kind"] for f in default["flags"]]

    monkeypatch.setattr(ink, "COLOR_MIN_PIXELS", 19)
    lowered = ink.cropcheck(twenty_red_pixels, (0, 0, 20, 1))
    assert "colored_ink" in [f["kind"] for f in lowered["flags"]]


# ------------------------- 切片 4：同一个像素阈值，多条路径（验收 2 的真正判据）

def test_saturation_threshold_is_the_only_thing_that_decides_a_colored_pixel():
    """一颗像素的边界值：饱和 40 与 60 在阈值 `>= 60` 的两侧。

    这是**常量本身的口径**（`proto/slice.py:849`、`:921` 的 `sat >= 60`），
    不是「两个常量相等」那种自我欺骗式断言。
    """
    faint = ink.InkImage(1, 1, [(140, 100, 100)])   # 饱和 40 → 不是红笔
    clear = ink.InkImage(1, 1, [(160, 100, 100)])   # 饱和 60 → 是红笔

    assert ink.colored_pixels(faint, (0, 0, 1, 1)) == 0
    assert ink.colored_pixels(clear, (0, 0, 1, 1)) == 1


def test_one_changed_constant_moves_both_sides(page, monkeypatch):
    """验收 2 逐字：「改一处，两处都变」。

    改的**只有** `COLORED_SATURATION_MIN` 一处，掩膜与按框统计必须**一起**跟着变
    （两者相差的 200 是「框外的红笔」——那正是体检与筛选各判各的时会漏掉/多算的量）。
    """
    img = ink.read_png(page)
    whole = (0, 0, 100, 60)
    red_only = (0, 0, 40, 20)

    before_mask = sum(ink.ink_masks(img)[0])
    before_count = ink.colored_pixels(img, whole)

    monkeypatch.setattr(ink, "COLORED_SATURATION_MIN", 300)

    after_mask = sum(ink.ink_masks(img)[0])
    after_count = ink.colored_pixels(img, whole)

    assert before_mask == after_mask + 800  # 那一块红笔整块从掩膜里消失
    assert after_mask == 0                  # 白纸与黑笔的饱和都不到 300
    assert after_count == after_mask        # 「两处」现在仍读同一个数
    assert before_count == 800
    # 同一颗常量也决定了框级判据的输入：像素级一变，框级判据就跟着变——
    # 这证明「框里有没有红笔」问的就是这一颗常量，不是另一条各判各的路径。
    assert "colored_ink" not in [
        f["kind"] for f in ink.cropcheck(img, red_only, problem_type="choice")["flags"]
    ]
    monkeypatch.undo()
    assert "colored_ink" in [
        f["kind"] for f in ink.cropcheck(img, red_only, problem_type="choice")["flags"]
    ]


def test_the_three_paths_agree_on_the_same_synthetic_image(page):
    """验收 2 的正确验证方式：**三条路径对同一张图给出同一个红笔像素数**。

    路径 1 = `ink_masks`（体检侧掩膜）；路径 2 = `colored_pixels`（筛选按框统计）；
    路径 3 = `ink_statistics`（统计）。断言的是**输出一致**，不是常量相等——
    后者把两颗常量改成一样也照样绿。
    """
    img = ink.read_png(page)
    box = (0, 0, 40, 20)
    x0, y0, x1, y1 = box

    from_mask = sum(
        on
        for on, (x, y) in zip(ink.ink_masks(img)[0], _coords(img), strict=True)
        if x0 <= x < x1 and y0 <= y < y1
    )

    assert from_mask == ink.colored_pixels(img, box) == ink.ink_statistics(img, box)["colored_px"]


def _coords(image: ink.InkImage):
    """每个像素的 `(x, y)`，与 `InkImage.pixels` 一一对应。"""
    return [(i % image.width, i // image.width) for i in range(image.width * image.height)]


def test_page_block_reports_missing_bbox_is_not_silently_zero():
    """块的边界缺失时**明说**，绝不给一个 `colored_px: 0`——0 与「不知道」是两件事。"""
    img = color_image(10, 10, WHITE)
    page = {"blocks": [{"id": "b1", "bbox_px": [0, 0, 5, 5]}, {"id": "b2", "bbox_px": None}]}

    reports = ink.page_block_reports(img, page)

    assert reports[0]["stats"]["colored_px"] == 0
    assert reports[0]["reason"] is None
    assert reports[1]["stats"] is None
    assert reports[1]["reason"] == "block_bbox_missing"
    assert reports[1]["message"]


# ------------------------------- 切片 4b：整页墨迹区域（覆盖率对账的输入，R1）
#
# 覆盖率判据（`segmentation.check_coverage`）吃的是「一片墨迹」的列表，而工厂里
# 没有第二处能造它——墨迹区域的唯一实现就是这里。红笔与深色都算墨迹：订正也是
# 页面上该被框住的内容（只数深色会把红笔订正漏出覆盖率之外）。

def test_page_ink_regions_are_connected_patches_with_their_pixel_counts():
    """白底 + 两片离得远的墨迹 → 两个区域，各带自己的 `bbox_norm`（整页归一化）与 `px`。"""
    img = white_with_blocks(100, 60, [(10, 10, 20, 20, RED), (60, 40, 80, 50, BLACK)])

    regions = ink.page_ink_regions(img)

    assert regions == [
        {"bbox_norm": [0.1, 10 / 60, 0.1, 10 / 60], "px": 100},
        {"bbox_norm": [0.6, 40 / 60, 0.2, 10 / 60], "px": 200},
    ]


def test_page_ink_regions_join_touching_pixels_and_separate_gaps():
    """一片墨迹是**连着的**墨迹像素（八连通）：贴着的算一片，隔开的算两片。"""
    img = white_with_blocks(10, 10, [(0, 0, 1, 1, BLACK), (2, 2, 3, 3, BLACK)])

    regions = ink.page_ink_regions(img)

    assert [r["px"] for r in regions] == [1, 1], "隔一格就是两片，不许并成一个"

    touching = white_with_blocks(10, 10, [(0, 0, 1, 1, BLACK), (1, 1, 2, 2, BLACK)])
    assert [r["px"] for r in ink.page_ink_regions(touching)] == [2], "斜对角贴着算一片"


def test_a_red_patch_is_ink_too_when_the_whole_page_is_reconciled():
    """红笔订正也是墨迹：只数深色会把一整片红笔订正漏出覆盖率之外。"""
    img = white_with_blocks(10, 10, [(0, 0, 4, 4, RED)])

    regions = ink.page_ink_regions(img)

    assert [r["px"] for r in regions] == [16]


def test_a_blank_page_has_no_ink_regions():
    """白纸就是没有墨迹——空列表，而不是一片盖住整页的假区域。"""
    assert ink.page_ink_regions(color_image(5, 5, WHITE)) == []


# ------------------------------------------- 切片 5：统计不做语义判断（验收 3）

SEMANTIC_WORDS = (
    "verdict", "kind", "label", "class", "is_correction", "is_correction_mark",
    "correction", "check", "judgement", "judgment", "guess", "wrong", "correct",
    "has_red", "has_red_ink", "semantic", "means",
)


def test_the_statistics_carry_no_semantic_verdict(page):
    """验收 3：统计只回答「有没有红笔、多少」。

    勾还是订正留给 #12（B4）的模型。**任何**看起来像判定的字段都不许出现——
    字段名里带 `kind`/`verdict`/`correction` 这类词就是在长第二份语义实现。
    """
    stats = ink.ink_statistics(ink.read_png(page), (0, 0, 40, 20))

    assert set(stats) == set(ink.STAT_KEYS)
    for key in stats:
        assert not any(word in key.lower() for word in SEMANTIC_WORDS), key
    for value in stats.values():
        assert isinstance(value, (int, float)), value


# ------------------------------------ 切片 6：真实照片的 PNG 形状，以及读不懂时喊出来

def pil_png(tmp_path, mode: str, color=(10, 20, 30)) -> pathlib.Path:
    """用 PIL 写一张真 PNG——真实页照片就是别的工具写出来的。

    PIL 在这里是**对手方**（互操作性），不是本模块的实现依赖；PIL 不在时跳过。
    """
    pil = pytest.importorskip("PIL.Image")
    path = tmp_path / f"pil-{mode}.png"
    pil.new(mode, (4, 3), color[: len(mode)]).save(path)
    return path


def craft_png(tmp_path, name: str, *, width: int, height: int, depth: int,
              color_type: int, body: bytes, palette: bytes = b"") -> pathlib.Path:
    """手工拼一张指定形状的 PNG：PIL 会按自己的意思挑位深，测不了边界形状。"""
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    blob = (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, depth, color_type, 0, 0, 0))
            + (chunk(b"PLTE", palette) if palette else b"")
            + chunk(b"IDAT", zlib.compress(body))
            + chunk(b"IEND", b""))
    path = tmp_path / name
    path.write_bytes(blob)
    return path


@pytest.mark.parametrize("mode", ["RGB", "RGBA", "L"])
def test_photos_written_by_other_tools_are_read(tmp_path, mode):
    """别的工具写出来的 PNG 必须读得出来——真实页照片不是本模块写的。"""
    assert ink.read_png(pil_png(tmp_path, mode)).size == (4, 3)


def test_separation_also_works_on_a_pil_drawn_image(tmp_path):
    """验收 1 的原文：「用 PIL 画（白底 + 红笔块 + 黑笔块）」。

    图**由 PIL 写**、再由本模块读（本模块自己不认识 PIL，只认识 PNG 字节）。
    不联网、不碰真实数据。
    """
    pil = pytest.importorskip("PIL.Image")
    image = pil.new("RGB", (60, 20), WHITE)
    for x in range(0, 20):
        for y in range(20):
            image.putpixel((x, y), RED)
    for x in range(40, 60):
        for y in range(20):
            image.putpixel((x, y), BLACK)
    path = tmp_path / "pil-blocks.png"
    image.save(path)

    img = ink.read_png(path)

    assert ink.colored_pixels(img, (0, 0, 20, 20)) == 400    # 红笔块
    assert ink.colored_pixels(img, (40, 0, 60, 20)) == 0     # 黑笔块：一个也不算红笔
    assert ink.dark_pixels(img, (40, 0, 60, 20)) == 400      # 但它是深色墨迹


def test_an_eight_bit_indexed_png_is_read(tmp_path):
    """8 位调色板 PNG：`PLTE` 索引 → RGB，数的是索引不是样本。"""
    palette = bytes([255, 0, 0, 0, 0, 0, 1, 2, 3])  # 三色：红 / 黑 / 深蓝
    # 每行前面是滤波器字节；索引从左到右是 红 黑 / 蓝 红。
    body = bytes([0, 0, 1, 0, 2, 0])
    path = craft_png(tmp_path, "indexed.png", width=2, height=2, depth=8, color_type=3,
                     body=body, palette=palette)

    img = ink.read_png(path)

    assert img.pixels == [(255, 0, 0), (0, 0, 0), (1, 2, 3), (255, 0, 0)]
    assert ink.colored_pixels(img, (0, 0, 1, 1)) == 1


@pytest.mark.parametrize("depth", [16, 4])
def test_shapes_this_module_cannot_read_are_refused_loudly(tmp_path, depth):
    """16 位要丢弃低位、4 位要按位拆包——本模块不做，那就**抛错**。

    静默把 4 位索引当 8 位样本读，会给出一张看起来正常、其实全错的图。
    """
    path = craft_png(tmp_path, f"depth{depth}.png", width=2, height=1, depth=depth,
                     color_type=0, body=bytes([0, 0x12, 0x34]))

    with pytest.raises(ink.UnsupportedImage):
        ink.read_png(path)


def test_a_truncated_png_is_refused_loudly(tmp_path):
    path = tmp_path / "truncated.png"
    path.write_bytes(pil_png(tmp_path, "RGB").read_bytes()[:20])

    with pytest.raises(ink.UnsupportedImage):
        ink.read_png(path)


def test_a_png_that_is_only_a_signature_is_refused_loudly(tmp_path):
    path = tmp_path / "header-only.png"
    path.write_bytes(b"\x89PNG\r\n\x1a\n")

    with pytest.raises(ink.UnsupportedImage):
        ink.read_png(path)


def test_write_png_refuses_a_pixel_list_that_does_not_fill_the_image(tmp_path):
    """写图也要喊：像素数与尺寸对不上时静默补/截会造出一张骗人的合成图。"""
    with pytest.raises(ValueError):
        ink.write_png(tmp_path / "bad.png", ink.InkImage(3, 3, [RED, WHITE]))
