"""页资源的「改」动作 —— 手动修正的最小集合（spec #2、工单 #14）。

**最小集合**（spec #2 定死，别顺手加）：拖边界、合并两块、拆分一块、整块丢弃、
切换收入／丢弃、改题型、改题号。**不做**：旋转校正、透视矫正、双栏自动分区。

三条纪律贯穿本模块：

1. **页文件是真相，界面是它的视图**：每次修正都写回页文件（`pages.save_page`），
   不是只存在界面里——「切分结果与收入决策一旦只存在于界面的内存里，『漏了一题』
   就永远查不出来」（spec #2 的立身之本）。
2. **人动过的决策走 #12 的入口**：切换收入／丢弃调 `intake.set_keep_by_human`，
   **不另立一套「收不收」的判断**（spec #2 的跨单元表：收入决策只有一处实现）。
3. **不许静默**：合并／拆分／丢弃有歧义时明确报出来（拆出来的两块题号怎么给、
   丢弃块上已有题卡绑定怎么办），而不是猜一个。

**「改」不改题卡**：本模块只动页文件。题卡只在「入库」动作里生成（#15），
重切也不写题卡（#10）。所以一次修正之后，页文件变了、索引没变——这是对的，
「收进来的块」与「已生成的卡」本来就该分开看。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from . import errors, intake, pages
from .warnings import _warn

# 修正动作的码表（契约 §2 的形状：`{code, message, id, level}`）。
# 级别只有服务能定：真矛盾 → warning；「这一步有歧义／我替你做了个决定」→ hint。
# 把「按设计如此」报成 warning 会训练人忽略体检（`proto/server.py:1046-1047`）。
BLOCK_UNKNOWN = "page_block_unknown"                  # warning：点名要改的块不在这一页上
BLOCK_EDIT_NOOP = "page_block_edit_noop"              # hint：这一项没变（重复提交幂等）
MERGE_REQUIRES_TWO = "page_merge_needs_two_blocks"    # warning：合并要两块，给少了
SPLIT_INTO_ONE = "page_split_needs_two_boxes"         # hint：拆出来的第二块留空
SPLIT_QUESTION_NUMBERS_UNSET = "page_split_question_no_unset"  # warning：拆出来的题号没给
SPLIT_CARD_BINDING_NOT_CARRIED = "page_split_binding_not_carried"  # warning：绑定只跟第一块
DROP_WITH_CARD_BINDING = "page_drop_with_card_binding"  # warning：丢弃的块上还绑着卡
MERGE_DIFFERENT_TYPES = "page_merge_type_conflict"    # hint：合并的两块题型不同
BOX_NOT_USABLE = "page_block_box_unusable"            # warning：边界读不出来（形状同 #9）
QUESTION_NO_INVALID = "page_question_no_invalid"      # warning：题号不是正整数
TYPE_UNKNOWN = "page_type_unknown"                    # warning：题型不在枚举里
BLOCK_BOX_CHANGED = "page_block_box_changed"          # hint：边界真的动了（含 bbox_px 作废）
BLOCKS_MERGED = "page_blocks_merged"                  # hint：合并成了（记下并了谁）
BLOCK_SPLIT = "page_block_split"                      # hint：拆成了（记下拆成几块）
EDIT_NOT_AN_OBJECT = "page_edit_not_an_object"        # warning：修正项不是对象
EDIT_UNKNOWN_ACTION = "page_edit_unknown_action"      # warning：不认识的动作名
EDIT_EMPTY = "page_edit_empty"                        # hint：这次请求里一条修正都没有
# 与 #9/#10 共用的两个码（形状同那里，本模块只复用字面量，不重复定义实现）
BLOCK_NOT_AN_OBJECT = "block_not_an_object"           # warning：列表里混进了非对象

# 题型枚举（契约 §3 `Problem.type` 的三取值 + 未定）。
# `None` = 「还没人定过」，与 `"solution"`（解答题）是两件事——
# 未定会让「解答题没有标准答案属正常」这条判据失效，所以不许拿它冒充任何一种。
TYPE_CHOICE = "choice"
TYPE_FILLIN = "fillin"
TYPE_SOLUTION = "solution"
PROBLEM_TYPES = (TYPE_CHOICE, TYPE_FILLIN, TYPE_SOLUTION)

# 修正动作的名字（报告里的 `action`，stable：审计要按它分类）。
EDITABLE_ACTIONS = ("move", "merge", "split", "drop", "keep", "type", "question_no")


def _page_warn(code: str, message: str, level: str = "warning", *,
               card_id: str | None = None) -> dict:
    """页级警告：形状的唯一实现在 `server/warnings.py`（BRIEF 硬规则 7）。"""
    return _warn(code, message, card_id, level)


def _blocks(page: dict) -> tuple[list[dict], list[dict]]:
    """页里的块：`(可用的块, 警告)`。非对象项**点名报出来**，不静默 filter
    （`proto/server.py:320-321`、`:933-936` 是同一种反面写法）。"""
    usable: list[dict] = []
    warnings: list[dict] = []
    for index, raw in enumerate(page.get("blocks") or []):
        if not isinstance(raw, dict):
            warnings.append(_page_warn(
                BLOCK_NOT_AN_OBJECT,
                f"页 {page.get('id')!r} 的块列表第 {index} 项不是对象（{raw!r}）"))
            continue
        usable.append(raw)
    return usable, warnings


def _index_of(blocks: list[dict], block_id) -> int | None:
    for index, block in enumerate(blocks):
        if block.get("id") == block_id:
            return index
    return None


def _require_blocks(blocks: list[dict], ids, warnings: list[dict]) -> list[dict]:
    """点名要动的块必须都在这一页上——**点不到的要说出来**，不许静默忽略。

    返回按**页里的顺序**排好的块（不是调用方给的顺序）：页文件的块序是稳定的事实，
    让输入顺序决定输出顺序会让同一份修正产生两份不同的页文件。
    """
    missing = [bid for bid in ids if _index_of(blocks, bid) is None]
    if missing:
        warnings.append(_page_warn(
            BLOCK_UNKNOWN,
            f"点名要改的块在这一页上找不到：{missing} → 这几块一个字节都没动"))
    found = [block for block in blocks if block.get("id") in set(ids)]
    return found


def _bad_box(block: dict) -> bool:
    return not pages.usable_box(block.get("bbox_norm"))


def _replace(page: dict, blocks: list[dict], *, at, action: str, note: str) -> dict:
    """把改完的块列表装回页文件。**只动 `blocks`**：页的身份、来源、照片都不碰。

    `updated_at` 与 `last_edit` 是审计面：页文件是真相，那它必须回答
    「上一次是谁在什么时候改了它、改的什么」——否则「漏了一题」查不出来。
    """
    return {
        **page,
        "blocks": blocks,
        "updated_at": at,
        "last_edit": {"action": action, "at": at, "note": note},
    }


# ---------------------------------------------------------------- 拖边界


def move_block(page: dict, block_id, box, *, at=None) -> dict:
    """**拖边界**：改一个块的 `bbox_norm`（整页归一化 xywh）。

    页文件里块边界的**规范基准就是 `bbox_norm`**（D5：不随图片重编码/缩放失效），
    所以拖边界写的是它。`bbox_px` 是交叉验证用的盘上原值，**拖动之后它必然过期**——
    所以这里把它清成 `None` 并显式报出来，而不是留一个和 `bbox_norm` 矛盾的像素框
    （留着它就是「安静的谎」：下游照着它算红笔统计，算的是旧位置）。
    """
    moment = at or datetime.now()
    stamp = _iso(moment)
    blocks, warnings = _blocks(page)
    target = _require_blocks(blocks, [block_id], warnings)
    if not target:
        return _result(page, at=stamp, action="move", warnings=warnings, changed=False)
    block = target[0]
    if not pages.usable_box(box):
        warnings.append(_page_warn(
            BOX_NOT_USABLE,
            f"块 {block_id!r} 的新边界读不出来（{box!r}）→ 这一块没动",
            card_id=block.get("card_id")))
        return _result(page, at=stamp, action="move", warnings=warnings, changed=False)

    moved = [float(v) for v in box]
    if list(block.get("bbox_norm") or []) == moved:
        warnings.append(_page_warn(
            BLOCK_EDIT_NOOP, f"块 {block_id!r} 的边界本来就是 {moved} → 没动", "hint",
            card_id=block.get("card_id")))
        return _result(page, at=stamp, action="move", warnings=warnings, changed=False)

    was = block.get("bbox_norm")
    updated = {**block, "bbox_norm": moved, "bbox_px": None}
    blocks = [updated if b is block else b for b in blocks]
    warnings.append(_page_warn(
        "page_block_box_changed",
        f"块 {block_id!r} 的边界从 {was} 拖到 {moved}；`bbox_px`（整页像素框）已作废置空"
        f" → 红笔统计要按新边界重算", "hint", card_id=block.get("card_id")))
    page = _replace(page, blocks, at=stamp, action="move",
                    note=f"块 {block_id} 边界 {was} → {moved}")
    return _result(page, at=stamp, action="move", warnings=warnings, changed=True,
                   blocks_changed=[block_id])


# ---------------------------------------------------------------- 合并两块


def merge_blocks(page: dict, block_ids, *, at=None) -> dict:
    """**合并两块**：把两块并成一块，边界取**并集**（能盖住两块的框）。

    需要**至少两块**；只给一块是明确的错误形状（不是静默的成功）。

    合并的歧义，逐条**报出来**而不是猜：

    - **题型**：两块题型不同 → 取第一块的，另报一条 hint（人一看就知道要不要改）；
    - **题号**：取第一块的（题号连续性检查靠它，所以合并后那个号仍然有据可查）；
    - **卡片绑定**：`card_id` 只跟第一块。第二块若也绑着卡，那条绑定**不会被合并进来**
      ——两块并成一块之后，一张卡只能有一个块，所以另一张卡会变成「页上没有它的块」
      （`page_binding_lost` 会喊）。这里显式报出来，让人决定要不要丢弃那张卡。
    - **去留**：`keep` 取第一块的（`decision` 由 #12 的入口给，不在这里重判）。
    """
    moment = at or datetime.now()
    stamp = _iso(moment)
    blocks, warnings = _blocks(page)
    wanted = list(block_ids or [])
    if len(wanted) < 2:
        warnings.append(_page_warn(
            MERGE_REQUIRES_TWO,
            f"合并至少要两块，收到 {len(wanted)} 个块 id（{wanted}）→ 页文件没动"))
        return _result(page, at=stamp, action="merge", warnings=warnings, changed=False)
    targets = _require_blocks(blocks, wanted, warnings)
    if len(targets) < 2:
        return _result(page, at=stamp, action="merge", warnings=warnings, changed=False)

    first = targets[0]
    # 合并是在**页里的顺序**上做的：并成的那一块占第一块原来的位置，
    # 其余被并掉的块从列表里移除（页文件的块序是稳定的事实）。
    for block in targets[1:]:
        if block.get("card_id"):
            warnings.append(_page_warn(
                DROP_WITH_CARD_BINDING,
                f"合并把块 {block.get('id')!r} 并进 {first.get('id')!r}，但前者绑着卡片 "
                f"{block['card_id']!r} → 这张卡的绑定不会被合并进来（一张卡只能有一个块），"
                f"它现在在这一页上没有块了，请人工确认要不要丢弃它",
                card_id=block.get("card_id")))
        if block.get("problem_type") != first.get("problem_type") and block.get("problem_type"):
            warnings.append(_page_warn(
                MERGE_DIFFERENT_TYPES,
                f"合并的两块题型不同：{first.get('id')!r} 是 {first.get('problem_type')!r}、"
                f"{block.get('id')!r} 是 {block.get('problem_type')!r} → 取第一块的"
                f"（合并之后是同一道题）", "hint", card_id=block.get("card_id")))

    merged_box = union_box([b.get("bbox_norm") for b in targets])
    unreadable = [b.get("id") for b in targets if _bad_box(b)]
    if merged_box is None:
        warnings.append(_page_warn(
            BOX_NOT_USABLE,
            f"要合并的块里没有一块的边界读得出来（{unreadable}）→ 页文件没动"))
        return _result(page, at=stamp, action="merge", warnings=warnings, changed=False)
    if unreadable:
        warnings.append(_page_warn(
            BOX_NOT_USABLE,
            f"合并里有边界读不出来的块：{unreadable} → 并集只按读得出来的那些算",
            card_id=first.get("card_id")))

    removed_ids = [b.get("id") for b in targets[1:]]
    merged = {
        **first,
        "bbox_norm": merged_box,
        # 边界变了 → 像素框作废（同 `move_block`，理由见那里）
        "bbox_px": None,
        "merged_from": [b.get("id") for b in targets],
    }
    # 并成的那一块占**第一块原来的位置**（页文件的块序是稳定的事实）；
    # 其余被并掉的块从列表里移除。按身份（`is`）判，不按 id——块 id 不保证唯一。
    absorbed = {id(b) for b in targets[1:]}
    kept: list[dict] = []
    for block in blocks:
        if id(block) in absorbed:
            continue
        kept.append(merged if block is first else block)
    blocks = kept
    page = _replace(page, blocks, at=stamp, action="merge",
                    note=f"合并 {[b.get('id') for b in targets]} → {first.get('id')}，"
                         f"边界取并集 {merged_box}")
    warnings.append(_page_warn(
        BLOCKS_MERGED,
        f"块 {removed_ids} 并进 {first.get('id')!r}，新边界 {merged_box}", "hint",
        card_id=first.get("card_id")))
    return _result(page, at=stamp, action="merge", warnings=warnings, changed=True,
                   blocks_changed=[first.get("id")], blocks_removed=removed_ids)


def union_box(boxes):
    """一串整页归一化 xywh 框的**并集**（能盖住全部的那个框）。全读不出来 → `None`。

    并集只按读得出来的框算——读不出来的框由调用方显式报出来，不在这里装作它是零。
    """
    good = []
    for box in boxes or []:
        if pages.usable_box(box):
            good.append([float(v) for v in box])
    if not good:
        return None
    x0 = min(b[0] for b in good)
    y0 = min(b[1] for b in good)
    x1 = max(b[0] + b[2] for b in good)
    y1 = max(b[1] + b[3] for b in good)
    return [x0, y0, x1 - x0, y1 - y0]


# ---------------------------------------------------------------- 拆分一块


def split_block(page: dict, block_id, boxes, *, question_numbers=None, at=None) -> dict:
    """**拆分一块**：把一个块切成若干块，每块给一个整页归一化边界。

    拆分的歧义，spec 没给做法，所以这里**明确报出来而不是猜**：

    - **题号怎么给**：拆出来的每一块都要有人给题号（`question_numbers`，与 `boxes`
      同序、`None` 表示「还没定」）。**不给就报 `page_split_question_no_unset`**
      ——题号连续性检查靠它，编一个号会让那条最强的检查失灵
      （`server/segmentation.py` 的已知弱点：题号连续性依赖题号报得对）。
      所以这里不猜、不自动编号，只把「你还没给」说出来。
      拆出来的第一块**继承原块的题号**（它本来就是从那一块来的），其余留空等人填。
    - **卡片绑定怎么办**：`card_id` 只保留在**第一块**上（拆分不改绑定，
      绑定只跟它原来那一块）。其余块 `card_id` 为 `None` = 还没入库，
      新人库时分配新 id（`pages.assign_card_ids`）。
    - **`keep` 与 `decision`**：只有第一块继承原块的（人动过的那一份）。
      其余块 `keep` 为 `None`（**待定**，不是「不收」）——#12 的决策入口下次跑会判它们，
      而「待定」会显式报出来，不会静默变成「不收」（同 `intake` 对 `null` 的纪律）。

    至少两块；只给一块（或空）是明确的错误形状。
    """
    moment = at or datetime.now()
    stamp = _iso(moment)
    blocks, warnings = _blocks(page)
    target = _require_blocks(blocks, [block_id], warnings)
    if not target:
        return _result(page, at=stamp, action="split", warnings=warnings, changed=False)
    block = target[0]
    wanted = list(boxes or [])
    if len(wanted) < 2:
        warnings.append(_page_warn(
            SPLIT_INTO_ONE,
            f"拆分要给出至少两块的新边界，收到 {len(wanted)} 个 → 页文件没动", "hint"))
        return _result(page, at=stamp, action="split", warnings=warnings, changed=False)

    good: list[list[float]] = []
    for index, box in enumerate(wanted):
        if not pages.usable_box(box):
            warnings.append(_page_warn(
                BOX_NOT_USABLE,
                f"拆分出来的第 {index + 1} 块边界读不出来（{box!r}）→ 这一块没有拆出来",
                card_id=block.get("card_id")))
            continue
        good.append([float(v) for v in box])
    if len(good) < 2:
        warnings.append(_page_warn(
            SPLIT_INTO_ONE, f"拆分里读得出来的边界不足两块（{wanted}）→ 页文件没动", "hint"))
        return _result(page, at=stamp, action="split", warnings=warnings, changed=False)

    numbers = list(question_numbers or [])
    first_number = block.get("question_no")
    pieces: list[dict] = []
    for index, box in enumerate(good):
        given = numbers[index] if index < len(numbers) else None
        if index == 0 and given is None:
            given = first_number       # 第一块继承原块的题号（它本来就是从那一块来的）
        if given is not None and not _is_question_no(given):
            warnings.append(_page_warn(
                QUESTION_NO_INVALID,
                f"拆分出来的第 {index + 1} 块题号不是一个正整数（{given!r}）→ 按「还没定」处理",
                card_id=block.get("card_id")))
            given = None
        if given is None and index > 0:
            warnings.append(_page_warn(
                SPLIT_QUESTION_NUMBERS_UNSET,
                f"拆分出来的第 {index + 1} 块还没给题号（`question_numbers`）→ 留空等人填；"
                f"**不猜、不自动编号**（题号连续性检查靠它）", "hint",
                card_id=block.get("card_id")))
        piece = {
            "id": f"{block.get('id')}s{index + 1}" if index > 0 else block.get("id"),
            "bbox_norm": box,
            "bbox_px": None,
            "question_no": given,
            # 只有第一块继承绑定与去留：拆分不改绑定，绑定只跟它原来那一块
            "card_id": block.get("card_id") if index == 0 else None,
            "keep": block.get("keep") if index == 0 else None,
            "problem_type": block.get("problem_type"),
        }
        if index == 0:
            if block.get("decision") is not None:
                piece["decision"] = block["decision"]
            if block.get("ink") is not None:
                piece["ink"] = block["ink"]
        pieces.append(piece)

    if block.get("card_id"):
        warnings.append(_page_warn(
            SPLIT_CARD_BINDING_NOT_CARRIED,
            f"拆分之后卡片 {block['card_id']!r} 的绑定只留在第一块 "
            f"{pieces[0]['id']!r} 上；其余 {len(pieces) - 1} 块还没入库"
            f"（入库时各分配一个新 id）", "hint", card_id=block.get("card_id")))

    index = _index_of(blocks, block_id)
    blocks = blocks[:index] + pieces + blocks[index + 1:]
    page = _replace(page, blocks, at=stamp, action="split",
                    note=f"块 {block_id} 拆成 {len(pieces)} 块："
                         f"{[p['id'] for p in pieces]}")
    warnings.append(_page_warn(
        BLOCK_SPLIT,
        f"块 {block_id!r} 拆成 {len(pieces)} 块"
        f"（第一块保留原块 id 与绑定）", "hint", card_id=block.get("card_id")))
    return _result(page, at=stamp, action="split", warnings=warnings, changed=True,
                   blocks_changed=[p["id"] for p in pieces], blocks_removed=[block_id])


def _is_question_no(value) -> bool:
    """正整数才是题号（`bool` 是 `int` 的实例，但它不是题号——同 `segmentation`）。"""
    return (not isinstance(value, bool) and isinstance(value, int) and value >= 1)


# ---------------------------------------------------------------- 整块丢弃 / 切换收入


def drop_block(page: dict, block_id, *, at=None) -> dict:
    """**整块丢弃**：把一块标成不收（走 #12 的人改去留入口）。

    丢弃**不删块**：块留在页文件里并带 `keep: false` + `decision.rule = "human_drop"`
    ——这样「为什么这块没入库」永远查得出来（spec #2：决策可审计）。
    删掉它才是静默丢题。
    """
    return _toggle_keep(page, block_id, keep=False, action="drop", at=at)


def keep_block(page: dict, block_id, *, at=None) -> dict:
    """**切换收入**：把一块标成收（走 #12 的人改去留入口）。"""
    return _toggle_keep(page, block_id, keep=True, action="keep", at=at)


