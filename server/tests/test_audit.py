"""审计：页 ↔ 卡双向对账（工单 #15 验收 2、spec #2 的 `--audit` 扩展）。

打的是**外部行为**：一个数据目录进去、一份结构化结论出来。审计**只读盘**，不写任何东西，
也不读索引（索引是派生物：重建一次索引，审计结论不变）。

三条必须能报出来的情况各有正例与反例：

1. **页里记了收入但卡片不存在** → `page_card_missing`（warning，带页/块/卡三个 id 与它推出来的路径）。
2. **卡片没有页绑定** → 消费 `pages.page_binding`（#9 的唯一实现，经 `warnings.card_warnings`）：
   旧卡 `page_binding_missing` = **hint**（旧数据不该因为新结构变成脏数据），
   页实体在场却对不上账 `page_binding_lost` = **warning**。两级不许混。
3. **同一页两张卡题干逐字相同** → `duplicate_transcript_on_page`（warning）。
   判据与索引级 `duplicate_transcript` **共用一份实现**；反例是「差一个字的近似」不许报
   （逐字就是逐字，没有模糊比、没有相似度）。

另外「不许静默」也在这里钉住：**没发现问题也要说清检查了哪几项**（`checked[]` 永远在），
每一条发现都能定位到具体的卡/页/块（`card_id`/`page_id`/`block_id`/`path`）。
"""

from __future__ import annotations

import json

from conftest import make_card
from server import audit
from server.catalog import Catalog


def write(root, *, cards=(), pages=(), raw_cards=None, images=(), photos=()):
    """造一个数据目录（卡、页、资产、以及故意写坏的原始文件）。"""
    (root / "problems").mkdir(parents=True, exist_ok=True)
    (root / "pages").mkdir(parents=True, exist_ok=True)
    (root / "assets").mkdir(parents=True, exist_ok=True)
    for card in cards:
        (root / "problems" / f"{card['id']}.json").write_text(
            json.dumps(card, ensure_ascii=False, indent=2), encoding="utf-8")
    for name, blob in (raw_cards or {}).items():
        (root / "problems" / name).write_bytes(blob)
    for page in pages:
        (root / "pages" / f"{page['id']}.json").write_text(
            json.dumps(page, ensure_ascii=False, indent=2), encoding="utf-8")
    for name in images:
        (root / "assets" / name).write_bytes(b"\x89PNG\r\n\x1a\n")
    for name in photos:
        (root / "pages" / name).write_bytes(b"\x89PNG\r\n\x1a\n")
    return Catalog(root)


def block(block_id, card_id=None, *, keep=True, box=(0.0, 0.0, 1.0, 0.5), question_no=None):
    return {"id": block_id, "bbox_norm": list(box), "bbox_px": None,
            "question_no": question_no, "card_id": card_id, "keep": keep}


def page(page_id, blocks, *, image=None, **overrides):
    out = {
        "version": 1,
        "id": page_id,
        "image": image if image is not None else f"{page_id}.png",
        "created_at": "2026-10-04T14:31:35+08:00",
        "origin": {"original_file": "2.png", "sheet": None, "page_number": None},
        "blocks": blocks,
    }
    out.update(overrides)
    return out


def a_card(pid, *, page_id, transcript="1. 一道题", **overrides):
    """一张「干净」的卡：字段齐全、擦除图与题面图都在、绑在 `page_id` 上。"""
    card = make_card(pid, **overrides)
    card["source"]["page_image"] = f"pages/{page_id}.png"
    card["problem"]["transcript"] = transcript
    return card


def findings(report, code=None):
    return [f for f in report["findings"] if code is None or f["code"] == code]


def levels(report, code):
    return sorted({f["level"] for f in findings(report, code)})


# ---------------------------------------------------------------- 情况 1：页里记了收入但卡片不存在


def test_a_kept_block_whose_card_is_gone_is_an_error_that_points_at_it(tmp_path):
    """页里记着收、绑着卡，卡却不在盘上 → warning，且能定位到「哪一页的哪一块的哪张卡」。"""
    catalog = write(tmp_path / "data", pages=[
        page("aaaa11112222", [block("b1", "p-20200101-aaaaaa"), block("b2", None)]),
    ])

    report = audit.audit(catalog)

    found = findings(report, "page_card_missing")
    assert len(found) == 1, report["findings"]
    assert found[0]["level"] == "warning"
    assert found[0]["page_id"] == "aaaa11112222"
    assert found[0]["block_id"] == "b1"
    assert found[0]["card_id"] == "p-20200101-aaaaaa"
    assert found[0]["path"].endswith("p-20200101-aaaaaa.json")
    assert report["ok"] is False


