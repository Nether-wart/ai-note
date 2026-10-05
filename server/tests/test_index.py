"""`GET /api/index` 的外部行为：一个 HTTP 请求进去，一个信封出来。"""

from __future__ import annotations

import json

from server import pages


def get_json(api, target: str):
    r = api.handle("GET", target)
    return r.status, json.loads(r.body)


def test_empty_data_dir_gives_an_empty_but_honest_index(api_for):
    """没有题卡不是错误：索引建成、说清它有几条、警告与跳过都是空的。"""
    status, body = get_json(api_for([]), "/api/index")

    assert status == 200
    assert body["ok"] is True
    assert body["data"]["count"] == 0
    assert body["data"]["problems"] == []
    assert body["warnings"] == []
    assert body["skipped"] == []


def only_problem(body: dict) -> dict:
    assert body["data"]["count"] == 1, body
    return body["data"]["problems"][0]


def test_card_content_fields_are_passed_through(api_for):
    """题卡里的东西原样出来：转录、选项、原答、订正、标准答案、正解、考点、错因。"""
    from conftest import make_card

    pid = "p-20200101-aaaaaa"
    status, body = get_json(api_for([make_card(pid)]), "/api/index")
    p = only_problem(body)

    assert status == 200
    assert (p["id"], p["created_at"]) == (pid, "2020-01-01T00:00:00+08:00")
    assert (p["type"], p["type_cn"]) == ("choice", "选择")
    assert p["transcript"] == "1. 一道题"
    assert p["options"] == [{"label": "A", "text": "甲"}, {"label": "B", "text": "乙"}]
    assert p["original_answer"] == "B"
    assert p["correction"] == "（红笔）订正"
    assert p["original_transcript"] == "（黑笔）推导"
    assert p["present"] is True
    assert p["standard_answer"] == "A"
    assert p["correct_solution"] == "正解正文"
    assert p["topics"] == ["函数与导数/极值与最值"]
    assert p["error_causes"] == ["概念不清"]
    assert p["new_tag_proposals"] == []
    assert p["review"] == "reviewed"
    assert p["reviewed_at"] == "2020-01-01T00:00:00+00:00"
    assert p["review_reopened_because"] is None
    assert (p["cells"], p["cells_source"]) == (1, "manual")


def test_mastery_and_default_list_readings(api_for):
    """掌握与冷却的读数是派生的：从未重做过的题以录入时间起算冷却。"""
    from conftest import make_card

    # 2020 年录入、从未重做 → 早就脱离冷却 → 在默认打印清单里
    status, body = get_json(api_for([make_card("p-old-aaaaaa")]), "/api/index")
    p = only_problem(body)

    assert p["mastery"] == {"state": "in_pool", "streak": 0, "last_attempt_at": None}
    assert p["mastery_cn"] == "在池"
    assert (p["graduated"], p["cooling"], p["cooldown_days"]) == (False, False, 0)
    assert (p["in_default_list"], p["excluded_from_default_because"]) == (True, None)
    assert p["attempts"] == 0
    assert (p["last_verdict"], p["last_verdict_cn"]) == (None, None)
    assert p["attempt_log"] == []
    # 排序键归一化到 UTC：原始字符串是 +08:00，不许直接比字符串
    assert p["sort_key"] == "2019-12-31T16:00:00+00:00"


def test_images_auto_judge_and_stats(api_for):
    """图片 URL、可判性读数与 stats 都从同一批记录一趟算出来。"""
    from conftest import PNG_1X1, make_card

    pid = "p-20200101-aaaaaa"
    images = {f"{pid}-problem.png": PNG_1X1, f"{pid}-clean.png": PNG_1X1}
    api = api_for([make_card(pid)], images=images)
    # 这张卡号称「干净」，所以它也得有页绑定：旧卡缺页绑定现在会响一条**提示**
    # （#9 验收 2）。先回填，把与本节无关的那条噪音去掉。
    pages.backfill_pages(api.catalog, apply=True)
    status, body = get_json(api, "/api/index")
    p = only_problem(body)

    assert p["has_clean"] is True
    assert p["images"] == {
        "original": f"/api/problem/{pid}/image/original",
        "clean": f"/api/problem/{pid}/image/clean",
        "mask": None,
    }
    assert p["auto_judge"] == {"eligible": True, "reason": None, "reason_text": None}
    assert p["warnings"] == []
    assert body["data"]["stats"] == {
        "problems": 1,
        "problems_skipped": 0,
        "in_default_list": 1,
        "cooling": 0,
        "graduated": 0,
        "auto_judge_eligible": 1,
        "auto_judge_ineligible": 0,
    }
    assert body["warnings"] == []


