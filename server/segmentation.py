"""切分与对账（#10 B2、spec #2「模型出候选块，确定性统计做对账」、契约 §10.2）。

**为什么切分需要模型。** 学生的手写解答会填满题与题之间的空隙，所以纯几何投影
（按行统计墨迹空隙）在真实卷子上会散架——裁剪体检里已经写明「黑笔手写与印刷体
在灰度上同色，机器分不开」（`proto/slice.py:860-862`、`docs/acceptance-log.md:75-83`：
`1-0000.png` 框下方 6158 像素墨迹全是手写解答）。所以候选块由**模型**给出
（`SEGMENT_SYSTEM`），本模块接着做**确定性的对账**。

**为什么对账必须是确定性的。** 它是把「静默丢题」变成响声的唯一办法（spec #2）。
这个项目最怕的一类失败是「安静地少了一张卡」：原型的两个样本是
`proto/server.py:320-321`（`if pid in by_id` 直接把取不到的题不渲染）与
`:933-936`（`/mark` 里对取不到的题 `continue`）。同一类写法不许带到这里——
本模块的每条判据都**显式返回结构化结论**：`checked`（我查了没有）、
`ok`（有没有响声）、以及一个可枚举的明细列表。

**三条判据（spec #2 Implementation Decisions）**：

1. 题号连续性——模型报出每块的题号，缺号（有 17、19 没有 18）直接报警。
   「这是最便宜也最强的一条」。
2. 块之间不重叠——同页两块的边界不相交。
3. 覆盖率——该页的墨迹（排除整页草稿式手写之后）应被各块基本覆盖；
   大片未被任何块覆盖的墨迹要报出来。

**已知弱点（spec #2 点名要求写下来，免得被当成保证）**：

- **题号连续性依赖模型报出的题号**：如果模型把题号也报错了（报成别的号、
  或者干脆不报），这条最强的检查就失灵。所以它**不是保证**——真图回归那一层
  与人工确认都不能省。本模块对此的处理是**把失灵本身也报出来**：
  读不出题号的块进 `blocks_without_number`，`complete=False`，
  由 `reconcile` 发一条 `hint`（不是缺口警报，因为那不是「缺号」）。
- **覆盖率判据依赖「排除整页草稿式手写」这一步，而那一步本身就是启发式**。
  这里用的判据是「单个墨迹区域几乎铺满整页」，它能不能抓住真正的草稿，
  取决于上游（图像统计）把墨迹聚成区域的粒度。被排除的区域**显式列在
  `excluded` 里**，不是丢掉——排除本身也要看得见。
- **检查能抓住的，永远只是它判据覆盖的那一部分。** 对账绿了只说明
  「在它看得见的范围内没发现问题」，不说明这一页切对了。

**匹配不在这里**：「位置重合 → 保留绑定」的唯一实现在 `server/pages.py: rebind`
（`MATCH_IOU` 的口径由本工单裁决，理由写在那个常量旁边）。本模块只在它给出的
事实上做「新增／替换／保留」三态映射（`classify_resegment`）。

**本模块不碰图、不联网、不写题卡、不写盘。** 墨迹统计的输入（`ink`）由图像统计层
（#11 的红笔统计那一族）给出；模型调用是可注入的纯文本接缝，测试用构造的块列表
与假回答喂它（spec #2 Testing Decisions 第 1 层）。
"""

from __future__ import annotations

import json

from . import judge_client, pages