def test_a_kept_block_that_has_no_card_yet_is_only_a_hint(tmp_path):
    """收了但还没入库 = 正常中间态（入库是另一个动作）→ hint，不许喊成问题。"""
    catalog = write(tmp_path / "data", pages=[
        page("aaaa11112222", [block("b1", None), block("b2", None)]),
    ], photos=["aaaa11112222.png"])

    report = audit.audit(catalog)

    found = findings(report, "page_block_not_committed")
    assert len(found) == 2
    assert {f["level"] for f in found} == {"hint"}
    assert {f["block_id"] for f in found} == {"b1", "b2"}
    assert report["ok"] is True, "hint 不算问题"


# ---------------------------------------------------------------- 情况 2：卡片没有页绑定（两级）


def test_a_legacy_card_without_a_page_file_is_a_hint(tmp_path):
    """旧卡还没有页文件 → **hint**：旧数据不该因为新结构变成脏数据（#9 验收 2）。"""
    card = a_card("p-20200101-aaaaaa", page_id="aaaa11112222")
    catalog = write(tmp_path / "data", cards=[card])

    report = audit.audit(catalog)

    assert levels(report, "page_binding_missing") == ["hint"]
    found = findings(report, "page_binding_missing")[0]
    assert found["card_id"] == "p-20200101-aaaaaa"
    assert found["check"] == "card_page_binding"


def test_a_card_the_page_does_not_bind_is_an_error(tmp_path):
    """页实体在、却没有块绑定这张卡 = 页↔卡对不上账 → **warning**（与上一条判据同源、级别不同）。"""
    card = a_card("p-20200101-aaaaaa", page_id="aaaa11112222")
    catalog = write(tmp_path / "data", cards=[card], pages=[
        page("aaaa11112222", [block("b1", "p-20200101-bbbbbb")]),
    ])

    report = audit.audit(catalog)

    assert levels(report, "page_binding_lost") == ["warning"]
    found = findings(report, "page_binding_lost")[0]
    assert found["card_id"] == "p-20200101-aaaaaa"
    assert found["page_id"] == "aaaa11112222"
    assert report["ok"] is False


def test_the_two_kinds_of_missing_binding_do_not_mix_into_one_level(tmp_path):
    """同一份数据里两级**同时**出现，各自保持自己的级别（混成一个级别 = 审计失去可信度）。"""
    legacy = a_card("p-20200101-aaaaaa", page_id="aaaa11112222")   # 页文件不在 → hint
    lost = a_card("p-20200101-bbbbbb", page_id="bbbb11112222")     # 页在却不绑它 → warning
    catalog = write(tmp_path / "data", cards=[legacy, lost], pages=[
        page("bbbb11112222", [block("b1", "p-20200101-cccccc")]),
    ])

    report = audit.audit(catalog)

    assert levels(report, "page_binding_missing") == ["hint"]
    assert levels(report, "page_binding_lost") == ["warning"]
    assert report["counts"]["by_level"] == {"warning": len(
        [f for f in report["findings"] if f["level"] == "warning"]), "hint": len(
        [f for f in report["findings"] if f["level"] == "hint"])}


# ---------------------------------------------------------------- 情况 3：同页题干逐字相同


def test_two_cards_from_the_same_page_with_the_same_transcript_are_an_error(tmp_path):
    """同一页两张卡题干**逐字**相同 → warning（spec #2：这条没有例外）。"""
    one = a_card("p-20200101-aaaaaa", page_id="aaaa11112222", transcript="17. 求 f(x) 的最小值")
    two = a_card("p-20200101-bbbbbb", page_id="aaaa11112222", transcript="17. 求 f(x) 的最小值")
    catalog = write(tmp_path / "data", cards=[one, two], pages=[
        page("aaaa11112222", [block("b1", "p-20200101-aaaaaa", question_no=17),
                              block("b2", "p-20200101-bbbbbb", question_no=18)]),
    ])

    report = audit.audit(catalog)

    found = findings(report, "duplicate_transcript_on_page")
    assert len(found) == 1, report["findings"]
    assert found[0]["level"] == "warning"
    assert found[0]["page_id"] == "aaaa11112222"
    assert found[0]["card_ids"] == ["p-20200101-aaaaaa", "p-20200101-bbbbbb"]
    assert "17. 求 f(x) 的最小值" in found[0]["message"]


