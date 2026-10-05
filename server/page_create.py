"""页资源的「建」动作：照片 → 存图 + 建页文件 + 跑切分（spec #2 的第一个动作、工单 #15）。

| 动作 | 路由 | 归属 |
|---|---|---|
| **建** | `POST /api/page` | 本模块 |
| 改 | `PATCH /api/page/<id>` | #14（`server/page_edit.py`） |
| 重切 | `POST /api/page/<id>/resegment` | #10（`server/segmentation.py`） |
| 入库 | `POST /api/page/<id>/commit` | #15（`server/page_commit.py`） |

**请求形状 = `POST /api/inbox` 那一套**：`multipart/form-data`、字段名 `file`
（手机上传页与 `curl -F file=@照片.png` 都这么发）。一个文件 = 一页。

**页 id = 照片内容哈希的前 12 位**（与 #13 收件目录同一套口径）：所以同一张照片再传一次
就是同一个页 id，于是「建」天然幂等——第二次报 `page_already_exists`（hint），
**不重跑切分、不覆盖块列表**。块列表可能被人改过（拖边界、合并、去留），
「人动过的部分不允许被一次重切抹掉」是同一条理由。

## 三条纪律

1. **模型失败 → 502，且 `data/` 里一个字节都不留**（D1/D9）。照片先落进**系统临时目录**
   跑切分，切分成功了才写进数据目录——所以 `test_a_model_failure_leaves_not_one_byte_behind`
   能断言「连 `pages/` 目录都没被建出来」。「拒绝就该一个字节都不动」是 #13 踩出来的
   （它曾经先 `ensure()` 建目录再判空）。
2. **切分不可用 / 解析不出块都不是「这一页没有题」**：页文件**不建**、`blocks` 给 `null`
   （**不是 `[]`**），并报 `segmentation_not_implemented` / `page_segmentation_unparsed`。
3. **统计与去留不在这里判**：红笔像素归 #11（`ink.page_block_reports`），
   建议去留归 #12（`intake.plan_decisions`，`semantics=None` → 有红笔的块按「判不准 →
   收」落向收，并报 `intake_semantics_fallback` hint 说清「这次没问模型」）。
   **建只花一次模型调用：切分那一次**——红笔语义那一趟是 `python3 -m server.intake --apply`。

块边界的**规范基准是 `bbox_norm`**（模型给的整页归一化 xywh，D5）；`bbox_px` 是按照片
**真实像素尺寸**推出来的初值（整页 xyxy，供 #11 的红笔统计用）——回填那条路照抄盘上原值，
新页没有原值可抄，所以在这里算一次；#14 的拖边界会让它作废置 `null`。
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from datetime import datetime
from pathlib import Path

from . import coords, errors, inbox, ink, intake, pages, segmentation, subjects
from .model_client import ModelUnavailable
from .warnings import _warn

# 警告码（契约 §8）。`page_file_unreadable` / `page_segmentation_unparsed` 沿用 #9/#10 的码。
PAGE_ALREADY_EXISTS = "page_already_exists"              # hint：同一个页 id 已经在了
PAGE_FILE_UNREADABLE = "page_file_unreadable"            # warning：页在却读不了 → 不覆盖
SEGMENTATION_NOT_IMPLEMENTED = "segmentation_not_implemented"   # warning：#13/#14 同一个码


def _page_warn(code: str, message: str, level: str = "warning", *, card_id=None) -> dict:
    """警告一律走 `server/warnings.py: _warn`（BRIEF 硬规则 7：不许手搓 dict）。"""
    return _warn(code, message, card_id, level)


def as_candidates(outcome) -> dict:
    """切分接缝的返回值 → `parse_candidate_blocks` 的统一形状（**这一处实现**）。

    接缝可以给**模型原文**（走 `segmentation.parse_candidate_blocks` 那套抠 JSON 与
    逐条拒块的理由），也可以给已经解析好的对象（注入的假切分器）。两种都收：
    统一形状（带 `parsed` 与 `blocks`）原样返回，免得把 `rejected` 又数一遍。
    """
    if isinstance(outcome, (str, bytes)):
        return segmentation.parse_candidate_blocks(outcome)
    if isinstance(outcome, dict) and "parsed" in outcome and "blocks" in outcome:
        return outcome
    return segmentation.parse_candidate_blocks(outcome)


def page_id_for(blob: bytes) -> str:
    """照片字节 → 页 id（内容哈希前 12 位，与 #13 收件目录同一套命名）。"""
    return hashlib.sha256(blob).hexdigest()[:12]


def _iso(moment) -> str:
    return moment.isoformat(timespec="seconds")