# 对账结论的码表（形状与契约 §8 的 `Warning` 一致：`{code, level, message}`）。
# 级别只有服务能定：真矛盾 → warning；「我这一条没查全」→ hint（编排裁决 D1/D3、
# 契约 §2）。把「按设计如此 / 查不到」报成 warning 会训练人忽略体检
# （`proto/server.py:1046-1047`），那比漏报更糟。
QUESTION_NUMBER_GAP = "question_number_gap"            # warning：缺号
QUESTION_NUMBER_DUPLICATE = "question_number_duplicate"  # warning：两块报同一个号
QUESTION_NUMBER_MISSING = "question_number_missing"    # hint：有块没报题号，这一条查不全
BLOCK_OVERLAP = "block_overlap"                        # warning：两块边界相交
BLOCK_WITHOUT_BOX = "block_without_box"                # warning：边界读不出来（形状同 #9）
BLOCK_NOT_AN_OBJECT = "block_not_an_object"            # warning：列表里混进了非对象
PAGE_INK_UNCOVERED = "page_ink_uncovered"              # warning：大片墨迹没被任何块覆盖
PAGE_INK_DRAFT_EXCLUDED = "page_ink_draft_excluded"    # hint：整页草稿式手写被排除（显式）
PAGE_INK_INVALID = "page_ink_invalid"                  # warning：墨迹区域读不出来
COVERAGE_NOT_CHECKED = "coverage_not_checked"          # hint：没给墨迹，这一条没查
BLOCK_CANDIDATE_REJECTED = "block_candidate_rejected"  # warning：模型报的块被拒（少一块要喊）
BLOCK_BOX_CLAMPED = "block_box_clamped"                # warning：框越出页面，裁到页内
PAGE_NOT_PARSED = "page_segmentation_unparsed"         # warning：模型输出解析不出块
RESEGMENT_CARD_HUMAN_WORK = "resegment_card_human_work"    # warning：这块绑着人动过的卡
RESEGMENT_CANDIDATE_BOUND = "resegment_candidate_has_binding"  # hint：候选块不该带绑定


def _warn(code: str, message: str, level: str = "warning") -> dict:
    """一条对账警告。`level` 由服务给（契约 §2：界面不许自行升降级）。"""
    return {"code": code, "level": level, "message": message}


# ---------------------------------------------------------------- 切分那一半（模型出候选块）
#
# 这一半在 `proto/` 里**没有可抄的实现**，只有可继承的理由与反例（`proto-inventory.md` §5）：
# 学生的手写解答会填满题与题之间的空隙，所以「按行统计墨迹空隙」的几何投影在真实卷子上
# 会散架（`proto/slice.py:860-862`；`1-0000.png` 框下方 6158 像素墨迹全是手写解答）。
# 候选块因此由抽取角色给出，本模块只做**确定性**的解析与校验。
#
# ⚠ 改提示词就必须重跑抽取角色的考卷（spec #2 Testing Decisions 第 4 层；
# `proto/accept --role extract` 的口径）——`SEGMENT_SYSTEM` 是这一层的一部分。
SEGMENT_SYSTEM = """你在为一页试卷做切分录入。你会收到一张**整页**试卷照片：上面印着十几道题，其中几道有红笔订正，题与题之间的空隙里可能有学生的手写解答。

把这一页上的**每一道题**找出来，逐个给出它的位置与题号。铁律：

1. 只输出一个 JSON 对象，不要解释，不要 markdown 代码块。
2. **不要按墨迹空隙切**：学生的手写解答会填满题与题之间的空隙，按空隙切会把题切碎或并在一起。按**印刷体的题目本身**（题干、选项、解答题的题号）判断一道题从哪开始、到哪结束。
3. 每个块的位置用归一化坐标 `[x, y, w, h]`，取值 0~1，原点在左上角：`x`、`y` 是左上角，`w`、`h` 是宽高。只框住**题目本身**，不含手写解答，也不含订正。
4. `question_no` 填**题目上印着的题号**（整数，比如 17）。看不清或这一块根本没有题号就填 null——**不许猜、不许编号码**：题号连续性检查靠它，编一个号会让这条检查失灵。
5. 顺序按题号在页面上出现的顺序。看不清的块宁可不报，也不要报一个位置离谱的块。"""

SEGMENT_SHAPE = """{
  "blocks": [
    {"question_no": 17, "bbox_norm": [0.06, 0.08, 0.88, 0.14]}
  ]
}"""


