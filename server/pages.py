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

import hashlib
import json
import re
from pathlib import Path

PAGE_VERSION = 1

# 页 id 会直接变成文件名，所以它必须是一个安全的文件名片段。
# 与 `catalog.ID_PATTERN` / `assets._NAME_RE` 同一套纵深防御（服务将来要经 Tailscale
# 暴露给手机，#13），只是这里更松：不要求 12 位十六进制——照片叫 `photo1.png` 也是合法的
# 一页，不能因为命名习惯把它判成「没有页」。
PAGE_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def is_page_id(page_id) -> bool:
    """这个字符串能不能当页 id（＝页文件名的主干）。**取页 id 的入口必须先问它**。

    为什么单列出来：页 id 会拼进路径（`<data>/pages/<id>.json`），而 #12 的收入决策
    是第一个从**外面**拿页 id 的入口。`../../problems/p-xxx` 这种 id 会让读打到
    `pages/` 外面、**写回则可能覆盖一张真题卡**。所以校验与「能不能当文件名」
    共用同一份判据（`page_hash_from_image` 也读它），不许各处再写一遍。
    """
    return isinstance(page_id, str) and bool(PAGE_ID_PATTERN.fullmatch(page_id))


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
    if not stem or stem.startswith(".") or not is_page_id(stem):
        return None
    return stem


def _is_box(value) -> bool:
    """一个四元数值框（不校验语义：xywh 与 xyxy 的形状都是四个数）。"""
    return (
        isinstance(value, (list, tuple))
        and len(value) == 4
        and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
    )


def page_path(catalog, page_id: str) -> Path:
    """页文件在盘上的位置：`<data>/pages/<page_id>.json`（与照片同目录并列）。"""
    return catalog.pages_dir / f"{page_id}.json"


def read_page(catalog, page_id: str) -> tuple[dict | None, str | None]:
    """读一个页文件：`(页, 错误原话)`。

    - 不在盘上 → `(None, None)`（这是「旧卡还没回填」，不是错误）
    - 在但读不了 / 不是 JSON 对象 → `(None, 原话)`（这是矛盾，调用方要喊）

    两种「没有页」分开，是因为它们的严重级别不同（#9 验收 2 与 #15 的审计扩展）。
    """
    path = page_path(catalog, page_id)
    if not path.is_file():
        return None, None
    try:
        page = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"{path} 读不了：{exc.__class__.__name__}: {exc}"
    if not isinstance(page, dict):
        return None, f"{path} 不是一个 JSON 对象"
    return page, None