def test_a_one_character_difference_is_not_a_duplicate(tmp_path):
    """差一个字就不是「逐字相同」：**不许**退化成模糊比或相似度（这条测试会咬住它）。"""
    one = a_card("p-20200101-aaaaaa", page_id="aaaa11112222", transcript="17. 求 f(x) 的最小值")
    two = a_card("p-20200101-bbbbbb", page_id="aaaa11112222", transcript="17. 求 f(x) 的最大值")
    catalog = write(tmp_path / "data", cards=[one, two], pages=[
        page("aaaa11112222", [block("b1", "p-20200101-aaaaaa"),
                              block("b2", "p-20200101-bbbbbb")]),
    ])

    report = audit.audit(catalog)

    assert findings(report, "duplicate_transcript_on_page") == []


def test_the_same_transcript_on_two_different_pages_is_not_the_page_level_check(tmp_path):
    """跨页的重复不归页级那条管（它由索引的 `duplicate_transcript` 报），这里不许重复报。"""
    one = a_card("p-20200101-aaaaaa", page_id="aaaa11112222", transcript="17. 同一段题干")
    two = a_card("p-20200101-bbbbbb", page_id="bbbb11112222", transcript="17. 同一段题干")
    catalog = write(tmp_path / "data", cards=[one, two], pages=[
        page("aaaa11112222", [block("b1", "p-20200101-aaaaaa")]),
        page("bbbb11112222", [block("b1", "p-20200101-bbbbbb")]),
    ])

    report = audit.audit(catalog)

    assert findings(report, "duplicate_transcript_on_page") == []
    # 但索引级那条仍然会喊（同一个判据、另一个范围）
    _, warnings, _ = catalog.index()
    assert "duplicate_transcript" in {w["code"] for w in warnings}


# ---------------------------------------------------------------- 页↔卡对账的另外三条


def test_a_card_bound_to_a_dropped_block_is_a_hint_not_a_silent_deletion(tmp_path):
    """卡绑的块被标成不收 → **卡不会被自动删**（#14 已定：删卡是人的事）→ hint。"""
    card = a_card("p-20200101-aaaaaa", page_id="aaaa11112222")
    catalog = write(tmp_path / "data", cards=[card], pages=[
        page("aaaa11112222", [block("b1", "p-20200101-aaaaaa", keep=False)]),
    ])

    report = audit.audit(catalog)

    found = findings(report, "card_block_not_kept")
    assert len(found) == 1
    assert found[0]["level"] == "hint"
    assert found[0]["card_id"] == "p-20200101-aaaaaa"
    assert found[0]["block_id"] == "b1"


def test_a_card_that_claims_another_page_is_a_two_way_mismatch(tmp_path):
    """卡自己的 `source.page_image` 指的是另一页，却被绑在这一页上 → warning（双向对账）。"""
    card = a_card("p-20200101-aaaaaa", page_id="bbbb11112222")   # 卡说是 bbbb
    catalog = write(tmp_path / "data", cards=[card], pages=[
        page("aaaa11112222", [block("b1", "p-20200101-aaaaaa")]),   # 页是 aaaa
        page("bbbb11112222", [block("b1", None)]),
    ])

    report = audit.audit(catalog)

    found = findings(report, "card_bound_to_other_page")
    assert len(found) == 1
    assert found[0]["level"] == "warning"
    assert found[0]["page_id"] == "aaaa11112222"
    assert found[0]["card_id"] == "p-20200101-aaaaaa"


def test_a_page_whose_photo_is_gone_is_an_error(tmp_path):
    """页实体在场、整页照片不在 → warning（照片是最后的底，页绑定指向一张取不到的图）。"""
    catalog = write(tmp_path / "data", pages=[
        page("aaaa11112222", [block("b1", None)], image="aaaa11112222.png"),
    ])

    report = audit.audit(catalog)

    found = findings(report, "page_photo_missing")
    assert len(found) == 1
    assert found[0]["level"] == "warning"
    assert found[0]["page_id"] == "aaaa11112222"


def test_an_unreadable_card_file_is_reported_not_silently_dropped(tmp_path):
    """读不了的卡要点名报出来（不是安静地少一张）。"""
    catalog = write(tmp_path / "data", raw_cards={"p-20200101-broken.json": b"{ not json"})

    report = audit.audit(catalog)

    found = findings(report, "card_file_unreadable")
    assert len(found) == 1
    assert found[0]["level"] == "warning"
    assert found[0]["card_id"] == "p-20200101-broken"
    assert report["audited"]["cards_unreadable"] == 1


# ---------------------------------------------------------------- 不许静默 + 与索引一致