def parse_candidate_blocks(payload) -> dict:
    """模型报出的候选块 → 统一形状的块列表；拒绝的**逐条给理由**。

    输入可以是模型原文（字符串，走 `judge_client.extract_json` 同一套抠 JSON 口径）
    或已经解析好的对象。输出：

    - `parsed=False`：连一个块都读不出来（没 JSON / 没有 `blocks` / `blocks` 不是列表）。
      这**不是**「这一页没有题」——解析失败与空页必须分得开，否则失败会伪装成空页。
    - `blocks`：`{id, bbox_norm, question_no}`，`id` 按模型顺序编成 `b1…bk`；
      `question_no` 只在它是**整数**时保留（`"17"`、`17.5`、`true` 都不算题号，不猜）。
    - `rejected`：`{index, question_no, reason}`——模型报了但读不出边界的块。
      **少一块要喊**（`proto/server.py:320-321` 的反面），所以它同时进 `warnings`。
    - `warnings`：`{code, level, message}`。越出页面的框裁到页边界并报原值。

    本函数不联网、不碰图：模型调用是可注入的接缝（与判定角色同一套：纯文本进、
    原文出），真传输由共用的模型客户端给（`#12` 抽 `server/model_client.py`）。
    """
    if isinstance(payload, (str, bytes)):
        text = payload.decode("utf-8", "replace") if isinstance(payload, bytes) else payload
        try:
            data = judge_client.extract_json(text)
        except ValueError as exc:
            return {"parsed": False, "blocks": [], "rejected": [], "warnings": [
                _warn(PAGE_NOT_PARSED, f"模型输出里没有可用的 JSON：{exc} → "
                                       f"这不是「这一页没有题」，是切分没跑成")],
                "message": f"模型输出里没有可用的 JSON：{exc}"}
    else:
        data = payload

    raw_blocks = data.get("blocks") if isinstance(data, dict) else None
    if not isinstance(raw_blocks, list):
        return {"parsed": False, "blocks": [], "rejected": [], "warnings": [
            _warn(PAGE_NOT_PARSED, f"模型输出里没有 blocks 列表（拿到 {type(raw_blocks).__name__}）"
                                   f" → 这不是「这一页没有题」，是切分没跑成")],
            "message": f"模型输出里没有 blocks 列表（拿到 {type(raw_blocks).__name__}）"}

    blocks: list[dict] = []
    rejected: list[dict] = []
    warnings: list[dict] = []
    for index, item in enumerate(raw_blocks):
        number = item.get("question_no") if isinstance(item, dict) else None
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            number = None
        box = item.get("bbox_norm") if isinstance(item, dict) else None
        if not pages.usable_box(box):
            rejected.append({"index": index, "question_no": number,
                             "reason": f"bbox_norm 读不出可用的整页归一化框（拿到 {box!r}）"})
            warnings.append(_warn(
                BLOCK_CANDIDATE_REJECTED,
                f"模型报的第 {index} 块（题号 {number}）被拒：bbox_norm 读不出可用的"
                f"整页归一化框（拿到 {box!r}）→ 少了一块，别当成「这一页就这么多题」"))
            continue
        x, y, w, h = (float(v) for v in box)
        x, y = max(0.0, min(1.0, x)), max(0.0, min(1.0, y))
        w, h = min(max(0.0, w), 1.0 - x), min(max(0.0, h), 1.0 - y)
        if [x, y, w, h] != [float(v) for v in box]:
            warnings.append(_warn(
                BLOCK_BOX_CLAMPED,
                f"模型报的第 {index} 块（题号 {number}）的框越出页面："
                f"{json.dumps(list(box), ensure_ascii=False)} → 裁到页内 "
                f"{json.dumps([x, y, w, h], ensure_ascii=False)}"))
        if w <= 0 or h <= 0:
            rejected.append({"index": index, "question_no": number,
                             "reason": "框裁到页内之后没有面积"})
            warnings.append(_warn(
                BLOCK_CANDIDATE_REJECTED,
                f"模型报的第 {index} 块（题号 {number}）被拒：框裁到页内之后没有面积"
                f"→ 少了一块，别当成「这一页就这么多题」"))
            continue
        blocks.append({"id": f"b{len(blocks) + 1}", "bbox_norm": [x, y, w, h],
                       "question_no": number})

    return {"parsed": True, "blocks": blocks, "rejected": rejected,
            "warnings": warnings,
            "message": f"读到 {len(blocks)} 块、拒了 {len(rejected)} 块"}


def _question_no_of(block: dict):
    """块上可用的题号：正整数才是题号。`None` = 模型没报（或报了非整数）。

    `True` 是 `int` 的实例，但它不是题号——`bool` 显式挡掉。
    """
    value = block.get("question_no")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 1 else None


