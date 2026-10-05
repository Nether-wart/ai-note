"""科目与词表（#17）。

这批测试盯的是一条**不变式**：

    sum(stats.by_subject[*].problems) + stats.unclassified == stats.problems

它一旦断了，侧栏里就会少掉一道题——而这个项目最怕的失败正是**静默少一道题**。

另外盯住「未归类」是个**一等状态**：没有科目的卡不是脏数据，是**还没归类**，
它必须在侧栏的「未归类」下看得见、道数写得出来，而不是从树上消失。
"""

from __future__ import annotations

import json

from conftest import make_card, make_data_dir


def _index(root):
    from server.catalog import Catalog

    return Catalog(root).index()


def _map_file(tmp_path, mapping) -> str:
    path = tmp_path / "科目表.json"
    path.write_text(json.dumps(mapping, ensure_ascii=False), encoding="utf-8")
    return str(path)


# ------------------------------------------------------------------ 词表


def test_a_card_without_a_subject_is_unclassified_not_dropped(tmp_path):
    root = make_data_dir(tmp_path, [make_card(**{"subject": None})])
    data, warnings, _ = _index(root)

    assert data["problems"][0]["subject"] is None
    assert data["stats"]["unclassified"] == 1
    assert data["stats"]["by_subject"]["数学"]["problems"] == 0

    hint = [w for w in warnings if w["code"] == "subject_missing"]
    assert len(hint) == 1
    # 级别是判据的一部分：未归类是**预期状态**，报 warning 会把它淹在噪声里
    assert hint[0]["level"] == "hint"


def test_an_unknown_subject_is_a_warning_and_still_counted(tmp_path):
    root = make_data_dir(tmp_path, [make_card(**{"subject": "化学"})])
    data, warnings, _ = _index(root)

    # 读数是记录的原值：服务**不替你**把它改写成 null（谁改的谁喊）
    assert data["problems"][0]["subject"] == "化学"
    unknown = [w for w in warnings if w["code"] == "subject_unknown"]
    assert len(unknown) == 1
    assert unknown[0]["level"] == "warning"
    # 但它必须仍然落在某个桶里，否则「一道题都不许少」当场就断
    assert data["stats"]["by_subject"]["化学"]["problems"] == 1
    assert data["stats"]["unclassified"] == 0


def test_the_rollup_invariant_holds_with_a_mixed_library(tmp_path):
    root = make_data_dir(tmp_path, [
        make_card("p-20200101-aaaaaa"),
        make_card("p-20200101-bbbbbb", **{"subject": "物理"}),
        make_card("p-20200101-cccccc", **{"subject": None}),
        make_card("p-20200101-dddddd", **{"subject": "化学"}),
    ])
    data, _, _ = _index(root)
    stats = data["stats"]

    total = sum(b["problems"] for b in stats["by_subject"].values()) + stats["unclassified"]
    assert total == stats["problems"] == 4
    assert stats["unclassified"] == 1
    # 词表里的科目哪怕 0 道也要在树上：侧栏要能画出空科目，不然人会以为它丢了
    assert "数学" in stats["by_subject"] and "物理" in stats["by_subject"]


def test_a_missing_vocabulary_is_shouted_not_shrugged(tmp_path):
    root = make_data_dir(tmp_path, [make_card()], vocab=False)
    data, warnings, _ = _index(root)
    codes = [w["code"] for w in warnings]

    assert "subjects_vocab_missing" in codes
    assert data["subjects"] == []
    # 词表没读出来时**不许**对每张卡喊 subject_unknown：那是噪声，不是判据
    assert "subject_unknown" not in codes
    assert data["problems"][0]["subject"] == "数学"


def test_an_outline_subject_outside_the_vocabulary_is_reported(tmp_path):
    root = make_data_dir(tmp_path, [])
    (root / "vocab" / "topic-outline.seed.json").write_text(
        json.dumps({"大纲": {"化学": {"有机化学": {"烃": []}}}}, ensure_ascii=False),
        encoding="utf-8")
    data, warnings, _ = _index(root)

    assert "outline_subject_unknown" in [w["code"] for w in warnings]
    # 大纲本身照给：它是数据，不是判据的牺牲品
    assert data["outline"]["化学"]


def test_editing_the_vocabulary_takes_effect_without_a_restart(tmp_path):
    """按 mtime 记忆：文件一动就重读。

    记死的话人改完词表会以为没生效——那正是「悄悄用着旧值」这一类静默。
    """
    from server.catalog import Catalog

    root = make_data_dir(tmp_path, [make_card()])
    catalog = Catalog(root)
    assert catalog.index()[0]["subjects"] == ["数学", "物理"]

    (root / "vocab" / "subjects.json").write_text(
        json.dumps({"科目": ["数学", "物理", "化学"]}, ensure_ascii=False), encoding="utf-8")

    assert catalog.index()[0]["subjects"] == ["数学", "物理", "化学"]


