"""两套坐标基准的**显式换算**（契约 §10.2 / §5、编排裁决 D5、工单 #14）。

一页照片上同时住着两套坐标，它们**不是同一个框的两种写法**：

| 基准 | 谁用它 | 形状 |
|---|---|---|
| **整页** | `source.bbox_norm`／`bbox_px`、页文件里块的 `bbox_norm`／`bbox_px` | `bbox_norm` 是 **xywh**，`bbox_px` 是 **xyxy** |
| **裁剪图** | `Problem.clean.boxes_norm`／`clean.manual.{add,drop}` | **xywh**，相对 `<pid>-problem.png` 归一化 |

**为什么必须收在一个模块里。** 「把块框画在整页照片上」与「把掩膜框画在题面裁剪图上」
用的是两套基准；混用**不会报错，只会静默错位**——那正是 ADR 0007 第 6 条最怕的
一类失败。所以换算只在这里实现一次，界面与测试都调它，各处不许现算。

**反例可复算**（真实数据 `p-20261004-41c86b`，只读核对过）：

    source.bbox_norm = [0.02, 0.12, 0.76, 0.28]     # 整页 xywh
    source.bbox_px   = [3, 20, 541, 79]             # 整页 xyxy（另加了 1.5% pad）
    clean.boxes_norm = [[0.58, 0.05, 0.08, 0.22]]   # 裁剪图 xywh

把那个掩膜框当整页坐标画到页上，x 会落在 0.58；换算之后是
`0.02 + 0.58*0.76 = 0.4608`——**差 0.12 个页宽**，肉眼看得出来，但代码一声不响。

**形状的坑另记一条**：`bbox_px` 不是 `bbox_norm` 的像素化（D5：前者加了 1.5% pad
又裁到页边界）。所以本模块只做**形状转换**，不发明 pad；回填 `bbox_px` 时照抄盘上原值。

**读不出来的框返回 `None`，绝不返回零框**：`(0,0,0,0)` 会静默画在左上角，而
「不知道」与「在角上」是两件事（同 `server/ink.py` 对 0 与 null 的纪律）。
形状判据的唯一实现在 `server/pages.py: usable_box`——本模块消费它，不重写。
"""

from __future__ import annotations

from .pages import usable_box

# 越出父框的框裁到父框后，面积可能被裁成 0（框整个在父框外面）。
# 那不是「一个空框」，是「这个框在父框里没有位置」——照旧给 `None`，由调用方报出来。
BOX_KEYS = ("x", "y", "w", "h")

# 掩膜被裁过的警告码（契约 §2 的形状）。级别是 `hint`：裁了不是矛盾，
# 是「这一步替你做了个决定」——把它报成 warning 会训练人忽略体检
# （`proto/server.py:1046-1047` 的口径）。
MASK_BOX_CLAMPED = "mask_box_clamped"


def _xywh(box):
    """一个读得出来的 xywh 框 → `(x, y, w, h)`；读不出来 → `None`。

    「读得出来」的判据只有一处（`pages.usable_box`）：四个数、`w > 0`、`h > 0`。
    负宽高与零宽高都不是框（`iou`／`overlap_coefficient` 同样靠它把坏框挑出来）。
    """
    if not usable_box(box):
        return None
    return tuple(float(v) for v in box)


def xywh_to_xyxy(box):
    """`[x, y, w, h]` → `[x0, y0, x1, y1]`。读不出来 → `None`。"""
    read = _xywh(box)
    if read is None:
        return None
    x, y, w, h = read
    return [x, y, x + w, y + h]


def xyxy_to_xywh(box):
    """`[x0, y0, x1, y1]` → `[x, y, w, h]`。读不出来（含 `x1 <= x0`）→ `None`。

    判据与 `xywh_to_xyxy` 同源：先把 xyxy 读成 xywh 再验。所以 `[3, 20, 541, 79]`
    读出来是 `[3, 20, 538, 59]`——**不是** `[3, 20, 541, 79]`（形状不同，别当两种写法）。
    """
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in box)
    except (TypeError, ValueError):
        return None
    if x1 <= x0 or y1 <= y0:
        return None
    return [x0, y0, x1 - x0, y1 - y0]


def contains_box(parent, child) -> bool:
    """`child` 整个落在 `parent` 里吗（两者都是 xywh）。任一个读不出来 → `False`。

    界面上「掩膜框画在页上之后还在块里吗」这一问就是它。返回 `False` 不区分
    「在外面」与「框读不出来」——调用方要看后者就自己先问 `usable_box`
    （同 `pages.iou` 对坏框返回 0.0 的口径：不猜）。
    """
    outer, inner = _xywh(parent), _xywh(child)
    if outer is None or inner is None:
        return False
    px, py, pw, ph = outer
    ix, iy, iw, ih = inner
    # 允许一点浮点误差：换算出来的框常常正好贴边（如 0.02+0.76 = 0.78）。
    eps = 1e-9
    return (ix >= px - eps and iy >= py - eps
            and ix + iw <= px + pw + eps and iy + ih <= py + ph + eps)