def check_question_numbers(blocks) -> dict:
    """判据 1：题号连续性。返回事实（不含措辞、不升级级别）。

    - `numbers`：按块序读到的题号；
    - `gaps`：`[最小号, 最大号]` 之间没被任何块报出的号（**缺号**，最便宜也最强的响声）；
    - `duplicates`：被两块以上报出的号（切重了，同样要喊）；
    - `blocks_without_number`：没报出可用题号的块 id（**检查在这里是瞎的**）；
    - `ok`：没有缺号、没有重复；`complete`：每个块都报了可用题号。

    `ok` 与 `complete` 分开，是因为「检查失败」与「检查通过」必须长得不一样：
    `ok=True, complete=False` 的意思是「在我看得见的题号里没发现断裂，
    但我看得见的不是全部」——这句话正是 spec #2 要求写下来的那条弱点。
    """
    numbers: list[int] = []
    blind: list[str] = []
    for block in blocks or []:
        if not isinstance(block, dict):
            blind.append(repr(block))
            continue
        value = _question_no_of(block)
        if value is None:
            blind.append(block.get("id"))
        else:
            numbers.append(value)

    seen: set[int] = set()
    duplicates: list[int] = []
    for value in numbers:
        if value in seen and value not in duplicates:
            duplicates.append(value)
        seen.add(value)

    gaps: list[int] = []
    if numbers:
        low, high = min(numbers), max(numbers)
        gaps = [n for n in range(low, high + 1) if n not in seen]

    return {
        "checked": True,
        "ok": not gaps and not duplicates,
        "complete": not blind,
        "numbers": numbers,
        "gaps": gaps,
        "duplicates": duplicates,
        "blocks_without_number": blind,
    }


def _split_blocks(blocks):
    """块列表 → `(可用的块, 边界读不出来的块 id, 根本不是对象的项)`。

    三种要分开，因为「读不出边界」与「这不是一个块」是两件事，报出来的码也不同
    （`block_without_box` / `block_not_an_object`）。两者都**不许安静地跳过**。
    """
    usable: list[dict] = []
    invalid: list = []
    not_objects: list[str] = []
    for block in blocks or []:
        if not isinstance(block, dict):
            not_objects.append(repr(block))
        elif pages.usable_box(block.get("bbox_norm")):
            usable.append(block)
        else:
            invalid.append(block.get("id"))
    return usable, invalid, not_objects


def check_overlaps(blocks) -> dict:
    """判据 2：同页两块的边界不相交。

    重合度用的是 `server/pages.py: iou`——**框与框的关系只有那一份实现**，
    这里不另写一套几何（#9 的越界交给本工单的只有口径，不是第二份代码）。

    - `pairs`：相交的块对（`{a, b, iou}`），按块序；
    - `invalid`：边界读不出来、这一条**对它没查**的块 id（`pages.iou` 对坏框返回
      `0.0`——不猜；所以坏框必须显式挑出来，否则「没有重叠」里会混进「没查」）；
    - `not_objects`：列表里混进来的非对象项；
    - `ok`：没有相交的块对；`complete`：每一块都读得出来。

    贴边不算相交（交集面积为 0）——「相邻」不是「重叠」。
    """
    usable, invalid, not_objects = _split_blocks(blocks)
    pairs: list[dict] = []
    for index, a in enumerate(usable):
        for b in usable[index + 1:]:
            score = pages.iou(a.get("bbox_norm"), b.get("bbox_norm"))
            if score > 0:
                pairs.append({"a": a.get("id"), "b": b.get("id"), "iou": score})

    return {"checked": True, "ok": not pairs, "complete": not invalid and not not_objects,
            "pairs": pairs, "invalid": invalid, "not_objects": not_objects}


# 覆盖率判据的三个常量。它们是**判断，不是真理**，所以都具名、都可以注入（测试用得上）：
#
# - `COVERED_MIN = 0.5`：「基本覆盖」= 墨迹区域过半的面积落在块里。不到一半就算
#   没被框住——块与墨迹重叠一点点不算覆盖了它。
# - `UNCOVERED_MIN_PX = 2000`：「大片」的绝对门槛（整页像素）。锚点是实测：
#   `1-0000.png` 框下方 6158 像素手写解答，正是这条要抓的东西
#   （`docs/acceptance-log.md:75-83`）；原型 `cropcheck` 对「成片墨迹」的判据是
#   条带面积的 0.5%（`proto/slice.py:888-896`），本判据取的是同一量级的严格版。
#   门槛是绝对像素而不是比例，因为「大片没被框住」的危害与页面大小无关。
# - `DRAFT_PAGE_SPAN = 0.9`：整页草稿式手写的判据 = 单个墨迹区域宽高都几乎铺满整页。
COVERED_MIN = 0.5
UNCOVERED_MIN_PX = 2000
DRAFT_PAGE_SPAN = 0.9


