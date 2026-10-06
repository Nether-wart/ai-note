"""整页照片的确定性预处理（`server/preprocess.py`）：
灰度权重、高斯可分核与边缘、中值去噪、Otsu 阈值、二值化不丢像素。

这一份的判据都是**手算得出来**的：算错了会红，而不是"看起来差不多"。
测试图像都是很小的人工图（几×几），所以纯 Python 的慢在这里不是问题。
"""

from __future__ import annotations

from server.ink import InkImage
from server.preprocess import (
    binarize, gaussian_blur_5x5, median_blur_3x3, otsu_threshold, preprocess, to_gray,
)


def flat(width: int, height: int, color=(255, 255, 255)) -> InkImage:
    return InkImage(width, height, [color] * (width * height))


def with_pixels(width: int, height: int, base=(255, 255, 255), marks=None) -> InkImage:
    pixels = [base] * (width * height)
    for (x, y), color in (marks or {}).items():
        pixels[y * width + x] = color
    return InkImage(width, height, pixels)


# ------------------------------------------------------------------ 灰度


def test_灰度权重与_OpenCV_一致():
    assert to_gray(InkImage(1, 1, [(255, 0, 0)])) == [76]        # 0.299 × 255
    assert to_gray(InkImage(1, 1, [(0, 255, 0)])) == [150]       # 0.587 × 255
    assert to_gray(InkImage(1, 1, [(0, 0, 255)])) == [29]        # 0.114 × 255
    assert to_gray(InkImage(1, 1, [(255, 255, 255)])) == [255]
    assert to_gray(InkImage(1, 1, [(0, 0, 0)])) == [0]


# ------------------------------------------------------------------ 高斯


def test_高斯是_5x5_可分核_中心的值能手算出来():
    # 5×5 全是 0、只有正中间 255。两趟卷积后中心 = 6×6×255/256 = 35.86 → 36
    image = with_pixels(5, 5, base=(0, 0, 0), marks={(2, 2): (255, 255, 255)})
    plane = gaussian_blur_5x5(to_gray(image), 5, 5)

    assert plane[2 * 5 + 2] == 36, "中心该是那 36/256 的份额"
    # 能量被摊开了：中心变暗、邻居被点亮（亮度只从这一点来）
    assert plane[2 * 5 + 2] < 255
    assert plane[2 * 5 + 1] > 0 and plane[1 * 5 + 2] > 0
    assert plane[2 * 5 + 2] == max(plane), "最大值仍在中心"


def test_高斯在边缘按复制处理_不出现黑洞():
    # 一条贴着左边的竖线；按复制处理时边缘不该被当成 0 拉暗
    image = with_pixels(5, 5, base=(0, 0, 0), marks={(0, y): (255, 255, 255) for y in range(5)})
    plane = gaussian_blur_5x5(to_gray(image), 5, 5)
    assert plane[0] > 0 and plane[4 * 5] > 0, "边界像素也要有值"


# ------------------------------------------------------------------ 中值


def test_中值把孤立噪点抹掉_而大面积块留下():
    marks = {(2, 2): (0, 0, 0), (0, 0): (0, 0, 0)}          # 两个孤立黑点
    image = with_pixels(5, 5, marks=marks)
    plane = median_blur_3x3(to_gray(image), 5, 5)
    assert 0 not in plane, "孤立黑点该被中值抹掉（高斯只会把它摊成一团灰）"

    # 3×3 的黑块：中心周边 8 个邻居里 8 个也是黑的 → 中心留下
    block = {(x, y): (0, 0, 0) for x in (1, 2, 3) for y in (1, 2, 3)}
    plane2 = median_blur_3x3(to_gray(with_pixels(5, 5, marks=block)), 5, 5)
    assert plane2[2 * 5 + 2] == 0, "成块的深色是内容，不是噪声"


# ------------------------------------------------------------------ Otsu 与二值化


def test_otsu_分得开两级亮度_而且阈值是算出来的():
    plane = [30] * 4 + [220] * 4
    threshold = otsu_threshold(plane)
    assert 30 <= threshold < 220, threshold

    out = binarize(plane, threshold)
    assert out.count(0) == 4 and out.count(255) == 4, "两级各归各的，一个像素都不丢"


def test_otsu_全同色的图不炸():
    assert otsu_threshold([128] * 9) in range(256)
    assert otsu_threshold([]) == 0


def test_二值化只有两个值_且边界是开区间():
    out = binarize([0, 10, 11, 12, 255], threshold=11)
    assert out == [0, 0, 0, 255, 255], "`src > threshold` 才白，等于阈值算黑"
    assert set(out) <= {0, 255}


# ------------------------------------------------------------------ 整条管道


def test_管道尺寸不变_只含黑白_笔画还在_但会被加粗一点():
    """注意**不是**"像素数不变"：模糊 ＋ 阈值会把笔画略微**加粗**（实测 16 → 32），
    那正是这套预处理想要的效果（笔画更实，OCR 更稳）。
    "一个像素都不丢"那条判据属于 `binarize` 本身，在下面单测里钉着。"""
    marks = {(x, y): (20, 20, 20) for x in (2, 3) for y in range(8)}
    out = preprocess(with_pixels(8, 8, marks=marks))

    assert out.size == (8, 8), "不裁切时尺寸不变"
    values = {pixel[0] for pixel in out.pixels}
    assert values <= {0, 255}, values
    assert out.pixels[4 * 8 + 2][0] == 0, "笔画核心该是黑的"
    assert out.pixels[4 * 8 + 0][0] == 255 and out.pixels[4 * 8 + 7][0] == 255, "远处底仍是白"
    assert sum(1 for pixel in out.pixels if pixel[0] == 0) >= 16


def test_中央裁切取的是中间一半():
    marks = {(x, y): (0, 0, 0) for x in (2, 3) for y in (2, 3)}
    out = preprocess(with_pixels(8, 8, marks=marks), roi_center=True)
    assert out.size == (4, 4)


def test_降采样取每块最暗的像素():
    """直接测 `downsample`，不经过管道：2×2 的图太小，中值会把孤立深色抹掉
    （那是中值的本职），拿它验降采样规则会被别的一步搅浑。"""
    from server.preprocess import downsample

    out = downsample(with_pixels(4, 4, marks={(1, 1): (0, 0, 0)}), 2)
    assert out.size == (2, 2)
    assert out.pixels[0] == (0, 0, 0), "那一块里有深色像素，就该取它（不许平均）"
    assert out.pixels[1] == (255, 255, 255)