def test_a_clean_dataset_is_reported_as_checked_and_clean(tmp_path):
    """**没发现问题也要说清检查了哪几项**：`checked[]` 永远在，`findings` 为空、`ok` 为真。"""
    pid = "p-20200101-aaaaaa"
    card = a_card(pid, page_id="aaaa11112222")
    catalog = write(tmp_path / "data", cards=[card], pages=[
        page("aaaa11112222", [block("b1", pid)]),
    ], images=[f"{pid}-problem.png", f"{pid}-clean.png"], photos=["aaaa11112222.png"])

    report = audit.audit(catalog)

    assert report["findings"] == [], report["findings"]
    assert report["ok"] is True
    assert report["counts"]["warning"] == 0 and report["counts"]["hint"] == 0
    assert report["counts"]["by_code"] == {}
    checked = {row["id"] for row in report["checked"]}
    assert {"card_self_check", "card_page_binding", "page_kept_block_card_exists",
            "duplicate_transcript_on_page"} <= checked
    assert all(row["what"] for row in report["checked"]), "每一项都要说清它查的是什么"
    assert report["audited"]["cards"] == 1 and report["audited"]["pages"] == 1


def test_the_audit_result_does_not_change_when_the_index_is_rebuilt(tmp_path):
    """审计只读盘上的卡与页：重建一次索引，结论逐字节相同（index 是派生物）。"""
    pid = "p-20200101-aaaaaa"
    catalog = write(tmp_path / "data", cards=[a_card(pid, page_id="aaaa11112222")], pages=[
        page("aaaa11112222", [block("b1", pid), block("b2", None)]),
    ])

    before = audit.audit(catalog)
    catalog.index()
    after = audit.audit(catalog)

    assert json.dumps(before, ensure_ascii=False, sort_keys=True) == \
        json.dumps(after, ensure_ascii=False, sort_keys=True)


def test_the_same_fact_is_reported_once_not_twice(tmp_path):
    """同一件事只报一次：页绑定那条由 `card_page_binding` 报，`card_self_check` 不许再报一遍。"""
    card = a_card("p-20200101-aaaaaa", page_id="aaaa11112222")
    catalog = write(tmp_path / "data", cards=[card])

    report = audit.audit(catalog)

    bindings = [f for f in report["findings"] if f["code"].startswith("page_binding_")]
    assert len(bindings) == 1, report["findings"]
    assert bindings[0]["check"] == "card_page_binding"
    assert bindings[0]["page_id"] == "aaaa11112222", "发现里要能定位到是哪一页"


def test_every_finding_carries_an_explicit_level_and_a_check(tmp_path):
    """每条发现都带 `check`（哪一项查出来的）、`code`、显式 `level` 与人的原话。"""
    card = a_card("p-20200101-aaaaaa", page_id="aaaa11112222")
    catalog = write(tmp_path / "data", cards=[card], pages=[
        page("aaaa11112222", [block("b1", "p-20200101-zzzzzz")]),
    ])

    report = audit.audit(catalog)

    assert report["findings"]
    checks = {row["id"] for row in report["checked"]}
    for finding in report["findings"]:
        assert finding["level"] in ("warning", "hint")
        assert finding["code"] and finding["message"]
        assert finding["check"] in checks, "发现必须归到一项声明过的检查上"


# ---------------------------------------------------------------- CLI


def test_the_cli_exits_nonzero_only_when_there_is_a_warning(tmp_path, capsys):
    """退出码：有 warning 级发现 → 1；只有 hint（或什么都没有）→ 0。hint 不算问题。"""
    hint_only = tmp_path / "hint"
    pid = "p-20200101-aaaaaa"
    # 干净的一张旧卡：字段齐全、两张图都在，只有「页文件还没回填」这一条 hint。
    write(hint_only, cards=[a_card(pid, page_id="aaaa11112222")],
          images=[f"{pid}-problem.png", f"{pid}-clean.png"])

    assert audit.main(["--data", str(hint_only)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["ok"] is True
    assert printed["checked"], "没发现问题也要说清检查了哪几项"

    broken = tmp_path / "broken"
    write(broken, pages=[page("aaaa11112222", [block("b1", "p-20200101-aaaaaa")])])

    assert audit.main(["--data", str(broken)]) == 1


def test_the_cli_says_the_data_dir_does_not_exist_instead_of_crashing(tmp_path, capsys):
    """盘上的失败也要说人话（D1：CLI 不许裸回溯）。"""
    assert audit.main(["--data", str(tmp_path / "nope")]) == 2
    assert "数据目录不存在" in capsys.readouterr().err