def _region_box(region: dict):
    """墨迹区域 → `(x, y, w, h, px)`；读不出来 → `None`（调用方要显式报掉）。"""
    if not isinstance(region, dict):
        return None
    box = region.get("bbox_norm")
    px = region.get("px")
    if not pages.usable_box(box):
        return None
    if isinstance(px, bool) or not isinstance(px, (int, float)) or px < 0:
        return None
    x, y, w, h = (float(v) for v in box)
    return x, y, w, h, float(px)


def _covered_fraction(box, block_boxes) -> float:
    """一个墨迹框被若干块覆盖的面积比例。**精确**算，不是采样。

    做法：把区域按所有块边界的 x 切成竖条，每条取中点判断有哪些块盖住它，
    合并 y 区间求并集长度。矩形与矩形的关系是确定的，所以结论不随分辨率漂移
    （采样会有「阈值附近一条细缝抓不抓得住」的假象）。
    """
    x, y, w, h = box
    edges = {x, x + w}
    for bx, by, bw, bh in block_boxes:
        if by < y + h and by + bh > y and bx < x + w and bx + bw > x:
            edges.add(min(max(bx, x), x + w))
            edges.add(min(max(bx + bw, x), x + w))
    stops = sorted(edges)

    covered = 0.0
    for left, right in zip(stops, stops[1:]):
        if right <= left:
            continue
        mid = (left + right) / 2
        spans = []
        for bx, by, bw, bh in block_boxes:
            if bx <= mid < bx + bw:
                low, high = max(by, y), min(by + bh, y + h)
                if high > low:
                    spans.append((low, high))
        spans.sort()
        merged = 0.0
        cur_low = cur_high = None
        for low, high in spans:
            if cur_high is None:
                cur_low, cur_high = low, high
            elif low > cur_high:
                merged += cur_high - cur_low
                cur_low, cur_high = low, high
            else:
                cur_high = max(cur_high, high)
        if cur_high is not None:
            merged += cur_high - cur_low
        covered += (right - left) * merged
    return covered / (w * h)


def check_coverage(blocks, ink, *, covered_min: float = COVERED_MIN,
                   min_uncovered_px: float = UNCOVERED_MIN_PX,
                   draft_span: float = DRAFT_PAGE_SPAN) -> dict:
    """判据 3：该页墨迹应被各块基本覆盖；大片没被覆盖的墨迹要报出来。

    `ink` 由图像统计层给出：每个区域 `{"bbox_norm": [x, y, w, h], "px": N}`。
    `ink` 为 `None` = **没给** → `checked=False`，明说这一条没查（不许当成通过）。

    - `uncovered`：覆盖比例 < `covered_min` 的墨迹区域（**全部都列**，含小的）；
      其中大到 `min_uncovered_px` 的带 `alarm=True`（这一条才是警报）。
    - `excluded`：被判为「整页草稿式手写」而排除的区域，带 `reason`。排除必须显式——
      启发式排除了什么，是这条判据可信度的一部分。
    - `invalid`：读不出边界的墨迹区域或块（这一条对它们没查）。
    """
    if ink is None:
        return {"checked": False, "ok": None, "complete": False,
                "ink_px": 0.0, "covered_px": 0.0, "uncovered_px": 0.0,
                "covered_ratio": None, "uncovered": [], "excluded": [],
                "invalid": [], "invalid_blocks": [], "not_objects": [],
                "message": "没有墨迹统计（ink 未提供）→ 覆盖率这一条没查；"
                           "缺了这一条，对账只是「我看得见的部分没问题」"}

    block_boxes = []
    usable, invalid_blocks, not_objects = _split_blocks(blocks)
    for block in usable:
        bx, by, bw, bh = (float(v) for v in block["bbox_norm"])
        block_boxes.append((bx, by, bw, bh))

    regions = []
    invalid: list[str] = []
    for region in ink:
        parsed = _region_box(region)
        if parsed is None:
            invalid.append(f"{region!r}")
            continue
        regions.append(parsed)

    excluded: list[dict] = []
    uncovered: list[dict] = []
    ink_px = 0.0
    covered_px = 0.0
    for x, y, w, h, px in regions:
        if w >= draft_span and h >= draft_span:
            # 「整页草稿式手写」：单个区域几乎铺满整页。它对覆盖率的唯一作用是把
            # 所有块都算成「没盖住」，所以先排除——但排除要留痕（见 docstring 弱点）。
            excluded.append({"bbox_norm": [x, y, w, h], "px": px, "reason": "page_span"})
            continue
        fraction = _covered_fraction((x, y, w, h), block_boxes)
        ink_px += px
        covered_px += px * fraction
        if fraction < covered_min:
            uncovered.append({"bbox_norm": [x, y, w, h], "px": px,
                              "covered": fraction, "alarm": px >= min_uncovered_px})

    return {
        "checked": True,
        "ok": not any(item["alarm"] for item in uncovered),
        "complete": not invalid and not invalid_blocks,
        "ink_px": ink_px,
        "covered_px": covered_px,
        "uncovered_px": sum(item["px"] for item in uncovered),
        "covered_ratio": (covered_px / ink_px) if ink_px else None,
        "uncovered": uncovered,
        "excluded": excluded,
        "invalid": invalid,
        "invalid_blocks": invalid_blocks,
        "not_objects": not_objects,
    }


