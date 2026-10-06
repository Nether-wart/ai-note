"""题卡属性的编辑（契约 §10.7）：闭集、词表两条口径、幂等、原子。

这一份盯的是**写入口那个接缝**：哪些字段能改、坏值会不会写进去、
"什么都没改"是不是被明说，以及科目改完索引跟不跟得上。
"""

from __future__ import annotations

import json

from server import cardstore
from server.tests.conftest import get_json, make_card


def patch_json(api, target: str, payload):
    response = api.handle("PATCH", target, body=json.dumps(payload).encode("utf-8"),
                          content_type="application/json")
    return response.status, json.loads(response.body)


def test_改科目_落盘并立刻反映到索引(api_for):
    card = make_card(subject="数学")
    api = api_for([card])

    status, env = patch_json(api, f"/api/problem/{card['id']}", {"subject": "物理"})
    assert status == 200
    assert env["data"]["changed"] == ["subject"]
    assert env["data"]["card"]["subject"] == "物理"
    # 盘上真的改了（直接读文件——串长表达式时出错也看不出是哪一层）
    on_disk = json.loads(cardstore.card_path(api.catalog, card["id"]).read_text(encoding="utf-8"))
    assert on_disk["subject"] == "物理"
    # 索引是按需重算的 → 不用重建索引，下一次读就是新的
    _status, index = get_json(api, "/api/index")
    by_subject = index["data"]["stats"]["by_subject"]        # 字典：科目 → 读数
    assert by_subject["物理"]["problems"] == 1
    assert by_subject["数学"]["problems"] == 0


def test_科目不在词表里明确拒绝_而且一个字节都没写(api_for):
    card = make_card(subject="数学")
    api = api_for([card])
    before = cardstore.card_path(api.catalog, card["id"]).read_bytes()

    status, env = patch_json(api, f"/api/problem/{card['id']}", {"subject": "体育"})
    assert status == 400
    assert env["error"]["reason"] == "subject_unknown"
    assert "数学" in env["error"]["details"]["allowed"]
    assert cardstore.card_path(api.catalog, card["id"]).read_bytes() == before


def test_未归类是一等状态_可以给_null(api_for):
    card = make_card(subject="数学")
    api = api_for([card])
    status, env = patch_json(api, f"/api/problem/{card['id']}", {"subject": None})
    assert status == 200
    assert env["data"]["card"]["subject"] is None
    assert env["data"]["changed"] == ["subject"]


def test_错因不在词表里拒绝(api_for):
    card = make_card(subject="数学")
    api = api_for([card])
    # 夹具默认**没有**错因词表（那时走"收下 ＋ 警告"那一支），这条要验的是"词表在而取值不在"
    (api.catalog.root / "vocab" / "error-causes.json").write_text(
        json.dumps({"错因": ["概念不清", "计算失误"]}, ensure_ascii=False), encoding="utf-8")

    status, env = patch_json(api, f"/api/problem/{card['id']}",
                             {"error_causes": ["概念不清", "今天心情不好"]})
    assert status == 400
    assert env["error"]["reason"] == "error_cause_unknown"
    assert env["error"]["details"]["unknown"] == ["今天心情不好"]


def test_不可改的字段明确拒绝并点名可改闭集(api_for):
    card = make_card(subject="数学")
    api = api_for([card])
    for field in ("mastery", "attempts", "standard_answer", "id", "created_at"):
        status, env = patch_json(api, f"/api/problem/{card['id']}", {field: 1})
        assert status == 400, field
        assert env["error"]["reason"] == "problem_field_not_editable", field
        assert "subject" in env["error"]["details"]["allowed"], field


def test_值没变就算没改_不写盘也不含糊(api_for):
    card = make_card(subject="数学")
    api = api_for([card])
    before = cardstore.card_path(api.catalog, card["id"]).read_bytes()

    status, env = patch_json(api, f"/api/problem/{card['id']}", {"subject": "数学"})
    assert status == 200
    assert env["data"]["changed"] == []
    assert cardstore.card_path(api.catalog, card["id"]).read_bytes() == before


def test_review_给字符串_落成对象_置回未审核时不留下旧时刻(api_for):
    card = make_card(subject="数学")
    api = api_for([card])

    _s, env = patch_json(api, f"/api/problem/{card['id']}", {"review": "reviewed"})
    review = env["data"]["card"]["review"]
    assert review["status"] == "reviewed" and review["reviewed_at"]

    _s, env = patch_json(api, f"/api/problem/{card['id']}", {"review": "unreviewed"})
    review = env["data"]["card"]["review"]
    assert review["status"] == "unreviewed"
    assert review["reviewed_at"] is None, "留着上一个时刻会让人以为这一刻被审过"


def test_考点去重但保序(api_for):
    card = make_card(subject="数学")
    api = api_for([card])
    _s, env = patch_json(api, f"/api/problem/{card['id']}",
                         {"topics": ["数列/求和", "函数与导数/极值与最值", "数列/求和"]})
    assert env["data"]["card"]["topics"] == ["数列/求和", "函数与导数/极值与最值"]


def test_没有这张卡是_404(api_for):
    api = api_for([make_card(subject="数学")])
    status, env = patch_json(api, "/api/problem/p-20200101-ffffff", {"subject": "数学"})
    assert status == 404
    assert env["error"]["code"] == "not_found"


def test_词表不在时收下并喊出来(api_for, tmp_path):
    root = make_data_dir_without_error_causes(tmp_path)
    card = make_card(subject="数学")
    (root / "problems" / f"{card['id']}.json").write_text(
        json.dumps(card, ensure_ascii=False), encoding="utf-8")
    api = api_for(root=root)

    status, env = patch_json(api, f"/api/problem/{card['id']}",
                             {"error_causes": ["反正是自己写的"]})
    assert status == 200, "词表不在是环境缺失，不是输入错——先收下"
    assert env["data"]["card"]["error_causes"] == ["反正是自己写的"]
    assert [w["code"] for w in env["warnings"]] == ["error_causes_vocab_missing"]


def make_data_dir_without_error_causes(tmp_path):
    """只少了错因词表的数据目录（科目词表还在）。"""
    from server.tests.conftest import make_data_dir
    root = make_data_dir(tmp_path, [])
    (root / "vocab" / "error-causes.json").unlink(missing_ok=True)
    return root
