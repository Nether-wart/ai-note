"""存量题卡 → 页文件：一条**显式**的迁移命令。

    python3 -m server.backfill --data data            # 预演：只报告，不写盘
    python3 -m server.backfill --data data --apply    # 真的建页文件

为什么是**另一条命令**而不是塞进审计：审计的原则是「只报告、不修改」
（`proto/server.py:1034`、`:1126`），而回填是写盘（spec #2「存量数据要能进来」）。
原型把 `--audit` 与 `--repair --apply` 分开，这里照做（spec-2 笔记 §I）。

输出是**结构化**的（JSON 到 stdout）：每张卡一行「我做了什么／没做什么／为什么跳过」，
外加一句人看的汇总与全部警告到 stderr。没有 `sys.exit`——返回值就是退出码
（编排裁决 D1：处理器里不许 `sys.exit`）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .catalog import Catalog
from .pages import backfill_pages
from .paths import default_data_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="把存量题卡反推回填成页文件（#9）")
    parser.add_argument("--data", default=str(default_data_dir()),
                        help="数据目录（默认**用户数据目录**，见 server/paths.py）")
    parser.add_argument("--apply", action="store_true",
                        help="真的写页文件；不传就是预演（只报告）")
    args = parser.parse_args(argv)

    data_dir = Path(args.data)
    if not data_dir.is_dir():
        print(f"数据目录不存在：{data_dir}", file=sys.stderr)
        return 2

    report = backfill_pages(Catalog(data_dir), apply=args.apply)
    print(json.dumps(report, ensure_ascii=False, indent=2))

    summary = report["summary"]
    verdict = "已回填" if args.apply else "预演（一个字节都没写；要写加 --apply）"
    print(
        f"{verdict}：看了 {summary['cards_seen']} 张卡，"
        f"建 {summary['created']} 页 / 追加 {summary['appended']} 块 / "
        f"不动 {summary['unchanged']} / 跳过 {summary['skipped']}；"
        f"页文件共 {summary['pages']} 份（这次动 {summary['pages_changed']}）",
        file=sys.stderr,
    )
    for card in report["cards"]:
        if card["action"] == "skipped":
            print(f"[skipped] {card['id']}：{card['message']}", file=sys.stderr)
    for warning in report["warnings"]:
        print(f"[{warning['code']}] {warning['message']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