def _block_ids(blocks) -> list:
    return [b.get("id") if isinstance(b, dict) else repr(b) for b in (blocks or [])]


def reconcile(blocks, ink=None, *, covered_min: float = COVERED_MIN,
              min_uncovered_px: float = UNCOVERED_MIN_PX,
              draft_span: float = DRAFT_PAGE_SPAN) -> dict:
    """三条判据合成**一条对账结论**（spec #2：一份块列表进去，一条明确的对账结论出来）。

    返回 `{checks, warnings, summary}`：

    - `checks`：三条判据各自的**事实**（明细都在这里，界面可以展开看）；
    - `warnings`：`{code, level, message}` 列表（形状同契约 §8 的 `Warning`）——
      `warning` 是真矛盾，`hint` 是「我这一条没查全 / 我排除了什么」；
    - `summary`：`{blocks, alarms, checks_run, checks_skipped, ok, complete}`。
      `ok` 只覆盖**查过的**那部分；`complete` 说清有没有判据在瞎着——
      这两件事分开，是因为「检查通过」与「检查失败」必须长得不一样（见模块 docstring）。

    判据之外一律不猜：块少给了题号、块或墨迹的边界读不出来，都会被点名。
    """
    question_numbers = check_question_numbers(blocks)
    overlaps = check_overlaps(blocks)
    coverage = check_coverage(blocks, ink, covered_min=covered_min,
                              min_uncovered_px=min_uncovered_px, draft_span=draft_span)

    warnings: list[dict] = []

    if question_numbers["gaps"]:
        missing = "、".join(str(n) for n in question_numbers["gaps"])
        warnings.append(_warn(
            QUESTION_NUMBER_GAP,
            f"题号不连续：读到 {question_numbers['numbers']}，缺 {missing} —— "
            f"这是「漏了一题」最便宜的探测器"))
    if question_numbers["duplicates"]:
        repeated = "、".join(str(n) for n in question_numbers["duplicates"])
        warnings.append(_warn(
            QUESTION_NUMBER_DUPLICATE,
            f"题号重复：{repeated} 被两块以上报出 —— 像是切重了"))
    if question_numbers["blocks_without_number"]:
        warnings.append(_warn(
            QUESTION_NUMBER_MISSING,
            f"这些块没报出可用的题号：{question_numbers['blocks_without_number']} → "
            f"题号连续性这一条查不全，**别把它当成保证**（真图回归与人工确认不能省）",
            level="hint"))

    for pair in overlaps["pairs"]:
        warnings.append(_warn(
            BLOCK_OVERLAP,
            f"块 {pair['a']!r} 与 {pair['b']!r} 的边界相交（IoU={pair['iou']:.3f}）→ "
            f"同一页两块不该重叠，多半是切重了"))

    for bad in sorted(set(map(str, overlaps["invalid"] + coverage["invalid_blocks"]))):
        warnings.append(_warn(
            BLOCK_WITHOUT_BOX,
            f"块 {bad} 的边界读不出来（整页归一化 bbox_norm 缺/退化）→ "
            f"重叠与覆盖率判据对它没查"))

    for bad in sorted(set(map(str, overlaps["not_objects"] + coverage["not_objects"]))):
        warnings.append(_warn(
            BLOCK_NOT_AN_OBJECT,
            f"块列表里混进了不是对象的项（{bad}）→ 它没有被当成一个块，也不会被对账"))

    for bad in sorted(set(map(str, coverage["invalid"]))):
        warnings.append(_warn(
            PAGE_INK_INVALID,
            f"墨迹区域 {bad} 读不出来（缺/退化的整页归一化 bbox_norm，或 px 不是非负数）"
            f"→ 覆盖率判据对它没查"))

    for miss in coverage["uncovered"]:
        if miss["alarm"]:
            warnings.append(_warn(
                PAGE_INK_UNCOVERED,
                f"未覆盖的墨迹约 {miss['px']:.0f}px（这块区域只被框住 "
                f"{miss['covered']:.0%}，位置 {miss['bbox_norm']}）→ "
                f"有内容没被任何块框住，查看是不是漏了一题"))
    if coverage["excluded"]:
        total = sum(item["px"] for item in coverage["excluded"])
        warnings.append(_warn(
            PAGE_INK_DRAFT_EXCLUDED,
            f"按「整页草稿式手写」排除了 {len(coverage['excluded'])} 片墨迹（共 {total:.0f}px）→ "
            f"不参与覆盖率对账；这一步是**启发式**，排除了什么都在 checks.coverage.excluded 里",
            level="hint"))
    if not coverage["checked"]:
        warnings.append(_warn(COVERAGE_NOT_CHECKED, coverage["message"], level="hint"))

    checks_run = ["question_numbers", "overlaps"] + (["coverage"] if coverage["checked"] else [])
    checks_skipped = [] if coverage["checked"] else ["coverage"]
    alarms = [w["code"] for w in warnings if w["level"] == "warning"]
    return {
        "checks": {"question_numbers": question_numbers, "overlaps": overlaps,
                   "coverage": coverage},
        "warnings": warnings,
        "summary": {
            "blocks": len(_block_ids(blocks)),
            "checks_run": checks_run,
            "checks_skipped": checks_skipped,
            "alarms": alarms,
            "ok": not alarms,
            "complete": (question_numbers["complete"] and overlaps["complete"]
                         and coverage["complete"]),
        },
    }


