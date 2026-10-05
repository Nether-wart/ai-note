"""落盘数据体检：页 ↔ 卡**双向**对账（工单 #15 验收 2、spec #2 的 `--audit` 扩展）。

    python3 -m server.audit [--data <数据目录>]

**只读盘、什么都不改、可随时重跑**（原型 `proto/server.py:1034`/`:1126` 的那条纪律：
体检只报告、不修改）。回填是另一条显式命令（`python3 -m server.backfill --apply`）——
「报告」与「写盘」不许混在一条命令里。

## 为什么需要它

一次真事故：验收是对 `runs/` 里那次调用打的分，而题卡在验收之后被界面改坏了，没人发现——
日志说这张图的转录完整，盘上的卡却写着另一道题的题干。所以检查必须跑在**落盘的数据**上。
spec #2 的立身之本也是这一条：「切分结果与收入决策一旦只存在于界面的内存里，
『漏了一题』就永远查不出来」。

## 三条必须能报出来的情况（验收 2）

1. **页里记了收入但卡片不存在** → `page_card_missing`（`warning`）。
2. **卡片没有页绑定** → **不另造码**：消费 `pages.page_binding`（#9 的唯一实现，
   经 `warnings.card_warnings` 出来）。旧卡 `page_binding_missing` = **hint**
   （旧数据不该因为新结构变成脏数据），页实体在场却对不上账 `page_binding_lost` = **warning**。
   **两级不许混**——混成一个级别会让审计失去可信度。
3. **同一页两张卡题干逐字相同** → `duplicate_transcript_on_page`（`warning`）。
   判据与索引级 `duplicate_transcript` 共用 `warnings.duplicate_transcript_groups`
   （`strip()` 之后 `==`，没有模糊比、没有相似度）。一页多题时相邻题互相污染的概率
   随同页题数上升（spec #2 Further Notes），所以这条**没有例外**。

同属「页↔卡对账」的还有三条，一并报：`page_block_not_committed`（收了的块还没有绑定 =
还没入库，正常中间态，hint）、`card_block_not_kept`（卡绑的块被标成不收 → **卡不会被自动删**，
删卡是人的事，hint）、`card_bound_to_other_page`（卡自己的 `source.page_image` 指的是另一页 =
双向对账矛盾，warning）。

## 不许静默

`checked[]` **永远**在报告里：没发现问题也要说清**检查了哪几项**，否则「没输出」到底是
「没问题」还是「没跑」分不清。每条发现都带 `check`（哪一项查出来的）、`code`、显式 `level`、
`message`（人的原话）与能定位的指针（`card_id`/`page_id`/`block_id`/`path`）。

## 退出码

有 `warning` 级发现 → 1，否则 0。**hint 不算问题**：把按设计如此的事报成问题会训练人
忽略体检（`proto/server.py:1046-1047`），那比漏报更糟。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import pages, warnings as warnings_mod
from .catalog import Catalog
from .paths import default_data_dir

# 审计自己的码（契约 §8）。凡是别处已有的码就沿用字面量，不另造近义名。
CARD_FILE_UNREADABLE = "card_file_unreadable"            # warning：卡文件读不了（不是安静少一张）
PAGE_FILE_UNREADABLE = "page_file_unreadable"            # warning：页文件读不了（沿用 #9/#12 的码）
PAGE_ID_MISMATCH = "page_id_mismatch"                    # warning：页里的 id 与文件名不一致
PAGE_IMAGE_UNSAFE = "page_image_unsafe"                  # warning：image 不是纯文件名
PAGE_PHOTO_MISSING = "page_photo_missing"                # warning：整页照片不在
BLOCK_NOT_AN_OBJECT = "block_not_an_object"              # warning：块列表里混进了非对象（沿用 #9/#10）
PROBLEM_ID_MISMATCH = "problem_id_mismatch"              # warning：文件名与卡内 id 不一致
PAGE_CARD_MISSING = "page_card_missing"                  # warning：页记着收+绑卡，卡却不在
PAGE_BLOCK_NOT_COMMITTED = "page_block_not_committed"    # hint：收了但还没入库
CARD_BLOCK_NOT_KEPT = "card_block_not_kept"              # hint：卡绑的块被标成不收（不自动删卡）
CARD_BOUND_TO_OTHER_PAGE = "card_bound_to_other_page"    # warning：卡自称来自另一页
DUPLICATE_TRANSCRIPT_ON_PAGE = "duplicate_transcript_on_page"  # warning：同页题干逐字相同

# 「我查了哪几项」——**这是一种对外承诺**：每一项都要真的实现，`findings[].check` 引用这里的 id。
CHECKS: tuple[dict, ...] = (
    {"id": "card_files_readable",
     "what": "每一份题卡文件都读得出来，且文件名与卡里的 id 一致（读不了的点名报出来）"},
    {"id": "card_self_check",
     "what": "每一张卡的字段自检（复用 warnings.card_warnings，规则只有一份）"},
    {"id": "card_page_binding",
     "what": "卡片有没有页绑定（级别由 pages.page_binding 定：旧卡 hint／页实体在场却对不上账 warning）"},
    {"id": "page_files_readable",
     "what": "每一份页文件都读得出来，且页里的 id 与文件名一致"},
    {"id": "page_blocks_shaped",
     "what": "页里的块都是一个对象（混进来的非对象项要点名，不是安静跳过）"},
    {"id": "page_photo_exists",
     "what": "页实体指向的整页照片在不在、image 是不是纯文件名（照片是最后的底，不该删）"},
    {"id": "page_kept_block_card_exists",
     "what": "页里记了收入（keep=true）又绑了卡的块：那张卡在不在盘上"},
    {"id": "page_kept_block_committed",
     "what": "收了的块还没有绑定 = 还没入库（正常中间态，说出来而不是当成问题）"},
    {"id": "card_bound_block_kept",
     "what": "卡片绑的那一块在页里还是不是「收」（卡不会被自动删，删卡由人定）"},
    {"id": "card_page_consistency",
     "what": "卡片自己的 source.page_image 与它被绑进的那一页是不是同一页（双向对账）"},
    {"id": "duplicate_transcript_on_page",
     "what": "同一页两张卡的题干逐字相同（与索引级 duplicate_transcript 共用一份判据）"},
)
CHECK_IDS = tuple(row["id"] for row in CHECKS)


def _finding(check: str, code: str, level: str, message: str, *, scope: str,
             card_id: str | None = None, page_id: str | None = None,
             block_id: str | None = None, path: str | None = None, **extra) -> dict:
    """一条发现。形状固定：`check`/`code`/`level`/`scope`/指针/人话（+ 该项特有的字段）。"""
    out = {
        "check": check, "code": code, "level": level, "scope": scope,
        "card_id": card_id, "page_id": page_id, "block_id": block_id,
        "path": path, "message": message,
    }
    out.update(extra)
    return out


def _read_cards(catalog) -> tuple[list[dict], dict, set, list[tuple[Path, str]]]:
    """盘上的题卡：`(有序的卡, 按 id/文件名可查的索引, 文件名主干集合, 读不了的)`。"""
    cards: list[dict] = []
    by_id: dict[str, dict] = {}
    stems: set[str] = set()
    unreadable: list[tuple[Path, str]] = []
    for path in sorted(catalog.problems_dir.glob("*.json")):
        stems.add(path.stem)
        try:
            card = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            unreadable.append((path, f"{path.name} 读不了：{exc.__class__.__name__}: {exc}"))
            continue
        if not isinstance(card, dict):
            unreadable.append((path, f"{path.name} 不是一个 JSON 对象"))
            continue
        cards.append(card)
        by_id[path.stem] = card
        if isinstance(card.get("id"), str) and card["id"]:
            by_id.setdefault(card["id"], card)
    return cards, by_id, stems, unreadable


def audit(catalog) -> dict:
    """体检一个数据目录。**只读**：不写任何文件、不改任何对象、不读索引。"""
    findings: list[dict] = []
    cards, by_id, stems, unreadable = _read_cards(catalog)

    for path, message in unreadable:
        findings.append(_finding("card_files_readable", CARD_FILE_UNREADABLE, "warning", message,
                                 scope="card", card_id=path.stem, path=str(path)))
    for card in cards:
        path = catalog.problems_dir / f"{card.get('id')}.json"
        if isinstance(card.get("id"), str) and card["id"] and path.stem != card["id"]:
            # 卡内 id 与文件名不一致：索引也报同一条码（不同面，各自都要喊）
            findings.append(_finding(
                "card_files_readable", PROBLEM_ID_MISMATCH, "warning",
                f"卡文件名叫 {path.stem!r}，卡里的 id 是 {card['id']!r} → 改名或复制粘贴事故",
                scope="card", card_id=card["id"], path=str(path)))

    # 1) 卡片自检：复用界面那一份规则（`card_warnings`），规则只留一份。
    #    页绑定那一条**直接问 `pages.page_binding`**（#9 的唯一实现）——这样发现里能带上
    #    `page_id`/`page_path` 两个指针（「能定位到东西」是验收 2 的要求），而 `card_warnings`
    #    只发 `{code,message,id,level}`。同一件事只报一次，所以自检那一轮跳过 `page_binding_*`。
    for card in cards:
        pid = card.get("id")
        binding = pages.page_binding(catalog, card)
        if not binding["bound"]:
            findings.append(_finding(
                "card_page_binding", binding["code"], binding["level"], binding["message"],
                scope="card", card_id=pid, page_id=binding["page_id"],
                path=binding["page_path"]))
        for warning in warnings_mod.card_warnings(card, catalog):
            code = warning["code"]
            if code.startswith("page_binding_"):
                continue     # 上面那一条已经报过了（同一份判据、只报一次）
            findings.append(_finding("card_self_check", code, warning.get("level") or "warning",
                                     warning["message"], scope="card",
                                     card_id=warning.get("id") or pid))

    # 2) 页这一侧：双向对账
    pages_seen = pages_unreadable = blocks_seen = bound_cards = 0
    for page_path in sorted(catalog.pages_dir.glob("*.json")):
        page_id = page_path.stem
        page, error = pages.read_page(catalog, page_id)
        if error:
            pages_unreadable += 1
            findings.append(_finding("page_files_readable", PAGE_FILE_UNREADABLE, "warning",
                                     f"{error} → 这一页的块与绑定都没查", scope="page",
                                     page_id=page_id, path=str(page_path)))
            continue
        pages_seen += 1
        if not pages.page_identity_ok(page, page_id):
            findings.append(_finding(
                "page_files_readable", PAGE_ID_MISMATCH, "warning",
                f"页文件名叫 {page_id!r}，页里的 id 是 {page.get('id')!r} → "
                f"写盘路径只认文件名，内容里的 id 只用于对账",
                scope="page", page_id=page_id, path=str(page_path)))

        image = page.get("image")
        if not pages.is_page_image_name(image):
            findings.append(_finding(
                "page_photo_exists", PAGE_IMAGE_UNSAFE, "warning",
                f"页 {page_id} 的 image 不是纯文件名：{image!r} → 不拿它拼路径（照片必须与"
                f"页文件同目录并列，D5）", scope="page", page_id=page_id, path=str(page_path)))
        elif not image or not (catalog.pages_dir / image).is_file():
            findings.append(_finding(
                "page_photo_exists", PAGE_PHOTO_MISSING, "warning",
                f"页 {page_id} 的整页照片不在：{catalog.pages_dir / str(image or '')} → "
                f"页绑定指向一张取不到的图（照片是最后的底，不该删）",
                scope="page", page_id=page_id, path=str(page_path)))

        bound: list[str] = []
        for index, raw in enumerate(page.get("blocks") or []):
            blocks_seen += 1
            if not isinstance(raw, dict):
                findings.append(_finding(
                    "page_blocks_shaped", BLOCK_NOT_AN_OBJECT, "warning",
                    f"页 {page_id} 的块列表第 {index} 项不是对象（{raw!r}）→ 这一项没有对账",
                    scope="page", page_id=page_id, path=str(page_path)))
                continue
            card_id = raw.get("card_id")
            if not card_id:
                if raw.get("keep") is True:
                    findings.append(_finding(
                        "page_kept_block_committed", PAGE_BLOCK_NOT_COMMITTED, "hint",
                        f"页 {page_id} 的块 {raw.get('id')!r} 记着收，但还没有绑定题卡 → "
                        f"还没入库（`POST /api/page/{page_id}/commit` 是入库那个动作）",
                        scope="page", page_id=page_id, block_id=raw.get("id"), path=str(page_path)))
                continue
            bound.append(card_id)
            card_path = catalog.problems_dir / f"{card_id}.json"
            present = card_id in by_id or card_id in stems
            if not present:
                findings.append(_finding(
                    "page_kept_block_card_exists", PAGE_CARD_MISSING, "warning",
                    f"页 {page_id} 的块 {raw.get('id')!r} 记着收（keep=true）并绑着卡片 "
                    f"{card_id}，但题卡文件不在：{card_path} → 页说它进库了，盘上没有它"
                    f"（重跑一次入库可以补建）",
                    scope="page", page_id=page_id, block_id=raw.get("id"), card_id=card_id,
                    path=str(card_path)))
            if raw.get("keep") is not True:
                findings.append(_finding(
                    "card_bound_block_kept", CARD_BLOCK_NOT_KEPT, "hint",
                    f"卡片 {card_id} 绑在页 {page_id} 的块 {raw.get('id')!r} 上，但这一块在页里"
                    f"被标成不收（keep={raw.get('keep')!r}）→ **卡不会被自动删**，"
                    f"删不删由人定",
                    scope="page", page_id=page_id, block_id=raw.get("id"), card_id=card_id,
                    path=str(page_path)))
            card = by_id.get(card_id)
            if card is not None:
                claimed = pages.page_hash_from_image((card.get("source") or {}).get("page_image"))
                if claimed is None:
                    findings.append(_finding(
                        "card_page_consistency", "card_page_image_missing", "hint",
                        f"卡片 {card_id} 绑在页 {page_id} 上，但它自己的 source.page_image 是空的"
                        f"（或推不出页 id）→ 这张卡的来源信息不全",
                        scope="card", card_id=card_id, page_id=page_id, block_id=raw.get("id"),
                        path=str(card_path)))
                elif claimed != page_id:
                    findings.append(_finding(
                        "card_page_consistency", CARD_BOUND_TO_OTHER_PAGE, "warning",
                        f"卡片 {card_id} 被绑在页 {page_id} 上，但它自己的 source.page_image 指的是"
                        f"另一页 {claimed} → 页↔卡对不上账（要么卡记错了来源，要么绑错了页）",
                        scope="card", card_id=card_id, page_id=page_id, block_id=raw.get("id"),
                        path=str(card_path)))

        bound_cards += len(set(bound))

        # 3) 同页题干逐字相同（判据与索引级那条共用一份实现，只是把范围收到这一页）
        entries = [(card_id, ((by_id.get(card_id) or {}).get("problem") or {}).get("transcript"))
                   for card_id in sorted(set(bound))]
        for group in warnings_mod.duplicate_transcript_groups(entries):
            findings.append(_finding(
                "duplicate_transcript_on_page", DUPLICATE_TRANSCRIPT_ON_PAGE, "warning",
                f"同一页（{page_id}）两张卡的题干逐字相同（{'、'.join(group['ids'])}）→ "
                f"两道不同的题不可能有同一段题干，检查是不是串题了。题干："
                f"{group['transcript'][:40]}",
                scope="page", page_id=page_id, card_ids=group["ids"]))

    by_level = {level: sum(1 for f in findings if f["level"] == level)
                for level in ("warning", "hint")}
    by_code: dict[str, int] = {}
    by_check: dict[str, int] = {}
    for finding in findings:
        by_code[finding["code"]] = by_code.get(finding["code"], 0) + 1
        by_check[finding["check"]] = by_check.get(finding["check"], 0) + 1
    return {
        "checked": [dict(row) for row in CHECKS],
        "findings": findings,
        "counts": {
            "warning": by_level["warning"], "hint": by_level["hint"],
            "by_level": by_level, "by_code": by_code, "by_check": by_check,
        },
        "audited": {
            "cards": len(cards),
            "cards_unreadable": len(unreadable),
            "pages": pages_seen,
            "pages_unreadable": pages_unreadable,
            "blocks": blocks_seen,
            "bound_cards": bound_cards,
        },
        "read_only": True,
        "ok": by_level["warning"] == 0,
    }


# ---------------------------------------------------------------- CLI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="落盘数据体检：页 ↔ 卡双向对账（只报告、不修改）")
    parser.add_argument("--data", default=str(default_data_dir()),
                        help="数据目录（默认**用户数据目录**，见 server/paths.py）")
    return parser


def _print_human(report: dict, stream) -> None:
    audited = report["audited"]
    print(f"落盘数据体检：{audited['cards']} 张题卡（读不了 {audited['cards_unreadable']}）、"
          f"{audited['pages']} 页（读不了 {audited['pages_unreadable']}）、"
          f"{audited['blocks']} 块、{audited['bound_cards']} 处页↔卡绑定", file=stream)
    print(f"检查了 {len(report['checked'])} 项：", file=stream)
    for row in report["checked"]:
        print(f"  · {row['id']} —— {row['what']}", file=stream)
    if not report["findings"]:
        print("  ✓ 没有查出问题", file=stream)
    else:
        counts = report["counts"]
        print(f"\n查出 {counts['warning']} 处问题、{counts['hint']} 条提示：", file=stream)
        for finding in report["findings"]:
            where = " ".join(filter(None, [
                f"页={finding['page_id']}" if finding.get("page_id") else None,
                f"块={finding['block_id']}" if finding.get("block_id") else None,
                f"卡={finding['card_id']}" if finding.get("card_id") else None,
            ]))
            mark = "✗" if finding["level"] == "warning" else "·"
            print(f"  {mark} [{finding['level']}] {finding['code']} {where} —— "
                  f"{finding['message']}", file=stream)
    print("体检只报告、不修改（回填是另一条命令：python3 -m server.backfill --apply）。"
          "可随时重跑——这正是它存在的意义。", file=stream)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    data_dir = Path(args.data)
    if not data_dir.is_dir():
        print(f"数据目录不存在：{data_dir}", file=sys.stderr)
        return 2

    report = audit(Catalog(data_dir))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    _print_human(report, sys.stderr)
    return 1 if report["counts"]["warning"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