def _page_blocks(candidates, image) -> list[dict]:
    """候选块 → **可以落进页文件**的块列表。

    只搬几何与题号（规范基准 `bbox_norm`），`card_id` / `keep` 都留空：
    id 在入库那一刻才分配，去留由 #12 判（这里只给建议）。`bbox_px` 按照片真实尺寸推。
    """
    blocks: list[dict] = []
    for index, candidate in enumerate(candidates or [], start=1):
        candidate = candidate if isinstance(candidate, dict) else {}
        box = candidate.get("bbox_norm")
        px = None
        if image is not None:
            scaled = coords.norm_to_px(box, width=image.width, height=image.height)
            xyxy = coords.xywh_to_xyxy(scaled) if scaled is not None else None
            # 像素框取整：像素没有小数（真实数据里的 bbox_px 也都是整数）。
            px = [int(round(v)) for v in xyxy] if xyxy else None
        blocks.append({
            "id": candidate.get("id") or f"b{index}",
            "bbox_norm": list(box) if isinstance(box, (list, tuple)) else box,
            "bbox_px": px,
            "question_no": candidate.get("question_no"),
            "card_id": None,
            "keep": None,
        })
    return blocks


def _write_photo(catalog, stored_as: str, blob: bytes) -> Path:
    """把整页照片写进 `data/pages/`（与页文件同目录并列，D5）。先临时文件再原子替换。"""
    catalog.pages_dir.mkdir(parents=True, exist_ok=True)
    path = catalog.pages_dir / stored_as
    tmp = path.with_name(f".{path.name}.part")
    tmp.write_bytes(blob)
    os.replace(tmp, path)
    return path


def _existing_row(catalog, page_id: str, page: dict, warning: dict) -> tuple[dict, list[dict]]:
    return {
        "page_id": page_id,
        "page_path": str(pages.page_path(catalog, page_id)),
        "image": page.get("image"),
        # 已经建过的那一页也要回显科目（同上：回执不回声，界面就只能靠再打一次索引去猜）
        "subject": page.get("subject"),
        "created": False,
        "existing": True,
        "segmentation": "skipped_existing",
        "blocks": page.get("blocks"),
        "message": "这一页已经建过了：不重跑切分、不覆盖块列表（块可能被人改过）",
    }, [warning]


def _field_text(parts: list[dict], name: str) -> str | None:
    """multipart 里的一个**文本**字段（没有文件名的那种）。空串算没给。"""
    for part in parts:
        if part.get("field") != name:
            continue
        raw = part.get("blob") or b""
        try:
            text = raw.decode("utf-8").strip()
        except UnicodeDecodeError:
            text = ""
        return text or None
    return None


def _check_subject(catalog, subject: str | None) -> tuple[str | None, list[dict]]:
    """科目由人在**录入时**指定（`CONTEXT.md`），取值必须来自受控词表。

    三条规矩，按代价排：

    1. **没给** → `None`，也就是未归类。那是一等状态，不是错误。
    2. **词表在** → 取值必须在里面，否则 **400 带可选值**。受控词表那条纪律是
       「AI 不得自造标签，找不到只能提名」；而人随时可以改词表，让一个表外的科目
       在侧栏里长出一根孤立分支，比当场拒掉更难收拾。
    3. **词表不在**（没建／读不了／空的）→ **收下这个科目**，但把那条词表级警告
       一并带回去。**不许因为词表不在就把录入整个挡住**：录入摩擦是这类工具的头号死因
       （ADR 0006 第 5 条），而人报的科目本身是权威读数——只是这一次没人能替它校验。
       代价是词表缺席期间可能积下拼错的科目；那正是索引级 `subjects_vocab_missing`
       要喊的事，也是词表补上之后 `subject_unknown` 会逐张指出来的事。
    """
    if not subject:
        return None, []
    vocabulary, vocab_warnings = subjects.load(catalog)
    if not vocabulary.get("loaded"):
        return subject, vocab_warnings
    known = list(vocabulary["subjects"])
    if subject not in known:
        raise errors.bad_request(
            f"科目 {subject!r} 不在受控词表里",
            hint="先把它加进 <数据目录>/vocab/subjects.json，或改成表里的一个；"
                 "不给这个字段也行——那就是「未归类」",
            param="subject", value=subject, allowed=known,
        )
    return subject, []


