"""整页照片的**确定性预处理**（灰度 → 高斯 → 中值 → Otsu 二值化），只用标准库。

来历：`origin/master` 上那份 `图像预处理.py` 用 OpenCV 做了同一套事（另外一半是调云端 OCR，
那半没有合进来——它是一个**新角色**，该走模型客户端那条路，不是 vendored 脚本）。
这份是把它**用标准库重写**：`server/` 零第三方依赖是硬约束（ADR 0006／0007），
而灰度、高斯 5×5、中值 3×3、Otsu 都是几十行的确定性算法——为它们引 OpenCV + numpy 不划算。
像素进出的类型复用 `server/ink.py` 的 `InkImage`（PNG 编解码、颜色掩膜都已经在那里）。

⚠ **它只能喂给"转录"，绝不能喂给"切分"**。二值化把颜色抹成黑白，而本仓库判"这题有没有红笔"
靠的正是**颜色**（`ink.py`：饱和度阈值 60 的彩色掩膜，ADR 0005／#11）。一份二值图喂给切分，
红笔信号就没了；喂给转录（模型读题面文字）才是它该去的地方。

⚠ **纯 Python 很慢**。代价是真实的：一次 3000×4000 的整页照片要过三遍像素
（高斯可分两次、中值每像素排 9 个数），在普通笔记本上大约是**几十秒**的量级。
所以它**默认不接进任何主流程**，是一个可以显式调用的工具；`--scale` 是留给使用者的取舍
（降采样更快，代价是模型看到的细节更少）。

打印/调试口径：每一档都只做**数学上确定的一件事**，没有随机、没有阈值猜测（Otsu 是算出来的）。
"""

from __future__ import annotations

import sys
from pathlib import Path

from .ink import InkImage, read_png, write_png

# 5×5 高斯核（σ=0 时 OpenCV 用的就是这颗，可分成两趟一维卷积，权重和 = 16×16 = 256）
GAUSSIAN_1D = (1, 4, 6, 4, 1)
GAUSSIAN_SUM = sum(GAUSSIAN_1D)          # 16
GAUSSIAN_TOTAL = GAUSSIAN_SUM * GAUSSIAN_SUM   # 256


