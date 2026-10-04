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