def save_page(catalog, page: dict, *, apply: bool = True) -> Path:
    """写页文件（先写临时文件再原子替换，免得写一半留下半个页）。

    `apply=False` 只算出路径、不碰盘——预演与真写走同一条代码路径，
    所以「预演说会建什么」与「真写建了什么」不会分叉。
    """
    path = page_path(catalog, page["id"])
    if not apply:
        return path
    catalog.pages_dir.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text(json.dumps(page, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def _new_page(page_id: str, card: dict) -> dict:
    """一个页文件的骨架。整页照片只存**文件名**：页文件与它同目录并列（D5）。"""
    source = card.get("source") or {}
    return {
        "version": PAGE_VERSION,
        "id": page_id,
        "image": Path(source.get("page_image") or "").name,
        "created_at": card.get("created_at"),
        "origin": {
            "original_file": source.get("original_file"),
            "sheet": None,          # 哪张卷子：由录入的人填（#13），存量数据没有
            "page_number": None,    # 第几页：同上
        },
        "blocks": [],
    }


def _block_from_card(card: dict, index: int) -> tuple[dict, list[str]]:
    """一张存量卡 → 页文件里的一个块（照片 + 单个块 = 那张卡的边界）。

    `bbox_px` **照抄盘上的原值**，不用 `bbox_norm` 重算：两者形状不同
    （xywh vs xyxy），且盘上那个是加了 1.5% pad 又裁到页边界的结果（`proto/slice.py:544`）。
    """
    source = card.get("source") or {}
    notes: list[str] = []
    bbox_norm = source.get("bbox_norm")
    if not _is_box(bbox_norm):
        notes.append("卡里没记可用的 bbox_norm（整页归一化边界）→ 这一块退化为整页")
        bbox_norm = [0.0, 0.0, 1.0, 1.0]
    bbox_px = source.get("bbox_px")
    if not _is_box(bbox_px):
        bbox_px = None
    return (
        {
            "id": f"b{index}",
            "bbox_norm": [float(v) for v in bbox_norm],
            "bbox_px": list(bbox_px) if bbox_px is not None else None,
            "card_id": card.get("id"),
            "keep": True,   # 卡片已经在库里 = 它当初就是「收」进来的
        },
        notes,
    )


def _card_report(pid: str | None, action: str, page_id: str | None, message: str) -> dict:
    return {"id": pid, "action": action, "page_id": page_id, "message": message}


def backfill_pages(catalog, *, apply: bool = False) -> dict:
    """存量题卡 → 页文件（spec #2「存量数据要能进来」、#9 验收 1）。

    **幂等**：跑两次不产生第二份页文件、不覆盖已有绑定、不改动题卡上的任何字段。
    已经记在页文件里的绑定谁也动不了——只会往页文件里补它还没有的块，
    也**只按 `source.page_image` 归页**，所以一页多题的存量数据会汇进同一个页文件。

    `apply=False` 是预演：报告里写清「会建 / 会加 / 不动」哪些页，一个字节都不写。
    两种「没有页」的严重级别不同（#9 验收 2）：盘上没有页文件 = 旧数据，**提示**；
    页文件在却读不了 = 矛盾，**警告**且不覆盖（先留证据）。
    """
    reports: list[dict] = []
    warnings: list[dict] = []
    pages_by_id: dict[str, dict | None] = {}
    page_errors: dict[str, str] = {}
    page_paths: dict[str, Path] = {}
    dirty: set[str] = set()
    photo_checked: set[str] = set()

    for path in sorted(catalog.problems_dir.glob("*.json")):
        try:
            card = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            reports.append(_card_report(path.stem, "skipped", None,
                                        f"{path.name} 读不了：{exc.__class__.__name__}: {exc}"))
            continue
        if not isinstance(card, dict) or not card.get("id"):
            reports.append(_card_report(path.stem, "skipped", None,
                                        f"{path.name} 不是一个带 id 的题卡对象"))
            continue
        pid = card["id"]

        page_id = page_hash_from_image((card.get("source") or {}).get("page_image"))
        if not page_id:
            reports.append(_card_report(
                pid, "skipped", None,
                "卡里没记整页照片（source.page_image）→ 反推不出页文件，跳过（不是错误）"))
            continue

        if page_id not in pages_by_id and page_id not in page_errors:
            page, error = read_page(catalog, page_id)
            page_paths[page_id] = page_path(catalog, page_id)
            pages_by_id[page_id] = page
            if error:
                page_errors[page_id] = error
                warnings.append({
                    "code": "page_file_unreadable",
                    "message": f"{error} → 这张卡没有回填；不覆盖坏文件，先留证据",
                    "id": pid,
                })

        if page_id in page_errors:
            reports.append(_card_report(pid, "skipped", page_id, page_errors[page_id]))
            continue

        page = pages_by_id[page_id]
        fresh = page is None
        if fresh:
            page = _new_page(page_id, card)
            pages_by_id[page_id] = page

        if page_id not in photo_checked:
            photo_checked.add(page_id)
            image_name = page.get("image") or ""
            if not image_name or not (catalog.pages_dir / image_name).is_file():
                warnings.append({
                    "code": "page_photo_missing",
                    "message": f"页 {page_id} 的整页照片不在：{catalog.pages_dir / image_name}"
                               f" → 这个页绑定指向一张取不到的图",
                    "id": pid,
                })

        if any(b.get("card_id") == pid for b in page["blocks"]):
            reports.append(_card_report(pid, "unchanged", page_id,
                                        "页文件里已经有这张卡的绑定，不动"))
            continue

        block, notes = _block_from_card(card, len(page["blocks"]) + 1)
        page["blocks"].append(block)
        dirty.add(page_id)
        for note in notes:
            warnings.append({"code": "block_box_fallback",
                             "message": f"{pid}：{note}", "id": pid})
        reports.append(_card_report(
            pid, "created" if fresh else "appended", page_id,
            "建了页文件，块 = 这张卡的边界" if fresh else "页文件已存在，追加一个块"))

    for page_id in sorted(dirty):
        page = pages_by_id[page_id]
        if page is not None:
            save_page(catalog, page, apply=apply)

    counts: dict[str, int] = {}
    for report in reports:
        counts[report["action"]] = counts.get(report["action"], 0) + 1
    live_pages = [pid for pid, page in sorted(pages_by_id.items()) if page is not None]
    return {
        "apply": apply,
        "cards": reports,
        "pages": [
            {
                "id": pid,
                "path": str(page_paths.get(pid, page_path(catalog, pid))),
                "blocks": len(pages_by_id[pid]["blocks"]),
                "changed": pid in dirty,
            }
            for pid in live_pages
        ],
        "summary": {
            "cards_seen": len(reports),
            "created": counts.get("created", 0),
            "appended": counts.get("appended", 0),
            "unchanged": counts.get("unchanged", 0),
            "skipped": counts.get("skipped", 0),
            "pages": len(live_pages),
            "pages_changed": len(dirty),
        },
        "warnings": warnings,
    }


# 「这张卡有没有页绑定」的两个码与两个级别（#9 验收 2；#15 的审计在它上面扩展）。
# 级别只有服务能定（契约 §2），界面不许自行升降级。
PAGE_BINDING_MISSING = "page_binding_missing"   # 提示级：旧数据（页文件还没建）
PAGE_BINDING_LOST = "page_binding_lost"         # 警告级：页实体在场却对不上账


def _binding(bound: bool, code: str | None, level: str | None, reason: str | None,
             page_id: str | None, path: Path | None, message: str) -> dict:
    return {
        "bound": bound,
        "code": code,
        "level": level,
        "reason": reason,
        "page_id": page_id,
        "page_path": str(path) if path is not None else None,
        "message": message,
    }


def page_binding(catalog, card: dict) -> dict:
    """「这张卡有没有页绑定」的**唯一实现**（`card_warnings` 与 #15 的审计都消费它）。

    返回 `{bound, code, level, reason, page_id, page_path, message}`；绑定时 `code` 为 `None`。

    两种「没绑定」的级别不同，因为它们的性质不同：

    - 盘上**没有**页文件（或卡里根本没记整页照片）→ `page_binding_missing`，**提示**：
      这是旧数据（存量卡至今没有页文件），不该因为新结构变成脏数据。
    - 页文件**在**却读不了、或里面没有任何块绑定这张卡 → `page_binding_lost`，**警告**：
      页实体已经在场，本该有绑定却对不上账，这是矛盾。

    ⚠ **这条检查能抓住的，永远只是它判据覆盖的那一部分**：绑定只记在页文件里，所以
    「页文件被整个删掉」与「旧卡从没有过页文件」在盘上长得一样——前者只能报提示。
    """
    pid = card.get("id")
    page_id = page_hash_from_image((card.get("source") or {}).get("page_image"))
    if not page_id:
        return _binding(False, PAGE_BINDING_MISSING, "hint", "no_page_image", None, None,
                        "卡里没记整页照片（source.page_image 为空/不可用）→ 推不出页文件，"
                        "回填也无从下手")
    path = page_path(catalog, page_id)
    page, error = read_page(catalog, page_id)
    if error:
        return _binding(False, PAGE_BINDING_LOST, "warning", "page_file_unreadable",
                        page_id, path, f"{error} → 页实体在，却读不出绑定")
    if page is None:
        return _binding(False, PAGE_BINDING_MISSING, "hint", "page_file_missing",
                        page_id, path,
                        f"按 source.page_image 推出来的页文件 {path} 不在 → 这是还没回填的旧数据"
                        f"（可以回填，不是错误）")
    blocks = page.get("blocks")
    blocks = blocks if isinstance(blocks, list) else []
    if any(isinstance(b, dict) and b.get("card_id") == pid for b in blocks):
        return _binding(True, None, None, None, page_id, path, f"已绑定在页 {page_id} 上")
    return _binding(False, PAGE_BINDING_LOST, "warning", "card_not_bound", page_id, path,
                    f"页文件 {path} 在，但里面没有任何块绑定这张卡 → 页↔卡对不上账")


# 「位置重合度」的判据 spec 没给公式（spec-2 笔记 §C 待确认），口径的**最终裁决在 #10**。
# 裁决结论（理由逐条写在 `rebind` 的 docstring 里）：
#
#   · 主判据仍是 **IoU**，阈值 **0.5** 不变。IoU 对称、对「凭空长大」与「凭空缩小」
#     一样敏感，而且 #9 已经用**手算的几何**把它钉在了两条边界上（完全重合 → 1.0；
#     部分重叠 0.2857 → 不是同一块）。这条边界本工单不动。
#   · 但纯 IoU 会**系统性漏掉**「同一个块、边界被重切细化了」：新框整个落在旧框里时
#     IoU = 新面积 / 旧面积，缩到一半以下就判成「旧的消失 + 新的出现」，
#     于是把一张可能已审核的卡孤立掉——正是 spec #2 最怕的那类失败
#     （「人动过的卡片不允许被一次重切抹掉」）。
#   · 所以补一条**包含**判据：重叠系数（交 / 较小那块）≥ `MATCH_CONTAIN` 也算同一个块。
#     它只放宽「一个框整个在另一个框里」这一种形状，「部分重叠但谁也不包含谁」不受影响
#     （#9 钉住的 0.2857 那例的重叠系数是 0.5 < 0.8，仍然不配——那条测试保持绿）。
MATCH_IOU = 0.5
MATCH_CONTAIN = 0.8
# 匹配上的块要从旧块继承的键 = **人动过**的那些：绑定与去留（#14 验收 2）。
# 几何（bbox_*）当然用新的；题号与红笔统计是切分/统计的产物，重新算，不继承。
PRESERVED_KEYS = ("card_id", "keep")


def _xywh(box):
    """一个可用的整页归一化框 → `(x, y, w, h)`；读不出来（或退化）→ `None`。"""
    if not _is_box(box):
        return None
    x, y, w, h = (float(v) for v in box)
    if w <= 0 or h <= 0:
        return None
    return x, y, w, h


def usable_box(box) -> bool:
    """这个框读得出来吗——**框的形状判据只有这一处**。

    `#10` 的对账（重叠、覆盖率）要能区分「两个框不相交」与「这个框根本读不出来」：
    `iou()` 对两种情况都返回 `0.0`（不猜），所以调用方必须先用这个函数把坏框挑出来
    显式报掉（ADR 0007 第 6 条不许静默），而不是让它们混进「没有重叠」里。
    """
    return _xywh(box) is not None


def iou(a, b) -> float:
    """两个**整页归一化** xywh 框的交并比。任一个读不出来 → `0.0`（不猜）。"""
    ra, rb = _xywh(a), _xywh(b)
    if ra is None or rb is None:
        return 0.0
    ax, ay, aw, ah = ra
    bx, by, bw, bh = rb
    ix = min(ax + aw, bx + bw) - max(ax, bx)
    iy = min(ay + ah, by + bh) - max(ay, by)
    if ix <= 0 or iy <= 0:
        return 0.0
    inter = ix * iy
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


def overlap_coefficient(a, b) -> float:
    """重叠系数 = 交 / **较小那块**的面积（也叫包含度）。读不出来 → `0.0`。

    与 `iou` 的分工：`iou` 惩罚面积变化（包含关系下 IoU = 小/大），
    `overlap_coefficient` 只问「小的是不是整个在大块里」。重切把同一个块的边界
    细化（把上一题的手写解答从框里剔出去）时，后者才认得出是同一个块——
    判据的裁决理由在 `MATCH_IOU / MATCH_CONTAIN` 那段注释与 `rebind` 的 docstring 里。
    """
    ra, rb = _xywh(a), _xywh(b)
    if ra is None or rb is None:
        return 0.0
    ax, ay, aw, ah = ra
    bx, by, bw, bh = rb
    ix = min(ax + aw, bx + bw) - max(ax, bx)
    iy = min(ay + ah, by + bh) - max(ay, by)
    if ix <= 0 or iy <= 0:
        return 0.0
    smaller = min(aw * ah, bw * bh)
    return (ix * iy) / smaller if smaller > 0 else 0.0


def rebind(old_blocks, new_blocks, *, iou_threshold: float = MATCH_IOU,
           contain_threshold: float = MATCH_CONTAIN) -> dict:
    """重切对账的**唯一**匹配逻辑：按位置重合度把新旧块配上，配上就**保留原有绑定**。

    - 一对一**贪心**：所有够格的（新, 旧）块对按「配得有多好」从大到小排序，依次配对
      （同分时按块序定序）。确定性、可测：同样的输入永远同样的输出。
    - 配上的新块继承旧块的 `PRESERVED_KEYS`——所以**重切不会给已审核、已重做过的
      卡片改名**，也不会把人定过的去留抹掉（spec #2 的目标句）。
    - 配不上的新块 `card_id` 为 `None`（**分配新 id 发生在入库那一刻**，
      `assign_card_ids`），除非调用方在候选块上塞了一个绑定——那种块**不认**它，
      由 #10 的对账显式报出来（候选块不该带绑定）。
    - 没被任何新块配上的旧块进 `removed`；**带着卡片的必须喊**——「人动过的卡片
      不允许被一次重切抹掉」（spec #2）。

    **口径的最终裁决（#10）**：够格 = `iou >= iou_threshold`（0.5）
    **或** `overlap_coefficient >= contain_threshold`（0.8）。

    为什么是这两条：匹配要回答的是「重切之后这个新块还是不是原来那个块」，
    它的目的是**保住人动过的绑定**，不是衡量检测质量。IoU 对称、对面积变化敏感，
    是主判据；但它有一个系统性盲点——新框整个落在旧框里时 IoU = 新/旧，
    重切把上一题的手写解答从框里剔出去（**这正是重切该做的事**）就会把 IoU 打到
    0.5 以下，于是「同一个块」被判成「旧的消失 + 新的出现」，一张可能已审核的卡
    就此被孤立。包含判据补的正是这一种形状：小框整个在大框里 = 同一个块被细化。
    它不放宽「部分重叠但谁也不包含谁」——`#9` 用手算几何钉住的 0.2857 那例
    重叠系数只有 0.5，仍然不配（`server/tests/test_pages.py` 那条测试保持绿）。

    代价（写在明处）：位置是这里唯一的信息，所以「一道新题恰好整个落在旧框里」
    会被当成同一个块。它不会安静——那一页若真有重叠，`#10` 的 `block_overlap`
    判据会另外喊；而对照里这一块显示「保留」，人在界面上能看见并改绑。
    反过来（把同一个块当成新块）要赔上一张卡的绑定，代价不对称，所以偏前者。

    本函数**不写盘、不写题卡**，只给事实：`matches`（与 `blocks` 同序的逐块对照，
    给出 `matched_from`、`iou` 与 `contain`）与 `removed`。界面上「新增／替换／保留」
    怎么措辞由 #10 定（它在这些事实上做三态映射，见 `server/segmentation.py`）。

    对照事实**放在 `matches` 里、不放进块**：块会长成页文件的一行，而
    `matched_from` 下一次重切就过期了——把过期事实写进存档文件，正是这个项目
    最怕的那种「安静的谎」。

    ⚠ 已知弱点：匹配的判据只有**位置**。块被大幅拖动（重合度跌到两条阈值以下）
    会被算成「旧的消失 + 新的出现」而不是「同一个块被移动」——所以消失的块
    带着卡片时会喊。
    """
    warnings: list[dict] = []

    old_list = []
    for old in old_blocks or []:
        if not isinstance(old, dict):
            warnings.append({"code": "block_not_an_object",
                             "message": f"旧块列表里有一项不是对象：{old!r}"})
            continue
        if _xywh(old.get("bbox_norm")) is None:
            warnings.append({"code": "block_without_box", "id": old.get("id"),
                             "message": f"旧块 {old.get('id')!r} 没有可用的 bbox_norm"
                                        f"（整页归一化边界）→ 无法按位置匹配"})
        old_list.append(old)

    new_list: list[dict] = []
    for new in new_blocks or []:
        if not isinstance(new, dict):
            warnings.append({"code": "block_not_an_object",
                             "message": f"新块列表里有一项不是对象：{new!r}"})
            continue
        if _xywh(new.get("bbox_norm")) is None:
            warnings.append({"code": "block_without_box", "id": new.get("id"),
                             "message": f"新块 {new.get('id')!r} 没有可用的 bbox_norm"
                                        f" → 无法按位置匹配"})
        new_list.append(new)

    matchable = [i for i, block in enumerate(old_list) if _xywh(block.get("bbox_norm"))]
    pairs = []
    for slot, old_index in enumerate(matchable):
        for new_index, new in enumerate(new_list):
            score = iou(old_list[old_index].get("bbox_norm"), new.get("bbox_norm"))
            contain = overlap_coefficient(old_list[old_index].get("bbox_norm"),
                                          new.get("bbox_norm"))
            if score >= iou_threshold or contain >= contain_threshold:
                # 排序用「配得有多好」= 两条判据里更强的那条（IoU 与包含度同量纲、都是 0~1）
                pairs.append((max(score, contain), slot, new_index))
    pairs.sort(key=lambda p: (-p[0], p[1], p[2]))

    matched_old: set[int] = set()
    matched_new: dict[int, tuple[dict, float, float]] = {}
    for _, slot, new_index in pairs:
        old_index = matchable[slot]
        if old_index in matched_old or new_index in matched_new:
            continue
        matched_old.add(old_index)
        old = old_list[old_index]
        matched_new[new_index] = (old,
                                  iou(old.get("bbox_norm"), new_list[new_index].get("bbox_norm")),
                                  overlap_coefficient(old.get("bbox_norm"),
                                                      new_list[new_index].get("bbox_norm")))

    blocks: list[dict] = []
    matches: list[dict] = []
    for new_index, new in enumerate(new_list):
        block = {**new}
        if new_index in matched_new:
            old, score, contain = matched_new[new_index]
            for key in PRESERVED_KEYS:
                block[key] = old.get(key)
            matches.append({"block_id": block.get("id"),
                            "matched_from": old.get("id"), "iou": score,
                            "contain": contain})
        else:
            for key in PRESERVED_KEYS:
                block.setdefault(key, None)
            matches.append({"block_id": block.get("id"),
                            "matched_from": None, "iou": None, "contain": None})
        blocks.append(block)

    removed: list[dict] = []
    for old_index, old in enumerate(old_list):
        if old_index in matched_old:
            continue
        removed.append({
            "id": old.get("id"),
            "card_id": old.get("card_id"),
            "keep": old.get("keep"),
            "bbox_norm": old.get("bbox_norm"),
        })
        if old.get("card_id"):
            warnings.append({
                "code": "block_removed_with_card",
                "id": old.get("card_id"),
                "message": f"块 {old.get('id')!r} 在新切分里找不到位置重合的块，"
                           f"但它绑着卡片 {old['card_id']} → 卡片不会被自动抹掉，先人工确认",
            })

    return {
        "blocks": blocks,
        "matches": matches,
        "removed": removed,
        "summary": {
            "matched": len(matched_new),
            "new": len(blocks) - len(matched_new),
            "removed": len(removed),
            "removed_with_card": sum(1 for r in removed if r["card_id"]),
        },
        "warnings": warnings,
    }


def allocate_card_id(taken, *, at, seed: str) -> str:
    """块的身份 → 一个**还没被占用**的题卡 id（spec #2：id 在首次入库时分配）。

    形状沿用存量习惯 `p-<YYYYMMDD>-<6hex>`（真实数据：`p-20261004-41c86b`）。
    日期是**入库日**，不是拍照日、不是凭证照片的时间——spec-2 笔记 §C 专门点了这个坑。

    候选由 `seed` 决定（调用方给块的身份，如 `<页 id>#<块 id>`），**唯一性由 `taken`
    保证**：候选在 `taken` 里就换下一个。所以 id 是「块」的函数，不再是「一张照片」
    的函数——一页多题因此不会互撞（#9 验收 3）。
    """
    day = at.strftime("%Y%m%d")
    attempt = 0
    while True:
        digest = hashlib.sha1(f"{seed}|{attempt}".encode("utf-8")).hexdigest()
        pid = f"p-{day}-{digest[:6]}"
        if pid not in taken:
            return pid
        attempt += 1


def assign_card_ids(page: dict, *, taken, at) -> dict:
    """给页里**还没有绑定**的块首次分配题卡 id（spec #2「入库」动作的分配那一半）。

    返回 `{"page": 新页, "assigned": [...], "kept": [...]}`：

    - `assigned` = 这一次分配出去的（每块一个，互不相同，也不与 `taken` 里已有的撞）；
    - `kept` = 本来就有绑定的块——**不重复生成**（#15 的入库幂等靠这一条，
      也是「重切不会把已审核、已重做过的卡片改名」在数据上的落点）。

    不改动传进来的 `page`、不写盘：分配是「决定」，写回页文件是调用方的事。
    #15 拿到这里的 `assigned` 去建卡，然后 `save_page`。
    """
    seen = {str(t) for t in taken or []}
    blocks: list[dict] = []
    assigned: list[dict] = []
    kept: list[dict] = []
    for index, block in enumerate(page.get("blocks") or [], start=1):
        block = dict(block) if isinstance(block, dict) else {}
        existing = block.get("card_id")
        if existing:
            kept.append({"block_id": block.get("id"), "card_id": existing})
        else:
            block_id = block.get("id") or f"b{index}"
            pid = allocate_card_id(seen, at=at, seed=f"{page.get('id')}#{block_id}")
            seen.add(pid)
            block["card_id"] = pid
            block.setdefault("id", block_id)
            assigned.append({"block_id": block_id, "card_id": pid})
        blocks.append(block)
    return {"page": {**page, "blocks": blocks}, "assigned": assigned, "kept": kept}