def _toggle_keep(page: dict, block_id, *, keep: bool, action: str, at=None) -> dict:
    """切换收入／丢弃的**唯一实现**：调 `intake.set_keep_by_human`。

    这里**绝不自己判「该不该收」**——那是 #12 的规则（痕迹语义 → 收／不收／待定），
    spec #2 的跨单元表把它定成一处实现。人的决定只是把它改成 `source: "human"`，
    于是自动决策重跑时不会把它翻回去（#12 已经测过）。
    """
    moment = at or datetime.now()
    stamp = _iso(moment)
    blocks, warnings = _blocks(page)
    target = _require_blocks(blocks, [block_id], warnings)
    if not target:
        return _result(page, at=stamp, action=action, warnings=warnings, changed=False)
    block = target[0]
    if block.get("keep") is keep:
        warnings.append(_page_warn(
            BLOCK_EDIT_NOOP,
            f"块 {block_id!r} 本来就{'已收' if keep else '是不收'} → 没动", "hint",
            card_id=block.get("card_id")))
        return _result(page, at=stamp, action=action, warnings=warnings, changed=False)

    outcome = intake.set_keep_by_human(page, [block_id], keep=keep, at=moment)
    warnings = warnings + list(outcome["warnings"])
    changed_block = next((b for b in outcome["page"]["blocks"]
                          if isinstance(b, dict) and b.get("id") == block_id), None)
    if not keep and block.get("card_id"):
        warnings.append(_page_warn(
            DROP_WITH_CARD_BINDING,
            f"块 {block_id!r} 被标成不收，但它绑着卡片 {block['card_id']!r} → 卡片还在库里；"
            f"「不入库」管的是这一次修正之后的块，已生成的卡要删是另一件事（#15）",
            card_id=block.get("card_id")))
    page = _replace(outcome["page"], list(outcome["page"]["blocks"]), at=stamp, action=action,
                    note=f"块 {block_id} 人去留改成 keep={keep}"
                         f"（原来 rule={((block.get('decision') or {}).get('rule'))!r}）")
    return _result(page, at=stamp, action=action, warnings=warnings, changed=True,
                   blocks_changed=[block_id],
                   decision=changed_block.get("decision") if changed_block else None)


