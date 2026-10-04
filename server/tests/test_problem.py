"""`GET /api/problem/<pid>`：详情是列表条目的**超集**（契约 §5）。"""

from __future__ import annotations

from conftest import PNG_1X1, get_json, make_card

PID = "p-20200101-aaaaaa"


def card_with_attempts() -> dict:
    return make_card(
        PID,
        attempts=[
            {"at": "2020-02-01T10:00:00+00:00", "channel": "paper", "verdict": "wrong",
             "source": "人工确认", "confidence": None, "error_causes": ["计算失误"],
             "note": "判错：清零回池"},
            {"at": "2020-03-01T10:00:00+00:00", "channel": "screen", "verdict": "correct",
             "source": "自动判定", "confidence": 0.93, "error_causes": [],
             "note": "判对且脱离冷却：连续正确 1/2"},
        ],
        **{"mastery.streak": 1, "mastery.last_attempt_at": "2020-03-01T10:00:00+00:00"},
    )


def test_detail_passes_through_the_whole_card(api_for):
    api = api_for([card_with_attempts()], images={f"{PID}-problem.png": PNG_1X1,
                                                 f"{PID}-clean.png": PNG_1X1})
    status, body = get_json(api, f"/api/problem/{PID}")
    rec = body["data"]

    assert status == 200 and body["ok"] is True
    assert rec["id"] == PID
    assert rec["transcript"] == "1. 一道题"
    assert rec["standard_answer"] == "A"
    assert rec["correct_solution"] == "正解正文"
    assert rec["attempts"] == 2
    assert rec["last_verdict"] == "correct"
    assert rec["last_verdict_cn"] == "对"
    assert rec["streak"] == 1
    # 2020 年重做过、此后没再动过 → 早脱离冷却，在默认清单里
    assert (rec["cooling"], rec["in_default_list"]) == (False, True)
    assert body["warnings"] == rec["warnings"] == []


def test_detail_is_a_superset_of_the_index_entry(api_for):
    """列表页与详情页对同一道题必须说同一套话——串题那类事故就是从这里钻进来的。"""
    api = api_for([card_with_attempts()], images={f"{PID}-problem.png": PNG_1X1,
                                                 f"{PID}-clean.png": PNG_1X1})
    _, index_body = get_json(api, "/api/index")
    _, detail_body = get_json(api, f"/api/problem/{PID}")
    entry, rec = index_body["data"]["problems"][0], detail_body["data"]

    for key, value in entry.items():
        assert rec[key] == value, key
    assert set(rec) - set(entry) == {"attempts_detail", "source", "clean", "provenance"}


def test_detail_carries_the_audit_material(api_for):
    card = card_with_attempts()
    api = api_for([card])
    status, body = get_json(api, f"/api/problem/{PID}")
    rec = body["data"]

    assert rec["source"] == card["source"]
    assert rec["provenance"] == card["provenance"]
    assert rec["clean"]["method"] == "erase_ink"
    assert rec["clean"]["mask_px"] == 100
    assert rec["clean"]["health"] == {"residual_color_px": 0, "print_holes_px": 0}
    # 掩膜的几何读数：**裁剪图坐标**（与整页坐标是两套基准，见契约 §5/§10.2）
    assert rec["clean"]["boxes_norm"] == [[0.1, 0.1, 0.2, 0.2]]
    assert rec["clean"]["manual"] == {"add": [], "drop": []}
    # `attempts_detail` = 卡里 attempts 的**原样**，不裁剪——provider/model 也在里面（裁决 D2）
    assert rec["attempts_detail"] == card["attempts"]


def test_attempt_log_is_the_last_six_summarised(api_for):
    many = [
        {"at": f"2020-01-{day:02d}T00:00:00+00:00", "channel": "paper", "verdict": "wrong",
         "source": "人工确认", "error_causes": ["概念不清"], "note": "第 %d 次" % day}
        for day in range(1, 8)
    ]
    api = api_for([make_card(PID, attempts=many)])
    _, body = get_json(api, f"/api/problem/{PID}")
    rec = body["data"]

    assert rec["attempts"] == 7
    assert len(rec["attempts_detail"]) == 7
    assert len(rec["attempt_log"]) == 6
    assert rec["attempt_log"][0]["at"] == "2020-01-02"
    assert rec["attempt_log"][0]["channel_cn"] == "纸上重做"
    assert rec["attempt_log"][0]["verdict_cn"] == "错"


def test_unknown_problem_is_a_404_that_says_which_id(api_for):
    status, body = get_json(api_for([]), "/api/problem/p-nope-123456")

    assert status == 404
    assert body["ok"] is False and "data" not in body
    assert body["error"]["code"] == "not_found"
    assert "p-nope-123456" in body["error"]["message"]
    assert body["error"]["details"] == {"what": "problem", "id": "p-nope-123456"}
    assert body["warnings"] == [] and body["skipped"] == []


def test_attempt_summary_says_who_gave_the_verdict(api_for):
    """D2：`source` 只有 auto/human 两个机器取值，中文走 source_cn；
    provider 与 model 分开，不许拼成 `provider/model`。"""
    card = make_card(
        PID,
        attempts=[
            {"at": "2020-03-01T10:00:00+00:00", "channel": "screen", "verdict": "correct",
             "source": "auto", "confidence": 0.93, "provider": "deepseek",
             "model": "deepseek-flash", "error_causes": [], "note": "判对"},
            {"at": "2020-04-01T10:00:00+00:00", "channel": "paper", "verdict": "wrong",
             "source": "human", "confidence": None, "error_causes": ["计算失误"],
             "note": "判错：清零回池"},
        ],
    )
    _, body = get_json(api_for([card]), f"/api/problem/{PID}")
    auto, human = body["data"]["attempt_log"]

    assert (auto["source"], auto["source_cn"]) == ("auto", "自动判定")
    assert (auto["provider"], auto["model"]) == ("deepseek", "deepseek-flash")
    assert (human["source"], human["source_cn"]) == ("human", "人工确认")
    assert human["provider"] is None and human["model"] is None
    # 失败路径不许混进来：那不是一次「看不清」的判定，而是一次没发生的重做
    assert all("model" in log and "provider" in log for log in body["data"]["attempt_log"])
