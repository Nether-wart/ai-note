"""页资源的「入库」动作：按块列表生成题卡 + 回写绑定（spec #2 的第四个动作、工单 #15）。

spec #2 原文：「**入库**：按当前块列表生成题卡（每块一个），并回写绑定；**已经生成过的块
不重复生成**。」本模块是那句话的唯一实现，`POST /api/page/<id>/commit` 是它的 HTTP 形状
（`server/page_api.py` 只做翻译，不实现规则）。

## 只给「收」的块发卡号

`keep is True` 的块才有卡：`keep is false`（明确不收）与 `keep is null`（统计读不出来，
待定）都进 `skipped[]`，**连 id 都不分配**——给丢弃的块发一个卡号就是造一条幽灵绑定，
而「为什么没入库」正是页文件要回答的问题（#12 的 `decision` 已经记着理由）。

题卡 id **首次入库时分配**（spec #2：id 与页面哈希解耦），分配实现只有一处：
`pages.assign_card_ids`（seed = `<页 id>#<块 id>`，唯一性在「盘上已有的 id 集合」上做
碰撞消解）。所以一页多块各得一个互不相同的 id，而重切不会给已审核的卡改名。

## 顺序：先写页，再建卡（这条顺序是幂等的一部分）

绑定先落进页文件，再按绑定建卡。中断（或卡写失败）之后重跑时，块上已经有 `card_id`，
第二次走 `reused` 那条路，**id 不会变**——若反过来先建卡，重跑会因为候选 id 已被占用
而碰撞到下一个候选，于是同一块换了一个 id，幂等就破了。

半途而废的那个状态是**看得见**的：页里记着绑定、卡不在 → 审计的 `page_card_missing`
（warning）会喊出来，重跑一次 `commit` 又能补齐（`test_a_page_that_says_a_card_exists_...`）。

## 骨架卡：题面/标准答案/擦除图都还没有

块上只有 `bbox_norm` / `question_no`（#10 的候选块形状），所以入库建的是**骨架卡**：
`review.status = "unreviewed"`（验收 3：新卡一律未审核 → 不参与自动判定、不进重做纸，
两处都读 `server/autojudge.py` 与 `server/records.py: screen_redo_gate` 这一份实现）、
`standard_answer.value = None`、`problem.transcript = ""`。

`problem.image` / `clean_image` 是 `None` 而**不是**一个取不到的路径：记一个不存在的文件
会让 `original_image_file_missing` 误报（#9 那句「把按设计如此的事报成问题，比漏报更糟」）。
擦除图不存在是事实，`no_clean_image`（D4 的硬闸门）正该报出来。

**卡上不加新字段**：绑定关系只记在页文件里（契约 §10.2.1）；卡上的 `source` 记
`{page_image, bbox_norm, bbox_px, original_file}`，`page_image` 是**相对数据目录**的
`pages/<页 id>.<后缀>`（`--data` 是可配的，所以不写 `data/` 前缀；页 id 的推法都只看
文件名主干，见 `pages.page_hash_from_image`）。
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

from . import intake, page_edit, pages
from .warnings import _warn

# 入库自己的警告码（契约 §8）。级别按 §8 的两级纪律：
# `warning` = 真矛盾（本该建卡却建不了）；`hint` = 「我做了什么/我没做什么」要说出来。
COMMIT_NOTHING_KEPT = "page_commit_nothing_kept"              # hint：这一页没有「收」的块
COMMIT_BLOCKS_SKIPPED = "page_commit_blocks_skipped"          # hint：没收的块不进库
COMMIT_BLOCK_WITHOUT_BOX = "page_commit_block_without_box"    # warning：边界读不出来 → 没建卡
COMMIT_CARD_UNREADABLE = "page_commit_card_unreadable"        # warning：卡在却读不了 → 不覆盖
# 页指向的整页照片不在：**同一个事实、同一个码**（#9 的回填与 #15 的审计也用这个码）。
PAGE_PHOTO_MISSING = "page_photo_missing"                     # warning：整页照片不在
# 题卡文件读不了（契约 §8）：与审计的 `card_files_readable` 同一个码、同一件事。
CARD_FILE_UNREADABLE = "card_file_unreadable"                 # warning：卡在却读不了


def _warning(code: str, message: str, card_id: str | None = None, level: str = "warning") -> dict:
    """本模块的警告一律走 `server/warnings.py: _warn`（BRIEF 硬规则 7：不许手搓 dict）。"""
    return _warn(code, message, card_id, level)


def _iso(moment) -> str:
    return moment.isoformat(timespec="seconds")


def _existing_card_ids(catalog) -> tuple[set[str], list[dict]]:
    """盘上已经被占用的题卡 id + 「哪些卡读不了」的记账。

    占用集合**先收文件名主干**再看卡里的 `id`：文件名就是写盘的落点，读不了也照样
    占着（否则一次入库会把新卡写到一份坏文件上）。

    读不了的卡**不许安静地 `continue`**（R4）：卡里那个 `id` 悄悄丢了，而「这张卡
    读不了」这件事一声不响。按 §8 报 `card_file_unreadable`（与审计同一件事、同一个码）。
    """
    taken: set[str] = set()
    warnings: list[dict] = []
    for path in sorted(catalog.problems_dir.glob("*.json")):
        taken.add(path.stem)
        try:
            card = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            warnings.append(_warning(
                CARD_FILE_UNREADABLE,
                f"题卡 {path.name} 读不了（{exc.__class__.__name__}: {exc}）→ 它占用的 id "
                f"只按文件名算，卡里的 id 查不到；先修好这个文件（不是安静少一张）",
                path.stem))
            continue
        if isinstance(card, dict) and isinstance(card.get("id"), str) and card["id"]:
            taken.add(card["id"])
        elif not isinstance(card, dict):
            warnings.append(_warning(
                CARD_FILE_UNREADABLE,
                f"题卡 {path.name} 不是一个 JSON 对象 → 它占用的 id 只按文件名算，"
                f"卡里没有可读的 id（不是安静少一张）", path.stem))
    return taken, warnings


def _card_page_image(page: dict) -> str | None:
    """卡上的 `source.page_image`：**相对数据目录**的 `pages/<页 id>.<后缀>`。

    页里没记照片（`image` 空）→ `None`：那个绑定在盘上就是推不出页的（`page_binding`
    会报 `page_binding_missing`），本模块把它当**事实**报出来，而不是编一个文件名。
    """
    name = page.get("image")
    if not name:
        return None
    return f"pages/{name}"


def _new_card(page: dict, block: dict, pid: str, at: str) -> dict:
    """一块 → 一张**骨架卡**（理由见模块 docstring：还没转录、还没擦除图、还没审核）。"""
    origin = page.get("origin") if isinstance(page.get("origin"), dict) else {}
    return {
        "id": pid,
        "created_at": at,
        # 科目从**页**上带下来：录入时人已经指定过一次，入库时再问一遍是让同一件事
        # 有两个答案。页上没有（老页文件／没给）→ `None` = 未归类，一等状态，不猜。
        "subject": page.get("subject"),
        "source": {
            "page_image": _card_page_image(page),
            "bbox_norm": block.get("bbox_norm"),
            "bbox_px": block.get("bbox_px"),
            "original_file": origin.get("original_file"),
        },
        "problem": {
            # 题面裁剪图与擦除图都还没有：**不记路径**（记一个取不到的文件会误报）。
            "image": None,
            "type": block.get("problem_type"),
            "transcript": "",
            "options": [],
        },
        "original_solution": {
            "present": None,
            "original_answer": None,
            "transcript": None,
            "correction_transcript": None,
        },
        "correct_solution": {"text": None, "source": None},
        "standard_answer": {"value": None, "confidence": None},
        "topics": [],
        "error_causes": [],
        "new_tag_proposals": [],
        # 验收 3：新卡一律未审核。两把闸门（不参与自动判定／不进重做纸）都读这一个字段。
        "review": {"status": "unreviewed", "reviewed_at": None},
        "attempts": [],
        "mastery": {"state": "in_pool", "streak": 0, "last_attempt_at": None},
        "provenance": {
            "role": "commit",
            "page_id": page.get("id"),
            "block_id": block.get("id"),
            "question_no": block.get("question_no"),
            "provider": None,
            "model": None,
            "extract_warnings": [],
        },
        "print": {},
    }


def _write_card(catalog, pid: str, card: dict) -> Path:
    """原子地写一张新卡（先临时文件再替换，与 `pages.save_page` 同一条纪律）。"""
    catalog.problems_dir.mkdir(parents=True, exist_ok=True)
    path = catalog.problems_dir / f"{pid}.json"
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    return path


def _readable_card(catalog, pid: str) -> tuple[dict | None, str | None]:
    path = catalog.problems_dir / f"{pid}.json"
    if not path.is_file():
        return None, None
    try:
        card = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return None, f"{path.name} 读不了：{exc.__class__.__name__}: {exc}"
    if not isinstance(card, dict):
        return None, f"{path.name} 不是一个 JSON 对象"
    return card, None


def commit_page(catalog, page_id: str, *, at=None) -> tuple[dict, list[dict]]:
    """`POST /api/page/<id>/commit` 的全部逻辑。返回 `(报告, 警告)`。

    **拒绝路径一个字节都不动**（D9）：页 id 非法 → 400、页不在 → 404、页文件读不了 → 500、
    页里的 `id`/`image` 不对账 → 400、整页照片不在 → 400——全都在**任何写盘之前**
    （`intake._load_page` 与 `page_edit.assert_page_payload_matches_id` 是那几条判据的
    唯一实现，#15 消费它们，不另写一份）。
    """
    page = intake._load_page(catalog, page_id)
    page_edit.assert_page_payload_matches_id(catalog, page, page_id)
    moment = at or datetime.now()
    stamp = _iso(moment)
    warnings: list[dict] = []

    image = page.get("image")
    if image and not (catalog.pages_dir / image).is_file():
        # 不是拒绝：页还在、块还在，只是它指向的整页照片取不到。要喊出来（不许静默）。
        warnings.append(_warning(
            PAGE_PHOTO_MISSING,
            f"页 {page_id} 的整页照片不在（{catalog.pages_dir / image}）→ 这几次入库的卡"
            f"指向一张取不到的页图（source.page_image = {_card_page_image(page)!r}）"))

    blocks = list(page.get("blocks") or [])
    kept: list[tuple[int, dict]] = []       # 收的、且边界可用 → 要发卡
    refused: list[dict] = []
    skipped: list[dict] = []
    not_an_object = 0
    for index, raw in enumerate(blocks):
        if not isinstance(raw, dict):
            not_an_object += 1
            warnings.append(_warning(
                "block_not_an_object",
                f"页 {page_id} 的块列表第 {index} 项不是对象（{raw!r}）→ 它没有入库"))
            continue
        if raw.get("keep") is not True:
            decision = raw.get("decision") if isinstance(raw.get("decision"), dict) else {}
            skipped.append({
                "block_id": raw.get("id"),
                "keep": raw.get("keep"),
                "rule": decision.get("rule"),
                "reason": decision.get("reason")
                          or ("这一块还没判过去留（keep 不是 true）" if raw.get("keep") is None
                              else "这一块被标成不入库（keep = false）"),
            })
            continue
        if not pages.usable_box(raw.get("bbox_norm")):
            refused.append({"block_id": raw.get("id"), "reason": "block_without_box",
                            "message": "bbox_norm 读不出可用的整页归一化框"})
            warnings.append(_warning(
                COMMIT_BLOCK_WITHOUT_BOX,
                f"块 {raw.get('id')!r} 记着收，但它的 bbox_norm 读不出可用的整页归一化框"
                f"（{raw.get('bbox_norm')!r}）→ **没有为它建卡、也没有给它分配 id**，"
                f"先修边界再入库（否则会生成一张定位不到的卡）"))
            continue
        kept.append((index, raw))

    if not kept and not_an_object == 0 and not refused:
        warnings.append(_warning(
            COMMIT_NOTHING_KEPT,
            f"页 {page_id} 没有一块「收」的块 → 这次没有生成任何题卡（"
            f"补收入口：`python3 -m server.intake --page {page_id} --include --apply`）", None, "hint"))
    if skipped:
        detail = "、".join(
            f"{row['block_id']}（{row['rule'] or '没判过'}）" for row in skipped)
        warnings.append(_warning(
            COMMIT_BLOCKS_SKIPPED,
            f"另有 {len(skipped)} 块没有入库：{detail} → 没有为它们建卡（理由在页文件的"
            f" `decision` 里，可一键补收）", None, "hint"))

    # 只给「收」且边界可用的块分配 id（补收过的块已带 card_id 的走 reused）。
    taken, card_read_warnings = _existing_card_ids(catalog)
    warnings.extend(card_read_warnings)
    subset = {**page, "blocks": [block for _, block in kept]}
    assignment = pages.assign_card_ids(subset, taken=taken, at=moment)
    assigned_blocks = assignment["page"]["blocks"]

    new_blocks = list(blocks)
    for position, (index, _) in enumerate(kept):
        new_blocks[index] = assigned_blocks[position]
    assigned = [row for row in assignment["assigned"]]
    new_page = {**page, "blocks": new_blocks}
    if new_page != page:
        new_page["updated_at"] = stamp
        new_page["last_edit"] = {
            "action": "commit", "at": stamp,
            "note": f"入库：为 {len(assigned)} 块首次分配题卡 id 并写回绑定" if assigned
                    else "入库：绑定没有变化（已经生成过的块不重复生成）",
        }
    wrote_page = new_page != page
    if wrote_page:
        pages.save_page(catalog, new_page, page_id=page_id, apply=True)

    # 先写页、再建卡：中断后重跑走 reused 那条路，id 不会变（幂等，见模块 docstring）。
    created: list[dict] = []
    reused: list[dict] = []
    for block in assigned_blocks:
        pid = block.get("card_id")
        existing, error = _readable_card(catalog, pid)
        if error:
            refused.append({"block_id": block.get("id"), "reason": "card_unreadable",
                            "message": error})
            warnings.append(_warning(
                COMMIT_CARD_UNREADABLE,
                f"块 {block.get('id')!r} 绑的卡片 {pid} 在盘上却读不了（{error}）→ "
                f"没有覆盖它，先留证据（这张卡没有重新生成）", pid))
            continue
        if existing is not None:
            reused.append({"block_id": block.get("id"), "card_id": pid,
                           "path": str(catalog.problems_dir / f"{pid}.json")})
            continue
        card = _new_card(new_page, block, pid, stamp)
        path = _write_card(catalog, pid, card)
        created.append({"block_id": block.get("id"), "card_id": pid, "path": str(path)})

    index_data, _index_warnings, index_skipped = catalog.index()
    report = {
        "page_id": page_id,
        "page_path": str(pages.page_path(catalog, page_id)),
        "at": stamp,
        "blocks": {
            "total": len(blocks),
            "kept": len(kept),
            "dropped": sum(1 for row in skipped if row["keep"] is False),
            "pending": sum(1 for row in skipped if row["keep"] is None),
            "not_an_object": not_an_object,
        },
        "created": created,
        "reused": reused,
        "skipped": skipped,
        "refused": refused,
        "wrote_page": wrote_page,
        "wrote_cards": bool(created),
        "index": {
            "count": index_data["count"],
            "card_ids": [rec["id"] for rec in index_data["problems"]],
            "built_at": index_data["built_at"],
            "warnings": len(index_data["warnings"]),
            "skipped": len(index_skipped),
        },
    }
    return report, warnings


__all__ = ["commit_page", "COMMIT_NOTHING_KEPT", "COMMIT_BLOCKS_SKIPPED",
           "COMMIT_BLOCK_WITHOUT_BOX", "COMMIT_CARD_UNREADABLE", "PAGE_PHOTO_MISSING",
           "CARD_FILE_UNREADABLE"]