# --------------------------------------------------------- 显式回填命令


def test_the_backfill_previews_then_writes_exactly_one_field(tmp_path):
    from server import subject_assign
    from server.catalog import Catalog

    root = make_data_dir(tmp_path, [make_card("p-20200101-aaaaaa", **{"subject": None})])
    catalog = Catalog(root)
    report, _ = subject_assign.plan(catalog, {"p-20200101-aaaaaa": "物理"})

    assert report["counts"]["will_change"] == 1
    # 预演：盘上一个字节都没动
    on_disk = json.loads((root / "problems" / "p-20200101-aaaaaa.json").read_text("utf-8"))
    assert on_disk["subject"] is None

    written = subject_assign.apply_plan(catalog, report, [])
    assert len(written) == 1
    on_disk = json.loads((root / "problems" / "p-20200101-aaaaaa.json").read_text("utf-8"))
    assert on_disk["subject"] == "物理"
    # 只改 subject 一个字段：别的字节一个都不动
    assert on_disk["problem"]["transcript"] == "1. 一道题"
    assert on_disk["id"] == "p-20200101-aaaaaa"


def test_an_unknown_subject_in_the_map_blocks_the_whole_write(tmp_path):
    """坏值**一票否决**：半途写一半比全不写更难收拾。"""
    from server import subject_assign
    from server.catalog import Catalog

    root = make_data_dir(tmp_path, [
        make_card("p-20200101-aaaaaa", **{"subject": None}),
        make_card("p-20200101-bbbbbb", **{"subject": None}),
    ])
    mapping = {"p-20200101-aaaaaa": "物理", "p-20200101-bbbbbb": "化学"}
    report, _ = subject_assign.plan(Catalog(root), mapping)

    assert [entry["value"] for entry in report["blocked_by"]] == ["化学"]
    assert report["counts"]["will_change"] == 1  # 那条好的是"能改"，但这一趟不写

    code = subject_assign.main(["--data", str(root), "--map", _map_file(tmp_path, mapping)])
    assert code == 1
    for pid in ("p-20200101-aaaaaa", "p-20200101-bbbbbb"):
        on_disk = json.loads((root / "problems" / f"{pid}.json").read_text("utf-8"))
        assert on_disk["subject"] is None, "一条坏值在场时，**任何**卡都不许被写"


def test_cards_left_out_of_the_map_are_reported_as_unclassified(tmp_path):
    from server import subject_assign
    from server.catalog import Catalog

    root = make_data_dir(tmp_path, [
        make_card("p-20200101-aaaaaa", **{"subject": None}),
        make_card("p-20200101-bbbbbb", **{"subject": None}),
        make_card("p-20200101-cccccc"),
    ])
    report, _ = subject_assign.plan(Catalog(root), {"p-20200101-aaaaaa": "数学"})

    assert report["still_unclassified"] == ["p-20200101-bbbbbb"]
    assert report["counts"]["still_unclassified"] == 1
    assert report["counts"]["cards_on_disk"] == 3


def test_the_map_can_name_a_card_that_does_not_exist(tmp_path):
    from server import subject_assign
    from server.catalog import Catalog

    root = make_data_dir(tmp_path, [make_card()])
    report, _ = subject_assign.plan(Catalog(root), {"p-19990101-ffffff": "数学"})

    assert report["counts"]["skipped"] == 1
    assert report["skipped"][0]["code"] == subject_assign.CARD_MISSING


def test_the_summary_says_whether_it_actually_wrote(tmp_path, capsys):
    """`--apply` 之后仍印「会改」是**说反话**——而那一行是人做完回填之后唯一会读的一句话。"""
    from server import subject_assign

    root = make_data_dir(tmp_path, [make_card("p-20200101-aaaaaa", **{"subject": None})])
    mapping = _map_file(tmp_path, {"p-20200101-aaaaaa": "数学"})

    assert subject_assign.main(["--data", str(root), "--map", mapping]) == 0
    assert "会改 1 张" in capsys.readouterr().err

    assert subject_assign.main(["--data", str(root), "--map", mapping, "--apply"]) == 0
    err = capsys.readouterr().err
    assert "已改 1 张" in err
    assert "会改 1 张" not in err, "写过了还说「会改」——那句话会让人以为没生效，再跑一遍"