def _save_page_without_blocks(catalog, *, page_id, stored_as, part, subject, stamp, blob, note):
    """切分没跑成 / 不可用时的页：**照片落盘、页文件照建、`blocks = null`**。

    为什么这也建页（#23/#25）：切分不可用时最该发生的事是**让人在照片上自己画框**
    ——机器切分只是**预设**，不是唯一来源。页文件不建、照片不落盘，人就没有东西可画；
    那条「`null` 不是 `[]`」的纪律于是从「防误读」变成了「挡住唯一的出路」。

    三态不动，另加一个**来源**：`null` 继续表示「不知道」，`[]` 继续表示「确实没有题」，
    `segmentation.mode` 说这份块列表是谁给的。
    """
    page = {
        "version": pages.PAGE_VERSION,
        "id": page_id,
        "image": stored_as,
        "created_at": stamp,
        "origin": {
            "original_file": inbox._display_name(part.get("name")),
            "sheet": None,          # 哪张卷子：由录入的人填
            "page_number": None,    # 第几页：同上
        },
        "subject": subject,
        "segmentation": {"mode": "unavailable", "at": stamp, "note": note},
        "blocks": None,
    }
    _write_photo(catalog, stored_as, blob)
    pages.save_page(catalog, page, page_id=page_id, apply=True)
    return page


def _create_one(catalog, part: dict, segmenter, moment, stamp: str,
                subject: str | None = None) -> tuple[dict, list[dict]]:
    """一张照片 → 一页。返回 `(报告行, 警告)`。"""
    blob = part["blob"]
    page_id = page_id_for(blob)
    stored_as = f"{page_id}{inbox._suffix_of(part.get('name'))}"
    path = pages.page_path(catalog, page_id)

    page, read_error = pages.read_page(catalog, page_id)
    if read_error:
        # 页文件在却读不了 = 真矛盾：**不覆盖**、也不动照片（同回填那条纪律：先留证据）。
        return {
            "page_id": page_id, "page_path": str(path), "image": stored_as,
            "subject": subject,
            "created": False, "existing": True, "segmentation": "unreadable_page_file",
            "blocks": None, "message": read_error,
        }, [_page_warn(
            PAGE_FILE_UNREADABLE,
            f"{read_error} → 这张照片没有建页；不覆盖坏文件，先留证据（照片也没有写进去）")]
    if page is not None:
        return _existing_row(catalog, page_id, page, _page_warn(
            PAGE_ALREADY_EXISTS,
            f"这一页的内容已经建过了（页 id = 照片内容哈希前 12 位）：{path} 在 → "
            f"没有重复建，也没有重跑切分", "hint"))

    if segmenter is None:
        # 切分不可用**也要建页**（#23、契约 §10.2.1）。理由写在 `_save_page_without_blocks`。
        _save_page_without_blocks(
            catalog, page_id=page_id, stored_as=stored_as, part=part, subject=subject,
            stamp=stamp, blob=blob,
            note="切分不可用：块列表是 null（不是「这一页没有题」），等人手动画框")
        return {
            "page_id": page_id, "page_path": str(path), "image": stored_as,
            "subject": subject,
            "created": True, "existing": False, "segmentation": "unavailable",
            "blocks": None,
            "message": "切分不可用 → 页建好了、块列表是 null；请在这张照片上手动画框"
                       "（不是「这一页没有题」）",
        }, []

    # 到这里才开始碰盘，而且碰的是**系统临时目录**：模型失败时数据目录一个字节都不留。
    handle = tempfile.NamedTemporaryFile(delete=False, suffix=Path(stored_as).suffix)
    try:
        handle.write(blob)
        handle.flush()
        handle.close()
        image = None
        warnings: list[dict] = []
        try:
            image = ink.read_png(handle.name)
        except ink.UnsupportedImage as exc:
            # 读不了图（不是 PNG / 损坏）→ 红笔统计做不了。**不许当成「没有红笔」**（#11/#12）。
            warnings.append(_page_warn(
                intake.INTAKE_PAGE_IMAGE_UNREADABLE,
                f"整页照片读不了（{part.get('name') or stored_as}）：{exc} → "
                f"这一页的红笔统计做不了，全部待定（未入库）"))
        try:
            outcome = segmenter(handle.name)
        except ModelUnavailable as exc:
            raise errors.model_unavailable(str(exc), pid=page_id) from exc
    finally:
        Path(handle.name).unlink(missing_ok=True)

    parsed = as_candidates(outcome)
    warnings.extend(parsed.get("warnings") or [])
    if not parsed.get("parsed", True):
        # 答了话却抠不出块：这是「切分没跑成」，**不是**「这一页没有题」。
        # 但页**照建**（#23/#25）：模型切坏时人的出路正是自己画框，而画框要有照片和页。
        _save_page_without_blocks(
            catalog, page_id=page_id, stored_as=stored_as, part=part, subject=subject,
            stamp=stamp, blob=blob,
            note=f"切分跑了但抠不出块（{parsed.get('message')}）：块列表是 null，等人手动画框")
        return {
            "page_id": page_id, "page_path": str(path), "image": stored_as,
            "subject": subject,
            "created": True, "existing": False, "segmentation": "unparsed",
            "blocks": None, "message": parsed.get("message"),
        }, warnings

    blocks = _page_blocks(parsed.get("blocks"), image)
    page = {
        "version": pages.PAGE_VERSION,
        "id": page_id,
        "image": stored_as,
        "created_at": stamp,
        "origin": {
            "original_file": inbox._display_name(part.get("name")),
            "sheet": None,          # 哪张卷子：由录入的人填（#13 的收件目录那条路也没有）
            "page_number": None,    # 第几页：同上
        },
        "subject": subject,
        # 这一份块列表的来源：机器切出来的**预设**（#25）。
        # 人在上面增删改之后 `mode` 会变 `manual`（`page_edit` 负责），
        # 重跑切分（`resegment`，语义是「重置为预设」）会把它变回 `model`。
        "segmentation": {"mode": "model", "at": stamp, "note": None},
        "blocks": blocks,
    }
    reports = ink.page_block_reports(image, page) if image is not None else []
    plan = intake.plan_decisions(page, ink_reports=reports, semantics=None, at=moment)
    saved = plan["page"]
    # 照片先落盘、页文件后写：页不会先于它的照片存在（页实体指向一张取不到的图是矛盾）。
    _write_photo(catalog, stored_as, blob)
    pages.save_page(catalog, saved, page_id=page_id, apply=True)

    # 对账（R1）：三条确定性判据跑在**刚落盘的块列表**上，把「静默丢题」变成响声。
    # **只报不改**（切分仍由人确认）；墨迹区域读不出来时覆盖率那一条会明说「没查」。
    reconciliation = segmentation.reconcile_response(
        saved["blocks"], ink.page_ink_regions(image) if image is not None else None)

    return {
        "page_id": page_id,
        "page_path": str(path),
        "image": stored_as,
        # 回显科目：界面提交了科目就得在回执里看见它被收下了，否则「我填的科目到底进没进去」
        # 只能靠再打一次索引去猜。没给就是 `null`（**未归类**，不是缺字段）。
        "subject": subject,
        "created": True,
        "existing": False,
        "segmentation": "ran",
        "blocks": saved["blocks"],
        "rejected": parsed.get("rejected") or [],
        "message": parsed.get("message"),
        "counts": plan["counts"],
        "not_kept": plan["not_kept"],
        "checks": reconciliation["checks"],
        "reconciliation": reconciliation["reconciliation"],
    }, warnings + list(plan["warnings"]) + reconciliation["warnings"]


