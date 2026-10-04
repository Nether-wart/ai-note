"""`python3 -m server.backfill`：存量卡 → 页文件的迁移命令。

按 spec-2 笔记 §I：**审计只报告、回填是另一条显式命令**（照原型把 `--audit` 与
`--repair --apply` 分开的做法）。所以默认是**预演**，要写盘必须显式 `--apply`。
"""

from __future__ import annotations

import json

from conftest import make_data_dir
from server import backfill
from test_pages import REAL_PAGE_HASH, real_shaped_card


def test_backfill_cli_is_a_dry_run_until_you_say_apply(tmp_path, capsys):
    """默认只报告，不写盘；`--apply` 才真的建页文件。"""
    root = make_data_dir(tmp_path, [real_shaped_card()])

    assert backfill.main(["--data", str(root)]) == 0
    printed = json.loads(capsys.readouterr().out)

    assert printed["apply"] is False
    assert [c["action"] for c in printed["cards"]] == ["created"]
    assert printed["summary"] == {
        "cards_seen": 1, "created": 1, "appended": 0, "unchanged": 0,
        "skipped": 0, "pages": 1, "pages_changed": 1,
    }
    assert not list((root / "pages").glob("*.json")), "预演不许写盘"

    assert backfill.main(["--data", str(root), "--apply"]) == 0
    assert (root / "pages" / f"{REAL_PAGE_HASH}.json").is_file()


def test_backfill_cli_says_so_when_there_is_nothing_to_do(tmp_path, capsys):
    """「没发现问题」也要明说——否则「没输出」分不清是没问题还是没跑。"""
    root = make_data_dir(tmp_path, [real_shaped_card()])
    backfill.main(["--data", str(root), "--apply"])
    capsys.readouterr()   # 第一次的输出先倒掉，下面只解析第二次的

    assert backfill.main(["--data", str(root), "--apply"]) == 0
    printed = json.loads(capsys.readouterr().out)

    assert printed["summary"]["unchanged"] == 1
    assert printed["summary"]["pages_changed"] == 0
    assert printed["cards"][0]["message"], "每一张卡都要有一句「我做了什么／没做什么」"


def test_backfill_cli_refuses_a_data_dir_that_is_not_there(tmp_path, capsys):
    """指错目录要说清、给非零退出码——不是安静地回填零张卡。"""
    assert backfill.main(["--data", str(tmp_path / "nope")]) == 2
    assert "不存在" in capsys.readouterr().err


def test_backfill_cli_reports_warnings_without_failing(tmp_path, capsys):
    """卡能回填但整页照片不在：命令照常成功（这是一次迁移），但必须喊出来。"""
    root = make_data_dir(tmp_path, [real_shaped_card()])

    assert backfill.main(["--data", str(root), "--apply"]) == 0
    captured = capsys.readouterr()
    printed = json.loads(captured.out)

    assert {w["code"] for w in printed["warnings"]} == {"page_photo_missing"}
    assert "page_photo_missing" in captured.err, "警告也要在终端上看得见，不能只躺在 JSON 里"
