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

import json
import re
from pathlib import Path

PAGE_VERSION = 1

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
