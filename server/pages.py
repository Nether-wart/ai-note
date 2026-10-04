"""页：一等实体（spec #2、CONTEXT「页」、契约 §10.2、编排裁决 D5）。

**为什么页必须是一个文件。** 切分结果与收入决策一旦只存在于界面的内存里，
「漏了一题」就永远查不出来——而静默丢题是这个项目最怕的一类失败。所以一页一个文件：
`data/pages/<hash12>.json`，与整页照片**同目录并列**。

**`<hash12>` 从哪来**：从 `source.page_image` 的**文件名主干**推（真实数据：
`data/pages/41c86bcfc007.png` → `41c86bcfc007`，12 位），**不从题卡 id 截**——
题卡 id 是 `p-20261004-41c86b`（6 位），长度都不一样（D5）。

**两套坐标基准，不许混用**（契约 §10.2 登记在案的坑）：

- 页文件的块用**整页**坐标：`bbox_norm` 是 `[x, y, w, h]`（xywh，与 `source.bbox_norm`
  同一语义，见 `proto/slice.py:281`），`bbox_px` 是 `[x0, y0, x1, y1]`（xyxy，
  是 `crop_problem(pad=0.015)` 加了 1.5% pad 又裁到页边界的结果，`proto/slice.py:544-555`）。
  两个字段**形状不同**，不是同一个框的两种写法。
- `Problem.clean.boxes_norm` / `manual` 是**裁剪图**坐标。任何「把块画到原图上」或
  「把掩膜框画到页上」的地方（尤其 #14）必须显式换算。

`bbox_norm` 是**存储基准**（不随图片重编码/缩放失效），`bbox_px` 只作**交叉验证**：
回填时读盘上的原值，不重算。
"""

from __future__ import annotations

import re
from pathlib import Path

# 页 id 会直接变成文件名，所以它必须是一个安全的文件名片段。
# 与 `catalog.ID_PATTERN` / `assets._NAME_RE` 同一套纵深防御（服务将来要经 Tailscale
# 暴露给手机，#13），只是这里更松：不要求 12 位十六进制——照片叫 `photo1.png` 也是合法的
# 一页，不能因为命名习惯把它判成「没有页」。
_PAGE_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def page_hash_from_image(page_image: str | None) -> str | None:
    """`source.page_image` → 页 id（照片文件名主干）。取不到就 `None`。

    取不到 = 没有整页照片 = 无法反推页文件，调用方据此报 `page_binding_missing`（提示级）。
    """
    if not page_image or not isinstance(page_image, str):
        return None
    path = Path(page_image)
    if ".." in path.parts:  # 穿越串不许借道变成页 id
        return None
    # 照片必须有后缀：这一条顺带把「传进来的是个目录」挡掉（`data/pages/` 的 stem 是 `pages`）。
    if not path.suffix:
        return None
    stem = path.stem
    # `.` / `..` / 隐藏文件（`.foo`）都不是照片名，是路径或历史残渣。
    if not stem or stem.startswith(".") or not _PAGE_ID_RE.fullmatch(stem):
        return None
    return stem