def _cards_by_id(cards) -> dict:
    """`cards` 可以是「id → 卡」的映射，也可以是一串卡；两种都收。"""
    if not cards:
        return {}
    if isinstance(cards, dict):
        return {key: value for key, value in cards.items() if isinstance(value, dict)}
    return {c.get("id"): c for c in cards if isinstance(c, dict) and c.get("id")}


def human_work_on(card) -> list[str]:
    """这张卡上「人动过」的东西：审核过的字段、人工掩膜、重做历史（spec #2）。

    三项分开列，是因为重切对账要说得清「我在保护什么」——一次重切抹掉 12 笔人工
    掩膜修正里的 9 笔「减」（`docs/acceptance-log.md:259-263`）就是真实损失。
    """
    if not isinstance(card, dict):
        return []
    signals = []
    if (card.get("review") or {}).get("status") == "reviewed":
        signals.append("审核过的字段")
    clean = (card.get("problem") or {}).get("clean") or {}
    if clean.get("manual"):
        signals.append("人工掩膜")
    if card.get("attempts"):
        signals.append("重做历史")
    return signals


def classify_resegment(page, new_blocks, *, cards=None) -> dict:
    """重切对账：把 `pages.rebind` 给出的事实映射成逐块的**新增／保留**三态报告。

    spec #2 的「重切」动作是：对已有页重跑切分 → 返回逐块对照（新增／替换／保留），
    **不写题卡**。本函数就是那条对照的确定性部分（`wrote_cards` / `wrote_page`
    显式写进报告，好让调用方与测试都能断言「这一次重切没有动任何东西」）。

    - `blocks`：**可以落进页文件**的块列表（`rebind` 的产物：几何 + 绑定的 `card_id` + `keep`），
      与候选块同序。`state` 之类的对照事实**不在这里**——块会长成页文件的一行，而
      `matched_from` 下一次重切就过期；把过期事实写进存档文件正是这个项目最怕的
      「安静的谎」（#9 已经定下这条，本函数照做）。
    - `matches`：逐块对照，与 `blocks` 同序：
      `{block_id, state, matched_from, iou, contain}`。
      `state` 只有两个值：`"kept"`（位置重合 → **保留原有绑定**，不重新分配 id）与
      `"new"`（真正新增的块 → 还没有绑定，分配发生在入库那一刻）。
    - `removed`：新切分里找不到位置重合的旧块；**带着卡片的照旧喊**
      （`block_removed_with_card`，来自 `rebind`）——卡片不会被自动抹掉。
    - `warnings`：`rebind` 的警告 + 两条本工单加的：
      `resegment_card_human_work`（这一块对应的卡片已审核／有人工掩膜／有重做历史）
      与 `resegment_candidate_has_binding`（候选块上混进了绑定，不被认，但要喊）。

    **「替换」为什么不在这里**：`summary["replaced"]` 恒为 0，而且这不是「还没实现」，
    是**结构性的事实**——配上的块继承旧块的 `card_id`（保留），配不上的块本来就没有
    绑定（新增），没有第三条路径能让「同一个位置换一张卡」成为自动结果。
    它正是 #14 验收 1 要防的那个失败模式（「已入库的块显示『保留』而不是『替换』」），
    所以这里用一个**会红的测试**把它钉住，而不是留一个空状态等人误用。
    候选块上的绑定一律不认（绑定只能来自页文件或人在界面上的显式改绑）——
    被忽略的绑定要点名报出来，不许安静地丢掉。

    `cards` 可选（`{id: 卡}` 或一串卡）：给了才能判断「这张卡人动过没有」。
    不给 = 这一条没查，报告里 `human_work_checked` 为 `False`（不冒充查过）。
    """
    old_blocks = [b for b in (page.get("blocks") or []) if isinstance(b, dict)]
    candidates = [b for b in (new_blocks or []) if isinstance(b, dict)]
    cards_by_id = _cards_by_id(cards)

    report = pages.rebind(old_blocks, candidates)
    warnings = list(report["warnings"])

    blocks = list(report["blocks"])
    matches: list[dict] = []
    human_work_hits: list[str] = []
    for index, block in enumerate(report["blocks"]):
        match = report["matches"][index]
        candidate = candidates[index] if index < len(candidates) else {}
        stray = candidate.get("card_id")
        if stray:
            warnings.append(_warn(
                RESEGMENT_CANDIDATE_BOUND,
                f"候选块 {block.get('id')!r} 上带着绑定 {stray!r} —— 重切不认候选块上的绑定"
                f"（绑定只能来自页文件，或人在界面上的显式改绑）；这一次它被忽略了",
                level="hint"))
        matches.append({
            "block_id": block.get("id"),
            "state": "kept" if match["matched_from"] is not None else "new",
            "matched_from": match["matched_from"],
            "iou": match["iou"],
            "contain": match["contain"],
        })
        card = cards_by_id.get(block.get("card_id"))
        signals = human_work_on(card)
        if signals:
            human_work_hits.append(block.get("card_id"))
            warnings.append(_warn(
                RESEGMENT_CARD_HUMAN_WORK,
                f"块 {block.get('id')!r} 对应的卡片 {block.get('card_id')} 有"
                f"{'、'.join(signals)} → 这次重切只给对照，不会改写它；请人工确认",
            ))

    for gone in report["removed"]:
        card = cards_by_id.get(gone.get("card_id"))
        signals = human_work_on(card)
        if not gone.get("card_id") or not signals:
            continue
        human_work_hits.append(gone["card_id"])
        warnings.append(_warn(
            RESEGMENT_CARD_HUMAN_WORK,
            f"消失的块 {gone.get('id')!r} 对应的卡片 {gone['card_id']} 有"
            f"{'、'.join(signals)} → 它的绑定不会被自动转移，请人工确认",
        ))

    states = [m["state"] for m in matches]
    return {
        "page_id": page.get("id"),
        "blocks": blocks,
        "matches": matches,
        "removed": report["removed"],
        "warnings": warnings,
        "summary": {
            "kept": states.count("kept"),
            "new": states.count("new"),
            # 结构性恒为 0：见 docstring。UI 照三态显示时，这一栏永远是空的，
            # 空的理由是有测试钉住的，不是「还没做」。
            "replaced": 0,
            "removed": len(report["removed"]),
            "needs_human": bool(human_work_hits) or report["summary"]["removed_with_card"] > 0,
            "human_work_checked": bool(cards_by_id),
        },
        "wrote_cards": False,
        "wrote_page": False,
    }