def to_gray(image: InkImage) -> list[int]:
    """RGB → 灰度（OpenCV 的 `COLOR_BGR2GRAY` 权重：0.299R + 0.587G + 0.114B）。"""
    out = []
    for r, g, b in image.pixels:
        out.append((299 * r + 587 * g + 114 * b + 500) // 1000)
    return out


def _clamp(value: int, low: int, high: int) -> int:
    return low if value < low else (high if value > high else value)


def gaussian_blur_5x5(plane: list[int], width: int, height: int) -> list[int]:
    """两趟一维卷积（可分核）。边缘按**复制**处理（OpenCV 的 `BORDER_REPLICATE`）。

    两趟都**只累加、不中途取整**，最后除一次 256：中途取整会把 4 次舍入误差叠起来，
    而这一步的输出马上要进 Otsu——误差会改阈值。
    """
    half = len(GAUSSIAN_1D) // 2
    horizontal = [0] * (width * height)
    for y in range(height):
        row = y * width
        for x in range(width):
            total = 0
            for offset, weight in enumerate(GAUSSIAN_1D):
                sx = _clamp(x + offset - half, 0, width - 1)
                total += weight * plane[row + sx]
            horizontal[row + x] = total

    out = [0] * (width * height)
    for y in range(height):
        for x in range(width):
            total = 0
            for offset, weight in enumerate(GAUSSIAN_1D):
                sy = _clamp(y + offset - half, 0, height - 1)
                total += weight * horizontal[sy * width + x]
            out[y * width + x] = (total + GAUSSIAN_TOTAL // 2) // GAUSSIAN_TOTAL
    return out


def median_blur_3x3(plane: list[int], width: int, height: int) -> list[int]:
    """3×3 中值。**它才是去椒盐噪声的那一步**（高斯会把孤立黑点摊成一团灰）。
    边缘同样按复制处理：越界的那几个取最近的边界像素。"""
    out = [0] * (width * height)
    for y in range(height):
        for x in range(width):
            window = []
            for dy in (-1, 0, 1):
                sy = _clamp(y + dy, 0, height - 1)
                for dx in (-1, 0, 1):
                    sx = _clamp(x + dx, 0, width - 1)
                    window.append(plane[sy * width + sx])
            window.sort()
            out[y * width + x] = window[4]
    return out


def otsu_threshold(plane: list[int]) -> int:
    """Otsu：在 0..255 上选一个使**类间方差最大**的阈值。图象全同色时返回 0。

    这一步是"图像预处理"里唯一有判断的一步，而它**不是猜**——阈值是算出来的，
    同一张图跑两次得同一个数（所以它可以被测试钉住）。
    """
    histogram = [0] * 256
    for value in plane:
        histogram[_clamp(value, 0, 255)] += 1
    total = len(plane)
    if total == 0:
        return 0

    sum_all = sum(index * count for index, count in enumerate(histogram))
    weight_background = 0
    sum_background = 0
    best_threshold = 0
    best_variance = -1.0
    for threshold in range(256):
        weight_background += histogram[threshold]
        if weight_background == 0:
            continue
        weight_foreground = total - weight_background
        if weight_foreground == 0:
            break
        sum_background += threshold * histogram[threshold]
        mean_background = sum_background / weight_background
        mean_foreground = (sum_all - sum_background) / weight_foreground
        variance = weight_background * weight_foreground * (mean_background - mean_foreground) ** 2
        if variance > best_variance:
            best_variance = variance
            best_threshold = threshold
    return best_threshold


def binarize(plane: list[int], threshold: int) -> list[int]:
    """`src > threshold ? 255 : 0`（OpenCV `THRESH_BINARY` 的判据，含边界）。"""
    return [255 if value > threshold else 0 for value in plane]


def _center_roi(plane: list[int], width: int, height: int) -> tuple[list[int], int, int]:
    """中央一半（与原始脚本的 `img[h//4:h*3//4, w//4:w*3//4]` 一致）。"""
    y0, y1 = height // 4, height * 3 // 4
    x0, x1 = width // 4, width * 3 // 4
    out = []
    for y in range(y0, y1):
        out.extend(plane[y * width + x0:y * width + x1])
    return out, x1 - x0, y1 - y0


def downsample(image: InkImage, scale: int) -> InkImage:
    """整数倍降采样：每个 `scale×scale` 块取**最暗**的那个像素。

    取最暗而不是平均：这一步的目的是"别把笔画抹掉"，平均会把细笔画稀释成灰
    （二值化时正好把它抹没）。
    """
    if scale < 2:
        return image
    width, height = image.width // scale, image.height // scale
    pixels = []
    for y in range(height):
        for x in range(width):
            chosen = None
            darkest = None
            for dy in range(scale):
                for dx in range(scale):
                    pixel = image.pixels[(y * scale + dy) * image.width + (x * scale + dx)]
                    value = 299 * pixel[0] + 587 * pixel[1] + 114 * pixel[2]
                    if darkest is None or value < darkest:
                        darkest = value
                        chosen = pixel
            pixels.append(chosen)
    return InkImage(width, height, pixels)


def preprocess(image: InkImage, *, roi_center: bool = False, scale: int = 1) -> InkImage:
    """整条管道：灰度 → 高斯 → 中值 → Otsu → 二值化（可选中央裁切、可选降采样）。

    `scale` > 1 时先按整数倍降采样再处理——纯 Python 的代价就摆在那里，
    这个旋钮是留给使用者的取舍（快，但模型看到的细节少）。
    """
    if scale < 1:
        raise ValueError("scale 至少是 1")
    # 降采样：取 scale×scale 块里最暗的那个像素。**取最暗而不是平均**——
    # 这一步的目的是"别把笔画抹掉"，平均会把细笔画稀释成灰。
    if scale > 1 and (image.width >= scale and image.height >= scale):
        image = downsample(image, scale)

    width, height = image.size
    plane = to_gray(image)
    plane = gaussian_blur_5x5(plane, width, height)
    plane = median_blur_3x3(plane, width, height)
    plane = binarize(plane, otsu_threshold(plane))

    if roi_center:
        plane, width, height = _center_roi(plane, width, height)
    return InkImage(width, height, [(value, value, value) for value in plane])


def main(argv: list[str] | None = None) -> int:
    """`python3 -m server.preprocess <in.png> <out.png> [--roi-center] [--scale N]`"""
    args = list(sys.argv[1:] if argv is None else argv)
    roi_center = "--roi-center" in args
    args = [arg for arg in args if arg != "--roi-center"]
    scale = 1
    if "--scale" in args:
        index = args.index("--scale")
        try:
            scale = int(args[index + 1])
        except (IndexError, ValueError):
            print("--scale 后面要给一个整数", file=sys.stderr)
            return 2
        del args[index:index + 2]
    if len(args) != 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        print("用法：python3 -m server.preprocess <in.png> <out.png> "
              "[--roi-center] [--scale N]", file=sys.stderr)
        return 2

    source, target = Path(args[0]), Path(args[1])
    image = read_png(source)
    result = preprocess(image, roi_center=roi_center, scale=scale)
    write_png(target, result)
    print(f"{source} → {target}（{image.width}×{image.height} → "
          f"{result.width}×{result.height}，scale={scale}）")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