# ---------------------------------------------------------------- 改题型 / 改题号


def set_problem_type(page: dict, block_id, problem_type, *, at=None) -> dict:
    """**改题型**：块上的 `problem_type`（契约 §3 的三取值）。

    题型是**人的判断**（「这道题是选择题还是解答题」），所以这里只接受枚举里的值，
    别的值明确拒绝并报出来——一个拼错的题型会静默让「解答题没有标准答案属正常」
    那条判据失效。`None` 是允许的，含义是「还没定」（与 `"solution"` 不是一回事）。
    """
    moment = at or datetime.now()
    stamp = _iso(moment)
    blocks, warnings = _blocks(page)
    target = _require_blocks(blocks, [block_id], warnings)
    if not target:
        return _result(page, at=stamp, action="type", warnings=warnings, changed=False)
    block = target[0]
    if problem_type is not None and problem_type not in PROBLEM_TYPES:
        warnings.append(_page_warn(
            TYPE_UNKNOWN,
            f"题型 {problem_type!r} 不在枚举里（可取值：{list(PROBLEM_TYPES)}）→ 这一块没动",
            card_id=block.get("card_id")))
        return _result(page, at=stamp, action="type", warnings=warnings, changed=False)
    if block.get("problem_type") == problem_type:
        warnings.append(_page_warn(
            BLOCK_EDIT_NOOP, f"块 {block_id!r} 的题型本来就是 {problem_type!r} → 没动", "hint",
            card_id=block.get("card_id")))
        return _result(page, at=stamp, action="type", warnings=warnings, changed=False)

    was = block.get("problem_type")
    updated = {**block, "problem_type": problem_type}
    blocks = [updated if b is block else b for b in blocks]
    page = _replace(page, blocks, at=stamp, action="type",
                    note=f"块 {block_id} 题型 {was!r} → {problem_type!r}")
    return _result(page, at=stamp, action="type", warnings=warnings, changed=True,
                   blocks_changed=[block_id])


