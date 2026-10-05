"""存量题卡 → 科目：一条**显式**的迁移命令。

    python3 -m server.subject_assign --data data --map 科目表.json          # 预演：只报告
    python3 -m server.subject_assign --data data --map 科目表.json --apply  # 真的写回

为什么是**另一条命令**：审计的原则是「只报告、不修改」，而回填是写盘（同 `backfill.py`）。

为什么**不许猜**：存量卡没有科目，而科目是导航的根——缺了它这张卡会从侧栏里消失。
但按考点或题型去**推**一个科目（「函数与导数 → 数学」）是拿相关性冒充事实：
**猜错的科目比没有科目更坏**，因为它看起来是对的，而没有人会去核对一个看起来对的答案。
所以只认人给的映射表；表里没有的卡原样不动，只在汇总里报出「仍未归类」的道数。

映射表形状：`{"p-20261004-41c86b": "数学", …}`。

**坏值一票否决**：表里出现一个不在受控词表里的科目时，`--apply` 一个字节都不写、
返回 1——半途写一半比全不写更难收拾。这与 `backfill.py` 的预演/`--apply` 分开是同一条纪律。

输出是**结构化**的（JSON 到 stdout）：每张卡一行「我做了什么／没做什么／为什么跳过」，
外加一句人看的汇总与全部警告到 stderr。处理器里不 `sys.exit`（编排裁决 D1）——
返回值就是退出码。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .catalog import Catalog
from .paths import default_data_dir
from .subjects import card_subject, load as load_vocabulary

UNKNOWN_SUBJECT = "subject_assign_unknown_subject"
CARD_UNREADABLE = "subject_assign_card_unreadable"
CARD_MISSING = "subject_assign_card_missing"


def plan(catalog: Catalog, mapping: dict[str, str]) -> tuple[dict, list[dict]]:
    """算一遍要写什么，**不碰盘**。返回 `(报告, 警告)`。"""
    vocabulary, vocab_warnings = load_vocabulary(catalog)
    known = list(vocabulary["subjects"])

    rows: list[dict] = []
    skipped: list[dict] = []
    bad_values: list[dict] = []

    for pid, subject in sorted(mapping.items()):
        value = (subject or "").strip()
        path = catalog.problems_dir / f"{pid}.json"
        if not path.is_file():
            skipped.append({"id": pid, "code": CARD_MISSING,
                            "message": f"没有这道题：{path} → 跳过"})
            continue
        if not known or value not in known:
            bad_values.append({"id": pid, "value": value, "allowed": known})
            skipped.append({
                "id": pid, "code": UNKNOWN_SUBJECT,
                "message": f"科目 {value!r} 不在受控词表里 → 跳过这一条；"
                           f"先把科目加进 subjects.json，或改成表里的一个",
            })
            continue
        try:
            card = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            skipped.append({
                "id": pid, "code": CARD_UNREADABLE,
                "message": f"{path.name} 读不了：{exc.__class__.__name__}: {exc} → 跳过",
            })
            continue
        rows.append({
            "id": pid,
            "from": card_subject(card),
            "to": value,
            "changed": card_subject(card) != value,
        })

    on_disk = sorted(p.stem for p in catalog.problems_dir.glob("*.json"))
    touched = set(mapping)
    still_unclassified: list[str] = []
    for pid in on_disk:
        if pid in touched:
            continue
        try:
            card = json.loads((catalog.problems_dir / f"{pid}.json").read_text(encoding="utf-8"))
        except Exception:
            continue
        if card_subject(card) is None:
            still_unclassified.append(pid)

    report = {
        "data_dir": str(catalog.root),
        "subjects": known,
        "cards": rows,
        "skipped": skipped,
        "still_unclassified": still_unclassified,
        "counts": {
            "cards_on_disk": len(on_disk),
            "in_map": len(mapping),
            "will_change": sum(1 for row in rows if row["changed"]),
            "unchanged": sum(1 for row in rows if not row["changed"]),
            "skipped": len(skipped),
            "still_unclassified": len(still_unclassified),
        },
        # 坏值在场 = 这一趟**什么都不会写**。机器读这个键来决定要不要拦。
        "blocked_by": bad_values,
    }
    return report, vocab_warnings


def apply_plan(catalog: Catalog, report: dict, warnings: list[dict]) -> list[dict]:
    """把预演过的改动真的写回。只改 `subject` 一个字段，别的字节一个都不动。"""
    written: list[dict] = []
    for row in report["cards"]:
        if not row["changed"]:
            continue
        path = catalog.problems_dir / f"{row['id']}.json"
        try:
            card = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            warnings.append({"code": CARD_UNREADABLE, "message": f"{path.name} 读不了：{exc}",
                             "id": row["id"], "level": "warning"})
            continue
        card["subject"] = row["to"]
        # 缩进与结尾换行照 `data/problems/*.json` 的现状：回填不该把整个文件重排一遍，
        # 那会让 `git diff` 看不清这次到底改了什么。
        path.write_text(json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        written.append(row)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="把存量题卡按人给的映射表写回科目（默认预演，不写盘）")
    parser.add_argument("--data", default=str(default_data_dir()),
                        help="数据目录（默认用户数据目录）")
    parser.add_argument("--map", required=True, dest="map_path",
                        help="映射表 JSON：{\"<题卡 id>\": \"<科目>\", …}")
    parser.add_argument("--apply", action="store_true",
                        help="真的写回；不给就只报告（一个字节都不写）")
    args = parser.parse_args(argv)

    data_dir = Path(args.data)
    if not data_dir.is_dir():
        print(f"数据目录不存在：{data_dir}", file=sys.stderr)
        return 2
    try:
        mapping = json.loads(Path(args.map_path).read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"映射表读不了（{args.map_path}）：{exc.__class__.__name__}: {exc}", file=sys.stderr)
        return 2
    if not isinstance(mapping, dict):
        print("映射表必须是一个对象：{\"<题卡 id>\": \"<科目>\"}", file=sys.stderr)
        return 2

    catalog = Catalog(data_dir)
    report, warnings = plan(catalog, {str(k): v for k, v in mapping.items()})

    blocked = report["blocked_by"]
    if blocked and args.apply:
        # 坏值一票否决：半途写一半比全不写更难收拾。
        for entry in blocked:
            print(f"[{UNKNOWN_SUBJECT}] {entry['id']}：{entry['value']!r} 不在 {entry['allowed']} 里",
                  file=sys.stderr)
    elif args.apply:
        report["written"] = apply_plan(catalog, report, warnings)
    else:
        report["written"] = []

    print(json.dumps(report, ensure_ascii=False, indent=2))

    counts = report["counts"]
    verdict = ("已写回" if args.apply and not blocked
               else "预演（一个字节都没写；要写加 --apply）" if not blocked
               else "**什么都没写**：映射表里有不在词表里的科目")
    print(
        f"{verdict}：{counts['cards_on_disk']} 张卡，映射表给了 {counts['in_map']} 条"
        f"（会改 {counts['will_change']} 张、不变 {counts['unchanged']} 张，跳过 {counts['skipped']} 条）；"
        f"仍有 {counts['still_unclassified']} 张**未归类**",
        file=sys.stderr,
    )
    for entry in report["skipped"]:
        print(f"[{entry['code']}] {entry['message']}", file=sys.stderr)
    for warning in warnings:
        print(f"[{warning['code']}] {warning['message']}", file=sys.stderr)

    if blocked:
        return 1
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