def create_pages(catalog, *, body, content_type, segmenter, at=None) -> tuple[dict, list[dict]]:
    """`POST /api/page` 的全部逻辑。返回 `(data, warnings)`。

    **拒绝路径一个字节都不动**（D9）：不是 multipart / 没有 `file` 段 / 全是空文件 → 400，
    全都发生在 `pages_dir.mkdir` 之前。
    """
    # body 归一化成 bytes（与 `http._inbox_upload` 同一条讲究）：不经这一步，一个 str body
    # 会在 `parse_multipart` 的字节拼接上抛 TypeError → 兜底 500，而 D1 要的是 400。
    if isinstance(body, str):
        body = body.encode("utf-8")
    parts = inbox.parse_multipart(body, content_type)
    files = inbox.upload_files(parts)
    # 科目在**录入时**由人指定（`CONTEXT.md`）：取值必须在受控词表里，不在就 400；
    # 词表本身不在时收下并带警告（理由见 `_check_subject`）。
    # 这一条判在**碰盘之前**——拒绝路径一个字节都不动（D9）。
    subject, subject_warnings = _check_subject(catalog, _field_text(parts, "subject"))
    moment = at or datetime.now()
    stamp = _iso(moment)
    status = inbox.segmentation_status(segmenter)

    rows: list[dict] = []
    warnings: list[dict] = list(subject_warnings)
    created: list[str] = []
    existing: list[str] = []
    for part in files:
        row, more = _create_one(catalog, part, segmenter, moment, stamp, subject)
        rows.append(row)
        warnings.extend(more)
        if row.get("page_id"):
            (created if row.get("created") else existing).append(row["page_id"])
    if not status["available"] and rows:
        # 只有**真有页要建**时才喊（空扫一遍还喊会训练人忽略警告，proto/server.py:1046）。
        warnings.insert(0, _page_warn(SEGMENTATION_NOT_IMPLEMENTED, status["message"]))

    return {
        "pages": rows,
        "created": created,
        "existing": existing,
        "segmentation": status,
        "wrote_pages": bool(created),
    }, warnings


__all__ = ["create_pages", "as_candidates", "page_id_for", "PAGE_ALREADY_EXISTS",
           "PAGE_FILE_UNREADABLE", "SEGMENTATION_NOT_IMPLEMENTED"]