def set_question_no(page: dict, block_id, question_no, *, at=None) -> dict:
    """**改题号**：块上的 `question_no`（题目上印着的那个号，正整数）。

    题号是题号连续性检查（spec #2 三条判据里「最便宜也最强的一条」）的唯一输入，
    所以这里**只接受正整数或 `None`**（`None` = 看不清／还没填，不许猜一个）。
    非整数一律明确拒绝，不许 `int()` 硬转——把 `"17.5"` 变成 17 就是编一个号。
    """
    moment = at or datetime.now()
    stamp = _iso(moment)
    blocks, warnings = _blocks(page)
    target = _require_blocks(blocks, [block_id], warnings)
    if not target:
        return _result(page, at=stamp, action="question_no", warnings=warnings, changed=False)
    block = target[0]
    if question_no is not None and not _is_question_no(question_no):
        warnings.append(_page_warn(
            QUESTION_NO_INVALID,
            f"题号 {question_no!r} 不是一个正整数（题目上印着的那个号，或空着不填）"
            f"→ 这一块没动", card_id=block.get("card_id")))
        return _result(page, at=stamp, action="question_no", warnings=warnings, changed=False)
    if block.get("question_no") == question_no:
        warnings.append(_page_warn(
            BLOCK_EDIT_NOOP, f"块 {block_id!r} 的题号本来就是 {question_no!r} → 没动", "hint",
            card_id=block.get("card_id")))
        return _result(page, at=stamp, action="question_no", warnings=warnings, changed=False)

    was = block.get("question_no")
    updated = {**block, "question_no": question_no}
    blocks = [updated if b is block else b for b in blocks]
    page = _replace(page, blocks, at=stamp, action="question_no",
                    note=f"块 {block_id} 题号 {was!r} → {question_no!r}")
    return _result(page, at=stamp, action="question_no", warnings=warnings, changed=True,
                   blocks_changed=[block_id])