# ---------------------------------------------------------------- 整页：归一化 ↔ 像素


def norm_to_px(box, *, width, height):
    """整页归一化 xywh → 整页像素 xywh（按照片的**实际**尺寸）。

    `width`／`height` ≤ 0 → `None`（没有尺寸就投不出来，不猜一个默认值）。
    界面画框时要的是「投到显示出来的尺寸上」，那是同一件事——给显示尺寸即可。
    """
    read = _xywh(box)
    if read is None:
        return None
    try:
        width, height = float(width), float(height)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    x, y, w, h = read
    return [x * width, y * height, w * width, h * height]


def px_to_norm(box, *, width, height):
    """整页像素 xywh → 整页归一化 xywh。尺寸 ≤ 0 或框读不出来 → `None`。"""
    read = _xywh(box)
    if read is None:
        return None
    try:
        width, height = float(width), float(height)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    x, y, w, h = read
    return [x / width, y / height, w / width, h / height]


# ---------------------------------------------------------------- 整页 ↔ 裁剪图


def _nested(box, parent, clamp: bool):
    """把一个**裁剪图** xywh 框投进**整页** `parent` 框（两者都是 xywh 归一化）。

    `x_page = parent.x + box.x * parent.w`，y/w/h 同理——先按块的尺寸缩放，再平移到
    块的原点。`clamp=True` 时把越出父框的部分裁到父框（裁完没面积 → `None`）。
    """
    child, outer = _xywh(box), _xywh(parent)
    if child is None or outer is None:
        return None
    px, py, pw, ph = outer
    x, y, w, h = child
    x, y, w, h = px + x * pw, py + y * ph, w * pw, h * ph
    if clamp:
        x0, y0 = max(x, px), max(y, py)
        x1, y1 = min(x + w, px + pw), min(y + h, py + ph)
        if x1 <= x0 or y1 <= y0:
            return None
        return [x0, y0, x1 - x0, y1 - y0]
    return [x, y, w, h]


def crop_box_to_page(box, block_box, *, clamp: bool = True):
    """**掩膜框（裁剪图）→ 整页框**：界面上「把掩膜画到整页照片上」的那一步。

    `box` 是 `clean.boxes_norm`／`manual.add` 里的一项（相对题面裁剪图归一化），
    `block_box` 是块在**整页**上的 `bbox_norm`。任一个读不出来 → `None`。

    `clamp=True`（默认）把越出块边界的掩膜裁到块内：手工掩膜是人在裁剪图上拖的，
    拖出界是常事，而画到页上越界的框会盖住邻居。裁没了的框给 `None`。
    """
    return _nested(box, block_box, clamp)


def page_box_to_crop(box, block_box):
    """**整页框 → 掩膜框（裁剪图）**：上面那条的逆运算。不裁（逆运算要能一来一回）。"""
    child, outer = _xywh(box), _xywh(block_box)
    if child is None or outer is None:
        return None
    px, py, pw, ph = outer
    x, y, w, h = child
    return [(x - px) / pw, (y - py) / ph, w / pw, h / ph]


def crop_boxes_to_page(boxes, block_box, *, clamp: bool = True, report: bool = False):
    """一串裁剪图框 → 一串整页框（`clean.boxes_norm`／`manual` 的形状）。

    - 默认返回**框的列表**，与输入同序；读不出来的那一项给 `None`
      （位置对上，调用方才知道是**哪一个**掩膜没画出来）。
    - `report=True` 时返回 `{boxes, clamped, warnings}`：被裁过的框数出来并**喊一声**
      （ADR 0007 第 6 条：不许静默）。裁到没有面积的那一项在 `boxes` 里是 `None`，
      同时计入 `clamped`——「它跑到块外面去了」是事实，不许当成「这一项没问题」。
    """
    result: list = []
    clamped: list[int] = []
    for index, box in enumerate(boxes or []):
        on_page = crop_box_to_page(box, block_box, clamp=clamp)
        if on_page is None and _xywh(box) is not None:
            # 框本身读得出来，投到页上却没了 → 它整个落在块外面（或被裁光）。
            clamped.append(index)
        elif on_page is not None and _xywh(box) is not None:
            raw = _nested(box, block_box, clamp=False)
            if raw is not None and any(abs(a - b) > 1e-9 for a, b in zip(raw, on_page)):
                clamped.append(index)
        result.append(on_page)
    if not report:
        return result
    warnings = []
    if clamped:
        warnings.append({
            "code": MASK_BOX_CLAMPED,
            "level": "hint",
            "message": f"有 {len(clamped)} 个掩膜框越出了它所属的块边界（第 {clamped} 项）"
                       f"→ 已裁到块内；裁到没有面积的框没有画出来",
        })
    return {"boxes": result, "clamped": len(clamped), "clamped_indexes": clamped,
            "warnings": warnings}