def test_index_states_which_address_the_phone_should_use(api_for):
    """`--public-base` 必须有暴露面：手机要打开的链接不能靠猜（ADR 0007 第 5 条）。

    #13 把这块扩成服务自述：对外地址、上传页链接（同一个 `public_base` 拼出来）、
    收件目录在哪，以及「我没有改任何已有数据」。逐项断言而不是整体相等——
    它是一段会长的自述，整体相等只会在每次加字段时假红。
    """
    status, body = get_json(api_for([]), "/api/index")
    server = body["data"]["server"]

    assert status == 200
    assert server["public_base"] == "http://127.0.0.1:8765"
    assert server["upload_url"] == "http://127.0.0.1:8765/upload"
    assert server["read_only"] is True
    assert "inbox" in server and server["reachable_from_other_devices"] is False
    # 没说出口的降级就是静默：读只读这句话要自己解释清楚它到底指什么
    assert "收件目录" in server["read_only_note"]


# ------------------------------------------- 入库建的是骨架卡：还没转录也要收录（#15 验收 1）

def test_a_card_that_has_no_transcript_yet_is_listed_with_a_warning(api_for):
    """**入库产生的是骨架卡**（题面还没转录）：索引必须收录它，并报一条 warning。

    为什么不能像以前那样静默跳过：spec #2 第 17 条「入库后直接进审核队列」——
    落盘了却在清单里看不见，就是「静默丢题」，而这个项目最怕的正是它。
    「还没转录」是一个**由人（或抽取角色）填写的字段还没填**，按 ADR 0007 第 6 条
    必须有一个会喊的检查。
    """
    from conftest import make_card

    pid = "p-20200101-aaaaaa"
    card = make_card(pid, **{"problem.transcript": ""})
    status, body = get_json(api_for([card]), "/api/index")

    problem = only_problem(body)
    assert status == 200
    assert problem["id"] == pid
    assert problem["transcript"] in (None, "")
    assert body["skipped"] == [], "收录进清单，不是建不出记录"
    assert body["data"]["stats"]["problems_skipped"] == 0
    missing = [w for w in problem["warnings"] if w["code"] == "problem_transcript_missing"]
    assert len(missing) == 1
    assert missing[0]["level"] == "warning"
    assert missing[0]["id"] == pid
    assert "转录" in missing[0]["message"]


def test_a_file_without_a_problem_object_is_still_skipped(api_for):
    """没有 `problem` 对象 = 真的建不出记录 → 仍然进 `skipped`（这一档没有变松）。"""
    from conftest import make_card

    card = make_card("p-20200101-aaaaaa")
    del card["problem"]
    status, body = get_json(api_for([card]), "/api/index")

    assert status == 200
    assert body["data"]["count"] == 0
    assert [row["code"] for row in body["skipped"]] == ["problem_missing_field"]
    assert body["data"]["stats"]["problems_skipped"] == 1


def test_a_problem_that_is_not_an_object_is_still_skipped(api_for):
    """`problem` 不是对象也一样建不出记录（拼错的结构不许冒充成骨架卡）。"""
    from conftest import make_card

    status, body = get_json(api_for([make_card(**{"problem": "一道题"})]), "/api/index")

    assert status == 200
    assert body["data"]["count"] == 0
    assert [row["code"] for row in body["skipped"]] == ["problem_missing_field"]


# ------------------------------------------------- 跳过码的形状（R3/D9）
#
# 「读不了 / 不是一个对象」这两档以前只被断言了「进了 skipped」，码与指针从没被断言过。
# 一条建不出来的记录是**比警告更重**的一档：索引里少一张卡必须是一个看得见的数字。


def test_an_unreadable_card_file_is_skipped_with_a_named_code(api_for):
    """题卡读不了 → `skipped[]` 点名（`problem_file_unreadable` + 路径 + 文件名主干）。"""
    api = api_for([], extra_files={"p-20200101-aaaaaa.json": b"{ not json"})

    status, body = get_json(api, "/api/index")

    assert status == 200
    (row,) = body["skipped"]
    assert row["code"] == "problem_file_unreadable"
    assert row["id"] == "p-20200101-aaaaaa", "指针要落到文件名主干上"
    assert "p-20200101-aaaaaa.json" in row["message"], "message 里带全路径"
    assert "JSONDecodeError" in row["message"], "message 带异常类名"
    assert body["data"]["count"] == 0
    assert body["data"]["stats"]["problems_skipped"] == 1


def test_a_card_file_that_is_not_an_object_is_skipped_with_a_named_code(api_for):
    """文件里不是 JSON 对象 → `problem_not_dict`（与「读不了」分开的两档）。"""
    api = api_for([], extra_files={"p-20200101-aaaaaa.json": b"[1, 2, 3]"})

    status, body = get_json(api, "/api/index")

    assert status == 200
    (row,) = body["skipped"]
    assert row["code"] == "problem_not_dict"
    assert row["id"] == "p-20200101-aaaaaa"
    assert "不是一个 JSON 对象" in row["message"]
    assert body["data"]["count"] == 0
    assert body["data"]["stats"]["problems_skipped"] == 1