# ---------------------------------------------------------------- 统一的报告形状


def _iso(moment) -> str:
    return moment.isoformat()


def _result(page: dict, *, at: str, action: str, warnings: list[dict], changed: bool,
            blocks_changed=None, blocks_removed=None, decision=None) -> dict:
    """一次修正的结果。**每一条都回答「我做了什么、我没做什么」**（ADR 0007 第 6 条）。

    `page` 是**改完之后**的页（还没写盘——写盘由调用方做，`save_page` 只有一处实现）。
    `changed=False` 时 `page` **就是传进来的那一个**（一个字节都没改），
    所以「预演／没改动」与「真改了」在数据上分得开。
    """
    return {
        "action": action,
        "at": at,
        "changed": bool(changed),
        "page": page,
        "blocks_changed": list(blocks_changed or []),
        "blocks_removed": list(blocks_removed or []),
        "decision": decision,
        "warnings": warnings,
        # 修正**不写题卡**：题卡只在「入库」动作里生成（#15）。显式写出来，
        # 好让调用方与测试都能断言「这一次修正没动任何卡」。
        "wrote_cards": False,
        "wrote_page": False,
    }


def assert_page_payload_matches_id(catalog, page: dict, page_id: str) -> None:
    """写回/读图**之前**的不变式：页载荷里的 `id`／`image` 不许决定读写到哪。

    **判据不在这里实现**——`pages.require_page_identity`（#12 的数据安全修复）是唯一实现，
    本函数只是本模块的调用点，并且**把 `image` 也一起验掉**（那是同一条漏洞的派生症状：
    页文件里的 `image` 可以穿越到 `pages/` 外面去读文件）。

    ⚠ 两个形状都必须是**纯文件名**（D5：页文件与照片同目录并列）：

    - `id` 恒等于**加载时用的那个 page_id**（`pages.page_identity_ok` 判，且要求它是
      一个安全的文件名片段）；
    - `image` 是纯文件名：`basename == 它自己`（同时挡掉绝对路径、`..`、`a/b.png`），
      且能过 `pages.page_hash_from_image`（照片必须有后缀、不是隐藏文件）。

    为什么必须验：`pages.save_page` 曾经用 `page["id"]` 拼路径，于是一份文件名正常、
    载荷里 `id: "../problems/p-xxx"` 的页文件会让一次「改页」**覆盖一张真题卡**，
    而且报出的 `page_path` 是假的、零警告（#12 的独立验证发现，本工单复现并修掉）。
    现在 `save_page` 自己也强制这条不变式（`require_page_identity`）——**这里先验一道**，
    好在碰盘之前就给出本模块自己的 400，而不是等写到一半。
    """
    # 这一条判据的唯一实现在 `pages`（#12 收口）。它抛 400 `page_id_mismatch`。
    pages.require_page_identity(catalog, page, page_id)
    image = page.get("image")
    if image is None:
        return
    if (not isinstance(image, str) or Path(image).name != image
            or not pages.page_hash_from_image(image)):
        raise errors.bad_request(
            f"页文件里的 image 不是一个纯文件名：{image!r}",
            hint="页文件与整页照片同目录并列（D5），所以 `image` 只能是照片的文件名，"
                 "不许带目录、不许是绝对路径",
            param="page_id", value=image,
        )


def apply_edit(catalog, page_id, edits, *, at=None, apply: bool = True) -> dict:
    """把一串修正**按顺序**作用在页文件上，并（`apply=True` 时）写回。

    这是界面「每次修正都写回页文件」那条验收的落点（#14 验收 3）：
    修正 → 写页文件 → 索引/审计能看到变化。

    - `edits` 是 `{action, ...}` 的列表，`action` ∈ `EDITABLE_ACTIONS`。
      顺序有意义（先合并再拖边界 ≠ 先拖再合并），所以**逐条按序**作用，
      每条的结果累加到同一个页上。
    - **预演（`apply=False`）一个字节都不写**，但走的是同一条代码路径
      （`pages.save_page(..., apply=False)` 只算路径）——所以「预演说会改什么」
      与「真改了什么」不会分叉。
    - 页 id 非法 → 400、页不在 → 404、读不了 → 500：**拒绝路径只有 `intake._load_page` 一处**
      （页 id 会拼进路径，`#12` 收的那个穿越缺口在这里同样要过 `is_page_id`）。
    - 一条修正都没有（空列表）→ 明确报出来，不当作「成功改了 0 条」。
    """
    from .intake import _load_page      # 拒绝路径的唯一实现（复用，不重写）

    page = _load_page(catalog, page_id)
    # **写回路径只由「加载时用的 page_id」决定**：载荷里的 id／image 一旦对不上，
    # 就在这里拒绝（而不是让 save_page 按载荷里的 id 写到别处去）。
    assert_page_payload_matches_id(catalog, page, page_id)
    moment = at or datetime.now()
    results: list[dict] = []
    warnings: list[dict] = []
    for edit in edits or []:
        result = apply_one(page, edit, at=moment)
        results.append({key: value for key, value in result.items() if key != "warnings"})
        warnings.extend(result["warnings"])
        page = result["page"]

    changed = any(r["changed"] for r in results)
    if edits:
        # **每一次修正都写回页文件**（不是只存在界面里）——但只在真的有改动时写，
        # 免得「幂等重复提交」把页文件的 mtime 与审计面刷成新的一次。
        #
        # 写之前**再验一次**：上面的编辑链不该动到 `id`／`image`（它们不在任何动作的
        # 可改字段里），但"不该"不是证明。这里验的是**真正要写下去的那份载荷**，
        # 所以哪怕将来某个动作顺手改了 `id`，也不会写到 `pages/` 外面去。
        assert_page_payload_matches_id(catalog, page, page_id)
        # **写盘路径只由这个 page_id 决定**（`save_page` 的 `page_id` 是关键字、必填）
        pages.save_page(catalog, page, page_id=page_id, apply=bool(apply and changed))
    else:
        warnings.append(_page_warn(
            EDIT_EMPTY, "这次请求里一条修正都没有（`edits` 是空的）→ 页文件没动", "hint"))

    return {
        "page_id": page_id,
        "page_path": str(pages.page_path(catalog, page_id)),
        "apply": bool(apply),
        "preview": not apply,
        "changed": changed,
        "edits": results,
        "page": page,
        "warnings": warnings,
        "wrote_cards": False,
    }


# 动作名 → 实现。**一份分派表**：加动作只改这里（界面与 HTTP 层都照名字调）。
DISPATCH = {
    "move": lambda page, edit, at: move_block(page, edit.get("block_id"),
                                              edit.get("bbox_norm"), at=at),
    "merge": lambda page, edit, at: merge_blocks(page, edit.get("block_ids"), at=at),
    "split": lambda page, edit, at: split_block(page, edit.get("block_id"),
                                                edit.get("boxes"),
                                                question_numbers=edit.get("question_numbers"),
                                                at=at),
    "drop": lambda page, edit, at: drop_block(page, edit.get("block_id"), at=at),
    "keep": lambda page, edit, at: keep_block(page, edit.get("block_id"), at=at),
    "type": lambda page, edit, at: set_problem_type(page, edit.get("block_id"),
                                                   edit.get("problem_type"), at=at),
    "question_no": lambda page, edit, at: set_question_no(page, edit.get("block_id"),
                                                          edit.get("question_no"), at=at),
}


def apply_one(page: dict, edit, *, at=None) -> dict:
    """作用**一条**修正。动作名不认识 → 明确的拒绝形状，不是静默成功。"""
    moment = at or datetime.now()
    stamp = _iso(moment)
    if not isinstance(edit, dict):
        return _result(page, at=stamp, action=None, warnings=[_page_warn(
            EDIT_NOT_AN_OBJECT, f"修正项不是一个对象：{edit!r} → 页文件没动")],
            changed=False)
    action = edit.get("action")
    handler = DISPATCH.get(action)
    if handler is None:
        return _result(page, at=stamp, action=action, warnings=[_page_warn(
            EDIT_UNKNOWN_ACTION,
            f"不认识的修正动作 {action!r}（可取值：{list(EDITABLE_ACTIONS)}）→ 页文件没动")],
            changed=False)
    return handler(page, edit, moment)


def bad_request_unknown_action(action):
    """HTTP 层用它把「不认识的修正动作」变成 400（D1：拒绝一律 JSON 信封）。"""
    return errors.bad_request(
        f"不认识的修正动作：{action!r}",
        hint=f"可取值：{list(EDITABLE_ACTIONS)}",
        param="action", value=action, allowed=list(EDITABLE_ACTIONS),
    )
